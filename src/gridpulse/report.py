"""The interactive static report — the product face, compiled from SQLite only.

Architecture: every number is computed at BUILD TIME in analyze.py and
embedded as one JSON blob; the page's JavaScript only selects, formats, and
draws. No backend, no API calls, no external assets — interactivity without
abandoning the zero-infrastructure design. The Streamlit app (app/) is the
same data's *internal tool* face; this page is the product face.

Design rules: theme-aware (light/dark), one axis per chart, region colors
fixed (CAISO blue, ERCOT orange, MISO aqua, PJM yellow), values in text
ink — series color lives on marks only.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .analyze import (
    coverage,
    daily_series,
    hourly_profile,
    hourly_recent,
    preset_findings,
    quality_summary,
)

REGION_LABELS = {"CISO": "CAISO", "ERCO": "ERCOT", "MISO": "MISO", "PJM": "PJM"}

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
.wrap { max-width:880px; margin:0 auto; display:flex; flex-direction:column; gap:1.1rem; }
header h1 { font-size:1.7rem; margin:0; letter-spacing:-0.01em; }
header p { margin:0.35rem 0 0; color:var(--ink2); max-width:70ch; }
.eyebrow { font-size:0.72rem; text-transform:uppercase; letter-spacing:0.12em;
  color:var(--muted); margin-bottom:0.4rem; }
.controls { display:flex; gap:0.5rem; flex-wrap:wrap; align-items:center;
  position:sticky; top:0; background:var(--page); padding:0.6rem 0; z-index:5;
  border-bottom:1px solid var(--grid); }
.rangebtn, .chip { border:1px solid var(--border); background:var(--surface);
  color:var(--ink2); border-radius:999px; padding:5px 14px; font-size:0.82rem;
  cursor:pointer; font-family:inherit; }
.rangebtn.active { background:var(--ink); color:var(--page); border-color:var(--ink); }
.chip { display:inline-flex; align-items:center; gap:7px; }
.chip .dot { width:9px; height:9px; border-radius:50%; }
.chip.off { opacity:0.38; }
.chip:focus-visible, .rangebtn:focus-visible { outline:2px solid var(--ciso); }
.spacer { flex:1; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:0.7rem; }
.tile { background:var(--surface); border:1px solid var(--border); border-radius:6px;
  padding:0.7rem 0.9rem; }
.tile .v { font-size:1.35rem; font-weight:650; line-height:1.15; }
.tile .k { font-size:0.76rem; color:var(--ink2); margin-top:2px; }
.tile .d { font-size:0.76rem; margin-top:2px; font-variant-numeric:tabular-nums; }
.up { color:var(--goodtext); } .down { color:#c0392b; }
.card { background:var(--surface); border:1px solid var(--border); border-radius:6px;
  padding:1rem 1.1rem 0.7rem; }
.card h2 { font-size:1.02rem; margin:0; }
.card .sub { font-size:0.82rem; color:var(--ink2); margin:0.15rem 0 0.5rem; }
.findings li { margin:0.4rem 0; font-size:0.92rem; }
.findings b { font-variant-numeric:tabular-nums; }
select { background:var(--surface); color:var(--ink); border:1px solid var(--border);
  border-radius:5px; padding:4px 8px; font:inherit; font-size:0.84rem; }
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
.qchip { border:1px solid var(--border); background:var(--surface); border-radius:999px;
  padding:4px 12px; font-size:0.8rem; color:var(--ink2); }
.qchip b { color:var(--ink); font-variant-numeric:tabular-nums; }
.dl { border:1px solid var(--border); background:var(--surface); color:var(--ink2);
  border-radius:5px; padding:5px 12px; font:inherit; font-size:0.8rem; cursor:pointer; }
footer { font-size:0.78rem; color:var(--muted); }
footer a { color:var(--ink2); }
"""

# The client app. Kept as one plain string — no templating beyond __DATA__.
JS = r"""
const D = __DATA__;
const REGIONS = ["CISO","ERCO","MISO","PJM"];
const NAMES = {CISO:"CAISO", ERCO:"ERCOT", MISO:"MISO", PJM:"PJM"};
const VARS = {CISO:"--ciso", ERCO:"--erco", MISO:"--miso", PJM:"--pjm"};
const PRESET_LABEL = {"7d":"last 7 days","30d":"last 30 days","90d":"last 90 days",
  "6m":"last 6 months","12m":"last 12 months","all":"full history"};
let preset = "90d";
let active = new Set(REGIONS.filter(r => (D.findings[preset].regions||{})[r]));

const W=760, H=240, PL=56, PR=96, PT=14, PB=26, IW=W-PL-PR, IH=H-PT-PB;

function fmtGW(mwh){ return (mwh/1000).toLocaleString(undefined,{maximumFractionDigits:1}); }
function esc(s){ return String(s).replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

function gridSteps(lo, hi){
  const span = hi-lo || 1;
  let step = Math.pow(10, Math.floor(Math.log10(span/3)));
  if (span/step > 6) step *= 2;
  if (span/step > 6) step *= 2.5;
  const out = [];
  for (let v = Math.ceil(lo/step)*step; v <= hi; v += step) out.push(v);
  return out;
}

// One chart renderer for every line chart on the page.
// series: [{name, vals:[null|num], cssvar, width, dash}], labels: x labels.
function drawChart(mount, series, labels, unit){
  const flat = series.flatMap(s => s.vals).filter(v => v != null);
  if (!flat.length){ mount.innerHTML = '<p class="sub">No data in this window.</p>'; return; }
  let lo = Math.min(...flat), hi = Math.max(...flat);
  const pad = (hi-lo)*0.08 || 1; lo -= pad; hi += pad;
  const n = labels.length;
  const X = i => PL + IW*i/Math.max(n-1,1);
  const Y = v => PT + IH*(1-(v-lo)/(hi-lo));
  let g = "";
  for (const v of gridSteps(lo,hi)){
    const y = Y(v).toFixed(1);
    const lab = unit === "%" ? Math.round(v)+"%" : Math.round(v/1000);
    g += `<line x1="${PL}" y1="${y}" x2="${W-PR}" y2="${y}" class="grid"/>` +
         `<text x="${PL-8}" y="${y}" class="tick" text-anchor="end" dy="3">${lab}</text>`;
  }
  const tickEvery = Math.max(Math.floor(n/6), 1);
  let xt = "";
  for (let i = 0; i < n; i += tickEvery){
    xt += `<text x="${X(i).toFixed(1)}" y="${H-8}" class="tick" text-anchor="middle">` +
          esc(labels[i]) + `</text>`;
  }
  let lines = "", ends = "";
  for (const s of series){
    let pts = "", last = null;
    s.vals.forEach((v,i) => { if (v != null){ pts += `${X(i).toFixed(1)},${Y(v).toFixed(1)} `;
      last = [X(i), Y(v)]; }});
    const dash = s.dash ? ` stroke-dasharray="${s.dash}"` : "";
    lines += `<polyline points="${pts.trim()}" fill="none" stroke="var(${s.cssvar})"` +
      ` stroke-width="${s.width||2}" stroke-linejoin="round" stroke-linecap="round"${dash}/>`;
    if (last) ends += `<text x="${(last[0]+6).toFixed(1)}" y="${(last[1]+(s.dy||0)).toFixed(1)}"` +
      ` class="endlabel" fill="var(${s.cssvar})" dy="4">${esc(s.name)}</text>`;
  }
  mount.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" role="img">${g}` +
    `<line x1="${PL}" y1="${PT+IH}" x2="${W-PR}" y2="${PT+IH}" class="axis"/>` +
    `${xt}${lines}${ends}` +
    `<line class="crosshair" x1="0" y1="${PT}" x2="0" y2="${PT+IH}" style="display:none"/></svg>` +
    `<div class="tooltip" style="display:none"></div>`;
  const svg = mount.querySelector("svg"), cross = mount.querySelector(".crosshair"),
        tip = mount.querySelector(".tooltip");
  svg.addEventListener("mousemove", ev => {
    const r = svg.getBoundingClientRect();
    const vx = (ev.clientX - r.left) * (W / r.width);
    let best = 0, bd = 1e9;
    for (let i = 0; i < n; i++){ const d = Math.abs(X(i)-vx); if (d < bd){ bd = d; best = i; } }
    cross.style.display = ""; cross.setAttribute("x1", X(best)); cross.setAttribute("x2", X(best));
    let rows = "";
    for (const s of series){
      const v = s.vals[best];
      if (v == null) continue;
      const shown = unit === "%" ? v.toFixed(1)+"%" : Math.round(v).toLocaleString()+" MWh";
      rows += `<div class="row"><span class="dot" style="background:var(${s.cssvar})"></span>` +
              `${esc(s.name)}<b>${shown}</b></div>`;
    }
    tip.innerHTML = `<div class="t">${esc(labels[best])}</div>` + rows;
    tip.style.display = "";
    const px = (X(best)/W) * r.width;
    tip.style.left = Math.min(px+12, r.width - tip.offsetWidth - 4) + "px";
    tip.style.top = "18px";
  });
  svg.addEventListener("mouseleave", () => { cross.style.display = "none"; tip.style.display = "none"; });
}

function dir(v){ return v >= 0 ? "up" : "down"; }
function cls(v){ return v >= 0 ? "up" : "down"; }

function renderFindings(){
  const f = D.findings[preset] || {regions:{}};
  const regs = REGIONS.filter(r => active.has(r) && f.regions[r]);
  let tiles = "", sentences = "";
  for (const r of regs){
    const s = f.regions[r], name = NAMES[r];
    const dPrev = s.demand_vs_prev_pct, dYoy = s.demand_vs_yoy_pct;
    tiles += `<div class="tile"><div class="v">${fmtGW(s.avg_demand)} GWh/h</div>` +
      `<div class="k">${name} avg demand</div>` +
      (dPrev == null ? "" : `<div class="d ${cls(dPrev)}">${dPrev>=0?"▲":"▼"} ` +
        `${Math.abs(dPrev).toFixed(1)}% vs prior window</div>`) + `</div>`;
    if (s.avg_share != null){
      const sPrev = s.share_vs_prev_pts;
      tiles += `<div class="tile"><div class="v">${s.avg_share.toFixed(1)}%</div>` +
        `<div class="k">${name} renewable share</div>` +
        (sPrev == null ? "" : `<div class="d ${cls(sPrev)}">${sPrev>=0?"▲":"▼"} ` +
          `${Math.abs(sPrev).toFixed(1)} pts vs prior</div>`) + `</div>`;
    }
    let bits = [`<b>${name}</b> averaged <b>${fmtGW(s.avg_demand)} GWh/h</b> of demand`];
    if (dPrev != null) bits.push(`${dir(dPrev)} <b>${Math.abs(dPrev).toFixed(1)}%</b> vs the prior window`);
    if (dYoy != null) bits.push(`${dir(dYoy)} <b>${Math.abs(dYoy).toFixed(1)}%</b> vs the same window last year`);
    sentences += `<li>${bits.join("; ")}.</li>`;
    if (s.avg_share != null){
      let sb = [`<b>${name}</b> renewables covered <b>${s.avg_share.toFixed(1)}%</b> of generation`];
      if (s.share_vs_prev_pts != null)
        sb.push(`${dir(s.share_vs_prev_pts)} <b>${Math.abs(s.share_vs_prev_pts).toFixed(1)} pts</b> vs the prior window`);
      if (s.share_vs_yoy_pts != null)
        sb.push(`${dir(s.share_vs_yoy_pts)} <b>${Math.abs(s.share_vs_yoy_pts).toFixed(1)} pts</b> year over year`);
      sentences += `<li>${sb.join("; ")}.</li>`;
    }
    if (s.peak) sentences += `<li><b>${name}</b> peaked at <b>${fmtGW(s.peak[1])} GWh</b> on ` +
      `${esc(s.peak[0].slice(0,13))}:00 UTC.</li>`;
    if (s.belly) sentences += `<li><b>${name}</b> net load bottomed at ` +
      `<b>${fmtGW(s.belly[1])} GWh</b> (${esc(s.belly[0].slice(0,13))}:00 UTC)` +
      (s.max_ramp != null ? `; steepest ramp <b>+${fmtGW(s.max_ramp)} GWh/h</b>.` : ".") + `</li>`;
  }
  document.getElementById("tiles").innerHTML = tiles ||
    '<div class="tile"><div class="v">—</div><div class="k">no data in window</div></div>';
  document.getElementById("sentences").innerHTML = sentences || "<li>No data in this window.</li>";
  document.getElementById("findwindow").textContent = PRESET_LABEL[preset];
}

function windowedDaily(region){
  const f = D.findings[preset];
  const start = f && f.start ? f.start.slice(0,10) : "";
  return (D.daily[region]||[]).filter(row => !start || row[0] >= start);
}

function renderTrends(){
  const regs = REGIONS.filter(r => active.has(r) && (D.daily[r]||[]).length);
  if (!regs.length) return;
  const days = [...new Set(regs.flatMap(r => windowedDaily(r).map(x => x[0])))].sort();
  const idx = new Map(days.map((d,i) => [d,i]));
  const mk = pos => regs.map(r => {
    const vals = Array(days.length).fill(null);
    windowedDaily(r).forEach(row => { if (row[pos] != null) vals[idx.get(row[0])] = row[pos]; });
    return {name:NAMES[r], vals, cssvar:VARS[r]};
  });
  const labels = days.map(d => d.slice(5));
  drawChart(document.getElementById("trend-demand"), mk(1), labels, "MWh");
  drawChart(document.getElementById("trend-share"), mk(2), labels, "%");
}

function renderProfile(){
  const prof = D.profile[preset] || {};
  const regs = REGIONS.filter(r => active.has(r) && prof[r]);
  const labels = Array.from({length:24}, (_,h) => String(h).padStart(2,"0")+":00");
  drawChart(document.getElementById("profile"),
    regs.map(r => ({name:NAMES[r], vals:prof[r], cssvar:VARS[r]})), labels, "MWh");
}

function duckDays(region){
  return [...new Set((D.hourly[region]||[]).map(row => row[0].slice(0,10)))];
}

function renderDuck(){
  const region = document.getElementById("duck-region").value;
  const daySel = document.getElementById("duck-day");
  const days = duckDays(region);
  if (daySel.dataset.region !== region){
    daySel.innerHTML = days.map(d => `<option>${d}</option>`).join("");
    daySel.value = days[days.length-1] || "";
    daySel.dataset.region = region;
  }
  const day = daySel.value;
  const rows = (D.hourly[region]||[]).filter(r => r[0].slice(0,10) === day);
  const labels = rows.map(r => r[0].slice(11)+":00");
  drawChart(document.getElementById("duck"), [
    {name:"Demand", vals:rows.map(r => r[1]), cssvar:"--muted", dash:"5 4", dy:-8},
    {name:"Net load", vals:rows.map(r => r[2]), cssvar:VARS[region], width:2.5, dy:8},
  ], labels, "MWh");
}

function download(name, text){
  const a = document.createElement("a");
  a.href = "data:text/csv;charset=utf-8," + encodeURIComponent(text);
  a.download = name; a.click();
}

function exportDaily(){
  let csv = "region,day,avg_demand_mwh,avg_renewable_share_pct\n";
  for (const r of REGIONS) for (const row of windowedDaily(r))
    csv += `${r},${row[0]},${row[1]},${row[2] ?? ""}\n`;
  download(`gridpulse_daily_${preset}.csv`, csv);
}

function exportHourly(){
  let csv = "region,hour_utc,demand_mwh,net_load_mwh\n";
  for (const r of REGIONS) for (const row of (D.hourly[r]||[]))
    csv += `${r},${row[0]},${row[1]},${row[2] ?? ""}\n`;
  download("gridpulse_hourly_30d.csv", csv);
}

function renderAll(){ renderFindings(); renderTrends(); renderProfile(); renderDuck(); }

document.querySelectorAll(".rangebtn").forEach(b => b.addEventListener("click", () => {
  preset = b.dataset.preset;
  document.querySelectorAll(".rangebtn").forEach(x =>
    x.classList.toggle("active", x === b));
  renderAll();
}));
document.querySelectorAll(".chip").forEach(c => c.addEventListener("click", () => {
  const r = c.dataset.region;
  if (active.has(r) && active.size > 1) active.delete(r); else active.add(r);
  c.classList.toggle("off", !active.has(r));
  renderFindings(); renderTrends(); renderProfile();
}));
document.getElementById("duck-region").addEventListener("change", renderDuck);
document.getElementById("duck-day").addEventListener("change", renderDuck);
document.getElementById("dl-daily").addEventListener("click", exportDaily);
document.getElementById("dl-hourly").addEventListener("click", exportHourly);
renderAll();
"""


def build_report(conn: sqlite3.Connection) -> str:
    """Assemble the interactive report: build-time analysis, client rendering."""
    cov = coverage(conn)
    quality = quality_summary(conn)
    payload = {
        "findings": preset_findings(conn),
        "daily": daily_series(conn),
        "hourly": hourly_recent(conn),
        "profile": hourly_profile(conn),
    }
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    range_buttons = "".join(
        f'<button class="rangebtn{" active" if key == "90d" else ""}" '
        f'data-preset="{key}">{label}</button>'
        for key, label in [("7d", "7 days"), ("30d", "30 days"), ("90d", "90 days"),
                           ("6m", "6 months"), ("12m", "12 months"), ("all", "All")]
    )
    region_chips = "".join(
        f'<button class="chip" data-region="{r}">'
        f'<span class="dot" style="background:var(--{r.lower()})"></span>{label}</button>'
        for r, label in REGION_LABELS.items()
    )
    duck_options = "".join(
        f'<option value="{r}">{label}</option>' for r, label in REGION_LABELS.items()
    )
    flag_chips = "".join(
        f'<span class="qchip"><b>{n}</b> rows flagged {flag}</span>'
        for flag, n in sorted(quality["flags"].items())
    ) or '<span class="qchip"><b>0</b> rows flagged</span>'
    ledger_rows = "".join(
        f"<tr><td class='mono'>{r['run_id'][:15]}</td><td>{r['stage']}</td>"
        f"<td class='mono'>{r['git_sha']}</td><td class='num'>{r['rows_received']:,}</td>"
        f"<td class='num'>{r['rows_valid']:,}</td><td class='num'>{r['rows_quarantined']}</td>"
        f"<td class='num'>{r['rows_upserted']:,}</td><td class='num'>{r['api_calls']}</td>"
        f"<td class='num'>{r['runtime_seconds']}s</td></tr>"
        for r in quality["runs"]
    )
    data_json = json.dumps(payload, separators=(",", ":"))

    return f'''<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GridPulse</title>
<style>{CSS}</style></head><body>
<div class="wrap">
<header>
  <div class="eyebrow">gridpulse v{__version__} · auto-regenerated · coverage
  {cov["start"][:10] if cov["start"] else "—"} → {cov["end"][:16] if cov["end"] else "—"} UTC
  · {cov["hours"]:,} hours held</div>
  <h1>GridPulse</h1>
  <p>Hourly US electricity demand and fuel mix for four major grids, through a
  deterministic, quality-flagged pipeline. Pick a time frame — every figure,
  finding, and chart below recomputes. All analysis is compiled into this page
  at build time from the pipeline's own database: no backend, no API calls,
  and every number traces to a stored raw payload.</p>
</header>

<div class="controls">
  {range_buttons}
  <span class="spacer"></span>
  {region_chips}
</div>

<div class="card">
  <h2>Findings — <span id="findwindow">last 90 days</span></h2>
  <p class="sub">Computed arithmetic over the gold tables: each window is
  compared against the preceding window of equal length and the same window
  one year earlier. No model, no estimation.</p>
  <div class="tiles" id="tiles"></div>
  <ul class="findings" id="sentences"></ul>
  <div class="chips">
    <button class="dl" id="dl-daily">⬇ Daily averages (CSV, this window)</button>
    <button class="dl" id="dl-hourly">⬇ Hourly detail (CSV, last 30 days)</button>
  </div>
</div>

<div class="card">
  <h2>Demand trend</h2>
  <p class="sub">Daily average demand per region over the selected window.
  Y-axis in GWh.</p>
  <div class="chartwrap" id="trend-demand"></div>
</div>

<div class="card">
  <h2>Renewable share trend</h2>
  <p class="sub">Wind + solar as a share of gross generation, daily average.</p>
  <div class="chartwrap" id="trend-share"></div>
</div>

<div class="card">
  <h2>The duck curve, any recent day</h2>
  <p class="sub">Demand vs net load (demand − wind − solar). Pick a region and
  a day from the last 30. Midday Pacific ≈ 19:00–22:00 UTC. Y-axis in GWh.</p>
  <div class="chips" style="margin-bottom:0.5rem">
    <select id="duck-region">{duck_options}</select>
    <select id="duck-day"></select>
  </div>
  <div class="chartwrap" id="duck"></div>
</div>

<div class="card">
  <h2>Daily profile — when each grid peaks</h2>
  <p class="sub">Average demand by hour of day (UTC) over the selected window.
  Change the window to watch the shape shift with the seasons. Y-axis in GWh.</p>
  <div class="chartwrap" id="profile"></div>
</div>

<div class="card">
  <h2>Quality, on the record</h2>
  <p class="sub">The written null/outlier policy in action — flagged, kept,
  never silently dropped. Quarantined rows are stored with their errors.</p>
  <div class="chips">{flag_chips}
    <span class="qchip"><b>{quality["quarantined"]}</b> rows quarantined</span></div>
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
<script>{JS.replace("__DATA__", data_json)}</script>
</body></html>'''


def write_report(conn: sqlite3.Connection, out_path: Path) -> Path:
    """Render the report to disk and return the path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_report(conn), encoding="utf-8", newline="\n")
    return out_path
