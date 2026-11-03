import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt.core.config import settings
from jobhunt.core.logging import logger
from jobhunt.core.models import Company, Job, Notification, Source
from jobhunt.notify.telegram import TelegramNotifier


CRITICAL_COMPANIES = {
    # UK tech tier A (small boards, high signal)
    "Monzo",
    "Stripe",
    "Palantir",
    "Cloudflare",
    "Graphcore",
    "Databricks",
    "Anthropic",
    "Qube Research & Technologies",
    "Jane Street",
    "Hudson River Trading",
    "Jump Trading",
    "Squarepoint Capital",
    "Tower Research Capital",
    # Financial tier B
    "American Express",
    "JPMorgan Chase",
    "BNY",
    "Barclays",
    # UK mid-tier
    "ASOS",
    "Wise",
    "Farfetch",
    # Defence / sitemap
    "BAE Systems",
}


def compute_source_priority(
    company_tier: Optional[str],
    ats_platform: str,
    company_name: Optional[str] = None,
    slug: Optional[str] = None,
) -> int:
    """Returns 1 (critical), 2 (important), 3 (bulk).
    P1 target: ~24 sources (3 safety/sitemap + 21 companies), 2-3 min baseline.
    """
    ats_norm = (ats_platform or "").lower()

    # Safety net always P1
    if ats_norm in ("simplify_github", "jobspy"):
        return 1

    # Sitemap always P1 (BAE, BBC, Unilever)
    if ats_norm == "sitemap_jsonld":
        return 1

    # Handpicked list gets P1
    if company_name in CRITICAL_COMPANIES:
        return 1

    # Workday / Oracle / Amazon: slow, always P2
    if ats_norm in ("workday", "oracle_hcm", "oracle", "amazon"):
        return 2

    # Playwright: broken
    if ats_norm == "playwright":
        return 3

    # Everything else: tier-based
    tier = (company_tier or "").upper()
    return {"A": 2, "B": 2, "C": 3}.get(tier, 3)


async def seed_companies_from_yaml(yaml_path: str, db: AsyncSession) -> Dict[str, int]:
    """Reads companies.yaml. For each company:
      - If not in companies table: INSERT
      - Set tier, hubs, sector, clearance from YAML
    Returns {"companies_created": N, "companies_existed": M}
    Idempotent — running twice doesn't duplicate.
    """
    path = Path(yaml_path)
    if not path.is_file():
        raise FileNotFoundError(f"Companies file not found at {yaml_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    raw_companies: List[Dict[str, Any]] = (
        data.get("companies", []) if isinstance(data, dict) else []
    )

    # Preload existing company names
    stmt = select(Company.name, Company)
    res = await db.execute(stmt)
    existing_companies = {row[0]: row[1] for row in res.all()}

    created = 0
    existed = 0

    # Include sentinel companies if not already in DB
    sentinels = [
        {"name": "Simplify (aggregator)", "tier": None, "hubs": ["uk", "london"], "sector": None, "clearance": False},
        {"name": "JobSpy (LinkedIn/Indeed)", "tier": None, "hubs": ["uk", "london"], "sector": None, "clearance": False},
    ]

    all_companies = list(raw_companies)
    for s in sentinels:
        if not any(c.get("name") == s["name"] for c in all_companies):
            all_companies.append(s)

    for comp_data in all_companies:
        name = comp_data.get("name")
        if not name:
            continue

        tier = comp_data.get("tier")
        hubs = comp_data.get("hubs")
        sector = comp_data.get("sector")
        clearance = bool(comp_data.get("clearance", False))

        if name in existing_companies:
            company = existing_companies[name]
            updated = False
            if company.tier != tier:
                company.tier = tier
                updated = True
            if company.hubs != hubs:
                company.hubs = hubs
                updated = True
            if company.sector != sector:
                company.sector = sector
                updated = True
            if company.clearance != clearance:
                company.clearance = clearance
                updated = True
            existed += 1
        else:
            new_comp = Company(
                name=name,
                tier=tier,
                hubs=hubs,
                sector=sector,
                clearance=clearance,
            )
            db.add(new_comp)
            existing_companies[name] = new_comp
            created += 1

    await db.commit()
    return {"companies_created": created, "companies_existed": existed}


async def seed_sources_from_yaml(yaml_path: str, db: AsyncSession) -> Dict[str, int]:
    """Reads companies.yaml. For each company with feed_url_verified=true
    and ats_platform not in (None, '', 'none', 'playwright'):
      - If no source row exists for (company_id, type): INSERT with
        baselined=false, interval_min=15, status='active'
      - config extracted from company.ats block (slug, tenant, site, etc.)
      - priority assigned using compute_source_priority

    Also seed the 3 sitemap_jsonld sources (BBC, BAE, Unilever) if missing.
    Also seed the 2 safety-net sources (simplify_github, jobspy) if missing.
    Seed Playwright sources as status='broken'.

    Returns {"sources_created": N, "sources_existed": M}
    """
    path = Path(yaml_path)
    if not path.is_file():
        raise FileNotFoundError(f"Companies file not found at {yaml_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    raw_companies: List[Dict[str, Any]] = (
        data.get("companies", []) if isinstance(data, dict) else []
    )

    # Preload companies and sources
    comp_stmt = select(Company.name, Company.id)
    comp_res = await db.execute(comp_stmt)
    company_ids = dict(comp_res.all())

    src_stmt = select(Source.company_id, Source.type)
    src_res = await db.execute(src_stmt)
    existing_sources = set(src_res.all())

    created = 0
    existed = 0
    sources_to_add: List[Source] = []

    for c in raw_companies:
        name = c.get("name")
        if not name or name not in company_ids:
            continue
        cid = company_ids[name]

        ats = c.get("ats") or {}
        ats_type = (ats.get("type") or "").lower()
        ats_platform = (c.get("ats_platform") or "").lower()
        verified = bool(c.get("feed_url_verified"))

        # 1. Standard verified ATS sources
        if verified or name == "Amazon":
            stype = None
            cfg: Dict[str, Any] = {}
            layer = "1"
            interval = 15
            status = "active"
            last_err = None

            if ats_platform == "greenhouse" or ats_type == "greenhouse":
                stype = "greenhouse"
                cfg = {"slug": ats.get("slug") or name.lower()}
            elif ats_platform == "ashby" or ats_type == "ashby":
                stype = "ashby"
                cfg = {"slug": ats.get("slug") or name.lower()}
            elif ats_platform == "lever" or ats_type == "lever":
                stype = "lever"
                slug_val = ats.get("slug") or name.lower()
                cfg = {"slug": slug_val}
                if slug_val == "frontier":
                    status = "broken"
                    last_err = "Client error '404 Not Found' for url 'https://api.lever.co/v0/postings/frontier?mode=json'"
            elif ats_platform == "workday" or ats_type == "workday":
                stype = "workday"
                cfg = {
                    "slug": ats.get("slug"),
                    "tenant": ats.get("tenant"),
                    "site": ats.get("site"),
                    "wd": ats.get("wd", "wd3"),
                }
            elif ats_platform == "smartrecruiters" or ats_type == "smartrecruiters":
                stype = "smartrecruiters"
                cfg = {"slug": ats.get("slug")}
            elif ats_platform == "recruitee" or ats_type == "recruitee":
                stype = "recruitee"
                cfg = {"slug": ats.get("slug")}
            elif ats_platform == "teamtailor" or ats_type == "teamtailor":
                stype = "teamtailor"
                cfg = {"slug": ats.get("slug")}
            elif ats_type in ("oracle", "oracle_hcm"):
                stype = "oracle_hcm"
                cfg = {
                    "slug": ats.get("slug"),
                    "tenant": ats.get("tenant"),
                    "site": ats.get("site"),
                }
            elif ats_type == "amazon" or name == "Amazon":
                stype = "amazon"
                cfg = {"slug": "amazon"}

            if stype:
                if (cid, stype) in existing_sources:
                    existed += 1
                else:
                    p = compute_source_priority(
                        c.get("tier"), stype, company_name=name, slug=cfg.get("slug")
                    )
                    sources_to_add.append(
                        Source(
                            company_id=cid,
                            layer=layer,
                            type=stype,
                            config=cfg,
                            interval_min=interval,
                            status=status,
                            last_error=last_err,
                            baselined=False,
                            priority=p,
                        )
                    )
                    existing_sources.add((cid, stype))
                    created += 1

    # 2. Seed 3 Sitemap JSON-LD sources (BBC, BAE Systems, Unilever)
    sitemap_configs = [
        (
            "BBC",
            "2",
            {
                "slug": "bbc",
                "url_filter": "/job/",
                "sitemap_url": "https://careers.bbc.co.uk/sitemap.xml",
                "max_first_run": 200,
            },
        ),
        (
            "BAE Systems",
            "1",
            {
                "slug": "baesystems",
                "url_filter": "/job/",
                "sitemap_url": "https://jobsearch.baesystems.com/jobs-sitemap.xml",
                "max_first_run": 500,
            },
        ),
        (
            "Unilever",
            "1",
            {
                "slug": "unilever",
                "url_filter": "/job/",
                "sitemap_url": "https://careers.unilever.com/sitemap.xml",
                "max_first_run": 300,
            },
        ),
    ]

    for cname, layer, cfg in sitemap_configs:
        cid = company_ids.get(cname)
        if cid:
            if (cid, "sitemap_jsonld") in existing_sources:
                existed += 1
            else:
                sources_to_add.append(
                    Source(
                        company_id=cid,
                        layer=layer,
                        type="sitemap_jsonld",
                        config=cfg,
                        interval_min=15,
                        status="active",
                        baselined=False,
                        priority=1,
                    )
                )
                existing_sources.add((cid, "sitemap_jsonld"))
                created += 1

    # 3. Seed 2 safety-net sources (Simplify, JobSpy)
    safety_net_configs = [
        (
            "Simplify (aggregator)",
            "simplify_github",
            "4",
            30,
            {
                "repo_url": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
                "max_first_run": 300,
            },
        ),
        (
            "JobSpy (LinkedIn/Indeed)",
            "jobspy",
            "4",
            120,
            {
                "sites": ["indeed", "linkedin"],
                "location": "United Kingdom",
                "hours_old": 48,
                "search_term": "software engineer intern",
                "max_first_run": 200,
            },
        ),
    ]

    for cname, stype, layer, interval, cfg in safety_net_configs:
        cid = company_ids.get(cname)
        if cid:
            if (cid, stype) in existing_sources:
                existed += 1
            else:
                sources_to_add.append(
                    Source(
                        company_id=cid,
                        layer=layer,
                        type=stype,
                        config=cfg,
                        interval_min=interval,
                        status="active",
                        baselined=False,
                        priority=1,
                    )
                )
                existing_sources.add((cid, stype))
                created += 1

    # 4. Seed Playwright sources as status='broken'
    playwright_configs = [
        (
            "Microsoft",
            {
                "slug": "microsoft",
                "start_url": "https://jobs.careers.microsoft.com/global/en/search?lc=United%20Kingdom",
                "xhr_pattern": "pcsx/search",
                "max_first_run": 100,
            },
        ),
        (
            "NatWest Group",
            {
                "slug": "natwestgroup",
                "start_url": "https://jobs.natwestgroup.com/search/jobs",
                "xhr_pattern": "jobs\\.json",
                "max_first_run": 100,
            },
        ),
        (
            "Goldman Sachs",
            {
                "slug": "goldmansachs",
                "start_url": "https://higher.gs.com/results",
                "xhr_pattern": "gateway/api/v1/graphql",
                "max_first_run": 100,
            },
        ),
    ]

    for cname, cfg in playwright_configs:
        cid = company_ids.get(cname)
        if cid:
            if (cid, "playwright") in existing_sources:
                existed += 1
            else:
                sources_to_add.append(
                    Source(
                        company_id=cid,
                        layer="3",
                        type="playwright",
                        config=cfg,
                        interval_min=60,
                        status="broken",
                        last_error="Experimental (Phase 4b) — unfiltered location, empty-description scoring bug; deferred to future phase",
                        baselined=False,
                        priority=3,
                    )
                )
                existing_sources.add((cid, "playwright"))
                created += 1

    if sources_to_add:
        db.add_all(sources_to_add)
    await db.commit()

    return {"sources_created": created, "sources_existed": existed}


async def baseline_sources_by_priority(db: AsyncSession, priority: int = 1) -> Dict[str, int]:
    """Poll all active sources with priority <= given value.
    Baseline semantics: insert jobs with cycle_id='baseline', set baselined=true, set last_ok=now(),
    DO NOT notify. Log summary.
    Returns {"sources_polled": N, "jobs_inserted": M}
    """
    from jobhunt.scheduler import load_active_sources
    from jobhunt.pipeline.dedupe import process_source

    active_sources = await load_active_sources(db, priority_lte=priority)
    sources_polled = 0
    jobs_inserted = 0

    for adapter, s_id, _ in active_sources:
        try:
            inserted, skipped = await process_source(adapter, db, cycle_id="baseline")
            sources_polled += 1
            jobs_inserted += inserted

            # Update source row to baselined=true and record last_ok
            stmt = select(Source).where(Source.id == s_id)
            res = await db.execute(stmt)
            s_row = res.scalar_one_or_none()
            if s_row:
                s_row.baselined = True
                s_row.last_ok = datetime.now(timezone.utc)
                s_row.fail_streak = 0
                s_row.last_error = None
                await db.commit()

            src_label = f"{adapter.type}:{getattr(adapter, 'slug', '')}"
            logger.info(
                "baseline_insert",
                source=src_label,
                jobs=inserted,
                cycle_id="baseline",
            )
            logger.info(
                "source_baselined",
                source=src_label,
                jobs_inserted=inserted,
                priority=getattr(adapter, "priority", priority),
            )
        except Exception as e:
            logger.error(
                "source_baseline_failed",
                source=f"{adapter.type}:{getattr(adapter, 'slug', '')}",
                error=str(e),
            )

    return {"sources_polled": sources_polled, "jobs_inserted": jobs_inserted}


async def welcome_batch(limit: int = 3, db: Optional[AsyncSession] = None) -> int:
    """Query for fresh high-signal UK tech jobs:
      - tier = 'high'
      - score >= 40
      - is_uk = true
      - is_test = false
      - role_family IN ('swe', 'ml_ai', 'data')
      - posted_at IS NOT NULL AND posted_at >= now() - INTERVAL '14 days'
      - id NOT IN (SELECT job_id FROM notifications WHERE channel='telegram')
    ORDER BY score DESC, posted_at DESC
    LIMIT :limit
    """
    from jobhunt.core.db import async_session_factory

    async def _run(session: AsyncSession) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=15)

        stmt = (
            select(Job, Company.name)
            .join(Company, Job.company_id == Company.id)
            .where(
                Job.tier == "high",
                Job.score >= 40,
                Job.is_uk == True,
                Job.is_test == False,
                Job.duplicate_of.is_(None),
                Job.role_family.in_(["swe", "ml_ai", "data"]),
                Job.posted_at.is_not(None),
                Job.posted_at >= cutoff,
                Job.id.not_in(
                    select(Notification.job_id).where(Notification.channel == "telegram")
                ),
            )
            .order_by(Job.score.desc(), Job.posted_at.desc())
            .limit(limit)
        )
        res = await session.execute(stmt)
        matched_jobs = res.all()

        if not matched_jobs:
            logger.info("welcome_batch_empty")
            print("Welcome batch: 0 jobs matched (welcome_batch_empty)")
            return 0

        token = settings.telegram_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = settings.telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            logger.warning("Telegram credentials missing; cannot send welcome batch")
            return 0

        notifier = TelegramNotifier(token, chat_id)
        sent = 0
        for job, comp_name in matched_jobs:
            job_dict = {
                "id": job.id,
                "title": job.title,
                "location_raw": job.location_raw,
                "score": job.score,
                "tier": job.tier,
                "job_kind": job.job_kind,
                "role_family": job.role_family,
                "posted_at": job.posted_at,
                "apply_url": job.apply_url,
                "source": job.source,
            }
            ok = await notifier.send_job(job_dict, comp_name)
            if ok:
                session.add(Notification(job_id=job.id, channel="telegram"))
                await session.commit()
                sent += 1
                await asyncio.sleep(1)

        print(f"Welcome batch: sent {sent} jobs")
        logger.info("welcome_batch_sent", sent=sent)
        return sent

    if db is not None:
        return await _run(db)
    else:
        async with async_session_factory() as session:
            return await _run(session)


async def send_welcome_batch(db: Optional[AsyncSession] = None, limit: int = 3) -> int:
    return await welcome_batch(limit=limit, db=db)


async def run_first_launch(yaml_path: str, db: AsyncSession) -> Dict[str, Any]:
    """Called by on_startup when DB has 0 companies.
    1. seed_companies_from_yaml
    2. seed_sources_from_yaml
    3. Return immediately — scheduler handles per-source polling and welcome batch.
    """
    comp_summary = await seed_companies_from_yaml(yaml_path, db)
    src_summary = await seed_sources_from_yaml(yaml_path, db)

    n_comp = comp_summary["companies_created"]
    m_src = src_summary["sources_created"]
    logger.info(
        f"bootstrap_complete: seeded {n_comp} companies, {m_src} sources. "
        "P1 sources poll on startup. P2 at +60m, P3 at +180m. "
        "Welcome batch fires when P1 first poll completes."
    )

    return {
        "companies": comp_summary,
        "sources": src_summary,
    }
