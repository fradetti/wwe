"""Check Ticketmaster for WWE tickets in Italy, scrape prices via Playwright stealth."""

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
    """Use Playwright with stealth to load the artist page and extract prices.

    Returns:
        mapping of ticketmaster.it schedule_id -> {"price_min": float|None, "price_max": float|None}
    """
    results = {}

    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
    except ImportError as exc:
        errors.append(f"Import error: {exc}")
        return results

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="it-IT",
                timezone_id="Europe/Rome",
                viewport={"width": 1920, "height": 1080},
            )
            stealth = Stealth()
            stealth.apply_stealth_sync(context)
            page = context.new_page()

            print(f"Loading artist page: {ARTIST_PAGE}")
            page.goto(ARTIST_PAGE, wait_until="domcontentloaded", timeout=60000)

            # Wait for React to render event listings
            try:
                page.wait_for_selector('a[href*="/event/"]', timeout=30000)
                print("Event links appeared in DOM")
            except Exception:
                print("Timed out waiting for event links")

            page.wait_for_timeout(3000)

            # Accept cookie consent if present
            for consent_sel in [
                '#onetrust-accept-btn-handler',
                'button[id*="accept"]',
                'button:has-text("Accetta")',
                'button:has-text("Accept")',
            ]:
                try:
                    btn = page.query_selector(consent_sel)
                    if btn and btn.is_visible():
                        btn.click()
                        print(f"Clicked consent: {consent_sel}")
                        page.wait_for_timeout(2000)
                        break
                except Exception:
                    pass

            print(f"Current URL: {page.url}")
            print(f"Page title: {page.title()}")

            # Save screenshot for debugging
            screenshot_path = STATUS_PATH.parent / "debug_screenshot.png"
            page.screenshot(path=str(screenshot_path), full_page=True)

            content = page.content()
            body_text = page.inner_text("body")
            print(f"Page: {len(content)} chars HTML, {len(body_text)} chars text")

            # Log lines with prices or event names for debugging
            for line in body_text.split("\n"):
                line = line.strip()
                if line and ("€" in line or "partire" in line.lower() or "WWE" in line):
                    print(f"  >> {line[:150]}")

            # Find event cards with multiple strategies
            event_links = page.query_selector_all('a[href*="/event/"]')
            print(f"Found {len(event_links)} event links")

            for link in event_links:
                href = link.get_attribute("href") or ""
                id_match = re.search(r'/event/([a-z0-9]+)', href)
                if not id_match:
                    continue
                schedule_id = id_match.group(1)

                # Walk up to find the containing card/row
                card = link
                for _ in range(5):
                    parent = card.evaluate_handle("el => el.parentElement")
                    if parent:
                        card = parent.as_element()
                        if not card:
                            break
                    else:
                        break

                card_text = ""
                try:
                    card_text = card.inner_text() if card else ""
                except Exception:
                    pass

                # Extract prices
                prices = set()
                for m in re.finditer(r'€\s*(\d+(?:[.,]\d{2})?)', card_text):
                    val = m.group(1).replace(",", ".")
                    prices.add(float(val))
                for m in re.finditer(r'(\d+(?:[.,]\d{2})?)\s*€', card_text):
                    val = m.group(1).replace(",", ".")
                    prices.add(float(val))
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
                    print(f"  {schedule_id}: no price found. Text: {card_text[:100]!r}")

            # Also try extracting prices from the full HTML via regex
            # TM sometimes embeds price data in JSON within script tags
            if not results:
                print("Trying HTML regex fallback...")
                for m in re.finditer(
                    r'/event/([a-z0-9]+).*?(\d+[.,]\d{2})\s*€',
                    content, re.DOTALL
                ):
                    sid = m.group(1)
                    price = float(m.group(2).replace(",", "."))
                    if 10 <= price <= 5000 and sid not in results:
                        results[sid] = {"price_min": price, "price_max": price}
                        print(f"  regex: {sid} = {price} EUR")

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
