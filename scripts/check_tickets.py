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


def scrape_prices_from_artist_page(errors: list[str]) -> dict[str, dict]:
    """Use Playwright to load the artist page and extract prices for each event.

    Returns:
        mapping of ticketmaster.it schedule_id -> {"price_min": float|None, "price_max": float|None}
    """
    results = {}

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
            page = context.new_page()

            print(f"Loading artist page: {ARTIST_PAGE}")
            page.goto(ARTIST_PAGE, wait_until="networkidle", timeout=60000)

            # Wait for React to render event listings
            try:
                page.wait_for_selector('a[href*="/event/"]', timeout=15000)
                print("Event links appeared in DOM")
            except Exception:
                print("Timed out waiting for event links")

            page.wait_for_timeout(5000)

            # Save screenshot for debugging
            screenshot_path = STATUS_PATH.parent / "debug_screenshot.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            print(f"Screenshot saved to {screenshot_path}")

            content = page.content()
            print(f"Artist page loaded, {len(content)} chars")

            # Save page HTML for debugging
            debug_html_path = STATUS_PATH.parent / "debug_page.html"
            debug_html_path.write_text(content[:50000], encoding="utf-8")

            # Try multiple selector strategies
            selectors = [
                'a[href*="/event/"]',
                '[data-testid="event-list-item"]',
                '[data-testid*="event"]',
                'li:has(a[href*="/event/"])',
                'div:has(> a[href*="/event/"])',
            ]
            event_cards = []
            used_selector = ""
            for sel in selectors:
                cards = page.query_selector_all(sel)
                if cards:
                    event_cards = cards
                    used_selector = sel
                    break

            print(f"Found {len(event_cards)} event card elements (selector: {used_selector!r})")

            for card in event_cards:
                card_html = card.inner_html()
                card_text = card.inner_text()

                # Find schedule ID in card links
                id_match = re.search(r'/event/([a-z0-9]+)', card_html)
                if not id_match:
                    continue
                schedule_id = id_match.group(1)

                # Find prices in card text
                prices = set()
                for m in re.finditer(r'€\s*(\d+(?:[.,]\d{2})?)', card_text):
                    val = m.group(1).replace(",", ".")
                    prices.add(float(val))
                for m in re.finditer(r'(\d+(?:[.,]\d{2})?)\s*€', card_text):
                    val = m.group(1).replace(",", ".")
                    prices.add(float(val))
                # Also check for "da X" pattern (starting from X)
                for m in re.finditer(r'(?:da|from)\s*€?\s*(\d+(?:[.,]\d{2})?)', card_text, re.IGNORECASE):
                    val = m.group(1).replace(",", ".")
                    prices.add(float(val))

                valid_prices = [p for p in prices if 10 <= p <= 5000]
                if valid_prices:
                    results[schedule_id] = {
                        "price_min": min(valid_prices),
                        "price_max": max(valid_prices),
                    }
                    print(f"  {schedule_id}: {min(valid_prices)}-{max(valid_prices)} EUR")
                else:
                    print(f"  {schedule_id}: no prices in card text: {card_text[:100]!r}")

            # Fallback: if no cards found, try scanning full page for price+event patterns
            if not results:
                print("No prices from cards, trying full page scan...")
                full_text = page.inner_text("body")
                print(f"Full page text length: {len(full_text)}")
                # Log a sample to debug
                for line in full_text.split("\n"):
                    if "€" in line or "partire" in line.lower():
                        print(f"  Price line: {line.strip()[:120]}")

            browser.close()

    except Exception as exc:
        errors.append(f"Playwright error: {exc}")
        print(f"Playwright error: {exc}", file=sys.stderr)

    return results


def match_schedule_id(event_url: str) -> str | None:
    """Extract schedule ID from ticketmaster.it URL."""
    m = re.search(r'/event/([a-z0-9]+)', event_url)
    return m.group(1) if m else None


def main():
    api_key = os.environ.get("TICKETMASTER_API_KEY", "")
    if not api_key:
        print("ERROR: TICKETMASTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    status = load_status()
    now = datetime.now(timezone.utc).isoformat()
    errors: list[str] = []
    events = []

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

    except httpx.HTTPStatusError as exc:
        errors.append(f"Search API error: {exc.response.status_code}")
    except httpx.RequestError as exc:
        errors.append(f"Request error: {exc}")

    # Step 2: Scrape prices from ticketmaster.it artist page via Playwright
    print("Scraping prices from artist page...")
    scraped = scrape_prices_from_artist_page(errors)
    print(f"Scraped prices for {len(scraped)} events")

    # Match scraped prices to events by schedule ID in URL
    for ev in events:
        schedule_id = match_schedule_id(ev["url"])
        if schedule_id and schedule_id in scraped:
            ev["price_min"] = scraped[schedule_id]["price_min"]
            ev["price_max"] = scraped[schedule_id]["price_max"]

            status["price_history"].append({
                "timestamp": now,
                "event_id": ev["id"],
                "price_min": ev["price_min"],
            })

    # Trim history
    status["price_history"] = status["price_history"][-MAX_HISTORY:]

    # Check alert condition
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
