from datetime import datetime, timezone
import os
from pathlib import Path
from typing import List, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import yaml

from jobhunt.core.logging import logger
from jobhunt.core.models import (
    Company,
    CompanyRule,
    Job as JobModel,
    Sighting,
    Source,
)
from jobhunt.core.schemas import RawJob
from jobhunt.pipeline.classify import classify
from jobhunt.pipeline.normalise import (
    compute_dedupe_key,
    compute_fingerprint,
    get_canonical_city,
    normalise,
)
from jobhunt.pipeline.score import evaluate


async def dedupe_raw_jobs(
    db: AsyncSession,
    company_id: int,
    source_id: int,
    source_type: str,
    raw_jobs: List[RawJob],
) -> Tuple[List[Tuple[RawJob, str, str]], int]:
    """Deduplicates raw jobs in order:
    1. external_id match via sightings (source_id, external_id) -> skip
    2. exact fingerprint match via jobs table (cross-source) -> skip & add sighting
    3. dedupe_key match within same company_id (cross-source) -> skip & add sighting
    otherwise -> INSERT job + INSERT sighting row
    """
    stmt = select(Sighting.external_id).where(Sighting.source_id == source_id)
    res = await db.execute(stmt)
    existing_ext_ids = set(res.scalars().all())

    # Pre-calculate fingerprints and dedupe_keys for all raw jobs
    raw_fps = []
    raw_dkeys = []
    cid_list = set()
    for rj in raw_jobs:
        city = get_canonical_city(rj.location_raw)
        fp = compute_fingerprint(rj.company_name, rj.title, city)
        raw_fps.append(fp)
        dkey = compute_dedupe_key(
            rj.company_name, rj.title, city, getattr(rj, "job_kind", None)
        )
        raw_dkeys.append(dkey)
        cid = rj.company_id or company_id
        if cid:
            cid_list.add(cid)

    cross_stmt = select(JobModel.id, JobModel.fingerprint).where(
        JobModel.fingerprint.in_(raw_fps),
        JobModel.source.is_not(None),
        JobModel.source != source_type,
    )
    cross_res = await db.execute(cross_stmt)
    cross_source_jobs = {fp: jid for jid, fp in cross_res.all()}

    # Query existing dedupe_keys within same company_id
    cross_dkey_jobs = {}
    if cid_list and raw_dkeys:
        dkey_stmt = select(JobModel.id, JobModel.company_id, JobModel.dedupe_key).where(
            JobModel.company_id.in_(list(cid_list)),
            JobModel.dedupe_key.in_(raw_dkeys),
            JobModel.source.is_not(None),
            JobModel.source != source_type,
        )
        dkey_res = await db.execute(dkey_stmt)
        cross_dkey_jobs = {
            (row.company_id, row.dedupe_key): row.id for row in dkey_res.all()
        }

    seen_ext_ids = set()
    seen_fps = set()
    seen_dkeys = set()
    to_insert: List[Tuple[RawJob, str, str]] = []
    skipped = 0

    now_utc = datetime.now(timezone.utc)
    for rj, fp, dkey in zip(raw_jobs, raw_fps, raw_dkeys):
        # Skip jobs older than 14 days to keep DB lean and baseline fast
        if rj.posted_at is not None:
            posted_dt = rj.posted_at if rj.posted_at.tzinfo is not None else rj.posted_at.replace(tzinfo=timezone.utc)
            age_days = (now_utc - posted_dt).days
            if age_days > 14:
                logger.debug("skip_old_job", title=rj.title, age_days=age_days)
                skipped += 1
                continue

        # 1. external_id match via sightings table -> skip
        if rj.external_id in existing_ext_ids or rj.external_id in seen_ext_ids:
            skipped += 1
            continue
        seen_ext_ids.add(rj.external_id)

        # 2. exact fingerprint match via jobs table -> skip and record sighting
        city = get_canonical_city(rj.location_raw)
        if fp in cross_source_jobs:
            matched_job_id = cross_source_jobs[fp]
            db.add(
                Sighting(
                    job_id=matched_job_id,
                    source_id=source_id,
                    external_id=rj.external_id,
                )
            )
            skipped += 1
            continue

        if fp in seen_fps:
            skipped += 1
            continue
        seen_fps.add(fp)

        # 3. dedupe_key match within same company_id -> skip and record sighting
        cid = rj.company_id or company_id
        if (cid, dkey) in cross_dkey_jobs:
            matched_job_id = cross_dkey_jobs[(cid, dkey)]
            db.add(
                Sighting(
                    job_id=matched_job_id,
                    source_id=source_id,
                    external_id=rj.external_id,
                )
            )
            skipped += 1
            continue

        if (cid, dkey) in seen_dkeys:
            skipped += 1
            continue
        seen_dkeys.add((cid, dkey))

        # Otherwise -> candidate for INSERT job + sighting row
        to_insert.append((rj, fp, dkey))

    return to_insert, skipped


def find_company_info(slug: str, fallback_name: str) -> dict:
    """Lookup company info from companies.yaml if available."""
    possible_paths = [
        Path("companies.yaml"),
        Path("/app/companies.yaml"),
        Path(os.getenv("COMPANIES_FILE", "")),
    ]
    target = next((p for p in possible_paths if p.is_file()), None)
    if target:
        try:
            data = yaml.safe_load(target.read_text(encoding="utf-8"))
            for c in data.get("companies", []):
                ats = c.get("ats") or {}
                norm_slug = slug.lower().replace(" ", "").replace("_", "").replace("-", "")
                c_norm = c.get("name", "").lower().replace(" ", "").replace("_", "").replace("-", "")
                if (
                    ats.get("slug") == slug
                    or c.get("name", "").lower() == slug.lower()
                    or c_norm == norm_slug
                    or norm_slug in c_norm
                ):
                    return {
                        "name": c.get("name", fallback_name),
                        "tier": c.get("tier"),
                        "hubs": c.get("hubs"),
                        "sector": c.get("sector"),
                    }
        except Exception:
            pass
    return {"name": fallback_name, "tier": None, "hubs": None, "sector": None}


async def process_source(
    source, db: AsyncSession, cycle_id: Optional[str] = None
) -> Tuple[int, int]:
    """Executes the pipeline: list_jobs -> dedupe -> normalise -> classify -> insert jobs rows.
    Updates source row (setting baselined=true if first run, and last_ok).
    Returns (inserted_count, skipped_count).
    """
    # 1. Fetch raw jobs
    raw_jobs = await source.list_jobs()

    # 2. Get or create Company
    info = find_company_info(source.slug, source.company_name)
    company_name = info["name"]

    stmt = select(Company).where(Company.name == company_name)
    res = await db.execute(stmt)
    company = res.scalar_one_or_none()
    if not company:
        company = Company(
            name=company_name,
            tier=info["tier"],
            hubs=info["hubs"],
            sector=info["sector"],
        )
        db.add(company)
        await db.flush()

    # 3. Get or create Source
    stmt = select(Source).where(
        Source.company_id == company.id, Source.type == source.type
    )
    res = await db.execute(stmt)
    source_row = res.scalar_one_or_none()
    if not source_row:
        source_row = Source(
            company_id=company.id,
            layer="1",
            type=source.type,
            config={"slug": source.slug},
            interval_min=15,
            status="active",
            baselined=False,
        )
        db.add(source_row)
        await db.flush()

    # 4. Dedupe
    to_insert, skipped = await dedupe_raw_jobs(
        db, company.id, source_row.id, source.type, raw_jobs
    )

    # 5. Normalise and Classify
    stmt_rule = select(CompanyRule)
    rule_res = await db.execute(stmt_rule)
    rules_map = {r.company_id: r for r in rule_res.scalars().all()}

    stmt_comp = select(Company.id, Company.tier)
    comp_res = await db.execute(stmt_comp)
    tiers_map = dict(comp_res.all())

    jobs_to_insert: List[JobModel] = []
    for rj, fp, dkey in to_insert:
        effective_cid = rj.company_id or company.id
        effective_rule = rules_map.get(effective_cid)
        effective_tier = tiers_map.get(effective_cid, company.tier or "")

        if not rj.description and hasattr(source, "get_detail"):
            rj = await source.get_detail(rj)
        job_schema = normalise(rj, effective_cid)
        classified_job = classify(job_schema)
        eval_res = evaluate(
            title=classified_job.title,
            desc=classified_job.description or "",
            department="",
            company_tier=effective_tier or "",
            employment_type=classified_job.job_kind or "",
            posted_at=classified_job.posted_at,
            first_seen=datetime.now(timezone.utc),
            is_test=classified_job.is_test,
            remote_ambiguous=classified_job.remote_ambiguous,
            company_rule=effective_rule,
            is_uk=classified_job.is_uk,
            is_manchester=classified_job.is_manchester,
            is_london=classified_job.is_london,
            is_remote=classified_job.is_remote,
        )
        db_job = JobModel(
            company_id=effective_cid,
            title=classified_job.title,
            location_raw=classified_job.location_raw,
            is_uk=classified_job.is_uk,
            is_manchester=classified_job.is_manchester,
            is_london=classified_job.is_london,
            is_remote=classified_job.is_remote,
            remote_ambiguous=classified_job.remote_ambiguous,
            is_test=classified_job.is_test,
            job_kind=classified_job.job_kind,
            role_family=classified_job.role_family,
            description=classified_job.description,
            apply_url=classified_job.apply_url,
            posted_at=classified_job.posted_at,
            fingerprint=fp,
            dedupe_key=dkey,
            inserted_cycle_id=cycle_id,
            priority=classified_job.priority,
            source=source.type,
            score=eval_res["score"],
            tier=eval_res["tier"],
            matched_rules=eval_res["matched_rules"],
            sightings=[
                Sighting(
                    source_id=source_row.id,
                    external_id=rj.external_id,
                )
            ],
        )
        jobs_to_insert.append(db_job)

    # 6. Insert jobs rows & update source
    if jobs_to_insert:
        db.add_all(jobs_to_insert)

    if not source_row.baselined or cycle_id == "baseline":
        source_row.baselined = True
        source_row.last_ok = datetime.now(timezone.utc)

    source_row.last_error = None
    source_row.fail_streak = 0

    await db.commit()

    return len(jobs_to_insert), skipped
