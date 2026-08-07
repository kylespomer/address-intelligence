"""
enrich.py

Appends underwriting-relevant location enrichment attributes to validated
records, producing data/03_enriched.csv.

Uses the Data Integrity Suite "Spatial Features" (OGC API - Features)
enrichment endpoints, confirmed against the actual OpenAPI spec (Precisely
trial account, 2026-08-04):
    GET {base}/v1/ogcapi/enrich/collections/{collectionId}/items
        ?bbox=minLon,minLat,maxLon,maxLat&limit=N

Collections used:
    risks/flood_risk           polygon dataset -> flood zone for the point
    properties/property_attributes  polygon dataset -> property attributes
    risks/fire_stations         point dataset -> nearest station, distance
                                 computed client-side (no "nearest" query
                                 exists on this API)

Field names confirmed against a real --sample 2 run (2026-08-04):
    risks/flood_risk.floodzone            FEMA flood zone code (e.g. "X")
    properties/property_attributes.prop_lu_desc   land use description
    risks/fire_stations.name / latitude / longitude

For flood_risk / property_attributes (polygon data), a small bbox around the
point is used as a stand-in for a true point-in-polygon query (exact CQL
spatial-filter syntax wasn't confirmed) -- the first feature returned is
taken as "the polygon containing this point." This is a reasonable
approximation for a small enough bbox but not a guaranteed point-in-polygon
match; revisit if results look wrong.

Usage:
    python src/enrich.py --sample 10
    python src/enrich.py
"""

import argparse
import base64
import csv
import math
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
ITEMS_URL = BASE_URL + "/v1/ogcapi/enrich/collections/{collection}/items"

_token = base64.b64encode(f"{API_KEY}:{API_SECRET}".encode()).decode()
AUTH_HEADERS = {
    "Authorization": f"Apikey {_token}",
    "Accept": "application/geo+json",
}

FLOOD_COLLECTION = "risks/flood_risk"
PROPERTY_COLLECTION = "properties/property_attributes"
FIRESTATION_COLLECTION = "risks/fire_stations"

# Small bbox (~100m) for polygon lookups -- point should fall inside it.
POLYGON_BBOX_DEG = 0.0009
# Progressive search radii (degrees) for nearest fire station.
FIRESTATION_BBOX_STEPS_DEG = [0.02, 0.1, 0.4]  # roughly 1mi, 7mi, 28mi


def _bbox(lat: float, lon: float, half_size_deg: float) -> str:
    return (
        f"{lon - half_size_deg},{lat - half_size_deg},"
        f"{lon + half_size_deg},{lat + half_size_deg}"
    )


def _get_items(collection: str, bbox: str, limit: int) -> list:
    try:
        resp = requests.get(
            ITEMS_URL.format(collection=collection),
            params={"bbox": bbox, "limit": limit},
            headers=AUTH_HEADERS,
            timeout=15,
        )
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []
    return resp.json().get("features", [])


def _haversine_miles(lat1, lon1, lat2, lon2) -> float:
    r_miles = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r_miles * math.asin(math.sqrt(a))


def lookup_polygon_collection(collection: str, lat: float, lon: float) -> dict | None:
    bbox = _bbox(lat, lon, POLYGON_BBOX_DEG)
    features = _get_items(collection, bbox, limit=5)
    if not features:
        return None
    return features[0].get("properties", {})


def lookup_nearest_fire_station(lat: float, lon: float) -> tuple[dict | None, float | None]:
    for radius in FIRESTATION_BBOX_STEPS_DEG:
        features = _get_items(FIRESTATION_COLLECTION, _bbox(lat, lon, radius), limit=25)
        if features:
            best_props, best_dist = None, None
            for feature in features:
                coords = feature.get("geometry", {}).get("coordinates")
                if not coords or len(coords) != 2:
                    continue
                station_lon, station_lat = coords
                dist = _haversine_miles(lat, lon, station_lat, station_lon)
                if best_dist is None or dist < best_dist:
                    best_dist, best_props = dist, feature.get("properties", {})
            if best_props is not None:
                return best_props, round(best_dist, 2)
    return None, None


def enrich_record(record: dict) -> dict:
    empty = {
        "flood_risk_zone": "",
        "property_type": "",
        "fire_station_name": "",
        "fire_station_distance_mi": "",
    }
    if not record.get("latitude") or not record.get("longitude"):
        return empty

    lat, lon = float(record["latitude"]), float(record["longitude"])

    flood_props = lookup_polygon_collection(FLOOD_COLLECTION, lat, lon) or {}
    property_props = lookup_polygon_collection(PROPERTY_COLLECTION, lat, lon) or {}
    station_props, station_dist = lookup_nearest_fire_station(lat, lon)
    station_props = station_props or {}

    return {
        "flood_risk_zone": flood_props.get("floodzone", ""),
        "property_type": property_props.get("prop_lu_desc", ""),
        "fire_station_name": station_props.get("name", ""),
        "fire_station_distance_mi": station_dist if station_dist is not None else "",
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

    results = []
    for record in tqdm(records, desc="Enriching"):
        record.update(enrich_record(record))
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
    print(f"Got flood_risk data for: {enriched_count}/{len(results)}")


if __name__ == "__main__":
    main()
