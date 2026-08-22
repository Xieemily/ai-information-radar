from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import calendar
import ipaddress
import os
import socket
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx


@dataclass(slots=True)
class FeedEntry:
    external_id: str
    title: str
    url: str
    author: str | None
    published_at: datetime | None
    content: str


FetchText = Callable[[str], Awaitable[str]]
PROXY_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def validate_public_http_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Feed URL must be a plain HTTP(S) URL without embedded credentials")
    if os.getenv("ALLOW_PRIVATE_SOURCES", "").lower() in {"1", "true", "yes"}:
        return
    if parsed.hostname.casefold() == "localhost":
        raise ValueError("Private or local feed addresses are disabled")
    try:
        literal_host = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal_host = None
    allow_proxy_fake_ips = os.getenv("ALLOW_PROXY_FAKE_IPS", "").lower() in {"1", "true", "yes"}
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError(f"Feed hostname could not be resolved: {parsed.hostname}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        # Clash and similar TUN proxies map public hostnames into the IANA
        # benchmarking range. This narrow opt-in keeps literal/private targets
        # blocked while allowing the proxy to route validated hostnames.
        if allow_proxy_fake_ips and literal_host is None and ip in PROXY_FAKE_IP_NETWORK:
            continue
        if not ip.is_global:
            raise ValueError("Private, loopback, link-local, or reserved feed addresses are disabled")


async def http_fetch_text(url: str) -> str:
    current = url
    timeout = float(os.getenv("FETCH_TIMEOUT_SECONDS", "15"))
    byte_limit = int(os.getenv("MAX_FEED_BYTES", str(5 * 1024 * 1024)))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(6):
            validate_public_http_url(current)
            async with client.stream("GET", current, headers={"User-Agent": "AI-Information-Radar/0.1"}) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        response.raise_for_status()
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > byte_limit:
                        raise ValueError(f"Feed response exceeded {byte_limit} bytes")
                return payload.decode(response.encoding or "utf-8", errors="replace")
    raise ValueError("Feed redirected too many times")


def _published(entry: dict) -> datetime | None:
    parsed_tuple = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed_tuple:
        try:
            return datetime.fromtimestamp(calendar.timegm(parsed_tuple), timezone.utc)
        except (TypeError, ValueError, OverflowError):
            pass
    value = entry.get("published") or entry.get("updated")
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None


class RSSAdapter:
    def __init__(self, fetch_text: FetchText = http_fetch_text):
        self.fetch_text = fetch_text

    async def fetch(self, url: str) -> tuple[str | None, list[FeedEntry]]:
        payload = await self.fetch_text(url)
        parsed = feedparser.parse(payload)
        if parsed.bozo and not parsed.entries:
            raise ValueError(f"Invalid feed: {parsed.bozo_exception}")
        title = parsed.feed.get("title")
        entries: list[FeedEntry] = []
        entry_limit = int(os.getenv("MAX_ITEMS_PER_SOURCE", "50"))
        content_limit = int(os.getenv("MAX_ITEM_CONTENT_CHARS", "50000"))
        for entry in parsed.entries[:entry_limit]:
            link = entry.get("link", "").strip()
            entry_title = entry.get("title", "Untitled").strip()
            if not link:
                continue
            content_blocks = entry.get("content") or []
            content = content_blocks[0].get("value", "") if content_blocks else entry.get("summary", "")
            entries.append(
                FeedEntry(
                    external_id=str(entry.get("id") or link),
                    title=entry_title,
                    url=link,
                    author=entry.get("author"),
                    published_at=_published(entry),
                    content=content[:content_limit],
                )
            )
        return title, entries
