"""Check Ticketmaster for WWE single-day tickets in Italy (31 May 2026)."""

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


def get_event_detail(client: httpx.Client, api_key: str, event_id: str) -> dict:
    resp = client.get(f"{BASE_URL}/events/{event_id}.json", params={"apikey": api_key})
    resp.raise_for_status()
    return resp.json()


def extract_prices(event: dict) -> tuple[float | None, float | None, str]:
    price_ranges = event.get("priceRanges", [])
    if not price_ranges:
        return None, None, "EUR"
    mins = [p["min"] for p in price_ranges if "min" in p]
    maxs = [p["max"] for p in price_ranges if "max" in p]
    currency = price_ranges[0].get("currency", "EUR")
    return (min(mins) if mins else None, max(maxs) if maxs else None, currency)


def extract_date(event: dict) -> str:
    dates = event.get("dates", {}).get("start", {})
    return dates.get("localDate", "")


def extract_url(event: dict) -> str:
    return event.get("url", "")


def main():
    api_key = os.environ.get("TICKETMASTER_API_KEY", "")
    if not api_key:
        print("ERROR: TICKETMASTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    status = load_status()
    now = datetime.now(timezone.utc).isoformat()
    errors: list[str] = []

    try:
        with httpx.Client(timeout=30) as client:
            raw_events = search_events(client, api_key)

            events = []
            for ev in raw_events:
                event_id = ev["id"]
                try:
                    detail = get_event_detail(client, api_key, event_id)
                except httpx.HTTPStatusError as exc:
                    errors.append(f"Detail fetch failed for {event_id}: {exc.response.status_code}")
                    detail = ev

                name = detail.get("name", "")
                price_min, price_max, currency = extract_prices(detail)
                event_date = extract_date(detail)
                single_day = not is_combo(name)

                events.append({
                    "id": event_id,
                    "name": name,
                    "date": event_date,
                    "is_single_day": single_day,
                    "price_min": price_min,
                    "price_max": price_max,
                    "currency": currency,
                    "url": extract_url(detail),
                })

                if price_min is not None:
                    status["price_history"].append({
                        "timestamp": now,
                        "event_id": event_id,
                        "price_min": price_min,
                    })

    except httpx.HTTPStatusError as exc:
        errors.append(f"Search API error: {exc.response.status_code}")
    except httpx.RequestError as exc:
        errors.append(f"Request error: {exc}")

    # Trim history
    status["price_history"] = status["price_history"][-MAX_HISTORY:]

    # Check alert condition: any single-day event under threshold
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

    print(f"Check #{status['checks_count']} done. Events found: {len(events)}. Alert: {alert}")
    if errors:
        print(f"Errors: {errors}", file=sys.stderr)


if __name__ == "__main__":
    main()
