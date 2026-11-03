from datetime import datetime
import email.utils
from typing import List, Optional
import xml.etree.ElementTree as ET
import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from jobhunt.core.schemas import RawJob

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like"
    " Gecko) Chrome/130.0 Safari/537.36"
)


class TeamtailorSource:
    type: str = "teamtailor"

    def __init__(self, slug: str, company_name: Optional[str] = None):
        self.slug = slug
        self.company_name = company_name or slug
        self.base_url = f"https://{slug}.teamtailor.com/jobs.rss"

    async def list_jobs(self) -> List[RawJob]:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=15.0) as client:
            r = await client.get(self.base_url)
            r.raise_for_status()

            root = ET.fromstring(r.content)
            channel = root.find("channel")
            items = channel.findall("item") if channel is not None else []

            raw_jobs: List[RawJob] = []
            for it in items:
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()

                desc_html = it.findtext("description") or ""
                description = None
                if desc_html:
                    tree = HTMLParser(desc_html)
                    description = tree.text(separator="\n", strip=True)

                pub_date_str = it.findtext("pubDate")
                posted_at = None
                if pub_date_str:
                    try:
                        posted_at = email.utils.parsedate_to_datetime(pub_date_str)
                    except Exception:
                        posted_at = None

                city = None
                country = None
                for el in it.iter():
                    if el.tag.endswith("city") and el.text:
                        city = el.text.strip()
                    if el.tag.endswith("country") and el.text:
                        country = el.text.strip()

                if city and country:
                    location_raw = f"{city}, {country}"
                elif city:
                    location_raw = str(city)
                elif country:
                    location_raw = str(country)
                else:
                    location_raw = "Unknown"

                raw_jobs.append(
                    RawJob(
                        source_type=self.type,
                        external_id=link,
                        company_name=self.company_name,
                        title=title,
                        location_raw=location_raw,
                        apply_url=link,
                        description=description,
                        posted_at=posted_at,
                    )
                )
            return raw_jobs

    async def get_detail(self, job: RawJob) -> RawJob:
        return job

