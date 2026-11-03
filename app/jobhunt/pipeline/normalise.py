import hashlib
import re
from typing import Optional
from jobhunt.core.schemas import Job, RawJob


def get_canonical_city(location_raw: Optional[str]) -> str:
    if not location_raw:
        return ""
    loc = location_raw.lower().strip()

    # Match common UK and tech hub cities
    cities = [
        "manchester",
        "london",
        "amsterdam",
        "edinburgh",
        "leeds",
        "bristol",
        "glasgow",
        "cambridge",
        "oxford",
        "salford",
        "birmingham",
        "belfast",
        "reading",
        "newcastle",
        "sheffield",
        "liverpool",
        "nottingham",
        "remote",
    ]
    for city in cities:
        if city in loc:
            return city

    # Fallback: first component before comma, slash, or semicolon
    for sep in [",", "/", ";", "-"]:
        if sep in loc:
            token = loc.split(sep)[0].strip()
            if token:
                return token
    return loc


def compute_fingerprint(company_name: str, title: str, canonical_city: str) -> str:
    raw = (
        company_name.lower().strip()
        + title.lower().strip()
        + canonical_city.lower().strip()
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_dedupe_key(
    company_name: Optional[str],
    title: Optional[str],
    location_city: Optional[str],
    job_kind: Optional[str],
) -> str:
    # Step 1: Normalize company name
    c = (company_name or "").lower()
    for s in ["limited", "ltd", "inc", "plc", "group", "holdings", "technologies"]:
        c = re.sub(r"\b" + s + r"\b", "", c)
    company_norm = re.sub(r"[^a-z0-9]", "", c)

    # Step 3: Normalize location city
    loc = (location_city or "").lower().split(",")[0].strip()
    loc = re.sub(r"\bgreater\s+", "", loc)
    city_norm = re.sub(r"[^a-z0-9]", "", loc)

    # Step 4: Normalize job_kind
    k = (job_kind or "").lower()
    if not k:
        t_low = (title or "").lower()
        if any(w in t_low for w in ["intern", "placement", "spring", "apprentice"]):
            kind_norm = "intern"
        elif "grad" in t_low:
            kind_norm = "grad"
        else:
            kind_norm = "other"
    elif k in ["summer_internship", "placement", "spring_week", "apprenticeship", "intern", "internship"]:
        kind_norm = "intern"
    elif "grad" in k:
        kind_norm = "grad"
    else:
        kind_norm = "other"

    # Step 2: Normalize title
    t = (title or "").lower()
    t = re.sub(r"\b20\d{2}\b", "", t)
    t = re.sub(
        r"\b(summer|winter|spring|graduate|grad|intern|internship|placement|student|programme|program|scheme|year)\b",
        "",
        t,
    )
    t = re.sub(r"\b(united kingdom|uk|gb)\b", "", t)
    if company_norm:
        t = re.sub(r"\b" + company_norm + r"\b", "", t)
    if city_norm:
        t = re.sub(r"\b" + city_norm + r"\b", "", t)

    t = re.sub(r"\bengineering\b", "engineer", t)
    t = re.sub(r"\bdev\b", "developer", t)
    t = re.sub(r"\bdeveloper\b", "engineer", t)
    t = re.sub(r"\b(and|the|of|for|with|to|in|at)\b", "", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    tokens = sorted(list(set(t.split())))
    title_norm = " ".join(tokens)

    # Step 5: sha256
    raw = f"{company_norm}|{title_norm}|{city_norm}|{kind_norm}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def normalise(raw_job: RawJob, company_id: int) -> Job:
    city = get_canonical_city(raw_job.location_raw)
    fp = compute_fingerprint(raw_job.company_name, raw_job.title, city)
    return Job(
        company_id=raw_job.company_id or company_id,
        title=raw_job.title,
        location_raw=raw_job.location_raw,
        apply_url=raw_job.apply_url,
        description=raw_job.description,
        posted_at=raw_job.posted_at,
        fingerprint=fp,
    )
