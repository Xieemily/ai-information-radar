from __future__ import annotations

from datetime import datetime, time, timezone
import json
import os
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Brief, EventCluster, Item, JobRun, Source
from app.services.briefing import application_today, generate_brief
from app.services.feedback import DEFAULT_ITEM_STATE, item_feedback_states, record_item_action, state_counts
from app.services.jobs import run_daily_job
from app.services.translation import TranslationNotConfigured, translate_item


router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")

TOPIC_CATEGORIES = (
    {"key": "tech", "label": "科技", "tags": ("tech", "engineering", "architecture", "devops", "devtools", "system-design", "infrastructure", "networking", "cloud", "cs", "math", "github", "hn")},
    {"key": "ai", "label": "AI", "tags": ("ai", "ml", "research")},
    {"key": "product", "label": "商业产品", "tags": ("product", "leadership", "growth", "strategy")},
    {"key": "news", "label": "新闻", "tags": ("news", "world", "chinese")},
    {"key": "film", "label": "影视创作", "tags": ("screenwriting", "filmmaking", "film-industry", "indie-film", "script-analysis", "writing-craft", "craft", "structure")},
    {"key": "entertainment", "label": "娱乐文化", "tags": ("culture", "trending", "podcast", "discovery")},
)


def _stats(session: Session) -> dict:
    today = application_today()
    local_timezone = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Shanghai"))
    start = datetime.combine(today, time.min, tzinfo=local_timezone).astimezone(timezone.utc).replace(tzinfo=None)
    source_count = session.scalar(select(func.count(Source.id)).where(Source.enabled.is_(True))) or 0
    healthy_sources = session.scalar(
        select(func.count(Source.id)).where(Source.enabled.is_(True), Source.consecutive_failures == 0)
    ) or 0
    return {
        "source_count": source_count,
        "active_sources": source_count,
        "healthy_sources": healthy_sources,
        "item_count": session.scalar(select(func.count(Item.id))) or 0,
        "today_items": session.scalar(
            select(func.count(Item.id)).where(
                Item.published_at >= start,
                Item.status != "published_at_unknown",
            )
        ) or 0,
        "pending_jobs": session.scalar(select(func.count(JobRun.id)).where(JobRun.status == "running")) or 0,
        "ai_calls_today": 0,
        "system_ok": healthy_sources == source_count or source_count == 0,
    }


@router.get("/", response_class=HTMLResponse)
def today_page(request: Request, session: Session = Depends(get_db)):
    brief = session.scalar(select(Brief).order_by(Brief.brief_date.desc()).limit(1))
    return templates.TemplateResponse(request, "today.html", {"brief": brief, "stats": _stats(session)})


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, session: Session = Depends(get_db)):
    sources = list(session.scalars(select(Source).order_by(Source.priority.desc(), Source.title)))
    return templates.TemplateResponse(request, "sources.html", {"sources": sources, "stats": _stats(session)})


@router.post("/sources")
def add_source(
    request: Request,
    url: str = Form(...),
    title: str = Form(""),
    type: str = Form("rss"),
    priority: int = Form(3),
    session: Session = Depends(get_db),
):
    clean_url = url.strip()
    parsed = urlsplit(clean_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return RedirectResponse("/sources?notice=invalid-url", status_code=303)
    source = Source(
        url=clean_url,
        title=title.strip() or parsed.hostname,
        type=type if type in {"rss", "youtube", "podcast"} else "rss",
        priority=max(1, min(priority, 5)),
    )
    session.add(source)
    try:
        session.commit()
        session.refresh(source)
    except IntegrityError:
        session.rollback()
        return RedirectResponse("/sources?notice=duplicate", status_code=303)
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "partials/source_row.html", {"source": source})
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/{source_id}/toggle")
def toggle_source(source_id: int, request: Request, session: Session = Depends(get_db)):
    source = session.get(Source, source_id)
    if source is None:
        return RedirectResponse("/sources?notice=not-found", status_code=303)
    source.enabled = not source.enabled
    session.commit()
    session.refresh(source)
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "partials/source_row.html", {"source": source})
    return RedirectResponse("/sources", status_code=303)


@router.get("/items", response_class=HTMLResponse)
def items_page(
    request: Request,
    q: str = "",
    type: str = "",
    category: str = "",
    view: str = "inbox",
    session: Session = Depends(get_db),
):
    valid_view = view if view in {"inbox", "all", "saved", "ignored"} else "inbox"
    all_item_ids = list(session.scalars(select(Item.id)))
    all_states = item_feedback_states(session, all_item_ids)
    if valid_view == "saved":
        visible_ids = [item_id for item_id in all_item_ids if all_states.get(item_id, DEFAULT_ITEM_STATE)["saved"]]
    elif valid_view == "ignored":
        visible_ids = [item_id for item_id in all_item_ids if all_states.get(item_id, DEFAULT_ITEM_STATE)["ignored"]]
    elif valid_view == "inbox":
        visible_ids = [
            item_id
            for item_id in all_item_ids
            if not all_states.get(item_id, DEFAULT_ITEM_STATE)["read"]
            and not all_states.get(item_id, DEFAULT_ITEM_STATE)["ignored"]
        ]
    else:
        visible_ids = all_item_ids
    statement = select(Item).join(Source).options(selectinload(Item.source), selectinload(Item.translation))
    statement = statement.where(Item.id.in_(visible_ids)) if visible_ids else statement.where(Item.id == -1)
    if q.strip():
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(Item.title.ilike(pattern), Item.clean_text.ilike(pattern), Source.title.ilike(pattern))
        )
    if type:
        statement = statement.where(Item.content_type == type)
    category_config = next((item for item in TOPIC_CATEGORIES if item["key"] == category), None)
    if category_config:
        serialized_tags = cast(Source.tags, String)
        statement = statement.where(
            or_(*(serialized_tags.like(f'%"{tag}"%') for tag in category_config["tags"]))
        )
    items = list(session.scalars(statement.order_by(Item.published_at.desc()).limit(200)))
    item_states = {item.id: all_states.get(item.id, dict(DEFAULT_ITEM_STATE)) for item in items}
    return templates.TemplateResponse(
        request,
        "items.html",
        {
            "items": items,
            "stats": _stats(session),
            "query": q,
            "selected_type": type,
            "selected_category": category if category_config else "",
            "topic_categories": TOPIC_CATEGORIES,
            "current_view": valid_view,
            "view_counts": state_counts(session, all_item_ids),
            "item_states": item_states,
        },
    )


@router.post("/items/{item_id}/action")
def item_action(
    item_id: int,
    request: Request,
    action: str = Form(...),
    view: str = Form("inbox"),
    session: Session = Depends(get_db),
):
    item = session.scalar(select(Item).where(Item.id == item_id).options(selectinload(Item.source)))
    if item is None:
        return RedirectResponse("/items?notice=not-found", status_code=303)
    try:
        state = record_item_action(session, item, action)
    except ValueError:
        return RedirectResponse("/items?notice=invalid-action", status_code=303)
    if request.headers.get("HX-Request") == "true":
        counts = state_counts(session, session.scalars(select(Item.id)).all())
        headers = {"HX-Trigger": json.dumps({"radarCounts": counts})}
        if view == "inbox" and (state["read"] or state["ignored"]):
            return Response(status_code=200, headers=headers)
        if view == "saved" and not state["saved"]:
            return Response(status_code=200, headers=headers)
        if view == "ignored" and not state["ignored"]:
            return Response(status_code=200, headers=headers)
        response = templates.TemplateResponse(
            request,
            "partials/item_row.html",
            {"item": item, "item_state": state, "current_view": view},
        )
        response.headers.update(headers)
        return response
    return RedirectResponse(f"/items?view={view}", status_code=303)


@router.post("/items/{item_id}/translate")
async def item_translate(item_id: int, request: Request, session: Session = Depends(get_db)):
    item = session.scalar(
        select(Item)
        .where(Item.id == item_id)
        .options(selectinload(Item.translation))
    )
    if item is None:
        return templates.TemplateResponse(
            request,
            "partials/translation.html",
            {"translation": None, "translation_error": "没有找到这条内容。"},
            status_code=404,
        )
    try:
        outcome = await translate_item(session, item)
        translation, error = outcome.translation, None
    except TranslationNotConfigured as exc:
        translation, error = None, str(exc)
    except Exception:
        translation, error = None, "翻译暂时失败，请检查模型连接后重试。"
    return templates.TemplateResponse(
        request,
        "partials/translation.html",
        {"translation": translation, "translation_error": error},
    )


@router.get("/events", response_class=HTMLResponse)
def events_page(request: Request, session: Session = Depends(get_db)):
    clusters = list(
        session.scalars(
            select(EventCluster)
            .options(selectinload(EventCluster.items).selectinload(Item.source))
            .order_by(EventCluster.last_seen_at.desc(), EventCluster.momentum_score.desc())
        )
    )
    states = item_feedback_states(session, [item.id for cluster in clusters for item in cluster.items])
    events = []
    for cluster in clusters:
        visible = [
            item
            for item in cluster.items
            if item.status != "published_at_unknown"
            and not states.get(item.id, DEFAULT_ITEM_STATE)["ignored"]
        ]
        if not visible:
            continue
        visible.sort(key=lambda item: item.published_at, reverse=True)
        events.append(
            {
                "id": cluster.id,
                "title": cluster.title,
                "summary": (visible[0].clean_text or visible[0].title)[:220],
                "last_seen_at": max(item.published_at for item in visible),
                "item_count": len(visible),
                "source_count": len({item.source_id for item in visible}),
                "sources": list(dict.fromkeys(item.source.title for item in visible))[:3],
                "score": cluster.momentum_score,
            }
        )
    events.sort(key=lambda event: (event["last_seen_at"], event["score"]), reverse=True)
    return templates.TemplateResponse(
        request,
        "events.html",
        {"events": events[:100], "stats": _stats(session)},
    )


@router.get("/events/{cluster_id}", response_class=HTMLResponse)
def event_detail_page(cluster_id: int, request: Request, session: Session = Depends(get_db)):
    cluster = session.scalar(
        select(EventCluster)
        .where(EventCluster.id == cluster_id)
        .options(selectinload(EventCluster.items).selectinload(Item.source))
    )
    if cluster is None:
        return RedirectResponse("/events?notice=not-found", status_code=303)
    states = item_feedback_states(session, [item.id for item in cluster.items])
    items = [
        item
        for item in cluster.items
        if item.status != "published_at_unknown"
        and not states.get(item.id, DEFAULT_ITEM_STATE)["ignored"]
    ]
    items.sort(key=lambda item: item.published_at, reverse=True)
    event = {
        "id": cluster.id,
        "title": cluster.title,
        "summary": (items[0].clean_text or items[0].title)[:420] if items else "此事件的证据已全部忽略。",
        "items": items,
        "source_count": len({item.source_id for item in items}),
        "first_seen_at": min((item.published_at for item in items), default=cluster.first_seen_at),
        "last_seen_at": max((item.published_at for item in items), default=cluster.last_seen_at),
    }
    return templates.TemplateResponse(
        request,
        "event_detail.html",
        {"event": event, "stats": _stats(session)},
    )


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, session: Session = Depends(get_db)):
    jobs = list(session.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(50)))
    return templates.TemplateResponse(request, "jobs.html", {"jobs": jobs, "stats": _stats(session)})


@router.post("/jobs/run")
async def run_job_from_web(
    request: Request,
    job_type: str = Form("all"),
    session: Session = Depends(get_db),
):
    if job_type == "brief":
        generate_brief(session)
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=204, headers={"HX-Redirect": "/"})
        return RedirectResponse("/", status_code=303)
    await run_daily_job(session, request.app.state.rss_adapter)
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": "/jobs"})
    return RedirectResponse("/", status_code=303)
