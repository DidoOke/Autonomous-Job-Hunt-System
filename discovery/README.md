# ATS Discovery & Company Ingestion Tools

This directory contains standalone utilities for discovering and verifying career page feeds and ATS systems across target companies.

## Tools Overview

### 1. Interactive Paste-a-Link Helper (`add_company.py`)
Quickly verify and add a new company directly into `companies.yaml`.

```bash
# Interactive prompt mode
python discovery/add_company.py

# Direct URL mode
python discovery/add_company.py --url https://boards.greenhouse.io/monzo

# Direct URL with explicit company name
python discovery/add_company.py --url https://careers.bbc.co.uk/sitemap.xml --name "BBC"
```

Supported inputs:
- Direct job posting URLs (e.g. Workday, Greenhouse, Ashby, Lever, SmartRecruiters, Recruitee, Teamtailor)
- General career board URLs
- Company landing pages (inspects redirects and outbound ATS links)

### 2. Automated Batch Prober (`probe_ats.py`)
Discovers and tests public job feeds for all companies defined in `seed_companies.yaml`.

- **Stage 1 (Fast & Reliable)**: Probes public JSON endpoints for Greenhouse (US & EU), Lever, Ashby, SmartRecruiters, Workable, and Recruitee.
- **Stage 2 (Search Discovery)**: Searches for careers pages of unresolved companies and inspects links.
- **Stage 3 (Workday Verification)**: Verifies Workday endpoints through their public CXS JSON endpoints.

```bash
# Run Stage 1 slug probing
python discovery/probe_ats.py

# Run for specific companies only
python discovery/probe_ats.py --only "Monzo,Starling Bank"

# Run with web search for unresolved targets
python discovery/probe_ats.py --search
```

Outputs:
- `discovery/probe_results.json`: Detailed diagnostic telemetry for each probed company.
- `companies.yaml`: Verified watchlist with ATS slugs and tiers ready for ingestion.

