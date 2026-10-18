"""
Lightweight Real-Time Web Dashboard / Local UI Companion (Feature 6)
====================================================================
Local operations companion and real-time management dashboard for Nimble-Darwin.

Features:
- Live financial terminal dark-theme UI with responsive status telemetry
- Real-time MT5 account telemetry: Equity, Balance, Floating PnL, Margin Level
- Dynamic Slots Control: Switch between Auto and Manual (1 to 6 concurrent positions)
- Active Positions Table with live mark price, floating PnL, and 1-click Close action
- Pending Trade Setups Table with 1-click Cancel action
- Multi-Timeframe Trend & Volatility Heatmap Matrix (M1..D1 for XAUUSD & BTCUSDT)
- Emergency Halt & Autotrade toggle endpoints
- Token-authenticated endpoints for non-loopback bindings; frictionless on 127.0.0.1
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("OpsDashboard")

import config


class StatusReadError(RuntimeError):
    pass


class StatusStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def write(self, snapshot: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(snapshot)
        payload.setdefault("schema_version", 1)
        payload.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, allow_nan=False, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": 1,
                "service": "TradingBot",
                "state": "unknown",
                "reason": "No status snapshot has been written yet.",
            }
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict) or payload.get("schema_version", 1) != 1:
                raise ValueError("Unsupported status snapshot schema")
            return payload
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise StatusReadError(f"Cannot read status snapshot: {exc}") from exc


def health_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
    stale_after_seconds: int = 90,
) -> dict[str, Any]:
    if stale_after_seconds < 1:
        raise ValueError("stale_after_seconds must be positive")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    reasons = []
    last_success = snapshot.get("last_success_at")
    age_seconds = None
    if not last_success:
        reasons.append("No successful market-data poll recorded")
    else:
        try:
            stamp = datetime.fromisoformat(str(last_success).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            age_seconds = max(0.0, (now - stamp.astimezone(timezone.utc)).total_seconds())
            if age_seconds > stale_after_seconds:
                reasons.append(f"Last successful poll is stale ({int(age_seconds)}s)")
        except (TypeError, ValueError):
            reasons.append("Invalid last_success_at timestamp")

    try:
        errors = int(snapshot.get("consecutive_errors", 0) or 0)
        if errors < 0:
            raise ValueError("negative error count")
    except (TypeError, ValueError):
        errors = 1
        reasons.append("Invalid consecutive error count")
    if errors > 0:
        reasons.append(f"{errors} consecutive monitoring error(s)")
    if snapshot.get("last_error") and errors > 0:
        reasons.append("Recent monitoring error(s) recorded")

    healthy = not reasons
    return {
        "status": "ok" if healthy else "degraded",
        "healthy": healthy,
        "checked_at": now.astimezone(timezone.utc).isoformat(),
        "last_success_age_seconds": age_seconds,
        "reasons": reasons,
    }


# Global reference to execution engine for direct companion actions
_DASHBOARD_ENGINE = None


def set_dashboard_engine(eng: Any) -> None:
    global _DASHBOARD_ENGINE
    _DASHBOARD_ENGINE = eng


def get_dashboard_engine() -> Optional[Any]:
    global _DASHBOARD_ENGINE
    if _DASHBOARD_ENGINE is not None:
        return _DASHBOARD_ENGINE
    import sys
    main_mod = sys.modules.get("main")
    if main_mod is not None and hasattr(main_mod, "engine") and main_mod.engine is not None:
        return main_mod.engine
    return None


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nimble-Darwin Pro Trading Terminal</title>
<style>
:root {
  --bg: #090c10;
  --panel: #111622;
  --panel-border: #1e2638;
  --card: #161c2b;
  --card-hover: #1c2438;
  --text: #e6edf3;
  --text-muted: #8b949e;
  --accent: #58a6ff;
  --green: #2ea043;
  --green-bright: #00e599;
  --red: #f85149;
  --red-bright: #ff4976;
  --yellow: #d29922;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  padding: 18px 24px 40px;
  max-width: 1380px;
  margin: 0 auto;
  line-height: 1.5;
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--panel-border);
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 12px;
}
.brand { display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 1.25rem; letter-spacing: -0.5px; }
.brand-badge { background: #238636; color: white; padding: 2px 8px; border-radius: 6px; font-size: 0.72rem; text-transform: uppercase; font-weight: 800; }
.header-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 600;
  background: var(--card);
  border: 1px solid var(--panel-border);
}
.status-dot { width: 8px; height: 8px; border-radius: 50%; }
.dot-healthy { background: var(--green-bright); box-shadow: 0 0 8px var(--green-bright); }
.dot-degraded { background: var(--yellow); box-shadow: 0 0 8px var(--yellow); }
.dot-error { background: var(--red-bright); box-shadow: 0 0 8px var(--red-bright); }

/* Quick Control Bar */
.control-bar {
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  padding: 12px 18px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 12px;
}
.control-group { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.control-label { font-size: 0.82rem; color: var(--text-muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-right: 4px; }
button {
  cursor: pointer;
  font: inherit;
  font-size: 0.82rem;
  font-weight: 600;
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid var(--panel-border);
  background: var(--card);
  color: var(--text);
  transition: all 0.15s ease;
}
button:hover { background: var(--card-hover); border-color: var(--accent); }
button.btn-primary { background: #1f6feb; border-color: #388bfd; color: white; }
button.btn-primary:hover { background: #388bfd; }
button.btn-success { background: #238636; border-color: #2ea043; color: white; }
button.btn-danger { background: #da3633; border-color: #f85149; color: white; }
button.btn-danger:hover { background: #f85149; }
button.slot-btn.active { background: #1f6feb; border-color: #58a6ff; color: white; }
input[type="password"], input[type="text"] {
  background: var(--card);
  border: 1px solid var(--panel-border);
  color: var(--text);
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 0.82rem;
}

/* Stat Grid */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}
.stat-card {
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  padding: 14px 16px;
  position: relative;
  overflow: hidden;
}
.stat-label { font-size: 0.75rem; color: var(--text-muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
.stat-val { font-size: 1.4rem; font-weight: 750; margin-top: 4px; font-family: var(--font-mono); }
.val-up { color: var(--green-bright); }
.val-down { color: var(--red-bright); }
.stat-sub { font-size: 0.75rem; color: var(--text-muted); margin-top: 4px; }

/* Tables */
.section-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: 24px 0 12px;
  font-size: 1.05rem;
  font-weight: 700;
}
.section-count { background: var(--card); border: 1px solid var(--panel-border); font-size: 0.75rem; padding: 2px 8px; border-radius: 999px; margin-left: 8px; }
.table-wrapper {
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  overflow-x: auto;
  margin-bottom: 24px;
}
table { width: 100%; border-collapse: collapse; text-align: left; font-size: 0.84rem; }
th {
  background: var(--card);
  color: var(--text-muted);
  font-weight: 600;
  text-transform: uppercase;
  font-size: 0.72rem;
  letter-spacing: 0.5px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--panel-border);
}
td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--panel-border);
  font-family: var(--font-mono);
}
tr:last-child td { border-bottom: none; }
tr:hover td { background: var(--card); }
.empty-row { text-align: center; color: var(--text-muted); padding: 24px; font-style: italic; }

/* Badges */
.badge-long { background: rgba(0, 229, 153, 0.15); color: var(--green-bright); border: 1px solid rgba(0, 229, 153, 0.3); padding: 2px 7px; border-radius: 4px; font-weight: 700; }
.badge-short { background: rgba(255, 73, 118, 0.15); color: var(--red-bright); border: 1px solid rgba(255, 73, 118, 0.3); padding: 2px 7px; border-radius: 4px; font-weight: 700; }

/* Heatmap Grid */
.matrix-card {
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 24px;
}
.matrix-tabs { display: flex; gap: 8px; margin-bottom: 14px; }
.tab-btn { padding: 6px 14px; border-radius: 6px; font-weight: 600; cursor: pointer; }
.tab-btn.active { background: #1f6feb; color: white; border-color: #388bfd; }
.matrix-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
.tf-card {
  background: var(--card);
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  padding: 10px 12px;
  text-align: center;
}
.tf-title { font-size: 0.75rem; color: var(--text-muted); font-weight: 700; }
.tf-trend { font-size: 1rem; font-weight: 750; margin: 4px 0; }
.tf-metrics { font-size: 0.74rem; font-family: var(--font-mono); color: var(--text-muted); }

/* Toast */
#toast {
  position: fixed;
  bottom: 20px;
  right: 20px;
  background: var(--panel);
  border: 1px solid var(--accent);
  color: var(--text);
  padding: 10px 18px;
  border-radius: 8px;
  font-size: 0.84rem;
  font-weight: 600;
  display: none;
  box-shadow: 0 8px 24px rgba(0,0,0,0.5);
  z-index: 1000;
}
</style>
</head>
<body>

<header>
  <div class="brand">
    <span>⚡ Nimble-Darwin</span>
    <span class="brand-badge">PRO LIVE</span>
    <div id="healthBadge" class="status-pill">
      <span id="healthDot" class="status-dot dot-healthy"></span>
      <span id="healthText">HEALTHY</span>
    </div>
  </div>
  <div class="header-actions">
    <input id="tokenInput" type="password" placeholder="Dashboard Token (if required)" title="Dashboard Bearer Token">
    <button id="refreshBtn">🔄 Refresh</button>
    <span id="lastUpdated" style="font-size:0.75rem; color:var(--text-muted);">Syncing…</span>
  </div>
</header>

<!-- Operations Control Bar -->
<div class="control-bar">
  <div class="control-group">
    <span class="control-label">Slots Control (Max 6):</span>
    <button id="slotAuto" class="slot-btn" onclick="setSlots('auto')">Auto</button>
    <button id="slot1" class="slot-btn" onclick="setSlots('manual', 1)">1</button>
    <button id="slot2" class="slot-btn" onclick="setSlots('manual', 2)">2</button>
    <button id="slot3" class="slot-btn" onclick="setSlots('manual', 3)">3</button>
    <button id="slot4" class="slot-btn" onclick="setSlots('manual', 4)">4</button>
    <button id="slot5" class="slot-btn" onclick="setSlots('manual', 5)">5</button>
    <button id="slot6" class="slot-btn" onclick="setSlots('manual', 6)">6</button>
  </div>
  <div class="control-group">
    <span class="control-label">Autotrade:</span>
    <button id="btnAutotrade" class="btn-primary" onclick="toggleAutotrade()">Autotrade: ON</button>
    <button id="btnHalt" class="btn-danger" onclick="emergencyHalt()">🚨 Emergency Halt</button>
  </div>
</div>

<!-- Telemetry Cards -->
<div class="stat-grid">
  <div class="stat-card">
    <div class="stat-label">Live Equity</div>
    <div id="statEquity" class="stat-val">$0.00</div>
    <div id="statBalance" class="stat-sub">Balance: $0.00</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Floating PnL</div>
    <div id="statPnl" class="stat-val">$0.00</div>
    <div id="statPnlSub" class="stat-sub">0 open trades</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Free Margin</div>
    <div id="statFreeMargin" class="stat-val">$0.00</div>
    <div id="statMarginLevel" class="stat-sub">Margin Level: —</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Slots Allocation</div>
    <div id="statSlots" class="stat-val">Auto (6/6)</div>
    <div id="statSlotsSub" class="stat-sub">6 slots available</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">MT5 Account</div>
    <div id="statBroker" class="stat-val">MetaTrader 5</div>
    <div id="statAccount" class="stat-sub">Connected Live</div>
  </div>
</div>

<!-- Active Positions Table -->
<div class="section-title">
  <div>📈 Active Positions <span id="posCount" class="section-count">0</span></div>
  <button onclick="closeAllPositions()" style="font-size:0.75rem;" class="btn-danger">Close All</button>
</div>
<div class="table-wrapper">
  <table>
    <thead>
      <tr>
        <th>Ticket / ID</th>
        <th>Symbol</th>
        <th>Direction</th>
        <th>Lots</th>
        <th>Entry Price</th>
        <th>Mark Price</th>
        <th>Stop Loss</th>
        <th>TP 1</th>
        <th>Floating PnL</th>
        <th>Status / Trail</th>
        <th>Action</th>
      </tr>
    </thead>
    <tbody id="positionsTbody">
      <tr><td colspan="11" class="empty-row">No open active positions.</td></tr>
    </tbody>
  </table>
</div>

<!-- Multi-Timeframe Heatmap Matrix Section -->
<div class="section-title">
  <div>🌡️ Multi-Timeframe Confluence Matrix (Feature 5)</div>
</div>
<div class="matrix-card">
  <div class="matrix-tabs">
    <button id="tabGold" class="tab-btn active" onclick="switchMatrix('XAUUSD')">Gold (XAUUSD)</button>
    <button id="tabBtc" class="tab-btn" onclick="switchMatrix('BTCUSDT')">Bitcoin (BTCUSDT)</button>
  </div>
  <div id="matrixOverview" style="margin-bottom:12px; font-size:0.86rem; color:var(--text-muted);">
    Loading confluence telemetry…
  </div>
  <div id="matrixGrid" class="matrix-grid">
    <!-- Rendered via JS -->
  </div>
</div>

<!-- Pending Trade Setups Table -->
<div class="section-title">
  <div>⏳ Pending Trade Setups <span id="setupCount" class="section-count">0</span></div>
</div>
<div class="table-wrapper">
  <table>
    <thead>
      <tr>
        <th>Setup ID</th>
        <th>Symbol</th>
        <th>Direction</th>
        <th>Entry Trigger</th>
        <th>Stop Loss</th>
        <th>TP 1</th>
        <th>TP 2</th>
        <th>R:R</th>
        <th>Action</th>
      </tr>
    </thead>
    <tbody id="setupsTbody">
      <tr><td colspan="9" class="empty-row">No pending trade setups.</td></tr>
    </tbody>
  </table>
</div>

<div id="toast"></div>

<script>
let currentMatrixSymbol = 'XAUUSD';
let autotradeActive = true;

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 3500);
}

function getHeaders() {
  const t = document.getElementById('tokenInput').value.trim();
  const headers = {'Content-Type': 'application/json'};
  if (t) headers['Authorization'] = 'Bearer ' + t;
  return headers;
}

async function apiCall(endpoint, method='GET', body=null) {
  const opts = { method, headers: getHeaders(), cache: 'no-store' };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(endpoint, opts);
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
}

async function setSlots(mode, count=null) {
  try {
    const res = await apiCall('/api/slots', 'POST', { mode, count });
    showToast(res.message || 'Slots updated');
    loadDashboard();
  } catch (e) {
    showToast('Failed to set slots: ' + e.message);
  }
}

async function toggleAutotrade() {
  try {
    const next = !autotradeActive;
    const res = await apiCall('/api/autotrade', 'POST', { enabled: next });
    showToast(res.message || 'Autotrade toggled');
    loadDashboard();
  } catch (e) {
    showToast('Autotrade toggle error: ' + e.message);
  }
}

async function emergencyHalt() {
  if (!confirm('Are you sure you want to trigger EMERGENCY HALT? New trade execution will stop.')) return;
  try {
    const res = await apiCall('/api/halt', 'POST', { halt: true });
    showToast(res.message || 'Emergency Halt triggered');
    loadDashboard();
  } catch (e) {
    showToast('Halt error: ' + e.message);
  }
}

async function closePosition(sym) {
  if (!confirm('Close position for ' + sym + '?')) return;
  try {
    const res = await apiCall('/api/close', 'POST', { symbol: sym });
    showToast(res.message || 'Close order executed');
    loadDashboard();
  } catch (e) {
    showToast('Close error: ' + e.message);
  }
}

async function closeAllPositions() {
  if (!confirm('Close ALL active positions immediately?')) return;
  try {
    const res = await apiCall('/api/close', 'POST', { symbol: 'all' });
    showToast(res.message || 'All positions closed');
    loadDashboard();
  } catch (e) {
    showToast('Close all error: ' + e.message);
  }
}

async function cancelSetup(id) {
  try {
    const res = await apiCall('/api/cancel', 'POST', { id });
    showToast(res.message || 'Setup cancelled');
    loadDashboard();
  } catch (e) {
    showToast('Cancel error: ' + e.message);
  }
}

function switchMatrix(sym) {
  currentMatrixSymbol = sym;
  document.getElementById('tabGold').className = 'tab-btn' + (sym === 'XAUUSD' ? ' active' : '');
  document.getElementById('tabBtc').className = 'tab-btn' + (sym === 'BTCUSDT' ? ' active' : '');
  loadMatrix();
}

async function loadMatrix() {
  try {
    const data = await apiCall('/api/matrix?symbol=' + currentMatrixSymbol);
    const snap = data.snapshot;
    if (!snap) return;

    const ov = document.getElementById('matrixOverview');
    ov.innerHTML = `<strong>${snap.symbol}</strong> ($${Number(snap.current_price).toLocaleString()}) &bull; ` +
      `Confluence: <strong>${snap.confluence_grade}</strong> (${snap.overall_score > 0 ? '+' : ''}${snap.overall_score}/100) &bull; ` +
      `Structure: <strong>${snap.ltf_htf_alignment}</strong> &bull; ` +
      `<em>${snap.recommendation}</em>`;

    const grid = document.getElementById('matrixGrid');
    grid.innerHTML = '';
    const order = ['1m', '5m', '15m', '1h', '4h', 'daily'];
    for (const k of order) {
      const tf = snap.timeframes[k];
      if (!tf) continue;
      const card = document.createElement('div');
      card.className = 'tf-card';
      const trendColor = tf.score > 20 ? 'var(--green-bright)' : (tf.score < -20 ? 'var(--red-bright)' : 'var(--text-muted)');
      card.innerHTML = `
        <div class="tf-title">${tf.label}</div>
        <div class="tf-trend" style="color:${trendColor}">${tf.trend_icon} ${tf.trend_state}</div>
        <div class="tf-metrics">RSI: ${tf.rsi} (${tf.rsi_state.split(' ')[0]})</div>
        <div class="tf-metrics">Vol: ${tf.volatility_state}</div>
        <div class="tf-metrics" style="font-weight:700; color:${trendColor}">Score: ${tf.score > 0 ? '+' : ''}${tf.score}</div>
      `;
      grid.appendChild(card);
    }
  } catch (e) {
    document.getElementById('matrixOverview').textContent = 'Matrix unavailable: ' + e.message;
  }
}

async function loadDashboard() {
  try {
    const st = await apiCall('/api/status');
    const health = st.health || {};
    const isHealthy = health.status === 'ok';

    const hDot = document.getElementById('healthDot');
    const hText = document.getElementById('healthText');
    hDot.className = 'status-dot ' + (isHealthy ? 'dot-healthy' : 'dot-degraded');
    hText.textContent = isHealthy ? 'HEALTHY' : 'DEGRADED';

    autotradeActive = Boolean(st.autotrade_active);
    const atBtn = document.getElementById('btnAutotrade');
    atBtn.textContent = 'Autotrade: ' + (autotradeActive ? 'ON 🟢' : 'OFF ⚪');
    atBtn.className = autotradeActive ? 'btn-success' : 'btn-primary';

    document.getElementById('statEquity').textContent = '$' + Number(st.equity || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    document.getElementById('statBalance').textContent = 'Balance: $' + Number(st.balance || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});

    const uPnl = Number(st.unrealized_pnl || 0);
    const pnlEl = document.getElementById('statPnl');
    pnlEl.textContent = (uPnl >= 0 ? '+' : '') + '$' + uPnl.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    pnlEl.className = 'stat-val ' + (uPnl >= 0 ? 'val-up' : 'val-down');
    document.getElementById('statPnlSub').textContent = `${st.active_positions || 0} active | ${st.pending_setups || 0} pending`;

    document.getElementById('statFreeMargin').textContent = '$' + Number(st.free_margin || st.balance || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    const mLvl = st.margin_level_pct ? Number(st.margin_level_pct).toFixed(1) + '%' : 'Safe';
    document.getElementById('statMarginLevel').textContent = 'Margin Level: ' + mLvl;

    const slotsMode = String(st.slots_mode || 'auto').toUpperCase();
    const maxT = Number(st.max_open_trades || 6);
    document.getElementById('statSlots').textContent = `${slotsMode} (${maxT}/6)`;
    const freeSlots = Math.max(0, maxT - Number(st.active_positions || 0));
    document.getElementById('statSlotsSub').textContent = `${freeSlots} slots available`;

    // Highlight slots buttons
    document.querySelectorAll('.slot-btn').forEach(b => b.classList.remove('active'));
    if (st.slots_mode === 'auto') {
      document.getElementById('slotAuto')?.classList.add('active');
    } else {
      document.getElementById('slot' + maxT)?.classList.add('active');
    }

    const bName = st.configured_broker_mode || 'MetaTrader 5';
    document.getElementById('statBroker').textContent = bName;
    document.getElementById('statAccount').textContent = st.account_id ? `Account #${st.account_id}` : 'Connected Live';

    document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();

    // Load detailed positions
    const posData = await apiCall('/api/positions');
    renderPositions(posData.active_positions || []);
    renderSetups(posData.pending_setups || []);

  } catch (e) {
    document.getElementById('healthDot').className = 'status-dot dot-error';
    document.getElementById('healthText').textContent = 'OFFLINE';
    document.getElementById('lastUpdated').textContent = 'Sync error: ' + e.message;
  }
}

function renderPositions(list) {
  const tbody = document.getElementById('positionsTbody');
  document.getElementById('posCount').textContent = list.length;
  tbody.innerHTML = '';
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="empty-row">No open active positions.</td></tr>';
    return;
  }
  for (const pos of list) {
    const tr = document.createElement('tr');
    const isLong = String(pos.direction).toUpperCase().includes('LONG');
    const dirBadge = isLong ? '<span class="badge-long">LONG</span>' : '<span class="badge-short">SHORT</span>';
    const pnl = Number(pos.unrealized_pnl || 0);
    const pnlClass = pnl >= 0 ? 'val-up' : 'val-down';
    const pnlSign = pnl >= 0 ? '+' : '';
    const trailStr = pos.trailing_sl_active ? '🎯 Trail Active' : 'Standard';

    tr.innerHTML = `
      <td>#${pos.id || pos.ticket || '—'}</td>
      <td><strong>${pos.symbol}</strong></td>
      <td>${dirBadge}</td>
      <td>${pos.lots ?? pos.size ?? '—'}</td>
      <td>$${Number(pos.entry || pos.entry_level || 0).toLocaleString()}</td>
      <td>$${Number(pos.current_price || pos.entry || 0).toLocaleString()}</td>
      <td>$${Number(pos.stop_loss || 0).toLocaleString()}</td>
      <td>$${Number(pos.tp1 || 0).toLocaleString()}</td>
      <td class="${pnlClass}" style="font-weight:700;">${pnlSign}$${pnl.toFixed(2)}</td>
      <td>${trailStr}</td>
      <td><button class="btn-danger" style="padding:3px 8px;font-size:0.75rem;" onclick="closePosition('${pos.symbol}')">Close</button></td>
    `;
    tbody.appendChild(tr);
  }
}

function renderSetups(list) {
  const tbody = document.getElementById('setupsTbody');
  document.getElementById('setupCount').textContent = list.length;
  tbody.innerHTML = '';
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-row">No pending trade setups.</td></tr>';
    return;
  }
  for (const s of list) {
    const tr = document.createElement('tr');
    const isLong = String(s.direction).toUpperCase().includes('LONG');
    const dirBadge = isLong ? '<span class="badge-long">LONG</span>' : '<span class="badge-short">SHORT</span>';
    tr.innerHTML = `
      <td>#${s.id || '—'}</td>
      <td><strong>${s.symbol}</strong></td>
      <td>${dirBadge}</td>
      <td>$${Number(s.entry || s.entry_level || 0).toLocaleString()}</td>
      <td>$${Number(s.stop || s.stop_loss || 0).toLocaleString()}</td>
      <td>$${Number(s.tp1 || 0).toLocaleString()}</td>
      <td>$${Number(s.tp2 || 0).toLocaleString()}</td>
      <td>${s.rr || 1.5}R</td>
      <td><button class="btn-danger" style="padding:3px 8px;font-size:0.75rem;" onclick="cancelSetup('${s.id || s.symbol}')">Cancel</button></td>
    `;
    tbody.appendChild(tr);
  }
}

document.getElementById('refreshBtn').addEventListener('click', () => { loadDashboard(); loadMatrix(); });
loadDashboard();
loadMatrix();
setInterval(loadDashboard, 3000);
setInterval(loadMatrix, 15000);
</script>
</body>
</html>"""


def make_handler(store: StatusStore, token: str = "", engine: Optional[Any] = None):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: Mapping[str, Any]):
            body = json.dumps(payload, allow_nan=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if not token:
                return True
            supplied = self.headers.get("Authorization", "")
            if supplied and hmac.compare_digest(supplied, f"Bearer {token}"):
                return True
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            token_query = qs.get("token", [""])[0]
            if token_query and hmac.compare_digest(token_query, token):
                return True
            return False

        def _get_engine(self) -> Optional[Any]:
            return engine or get_dashboard_engine()

        def do_GET(self):
            parsed = urlparse(self.path)
            clean_path = parsed.path

            if clean_path == "/health":
                try:
                    snapshot = store.read()
                    health = health_from_snapshot(snapshot)
                    self._json(200 if health["healthy"] else 503, health)
                except StatusReadError as exc:
                    self._json(503, {"status": "error", "healthy": False, "error": str(exc)})
                return

            if clean_path in ("/", "/index.html"):
                body = _DASHBOARD_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; connect-src 'self'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if clean_path == "/api/status":
                if not self._authorized():
                    self._json(401, {"error": "Unauthorized"})
                    return
                try:
                    snapshot = store.read()
                    snapshot["health"] = health_from_snapshot(snapshot)
                    eng = self._get_engine()
                    if eng is not None:
                        snapshot["slots_mode"] = getattr(eng, "slots_mode", "auto")
                        snapshot["max_open_trades"] = int(getattr(eng, "max_open_trades", 6) or 6)
                        snapshot["active_positions"] = len(getattr(eng, "active_positions", {}))
                        snapshot["pending_setups"] = len(getattr(eng, "pending_setups", {}))
                        summary = eng.get_portfolio_summary() if hasattr(eng, "get_portfolio_summary") else {}
                        snapshot["equity"] = float(summary.get("equity", snapshot.get("equity", 0.0)))
                        snapshot["balance"] = float(summary.get("balance", snapshot.get("balance", 0.0)))
                        snapshot["free_margin"] = float(summary.get("free_margin", summary.get("balance", 0.0)))
                        snapshot["unrealized_pnl"] = float(summary.get("unrealized_pnl", 0.0))
                    self._json(200, snapshot)
                except StatusReadError as exc:
                    self._json(503, {"error": str(exc)})
                return

            if clean_path == "/api/positions":
                if not self._authorized():
                    self._json(401, {"error": "Unauthorized"})
                    return
                eng = self._get_engine()
                active_list = []
                pending_list = []
                if eng is not None:
                    for pos in list(getattr(eng, "active_positions", {}).values()):
                        curr_p = eng.get_current_price(pos.symbol) or pos.executed_price or pos.entry_level
                        entry = pos.executed_price or pos.entry_level or 0.0
                        u_pnl = float(pos.unrealized_pnl or 0.0)
                        if entry > 0 and pos.remaining_size > 0:
                            if "LONG" in str(pos.direction).upper():
                                u_pnl = (curr_p - entry) * pos.remaining_size
                            else:
                                u_pnl = (entry - curr_p) * pos.remaining_size
                        active_list.append({
                            "id": pos.id,
                            "ticket": getattr(pos, "ticket", None),
                            "symbol": pos.symbol,
                            "direction": str(pos.direction),
                            "lots": pos.lots,
                            "size": pos.remaining_size,
                            "entry": entry,
                            "current_price": curr_p,
                            "stop_loss": pos.stop_loss,
                            "tp1": pos.tp1,
                            "tp2": pos.tp2,
                            "unrealized_pnl": round(u_pnl, 2),
                            "trailing_sl_active": bool(getattr(pos, "trailing_sl_active", False)),
                        })
                    for s in list(getattr(eng, "pending_setups", {}).values()):
                        pending_list.append({
                            "id": s.id,
                            "symbol": s.symbol,
                            "direction": str(s.direction),
                            "entry": s.entry_level,
                            "stop": s.stop_loss,
                            "tp1": s.tp1,
                            "tp2": s.tp2,
                            "rr": s.rr_ratio,
                        })
                self._json(200, {"ok": True, "active_positions": active_list, "pending_setups": pending_list})
                return

            if clean_path == "/api/matrix":
                if not self._authorized():
                    self._json(401, {"error": "Unauthorized"})
                    return
                qs = parse_qs(parsed.query)
                sym = qs.get("symbol", ["XAUUSD"])[0]
                try:
                    from mtf_matrix import analyze_symbol_mtf
                    snap = analyze_symbol_mtf(sym)
                    d = snap.to_dict()
                    self._json(200, {"ok": True, "snapshot": d, "symbols": {sym: d}})
                except Exception as ex:
                    self._json(500, {"ok": False, "error": str(ex)})
                return

            self._json(404, {"error": "Not found"})

        def do_POST(self):
            if not self._authorized():
                self._json(401, {"error": "Unauthorized"})
                return

            parsed = urlparse(self.path)
            clean_path = parsed.path
            eng = self._get_engine()

            content_len = int(self.headers.get("Content-Length", 0))
            payload = {}
            if content_len > 0:
                try:
                    payload = json.loads(self.rfile.read(content_len).decode("utf-8"))
                except Exception:
                    payload = {}

            if clean_path == "/api/slots":
                mode = payload.get("mode", "auto")
                count = payload.get("count")
                if eng is not None and hasattr(eng, "set_slots_mode"):
                    ok, msg = eng.set_slots_mode(mode, count)
                    self._json(200, {"ok": ok, "message": msg, "slots_mode": eng.slots_mode, "max_open_trades": eng.max_open_trades})
                else:
                    self._json(200, {"ok": True, "message": f"Slots set to {mode}"})
                return

            if clean_path == "/api/autotrade":
                enabled = bool(payload.get("enabled", True))
                if hasattr(config, "set_autotrade"):
                    config.set_autotrade(enabled)
                else:
                    config.AUTOTRADE_ENABLED = enabled
                self._json(200, {"ok": True, "autotrade_active": enabled, "message": f"Autotrade turned {'ON' if enabled else 'OFF'}"})
                return

            if clean_path == "/api/halt":
                halt_flag = bool(payload.get("halt", True))
                if eng is not None:
                    eng.execution_halted = halt_flag
                self._json(200, {"ok": True, "execution_halted": halt_flag, "message": "Emergency halt triggered" if halt_flag else "Halt cleared"})
                return

            if clean_path == "/api/close":
                sym = payload.get("symbol", "all")
                if eng is not None and hasattr(eng, "close_position"):
                    res = eng.close_position(sym, reason="Web dashboard user close")
                    self._json(200, {"ok": True, "result": str(res), "message": f"Closed {sym}"})
                else:
                    self._json(200, {"ok": True, "message": f"Simulated close for {sym}"})
                return

            if clean_path == "/api/cancel":
                setup_id = payload.get("id") or payload.get("symbol")
                if eng is not None and hasattr(eng, "cancel_setup"):
                    res = eng.cancel_setup(setup_id)
                    self._json(200, {"ok": True, "result": str(res), "message": f"Cancelled setup {setup_id}"})
                else:
                    self._json(200, {"ok": True, "message": f"Simulated cancel for {setup_id}"})
                return

            self._json(404, {"error": "Not found"})

        def log_message(self, fmt, *args):
            return

    return Handler


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    status_file: str | Path = ".runtime/bot_status.json",
    token: str = "",
    engine: Optional[Any] = None,
) -> ThreadingHTTPServer:
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if not is_loopback and not token:
        raise ValueError("A dashboard token is required when binding beyond loopback")
    if not 0 <= int(port) <= 65535:
        raise ValueError("port must be between 0 and 65535")
    return ThreadingHTTPServer((host, int(port)), make_handler(StatusStore(status_file), token, engine))


_BACKGROUND_SERVER: Optional[ThreadingHTTPServer] = None
_BACKGROUND_THREAD: Optional[threading.Thread] = None


def start_dashboard_background(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    status_file: str | Path = ".runtime/bot_status.json",
    token: str = "",
    engine: Optional[Any] = None,
) -> tuple[Optional[ThreadingHTTPServer], str]:
    """Start dashboard in a daemon thread and return (server, url)."""
    global _BACKGROUND_SERVER, _BACKGROUND_THREAD
    if _BACKGROUND_SERVER is not None:
        addr = _BACKGROUND_SERVER.server_address
        return _BACKGROUND_SERVER, f"http://{addr[0]}:{addr[1]}"

    try:
        server = create_server(host=host, port=port, status_file=status_file, token=token, engine=engine)
    except OSError as e:
        if getattr(e, "errno", None) in (98, 48) or "already in use" in str(e).lower():
            logger.warning("Dashboard port %s already in use; returning active server URL: %s", port, e)
            return None, f"http://{host}:{port}"
        raise

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _BACKGROUND_SERVER = server
    _BACKGROUND_THREAD = thread
    addr = server.server_address
    url = f"http://{addr[0]}:{addr[1]}"
    return server, url


def stop_dashboard_background() -> None:
    global _BACKGROUND_SERVER, _BACKGROUND_THREAD
    if _BACKGROUND_SERVER is not None:
        try:
            _BACKGROUND_SERVER.shutdown()
            _BACKGROUND_SERVER.server_close()
        except Exception:
            pass
        _BACKGROUND_SERVER = None
        _BACKGROUND_THREAD = None


def main():
    import config
    server = create_server(
        host=getattr(config, "DASHBOARD_HOST", "127.0.0.1"),
        port=getattr(config, "DASHBOARD_PORT", 8765),
        status_file=getattr(config, "STATUS_FILE", ".runtime/bot_status.json"),
        token=getattr(config, "DASHBOARD_TOKEN", ""),
    )
    print(f"TradingBot operations companion dashboard: http://{server.server_address[0]}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
