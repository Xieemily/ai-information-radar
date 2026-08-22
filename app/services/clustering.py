from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import EventCluster, Item


def title_tokens(title: str) -> set[str]:
    lowered = title.casefold()
    latin = re.findall(r"[a-z0-9]{2,}", lowered)
    han_chunks = re.findall(r"[\u3400-\u9fff]{2,}", lowered)
    han_bigrams = [chunk[index : index + 2] for chunk in han_chunks for index in range(len(chunk) - 1)]
    return set(latin + han_bigrams)


def title_similarity(left: str, right: str) -> float:
    a, b = title_tokens(left), title_tokens(right)
    if not a or not b:
        return 1.0 if left.casefold().strip() == right.casefold().strip() else 0.0
    return len(a & b) / len(a | b)


def cluster_unassigned_items(session: Session, lookback_hours: int = 72) -> list[EventCluster]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    items = session.scalars(
        select(Item)
        .where(Item.published_at >= cutoff, ~Item.clusters.any())
        .order_by(Item.published_at)
    ).all()
    clusters = session.scalars(
        select(EventCluster)
        .where(EventCluster.last_seen_at >= cutoff)
        .options(selectinload(EventCluster.items).selectinload(Item.source))
    ).all()
    changed: list[EventCluster] = []
    for item in items:
        best = max(clusters, key=lambda candidate: title_similarity(item.title, candidate.title), default=None)
        similarity = title_similarity(item.title, best.title) if best else 0.0
        if best is None or similarity < 0.28:
            best = EventCluster(
                title=item.title,
                first_seen_at=item.published_at,
                last_seen_at=item.published_at,
                items=[item],
            )
            session.add(best)
            clusters.append(best)
        else:
            best.items.append(item)
            best.first_seen_at = min(best.first_seen_at, item.published_at)
            best.last_seen_at = max(best.last_seen_at, item.published_at)
        distinct_sources = len({entry.source_id for entry in best.items})
        priority_bonus = max((entry.source.priority for entry in best.items if entry.source), default=1) * 0.2
        best.momentum_score = round(len(best.items) + max(0, distinct_sources - 1) * 1.5 + priority_bonus, 2)
        if best not in changed:
            changed.append(best)
    session.commit()
    return changed
