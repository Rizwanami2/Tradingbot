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
import logging
import os

logger = logging.getLogger("multi_agent")
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

try:
    from orr_strategy import ORRAgent, ORRDir
except ImportError:
    ORRAgent = None
    ORRDir = None

try:
    from supply_demand_strategy import SupplyDemandAgent, SDDir
except ImportError:
    SupplyDemandAgent = None
    SDDir = None

try:
    from ote_strategy import OTEAgent, OTEDir
except ImportError:
    OTEAgent = None
    OTEDir = None

try:
    from volatility_squeeze_scalp import SqueezeScalpAgent, SqueezeDir
except ImportError:
    SqueezeScalpAgent = None
    SqueezeDir = None

try:
    from momentum_ribbon_scalp import RibbonScalpAgent, RibbonDir
except ImportError:
    RibbonScalpAgent = None
    RibbonDir = None

try:
    from stoch_rsi_scalp import StochRSIScalpAgent, StochDir
except ImportError:
    StochRSIScalpAgent = None
    StochDir = None

try:
    from auto_trend_1m_strategy import AutoTrend1mAgent, AutoTrendDir, verify_auto_trend_alignment
except ImportError:
    AutoTrend1mAgent = None
    AutoTrendDir = None
    verify_auto_trend_alignment = None

try:
    from agent_swarm import get_agent_swarm, build_market_context, AgentSwarm
except ImportError:
    get_agent_swarm = None
    build_market_context = None
    AgentSwarm = None

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
        if self.source == "demo" or os.getenv("PYTEST_CURRENT_TEST"):
            sym = (self.symbol or "").upper()
            base = 82000.0 if "BTC" in sym else (2650.0 if "XAU" in sym or "GOLD" in sym else 100.0)
            return {"ok": True, "mark": base, "close": base, "source": "demo"}
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
        if self.source == "demo" or os.getenv("PYTEST_CURRENT_TEST"):
            return self._demo(limit)
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
            raw = fetch_tradingview_bars(self.symbol, timeframe or "1m", limit)
            if isinstance(raw, pd.DataFrame) and not raw.empty:
                return raw
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
            sym = (self.symbol or "").upper()
            if "BTC" in sym:
                base_price = 82000.0
            elif "XAU" in sym or "GOLD" in sym:
                base_price = 2650.0
            else:
                base_price = 100.0
            if not os.getenv("PYTEST_CURRENT_TEST") and getattr(self, "source", "") != "demo":
                try:
                    from realtime_prices import fetch_spot_price
                    rt = fetch_spot_price(self.symbol)
                    if rt.get("ok") and float(rt.get("mark") or 0) > 0:
                        base_price = float(rt["mark"])
                except Exception:
                    pass
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
        b = None
        try:
            from mt5_broker import get_active_broker
            b = get_active_broker()
        except Exception:
            pass

        for tf, rule in [("5m", "5min"), ("15m", "15min"), ("1h", "1h")]:
            df_native = None
            if b is not None and hasattr(b, "get_candles"):
                try:
                    df_native = b.get_candles(self.symbol, timeframe=tf, limit=300)
                except Exception:
                    pass
            if df_native is not None and not df_native.empty and len(df_native) >= 5:
                out[tf] = df_native
            else:
                try:
                    out[tf] = df1.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
                except Exception:
                    out[tf] = pd.DataFrame()

        df_daily = None
        if b is not None and hasattr(b, "get_candles"):
            try:
                df_daily = b.get_candles(self.symbol, timeframe="1d", limit=100)
            except Exception:
                pass
        if df_daily is not None and not df_daily.empty and len(df_daily) >= 3:
            out["daily"] = df_daily
        else:
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
    stop_price: Optional[float] = None
    tp_price: Optional[float] = None
    entry_price: Optional[float] = None


class TrendAgent:
    name = "TREND"

    def vote(
        self,
        df: pd.DataFrame,
        df5: Optional[pd.DataFrame] = None,
        df15: Optional[pd.DataFrame] = None,
        df1h: Optional[pd.DataFrame] = None,
        *args,
        **kwargs,
    ) -> Vote:
        d = detect_trend(df)
        if d == Dir.FLAT:
            return Vote(self.name, Dir.FLAT, 0.0, "No clear trend")
        strength = 0.65
        reason = f"EMA trend {d.name}"

        # Multi-timeframe trend alignment confluence
        htf_align = 0
        if df15 is not None and not df15.empty and len(df15) >= 15:
            d15 = detect_trend(df15)
            if d15 == d:
                htf_align += 1
        if df1h is not None and not df1h.empty and len(df1h) >= 15:
            d1h = detect_trend(df1h)
            if d1h == d:
                htf_align += 1
            elif d1h != Dir.FLAT and d1h != d:
                htf_align -= 1

        if htf_align >= 2:
            strength = 0.85
            reason = f"Multi-timeframe trend aligned (M1+M15+H1 {d.name})"
        elif htf_align < 0:
            strength = 0.45
            reason = f"M1 trend {d.name} (counter HTF)"

        return Vote(self.name, d, strength, reason)


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


class MT5ChartAgent:
    """
    Evaluates institutional MT5 candlestick chart structure, moving average stacks,
    classical floor pivots (R2, R1, Pivot, S1, S2), RSI, and ATR directly from
    the live MT5 chart engine.
    """
    name = "MT5-CHART"

    def vote(self, df1: pd.DataFrame, symbol: str = "XAUUSD", broker: Optional[Any] = None) -> Vote:
        try:
            from mt5_chart import get_mt5_live_chart
            chart = get_mt5_live_chart(symbol=symbol, timeframe="1m", broker=broker)

            if not chart or not chart.get("ok"):
                return Vote(self.name, Dir.FLAT, 0.0, "MT5 live chart feed standby")

            t = chart.get("technicals", {})
            mark = float(chart.get("mark", 0.0))
            if mark <= 0:
                return Vote(self.name, Dir.FLAT, 0.0, "MT5 price unavailable")

            ema9 = float(t.get("ema9", mark))
            ema21 = float(t.get("ema21", mark))
            rsi = float(t.get("rsi", 50.0))
            pivot = float(t.get("pivot", mark))
            r1 = float(t.get("r1", mark * 1.005))
            r2 = float(t.get("r2", mark * 1.010))
            s1 = float(t.get("s1", mark * 0.995))
            s2 = float(t.get("s2", mark * 0.990))
            bias = str(t.get("trend_bias", "")).upper()
            ema_sig = str(t.get("ema_signal", ""))

            long_points = 0.0
            short_points = 0.0

            # 1. MT5 Moving Average Stack
            if ema9 > ema21:
                long_points += 0.35
            elif ema9 < ema21:
                short_points += 0.35

            # 2. MT5 Candlestick Structure Bias
            if "BULLISH" in bias:
                long_points += 0.30
            elif "BEARISH" in bias:
                short_points += 0.30

            # 3. MT5 Pivot Level Rebounds and Breakouts
            if mark > pivot:
                long_points += 0.20
            elif mark < pivot:
                short_points += 0.20

            # Support bounce vs Resistance rejection
            if mark <= s1 * 1.0015 and rsi <= 45.0:
                long_points += 0.25
            elif mark >= r1 * 0.9985 and rsi >= 55.0:
                short_points += 0.25

            # 4. RSI Momentum confirmation
            if 52.0 <= rsi <= 75.0:
                long_points += 0.15
            elif 25.0 <= rsi <= 48.0:
                short_points += 0.15

            if long_points > short_points and long_points >= 0.50:
                strength = min(0.95, round(long_points, 2))
                reason = f"MT5 Structure: {ema_sig or 'Bullish Stack'} | Above Pivot ${pivot:,.2f} | RSI {rsi:.1f}"
                return Vote(self.name, Dir.LONG, strength, reason, stop_price=s1, tp_price=r1, entry_price=pivot)
            elif short_points > long_points and short_points >= 0.50:
                strength = min(0.95, round(short_points, 2))
                reason = f"MT5 Structure: {ema_sig or 'Bearish Stack'} | Below Pivot ${pivot:,.2f} | RSI {rsi:.1f}"
                return Vote(self.name, Dir.SHORT, strength, reason, stop_price=r1, tp_price=s1, entry_price=pivot)
            else:
                return Vote(self.name, Dir.FLAT, 0.35, f"MT5 Structure: Range near Pivot ${pivot:,.2f} | RSI {rsi:.1f}")
        except Exception as e:
            return Vote(self.name, Dir.FLAT, 0.0, f"MT5 Chart note: {e}")


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
        "MT5-CHART": 1.35,
        "ORR-STRATEGY": 1.50,
        "SUPPLY-DEMAND": 1.45,
        "OTE-STRATEGY": 1.45,
        "VOLATILITY-SQUEEZE": 1.50,
        "RIBBON-SCALP": 1.45,
        "STOCH-RSI-SCALP": 1.50,
        "AUTO-TREND-1M": 1.60,
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

        # Combine with Brain self-learned agent weights
        try:
            from autonomous_learner import get_brain
            brain = get_brain(auto_bootstrap=False)
            if brain and getattr(brain.knowledge, "enabled", True) and brain.knowledge.agent_weights:
                for k, w in brain.knowledge.agent_weights.items():
                    if k in self.WEIGHTS:
                        self.WEIGHTS[k] = round((self.WEIGHTS[k] + float(w)) / 2.0, 3)
                    else:
                        self.WEIGHTS[k] = round(float(w), 3)
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
        self.mt5_chart = MT5ChartAgent()
        self.orr = ORRAgent() if ORRAgent is not None else None
        self.sd = SupplyDemandAgent() if SupplyDemandAgent is not None else None
        self.ote = OTEAgent() if OTEAgent is not None else None
        self.sqz = SqueezeScalpAgent() if SqueezeScalpAgent is not None else None
        self.ribbon = RibbonScalpAgent() if RibbonScalpAgent is not None else None
        self.stoch = StochRSIScalpAgent() if StochRSIScalpAgent is not None else None
        self.auto_trend_1m = AutoTrend1mAgent() if AutoTrend1mAgent is not None else None
        self.swarm = get_agent_swarm() if get_agent_swarm is not None else None
        if self.swarm:
            for sa in self.swarm.agents:
                if sa.name not in self.WEIGHTS:
                    self.WEIGHTS[sa.name] = sa.weight

    def decide(
        self,
        df1,
        df5,
        df15,
        df1h,
        daily,
        risk_mgr,
        symbol="XAUUSD",
        on_demand: bool = False,
        engine: Optional[Any] = None,
        include_swarm: bool = True,
    ):
        import config
        votes = [
            self.trend.vote(df1, df5=df5, df15=df15, df1h=df1h),
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
                v = Vote(gv.agent, direction, float(gv.strength), gv.reason)
                if hasattr(gv, "stop_price"):
                    v.stop_price = gv.stop_price
                if hasattr(gv, "tp_price"):
                    v.tp_price = gv.tp_price
                if getattr(gv, "entry_price", None):
                    v.entry_price = gv.entry_price
                votes.append(v)
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

        if getattr(self, "mt5_chart", None) is not None:
            try:
                cv = self.mt5_chart.vote(df1, symbol=symbol)
                votes.append(cv)
            except Exception:
                pass

        if getattr(self, "orr", None) is not None:
            try:
                ov = self.orr.vote(df1, daily if daily is not None else pd.DataFrame())
                dname = getattr(ov.direction, "name", str(ov.direction)).upper()
                direction = Dir.LONG if "LONG" in dname else (Dir.SHORT if "SHORT" in dname else Dir.FLAT)
                v = Vote(ov.agent, direction, float(ov.strength), ov.reason)
                if hasattr(ov, "stop_price"):
                    v.stop_price = ov.stop_price
                if hasattr(ov, "tp_price"):
                    v.tp_price = ov.tp_price
                if getattr(ov, "entry_price", None):
                    v.entry_price = ov.entry_price
                elif getattr(ov, "retest_level", None):
                    v.entry_price = ov.retest_level
                votes.append(v)
            except Exception:
                pass

        if getattr(self, "sd", None) is not None:
            try:
                sd_v = self.sd.vote(df1, daily if daily is not None else pd.DataFrame())
                dname = getattr(sd_v.direction, "name", str(sd_v.direction)).upper()
                direction = Dir.LONG if "LONG" in dname else (Dir.SHORT if "SHORT" in dname else Dir.FLAT)
                v = Vote(sd_v.agent, direction, float(sd_v.strength), sd_v.reason)
                if hasattr(sd_v, "stop_price"):
                    v.stop_price = sd_v.stop_price
                if hasattr(sd_v, "tp_price"):
                    v.tp_price = sd_v.tp_price
                if getattr(sd_v, "entry_price", None):
                    v.entry_price = sd_v.entry_price
                elif getattr(sd_v, "zone", None) and sd_v.zone:
                    v.entry_price = sd_v.zone.top if direction == Dir.LONG else sd_v.zone.bottom
                votes.append(v)
            except Exception:
                pass

        if getattr(self, "ote", None) is not None:
            try:
                ote_v = self.ote.vote(df1, daily if daily is not None else pd.DataFrame())
                dname = getattr(ote_v.direction, "name", str(ote_v.direction)).upper()
                direction = Dir.LONG if "LONG" in dname else (Dir.SHORT if "SHORT" in dname else Dir.FLAT)
                v = Vote(ote_v.agent, direction, float(ote_v.strength), ote_v.reason)
                if hasattr(ote_v, "stop_price"):
                    v.stop_price = ote_v.stop_price
                if hasattr(ote_v, "tp_price"):
                    v.tp_price = ote_v.tp_price
                if getattr(ote_v, "entry_price", None):
                    v.entry_price = ote_v.entry_price
                elif getattr(ote_v, "fib_levels", None) and isinstance(ote_v.fib_levels, dict):
                    v.entry_price = ote_v.fib_levels.get("fib_705") or ote_v.fib_levels.get("fib_618")
                votes.append(v)
            except Exception:
                pass

        if getattr(self, "sqz", None) is not None:
            try:
                sqz_v = self.sqz.vote(df1, daily if daily is not None else pd.DataFrame())
                dname = getattr(sqz_v.direction, "name", str(sqz_v.direction)).upper()
                direction = Dir.LONG if "LONG" in dname else (Dir.SHORT if "SHORT" in dname else Dir.FLAT)
                v = Vote(sqz_v.agent, direction, float(sqz_v.strength), sqz_v.reason)
                if hasattr(sqz_v, "stop_price"):
                    v.stop_price = sqz_v.stop_price
                if hasattr(sqz_v, "tp_price"):
                    v.tp_price = sqz_v.tp_price
                if getattr(sqz_v, "entry_price", None):
                    v.entry_price = sqz_v.entry_price
                votes.append(v)
            except Exception:
                pass

        if getattr(self, "ribbon", None) is not None:
            try:
                rib_v = self.ribbon.vote(df1, daily if daily is not None else pd.DataFrame())
                dname = getattr(rib_v.direction, "name", str(rib_v.direction)).upper()
                direction = Dir.LONG if "LONG" in dname else (Dir.SHORT if "SHORT" in dname else Dir.FLAT)
                v = Vote(rib_v.agent, direction, float(rib_v.strength), rib_v.reason)
                if hasattr(rib_v, "stop_price"):
                    v.stop_price = rib_v.stop_price
                if hasattr(rib_v, "tp_price"):
                    v.tp_price = rib_v.tp_price
                if getattr(rib_v, "entry_price", None):
                    v.entry_price = rib_v.entry_price
                votes.append(v)
            except Exception:
                pass

        if getattr(self, "stoch", None) is not None:
            try:
                stoch_v = self.stoch.vote(df1, daily if daily is not None else pd.DataFrame())
                dname = getattr(stoch_v.direction, "name", str(stoch_v.direction)).upper()
                direction = Dir.LONG if "LONG" in dname else (Dir.SHORT if "SHORT" in dname else Dir.FLAT)
                v = Vote(stoch_v.agent, direction, float(stoch_v.strength), stoch_v.reason)
                if hasattr(stoch_v, "stop_price"):
                    v.stop_price = stoch_v.stop_price
                if hasattr(stoch_v, "tp_price"):
                    v.tp_price = stoch_v.tp_price
                if getattr(stoch_v, "entry_price", None):
                    v.entry_price = stoch_v.entry_price
                votes.append(v)
            except Exception:
                pass

        if getattr(self, "auto_trend_1m", None) is not None:
            try:
                at_v = self.auto_trend_1m.vote(df1, daily=daily if daily is not None else pd.DataFrame())
                dname = getattr(at_v.direction, "name", str(at_v.direction)).upper()
                direction = Dir.LONG if "LONG" in dname else (Dir.SHORT if "SHORT" in dname else Dir.FLAT)
                v = Vote(at_v.agent, direction, float(at_v.strength), at_v.reason)
                if hasattr(at_v, "stop_price"):
                    v.stop_price = at_v.stop_price
                if hasattr(at_v, "tp_price"):
                    v.tp_price = at_v.tp_price
                if getattr(at_v, "entry_price", None):
                    v.entry_price = at_v.entry_price
                votes.append(v)
            except Exception:
                pass

        # 100-Agent Specialized Strategy Swarm Consensus Integration (118 Agents Total)
        _swarm_on = os.getenv("SWARM_ENABLED", "true").lower() in ("true", "1", "yes", "on")
        if include_swarm and _swarm_on and getattr(self, "swarm", None) is not None and build_market_context is not None:
            try:
                news_sent = 0.0
                for v in votes:
                    if v.agent == "NEWS":
                        dname = getattr(v.direction, "name", str(v.direction)).upper()
                        news_sent = 0.8 if "LONG" in dname else (-0.8 if "SHORT" in dname else 0.0)
                        break
                ctx = build_market_context(
                    symbol=symbol,
                    df1=df1,
                    df5=df5,
                    df15=df15,
                    df1h=df1h,
                    daily=daily,
                    news_sentiment=news_sent,
                )
                swarm_votes = self.swarm.evaluate(ctx)
                logger.info("Swarm votes for %s: %d", symbol, len(swarm_votes or []))
                for sv in swarm_votes:
                    sd_name = getattr(sv.direction, "name", str(sv.direction)).upper()
                    s_dir = Dir.LONG if "LONG" in sd_name else (Dir.SHORT if "SHORT" in sd_name else Dir.FLAT)
                    v = Vote(
                        agent=sv.agent,
                        direction=s_dir,
                        strength=float(sv.strength),
                        reason=sv.reason,
                        stop_price=sv.stop_price,
                        tp_price=sv.tp_price,
                        entry_price=sv.entry_price,
                    )
                    votes.append(v)
            except Exception as _swarm_err:
                logger.debug("Agent swarm evaluation note: %s", _swarm_err)

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

        # Institutional Synergy Booster:
        # Multiple institutional smart-money confirmations (Liquidity, MTF, ORR, S/D, OTE, AutoTrend1M)
        # reinforce high-accuracy trade execution
        INSTITUTIONAL_AGENTS = {"LIQUIDITY", "MTF-CONFLUENCE", "ORR-STRATEGY", "SUPPLY-DEMAND", "OTE-STRATEGY", "FAKE-BREAKOUT", "VOLATILITY-SQUEEZE", "RIBBON-SCALP", "STOCH-RSI-SCALP", "AUTO-TREND-1M"}
        long_inst_n = sum(1 for v in votes if (v.agent in INSTITUTIONAL_AGENTS or v.agent.startswith(("ICT-", "MTF-", "PIVOT-", "FIB-", "SD-"))) and v.direction == Dir.LONG and v.strength >= 0.75)
        short_inst_n = sum(1 for v in votes if (v.agent in INSTITUTIONAL_AGENTS or v.agent.startswith(("ICT-", "MTF-", "PIVOT-", "FIB-", "SD-"))) and v.direction == Dir.SHORT and v.strength >= 0.75)
        if long_inst_n >= 2:
            long_score = round(long_score * 1.15, 3)
        if short_inst_n >= 2:
            short_score = round(short_score * 1.15, 3)

        # Scalp Velocity Booster:
        # In scalp mode, accelerate conviction when fast momentum triggers fire
        if getattr(self, "is_scalp", False):
            SCALP_BURST_AGENTS = {"VOLATILITY-SQUEEZE", "RIBBON-SCALP", "STOCH-RSI-SCALP", "MEAN-REVERSION", "AUTO-TREND-1M"}
            if any((v.agent in SCALP_BURST_AGENTS or v.agent.startswith(("MICRO-", "BB-", "KELTNER-", "STOCH-", "RSI-"))) and v.direction == Dir.LONG and v.strength >= 0.80 for v in votes):
                long_score = round(long_score * 1.10, 3)
            if any((v.agent in SCALP_BURST_AGENTS or v.agent.startswith(("MICRO-", "BB-", "KELTNER-", "STOCH-", "RSI-"))) and v.direction == Dir.SHORT and v.strength >= 0.80 for v in votes):
                short_score = round(short_score * 1.10, 3)

        direction = Dir.FLAT
        score = 0.0
        if long_n >= self.MIN_AGREEMENT and long_score >= self.MIN_SCORE and long_score > short_score:
            direction = Dir.LONG
            score = long_score
        elif short_n >= self.MIN_AGREEMENT and short_score >= self.MIN_SCORE and short_score > long_score:
            direction = Dir.SHORT
            score = short_score

        # High-Accuracy Clean Directional Check ("straight for more accurate"):
        # Filters out false signals and whipsaws during conflicting agent deadlock
        if direction != Dir.FLAT:
            opposing_score = short_score if direction == Dir.LONG else long_score
            opposing_n = short_n if direction == Dir.LONG else long_n
            if opposing_score >= 0.90:
                if score < opposing_score * 1.30 or (score - opposing_score) < 0.50:
                    conflict_msg = (
                        f"Chop / Conflict: L({long_score:.2f}/{long_n}) vs S({short_score:.2f}/{short_n}) "
                        f"— waiting for clean directional straight consensus"
                    )
                    if session_warn:
                        conflict_msg += f" | {session_warn}"
                    return None, conflict_msg, votes

        if direction == Dir.FLAT:
            msg = f"No consensus (L:{long_score:.2f}/{long_n} S:{short_score:.2f}/{short_n})"
            if session_warn:
                msg += f" | {session_warn}"
            return None, msg, votes

        # Strict Auto-Trend Alignment Check: Reject counter-trend consensus setups
        if direction != Dir.FLAT and verify_auto_trend_alignment is not None:
            try:
                trend_ok, trend_why = verify_auto_trend_alignment(df1, direction.name)
                if not trend_ok:
                    conflict_msg = f"Auto-Trend Filter: {trend_why}"
                    if session_warn:
                        conflict_msg += f" | {session_warn}"
                    return None, conflict_msg, votes
            except Exception:
                pass

        # Strict Single-Direction Rule: Do not BUY and SELL positions at the same time on the same currency unless hedging is enabled
        if direction != Dir.FLAT and engine is not None:
            try:
                import config
                allow_hedge = getattr(config, "is_hedging_allowed", lambda: False)() or getattr(self, "allow_hedging", False) or getattr(engine, "allow_hedging", False)
                if not allow_hedge:
                    from anti_spam import canonical_currency
                    curr = canonical_currency(symbol)
                    is_buy = direction == Dir.LONG
                    is_sell = direction == Dir.SHORT
                    active_curr = [p for p in getattr(engine, "active_positions", {}).values()
                                   if canonical_currency(getattr(p, "symbol", "")) == curr]
                    pending_curr = [p for p in getattr(engine, "pending_setups", {}).values()
                                    if canonical_currency(getattr(p, "symbol", "")) == curr]
                    for p in active_curr + pending_curr:
                        p_dir = str(getattr(p, "direction", "")).upper()
                        if is_buy and p_dir in ("SHORT", "SELL"):
                            return None, f"Opposing direction: active SELL position exists on {curr} (cannot BUY and SELL same time)", votes
                        if is_sell and p_dir in ("LONG", "BUY"):
                            return None, f"Opposing direction: active BUY position exists on {curr} (cannot BUY and SELL same time)", votes
            except Exception:
                pass

        entry = float(df1["close"].iloc[-1])
        if getattr(self, "is_scalp", False):
            sl_pct = float(getattr(self, "scalp_sl_pct", 0.12)) / 100.0
            atr_val = atr(df1) or (entry * 0.001)
            stop_dist = min(max(atr_val * 0.5, entry * (sl_pct * 0.6)), entry * (sl_pct * 1.5))
            tp_mult = float(getattr(self, "min_rr", 1.0) or 1.0)
            tp2_mult = max(tp_mult * 1.5, 1.5)
            if direction == Dir.LONG:
                stop = entry - stop_dist
                tp1 = entry + stop_dist * tp_mult
                tp2 = entry + stop_dist * tp2_mult
            else:
                stop = entry + stop_dist
                tp1 = entry - stop_dist * tp_mult
                tp2 = entry - stop_dist * tp2_mult
        else:
            atr_val = atr(df1) or entry * 0.002
            min_stop_dist = entry * 0.003
            stop_dist = max(atr_val * 1.5, min_stop_dist)
            if direction == Dir.LONG:
                stop = entry - stop_dist
            else:
                stop = entry + stop_dist
            tp1 = tp2 = None

        # High-Accuracy Structural Stop Loss refinement:
        # Ground stop loss directly behind institutional structure (Demand base / Supply ceiling / OTE origin / ORR retest)
        for v in votes:
            if v.direction == direction and getattr(v, "stop_price", None) is not None and v.strength >= 0.85:
                sp = float(v.stop_price)
                if direction == Dir.LONG and sp < entry and (entry - sp) <= stop_dist * 2.2 and (entry - sp) >= stop_dist * 0.4:
                    stop = sp
                    if getattr(self, "is_scalp", False):
                        tp1 = entry + (entry - sp) * tp_mult
                        tp2 = entry + (entry - sp) * tp2_mult
                    break
                elif direction == Dir.SHORT and sp > entry and (sp - entry) <= stop_dist * 2.2 and (sp - entry) >= stop_dist * 0.4:
                    stop = sp
                    if getattr(self, "is_scalp", False):
                        tp1 = entry - (sp - entry) * tp_mult
                        tp2 = entry - (sp - entry) * tp2_mult
                    break

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
            "agents": [v.agent for v in votes if getattr(v.direction, "name", str(v.direction)).upper() == direction.name],
            "mt5_chart": None,
        }

        # Collect collective agent structural levels (Unified Consensus)
        collective_agent_levels = {}
        for v in votes:
            if v.direction == direction:
                an = v.agent.lower().replace("-", "_")
                if getattr(v, "stop_price", None):
                    collective_agent_levels[f"{an}_stop"] = float(v.stop_price)
                if getattr(v, "tp_price", None):
                    collective_agent_levels[f"{an}_tp"] = float(v.tp_price)
                if getattr(v, "entry_price", None):
                    collective_agent_levels[f"{an}_entry"] = float(v.entry_price)

        # For balanced / non-scalp mode: Multi-Agent consensus must give a perfect entry level signal
        # (calculated structural key level, pullback zone, order block, or OTE level), NOT current market position.
        if not getattr(self, "is_scalp", False):
            try:
                from entry_level_planner import plan_best_entry, attach_plan_to_signal
                dname = direction.name if hasattr(direction, "name") else str(direction)
                prof = getattr(config, "CURRENT_PROFILE", "balanced")
                entry_plan = plan_best_entry(
                    symbol, df1, direction=dname, mark=entry, stop_hint=stop,
                    profile=prof, agent_levels=collective_agent_levels, votes=votes
                )
                sig = attach_plan_to_signal(sig, entry_plan)
            except Exception as _plan_err:
                logger.debug("Entry level planning note in arbiter: %s", _plan_err)

        if not os.getenv("PYTEST_CURRENT_TEST"):
            try:
                from mt5_chart import get_mt5_live_chart
                ch_data = get_mt5_live_chart(symbol=symbol, timeframe="1m")
                if ch_data and ch_data.get("ok", True):
                    sig["mt5_chart"] = ch_data
            except Exception:
                pass
        else:
            sig["mt5_chart"] = {"ok": True, "symbol": symbol, "timeframe": "1m"}

        status_msg = (
            f"⚡ SCALP {direction.name} score={score:.2f} (micro SL/TP)"
            if getattr(self, "is_scalp", False)
            else f"🎯 BALANCED {direction.name} @ {sig['entry']:,.2f} score={score:.2f} (Perfect Entry)"
        )
        if session_warn:
            status_msg += f" ({session_warn})"
        return sig, status_msg, votes


def evaluate_symbol(symbol="XAUUSD", source="demo", exchange_id="binance", leverage=None, on_demand: bool = True, include_swarm: bool = True):
    symbol = _norm_trade_symbol(symbol)
    feed = MarketDataFeed(symbol=symbol, source=source, exchange_id=exchange_id)
    data = feed.multi_tf(limit_1m=400)
    if not data or data["1m"].empty:
        return {"symbol": symbol, "signal": None, "status": "No data", "votes": [], "price": 0.0}
    risk = RiskManager(equity=getattr(config, "INITIAL_EQUITY", 10000.0), symbol=symbol)
    if leverage:
        risk.set_leverage(leverage)
    arb = Arbiter()
    sig, why, votes = arb.decide(
        data["1m"], data.get("5m"), data.get("15m"), data.get("1h"), data.get("daily"), risk, symbol=symbol, on_demand=on_demand, include_swarm=include_swarm
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
    if "BTC" in symbol and 0 < mark < 1000 and not os.getenv("PYTEST_CURRENT_TEST") and source != "demo":
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
    mt5_ch = (sig.get("mt5_chart") if sig else None)
    if not mt5_ch:
        if not os.getenv("PYTEST_CURRENT_TEST") and source != "demo":
            try:
                from mt5_chart import get_mt5_live_chart
                mt5_ch = get_mt5_live_chart(symbol=symbol, timeframe="1m")
            except Exception:
                mt5_ch = None
        else:
            mt5_ch = {"ok": True, "symbol": symbol, "timeframe": "1m"}
    return {
        "symbol": symbol,
        "price": mark,
        "signal": sig,
        "status": why,
        "votes": votes_out,
        "risk_summary": risk.summary() if hasattr(risk, "summary") else "",
        "mt5_chart": mt5_ch,
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
                        # Execute market candle immediately before sending outbound network alerts
                        trig_events = engine.update_market_candle(symbol, candle_dict)
                        for ev in trig_events:
                            print(f"[ExecutionEngine] {ev.get('event')}: {ev.get('message')}")
                            send_telegram(ev.get("message", ""))

                        disp_entry = float(getattr(order, "executed_price", 0.0) or getattr(order, "entry_level", 0.0))
                        alert = (
                            f"⚡ [SETUP] {symbol} {order.direction} @ {disp_entry:.2f} | "
                            f"SL: {order.stop_loss:.2f} | Lots: {order.lots} | Lev: {order.leverage:.0f}x | {order.setup_reason}"
                        )
                        print(f"\n{alert}\n")
                        send_telegram(alert)
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
