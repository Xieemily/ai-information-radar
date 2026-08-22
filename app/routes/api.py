from __future__ import annotations

from datetime import date
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Brief, EventCluster, Item, JobRun, Source
from app.schemas import BriefRead, ItemRead, JobRead, SourceCreate, SourceRead, SourceUpdate
from app.services.briefing import generate_brief
from app.services.importer import import_legacy_feeds
from app.services.jobs import run_collection_job, run_daily_job
from app.services.obsidian import export_brief_to_obsidian
from app.services.feedback import record_item_action
from app.services.translation import TranslationNotConfigured, translate_item


router = APIRouter(prefix="/api")


@router.get("/dashboard")
def dashboard(session: Session = Depends(get_db)) -> dict:
    latest_brief = session.scalar(select(Brief).order_by(Brief.brief_date.desc()).limit(1))
    latest_job = session.scalar(select(JobRun).order_by(JobRun.started_at.desc()).limit(1))
    return {
        "counts": {
            "sources": session.scalar(select(func.count(Source.id))) or 0,
            "enabled_sources": session.scalar(select(func.count(Source.id)).where(Source.enabled.is_(True))) or 0,
            "items": session.scalar(select(func.count(Item.id))) or 0,
            "clusters": session.scalar(select(func.count(EventCluster.id))) or 0,
        },
        "latest_brief": BriefRead.model_validate(latest_brief).model_dump(mode="json") if latest_brief else None,
        "latest_job": JobRead.model_validate(latest_job).model_dump(mode="json") if latest_job else None,
    }


@router.get("/sources", response_model=list[SourceRead])
def list_sources(session: Session = Depends(get_db)) -> list[Source]:
    return list(session.scalars(select(Source).order_by(Source.priority.desc(), Source.title)))


@router.get("/sources/{source_id}", response_model=SourceRead)
def get_source(source_id: int, session: Session = Depends(get_db)) -> Source:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.post("/sources", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
def create_source(payload: SourceCreate, session: Session = Depends(get_db)) -> Source:
    source = Source(**payload.model_dump())
    session.add(source)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Source URL already exists") from exc
    session.refresh(source)
    return source


@router.patch("/sources/{source_id}", response_model=SourceRead)
def update_source(source_id: int, payload: SourceUpdate, session: Session = Depends(get_db)) -> Source:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(source, field, value)
    session.commit()
    session.refresh(source)
    return source


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, session: Session = Depends(get_db)) -> None:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    session.delete(source)
    session.commit()


@router.post("/sources/import")
def import_sources(session: Session = Depends(get_db)) -> dict[str, int]:
    configured_path = os.getenv("LEGACY_FEEDS_FILE")
    if not configured_path:
        raise HTTPException(status_code=503, detail="LEGACY_FEEDS_FILE is not configured")
    candidate = Path(configured_path).expanduser().resolve()
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Configured legacy feeds file was not found")
    return {"created": import_legacy_feeds(session, candidate)}


@router.get("/items", response_model=list[ItemRead])
def list_items(
    source_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> list[Item]:
    statement = select(Item)
    if source_id is not None:
        statement = statement.where(Item.source_id == source_id)
    return list(session.scalars(statement.order_by(Item.published_at.desc()).offset(offset).limit(limit)))


@router.post("/items/{item_id}/feedback")
def item_feedback(item_id: int, action: str, session: Session = Depends(get_db)) -> dict:
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    try:
        state = record_item_action(session, item, action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item_id": item_id, "state": state}


@router.post("/items/{item_id}/translate")
async def translate_item_api(
    item_id: int,
    force: bool = False,
    session: Session = Depends(get_db),
) -> dict:
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    try:
        outcome = await translate_item(session, item, force=force)
    except TranslationNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    translation = outcome.translation
    return {
        "item_id": item_id,
        "target_language": translation.target_language,
        "translated_title": translation.translated_title,
        "translated_text": translation.translated_text,
        "provider": translation.provider,
        "is_mock": translation.is_mock,
        "cached": outcome.cached,
    }


@router.get("/jobs", response_model=list[JobRead])
def list_jobs(limit: int = Query(default=20, ge=1, le=100), session: Session = Depends(get_db)) -> list[JobRun]:
    return list(session.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)))


@router.post("/jobs/run")
async def run_job(request: Request, kind: str = "daily", session: Session = Depends(get_db)) -> dict:
    adapter = request.app.state.rss_adapter
    if kind == "collect":
        job = await run_collection_job(session, adapter)
        return {"job": JobRead.model_validate(job).model_dump(mode="json")}
    if kind == "daily":
        job, brief_id = await run_daily_job(session, adapter)
        return {"job": JobRead.model_validate(job).model_dump(mode="json"), "brief_id": brief_id}
    raise HTTPException(status_code=400, detail="kind must be collect or daily")


@router.post("/brief", response_model=BriefRead)
def create_brief(brief_date: date | None = None, session: Session = Depends(get_db)) -> Brief:
    return generate_brief(session, brief_date)


@router.get("/brief", response_model=BriefRead)
def get_brief(brief_date: date | None = None, session: Session = Depends(get_db)) -> Brief:
    if brief_date:
        brief = session.scalar(select(Brief).where(Brief.brief_date == brief_date))
    else:
        brief = session.scalar(select(Brief).order_by(Brief.brief_date.desc()).limit(1))
    if brief is None:
        raise HTTPException(status_code=404, detail="Brief not found")
    return brief


@router.post("/brief/export")
def export_brief(
    brief_date: date | None = None,
    session: Session = Depends(get_db),
) -> dict[str, str]:
    brief = (
        session.scalar(select(Brief).where(Brief.brief_date == brief_date))
        if brief_date
        else session.scalar(select(Brief).order_by(Brief.brief_date.desc()).limit(1))
    )
    if brief is None:
        raise HTTPException(status_code=404, detail="Brief not found")
    vault_path = os.getenv("OBSIDIAN_VAULT_PATH")
    if not vault_path:
        raise HTTPException(status_code=503, detail="OBSIDIAN_VAULT_PATH is not configured")
    try:
        archive, daily = export_brief_to_obsidian(brief, vault_path, force=False)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"archive_path": str(archive), "daily_path": str(daily)}
