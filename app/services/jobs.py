from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.rss import RSSAdapter
from app.models import JobRun, Source
from app.services.briefing import generate_brief
from app.services.clustering import cluster_unassigned_items
from app.services.ingestion import ingest_entries
from app.services.providers import enrich_brief_if_configured


async def run_collection_job(session: Session, adapter: RSSAdapter) -> JobRun:
    job = JobRun(job_type="collect", status="running")
    session.add(job)
    session.commit()
    session.refresh(job)
    sources = session.scalars(
        select(Source).where(Source.enabled.is_(True), Source.type.in_(["rss", "podcast", "youtube"]))
    ).all()
    job.input_count = len(sources)
    session.commit()
    errors: list[str] = []
    inserted_count = 0
    for source in sources:
        try:
            discovered_title, entries = await adapter.fetch(source.url)
            if discovered_title and source.title == source.url:
                source.title = discovered_title
            inserted_count += len(ingest_entries(session, source, entries))
        except Exception as exc:  # one broken source must not stop the daily radar
            session.rollback()
            source = session.get(Source, source.id)
            if source:
                source.consecutive_failures += 1
            detail = str(exc).strip() or exc.__class__.__name__
            errors.append(f"source {source.id if source else '?'}: {detail}")
            session.commit()
    try:
        cluster_unassigned_items(session)
    except Exception as exc:
        session.rollback()
        errors.append(f"clustering failed: {exc}")
    job = session.get(JobRun, job.id)
    assert job is not None
    job.output_count = inserted_count
    job.finished_at = datetime.now(timezone.utc)
    job.status = "partial" if errors and inserted_count else "failed" if errors else "succeeded"
    job.error = "\n".join(errors) or None
    session.commit()
    session.refresh(job)
    return job


async def run_daily_job(session: Session, adapter: RSSAdapter) -> tuple[JobRun, int]:
    job = await run_collection_job(session, adapter)
    try:
        brief = generate_brief(session)
    except Exception as exc:
        session.rollback()
        job = session.get(JobRun, job.id)
        assert job is not None
        job.status = "failed"
        job.error = "\n".join(filter(None, [job.error, f"Brief generation failed: {exc}"]))
        job.finished_at = datetime.now(timezone.utc)
        session.commit()
        raise
    try:
        brief = await enrich_brief_if_configured(session, brief)
    except Exception as exc:
        job = session.get(JobRun, job.id)
        assert job is not None
        job.status = "partial"
        job.error = "\n".join(filter(None, [job.error, f"LLM enhancement failed: {exc}"]))
        session.commit()
    return job, brief.id
