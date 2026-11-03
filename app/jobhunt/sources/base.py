from typing import Protocol
from jobhunt.core.schemas import RawJob


class Source(Protocol):
    type: str  # "greenhouse", "workday", "sitemap", ...

    async def list_jobs(self) -> list[RawJob]:  # full current listing (or new items for feeds)
        ...

    async def get_detail(self, job: RawJob) -> RawJob:  # fill description if the list lacks it
        ...
