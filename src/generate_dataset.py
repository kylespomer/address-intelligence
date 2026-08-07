"""
generate_dataset.py

Builds a synthetic policyholder dataset with realistic address noise for the
insurance-carrier scenario. Writes data/01_raw_synthetic.csv.

Every "base" address is a real one, sampled from
data/reference/real_addresses.csv (built by src/fetch_real_addresses.py from
Precisely's own property_attributes data). Errors are then injected on top
of that real base, so "clean" records genuinely verify/geocode against the
real Precisely API, and each error type is a realistic corruption of a real
address rather than a corruption of fake data.

Error methodology (documented per assignment requirements):
  - 15% misspellings / transposed characters in the (real) street name
  - 15% missing apartment/suite numbers (dropped even when one would have
    been present)
  - 10% ZIP/city mismatches (city swapped for a different real same-state
    city from the pool, ZIP left unchanged so it no longer matches)
  - 10% outdated/non-existent addresses (real house number bumped out of
    any plausible real range)
  - Remaining ~50% clean records, used as the "before" baseline

Every row gets an `injected_error` column recording which error type (if any)
was applied, so match-rate improvement can be measured against ground truth
later rather than eyeballed.

Every record also gets a `stated_property_type` -- what the policyholder
declared on their application, chosen independently of the real address'
actual property type. This is deliberately NOT derived from the real
address, so it sometimes coincidentally matches what enrichment later
reveals and sometimes doesn't -- that gap is what ai_layer.py's anomaly
flag is built on (self-reported vs. enriched-data mismatch, a real
underwriting/fraud concern), computed deterministically from this real
field rather than left for an LLM to guess at with no ground truth.

Usage:
    python src/fetch_real_addresses.py          # one-time, builds the real-address pool
    python src/generate_dataset.py --n 350 --seed 42
"""

import argparse
import csv
import random
from collections import defaultdict
from pathlib import Path

from faker import Faker

ERROR_TYPES = [
    "misspelling",
    "missing_unit",
    "zip_city_mismatch",
    "outdated_address",
    None,  # clean record
]
# Weights matching the methodology above: 15/15/10/10/50
ERROR_WEIGHTS = [15, 15, 10, 10, 50]

# What a policyholder might declare on an application. Rough mix for a
# residential-leaning book of business; kept in sync with the categories
# ai_layer.py normalizes real enrichment property types into.
STATED_PROPERTY_TYPES = [
    "Single Family Residence",
    "Multi-Family / Rental",
    "Condominium / Townhome",
    "Vacant Land",
    "Commercial",
]
STATED_PROPERTY_TYPE_WEIGHTS = [55, 15, 10, 10, 10]

REAL_ADDRESSES_PATH = Path("data/reference/real_addresses.csv")


def load_real_addresses(path: Path) -> list:
    if not path.exists():
        raise SystemExit(
            f"{path} not found. Run `python src/fetch_real_addresses.py` first "
            "to build the real-address reference pool."
        )
    with path.open(newline="") as f:
        pool = list(csv.DictReader(f))
    if not pool:
        raise SystemExit(f"{path} is empty. Re-run src/fetch_real_addresses.py.")
    return pool


def typo_street(street: str) -> str:
    """Introduce a transposed-character or common-word misspelling."""
    swaps = {
        "Street": "Steret",
        "Avenue": "Aveune",
        "Boulevard": "Boulevad",
        "Drive": "Drve",
        "Lane": "Lnae",
        "Court": "Ct.",
        "Road": "Rd.",
    }
    for full, typo in swaps.items():
        if full in street:
            return street.replace(full, typo)
    # fallback: transpose two adjacent characters in the street name
    if len(street) > 4:
        i = random.randint(1, len(street) - 3)
        chars = list(street)
        chars[i], chars[i + 1] = chars[i + 1], chars[i]
        return "".join(chars)
    return street


def mismatch_city(cities_by_state: dict, state: str, city: str) -> str:
    """Swap in a different real city from the same state; zip is left as-is
    by the caller, so the returned city genuinely doesn't match that zip."""
    candidates = [c for c in cities_by_state[state] if c != city]
    return random.choice(candidates) if candidates else city


def outdate_address(street_number: str) -> str:
    try:
        n = int(street_number)
        return str(n + random.randint(50000, 99000))
    except ValueError:
        return street_number


def build_record(fake: Faker, pool: list, cities_by_state: dict, policy_id: int) -> dict:
    base = random.choice(pool)
    street_number = base["street_number"]
    street_name = base["street_name"]
    city = base["city"]
    state = base["state"]
    zip_code = base["zip"]
    unit = fake.secondary_address() if random.random() < 0.4 else ""

    error = random.choices(ERROR_TYPES, weights=ERROR_WEIGHTS, k=1)[0]

    if error == "misspelling":
        street_name = typo_street(street_name)
    elif error == "missing_unit":
        unit = ""  # forces the drop even if one would have been present
    elif error == "zip_city_mismatch":
        city = mismatch_city(cities_by_state, state, city)
    elif error == "outdated_address":
        street_number = outdate_address(street_number)

    stated_property_type = random.choices(
        STATED_PROPERTY_TYPES, weights=STATED_PROPERTY_TYPE_WEIGHTS, k=1
    )[0]

    return {
        "policy_id": f"POL-{policy_id:06d}",
        "policyholder_name": fake.name(),
        "street_number": street_number,
        "street_name": street_name,
        "unit": unit,
        "city": city,
        "state": state,
        "zip": zip_code,
        "injected_error": error or "clean",
        "stated_property_type": stated_property_type,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=350, help="number of records to generate")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="data/01_raw_synthetic.csv")
    args = parser.parse_args()

    random.seed(args.seed)
    fake = Faker()
    Faker.seed(args.seed)

    pool = load_real_addresses(REAL_ADDRESSES_PATH)
    cities_by_state = defaultdict(set)
    for addr in pool:
        cities_by_state[addr["state"]].add(addr["city"])
    # sorted() for deterministic ordering -- set iteration order isn't
    # stable across runs due to Python's string hash randomization, which
    # would silently break --seed reproducibility otherwise
    cities_by_state = {state: sorted(cities) for state, cities in cities_by_state.items()}

    records = [build_record(fake, pool, cities_by_state, i + 1) for i in range(args.n)]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)

    error_counts = {}
    for r in records:
        error_counts[r["injected_error"]] = error_counts.get(r["injected_error"], 0) + 1

    print(f"Wrote {len(records)} records to {out_path}")
    print("Error distribution:")
    for err, count in sorted(error_counts.items(), key=lambda x: -x[1]):
        print(f"  {err:20s} {count:4d}  ({100 * count / len(records):.1f}%)")


if __name__ == "__main__":
    main()
