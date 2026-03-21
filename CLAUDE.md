# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Personal monitoring dashboard that tracks WWE ticket prices for Italian venues and Emirates flight status. Scrapes data from Ticketmaster.it, StubHub.com, and FlightStats, and publishes a unified dashboard via GitHub Pages.

## Architecture

- **Ticketmaster scraper** (`scripts/check_tickets.py`): Uses Ticketmaster Discovery API v2 to find events, then Playwright with stealth mode to scrape package details from event pages. Writes to `data/status.json`.
- **StubHub scraper** (`scripts/check_stubhub.py`): Scrapes StubHub listings for WWE events in Italian cities. Writes to `data/stubhub.json`.
- **Emirates flight scraper** (`scripts/fetch_flights.py`): Scrapes FlightStats for EK78, EK705, EK708, EK77 flight data (departure/arrival times, delays, status). Writes to `data/flights.json`.
- **API-only fallback** (`scripts/check_tickets_api.py`): Lightweight version using only the Ticketmaster API (no browser), used by GitHub Actions CI.
- **Dashboard** (`index.html`): Single-file vanilla JS/HTML dashboard with dark/light theme, three tabs (Ticketmaster, StubHub, Emirates). Reads JSON data files.
- **Docker container** (`docker/`): Runs all three scrapers on configurable intervals (default 15 min), auto-commits and pushes status updates.

## Commands

```bash
# Install dependencies
pip install -r scripts/requirements.txt

# Run full Ticketmaster check (requires TICKETMASTER_API_KEY env var)
python scripts/check_tickets.py

# Run StubHub check
python scripts/check_stubhub.py

# Run Emirates flight check
DATA_FILE=data/flights.json python scripts/fetch_flights.py

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
- **Flight tracking**: Tracks 5 Emirates flights (EK78, EK705, EK706, EK708, EK77) over a 6-day rolling window (3 past + 3 future). Skips already-landed flights. Data sourced from FlightStats `__NEXT_DATA__` JSON. Output format: `{"last_check": ISO, "flights": [...]}` with backward-compatible loading. Multi-leg flights (EK708) use `otherDays[].flights[]` to find the correct leg via `flightId`.
- **CI/CD**: `check.yml` runs API-only check every 6 hours. `pages.yml` deploys to GitHub Pages on push. Auto-commits use `[skip ci]` tag and `git pull --rebase`.
