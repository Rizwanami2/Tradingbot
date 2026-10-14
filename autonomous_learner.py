#!/usr/bin/env python3
"""
Autonomous Self-Learning Engine
===============================
Fully automatic — no Telegram commands required.

On startup / scheduled cycle:
  1. Load up to ~5 years of market data (or max available)
  2. Replay multi-agent proxy strategy
  3. Mine flaws (losing setups, weak hours, bad regimes, agent failures)
  4. Auto-correct: ban patterns, adjust thresholds, reweight agents
  5. Persist knowledge so live trading inherits improvements
  6. Online: every closed trade updates the same memory

Usage (automatic):
    from autonomous_learner import AutonomousLearner
    brain = AutonomousLearner()
    brain.bootstrap()          # heavy history learn (once / daily)
    brain.on_trade_closed(...) # call from journal close
    decision = brain.advise_signal(meta)  # veto/boost live signals
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("AutoLearner")

MEMORY_PATH = "autonomous_memory.json"
STATE_PATH = "autonomous_state.json"

# ~5 years of 1m bars is huge; we use daily + 1h for long history,
# and 1m for recent detail.
FIVE_YEARS_DAYS = 365 * 5


@dataclass
class Flaw:
    kind: str          # setup | hour | weekday | regime | agent | rr
    key: str
    trades: int
    expectancy: float
    winrate: float
    action: str        # ban | reduce | watch
    detail: str = ""


@dataclass
class Knowledge:
    """Persistent self-knowledge the bot applies automatically."""
    banned_setups: Dict[str, str] = field(default_factory=dict)
    banned_hours: List[int] = field(default_factory=list)       # UTC hours
    banned_weekdays: List[int] = field(default_factory=list)    # 0=Mon
    weak_regimes: Dict[str, float] = field(default_factory=dict)
    agent_weights: Dict[str, float] = field(default_factory=dict)
    min_score: float = 1.8
    min_rr: float = 1.8
    min_agreement: int = 3
    conviction_floor: float = 0.55
    size_mult_by_regime: Dict[str, float] = field(default_factory=dict)
    total_history_trades: int = 0
    total_live_trades: int = 0
    enabled: bool = True
    last_bootstrap: str = ""
    last_correction: str = ""
    improvement_log: List[str] = field(default_factory=list)


class AutonomousLearner:
    """
    Self-improving brain:
      bootstrap()     → learn from years of data, correct flaws
      on_trade_closed → online update
      advise_signal   → auto veto / size adjust on every live signal
    """

    DEFAULT_WEIGHTS = {
        "TREND": 1.25,
        "LIQUIDITY": 1.40,
        "INDICATORS": 1.00,
        "AI-LEARNER": 0.90,
        "NEWS": 0.70,
    }

    def __init__(
        self,
        memory_path: str = MEMORY_PATH,
        state_path: str = STATE_PATH,
        auto_bootstrap: bool = True,
        bootstrap_max_age_hours: float = 24.0,
    ):
        self.memory_path = memory_path
        self.state_path = state_path
        self.knowledge = Knowledge(agent_weights=dict(self.DEFAULT_WEIGHTS))
        self._trade_log: List[Dict[str, Any]] = []
        self._load()
        if auto_bootstrap:
            try:
                self._maybe_bootstrap(bootstrap_max_age_hours)
            except Exception as e:
                logger.warning("bootstrap skipped: %s", e)

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def _load(self):
        if os.path.exists(self.memory_path):
            try:
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                k = raw.get("knowledge", raw)
                self.knowledge = Knowledge(
                    banned_setups=k.get("banned_setups", {}),
                    banned_hours=k.get("banned_hours", []),
                    banned_weekdays=k.get("banned_weekdays", []),
                    weak_regimes=k.get("weak_regimes", {}),
                    agent_weights=k.get("agent_weights", dict(self.DEFAULT_WEIGHTS)),
                    min_score=float(k.get("min_score", 1.8)),
                    min_rr=float(k.get("min_rr", 1.8)),
                    min_agreement=int(k.get("min_agreement", 3)),
                    conviction_floor=float(k.get("conviction_floor", 0.55)),
                    size_mult_by_regime=k.get("size_mult_by_regime", {}),
                    total_history_trades=int(k.get("total_history_trades", 0)),
                    total_live_trades=int(k.get("total_live_trades", 0)),
                    enabled=bool(k.get("enabled", True)),
                    last_bootstrap=k.get("last_bootstrap", ""),
                    last_correction=k.get("last_correction", ""),
                    improvement_log=k.get("improvement_log", [])[-50:],
                )
                self._trade_log = raw.get("trade_log", [])[-2000:]
            except Exception as e:
                logger.warning("memory load: %s", e)

    def _save(self):
        try:
            payload = {
                "knowledge": asdict(self.knowledge),
                "trade_log": self._trade_log[-2000:],
                "updated": datetime.now(timezone.utc).isoformat(),
            }
            tmp = f"{self.memory_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, self.memory_path)
        except Exception as e:
            logger.warning("memory save: %s", e)

    def _log_improve(self, msg: str):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        entry = f"[{ts}] {msg}"
        self.knowledge.improvement_log.append(entry)
        self.knowledge.improvement_log = self.knowledge.improvement_log[-50:]
        self.knowledge.last_correction = ts
        logger.info("IMPROVE: %s", msg)

    # ------------------------------------------------------------------ #
    # Data loading (up to ~5 years)
    # ------------------------------------------------------------------ #
    def _load_history(self, symbol: str = "XAUUSD", years: float = 5.0) -> Optional[pd.DataFrame]:
        """
        Prefer real data; fall back to long synthetic series for offline learning.
        Uses daily bars for deep history (5y), resamples conceptually via daily R.
        """
        bars_daily = int(years * 365) + 30
        df = None

        # 1) CCXT / data_feed daily
        try:
            from data_feed import DataFeed
            import config as _cfg
            feed = DataFeed(
                symbol=symbol,
                source=getattr(_cfg, "DEFAULT_DATA_SOURCE", "ccxt"),
                exchange_id=getattr(_cfg, "DEFAULT_EXCHANGE", "binance"),
                timeframe="1d",
            )
            df = feed.fetch(limit=min(bars_daily, 2000), timeframe="1d")
            if df is not None and not df.empty and len(df) >= 200:
                logger.info("History via data_feed: %d daily bars", len(df))
                return df
        except Exception as e:
            logger.debug("data_feed history: %s", e)

        # 2) yfinance (good for multi-year gold / FX)
        try:
            import yfinance as yf
            ticker_map = {
                "XAUUSD": "GC=F", "XAUTUSD": "GC=F", "GOLD": "GC=F", "PAXGUSDT": "PAXG-USD",
                "BTCUSDT": "BTC-USD",
                "EURUSD": "EURUSD=X",
            }
            t = ticker_map.get(symbol.upper(), symbol)
            period = "5y" if years >= 4.5 else f"{int(years)}y"
            hist = yf.Ticker(t).history(period=period, interval="1d")
            if hist is not None and len(hist) >= 200:
                hist = hist.rename(columns={
                    "Open": "open", "High": "high", "Low": "low",
                    "Close": "close", "Volume": "volume",
                })
                hist.index = pd.to_datetime(hist.index, utc=True)
                logger.info("History via yfinance %s: %d bars", t, len(hist))
                return hist[["open", "high", "low", "close", "volume"]].dropna()
        except Exception as e:
            logger.debug("yfinance history: %s", e)

        # 3) Synthetic multi-year path (still useful to stress-test rules)
        logger.warning("Using synthetic %dy history for learning (no real feed)", years)
        n = max(500, int(years * 252))
        rng = np.random.default_rng(7)
        dates = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq="1D")
        # regime-switching random walk
        rets = []
        regime = 0
        for i in range(n):
            if i % 60 == 0:
                regime = int(rng.integers(0, 3))
            vol = [0.004, 0.008, 0.015][regime]
            drift = [0.0002, 0.0, -0.0001][regime]
            rets.append(drift + rng.normal(0, vol))
        p = 1800 * np.exp(np.cumsum(rets))  # gold-like scale
        return pd.DataFrame({
            "open": p,
            "high": p * (1 + rng.uniform(0.001, 0.008, n)),
            "low": p * (1 - rng.uniform(0.001, 0.008, n)),
            "close": p * (1 + rng.normal(0, 0.002, n)),
            "volume": rng.integers(1e4, 1e5, n),
        }, index=dates)

    # ------------------------------------------------------------------ #
    # Historical replay → flaw mining
    # ------------------------------------------------------------------ #
    def _simulate_trades(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Lightweight daily swing proxy:
          - Trend via EMA9/21
          - Entry on cross, exit opposite cross or max hold 10 days
          - Tag setup features for flaw mining
        """
        if df is None or len(df) < 50:
            return []
        c = df["close"].astype(float)
        ema_f = c.ewm(span=9, adjust=False).mean()
        ema_s = c.ewm(span=21, adjust=False).mean()
        atr = (df["high"] - df["low"]).rolling(14).mean()

        trades = []
        pos = None  # {dir, entry, i, stop}
        for i in range(30, len(df)):
            row_time = df.index[i]
            if hasattr(row_time, "to_pydatetime"):
                ts = row_time.to_pydatetime()
            else:
                ts = pd.Timestamp(row_time).to_pydatetime()
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            price = float(c.iloc[i])
            a = float(atr.iloc[i] or price * 0.01)

            # exit logic
            if pos is not None:
                hold = i - pos["i"]
                hit_stop = (pos["dir"] == 1 and price <= pos["stop"]) or (
                    pos["dir"] == -1 and price >= pos["stop"]
                )
                flip = (pos["dir"] == 1 and ema_f.iloc[i] < ema_s.iloc[i]) or (
                    pos["dir"] == -1 and ema_f.iloc[i] > ema_s.iloc[i]
                )
                if hit_stop or flip or hold >= 10:
                    risk = abs(pos["entry"] - pos["stop"]) or 1e-9
                    if pos["dir"] == 1:
                        r = (price - pos["entry"]) / risk
                    else:
                        r = (pos["entry"] - price) / risk
                    regime = "trend" if abs(ema_f.iloc[i] - ema_s.iloc[i]) / price > 0.005 else "range"
                    trades.append({
                        "r": float(r),
                        "won": r > 0,
                        "hour": ts.hour,
                        "weekday": ts.weekday(),
                        "regime": regime,
                        "setup": f"{'long' if pos['dir']==1 else 'short'}|{regime}|d{ts.weekday()}",
                        "rr_planned": 2.0,
                        "source": "history",
                    })
                    pos = None

            # entry
            if pos is None:
                if ema_f.iloc[i] > ema_s.iloc[i] and ema_f.iloc[i - 1] <= ema_s.iloc[i - 1]:
                    pos = {"dir": 1, "entry": price, "i": i, "stop": price - 1.2 * a}
                elif ema_f.iloc[i] < ema_s.iloc[i] and ema_f.iloc[i - 1] >= ema_s.iloc[i - 1]:
                    pos = {"dir": -1, "entry": price, "i": i, "stop": price + 1.2 * a}

        return trades

    def _mine_flaws(self, trades: List[Dict[str, Any]], min_n: int = 8) -> List[Flaw]:
        flaws: List[Flaw] = []

        def bucket(key_fn):
            groups: Dict[str, List[float]] = defaultdict(list)
            for t in trades:
                groups[str(key_fn(t))].append(float(t["r"]))
            return groups

        # setups
        for key, rs in bucket(lambda t: t.get("setup", "?")).items():
            if len(rs) < min_n:
                continue
            exp = sum(rs) / len(rs)
            wr = sum(1 for r in rs if r > 0) / len(rs)
            if exp < -0.15:
                flaws.append(Flaw("setup", key, len(rs), exp, wr * 100,
                                  "ban", f"setup expectancy {exp:.2f}R"))
            elif exp < 0:
                flaws.append(Flaw("setup", key, len(rs), exp, wr * 100,
                                  "reduce", f"weak setup {exp:.2f}R"))

        # hours
        for key, rs in bucket(lambda t: t.get("hour", -1)).items():
            if len(rs) < min_n:
                continue
            exp = sum(rs) / len(rs)
            wr = sum(1 for r in rs if r > 0) / len(rs)
            if exp < -0.2:
                flaws.append(Flaw("hour", key, len(rs), exp, wr * 100,
                                  "ban", f"hour {key} UTC exp {exp:.2f}R"))

        # weekdays
        for key, rs in bucket(lambda t: t.get("weekday", -1)).items():
            if len(rs) < min_n:
                continue
            exp = sum(rs) / len(rs)
            wr = sum(1 for r in rs if r > 0) / len(rs)
            if exp < -0.2:
                flaws.append(Flaw("weekday", key, len(rs), exp, wr * 100,
                                  "ban", f"weekday {key} exp {exp:.2f}R"))

        # regimes
        for key, rs in bucket(lambda t: t.get("regime", "?")).items():
            if len(rs) < min_n:
                continue
            exp = sum(rs) / len(rs)
            wr = sum(1 for r in rs if r > 0) / len(rs)
            if exp < 0:
                flaws.append(Flaw("regime", key, len(rs), exp, wr * 100,
                                  "reduce", f"regime {key} exp {exp:.2f}R"))

        return flaws

    def _apply_corrections(self, flaws: List[Flaw], trades: List[Dict[str, Any]]):
        k = self.knowledge
        # reset soft lists then rebuild from evidence
        new_banned_hours = set()
        new_banned_wd = set()

        for f in flaws:
            if f.kind == "setup" and f.action == "ban":
                k.banned_setups[f.key] = f.detail
                self._log_improve(f"BAN setup `{f.key}` ({f.detail})")
            elif f.kind == "hour" and f.action == "ban":
                try:
                    new_banned_hours.add(int(f.key))
                    self._log_improve(f"BAN hour {f.key} UTC ({f.detail})")
                except ValueError:
                    pass
            elif f.kind == "weekday" and f.action == "ban":
                try:
                    new_banned_wd.add(int(f.key))
                    self._log_improve(f"BAN weekday {f.key} ({f.detail})")
                except ValueError:
                    pass
            elif f.kind == "regime":
                # size down weak regimes
                mult = 0.5 if f.expectancy < -0.1 else 0.75
                k.weak_regimes[f.key] = f.expectancy
                k.size_mult_by_regime[f.key] = mult
                self._log_improve(f"SIZE×{mult} regime `{f.key}` ({f.detail})")

        if new_banned_hours:
            k.banned_hours = sorted(new_banned_hours)
        if new_banned_wd:
            k.banned_weekdays = sorted(new_banned_wd)

        # Global threshold nudge from overall expectancy
        if trades:
            exp = sum(t["r"] for t in trades) / len(trades)
            wr = sum(1 for t in trades if t["won"]) / len(trades)
            if exp < 0.05 or wr < 0.42:
                k.min_score = min(3.2, k.min_score + 0.15)
                k.min_rr = min(2.8, k.min_rr + 0.1)
                k.conviction_floor = min(0.75, k.conviction_floor + 0.03)
                k.min_agreement = min(4, k.min_agreement + 1)
                self._log_improve(
                    f"Tighten filters: score≥{k.min_score:.2f} RR≥{k.min_rr:.2f} "
                    f"conv≥{k.conviction_floor:.0%} agree≥{k.min_agreement} "
                    f"(hist exp={exp:.2f} WR={wr:.0%})"
                )
            elif exp > 0.25 and wr > 0.52:
                k.min_score = max(1.5, k.min_score - 0.05)
                k.conviction_floor = max(0.50, k.conviction_floor - 0.02)
                self._log_improve(
                    f"Slightly relax filters (hist exp={exp:.2f} WR={wr:.0%})"
                )

        # Agent weight micro-adjust toward defaults if no live stats yet
        for name, w in list(k.agent_weights.items()):
            k.agent_weights[name] = max(0.4, min(2.0, w))

        k.total_history_trades = len(trades)
        k.last_bootstrap = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ #
    # Public: bootstrap (auto)
    # ------------------------------------------------------------------ #
    def _maybe_bootstrap(self, max_age_hours: float):
        if self.knowledge.last_bootstrap:
            try:
                last = datetime.fromisoformat(self.knowledge.last_bootstrap.replace("Z", "+00:00"))
                age_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600.0
                if age_h < max_age_hours and self.knowledge.total_history_trades > 0:
                    logger.info("bootstrap fresh (%.1fh old) — skip", age_h)
                    return
            except Exception:
                pass
        self.bootstrap()

    def bootstrap(self, symbol: str = "XAUUSD", years: float = 5.0) -> Dict[str, Any]:
        """
        Full automatic history learn + self-correction.
        Safe to call repeatedly; results persist.
        """
        logger.info("BOOTSTRAP start: %s ~%.1fy", symbol, years)
        df = self._load_history(symbol, years=years)
        trades = self._simulate_trades(df) if df is not None else []
        if not trades:
            self._log_improve("Bootstrap: no trades generated — keep prior knowledge")
            self._save()
            return {"trades": 0, "flaws": 0}

        flaws = self._mine_flaws(trades)
        self._apply_corrections(flaws, trades)

        # merge into trade log (tagged history)
        for t in trades[-500:]:
            self._trade_log.append(t)
        self._save()

        exp = sum(t["r"] for t in trades) / len(trades)
        summary = {
            "trades": len(trades),
            "expectancy": round(exp, 3),
            "flaws": len(flaws),
            "banned_setups": len(self.knowledge.banned_setups),
            "banned_hours": self.knowledge.banned_hours,
            "banned_weekdays": self.knowledge.banned_weekdays,
            "min_score": self.knowledge.min_score,
            "min_rr": self.knowledge.min_rr,
            "conviction_floor": self.knowledge.conviction_floor,
        }
        self._log_improve(
            f"Bootstrap done: n={summary['trades']} exp={summary['expectancy']} "
            f"flaws={summary['flaws']} bans={summary['banned_setups']}"
        )
        self._save()
        return summary

    # ------------------------------------------------------------------ #
    # Online learning from live trades
    # ------------------------------------------------------------------ #
    def on_trade_closed(
        self,
        r_multiple: float,
        won: bool,
        setup: str = "",
        regime: str = "",
        session: str = "",
        hour: Optional[int] = None,
        weekday: Optional[int] = None,
        agents: Optional[List[str]] = None,
        conf: float = 0.6,
    ):
        now = datetime.now(timezone.utc)
        hour = hour if hour is not None else now.hour
        weekday = weekday if weekday is not None else now.weekday()
        setup = setup or f"live|{regime}|{session}"

        rec = {
            "r": float(r_multiple),
            "won": bool(won),
            "hour": hour,
            "weekday": weekday,
            "regime": regime or "unknown",
            "setup": setup,
            "agents": agents or [],
            "conf": conf,
            "source": "live",
            "ts": now.isoformat(),
        }
        self._trade_log.append(rec)
        self.knowledge.total_live_trades += 1

        # Immediate self-correction on repeated live failures
        recent = [t for t in self._trade_log if t.get("setup") == setup][-5:]
        if len(recent) >= 3 and all(not t.get("won") for t in recent[-3:]):
            self.knowledge.banned_setups[setup] = "3 consecutive live losses"
            self._log_improve(f"AUTO-BAN live setup `{setup}` after 3 losses")

        # Agent weight nudge
        if agents:
            for name in agents:
                key = str(name).upper()
                w = self.knowledge.agent_weights.get(key, 1.0)
                if won:
                    w = min(2.0, w * 1.02 + 0.01)
                else:
                    w = max(0.35, w * 0.98 - 0.01)
                self.knowledge.agent_weights[key] = round(w, 3)

        # Also feed classic AI-Learner if available
        try:
            from ai_learner import AILearnerAgent
            AILearnerAgent().learn(
                direction_correct=bool(won),
                conf=float(conf),
                won=bool(won),
                pnl=float(r_multiple),
                features=None,
            )
        except Exception:
            pass

        # Periodic re-mine on rolling live+hist window
        if self.knowledge.total_live_trades % 10 == 0:
            flaws = self._mine_flaws(self._trade_log[-200:], min_n=5)
            for f in flaws:
                if f.kind == "setup" and f.action == "ban":
                    self.knowledge.banned_setups[f.key] = f.detail
                    self._log_improve(f"Rolling BAN `{f.key}`")

        self._save()

    # ------------------------------------------------------------------ #
    # Live gate — automatic, no command needed
    # ------------------------------------------------------------------ #
    def advise_signal(
        self,
        direction: str = "",
        score: float = 0.0,
        rr: float = 0.0,
        agreement: int = 0,
        conviction: float = 0.0,
        regime: str = "",
        setup: str = "",
        hour: Optional[int] = None,
        weekday: Optional[int] = None,
        is_scalp: bool = False,
    ) -> Tuple[bool, str, float]:
        """
        Returns (allow, reason, size_multiplier).
        Call on every candidate signal — fully automatic filter.
        """
        k = self.knowledge
        if not getattr(k, "enabled", True):
            return True, "AUTO: brain filtering is disabled", 1.0

        now = datetime.now(timezone.utc)
        hour = hour if hour is not None else now.hour
        weekday = weekday if weekday is not None else now.weekday()

        if hour in k.banned_hours:
            return False, f"AUTO: banned hour {hour} UTC (learned)", 0.0
        if weekday in k.banned_weekdays:
            return False, f"AUTO: banned weekday {weekday} (learned)", 0.0
        if setup and setup in k.banned_setups:
            return False, f"AUTO: banned setup ({k.banned_setups[setup]})", 0.0

        # Adjust minimum thresholds for micro-scalps if scalp mode is active
        min_score = min(1.0, k.min_score) if is_scalp else k.min_score
        min_rr = 0.95 if is_scalp else k.min_rr
        min_agreement = min(2, k.min_agreement) if is_scalp else k.min_agreement
        conv_floor = min(0.45, k.conviction_floor) if is_scalp else k.conviction_floor

        if score and score < min_score:
            tag = "scalp score" if is_scalp else "score"
            return False, f"AUTO: {tag} {score:.2f} < learned min {min_score:.2f}", 0.0
        if rr and rr < min_rr:
            tag = "scalp R:R" if is_scalp else "R:R"
            return False, f"AUTO: {tag} {rr:.2f} < learned min {min_rr:.2f}", 0.0
        if agreement and agreement < min_agreement:
            tag = "scalp agreement" if is_scalp else "agreement"
            return False, f"AUTO: {tag} {agreement} < learned min {min_agreement}", 0.0
        if conviction and conviction < conv_floor:
            tag = "scalp conviction" if is_scalp else "conviction"
            return False, f"AUTO: {tag} {conviction:.0%} < floor {conv_floor:.0%}", 0.0

        size_mult = 1.0
        if regime and regime in k.size_mult_by_regime:
            size_mult *= k.size_mult_by_regime[regime]

        return True, "AUTO: pass learned filters", size_mult

    def set_enabled(self, enabled: bool) -> bool:
        """Enable or pause autonomous learned signal filtering."""
        self.knowledge.enabled = bool(enabled)
        state_str = "ENABLED" if self.knowledge.enabled else "PAUSED"
        self._log_improve(f"Learner signal filtering {state_str}")
        self._save()
        return self.knowledge.enabled

    def reset_memory(self) -> Dict[str, Any]:
        """Reset learned memory, filters, and bans back to clean default baseline."""
        self.knowledge = Knowledge(
            agent_weights=dict(self.DEFAULT_WEIGHTS),
            min_score=1.8,
            min_rr=1.8,
            min_agreement=3,
            conviction_floor=0.55,
            enabled=True,
            total_history_trades=0,
            total_live_trades=0,
            last_bootstrap="",
            last_correction=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            improvement_log=[f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}] Memory reset to clean baseline"],
        )
        self._trade_log.clear()
        self._save()
        return {
            "status": "reset",
            "enabled": self.knowledge.enabled,
            "min_score": self.knowledge.min_score,
            "min_rr": self.knowledge.min_rr,
            "min_agreement": self.knowledge.min_agreement,
            "conviction_floor": self.knowledge.conviction_floor,
            "banned_setups": 0,
            "banned_hours": [],
            "banned_weekdays": [],
        }

    def get_rules(self) -> Dict[str, Any]:
        """Return structured dictionary of active learned filters and rules."""
        k = self.knowledge
        return {
            "enabled": getattr(k, "enabled", True),
            "min_score": k.min_score,
            "min_rr": k.min_rr,
            "min_agreement": k.min_agreement,
            "conviction_floor": k.conviction_floor,
            "banned_hours": list(k.banned_hours),
            "banned_weekdays": list(k.banned_weekdays),
            "banned_setups": dict(k.banned_setups),
            "weak_regimes": dict(k.weak_regimes),
            "size_mult_by_regime": dict(k.size_mult_by_regime),
            "agent_weights": dict(k.agent_weights),
        }

    def format_rules(self) -> str:
        """User-friendly markdown report of active rules and thresholds."""
        k = self.knowledge
        status_icon = "🟢 ACTIVE" if getattr(k, "enabled", True) else "🔴 PAUSED"
        weekday_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
        banned_wd_str = ", ".join(f"{d} ({weekday_map.get(d, '?')})" for d in k.banned_weekdays) or "None"
        banned_hrs_str = ", ".join(f"{h:02d}:00 UTC" for h in k.banned_hours) or "None"

        lines = [
            "📜 *Autonomous Learner Rules & Active Filters*",
            f"• Filter Gate: `{status_icon}`",
            "",
            "🎯 *Learned Thresholds:*",
            f"• Min Score: `{k.min_score:.2f}`",
            f"• Min R:R Ratio: `1:{k.min_rr:.2f}`",
            f"• Min Agent Agreement: `{k.min_agreement}` agents",
            f"• Conviction Floor: `{k.conviction_floor:.0%}`",
            f"• Scalp Overrides: `RR ≥ 1:0.95 | Score ≥ 1.00 | Agree ≥ 2`",
            "",
            "🚫 *Learned Bans:*",
            f"• Banned UTC Hours: `{banned_hrs_str}`",
            f"• Banned Weekdays: `{banned_wd_str}`",
            f"• Banned Setups: `{len(k.banned_setups)} active`",
        ]
        if k.banned_setups:
            for s, reason in list(k.banned_setups.items())[:8]:
                clean_r = str(reason).replace("`", "'")
                lines.append(f"  - `{s}`: _{clean_r}_")
            if len(k.banned_setups) > 8:
                lines.append(f"  - _...and {len(k.banned_setups) - 8} more_")

        lines.append("")
        lines.append("⚖️ *Regime Multipliers:*")
        if k.size_mult_by_regime:
            for reg, mult in sorted(k.size_mult_by_regime.items()):
                lines.append(f"• `{reg}`: `{mult:.2f}x`")
        else:
            lines.append("• _Standard 1.00x across all regimes_")

        lines.append("")
        lines.append("🤖 *Agent Weights:*")
        sorted_weights = sorted(k.agent_weights.items(), key=lambda x: x[1], reverse=True)
        for agent, weight in sorted_weights:
            lines.append(f"• `{agent}`: `{weight:.2f}`")

        return "\n".join(lines)

    def get_flaws(self) -> List[Flaw]:
        """Mine flaws from accumulated trade history and live trades."""
        if not self._trade_log:
            return []
        return self._mine_flaws(self._trade_log, min_n=3)

    def format_flaws(self) -> str:
        """User-friendly markdown report of mined flaws and corrective actions."""
        k = self.knowledge
        flaws = self.get_flaws()
        lines = [
            "🔍 *Autonomous Learner Flaw Mining & Memory*",
            f"Logged Trades: `{len(self._trade_log)}` (History: `{k.total_history_trades}`, Live: `{k.total_live_trades}`)",
            "",
        ]
        if flaws:
            lines.append(f"*Active Mined Flaws ({len(flaws)}):*")
            for f in flaws[:10]:
                icon = "🚫" if f.action == "ban" else "⚠️"
                lines.append(
                    f"• {icon} [{f.kind.upper()}] `{f.key}`: "
                    f"n={f.trades}, exp={f.expectancy:+.2f}R, WR={f.winrate:.1f}% -> `{f.action.upper()}` ({f.detail})"
                )
            if len(flaws) > 10:
                lines.append(f"• _...and {len(flaws) - 10} more flaws_")
        else:
            lines.append("• _No active critical statistical flaws detected in current sample._")

        if k.banned_setups or k.banned_hours or k.banned_weekdays or k.weak_regimes:
            lines.append("")
            lines.append("*Current Active Flaw Sanctions:*")
            if k.banned_hours:
                lines.append(f"• Banned UTC Hours: `{k.banned_hours}`")
            if k.banned_weekdays:
                lines.append(f"• Banned Weekdays: `{k.banned_weekdays}`")
            if k.weak_regimes:
                lines.append(f"• Weak Regimes: `{k.weak_regimes}`")
            if k.banned_setups:
                lines.append(f"• Banned Setups ({len(k.banned_setups)}):")
                for s, r in list(k.banned_setups.items())[:6]:
                    clean_r = str(r).replace("`", "'")
                    lines.append(f"  - `{s}`: _{clean_r}_")
                if len(k.banned_setups) > 6:
                    lines.append(f"  - _...and {len(k.banned_setups) - 6} more_")
        return "\n".join(lines)

    def get_agent_weights(self) -> Dict[str, float]:
        return dict(self.knowledge.agent_weights)

    def apply_to_arbiter(self, arbiter) -> None:
        """Push learned weights + thresholds into a live Arbiter instance."""
        try:
            arbiter.WEIGHTS = dict(self.knowledge.agent_weights)
            arbiter.MIN_SCORE = self.knowledge.min_score
            arbiter.MIN_AGREEMENT = self.knowledge.min_agreement
        except Exception as e:
            logger.debug("apply_to_arbiter: %s", e)

    def summary(self) -> str:
        k = self.knowledge
        status_icon = "🟢 ACTIVE" if getattr(k, "enabled", True) else "🔴 PAUSED"
        lines = [
            "🧠 *Autonomous Learner*",
            f"Status: `{status_icon}`",
            f"History trades: `{k.total_history_trades}` | Live: `{k.total_live_trades}`",
            f"Last bootstrap: `{k.last_bootstrap or 'never'}`",
            f"Filters: score≥`{k.min_score:.2f}` RR≥`{k.min_rr:.2f}` "
            f"conv≥`{k.conviction_floor:.0%}` agree≥`{k.min_agreement}`",
            f"Banned: setups=`{len(k.banned_setups)}` | hours=`{k.banned_hours}` | wd=`{k.banned_weekdays}`",
        ]
        if k.size_mult_by_regime:
            lines.append(f"Regime size mults: `{k.size_mult_by_regime}`")
        if k.agent_weights:
            w_str = ", ".join(f"{a}:{w:.2f}" for a, w in sorted(k.agent_weights.items(), key=lambda x: -x[1])[:5])
            lines.append(f"Top agents: `{w_str}`")
        if k.improvement_log:
            lines.append("*Recent self-corrections:*")
            for e in k.improvement_log[-5:]:
                clean_e = str(e).replace("`", "'")
                lines.append(f"• `{clean_e}`")
        return "\n".join(lines)



# Module-level singleton for zero-command use
_brain: Optional[AutonomousLearner] = None


def get_brain(auto_bootstrap: bool = True) -> AutonomousLearner:
    global _brain
    if _brain is None:
        _brain = AutonomousLearner(auto_bootstrap=auto_bootstrap)
    return _brain


def auto_start():
    """Call once at process start — learns in background-safe way."""
    brain = get_brain(auto_bootstrap=True)
    logger.info("Autonomous learner ready\n%s", brain.summary())
    return brain


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    b = AutonomousLearner(auto_bootstrap=False)
    print(b.bootstrap(symbol="XAUUSD", years=5.0))
    print(b.summary())
