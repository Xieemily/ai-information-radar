from __future__ import annotations

from pathlib import Path
import shlex
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Source


def parse_feeds_text(text: str) -> list[tuple[str, str]]:
    """Parse legacy lines in `url`, `title|url`, or whitespace-separated form."""
    feeds: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            title, url = (part.strip() for part in line.split("|", 1))
        else:
            parts = line.split(maxsplit=1)
            if len(parts) == 2 and parts[1].startswith(("http://", "https://")):
                title, url = parts
            else:
                url = line
                title = urlsplit(url).hostname or url
        if url.startswith(("http://", "https://")):
            feeds.append((title or url, url))
    return feeds


def parse_newsboat_feeds(text: str) -> list[tuple[str, str, list[str]]]:
    """Parse full Newsboat lines while preserving display name and tags."""
    feeds: list[tuple[str, str, list[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            title, url = (part.strip() for part in line.split("|", 1))
            if url.startswith(("http://", "https://")):
                feeds.append((title or urlsplit(url).hostname or url, url, []))
            continue
        try:
            parts = shlex.split(line, comments=True)
        except ValueError:
            continue
        if not parts or not parts[0].startswith(("http://", "https://")):
            continue
        url = parts[0]
        title = urlsplit(url).hostname or url
        tags: list[str] = []
        for part in parts[1:]:
            if part.startswith("~") and len(part) > 1:
                title = part[1:]
            else:
                tags.append(part)
        feeds.append((title, url, tags))
    return feeds


def import_legacy_feeds(session: Session, path: str | Path = "feeds.txt") -> int:
    file_path = Path(path)
    if not file_path.is_file():
        return 0
    created = 0
    for title, url, tags in parse_newsboat_feeds(file_path.read_text(encoding="utf-8")):
        if session.scalar(select(Source.id).where(Source.url == url)):
            continue
        session.add(Source(title=title, url=url, type="rss", tags=tags))
        created += 1
    session.commit()
    return created
