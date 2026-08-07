"""
fetch_real_addresses.py

One-time reference-data build: pulls real US street addresses (house number,
street, city, state, zip) from Precisely's own `properties/property_attributes`
enrichment collection (the same OGC Features API used in enrich.py), for a
curated list of real cities. Writes data/reference/real_addresses.csv, which
generate_dataset.py samples from as its base "real" address before injecting
synthetic errors on top.

Switched from OpenStreetMap/Overpass to Precisely's own data after the public
Overpass instance rate-limited/blocked us mid-fetch (2026-08-06) -- this is
also a better fit since it reuses credentials and infrastructure already
proven against the real trial account, and ties the synthetic dataset's
origin back to Precisely's own reference data.

Not part of the regular pipeline re-run and not hit at dataset-generation
time -- this is reference data, checked into the repo, so generate_dataset.py
never needs network access. Uses a modest number of Precisely API calls
(one bbox query per city below) against the trial's credit budget.

Usage:
    python src/fetch_real_addresses.py
"""

import argparse
import base64
import csv
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("PRECISELY_API_KEY")
API_SECRET = os.getenv("PRECISELY_API_SECRET")
BASE_URL = os.getenv("PRECISELY_BASE_URL", "https://api.cloud.precisely.com")
ITEMS_URL = BASE_URL + "/v1/ogcapi/enrich/collections/properties/property_attributes/items"

_token = base64.b64encode(f"{API_KEY}:{API_SECRET}".encode()).decode()
AUTH_HEADERS = {"Authorization": f"Apikey {_token}", "Accept": "application/geo+json"}

# (city, state, lat, lon) -- approximate city-center coordinates, used only
# to build a bbox for pulling nearby real parcels. Precision doesn't matter
# much here since we just want a diverse, real sample near downtown.
CITIES = [
    ("Orlando", "FL", 28.5383, -81.3792), ("Lake Hopatcong", "NJ", 40.9370, -74.6555),
    ("Jefferson", "NJ", 41.0004, -74.5688), ("Clearwater", "FL", 27.9659, -82.8001),
    ("Jacksonville", "FL", 30.3322, -81.6557), ("Miami", "FL", 25.7617, -80.1918),
    ("Plant City", "FL", 28.0186, -82.1131),
    ("Des Moines", "IA", 41.5868, -93.6250), ("West Des Moines", "IA", 41.5772, -93.7113),
    ("Ankeny", "IA", 41.7317, -93.6001),
    ("Denver", "CO", 39.7392, -104.9903), ("Boulder", "CO", 40.0150, -105.2705),
    ("Golden", "CO", 39.7555, -105.2211),
    ("Austin", "TX", 30.2672, -97.7431), ("Round Rock", "TX", 30.5083, -97.6789),
    ("Houston", "TX", 29.7604, -95.3698), ("Dallas", "TX", 32.7767, -96.7970),
    ("San Antonio", "TX", 29.4241, -98.4936),
    ("Allentown", "PA", 40.6023, -75.4714), ("Tucson", "AZ", 32.2226, -110.9747),
    ("Mesa", "AZ", 33.4152, -111.8315),
    ("Seattle", "WA", 47.6062, -122.3321), ("Tacoma", "WA", 47.2529, -122.4443),
    ("Spokane", "WA", 47.6588, -117.4260),
    ("Portland", "OR", 45.5152, -122.6784), ("Eugene", "OR", 44.0521, -123.0868),
    ("Salem", "OR", 44.9429, -123.0351),
    ("Boston", "MA", 42.3601, -71.0589), ("Worcester", "MA", 42.2626, -71.8023),
    ("Springfield", "MA", 42.1015, -72.5898),
    ("New York", "NY", 40.7128, -74.0060), ("Buffalo", "NY", 42.8864, -78.8784),
    ("Ithaca", "NY", 42.4440, -76.5019),
    ("Chicago", "IL", 41.8781, -87.6298), ("Springfield", "IL", 39.7817, -89.6501),
    ("Peoria", "IL", 40.6936, -89.5890),
    ("Columbus", "OH", 39.9612, -82.9988), ("Cleveland", "OH", 41.4993, -81.6944),
    ("Cincinnati", "OH", 39.1031, -84.5120),
    ("Atlanta", "GA", 33.7490, -84.3880), ("Savannah", "GA", 32.0809, -81.0912),
    ("Augusta", "GA", 33.4735, -82.0105),
    ("Charlotte", "NC", 35.2271, -80.8431), ("Raleigh", "NC", 35.7796, -78.6382),
    ("Greensboro", "NC", 36.0726, -79.7920),
    ("Nashville", "TN", 36.1627, -86.7816), ("Chattanooga", "TN", 35.0456, -85.3097),
    ("Knoxville", "TN", 35.9606, -83.9207),
    ("Las Vegas", "NV", 36.1699, -115.1398), ("Reno", "NV", 39.5296, -119.8138),
    ("Henderson", "NV", 36.0395, -114.9817),
    ("Anaheim", "CA", 33.8366, -117.9143), ("Long Beach", "CA", 33.7701, -118.1937),
    ("Ontario", "CA", 34.0633, -117.6509),
    ("Minneapolis", "MN", 44.9778, -93.2650), ("St. Paul", "MN", 44.9537, -93.0900),
    ("Duluth", "MN", 46.7867, -92.1005),
    ("Kansas City", "MO", 39.0997, -94.5786), ("St. Louis", "MO", 38.6270, -90.1994),
    ("Springfield", "MO", 37.2090, -93.2923),
    ("Salt Lake City", "UT", 40.7608, -111.8910), ("Provo", "UT", 40.2338, -111.6585),
    ("Ogden", "UT", 41.2230, -111.9738),
]

BBOX_HALF_DEG = 0.03  # roughly 2-3 miles around each city center
PER_CITY_LIMIT = 20


def fetch_city(city: str, state: str, lat: float, lon: float) -> list:
    bbox = f"{lon - BBOX_HALF_DEG},{lat - BBOX_HALF_DEG},{lon + BBOX_HALF_DEG},{lat + BBOX_HALF_DEG}"
    try:
        # (connect_timeout, read_timeout) -- bounds worst case per request to 18s
        resp = requests.get(
            ITEMS_URL, params={"bbox": bbox, "limit": PER_CITY_LIMIT}, headers=AUTH_HEADERS, timeout=(5, 13)
        )
    except requests.RequestException as e:
        print(f"  (skipped {city}, {state}: {e.__class__.__name__})", flush=True)
        return []
    if resp.status_code != 200:
        print(f"  (skipped {city}, {state}: HTTP {resp.status_code})", flush=True)
        return []

    rows = []
    for feature in resp.json().get("features", []):
        props = feature.get("properties", {})
        number = props.get("add_number")
        street = props.get("streetname")
        strtype = props.get("strtype") or ""
        predir = props.get("predir") or ""
        postdir = props.get("postdir") or ""
        zip_code = props.get("zipcode")
        record_city = props.get("city") or city
        if not number or not street or not zip_code:
            continue
        full_street = " ".join(part for part in [predir, street, strtype, postdir] if part)
        rows.append({
            "street_number": number,
            "street_name": full_street,
            "city": record_city.title() if record_city.isupper() else record_city,
            "state": state,
            "zip": str(zip_code),
        })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=4.0, help="seconds between requests")
    parser.add_argument(
        "--resume", action="store_true",
        help="keep existing data/reference/real_addresses.csv and skip cities already fetched",
    )
    args = parser.parse_args()

    out_path = Path("data/reference/real_addresses.csv")
    fieldnames = ["street_number", "street_name", "city", "state", "zip"]

    all_rows = []
    seen = set()
    done_cities = set()
    if args.resume and out_path.exists():
        with out_path.open(newline="") as f:
            for r in csv.DictReader(f):
                all_rows.append(r)
                seen.add((r["street_number"], r["street_name"], r["city"], r["zip"]))
                done_cities.add((r["city"], r["state"]))
        print(f"Resuming: {len(all_rows)} addresses already have, {len(done_cities)} cities done.")

    # write incrementally so a crash/interrupt never loses prior progress
    out_path.parent.mkdir(parents=True, exist_ok=True)
    f = out_path.open("w", newline="")
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(all_rows)
    f.flush()

    for i, (city, state, lat, lon) in enumerate(CITIES, 1):
        if (city, state) in done_cities:
            continue
        print(f"[{i}/{len(CITIES)}] {city}, {state}...", end=" ", flush=True)
        rows = fetch_city(city, state, lat, lon)
        added = 0
        for r in rows:
            key = (r["street_number"], r["street_name"], r["city"], r["zip"])
            if key in seen:
                continue
            seen.add(key)
            all_rows.append(r)
            writer.writerow(r)
            added += 1
        f.flush()
        print(f"{added} addresses", flush=True)
        time.sleep(args.delay)

    f.close()
    print(f"\nWrote {len(all_rows)} real addresses to {out_path}")


if __name__ == "__main__":
    main()
