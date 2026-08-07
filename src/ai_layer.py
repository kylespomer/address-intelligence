"""
ai_layer.py

The one focused AI workflow: for each enriched record, an LLM writes a 2-3
sentence plain-English risk narrative for an underwriter, and -- when the
policyholder's self-reported property type doesn't match what Precisely's
enrichment found for that address -- a one-sentence explanation of the
mismatch. Combining both keeps this one purposeful step rather than two
shallow ones.

The anomaly flag itself is NOT decided by the LLM. It's computed
deterministically (see `property_type_mismatch` below) by comparing
generate_dataset.py's `stated_property_type` (what the policyholder
declared) against the real enriched `property_type` from Precisely. An
earlier version of this script asked the LLM to judge whether "enriched
data contradicts what the raw record implies" -- but the raw record never
carried anything to contradict, so the model would have been flagging
anomalies with no ground truth to base them on. Self-reported vs.
enriched-data mismatch is a real underwriting/fraud-adjacent signal
(e.g. a policy rated as owner-occupied single-family that's actually a
multi-unit rental), so it's worth getting the flag itself right and
reserving the LLM for what it's actually good at: the narrative and the
plain-English explanation.

Uses Groq's free-tier API by default (OpenAI-compatible). To swap in
Gemini or Ollama instead, replace `call_llm()` — everything else in this
file is provider-agnostic.

Usage:
    python src/ai_layer.py --sample 10
    python src/ai_layer.py
"""

import argparse
import csv
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq
from tqdm import tqdm

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.1-8b-instant"  # fast + free-tier friendly; bump to a larger
                                  # model if narrative quality needs it

# Keyword -> broad category, used to normalize Precisely's free-text
# property_type (e.g. "MULTI-UNITS (9+) IMPROVEMENTS", "VACANT RESIDENTIAL
# LOT") into the same categories generate_dataset.py's stated_property_type
# is drawn from. Order matters -- first match wins, most specific first.
PROPERTY_TYPE_KEYWORDS = [
    ("VACANT", "Vacant Land"),
    ("MULTI", "Multi-Family / Rental"),
    ("APARTMENT", "Multi-Family / Rental"),
    ("CONDO", "Condominium / Townhome"),
    ("TOWNHOME", "Condominium / Townhome"),
    ("COMMERCIAL", "Commercial"),
    ("RETAIL", "Commercial"),
    ("OFFICE", "Commercial"),
    ("INDUSTRIAL", "Commercial"),
    ("SINGLE FAMILY", "Single Family Residence"),
    ("RESIDENT", "Single Family Residence"),
]


def normalize_property_type(raw: str) -> str:
    """Map Precisely's free-text property_type to a broad category, or
    "" if empty/unrecognized -- we only flag a mismatch when we actually
    have enrichment data to compare against."""
    if not raw:
        return ""
    upper = raw.upper()
    for keyword, category in PROPERTY_TYPE_KEYWORDS:
        if keyword in upper:
            return category
    return ""


def property_type_mismatch(record: dict) -> tuple:
    """Deterministic anomaly check: does the policyholder's stated property
    type match what enrichment actually found? Returns
    (is_anomaly, stated, enriched_category)."""
    stated = record.get("stated_property_type", "")
    enriched_category = normalize_property_type(record.get("property_type", ""))
    if not stated or not enriched_category:
        return False, stated, enriched_category  # can't judge without both
    return stated != enriched_category, stated, enriched_category


SYSTEM_PROMPT = """You are an underwriting assistant at an insurance carrier.
Given one policyholder's verified and enriched address record, do two things:

1. Write a 2-3 sentence plain-English risk summary an underwriter can act on,
   covering flood risk and fire station proximity. You may mention the
   property type factually (e.g. "a single-family residence"), but do NOT
   compare stated_property_type against property_type or comment on whether
   they match -- that judgment is handled separately by `anomaly`, not by you.
2. If `anomaly` is true, write a one-sentence anomaly_reason, in underwriting
   terms, explaining why the stated vs. actual property type mismatch matters
   (e.g. pricing risk, potential misclassification) -- this mismatch
   commentary belongs ONLY in anomaly_reason, never in risk_narrative. If
   `anomaly` is false, leave anomaly_reason as an empty string.

FEMA flood_risk_zone reference -- go by the letter PREFIX, ignore any
trailing digits or suffixes (e.g. "AE0009" is zone AE; "X-LOMR" is zone X
with a map revision note; "SHX" is shaded-X):
  X / SHX (shaded)  = LOW-to-moderate hazard, outside/near the 500-year
                       floodplain -- this is NOT high risk, don't call it that
  A, AE, AH, AO, AR = HIGH risk, inside the 100-year floodplain (Special
                       Flood Hazard Area) -- trailing digits are typically a
                       base flood elevation, not a severity score
  V, VE = HIGH risk coastal zone with wave action
  D = risk undetermined, insufficient data
If flood_risk_zone is empty, don't speculate about flood risk at all.

Respond ONLY with JSON in this exact shape, no markdown fences:
{"risk_narrative": "...", "anomaly_reason": "..."}
"""


def call_llm(record: dict, is_anomaly: bool, stated: str, enriched_category: str) -> dict:
    user_prompt = json.dumps(
        {
            "address": f"{record.get('verified_street', '')}, "
            f"{record.get('verified_city', '')}, "
            f"{record.get('verified_state', '')} {record.get('verified_zip', '')}",
            "flood_risk_zone": record.get("flood_risk_zone", ""),
            "fire_station_distance_mi": record.get("fire_station_distance_mi", ""),
            "property_type": record.get("property_type", ""),
            "stated_property_type": stated,
            "anomaly": is_anomaly,
        }
    )
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=300,
    )
    raw = resp.choices[0].message.content.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"risk_narrative": raw, "anomaly_reason": ""}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", default="data/03_enriched.csv")
    parser.add_argument("--out", dest="out_path", default="data/04_ai_narratives.csv")
    parser.add_argument("--sample", type=int, default=None)
    args = parser.parse_args()

    with open(args.in_path, newline="") as f:
        records = list(csv.DictReader(f))
    if args.sample:
        records = records[: args.sample]

    results = []
    anomaly_count = 0
    for record in tqdm(records, desc="Generating risk narratives"):
        is_anomaly, stated, enriched_category = property_type_mismatch(record)
        ai_result = call_llm(record, is_anomaly, stated, enriched_category)
        record["risk_narrative"] = ai_result.get("risk_narrative", "")
        record["anomaly_flag"] = is_anomaly
        record["anomaly_reason"] = ai_result.get("anomaly_reason", "") if is_anomaly else ""
        if is_anomaly:
            anomaly_count += 1
        results.append(record)
        time.sleep(0.2)  # stay well under free-tier rate limits

    out_path = Path(args.out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    print(f"\nWrote {len(results)} records to {out_path}")
    print(f"Anomalies flagged: {anomaly_count}/{len(results)}")
    print("\nSample narrative:")
    print(results[0]["risk_narrative"])


if __name__ == "__main__":
    main()
