"""
generate_dataset.py

Builds a synthetic policyholder dataset with realistic address noise for the
insurance-carrier scenario. Writes data/01_raw_synthetic.csv.

Error methodology (documented per assignment requirements):
  - 15% misspellings / transposed characters in the street name
    (e.g. "Steret" for "Street", "Maple Aveune" for "Maple Avenue")
  - 15% missing apartment/suite numbers (dropped even when the base
    address implies a multi-unit building)
  - 10% ZIP/city mismatches (city swapped for a same-state neighbor,
    ZIP left unchanged)
  - 10% outdated/non-existent addresses (house number bumped out of any
    plausible real range)
  - Remaining ~50% clean records, used as the "before" baseline

Every row gets an `injected_error` column recording which error type (if any)
was applied, so match-rate improvement can be measured against ground truth
later rather than eyeballed.

Usage:
    python src/generate_dataset.py --n 350 --seed 42
"""

import argparse
import csv
import random
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

# A few same-state neighbor-city swaps for the zip/city mismatch error.
# Extend this if your Faker locale/state mix needs more coverage.
CITY_SWAPS = {
    "Orlando": "Kissimmee",
    "Tampa": "Clearwater",
    "Jacksonville": "St. Augustine",
    "Miami": "Hialeah",
    "Des Moines": "West Des Moines",
    "Denver": "Aurora",
    "Austin": "Round Rock",
}


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


def mismatch_city(city: str) -> str:
    return CITY_SWAPS.get(city, city + " Heights")  # fallback swap if not in map


def outdate_address(street_number: str) -> str:
    try:
        n = int(street_number)
        return str(n + random.randint(50000, 99000))
    except ValueError:
        return street_number


def build_record(fake: Faker, policy_id: int) -> dict:
    street_number = str(random.randint(100, 9999))
    street_name = fake.street_name()
    unit = fake.secondary_address() if random.random() < 0.4 else ""
    city = fake.city()
    state = fake.state_abbr()
    zip_code = fake.zipcode()

    error = random.choices(ERROR_TYPES, weights=ERROR_WEIGHTS, k=1)[0]

    if error == "misspelling":
        street_name = typo_street(street_name)
    elif error == "missing_unit":
        unit = ""  # forces the drop even if one would have been present
    elif error == "zip_city_mismatch":
        city = mismatch_city(city)
    elif error == "outdated_address":
        street_number = outdate_address(street_number)

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

    records = [build_record(fake, i + 1) for i in range(args.n)]

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
