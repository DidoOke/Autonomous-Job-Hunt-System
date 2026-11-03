import asyncio
from datetime import datetime, timezone
import hashlib
from typing import List, Optional
from jobhunt.core.schemas import RawJob
from jobspy import scrape_jobs


class JobSpySource:
    type: str = "jobspy"

    def __init__(
        self,
        search_term: str = "software engineer intern",
        location: str = "United Kingdom",
        sites: Optional[List[str]] = None,
        max_first_run: int = 200,
        hours_old: int = 48,
        company_name: str = "JobSpy (LinkedIn/Indeed)",
        slug: str = "jobspy",
    ):
        self.search_term = search_term
        self.location = location
        self.sites = sites or ["indeed", "linkedin"]
        self.max_first_run = max_first_run
        self.hours_old = hours_old
        self.company_name = company_name
        self.slug = slug
        self.is_baselined = False

    async def list_jobs(self) -> List[RawJob]:
        def _scrape():
            return scrape_jobs(
                site_name=self.sites,
                search_term=self.search_term,
                location=self.location,
                results_wanted=self.max_first_run,
                hours_old=self.hours_old,
                linkedin_fetch_description=False,
            )

        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, _scrape)

        companies_cache = {}
        try:
            from jobhunt.core.db import async_session_factory
            from sqlalchemy import select
            from jobhunt.core.models import Company

            async with async_session_factory() as session:
                res = await session.execute(select(Company.id, Company.name))
                for cid, cname in res.all():
                    companies_cache[cname.lower()] = (cid, cname)
        except Exception:
            pass

        def resolve_company(row_comp: str, cache: dict, sentinel_id: int):
            c_clean = str(row_comp or "").strip()
            c_low = c_clean.lower()
            if c_low in cache:
                return cache[c_low][0], cache[c_low][1]
            for k, (cid, name) in cache.items():
                if len(k) >= 3 and (c_low.startswith(k) or k.startswith(c_low)):
                    return cid, name
            for k, (cid, name) in cache.items():
                if len(k) >= 4 and len(c_low) >= 4 and (k in c_low or c_low in k):
                    return cid, name
            return sentinel_id, row_comp or "JobSpy (LinkedIn/Indeed)"

        sentinel_id = companies_cache.get("jobspy (linkedin/indeed)", (116, "JobSpy (LinkedIn/Indeed)"))[0]

        seen = set()
        raw_jobs: List[RawJob] = []

        if df is not None and not df.empty:
            for _, row in df.iterrows():
                comp = str(row.get("company") or "").strip()
                title = str(row.get("title") or "").strip()
                loc = str(row.get("location") or "").strip()
                url = str(row.get("job_url") or "").strip()
                desc = str(row.get("description") or "").strip() if row.get("description") else ""
                if desc.lower() == "nan":
                    desc = ""

                key = (comp.lower(), title.lower(), loc.lower())
                if key in seen:
                    continue
                seen.add(key)

                ext_id = hashlib.sha256(f"{comp}{title}{loc}{url}".encode()).hexdigest()[:16]

                posted_at = None
                date_posted = row.get("date_posted")
                if date_posted and str(date_posted).lower() != "nat":
                    try:
                        if hasattr(date_posted, "to_pydatetime"):
                            posted_at = date_posted.to_pydatetime()
                        elif isinstance(date_posted, datetime):
                            posted_at = date_posted
                        elif isinstance(date_posted, str):
                            posted_at = datetime.fromisoformat(date_posted)
                        if posted_at and posted_at.tzinfo is None:
                            posted_at = posted_at.replace(tzinfo=timezone.utc)
                    except Exception:
                        posted_at = None

                comp_id, comp_name = resolve_company(comp or self.company_name, companies_cache, sentinel_id)
                raw_jobs.append(
                    RawJob(
                        source_type=self.type,
                        external_id=ext_id,
                        company_id=comp_id,
                        company_name=comp_name,
                        title=title,
                        location_raw=loc,
                        apply_url=url,
                        description=desc,
                        posted_at=posted_at,
                    )
                )

        is_base = getattr(self, "is_baselined", False)
        if not is_base and len(raw_jobs) > self.max_first_run:
            raw_jobs = raw_jobs[: self.max_first_run]

        return raw_jobs

    async def get_detail(self, job: RawJob) -> RawJob:
        return job

