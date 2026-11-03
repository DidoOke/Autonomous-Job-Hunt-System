from datetime import datetime, timezone
from typing import List, Optional
import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser
from jobhunt.core.schemas import RawJob

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like"
    " Gecko) Chrome/130.0 Safari/537.36"
)


class GreenhouseSource:
    type: str = "greenhouse"

    def __init__(self, slug: str, company_name: Optional[str] = None):
        self.slug = slug
        self.company_name = company_name or slug
        self.base_url = f"https://boards-api.greenhouse.io/v1/boards/{slug}"

    async def list_jobs(self) -> List[RawJob]:
        url = f"{self.base_url}/jobs?content=true"
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
            items = data.get("jobs", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            raw_jobs: List[RawJob] = []
            for item in items:
                loc = item.get("location")
                loc_name = loc.get("name") if isinstance(loc, dict) else str(loc or "")
                pub_str = item.get("first_published") or item.get("updated_at")
                posted_at = None
                if pub_str:
                    try:
                        posted_at = datetime.fromisoformat(
                            pub_str.replace("Z", "+00:00")
                        ).astimezone(timezone.utc)
                    except Exception:
                        posted_at = None
                desc = None
                content_html = item.get("content")
                if content_html:
                    tree = HTMLParser(content_html)
                    desc = tree.text(separator="\n", strip=True)
                raw_jobs.append(
                    RawJob(
                        source_type=self.type,
                        external_id=str(item.get("id")),
                        company_name=self.company_name,
                        title=item.get("title", ""),
                        location_raw=loc_name,
                        apply_url=item.get("absolute_url"),
                        description=desc,
                        posted_at=posted_at,
                    )
                )
            return raw_jobs

    async def get_detail(self, job: RawJob) -> RawJob:
        if job.description:
            return job
        url = f"{self.base_url}/jobs/{job.external_id}"
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=15.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                content_html = data.get("content", "")
                if content_html:
                    tree = HTMLParser(content_html)
                    text = tree.text(separator="\n", strip=True)
                    job.description = text
        return job
