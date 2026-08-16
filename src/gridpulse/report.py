"""The static HTML report — the public face, regenerated from SQLite only.

Design rules: self-contained (no external assets), theme-aware (light/dark),
one axis per chart, region colors fixed (CISO blue, ERCO orange, MISO aqua,
PJM yellow), values in text ink — series color lives on marks only. The
report never touches the API; that is a load-bearing architecture decision.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .analyze import coverage, latest_series, quality_summary, question_answers

REGION_LABELS = {"CISO": "CAISO", "ERCO": "ERCOT", "MISO": "MISO", "PJM": "PJM"}

W, H, PL, PR, PT, PB = 760, 240, 56, 96, 14, 26
IW, IH = W - PL - PR, H - PT - PB

CSS = """
:root { color-scheme: light;
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,0.10);
  --ciso:#2a78d6; --erco:#eb6834; --miso:#1baf7a; --pjm:#eda100;
  --goodtext:#006300; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
  --ciso:#3987e5; --erco:#d95926; --miso:#199e70; --pjm:#c98500;
  --goodtext:#0ca30c; } }
:root[data-theme="dark"] {
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
  --ciso:#3987e5; --erco:#d95926; --miso:#199e70; --pjm:#c98500;
  --goodtext:#0ca30c; }
* { box-sizing: border-box; }
body { margin:0; background:var(--page); color:var(--ink);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; padding:2.5rem 1rem 4rem; }
.wrap { max-width:820px; margin:0 auto; display:flex; flex-direction:column; gap:1.1rem; }
header h1 { font-size:1.7rem; margin:0; letter-spacing:-0.01em; }
header p { margin:0.35rem 0 0; color:var(--ink2); max-width:66ch; }
.eyebrow { font-size:0.72rem; text-transform:uppercase; letter-spacing:0.12em;
  color:var(--muted); margin-bottom:0.4rem; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:0.7rem; }
.tile { background:var(--surface); border:1px solid var(--border); border-radius:6px;
  padding:0.75rem 0.9rem; }
.tile .v { font-size:1.5rem; font-weight:650; line-height:1.15; }
.tile .k { font-size:0.78rem; color:var(--ink2); margin-top:2px; }
.tile .ok { color:var(--goodtext); font-weight:650; }
.card { background:var(--surface); border:1px solid var(--border); border-radius:6px;
  padding:1rem 1.1rem 0.7rem; }
.card h2 { font-size:1.02rem; margin:0; }
.card .sub { font-size:0.82rem; color:var(--ink2); margin:0.15rem 0 0.5rem; }
.legend { display:flex; gap:1rem; flex-wrap:wrap; font-size:0.8rem;
  color:var(--ink2); margin:0.1rem 0 0.2rem; }
.legend span { display:inline-flex; align-items:center; gap:6px; }
.swatch { width:14px; height:3px; border-radius:2px; display:inline-block; }
.chartwrap { position:relative; }
svg { width:100%; height:auto; display:block; }
.grid { stroke:var(--grid); stroke-width:1; }
.axis { stroke:var(--axis); stroke-width:1; }
.tick { fill:var(--muted); font-size:10.5px; font-variant-numeric:tabular-nums; }
.endlabel { font-size:11px; font-weight:600; }
.crosshair { stroke:var(--axis); stroke-width:1; stroke-dasharray:3 3; }
.tooltip { position:absolute; pointer-events:none; background:var(--surface);
  border:1px solid var(--border); border-radius:5px; padding:6px 9px;
  font-size:0.76rem; box-shadow:0 2px 10px rgba(0,0,0,0.12); white-space:nowrap; }
.tooltip .t { color:var(--muted); margin-bottom:2px; }
.tooltip .row { display:flex; align-items:center; gap:6px; }
.tooltip .row b { font-variant-numeric:tabular-nums; font-weight:600;
  margin-left:auto; padding-left:10px; }
.dot { width:8px; height:8px; border-radius:50%; display:inline-block; }
table { border-collapse:collapse; width:100%; font-size:0.8rem; }
th { text-align:left; color:var(--muted); font-weight:600; font-size:0.7rem;
  text-transform:uppercase; letter-spacing:0.07em; padding:4px 10px 4px 0;
  border-bottom:1px solid var(--axis); }
td { padding:5px 10px 5px 0; border-bottom:1px solid var(--grid); }
.num { text-align:right; font-variant-numeric:tabular-nums; }
th.num-h { text-align:right; }
.mono { font-family:Consolas,ui-monospace,monospace; font-size:0.76rem; }
.tablewrap { overflow-x:auto; }
.chips { display:flex; gap:0.6rem; flex-wrap:wrap; }
.chip { border:1px solid var(--border); background:var(--surface); border-radius:999px;
  padding:4px 12px; font-size:0.8rem; color:var(--ink2); }
.chip b { color:var(--ink); font-variant-numeric:tabular-nums; }
footer { font-size:0.78rem; color:var(--muted); }
footer a { color:var(--ink2); }
"""

JS = """
document.querySelectorAll('.chartwrap').forEach(function (wrap) {
  var svg = wrap.querySelector('svg');
  var cross = wrap.querySelector('.crosshair');
  var tip = wrap.querySelector('.tooltip');
  var series = JSON.parse(wrap.dataset.series);
  var xs = JSON.parse(wrap.dataset.x);
  var labels = JSON.parse(wrap.dataset.labels);
  var unit = wrap.dataset.unit;
  svg.addEventListener('mousemove', function (ev) {
    var r = svg.getBoundingClientRect();
    var vx = (ev.clientX - r.left) * (__W__ / r.width);
    var best = 0, bd = 1e9;
    xs.forEach(function (x, i) { var d = Math.abs(x - vx); if (d < bd) { bd = d; best = i; } });
    cross.style.display = '';
    cross.setAttribute('x1', xs[best]); cross.setAttribute('x2', xs[best]);
    var rows = series.map(function (s) {
      var v = s.vals[best];
      if (v === null || v === undefined) return '';
      var shown = unit === '%' ? v.toFixed(1) + '%' : v.toLocaleString() + ' MWh';
      return '<div class="row"><span class="dot" style="background:var(' + s.var + ')"></span>' +
             s.name + '<b>' + shown + '</b></div>';
    }).join('');
    tip.innerHTML = '<div class="t">' + labels[best] + '</div>' + rows;
    tip.style.display = '';
    var px = (xs[best] / __W__) * r.width;
    tip.style.left = Math.min(px + 12, r.width - tip.offsetWidth - 4) + 'px';
    tip.style.top = '18px';
  });
  svg.addEventListener('mouseleave', function () {
    cross.style.display = 'none'; tip.style.display = 'none';
  });
});
""".replace("__W__", str(W))


def _pts(vals: list, lo: float, hi: float) -> list[tuple[float, float]]:
    out = []
    for i, v in enumerate(vals):
        if v is None:
            continue
        x = PL + IW * i / max(len(vals) - 1, 1)
        y = PT + IH * (1 - (v - lo) / (hi - lo))
        out.append((round(x, 1), round(y, 1)))
    return out


def _gridlines(lo: float, hi: float, is_pct: bool) -> str:
    span = hi - lo
    step = 10 ** math.floor(math.log10(span / 3))
    if span / step > 6:
        step *= 2
    if span / step > 6:
        step *= 2.5
    out, v = [], math.ceil(lo / step) * step
    while v <= hi:
        y = PT + IH * (1 - (v - lo) / (hi - lo))
        label = f"{v:.0f}%" if is_pct else f"{v/1000:.0f}"
        out.append(
            f'<line x1="{PL}" y1="{y:.1f}" x2="{W-PR}" y2="{y:.1f}" class="grid"/>'
            f'<text x="{PL-8}" y="{y:.1f}" class="tick" text-anchor="end" dy="3">{label}</text>'
        )
        v += step
    return "".join(out)


def _chart(chart_id: str, series_defs: list, labels: list[str], unit: str) -> str:
    """One line chart. series_defs: (name, vals, css_var, width, dash, label_dy)."""
    flat = [v for s in series_defs for v in s[1] if v is not None]
    if not flat:
        return '<p class="sub">No data in this window yet.</p>'
    lo, hi = min(flat), max(flat)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    n = len(labels)
    lines, ends = [], []
    for name, vals, var, width, dash, dy in series_defs:
        p = " ".join(f"{x},{y}" for x, y in _pts(vals, lo, hi))
        d = f' stroke-dasharray="{dash}"' if dash else ""
        lines.append(
            f'<polyline points="{p}" fill="none" stroke="var({var})" '
            f'stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"{d}/>'
        )
        pts = _pts(vals, lo, hi)
        if pts:
            x, y = pts[-1]
            ends.append(f'<text x="{x+6}" y="{y+dy}" class="endlabel" '
                        f'fill="var({var})" dy="4">{name}</text>')
    xticks = "".join(
        f'<text x="{PL + IW * i / max(n - 1, 1):.1f}" y="{H-8}" class="tick" '
        f'text-anchor="middle">{labels[i][11:16]}</text>'
        for i in range(0, n, max(n // 6, 1))
    )
    series_json = json.dumps(
        [{"name": s[0], "vals": s[1], "var": s[2]} for s in series_defs]
    )
    xs = [round(PL + IW * i / max(n - 1, 1), 1) for i in range(n)]
    tip_labels = json.dumps([f"{lb[:16]} UTC" for lb in labels])
    return f'''<div class="chartwrap" id="{chart_id}" data-series='{series_json}'
 data-x='{json.dumps(xs)}' data-labels='{tip_labels}' data-unit="{unit}">
<svg viewBox="0 0 {W} {H}" role="img">
  {_gridlines(lo, hi, unit == "%")}
  <line x1="{PL}" y1="{PT+IH}" x2="{W-PR}" y2="{PT+IH}" class="axis"/>
  {xticks}
  {"".join(lines)}{"".join(ends)}
  <line class="crosshair" x1="0" y1="{PT}" x2="0" y2="{PT+IH}" style="display:none"/>
</svg>
<div class="tooltip" style="display:none"></div>
</div>'''


def build_report(conn: sqlite3.Connection) -> str:
    """Assemble the full report HTML from the database."""
    cov = coverage(conn)
    series = latest_series(conn)
    answers = question_answers(conn)
    quality = quality_summary(conn)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    labels = series["periods"]

    duck = _chart("duck", [
        ("Demand", series["ciso_demand"], "--muted", 2, "5 4", -8),
        ("Net load", series["ciso_net"], "--ciso", 2.5, "", 8),
    ], labels, "MWh") if labels else ""
    share = _chart("share", [
        ("CAISO", series["share"].get("CISO", []), "--ciso", 2, "", 0),
        ("ERCOT", series["share"].get("ERCO", []), "--erco", 2, "", 0),
        ("MISO", series["share"].get("MISO", []), "--miso", 2, "", 10),
        ("PJM", series["share"].get("PJM", []), "--pjm", 2, "", -10),
    ], labels, "%") if labels else ""
    demand = _chart("demand", [
        ("PJM", series["demand"].get("PJM", []), "--pjm", 2, "", 0),
        ("MISO", series["demand"].get("MISO", []), "--miso", 2, "", 0),
        ("ERCOT", series["demand"].get("ERCO", []), "--erco", 2, "", 0),
        ("CAISO", series["demand"].get("CISO", []), "--ciso", 2, "", 0),
    ], labels, "MWh") if labels else ""

    q3 = answers["q3"]
    q3_tiles = "" if not q3 else f'''
  <div class="tile"><div class="v">{q3["max_share_pct"]}%</div>
    <div class="k">peak CAISO renewable share</div></div>
  <div class="tile"><div class="v">{q3["min_net_load_mwh"]:,}</div>
    <div class="k">min net load, MWh (the duck's belly)</div></div>
  <div class="tile"><div class="v">+{q3["max_ramp_mwh"]:,}</div>
    <div class="k">steepest hourly ramp, MWh (the duck's neck)</div></div>'''

    q2_rows = "".join(
        f"<tr><td>{REGION_LABELS[r]}</td>"
        f"<td class='mono'>{v['peak_hour_utc'][:16]}</td>"
        f"<td class='num'>{v['peak_mwh']:,}</td>"
        f"<td class='num'>{answers['q1'].get(r, '—')}%</td></tr>"
        for r, v in answers["q2"].items()
    )
    flag_chips = "".join(
        f'<span class="chip"><b>{n}</b> rows flagged {flag}</span>'
        for flag, n in sorted(quality["flags"].items())
    ) or '<span class="chip"><b>0</b> rows flagged</span>'
    ledger_rows = "".join(
        f"<tr><td class='mono'>{r['run_id'][:15]}</td><td>{r['stage']}</td>"
        f"<td class='mono'>{r['git_sha']}</td><td class='num'>{r['rows_received']:,}</td>"
        f"<td class='num'>{r['rows_valid']:,}</td><td class='num'>{r['rows_quarantined']}</td>"
        f"<td class='num'>{r['rows_upserted']:,}</td><td class='num'>{r['api_calls']}</td>"
        f"<td class='num'>{r['runtime_seconds']}s</td></tr>"
        for r in quality["runs"]
    )

    return f'''<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GridPulse</title>
<style>{CSS}</style></head><body>
<div class="wrap">
<header>
  <div class="eyebrow">gridpulse v{__version__} · auto-regenerated · window
  {cov["start"][:16] if cov["start"] else "—"} → {cov["end"][:16] if cov["end"] else "—"} UTC
  · {cov["hours"]} hours held</div>
  <h1>GridPulse</h1>
  <p>Hourly US electricity demand and fuel mix for four major grids, pulled
  from the EIA open-data API through a deterministic, quality-flagged
  conversion pipeline. This page is regenerated on a schedule from the
  pipeline's own database — it never calls the API, and every number traces
  to a stored raw payload.</p>
</header>

<div class="tiles">{q3_tiles}
  <div class="tile"><div class="v ok">{quality["quarantined"]}</div>
    <div class="k">rows quarantined</div></div>
</div>

<div class="card">
  <h2>The California duck curve</h2>
  <p class="sub">CAISO, latest {len(labels)} hours. When solar floods in at
  midday, the load the grid must actually serve (net load) collapses under
  total demand — then ramps hard into the evening. Midday Pacific ≈
  19:00–22:00 UTC. Y-axis in GWh.</p>
  <div class="legend">
    <span><span class="swatch" style="background:var(--muted)"></span>Demand</span>
    <span><span class="swatch" style="background:var(--ciso)"></span>Net load
    (demand − wind − solar)</span>
  </div>
  {duck}
</div>

<div class="card">
  <h2>Renewable share by region</h2>
  <p class="sub">Wind + solar as a share of gross generation, hourly.</p>
  <div class="legend">
    <span><span class="swatch" style="background:var(--ciso)"></span>CAISO</span>
    <span><span class="swatch" style="background:var(--erco)"></span>ERCOT</span>
    <span><span class="swatch" style="background:var(--miso)"></span>MISO</span>
    <span><span class="swatch" style="background:var(--pjm)"></span>PJM</span>
  </div>
  {share}
</div>

<div class="card">
  <h2>Hourly demand, four grids</h2>
  <p class="sub">Same hours, absolute scale — each grid peaks on its own
  local clock. Y-axis in GWh.</p>
  <div class="legend">
    <span><span class="swatch" style="background:var(--pjm)"></span>PJM</span>
    <span><span class="swatch" style="background:var(--miso)"></span>MISO</span>
    <span><span class="swatch" style="background:var(--erco)"></span>ERCOT</span>
    <span><span class="swatch" style="background:var(--ciso)"></span>CAISO</span>
  </div>
  {demand}
</div>

<div class="card">
  <h2>Peaks and averages</h2>
  <p class="sub">Peak observed demand per region, with the average renewable
  share over the full held window.</p>
  <div class="tablewrap"><table>
    <tr><th>Region</th><th>Peak hour (UTC)</th><th class="num-h">Peak MWh</th>
        <th class="num-h">Avg renewable share</th></tr>
    {q2_rows}
  </table></div>
</div>

<div class="card">
  <h2>Quality, on the record</h2>
  <p class="sub">The written null/outlier policy in action — flagged, kept,
  never silently dropped. Quarantined rows are stored with their errors.</p>
  <div class="chips">{flag_chips}
    <span class="chip"><b>{quality["quarantined"]}</b> rows quarantined</span></div>
</div>

<div class="card">
  <h2>Run ledger</h2>
  <p class="sub">Recent pipeline runs, keyed to the git commit that produced
  them — <span class="mono">upserted=0</span> on a replay is idempotency,
  visible.</p>
  <div class="tablewrap"><table>
    <tr><th>run</th><th>stage</th><th>commit</th><th class="num-h">received</th>
        <th class="num-h">valid</th><th class="num-h">quar.</th>
        <th class="num-h">upserted</th><th class="num-h">API</th><th class="num-h">time</th></tr>
    {ledger_rows}
  </table></div>
</div>

<footer>Generated {generated} ·
<a href="https://github.com/evanderpool/gridpulse">source &amp; docs</a> ·
data: US Energy Information Administration (EIA) open-data API v2 ·
built by Erick Vanderpool</footer>
</div>
<script>{JS}</script>
</body></html>'''


def write_report(conn: sqlite3.Connection, out_path: Path) -> Path:
    """Render the report to disk and return the path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_report(conn), encoding="utf-8", newline="\n")
    return out_path
