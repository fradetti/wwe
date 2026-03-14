# WWE Ticket Monitor

Automated price tracker for WWE events in Italy. Monitors **Ticketmaster.it** and **StubHub** for ticket availability and pricing, with a live dashboard published on GitHub Pages.

## Features

- **Ticketmaster scraping** — Discovery API + Playwright stealth browser to extract package names, prices, and availability
- **StubHub scraping** — Monitors resale listings for WWE events in Italian cities
- **Price history** — Tracks price changes over time with up to 1000 data points per event
- **Price alerts** — Notifications when tickets drop below €400
- **Live dashboard** — Dark/light theme, per-event price charts, source filtering (Ticketmaster / StubHub)
- **Fully automated** — GitHub Actions checks every 6h; Docker container checks every 15 min with auto-commit

## Live Dashboard

Hosted on GitHub Pages:
**https://fradetti.github.io/wwe/**

## Quick Start

### Local

```bash
pip install -r scripts/requirements.txt
playwright install chromium

# Run Ticketmaster check (API + scraping)
export TICKETMASTER_API_KEY=your_key
python scripts/check_tickets.py

# Run StubHub check
python scripts/check_stubhub.py

# API-only mode (no browser needed)
python scripts/check_tickets_api.py
```

### Docker

```bash
# Create a .env file
cat > docker/.env <<EOF
TICKETMASTER_API_KEY=your_key
GITHUB_PAT=your_github_pat
GIT_REPO=fradetti/wwe
CHECK_INTERVAL=900
EOF

docker-compose -f docker/docker-compose.yml up --build
```

The container runs in a loop: pull → scrape Ticketmaster → scrape StubHub → commit & push → sleep.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TICKETMASTER_API_KEY` | Yes | Ticketmaster Discovery API key |
| `GITHUB_PAT` | Docker only | GitHub PAT for pushing status updates |
| `GIT_REPO` | No | Repository path (default: `fradetti/wwe`) |
| `CHECK_INTERVAL` | No | Seconds between checks (default: `900`) |

## How It Works

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
│ Ticketmaster │     │   check_tickets  │     │ status.json  │
│ Discovery API│────▶│   .py (Playwright│────▶│              │
│              │     │   + API)         │     │              │
└──────────────┘     └──────────────────┘     └──────┬───────┘
                                                     │
┌──────────────┐     ┌──────────────────┐     ┌──────▼───────┐
│   StubHub    │     │  check_stubhub   │     │  index.html  │
│   .com       │────▶│  .py (Playwright)│────▶│  (dashboard) │
└──────────────┘     └──────────────────┘     └──────────────┘
                                                     │
                                              GitHub Pages ──▶ 🌐
```

1. **Discovery** — Queries Ticketmaster API for WWE events in Italy (May 30 – Jun 8, 2026)
2. **Scraping** — Playwright visits each event page to extract detailed package pricing
3. **Storage** — Results saved to `data/status.json` and `data/stubhub.json`
4. **Publishing** — GitHub Actions auto-commits changes and deploys the dashboard

## CI/CD

- **`check.yml`** — Scheduled every 6 hours (API-only, no browser). Also triggerable manually.
- **`pages.yml`** — Deploys `index.html` + data files to GitHub Pages on every push.

## Project Structure

```
scripts/
  check_tickets.py       # Full Ticketmaster scraper (API + Playwright)
  check_tickets_api.py   # Lightweight API-only fallback
  check_stubhub.py       # StubHub scraper
  entrypoint.sh          # Docker loop script
  requirements.txt       # Python dependencies
data/
  status.json            # Ticketmaster event data + price history
  stubhub.json           # StubHub event data + price history
docker/
  Dockerfile             # Playwright Python base image
  docker-compose.yml     # Container orchestration
index.html               # Single-file dashboard (vanilla JS + Chart.js)
```
