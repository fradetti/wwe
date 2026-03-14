"""Check StubHub for WWE tickets in Italy, scrape prices via Playwright stealth."""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

GROUPING_URL = "https://www.stubhub.it/biglietti-wwe/grouping/131/"
STATUS_PATH = Path(os.environ.get("STUBHUB_STATUS_PATH", Path(__file__).resolve().parent.parent / "data" / "stubhub.json"))
THRESHOLD_EUR = 400
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


def _dismiss_cookies(page) -> None:
    """Accept cookie consent if present."""
    for sel in [
        '#onetrust-accept-btn-handler',
        'button[id*="accept"]',
        'button:has-text("Accetta")',
        'button:has-text("Accept")',
        'button:has-text("Accetta tutti")',
    ]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(2000)
                return
        except Exception:
            pass


def _extract_event_links(page) -> list[dict]:
    """Extract individual event links from StubHub grouping page.

    Returns list of dicts with keys: name, date, url, venue.
    """
    events = []
    # StubHub grouping pages list events as links with event info
    # Look for links that point to individual event pages
    links = page.query_selector_all('a[href*="/biglietti-"]')
    seen_urls = set()

    for link in links:
        try:
            href = link.get_attribute("href") or ""
            # Skip the grouping page itself and non-event links
            if "/grouping/" in href or not href:
                continue
            # Make absolute URL if needed
            if href.startswith("/"):
                href = "https://www.stubhub.it" + href
            if href in seen_urls:
                continue
            seen_urls.add(href)

            text = link.inner_text().strip()
            if not text or "wwe" not in text.lower():
                continue

            events.append({
                "name": text,
                "url": href,
            })
        except Exception:
            pass

    return events


def _extract_listings(page_text: str) -> list[dict]:
    """Extract ticket listings from a StubHub event page.

    StubHub shows listings as rows with section/seat info and prices.
    All listed tickets are available (StubHub doesn't show sold-out listings).
    """
    packages = []
    lines = page_text.split("\n")

    # StubHub price patterns: "123,45 €" or "€ 123,45" or "123.45 €"
    price_pattern = r'(\d[\d.,]*\d)\s*€'

    for i, line in enumerate(lines):
        m = re.search(price_pattern, line)
        if not m:
            continue

        price = _parse_price(m.group(1))
        if price is None or price < 10 or price > 50000:
            continue

        # Try to find section/category name from nearby lines
        pkg_name = ""
        # Check the line itself (price might be on same line as section)
        line_clean = re.sub(price_pattern, '', line).strip()
        line_clean = re.sub(r'[€\s]+$', '', line_clean).strip()
        if line_clean and len(line_clean) > 2 and not re.match(r'^[\d.,\s€]+$', line_clean):
            pkg_name = line_clean
        else:
            # Walk back to find section name
            for j in range(i - 1, max(i - 5, -1), -1):
                candidate = lines[j].strip()
                if candidate and len(candidate) > 2 and not re.search(r'€|ciascuno|tassa|commissioni', candidate, re.IGNORECASE) and not re.match(r'^[\d.,\s]+$', candidate):
                    pkg_name = candidate
                    break

        # Avoid duplicate entries for same name+price
        packages.append({
            "name": pkg_name,
            "price": price,
            "available": True,  # StubHub only shows available tickets
        })

    return packages


def _deduplicate_packages(packages: list[dict]) -> list[dict]:
    """Remove exact duplicates (same name + price)."""
    seen = set()
    result = []
    for pkg in packages:
        key = (pkg["name"], pkg["price"])
        if key not in seen:
            seen.add(key)
            result.append(pkg)
    return result


def _extract_event_info_from_page(page_text: str) -> dict:
    """Try to extract event name, date, and venue from StubHub event page text."""
    info = {"name": "", "date": "", "venue": ""}

    # Date patterns: "31 maggio 2026", "31 mag 2026", "sab 31 mag 2026"
    month_map = {
        "gen": "01", "feb": "02", "mar": "03", "apr": "04", "mag": "05", "giu": "06",
        "lug": "07", "ago": "08", "set": "09", "ott": "10", "nov": "11", "dic": "12",
        "gennaio": "01", "febbraio": "02", "marzo": "03", "aprile": "04",
        "maggio": "05", "giugno": "06", "luglio": "07", "agosto": "08",
        "settembre": "09", "ottobre": "10", "novembre": "11", "dicembre": "12",
    }

    date_pattern = r'(\d{1,2})\s+(' + '|'.join(month_map.keys()) + r')\s+(\d{4})'
    m = re.search(date_pattern, page_text.lower())
    if m:
        day = m.group(1).zfill(2)
        month = month_map[m.group(2)]
        year = m.group(3)
        info["date"] = f"{year}-{month}-{day}"

    return info


def scrape_stubhub(errors: list[str]) -> list[dict]:
    """Scrape StubHub for WWE Italy events.

    Returns list of event dicts matching the status.json structure.
    """
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

            # Step 1: Visit grouping page to discover event URLs
            print(f"Visiting StubHub grouping page: {GROUPING_URL}")
            page.goto(GROUPING_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(8000)

            _dismiss_cookies(page)
            page.wait_for_timeout(2000)

            # Extract event links from grouping page
            event_links = _extract_event_links(page)
            print(f"Found {len(event_links)} event links on grouping page")

            if not event_links:
                # Fallback: grab all links from page text
                page_text = page.inner_text("body")
                print(f"Grouping page text length: {len(page_text)}")
                errors.append("No event links found on StubHub grouping page")

            # Step 2: Visit each event page
            for idx, ev_link in enumerate(event_links):
                url = ev_link["url"]
                try:
                    print(f"  [{idx+1}/{len(event_links)}] Visiting {url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(8000)  # Longer wait for StubHub anti-bot

                    page_text = page.inner_text("body")
                    info = _extract_event_info_from_page(page_text)
                    listings = _extract_listings(page_text)
                    listings = _deduplicate_packages(listings)

                    # Use link text as name if we couldn't extract from page
                    event_name = info.get("name") or ev_link.get("name", "")
                    event_date = info.get("date", "")

                    # Generate a stable ID from URL
                    event_id = re.sub(r'[^a-z0-9]', '', url.split("/")[-2] if url.rstrip("/").count("/") > 2 else url)[-20:]

                    if listings:
                        all_prices = [pkg["price"] for pkg in listings]
                        price_min = min(all_prices)
                        price_max = max(all_prices)
                        print(f"    {len(listings)} listings, {price_min:.2f}€ - {price_max:.2f}€")
                    else:
                        price_min = None
                        price_max = None
                        print(f"    No listings found")

                    events.append({
                        "id": f"stubhub-{event_id}",
                        "name": event_name,
                        "date": event_date,
                        "venue": info.get("venue", ""),
                        "is_single_day": True,
                        "price_min": price_min,
                        "price_max": price_max,
                        "currency": "EUR",
                        "url": url,
                        "packages": listings,
                    })

                    # Wait between pages to avoid anti-bot
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

    # Trim history
    status["price_history"] = status["price_history"][-MAX_HISTORY:]

    # Check alert condition — only for Clash in Italy 31/05
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
