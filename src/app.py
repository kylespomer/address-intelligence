"""
app.py

Streamlit review UI for the pipeline's final output. Shows overall
pipeline stats, then every flagged anomaly as a card: property details,
a map of the geocoded location, and the AI-written risk narrative +
anomaly reason from ai_layer.py.

Each card lets an underwriter act on the anomaly:
  - "Flag for Review" marks it for follow-up (stays visible, badged)
  - "Ignore" dismisses it from the default view (an underwriter has looked
    and decided it's not worth acting on)
Decisions persist to data/05_review_status.csv, not just in-memory session
state, since this is meant to be a real triage tool, not a one-off demo --
closing the browser shouldn't lose an underwriter's work. Ignored anomalies
can be brought back via the sidebar toggle if a decision needs revisiting.

This is an extra deliverable on top of business_summary.md / slides/, not
a replacement -- built early (before the full 350-record run) so the real
anomaly examples it surfaces can inform what goes in those.

Usage:
    streamlit run src/app.py
"""

import csv
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

DATA_PATH = "data/04_ai_narratives.csv"
REVIEW_STATUS_PATH = "data/05_review_status.csv"
REVIEW_FIELDS = ["policy_id", "status", "reviewed_at"]

st.set_page_config(page_title="Address Intelligence -- Anomaly Review", layout="wide")


@st.cache_data
def load_data(path: str, _mtime: float) -> pd.DataFrame:
    # _mtime is part of the cache key (unused otherwise) so a re-run of the
    # pipeline invalidates the cache instead of serving stale narratives
    df = pd.read_csv(path)
    df["anomaly_flag"] = df["anomaly_flag"].astype(str) == "True"
    return df


def load_review_status(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {row["policy_id"]: row for row in csv.DictReader(f)}


def save_review_status(path: str, status_map: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(status_map.values())


def set_review_status(policy_id: str, status: str) -> None:
    st.session_state.review_status[policy_id] = {
        "policy_id": policy_id,
        "status": status,
        "reviewed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    save_review_status(REVIEW_STATUS_PATH, st.session_state.review_status)


def clear_review_status(policy_id: str) -> None:
    st.session_state.review_status.pop(policy_id, None)
    save_review_status(REVIEW_STATUS_PATH, st.session_state.review_status)


def property_address(row: pd.Series) -> str:
    street = row.get("verified_street") or row.get("street_name", "")
    city = row.get("verified_city") or row.get("city", "")
    state = row.get("verified_state") or row.get("state", "")
    zip_code = row.get("verified_zip") or row.get("zip", "")
    return f"{street}, {city}, {state} {zip_code}".strip()


def render_review_controls(policy_id: str) -> None:
    info = st.session_state.review_status.get(policy_id)
    status = info["status"] if info else None

    if status == "flagged_for_review":
        st.info(f"🚩 Flagged for review — {info['reviewed_at']}")
        if st.button("Undo", key=f"undo_{policy_id}"):
            clear_review_status(policy_id)
            st.rerun()
    elif status == "ignored":
        st.caption(f"✖️ Ignored — {info['reviewed_at']}")
        if st.button("Undo", key=f"undo_{policy_id}"):
            clear_review_status(policy_id)
            st.rerun()
    else:
        b1, b2 = st.columns(2)
        if b1.button("🚩 Flag for Review", key=f"flag_{policy_id}", use_container_width=True):
            set_review_status(policy_id, "flagged_for_review")
            st.rerun()
        if b2.button("✖️ Ignore", key=f"ignore_{policy_id}", use_container_width=True):
            set_review_status(policy_id, "ignored")
            st.rerun()


def render_anomaly_card(row: pd.Series) -> None:
    policy_id = row["policy_id"]
    with st.container(border=True):
        left, right = st.columns([2, 1])

        with left:
            st.markdown(f"**{policy_id}** -- {row.get('policyholder_name', '')}")
            st.caption(property_address(row))

            stated = row.get("stated_property_type", "")
            actual = row.get("property_type", "")
            st.markdown(f"**Stated property type:** {stated}  →  **Actual (enriched):** {actual}")

            flood = row.get("flood_risk_zone") or "n/a"
            fire_dist = row.get("fire_station_distance_mi", "")
            fire_name = row.get("fire_station_name", "")
            st.markdown(f"**Flood zone:** {flood}  |  **Nearest fire station:** {fire_dist} mi ({fire_name})")

            st.write(row.get("risk_narrative", ""))
            reason = row.get("anomaly_reason", "")
            if reason:
                st.warning(f"⚠️ {reason}")

            render_review_controls(policy_id)

        with right:
            lat, lon = row.get("latitude"), row.get("longitude")
            if pd.notna(lat) and pd.notna(lon):
                st.map(pd.DataFrame({"lat": [lat], "lon": [lon]}), zoom=13)
            else:
                st.caption("No coordinates available for this record.")


def main():
    st.title("Address Intelligence -- Anomaly Review")

    if "review_status" not in st.session_state:
        st.session_state.review_status = load_review_status(REVIEW_STATUS_PATH)

    try:
        df = load_data(DATA_PATH, os.path.getmtime(DATA_PATH))
    except FileNotFoundError:
        st.error(
            f"`{DATA_PATH}` not found. Run the pipeline through `src/ai_layer.py` first "
            "(a `--sample` run is enough to preview this app)."
        )
        return

    total = len(df)
    matched = (df["match_status"] == "matched").sum() if "match_status" in df else 0
    anomalies = df[df["anomaly_flag"]]

    reviewed_count = sum(
        1 for pid in anomalies["policy_id"] if pid in st.session_state.review_status
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Records processed", total)
    col2.metric("Verify match rate", f"{100 * matched / total:.1f}%" if total else "n/a")
    col3.metric(
        "Anomalies flagged",
        f"{len(anomalies)} ({100 * len(anomalies) / total:.1f}%)" if total else "0",
    )
    col4.metric(
        "Triaged by underwriter",
        f"{reviewed_count}/{len(anomalies)}" if len(anomalies) else "0",
    )

    st.divider()

    with st.sidebar:
        st.header("Filters")
        states = sorted(df["state"].dropna().unique().tolist()) if "state" in df else []
        state_filter = st.multiselect("State", states)
        show_all = st.checkbox("Show all records, not just anomalies", value=False)
        show_ignored = st.checkbox("Show ignored anomalies", value=False)

    view = anomalies if not show_all else df
    if state_filter:
        view = view[view["state"].isin(state_filter)]

    if not show_ignored:
        ignored_ids = {
            pid for pid, info in st.session_state.review_status.items() if info["status"] == "ignored"
        }
        view = view[~view["policy_id"].isin(ignored_ids)]

    label = "records" if show_all else "flagged anomalies"
    st.subheader(f"{len(view)} {label}")

    if view.empty:
        st.info("Nothing matches the current filters.")
        return

    for _, row in view.iterrows():
        if show_all and not row["anomaly_flag"]:
            with st.container(border=True):
                st.markdown(f"**{row['policy_id']}** -- {row.get('policyholder_name', '')}")
                st.caption(property_address(row))
                st.write(row.get("risk_narrative", ""))
        else:
            render_anomaly_card(row)


if __name__ == "__main__":
    main()
