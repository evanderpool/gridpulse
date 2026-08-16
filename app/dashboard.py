"""GridPulse interactive dashboard (Streamlit Community Cloud).

Reads the published SQLite database straight from the repo's orphan `data`
branch — the app never calls the EIA API, same rule as the static report.
Standalone by design: it does not import the gridpulse package, so the
cloud environment stays light (streamlit + pandas + requests only).

The Findings tab is deterministic analysis — computed comparisons, not an
LLM: every sentence traces to arithmetic over the selected window vs the
preceding equal window and the same window one year earlier.
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

DB_URL = "https://raw.githubusercontent.com/evanderpool/gridpulse/data/gridpulse.db"
REPORT_URL = "https://evanderpool.github.io/gridpulse/"
REPO_URL = "https://github.com/evanderpool/gridpulse"
REGION_LABELS = {"CISO": "CAISO", "ERCO": "ERCOT", "MISO": "MISO", "PJM": "PJM"}
RANGE_PRESETS = {
    "Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90,
    "Last 6 months": 182, "Last 12 months": 365,
    "Full history": None, "Custom range": "custom",
}

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


def in_window(df: pd.DataFrame, start, end) -> pd.DataFrame:
    return df[(df["period_utc"] >= start) & (df["period_utc"] < end)]


def pct(cur: float, prev: float) -> float | None:
    if prev and pd.notna(prev) and pd.notna(cur):
        return (cur - prev) / prev * 100
    return None


def fmt_delta(value: float | None, unit: str = "%") -> str:
    if value is None:
        return "no comparison data"
    arrow = "▲" if value >= 0 else "▼"
    return f"{arrow} {abs(value):.1f}{unit}"


data = load_tables()
metrics_all, demand_all = data["metrics"], data["demand"]
cov_start, cov_end = demand_all["period_utc"].min(), demand_all["period_utc"].max()

st.title("⚡ GridPulse")
st.caption(
    f"Hourly US electricity demand and fuel mix through a deterministic, "
    f"quality-flagged pipeline · [static report]({REPORT_URL}) · "
    f"[source & docs]({REPO_URL}) · data: EIA open-data API v2 · all times UTC. "
    f"This app reads the pipeline's published database — it never calls the API."
)

# ---------- sidebar: the analysis window ----------
st.sidebar.header("Analysis window")
preset = st.sidebar.radio("Time frame", list(RANGE_PRESETS), index=2)
if RANGE_PRESETS[preset] == "custom":
    c1, c2 = st.sidebar.columns(2)
    w_start = pd.Timestamp(c1.date_input("From", cov_start.date()), tz="UTC")
    w_end = pd.Timestamp(c2.date_input("To", cov_end.date()), tz="UTC") + timedelta(days=1)
elif RANGE_PRESETS[preset] is None:
    w_start, w_end = cov_start, cov_end + timedelta(hours=1)
else:
    w_end = cov_end + timedelta(hours=1)
    w_start = w_end - timedelta(days=RANGE_PRESETS[preset])
window_days = max((w_end - w_start).days, 1)

regions = st.sidebar.multiselect(
    "Regions", options=list(REGION_LABELS), default=list(REGION_LABELS),
    format_func=REGION_LABELS.get)
st.sidebar.caption(
    f"Coverage: {cov_start:%Y-%m-%d} → {cov_end:%Y-%m-%d} "
    f"({demand_all['period_utc'].nunique():,} hours). Comparisons use the "
    "preceding window of equal length and the same window one year earlier.")

demand = in_window(demand_all[demand_all["region"].isin(regions)], w_start, w_end)
metrics = in_window(metrics_all[metrics_all["region"].isin(regions)], w_start, w_end)

# comparison windows
prev_start, prev_end = w_start - (w_end - w_start), w_start
yoy_start, yoy_end = w_start - timedelta(days=365), w_end - timedelta(days=365)

tab_find, tab_trend, tab_duck, tab_profile, tab_quality = st.tabs(
    ["📋 Findings", "📈 Trends", "🦆 Duck curve", "🕐 Daily profile", "✅ Data quality"])

# ---------- Findings: deterministic auto-analysis ----------
with tab_find:
    st.subheader(f"Computed findings — {preset.lower()} "
                 f"({w_start:%Y-%m-%d} → {(w_end - timedelta(days=1)):%Y-%m-%d})")
    cols = st.columns(len(regions) or 1)
    sentences: list[str] = []
    for col, region in zip(cols, regions):
        name = REGION_LABELS[region]
        cur_d = in_window(demand_all[demand_all["region"] == region], w_start, w_end)
        prev_d = in_window(demand_all[demand_all["region"] == region], prev_start, prev_end)
        yoy_d = in_window(demand_all[demand_all["region"] == region], yoy_start, yoy_end)
        cur_m = in_window(metrics_all[metrics_all["region"] == region], w_start, w_end)
        prev_m = in_window(metrics_all[metrics_all["region"] == region], prev_start, prev_end)
        yoy_m = in_window(metrics_all[metrics_all["region"] == region], yoy_start, yoy_end)

        avg_now = cur_d["demand_mwh"].mean()
        d_prev = pct(avg_now, prev_d["demand_mwh"].mean() if len(prev_d) else None)
        d_yoy = pct(avg_now, yoy_d["demand_mwh"].mean() if len(yoy_d) else None)
        share_now = cur_m["renewable_share"].mean()
        share_prev = prev_m["renewable_share"].mean() if len(prev_m) else None
        share_yoy = yoy_m["renewable_share"].mean() if len(yoy_m) else None

        col.metric(f"{name} · avg demand",
                   f"{avg_now/1000:,.1f} GWh/h" if pd.notna(avg_now) else "—",
                   None if d_prev is None else f"{d_prev:+.1f}% vs prior window")
        col.metric(f"{name} · renewable share",
                   f"{share_now*100:.1f}%" if pd.notna(share_now) else "—",
                   None if share_prev is None or pd.isna(share_prev)
                   else f"{(share_now - share_prev)*100:+.1f} pts vs prior")

        if pd.notna(avg_now):
            bits = [f"**{name}** averaged **{avg_now/1000:,.1f} GWh/h** of demand"]
            if d_prev is not None:
                bits.append(f"{'up' if d_prev >= 0 else 'down'} "
                            f"**{abs(d_prev):.1f}%** vs the prior {window_days} days")
            if d_yoy is not None:
                bits.append(f"{'up' if d_yoy >= 0 else 'down'} "
                            f"**{abs(d_yoy):.1f}%** vs the same window last year")
            sentences.append("; ".join(bits) + ".")
        if pd.notna(share_now):
            bits = [f"**{name}** renewables covered **{share_now*100:.1f}%** of generation"]
            if share_prev is not None and pd.notna(share_prev):
                delta = (share_now - share_prev) * 100
                bits.append(f"{'up' if delta >= 0 else 'down'} **{abs(delta):.1f} points** "
                            "vs the prior window")
            if share_yoy is not None and pd.notna(share_yoy):
                delta = (share_now - share_yoy) * 100
                bits.append(f"{'up' if delta >= 0 else 'down'} **{abs(delta):.1f} points** "
                            "year over year")
            sentences.append("; ".join(bits) + ".")
        if len(cur_d):
            peak = cur_d.loc[cur_d["demand_mwh"].idxmax()]
            sentences.append(
                f"**{name}** peaked at **{peak['demand_mwh']/1000:,.1f} GWh** on "
                f"{peak['period_utc']:%Y-%m-%d at %H:00} UTC.")
        if len(cur_m) and cur_m["net_load_mwh"].notna().any():
            belly = cur_m.loc[cur_m["net_load_mwh"].idxmin()]
            ramp = cur_m["ramp_mwh_per_h"].max()
            sentences.append(
                f"**{name}** net load bottomed at "
                f"**{belly['net_load_mwh']/1000:,.1f} GWh** "
                f"({belly['period_utc']:%Y-%m-%d %H:00} UTC)"
                + (f"; steepest evening ramp **+{ramp/1000:,.1f} GWh/h**."
                   if pd.notna(ramp) else "."))

    st.markdown("#### What the data says")
    for s in sentences:
        st.markdown(f"- {s}")
    if not sentences:
        st.info("No data in this window — widen the time frame.")
    st.caption("Every sentence above is computed arithmetic over the pipeline's "
               "gold tables — no model, no estimation. Export the exact slice "
               "below to verify or extend the analysis.")
    d1, d2 = st.columns(2)
    d1.download_button("⬇ Download demand slice (CSV)",
                       demand.to_csv(index=False), "gridpulse_demand.csv", "text/csv")
    d2.download_button("⬇ Download metrics slice (CSV)",
                       metrics.to_csv(index=False), "gridpulse_metrics.csv", "text/csv")

# ---------- Trends ----------
with tab_trend:
    st.subheader("Daily average demand")
    freq = "D" if window_days <= 120 else "W"
    dd = (demand.assign(bucket=lambda d: d["period_utc"].dt.floor(freq))
          .pivot_table(index="bucket", columns="region", values="demand_mwh"))
    st.line_chart(dd.rename(columns=REGION_LABELS), height=320)
    st.subheader("Daily average renewable share (%)")
    ss = (metrics.assign(bucket=lambda d: d["period_utc"].dt.floor(freq),
                         pct=lambda d: d["renewable_share"] * 100)
          .pivot_table(index="bucket", columns="region", values="pct"))
    st.line_chart(ss.rename(columns=REGION_LABELS), height=320)
    st.caption(f"Bucketed {'daily' if freq == 'D' else 'weekly'} for this window size.")

# ---------- Duck curve ----------
with tab_duck:
    st.subheader("Net load vs demand — any day on record")
    col_a, col_b = st.columns([1, 3])
    region = col_a.selectbox("Region", regions or list(REGION_LABELS),
                             format_func=REGION_LABELS.get)
    default_day = min(cov_end.date(), (w_end - timedelta(days=1)).date())
    day = col_b.date_input("Day (UTC)", value=default_day,
                           min_value=cov_start.date(), max_value=cov_end.date())
    m = metrics_all[(metrics_all["region"] == region)
                    & (metrics_all["period_utc"].dt.date == day)].set_index("period_utc")
    d = demand_all[(demand_all["region"] == region)
                   & (demand_all["period_utc"].dt.date == day)].set_index("period_utc")
    if m.empty:
        st.info("No gold rows for that day — pick another.")
    else:
        st.line_chart(pd.DataFrame({"Demand (MWh)": d["demand_mwh"],
                                    "Net load (MWh)": m["net_load_mwh"]}), height=380)
        ramp = m["ramp_mwh_per_h"].max()
        if pd.notna(ramp):
            st.caption(f"Steepest hourly ramp this day: +{int(ramp):,} MWh/h")

# ---------- Daily profile ----------
with tab_profile:
    st.subheader("Average demand by hour of day (UTC) — when each grid peaks")
    prof = (demand.assign(hour=lambda d: d["period_utc"].dt.hour)
            .pivot_table(index="hour", columns="region", values="demand_mwh"))
    st.line_chart(prof.rename(columns=REGION_LABELS), height=380)
    st.caption(
        "Averaged over the selected window. Local peak times differ by grid — "
        "e.g. CAISO's evening peak lands ~01:00–03:00 UTC (6–8 pm Pacific). "
        "Change the window to see seasonal shift in the shape.")

# ---------- Data quality ----------
with tab_quality:
    st.subheader("The quality panel — nothing fails silently")
    left, right = st.columns(2)
    with left:
        st.markdown("**Quality flags (kept + labeled, never dropped)**")
        st.dataframe(data["flags"].rename(
            columns={"quality_flags": "flag", "rows_": "rows"}),
            hide_index=True, width='stretch')
        st.markdown(
            "- `negative_generation` — nighttime solar drawing station power\n"
            "- `storage_charging` — batteries charging (routine, own flag so "
            "the anomaly flag keeps meaning)")
        st.metric("Rows quarantined (all time)", int(data["quarantined"]["n"].iloc[0]))
    with right:
        st.markdown("**Newly quarantined per transform run**")
        runs = data["runs"]
        tr = runs[runs["stage"].str.startswith("transform")].copy()
        tr["started_at"] = pd.to_datetime(tr["started_at"], format="ISO8601")
        q = tr.groupby(tr["started_at"].dt.floor("D"))["rows_quarantined"].sum()
        st.bar_chart(q, height=240)
        st.caption("The improvement loop's headline series — merges of new "
                   "conversion rules should bend this toward zero.")
    st.markdown("**Run ledger** — every run keyed to the commit that produced it")
    st.dataframe(data["runs"].head(30), hide_index=True, width='stretch')
