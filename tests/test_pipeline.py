from __future__ import annotations

from datetime import date, datetime, timezone
import socket

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.adapters.rss import FeedEntry, RSSAdapter, validate_public_http_url
from app.database import Base
from app.models import EventCluster, Item, Source
from app.services.briefing import application_today, generate_brief
from app.services.clustering import cluster_unassigned_items, title_similarity
from app.services.importer import import_legacy_feeds, parse_feeds_text, parse_newsboat_feeds
from app.services.ingestion import canonicalize_url, ingest_entries
from app.services.obsidian import GENERATED_MARKER, export_brief_to_obsidian
from app.services.providers import OpenAICompatibleProvider, enrich_brief_if_configured
from app.services.feedback import record_item_action


def make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_parse_and_import_legacy_feeds(tmp_path):
    text = "# old list\nBBC|https://example.com/rss\nhttps://second.example/feed\n"
    assert parse_feeds_text(text) == [
        ("BBC", "https://example.com/rss"),
        ("second.example", "https://second.example/feed"),
    ]
    legacy = tmp_path / "feeds.txt"
    legacy.write_text(text, encoding="utf-8")
    session = make_session()
    assert import_legacy_feeds(session, legacy) == 2
    assert import_legacy_feeds(session, legacy) == 0


def test_newsboat_import_preserves_display_name_and_tags(tmp_path):
    text = 'https://example.com/rss.xml "~Example Feed" ai tech\n'
    assert parse_newsboat_feeds(text) == [
        ("Example Feed", "https://example.com/rss.xml", ["ai", "tech"]),
    ]
    legacy = tmp_path / "feeds.txt"
    legacy.write_text(text, encoding="utf-8")
    session = make_session()
    assert import_legacy_feeds(session, legacy) == 1
    source = session.scalars(select(Source)).one()
    assert source.title == "Example Feed"
    assert source.tags == ["ai", "tech"]


def test_fetch_url_guard_rejects_local_addresses(monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_SOURCES", raising=False)
    for url in ("http://127.0.0.1/feed", "http://localhost/feed", "file:///etc/passwd"):
        try:
            validate_public_http_url(url)
        except ValueError:
            pass
        else:
            raise AssertionError(f"local address should be rejected: {url}")


def test_fetch_url_guard_can_accept_proxy_fake_ip_for_hostname(monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_SOURCES", raising=False)
    monkeypatch.setenv("ALLOW_PROXY_FAKE_IPS", "true")
    monkeypatch.setattr(
        "app.adapters.rss.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.1.2", 443))],
    )
    validate_public_http_url("https://public.example/feed")
    try:
        validate_public_http_url("https://198.18.1.2/feed")
    except ValueError:
        pass
    else:
        raise AssertionError("literal benchmarking-range targets must remain blocked")


def test_rss_parse_and_fingerprint_deduplication():
    xml = """<?xml version="1.0"?><rss version="2.0"><channel><title>Demo</title>
    <item><guid>one</guid><title>AI ships today</title><link>https://example.com/a?utm_source=x</link>
    <description><![CDATA[<p>Evidence text</p>]]></description><pubDate>Sun, 23 Aug 2026 08:00:00 GMT</pubDate></item>
    </channel></rss>"""

    async def fake_fetch(_: str) -> str:
        return xml

    import asyncio

    title, entries = asyncio.run(RSSAdapter(fake_fetch).fetch("https://example.com/rss"))
    assert title == "Demo"
    session = make_session()
    source = Source(title="Demo", url="https://example.com/rss")
    session.add(source)
    session.commit()
    assert len(ingest_entries(session, source, entries)) == 1
    assert len(ingest_entries(session, source, entries)) == 0
    item = session.scalars(select(Item)).one()
    assert item.canonical_url == "https://example.com/a"
    assert item.clean_text == "Evidence text"


def test_missing_feed_date_is_explicitly_unknown():
    xml = """<?xml version="1.0"?><rss version="2.0"><channel><title>Demo</title>
    <item><guid>undated</guid><title>Undated item</title><link>https://example.com/undated</link></item>
    </channel></rss>"""

    async def fake_fetch(_: str) -> str:
        return xml

    import asyncio

    _, entries = asyncio.run(RSSAdapter(fake_fetch).fetch("https://example.com/rss"))
    assert entries[0].published_at is None
    session = make_session()
    source = Source(title="Demo", url="https://example.com/rss")
    session.add(source)
    session.commit()
    item = ingest_entries(session, source, entries)[0]
    assert item.status == "published_at_unknown"


def test_ingestion_deduplicates_urls_within_one_unflushed_batch():
    session = make_session()
    source = Source(title="Demo", url="https://example.com/rss")
    session.add(source)
    session.commit()
    timestamp = datetime(2026, 8, 23, 8, tzinfo=timezone.utc)
    entries = [
        FeedEntry("one", "First", "https://example.com/same", None, timestamp, "One"),
        FeedEntry("two", "Second", "https://example.com/same", None, timestamp, "Two"),
    ]
    assert len(ingest_entries(session, source, entries)) == 1
    assert len(list(session.scalars(select(Item)))) == 1


def test_canonical_url_and_lightweight_cluster_brief():
    assert canonicalize_url("HTTPS://EXAMPLE.COM/a/?b=2&utm_campaign=x&a=1#part") == "https://example.com/a?a=1&b=2"
    assert title_similarity("OpenAI 发布新模型", "OpenAI 新模型正式发布") > 0.28
    session = make_session()
    one = Source(title="One", url="https://one.example/rss", priority=5)
    two = Source(title="Two", url="https://two.example/rss")
    session.add_all([one, two])
    session.commit()
    timestamp = datetime(2026, 8, 23, 8, tzinfo=timezone.utc)
    ingest_entries(session, one, [FeedEntry("1", "OpenAI 发布新模型", "https://one.example/1", None, timestamp, "模型能力提升")])
    ingest_entries(session, two, [FeedEntry("2", "OpenAI 新模型正式发布", "https://two.example/2", None, timestamp, "第二来源确认")])
    clusters = cluster_unassigned_items(session, lookback_hours=24 * 365)
    assert len(clusters) == 1
    assert len(clusters[0].items) == 2
    brief = generate_brief(session, date(2026, 8, 23))
    assert brief.model == "deterministic"
    assert len(brief.content_json["entries"][0]["evidence"]) == 2
    assert "https://one.example/1" in brief.content_markdown


def test_obsidian_export_is_atomic_idempotent_and_protects_user_file(tmp_path):
    session = make_session()
    brief = generate_brief(session, date(2026, 8, 23))
    archive, daily = export_brief_to_obsidian(brief, tmp_path)
    export_brief_to_obsidian(brief, tmp_path)
    assert GENERATED_MARKER in archive.read_text(encoding="utf-8")
    daily_text = daily.read_text(encoding="utf-8")
    assert daily_text.count("**今日资讯**\n[[今日资讯 2026-08-23]]") == 1
    archive.write_text("my own note", encoding="utf-8")
    try:
        export_brief_to_obsidian(brief, tmp_path)
    except FileExistsError:
        pass
    else:
        raise AssertionError("non-generated note should be protected")


def test_optional_llm_cannot_replace_server_side_evidence(monkeypatch):
    import asyncio

    session = make_session()
    source = Source(title="Evidence", url="https://evidence.example/rss")
    session.add(source)
    session.commit()
    timestamp = datetime(2026, 8, 23, 8, tzinfo=timezone.utc)
    ingest_entries(
        session,
        source,
        [FeedEntry("e1", "Evidence-bound event", "https://evidence.example/1", None, timestamp, "Original")],
    )
    cluster_unassigned_items(session, lookback_hours=24 * 365)
    brief = generate_brief(session, date(2026, 8, 23))
    original = brief.content_json["entries"][0]

    async def safe_enrich(_self, _draft):
        return {
            "entries": [
                {
                    **original,
                    "summary": "更清晰的摘要",
                    "evidence": [{**original["evidence"][0], "url": "https://attacker.example/replace"}],
                }
            ]
        }

    monkeypatch.setenv("LLM_BASE_URL", "http://model.example/v1")
    monkeypatch.setenv("LLM_MODEL", "local-model")
    monkeypatch.setattr(OpenAICompatibleProvider, "enrich", safe_enrich)
    enriched = asyncio.run(enrich_brief_if_configured(session, brief))
    evidence = enriched.content_json["entries"][0]["evidence"][0]
    assert enriched.model == "local-model"
    assert evidence["url"] == "https://evidence.example/1"


def test_brief_caps_events_and_excludes_unknown_or_ignored_items():
    session = make_session()
    now = datetime.now(timezone.utc)
    for index in range(13):
        source = Source(title=f"Source {index}", url=f"https://source-{index}.example/rss")
        session.add(source)
        session.flush()
        item = Item(
            source_id=source.id,
            external_id=str(index),
            canonical_url=f"https://source-{index}.example/{index}",
            title=f"Unique event {index}",
            published_at=now,
            clean_text=f"Evidence {index}",
            url_fingerprint=f"{index:064d}",
            title_fingerprint=f"{index + 100:064d}",
            status="published_at_unknown" if index == 0 else "new",
        )
        cluster = EventCluster(title=item.title, items=[item], first_seen_at=now, last_seen_at=now, momentum_score=float(index))
        session.add(cluster)
        if index == 1:
            session.flush()
            record_item_action(session, item, "ignore")
    session.commit()
    brief = generate_brief(session, application_today())
    assert len(brief.content_json["entries"]) == 10
    evidence_ids = {entry["evidence"][0]["item_id"] for entry in brief.content_json["entries"]}
    excluded_ids = {
        item.id
        for item in session.scalars(select(Item).where(Item.status == "published_at_unknown"))
    }
    assert not evidence_ids & excluded_ids
