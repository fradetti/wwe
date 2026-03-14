"""Check Ticketmaster for WWE tickets in Italy, scrape prices via Playwright stealth."""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE_URL = "https://app.ticketmaster.com/discovery/v2"
STATUS_PATH = Path(os.environ.get("STATUS_PATH", Path(__file__).resolve().parent.parent / "data" / "status.json"))
THRESHOLD_EUR = 400
COMBO_KEYWORDS = ["combo", "2-day", "weekend", "2 giorni", "2-giorni", "two day"]
MAX_HISTORY = 1000


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


def _parse_price(raw: str) -> float | None:
    """Parse a price string handling both Italian (1.234,56) and English (1,234.56) formats."""
    raw = raw.strip()
    if not raw:
        return None
    # Italian format: 1.234,56 or 10.142,50 (dot=thousands, comma=decimal)
    if re.match(r'^\d{1,3}(?:\.\d{3})+,\d{2}$', raw):
        return float(raw.replace(".", "").replace(",", "."))
    # Italian without thousands separator: 6017,50 or 234,56
    if re.match(r'^\d+,\d{2}$', raw):
        return float(raw.replace(",", "."))
    # English format: 1,234.56 (comma=thousands, dot=decimal)
    if re.match(r'^\d{1,3}(?:,\d{3})+\.\d{2}$', raw):
        return float(raw.replace(",", ""))
    # Simple with dot decimal: 234.56
    if re.match(r'^\d+\.\d{2}$', raw):
        return float(raw)
    # Integer: 234
    if re.match(r'^\d+$', raw):
        return float(raw)
    return None


def _extract_packages(text: str) -> list[dict]:
    """Extract ticket packages from page text with name, price, and availability.

    Ticketmaster.it format (line by line):
        Package Name
        1.234,56€ cad.
        + commissioni
        NON DISPONIBILE  (optional — if absent, the package is available)
    """
    packages = []
    lines = text.split("\n")
    price_pattern = r'(\d[\d.,]*\d)\s*€\s*cad\.'

    for i, line in enumerate(lines):
        m = re.search(price_pattern, line)
        if not m:
            continue
        price = _parse_price(m.group(1))
        if price is None or price < 10 or price > 50000:
            continue

        # Package name: walk back to find non-empty line that isn't a price/fee/filter
        pkg_name = ""
        for j in range(i - 1, max(i - 5, -1), -1):
            candidate = lines[j].strip()
            if candidate and not re.search(r'€|cad\.|commissioni|prezzi|posti migliori', candidate, re.IGNORECASE):
                pkg_name = candidate
                break

        # Availability: check the next few lines for "NON DISPONIBILE"
        available = True
        for j in range(i + 1, min(i + 4, len(lines))):
            next_line = lines[j].strip().upper()
            if "NON DISPONIBILE" in next_line or "SOLD OUT" in next_line or "ESAURIT" in next_line:
                available = False
                break
            # Stop looking if we hit the next package name or price
            if re.search(price_pattern, lines[j]):
                break
            if next_line and not re.search(r'COMMISSION|\+|€', next_line, re.IGNORECASE):
                break

        packages.append({
            "name": pkg_name,
            "price": price,
            "available": available,
        })

    return packages


def _dismiss_cookies(page) -> None:
    """Accept cookie consent if present."""
    for sel in [
        '#onetrust-accept-btn-handler',
        'button[id*="accept"]',
        'button:has-text("Accetta")',
        'button:has-text("Accept")',
    ]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(2000)
                return
        except Exception:
            pass


def scrape_event_pages(event_urls: list[str], errors: list[str]) -> dict[str, dict]:
    """Visit each event page individually via Playwright and extract packages.

    Returns:
        mapping of schedule_id -> {
            "packages": [{"name": str, "price": float, "available": bool}, ...],
            "price_min": float|None,
            "price_max": float|None,
        }
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
            cookies_dismissed = False

            for url in event_urls:
                schedule_id = match_schedule_id(url)
                if not schedule_id:
                    continue

                try:
                    print(f"  Visiting {url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(5000)

                    if not cookies_dismissed:
                        _dismiss_cookies(page)
                        cookies_dismissed = True

                    # Try to find and click a "Biglietti" / "Tickets" button to reveal prices
                    for btn_sel in [
                        'button:has-text("Biglietti")',
                        'button:has-text("Tickets")',
                        'button:has-text("Vedi biglietti")',
                        'button:has-text("See Tickets")',
                        'a:has-text("Biglietti")',
                        'a:has-text("Tickets")',
                    ]:
                        try:
                            btn = page.query_selector(btn_sel)
                            if btn and btn.is_visible():
                                btn.click()
                                page.wait_for_timeout(3000)
                                break
                        except Exception:
                            pass

                    page_text = page.inner_text("body")
                    packages = _extract_packages(page_text)

                    if packages:
                        all_prices = [pkg["price"] for pkg in packages]
                        avail_count = sum(1 for pkg in packages if pkg["available"])
                        results[schedule_id] = {
                            "packages": packages,
                            "price_min": min(all_prices),
                            "price_max": max(all_prices),
                        }
                        print(f"    {len(packages)} packages ({avail_count} available), "
                              f"{min(all_prices):.2f}€ - {max(all_prices):.2f}€")
                    else:
                        print(f"    No packages found")

                except Exception as exc:
                    print(f"    Error on {url}: {exc}", file=sys.stderr)
                    errors.append(f"Scrape error ({schedule_id}): {exc}")

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
                    "packages": [],
                })

    except httpx.HTTPStatusError as exc:
        errors.append(f"Search API error: {exc.response.status_code}")
    except httpx.RequestError as exc:
        errors.append(f"Request error: {exc}")

    # Step 2: Scrape packages from individual event pages via Playwright
    event_urls = [ev["url"] for ev in events if ev["url"]]
    print(f"Scraping packages from {len(event_urls)} event pages...")
    scraped = scrape_event_pages(event_urls, errors)
    print(f"Scraped packages for {len(scraped)} events")

    # Match scraped data to events by schedule ID in URL
    for ev in events:
        schedule_id = match_schedule_id(ev["url"])
        if schedule_id and schedule_id in scraped:
            data = scraped[schedule_id]
            ev["packages"] = data["packages"]
            ev["price_min"] = data["price_min"]
            ev["price_max"] = data["price_max"]

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
