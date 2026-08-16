"""GridPulse interactive dashboard (Streamlit Community Cloud).

Reads the published SQLite database straight from the repo's orphan `data`
branch — the app never calls the EIA API, same rule as the static report.
Standalone by design: it does not import the gridpulse package, so the
cloud environment stays light (streamlit + pandas + requests only).
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

DB_URL = "https://raw.githubusercontent.com/evanderpool/gridpulse/data/gridpulse.db"
REPORT_URL = "https://evanderpool.github.io/gridpulse/"
REPO_URL = "https://github.com/evanderpool/gridpulse"
REGION_LABELS = {"CISO": "CAISO", "ERCO": "ERCOT", "MISO": "MISO", "PJM": "PJM"}

st.set_page_config(page_title="GridPulse", page_icon="⚡", layout="wide")


@st.cache_data(ttl=3600, show_spinner="Fetching the latest pipeline database…")
def load_tables() -> dict[str, pd.DataFrame]:
    """Download the published DB (hourly cache) and load the tables used here."""
    resp = requests.get(DB_URL, timeout=120)
    resp.raise_for_status()
    path = Path(tempfile.gettempdir()) / "gridpulse.db"
    path.write_bytes(resp.content)
    conn = sqlite3.connect(path)
    try:
        metrics = pd.read_sql_query(
            "SELECT region, period_utc, renewable_share, net_load_mwh, ramp_mwh_per_h "
            "FROM metrics_hourly", conn, parse_dates=["period_utc"])
        demand = pd.read_sql_query(
            "SELECT region, period_utc, demand_mwh, quality_flags FROM demand_hourly",
            conn, parse_dates=["period_utc"])
        flags = pd.read_sql_query(
            "SELECT quality_flags, COUNT(*) AS rows_ FROM fuelmix_hourly "
            "WHERE quality_flags != '' GROUP BY quality_flags", conn)
        runs = pd.read_sql_query(
            "SELECT run_id, stage, started_at, git_sha, rows_received, rows_valid, "
            "rows_quarantined, rows_upserted, api_calls, runtime_seconds "
            "FROM pipeline_runs ORDER BY started_at DESC", conn)
        quarantined = pd.read_sql_query("SELECT COUNT(*) AS n FROM quarantine", conn)
    finally:
        conn.close()
    return {"metrics": metrics, "demand": demand, "flags": flags,
            "runs": runs, "quarantined": quarantined}


data = load_tables()
metrics, demand = data["metrics"], data["demand"]

st.title("⚡ GridPulse")
st.caption(
    f"Hourly US electricity demand and fuel mix through a deterministic, "
    f"quality-flagged pipeline · [static report]({REPORT_URL}) · "
    f"[source & docs]({REPO_URL}) · data: EIA open-data API v2. "
    f"This app reads the pipeline's published database — it never calls the API."
)

cov_start, cov_end = demand["period_utc"].min(), demand["period_utc"].max()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Hours held", f"{demand['period_utc'].nunique():,}")
c2.metric("Window", f"{cov_start:%Y-%m-%d} → {cov_end:%Y-%m-%d}")
c3.metric("Gold metric rows", f"{len(metrics):,}")
c4.metric("Rows quarantined", int(data["quarantined"]["n"].iloc[0]))

regions = st.multiselect(
    "Regions", options=list(REGION_LABELS), default=list(REGION_LABELS),
    format_func=REGION_LABELS.get)

tab_share, tab_duck, tab_demand, tab_quality = st.tabs(
    ["Renewable share", "Duck curve", "Demand", "Data quality"])

with tab_share:
    st.subheader("Renewable share of generation, daily average")
    daily = (metrics[metrics["region"].isin(regions)]
             .assign(day=lambda d: d["period_utc"].dt.floor("D"),
                     pct=lambda d: d["renewable_share"] * 100)
             .groupby(["day", "region"])["pct"].mean().unstack())
    st.line_chart(daily.rename(columns=REGION_LABELS), height=380)
    st.caption("Thirteen months of daily averages — ERCOT's build-out and "
               "CAISO's seasonal swing are both visible at this scale.")

with tab_duck:
    st.subheader("Net load vs demand — the duck curve, any day on record")
    col_a, col_b = st.columns([1, 3])
    region = col_a.selectbox("Region", regions or list(REGION_LABELS),
                             format_func=REGION_LABELS.get)
    day = col_b.date_input(
        "Day (UTC)", value=cov_end.date(), min_value=cov_start.date(),
        max_value=cov_end.date())
    m = metrics[(metrics["region"] == region)
                & (metrics["period_utc"].dt.date == day)].set_index("period_utc")
    d = demand[(demand["region"] == region)
               & (demand["period_utc"].dt.date == day)].set_index("period_utc")
    if m.empty:
        st.info("No gold rows for that day — pick another.")
    else:
        frame = pd.DataFrame({"Demand (MWh)": d["demand_mwh"],
                              "Net load (MWh)": m["net_load_mwh"]})
        st.line_chart(frame, height=380)
        ramp = m["ramp_mwh_per_h"].max()
        st.caption(f"Steepest hourly ramp this day: "
                   f"{'+' if pd.notna(ramp) and ramp >= 0 else ''}"
                   f"{int(ramp):,} MWh/h" if pd.notna(ramp) else "No ramp computable.")

with tab_demand:
    st.subheader("Hourly demand, trailing 14 days")
    recent = demand[(demand["region"].isin(regions))
                    & (demand["period_utc"] >= cov_end - pd.Timedelta(days=14))]
    st.line_chart(
        recent.pivot_table(index="period_utc", columns="region",
                           values="demand_mwh").rename(columns=REGION_LABELS),
        height=380)

with tab_quality:
    st.subheader("The quality panel — nothing fails silently")
    left, right = st.columns(2)
    with left:
        st.markdown("**Quality flags (kept + labeled, never dropped)**")
        flags = data["flags"].rename(
            columns={"quality_flags": "flag", "rows_": "rows"})
        st.dataframe(flags, hide_index=True, use_container_width=True)
        st.markdown(
            "- `negative_generation` — nighttime solar drawing station power\n"
            "- `storage_charging` — batteries charging (routine, own flag so "
            "the anomaly flag keeps meaning)")
    with right:
        st.markdown("**Newly quarantined per transform run**")
        runs = data["runs"]
        tr = runs[runs["stage"].str.startswith("transform")].copy()
        tr["started_at"] = pd.to_datetime(tr["started_at"])
        q = tr.groupby(tr["started_at"].dt.floor("D"))["rows_quarantined"].sum()
        st.bar_chart(q, height=240)
        st.caption("The improvement loop's headline series — merges of new "
                   "conversion rules should bend this toward zero.")
    st.markdown("**Run ledger** — every run keyed to the commit that produced it")
    st.dataframe(data["runs"].head(30), hide_index=True, use_container_width=True)
