"""Interactive 'Paste-a-Link' helper to verify and add unresolved companies to companies.yaml.

You can paste:
1. A direct job posting link (e.g. https://autotrader.wd3.myworkdayjobs.com/AutoTrader_Careers/job/...)
2. An ATS board link (e.g. https://boards.greenhouse.io/monzo or https://jobs.lever.co/palantir)
3. A company's general careers page URL (the tool will inspect links and redirects)

Usage:
    python discovery/add_company.py                            # Interactive prompt mode
    python discovery/add_company.py --url <URL>               # Direct URL mode
    python discovery/add_company.py --url <URL> --name "BBC"  # Direct with explicit company name
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "companies.yaml"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

UK_RE = re.compile(
    r"united kingdom|\buk\b|\bgb\b|great britain|england|scotland|wales|northern ireland|london|"
    r"manchester|salford|leeds|edinburgh|glasgow|bristol|cambridge|oxford|birmingham|belfast|cardiff|"
    r"reading|newcastle|sheffield|liverpool|nottingham|milton keynes|guildford|brighton|\bbath\b|exeter|"
    r"warrington|knutsford|cheltenham|macclesfield|crewe|wilmslow|coventry|southampton|swindon",
    re.I,
)
MANC_RE = re.compile(r"manchester|salford", re.I)
INTERN_RE = re.compile(
    r"\b(intern(ship)?s?|placement|industrial|summer (analyst|associate|engineer|programme|program)|"
    r"spring (week|insight)|insight (week|programme|program|day)|undergraduate|student|"
    r"year in industry|work experience|co-?op)\b",
    re.I,
)

PATTERNS = [
    ("workday", re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)")),
    ("greenhouse", re.compile(r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)")),
    ("lever", re.compile(r"https?://jobs\.(?:eu\.)?lever\.co/([\w-]+)")),
    ("ashby", re.compile(r"https?://jobs\.ashbyhq\.com/([\w.%-]+)")),
    ("smartrecruiters", re.compile(r"https?://(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)")),
    ("workable", re.compile(r"https?://apply\.workable\.com/([\w-]+)")),
    ("teamtailor", re.compile(r"https?://([\w-]+)\.teamtailor\.com")),
    ("recruitee", re.compile(r"https?://([\w-]+)\.recruitee\.com")),
    ("eightfold", re.compile(r"https?://([\w.-]+)\.eightfold\.ai")),
    ("icims", re.compile(r"https?://([\w-]+)\.icims\.com")),
    ("oracle", re.compile(r"https?://([\w-]+)\.fa\.[\w.-]*oraclecloud\.com")),
]


def stats(jobs: list[tuple[str, str]]) -> dict:
    return {
        "total": len(jobs),
        "uk": sum(bool(UK_RE.search(loc)) for _, loc in jobs),
        "manchester": sum(bool(MANC_RE.search(loc)) for _, loc in jobs),
        "intern": sum(bool(INTERN_RE.search(t)) for t, _ in jobs),
        "intern_uk": sum(bool(INTERN_RE.search(t) and UK_RE.search(loc)) for t, loc in jobs),
        "sample": [t for t, _ in jobs if INTERN_RE.search(t)][:3] or [t for t, _ in jobs][:3],
    }


async def verify_workday(cl: httpx.AsyncClient, tenant: str, wd: str, site: str) -> dict | None:
    cxs_url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    try:
        r = await cl.post(cxs_url, json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""})
        if r.status_code != 200:
            return None
        data = r.json()
        total = data.get("total", 0)
        postings = data.get("jobPostings", [])
        jobs = [(p.get("title", ""), p.get("locationsText", "")) for p in postings]
        
        # Also query for intern keyword
        r_intern = await cl.post(cxs_url, json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "intern"})
        intern_jobs = []
        intern_total = 0
        if r_intern.status_code == 200:
            int_data = r_intern.json()
            intern_total = int_data.get("total", 0)
            intern_jobs = [(p.get("title", ""), p.get("locationsText", "")) for p in int_data.get("jobPostings", [])]

        st = stats(jobs if not intern_jobs else (jobs + intern_jobs))
        st["total"] = total
        st["intern"] = intern_total
        return {
            "type": "workday",
            "tenant": tenant,
            "wd": wd,
            "site": site,
            "live": st,
        }
    except Exception as e:
        print(f"Workday probe error: {e}")
        return None


async def verify_generic_api(cl: httpx.AsyncClient, ats_type: str, slug: str) -> dict | None:
    try:
        if ats_type == "greenhouse":
            r = await cl.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
            if r.status_code == 200:
                d = r.json()
                jobs = [(j.get("title", ""), (j.get("location") or {}).get("name", "")) for j in d.get("jobs", [])]
                return {"type": "greenhouse", "slug": slug, "region": "us", "live": stats(jobs)}
        elif ats_type == "lever":
            r = await cl.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
            if r.status_code == 200:
                d = r.json()
                jobs = [(j.get("text", ""), (j.get("categories") or {}).get("location", "")) for j in d]
                return {"type": "lever", "slug": slug, "region": "us", "live": stats(jobs)}
        elif ats_type == "ashby":
            r = await cl.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
            if r.status_code == 200:
                d = r.json()
                jobs = [(j.get("title", ""), j.get("location", "")) for j in d.get("jobs", [])]
                return {"type": "ashby", "slug": slug, "live": stats(jobs)}
        elif ats_type == "smartrecruiters":
            r = await cl.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 50})
            if r.status_code == 200:
                d = r.json()
                jobs = [(j.get("name", ""), (j.get("location") or {}).get("city", "")) for j in d.get("content", [])]
                st = stats(jobs)
                st["total"] = d.get("totalFound", len(jobs))
                return {"type": "smartrecruiters", "slug": slug, "live": st}
        elif ats_type == "workable":
            r = await cl.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
            if r.status_code == 200:
                d = r.json()
                jobs = [(j.get("title", ""), f"{j.get('city', '')} {j.get('country', '')}") for j in d.get("jobs", [])]
                return {"type": "workable", "slug": slug, "live": stats(jobs)}
        elif ats_type == "teamtailor":
            r = await cl.get(f"https://{slug}.teamtailor.com/jobs.rss")
            if r.status_code == 200:
                titles = re.findall(r"<title>(.*?)</title>", r.text)[1:]
                jobs = [(t, "United Kingdom") for t in titles]
                return {"type": "teamtailor", "slug": slug, "feed_url": f"https://{slug}.teamtailor.com/jobs.rss", "live": stats(jobs)}
    except Exception as e:
        print(f"Generic API probe error: {e}")
    return None


async def inspect_url(cl: httpx.AsyncClient, target_url: str) -> dict | None:
    # 1. Match directly against target_url string
    for typ, pat in PATTERNS:
        m = pat.search(target_url)
        if m:
            if typ == "workday":
                return await verify_workday(cl, m.group(1), m.group(2), m.group(3))
            else:
                res = await verify_generic_api(cl, typ, m.group(1))
                if res:
                    return res
                return {"type": typ, "slug": m.group(1), "custom_url": target_url}

    # 2. Fetch page and find ATS links / redirects
    try:
        r = await cl.get(target_url, timeout=12, follow_redirects=True)
        final_url = str(r.url)
        for typ, pat in PATTERNS:
            m = pat.search(final_url)
            if m:
                if typ == "workday":
                    return await verify_workday(cl, m.group(1), m.group(2), m.group(3))
                else:
                    res = await verify_generic_api(cl, typ, m.group(1))
                    if res:
                        return res
                    return {"type": typ, "slug": m.group(1), "custom_url": final_url}

        # Search HTML body for ATS links
        for typ, pat in PATTERNS:
            m = pat.search(r.text)
            if m:
                if typ == "workday":
                    return await verify_workday(cl, m.group(1), m.group(2), m.group(3))
                else:
                    res = await verify_generic_api(cl, typ, m.group(1))
                    if res:
                        return res
                    return {"type": typ, "slug": m.group(1)}
    except Exception as e:
        print(f"Error fetching URL {target_url}: {e}")

    return None


def update_companies_yaml(company_name: str, ats_info: dict) -> bool:
    data = yaml.safe_load(COMPANIES_FILE.read_text(encoding="utf-8"))
    comps = data.get("companies", [])

    matched = None
    target_clean = re.sub(r"\W", "", company_name.lower())
    for c in comps:
        if re.sub(r"\W", "", c["name"].lower()) == target_clean:
            matched = c
            break

    if not matched:
        # Check substring match
        for c in comps:
            c_clean = re.sub(r"\W", "", c["name"].lower())
            if target_clean in c_clean or c_clean in target_clean:
                matched = c
                break

    if matched:
        print(f"Updating existing record: {matched['name']} (Tier {matched.get('tier')})")
        matched["confidence"] = "high"
        matched["ats"] = {k: v for k, v in ats_info.items() if k != "live"}
        if "live" in ats_info:
            matched["live"] = ats_info["live"]
    else:
        print(f"Creating new company record: {company_name}")
        new_entry = {
            "name": company_name,
            "tier": "A",
            "hubs": ["uk"],
            "sector": "tech",
            "confidence": "high",
            "ats": {k: v for k, v in ats_info.items() if k != "live"},
        }
        if "live" in ats_info:
            new_entry["live"] = ats_info["live"]
        comps.append(new_entry)

    COMPANIES_FILE.write_text(yaml.safe_dump({"companies": comps}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"Successfully saved to {COMPANIES_FILE}")
    return True


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="Job listing URL or careers page URL")
    parser.add_argument("--name", help="Company name (optional, will prompt or infer)")
    args = parser.parse_args()

    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": UA}, follow_redirects=True) as cl:
        if args.url:
            url = args.url.strip()
            print(f"Inspecting: {url}")
            res = await inspect_url(cl, url)
            if not res:
                print("Could not automatically detect ATS feed from URL.")
                sys.exit(1)
            print("\nFound ATS Feed:")
            print(yaml.safe_dump(res, sort_keys=False))

            name = args.name
            if not name:
                name = input("Enter company name to save against: ").strip()
            if name:
                update_companies_yaml(name, res)
            return

        # Interactive Mode
        print("=== Interactive Paste-a-Link Helper ===")
        print("Paste any job posting or careers page URL (or 'q' to quit).\n")
        while True:
            url = input("Paste URL: ").strip()
            if not url or url.lower() in ("q", "quit", "exit"):
                break

            print("Probing ATS endpoint...")
            res = await inspect_url(cl, url)
            if not res:
                print("Could not detect known ATS from that URL. Try pasting the direct link when browsing a job.")
                continue

            print("\nSuccessfully Resolved ATS Configuration:")
            print(yaml.safe_dump(res, sort_keys=False))

            name = input("Company name to attach to (e.g. Barclays, BBC, BAE Systems): ").strip()
            if name:
                update_companies_yaml(name, res)
            print("-" * 50)


if __name__ == "__main__":
    asyncio.run(main())
