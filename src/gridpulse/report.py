"""The interactive static report — the product face, compiled from SQLite only.

Architecture: every number is computed at BUILD TIME in analyze.py and
embedded as one JSON blob; the page's JavaScript only selects, formats, and
draws. No backend, no API calls, no external assets.

UX model (operator-directed): a top-nav single-page dashboard — one focused
view per section, Title Case labels, soft rounded "bubbly corporate"
styling, NO animations (operator rule: hover feedback is instant, nothing
moves on its own), region hover cards everywhere a region appears,
clickable KPI tiles, an Ask the Analyst tab with a real grounded sample
exchange, and a Case Study view written for recruiters.
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
from .mdlite import render as render_md

REGION_LABELS = {"CISO": "California", "ERCO": "Texas",
                 "MISO": "Midwest", "PJM": "Mid-Atlantic"}

# Set when the companion Streamlit app has its public URL; the Ask view then
# gains a launch button and live embed.
STREAMLIT_URL = ""

CSS = """
:root { color-scheme: light;
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,0.10);
  --ciso:#2a78d6; --erco:#eb6834; --miso:#1baf7a; --pjm:#eda100;
  --goodtext:#006300; --accent:#2a78d6; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
  --ciso:#3987e5; --erco:#d95926; --miso:#199e70; --pjm:#c98500;
  --goodtext:#0ca30c; --accent:#3987e5; } }
:root[data-theme="dark"] {
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
  --ciso:#3987e5; --erco:#d95926; --miso:#199e70; --pjm:#c98500;
  --goodtext:#0ca30c; --accent:#3987e5; }
* { box-sizing: border-box; }
body { margin:0; background:var(--page); color:var(--ink);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; }

/* ---- Top Bar ---- */
.topbar { position:sticky; top:0; z-index:20; background:var(--page);
  border-bottom:1px solid var(--grid); }
.topbar-inner { max-width:1000px; margin:0 auto; padding:0.55rem 1rem;
  display:flex; align-items:center; gap:0.4rem; flex-wrap:wrap; }
.brand { font-weight:750; font-size:1.05rem; letter-spacing:-0.01em;
  margin-right:0.5rem; white-space:nowrap; }
.brand .bolt { color:var(--erco); }
nav { display:flex; gap:0.1rem; flex-wrap:wrap; }
.navlink { border:0; background:none; color:var(--ink2); font:inherit;
  font-size:0.85rem; padding:6px 10px; border-radius:6px; cursor:pointer;
  position:relative; }
.navlink:hover { background:var(--surface); }
.navlink.active { color:var(--ink); font-weight:650; }
.navlink.active::after { content:""; position:absolute; left:10px; right:10px;
  bottom:-1px; height:2px; background:var(--accent); border-radius:2px; }
.helpbtn { margin-left:auto; border:1px solid var(--border); background:var(--surface);
  color:var(--ink2); width:28px; height:28px; border-radius:50%; cursor:pointer;
  font-size:0.85rem; font-weight:700; }
.subbar { border-top:1px solid var(--grid); }
.subbar-inner { max-width:1000px; margin:0 auto; padding:0.45rem 1rem;
  display:flex; align-items:center; gap:0.35rem; flex-wrap:wrap; }
.sublabel { font-size:0.68rem; text-transform:uppercase; letter-spacing:0.11em;
  color:var(--muted); margin-right:0.2rem; }
.rangebtn { border:1px solid var(--border); background:var(--surface); color:var(--ink2);
  border-radius:999px; padding:4px 13px; font-size:0.78rem; cursor:pointer;
  font-family:inherit; }
.rangebtn:hover { border-color:var(--axis); color:var(--ink); }
.rangebtn.active { background:var(--ink); color:var(--page); border-color:var(--ink); }
.chip { border:1px solid var(--border); background:var(--surface); color:var(--ink2);
  border-radius:999px; padding:4px 13px; font-size:0.78rem; cursor:pointer;
  font-family:inherit; display:inline-flex; align-items:center; gap:6px; }
.chip:hover { border-color:var(--axis); color:var(--ink); }
.chip .dot { width:8px; height:8px; border-radius:50%; }
.chip.off { opacity:0.35; }

/* ---- Views ---- */
.wrap { max-width:1000px; margin:0 auto; padding:1.5rem 1rem 4rem;
  display:flex; flex-direction:column; gap:1rem; }
.view { display:none; flex-direction:column; gap:1rem; }
.view.active { display:flex; }
.viewhead h1 { font-size:1.5rem; margin:0 0 0.25rem; letter-spacing:-0.015em; }
.viewhead p { margin:0; color:var(--ink2); max-width:72ch; font-size:0.92rem; }
.card { background:var(--surface); border:1px solid var(--border); border-radius:18px;
  padding:1.15rem 1.3rem 0.95rem;
  box-shadow:0 1px 2px rgba(0,0,0,0.04), 0 10px 28px -22px rgba(0,0,0,0.28); }
.card:hover { box-shadow:0 2px 4px rgba(0,0,0,0.05), 0 16px 36px -20px rgba(0,0,0,0.32); }
.card h2 { font-size:0.98rem; margin:0 0 0.15rem; }
.card .sub { font-size:0.8rem; color:var(--ink2); margin:0 0 0.5rem; }
.hero { font-size:1.12rem; line-height:1.55; max-width:62ch; }
.hero b { font-variant-numeric:tabular-nums; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:0.8rem; }
.tile { background:var(--surface); border:1px solid var(--border); border-radius:18px;
  padding:0.9rem 1.05rem 0.8rem; border-top:4px solid var(--tilecolor, var(--axis));
  cursor:pointer; position:relative;
  box-shadow:0 1px 2px rgba(0,0,0,0.04), 0 10px 28px -22px rgba(0,0,0,0.28); }
.tile { background:color-mix(in srgb, var(--tilecolor, var(--axis)) 6%, var(--surface)); }
.tile:hover { border-color:var(--tilecolor);
  box-shadow:0 2px 5px rgba(0,0,0,0.06), 0 18px 38px -20px rgba(0,0,0,0.35); }
.tile .go { position:absolute; top:0.7rem; right:0.9rem; color:var(--tilecolor);
  font-weight:700; opacity:0; font-size:0.95rem; }
.tile:hover .go { opacity:1; }
.tile .v { font-size:1.55rem; font-weight:700; line-height:1.1;
  font-variant-numeric:tabular-nums; letter-spacing:-0.01em; }
.tile .v small { font-size:0.85rem; font-weight:600; color:var(--ink2); }
.tile .k { font-size:0.74rem; color:var(--ink2); margin-top:3px; }
.tile .d { font-size:0.74rem; margin-top:4px; font-variant-numeric:tabular-nums;
  font-weight:600; }
.hovercard { position:fixed; z-index:60; width:270px; pointer-events:none;
  display:none; background:var(--surface); border:1px solid var(--border);
  border-radius:14px; padding:0.85rem 1rem;
  box-shadow:0 6px 16px rgba(0,0,0,0.10), 0 24px 48px -24px rgba(0,0,0,0.35); }
.hovercard .hc-title { font-weight:700; display:flex; align-items:center; gap:8px;
  margin-bottom:0.35rem; }
.hovercard .hc-row { display:flex; justify-content:space-between; gap:10px;
  font-size:0.78rem; color:var(--ink2); padding:2px 0; }
.hovercard .hc-row b { color:var(--ink); font-variant-numeric:tabular-nums;
  text-align:right; }
.up { color:var(--goodtext); } .down { color:#c0392b; }
.findings { margin:0.2rem 0 0.4rem; padding-left:1.15rem; }
.findings li { margin:0.45rem 0; font-size:0.9rem; max-width:78ch; }
.findings b { font-variant-numeric:tabular-nums; }
select { background:var(--surface); color:var(--ink); border:1px solid var(--border);
  border-radius:6px; padding:4px 9px; font:inherit; font-size:0.84rem; }
.chartwrap { position:relative; }
svg { width:100%; height:auto; display:block; }
.grid { stroke:var(--grid); stroke-width:1; }
.axis { stroke:var(--axis); stroke-width:1; }
.tick { fill:var(--muted); font-size:10.5px; font-variant-numeric:tabular-nums; }
.endlabel { font-size:11px; font-weight:600; }
.crosshair { stroke:var(--axis); stroke-width:1; stroke-dasharray:3 3; }
.tooltip { position:absolute; pointer-events:none; background:var(--surface);
  border:1px solid var(--border); border-radius:6px; padding:6px 9px;
  font-size:0.76rem; box-shadow:0 3px 14px rgba(0,0,0,0.14); white-space:nowrap; }
.tooltip .t { color:var(--muted); margin-bottom:2px; }
.tooltip .row { display:flex; align-items:center; gap:6px; }
.tooltip .row b { font-variant-numeric:tabular-nums; font-weight:600;
  margin-left:auto; padding-left:10px; }
.dot { width:8px; height:8px; border-radius:50%; display:inline-block; }
table { border-collapse:collapse; width:100%; font-size:0.79rem; }
th { text-align:left; color:var(--muted); font-weight:600; font-size:0.68rem;
  text-transform:uppercase; letter-spacing:0.07em; padding:4px 10px 4px 0;
  border-bottom:1px solid var(--axis); }
td { padding:5px 10px 5px 0; border-bottom:1px solid var(--grid); }
.num { text-align:right; font-variant-numeric:tabular-nums; }
th.num-h { text-align:right; }
.mono { font-family:Consolas,ui-monospace,monospace; font-size:0.75rem; }
.tablewrap { overflow-x:auto; }
.chips { display:flex; gap:0.6rem; flex-wrap:wrap; }
.qchip { border:1px solid var(--border); background:var(--surface); border-radius:999px;
  padding:4px 12px; font-size:0.8rem; color:var(--ink2); }
.qchip b { color:var(--ink); font-variant-numeric:tabular-nums; }
.dl, .cta { border:1px solid var(--border); background:var(--surface); color:var(--ink2);
  border-radius:6px; padding:6px 13px; font:inherit; font-size:0.82rem; cursor:pointer;
  text-decoration:none; display:inline-block; }
.cta.primary { background:var(--ink); color:var(--page); border-color:var(--ink);
  font-weight:600; }
.qa { border-left:3px solid var(--accent); padding:0.2rem 0 0.2rem 1rem;
  margin:0.6rem 0; }
.qa .q { font-weight:650; margin-bottom:0.4rem; }
.qa .a { font-size:0.9rem; max-width:74ch; }
.sql { background:var(--page); border:1px solid var(--grid); border-radius:6px;
  padding:0.5rem 0.8rem; font-family:Consolas,ui-monospace,monospace;
  font-size:0.74rem; overflow-x:auto; margin:0.4rem 0; white-space:pre; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
  gap:0.7rem; margin:0.4rem 0 0.6rem; }
.stat .n { font-size:1.35rem; font-weight:700; font-variant-numeric:tabular-nums; }
.stat .l { font-size:0.72rem; color:var(--ink2); }
footer { font-size:0.76rem; color:var(--muted); max-width:1000px;
  margin:0 auto; padding:0 1rem 2.5rem; }
footer a { color:var(--ink2); }

.mdbody { line-height:1.6; }
.mdbody h1 { font-size:1.45rem; margin:0.2rem 0 0.6rem; letter-spacing:-0.015em; }
.mdbody h2 { font-size:1.05rem; margin:1.5rem 0 0.4rem; }
.mdbody h3 { font-size:0.92rem; margin:1.1rem 0 0.3rem; }
.mdbody p { margin:0.55rem 0; max-width:78ch; font-size:0.92rem; }
.mdbody ul { padding-left:1.2rem; margin:0.5rem 0; }
.mdbody li { margin:0.4rem 0; font-size:0.9rem; max-width:76ch; }
.mdbody pre { background:var(--page); border:1px solid var(--grid); border-radius:12px;
  padding:0.8rem 1rem; font-family:Consolas,ui-monospace,monospace; font-size:0.74rem;
  line-height:1.45; overflow-x:auto; }
.mdbody code { background:var(--page); border:1px solid var(--grid); border-radius:5px;
  padding:0.05em 0.35em; font-family:Consolas,ui-monospace,monospace; font-size:0.82em; }
.mdbody pre code { border:0; padding:0; }
.mdbody blockquote { border-left:3px solid var(--accent); margin:0.7rem 0;
  padding:0.3rem 0 0.3rem 1rem; color:var(--ink2); font-size:0.92rem; }
.mdbody a { color:var(--accent); }
.mdbody hr { border:0; border-top:1px solid var(--grid); margin:1.2rem 0; }
.mdbody table { margin:0.5rem 0; }
.mdbody .tablewrap:first-of-type th { font-size:1.35rem; color:var(--ink);
  text-transform:none; letter-spacing:-0.01em; border-bottom:0; padding-right:1.6rem; }
.mdbody .tablewrap:first-of-type td { color:var(--ink2); font-size:0.78rem;
  border-bottom:0; padding-right:1.6rem; }

/* ---- Glossary Overlay ---- */
.overlay { position:fixed; inset:0; background:rgba(0,0,0,0.45); z-index:40;
  display:none; align-items:flex-start; justify-content:center; padding:6vh 1rem; }
.overlay.open { display:flex; }
.panel { background:var(--surface); color:var(--ink); border-radius:12px;
  max-width:640px; width:100%; max-height:82vh; overflow-y:auto;
  padding:1.2rem 1.4rem; border:1px solid var(--border); }
.panel h2 { margin:0 0 0.4rem; font-size:1.05rem; }
.panel p, .panel td { font-size:0.88rem; }
.panel .close { float:right; border:0; background:none; color:var(--muted);
  font-size:1.1rem; cursor:pointer; }
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior:auto; }
}
"""

JS = r"""
const D = __DATA__;
const REGIONS = ["CISO","ERCO","MISO","PJM"];
const NAMES = {CISO:"California", ERCO:"Texas", MISO:"Midwest", PJM:"Mid-Atlantic"};
const VARS = {CISO:"--ciso", ERCO:"--erco", MISO:"--miso", PJM:"--pjm"};
const PRESET_LABEL = {"7d":"the Last 7 Days","30d":"the Last 30 Days","90d":"the Last 90 Days",
  "6m":"the Last 6 Months","12m":"the Last 12 Months","all":"Everything on Record"};
let preset = "90d";
let active = new Set(REGIONS.filter(r => (D.findings[preset].regions||{})[r]));


const W=760, H=240, PL=56, PR=100, PT=14, PB=26, IW=W-PL-PR, IH=H-PT-PB;
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
    const dash = s.dash ? ` data-dash="${s.dash}"` : "";
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
  mount.querySelectorAll("polyline[data-dash]").forEach(pl => {
    pl.style.strokeDasharray = pl.dataset.dash.replace(" ", ",");
  });
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
  svg.addEventListener("mouseleave", () => { cross.style.display="none"; tip.style.display="none"; });
}

function dir(v){ return v >= 0 ? "up" : "down"; }
function cls(v){ return v >= 0 ? "up" : "down"; }

function buildFindings(){
  const f = D.findings[preset] || {regions:{}};
  const regs = REGIONS.filter(r => active.has(r) && f.regions[r]);
  const out = {tiles:"", usage:[], share:[], duck:[]};
  for (const r of regs){
    const s = f.regions[r], name = NAMES[r];
    const dPrev = s.demand_vs_prev_pct, dYoy = s.demand_vs_yoy_pct;
    out.tiles += `<div class="tile" data-region="${r}" title="View ${name} in Usage" ` +
      `style="--tilecolor:var(${VARS[r]})"><span class="go">→</span>` +
      `<div class="v">${fmtGW(s.avg_demand)} <small>GWh</small></div>` +
      `<div class="k">${name} — Average Hourly Use</div>` +
      (dPrev == null ? "" : `<div class="d ${cls(dPrev)}">${dPrev>=0?"▲":"▼"} ` +
        `${Math.abs(dPrev).toFixed(1)}% vs the window before</div>`) + `</div>`;
    let bits = [`<b>${name}</b> used an average of <b>${fmtGW(s.avg_demand)} GWh</b> each hour`];
    if (dPrev != null) bits.push(`${dir(dPrev)} <b>${Math.abs(dPrev).toFixed(1)}%</b> vs the window before`);
    if (dYoy != null) bits.push(`${dir(dYoy)} <b>${Math.abs(dYoy).toFixed(1)}%</b> vs a year ago`);
    out.usage.push(bits.join("; ") + ".");
    if (s.peak) out.usage.push(`<b>${name}</b>'s busiest hour: <b>${fmtGW(s.peak[1])} GWh</b> on ` +
      `${esc(s.peak[0].slice(0,10))} at ${esc(s.peak[0].slice(11,13))}:00 UTC — roughly enough for ` +
      `<b>${Math.round(s.peak[1]/1.3/1000)} million homes</b> that hour.`);
    if (s.avg_share != null){
      let sb = [`Wind and solar supplied <b>${s.avg_share.toFixed(1)}%</b> of <b>${name}</b>'s electricity`];
      if (s.share_vs_prev_pts != null)
        sb.push(`${dir(s.share_vs_prev_pts)} <b>${Math.abs(s.share_vs_prev_pts).toFixed(1)} pts</b> vs the window before`);
      if (s.share_vs_yoy_pts != null)
        sb.push(`${dir(s.share_vs_yoy_pts)} <b>${Math.abs(s.share_vs_yoy_pts).toFixed(1)} pts</b> vs a year ago`);
      out.share.push(sb.join("; ") + ".");
    }
    if (s.belly) out.duck.push(`Solar pushed <b>${name}</b>'s power plants down to ` +
      `<b>${fmtGW(s.belly[1])} GWh</b> at their quietest (${esc(s.belly[0].slice(0,10))} ` +
      `${esc(s.belly[0].slice(11,13))}:00 UTC)` +
      (s.max_ramp != null ? `; after sunset they ramped back up by as much as ` +
        `<b>+${fmtGW(s.max_ramp)} GWh in one hour</b>.` : "."));
  }
  return out;
}

function fillList(id, items, max){
  const el = document.getElementById(id);
  if (!el) return;
  if (!items.length){ el.innerHTML = "<li>No data in this window.</li>"; return; }
  el.innerHTML = (max ? items.slice(0, max) : items).map(s => `<li>${s}</li>`).join("");
}

function heroSentence(){
  const f = D.findings["12m"] || D.findings[preset];
  const tx = f.regions && f.regions.ERCO;
  if (tx && tx.share_vs_yoy_pts != null && tx.avg_share != null)
    return `Right now wind and solar supply <b>${tx.avg_share.toFixed(1)}%</b> of Texas's ` +
      `electricity — <b>${dir(tx.share_vs_yoy_pts)} ${Math.abs(tx.share_vs_yoy_pts).toFixed(1)} ` +
      `points in a year</b>, the fastest shift of the four regions tracked here.`;
  return "Four regional power grids, tracked hour by hour.";
}

function windowedDaily(region){
  const f = D.findings[preset];
  const start = f && f.start ? f.start.slice(0,10) : "";
  return (D.daily[region]||[]).filter(row => !start || row[0] >= start);
}

function renderTrend(mountId, pos, unit){
  const mount = document.getElementById(mountId);
  if (!mount) return;
  const regs = REGIONS.filter(r => active.has(r) && (D.daily[r]||[]).length);
  if (!regs.length) return;
  const days = [...new Set(regs.flatMap(r => windowedDaily(r).map(x => x[0])))].sort();
  const idx = new Map(days.map((d,i) => [d,i]));
  const series = regs.map(r => {
    const vals = Array(days.length).fill(null);
    windowedDaily(r).forEach(row => { if (row[pos] != null) vals[idx.get(row[0])] = row[pos]; });
    return {name:NAMES[r], vals, cssvar:VARS[r]};
  });
  drawChart(mount, series, days.map(d => d.slice(5)), unit);
}

function renderProfile(){
  const prof = D.profile[preset] || {};
  const regs = REGIONS.filter(r => active.has(r) && prof[r]);
  const labels = Array.from({length:24}, (_,h) => String(h).padStart(2,"0")+":00");
  drawChart(document.getElementById("profile"),
    regs.map(r => ({name:NAMES[r], vals:prof[r], cssvar:VARS[r]})), labels, "MWh");
}

function renderDuck(){
  const region = document.getElementById("duck-region").value;
  const daySel = document.getElementById("duck-day");
  const days = [...new Set((D.hourly[region]||[]).map(row => row[0].slice(0,10)))];
  if (daySel.dataset.region !== region){
    daySel.innerHTML = days.map(d => `<option>${d}</option>`).join("");
    daySel.value = days[days.length-1] || "";
    daySel.dataset.region = region;
  }
  const day = daySel.value;
  const rows = (D.hourly[region]||[]).filter(r => r[0].slice(0,10) === day);
  drawChart(document.getElementById("duck"), [
    {name:"All Power Used", vals:rows.map(r => r[1]), cssvar:"--muted", dash:"5 4", dy:-8},
    {name:"Power Plants", vals:rows.map(r => r[2]), cssvar:VARS[region], width:2.5, dy:8},
  ], rows.map(r => r[0].slice(11)+":00"), "MWh");
}

function download(name, text){
  const a = document.createElement("a");
  a.href = "data:text/csv;charset=utf-8," + encodeURIComponent(text);
  a.download = name; a.click();
}

function renderAll(){
  const f = buildFindings();
  document.getElementById("tiles").innerHTML = f.tiles ||
    '<div class="tile"><div class="v">—</div><div class="k">No Data in Window</div></div>';
  fillList("find-top", [...f.usage, ...f.share, ...f.duck], 3);
  fillList("find-usage", f.usage);
  fillList("find-share", f.share);
  fillList("find-duck", f.duck);
  document.getElementById("hero").innerHTML = heroSentence();
  document.querySelectorAll(".windowname").forEach(el =>
    el.textContent = PRESET_LABEL[preset]);
  renderTrend("trend-demand", 1, "MWh");
  renderTrend("trend-share", 2, "%");
  renderTrend("trend-share2", 2, "%");
  renderProfile();
  renderDuck();
}

/* ---- Navigation ---- */
function show(view){
  document.querySelectorAll(".view").forEach(v =>
    v.classList.toggle("active", v.dataset.view === view));
  document.querySelectorAll(".navlink").forEach(b =>
    b.classList.toggle("active", b.dataset.view === view));
  history.replaceState(null, "", "#" + view);
  window.scrollTo({top:0});
}
document.querySelectorAll("[data-view]").forEach(b => {
  if (b.classList.contains("view")) return;
  b.addEventListener("click", () => show(b.dataset.view));
});
document.querySelectorAll(".rangebtn").forEach(b => b.addEventListener("click", () => {
  preset = b.dataset.preset;
  document.querySelectorAll(".rangebtn").forEach(x => x.classList.toggle("active", x === b));
  renderAll();
}));
document.querySelectorAll(".chip[data-region]").forEach(c => c.addEventListener("click", () => {
  const r = c.dataset.region;
  if (active.has(r) && active.size > 1) active.delete(r); else active.add(r);
  c.classList.toggle("off", !active.has(r));
  renderAll();
}));
document.getElementById("duck-region").addEventListener("change", renderDuck);
document.getElementById("duck-day").addEventListener("change", renderDuck);
document.getElementById("dl-daily").addEventListener("click", () => {
  let csv = "region,day,avg_demand_mwh,avg_renewable_share_pct\n";
  for (const r of REGIONS) for (const row of windowedDaily(r))
    csv += `${r},${row[0]},${row[1]},${row[2] ?? ""}\n`;
  download(`gridpulse_daily_${preset}.csv`, csv);
});
document.getElementById("dl-hourly").addEventListener("click", () => {
  let csv = "region,hour_utc,demand_mwh,net_load_mwh\n";
  for (const r of REGIONS) for (const row of (D.hourly[r]||[]))
    csv += `${r},${row[0]},${row[1]},${row[2] ?? ""}\n`;
  download("gridpulse_hourly_30d.csv", csv);
});
const overlay = document.getElementById("glossary");
document.getElementById("help").addEventListener("click", () => overlay.classList.add("open"));
overlay.addEventListener("click", ev => {
  if (ev.target === overlay || ev.target.classList.contains("close"))
    overlay.classList.remove("open");
});
document.addEventListener("keydown", ev => {
  if (ev.key === "Escape") overlay.classList.remove("open");
});

/* ---- Region Hover Cards (instant, no motion) ---- */
const REGION_INFO = {
  CISO: {op:"CAISO", serves:"≈ 32 million people", note:"Solar-heavy — home of the duck curve"},
  ERCO: {op:"ERCOT", serves:"≈ 27 million people", note:"Wind + solar build-out leader"},
  MISO: {op:"MISO", serves:"≈ 45 million people", note:"15 states, Minnesota to Louisiana"},
  PJM:  {op:"PJM", serves:"≈ 65 million people", note:"13 states — the biggest grid here"},
};
const hc = document.getElementById("hovercard");
function hovercardHTML(r){
  const info = REGION_INFO[r];
  const s = (D.findings[preset].regions || {})[r];
  let rows = `<div class="hc-row"><span>Grid operator</span><b>${info.op}</b></div>` +
             `<div class="hc-row"><span>Serves</span><b>${info.serves}</b></div>`;
  if (s){
    rows += `<div class="hc-row"><span>Average use</span><b>${fmtGW(s.avg_demand)} GWh/hr</b></div>`;
    if (s.avg_share != null)
      rows += `<div class="hc-row"><span>Wind + solar</span><b>${s.avg_share.toFixed(1)}%</b></div>`;
    if (s.demand_vs_yoy_pct != null)
      rows += `<div class="hc-row"><span>Use vs last year</span>` +
              `<b class="${cls(s.demand_vs_yoy_pct)}">${s.demand_vs_yoy_pct >= 0 ? "▲" : "▼"} ` +
              `${Math.abs(s.demand_vs_yoy_pct).toFixed(1)}%</b></div>`;
  }
  return `<div class="hc-title"><span class="dot" style="background:var(${VARS[r]})"></span>` +
    `${NAMES[r]}</div>${rows}` +
    `<div class="hc-row" style="margin-top:4px"><span>${info.note}</span></div>`;
}
function moveHovercard(ev){
  const pad = 14, w = 270, h = hc.offsetHeight || 160;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > innerWidth - 8) x = ev.clientX - w - pad;
  if (y + h > innerHeight - 8) y = ev.clientY - h - pad;
  hc.style.left = x + "px"; hc.style.top = y + "px";
}
document.addEventListener("mouseover", ev => {
  const el = ev.target.closest("[data-region]");
  if (!el || !REGION_INFO[el.dataset.region]){ hc.style.display = "none"; return; }
  hc.innerHTML = hovercardHTML(el.dataset.region);
  hc.style.display = "block";
  moveHovercard(ev);
});
document.addEventListener("mousemove", ev => {
  if (hc.style.display === "block" && ev.target.closest("[data-region]")) moveHovercard(ev);
});
document.addEventListener("mouseout", ev => {
  if (!ev.relatedTarget || !ev.relatedTarget.closest ||
      !ev.relatedTarget.closest("[data-region]")) hc.style.display = "none";
});

/* ---- Clickable KPI Tiles → that region's Usage view ---- */
document.getElementById("tiles").addEventListener("click", ev => {
  const tile = ev.target.closest(".tile[data-region]");
  if (!tile) return;
  const r = tile.dataset.region;
  active = new Set([r]);
  document.querySelectorAll(".chip[data-region]").forEach(c =>
    c.classList.toggle("off", c.dataset.region !== r));
  hc.style.display = "none";
  renderAll();
  show("usage");
});

const initial = (location.hash || "#overview").slice(1);
show(document.querySelector(`.view[data-view="${initial}"]`) ? initial : "overview");
renderAll();
"""


def _case_study_html() -> str:
    """CASE-STUDY.md rendered at build time — one canonical file, no drift."""
    for candidate in (Path("CASE-STUDY.md"),
                      Path(__file__).resolve().parents[2] / "CASE-STUDY.md"):
        if candidate.exists():
            return render_md(candidate.read_text(encoding="utf-8"))
    return "<p>The case study ships with the repository: see CASE-STUDY.md.</p>"


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
        for key, label in [("7d", "7 Days"), ("30d", "30 Days"), ("90d", "90 Days"),
                           ("6m", "6 Months"), ("12m", "12 Months"), ("all", "All")]
    )
    region_chips = "".join(
        f'<button class="chip" data-region="{r}">'
        f'<span class="dot" style="background:var(--{r.lower()})"></span>{label}</button>'
        for r, label in REGION_LABELS.items()
    )
    nav_links = "".join(
        f'<button class="navlink" data-view="{v}">{label}</button>'
        for v, label in [("overview", "Overview"), ("usage", "Usage"),
                         ("renewables", "Renewables"), ("duck", "Duck Curve"),
                         ("pattern", "Daily Pattern"), ("ask", "Ask the Analyst"),
                         ("quality", "Data Quality"), ("about", "Case Study")]
    )
    duck_options = "".join(
        f'<option value="{r}">{label}</option>' for r, label in REGION_LABELS.items()
    )
    flag_rows = "".join(
        f"<tr><td class='mono'>{flag}</td><td class='num'>{n:,}</td>"
        f"<td>{'Solar equipment drawing a trickle of power at night — real, kept, labeled' if flag == 'negative_generation' else 'Batteries charging (storing energy) — routine, kept, labeled'}</td></tr>"
        for flag, n in sorted(quality["flags"].items())
    )
    ledger_rows = "".join(
        f"<tr><td class='mono'>{r['run_id'][:15]}</td><td>{r['stage']}</td>"
        f"<td class='mono'>{r['git_sha']}</td><td class='num'>{r['rows_received']:,}</td>"
        f"<td class='num'>{r['rows_valid']:,}</td><td class='num'>{r['rows_quarantined']}</td>"
        f"<td class='num'>{r['rows_upserted']:,}</td><td class='num'>{r['api_calls']}</td>"
        f"<td class='num'>{r['runtime_seconds']}s</td></tr>"
        for r in quality["runs"]
    )
    ask_live = (
        f'<a class="cta primary" href="{STREAMLIT_URL}" target="_blank" '
        f'rel="noopener">Open the Live Analyst ↗</a>'
        if STREAMLIT_URL else
        '<span class="qchip">The live analyst runs in the companion app — '
        'link coming online shortly.</span>'
    )
    data_json = json.dumps(payload, separators=(",", ":"))

    return f'''<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GridPulse</title>
<style>{CSS}</style></head><body>

<div class="topbar">
  <div class="topbar-inner">
    <span class="brand"><span class="bolt">⚡</span> GridPulse</span>
    <nav>{nav_links}</nav>
    <button class="helpbtn" id="help" title="What Do These Words Mean?">?</button>
  </div>
  <div class="subbar"><div class="subbar-inner">
    <span class="sublabel">Time Frame</span>{range_buttons}
    <span class="sublabel" style="margin-left:0.6rem">Regions</span>{region_chips}
  </div></div>
</div>

<div class="wrap">

<div class="view" data-view="overview">
  <div class="viewhead">
    <h1>The US Grid, Live</h1>
    <p class="hero" id="hero"></p>
  </div>
  <div class="tiles" id="tiles"></div>
  <div class="card">
    <h2>Top Findings — <span class="windowname">the Last 90 Days</span></h2>
    <p class="sub">Straight arithmetic on the data — nothing estimated. Each
    section in the navigation carries its own full set.</p>
    <ul class="findings" id="find-top"></ul>
  </div>
  <div class="card">
    <h2>Power From Wind &amp; Solar</h2>
    <p class="sub">The share of generated electricity that came from wind and
    solar, daily. The full story lives under <b>Renewables</b>.</p>
    <div class="chartwrap" id="trend-share"></div>
  </div>
  <div class="card">
    <h2>Ask the Analyst</h2>
    <p class="sub">Have a question the charts don't answer? The AI analyst
    researches it by querying this database directly — and shows its work.</p>
    <button class="cta primary" data-view="ask">Ask a Question →</button>
  </div>
</div>

<div class="view" data-view="usage">
  <div class="viewhead">
    <h1>How Much Electricity Each Region Uses</h1>
    <p>Daily averages for <span class="windowname">the Last 90 Days</span>.
    1 GWh ≈ one hour of power for 750,000 homes. Hover any chart for exact
    numbers; switch the time frame above.</p>
  </div>
  <div class="card"><div class="chartwrap" id="trend-demand"></div></div>
  <div class="card">
    <h2>Findings</h2>
    <ul class="findings" id="find-usage"></ul>
    <div class="chips">
      <button class="dl" id="dl-daily">⬇ Daily Numbers (CSV)</button>
      <button class="dl" id="dl-hourly">⬇ Hourly Detail, Last 30 Days (CSV)</button>
    </div>
  </div>
</div>

<div class="view" data-view="renewables">
  <div class="viewhead">
    <h1>Power From Wind &amp; Solar</h1>
    <p>The "Renewable share": of every 100 units of electricity generated, how
    many came from wind and solar. Texas's climb is the standout story.</p>
  </div>
  <div class="card"><div class="chartwrap" id="trend-share2"></div></div>
  <div class="card">
    <h2>Findings</h2>
    <ul class="findings" id="find-share"></ul>
  </div>
</div>

<div class="view" data-view="duck">
  <div class="viewhead">
    <h1>The "Duck Curve"</h1>
    <p>Two lines, one day. <b>Gray dashed</b> = all power used. <b>Solid</b> =
    what conventional power plants supplied once wind and solar chipped in.
    Midday the solid line sags — the sun is doing the work; at sunset it
    surges back, the grid's hardest daily moment. The shape looks like a
    duck curve's silhouette, hence the name.</p>
  </div>
  <div class="card">
    <div class="chips" style="margin-bottom:0.5rem">
      <select id="duck-region">{duck_options}</select>
      <select id="duck-day"></select>
    </div>
    <div class="chartwrap" id="duck"></div>
  </div>
  <div class="card">
    <h2>Findings</h2>
    <ul class="findings" id="find-duck"></ul>
  </div>
</div>

<div class="view" data-view="pattern">
  <div class="viewhead">
    <h1>A Typical Day</h1>
    <p>The average shape of a day over <span class="windowname">the Last 90
    Days</span>: when each region's electricity use rises and falls across 24
    hours. Times are UTC — subtract 4 for Eastern, 7 for Pacific. Every region
    climbs through the morning and peaks in the early evening.</p>
  </div>
  <div class="card"><div class="chartwrap" id="profile"></div></div>
</div>

<div class="view" data-view="ask">
  <div class="viewhead">
    <h1>Ask the Analyst</h1>
    <p>Type any question about this data and an AI analyst researches it by
    writing and running real database queries — then answers in plain
    language and <b>shows every query it ran</b>, so nothing rests on trust.
    It is only allowed to read; it cannot change a single number.</p>
  </div>
  <div class="card">
    <h2>A Real Exchange</h2>
    <p class="sub">Generated by the analyst against this exact database.</p>
    <div class="qa">
      <div class="q">Which month was Texas's biggest for electricity use —
      and how did wind and solar hold up?</div>
      <div class="a">August 2026 is Texas's biggest month on record here:
      the state averaged <b>73.1 GWh every hour</b> — day and night — through
      the summer heat. Even at that scale, wind and solar still carried
      <b>42.0%</b> of the electricity generated that month. For a sense of
      the extremes: Texas's single busiest hour was <b>91.1 GWh</b> on
      July 22 at 23:00 UTC (6 pm local) — roughly enough electricity for
      <b>70 million homes</b> in that one hour.</div>
    </div>
    <details><summary class="sub" style="cursor:pointer">The 2 SQL queries
    behind that answer</summary>
    <div class="sql">SELECT substr(period_utc,1,7) AS month, ROUND(AVG(demand_mwh)) AS avg_mwh
FROM demand_hourly WHERE region='ERCO'
GROUP BY month ORDER BY avg_mwh DESC LIMIT 1;</div>
    <div class="sql">SELECT ROUND(AVG(renewable_share)*100,1)
FROM metrics_hourly
WHERE region='ERCO' AND period_utc LIKE '2026-08%';</div>
    </details>
  </div>
  <div class="card">
    <h2>Try It Yourself</h2>
    <p class="sub">The interactive analyst lives in the companion app (this
    page is a pure static site by design — it holds no API keys). Every
    answer is grounded the same way: database queries only, all of them
    shown.</p>
    {ask_live}
  </div>
</div>

<div class="view" data-view="quality">
  <div class="viewhead">
    <h1>Is This Data Trustworthy?</h1>
    <p>Yes — and you don't have to take that on faith. Every number on this
    site passed through three checkpoints before you saw it, and the leftovers
    from each checkpoint are published below, not hidden.</p>
  </div>
  <div class="card">
    <h2>The Three Checkpoints</h2>
    <p class="sub" style="max-width:78ch">
    <b>1 — Validation.</b> Every incoming record must have the right shape: a
    real timestamp, a known region, a readable number. Records that fail are
    <b>quarantined</b> — stored with the reason, never silently deleted.
    Current quarantine: <b>{quality["quarantined"]} rows</b> out of about
    360,000 processed.<br>
    <b>2 — Quality flags.</b> Some values are odd but real. Instead of
    "fixing" them, the pipeline keeps them and attaches a label, so every
    later calculation can decide what to do with eyes open:</p>
    <div class="tablewrap"><table>
      <tr><th>Flag</th><th class="num-h">Rows</th><th>What It Means</th></tr>
      {flag_rows}
    </table></div>
    <p class="sub" style="max-width:78ch; margin-top:0.5rem">
    <b>3 — The audit trail.</b> Every automatic update writes a row to the
    ledger below: what came in, what passed, what changed, and the exact
    software version that did the work. A run that changed nothing shows
    <span class="mono">upserted 0</span> — proof the pipeline re-running is
    harmless (engineers call this idempotency).</p>
  </div>
  <div class="card">
    <h2>Run Ledger — Every Update, on the Record</h2>
    <p class="sub">This page rebuilds itself twice a day. Newest first.</p>
    <div class="tablewrap"><table>
      <tr><th>Run</th><th>Stage</th><th>Commit</th><th class="num-h">Received</th>
          <th class="num-h">Valid</th><th class="num-h">Quar.</th>
          <th class="num-h">Upserted</th><th class="num-h">API</th><th class="num-h">Time</th></tr>
      {ledger_rows}
    </table></div>
  </div>
</div>

<div class="view" data-view="about">
  <div class="card mdbody">{_case_study_html()}</div>
</div>

</div>

<div class="overlay" id="glossary">
  <div class="panel">
    <button class="close">✕</button>
    <h2>How to Read This Site</h2>
    <div class="tablewrap"><table>
      <tr><th>Region</th><th>Who That Is</th></tr>
      <tr><td><b>California</b></td><td>the California grid (operator: CAISO) —
        about 32 million people</td></tr>
      <tr><td><b>Texas</b></td><td>the Texas grid (operator: ERCOT) — about 27
        million people; famous for wind power</td></tr>
      <tr><td><b>Midwest</b></td><td>15 states, Minnesota to Louisiana
        (operator: MISO) — about 45 million people</td></tr>
      <tr><td><b>Mid-Atlantic</b></td><td>13 states around Pennsylvania,
        Virginia, Ohio, Illinois (operator: PJM) — about 65 million people,
        the biggest of the four</td></tr>
    </table></div>
    <p><b>Demand</b> — how much electricity everyone is using, measured in
    <b>GWh</b> (gigawatt-hours). Feel for scale: 1 GWh is roughly one hour of
    electricity for <b>750,000 homes</b>.<br>
    <b>Renewable share</b> — the slice of generated power that came from wind
    and solar.<br>
    <b>Net load</b> — what conventional power plants (gas, nuclear, coal,
    hydro) must supply after wind and solar have done their part; its midday
    sag and sunset surge is the famous duck curve.<br>
    <b>Times</b> — all clocks are UTC: subtract 4 hours for Eastern time, 7
    for Pacific.</p>
    <p class="sub">Data: US Energy Information Administration (EIA) ·
    coverage {cov["start"][:10] if cov["start"] else "—"} →
    {cov["end"][:10] if cov["end"] else "—"} · {cov["hours"]:,} hours held ·
    refreshed automatically twice a day.</p>
  </div>
</div>

<div class="hovercard" id="hovercard"></div>
<footer>GridPulse v{__version__} · generated {generated} ·
<a href="https://github.com/evanderpool/gridpulse">source &amp; docs</a> ·
data: US Energy Information Administration (EIA) open-data API v2 ·
built by Erick Vanderpool</footer>
<script>{JS.replace("__DATA__", data_json)}</script>
</body></html>'''


def write_report(conn: sqlite3.Connection, out_path: Path) -> Path:
    """Render the report to disk and return the path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_report(conn), encoding="utf-8", newline="\n")
    return out_path
