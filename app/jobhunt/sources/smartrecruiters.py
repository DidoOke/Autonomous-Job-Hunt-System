from datetime import datetime, timezone
from typing import List, Optional
import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from jobhunt.core.schemas import RawJob

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like"
    " Gecko) Chrome/130.0 Safari/537.36"
)


class SmartRecruitersSource:
    type: str = "smartrecruiters"

    def __init__(self, slug: str, company_name: Optional[str] = None):
        self.slug = slug
        self.company_name = company_name or slug
        self.base_url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": UA},
                timeout=15.0,
            )
        return self._client

    async def list_jobs(self) -> List[RawJob]:
        client = await self._get_client()
        raw_jobs: List[RawJob] = []
        offset = 0
        limit = 100

        while True:
            url = f"{self.base_url}?limit={limit}&offset={offset}"
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
            total_found = data.get("totalFound", 0)
            content = data.get("content", [])

            for item in content:
                loc = item.get("location") or {}
                city = loc.get("city")
                country = loc.get("country")
                if city and country:
                    location_raw = f"{city}, {country}"
                elif city:
                    location_raw = str(city)
                elif country:
                    location_raw = str(country)
                else:
                    location_raw = loc.get("fullLocation")

                released_date = item.get("releasedDate")
                posted_at = None
                if released_date:
                    try:
                        posted_at = datetime.fromisoformat(
                            released_date.replace("Z", "+00:00")
                        )
                    except Exception:
                        posted_at = None

                apply_url = (
                    item.get("ref")
                    or f"https://jobs.smartrecruiters.com/{self.slug}/{item.get('id')}"
                )

                raw_jobs.append(
                    RawJob(
                        source_type=self.type,
                        external_id=str(item.get("id")),
                        company_name=self.company_name,
                        title=item.get("name", ""),
                        location_raw=location_raw,
                        apply_url=apply_url,
                        description=None,
                        posted_at=posted_at,
                    )
                )

            offset += len(content)
            if offset >= total_found or not content:
                break

        return raw_jobs

    async def get_detail(self, job: RawJob) -> RawJob:
        if job.description:
            return job

        client = await self._get_client()
        url = f"{self.base_url}/{job.external_id}"
        r = await client.get(url)
        if r.status_code == 200:
            data = r.json()
            job_ad = data.get("jobAd") or {}
            sections = job_ad.get("sections") or {}
            job_desc = sections.get("jobDescription") or {}
            html_text = job_desc.get("text")
            if html_text:
                tree = HTMLParser(html_text)
                job.description = tree.text(separator="\n", strip=True)

            posting_url = data.get("postingUrl") or data.get("applyUrl")
            if posting_url:
                job.apply_url = posting_url

        return job
