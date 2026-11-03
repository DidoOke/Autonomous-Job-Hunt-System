from datetime import datetime
from typing import List, Optional
import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from jobhunt.core.schemas import RawJob

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like"
    " Gecko) Chrome/130.0 Safari/537.36"
)


class AshbySource:
    type: str = "ashby"

    def __init__(self, slug: str, company_name: Optional[str] = None):
        self.slug = slug
        self.company_name = company_name or slug
        self.base_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"

    async def list_jobs(self) -> List[RawJob]:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=15.0) as client:
            r = await client.get(self.base_url)
            r.raise_for_status()
            data = r.json()
            items = data.get("jobs", []) if isinstance(data, dict) else []

            raw_jobs: List[RawJob] = []
            for item in items:
                desc_html = item.get("descriptionHtml")
                description = None
                if desc_html:
                    tree = HTMLParser(desc_html)
                    description = tree.text(separator="\n", strip=True)
                elif item.get("descriptionPlain"):
                    description = item.get("descriptionPlain")

                published_at_str = item.get("publishedAt")
                posted_at = None
                if published_at_str:
                    try:
                        posted_at = datetime.fromisoformat(
                            published_at_str.replace("Z", "+00:00")
                        )
                    except Exception:
                        posted_at = None

                raw_jobs.append(
                    RawJob(
                        source_type=self.type,
                        external_id=str(item.get("id")),
                        company_name=self.company_name,
                        title=item.get("title", ""),
                        location_raw=item.get("location"),
                        apply_url=item.get("jobUrl") or item.get("applyUrl"),
                        description=description,
                        posted_at=posted_at,
                    )
                )
            return raw_jobs

    async def get_detail(self, job: RawJob) -> RawJob:
        return job
