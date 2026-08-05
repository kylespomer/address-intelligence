"""
ai_layer.py

The one focused AI workflow: for each enriched record, an LLM produces a
2-3 sentence plain-English risk narrative for an underwriter AND flags
whether the enriched location data contradicts the policy's stated risk
zone. Combining both keeps this one purposeful step rather than two
shallow ones.

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

SYSTEM_PROMPT = """You are an underwriting assistant at an insurance carrier.
Given one policyholder's verified and enriched address record, do two things:

1. Write a 2-3 sentence plain-English risk summary an underwriter can act on.
   No jargon, no coordinates, just what matters for pricing/risk decisions.
2. Decide if the enriched data CONTRADICTS what the raw record implies about
   risk (e.g. property listed/assumed low-risk but sits in a high flood zone,
   or property type doesn't match the enrichment). Set anomaly to true/false
   and give a one-sentence reason.

Respond ONLY with JSON in this exact shape, no markdown fences:
{"risk_narrative": "...", "anomaly": true/false, "anomaly_reason": "..."}
"""


def call_llm(record: dict) -> dict:
    user_prompt = json.dumps(
        {
            "address": f"{record.get('verified_street', '')}, "
            f"{record.get('verified_city', '')}, "
            f"{record.get('verified_state', '')} {record.get('verified_zip', '')}",
            "flood_risk_zone": record.get("flood_risk_zone", ""),
            "fire_station_distance_mi": record.get("fire_station_distance_mi", ""),
            "property_type": record.get("property_type", ""),
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
        return {"risk_narrative": raw, "anomaly": False, "anomaly_reason": ""}


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
        ai_result = call_llm(record)
        record["risk_narrative"] = ai_result.get("risk_narrative", "")
        record["anomaly_flag"] = ai_result.get("anomaly", False)
        record["anomaly_reason"] = ai_result.get("anomaly_reason", "")
        if record["anomaly_flag"]:
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
