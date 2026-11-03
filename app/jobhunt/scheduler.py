"""Scheduler for per-source background polling of job sources."""

import asyncio
from datetime import datetime, timedelta, timezone
import os
import time
from typing import Any, List, Optional, Tuple
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt.bootstrap import send_welcome_batch
from jobhunt.core.config import settings
from jobhunt.core.db import async_session_factory
from jobhunt.core.logging import logger, setup_logging
from jobhunt.core.models import Company, Source
from jobhunt.notify.telegram import TelegramNotifier
from jobhunt.pipeline.dedupe import process_source
from jobhunt.sources.amazon import AmazonSource
from jobhunt.sources.ashby import AshbySource
from jobhunt.sources.greenhouse import GreenhouseSource
from jobhunt.sources.lever import LeverSource
from jobhunt.sources.oracle_hcm import OracleHCMSource
from jobhunt.sources.recruitee import RecruiteeSource
from jobhunt.sources.smartrecruiters import SmartRecruitersSource
from jobhunt.sources.teamtailor import TeamtailorSource
from jobhunt.sources.workday import WorkdaySource
from jobhunt.sources.sitemap_jsonld import SitemapJSONLDSource
from jobhunt.sources.simplify_github import SimplifyGitHubSource
from jobhunt.sources.jobspy_src import JobSpySource
from jobhunt.sources.browser import PlaywrightSource

setup_logging()

scheduler: Optional[AsyncIOScheduler] = None
_poll_semaphore = asyncio.Semaphore(1)


def build_adapter(source_row: Source, company_name: str) -> Optional[Any]:
    """Instantiate adapter object from a Source ORM row."""
    cfg = source_row.config or {}
    stype = source_row.type
    slug = cfg.get("slug", "")
    adapter = None

    if stype == "greenhouse":
        adapter = GreenhouseSource(slug=slug, company_name=company_name)
    elif stype == "lever":
        adapter = LeverSource(slug=slug, company_name=company_name)
    elif stype == "ashby":
        adapter = AshbySource(slug=slug, company_name=company_name)
    elif stype == "smartrecruiters":
        adapter = SmartRecruitersSource(slug=slug, company_name=company_name)
    elif stype == "recruitee":
        adapter = RecruiteeSource(slug=slug, company_name=company_name)
    elif stype == "teamtailor":
        adapter = TeamtailorSource(slug=slug, company_name=company_name)
    elif stype == "amazon":
        adapter = AmazonSource(company_name=company_name, slug=slug or "amazon")
    elif stype == "oracle_hcm":
        adapter = OracleHCMSource(
            tenant=cfg.get("tenant", ""),
            site=cfg.get("site", ""),
            company_name=company_name,
            slug=slug or None,
        )
    elif stype == "workday":
        adapter = WorkdaySource(
            tenant=cfg.get("tenant", slug),
            wd=cfg.get("wd", "wd3"),
            site=cfg.get("site", "External_Career_Site"),
            company_name=company_name,
        )
    elif stype == "sitemap_jsonld":
        adapter = SitemapJSONLDSource(
            company_name=company_name,
            sitemap_url=cfg.get("sitemap_url", ""),
            url_filter=cfg.get("url_filter", "/job/"),
            max_first_run=cfg.get("max_first_run", 500),
            slug=slug or "sitemap",
        )
    elif stype == "simplify_github":
        adapter = SimplifyGitHubSource(
            repo_url=cfg.get(
                "repo_url",
                "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
            ),
            max_first_run=cfg.get("max_first_run", 300),
            company_name=company_name,
            slug=slug or "simplify",
        )
    elif stype == "jobspy":
        adapter = JobSpySource(
            search_term=cfg.get("search_term", "software engineer intern"),
            location=cfg.get("location", "United Kingdom"),
            sites=cfg.get("sites", ["indeed", "linkedin"]),
            max_first_run=cfg.get("max_first_run", 200),
            hours_old=cfg.get("hours_old", 48),
            company_name=company_name,
            slug=slug or "jobspy",
        )
    elif stype == "playwright":
        adapter = PlaywrightSource(
            company_name=company_name,
            start_url=cfg.get("start_url", ""),
            xhr_pattern=cfg.get("xhr_pattern"),
            wait_selector=cfg.get("wait_selector"),
            max_first_run=cfg.get("max_first_run", 100),
            slug=slug,
        )
    else:
        logger.warning("unknown_source_type", type=stype, id=source_row.id)
        return None

    if adapter:
        adapter.source_db_id = source_row.id
        adapter.is_baselined = bool(source_row.baselined and source_row.last_ok is not None)
        adapter.priority = source_row.priority
        adapter.last_ok = source_row.last_ok
    return adapter


async def load_active_sources(
    session, priority_lte: Optional[int] = None
) -> List[Tuple[Any, int, bool]]:
    """Loads active sources from DB and instantiates adapter objects."""
    stmt = (
        select(Source, Company.name)
        .join(Company, Source.company_id == Company.id)
        .where(Source.status == "active")
    )
    if priority_lte is not None:
        stmt = stmt.where(Source.priority <= priority_lte)
    stmt = stmt.order_by(Source.priority.asc(), Source.id.asc())
    res = await session.execute(stmt)
    active = []
    for source_row, company_name in res.all():
        adapter = build_adapter(source_row, company_name)
        if adapter:
            active.append((adapter, source_row.id, adapter.is_baselined))
    return active


async def send_source_failure_alert(source, reason: str):
    """Send health alert when an adapter fails completely."""
    token = settings.telegram_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = settings.telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        notifier = TelegramNotifier(token, chat_id)
        msg = f"Source {source.type}:{getattr(source, 'slug', '')} broken: {reason}"
        await notifier.send_text(msg)
    except Exception as e:
        logger.error("Failed to send source failure alert", error=str(e))


async def notify_new_jobs_from_source(source: Source, cycle_id: str, db: AsyncSession):
    """After a source poll, notify on new matching jobs."""
    query = """
    SELECT j.*, c.name AS company_name FROM jobs j
    JOIN companies c ON c.id = j.company_id
    WHERE j.inserted_cycle_id = :cycle_id
      AND j.tier = 'high'
      AND j.score >= 40
      AND j.is_uk = true
      AND j.is_test = false
      AND j.duplicate_of IS NULL
      AND j.role_family IN ('swe', 'ml_ai', 'data')
      AND (
        (j.posted_at IS NOT NULL AND j.posted_at > now() - INTERVAL '7 days')
        OR (j.posted_at IS NULL AND j.first_seen > now() - INTERVAL '1 day')
      )
      AND j.id NOT IN (SELECT job_id FROM notifications WHERE channel='telegram')
    ORDER BY j.score DESC
    LIMIT 10
    """
    res = await db.execute(text(query), {"cycle_id": cycle_id})
    jobs = res.mappings().all()
    if not jobs:
        return

    token = settings.telegram_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = settings.telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return

    notifier = TelegramNotifier(token, chat_id)
    for job in jobs:
        ok = await notifier.send_job(dict(job), job["company_name"])
        if ok:
            await db.execute(
                text(
                    "INSERT INTO notifications (job_id, channel, sent_at) "
                    "VALUES (:job_id, 'telegram', now())"
                ),
                {"job_id": job["id"]},
            )
    await db.commit()

    if len(jobs) >= 10:
        logger.warning("cycle_alert_cap_hit", source=source.id, cap=10)


async def _poll_source_impl(source_id: int, db: AsyncSession):
    stmt = (
        select(Source, Company.name)
        .join(Company, Source.company_id == Company.id)
        .where(Source.id == source_id)
    )
    res = await db.execute(stmt)
    row = res.one_or_none()
    if not row:
        return
    source, company_name = row
    adapter = build_adapter(source, company_name)
    if not adapter:
        return

    now_utc = datetime.now(timezone.utc)
    cycle_id = f"poll_{source_id}_{int(now_utc.timestamp())}"

    # Check if this is the first poll ever
    is_first_poll = source.last_ok is None
    start = time.perf_counter()

    try:
        # Poll and insert (with insert filter from Part A)
        inserted, skipped = await process_source(adapter, db, cycle_id)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

        # Update source state
        source.last_ok = datetime.now(timezone.utc)
        source.baselined = True  # mark as polled at least once
        source.fail_streak = 0
        source.last_error = None
        await db.commit()

        # Log
        logger.info(
            "source_polled",
            source=f"{source.type}:{getattr(adapter, 'slug', '')}",
            inserted=inserted,
            skipped=skipped,
            is_first_poll=is_first_poll,
            elapsed_ms=elapsed_ms,
        )

        # Notify on new jobs (unless this was the first poll → suppress)
        if not is_first_poll:
            await notify_new_jobs_from_source(source, cycle_id, db)

    except Exception as e:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error(
            "source_poll_failed",
            source=f"{source.type}:{getattr(adapter, 'slug', '')}",
            error=str(e),
            elapsed_ms=elapsed_ms,
        )
        try:
            source.last_error = str(e)
            source.fail_streak += 1
            became_broken = False
            if source.fail_streak >= 3 and source.status != "broken":
                source.status = "broken"
                became_broken = True
            await db.commit()
            if became_broken:
                await send_source_failure_alert(adapter, str(e))
        except Exception as db_err:
            logger.error("error_updating_source_failure", error=str(db_err))


async def poll_source(source_id: int, db: Optional[AsyncSession] = None):
    """Poll one source. Called by APScheduler on its own timer."""
    async with _poll_semaphore:
        if db is not None:
            return await _poll_source_impl(source_id, db)
        async with async_session_factory() as session:
            return await _poll_source_impl(source_id, session)


def schedule_source(sched: AsyncIOScheduler, source: Source):
    """Register a per-source APScheduler job."""
    interval = {1: 15, 2: 60, 3: 180}[source.priority]
    now_utc = datetime.now(timezone.utc)

    # First-run time: P1 immediately, P2 at +60m, P3 at +180m
    if source.last_ok is not None:
        # Already polled at least once (idempotent restart) - use interval
        next_run = now_utc + timedelta(minutes=interval)
    elif source.priority == 1:
        next_run = now_utc + timedelta(seconds=5)  # almost immediately
    elif source.priority == 2:
        next_run = now_utc + timedelta(minutes=60)
    else:  # priority 3
        next_run = now_utc + timedelta(minutes=180)

    sched.add_job(
        poll_source,
        "interval",
        minutes=interval,
        args=[source.id],
        id=f"source_{source.id}",
        next_run_time=next_run,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )


async def send_welcome_batch_when_ready(db: Optional[AsyncSession] = None):
    """Fires every 30s. Self-removes once P1 all polled + batch sent."""
    async def _check(session: AsyncSession):
        result = await session.execute(
            select(func.count()).select_from(Source).where(
                Source.priority == 1,
                Source.status == "active",
                Source.last_ok.is_(None),
            )
        )
        unpolled_p1 = result.scalar()

        if unpolled_p1 == 0:
            # All P1 sources polled at least once. Send welcome batch.
            await send_welcome_batch(session, limit=3)
            # Self-remove
            from jobhunt.scheduler import scheduler as sched
            if sched and sched.get_job("welcome_batch_checker"):
                sched.remove_job("welcome_batch_checker")
            logger.info("welcome_batch_checker_completed")

    if db is not None:
        await _check(db)
    else:
        async with async_session_factory() as session:
            await _check(session)


async def start_scheduler(
    db: Optional[AsyncSession] = None, dry_run: bool = False, once: bool = False
):
    global scheduler
    scheduler = AsyncIOScheduler()

    # Load all active sources
    if db is not None:
        res = await db.execute(
            select(Source)
            .where(Source.status == "active")
            .order_by(Source.priority.asc(), Source.id.asc())
        )
        sources = res.scalars().all()
    else:
        async with async_session_factory() as session:
            res = await session.execute(
                select(Source)
                .where(Source.status == "active")
                .order_by(Source.priority.asc(), Source.id.asc())
            )
            sources = res.scalars().all()

    for source in sources:
        schedule_source(scheduler, source)

    # Schedule welcome batch checker (fires every 30s, self-disables
    # once P1 sources all polled)
    scheduler.add_job(
        send_welcome_batch_when_ready,
        "interval",
        seconds=30,
        id="welcome_batch_checker",
        max_instances=1,
    )

    scheduler.start()
    logger.info(
        "scheduler_started",
        source_count=len(sources),
        p1_count=sum(1 for s in sources if s.priority == 1),
    )

    if once or dry_run:
        return

    # Block forever
    await asyncio.Event().wait()
