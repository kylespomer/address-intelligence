# Address Intelligence Prototype — Powered by Precisely APIs

A working prototype for a mid-sized national insurance carrier scenario: 300k+ policyholder
records with messy addresses causing mispriced premiums, misrouted claims, and compliance
exposure. This repo verifies/standardizes addresses, geocodes them, enriches with
underwriting-relevant location attributes, and layers an AI step on top to turn the result
into an underwriter-ready decision, not just clean data.

## What's in here

```
data/
  reference/
    real_addresses.csv     real US addresses pulled from Precisely's own
                            property data -- the base every synthetic
                            record is built from (see fetch_real_addresses.py)
  01_raw_synthetic.csv     synthetic dirty policyholder records
  02_validated.csv         after Precisely verification + geocoding
  03_enriched.csv          after location enrichment
  summary_stats.md         before/after match rates and coverage
src/
  fetch_real_addresses.py  one-time pull of real addresses to seed the dataset
  generate_dataset.py      builds the synthetic dirty dataset from real addresses
  validate_geocode.py      Precisely verification + geocoding calls
  enrich.py                Precisely location enrichment calls
  ai_layer.py              risk narrative + anomaly-flagging AI step
notebooks/
  demo.ipynb               optional end-to-end walkthrough
business_summary.md        one-page VP-facing impact summary
slides/                    8-10 slide deck (added separately)
```

## Setup

1. Clone the repo and create a virtual environment:
   ```
   python -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in your keys:
   ```
   cp .env.example .env
   ```
   - `PRECISELY_API_KEY` / `PRECISELY_API_SECRET` — from your [Precisely Data Integrity Suite trial](https://developer.precisely.com/)
   - `GROQ_API_KEY` — from [Groq Cloud](https://console.groq.com/) (or swap in Gemini/Ollama, see `src/ai_layer.py`)

## Running the pipeline

```
python src/fetch_real_addresses.py  # one-time -> data/reference/real_addresses.csv
python src/generate_dataset.py      # -> data/01_raw_synthetic.csv
python src/validate_geocode.py      # -> data/02_validated.csv
python src/enrich.py                # -> data/03_enriched.csv
python src/ai_layer.py              # prints/saves risk narratives + anomaly flags
```

`fetch_real_addresses.py` only needs to run once (its output is checked into the repo) — it
pulls real addresses via Precisely's own property data so every synthetic record is built on a
real base address before errors are injected. It supports `--resume` to pick up cities it
couldn't reach on a prior run (the public API rate-limits after a burst of requests; see its
docstring).

## Credit budget

The Precisely trial includes 2,500 credits for 30 days. This pipeline is built to run against
a small sample first (see `--sample` flag on each script) before pointing at the full dataset,
to avoid burning credits on debugging.

## Sample outputs

*(Add a few before/after example records here once you've run the pipeline — this is one of
the first things a reviewer will look at.)*

## Dataset error methodology

See the header of `src/generate_dataset.py` for the exact error rates and types introduced
into the synthetic data.
