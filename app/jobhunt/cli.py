import asyncio
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import subprocess
from typing import Optional
import httpx
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, or_, select
import typer
import yaml

from jobhunt.bootstrap import run_first_launch
from jobhunt.core.config import settings
from jobhunt.core.db import async_session_factory
from jobhunt.core.logging import logger, setup_logging
from jobhunt.core.models import Company, CompanyRule, Job, Notification, Source
from jobhunt.notify.telegram import TelegramNotifier
from jobhunt.pipeline.dedupe import process_source
from jobhunt.pipeline.score import evaluate
from jobhunt.scheduler import start_scheduler
from jobhunt.sources.greenhouse import GreenhouseSource
from jobhunt.sources.lever import LeverSource
from jobhunt.sources.ashby import AshbySource
from jobhunt.sources.smartrecruiters import SmartRecruitersSource
from jobhunt.sources.recruitee import RecruiteeSource
from jobhunt.sources.teamtailor import TeamtailorSource
from jobhunt.sources.workday import WorkdaySource
from jobhunt.sources.oracle_hcm import OracleHCMSource
from jobhunt.sources.amazon import AmazonSource
from jobhunt.sources.sitemap_jsonld import SitemapJSONLDSource
from jobhunt.sources.simplify_github import SimplifyGitHubSource
from jobhunt.sources.jobspy_src import JobSpySource
from jobhunt.sources.browser import PlaywrightSource

app = typer.Typer(help="Job Hunt CLI")


@app.command()
def migrate():
    """Runs alembic upgrade head."""
    ini_path = Path("alembic.ini")
    if not ini_path.exists():
        ini_path = Path("/app/alembic.ini")
    cmd = ["alembic", "-c", str(ini_path), "upgrade", "head"]
    subprocess.run(cmd, check=True)


@app.command("run-source")
def run_source(
    source_spec: str = typer.Argument(
        ..., help="Source specification in format type:slug"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run mode"),
):
    """Builds the source, runs list_jobs once, prints each job as 'title | location' or writes to DB."""
    if ":" not in source_spec:
        typer.echo(f"Invalid format '{source_spec}'. Expected type:slug")
        raise typer.Exit(code=1)

    src_type, slug = source_spec.split(":", 1)
    if src_type == "greenhouse":
        source = GreenhouseSource(slug=slug)
    elif src_type == "lever":
        source = LeverSource(slug=slug)
    elif src_type == "ashby":
        source = AshbySource(slug=slug)
    elif src_type == "smartrecruiters":
        source = SmartRecruitersSource(slug=slug)
    elif src_type == "recruitee":
        source = RecruiteeSource(slug=slug)
    elif src_type == "teamtailor":
        source = TeamtailorSource(slug=slug)
    elif src_type == "workday":
        info = None
        target = next(
            (
                p
                for p in [
                    Path("companies.yaml"),
                    Path("/app/companies.yaml"),
                    Path(os.getenv("COMPANIES_FILE", "")),
                ]
                if p.is_file()
            ),
            None,
        )
        if target:
            try:
                data = yaml.safe_load(target.read_text(encoding="utf-8"))
                for c in data.get("companies", []):
                    ats = c.get("ats") or {}
                    if ats.get("type") == "workday":
                        if (
                            ats.get("tenant") == slug
                            or ats.get("slug") == slug
                            or c.get("name", "").lower() == slug.lower()
                        ):
                            info = (
                                ats.get("tenant"),
                                ats.get("wd"),
                                ats.get("site"),
                                c.get("name"),
                            )
                            break
            except Exception:
                pass
        if info:
            tenant, wd, site, company_name = info
            source = WorkdaySource(
                tenant=tenant, wd=wd, site=site, company_name=company_name
            )
        else:
            typer.echo(
                f"Could not find Workday configuration for slug '{slug}' in companies.yaml"
            )
            raise typer.Exit(code=1)
    elif src_type == "oracle_hcm":
        info = None
        target = next(
            (
                p
                for p in [
                    Path("companies.yaml"),
                    Path("/app/companies.yaml"),
                    Path(os.getenv("COMPANIES_FILE", "")),
                ]
                if p.is_file()
            ),
            None,
        )
        if target:
            try:
                import re
                from urllib.parse import urlparse
                data = yaml.safe_load(target.read_text(encoding="utf-8"))
                for c in data.get("companies", []):
                    ats = c.get("ats") or {}
                    if ats.get("type") in ("oracle", "oracle_hcm"):
                        norm_slug = slug.lower().replace(" ", "").replace("_", "").replace("-", "")
                        c_norm = c.get("name", "").lower().replace(" ", "").replace("_", "").replace("-", "")
                        ats_slug = str(ats.get("slug") or "").lower()
                        if (
                            ats_slug == norm_slug
                            or ats.get("tenant") == slug
                            or c_norm == norm_slug
                            or norm_slug in c_norm
                        ):
                            tenant = ats.get("tenant")
                            if not tenant and ats.get("custom_url"):
                                tenant = urlparse(ats.get("custom_url")).netloc
                            site = ats.get("site")
                            if not site and ats.get("custom_url"):
                                match = re.search(r"/sites/([^/]+)", ats.get("custom_url"))
                                if match:
                                    site = match.group(1)
                            info = (tenant, site or "CX_1001", c.get("name"), ats.get("slug") or slug)
                            break
            except Exception:
                pass
        if info:
            tenant, site, company_name, ats_slug = info
            source = OracleHCMSource(
                tenant=tenant, site=site, company_name=company_name, slug=ats_slug
            )
        else:
            typer.echo(
                f"Could not find Oracle HCM configuration for slug '{slug}' in companies.yaml"
            )
            raise typer.Exit(code=1)
    elif src_type == "amazon":
        source = AmazonSource(company_name="Amazon", slug=slug)
    elif src_type == "sitemap_jsonld":
        comp_name = "BBC" if slug.lower() == "bbc" else slug.title()
        sitemap_url = "https://careers.bbc.co.uk/sitemap.xml" if slug.lower() == "bbc" else f"https://{slug}.com/sitemap.xml"
        url_filter = "/job/"
        max_first_run = 200

        try:
            with open("companies.yaml", "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                comp_list = data.get("companies", data) if isinstance(data, dict) else data
                for c in comp_list:
                    if not isinstance(c, dict):
                        continue
                    ats = c.get("ats") or {}
                    c_clean = c.get("name", "").lower().replace(" ", "").replace("_", "").replace("-", "")
                    s_clean = slug.lower().replace(" ", "").replace("_", "").replace("-", "")
                    if ats.get("type") == "sitemap_jsonld" and (
                        ats.get("slug") == slug or c_clean == s_clean
                    ):
                        comp_name = c.get("name")
                        sitemap_url = ats.get("sitemap_url", sitemap_url)
                        url_filter = ats.get("url_filter", url_filter)
                        max_first_run = ats.get("max_first_run", max_first_run)
                        break
        except Exception:
            pass

        source = SitemapJSONLDSource(
            company_name=comp_name,
            sitemap_url=sitemap_url,
            url_filter=url_filter,
            max_first_run=max_first_run,
            slug=slug,
        )
    elif src_type == "simplify_github":
        source = SimplifyGitHubSource(
            repo_url="https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
            max_first_run=300,
            company_name="Simplify (aggregator)",
            slug=slug or "simplify",
        )
    elif src_type == "jobspy":
        source = JobSpySource(
            search_term="software engineer intern",
            location="United Kingdom",
            sites=["indeed", "linkedin"],
            max_first_run=200,
            hours_old=48,
            company_name="JobSpy (LinkedIn/Indeed)",
            slug=slug or "jobspy",
        )
    elif src_type == "playwright":
        config_map = {
            "microsoft": {
                "company_name": "Microsoft",
                "start_url": "https://jobs.careers.microsoft.com/global/en/search?lc=United%20Kingdom",
                "xhr_pattern": r"pcsx/search",
                "max_first_run": 100,
            },
            "natwestgroup": {
                "company_name": "NatWest Group",
                "start_url": "https://jobs.natwestgroup.com/search/jobs",
                "xhr_pattern": r"jobs\.json",
                "max_first_run": 100,
            },
            "natwest": {
                "company_name": "NatWest Group",
                "start_url": "https://jobs.natwestgroup.com/search/jobs",
                "xhr_pattern": r"jobs\.json",
                "max_first_run": 100,
            },
            "goldmansachs": {
                "company_name": "Goldman Sachs",
                "start_url": "https://higher.gs.com/results",
                "xhr_pattern": r"gateway/api/v1/graphql",
                "max_first_run": 100,
            },
            "goldman": {
                "company_name": "Goldman Sachs",
                "start_url": "https://higher.gs.com/results",
                "xhr_pattern": r"gateway/api/v1/graphql",
                "max_first_run": 100,
            },
        }

        norm_slug = slug.lower().replace(" ", "").replace("_", "").replace("-", "")
        cfg = config_map.get(norm_slug, {})
        company_name = cfg.get("company_name", slug.title())
        start_url = cfg.get("start_url", f"https://{slug}.com/careers")
        xhr_pattern = cfg.get("xhr_pattern")
        wait_selector = cfg.get("wait_selector")
        max_first_run = cfg.get("max_first_run", 100)

        source = PlaywrightSource(
            company_name=company_name,
            start_url=start_url,
            xhr_pattern=xhr_pattern,
            wait_selector=wait_selector,
            max_first_run=max_first_run,
            slug=slug,
        )
    else:
        typer.echo(
            f"Unsupported source type '{src_type}'. Supported: greenhouse, lever, ashby, smartrecruiters, recruitee, teamtailor, workday, oracle_hcm, amazon, sitemap_jsonld, simplify_github, jobspy, playwright."
        )
        raise typer.Exit(code=1)

    if dry_run:
        jobs = asyncio.run(source.list_jobs())
        print(f"{len(jobs)} jobs discovered")
        for j in jobs:
            print(f"{j.title or j.apply_url} | {j.location_raw}")
    else:
        async def _run():
            async with async_session_factory() as session:
                inserted, skipped = await process_source(source, session)
                print(f"inserted {inserted}, skipped {skipped}")

        asyncio.run(_run())


@app.command("rescore")
def rescore():
    """Iterate all jobs in DB, recompute score+tier+matched_rules, UPDATE.
    Prints 'rescored N jobs: H high, M medium, L low, D drop'.
    """
    async def _rescore():
        async with async_session_factory() as session:
            rule_stmt = select(CompanyRule)
            rule_res = await session.execute(rule_stmt)
            company_rules = {r.company_id: r for r in rule_res.scalars().all()}

            comp_stmt = select(Company)
            comp_res = await session.execute(comp_stmt)
            companies_map = {c.id: c for c in comp_res.scalars().all()}

            stmt = select(Job)
            res = await session.execute(stmt)
            jobs = res.scalars().all()

            counts = {"high": 0, "medium": 0, "low": 0, "drop": 0}
            for job in jobs:
                c_rule = company_rules.get(job.company_id)
                comp = companies_map.get(job.company_id)
                comp_tier = comp.tier if comp else ""
                eval_res = evaluate(
                    title=job.title,
                    desc=job.description or "",
                    department="",
                    company_tier=comp_tier,
                    employment_type=job.job_kind or "",
                    posted_at=job.posted_at,
                    first_seen=job.first_seen,
                    is_test=job.is_test,
                    remote_ambiguous=job.remote_ambiguous,
                    company_rule=c_rule,
                    is_uk=job.is_uk,
                    is_manchester=job.is_manchester,
                    is_london=job.is_london,
                    is_remote=job.is_remote,
                    job=job,
                )
                job.score = eval_res["score"]
                job.tier = eval_res["tier"]
                job.matched_rules = eval_res["matched_rules"]
                counts[eval_res["tier"]] = counts.get(eval_res["tier"], 0) + 1

            await session.commit()
            print(
                f"rescored {len(jobs)} jobs: {counts['high']} high, {counts['medium']} medium, {counts['low']} low, {counts['drop']} drop"
            )

    asyncio.run(_rescore())


@app.command("top")
def top(
    limit: int = typer.Option(20, "--limit", help="Max jobs to show"),
    tier: Optional[str] = typer.Option(None, "--tier", help="Filter by tier (high, medium, low, drop)"),
    include_drops: bool = typer.Option(False, "--include-drops", help="Include drop tier"),
):
    """Shows top jobs ranked by score DESC."""
    async def _top():
        async with async_session_factory() as session:
            stmt = select(Job, Company.name).join(Company, Job.company_id == Company.id)
            stmt = stmt.where(Job.duplicate_of.is_(None))
            if tier:
                stmt = stmt.where(Job.tier == tier.lower())
            elif not include_drops:
                stmt = stmt.where(Job.tier.in_(["high", "medium"]))
            stmt = stmt.order_by(Job.score.desc(), Job.id.asc()).limit(limit)
            res = await session.execute(stmt)
            rows = res.all()

            console = Console()
            table = Table()
            table.add_column("tier")
            table.add_column("score", justify="right")
            table.add_column("company")
            table.add_column("title")
            table.add_column("location")
            table.add_column("kind")
            table.add_column("role")
            table.add_column("source")

            high_cnt = 0
            med_cnt = 0
            for job, comp_name in rows:
                if job.tier == "high":
                    high_cnt += 1
                elif job.tier == "medium":
                    med_cnt += 1
                table.add_row(
                    job.tier,
                    str(job.score),
                    comp_name,
                    job.title[:55],
                    (job.location_raw or "")[:22],
                    job.job_kind or "",
                    job.role_family or "",
                    job.source or "",
                )

            console.print(table)
            if tier:
                console.print(f"{len(rows)} {tier}")
            elif include_drops:
                drop_cnt = sum(1 for j, _ in rows if j.tier == "drop")
                low_cnt = sum(1 for j, _ in rows if j.tier == "low")
                parts = []
                if high_cnt:
                    parts.append(f"{high_cnt} high")
                if med_cnt:
                    parts.append(f"{med_cnt} medium")
                if low_cnt:
                    parts.append(f"{low_cnt} low")
                if drop_cnt:
                    parts.append(f"{drop_cnt} drop")
                console.print(", ".join(parts) if parts else "0 jobs")
            else:
                console.print(f"{high_cnt} high, {med_cnt} medium")

    asyncio.run(_top())


@app.command("coverage")
def coverage():
    """Prints coverage ledger table across all companies."""
    async def _coverage():
        async with async_session_factory() as session:
            stmt = select(
                Company.id,
                Company.name,
                Company.tier,
                Source.layer,
                Source.last_ok,
                func.count(Job.id.distinct()).label("job_count"),
            ).outerjoin(
                Source, Source.company_id == Company.id
            ).outerjoin(
                Job, Job.company_id == Company.id
            ).group_by(
                Company.id, Company.name, Company.tier, Source.layer, Source.last_ok
            )
            res = await session.execute(stmt)
            db_companies = {r.name.lower(): r for r in res.all()}

            target = next(
                (
                    p
                    for p in [
                        Path("companies.yaml"),
                        Path("/app/companies.yaml"),
                        Path(os.getenv("COMPANIES_FILE", "")),
                    ]
                    if p.is_file()
                ),
                None,
            )
            data = yaml.safe_load(target.read_text(encoding="utf-8")) if target else {}
            yaml_companies = [
                c
                for c in data.get("companies", [])
                if c.get("coverage_layer") == 1 or c.get("feed_url")
            ]

            now = datetime.now(timezone.utc)
            three_hours = timedelta(hours=3)

            console = Console()
            table = Table()
            table.add_column("name")
            table.add_column("tier")
            table.add_column("layer")
            table.add_column("last_ok")
            table.add_column("job_count", justify="right")
            table.add_column("broken?")

            l1_total = 0
            l1_working = 0
            l1_broken = 0

            for c in yaml_companies:
                c_name = c.get("name", "")
                db_c = db_companies.get(c_name.lower())
                c_tier = (db_c.tier if db_c and db_c.tier else c.get("tier")) or "NULL"
                feed_url_verified = c.get("feed_url_verified", False)

                if db_c and db_c.layer:
                    layer = str(db_c.layer)
                    last_ok_dt = db_c.last_ok
                    last_ok_str = last_ok_dt.strftime("%Y-%m-%d %H:%M") if last_ok_dt else "NULL"
                    job_count_str = str(db_c.job_count)

                    is_broken = False
                    if layer == "1" and feed_url_verified is True:
                        if not last_ok_dt or (now - last_ok_dt) > three_hours:
                            is_broken = True

                    if layer == "1":
                        l1_total += 1
                        if is_broken:
                            l1_broken += 1
                        else:
                            l1_working += 1

                    broken_str = "True" if is_broken else "False"
                else:
                    layer = "NULL"
                    last_ok_str = "NULL"
                    job_count_str = "0"
                    broken_str = "False"

                table.add_row(
                    c_name,
                    c_tier,
                    layer,
                    last_ok_str,
                    job_count_str,
                    broken_str,
                )

            console.print(table)
            console.print(f"Layer 1: {l1_total} feeds, {l1_working} working, {l1_broken} broken")

    asyncio.run(_coverage())


@app.command("notify-test")
def notify_test():
    """Sends a test message to Telegram."""
    token = settings.telegram_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = settings.telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    token = token.strip()
    chat_id = chat_id.strip()

    if not token or not chat_id:
        print("Error: Missing required Telegram environment variables.")
        print("Required:")
        if not token:
            print("  - TELEGRAM_BOT_TOKEN")
        if not chat_id:
            print("  - TELEGRAM_CHAT_ID")
        raise typer.Exit(code=1)

    async def _test():
        notifier = TelegramNotifier(token, chat_id)
        ok = await notifier.send_text("✅ job-hunt notifier connected. This is a test message.")
        if ok:
            print("Sent test message")
        else:
            print("Failed to send test message")
            raise typer.Exit(code=1)

    asyncio.run(_test())


@app.command("notify")
def notify(
    limit: Optional[int] = typer.Option(None, "--limit", help="Max jobs to notify"),
    tier: str = typer.Option("high", "--tier", help="Filter by tier: high, medium, all"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run mode (print what WOULD be sent)"),
    send: bool = typer.Option(False, "--send", help="Actually send notifications"),
    id: Optional[int] = typer.Option(None, "--id", help="Send a specific job ID"),
):
    """Notify Telegram of top jobs."""
    is_send = bool(send)
    is_dry_run = dry_run or not is_send

    async def _notify():
        async with async_session_factory() as session:
            if id is not None:
                stmt = select(Job, Company.name).join(Company, Job.company_id == Company.id).where(Job.id == id)
                res = await session.execute(stmt)
                row = res.first()
                if not row:
                    print(f"Error: Job {id} not found.")
                    raise typer.Exit(code=1)
                candidate_jobs = [row]
            else:
                stmt = select(Job, Company.name).join(Company, Job.company_id == Company.id)
                stmt = stmt.where(
                    Job.is_test == False,
                    Job.is_uk == True,
                    Job.duplicate_of.is_(None),
                )
                tier_l = tier.lower()
                if tier_l == "high":
                    stmt = stmt.where(Job.tier == "high")
                elif tier_l == "medium":
                    stmt = stmt.where(Job.tier == "medium")
                elif tier_l == "all":
                    stmt = stmt.where(Job.tier.in_(["high", "medium", "low"]))
                else:
                    stmt = stmt.where(Job.tier == tier_l)

                stmt = stmt.order_by(Job.score.desc(), Job.id.asc())
                if limit is not None:
                    stmt = stmt.limit(limit)

                res = await session.execute(stmt)
                candidate_jobs = res.all()

            # Query existing telegram notifications for candidate jobs
            job_ids = [j.id for j, _ in candidate_jobs]
            notified_ids = set()
            if job_ids:
                n_stmt = select(Notification.job_id).where(
                    Notification.channel == "telegram",
                    Notification.job_id.in_(job_ids),
                )
                n_res = await session.execute(n_stmt)
                notified_ids = set(n_res.scalars().all())

            if is_dry_run:
                console = Console()
                table = Table()
                table.add_column("tier")
                table.add_column("score", justify="right")
                table.add_column("company")
                table.add_column("title")
                table.add_column("location")
                table.add_column("kind")
                table.add_column("role")
                table.add_column("source")

                unnotified_count = 0
                for job, comp_name in candidate_jobs:
                    if job.id not in notified_ids:
                        unnotified_count += 1
                        table.add_row(
                            job.tier,
                            str(job.score),
                            comp_name,
                            job.title[:55],
                            (job.location_raw or "")[:22],
                            job.job_kind or "",
                            job.role_family or "",
                            job.source or "",
                        )

                console.print(table)
                console.print(f"Would send {unnotified_count} jobs, {len(notified_ids)} skipped (already notified)")
                return

            token = settings.telegram_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
            chat_id = settings.telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
            token = token.strip()
            chat_id = chat_id.strip()

            if not token or not chat_id:
                print("Error: Missing required Telegram environment variables.")
                print("Required:")
                if not token:
                    print("  - TELEGRAM_BOT_TOKEN")
                if not chat_id:
                    print("  - TELEGRAM_CHAT_ID")
                raise typer.Exit(code=1)

            notifier = TelegramNotifier(token, chat_id)
            sent = 0
            skipped = 0

            for job, comp_name in candidate_jobs:
                if job.id in notified_ids:
                    skipped += 1
                    continue

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
                    notif = Notification(job_id=job.id, channel="telegram")
                    session.add(notif)
                    await session.commit()
                    sent += 1
                    await asyncio.sleep(1)
                else:
                    print(f"Failed to send job {job.id}")
                    break

            print(f"Sent {sent}, skipped {skipped}")

    asyncio.run(_notify())


@app.command("run-scheduler")
def run_scheduler(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Run one cycle of all adapters without sending notifications"
    ),
    once: bool = typer.Option(
        False, "--once", help="Run one cycle of all adapters and exit"
    ),
    skip_migrate: bool = typer.Option(
        False, "--skip-migrate", help="Skip running database migrations"
    ),
):
    """Starts the scheduler with auto-migration and bootstrap."""
    setup_logging()

    # 1. Run migrations automatically
    if not skip_migrate:
        ini_path = Path("alembic.ini")
        if not ini_path.exists():
            ini_path = Path("/app/alembic.ini")
        if ini_path.exists():
            cmd = ["alembic", "-c", str(ini_path), "upgrade", "head"]
            subprocess.run(cmd, check=True, cwd=str(ini_path.parent))
            logger.info("migrations_applied")

    # 2. Check DB and bootstrap if needed, then run scheduler
    async def _start():
        async with async_session_factory() as session:
            result = await session.execute(select(func.count()).select_from(Company))
            company_count = result.scalar() or 0
            if company_count == 0:
                yaml_path = next(
                    (
                        p
                        for p in [
                            Path("/app/companies.yaml"),
                            Path("companies.yaml"),
                            Path(os.getenv("COMPANIES_FILE", "")),
                        ]
                        if p.is_file()
                    ),
                    Path("companies.yaml"),
                )
                await run_first_launch(str(yaml_path), session)
                logger.info("first_launch_complete")
            else:
                logger.info("existing_db_detected", company_count=company_count)

        await start_scheduler(dry_run=dry_run, once=once)

    asyncio.run(_start())


@app.command("welcome-batch")
def cli_welcome_batch(
    limit: int = typer.Option(3, "--limit", "-l", help="Number of fresh high-signal jobs to send"),
):
    """Send welcome batch of 1-3 best fresh jobs."""
    from jobhunt.bootstrap import welcome_batch
    asyncio.run(welcome_batch(limit=limit))


@app.command("status")
def status(
    watch: bool = typer.Option(False, "--watch", "-w", help="Refresh status every 10 seconds"),
):
    """Display comprehensive system status metrics table."""
    import time as pytime

    async def _get_status():
        async with async_session_factory() as session:
            # 1. Total companies
            comp_count = (await session.execute(select(func.count()).select_from(Company))).scalar() or 0

            # 2. Total sources breakdown
            src_total = (await session.execute(select(func.count()).select_from(Source))).scalar() or 0
            src_active = (await session.execute(select(func.count()).select_from(Source).where(Source.status == "active"))).scalar() or 0
            src_broken = (await session.execute(select(func.count()).select_from(Source).where(Source.status == "broken"))).scalar() or 0

            # 3. Priority breakdown
            p_stats = {}
            for p in [1, 2, 3]:
                p_tot = (await session.execute(
                    select(func.count()).select_from(Source).where(Source.priority == p, Source.status == "active")
                )).scalar() or 0
                p_base = (await session.execute(
                    select(func.count()).select_from(Source).where(
                        Source.priority == p, Source.baselined == True, Source.last_ok.is_not(None), Source.status == "active"
                    )
                )).scalar() or 0
                p_stats[p] = (p_base, p_tot)

            # 4. Total jobs (canonical)
            canonical_jobs = (await session.execute(
                select(func.count()).select_from(Job).where(Job.duplicate_of.is_(None))
            )).scalar() or 0

            # 5. Jobs with high tier (canonical)
            high_jobs = (await session.execute(
                select(func.count()).select_from(Job).where(
                    Job.duplicate_of.is_(None), Job.tier == "high"
                )
            )).scalar() or 0

            # 6. Jobs with high tier matching notification filter (recent+tech)
            now_utc = datetime.now(timezone.utc)
            cutoff_posted = now_utc - timedelta(days=7)
            cutoff_first_seen = now_utc - timedelta(days=1)
            matching_tech_jobs = (await session.execute(
                select(func.count()).select_from(Job).where(
                    Job.tier == "high",
                    Job.score >= 40,
                    Job.is_uk == True,
                    Job.is_test == False,
                    Job.duplicate_of.is_(None),
                    Job.role_family.in_(["swe", "ml_ai", "data"]),
                    or_(
                        (Job.posted_at.is_not(None) & (Job.posted_at > cutoff_posted)),
                        (Job.posted_at.is_(None) & (Job.first_seen > cutoff_first_seen)),
                    ),
                )
            )).scalar() or 0

            # 7. Notifications sent (total, last 24h)
            notif_count = (await session.execute(
                select(func.count()).select_from(Notification)
            )).scalar() or 0
            cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
            notif_24h = (await session.execute(
                select(func.count()).select_from(Notification).where(Notification.sent_at >= cutoff_24h)
            )).scalar() or 0

            # 8. Per-source poll info
            last_ok_max = (await session.execute(select(func.max(Source.last_ok)))).scalar()
            last_ok_min_p1 = (await session.execute(
                select(func.min(Source.last_ok)).where(Source.priority == 1, Source.status == "active")
            )).scalar()
            current_cycle_str = "per-source (P1:15m, P2:60m, P3:180m)"
            if last_ok_max:
                elapsed_min = int((now_utc - last_ok_max).total_seconds() // 60)
                cycle_started_str = f"{elapsed_min} min ago" if elapsed_min > 0 else "just now"
                next_min = max(0, 15 - elapsed_min)
                next_cycle_str = f"in {next_min} min"
            else:
                cycle_started_str = "N/A"
                next_cycle_str = "N/A"

            if last_ok_max and last_ok_min_p1:
                dur_s = max(0, int((last_ok_max - last_ok_min_p1).total_seconds()))
                mins = int(dur_s // 60)
                secs = int(dur_s % 60)
                duration_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
            else:
                duration_str = "N/A"

            # 9. Fresh DB / Baseline in progress (P1 first poll)
            p1_unpolled = (await session.execute(
                select(func.count()).select_from(Source).where(
                    Source.priority == 1, Source.status == "active", Source.last_ok.is_(None)
                )
            )).scalar() or 0
            is_fresh = "Yes" if src_total == 0 and comp_count == 0 else "No"
            baseline_in_progress = "Yes" if p1_unpolled > 0 else "No"

            return {
                "comp_count": comp_count,
                "src_active": src_active,
                "p_stats": p_stats,
                "src_broken": src_broken,
                "canonical_jobs": canonical_jobs,
                "high_jobs": high_jobs,
                "matching_tech_jobs": matching_tech_jobs,
                "notif_count": notif_count,
                "notif_24h": notif_24h,
                "current_cycle_str": current_cycle_str,
                "cycle_started_str": cycle_started_str,
                "duration_str": duration_str,
                "next_cycle_str": next_cycle_str,
                "is_fresh": is_fresh,
                "baseline_in_progress": baseline_in_progress,
            }

    def _render():
        data = asyncio.run(_get_status())
        console = Console()
        table = Table(title="JOB HUNT STATUS", show_header=False)
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="magenta")

        table.add_row("Companies", str(data["comp_count"]))
        table.add_row("Sources active", str(data["src_active"]))
        p1_b, p1_t = data["p_stats"][1]
        p2_b, p2_t = data["p_stats"][2]
        p3_b, p3_t = data["p_stats"][3]
        table.add_row("  P1", f"{p1_b}/{p1_t} baselined")
        table.add_row("  P2", f"{p2_b}/{p2_t} baselined")
        table.add_row("  P3", f"{p3_b}/{p3_t} baselined")
        table.add_row("Sources broken", str(data["src_broken"]))
        table.add_section()

        table.add_row("Total jobs", f"{data['canonical_jobs']:,}")
        table.add_row("High-tier canonical", str(data["high_jobs"]))
        table.add_row("Matching notify filter", str(data["matching_tech_jobs"]))
        table.add_section()

        table.add_row("Notifications:", "")
        notif_total_desc = (
            f"{data['notif_count']} (welcome batch)"
            if 0 < data["notif_count"] <= 3
            else str(data["notif_count"])
        )
        table.add_row("  Total", notif_total_desc)
        table.add_row("  Last 24h", str(data["notif_24h"]))
        table.add_section()

        table.add_row("Cycle info:", "")
        table.add_row("  Current cycle", data["current_cycle_str"])
        table.add_row("  Cycle started", data["cycle_started_str"])
        table.add_row("  Last cycle duration", data["duration_str"])
        table.add_row("  Next cycle", data["next_cycle_str"])
        table.add_section()

        table.add_row("Fresh DB", data["is_fresh"])
        table.add_row("Baseline in progress", data["baseline_in_progress"])

        console.print(table)

    if watch:
        console = Console()
        try:
            while True:
                console.clear()
                _render()
                pytime.sleep(10)
        except KeyboardInterrupt:
            pass
    else:
        _render()


if __name__ == "__main__":
    app()
