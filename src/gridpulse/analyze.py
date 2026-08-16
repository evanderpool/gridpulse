"""Gold aggregates for the report — the three questions, answered from SQLite.

Everything here reads the local database only; the report can never trigger
an API call. Aggregation happens in SQL where SQLite is strong and in plain
Python where it is clearer — no dataframe engine needed at report scale.
"""

from __future__ import annotations

import sqlite3

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
