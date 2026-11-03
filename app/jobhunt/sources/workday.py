from datetime import datetime, timedelta, timezone
import re
from typing import List, Optional, Tuple
import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from jobhunt.core.schemas import RawJob

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like"
    " Gecko) Chrome/130.0 Safari/537.36"
)

UK_KEYWORDS = [
    "united kingdom", " uk", ", uk", "(uk)", "great britain", "england",
    "scotland", "wales", "northern ireland", "london", "manchester", "salford",
    "leeds", "edinburgh", "glasgow", "bristol", "cambridge", "oxford",
    "birmingham", "belfast", "reading", "newcastle", "sheffield", "liverpool",
    "nottingham", "milton keynes", "guildford", "brighton", "bath", "exeter",
    "warrington", "knutsford", "cheltenham", "macclesfield", "crewe", "wilmslow",
    "coventry", "southampton", "swindon"
]


def parse_posted_on(posted_on: Optional[str]) -> Optional[datetime]:
    if not posted_on:
        return None
    now = datetime.now(timezone.utc)
    s = posted_on.strip().lower()
    if "today" in s:
        return now
    if "yesterday" in s:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s+days?\s+ago", s)
    if m:
        days = int(m.group(1))
        return now - timedelta(days=days)
    m = re.search(r"(\d+)\+?\s+months?\s+ago", s)
    if m:
        months = int(m.group(1))
        return now - timedelta(days=months * 30)
    return None


class WorkdaySource:
    type: str = "workday"

    def __init__(
        self,
        tenant: str,
        wd: str,
        site: str,
        company_name: Optional[str] = None,
    ):
        self.tenant = tenant
        self.wd = wd
        self.site = site
        self.company_name = company_name or tenant
        self.slug = tenant
        self.base_url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
        self.jobs_url = f"{self.base_url}/jobs"
        self.uk_facet_param: Optional[str] = None
        self.uk_facet_id: Optional[str] = None
        self.is_fallback: bool = False
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
        return self._client

    def _search_facets(self, flist: list) -> Optional[Tuple[str, str]]:
        for f in flist:
            param = f.get("facetParameter")
            for v in f.get("values", []):
                if "facetParameter" in v:
                    res = self._search_facets([v])
                    if res:
                        return res
                elif isinstance(v, dict):
                    desc = (v.get("descriptor") or "").strip().lower()
                    if desc == "united kingdom":
                        return param, v.get("id")
                    elif "country" in (param or "").lower() and "united kingdom" in desc:
                        return param, v.get("id")
        return None

    async def _discover_facets(self) -> None:
        if self.uk_facet_id is not None or self.is_fallback:
            return

        client = await self._get_client()
        try:
            r = await client.post(self.jobs_url, json={"limit": 1})
            if r.status_code == 200:
                body = r.json()
                facets = body.get("facets", [])
                res = self._search_facets(facets)
                if res:
                    self.uk_facet_param, self.uk_facet_id = res
                    self.is_fallback = False
                else:
                    self.uk_facet_id = None
                    self.is_fallback = True
            else:
                self.is_fallback = True
        except Exception:
            self.is_fallback = True

    def _map_posting(self, p: dict) -> RawJob:
        bullet_fields = p.get("bulletFields") or []
        external_path = p.get("externalPath", "")
        if bullet_fields:
            external_id = str(bullet_fields[0])
        elif external_path:
            external_id = external_path.split("/")[-1].split("?")[0]
        else:
            external_id = str(p.get("title", ""))

        title = p.get("title", "")
        location_raw = p.get("locationsText")
        if location_raw and re.match(r"^\d+\s+Locations?$", location_raw, re.I):
            location_raw = f"{location_raw}, GB"
        posted_at = parse_posted_on(p.get("postedOn"))
        apply_url = f"https://{self.tenant}.{self.wd}.myworkdayjobs.com/en-US/{self.site}{external_path}"

        return RawJob(
            source_type=self.type,
            external_id=external_id,
            company_name=self.company_name,
            title=title,
            location_raw=location_raw,
            apply_url=apply_url,
            description=None,
            posted_at=posted_at,
        )

    async def list_jobs(self) -> List[RawJob]:
        await self._discover_facets()
        client = await self._get_client()
        raw_jobs: List[RawJob] = []

        if not self.is_fallback and self.uk_facet_id:
            offset = 0
            limit = 20
            total_count = None
            facet_param = self.uk_facet_param or "locationCountry"
            while True:
                payload = {
                    "appliedFacets": {facet_param: [self.uk_facet_id]},
                    "limit": limit,
                    "offset": offset,
                    "searchText": "",
                }
                r = await client.post(self.jobs_url, json=payload)
                r.raise_for_status()
                data = r.json()
                if total_count is None:
                    total_count = data.get("total", 0)
                postings = data.get("jobPostings", [])
                if not postings:
                    break
                for p in postings:
                    raw_jobs.append(self._map_posting(p))
                offset += len(postings)
                if total_count is not None and offset >= total_count:
                    break
        else:
            # Fallback for tenants without UK country facet (e.g. Barclays, Kainos)
            limit = 20
            total_count = None
            for page in range(3):
                offset = page * limit
                payload = {
                    "limit": limit,
                    "offset": offset,
                    "searchText": "",
                }
                r = await client.post(self.jobs_url, json=payload)
                if r.status_code != 200:
                    break
                data = r.json()
                if total_count is None:
                    total_count = data.get("total", 0)
                postings = data.get("jobPostings", [])
                if not postings:
                    break
                for p in postings:
                    loc = (p.get("locationsText") or "").lower()
                    if any(k in loc for k in UK_KEYWORDS):
                        raw_jobs.append(self._map_posting(p))
                if total_count is not None and (page + 1) * limit >= total_count:
                    break

        return raw_jobs

    async def get_detail(self, job: RawJob) -> RawJob:
        if job.description:
            return job

        client = await self._get_client()
        if f"/{self.site}" in (job.apply_url or ""):
            external_path = job.apply_url.split(f"/{self.site}")[-1]
            detail_url = f"{self.base_url}{external_path}"
        else:
            detail_url = f"{self.base_url}/job/{job.external_id}"

        r = await client.get(detail_url)
        if r.status_code == 200:
            data = r.json()
            job_info = data.get("jobPostingInfo") or {}
            desc_html = job_info.get("jobDescription")
            if desc_html:
                tree = HTMLParser(desc_html)
                job.description = tree.text(separator="\n", strip=True)

            external_url = job_info.get("externalUrl")
            if external_url:
                job.apply_url = external_url

            if job.posted_at is None and job_info.get("startDate"):
                try:
                    job.posted_at = datetime.fromisoformat(job_info["startDate"])
                except Exception:
                    pass

        return job
