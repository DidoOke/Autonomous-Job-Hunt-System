import hashlib
import re
from typing import List, Optional
import httpx
from jobhunt.core.schemas import RawJob
from selectolax.lexbor import LexborHTMLParser

UK_KEYWORDS = [
    "united kingdom", "uk", "london", "manchester", "edinburgh",
    "leeds", "bristol", "glasgow", "cambridge", "oxford",
    "salford", "birmingham", "belfast",
]
UK_PATTERN = re.compile(r'\b(?:' + '|'.join(UK_KEYWORDS) + r')\b', re.IGNORECASE)


class SimplifyGitHubSource:
    type: str = "simplify_github"

    def __init__(
        self,
        repo_url: str = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
        max_first_run: int = 300,
        company_name: str = "Simplify (aggregator)",
        slug: str = "simplify",
    ):
        self.repo_url = repo_url
        self.max_first_run = max_first_run
        self.company_name = company_name
        self.slug = slug
        self.is_baselined = False

    async def list_jobs(self) -> List[RawJob]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30.0) as client:
            r = await client.get(self.repo_url)
            r.raise_for_status()
            content = r.text

        parsed_rows = []
        if "<table" in content.lower():
            tree = LexborHTMLParser(content)
            rows = tree.css("table tbody tr")
            if not rows:
                rows = tree.css("table tr")
            last_company = "Unknown"
            for row in rows:
                tds = row.css("td")
                if len(tds) < 4:
                    continue
                c0 = tds[0].text().strip()
                if "↳" in c0 or not c0:
                    company = last_company
                else:
                    comp_a = tds[0].css_first("a")
                    company = comp_a.text().strip() if comp_a else c0
                    company = re.sub(r"^[^\w\s]+", "", company).strip()
                    last_company = company

                role = tds[1].text().strip()
                loc = tds[2].text().strip()
                app_a = tds[3].css_first("a")
                apply_url = app_a.attributes.get("href", "").strip() if app_a else ""
                if not apply_url:
                    m = re.search(r'href=[\'"]([^\'"]+)[\'"]', tds[3].html or "")
                    if m:
                        apply_url = m.group(1).strip()

                if UK_PATTERN.search(loc):
                    parsed_rows.append((company, role, loc, apply_url))
        else:
            last_company = "Unknown"
            for line in content.splitlines():
                line_s = line.strip()
                if not line_s.startswith("|"):
                    continue
                parts = [p.strip() for p in line_s.split("|")[1:-1]]
                if len(parts) < 4 or "---" in parts[0] or "Company" in parts[0]:
                    continue
                c0 = parts[0]
                if "↳" in c0 or not c0:
                    company = last_company
                else:
                    m_comp = re.search(r"\[([^\]]+)\]", c0)
                    company = m_comp.group(1) if m_comp else c0
                    company = re.sub(r"^[^\w\s]+", "", company).strip()
                    last_company = company

                role = parts[1]
                loc = parts[2]
                app_part = parts[3]
                m_url = re.search(r'\((https?://[^\)]+)\)', app_part)
                if not m_url:
                    m_url = re.search(r'href=[\'"]([^\'"]+)[\'"]', app_part)
                apply_url = m_url.group(1) if m_url else ""

                if UK_PATTERN.search(loc):
                    parsed_rows.append((company, role, loc, apply_url))

        is_base = getattr(self, "is_baselined", False)
        if not is_base and len(parsed_rows) > self.max_first_run:
            parsed_rows = parsed_rows[: self.max_first_run]

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
            c_clean = re.sub(r"^[^\w\s]+", "", row_comp).strip()
            c_low = c_clean.lower()
            if c_low in cache:
                return cache[c_low][0], cache[c_low][1]
            for k, (cid, name) in cache.items():
                if len(k) >= 3 and (c_low.startswith(k) or k.startswith(c_low)):
                    return cid, name
            for k, (cid, name) in cache.items():
                if len(k) >= 4 and len(c_low) >= 4 and (k in c_low or c_low in k):
                    return cid, name
            return sentinel_id, row_comp

        sentinel_id = companies_cache.get("simplify (aggregator)", (115, "Simplify (aggregator)"))[0]
        raw_jobs: List[RawJob] = []
        for company, role, loc, apply_url in parsed_rows:
            comp_id, comp_name = resolve_company(company, companies_cache, sentinel_id)
            ext_id = hashlib.sha256(f"{comp_name}|{role}|{apply_url}".encode()).hexdigest()[:16]
            raw_jobs.append(
                RawJob(
                    source_type=self.type,
                    external_id=ext_id,
                    company_id=comp_id,
                    company_name=comp_name,
                    title=role,
                    location_raw=loc,
                    apply_url=apply_url,
                    description="",
                    posted_at=None,
                )
            )
        return raw_jobs

    async def get_detail(self, job: RawJob) -> RawJob:
        return job

