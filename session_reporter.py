#!/usr/bin/env python3
"""
Automated End-of-Session Performance Reporting Module.
======================================================
Compiles institutional performance metrics at the close of every major trading session
(Asian 07:00 UTC, London 16:00 UTC, New York 21:00 UTC) and formats rich alerts for Telegram.

Usage:
  python session_reporter.py --session LONDON
  python session_reporter.py --latest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

JOURNAL_FILE = Path("trade_journal.json")


@dataclass
class SessionSummary:
    session_name: str
    start_utc: str
    end_utc: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate_pct: float = 0.0
    net_pnl_usd: float = 0.0
    total_r: float = 0.0
    profit_factor: float = 0.0
    best_trade_pnl: float = 0.0
    best_trade_symbol: str = ""
    open_positions_count: int = 0
    open_positions_unrealized_usd: float = 0.0
    next_session_name: str = ""
    next_session_time_utc: str = ""


# Major session definitions (Start time UTC, End time UTC, Next session label, Next session time)
SESSION_WINDOWS = {
    "ASIAN": {
        "start": time(0, 0),
        "end": time(7, 0),
        "display": "Asian Session",
        "next_name": "London Open Killzone",
        "next_time": "07:00 UTC",
    },
    "LONDON": {
        "start": time(7, 0),
        "end": time(16, 0),
        "display": "London Session",
        "next_name": "New York Open Overlap",
        "next_time": "12:30 UTC",
    },
    "NEW_YORK": {
        "start": time(12, 0),
        "end": time(21, 0),
        "display": "New York Session",
        "next_name": "Asian Session",
        "next_time": "00:00 UTC",
    },
}


def load_journal_trades() -> List[Dict[str, Any]]:
    """Loads closed trade records from trade_journal.json."""
    if not JOURNAL_FILE.is_file():
        return []
    try:
        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("trades", data.get("history", []))
    except Exception:
        pass
    return []


def filter_trades_by_session(
    trades: List[Dict[str, Any]],
    session_start: time,
    session_end: time,
    target_date: Optional[datetime.date] = None,
) -> List[Dict[str, Any]]:
    """Filters trades closed within the session time window on the target date."""
    filtered: List[Dict[str, Any]] = []
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()

    for t in trades:
        ts_str = t.get("closed_at") or t.get("exit_time") or t.get("time") or t.get("timestamp")
        if not ts_str:
            continue
        try:
            if isinstance(ts_str, (int, float)):
                dt = datetime.fromtimestamp(float(ts_str), tz=timezone.utc)
            else:
                dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)

            if dt.date() == target_date:
                trade_time = dt.time()
                if session_start <= session_end:
                    in_window = session_start <= trade_time <= session_end
                else:
                    in_window = trade_time >= session_start or trade_time <= session_end

                if in_window:
                    filtered.append(t)
        except Exception:
            continue

    return filtered


def compile_session_summary(
    session_key: str,
    trades: Optional[List[Dict[str, Any]]] = None,
    open_positions: Optional[List[Any]] = None,
    current_utc: Optional[datetime] = None,
) -> SessionSummary:
    """Calculates quantitative performance metrics for the specified session."""
    if current_utc is None:
        current_utc = datetime.now(timezone.utc)

    key = session_key.upper().replace(" ", "_")
    cfg = SESSION_WINDOWS.get(key, SESSION_WINDOWS["LONDON"])

    if trades is None:
        all_trades = load_journal_trades()
        trades = filter_trades_by_session(all_trades, cfg["start"], cfg["end"], target_date=current_utc.date())

    summary = SessionSummary(
        session_name=cfg["display"],
        start_utc=cfg["start"].strftime("%H:%M UTC"),
        end_utc=cfg["end"].strftime("%H:%M UTC"),
        next_session_name=cfg["next_name"],
        next_session_time_utc=cfg["next_time"],
        total_trades=len(trades),
    )

    gross_profit = 0.0
    gross_loss = 0.0

    for t in trades:
        pnl = float(t.get("pnl", t.get("profit", t.get("final_pnl", 0.0))) or 0.0)
        r = float(t.get("r_multiple", t.get("r", 0.0)) or 0.0)
        sym = str(t.get("symbol", "ASSET"))

        summary.net_pnl_usd += pnl
        summary.total_r += r

        if pnl > 0:
            summary.wins += 1
            gross_profit += pnl
            if pnl > summary.best_trade_pnl:
                summary.best_trade_pnl = pnl
                summary.best_trade_symbol = sym
        elif pnl < 0:
            summary.losses += 1
            gross_loss += abs(pnl)

    if summary.total_trades > 0:
        summary.win_rate_pct = round((summary.wins / summary.total_trades) * 100.0, 1)

    if gross_loss > 0:
        summary.profit_factor = round(gross_profit / gross_loss, 2)
    elif gross_profit > 0:
        summary.profit_factor = 99.99  # No losses
    else:
        summary.profit_factor = 0.0

    summary.net_pnl_usd = round(summary.net_pnl_usd, 2)
    summary.total_r = round(summary.total_r, 2)

    if open_positions:
        summary.open_positions_count = len(open_positions)
        unrealized = 0.0
        for p in open_positions:
            unrealized += float(getattr(p, "unrealized_pnl", getattr(p, "pnl", 0.0)) or 0.0)
        summary.open_positions_unrealized_usd = round(unrealized, 2)

    return summary


def format_session_telegram_message(summary: SessionSummary) -> str:
    """Formats an institutional Markdown report suitable for Telegram."""
    pnl_badge = "🟢" if summary.net_pnl_usd >= 0 else "🔴"
    pnl_sign = "+" if summary.net_pnl_usd >= 0 else ""
    r_sign = "+" if summary.total_r >= 0 else ""

    lines = [
        f"🏁 **END-OF-SESSION PERFORMANCE REPORT: {summary.session_name.upper()}**",
        "━" * 32,
        f"🕒 **Window:** `{summary.start_utc} — {summary.end_utc}`",
        f"• **Trades Closed:** `{summary.total_trades}` ({summary.wins}W / {summary.losses}L)",
        f"• **Win Rate:** `{summary.win_rate_pct:.1f}%`",
        f"• **Session PnL:** {pnl_badge} `{pnl_sign}${summary.net_pnl_usd:,.2f}` (`{r_sign}{summary.total_r:.2f}R`)",
        f"• **Profit Factor:** `{summary.profit_factor:.2f}`",
    ]

    if summary.best_trade_pnl > 0:
        lines.append(f"• **Best Trade:** `+${summary.best_trade_pnl:,.2f}` ({summary.best_trade_symbol})")

    if summary.open_positions_count > 0:
        u_sign = "+" if summary.open_positions_unrealized_usd >= 0 else ""
        lines.append(f"• **Carried Positions:** `{summary.open_positions_count}` active (`{u_sign}${summary.open_positions_unrealized_usd:,.2f}` unrealized)")
    else:
        lines.append("• **Open Positions:** 0 (Flat into next session)")

    lines.extend([
        "━" * 32,
        f"🔜 **Next Session:** {summary.next_session_name} ({summary.next_session_time_utc})",
    ])

    return "\n".join(lines)


# State tracker to prevent duplicate session alerts within the same calendar day
_REPORTED_SESSIONS: Dict[str, str] = {}


def check_and_generate_transition_report(
    current_utc: Optional[datetime] = None,
    open_positions: Optional[List[Any]] = None,
) -> Optional[Tuple[str, str]]:
    """
    Checks if a major session just completed (within a 10-minute transition window).
    Returns (session_name, telegram_message) if an alert should be sent, or None.
    """
    if current_utc is None:
        current_utc = datetime.now(timezone.utc)

    today_str = current_utc.strftime("%Y-%m-%d")
    curr_time = current_utc.time()

    # Transition trigger windows (within 10 mins after session end):
    # Asian ends at 07:00 -> trigger between 07:00 and 07:10
    # London ends at 16:00 -> trigger between 16:00 and 16:10
    # NY ends at 21:00 -> trigger between 21:00 and 21:10
    triggers = [
        ("ASIAN", time(7, 0), time(7, 10)),
        ("LONDON", time(16, 0), time(16, 10)),
        ("NEW_YORK", time(21, 0), time(21, 10)),
    ]

    for key, start_t, end_t in triggers:
        if start_t <= curr_time <= end_t:
            report_key = f"{key}_{today_str}"
            if _REPORTED_SESSIONS.get(key) != today_str:
                _REPORTED_SESSIONS[key] = today_str
                summary = compile_session_summary(key, open_positions=open_positions, current_utc=current_utc)
                msg = format_session_telegram_message(summary)
                return key, msg

    return None


def main():
    parser = argparse.ArgumentParser(description="End-of-Session Performance Reporter")
    parser.add_argument("--session", choices=["ASIAN", "LONDON", "NEW_YORK"], default="LONDON")
    parser.add_argument("--latest", action="store_true", help="Auto-detect current or most recent session")
    args = parser.parse_args()

    target_session = args.session
    if args.latest:
        now_t = datetime.now(timezone.utc).time()
        if now_t < time(7, 0):
            target_session = "ASIAN"
        elif now_t < time(16, 0):
            target_session = "LONDON"
        else:
            target_session = "NEW_YORK"

    summary = compile_session_summary(target_session)
    msg = format_session_telegram_message(summary)
    print("\n" + msg + "\n")


if __name__ == "__main__":
    main()
