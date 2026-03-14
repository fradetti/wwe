"""Check StubHub (.com) for WWE tickets in Italy, scrape prices via Playwright stealth."""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SEARCH_URL = "https://www.stubhub.com/search?q=WWE+Italy"
STATUS_PATH = Path(os.environ.get("STUBHUB_STATUS_PATH", Path(__file__).resolve().parent.parent / "data" / "stubhub.json"))
THRESHOLD_EUR = 400
MAX_HISTORY = 1000

# Keywords to identify Italian WWE events in search results
ITALY_EVENT_KEYWORDS = ["italy", "torino", "turin", "roma", "rome", "bologna",
                        "casalecchio", "firenze", "florence", "milano", "milan"]


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


def _parse_price(raw: str) -> float | None:
    """Parse a price string like '1,234' or '416' or '2,427'."""
    raw = raw.strip().replace(",", "")
    if not raw:
        return None
    try:
        val = float(raw)
        return val if 10 <= val <= 50000 else None
    except ValueError:
        return None


def _dismiss_cookies(page) -> None:
    """Accept cookie consent if present."""
    for sel in [
        '#onetrust-accept-btn-handler',
        'button[id*="accept"]',
        'button:has-text("Accept")',
        'button:has-text("Accetta")',
    ]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(2000)
                return
        except Exception:
            pass


def _discover_italy_events(page, errors: list[str]) -> list[dict]:
    """Search StubHub for WWE events in Italy.

    Returns list of dicts with keys: name, url.
    """
    events = []
    seen_urls = set()

    print(f"Searching StubHub: {SEARCH_URL}")
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(10000)
    _dismiss_cookies(page)
    page.wait_for_timeout(2000)

    links = page.query_selector_all("a")
    for link in links:
        try:
            href = link.get_attribute("href") or ""
            if "/event/" not in href:
                continue
            if href.startswith("/"):
                href = "https://www.stubhub.com" + href
            # Strip query params for dedup
            clean_url = href.split("?")[0]
            if clean_url in seen_urls:
                continue

            text = link.inner_text().strip()
            combined = (href + " " + text).lower()
            # Must be a WWE event in Italy
            if "wwe" not in combined:
                continue
            if not any(kw in combined for kw in ITALY_EVENT_KEYWORDS):
                continue

            seen_urls.add(clean_url)
            # Clean up multi-line text
            name = " ".join(text.split("\n")[0:3]).strip()
            events.append({"name": name, "url": clean_url})
        except Exception:
            pass

    if not events:
        errors.append("No Italian WWE events found on StubHub search")

    return events


def _extract_listings_from_page(page) -> list[dict]:
    """Extract ticket listings from a StubHub event page.

    StubHub format (line by line after 'X listings'):
        Section XXX  (or "I Anello Est", "Floor", etc.)
        Row X        (optional)
        2 tickets together
        ...features...
        €PRICE

    All listed tickets are available.
    """
    # Click "Show more" until all listings are visible
    for _ in range(15):
        try:
            btn = page.query_selector('button:has-text("Show more")')
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(3000)
            else:
                break
        except Exception:
            break

    text = page.inner_text("body")
    lines = text.split("\n")
    packages = []

    # Price pattern: €416 or €1,234 or €2,427
    price_re = re.compile(r'^€([\d,]+)\s*$')
    section_re = re.compile(r'^(Section\s+.+|I+\s+Anello\s+.+|Floor|Ring|MIX)$', re.IGNORECASE)

    current_section = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Track current section
        sm = section_re.match(line)
        if sm:
            current_section = sm.group(1)
            continue

        # Also capture "Section X" from lines like "Section J"
        sm2 = re.match(r'^Section\s+(\S+)', line)
        if sm2:
            current_section = line
            continue

        # Check for price
        pm = price_re.match(line)
        if pm:
            price = _parse_price(pm.group(1))
            if price is not None:
                packages.append({
                    "name": current_section or "Sconosciuto",
                    "price": price,
                    "available": True,
                })

    return packages


def _extract_event_meta(page_text: str) -> dict:
    """Extract event name, date, venue from StubHub event page header.

    Expected format in page text:
        WWE Clash in Italy
        Sun May 31 2026 at 7:30 PM
        Pala Alpitour (Inalpi Arena), Turin, Italy
    """
    info = {"name": "", "date": "", "venue": ""}

    # English date: "Sun May 31 2026" or "May 31 2026"
    month_map = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
        "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    m = re.search(r'(?:Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s+(\w{3})\s+(\d{1,2})\s+(\d{4})', page_text)
    if m:
        month = month_map.get(m.group(1).lower()[:3], "")
        if month:
            day = m.group(2).zfill(2)
            info["date"] = f"{m.group(3)}-{month}-{day}"

    # Venue: line containing arena/city info near the top
    lines = page_text.split("\n")
    for i, line in enumerate(lines):
        if re.search(r'\d{4}\s+at\s+\d', line):
            # Look at next few lines for venue (skip empty, "Favorite", etc.)
            for j in range(i + 1, min(i + 4, len(lines))):
                candidate = lines[j].strip()
                if candidate and "Favorite" not in candidate and "EUR" not in candidate and len(candidate) > 5:
                    info["venue"] = candidate
                    break
            break

    # Name: first meaningful line (usually the event title)
    for line in lines[:10]:
        clean = line.strip()
        if clean and "WWE" in clean and len(clean) > 5:
            info["name"] = clean
            break

    return info


def scrape_stubhub(errors: list[str]) -> list[dict]:
    """Scrape StubHub for WWE Italy events."""
    events = []

    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
    except ImportError as exc:
        errors.append(f"Import error: {exc}")
        return events

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

            # Step 1: Discover Italian WWE events via search
            event_links = _discover_italy_events(page, errors)
            print(f"Found {len(event_links)} Italian WWE event(s)")

            # Step 2: Visit each event page and extract listings
            for idx, ev_link in enumerate(event_links):
                url = ev_link["url"]
                try:
                    print(f"  [{idx+1}/{len(event_links)}] Visiting {url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(10000)

                    page_text = page.inner_text("body")
                    meta = _extract_event_meta(page_text)
                    listings = _extract_listings_from_page(page)

                    event_name = meta.get("name") or ev_link.get("name", "")
                    event_date = meta.get("date", "")
                    venue = meta.get("venue", "")

                    # Generate stable ID from URL
                    url_parts = url.rstrip("/").split("/")
                    event_id = url_parts[-1] if url_parts else "unknown"

                    if listings:
                        all_prices = [pkg["price"] for pkg in listings]
                        print(f"    {len(listings)} listings, {min(all_prices):.0f}€ - {max(all_prices):.0f}€")
                        price_min = min(all_prices)
                        price_max = max(all_prices)
                    else:
                        print("    No listings found")
                        price_min = None
                        price_max = None

                    events.append({
                        "id": f"stubhub-{event_id}",
                        "name": event_name,
                        "date": event_date,
                        "venue": venue,
                        "is_single_day": True,
                        "price_min": price_min,
                        "price_max": price_max,
                        "currency": "EUR",
                        "url": url,
                        "packages": listings,
                    })

                    if idx < len(event_links) - 1:
                        page.wait_for_timeout(10000)

                except Exception as exc:
                    print(f"    Error on {url}: {exc}", file=sys.stderr)
                    errors.append(f"StubHub scrape error ({url}): {exc}")

            browser.close()

    except Exception as exc:
        errors.append(f"Playwright error: {exc}")
        print(f"Playwright error: {exc}", file=sys.stderr)

    return events


def main():
    status = load_status()
    now = datetime.now(timezone.utc).isoformat()
    errors: list[str] = []

    print("Starting StubHub check...")
    events = scrape_stubhub(errors)
    print(f"StubHub: found {len(events)} events")

    # Price history
    for ev in events:
        if ev["price_min"] is not None:
            status["price_history"].append({
                "timestamp": now,
                "event_id": ev["id"],
                "price_min": ev["price_min"],
            })

    status["price_history"] = status["price_history"][-MAX_HISTORY:]

    # Alert: Clash in Italy 31/05 under threshold
    clash = next(
        (e for e in events if "Clash in Italy" in e.get("name", "") and e.get("date") == "2026-05-31"),
        None,
    )
    clash_avail_prices = [
        pkg["price"] for pkg in (clash.get("packages", []) if clash else []) if pkg.get("available")
    ]
    alert = bool(clash_avail_prices) and min(clash_avail_prices) < THRESHOLD_EUR

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

    print(f"StubHub check #{status['checks_count']} done. Events: {len(events)}. Alert: {alert}")
    if errors:
        print(f"Errors: {errors}", file=sys.stderr)


if __name__ == "__main__":
    main()
