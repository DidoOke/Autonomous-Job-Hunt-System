"""Find and verify the ATS job feed for every company in seed_companies.yaml.

Stage 1  Probe the public JSON APIs with slug guesses:
         Greenhouse (US+EU), Lever (US+EU), Ashby, SmartRecruiters, Workable, Recruitee.
Stage 2  For anything unresolved, run a web search (DuckDuckGo HTML) for the company's
         careers page and detect the ATS from result URLs. This catches Workday plus
         "custom" systems (SuccessFactors, Oracle, Eightfold, iCIMS, Avature, ...).
Stage 3  Verify Workday hits via their public CXS endpoint.

Every hit records live stats (total jobs, UK jobs, intern-like jobs), so a wrong
slug (e.g. "sky" belonging to a different company) stands out.

Usage:
    python discovery/probe_ats.py                 # stage 1 only (fast, reliable)
    python discovery/probe_ats.py --search        # + best-effort web search (often rate-limited)
    python discovery/probe_ats.py --only "Monzo,Auto Trader"
Outputs:
    discovery/probe_results.json   full evidence per company
    companies.yaml                 resolved watchlist used by the pipeline
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "discovery" / "seed_companies.yaml"
RESULTS = ROOT / "discovery" / "probe_results.json"
SEARCH_CACHE = ROOT / "discovery" / "cache" / "search.json"
OUT = ROOT / "companies.yaml"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

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

STOP = {"uk", "group", "plc", "ltd", "limited", "the", "inc", "co", "company"}


# --------------------------------------------------------------------------- helpers
def slug_candidates(c: dict) -> list[str]:
    base = re.sub(r"\(.*?\)", "", c["name"]).lower().replace("&", "and").replace("+", "and")
    words = re.findall(r"[a-z0-9]+", base)
    core = [w for w in words if w not in STOP] or words
    cands = list(c.get("slugs", [])) + [
        "".join(words), "-".join(words), "".join(core), "-".join(core),
    ]
    seen, out = set(), []
    for s in cands:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out[:6]


def stats(jobs: list[tuple[str, str]]) -> dict:
    """jobs = [(title, location_text)]"""
    return {
        "total": len(jobs),
        "uk": sum(bool(UK_RE.search(loc)) for _, loc in jobs),
        "manchester": sum(bool(MANC_RE.search(loc)) for _, loc in jobs),
        "intern": sum(bool(INTERN_RE.search(t)) for t, _ in jobs),
        "intern_uk": sum(bool(INTERN_RE.search(t) and UK_RE.search(loc)) for t, loc in jobs),
        "sample": [t for t, _ in jobs if INTERN_RE.search(t)][:3] or [t for t, _ in jobs][:3],
    }


async def get_json(client: httpx.AsyncClient, url: str, **kw):
    try:
        r = await client.get(url, **kw)
        if r.status_code == 200:
            return r.json()
    except (httpx.HTTPError, ValueError):
        pass
    return None


# --------------------------------------------------------------------------- stage 1 probes
async def p_greenhouse(cl, s):
    for host in ("boards-api.greenhouse.io", "boards-api.eu.greenhouse.io"):
        d = await get_json(cl, f"https://{host}/v1/boards/{s}/jobs")
        if d and "jobs" in d:
            meta = await get_json(cl, f"https://{host}/v1/boards/{s}") or {}
            jobs = [(j.get("title", ""), (j.get("location") or {}).get("name", "")) for j in d["jobs"]]
            return {"type": "greenhouse", "slug": s, "region": "eu" if ".eu." in host else "us",
                    "board_name": meta.get("name"), **stats(jobs)}


async def p_lever(cl, s):
    for host in ("api.lever.co", "api.eu.lever.co"):
        d = await get_json(cl, f"https://{host}/v0/postings/{s}", params={"mode": "json"})
        if isinstance(d, list):
            jobs = []
            for j in d:
                cat = j.get("categories") or {}
                locs = " / ".join([cat.get("location") or ""] + (cat.get("allLocations") or []))
                jobs.append((j.get("text", ""), f"{locs} {j.get('country') or ''}"))
            return {"type": "lever", "slug": s, "region": "eu" if ".eu." in host else "us", **stats(jobs)}


async def p_ashby(cl, s):
    d = await get_json(cl, f"https://api.ashbyhq.com/posting-api/job-board/{s}")
    if d and "jobs" in d:
        jobs = []
        for j in d["jobs"]:
            locs = [j.get("location") or ""] + [x.get("location", "") for x in j.get("secondaryLocations") or []]
            country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or ""
            jobs.append((j.get("title", ""), " / ".join(locs) + " " + country))
        return {"type": "ashby", "slug": s, **stats(jobs)}


async def p_smartrecruiters(cl, s):
    d = await get_json(cl, f"https://api.smartrecruiters.com/v1/companies/{s}/postings", params={"limit": 100})
    if d and d.get("totalFound", 0) > 0:
        jobs = []
        for j in d.get("content", []):
            loc = j.get("location") or {}
            jobs.append((j.get("name", ""), f"{loc.get('city', '')} {loc.get('country', '')} "
                                              f"{'United Kingdom' if loc.get('country') == 'gb' else ''}"))
        name = (d["content"][0].get("company") or {}).get("name") if d.get("content") else None
        st = stats(jobs)
        st["total"] = d["totalFound"]  # first page only for the other counts
        return {"type": "smartrecruiters", "slug": s, "board_name": name, **st}


async def p_workable(cl, s):
    d = await get_json(cl, f"https://apply.workable.com/api/v1/widget/accounts/{s}")
    if d and "jobs" in d:
        jobs = [(j.get("title", ""), f"{j.get('city', '')} {j.get('country', '')}") for j in d["jobs"]]
        return {"type": "workable", "slug": s, "board_name": d.get("name"), **stats(jobs)}


async def p_recruitee(cl, s):
    d = await get_json(cl, f"https://{s}.recruitee.com/api/offers/")
    if d and "offers" in d:
        jobs = [(j.get("title", ""), f"{j.get('location', '')} {j.get('country', '')}") for j in d["offers"]]
        return {"type": "recruitee", "slug": s, **stats(jobs)}


PROBES = [p_greenhouse, p_lever, p_ashby, p_smartrecruiters, p_workable, p_recruitee]


async def stage1(cl, sem, c) -> list[dict]:
    async def run(p, s):
        async with sem:
            return await p(cl, s)
    tasks = [run(p, s) for s in slug_candidates(c) for p in PROBES]
    return [h for h in await asyncio.gather(*tasks) if h]


# --------------------------------------------------------------------------- stage 2: search
ATS_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)")),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)")),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)")),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)")),
    ("recruitee", re.compile(r"([\w-]+)\.recruitee\.com")),
    ("workday", re.compile(r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)")),
    ("eightfold", re.compile(r"([\w-]+)\.eightfold\.ai")),
    ("oracle", re.compile(r"([\w-]+)\.fa\.[\w.-]*oraclecloud\.com")),
    ("successfactors", re.compile(r"(successfactors\.(?:com|eu)|jobs\.sap\.com|career\d*\.sapsf)")),
    ("icims", re.compile(r"([\w-]+)\.icims\.com")),
    ("avature", re.compile(r"([\w-]+)\.avature\.net")),
    ("taleo", re.compile(r"([\w-]+)\.taleo\.net")),
    ("jobvite", re.compile(r"jobs\.jobvite\.com/([\w-]+)")),
    ("teamtailor", re.compile(r"([\w-]+)\.teamtailor\.com")),
    ("pinpoint", re.compile(r"([\w-]+)\.pinpointhq\.com")),
    ("personio", re.compile(r"([\w-]+)\.jobs\.personio\.(?:de|com)")),
]


async def ddg(cl: httpx.AsyncClient, q: str) -> list[str] | None:
    try:
        r = await cl.post("https://html.duckduckgo.com/html/", data={"q": q},
                          headers={"User-Agent": UA, "Referer": "https://html.duckduckgo.com/"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or "anomaly" in r.text[:5000].lower():
        return None
    return [unquote(u) for u in re.findall(r"uddg=([^&\"']+)", r.text)]


def detect(urls: list[str]) -> list[dict]:
    found, seen = [], set()
    for u in urls:
        for typ, pat in ATS_PATTERNS:
            m = pat.search(u)
            if m:
                key = (typ,) + m.groups()
                if key in seen:
                    continue
                seen.add(key)
                hit = {"type": typ, "url": u}
                if typ == "workday":
                    hit.update(tenant=m.group(1), wd=m.group(2), site=m.group(3))
                else:
                    hit["slug"] = m.group(1)
                found.append(hit)
    return found


async def verify_workday(cl, h) -> dict | None:
    url = f"https://{h['tenant']}.{h['wd']}.myworkdayjobs.com/wday/cxs/{h['tenant']}/{h['site']}/jobs"
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    try:
        r = await cl.post(url, json=body)
        if r.status_code != 200:
            return None
        total = r.json().get("total", 0)
        r2 = await cl.post(url, json={**body, "searchText": "intern"})
        posts = r2.json().get("jobPostings", []) if r2.status_code == 200 else []
        jobs = [(p.get("title", ""), p.get("locationsText", "")) for p in posts]
        st = stats(jobs)
        return {**h, "total": total, "intern": r2.json().get("total", 0) if r2.status_code == 200 else 0,
                "intern_uk": st["intern_uk"], "sample": st["sample"], "verified": True}
    except (httpx.HTTPError, ValueError):
        return None


# --------------------------------------------------------------------------- choose best
def score(h: dict) -> tuple:
    return (h.get("intern_uk", 0), h.get("uk", 0), h.get("total", 0))


def choose(c: dict, hits: list[dict]) -> tuple[dict | None, str]:
    rejected = set(c.get("reject", []))
    hits = [h for h in hits if f"{h['type']}:{h.get('slug') or h.get('tenant')}" not in rejected]
    api = [h for h in hits if h["type"] in {"greenhouse", "lever", "ashby", "smartrecruiters",
                                            "workable", "recruitee"} and "total" in h]
    wd = [h for h in hits if h["type"] == "workday" and h.get("verified")]
    pool = [h for h in api + wd if h.get("total", 0) > 0]
    if pool:
        best = max(pool, key=score)
        name_ok = bool(best.get("board_name")) and re.sub(r"\W", "", c["name"].lower())[:5] in \
            re.sub(r"\W", "", (best.get("board_name") or "").lower())
        uk_ok = best.get("uk", 0) > 0 or best.get("intern_uk", 0) > 0
        conf = "high" if (uk_ok or (name_ok and best.get("total", 0) >= 10)) else "check"
        return best, conf
    custom = [h for h in hits if h["type"] not in {"greenhouse", "lever", "ashby", "smartrecruiters",
                                                   "workable", "recruitee", "workday"}]
    if custom:
        return custom[0], "custom"
    return None, "unresolved"


# --------------------------------------------------------------------------- main
async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", action="store_true", help="best-effort DuckDuckGo stage (often blocked)")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    seed = yaml.safe_load(SEED.read_text(encoding="utf-8"))["companies"]
    if args.only:
        wanted = {n.strip().lower() for n in args.only.split(",")}
        seed = [c for c in seed if c["name"].lower() in wanted]

    cache = json.loads(SEARCH_CACHE.read_text()) if SEARCH_CACHE.exists() else {}
    SEARCH_CACHE.parent.mkdir(parents=True, exist_ok=True)

    limits = httpx.Limits(max_connections=24)
    async with httpx.AsyncClient(timeout=15, limits=limits, headers={"User-Agent": UA},
                                 follow_redirects=True) as cl:
        sem = asyncio.Semaphore(24)

        print(f"Stage 1: probing {len(seed)} companies on public ATS APIs...")
        s1 = await asyncio.gather(*(stage1(cl, sem, c) for c in seed))
        results = {c["name"]: {"seed": c, "hits": h} for c, h in zip(seed, s1)}

        if args.search:
            todo = [c for c in seed if not any(h.get("total", 0) > 0 for h in results[c["name"]]["hits"])]
            print(f"Stage 2: searching for {len(todo)} unresolved companies (rate-limited)...")
            for i, c in enumerate(todo, 1):
                q = f"{c['name']} careers jobs"
                if q not in cache:
                    urls = await ddg(cl, q)
                    if urls is None:
                        print("  search blocked/failed, backing off 30s")
                        await asyncio.sleep(30)
                        urls = await ddg(cl, q)
                    cache[q] = urls or []
                    SEARCH_CACHE.write_text(json.dumps(cache, indent=1))
                    await asyncio.sleep(2.5)
                found = detect(cache[q])
                # verify search-found API slugs and Workday tenants
                for h in found:
                    if h["type"] == "workday":
                        v = await verify_workday(cl, h)
                        results[c["name"]]["hits"].append(v or h)
                    else:
                        probe = {p.__name__[2:]: p for p in PROBES}.get(h["type"])
                        v = await probe(cl, h["slug"]) if probe else None
                        results[c["name"]]["hits"].append(v or h)
                results[c["name"]]["search_urls"] = cache[q][:8]
                if i % 10 == 0:
                    print(f"  {i}/{len(todo)}")

    # ---------------------------------------------------------------- write outputs
    RESULTS.write_text(json.dumps(results, indent=1), encoding="utf-8")

    out, counts = [], {}
    for name, r in results.items():
        c = r["seed"]
        best, conf = choose(c, r["hits"])
        counts[conf] = counts.get(conf, 0) + 1
        entry = {k: c[k] for k in ("name", "tier", "hubs", "sector") if k in c}
        if c.get("clearance"):
            entry["clearance"] = True
        entry["confidence"] = conf
        if best:
            ats = {k: best[k] for k in ("type", "slug", "region", "tenant", "wd", "site") if k in best}
            if conf == "custom":
                ats["url"] = best.get("url")
            entry["ats"] = ats
            if "total" in best:
                entry["live"] = {k: best.get(k) for k in ("total", "uk", "manchester", "intern", "intern_uk")
                                 if best.get(k) is not None}
        else:
            entry["ats"] = None
            if r.get("search_urls"):
                entry["careers_hint"] = r["search_urls"][0]
        out.append(entry)

    order = {"high": 0, "check": 1, "custom": 2, "unresolved": 3}
    out.sort(key=lambda e: (order[e["confidence"]], e["tier"], e["name"]))
    header = ("# AUTO-GENERATED by discovery/probe_ats.py. Review 'check' entries by hand.\n"
              "# confidence: high = verified feed with UK jobs or matching name | check = feed works but\n"
              "# verify it's the right company | custom = in-house/enterprise ATS (needs custom adapter)\n"
              "# | unresolved = not found yet\n")
    OUT.write_text(header + yaml.safe_dump({"companies": out}, sort_keys=False, allow_unicode=True,
                                           width=120), encoding="utf-8")

    print("\nSummary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda kv: order[kv[0]])))
    print(f"Wrote {OUT.relative_to(ROOT)} and {RESULTS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
