"""Test scraper: visit all event pages and dump relevant text."""

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

URLS = [
    ("SmackDown", "https://www.ticketmaster.it/biglietti/wwe-friday-night-smackdown-casalecchio-di-reno-05-06-2026/event/gaw6l9am8v3m"),
    ("Roma", "https://www.ticketmaster.it/biglietti/wwe-european-summer-tour-roma-06-06-2026/event/bi1t6rrbxn4q"),
    ("Firenze", "https://www.ticketmaster.it/biglietti/wwe-european-summer-tour-firenze-07-06-2026/event/xxlb2e7953yg"),
]


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
        cookies_done = False

        for name, url in URLS:
            print(f"\n{'=' * 60}")
            print(f"EVENT: {name}")
            print(f"URL: {url}")
            print("=" * 60)

            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            if not cookies_done:
                for sel in ['#onetrust-accept-btn-handler', 'button:has-text("Accetta")']:
                    try:
                        btn = page.query_selector(sel)
                        if btn and btn.is_visible():
                            btn.click()
                            page.wait_for_timeout(2000)
                            cookies_done = True
                            break
                    except Exception:
                        pass

            body_text = page.inner_text("body")
            # Print lines that contain price-related keywords
            for line in body_text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Show lines with €, cad, price, bigliett, package, standard, vip, or ticket-related words
                lower = line.lower()
                if any(kw in lower for kw in ["€", "cad", "prezzo", "price", "bigliett", "package", "standard", "vip", "gold", "champion", "sold out", "esaurit", "non disponibil", "coming soon", "prossimamente"]):
                    print(f"  {line}")

            # Also print everything after "Biglietti" section if found
            if "Biglietti" in body_text or "Tickets" in body_text:
                in_section = False
                for line in body_text.split("\n"):
                    line = line.strip()
                    if line in ("Biglietti", "Tickets", "Standard", "Biglietti VIP"):
                        in_section = True
                    if in_section and line:
                        print(f"  [section] {line}")
                    if in_section and ("Sleeping" in line or "footer" in line.lower()):
                        break

        browser.close()


if __name__ == "__main__":
    main()
