import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET
import httpx
from selectolax.lexbor import LexborHTMLParser

from jobhunt.core.logging import logger
from jobhunt.core.schemas import RawJob

UA = "Mozilla/5.0 (compatible; job-hunt-bot/0.1)"


class SitemapJSONLDSource:
    """Source adapter for sitemaps containing JSON-LD or HTML5 Microdata JobPosting.

    Freshness note: <lastmod> in sitemaps reflects sitemap generation time rather
    than genuine job posting dates. Therefore, posted_at is set to None by default
    and only populated if the job page explicitly provides an itemprop="datePosted"
    or JSON-LD datePosted field.
    """
    type: str = "sitemap_jsonld"

    def __init__(
        self,
        company_name: str,
        sitemap_url: str,
        url_filter: str = "/job/",
        max_first_run: int = 200,
        slug: Optional[str] = None,
    ):
        self.company_name = company_name
        self.sitemap_url = sitemap_url
        self.url_filter = url_filter
        self.max_first_run = max_first_run
        self.slug = slug or company_name.lower().replace(" ", "").replace("_", "").replace("-", "")
        self.is_baselined = False

    async def list_jobs(self) -> List[RawJob]:
        headers = {"User-Agent": UA}
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=25.0) as client:
            r = await client.get(self.sitemap_url)
            r.raise_for_status()

            root = ET.fromstring(r.text)
            urls_with_mod: List[tuple[str, Optional[datetime]]] = []

            # Handle <sitemapindex>
            if "sitemapindex" in root.tag.lower():
                child_sitemaps = []
                for elem in root.iter():
                    if elem.tag.endswith("loc") and elem.text:
                        child_sitemaps.append(elem.text.strip())
                # Filter child sitemaps containing job/vacanc/career or use all
                selected = [
                    cs for cs in child_sitemaps
                    if any(k in cs.lower() for k in ["job", "vacanc", "career"])
                ] or child_sitemaps

                for cs in selected:
                    try:
                        cr = await client.get(cs)
                        croot = ET.fromstring(cr.text)
                        for url_node in croot:
                            loc_elem = url_node.find("{http://www.google.com/schemas/sitemap/0.9}loc")
                            lastmod_elem = url_node.find("{http://www.google.com/schemas/sitemap/0.9}lastmod")
                            if loc_elem is not None and loc_elem.text:
                                lmod = None
                                if lastmod_elem is not None and lastmod_elem.text:
                                    try:
                                        lmod = datetime.fromisoformat(lastmod_elem.text.strip().replace("Z", "+00:00"))
                                    except Exception:
                                        pass
                                urls_with_mod.append((loc_elem.text.strip(), lmod))
                    except Exception as e:
                        logger.warning("sitemap_child_fetch_error", url=cs, error=str(e))
            else:
                for url_node in root.findall("{http://www.google.com/schemas/sitemap/0.9}url"):
                    loc_elem = url_node.find("{http://www.google.com/schemas/sitemap/0.9}loc")
                    lastmod_elem = url_node.find("{http://www.google.com/schemas/sitemap/0.9}lastmod")
                    if loc_elem is not None and loc_elem.text:
                        lmod = None
                        if lastmod_elem is not None and lastmod_elem.text:
                            try:
                                lmod = datetime.fromisoformat(lastmod_elem.text.strip().replace("Z", "+00:00"))
                            except Exception:
                                pass
                        urls_with_mod.append((loc_elem.text.strip(), lmod))
                if not urls_with_mod:
                    for elem in root.iter():
                        if elem.tag.endswith("loc") and elem.text:
                            urls_with_mod.append((elem.text.strip(), None))

        # Deduplicate URLs while preserving order
        seen_urls = set()
        unique_urls_with_mod = []
        for u, m in urls_with_mod:
            if u not in seen_urls:
                seen_urls.add(u)
                unique_urls_with_mod.append((u, m))

        # 2. Filter URLs matching url_filter
        filtered = [
            (u, m) for (u, m) in unique_urls_with_mod
            if self.url_filter.lower() in u.lower()
        ]

        # 3. Filter by lastmod if present (keep within last 30 days or keep all if missing)
        now = datetime.now(timezone.utc)
        cutoff_30d = now - timedelta(days=30)
        kept = []
        for u, m in filtered:
            if m is not None:
                # Ensure timezone aware
                if m.tzinfo is None:
                    m = m.replace(tzinfo=timezone.utc)
                if m >= cutoff_30d:
                    kept.append((u, m))
            else:
                kept.append((u, None))

        # 4. Cap at max_first_run on first run if not baselined
        is_base = getattr(self, "is_baselined", False)
        if not is_base and len(kept) > self.max_first_run:
            kept = kept[: self.max_first_run]

        # 5. Return RawJob stubs
        raw_jobs: List[RawJob] = []
        for url, lmod in kept:
            ext_id = hashlib.sha256(url.encode()).hexdigest()[:16]
            raw_jobs.append(
                RawJob(
                    source_type=self.type,
                    external_id=ext_id,
                    company_name=self.company_name,
                    title="",
                    location_raw="",
                    apply_url=url,
                    description="",
                    posted_at=None,
                )
            )
        return raw_jobs

    async def get_detail(self, job: RawJob) -> RawJob:
        await asyncio.sleep(0.5)
        job.posted_at = None
        url = job.apply_url
        if not url:
            return job

        headers = {"User-Agent": UA}
        try:
            async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20.0) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    return job
                html_text = r.text
        except Exception as e:
            logger.warning("sitemap_get_detail_error", url=url, error=str(e))
            return job

        tree = LexborHTMLParser(html_text)

        # 1. Try JSON-LD first
        ld_scripts = tree.css('script[type="application/ld+json"]')
        for s in ld_scripts:
            try:
                data = json.loads(s.text(), strict=False)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict):
                        jp_candidates = []
                        if "@graph" in item and isinstance(item["@graph"], list):
                            jp_candidates = [g for g in item["@graph"] if isinstance(g, dict) and g.get("@type") == "JobPosting"]
                        elif item.get("@type") == "JobPosting" or (isinstance(item.get("@type"), list) and "JobPosting" in item.get("@type")):
                            jp_candidates = [item]

                        if jp_candidates:
                            jp = jp_candidates[0]
                            job.title = jp.get("title", "") or job.title
                            desc_html = jp.get("description", "")
                            if desc_html:
                                d_tree = LexborHTMLParser(desc_html)
                                job.description = d_tree.text(separator="\n", strip=True)

                            dp = jp.get("datePosted")
                            if dp:
                                try:
                                    job.posted_at = datetime.fromisoformat(dp.replace("Z", "+00:00"))
                                except Exception:
                                    pass

                            loc = jp.get("jobLocation")
                            if isinstance(loc, list):
                                cities = []
                                for l in loc:
                                    if isinstance(l, dict) and "address" in l:
                                        addr = l["address"]
                                        c = addr.get("addressLocality") or ""
                                        co = addr.get("addressCountry") or ""
                                        if isinstance(co, dict):
                                            co = co.get("name") or ""
                                        cities.append(f"{c}, {co}".strip(", "))
                                job.location_raw = "; ".join(cities)
                            elif isinstance(loc, dict) and "address" in loc:
                                addr = loc["address"]
                                c = addr.get("addressLocality") or ""
                                co = addr.get("addressCountry") or ""
                                if isinstance(co, dict):
                                    co = co.get("name") or ""
                                job.location_raw = f"{c}, {co}".strip(", ")

                            job.apply_url = jp.get("url") or url
                            logger.debug("jsonld_extracted", title=job.title, location=job.location_raw)
                            return job
            except Exception:
                pass

        # 2. Try HTML5 Microdata fallback
        root = tree.css_first('[itemtype*="JobPosting"]')
        if root is None and not tree.css('[itemprop]'):
            return job

        fields_found = []
        fields_missing = []

        title_el = (root.css_first('[itemprop="title"]') if root else None) or tree.css_first('[itemprop="title"]')
        if title_el:
            job.title = title_el.attributes.get("content") or title_el.text(strip=True)
            fields_found.append("title")
        else:
            fields_missing.append("title")

        desc_el = (root.css_first('[itemprop="description"]') if root else None) or tree.css_first('[itemprop="description"]')
        if desc_el:
            raw_d = desc_el.attributes.get("content") or desc_el.html
            d_tree = LexborHTMLParser(raw_d)
            job.description = d_tree.text(separator="\n", strip=True)
            fields_found.append("description")
        else:
            fields_missing.append("description")

        date_el = (root.css_first('[itemprop="datePosted"]') if root else None) or tree.css_first('[itemprop="datePosted"]')
        if date_el:
            dp = date_el.attributes.get("content") or date_el.attributes.get("datetime") or date_el.text(strip=True)
            try:
                job.posted_at = datetime.fromisoformat(dp.replace("Z", "+00:00"))
                fields_found.append("datePosted")
            except Exception:
                job.posted_at = None
                fields_missing.append("datePosted")
        else:
            job.posted_at = None
            fields_missing.append("datePosted")

        loc_el = (root.css_first('[itemprop="jobLocation"]') if root else None) or tree.css_first('[itemprop="jobLocation"]')
        loc_str = ""
        if loc_el:
            city_el = loc_el.css_first('[itemprop="addressLocality"]')
            country_el = loc_el.css_first('[itemprop="addressCountry"]')
            if city_el or country_el:
                c = city_el.text(strip=True) if city_el else ""
                co = country_el.text(strip=True) if country_el else ""
                loc_str = f"{c}, {co}".strip(", ")
            else:
                loc_str = loc_el.attributes.get("content") or loc_el.text(strip=True)

        if not loc_str:
            # Layout tokens fallback for SuccessFactors / Jobs2Web (e.g. BBC)
            tokens = (root.css(".joblayouttoken") if root else []) or tree.css(".joblayouttoken")
            for tok in tokens:
                txt = tok.text(strip=True)
                low = txt.lower()
                if any(low.startswith(p) for p in ["job requisition", "requisition id", "job closing", "closing date", "package description", "job details"]):
                    continue
                if job.title and job.title.lower() in low:
                    continue
                if len(txt) < 150 and any(c in txt for c in [",", "|", "London", "Salford", "Cardiff", "Glasgow", "Belfast", "Birmingham", "Manchester", "Guernsey", "Plymouth", "Sydney", "New York", "Los Angeles", "Nairobi", "Singapore", "Tbilisi"]):
                    loc_str = txt
                    break

        if not loc_str:
            m = re.search(r"/job/([^-/]+)-", url)
            if m:
                loc_str = m.group(1)

        if loc_str:
            job.location_raw = loc_str
            fields_found.append("jobLocation")
        else:
            fields_missing.append("jobLocation")

        job.apply_url = url
        logger.debug(
            "microdata_extracted",
            title=job.title,
            location=job.location_raw,
            found=fields_found,
            missing=fields_missing,
        )
        return job
