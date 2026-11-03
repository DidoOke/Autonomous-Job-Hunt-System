import asyncio
from datetime import datetime, timezone
import json
import logging
import re
from typing import List, Optional
from playwright.async_api import async_playwright

from jobhunt.core.schemas import RawJob

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class PlaywrightSource:
    type: str = "playwright"

    def __init__(
        self,
        company_name: str,
        start_url: str,
        xhr_pattern: Optional[str] = None,
        wait_selector: Optional[str] = None,
        max_first_run: int = 100,
        slug: Optional[str] = None,
    ):
        self.company_name = company_name
        self.start_url = start_url
        self.xhr_pattern = xhr_pattern
        self.wait_selector = wait_selector
        self.max_first_run = max_first_run
        self.slug = slug or company_name.lower().replace(" ", "").replace("_", "")

    async def list_jobs(self) -> List[RawJob]:
        captured_jobs: List[RawJob] = []
        seen_ext_ids = set()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            async def handle_response(response):
                try:
                    if response.status != 200:
                        return
                    url = response.url
                    if self.xhr_pattern and not re.search(self.xhr_pattern, url, re.IGNORECASE):
                        if not any(
                            k in url.lower()
                            for k in ["pcsx/search", "jobs.json", "gateway/api/v1/graphql"]
                        ):
                            return

                    ct = response.headers.get("content-type", "")
                    if "json" not in ct:
                        return

                    data = await response.json()
                    self._extract_jobs(data, url, captured_jobs, seen_ext_ids)
                except Exception as e:
                    logger.debug("Error parsing response: %s", e)

            page.on("response", handle_response)

            try:
                await page.goto(self.start_url, wait_until="networkidle", timeout=45000)
            except Exception as e:
                logger.warning("Timeout or error navigating to %s: %s", self.start_url, e)

            if self.wait_selector:
                try:
                    await page.wait_for_selector(self.wait_selector, timeout=10000)
                except Exception:
                    pass

            # Wait a few seconds for background requests to settle
            await page.wait_for_timeout(5000)

            content = await page.content()
            if not captured_jobs and (
                "captcha" in content.lower() or "cf-challenge" in content.lower()
            ):
                logger.warning("Playwright blocked by CAPTCHA for %s", self.company_name)

            await browser.close()

        return captured_jobs[: self.max_first_run]

    def _extract_jobs(
        self, data: dict, url: str, captured: List[RawJob], seen: set
    ):
        if not isinstance(data, dict):
            return

        # 1. Microsoft PCSX Search format
        pos_list = []
        if (
            "data" in data
            and isinstance(data["data"], dict)
            and "positions" in data["data"]
        ):
            pos_list = data["data"]["positions"]
        elif "positions" in data and isinstance(data["positions"], list):
            pos_list = data["positions"]

        if pos_list and isinstance(pos_list, list):
            for pos in pos_list:
                if not isinstance(pos, dict):
                    continue
                ext_id = str(
                    pos.get("id") or pos.get("atsJobId") or pos.get("displayJobId")
                )
                if not ext_id or ext_id in seen:
                    continue
                seen.add(ext_id)
                title = pos.get("name") or "Unknown"
                locs = pos.get("locations") or pos.get("standardizedLocations") or []
                loc_raw = (
                    ", ".join(locs)
                    if isinstance(locs, list) and locs
                    else "United Kingdom"
                )
                posted_ts = pos.get("postedTs")
                posted_at = None
                if posted_ts:
                    try:
                        posted_at = datetime.fromtimestamp(posted_ts, tz=timezone.utc)
                    except Exception:
                        pass
                apply_url = f"https://jobs.careers.microsoft.com/global/en/share/{ext_id}"
                desc = pos.get("description") or ""

                captured.append(
                    RawJob(
                        source_type="playwright",
                        external_id=ext_id,
                        company_name=self.company_name,
                        title=title,
                        location_raw=loc_raw,
                        apply_url=apply_url,
                        description=desc,
                        posted_at=posted_at,
                    )
                )
            return

        # 2. NatWest Talemetry format
        if "entries" in data and isinstance(data["entries"], list):
            for entry in data["entries"]:
                if not isinstance(entry, dict):
                    continue
                ext_id = str(entry.get("id") or entry.get("talemetry_job_id"))
                if not ext_id or ext_id in seen:
                    continue
                seen.add(ext_id)
                title = entry.get("title") or "Unknown"
                loc_obj = entry.get("location") or {}
                if isinstance(loc_obj, dict):
                    locality = loc_obj.get("locality") or loc_obj.get("name") or ""
                    country = loc_obj.get("country") or ""
                    loc_raw = f"{locality}, {country}".strip(", ") or "United Kingdom"
                else:
                    loc_raw = str(loc_obj) or "United Kingdom"
                permalink = entry.get("permalink")
                apply_url = (
                    f"https://jobs.natwestgroup.com/jobs/{permalink}"
                    if permalink
                    else f"https://jobs.natwestgroup.com/jobs/{ext_id}"
                )
                captured.append(
                    RawJob(
                        source_type="playwright",
                        external_id=ext_id,
                        company_name=self.company_name,
                        title=title,
                        location_raw=loc_raw,
                        apply_url=apply_url,
                        description="",
                        posted_at=None,
                    )
                )
            return

        # 3. Goldman Sachs GraphQL format
        rs = (
            data.get("data", {}).get("roleSearch")
            if isinstance(data.get("data"), dict)
            else None
        )
        if rs and isinstance(rs, dict) and "items" in rs:
            items = rs.get("items") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                ext_id = str(
                    item.get("roleId")
                    or item.get("externalSource", {}).get("sourceId")
                )
                if not ext_id or ext_id in seen:
                    continue
                seen.add(ext_id)
                title = item.get("jobTitle") or "Unknown"
                locs = item.get("locations") or []
                loc_strs = []
                for l in locs:
                    if isinstance(l, dict):
                        city = l.get("city", "")
                        country = l.get("country", "")
                        loc_part = f"{city}, {country}".strip(", ")
                        if loc_part:
                            loc_strs.append(loc_part)
                loc_raw = "; ".join(loc_strs) if loc_strs else "United Kingdom"
                apply_url = f"https://higher.gs.com/roles/{ext_id}"

                captured.append(
                    RawJob(
                        source_type="playwright",
                        external_id=ext_id,
                        company_name=self.company_name,
                        title=title,
                        location_raw=loc_raw,
                        apply_url=apply_url,
                        description="",
                        posted_at=None,
                    )
                )
            return

        # 4. Workday CXS format fallback
        if "jobPostings" in data and isinstance(data["jobPostings"], list):
            for jp in data["jobPostings"]:
                ext_id = str(jp.get("bulletFields", [None])[0] or jp.get("title"))
                if not ext_id or ext_id in seen:
                    continue
                seen.add(ext_id)
                title = jp.get("title") or "Unknown"
                loc_raw = jp.get("locationsText") or "United Kingdom"
                apply_url = jp.get("externalPath")
                captured.append(
                    RawJob(
                        source_type="playwright",
                        external_id=ext_id,
                        company_name=self.company_name,
                        title=title,
                        location_raw=loc_raw,
                        apply_url=apply_url,
                        description="",
                        posted_at=None,
                    )
                )

    async def get_detail(self, job: RawJob) -> RawJob:
        return job

