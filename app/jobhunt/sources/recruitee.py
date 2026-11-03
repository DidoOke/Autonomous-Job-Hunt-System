from datetime import datetime, timezone
from typing import List, Optional
import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from jobhunt.core.schemas import RawJob

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like"
    " Gecko) Chrome/130.0 Safari/537.36"
)


class RecruiteeSource:
    type: str = "recruitee"

    def __init__(self, slug: str, company_name: Optional[str] = None):
        self.slug = slug
        self.company_name = company_name or slug
        self.base_url = f"https://{slug}.recruitee.com/api/offers/"

    async def list_jobs(self) -> List[RawJob]:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=15.0) as client:
            r = await client.get(self.base_url)
            r.raise_for_status()
            data = r.json()
            offers = data.get("offers", []) if isinstance(data, dict) else []

            raw_jobs: List[RawJob] = []
            for offer in offers:
                city = offer.get("city")
                country_code = offer.get("country_code")
                if city and country_code:
                    location_raw = f"{city}, {country_code}"
                elif city:
                    location_raw = str(city)
                elif country_code:
                    location_raw = str(country_code)
                else:
                    location_raw = offer.get("location")

                desc_html = offer.get("description")
                description = None
                if desc_html:
                    tree = HTMLParser(desc_html)
                    description = tree.text(separator="\n", strip=True)

                created_at_str = offer.get("created_at")
                posted_at = None
                if created_at_str:
                    try:
                        clean_str = created_at_str.replace(" UTC", "+00:00").replace("Z", "+00:00")
                        posted_at = datetime.fromisoformat(clean_str)
                    except Exception:
                        posted_at = None

                apply_url = offer.get("careers_url") or offer.get("careers_apply_url")

                raw_jobs.append(
                    RawJob(
                        source_type=self.type,
                        external_id=str(offer.get("id")),
                        company_name=self.company_name,
                        title=offer.get("title", ""),
                        location_raw=location_raw,
                        apply_url=apply_url,
                        description=description,
                        posted_at=posted_at,
                    )
                )
            return raw_jobs

    async def get_detail(self, job: RawJob) -> RawJob:
        return job

