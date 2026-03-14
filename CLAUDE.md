# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WWE ticket price monitoring system for Italian venues. Scrapes WWE ticket availability and pricing from Ticketmaster.it (API + Playwright) and StubHub.com, tracks prices over time, sends alerts when tickets drop below €400, and publishes a real-time dashboard via GitHub Pages.

## Architecture

- **Ticketmaster scraper** (`scripts/check_tickets.py`): Uses Ticketmaster Discovery API v2 to find events, then Playwright with stealth mode to scrape package details from event pages. Writes to `data/status.json`.
- **StubHub scraper** (`scripts/check_stubhub.py`): Scrapes StubHub listings for WWE events in Italian cities. Writes to `data/stubhub.json`.
- **API-only fallback** (`scripts/check_tickets_api.py`): Lightweight version using only the Ticketmaster API (no browser), used by GitHub Actions CI.
- **Dashboard** (`index.html`): Single-file vanilla JS/HTML dashboard with Chart.js price charts, dark/light theme, event filtering. Reads JSON data files.
- **Docker container** (`docker/`): Runs full scraper on configurable intervals (default 15 min), auto-commits and pushes status updates.

## Commands

```bash
# Install dependencies
pip install -r scripts/requirements.txt

# Run full Ticketmaster check (requires TICKETMASTER_API_KEY env var)
python scripts/check_tickets.py

# Run StubHub check
python scripts/check_stubhub.py

# Run API-only check (no browser needed)
python scripts/check_tickets_api.py

# Test individual scrapers
python scripts/test_scrape.py
python scripts/test_packages.py
python scripts/test_scrape_all.py

# Docker
docker-compose -f docker/docker-compose.yml up --build
```

## Environment Variables

- `TICKETMASTER_API_KEY` — required for Ticketmaster API access
- `GITHUB_PAT` — for auto-pushing status updates from Docker
- `GIT_REPO` — repo path (default: `fradetti/wwe`)
- `CHECK_INTERVAL` — scraper interval in seconds (default: 900)

## Key Details

- **Price parsing**: Handles both Italian (`1.234,56€`) and English (`1,234.56€`) number formats. Availability detection uses Italian keywords (`NON DISPONIBILE`, `ESAURIT`) and English (`SOLD OUT`).
- **Date range filter**: Events are filtered to May 30 – June 8, 2026 window.
- **StubHub city filter**: Events filtered by URL-based Italian city keywords (torino, roma, bologna, etc.).
- **Price history**: JSON files store up to 1000 historical price entries per event.
- **CI/CD**: `check.yml` runs API-only check every 6 hours. `pages.yml` deploys to GitHub Pages on push. Auto-commits use `[skip ci]` tag and `git pull --rebase`.
