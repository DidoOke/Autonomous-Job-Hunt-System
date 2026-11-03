from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class RawJob(BaseModel):
    source_type: str
    external_id: str
    company_name: str
    title: str
    company_id: Optional[int] = None
    location_raw: Optional[str] = None
    apply_url: Optional[str] = None
    description: Optional[str] = None
    posted_at: Optional[datetime] = None
    is_test: bool = False


class Job(BaseModel):
    id: Optional[int] = None
    company_id: int
    title: str
    location_raw: Optional[str] = None
    is_uk: bool = False
    is_manchester: bool = False
    is_london: bool = False
    is_remote: bool = False
    remote_ambiguous: bool = False
    is_test: bool = False
    job_kind: Optional[str] = None
    role_family: Optional[str] = None
    description: Optional[str] = None
    apply_url: Optional[str] = None
    posted_at: Optional[datetime] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    fingerprint: str
    dedupe_key: Optional[str] = None
    duplicate_of: Optional[int] = None
    priority: int = 0
    source: Optional[str] = None
    score: int = 0
    tier: str = "drop"
    matched_rules: Optional[List[str]] = None


class JobOut(Job):
    pass
