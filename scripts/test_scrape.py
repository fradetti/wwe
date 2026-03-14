"""Test scraper: visit the Clash in Italy event page and dump all packages with prices."""

import re
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

URL = "https://www.ticketmaster.it/biglietti/wwe-clash-in-italy-torino-31-05-2026/event/ur737yvj5cba"


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

        # Dismiss cookies
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

        # Extract packages: "Package Name\n1.234,56€ cad."
        print("\n" + "=" * 60)
        print("PACKAGES FOUND:")
        print("=" * 60)

        price_pattern = r'(\d[\d.,]*\d)'
        lines = body_text.split("\n")
        for i, line in enumerate(lines):
            m = re.search(price_pattern + r'\s*€\s*cad\.', line)
            if m:
                price = parse_price(m.group(1))
                # Package name is usually the previous non-empty line
                pkg_name = ""
                for j in range(i - 1, max(i - 3, -1), -1):
                    if lines[j].strip() and not re.search(r'€|cad\.|commissioni', lines[j]):
                        pkg_name = lines[j].strip()
                        break
                print(f"  {pkg_name:<30s} {price:>10,.2f}€")

        # Also show all "cad." prices for verification
        print("\n" + "=" * 60)
        print("ALL 'cad.' PRICES (raw):")
        print("=" * 60)
        for m in re.finditer(price_pattern + r'\s*€\s*cad\.', body_text):
            raw = m.group(1)
            parsed = parse_price(raw)
            print(f"  raw: {raw:<15s} -> parsed: {parsed}")

        browser.close()


if __name__ == "__main__":
    main()
