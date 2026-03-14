"""Test: extract packages with availability from Roma event page."""

import re
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

URL = "https://www.ticketmaster.it/biglietti/wwe-european-summer-tour-roma-06-06-2026/event/bi1t6rrbxn4q"


def parse_price(raw):
    raw = raw.strip()
    if re.match(r'^\d{1,3}(?:\.\d{3})+,\d{2}$', raw):
        return float(raw.replace(".", "").replace(",", "."))
    if re.match(r'^\d+,\d{2}$', raw):
        return float(raw.replace(",", "."))
    if re.match(r'^\d{1,3}(?:,\d{3})+\.\d{2}$', raw):
        return float(raw.replace(",", ""))
    if re.match(r'^\d+\.\d{2}$', raw):
        return float(raw)
    if re.match(r'^\d+$', raw):
        return float(raw)
    return None


def extract_packages(text):
    packages = []
    lines = text.split("\n")
    price_pattern = r'(\d[\d.,]*\d)\s*€\s*cad\.'

    for i, line in enumerate(lines):
        m = re.search(price_pattern, line)
        if not m:
            continue
        price = parse_price(m.group(1))
        if price is None or price < 10 or price > 50000:
            continue

        pkg_name = ""
        for j in range(i - 1, max(i - 5, -1), -1):
            candidate = lines[j].strip()
            if candidate and not re.search(r'€|cad\.|commissioni|prezzi|posti migliori', candidate, re.IGNORECASE):
                pkg_name = candidate
                break

        available = True
        for j in range(i + 1, min(i + 4, len(lines))):
            next_line = lines[j].strip().upper()
            if "NON DISPONIBILE" in next_line or "SOLD OUT" in next_line or "ESAURIT" in next_line:
                available = False
                break
            if re.search(price_pattern, lines[j]):
                break
            if next_line and not re.search(r'COMMISSION|\+|€', next_line, re.IGNORECASE):
                break

        packages.append({"name": pkg_name, "price": price, "available": available})

    return packages


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
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

        print(f"Loading {URL}")
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        for sel in ['#onetrust-accept-btn-handler', 'button:has-text("Accetta")']:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                pass

        body_text = page.inner_text("body")
        packages = extract_packages(body_text)

        print(f"\n{'=' * 70}")
        print(f"{'PACKAGE':<40s} {'PRICE':>10s}  {'STATUS'}")
        print(f"{'=' * 70}")
        for pkg in packages:
            status = "DISPONIBILE" if pkg["available"] else "NON DISPONIBILE"
            print(f"  {pkg['name']:<38s} {pkg['price']:>8,.2f}€  {status}")

        avail = [p for p in packages if p["available"]]
        print(f"\nTotale: {len(packages)} pacchetti, {len(avail)} disponibili")

        browser.close()


if __name__ == "__main__":
    main()
