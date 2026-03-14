"""Lightweight fallback: Discovery API only (no Playwright scraping).

Used by GitHub Actions as a backup. Prices come from the Docker container's
Playwright scraping — this script only updates event metadata and preserves
existing prices from previous runs.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE_URL = "https://app.ticketmaster.com/discovery/v2"
STATUS_PATH = Path(__file__).resolve().parent.parent / "data" / "status.json"
THRESHOLD_EUR = 400
COMBO_KEYWORDS = ["combo", "2-day", "weekend", "2 giorni", "2-giorni", "two day"]


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
    return data.get("_embedded", {}).get("events", [])


def main():
    api_key = os.environ.get("TICKETMASTER_API_KEY", "")
    if not api_key:
        print("ERROR: TICKETMASTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    status = load_status()
    now = datetime.now(timezone.utc).isoformat()
    errors: list[str] = []

    # Build a map of existing prices by event ID (from Docker scraping)
    existing_prices = {}
    for ev in status.get("events", []):
        if ev.get("price_min") is not None:
            existing_prices[ev["id"]] = {
                "price_min": ev["price_min"],
                "price_max": ev["price_max"],
            }

    events = []
    try:
        with httpx.Client(timeout=30) as client:
            raw_events = search_events(client, api_key)
            print(f"Discovery API: {len(raw_events)} events")

            for ev in raw_events:
                event_id = ev["id"]
                name = ev.get("name", "")
                dates = ev.get("dates", {}).get("start", {})
                event_date = dates.get("localDate", "")
                url = ev.get("url", "")
                venues = ev.get("_embedded", {}).get("venues", [])
                venue = ""
                if venues:
                    vname = venues[0].get("name", "")
                    city = venues[0].get("city", {}).get("name", "")
                    venue = f"{vname}, {city}" if city else vname

                # Preserve prices from previous Docker scraping
                prices = existing_prices.get(event_id, {})

                events.append({
                    "id": event_id,
                    "name": name,
                    "date": event_date,
                    "venue": venue,
                    "is_single_day": not is_combo(name),
                    "price_min": prices.get("price_min"),
                    "price_max": prices.get("price_max"),
                    "currency": "EUR",
                    "url": url,
                })

    except httpx.HTTPStatusError as exc:
        errors.append(f"Search API error: {exc.response.status_code}")
    except httpx.RequestError as exc:
        errors.append(f"Request error: {exc}")

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
