from datetime import datetime, timezone
from typing import List, Optional
import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from jobhunt.core.schemas import RawJob

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like"
    " Gecko) Chrome/130.0 Safari/537.36"
)


class LeverSource:
    type: str = "lever"

    def __init__(self, slug: str, company_name: Optional[str] = None):
        self.slug = slug
        self.company_name = company_name or slug
        self.base_url = f"https://api.lever.co/v0/postings/{slug}"

    async def list_jobs(self) -> List[RawJob]:
        url = f"{self.base_url}?mode=json"
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=15.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            items = r.json()
            if not isinstance(items, list):
                return []

            raw_jobs: List[RawJob] = []
            for item in items:
                cats = item.get("categories") or {}
                location_raw = cats.get("location") if isinstance(cats, dict) else None

                description = item.get("descriptionPlain")
                if not description:
                    html_desc = item.get("description")
                    if html_desc:
                        tree = HTMLParser(html_desc)
                        description = tree.text(separator="\n", strip=True)

                created_at_ms = item.get("createdAt")
                posted_at = None
                if created_at_ms:
                    try:
                        posted_at = datetime.fromtimestamp(created_at_ms / 1000.0, tz=timezone.utc)
                    except Exception:
                        posted_at = None

                raw_jobs.append(
                    RawJob(
                        source_type=self.type,
                        external_id=str(item.get("id")),
                        company_name=self.company_name,
                        title=item.get("text", ""),
                        location_raw=location_raw,
                        apply_url=item.get("hostedUrl") or item.get("applyUrl"),
                        description=description,
                        posted_at=posted_at,
                    )
                )
            return raw_jobs

    async def get_detail(self, job: RawJob) -> RawJob:
        if job.description:
            return job

        url = f"{self.base_url}/{job.external_id}"
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=15.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                desc = data.get("descriptionPlain")
                if not desc:
                    html_desc = data.get("description")
                    if html_desc:
                        tree = HTMLParser(html_desc)
                        desc = tree.text(separator="\n", strip=True)
                job.description = desc
        return job
