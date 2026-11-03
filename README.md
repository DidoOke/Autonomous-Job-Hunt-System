<div align="center">
  <img src="assets/logo.svg" alt="Autonomous Job Hunt System Logo" width="100" height="100" />
  <h1>Autonomous Job Hunt System</h1>
  <p>Automated job intelligence, ATS feed aggregation, cross-source deduplication, and real-time notification engine for UK software engineering, AI/ML, and data roles.</p>
</div>

---

## Overview

Job Hunt monitors career boards across **260+ top companies** (direct ATS APIs, corporate sitemaps, and aggregator safety nets). It normalizes listings, filters ancient postings, computes multi-stage relevance scores, deduplicates identical openings across sources, and dispatches high-signal opportunities directly to Telegram.

### Core Capabilities
- **12+ Production Source Adapters**: Ingests directly from employer ATS platforms, corporate XML sitemaps, and aggregator feeds.
- **Per-Source Interval Scheduler**: Eliminates monolithic loops. Each source runs on its own independent timer (Priority 1: 15m, Priority 2: 60m, Priority 3: 180m).
- **Zero-Spam Baselining**: Discards old postings (>14 days) at ingestion. Initial polls run silently to avoid alert floods. A curated welcome batch (top 1-3 listings) fires within ~2 minutes of startup.
- **Cross-Source Deduplication**: SHA-256 fingerprinting and semantic title normalization link aggregator postings back to the canonical employer feed.
- **Built-in CV Generator & ATS Verification**: Typst-powered single-page resume builder with automated ATS reverse text-extraction validation.
- **Automated ATS Discovery Suite**: Interactive "paste-a-link" helper and automated probers to discover and verify career endpoints.

---

## Supported Job Sources

The system uses dedicated adapters designed for specific ATS APIs and web data structures:

### Direct Employer ATS Platforms
- **Greenhouse (`greenhouse`)**: Ingests public job listings and descriptions directly from the Greenhouse REST API (`/v1/boards/{slug}/jobs?content=true`).
- **Lever (`lever`)**: Fetches structured postings from Lever's JSON endpoint (`/v0/postings/{slug}?mode=json`).
- **Ashby (`ashby`)**: Queries Ashby's public board endpoints with detailed job metadata extraction.
- **SmartRecruiters (`smartrecruiters`)**: Connects to the SmartRecruiters Postings API with pagination and full description retrieval.
- **Workday (`workday`)**: Interacts directly with Workday's client candidate experience endpoints (`myworkdayjobs.com`) parsing JSON data.
- **Oracle HCM Cloud (`oracle_hcm`)**: Queries Oracle Recruiting CE REST APIs (`recruitingCEJobRequisitions`), leveraging location facet IDs for accurate UK-targeted filtering.
- **Teamtailor (`teamtailor`)**: Ingests and parses company XML / RSS job feeds.
- **Recruitee (`recruitee`)**: Interacts with the Recruitee public JSON job API.
- **Amazon Jobs (`amazon`)**: Queries Amazon's careers search API for corporate software roles.

### Corporate Sitemaps & Structured Data
- **Sitemap JSON-LD & HTML5 Microdata (`sitemap_jsonld`)**: Crawls company XML sitemaps (`sitemap.xml`) to discover career pages, then extracts structured metadata using high-performance Lexbor parsing for Schema.org `JobPosting` JSON-LD blocks and HTML5 Microdata fallbacks (used for BBC, BAE Systems, Unilever, etc.).

### Aggregator & Safety Net Feeds
- **Simplify GitHub (`simplify_github`)**: Parses curated Summer 2027 internship markdown tables from community repositories, attributing listings to real employer companies.
- **JobSpy (`jobspy`)**: Live multi-board scraper targeting LinkedIn and Indeed for newly posted UK tech internship listings.

### Headless Browser (Experimental)
- **Playwright (`playwright`)**: Headless browser automation for single-page applications (Microsoft, Goldman Sachs, NatWest). *Note: Currently marked broken and deferred to a future phase due to un-filtered location data.*

---

## Job Targeting & Customizing the Pipeline

### How Ingestion & Targeting Work
1. **Broad Sourcing**: The adapters fetch broad candidate listings directly from employer career boards (e.g., all engineering vacancies or all open roles).
2. **Two-Stage Filtering Pipeline**:
   - **Ingestion Filter**: Postings with `posted_at > 14 days old` are skipped immediately to maintain a lean database and ensure rapid polling.
   - **Classification & Scoring**: Raw listings pass through normalization, classification (`classify.py`), and multi-gate scoring (`score.py`).
3. **Current Default Configuration (UK Tech Internships)**:
   - **Gate 1 (Role Kind)**: Requires internship or student placement indicators (`intern`, `placement`, `summer analyst`, `industrial placement`, `spring week`, etc.).
   - **Gate 2 (Tech Relevance)**: Requires strong software, AI, or data signals in title, department, or description (e.g. `software engineer`, `machine learning`, `data scientist`, `backend`, `algorithms`).
   - **Location Checks**: Scores UK, Manchester, and London roles with positive weighting; non-UK locations are dropped.
   - **Negative Penalties**: Strictly penalizes non-tech roles, IT helpdesk/support, data entry, and senior/executive positions.

### Customizing for Other Use Cases
The system can be adapted for any career level, industry, or geography:

- **Targeting Graduate Roles or Full-Time Roles**:
  In `app/jobhunt/pipeline/score.py`, adjust **Gate 1** (`eval_gate1_internship`) to check for `graduate`, `associate`, or `entry level` keywords, or disable Gate 1 to track all full-time positions.
- **Targeting Other Role Families (e.g. Product Management, Cyber Security)**:
  Update the keyword maps in `app/jobhunt/pipeline/classify.py` (`ROLE_FAMILY_PATTERNS`) and Gate 2 in `score.py` to match your targeted titles and skills.
- **Targeting Other Geographies (e.g. US, Remote Worldwide)**:
  In `app/jobhunt/pipeline/normalise.py` and `app/jobhunt/pipeline/score.py`, update `get_canonical_city` and `evaluate` to award bonuses for your target countries/regions instead of UK-specific checks.

---

## Scheduler Architecture & Polling Frequencies

To prevent sequential bottlenecks where large enterprise feeds (such as Workday or Oracle) block fast boards, Job Hunt uses an independent **per-source interval scheduling model** managed by APScheduler:

| Priority | Cadence | First Run After Startup | Source Types Included | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **Priority 1 (P1)** | Every **15 minutes** | **+5 seconds** (Immediate) | 23 sources: High-signal Tier A tech boards (Monzo, Stripe, Palantir, Databricks, Jane Street, etc.), sitemaps (BBC, BAE Systems), and safety nets (Simplify, JobSpy). | Immediate baseline and rapid discovery of competitive roles. |
| **Priority 2 (P2)** | Every **60 minutes** | **+60 minutes** | 70 sources: Tier B companies, heavy enterprise ATS platforms (Workday, Oracle HCM). | Thorough periodic polling of slower, higher-volume enterprise boards. |
| **Priority 3 (P3)** | Every **180 minutes** | **+180 minutes** | 21 sources: Bulk, Tier C companies, and experimental feeds. | Low-overhead long-tail coverage. |

### Scheduler Execution Timeline

```
Timeline:
T = 0s         T = 5s                     T = ~120s               T = 15m            T = 60m            T = 180m
┌──────────────┬──────────────────────────┬───────────────────────┬──────────────────┬──────────────────┬──────────────────┐
│ Container Up │ P1 First Run (23 boards) │ Welcome Batch Sent    │ P1 Second Poll   │ P2 First Run     │ P3 First Run     │
│ - Migrations │ - Fast parallel polling  │ - Top 1-3 fresh jobs  │ - Notifies on    │ (70 boards)      │ (21 boards)      │
│ - Seed DB    │ - Silent baseline pass   │ - Dispatched to chat  │   new jobs only  │ - Silent baseline│ - Silent baseline│
│ - Schedulers │ - Zero alert spam        │ - Checker auto-stops  │ - Recurs q15m    │ - Recurs q60m    │ - Recurs q180m   │
└──────────────┴──────────────────────────┴───────────────────────┴──────────────────┴──────────────────┴──────────────────┘
```

### Baselining & Alert Rules
- **Silent Baseline**: When any source runs for the first time (`last_ok IS NULL`), all existing postings are ingested with `inserted_cycle_id='baseline'`, and alert notifications are suppressed.
- **Welcome Batch**: A lightweight checker runs every 30 seconds after startup. The moment all 23 Priority 1 sources complete their initial pass (~2 minutes), it queries for the top 1-3 fresh, high-scoring openings and dispatches them as an initial welcome batch.
- **Live Notifications**: After a source's first baseline is established, subsequent scheduled runs detect genuine new postings and deliver formatted Markdown alerts to Telegram.

---

## Architecture & Technology Stack

| Component | Technology | Purpose |
| :--- | :--- | :--- |
| **Backend & CLI** | Python 3.12, Typer, Rich | Core pipeline execution, CLI commands, and visual telemetry |
| **Scheduler** | APScheduler (AsyncIO) | Per-source non-blocking interval execution |
| **Database & ORM** | PostgreSQL 16, SQLAlchemy 2 (Async), asyncpg | Relational persistence, sightings ledger, and deduplication indexes |
| **Migrations** | Alembic | Version-controlled schema migrations applied automatically on startup |
| **API Server** | FastAPI, Uvicorn | Lightweight REST API for external integrations and metrics |
| **Parsing & Scraping** | selectolax, httpx, python-jobspy | High-performance HTML parsing, JSON extraction, and aggregator scraping |
| **CV Generation** | Typst, PyPDF, PyYAML | Deterministic ATS-compatible resume compilation |
| **Containerization** | Docker, Docker Compose | Multi-container deployment with database health checking |

---

## Planned Features & Roadmap

The following enhancements are planned for upcoming releases:

1. **Replace custom `jobspy_src.py` with `python-jobspy` library**: Standardize aggregator scraping on the upstream library.
2. **MCP / Coding Agent Integration**: Enable autonomous agents to interact with the database and auto-fill applications via Playwright.
3. **IMAP (Gmail) Parsing**: Ingest application confirmations, interview invitations, and status updates directly from email.
4. **CV Builder / Tailoring Engine**: Dynamically reorder skills, coursework, and bullet variants based on job description keyword density.
5. **Cover Letter Generator**: Produce tailored cover letters matching target job descriptions and company values.
6. **Playwright Fixes**: Resolve location filtering and empty-description scoring for deferred corporate boards.
7. **DASHBOARD**: Web-based graphical dashboard for job tracking, application stages, and source health metrics.
8. **Database Cleanup**: Automated retention policies to archive or delete expired and closed job listings.
9. **Backlog Notification Command (`jobhunt notify-backlog`)**: Command to safely review and dispatch alerts for silent baseline jobs matching user criteria.

---

## Quickstart Guide

### 1. Prerequisites
- [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/) installed on your machine.

### 2. Configure Environment
```bash
# Clone the repository
git clone https://github.com/DidoOke/Autonomous-Job-Hunt-System.git
cd Autonomous-Job-Hunt-System

# Create environment file from template
cp .env.example .env
```

Edit `.env` to configure optional Telegram alert credentials:
```ini
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/jobhunt
ALEMBIC_DATABASE_URL=postgresql://postgres:postgres@db:5432/jobhunt
LOG_LEVEL=INFO

# Optional: Add credentials from @BotFather and @userinfobot
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_CHAT_ID=987654321
NOTIFY_ENABLED=true
```

### 3. Start Services
```bash
docker compose up -d
```
Docker Compose builds the images, waits for PostgreSQL to become healthy, runs Alembic migrations, seeds companies and sources, and launches background schedulers.

### 4. Check Status
```bash
docker compose exec worker jobhunt status
```

Example status table:
```text
                         JOB HUNT STATUS                         
┌────────────────────────┬──────────────────────────────────────┐
│ Companies              │ 262                                  │
│ Sources active         │ 114                                  │
│   P1                   │ 23/23 baselined                      │
│   P2                   │ 0/70 baselined                       │
│   P3                   │ 0/21 baselined                       │
│ Sources broken         │ 3                                    │
├────────────────────────┼──────────────────────────────────────┤
│ Total jobs             │ 935                                  │
│ High-tier canonical    │ 98                                   │
│ Matching notify filter │ 66                                   │
├────────────────────────┼──────────────────────────────────────┤
│ Notifications:         │                                      │
│   Total                │ 3 (welcome batch)                    │
│   Last 24h             │ 3                                    │
├────────────────────────┼──────────────────────────────────────┤
│ Cycle info:            │                                      │
│   Current cycle        │ per-source (P1:15m, P2:60m, P3:180m) │
│   Cycle started        │ 2 min ago                            │
│   Last cycle duration  │ 1m 51s                               │
│   Next cycle           │ in 13 min                            │
├────────────────────────┼──────────────────────────────────────┤
│ Fresh DB               │ No                                   │
│ Baseline in progress   │ No                                   │
└────────────────────────┴──────────────────────────────────────┘
```

---

## CLI Reference

Run commands inside the worker container using `docker compose exec worker jobhunt <command>`:

| Command | Description | Example |
| :--- | :--- | :--- |
| `status` | Display real-time health, priority breakdown, and job counts. | `jobhunt status` or `jobhunt status --watch` |
| `top` | List top-ranked opportunities sorted by relevance score. | `jobhunt top --limit 20` |
| `notify` | Inspect matching candidates or dispatch manual Telegram alerts. | `jobhunt notify --dry-run` or `jobhunt notify --send` |
| `notify-test` | Send a test ping to the configured Telegram chat. | `jobhunt notify-test` |
| `run-source` | Manually poll an individual board adapter for debugging. | `jobhunt run-source greenhouse:monzo --dry-run` |
| `rescore` | Recalculate scoring rules across all jobs in the database. | `jobhunt rescore` |
| `coverage` | View coverage status and last successful poll across companies. | `jobhunt coverage` |

---

## Project Structure

```text
Autonomous-Job-Hunt-System/
├── assets/
│   └── logo.svg                # System vector logo
├── app/
│   ├── alembic/                # Database migrations (0001 to 0007)
│   ├── jobhunt/
│   │   ├── api/                # FastAPI web server
│   │   ├── core/               # Database connection, models, schemas, config
│   │   ├── notify/             # Telegram MarkdownV2 alerting
│   │   ├── pipeline/           # Classify, Dedupe, Normalise, Score modules
│   │   ├── sources/            # 12+ Source adapters (Greenhouse, Workday, etc.)
│   │   ├── bootstrap.py        # Database seeder and priority management
│   │   ├── cli.py              # Rich CLI commands
│   │   └── scheduler.py        # Per-source APScheduler engine
│   ├── Dockerfile              # Production Python 3.12 container
│   └── pyproject.toml          # App package configuration & dependencies
│
├── cv/                         # ATS-optimized CV Builder
│   ├── base.yaml               # Template YAML for resume details
│   ├── build.py                # Typst compiler & ATS validation script
│   ├── template.typ            # Typst document styling template
│   ├── fonts/                  # Deterministic Lato typography fonts
│   └── README.md               # CV builder documentation
│
├── discovery/                  # ATS Discovery & Ingestion Suite
│   ├── add_company.py          # Interactive "paste-a-link" helper
│   ├── probe_ats.py            # Automated multi-stage ATS prober
│   ├── seed_companies.yaml     # Seed list of candidate companies
│   └── README.md               # Discovery documentation
│
├── companies.yaml              # Master watchlist (260+ companies & ATS configurations)
├── docker-compose.yml          # Container orchestration (db, worker, api)
├── .env.example                # Environment configuration template
├── .gitignore                  # Git ignore rules
└── README.md                   # This documentation
```

---

## License

This project is licensed under the MIT License.
