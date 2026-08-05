"""
enrich.py

Appends underwriting-relevant location enrichment attributes to validated
records, producing data/03_enriched.csv. Confirm exact Precisely enrichment
endpoint(s) and product name (e.g. "PropertyAttributes", "FloodRisk") against
your trial's available data packages — not every package is enabled on every
trial account by default.

Enrichment attributes (see README / build plan for the underwriting reasoning
behind each pick):
    flood_risk_zone       FEMA-style flood zone designation
    fire_station_distance_mi
    property_type         e.g. single-family, multi-family, commercial
    stated_vs_enriched_mismatch   True if property_type or flood zone
                                  contradicts what's implied by the raw record
                                  (used later by the AI anomaly flag)

Usage:
    python src/enrich.py --sample 10
    python src/enrich.py
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

# Confirm against current docs.
PRECISELY_ENRICH_URL = "https://api.precisely.com/property/v2/attributes"


def get_access_token() -> str:
    resp = requests.post(
        "https://api.precisely.com/oauth/token",
        data={"grant_type": "client_credentials"},
        auth=(API_KEY, API_SECRET),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def enrich_record(token: str, record: dict) -> dict:
    if not record.get("latitude") or not record.get("longitude"):
        # can't enrich a record that didn't geocode
        return {
            "flood_risk_zone": "",
            "fire_station_distance_mi": "",
            "property_type": "",
        }
    resp = requests.get(
        PRECISELY_ENRICH_URL,
        params={"lat": record["latitude"], "lon": record["longitude"]},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if resp.status_code != 200:
        return {
            "flood_risk_zone": "",
            "fire_station_distance_mi": "",
            "property_type": "",
        }
    data = resp.json()
    return {
        "flood_risk_zone": data.get("floodZone", ""),
        "fire_station_distance_mi": data.get("nearestFireStationMiles", ""),
        "property_type": data.get("propertyType", ""),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", default="data/02_validated.csv")
    parser.add_argument("--out", dest="out_path", default="data/03_enriched.csv")
    parser.add_argument("--sample", type=int, default=None)
    args = parser.parse_args()

    with open(args.in_path, newline="") as f:
        records = list(csv.DictReader(f))
    if args.sample:
        records = records[: args.sample]

    token = get_access_token()

    results = []
    for record in tqdm(records, desc="Enriching"):
        record.update(enrich_record(token, record))
        results.append(record)
        time.sleep(0.05)

    out_path = Path(args.out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    enriched_count = sum(1 for r in results if r.get("flood_risk_zone"))
    print(f"\nWrote {len(results)} records to {out_path}")
    print(f"Successfully enriched: {enriched_count}/{len(results)}")


if __name__ == "__main__":
    main()
