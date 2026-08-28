from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime

from fastapi.testclient import TestClient

from app.adapters.rss import RSSAdapter
from app.main import create_app


def current_feed() -> str:
    published_at = format_datetime(datetime.now(timezone.utc))
    return f"""<?xml version="1.0"?><rss version="2.0"><channel><title>Demo feed</title>
<item><guid>a1</guid><title>Useful AI release</title><link>https://example.com/a1</link>
<description>Primary evidence</description><pubDate>{published_at}</pubDate></item>
</channel></rss>"""


async def fake_fetch(_: str) -> str:
    return current_feed()


def test_source_crud_dashboard_and_offline_daily_job(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'test.db'}", RSSAdapter(fake_fetch))
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        created = client.post(
            "/api/sources",
            json={"title": "Demo", "url": "https://example.com/rss", "tags": ["ai"]},
        )
        assert created.status_code == 201
        source_id = created.json()["id"]
        assert client.patch(f"/api/sources/{source_id}", json={"priority": 5}).json()["priority"] == 5
        result = client.post("/api/jobs/run?kind=daily")
        assert result.status_code == 200
        assert result.json()["job"]["input_count"] == 1
        assert result.json()["job"]["output_count"] == 1
        assert len(client.get("/api/items").json()) == 1
        brief = client.get("/api/brief").json()
        assert brief["content_json"]["entries"][0]["evidence"][0]["item_id"]
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["counts"] == {"sources": 1, "enabled_sources": 1, "items": 1, "clusters": 1}
        assert client.delete(f"/api/sources/{source_id}").status_code == 204


def test_web_pages_and_forms_are_wired(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'web.db'}", RSSAdapter(fake_fetch))
    with TestClient(app) as client:
        for path in ("/", "/sources", "/items", "/jobs", "/static/app.css"):
            assert client.get(path).status_code == 200
        created = client.post(
            "/sources",
            data={"title": "Web Source", "url": "https://example.com/web.xml", "type": "rss", "priority": 3},
            follow_redirects=False,
        )
        assert created.status_code == 303
        page = client.get("/sources")
        assert "Web Source" in page.text
        source_id = client.get("/api/sources").json()[0]["id"]
        toggled = client.post(f"/sources/{source_id}/toggle", headers={"HX-Request": "true"})
        assert toggled.status_code == 200
        assert "已停用" in toggled.text


def test_htmx_job_redirects_to_visible_result(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'job-web.db'}", RSSAdapter(fake_fetch))
    with TestClient(app) as client:
        client.post(
            "/api/sources",
            json={"title": "Demo", "url": "https://example.com/rss"},
        )
        response = client.post(
            "/jobs/run",
            data={"job_type": "all"},
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 204
        assert response.headers["HX-Redirect"] == "/jobs"


def test_topic_category_filters_items_by_source_tags(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'categories.db'}", RSSAdapter(fake_fetch))
    with TestClient(app) as client:
        tech = client.post(
            "/api/sources",
            json={"title": "Tech Source", "url": "https://tech.example/rss", "tags": ["tech"]},
        ).json()
        film = client.post(
            "/api/sources",
            json={"title": "Film Source", "url": "https://film.example/rss", "tags": ["screenwriting"]},
        ).json()
        from datetime import datetime, timezone
        from app.models import Item
        from app.services.ingestion import fingerprint

        with app.state.session_factory() as session:
            session.add_all(
                [
                    Item(source_id=tech["id"], external_id="tech-1", canonical_url="https://tech.example/1", title="Tech Only", published_at=datetime.now(timezone.utc), url_fingerprint=fingerprint("https://tech.example/1"), title_fingerprint=fingerprint("Tech Only")),
                    Item(source_id=film["id"], external_id="film-1", canonical_url="https://film.example/1", title="Film Only", published_at=datetime.now(timezone.utc), url_fingerprint=fingerprint("https://film.example/1"), title_fingerprint=fingerprint("Film Only")),
                ]
            )
            session.commit()
        page = client.get("/items?category=film")
        assert page.status_code == 200
        assert "Film Only" in page.text
        assert "Tech Only" not in page.text
        assert 'class="active">影视创作' in page.text


def test_feedback_views_and_event_pages(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'feedback.db'}", RSSAdapter(fake_fetch))
    with TestClient(app) as client:
        client.post(
            "/api/sources",
            json={"title": "Demo", "url": "https://example.com/rss", "tags": ["tech"]},
        )
        client.post("/api/jobs/run?kind=daily")
        item = client.get("/api/items").json()[0]
        saved = client.post(
            f"/items/{item['id']}/action",
            data={"action": "save", "view": "all"},
            headers={"HX-Request": "true"},
        )
        assert saved.status_code == 200
        assert "已收藏" in saved.text
        assert '"saved": 1' in saved.headers["HX-Trigger"]
        assert "Useful AI release" in client.get("/items?view=saved").text
        api_state = client.post(f"/api/items/{item['id']}/feedback?action=read").json()["state"]
        assert api_state == {"read": True, "saved": True, "ignored": False}
        assert client.get("/events").status_code == 200
        with app.state.session_factory() as session:
            from sqlalchemy import select
            from app.models import EventCluster

            cluster_id = session.scalar(select(EventCluster.id))
        detail = client.get(f"/events/{cluster_id}")
        assert detail.status_code == 200
        assert "证据时间线" in detail.text
        with app.state.session_factory() as session:
            from sqlalchemy import select
            from app.models import Item

            stored_item = session.scalar(select(Item).where(Item.id == item["id"]))
            stored_item.status = "published_at_unknown"
            session.commit()
        assert "Useful AI release" not in client.get("/events").text


def test_mock_translation_api_is_cached_and_rendered_inline(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSLATION_PROVIDER", "mock")
    app = create_app(f"sqlite:///{tmp_path / 'translation.db'}", RSSAdapter(fake_fetch))
    with TestClient(app) as client:
        client.post(
            "/api/sources",
            json={"title": "Demo", "url": "https://example.com/rss"},
        )
        client.post("/api/jobs/run?kind=daily")
        item_id = client.get("/api/items").json()[0]["id"]

        first = client.post(f"/api/items/{item_id}/translate")
        assert first.status_code == 200
        assert first.json()["translated_title"] == "【模拟译文】Useful AI release"
        assert first.json()["is_mock"] is True
        assert first.json()["cached"] is False

        second = client.post(f"/api/items/{item_id}/translate")
        assert second.status_code == 200
        assert second.json()["cached"] is True

        page = client.get("/items?view=all")
        assert "【模拟译文】Useful AI release" in page.text
        assert "模拟译文 · 非真实翻译" in page.text
        assert 'title="查看中文翻译"' in page.text

        partial = client.post(
            f"/items/{item_id}/translate",
            headers={"HX-Request": "true"},
        )
        assert partial.status_code == 200
        assert "【模拟中文摘要】Primary evidence" in partial.text


def test_translation_routes_explain_missing_item_or_provider(tmp_path, monkeypatch):
    for key in ("OLLAMA_MODEL", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TRANSLATION_PROVIDER", "auto")
    app = create_app(f"sqlite:///{tmp_path / 'translation-off.db'}", RSSAdapter(fake_fetch))
    with TestClient(app) as client:
        client.post(
            "/api/sources",
            json={"title": "Demo", "url": "https://example.com/rss"},
        )
        client.post("/api/jobs/run?kind=daily")
        item_id = client.get("/api/items").json()[0]["id"]

        assert client.post("/api/items/9999/translate").status_code == 404
        unavailable = client.post(f"/api/items/{item_id}/translate")
        assert unavailable.status_code == 503
        assert "翻译模型" in unavailable.json()["detail"]

        partial = client.post(
            f"/items/{item_id}/translate",
            headers={"HX-Request": "true"},
        )
        assert partial.status_code == 200
        assert "暂时无法翻译" in partial.text
        assert "OLLAMA_MODEL" in partial.text
