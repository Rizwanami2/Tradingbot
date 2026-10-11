#!/usr/bin/env python3
"""
MULTI-AGENT TRADING BOT
=======================
Institutional multi-agent algorithmic trading engine.
Integrates improved modules (data_feed, ai_learner, news_agent, smc_liquidity,
session_filter, market_regime) when available, with graceful fallback.

Compatible with Python 3.8+.
"""

import argparse
import json
import math
import os
import sys
import time as time_module
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

import config
from execution_engine import ExecutionEngine, TradeOrder, OrderStatus
from risk_manager import RiskManager, RiskConfig, INSTRUMENTS, TradeRiskResult
from trading_strategy_indicators_pro import IndicatorEngine, IndicatorSnapshot

# Prefer improved modules when available
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
    _HAS_SMC = True
except ImportError:
    _HAS_SMC = False
    SMCDir = None

try:
    from session_filter import is_tradeable_session
    _HAS_SESSION = True
except ImportError:
    _HAS_SESSION = False

try:
    from market_regime import classify_market_regime
    _HAS_REGIME = True
except ImportError:
    _HAS_REGIME = False

MEMORY_FILE = "multi_agent_memory.json"


class Dir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


# ======================================================================
# DATA FEED (embedded fallback - overridden by data_feed.py if present)
# ======================================================================
class MarketDataFeed:
    """Multi-source market data feed: Delta Exchange, CCXT, yfinance, or Demo."""

    def __init__(self, symbol: str = "XAUUSD", source: str = "demo",
                 exchange_id: str = "binance", timeframe: str = "1m",
                 api_key: str = "", api_secret: str = ""):
        self.symbol = symbol.upper()
        self.source = source.lower()
        self.exchange_id = exchange_id.lower()
        self.timeframe = timeframe
        self.api_key = api_key or config.EXCHANGE_API_KEY
        self.api_secret = api_secret or config.EXCHANGE_API_SECRET
        self._ex = None
        self._yf = None
        self._init()

    def _init(self):
        if self.source in ("tradingview", "tv"):
            print(f"[Data] TradingView feed -> {self.symbol}")
        elif self.source in ("ccxt", "binance", "bybit"):
            try:
                import ccxt
                ex_name = self.exchange_id if self.source == "ccxt" else "binance"
                cls = getattr(ccxt, ex_name)
                self._ex = cls({"apiKey": self.api_key, "secret": self.api_secret,
                                "enableRateLimit": True, "options": {"defaultType": "future"}})
                print(f"[Data] CCXT connected -> {ex_name} | {self.symbol}")
            except Exception as e:
                print(f"[Data] CCXT init failed ({e}) -> falling back to demo feed")
                self.source = "demo"
        elif self.source == "yfinance":
            try:
                import yfinance as yf
                self._yf = yf
                print(f"[Data] yfinance feed -> {self.symbol}")
            except ImportError:
                print("[Data] yfinance not installed -> falling back to demo feed")
                self.source = "demo"
        else:
            self.source = "demo"
            print(f"[Data] Demo synthetic feed -> {self.symbol}")

    def get_latest_price(self) -> Dict[str, float]:
        if self.source in ("tradingview", "tv") or not self.source:
            try:
                from tradingview_feed import fetch_tradingview_quote
                tv_q = fetch_tradingview_quote(self.symbol)
                if tv_q.get("ok"):
                    return {"mark": tv_q["mark"], "close": tv_q["close"], "high": tv_q["high"],
                            "low": tv_q["low"], "open": tv_q["open"],
                            "change_pct": tv_q.get("change_pct", 0.0),
                            "recommendation": tv_q.get("recommendation", "NEUTRAL"),
                            "source": "TradingView"}
            except Exception as e:
                print(f"[Data] TradingView quote notice: {e}")
        df = self.fetch(limit=10)
        if not df.empty:
            c = df.iloc[-1]
            return {"mark": float(c["close"]), "close": float(c["close"]),
                    "high": float(c["high"]), "low": float(c["low"]),
                    "open": float(c["open"]), "source": "Synthetic"}
        return {"mark": 2650.0, "close": 2650.0, "high": 2651.0, "low": 2649.0, "open": 2650.0, "source": "Demo"}

    def fetch(self, limit: int = 600, timeframe: Optional[str] = None) -> pd.DataFrame:
        tf = timeframe or self.timeframe
        try:
            if self.source in ("tradingview", "tv"):
                from tradingview_feed import fetch_tradingview_bars
                return fetch_tradingview_bars(self.symbol, limit=limit)
            if self.source in ("ccxt", "binance", "bybit") and self._ex:
                sym = "XAU/USDT" if self.symbol == "XAUUSD" else self.symbol
                if "/" not in sym and sym.endswith("USD"):
                    sym = sym.replace("USD", "/USDT")
                raw = self._ex.fetch_ohlcv(sym, timeframe=tf, limit=limit)
                df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                return df.set_index("timestamp")[["open", "high", "low", "close", "volume"]].astype(float)
            if self.source == "yfinance" and self._yf:
                yf_sym = "GC=F" if self.symbol in ("XAUUSD", "GOLD", "XAU") else self.symbol
                t = self._yf.Ticker(yf_sym)
                period = "7d" if tf == "1m" else "60d"
                df = t.history(period=period, interval=tf)
                if not df.empty:
                    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].tail(limit)
                    df.index = pd.to_datetime(df.index, utc=True)
                    return df.astype(float)
        except Exception as e:
            print(f"[Data] Fetch error ({e}) -> generating fallback data")
        return self._demo(limit=limit)

    def _demo(self, limit: int = 600, base_price: float = 2650.0) -> pd.DataFrame:
        dates = pd.date_range(end=datetime.now(timezone.utc), periods=limit, freq="1min")
        returns = np.random.randn(limit) * 0.35
        p = base_price + np.cumsum(returns)
        highs = p + np.random.uniform(0.1, 1.8, limit)
        lows = p - np.random.uniform(0.1, 1.8, limit)
        closes = p + np.random.randn(limit) * 0.15
        highs = np.maximum(highs, np.maximum(p, closes))
        lows = np.minimum(lows, np.minimum(p, closes))
        volumes = np.random.randint(50, 1500, limit)
        return pd.DataFrame({"open": p, "high": highs, "low": lows, "close": closes, "volume": volumes}, index=dates)

    def multi_tf(self, limit_1m: int = 700) -> Dict[str, pd.DataFrame]:
        df1 = self.fetch(limit=limit_1m, timeframe="1m")
        if df1.empty:
            return {}
        out = {"1m": df1}
        for tf, rule in [("5m", "5min"), ("15m", "15min"), ("1h", "1h")]:
            try:
                out[tf] = df1.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
            except Exception:
                out[tf] = pd.DataFrame()
        try:
            out["daily"] = df1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        except Exception:
            out["daily"] = pd.DataFrame()
        return out


# Prefer improved DataFeed if available
if _ImprovedDataFeed is not None:
    MarketDataFeed = _ImprovedDataFeed  # type: ignore
    print("[MultiAgent] Using improved data_feed.DataFeed")


def atr(df: pd.DataFrame, p: int = 14) -> float:
    if len(df) < p + 1:
        return 0.0
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    return float(np.mean(tr[-p:]))


def detect_trend(df: pd.DataFrame, lb: int = 10) -> Dir:
    if df is None or len(df) < lb:
        return Dir.FLAT
    r = df.iloc[-lb:]
    g = (r["close"] > r["open"]).sum()
    rd = (r["close"] < r["open"]).sum()
    if g >= lb * 0.65 and (r["low"].is_monotonic_increasing or r["high"].is_monotonic_increasing):
        return Dir.LONG
    if rd >= lb * 0.65 and (r["low"].is_monotonic_decreasing or r["high"].is_monotonic_decreasing):
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
    def vote(self, df1, df5, df15, df1h) -> Vote:
        votes = []
        for df, lb in [(df1, 12), (df5, 10), (df15, 8), (df1h, 6)]:
            if df is not None and len(df) >= lb:
                votes.append(detect_trend(df, lb))
        longs = sum(1 for v in votes if v == Dir.LONG)
        shorts = sum(1 for v in votes if v == Dir.SHORT)
        n = max(len(votes), 1)
        if longs >= 2 and longs > shorts:
            return Vote(self.name, Dir.LONG, longs / n, f"{longs}/{n} TF Bullish Structure")
        if shorts >= 2 and shorts > longs:
            return Vote(self.name, Dir.SHORT, shorts / n, f"{shorts}/{n} TF Bearish Structure")
        return Vote(self.name, Dir.FLAT, 0.0, "No multi-TF alignment")


class LiquidityAgent:
    """Advanced SMC Liquidity Agent – uses smc_liquidity when available."""
    name = "LIQUIDITY"

    def vote(self, df: pd.DataFrame, daily: pd.DataFrame) -> Vote:
        if len(df) < 14:
            return Vote(self.name, Dir.FLAT, 0.0, "Insufficient data")

        # Prefer full SMC engine
        if _HAS_SMC:
            try:
                direction, strength, reason = best_smc_vote(df, daily if daily is not None and not daily.empty else None)
                if direction == SMCDir.BULLISH:
                    return Vote(self.name, Dir.LONG, float(strength), reason)
                if direction == SMCDir.BEARISH:
                    return Vote(self.name, Dir.SHORT, float(strength), reason)
                return Vote(self.name, Dir.FLAT, 0.0, reason or "No SMC setup")
            except Exception as e:
                print(f"[Liquidity] SMC engine notice: {e}")

        # Fallback: original PDH/PDL + simple BOS
        if daily is None or len(daily) < 2:
            return Vote(self.name, Dir.FLAT, 0.0, "Insufficient daily data")
        pdh = float(daily.iloc[-2]["high"])
        pdl = float(daily.iloc[-2]["low"])
        prev, curr = df.iloc[-2], df.iloc[-1]
        if float(prev["high"]) > pdh and float(curr["close"]) < pdh and curr["close"] < curr["open"]:
            return Vote(self.name, Dir.SHORT, 0.85, "PDH Liquidity Swept + Bearish Rejection")
        if float(prev["low"]) < pdl and float(curr["close"]) > pdl and curr["close"] > curr["open"]:
            return Vote(self.name, Dir.LONG, 0.85, "PDL Liquidity Swept + Bullish Rejection")
        highs = df["high"].iloc[-12:-1]
        lows = df["low"].iloc[-12:-1]
        if float(curr["close"]) > float(highs.max()):
            return Vote(self.name, Dir.LONG, 0.70, "BOS Continuation Up")
        if float(curr["close"]) < float(lows.min()):
            return Vote(self.name, Dir.SHORT, 0.70, "BOS Continuation Down")
        return Vote(self.name, Dir.FLAT, 0.0, "No SMC setup firing")


class IndicatorAgent:
    name = "INDICATORS"
    MIN_SCORE = 1.8
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
    """Embedded fallback - overridden by ai_learner.py when available."""
    name = "AI-LEARNER"
    def __init__(self, path: str = MEMORY_FILE):
        self.path = path
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
    def predict(self, df: pd.DataFrame, horizon: int = 22):
        if len(df) < 60:
            return Dir.FLAT, 0.5, 0.0
        return Dir.FLAT, 0.5, 0.0
    def vote(self, df: pd.DataFrame) -> Vote:
        d, conf, _ = self.predict(df)
        if d == Dir.FLAT or conf < self.memory["min_conf"]:
            return Vote(self.name, Dir.FLAT, conf, f"AI conf {conf:.0%} low")
        return Vote(self.name, d, conf, f"AI signal (conf {conf:.0%})")
    def learn(self, *args, **kwargs):
        pass


class NewsAgent:
    """Embedded fallback - overridden by news_agent.py when available."""
    name = "NEWS"
    def __init__(self, symbol: str = "XAUUSD", window_min: int = 30, enabled: bool = True):
        self.symbol = symbol.upper()
        self.window = timedelta(minutes=window_min)
        self.enabled = enabled
        self.last_msg = ""
    def blackout(self, ts):
        return False, ""
    def vote(self) -> Vote:
        return Vote(self.name, Dir.FLAT, 0.0, "Neutral (fallback)")


class Arbiter:
    """Weighted, regime-aware, session-aware consensus engine."""
    MIN_AGREEMENT = 3
    MIN_SCORE = 1.8
    MIN_RR = 1.9

    # Base weights – Liquidity & Trend are more important than pure indicators
    WEIGHTS = {
        "TREND": 1.25,
        "LIQUIDITY": 1.40,
        "INDICATORS": 1.00,
        "AI-LEARNER": 0.90,
        "NEWS": 0.70,
    }

    def __init__(self, require_killzone: bool = False, allow_asian: bool = False):
        self.trend = TrendAgent()
        self.liq = LiquidityAgent()
        self.ind = IndicatorAgent()
        self.require_killzone = require_killzone
        self.allow_asian = allow_asian
        if _ImprovedAILearner is not None:
            self.ai = _ImprovedAILearner()
            print("[MultiAgent] Using improved ai_learner.AILearnerAgent")
        else:
            self.ai = AILearnerAgent()
        if _ImprovedNewsAgent is not None:
            self.news = _ImprovedNewsAgent(enabled=getattr(config, "NEWS_ENABLED", True),
                                          window_min=getattr(config, "NEWS_WINDOW_MIN", 30))
            print("[MultiAgent] Using improved news_agent.NewsAgent")
        else:
            self.news = NewsAgent(enabled=getattr(config, "NEWS_ENABLED", True),
                                 window_min=getattr(config, "NEWS_WINDOW_MIN", 30))
        if _HAS_SMC:
            print("[MultiAgent] Advanced SMC Liquidity engine active")
        if _HAS_SESSION:
            print("[MultiAgent] Session/Killzone filter active")
        if _HAS_REGIME:
            print("[MultiAgent] Market regime classifier active")

    def decide(self, df1, df5, df15, df1h, daily, risk, news=None, symbol="XAUUSD"):
        ts = df1.index[-1] if hasattr(df1.index[-1], "to_pydatetime") else datetime.now(timezone.utc)

        # 1. Session filter
        if _HAS_SESSION:
            ok, session_reason = is_tradeable_session(ts, require_killzone=self.require_killzone, allow_asian=self.allow_asian)
            if not ok:
                return None, f"SESSION VETO: {session_reason}", []

        # 2. Regime adjustment
        min_score = self.MIN_SCORE
        min_agree = self.MIN_AGREEMENT
        regime_name = "unknown"
        if _HAS_REGIME:
            try:
                regime = classify_market_regime(df1)
                regime_name = regime.get("regime", "unknown")
                if regime_name == "ranging":
                    min_score = 2.4          # stricter in ranges
                    min_agree = 4
                elif regime_name == "high_volatility":
                    min_score = 2.1
                elif regime_name == "trending":
                    min_score = 1.6          # slightly more permissive in trends
            except Exception as e:
                print(f"[Arbiter] Regime notice: {e}")

        active_news = news or self.news
        votes = [
            self.trend.vote(df1, df5, df15, df1h),
            self.liq.vote(df1, daily),
            self.ind.vote(df1),
            self.ai.vote(df1),
        ]
        if active_news and getattr(active_news, "enabled", True):
            black, msg = active_news.blackout(ts)
            if black:
                return None, f"VETO: {msg}", votes
            votes.append(active_news.vote())

        can_trade, why = risk.can_trade()
        if not can_trade:
            return None, f"RISK VETO: {why}", votes

        # Weighted scoring
        for d in (Dir.LONG, Dir.SHORT):
            aligned = [v for v in votes if v.direction == d]
            weighted_score = sum(v.strength * self.WEIGHTS.get(v.agent, 1.0) for v in aligned)
            if len(aligned) >= min_agree and weighted_score >= min_score:
                return {
                    "direction": d,
                    "score": round(weighted_score, 3),
                    "symbol": symbol,
                    "regime": regime_name,
                    "agreement": len(aligned),
                }, f"TRADE TRIGGERED (regime={regime_name})", votes

        return None, f"No consensus (regime={regime_name})", votes


def evaluate_symbol(symbol="XAUUSD", source="demo", exchange_id="binance"):
    feed = MarketDataFeed(symbol=symbol, source=source, exchange_id=exchange_id)
    arbiter = Arbiter()
    risk = RiskManager(equity=getattr(config, "INITIAL_EQUITY", 10000), symbol=symbol)
    quote = feed.get_latest_price()
    data = feed.multi_tf(limit_1m=700)
    if not data or "1m" not in data or data["1m"].empty:
        return {"symbol": symbol, "price": quote.get("mark", 0), "signal": None, "status": "No data", "votes": []}
    df1 = data["1m"]
    daily = data.get("daily", pd.DataFrame())
    sig, why, votes = arbiter.decide(df1, data.get("5m"), data.get("15m"), data.get("1h"), daily, risk, symbol=symbol)
    return {"symbol": symbol, "price": quote.get("mark"), "signal": sig, "status": why,
            "votes": [{"agent": v.agent, "direction": v.direction.name, "strength": round(v.strength, 2), "reason": v.reason} for v in votes]}


if __name__ == "__main__":
    print("Multi-Agent Trading Bot loaded.")
    print("Improved modules:", "data_feed" if _ImprovedDataFeed else "embedded",
          "| ai_learner" if _ImprovedAILearner else "| embedded AI",
          "| news_agent" if _ImprovedNewsAgent else "| embedded News",
          "| SMC" if _HAS_SMC else "",
          "| Session" if _HAS_SESSION else "",
          "| Regime" if _HAS_REGIME else "")
