"""Gold aggregates for the report — the three questions, answered from SQLite.

Everything here reads the local database only; the report can never trigger
an API call. Aggregation happens in SQL where SQLite is strong and in plain
Python where it is clearer — no dataframe engine needed at report scale.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from .config import REGIONS


def coverage(conn: sqlite3.Connection) -> dict:
    """The window the database actually holds — the report states it honestly."""
    row = conn.execute(
        "SELECT MIN(period_utc), MAX(period_utc), COUNT(DISTINCT period_utc) "
        "FROM demand_hourly"
    ).fetchone()
    return {"start": row[0], "end": row[1], "hours": row[2]}


def latest_series(conn: sqlite3.Connection, hours: int = 25) -> dict:
    """Per-region series for the chart window: the newest ``hours`` periods."""
    periods = [r[0] for r in conn.execute(
        "SELECT DISTINCT period_utc FROM metrics_hourly ORDER BY period_utc DESC LIMIT ?",
        (hours,),
    )][::-1]
    if not periods:
        return {"periods": [], "demand": {}, "share": {}, "ciso_net": [], "ciso_demand": []}
    placeholders = ",".join("?" * len(periods))
    demand: dict[str, list] = {r: [None] * len(periods) for r in REGIONS}
    share: dict[str, list] = {r: [None] * len(periods) for r in REGIONS}
    index = {p: i for i, p in enumerate(periods)}
    for region, period, mwh in conn.execute(
        f"SELECT region, period_utc, demand_mwh FROM demand_hourly "
        f"WHERE period_utc IN ({placeholders})", periods,
    ):
        if region in demand:
            demand[region][index[period]] = mwh
    ciso_net = [None] * len(periods)
    for region, period, s, net in conn.execute(
        f"SELECT region, period_utc, renewable_share, net_load_mwh FROM metrics_hourly "
        f"WHERE period_utc IN ({placeholders})", periods,
    ):
        if region in share:
            share[region][index[period]] = None if s is None else round(s * 100, 2)
        if region == "CISO":
            ciso_net[index[period]] = net
    return {"periods": periods, "demand": demand, "share": share,
            "ciso_net": ciso_net, "ciso_demand": demand.get("CISO", [])}


def question_answers(conn: sqlite3.Connection) -> dict:
    """Headline numbers for Q1 (share), Q2 (peaks), Q3 (duck curve)."""
    q1 = {}
    for region, avg_share in conn.execute(
        "SELECT region, AVG(renewable_share) FROM metrics_hourly "
        "WHERE renewable_share IS NOT NULL GROUP BY region"
    ):
        q1[region] = round(avg_share * 100, 1)
    q2 = {}
    for region in REGIONS:
        row = conn.execute(
            "SELECT period_utc, demand_mwh FROM demand_hourly WHERE region=? "
            "ORDER BY demand_mwh DESC LIMIT 1", (region,),
        ).fetchone()
        if row:
            q2[region] = {"peak_hour_utc": row[0], "peak_mwh": row[1]}
    q3 = None
    duck = conn.execute(
        "SELECT MAX(renewable_share), MIN(net_load_mwh), MAX(ramp_mwh_per_h) "
        "FROM metrics_hourly WHERE region='CISO'"
    ).fetchone()
    if duck and duck[0] is not None:
        q3 = {"max_share_pct": round(duck[0] * 100, 1),
              "min_net_load_mwh": duck[1], "max_ramp_mwh": duck[2]}
    return {"q1": q1, "q2": q2, "q3": q3}


PRESETS: dict[str, int | None] = {
    "7d": 7, "30d": 30, "90d": 90, "6m": 182, "12m": 365, "all": None,
}


def _window_bounds(conn: sqlite3.Connection, days: int | None) -> tuple[str, str]:
    """[start, end) ISO bounds for a preset, anchored at the newest hour."""
    end = conn.execute("SELECT MAX(period_utc) FROM demand_hourly").fetchone()[0]
    if end is None:
        return "", ""
    end_dt = datetime.fromisoformat(end) + timedelta(hours=1)
    if days is None:
        return "", end_dt.isoformat()
    return (end_dt - timedelta(days=days)).isoformat(), end_dt.isoformat()


def _region_window_stats(conn: sqlite3.Connection, region: str,
                         start: str, end: str) -> dict | None:
    """Averages and extremes for one region over [start, end)."""
    avg_d = conn.execute(
        "SELECT AVG(demand_mwh) FROM demand_hourly WHERE region=? "
        "AND period_utc >= ? AND period_utc < ?", (region, start, end)).fetchone()[0]
    if avg_d is None:
        return None
    avg_s = conn.execute(
        "SELECT AVG(renewable_share) FROM metrics_hourly WHERE region=? "
        "AND period_utc >= ? AND period_utc < ? AND renewable_share IS NOT NULL",
        (region, start, end)).fetchone()[0]
    peak = conn.execute(
        "SELECT period_utc, demand_mwh FROM demand_hourly WHERE region=? "
        "AND period_utc >= ? AND period_utc < ? ORDER BY demand_mwh DESC LIMIT 1",
        (region, start, end)).fetchone()
    belly = conn.execute(
        "SELECT period_utc, net_load_mwh FROM metrics_hourly WHERE region=? "
        "AND period_utc >= ? AND period_utc < ? ORDER BY net_load_mwh ASC LIMIT 1",
        (region, start, end)).fetchone()
    ramp = conn.execute(
        "SELECT MAX(ramp_mwh_per_h) FROM metrics_hourly WHERE region=? "
        "AND period_utc >= ? AND period_utc < ?", (region, start, end)).fetchone()[0]
    return {
        "avg_demand": round(avg_d, 1),
        "avg_share": None if avg_s is None else round(avg_s * 100, 2),
        "peak": None if not peak else [peak[0], peak[1]],
        "belly": None if not belly else [belly[0], belly[1]],
        "max_ramp": ramp,
    }


def preset_findings(conn: sqlite3.Connection) -> dict:
    """Per preset, per region: current stats + prior-window and YoY deltas.

    Everything is computed here at build time so the published page needs no
    backend and stays deterministic — same DB, same findings.
    """
    out: dict[str, dict] = {}
    for key, days in PRESETS.items():
        start, end = _window_bounds(conn, days)
        if not end:
            out[key] = {}
            continue
        regions: dict[str, dict] = {}
        for region in REGIONS:
            cur = _region_window_stats(conn, region, start, end)
            if cur is None:
                continue
            span = (datetime.fromisoformat(end) - datetime.fromisoformat(start)
                    if start else None)
            prev = yoy = None
            if span is not None:
                p_end, p_start = start, (datetime.fromisoformat(start) - span).isoformat()
                prev = _region_window_stats(conn, region, p_start, p_end)
                y_start = (datetime.fromisoformat(start) - timedelta(days=365)).isoformat()
                y_end = (datetime.fromisoformat(end) - timedelta(days=365)).isoformat()
                yoy = _region_window_stats(conn, region, y_start, y_end)

            def _pct(now: float, then: dict | None) -> float | None:
                if then is None or not then.get("avg_demand"):
                    return None
                return round((now - then["avg_demand"]) / then["avg_demand"] * 100, 1)

            def _pts(now: float | None, then: dict | None) -> float | None:
                if now is None or then is None or then.get("avg_share") is None:
                    return None
                return round(now - then["avg_share"], 1)

            cur["demand_vs_prev_pct"] = _pct(cur["avg_demand"], prev)
            cur["demand_vs_yoy_pct"] = _pct(cur["avg_demand"], yoy)
            cur["share_vs_prev_pts"] = _pts(cur["avg_share"], prev)
            cur["share_vs_yoy_pts"] = _pts(cur["avg_share"], yoy)
            regions[region] = cur
        out[key] = {"start": start or None, "end": end, "regions": regions}
    return out


def daily_series(conn: sqlite3.Connection) -> dict:
    """Full-history daily averages per region: [day, avg_demand, avg_share_pct]."""
    out: dict[str, list] = {r: [] for r in REGIONS}
    shares = {
        (r, d): s for r, d, s in conn.execute(
            "SELECT region, substr(period_utc,1,10), AVG(renewable_share) "
            "FROM metrics_hourly WHERE renewable_share IS NOT NULL "
            "GROUP BY region, substr(period_utc,1,10)")
    }
    for region, day, avg_d in conn.execute(
        "SELECT region, substr(period_utc,1,10), AVG(demand_mwh) FROM demand_hourly "
        "GROUP BY region, substr(period_utc,1,10) ORDER BY 2"
    ):
        if region in out:
            share = shares.get((region, day))
            out[region].append([day, round(avg_d), None if share is None
                                else round(share * 100, 2)])
    return out


def hourly_recent(conn: sqlite3.Connection, days: int = 30) -> dict:
    """Hourly demand + net load per region for the duck-curve day explorer."""
    start, end = _window_bounds(conn, days)
    if not end:
        return {r: [] for r in REGIONS}
    nets = {
        (r, p): n for r, p, n in conn.execute(
            "SELECT region, period_utc, net_load_mwh FROM metrics_hourly "
            "WHERE period_utc >= ? AND period_utc < ?", (start, end))
    }
    out: dict[str, list] = {r: [] for r in REGIONS}
    for region, period, mwh in conn.execute(
        "SELECT region, period_utc, demand_mwh FROM demand_hourly "
        "WHERE period_utc >= ? AND period_utc < ? ORDER BY period_utc", (start, end)
    ):
        if region in out:
            out[region].append([period[:13], mwh, nets.get((region, period))])
    return out


def hourly_profile(conn: sqlite3.Connection) -> dict:
    """Per preset, per region: average demand by hour of day (UTC), 24 values."""
    out: dict[str, dict] = {}
    for key, days in PRESETS.items():
        start, end = _window_bounds(conn, days)
        if not end:
            out[key] = {}
            continue
        per_region: dict[str, list] = {}
        for region in REGIONS:
            rows = conn.execute(
                "SELECT CAST(substr(period_utc,12,2) AS INTEGER) h, AVG(demand_mwh) "
                "FROM demand_hourly WHERE region=? AND period_utc >= ? "
                "AND period_utc < ? GROUP BY h ORDER BY h",
                (region, start, end)).fetchall()
            if rows:
                by_hour = dict(rows)
                per_region[region] = [round(by_hour.get(h, 0)) for h in range(24)]
        out[key] = per_region
    return out


def quality_summary(conn: sqlite3.Connection) -> dict:
    """The quality panel: flags, quarantine, and the recent run ledger."""
    flags = dict(conn.execute(
        "SELECT quality_flags, COUNT(*) FROM fuelmix_hourly "
        "WHERE quality_flags != '' GROUP BY quality_flags"
    ).fetchall())
    for flag, n in conn.execute(
        "SELECT quality_flags, COUNT(*) FROM demand_hourly "
        "WHERE quality_flags != '' GROUP BY quality_flags"
    ):
        flags[flag] = flags.get(flag, 0) + n
    quarantined = conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
    runs = [dict(zip(
        ("run_id", "stage", "git_sha", "rows_received", "rows_valid",
         "rows_quarantined", "rows_upserted", "api_calls", "runtime_seconds"), r))
        for r in conn.execute(
            "SELECT run_id, stage, git_sha, rows_received, rows_valid, "
            "rows_quarantined, rows_upserted, api_calls, runtime_seconds "
            "FROM pipeline_runs ORDER BY started_at DESC LIMIT 12")]
    return {"flags": flags, "quarantined": quarantined, "runs": runs}
