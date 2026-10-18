#!/usr/bin/env python3
"""
MULTI-AGENT TRADING BOT
=======================
Institutional multi-agent consensus engine.
Integrates improved modules (data_feed, ai_learner, news_agent, smc_liquidity,
session_filter, market_regime) when available, with graceful fallback.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional

import numpy as np
import pandas as pd

import config
from execution_engine import ExecutionEngine, TradeOrder

# Backward-compatible alias (main.py historically imported Position)
Position = TradeOrder
from risk_manager import RiskManager
from trading_strategy_indicators_pro import IndicatorEngine

try:
    from data_feed import DataFeed as _ImprovedDataFeed
except ImportError:
    _ImprovedDataFeed = None

try:
    from ai_learner import AILearnerAgent as _ImprovedAILearner
except ImportError:
    _ImprovedAILearner = None

try:
    from news_agent import NewsAgent as _ImprovedNewsAgent
except ImportError:
    _ImprovedNewsAgent = None

try:
    from smc_liquidity import best_smc_vote, StructureDir as SMCDir
except ImportError:
    best_smc_vote = None
    SMCDir = None

try:
    from session_filter import is_tradeable_session
except ImportError:
    is_tradeable_session = None

try:
    from market_regime import classify_market_regime
except ImportError:
    classify_market_regime = None

try:
    from gautam_jha_strategy import GautamJhaAgent, GJDir
except ImportError:
    GautamJhaAgent = None
    GJDir = None

try:
    from mean_reversion_vwap_strategy import MeanReversionVWAPAgent, MRDir
except ImportError:
    MeanReversionVWAPAgent = None
    MRDir = None

try:
    from fake_breakout_strategy import FakeBreakoutAgent, FBDir
except ImportError:
    FakeBreakoutAgent = None
    FBDir = None

try:
    from mtf_fvg_ob import MTFConfluenceAgent, MTFDir
except ImportError:
    MTFConfluenceAgent = None
    MTFDir = None

try:
    from asian_range_breakout import AsianBreakoutAgent, AsianDir
except ImportError:
    AsianBreakoutAgent = None
    AsianDir = None

MEMORY_FILE = "multi_agent_memory.json"


class Dir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


def _norm_trade_symbol(symbol: str) -> str:
    s = (symbol or "").upper().replace("/", "").replace("-", "").replace(":", "").strip()
    if s in ("BTC", "BTCUSD", "XBT", "XBTUSD", "BITCOIN"):
        return "BTCUSDT"
    if s in ("GOLD", "XAU", "XAUT", "XAUTUSD", "XAUUSD", "PAXG", "PAXGUSDT"):
        return "XAUUSD"
    if s.endswith("USD") and not s.endswith("USDT") and any(x in s for x in ("BTC", "XAU", "GOLD", "PAXG", "XAUT")):
        return s[:-3] + "USDT"
    return s or "XAUUSD"


class MarketDataFeed:
    def __init__(self, symbol: str = "XAUUSD", source: str = "demo", exchange_id: str = "binance"):
        self.symbol = _norm_trade_symbol(symbol)
        self.source = source
        self.exchange_id = exchange_id
        self._improved = None
        if _ImprovedDataFeed is not None:
            try:
                self._improved = _ImprovedDataFeed(
                    symbol=self.symbol, source=source, exchange_id=exchange_id, timeframe="1m"
                )
                print("[MultiAgent] Using improved data_feed.DataFeed")
            except Exception as e:
                print(f"[MultiAgent] Improved data_feed init failed: {e}")

    def get_latest_price(self) -> dict:
        try:
            from mt5_broker import get_active_broker
            b = get_active_broker()
            if b is not None and hasattr(b, "get_live_quote"):
                q = b.get_live_quote(self.symbol)
                if q.get("ok") and float(q.get("mark") or 0.0) > 0.0:
                    return q
        except Exception:
            pass
        try:
            from realtime_prices import fetch_spot_price
            rt = fetch_spot_price(self.symbol)
            if rt.get("ok") and float(rt.get("mark") or 0) > 0:
                return rt
        except Exception:
            pass
        if self._improved is not None:
            try:
                return self._improved.get_latest_price()
            except Exception:
                pass
        try:
            import ccxt
            exchange = getattr(ccxt, self.exchange_id)()
            sym = self.symbol
            if sym in ("XAUUSD", "XAUTUSD", "GOLD"):
                sym = "PAXG/USDT" if self.exchange_id == "binance" else "XAU/USDT"
            elif "/" not in sym:
                sym = f"{sym[:-4]}/USDT" if sym.endswith("USDT") else (f"{sym[:-3]}/USDT" if sym.endswith("USD") else f"{sym}/USDT")
            ticker = exchange.fetch_ticker(sym)
            last = float(ticker.get("last") or ticker.get("close") or 0)
            return {"ok": True, "mark": last, "close": last, "source": self.exchange_id}
        except Exception:
            pass
        try:
            import yfinance as yf
            yf_sym = self.symbol
            if yf_sym in ("XAUUSD", "XAUTUSD", "GOLD"):
                yf_sym = "GC=F"
            elif yf_sym.endswith("USDT"):
                yf_sym = f"{yf_sym[:-4]}-USD"
            elif yf_sym.endswith("USD") and not yf_sym.endswith("=X"):
                yf_sym = f"{yf_sym[:-3]}-USD"
            t = yf.Ticker(yf_sym)
            hist = t.history(period="1d")
            if not hist.empty:
                last = float(hist["Close"].iloc[-1])
                return {"ok": True, "mark": last, "close": last, "source": "yfinance"}
        except Exception:
            pass
        try:
            from tradingview_feed import fetch_tradingview_quote
            return fetch_tradingview_quote(self.symbol)
        except Exception:
            return {"ok": False, "mark": 0.0, "error": "no price"}

    def fetch(self, limit: int = 1500, timeframe: Optional[str] = None) -> pd.DataFrame:
        try:
            from mt5_broker import get_active_broker
            b = get_active_broker()
            if b is not None and hasattr(b, "get_candles"):
                df_mt5 = b.get_candles(self.symbol, timeframe=timeframe or "1m", limit=limit)
                if df_mt5 is not None and not df_mt5.empty and len(df_mt5) >= 3:
                    return df_mt5
        except Exception:
            pass
        if self._improved is not None:
            try:
                df = self._improved.fetch(limit=limit, timeframe=timeframe or "1m")
                if df is not None and not df.empty:
                    return df
            except Exception:
                pass
        if self.source == "demo":
            return self._demo(limit)
        try:
            from tradingview_feed import fetch_tradingview_bars
            raw = fetch_tradingview_bars(self.symbol, timeframe or "1", limit)
            if raw:
                df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                df = df.set_index("timestamp")
                return df
        except Exception:
            pass
        return self._demo(limit)

    def _demo(self, limit: int = 600, base_price: float = None) -> pd.DataFrame:
        if base_price is None:
            base_price = 2650.0
            try:
                from realtime_prices import fetch_spot_price
                rt = fetch_spot_price(self.symbol)
                if rt.get("ok") and float(rt.get("mark") or 0) > 0:
                    base_price = float(rt["mark"])
            except Exception:
                sym = (self.symbol or "").upper()
                if "BTC" in sym:
                    base_price = 82000.0
                elif "XAU" in sym or "GOLD" in sym:
                    base_price = 4100.0
        dates = pd.date_range(end=datetime.now(timezone.utc), periods=limit, freq="1min")
        rng = np.random.default_rng(42)
        tick = max(base_price * 0.0002, 0.3)
        rets = rng.normal(0, tick, limit)
        p = base_price + np.cumsum(rets)
        highs = p + rng.uniform(tick * 0.3, tick * 2, limit)
        lows = p - rng.uniform(tick * 0.3, tick * 2, limit)
        closes = p + rng.normal(0, 0.1, limit)
        volumes = rng.integers(50, 1000, limit)
        return pd.DataFrame({"open": p, "high": highs, "low": lows, "close": closes, "volume": volumes}, index=dates)

    def multi_tf(self, limit_1m: int = 1500) -> Dict[str, pd.DataFrame]:
        df1 = self.fetch(limit=limit_1m, timeframe="1m")
        out = {"1m": df1}
        for tf, rule in [("5m", "5min"), ("15m", "15min"), ("1h", "1h")]:
            try:
                out[tf] = df1.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
            except Exception:
                out[tf] = pd.DataFrame()
        try:
            out["daily"] = df1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        except Exception:
            out["daily"] = pd.DataFrame()
        return out


def atr(df: pd.DataFrame, p: int = 14) -> float:
    if len(df) < p + 1:
        return 0.0
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    return float(np.mean(tr[-p:])) if len(tr) >= p else 0.0


def detect_trend(df: pd.DataFrame, lb: int = 10) -> Dir:
    if len(df) < lb + 5:
        return Dir.FLAT
    c = df["close"].values
    ema_f = pd.Series(c).ewm(span=9, adjust=False).mean().iloc[-1]
    ema_s = pd.Series(c).ewm(span=21, adjust=False).mean().iloc[-1]
    if ema_f > ema_s and c[-1] > ema_f:
        return Dir.LONG
    if ema_f < ema_s and c[-1] < ema_f:
        return Dir.SHORT
    return Dir.FLAT


@dataclass
class Vote:
    agent: str
    direction: Dir
    strength: float
    reason: str = ""


class TrendAgent:
    name = "TREND"

    def vote(self, df: pd.DataFrame, *args, **kwargs) -> Vote:
        d = detect_trend(df)
        if d == Dir.FLAT:
            return Vote(self.name, Dir.FLAT, 0.0, "No clear trend")
        strength = 0.65
        return Vote(self.name, d, strength, f"EMA trend {d.name}")


class LiquidityAgent:
    """Advanced SMC Liquidity Agent – uses smc_liquidity when available."""
    name = "LIQUIDITY"

    def vote(self, df: pd.DataFrame, daily: Optional[pd.DataFrame] = None) -> Vote:
        if best_smc_vote is not None:
            try:
                direction, strength, reason = best_smc_vote(df, daily if daily is not None and not daily.empty else None)
                if direction == SMCDir.BULLISH:
                    return Vote(self.name, Dir.LONG, float(strength), reason)
                if direction == SMCDir.BEARISH:
                    return Vote(self.name, Dir.SHORT, float(strength), reason)
                return Vote(self.name, Dir.FLAT, 0.0, reason)
            except Exception:
                pass
        # Fallback classic SMC heuristics
        if len(df) < 20:
            return Vote(self.name, Dir.FLAT, 0.0, "Need more bars")
        if daily is not None and not daily.empty and len(daily) >= 2:
            pdh = float(daily.iloc[-2]["high"])
            pdl = float(daily.iloc[-2]["low"])
            prev, curr = df.iloc[-2], df.iloc[-1]
            if float(prev["high"]) > pdh and float(curr["close"]) < pdh and curr["close"] < curr["open"]:
                return Vote(self.name, Dir.SHORT, 0.85, "PDH Liquidity Swept + Bearish Rejection")
            if float(prev["low"]) < pdl and float(curr["close"]) > pdl and curr["close"] > curr["open"]:
                return Vote(self.name, Dir.LONG, 0.85, "PDL Liquidity Swept + Bullish Rejection")
        highs = df["high"].iloc[-12:-1]
        lows = df["low"].iloc[-12:-1]
        curr = df.iloc[-1]
        if float(curr["close"]) > float(highs.max()):
            return Vote(self.name, Dir.LONG, 0.70, "BOS Continuation Up")
        if float(curr["close"]) < float(lows.min()):
            return Vote(self.name, Dir.SHORT, 0.70, "BOS Continuation Down")
        return Vote(self.name, Dir.FLAT, 0.0, "No SMC setup firing")


class IndicatorAgent:
    name = "INDICATORS"
    MIN_SCORE = 1.2

    def __init__(self):
        self.engine = IndicatorEngine()

    def vote(self, df: pd.DataFrame) -> Vote:
        if len(df) < 60:
            return Vote(self.name, Dir.FLAT, 0.0, "Need at least 60 bars")
        snap = self.engine.compute(df)
        score = snap.score
        summary = f"Score:{score:+.1f} | RSI:{snap.rsi:.0f} | ADX:{snap.adx:.0f}"
        if score >= self.MIN_SCORE:
            return Vote(self.name, Dir.LONG, min(abs(score) / 5.0, 1.0), f"Bullish Confluence ({summary})")
        if score <= -self.MIN_SCORE:
            return Vote(self.name, Dir.SHORT, min(abs(score) / 5.0, 1.0), f"Bearish Confluence ({summary})")
        return Vote(self.name, Dir.FLAT, 0.0, f"Neutral Confluence ({summary})")


class AILearnerAgent:
    name = "AI-LEARNER"

    def __init__(self, path: str = MEMORY_FILE):
        self.path = path
        self._improved = None
        if _ImprovedAILearner is not None:
            try:
                self._improved = _ImprovedAILearner(path)
            except Exception:
                pass
        self.memory = {"trades": 0, "wins": 0, "pnl": 0.0, "calibration": {}, "weights": {"momentum": 1.0, "slope": 1.0}, "min_conf": 0.64}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.memory.update(json.load(f))
            except Exception:
                pass

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.memory, f, indent=2)
        except Exception:
            pass

    def vote(self, df: pd.DataFrame) -> Vote:
        if self._improved is not None:
            try:
                v = self._improved.vote(df)
                d = v.direction
                if getattr(d, "name", None) == "LONG":
                    direction = Dir.LONG
                elif getattr(d, "name", None) == "SHORT":
                    direction = Dir.SHORT
                else:
                    direction = Dir.FLAT
                return Vote(self.name, direction, float(v.strength), v.reason)
            except Exception:
                pass
        if len(df) < 60:
            return Vote(self.name, Dir.FLAT, 0.0, "Need 60 bars")
        c = df["close"].values.astype(float)
        ret = (c[-1] - c[-15]) / (c[-15] + 1e-12)
        slope = np.polyfit(np.arange(20), c[-20:], 1)[0] / (c[-1] + 1e-12)
        score = ret * 50 + slope * 200
        conf = 1 / (1 + np.exp(-score))
        min_conf = float(self.memory.get("min_conf", 0.64))
        if conf >= min_conf:
            return Vote(self.name, Dir.LONG, float(conf), f"AI conf {conf:.0%}")
        if conf <= 1 - min_conf:
            return Vote(self.name, Dir.SHORT, float(1 - conf), f"AI conf {1-conf:.0%}")
        return Vote(self.name, Dir.FLAT, float(conf), f"AI below threshold ({conf:.0%})")


class NewsAgent:
    name = "NEWS"

    def __init__(self, enabled: bool = True, window_min: int = 30, **kwargs):
        self.enabled = bool(enabled)
        self.window_min = int(window_min)
        self._cal = None
        self._improved = None
        if self.enabled and _ImprovedNewsAgent is not None:
            try:
                try:
                    self._improved = _ImprovedNewsAgent(enabled=True, window_min=self.window_min)
                except TypeError:
                    self._improved = _ImprovedNewsAgent()
            except Exception:
                pass

    def blackout(self, ts=None):
        if not self.enabled:
            return False, "disabled"
        if getattr(self, "_cal", None):
            from datetime import datetime, timezone
            if ts is None:
                ts = datetime.now(timezone.utc)
            if getattr(ts, "tzinfo", None) is None:
                ts = ts.replace(tzinfo=timezone.utc)
            window = getattr(self, "window_min", 30) * 60
            for item in self._cal:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    evt_ts, title = item[0], item[1]
                elif isinstance(item, dict):
                    evt_ts, title = item.get("dt"), item.get("title", "")
                else:
                    continue
                if evt_ts is None:
                    continue
                if getattr(evt_ts, "tzinfo", None) is None:
                    evt_ts = evt_ts.replace(tzinfo=timezone.utc)
                if abs((evt_ts - ts).total_seconds()) <= window:
                    return True, f"High Impact Event: {title}"
            return False, "ok"
        if self._improved is not None and hasattr(self._improved, "blackout"):
            try:
                return self._improved.blackout(ts)
            except Exception as e:
                return False, str(e)
        return False, "no calendar"

    def vote(self) -> Vote:
        if not self.enabled:
            return Vote(self.name, Dir.FLAT, 0.0, "News filter disabled")
        if self._improved is not None:
            try:
                v = self._improved.vote()
                d = v.direction
                if getattr(d, "name", None) == "LONG":
                    direction = Dir.LONG
                elif getattr(d, "name", None) == "SHORT":
                    direction = Dir.SHORT
                else:
                    direction = Dir.FLAT
                return Vote(self.name, direction, float(getattr(v, "strength", 0.0)), getattr(v, "reason", ""))
            except Exception:
                pass
        return Vote(self.name, Dir.FLAT, 0.0, "No high-impact news filter active")


class Arbiter:
    WEIGHTS = {
        "TREND": 1.25,
        "LIQUIDITY": 1.40,
        "INDICATORS": 1.00,
        "AI-LEARNER": 0.90,
        "NEWS": 0.70,
        "GAUTAM-JHA": 1.55,
        "MEAN-REVERSION": 1.35,
        "FAKE-BREAKOUT": 1.35,
        "MTF-CONFLUENCE": 1.40,
        "ASIAN-BREAKOUT": 1.25,
    }
    MIN_AGREEMENT = 2
    MIN_SCORE = 1.2

    def __init__(self, require_killzone: Optional[bool] = None, allow_asian: Optional[bool] = None):
        self.require_killzone = require_killzone if require_killzone is not None else False
        self.allow_asian = allow_asian if allow_asian is not None else True
        self.is_scalp = False
        self.fast_market_fill = False
        self.scalp_sl_pct = 0.12
        self.scalp_tp_pct = 0.10
        self.scalp_tp_usd = 2.50
        self.scalp_sl_usd = 3.00
        self.strict_htf = False
        try:
            import config
            prof_name = getattr(config, "CURRENT_PROFILE", "scalp")
            from strategy_profiles import get_profile
            prof = get_profile(prof_name)
            self.WEIGHTS = dict(prof.weights)
            self.MIN_AGREEMENT = prof.min_agreement
            self.MIN_SCORE = prof.min_score
            if require_killzone is None:
                self.require_killzone = prof.require_killzone
            if allow_asian is None:
                self.allow_asian = prof.allow_asian
            self.strict_htf = prof.strict_htf
            self.is_scalp = getattr(prof, "is_scalp", False)
            self.scalp_sl_pct = getattr(prof, "scalp_sl_pct", 0.12)
            self.scalp_tp_pct = getattr(prof, "scalp_tp_pct", 0.10)
            self.scalp_tp_usd = getattr(prof, "scalp_tp_usd", 2.50)
            self.scalp_sl_usd = getattr(prof, "scalp_sl_usd", 3.00)
            self.fast_market_fill = getattr(prof, "fast_market_fill", False)
        except Exception:
            pass
        self.trend = TrendAgent()
        self.liq = LiquidityAgent()
        self.ind = IndicatorAgent()
        self.ai = AILearnerAgent()
        self.news = NewsAgent()
        self.gautam = GautamJhaAgent() if GautamJhaAgent is not None else None
        self.mr = MeanReversionVWAPAgent() if MeanReversionVWAPAgent is not None else None
        self.fb = FakeBreakoutAgent() if FakeBreakoutAgent is not None else None
        self.mtf = MTFConfluenceAgent() if MTFConfluenceAgent is not None else None
        self.asian = AsianBreakoutAgent() if AsianBreakoutAgent is not None else None

    def decide(self, df1, df5, df15, df1h, daily, risk_mgr, symbol="XAUUSD", on_demand: bool = False):
        votes = [
            self.trend.vote(df1),
            self.liq.vote(df1, daily if daily is not None else pd.DataFrame()),
            self.ind.vote(df1),
            self.ai.vote(df1),
            self.news.vote(),
        ]
        if getattr(self, "gautam", None) is not None:
            try:
                gv = self.gautam.vote(df1, daily if daily is not None else pd.DataFrame())
                dname = getattr(gv.direction, "name", str(gv.direction))
                direction = Dir.LONG if dname == "LONG" else (Dir.SHORT if dname == "SHORT" else Dir.FLAT)
                votes.append(Vote(gv.agent, direction, float(gv.strength), gv.reason))
            except Exception:
                pass

        if getattr(self, "mr", None) is not None:
            try:
                mv = self.mr.vote(df1, daily if daily is not None else pd.DataFrame())
                dname = getattr(mv.direction, "name", str(mv.direction))
                direction = Dir.LONG if dname == "LONG" else (Dir.SHORT if dname == "SHORT" else Dir.FLAT)
                votes.append(Vote(mv.agent, direction, float(mv.strength), mv.reason))
            except Exception:
                pass

        if getattr(self, "fb", None) is not None:
            try:
                fb_v = self.fb.vote(df1, daily if daily is not None else pd.DataFrame())
                dname = getattr(fb_v.direction, "name", str(fb_v.direction))
                direction = Dir.LONG if dname == "LONG" else (Dir.SHORT if dname == "SHORT" else Dir.FLAT)
                votes.append(Vote(fb_v.agent, direction, float(fb_v.strength), fb_v.reason))
            except Exception:
                pass

        if getattr(self, "mtf", None) is not None:
            try:
                mtf_v = self.mtf.vote(df1, df5=df5, df15=df15, df1h=df1h)
                dname = getattr(mtf_v.direction, "name", str(mtf_v.direction)).upper()
                direction = Dir.LONG if "BULLISH" in dname else (Dir.SHORT if "BEARISH" in dname else Dir.FLAT)
                votes.append(Vote(mtf_v.agent, direction, float(mtf_v.strength), mtf_v.reason))
            except Exception:
                pass

        if getattr(self, "asian", None) is not None:
            try:
                av = self.asian.vote(df1, daily if daily is not None else pd.DataFrame())
                dname = getattr(av.direction, "name", str(av.direction)).upper()
                direction = Dir.LONG if "LONG" in dname else (Dir.SHORT if "SHORT" in dname else Dir.FLAT)
                votes.append(Vote(av.agent, direction, float(av.strength), av.reason))
            except Exception:
                pass

        session_warn = None
        if is_tradeable_session is not None:
            try:
                ok, reason = is_tradeable_session(
                    require_killzone=self.require_killzone,
                    allow_asian=self.allow_asian,
                    symbol=symbol,
                )
                if not ok:
                    if not on_demand:
                        return None, f"SESSION: {reason}", votes
                    session_warn = f"Session note: {reason}"
            except Exception:
                pass

        long_score = short_score = 0.0
        long_n = short_n = 0
        for v in votes:
            w = self.WEIGHTS.get(v.agent, 1.0)
            if v.direction == Dir.LONG:
                long_score += w * v.strength
                long_n += 1
            elif v.direction == Dir.SHORT:
                short_score += w * v.strength
                short_n += 1

        direction = Dir.FLAT
        score = 0.0
        if long_n >= self.MIN_AGREEMENT and long_score >= self.MIN_SCORE and long_score > short_score:
            direction = Dir.LONG
            score = long_score
        elif short_n >= self.MIN_AGREEMENT and short_score >= self.MIN_SCORE and short_score > long_score:
            direction = Dir.SHORT
            score = short_score

        if direction == Dir.FLAT:
            msg = f"No consensus (L:{long_score:.2f}/{long_n} S:{short_score:.2f}/{short_n})"
            if session_warn:
                msg += f" | {session_warn}"
            return None, msg, votes

        entry = float(df1["close"].iloc[-1])
        if getattr(self, "is_scalp", False):
            sl_pct = float(getattr(self, "scalp_sl_pct", 0.12)) / 100.0
            atr_val = atr(df1) or (entry * 0.001)
            stop_dist = min(max(atr_val * 0.5, entry * (sl_pct * 0.6)), entry * (sl_pct * 1.5))
            if direction == Dir.LONG:
                stop = entry - stop_dist
                tp1 = entry + stop_dist * 1.0
                tp2 = entry + stop_dist * 1.5
            else:
                stop = entry + stop_dist
                tp1 = entry - stop_dist * 1.0
                tp2 = entry - stop_dist * 1.5
        else:
            atr_val = atr(df1) or entry * 0.002
            min_stop_dist = entry * 0.003
            stop_dist = max(atr_val * 1.5, min_stop_dist)
            if direction == Dir.LONG:
                stop = entry - stop_dist
            else:
                stop = entry + stop_dist
            tp1 = tp2 = None

        plan = risk_mgr.position_size(entry=entry, stop=stop, symbol=symbol)
        if plan.lots <= 0:
            return None, f"Size blocked: {plan.reason}", votes

        regime = "unknown"
        if classify_market_regime is not None:
            try:
                regime = classify_market_regime(df1)
            except Exception:
                pass

        final_tp1 = tp1 if (getattr(self, "is_scalp", False) and tp1 is not None) else plan.tp1
        final_tp2 = tp2 if (getattr(self, "is_scalp", False) and tp2 is not None) else plan.tp2
        target_tp_eval = final_tp1 if getattr(self, "is_scalp", False) else (final_tp2 or final_tp1)
        rr_calc = round(abs(target_tp_eval - entry) / abs(entry - stop), 2) if abs(entry - stop) else 0
        sig = {
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "tp1": final_tp1,
            "tp2": final_tp2,
            "tp3": getattr(plan, "tp3", None),
            "lots": plan.lots,
            "size": plan.size,
            "leverage": plan.leverage,
            "margin_required": plan.margin_required,
            "risk_amount": plan.risk_amount,
            "rr": rr_calc,
            "score": score,
            "regime": regime,
            "reason": (
                f"Scalp {direction.name} score={score:.2f} (micro SL/TP)"
                if getattr(self, "is_scalp", False)
                else f"Consensus {direction.name} score={score:.2f}"
            ),
            "symbol": symbol,
            "is_scalp": bool(getattr(self, "is_scalp", False)),
            "fast_market_fill": bool(getattr(self, "fast_market_fill", False)),
            "lot_mode": getattr(plan, "lot_mode", "auto"),
        }
        status_msg = (
            f"⚡ SCALP {direction.name} score={score:.2f} (micro SL/TP)"
            if getattr(self, "is_scalp", False)
            else f"SETUP {direction.name} score={score:.2f}"
        )
        if session_warn:
            status_msg += f" ({session_warn})"
        return sig, status_msg, votes


def evaluate_symbol(symbol="XAUUSD", source="demo", exchange_id="binance", leverage=None, on_demand: bool = True):
    symbol = _norm_trade_symbol(symbol)
    feed = MarketDataFeed(symbol=symbol, source=source, exchange_id=exchange_id)
    data = feed.multi_tf(limit_1m=1500)
    if not data or data["1m"].empty:
        return {"symbol": symbol, "signal": None, "status": "No data", "votes": [], "price": 0.0}
    risk = RiskManager(equity=getattr(config, "INITIAL_EQUITY", 10000.0), symbol=symbol)
    if leverage:
        risk.set_leverage(leverage)
    arb = Arbiter()
    sig, why, votes = arb.decide(
        data["1m"], data.get("5m"), data.get("15m"), data.get("1h"), data.get("daily"), risk, symbol=symbol, on_demand=on_demand
    )
    quote = feed.get_latest_price()
    mark = 0.0
    try:
        mark = float(quote.get("mark") or 0.0)
    except Exception:
        mark = 0.0
    if mark <= 0:
        try:
            mark = float(data["1m"]["close"].iloc[-1])
        except Exception:
            mark = 0.0
    if "BTC" in symbol and 0 < mark < 1000:
        try:
            from realtime_prices import fetch_spot_price
            rt = fetch_spot_price("BTCUSDT")
            if rt.get("ok") and float(rt.get("mark") or 0) > 1000:
                mark = float(rt["mark"])
        except Exception:
            pass
    votes_out = [
        {"agent": v.agent, "direction": v.direction.name, "strength": round(v.strength, 2), "reason": v.reason}
        for v in votes
    ]
    return {
        "symbol": symbol,
        "price": mark,
        "signal": sig,
        "status": why,
        "votes": votes_out,
        "risk_summary": risk.summary() if hasattr(risk, "summary") else "",
    }


def send_telegram(msg: str):
    """Optional helper; actual sends go through main.py bot instance."""
    print(f"[Telegram] {msg[:200]}")


def run_backtest(args=None):
    """
    Run strategy backtest suite.
    Delegates to auto_backtest.run_auto_backtest with fallback to AdvancedSimulator.
    """
    symbol = (getattr(args, "symbol", None) or (args.get("symbol") if isinstance(args, dict) else None) or config.DEFAULT_SYMBOL).upper()
    bars = int(getattr(args, "bars", None) or (args.get("bars") if isinstance(args, dict) else None) or 2000)
    source = getattr(args, "source", None) or (args.get("source") if isinstance(args, dict) else None) or config.DEFAULT_DATA_SOURCE
    equity = float(getattr(args, "equity", None) or (args.get("equity") if isinstance(args, dict) else None) or config.INITIAL_EQUITY)
    risk = float(getattr(args, "risk", None) or (args.get("risk") if isinstance(args, dict) else None) or config.DEFAULT_RISK_PCT)
    if risk > 1.0:
        risk = risk / 100.0

    print("\n==================================================")
    print(f" [Multi-Agent Backtest] Symbol: {symbol} | Bars: {bars}")
    print(f" Source: {source} | Equity: ${equity:,.2f} | Risk: {risk:.2%}")
    print("==================================================\n")

    try:
        from auto_backtest import run_auto_backtest
        report = run_auto_backtest(
            symbol=symbol,
            bars=bars,
            source=source,
            equity=equity,
            risk=risk,
            mc_runs=getattr(args, "mc_runs", 300) if not isinstance(args, dict) else args.get("mc_runs", 300),
            wf_windows=getattr(args, "wf_windows", 4) if not isinstance(args, dict) else args.get("wf_windows", 4),
            do_sweep=True,
        )
        print(report.text(max_len=100000))
        return report
    except Exception as exc:
        print(f"[Multi-Agent Backtest] auto_backtest fallback notice: {exc}")
        from advanced_backtest import load_data, SimConfig, AdvancedSimulator, ema_cross_signal
        df = load_data(symbol, bars, source, None)
        cfg = SimConfig(initial_equity=equity, risk_per_trade=risk)
        sim = AdvancedSimulator(cfg)
        metrics, trades, _ = sim.run(df, ema_cross_signal, symbol)
        print(metrics.summary())
        return metrics


def run_live(args=None):
    """
    Run live multi-agent trading loop.
    Monitors market candles, tracks positions & setups via ExecutionEngine,
    and queries Arbiter for consensus trading setups.
    """
    import time

    symbol = (getattr(args, "symbol", None) or (args.get("symbol") if isinstance(args, dict) else None) or config.DEFAULT_SYMBOL).upper()
    source = getattr(args, "source", None) or (args.get("source") if isinstance(args, dict) else None) or config.DEFAULT_DATA_SOURCE
    exchange = getattr(args, "exchange", None) or (args.get("exchange") if isinstance(args, dict) else None) or config.DEFAULT_EXCHANGE
    poll_sec = int(getattr(args, "poll", None) or (args.get("poll") if isinstance(args, dict) else None) or config.POLL_INTERVAL)
    equity = float(getattr(args, "equity", None) or (args.get("equity") if isinstance(args, dict) else None) or config.INITIAL_EQUITY)
    risk_pct = float(getattr(args, "risk", None) or (args.get("risk") if isinstance(args, dict) else None) or config.DEFAULT_RISK_PCT)
    leverage = float(getattr(args, "leverage", None) or (args.get("leverage") if isinstance(args, dict) else None) or config.DEFAULT_LEVERAGE)

    print("\n==================================================")
    print(" [Multi-Agent Live Trader] Initializing")
    print(f" Symbol:   {symbol}")
    print(f" Source:   {source} (Exchange: {exchange})")
    print(f" Equity:   ${equity:,.2f} | Leverage: {leverage:.0f}x")
    print(f" Risk Pct: {risk_pct}% | Poll Interval: {poll_sec}s")
    print("==================================================\n")

    risk_mgr = RiskManager(equity=equity, symbol=symbol)
    if leverage:
        risk_mgr.set_leverage(leverage)
    engine = ExecutionEngine(risk_manager=risk_mgr)
    feed = MarketDataFeed(symbol=symbol, source=source, exchange_id=exchange)
    arb = Arbiter(allow_asian=True)

    iteration = 0
    try:
        while True:
            iteration += 1
            try:
                data = feed.multi_tf(limit_1m=1500)
                if not data or "1m" not in data or data["1m"].empty:
                    print(f"[MultiAgent-Live] [{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Waiting for market data for {symbol}...")
                    time.sleep(poll_sec)
                    continue

                df1 = data["1m"]
                candle = df1.iloc[-1]
                candle_dict = {
                    "open": float(candle.get("open", candle["close"])),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                }

                # Update existing positions or pending setups with latest candle
                events = engine.update_market_candle(symbol, candle_dict)
                for ev in events:
                    msg = ev.get("message", "")
                    print(f"[ExecutionEngine] {ev.get('event')}: {msg}")
                    send_telegram(msg)

                # Check if we should evaluate for a new entry setup
                is_scalping = getattr(engine, "scalp_mode", False) or getattr(arb, "is_scalp", False)
                max_trades = getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 2)) or 2
                open_n = len(engine.active_positions) + len(engine.pending_setups)
                can_open = (open_n < max_trades) if is_scalping else (symbol not in engine.active_positions and symbol not in engine.pending_setups and open_n < max_trades)
                if can_open:
                    daily = data.get("daily")
                    if daily is None or getattr(daily, "empty", True):
                        daily = pd.DataFrame()

                    sig, why, votes = arb.decide(
                        df1,
                        data.get("5m"),
                        data.get("15m"),
                        data.get("1h"),
                        daily,
                        risk_mgr,
                        symbol=symbol,
                        on_demand=True,
                    )

                    if sig:
                        order = engine.register_setup(sig, reason=sig.get("reason", ""))
                        alert = (
                            f"⚡ [SETUP] {symbol} {order.direction} @ {order.entry_level:.2f} | "
                            f"SL: {order.stop_loss:.2f} | Lots: {order.lots} | Lev: {order.leverage:.0f}x | {order.setup_reason}"
                        )
                        print(f"\n{alert}\n")
                        send_telegram(alert)

                        trig_events = engine.update_market_candle(symbol, candle_dict)
                        for ev in trig_events:
                            print(f"[ExecutionEngine] {ev.get('event')}: {ev.get('message')}")
                            send_telegram(ev.get("message", ""))
                    else:
                        if iteration % 12 == 1:
                            current_px = candle_dict["close"]
                            print(f"[MultiAgent-Live] [{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {symbol} @ {current_px:.2f} | Status: {why}")

                sleep_interval = config.FAST_POLL_INTERVAL if (engine.active_positions or engine.pending_setups) else poll_sec
                time.sleep(sleep_interval)

            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"[MultiAgent-Live] Loop iteration error: {exc}")
                time.sleep(poll_sec)
    except KeyboardInterrupt:
        print("\n[MultiAgent-Live] Live trader stopped by user.")
