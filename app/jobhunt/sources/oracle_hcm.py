from datetime import datetime, timezone
from typing import List, Optional
import httpx

from jobhunt.core.schemas import RawJob

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like"
    " Gecko) Chrome/130.0 Safari/537.36"
)


class OracleHCMSource:
    type: str = "oracle_hcm"

    def __init__(
        self,
        tenant: str,
        site: str,
        company_name: Optional[str] = None,
        slug: Optional[str] = None,
    ):
        self.tenant = tenant
        self.site = site
        self.company_name = company_name or tenant
        self.slug = slug or tenant.split(".")[0]

        if tenant.startswith("http://") or tenant.startswith("https://"):
            self.base_url = tenant.rstrip("/")
        elif "." in tenant:
            self.base_url = f"https://{tenant.rstrip('/')}"
        else:
            self.base_url = f"https://{tenant}.fa.oraclecloud.com"

    async def list_jobs(self) -> List[RawJob]:
        async with httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=25.0,
        ) as client:
            discovery_url = (
                f"{self.base_url}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
                f"?finder=findReqs;siteNumber={self.site},facetsList=LOCATIONS"
            )
            r = await client.get(discovery_url)
            r.raise_for_status()
            data = r.json()
            items = data.get("items", [])
            if not items:
                return []

            locations_facet = items[0].get("locationsFacet", [])
            uk_id = None
            for loc in locations_facet:
                name = (loc.get("Name") or "").strip().lower()
                if name == "united kingdom":
                    uk_id = loc.get("Id")
                    break
            if not uk_id:
                for loc in locations_facet:
                    name = (loc.get("Name") or "").strip().lower()
                    if "united kingdom" in name:
                        uk_id = loc.get("Id")
                        break

            if not uk_id:
                return []

            total_jobs = items[0].get("TotalJobsCount") or 0
            raw_jobs: List[RawJob] = []
            seen_ids = set()
            offset = 0
            limit = 200

            while offset < total_jobs:
                reqs_url = (
                    f"{self.base_url}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
                    f"?finder=findReqs;siteNumber={self.site},locationId={uk_id},"
                    f"offset={offset},limit={limit}&expand=requisitionList"
                )
                r = await client.get(reqs_url)
                r.raise_for_status()
                res_data = r.json()
                res_items = res_data.get("items", [])
                if not res_items:
                    break
                req_list = res_items[0].get("requisitionList", [])
                if not req_list:
                    break

                for req in req_list:
                    req_id = str(req.get("Id") or req.get("RequisitionId") or "")
                    if not req_id or req_id in seen_ids:
                        continue
                    seen_ids.add(req_id)

                    title = req.get("Title") or ""
                    location_raw = req.get("PrimaryLocation") or "United Kingdom"
                    apply_url = (
                        f"{self.base_url}/hcmUI/CandidateExperience/en/sites/"
                        f"{self.site}/job/{req_id}"
                    )
                    description = req.get("ShortDescriptionStr")

                    posted_date_str = req.get("PostedDate")
                    posted_at = None
                    if posted_date_str:
                        try:
                            posted_at = datetime.fromisoformat(posted_date_str).replace(
                                tzinfo=timezone.utc
                            )
                        except Exception:
                            posted_at = None

                    raw_jobs.append(
                        RawJob(
                            source_type=self.type,
                            external_id=req_id,
                            company_name=self.company_name,
                            title=title,
                            location_raw=location_raw,
                            apply_url=apply_url,
                            description=description,
                            posted_at=posted_at,
                        )
                    )

                offset += len(req_list)
                if offset >= total_jobs:
                    break

            return raw_jobs

    async def get_detail(self, job: RawJob) -> RawJob:
        return job
