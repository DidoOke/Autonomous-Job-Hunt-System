from datetime import datetime, timezone
from typing import List, Optional
import httpx

from jobhunt.core.schemas import RawJob

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like"
    " Gecko) Chrome/130.0 Safari/537.36"
)


class AmazonSource:
    type: str = "amazon"

    def __init__(self, company_name: str = "Amazon", slug: str = "amazon"):
        self.company_name = company_name
        self.slug = slug
        self.base_url = "https://www.amazon.jobs/en/search.json"

    async def list_jobs(self) -> List[RawJob]:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=25.0) as client:
            raw_jobs: List[RawJob] = []
            offset = 0
            limit = 100
            total_hits = None

            while True:
                url = (
                    f"{self.base_url}?base_query=&country[]=GBR&normalized_country_code[]=GBR"
                    f"&result_limit={limit}&offset={offset}"
                )
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()

                if total_hits is None:
                    total_hits = data.get("hits", 0)

                jobs = data.get("jobs", [])
                if not jobs:
                    break

                for j in jobs:
                    ext_id = str(j.get("id_icims") or j.get("id"))
                    title = j.get("title", "")
                    location_raw = j.get("location") or "United Kingdom"
                    job_path = j.get("job_path")
                    if job_path:
                        apply_url = f"https://www.amazon.jobs{job_path}"
                    else:
                        apply_url = (
                            j.get("url_next_step")
                            or f"https://www.amazon.jobs/en/jobs/{ext_id}"
                        )

                    description = j.get("description") or j.get("description_short")

                    posted_date_str = j.get("posted_date")
                    posted_at = None
                    if posted_date_str:
                        try:
                            posted_at = datetime.strptime(
                                posted_date_str, "%B %d, %Y"
                            ).replace(tzinfo=timezone.utc)
                        except Exception:
                            posted_at = None

                    raw_jobs.append(
                        RawJob(
                            source_type=self.type,
                            external_id=ext_id,
                            company_name=self.company_name,
                            title=title,
                            location_raw=location_raw,
                            apply_url=apply_url,
                            description=description,
                            posted_at=posted_at,
                        )
                    )

                offset += len(jobs)
                if total_hits is not None and offset >= total_hits:
                    break
                if len(jobs) < limit:
                    break

            return raw_jobs

    async def get_detail(self, job: RawJob) -> RawJob:
        return job

