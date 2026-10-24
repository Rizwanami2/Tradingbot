#!/usr/bin/env python3
"""Combined Bot Agent — FIT skills gate for Tradingbot autotrade."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, time, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


class Bias(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    CHOP = "CHOP"


@dataclass
class AgentConfig:
    symbol: str = "XAUUSD"
    tp_usd: float = 3.0
    sl_usd: float = 2.5
    trail_activation_usd: float = 1.20
    trail_distance_usd: float = 0.70
    risk_pct: float = 0.0035
    max_open: int = 2
    max_per_symbol: int = 1
    daily_loss_pct: float = 0.015
    daily_loss_r: float = 2.0
    max_consec_losses: int = 3
    min_rr: float = 1.0
    max_spread_frac_of_tp: float = 0.30
    london: Tuple[time, time] = (time(7, 0), time(10, 0))
    overlap: Tuple[time, time] = (time(12, 0), time(16, 0))
    news_window_min: int = 30
    require_m1_align: bool = True
    session_filter_enabled: bool = False
    allow_break_retest: bool = True
    journal_path: str = "data/combined_agent_journal.csv"
    min_trades_kill: int = 30
    kill_avg_r: float = 0.0


@dataclass
class AgentState:
    equity_day_start: float = 10_000.0
    equity_now: float = 10_000.0
    consec_losses: int = 0
    day_pnl: float = 0.0
    day_r: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    live_opt_in: bool = False

    def refresh_halt(self, cfg: AgentConfig) -> None:
        if self.consec_losses >= cfg.max_consec_losses:
            self.halted, self.halt_reason = True, f"consec_losses={self.consec_losses}"
            return
        if self.equity_day_start > 0:
            dd = (self.equity_day_start - self.equity_now) / self.equity_day_start
            if dd >= cfg.daily_loss_pct:
                self.halted, self.halt_reason = True, f"daily_dd={dd:.2%}"
                return
        if self.day_r <= -abs(cfg.daily_loss_r):
            self.halted, self.halt_reason = True, f"daily_r={self.day_r:.2f}"


def _utc(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _session(now: Optional[datetime], cfg: AgentConfig) -> str:
    t = _utc(now).time().replace(tzinfo=None)
    a, b = cfg.london
    if a <= t < b:
        return "london"
    a, b = cfg.overlap
    if a <= t < b:
        return "london_ny"
    return "off"


class CombinedBotAgent:
    """FIT-skill combined gate for autotrade signal path."""

    def __init__(self, cfg: Optional[AgentConfig] = None, state: Optional[AgentState] = None):
        import os
        self.cfg = cfg or AgentConfig()
        self.state = state or AgentState()
        self.cfg.session_filter_enabled = os.getenv("COMBINED_AGENT_SESSION_FILTER", "false").lower() in ("true", "1", "yes", "on")
        self._disabled = os.getenv("COMBINED_AGENT_ENABLED", "true").lower() in ("false", "0", "no", "off")

    def set_live_opt_in(self, enabled: bool) -> None:
        self.state.live_opt_in = bool(enabled)

    def _session_ok(self, now: Optional[datetime]) -> Tuple[bool, str]:
        s = _session(now, self.cfg)
        if not getattr(self.cfg, "session_filter_enabled", False):
            return True, s if s != "off" else "all_sessions"
        if s == "off":
            return False, "session_off"
        return True, s

    def _news_ok(self, now: Optional[datetime], events: Sequence[datetime]) -> Tuple[bool, str]:
        if not events:
            return True, "no_news_list"
        now = _utc(now)
        w = self.cfg.news_window_min * 60
        for ev in events:
            if abs((now - _utc(ev)).total_seconds()) <= w:
                return False, "news_window"
        return True, "news_clear"

    def _spread_ok(self, spread: Optional[float], tp_dist: Optional[float] = None) -> Tuple[bool, str]:
        if spread is None or spread <= 0:
            return True, "spread_unknown_allow"
        effective_tp = float(tp_dist) if (tp_dist is not None and tp_dist > 0) else abs(self.cfg.tp_usd)
        budget = effective_tp * self.cfg.max_spread_frac_of_tp
        if spread > budget:
            return False, f"spread>{budget:.2f}"
        return True, "spread_ok"

    def _risk_ok(
        self,
        open_trades: int,
        open_symbol: int,
        max_open: Optional[int] = None,
        max_per_symbol: Optional[int] = None,
    ) -> Tuple[bool, str]:
        self.state.refresh_halt(self.cfg)
        if self.state.halted:
            return False, f"halt:{self.state.halt_reason}"
        limit_total = max_open if max_open is not None else self.cfg.max_open
        limit_sym = max_per_symbol if max_per_symbol is not None else self.cfg.max_per_symbol
        if open_trades >= limit_total:
            return False, f"max_open ({open_trades}/{limit_total})"
        if open_symbol >= limit_sym:
            return False, f"max_symbol ({open_symbol}/{limit_sym})"
        return True, "risk_ok"

    def _structure_ok(
        self,
        direction: str,
        *,
        htf_bias: str = "",
        m1_dir: str = "",
        setup: str = "",
        is_fomo_midrange: bool = False,
    ) -> Tuple[bool, str]:
        if is_fomo_midrange:
            return False, "fomo_midrange"
        d = (direction or "").upper()
        bias = (htf_bias or d).upper()
        if bias in ("CHOP", "NONE", "FLAT"):
            return False, "htf_chop"
        if self.cfg.require_m1_align:
            m1 = (m1_dir or d).upper()
            longish = ("LONG", "BUY", "UP")
            shortish = ("SHORT", "SELL", "DOWN")
            if d in longish and m1 not in longish:
                return False, "m1_not_aligned"
            if d in shortish and m1 not in shortish:
                return False, "m1_not_aligned"
        su = (setup or "pullback").lower()
        # Free-text reason strings (e.g. "consensus long score=28.62") or long strings must never be false-vetoed
        if "=" in su or "score" in su or len(su) > 24:
            return True, "structure_ok"
        allowed = {"pullback", "sweep_reclaim", "sweep", "liquidity", "consensus", "balanced", ""}
        if self.cfg.allow_break_retest:
            allowed |= {"break_retest", "breakout"}
        allowed_keywords = (
            "consensus", "score", "long", "short", "agent", "arbiter", "swarm",
            "pull", "sweep", "liquid", "trend", "scalp", "balanced", "perfect",
            "entry", "fvg", "ob", "ote", "bos", "smc", "ict"
        )
        if su and su not in allowed:
            if not any(k in su for k in allowed_keywords):
                return False, f"setup_blocked:{su[:40]}"
        return True, "structure_ok"

    def _apply_levels(self, sig: dict, direction: str, entry: float) -> dict:
        sig = dict(sig)
        # Preserve existing risk-managed structural SL/TP (e.g. from Balanced profile or RiskManager)
        # to ensure trades maintain their designed R:R without reckless micro-stop risk.
        existing_sl = float(sig.get("stop") or sig.get("stop_loss") or 0.0)
        existing_tp = float(sig.get("tp1") or sig.get("tp") or 0.0)
        if existing_sl > 0 and existing_tp > 0:
            sig["sl"] = existing_sl
            sig["stop_loss"] = existing_sl
            sig["tp"] = existing_tp
            sig["tp1"] = existing_tp
            risk = abs(entry - existing_sl)
            reward = abs(existing_tp - entry)
            sig["rr"] = round(reward / risk, 2) if risk > 0 else float(sig.get("rr", 0.0) or 0.0)
            return sig

        d = direction.upper()
        if d in ("LONG", "BUY"):
            sig["sl"] = entry - self.cfg.sl_usd
            sig["tp"] = entry + self.cfg.tp_usd
        else:
            sig["sl"] = entry + self.cfg.sl_usd
            sig["tp"] = entry - self.cfg.tp_usd
        sig["stop_loss"] = sig["sl"]
        sig["tp1"] = sig["tp"]
        sig["trail_activation_usd"] = self.cfg.trail_activation_usd
        sig["trail_distance_usd"] = self.cfg.trail_distance_usd
        risk = abs(entry - float(sig["sl"]))
        reward = abs(float(sig["tp"]) - entry)
        sig["rr"] = (reward / risk) if risk > 0 else 0.0
        return sig

    def size_mult(self, conviction: float = 0.6) -> float:
        c = max(0.3, min(1.0, float(conviction or 0.6)))
        return round(0.7 + 0.3 * c, 3)

    def evaluate(
        self,
        sig: Optional[dict],
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, dict, List[str]]:
        reasons: List[str] = []
        sig = dict(sig or {})
        ctx = context or {}

        if getattr(self, "_disabled", False):
            return True, sig, ["combined_disabled"]

        if ctx.get("is_live") and not self.state.live_opt_in:
            # allow autotrade on demo; only hard-block when caller marks live + no opt-in
            # If is_live True and not opt-in: block
            return False, sig, ["live_blocked_no_opt_in"]

        now = ctx.get("now")
        ok, why = self._session_ok(now)
        if not ok:
            return False, sig, [why]
        reasons.append(f"session:{why}")

        ok, why = self._news_ok(now, ctx.get("news_events") or [])
        if not ok:
            return False, sig, [why]

        spread = float(ctx.get("spread") or sig.get("spread") or 0.0)
        # If spread unknown (0/None), do not block — only block when measured wide relative to planned TP
        if spread > 0:
            entry_p = float(sig.get("entry") or ctx.get("entry") or 0.0)
            tp_p = float(sig.get("tp1") or sig.get("tp") or 0.0)
            tp_dist = abs(tp_p - entry_p) if (entry_p > 0 and tp_p > 0) else None
            ok, why = self._spread_ok(spread, tp_dist=tp_dist)
            if not ok:
                return False, sig, [why]
            reasons.append(why)
        else:
            reasons.append("spread_ok")

        ok, why = self._risk_ok(
            int(ctx.get("open_trades") or 0),
            int(ctx.get("open_for_symbol") or 0),
            max_open=int(ctx["max_open"]) if "max_open" in ctx else None,
            max_per_symbol=int(ctx["max_per_symbol"]) if "max_per_symbol" in ctx else None,
        )
        if not ok:
            return False, sig, [why]
        reasons.append(why)

        d_raw = sig.get("direction")
        if hasattr(d_raw, "name"):
            direction = d_raw.name
        else:
            direction = str(d_raw or ctx.get("direction") or "").upper()
        if direction == "BUY":
            direction = "LONG"
        if direction == "SELL":
            direction = "SHORT"

        ok, why = self._structure_ok(
            direction,
            htf_bias=str(ctx.get("htf_bias") or sig.get("regime") or direction),
            m1_dir=str(ctx.get("m1_direction") or direction),
            setup=str(ctx.get("setup") or sig.get("setup") or "pullback"),
            is_fomo_midrange=bool(ctx.get("is_fomo_midrange")),
        )
        if not ok:
            return False, sig, [why]
        reasons.append(why)

        entry = float(sig.get("entry") or sig.get("entry_level") or ctx.get("entry") or 0.0)
        if entry > 0:
            sig = self._apply_levels(sig, direction, entry)
            if float(sig.get("rr") or 0) < self.cfg.min_rr:
                return False, sig, [f"rr<{self.cfg.min_rr}"]

        conv = sig.get("conviction")
        if isinstance(conv, dict):
            conv = conv.get("score", 0.6)
        mult = self.size_mult(float(conv or 0.6))
        sig["combined_agent_size_mult"] = mult
        sig["combined_agent"] = True
        sig["combined_reasons"] = list(reasons)
        reasons.append(f"size_mult={mult}")
        return True, sig, reasons

    def on_close(
        self,
        *,
        pnl: float,
        r_multiple: float,
        direction: str = "",
        entry: float = 0.0,
        notes: str = "",
    ) -> None:
        if pnl >= 0:
            self.state.consec_losses = 0
        else:
            self.state.consec_losses += 1
        self.state.day_pnl += pnl
        self.state.day_r += r_multiple
        self.state.equity_now += pnl
        self.state.refresh_halt(self.cfg)

        path = Path(self.cfg.journal_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["ts", "direction", "entry", "pnl", "r", "won", "notes"])
            w.writerow([
                _utc().isoformat(), direction, entry,
                round(pnl, 4), round(r_multiple, 4), int(pnl >= 0), notes,
            ])

    def metrics(self) -> Dict[str, Any]:
        path = Path(self.cfg.journal_path)
        if not path.exists():
            return {"n": 0, "avg_r": 0.0, "win_rate": 0.0}
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return {"n": 0, "avg_r": 0.0, "win_rate": 0.0}
        rs = [float(r["r"]) for r in rows]
        wins = sum(1 for r in rs if r > 0)
        n = len(rs)
        return {
            "n": n,
            "avg_r": round(sum(rs) / n, 4),
            "win_rate": round(wins / n, 4),
            "halted": self.state.halted,
            "halt_reason": self.state.halt_reason,
        }


_AGENT_SINGLETON: Optional[CombinedBotAgent] = None


def get_combined_agent() -> CombinedBotAgent:
    global _AGENT_SINGLETON
    if _AGENT_SINGLETON is None:
        _AGENT_SINGLETON = CombinedBotAgent()
    return _AGENT_SINGLETON


def main():
    import argparse
    import sys
    parser = argparse.ArgumentParser(description="Combined Bot Agent (FIT skills gate & runner)")
    parser.add_argument("--metrics", action="store_true", help="Print journal metrics")
    parser.add_argument("--check", action="store_true", help="Run diagnostic health check")
    parser.add_argument("--once", action="store_true", help="Run a single consensus cycle")
    parser.add_argument("--live", action="store_true", help="Run continuous combined trading engine")
    args = parser.parse_args()

    cba = CombinedBotAgent()
    if args.metrics:
        m = cba.metrics()
        print(f"CombinedBotAgent Metrics: {m}")
        return

    # Forward to artifacts/combined_bot_agent.py runner
    try:
        from artifacts.combined_bot_agent import main_launcher
        main_launcher()
    except Exception as e:
        print(f"Error launching combined runner: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
