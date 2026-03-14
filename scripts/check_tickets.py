"""Check Ticketmaster for WWE tickets in Italy, scrape prices via Playwright."""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE_URL = "https://app.ticketmaster.com/discovery/v2"
STATUS_PATH = Path(__file__).resolve().parent.parent / "data" / "status.json"
THRESHOLD_EUR = 400
COMBO_KEYWORDS = ["combo", "2-day", "weekend", "2 giorni", "2-giorni", "two day"]
MAX_HISTORY = 1000

# Ticketmaster.it event page URLs (from Discovery API) are behind queue-it,
# but the artist listing page is accessible and contains event schedule IDs.
# We use those IDs to load individual event pages via Playwright.
ARTIST_PAGE = "https://www.ticketmaster.it/artist/wwe-biglietti/2453"


def load_status() -> dict:
    if STATUS_PATH.exists():
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    return {
        "last_check": None,
        "threshold_eur": THRESHOLD_EUR,
        "alert_active": False,
        "events": [],
        "price_history": [],
        "checks_count": 0,
        "errors": [],
    }


def is_combo(name: str) -> bool:
    lower = name.lower()
    return any(kw in lower for kw in COMBO_KEYWORDS)


def search_events(client: httpx.Client, api_key: str) -> list[dict]:
    params = {
        "keyword": "WWE",
        "countryCode": "IT",
        "startDateTime": "2026-05-30T00:00:00Z",
        "endDateTime": "2026-06-08T23:59:59Z",
        "apikey": api_key,
        "size": 50,
    }
    resp = client.get(f"{BASE_URL}/events.json", params=params)
    resp.raise_for_status()
    data = resp.json()
    embedded = data.get("_embedded", {})
    return embedded.get("events", [])


def extract_date(event: dict) -> str:
    dates = event.get("dates", {}).get("start", {})
    return dates.get("localDate", "")


def extract_url(event: dict) -> str:
    return event.get("url", "")


def extract_venue(event: dict) -> str:
    venues = event.get("_embedded", {}).get("venues", [])
    if venues:
        name = venues[0].get("name", "")
        city = venues[0].get("city", {}).get("name", "")
        return f"{name}, {city}" if city else name
    return ""


def scrape_prices(event_urls: dict[str, str], errors: list[str]) -> dict[str, dict]:
    """Use Playwright to scrape prices from ticketmaster.it event pages.

    Args:
        event_urls: mapping of event_id -> ticketmaster.it URL
        errors: list to append error messages to

    Returns:
        mapping of event_id -> {"price_min": float|None, "price_max": float|None}
    """
    results = {eid: {"price_min": None, "price_max": None} for eid in event_urls}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        errors.append("Playwright not installed, skipping price scrape")
        return results

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="it-IT",
            )

            for event_id, url in event_urls.items():
                try:
                    page = context.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)

                    # Wait for possible queue-it redirect to resolve
                    # If we land on queue-it, wait and retry
                    if "queue-it" in page.url:
                        page.wait_for_url("**/ticketmaster.it/**", timeout=90000)

                    # Wait for price elements to load
                    page.wait_for_timeout(5000)

                    # Try multiple strategies to find prices
                    prices = set()

                    # Strategy 1: look for price patterns in page text
                    content = page.content()
                    # Match patterns like "€ 45,00" or "45,00 €" or "EUR 45.00"
                    for m in re.finditer(r'€\s*(\d+[.,]\d{2})|(\d+[.,]\d{2})\s*€', content):
                        val = m.group(1) or m.group(2)
                        val = val.replace(",", ".")
                        prices.add(float(val))

                    # Strategy 2: look for data attributes or JSON with prices
                    for m in re.finditer(r'"price"\s*:\s*(\d+\.?\d*)', content):
                        prices.add(float(m.group(1)))
                    for m in re.finditer(r'"amount"\s*:\s*(\d+\.?\d*)', content):
                        prices.add(float(m.group(1)))
                    for m in re.finditer(r'"formattedMinPrice"\s*:\s*"(\d+[.,]\d{2})"', content):
                        prices.add(float(m.group(1).replace(",", ".")))

                    # Filter out obviously wrong prices (fees, coordinates, etc.)
                    valid_prices = [p for p in prices if 10 <= p <= 5000]

                    if valid_prices:
                        results[event_id] = {
                            "price_min": min(valid_prices),
                            "price_max": max(valid_prices),
                        }
                        print(f"  {event_id}: prices {min(valid_prices)}-{max(valid_prices)} EUR")
                    else:
                        print(f"  {event_id}: no prices found on page")

                    page.close()

                except Exception as exc:
                    errors.append(f"Scrape failed for {event_id}: {exc}")
                    print(f"  {event_id}: scrape error: {exc}", file=sys.stderr)

            browser.close()

    except Exception as exc:
        errors.append(f"Playwright error: {exc}")

    return results


def main():
    api_key = os.environ.get("TICKETMASTER_API_KEY", "")
    if not api_key:
        print("ERROR: TICKETMASTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    status = load_status()
    now = datetime.now(timezone.utc).isoformat()
    errors: list[str] = []
    events = []
    event_urls: dict[str, str] = {}

    # Step 1: Get event list from Discovery API
    try:
        with httpx.Client(timeout=30) as client:
            raw_events = search_events(client, api_key)
            print(f"Discovery API: found {len(raw_events)} events")

            for ev in raw_events:
                event_id = ev["id"]
                name = ev.get("name", "")
                event_date = extract_date(ev)
                single_day = not is_combo(name)
                url = extract_url(ev)
                venue = extract_venue(ev)

                events.append({
                    "id": event_id,
                    "name": name,
                    "date": event_date,
                    "venue": venue,
                    "is_single_day": single_day,
                    "price_min": None,
                    "price_max": None,
                    "currency": "EUR",
                    "url": url,
                })

                if url:
                    event_urls[event_id] = url

    except httpx.HTTPStatusError as exc:
        errors.append(f"Search API error: {exc.response.status_code}")
    except httpx.RequestError as exc:
        errors.append(f"Request error: {exc}")

    # Step 2: Scrape prices from ticketmaster.it via Playwright
    if event_urls:
        print(f"Scraping prices for {len(event_urls)} events...")
        scraped = scrape_prices(event_urls, errors)

        for ev in events:
            if ev["id"] in scraped:
                ev["price_min"] = scraped[ev["id"]]["price_min"]
                ev["price_max"] = scraped[ev["id"]]["price_max"]

                if ev["price_min"] is not None:
                    status["price_history"].append({
                        "timestamp": now,
                        "event_id": ev["id"],
                        "price_min": ev["price_min"],
                    })

    # Trim history
    status["price_history"] = status["price_history"][-MAX_HISTORY:]

    # Check alert condition: any single-day event under threshold with a known price
    alert = any(
        e["is_single_day"] and e["price_min"] is not None and e["price_min"] < THRESHOLD_EUR
        for e in events
    )

    status.update({
        "last_check": now,
        "threshold_eur": THRESHOLD_EUR,
        "alert_active": alert,
        "events": events,
        "checks_count": status["checks_count"] + 1,
        "errors": errors,
    })

    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Check #{status['checks_count']} done. Events: {len(events)}. Alert: {alert}")
    if errors:
        print(f"Errors: {errors}", file=sys.stderr)


if __name__ == "__main__":
    main()
