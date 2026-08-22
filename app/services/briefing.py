from __future__ import annotations

import os
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo
import re

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Brief, EventCluster, Item
from app.services.feedback import DEFAULT_ITEM_STATE, item_feedback_states


def _entry(cluster: EventCluster, items: list[Item]) -> dict[str, Any]:
    items = sorted(items, key=lambda item: item.published_at, reverse=True)
    evidence = [
        {
            "item_id": item.id,
            "title": item.title,
            "url": item.canonical_url,
            "source_id": item.source_id,
            "published_at": item.published_at.isoformat(),
        }
        for item in items[:3]
    ]
    source_count = len({item.source_id for item in items})
    return {
        "cluster_id": cluster.id,
        "headline": cluster.title,
        "summary": (items[0].clean_text or items[0].title)[:280],
        "why_it_matters": f"{len(items)} 条内容、{source_count} 个独立来源共同覆盖此事件。",
        "confidence": round(min(0.95, 0.45 + source_count * 0.15 + len(items) * 0.05), 2),
        "evidence": evidence,
        "score": cluster.momentum_score,
    }


def _markdown_text(value: Any) -> str:
    return re.sub(r"[\r\n]+", " ", str(value)).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def render_markdown(brief_date: date, entries: list[dict[str, Any]]) -> str:
    lines = [f"# AI 信息雷达 · {brief_date.isoformat()}", "", f"今日筛选出 {len(entries)} 个重要事件。", ""]
    for index, entry in enumerate(entries, 1):
        lines.extend([
            f"## {index}. {_markdown_text(entry['headline'])}",
            "",
            _markdown_text(entry["summary"]),
            "",
            _markdown_text(entry["why_it_matters"]),
            "",
            "证据：",
        ])
        lines.extend(
            f"- [{_markdown_text(evidence['title'])}]({evidence['url']})" for evidence in entry["evidence"]
        )
        lines.append("")
    return "\n".join(lines)


def application_today() -> date:
    return datetime.now(ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Shanghai"))).date()


def generate_brief(session: Session, brief_date: date | None = None, limit: int = 10) -> Brief:
    target = brief_date or application_today()
    local_timezone = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Shanghai"))
    # SQLite stores DateTime values without an offset, so compare using naive UTC boundaries.
    start = datetime.combine(target, time.min, tzinfo=local_timezone).astimezone(timezone.utc).replace(tzinfo=None)
    end = datetime.combine(target, time.max, tzinfo=local_timezone).astimezone(timezone.utc).replace(tzinfo=None)
    clusters = session.scalars(
        select(EventCluster)
        .join(EventCluster.items)
        .where(
            Item.published_at.between(start, end),
            Item.status != "published_at_unknown",
        )
        .options(selectinload(EventCluster.items))
        .order_by(EventCluster.momentum_score.desc(), EventCluster.last_seen_at.desc())
        .distinct()
        .limit(limit * 4)
    ).all()
    all_item_ids = [item.id for cluster in clusters for item in cluster.items]
    states = item_feedback_states(session, all_item_ids)
    ranked_entries: list[dict[str, Any]] = []
    for cluster in clusters:
        current_items = [
            item
            for item in cluster.items
            if start <= item.published_at.replace(tzinfo=None) <= end
            and item.status != "published_at_unknown"
            and not states.get(item.id, DEFAULT_ITEM_STATE)["ignored"]
        ]
        if not current_items:
            continue
        entry = _entry(cluster, current_items)
        entry["score"] += sum(0.4 for item in current_items if states.get(item.id, DEFAULT_ITEM_STATE)["saved"])
        ranked_entries.append(entry)
    entries = sorted(ranked_entries, key=lambda entry: entry["score"], reverse=True)[:limit]
    content = {
        "date": target.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "deterministic-evidence-v1",
        "event_count": len(entries),
        "headline": "今天真正值得关注的变化",
        "executive_summary": f"从今日新增内容中压缩出 {len(entries)} 个事件；每个结论最多保留 3 条原始证据。",
        "entries": entries,
    }
    brief = session.scalar(select(Brief).where(Brief.brief_date == target))
    if brief is None:
        brief = Brief(brief_date=target)
        session.add(brief)
    brief.status = "ready"
    brief.content_json = content
    brief.content_markdown = render_markdown(target, entries)
    brief.model = "deterministic"
    session.commit()
    session.refresh(brief)
    return brief
