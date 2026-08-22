from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.adapters.rss import FeedEntry
from app.models import Item, Source


TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(sorted(query)), ""))


def normalize_title(title: str) -> str:
    text = html.unescape(title).casefold()
    return re.sub(r"[^\w\u3400-\u9fff]+", "", text)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clean_html(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def ingest_entries(session: Session, source: Source, entries: list[FeedEntry]) -> list[Item]:
    inserted: list[Item] = []
    pending_url_fingerprints: set[str] = set()
    pending_external_ids: set[str] = set()
    for entry in entries:
        canonical_url = canonicalize_url(entry.url)
        parsed_url = urlsplit(canonical_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            continue
        url_fp = fingerprint(canonical_url)
        title_fp = fingerprint(normalize_title(entry.title))
        if url_fp in pending_url_fingerprints or entry.external_id in pending_external_ids:
            continue
        duplicate = session.scalar(
            select(Item).where(
                or_(
                    Item.url_fingerprint == url_fp,
                    (Item.source_id == source.id) & (Item.external_id == entry.external_id),
                )
            )
        )
        if duplicate:
            if entry.published_at and duplicate.status == "published_at_unknown":
                duplicate.published_at = entry.published_at
                duplicate.status = "new"
            continue
        effective_published_at = entry.published_at or datetime.now(timezone.utc)
        item = Item(
            source_id=source.id,
            external_id=entry.external_id,
            canonical_url=canonical_url,
            content_type="podcast" if source.type == "podcast" else "video" if source.type == "youtube" else "article",
            title=entry.title,
            author=entry.author,
            published_at=effective_published_at,
            raw_content=entry.content,
            clean_text=clean_html(entry.content),
            url_fingerprint=url_fp,
            title_fingerprint=title_fp,
            status="new" if entry.published_at else "published_at_unknown",
        )
        session.add(item)
        inserted.append(item)
        pending_url_fingerprints.add(url_fp)
        pending_external_ids.add(entry.external_id)
    source.last_success_at = datetime.now(timezone.utc)
    source.consecutive_failures = 0
    session.commit()
    for item in inserted:
        session.refresh(item)
    return inserted
