"""
validate_geocode.py

Runs the synthetic dataset through Precisely address verification/
standardization and geocoding, producing data/02_validated.csv with a
before/after comparison.

NOTE: Confirm exact endpoint paths, auth flow, and response field names
against your live Precisely Data Integrity Suite trial docs at
https://developer.precisely.com/ once you're signed up — free trials
sometimes version their endpoints, so treat PRECISELY_VERIFY_URL and
PRECISELY_GEOCODE_URL below as the two things most likely to need a
one-line edit.

Adds these columns to each record:
    verified_street, verified_city, verified_state, verified_zip
    match_status        ("matched" | "partial" | "no_match")
    latitude, longitude
    precisely_id         persistent location identifier, if returned

Usage:
    python src/validate_geocode.py --sample 10     # cheap smoke test
    python src/validate_geocode.py                 # full dataset
"""

import argparse
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

# Confirm against current docs — placeholders based on the Data Integrity
# Suite REST pattern at time of writing.
PRECISELY_AUTH_URL = "https://api.precisely.com/oauth/token"
PRECISELY_VERIFY_URL = "https://api.precisely.com/address/v1/verify"
PRECISELY_GEOCODE_URL = "https://api.precisely.com/geocode/v1/lookup"


def get_access_token() -> str:
    resp = requests.post(
        PRECISELY_AUTH_URL,
        data={"grant_type": "client_credentials"},
        auth=(API_KEY, API_SECRET),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def verify_address(token: str, record: dict) -> dict:
    payload = {
        "addressLines": [
            f"{record['street_number']} {record['street_name']} {record['unit']}".strip(),
        ],
        "city": record["city"],
        "state": record["state"],
        "zip": record["zip"],
        "country": "US",
    }
    resp = requests.post(
        PRECISELY_VERIFY_URL,
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if resp.status_code != 200:
        return {"match_status": "no_match"}
    data = resp.json()
    return {
        "verified_street": data.get("formattedStreet", ""),
        "verified_city": data.get("city", ""),
        "verified_state": data.get("state", ""),
        "verified_zip": data.get("zip", ""),
        "match_status": data.get("matchStatus", "no_match"),
        "precisely_id": data.get("preciselyId", ""),
    }


def geocode_address(token: str, record: dict) -> dict:
    if record.get("match_status") == "no_match":
        return {"latitude": "", "longitude": ""}
    payload = {
        "address": f"{record.get('verified_street', '')}, "
        f"{record.get('verified_city', '')}, "
        f"{record.get('verified_state', '')} {record.get('verified_zip', '')}"
    }
    resp = requests.post(
        PRECISELY_GEOCODE_URL,
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if resp.status_code != 200:
        return {"latitude": "", "longitude": ""}
    data = resp.json()
    return {"latitude": data.get("lat", ""), "longitude": data.get("lon", "")}


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

    token = get_access_token()

    results = []
    match_counts = {"matched": 0, "partial": 0, "no_match": 0}
    for record in tqdm(records, desc="Verifying + geocoding"):
        verify_result = verify_address(token, record)
        record.update(verify_result)
        geo_result = geocode_address(token, record)
        record.update(geo_result)
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
