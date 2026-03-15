import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

FLIGHTS = [
    ("EK", "78", None),            # NCE→DXB — single leg
    ("EK", "705", None),           # DXB→SEZ — single leg
    ("EK", "708", ("SEZ", "DXB")), # Multi-leg: we want SEZ→DXB
    ("EK", "77", None),            # DXB→NCE — single leg
]

DATA_FILE = os.environ.get(
    "DATA_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "flights.json"),
)

USER_AGENT = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}


def _parse_next_data(html):
    """Extract __NEXT_DATA__ JSON from FlightStats HTML."""
    marker = "__NEXT_DATA__"
    idx = html.find(marker)
    if idx == -1:
        raise ValueError("Could not find __NEXT_DATA__ in page")
    brace_start = html.index("{", idx)
    decoder = json.JSONDecoder()
    next_data, _ = decoder.raw_decode(html, brace_start)
    return next_data


def _extract_flight_entry(next_data, carrier, flight_num, date_str):
    """Extract a flight entry dict from parsed __NEXT_DATA__."""
    flight = (
        next_data.get("props", {})
        .get("initialState", {})
        .get("flightTracker", {})
        .get("flight", {})
    )
    if not flight:
        return None

    schedule = flight.get("schedule", {})
    dep_airport = flight.get("departureAirport", {})
    arr_airport = flight.get("arrivalAirport", {})
    flight_note = flight.get("flightNote", {})

    # Departure times
    dep_times = dep_airport.get("times", {})
    dep_sched = dep_times.get("scheduled", {}).get("time24", "")
    dep_actual = dep_times.get("estimatedActual", {}).get("time24", "")

    # Arrival times
    arr_times = arr_airport.get("times", {})
    arr_sched = arr_times.get("scheduled", {}).get("time24", "")
    arr_actual = arr_times.get("estimatedActual", {}).get("time24", "")

    # Timezone from airport times
    dep_tz = dep_times.get("scheduled", {}).get("timezone", "")
    arr_tz = arr_times.get("scheduled", {}).get("timezone", "")

    # Calculate delays from schedule ISO timestamps
    dep_delay = _calc_delay(
        schedule.get("scheduledDeparture", ""),
        schedule.get("estimatedActualDeparture", ""),
    )
    arr_delay = _calc_delay(
        schedule.get("scheduledArrival", ""),
        schedule.get("estimatedActualArrival", ""),
    )

    # Status — clean up FlightStats phase names
    PHASE_MAP = {
        "currentdatepreflight": "Scheduled",
        "currentdatepredeparture": "Scheduled",
        "preflight": "Scheduled",
        "predeparture": "Scheduled",
        "departedgate": "Departed",
        "departedrunway": "Departed",
        "cruising": "En Route",
        "en-route": "En Route",
        "approaching": "En Route",
        "landing": "Landing",
        "landed": "Landed",
        "arrived": "Landed",
    }
    status = ""
    if flight_note.get("canceled"):
        status = "Cancelled"
    elif flight_note.get("landed"):
        status = "Landed"
    else:
        phase = flight_note.get("phase", "")
        state = flight.get("flightState", "")
        raw = phase or state
        status = PHASE_MAP.get(raw.lower(), raw.capitalize() if raw else "Unknown")

    return {
        "date": date_str,
        "flight": f"{carrier}{flight_num}",
        "origin": dep_airport.get("iata", ""),
        "destination": arr_airport.get("iata", ""),
        "scheduled_departure": dep_sched,
        "actual_departure": dep_actual,
        "dep_timezone": dep_tz,
        "scheduled_arrival": arr_sched,
        "actual_arrival": arr_actual,
        "arr_timezone": arr_tz,
        "departure_delay_min": dep_delay,
        "arrival_delay_min": arr_delay,
        "status": status,
    }


def scrape_flightstats(carrier, flight_num, date_str, desired_route=None):
    """Scrape flight data from FlightStats __NEXT_DATA__ JSON.

    If desired_route is a (origin, destination) tuple and the default leg
    doesn't match, search otherDays for the correct leg's flightId and
    re-fetch.
    """
    year, month, day = date_str.split("-")
    url = (
        f"https://www.flightstats.com/v2/flight-tracker/"
        f"{carrier}/{flight_num}"
        f"?year={year}&month={int(month)}&date={int(day)}"
    )
    resp = requests.get(url, headers=USER_AGENT, timeout=30)
    resp.raise_for_status()

    next_data = _parse_next_data(resp.text)
    entry = _extract_flight_entry(next_data, carrier, flight_num, date_str)
    if entry is None:
        return None

    # If desired_route specified and current leg doesn't match, find the right leg
    if desired_route:
        desired_origin, desired_dest = desired_route
        if entry["origin"] != desired_origin or entry["destination"] != desired_dest:
            other_days = (
                next_data.get("props", {})
                .get("initialState", {})
                .get("flightTracker", {})
                .get("otherDays", [])
            )
            flight_id = None
            for day_group in other_days:
                items = day_group if isinstance(day_group, list) else [day_group]
                for item in items:
                    dep_iata = item.get("departureAirport", {}).get("iata", "")
                    arr_iata = item.get("arrivalAirport", {}).get("iata", "")
                    if dep_iata == desired_origin and arr_iata == desired_dest:
                        url_str = item.get("url", "")
                        if "flightId=" in url_str:
                            flight_id = url_str.split("flightId=")[-1].split("&")[0]
                            break
                if flight_id:
                    break

            if flight_id:
                url2 = url + f"&flightId={flight_id}"
                print(f"    Re-fetching with flightId={flight_id} for {desired_origin}->{desired_dest}")
                resp2 = requests.get(url2, headers=USER_AGENT, timeout=30)
                resp2.raise_for_status()
                next_data2 = _parse_next_data(resp2.text)
                entry2 = _extract_flight_entry(next_data2, carrier, flight_num, date_str)
                if entry2 is not None:
                    entry = entry2
            else:
                print(f"    Warning: could not find flightId for {desired_origin}->{desired_dest}")

    return entry


def _calc_delay(scheduled_iso, actual_iso):
    """Calculate delay in minutes between two ISO timestamps."""
    if not scheduled_iso or not actual_iso:
        return None
    try:
        # Timestamps like "2026-03-14T07:35:00.000" (no timezone)
        fmt = "%Y-%m-%dT%H:%M:%S.%f"
        s = datetime.strptime(scheduled_iso[:23], fmt)
        a = datetime.strptime(actual_iso[:23], fmt)
        return int((a - s).total_seconds() / 60)
    except (ValueError, TypeError):
        return None


def is_complete(entry):
    """Flight is complete if it has landed with actual arrival time."""
    if not entry.get("actual_arrival"):
        return False
    return entry.get("status", "").lower() == "landed"


def load_existing_data():
    """Load existing flights.json data."""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                raw = json.load(f)
                if isinstance(raw, list):
                    return raw  # vecchio formato
                return raw.get("flights", [])
            except json.JSONDecodeError:
                return []
    return []


def save_data(data):
    """Save data to flights.json with last_check metadata."""
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    output = {
        "last_check": datetime.now(timezone.utc).isoformat(),
        "flights": data,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def main():
    today = date.today()
    past = [today - timedelta(days=i) for i in range(3)]      # oggi, ieri, altroieri
    future = [today + timedelta(days=i) for i in range(1, 4)]  # domani, dopodomani, +3
    dates = past + future

    existing = load_existing_data()

    # Index existing entries by (date, flight) for quick lookup
    existing_map = {}
    for i, entry in enumerate(existing):
        key = (entry["date"], entry["flight"])
        existing_map[key] = i

    updated = 0
    added = 0

    for d in dates:
        date_str = d.isoformat()
        for carrier, num, route in FLIGHTS:
            flight_code = f"{carrier}{num}"
            key = (date_str, flight_code)

            # Skip if already complete
            if key in existing_map and is_complete(existing[existing_map[key]]):
                print(f"  {flight_code} on {date_str}: complete, skipping")
                continue

            print(f"  Fetching {flight_code} on {date_str}...")
            try:
                entry = scrape_flightstats(carrier, num, date_str, desired_route=route)
                if entry is None:
                    print(f"    No data found")
                    continue

                if key in existing_map:
                    existing[existing_map[key]] = entry
                    updated += 1
                    print(f"    Updated (status: {entry['status']})")
                else:
                    existing.append(entry)
                    existing_map[key] = len(existing) - 1
                    added += 1
                    print(f"    Added (status: {entry['status']})")

                # Be polite to FlightStats
                time.sleep(2)

            except requests.exceptions.HTTPError as e:
                print(f"    HTTP error: {e}")
            except Exception as e:
                print(f"    Error: {e}")

    if added or updated:
        # Sort by date descending, then flight
        existing.sort(key=lambda x: (x["date"], x["flight"]), reverse=True)
        save_data(existing)
        print(f"\nDone: {added} added, {updated} updated. Total: {len(existing)}")
    else:
        print("\nNo changes.")


if __name__ == "__main__":
    main()
