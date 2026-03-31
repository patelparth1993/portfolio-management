# Portfolio Management Dashboard

A personal finance dashboard that pulls investment statements from Google Drive, parses them, and publishes a self-contained interactive HTML dashboard — automatically every month via GitHub Actions + Netlify.

## What it does

- **Ingests** PDF statements from WealthSimple and Manulife (via Google Drive or local `statements/` folder)
- **Parses** holdings, values, deposits, and dates using pdfplumber; falls back to Gemini AI for complex layouts
- **Analyzes** net worth over time, asset allocation, contribution room (TFSA/RRSP/RESP), cost basis vs. current value, and retirement projections
- **Publishes** a single self-contained `dashboard/index.html` with interactive Plotly charts

## Project structure

```
portfolio-management/
├── .github/workflows/portfolio.yml   # Monthly GH Actions pipeline
├── data/
│   ├── history.json                  # Cumulative snapshots (committed, updated by CI)
│   ├── processed/                    # Parsed JSON per statement (committed, updated by CI)
│   └── profile.yaml                  # Personal settings (DOB, contribution room, goals)
├── scripts/
│   ├── parsers/
│   │   ├── wealthsimple.py           # WealthSimple PDF parser
│   │   └── manulife.py               # Manulife PDF parser
│   ├── ingest.py                     # Orchestrates parsing + GDrive download
│   ├── analyze.py                    # Metrics, projections, contribution room
│   ├── report.py                     # Renders HTML dashboard via Jinja2 + Plotly
│   └── gdrive.py                     # Google Drive download helper
├── templates/dashboard.html.j2       # Dashboard Jinja2 template
├── dashboard/index.html              # Generated output (served by Netlify)
├── statements/                       # PDF drop folder — gitignored, never committed
├── config.yaml                       # Account types, TFSA limits, growth assumptions
├── data/profile.yaml                 # Personal profile (see below)
└── run.py                            # Entry point
```

## Running locally

```bash
# Install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Drop PDFs into statements/<institution>/<account-subdir>/
# e.g. statements/wealthsimple/HF4253745CAD_RRSP/February_2026.pdf

# Run full pipeline
python run.py

# Skip ingestion, just regenerate dashboard
python run.py --report-only
```

The dashboard opens automatically in your browser at `dashboard/index.html`.

## Automated pipeline (GitHub Actions + Netlify)

The workflow in `.github/workflows/portfolio.yml` runs on the 1st of every month (or manually via `workflow_dispatch`):

1. Checks out the repo (includes `data/processed/` and `data/history.json` from prior runs)
2. Downloads **new** PDFs from Google Drive into `statements/` (transient — not committed)
3. Parses new statements, appends to `data/processed/`
4. Runs analysis and regenerates `dashboard/index.html`
5. Commits `data/history.json`, `data/processed/`, and `dashboard/index.html` back to `master`
6. Netlify detects the commit and redeploys the dashboard

**Statements are never committed to the repo.** Only the extracted data (processed JSONs) and generated dashboard are committed.

## GitHub Secrets required

| Secret | Description |
|--------|-------------|
| `GDRIVE_SERVICE_ACCOUNT_JSON` | Full JSON content of the GCP service account key |
| `GDRIVE_FOLDER_ID` | Google Drive folder ID containing statement PDFs |
| `GEMINI_API_KEY` | Gemini API key (used as pdfplumber fallback) |

Set these under **Settings → Secrets and variables → Actions** in your GitHub repo.

> **Private repo note:** `data/profile.yaml` contains personal settings and is committed to the repo. Keep the repository **private**. If you need a public repo, move sensitive profile fields to a `PROFILE_YAML` GitHub Secret and write it to disk at the start of the workflow.

## Google Drive folder structure

The GDrive folder should mirror the local `statements/` layout:

```
GDrive folder/
├── wealthsimple/
│   ├── <ACCOUNT_NUMBER>_TFSA/
│   ├── <ACCOUNT_NUMBER>_RRSP/
│   └── <ACCOUNT_NUMBER>_TFSA_PORTFOLIO/
└── manulife/
    ├── Annual statement - Dec 2025.pdf
    └── Quarterly statement - Sep 2025.pdf
```

The subdirectory name (e.g. `HF4253745CAD_RRSP`) is used as the unique `account_id` and also tells the parser which account type to expect.

## `data/profile.yaml`

Key sections to keep up to date:

```yaml
personal:
  date_of_birth: "YYYY-MM-DD"
  province: ON

accounts:
  tfsa:
    current_room_available: 0     # Update from CRA MyAccount each January
    ytd_contributions: 0
  rrsp:
    current_room_available: 0     # From your latest NOA
    ytd_contributions: 0

cost_basis_overrides:
  manulife_RPP: 0    # Total contributions (member + employer) to date
  manulife_RRSP: 0   # Total Manulife group RRSP deposits to date
  # WealthSimple accounts are auto-calculated from statement history

retirement:
  target_retirement_age: 65
  target_monthly_income: 5000
```

## Supported institutions

| Institution | Parser | Gemini fallback |
|-------------|--------|-----------------|
| WealthSimple (self-directed + managed) | pdfplumber | Yes |
| Manulife group savings (RPP + RRSP) | pdfplumber | Yes |

To add a new institution: create `scripts/parsers/<name>.py` with `parse()` and `apply_gemini_response()` functions, then register it in `scripts/parsers/__init__.py`.

## Updating contribution room

After receiving your CRA MyAccount notice each January, update `data/profile.yaml`:
- `accounts.tfsa.current_room_available` — from CRA TFSA room calculation
- `accounts.rrsp.current_room_available` — from your latest Notice of Assessment
- `prior_year_contributions` — for the prior year chart on the dashboard
