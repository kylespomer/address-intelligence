"""
validate_geocode.py

Runs the synthetic dataset through Precisely address verification/
standardization and geocoding, producing data/02_validated.csv with a
before/after comparison.

Endpoints confirmed against the actual Data Integrity Suite "Geo Addressing"
OpenAPI spec (Precisely trial account, 2026-08-04) — base host is
api.cloud.precisely.com, not api.precisely.com, and auth is a plain API-key
header, not an OAuth token exchange.

Adds these columns to each record:
    verified_street, verified_city, verified_state, verified_zip
    verify_score         Precisely's 0-100 match confidence (from /v1/verify)
    match_status         our own bucketing of verify_score: "matched" (>=90),
                          "partial" (>0), "no_match" (verify call failed/no result)
    latitude, longitude
    precisely_id          PB_KEY from customFields, if returned

Usage:
    python src/validate_geocode.py --sample 10     # cheap smoke test
    python src/validate_geocode.py                 # full dataset
"""

import argparse
import base64
import csv
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

API_KEY = os.getenv("PRECISELY_API_KEY")
API_SECRET = os.getenv("PRECISELY_API_SECRET")

BASE_URL = os.getenv("PRECISELY_BASE_URL", "https://api.cloud.precisely.com")
VERIFY_URL = f"{BASE_URL}/v1/verify"
GEOCODE_URL = f"{BASE_URL}/v1/geocode"

_token = base64.b64encode(f"{API_KEY}:{API_SECRET}".encode()).decode()
AUTH_HEADERS = {
    "Authorization": f"Apikey {_token}",
    "Content-Type": "application/json",
}


def _address_payload(record: dict) -> dict:
    street_line = f"{record['street_number']} {record['street_name']} {record['unit']}".strip()
    return {
        "addressId": "1",
        "addressLines": [street_line],
        "country": "USA",
        "city": record["city"],
        "admin1": record["state"],
        "postalCode": record["zip"],
    }


def verify_address(record: dict) -> dict:
    payload = {
        "preferences": {"maxResults": 1, "returnAllInfo": True},
        "addresses": [_address_payload(record)],
    }
    resp = requests.post(VERIFY_URL, json=payload, headers=AUTH_HEADERS, timeout=15)
    if resp.status_code not in (200, 206):
        return {"match_status": "no_match", "verify_score": ""}

    responses = resp.json().get("responses", [])
    if not responses or responses[0].get("status") != "OK" or not responses[0].get("results"):
        return {"match_status": "no_match", "verify_score": ""}

    result = responses[0]["results"][0]
    score = result.get("score", 0)
    addr = result.get("address", {})
    match_status = "matched" if score >= 90 else "partial" if score > 0 else "no_match"

    return {
        "verified_street": addr.get("formattedStreetAddress", ""),
        "verified_city": addr.get("city", {}).get("shortName", ""),
        "verified_state": addr.get("admin1", {}).get("shortName", ""),
        "verified_zip": addr.get("postalCode", ""),
        "verify_score": score,
        "match_status": match_status,
        "precisely_id": result.get("customFields", {}).get("PB_KEY", ""),
    }


def geocode_address(record: dict) -> dict:
    if record.get("match_status") == "no_match":
        return {"latitude": "", "longitude": ""}

    payload = {
        "preferences": {"maxResults": 1, "returnAllInfo": True},
        "addresses": [_address_payload(record)],
    }
    resp = requests.post(GEOCODE_URL, json=payload, headers=AUTH_HEADERS, timeout=15)
    if resp.status_code not in (200, 206):
        return {"latitude": "", "longitude": ""}

    responses = resp.json().get("responses", [])
    if not responses or responses[0].get("status") != "OK" or not responses[0].get("results"):
        return {"latitude": "", "longitude": ""}

    coords = (
        responses[0]["results"][0]
        .get("location", {})
        .get("feature", {})
        .get("geometry", {})
        .get("coordinates", [])
    )
    if len(coords) != 2:
        return {"latitude": "", "longitude": ""}
    # GeoJSON order is [longitude, latitude]
    longitude, latitude = coords
    return {"latitude": latitude, "longitude": longitude}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", default="data/01_raw_synthetic.csv")
    parser.add_argument("--out", dest="out_path", default="data/02_validated.csv")
    parser.add_argument("--sample", type=int, default=None, help="only process first N rows")
    args = parser.parse_args()

    with open(args.in_path, newline="") as f:
        records = list(csv.DictReader(f))
    if args.sample:
        records = records[: args.sample]

    results = []
    match_counts = {"matched": 0, "partial": 0, "no_match": 0}
    for record in tqdm(records, desc="Verifying + geocoding"):
        record.update(verify_address(record))
        record.update(geocode_address(record))
        results.append(record)
        match_counts[record.get("match_status", "no_match")] = (
            match_counts.get(record.get("match_status", "no_match"), 0) + 1
        )
        time.sleep(0.05)  # gentle on rate limits

    out_path = Path(args.out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    total = len(results)
    print(f"\nWrote {total} records to {out_path}")
    print("Match status breakdown:")
    for status, count in match_counts.items():
        print(f"  {status:10s} {count:4d}  ({100 * count / total:.1f}%)")


if __name__ == "__main__":
    main()
