#!/usr/bin/env python3
"""
MULTI-AGENT TRADING BOT
=======================
Institutional multi-agent algorithmic trading engine.
Integrates:
1. TREND AGENT        - Multi-timeframe trend alignment (1m / 5m / 15m / 1h)
2. LIQUIDITY AGENT    - SMC / ICT price-action setups: PDH/PDL sweeps & BOS continuation
3. INDICATOR AGENT    - 10-indicator confirmation suite from trading_strategy_indicators_pro
4. AI LEARNER AGENT   - Adaptive momentum & slope predictor with persistent memory
5. NEWS AGENT         - ForexFactory economic calendar blackout & crypto sentiment shock filter
6. RISK MANAGER       - Advanced lot sizing, progressive auto-BE (+0.5R/+1.0R), and veto power
7. ARBITER            - Democratic consensus engine (requires >= MIN_AGREEMENT and MIN_SCORE)

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

MEMORY_FILE = "multi_agent_memory.json"


class Dir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


# ======================================================================
# DATA FEED
# ======================================================================
class MarketDataFeed:
    """Multi-source market data feed: Delta Exchange, CCXT, yfinance, or Demo."""

    def __init__(
        self,
        symbol: str = "XAUUSD",
        source: str = "demo",
        exchange_id: str = "binance",
        timeframe: str = "1m",
        api_key: str = "",
        api_secret: str = "",
    ):
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
                self._ex = cls({
                    "apiKey": self.api_key,
                    "secret": self.api_secret,
                    "enableRateLimit": True,
                    "options": {"defaultType": "future"},
                })
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

        elif self.source == "delta":
            print(f"[Data] Delta Exchange feed -> {self.symbol}")

        else:
            self.source = "demo"
            print(f"[Data] Demo synthetic feed -> {self.symbol}")

    def get_latest_price(self) -> Dict[str, float]:
        """Fetch latest price quote for symbol."""
        # Primary live feed: TradingView
        if self.source in ("tradingview", "tv") or not self.source:
            try:
                from tradingview_feed import fetch_tradingview_quote
                tv_q = fetch_tradingview_quote(self.symbol)
                if tv_q.get("ok"):
                    return {
                        "mark": tv_q["mark"],
                        "close": tv_q["close"],
                        "high": tv_q["high"],
                        "low": tv_q["low"],
                        "open": tv_q["open"],
                        "change_pct": tv_q.get("change_pct", 0.0),
                        "recommendation": tv_q.get("recommendation", "NEUTRAL"),
                        "tv_symbol": tv_q.get("tv_symbol", self.symbol),
                        "source": "TradingView",
                    }
            except Exception as e:
                print(f"[Data] TradingView quote notice: {e}")

        # Secondary: Delta Exchange if explicitly requested
        if self.source == "delta" or self.symbol in ("XAUTUSD", "BTCUSD", "ETHUSD", "SOLUSD"):
            try:
                sym = self.symbol
                if sym.endswith("USDT") and not sym.endswith("USD"):
                    sym = sym.replace("USDT", "USD")
                url = f"{config.DELTA_API}/{sym}"
                r = requests.get(url, timeout=8)
                if r.status_code == 200:
                    t = r.json().get("result", {})
                    mark = float(t.get("mark_price") or t.get("close") or 0.0)
                    if mark > 0:
                        return {
                            "mark": mark,
                            "close": float(t.get("close") or mark),
                            "high": float(t.get("high") or mark),
                            "low": float(t.get("low") or mark),
                            "open": float(t.get("open") or mark),
                            "source": "Delta",
                        }
            except Exception:
                pass

        # Fallback to last candle of fetch
        df = self.fetch(limit=10)
        if not df.empty:
            c = df.iloc[-1]
            return {
                "mark": float(c["close"]),
                "close": float(c["close"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "open": float(c["open"]),
                "source": "Synthetic",
            }
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

            if self.source == "delta":
                # For Delta, generate realistic bars anchored around the live Delta mark price
                quote = self.get_latest_price()
                return self._demo(limit=limit, base_price=quote.get("mark", 2650.0))

        except Exception as e:
            print(f"[Data] Fetch error ({e}) -> generating fallback data")

        return self._demo(limit=limit)

    def _demo(self, limit: int = 600, base_price: float = 2650.0) -> pd.DataFrame:
        """Synthetic geometric brownian motion price series."""
        dates = pd.date_range(end=datetime.now(timezone.utc), periods=limit, freq="1min")
        returns = np.random.randn(limit) * 0.35
        p = base_price + np.cumsum(returns)
        highs = p + np.random.uniform(0.1, 1.8, limit)
        lows = p - np.random.uniform(0.1, 1.8, limit)
        closes = p + np.random.randn(limit) * 0.15
        highs = np.maximum(highs, np.maximum(p, closes))
        lows = np.minimum(lows, np.minimum(p, closes))
        volumes = np.random.randint(50, 1500, limit)
        return pd.DataFrame(
            {"open": p, "high": highs, "low": lows, "close": closes, "volume": volumes},
            index=dates,
        )

    def multi_tf(self, limit_1m: int = 700) -> Dict[str, pd.DataFrame]:
        df1 = self.fetch(limit=limit_1m, timeframe="1m")
        if df1.empty:
            return {}
        out = {"1m": df1}
        for tf, rule in [("5m", "5min"), ("15m", "15min"), ("1h", "1h")]:
            try:
                out[tf] = df1.resample(rule).agg(
                    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
                ).dropna()
            except Exception:
                out[tf] = pd.DataFrame()
        try:
            out["daily"] = df1.resample("1D").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last"}
            ).dropna()
        except Exception:
            out["daily"] = pd.DataFrame()
        return out


# ======================================================================
# TECHNICAL INDICATOR HELPERS
# ======================================================================
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


# ======================================================================
# VOTE DATA STRUCTURE
# ======================================================================
@dataclass
class Vote:
    agent: str
    direction: Dir
    strength: float  # 0.0 to 1.0
    reason: str = ""


# ======================================================================
# AGENT 1: TREND AGENT (Multi-timeframe 1m / 5m / 15m / 1h)
# ======================================================================
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


# ======================================================================
# AGENT 2: LIQUIDITY AGENT (SMC / ICT Sweeps & BOS)
# ======================================================================
class LiquidityAgent:
    name = "LIQUIDITY"

    def vote(self, df: pd.DataFrame, daily: pd.DataFrame) -> Vote:
        if len(df) < 14 or len(daily) < 2:
            return Vote(self.name, Dir.FLAT, 0.0, "Insufficient data")
        pdh = float(daily.iloc[-2]["high"])
        pdl = float(daily.iloc[-2]["low"])
        prev, curr = df.iloc[-2], df.iloc[-1]

        # Liquidity Sweep Rejections
        if float(prev["high"]) > pdh and float(curr["close"]) < pdh and curr["close"] < curr["open"]:
            return Vote(self.name, Dir.SHORT, 0.85, "PDH Liquidity Swept + Bearish Rejection")
        if float(prev["low"]) < pdl and float(curr["close"]) > pdl and curr["close"] > curr["open"]:
            return Vote(self.name, Dir.LONG, 0.85, "PDL Liquidity Swept + Bullish Rejection")

        # Break of Structure (BOS)
        highs = df["high"].iloc[-12:-1]
        lows = df["low"].iloc[-12:-1]
        if float(curr["close"]) > float(highs.max()):
            return Vote(self.name, Dir.LONG, 0.70, "BOS Continuation Up")
        if float(curr["close"]) < float(lows.min()):
            return Vote(self.name, Dir.SHORT, 0.70, "BOS Continuation Down")

        return Vote(self.name, Dir.FLAT, 0.0, "No SMC setup firing")


# ======================================================================
# AGENT 3: INDICATOR AGENT (10-Indicator Confluence)
# ======================================================================
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
            strength = min(abs(score) / 5.0, 1.0)
            return Vote(self.name, Dir.LONG, strength, f"Bullish Confluence ({summary})")
        if score <= -self.MIN_SCORE:
            strength = min(abs(score) / 5.0, 1.0)
            return Vote(self.name, Dir.SHORT, strength, f"Bearish Confluence ({summary})")
        return Vote(self.name, Dir.FLAT, 0.0, f"Neutral Confluence ({summary})")


# ======================================================================
# AGENT 4: AI LEARNER AGENT (Self-Calibrating Momentum Predictor)
# ======================================================================
class AILearnerAgent:
    name = "AI-LEARNER"

    def __init__(self, path: str = MEMORY_FILE):
        self.path = path
        self.memory = {
            "trades": 0,
            "wins": 0,
            "pnl": 0.0,
            "calibration": {},
            "weights": {"momentum": 1.0, "slope": 1.0},
            "min_conf": 0.64,
        }
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.memory.update(json.load(f))
            except Exception as e:
                print(f"[AI-Learner] Memory load error ({e}) -> using defaults")

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.memory, f, indent=2)
        except Exception:
            pass

    def predict(self, df: pd.DataFrame, horizon: int = 22) -> Tuple[Dir, float, float]:
        if len(df) < 60:
            return Dir.FLAT, 0.5, 0.0
        c = df["close"].values
        h, l = df["high"].values, df["low"].values
        w = self.memory["weights"]

        def mom(n):
            return (c[-1] - c[-n - 1]) / c[-n - 1] if len(c) > n else 0.0

        tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
        a = np.mean(tr[-14:])
        slope = np.polyfit(np.arange(25), c[-25:], 1)[0] / c[-1]
        hh = np.mean(h[-10:] > h[-11:-1]) if len(h) > 11 else 0.5
        ll = np.mean(l[-10:] < l[-11:-1]) if len(l) > 11 else 0.5

        score = (
            mom(5) * 3.0 * w["momentum"]
            + mom(15) * 2.2 * w["momentum"]
            + slope * 4.2 * w["slope"]
            + (hh - ll) * 1.4
        )
        raw = float(np.tanh(score * 8.5))
        bull = 0.5 + raw * 0.47
        bear = 1.0 - bull

        if bull > 0.64:
            d, conf = Dir.LONG, bull
        elif bear > 0.64:
            d, conf = Dir.SHORT, bear
        else:
            d, conf = Dir.FLAT, max(bull, bear)

        exp = a * np.sqrt(horizon / 14) * (0.55 + conf * 0.85)

        # Historical calibration lookup
        key = f"{round(conf * 20) / 20:.2f}"
        hist = self.memory["calibration"].get(key)
        if hist is not None and self.memory["trades"] > 15:
            conf = 0.6 * conf + 0.4 * hist

        return d, float(conf), float(exp)

    def vote(self, df: pd.DataFrame) -> Vote:
        d, conf, _ = self.predict(df)
        if d == Dir.FLAT or conf < self.memory["min_conf"]:
            return Vote(self.name, Dir.FLAT, conf, f"Confidence {conf:.0%} < {self.memory['min_conf']:.0%}")
        return Vote(self.name, d, conf, f"AI Momentum Signal (Confidence: {conf:.0%})")

    def learn(self, direction_correct: bool, conf: float, won: bool, pnl: float):
        m = self.memory
        m["trades"] += 1
        m["wins"] += 1 if won else 0
        m["pnl"] = round(m["pnl"] + pnl, 2)
        key = f"{round(conf * 20) / 20:.2f}"
        old = m["calibration"].get(key, 0.5)
        m["calibration"][key] = round(old * 0.9 + (1.0 if won else 0.0) * 0.1, 4)

        if direction_correct:
            m["weights"]["momentum"] = round(min(1.7, m["weights"]["momentum"] + 0.02), 3)
            m["weights"]["slope"] = round(min(1.7, m["weights"]["slope"] + 0.02), 3)
        else:
            m["weights"]["momentum"] = round(max(0.65, m["weights"]["momentum"] - 0.025), 3)

        if won and conf > 0.72:
            m["min_conf"] = round(min(0.82, m["min_conf"] + 0.004), 3)
        elif not won and conf < 0.58:
            m["min_conf"] = round(max(0.55, m["min_conf"] - 0.008), 3)

        self._save()


# ======================================================================
# AGENT 5: NEWS AGENT (Economic Calendar & Crypto RSS Sentiment)
# ======================================================================
class NewsAgent:
    """
    Tracks high-impact macroeconomic events and crypto news.
    Uses requests with certifi for reliable SSL handshake.
    """

    name = "NEWS"
    FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    BTC_FEEDS = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
    ]

    CAL_KEYWORDS = [
        "fomc", "federal funds", "fed chair", "powell", "cpi", "consumer price",
        "non-farm", "nonfarm", "nfp", "unemployment", "ppi", "producer price",
        "retail sales", "gdp", "pce", "interest rate", "fomc member", "treasury",
    ]

    BULL_WORDS = [
        "etf inflow", "record inflow", "rate cut", "rate cuts", "adoption",
        "surge", "rally", "soars", "breakout", "all-time high", "ath",
        "approved", "accumulation", "bullish", "partnership",
    ]
    BEAR_WORDS = [
        "etf outflow", "rate hike", "hack", "hacked", "exploit", "ban",
        "banned", "lawsuit", "sec charges", "collapse", "liquidation", "crash",
        "fraud", "downgrade", "outflow", "bearish", "selloff", "insolvency",
    ]
    SHOCK_WORDS = [
        "hack", "hacked", "exploit", "ban", "collapse", "sec charges",
        "insolvency", "emergency", "default",
    ]

    def __init__(self, symbol: str = "XAUUSD", window_min: int = 30, enabled: bool = True):
        self.symbol = symbol.upper()
        self.window = timedelta(minutes=window_min)
        self.enabled = enabled
        self._cal = None
        self._cal_at = 0.0
        self._feed = None
        self._feed_at = 0.0
        self.last_msg = ""

    def calendar(self) -> List[Tuple[datetime, str]]:
        """High-impact relevant events: list of (datetime_utc, title)."""
        if time_module.time() - self._cal_at < 900 and self._cal is not None:
            return self._cal
        try:
            resp = requests.get(self.FF_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if resp.status_code == 200:
                raw = resp.json()
                evs = []
                now = datetime.now(timezone.utc)
                for e in raw:
                    if str(e.get("impact", "")).lower() != "high":
                        continue
                    title = str(e.get("title", "")).lower()
                    if not any(k in title for k in self.CAL_KEYWORDS):
                        continue
                    try:
                        dt = datetime.fromisoformat(str(e["date"]).replace("Z", "+00:00"))
                    except Exception:
                        continue
                    if dt > now - timedelta(days=1):
                        evs.append((dt, str(e.get("title", ""))))
                self._cal = evs
                self._cal_at = time_module.time()
        except Exception as ex:
            self.last_msg = f"Calendar feed notice: {ex}"
            self._cal = self._cal or []
        return self._cal or []

    def crypto_headlines(self) -> List[str]:
        """Fetch latest crypto headlines from RSS feeds."""
        if time_module.time() - self._feed_at < 600 and self._feed is not None:
            return self._feed
        import xml.etree.ElementTree as ET

        heads = []
        for url in self.BTC_FEEDS:
            try:
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
                if r.status_code == 200:
                    root = ET.fromstring(r.content)
                    for item in root.iter("item"):
                        t = item.findtext("title")
                        if t:
                            heads.append(t.lower())
                        if len(heads) >= 40:
                            break
            except Exception:
                pass
        self._feed = heads[:60]
        self._feed_at = time_module.time()
        return self._feed

    @staticmethod
    def _to_utc(dt_val) -> datetime:
        if dt_val is None:
            return datetime.now(timezone.utc)
        if hasattr(dt_val, "to_pydatetime"):
            dt_val = dt_val.to_pydatetime()
        if getattr(dt_val, "tzinfo", None) is None:
            return dt_val.replace(tzinfo=timezone.utc)
        return dt_val.astimezone(timezone.utc)

    def blackout(self, ts: datetime) -> Tuple[bool, str]:
        """True => hard veto (high-impact event within window)."""
        if not self.enabled:
            return False, ""
        ts_utc = self._to_utc(ts)
        for dt, title in self.calendar():
            d_utc = self._to_utc(dt)
            if abs((d_utc - ts_utc).total_seconds()) <= self.window.total_seconds():
                msg = f"News blackout active: {title}"
                if msg != self.last_msg:
                    self.last_msg = msg
                return True, msg
        return False, ""

    def sentiment(self) -> Tuple[Dir, float, bool]:
        """Returns (direction, strength, shock_detected)."""
        if not self.enabled:
            return Dir.FLAT, 0.0, False
        heads = self.crypto_headlines()
        if not heads:
            return Dir.FLAT, 0.0, False
        bull = sum(1 for h in heads for w in self.BULL_WORDS if w in h)
        bear = sum(1 for h in heads for w in self.BEAR_WORDS if w in h)
        shock = any(w in h for h in heads for w in self.SHOCK_WORDS)
        net = bull - bear
        if shock:
            return Dir.FLAT, 0.0, True
        if net >= 2:
            return Dir.LONG, min(0.40, 0.15 + net * 0.05), False
        if net <= -2:
            return Dir.SHORT, min(0.40, 0.15 + abs(net) * 0.05), False
        return Dir.FLAT, 0.0, False

    def vote(self) -> Vote:
        d, s, shock = self.sentiment()
        if shock:
            return Vote(self.name, Dir.FLAT, 0.0, "Shock headlines detected -> Stay FLAT")
        if d == Dir.FLAT:
            return Vote(self.name, Dir.FLAT, 0.0, "Neutral news sentiment")
        label = "Bullish" if d == Dir.LONG else "Bearish"
        return Vote(self.name, d, s, f"{label} headline sentiment")


# ======================================================================
# POSITION MODEL
# ======================================================================
@dataclass
class Position:
    direction: Dir
    entry: float
    stop: float
    size: float
    lots: float
    tp1: float
    tp2: float
    tp3: Optional[float]
    remaining: float
    risk: float
    highest: float
    lowest: float
    partials: int = 0
    symbol: str = "XAUUSD"


# ======================================================================
# ARBITER (Consensus Engine)
# ======================================================================
class Arbiter:
    """Democratic decision arbiter requiring aligned consensus across agents."""

    MIN_AGREEMENT = 3  # At least 3 non-flat aligned agents
    MIN_SCORE = 1.8    # Sum of aligned convictions
    MIN_RR = 1.9       # Minimum Risk-to-Reward ratio

    def __init__(self):
        self.trend = TrendAgent()
        self.liq = LiquidityAgent()
        self.ind = IndicatorAgent()
        self.ai = AILearnerAgent()
        self.news = NewsAgent(enabled=config.NEWS_ENABLED, window_min=config.NEWS_WINDOW_MIN)

    def decide(
        self,
        df1: pd.DataFrame,
        df5: Optional[pd.DataFrame],
        df15: Optional[pd.DataFrame],
        df1h: Optional[pd.DataFrame],
        daily: pd.DataFrame,
        risk: RiskManager,
        news: Optional[NewsAgent] = None,
        symbol: str = "XAUUSD",
    ) -> Tuple[Optional[Dict], str, List[Vote]]:
        ts = df1.index[-1]
        active_news = news or self.news

        votes = [
            self.trend.vote(df1, df5, df15, df1h),
            self.liq.vote(df1, daily),
            self.ind.vote(df1),
            self.ai.vote(df1),
        ]

        if active_news and active_news.enabled:
            black, msg = active_news.blackout(ts)
            if black:
                return None, f"VETO: {msg}", votes
            votes.append(active_news.vote())

        # Check Risk Manager permissions
        can_trade, why = risk.can_trade()
        if not can_trade:
            return None, f"RISK VETO: {why}", votes

        # Evaluate consensus for LONG and SHORT
        for d in (Dir.LONG, Dir.SHORT):
            aligned = [v for v in votes if v.direction == d]
            score = sum(v.strength for v in aligned)
            if len(aligned) >= self.MIN_AGREEMENT and score >= self.MIN_SCORE:
                sig = self._build(df1, d, votes, score, risk, symbol)
                if sig:
                    return sig, "TRADE TRIGGERED", votes

        return None, "No consensus", votes

    def _build(
        self,
        df: pd.DataFrame,
        d: Dir,
        votes: List[Vote],
        score: float,
        risk: RiskManager,
        symbol: str,
    ) -> Optional[Dict]:
        a = atr(df)
        if a <= 0:
            return None
        curr = df.iloc[-1]
        entry = float(curr["close"])

        if d == Dir.LONG:
            stop = float(df["low"].iloc[-10:].min()) - a * 0.12
            if entry <= stop:
                return None
            plan = risk.autotrade_plan(entry, stop, direction="long", symbol=symbol)
            if not plan.get("ok"):
                return None
            rr = max(self.MIN_RR, round((plan["tp1"] - entry) / (entry - stop), 2))
            return {
                "direction": d,
                "entry": entry,
                "stop": stop,
                "lots": plan.get("lots", 0.01),
                "size": plan.get("size", 1.0),
                "tp1": plan["tp1"],
                "tp2": plan["tp2"],
                "tp3": plan.get("tp3"),
                "rr": rr,
                "score": round(score, 2),
                "symbol": symbol,
                "risk_amount": plan.get("risk_amount", 0.0),
                "risk_pct": plan.get("risk_pct", 0.18),
                "leverage": plan.get("leverage"),
                "margin_required": plan.get("margin_required", 0.0),
                "liquidation_price": plan.get("liquidation_price"),
                "notional_value": plan.get("notional_value", 0.0),
                "reason": " | ".join(f"{v.agent}:{v.reason}" for v in votes),
            }
        else:
            stop = float(df["high"].iloc[-10:].max()) + a * 0.12
            if stop <= entry:
                return None
            plan = risk.autotrade_plan(entry, stop, direction="short", symbol=symbol)
            if not plan.get("ok"):
                return None
            rr = max(self.MIN_RR, round((entry - plan["tp1"]) / (stop - entry), 2))
            return {
                "direction": d,
                "entry": entry,
                "stop": stop,
                "lots": plan.get("lots", 0.01),
                "size": plan.get("size", 1.0),
                "tp1": plan["tp1"],
                "tp2": plan["tp2"],
                "tp3": plan.get("tp3"),
                "rr": rr,
                "score": round(score, 2),
                "symbol": symbol,
                "risk_amount": plan.get("risk_amount", 0.0),
                "risk_pct": plan.get("risk_pct", 0.18),
                "leverage": plan.get("leverage"),
                "margin_required": plan.get("margin_required", 0.0),
                "liquidation_price": plan.get("liquidation_price"),
                "notional_value": plan.get("notional_value", 0.0),
                "reason": " | ".join(f"{v.agent}:{v.reason}" for v in votes),
            }

    def manage(self, pos: Position, candle, risk: RiskManager) -> Tuple[float, bool, str]:
        """
        Manage open position using progressive break-even & trailing stops.
        Returns (exit_price, is_fully_closed, reason).
        """
        h = float(candle["high"])
        l = float(candle["low"])
        pos.highest = max(pos.highest, h)
        pos.lowest = min(pos.lowest, l)
        dir_str = "long" if pos.direction == Dir.LONG else "short"

        # Check progressive auto break-even
        be_stop, be_reason = risk.auto_breakeven_stop(
            dir_str, pos.entry, pos.stop, pos.highest, pos.lowest, pos.risk
        )
        if be_stop != pos.stop:
            pos.stop = be_stop

        if pos.direction == Dir.LONG:
            # TP1 Hit
            if pos.partials == 0 and h >= pos.tp1:
                pos.remaining *= 0.5
                pos.partials = 1
                pos.stop = pos.entry + pos.risk * 0.05  # Buffer BE
                return pos.tp1, False, "TP1 Hit (+1.5R) -> Partial Taken + BE Locked"

            # TP2 Hit
            if pos.partials == 1 and pos.tp2 and h >= pos.tp2:
                pos.remaining *= 0.5
                pos.partials = 2
                return pos.tp2, False, "TP2 Hit (+2.5R) -> Partial Taken + Trailing Stop"

            # Trailing stop update after TP1
            if pos.partials >= 1:
                trail_stop = pos.highest - pos.risk * 1.5
                pos.stop = max(pos.stop, pos.entry, trail_stop)

            # Stop Loss / BE Hit
            if l <= pos.stop:
                return pos.stop, True, f"Exit Hit @ {pos.stop:.2f}"

        else:  # SHORT
            # TP1 Hit
            if pos.partials == 0 and l <= pos.tp1:
                pos.remaining *= 0.5
                pos.partials = 1
                pos.stop = pos.entry - pos.risk * 0.05
                return pos.tp1, False, "TP1 Hit (+1.5R) -> Partial Taken + BE Locked"

            # TP2 Hit
            if pos.partials == 1 and pos.tp2 and l <= pos.tp2:
                pos.remaining *= 0.5
                pos.partials = 2
                return pos.tp2, False, "TP2 Hit (+2.5R) -> Partial Taken + Trailing Stop"

            # Trailing stop update after TP1
            if pos.partials >= 1:
                trail_stop = pos.lowest + pos.risk * 1.5
                pos.stop = min(pos.stop, pos.entry, trail_stop)

            # Stop Loss / BE Hit
            if h >= pos.stop:
                return pos.stop, True, f"Exit Hit @ {pos.stop:.2f}"

        return 0.0, False, ""


# ======================================================================
# OUTBOUND TELEGRAM ALERTS
# ======================================================================
def send_telegram(text: str, token: str = "", chat_id: str = ""):
    tok = token or config.TELEGRAM_TOKEN
    cid = chat_id or config.TELEGRAM_CHAT_ID
    if not tok or not cid:
        return
    try:
        url = f"https://api.telegram.org/bot{tok}/sendMessage"
        payload = {"chat_id": cid, "text": text, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=8)
    except Exception as e:
        print(f"[Telegram Alert] Failed: {e}")


# ======================================================================
# PUBLIC HELPER: ON-DEMAND SYMBOL EVALUATION
# ======================================================================
def evaluate_symbol(
    symbol: str = "XAUUSD",
    source: str = "demo",
    exchange_id: str = "binance",
    leverage: Optional[float] = None,
) -> Dict:
    """
    Evaluates current market conditions for a symbol across all 5 agents.
    Returns structured results for Telegram bot or CLI.
    """
    feed = MarketDataFeed(symbol=symbol, source=source, exchange_id=exchange_id)
    arbiter = Arbiter()
    risk = RiskManager(equity=config.INITIAL_EQUITY, symbol=symbol)
    risk.set_leverage(leverage if leverage is not None else config.DEFAULT_LEVERAGE)
    quote = feed.get_latest_price()

    data = feed.multi_tf(limit_1m=700)
    if not data or "1m" not in data or data["1m"].empty:
        return {
            "symbol": symbol,
            "price": quote.get("mark", 0.0),
            "signal": None,
            "status": "No market data available",
            "votes": [],
        }

    df1 = data["1m"]
    daily = data.get("daily", pd.DataFrame())
    if len(daily) < 2:
        daily = df1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()

    sig, why, votes = arbiter.decide(
        df1, data.get("5m"), data.get("15m"), data.get("1h"), daily, risk, symbol=symbol
    )

    formatted_votes = []
    for v in votes:
        formatted_votes.append({
            "agent": v.agent,
            "direction": v.direction.name,
            "strength": round(v.strength, 2),
            "reason": v.reason,
        })

    return {
        "symbol": symbol,
        "price": quote.get("mark", float(df1.iloc[-1]["close"])),
        "signal": sig,
        "status": why,
        "votes": formatted_votes,
        "risk_summary": risk.summary(),
    }


# ======================================================================
# LIVE EXECUTION LOOP
# ======================================================================
def run_live(args):
    feed = MarketDataFeed(symbol=args.symbol, source=args.source, exchange_id=args.exchange)
    arbiter = Arbiter()
    arbiter.news = NewsAgent(symbol=args.symbol, window_min=args.news_window, enabled=args.news)
    risk = RiskManager(equity=args.equity, symbol=args.symbol)

    leverage = getattr(args, "leverage", config.DEFAULT_LEVERAGE)
    risk.set_leverage(leverage)

    def on_execution_event(event_name: str, order: TradeOrder, message: str):
        print(f"\n{message}\n")
        send_telegram(message)

    engine = ExecutionEngine(risk_manager=risk, event_callback=on_execution_event)

    print(f"\n{'='*64}")
    print(f"  MULTI-AGENT TRADING ENGINE - LIVE")
    print(f"  Symbol: {args.symbol} | Feed: {feed.source} | Poll: {args.poll}s | Equity: {risk.state.equity:.2f}")
    print(f"  Leverage: {risk.get_leverage():.0f}x (1x-500x adjustable) | Autotrade: ACTIVE")
    print(f"  Agents: TREND + LIQUIDITY + INDICATORS + AI-LEARNER + NEWS -> ARBITER -> RISK VETO")
    print(f"{'='*64}\n")

    while True:
        try:
            data = feed.multi_tf(limit_1m=700)
            if not data or "1m" not in data or data["1m"].empty:
                time_module.sleep(args.poll)
                continue

            df1 = data["1m"]
            daily = data.get("daily", pd.DataFrame())
            if len(daily) < 2:
                daily = df1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()

            candle = df1.iloc[-1]
            candle_dict = {
                "open": float(candle.get("open", candle["close"])),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
            }

            # 1. Update active and pending trades with live candle
            engine.update_market_candle(args.symbol, candle_dict)

            # 2. Look for new setups if flat and no pending order
            if args.symbol not in engine.active_positions and args.symbol not in engine.pending_setups:
                sig, why, votes = arbiter.decide(
                    df1, data.get("5m"), data.get("15m"), data.get("1h"), daily, risk,
                    news=arbiter.news, symbol=args.symbol,
                )
                if sig:
                    order = engine.register_setup(sig, reason=sig.get("reason", ""))
                    msg = (
                        f"⚡ **NEW MULTI-AGENT SETUP IDENTIFIED**\n"
                        f"Symbol: `{args.symbol}` | **{order.direction}**\n"
                        f"• Entry Level: `${order.entry_level:,.2f}`\n"
                        f"• Stop Loss: `${order.stop_loss:,.2f}` | R:R: `1:{order.rr_ratio:.1f}`\n"
                        f"• Lots: `{order.lots}` | Leverage: `{order.leverage:.0f}x` | Margin: `${order.margin_required:,.2f}`\n"
                        f"• Target 1: `${order.tp1:,.2f}` | Target 2: `${order.tp2:,.2f}`\n\n"
                        f"**Confluence:** {order.setup_reason}\n"
                        f"_Monitoring market levels. Trade will execute automatically upon reach._"
                    )
                    print(f"\n{'='*60}\n{msg}\n{'='*60}\n")
                    send_telegram(msg)
                    # Check if entry is reached on current candle
                    engine.update_market_candle(args.symbol, candle_dict)

            time_module.sleep(args.poll)

        except KeyboardInterrupt:
            print("\n[Engine] Stopped by user")
            break
        except Exception as e:
            print(f"[Engine] Error: {e}")
            traceback.print_exc()
            time_module.sleep(args.poll)


# ======================================================================
# BACKTEST ENGINE
# ======================================================================
def run_backtest(args) -> Dict:
    print(f"\n{'='*64}")
    print(f"  MULTI-AGENT SYSTEM BACKTEST")
    print(f"  Bars: {args.bars} | Symbol: {args.symbol} | Equity: {args.equity}")
    print(f"{'='*64}\n")

    feed = MarketDataFeed(symbol=args.symbol, source="demo")
    df1 = feed.fetch(limit=args.bars)
    df5 = df1.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    df15 = df1.resample("15min").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    df1h = df1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    daily = df1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()

    import tempfile
    arbiter = Arbiter()
    arbiter.news.enabled = False
    arbiter.ai.path = os.path.join(tempfile.gettempdir(), "bt_temp_mem.json")
    risk = RiskManager(equity=args.equity, symbol=args.symbol, state_file=os.path.join(tempfile.gettempdir(), "bt_temp_risk.json"))
    pos: Optional[Position] = None
    trades = 0
    wins = 0
    pnl_sum = 0.0
    curve = [args.equity]

    start_idx = min(200, len(df1) // 3)
    for i in range(start_idx, len(df1) - 1):
        ts = df1.index[i]
        candle = df1.iloc[i]
        w1 = df1.iloc[max(0, i - 250):i + 1]
        idx5 = df5.index.searchsorted(ts, side="right")
        w5 = df5.iloc[:idx5]
        idx15 = df15.index.searchsorted(ts, side="right")
        w15 = df15.iloc[:idx15]
        idx1h = df1h.index.searchsorted(ts, side="right")
        w1h = df1h.iloc[:idx1h]
        idxd = daily.index.searchsorted(ts, side="right")
        wd = daily.iloc[:idxd]

        if pos is not None:
            ep, closed, _ = arbiter.manage(pos, candle, risk)
            if ep > 0:
                pnl = ((ep - pos.entry) if pos.direction == Dir.LONG else (pos.entry - ep)) * pos.remaining
                pnl_sum += pnl
                trades += 1
                if pnl > 0:
                    wins += 1
                r_mult = pnl / (pos.risk * pos.size) if (pos.risk * pos.size) > 0 else 0.0
                risk.update_after_trade(pnl, r_multiple=r_mult)
                if closed:
                    pos = None

        if pos is None and len(wd) >= 2:
            sig, why, _ = arbiter.decide(w1, w5, w15, w1h, wd, risk, symbol=args.symbol)
            if sig:
                pos = Position(
                    direction=sig["direction"],
                    entry=sig["entry"],
                    stop=sig["stop"],
                    size=sig["size"],
                    lots=sig["lots"],
                    tp1=sig["tp1"],
                    tp2=sig["tp2"],
                    tp3=sig.get("tp3"),
                    remaining=sig["size"],
                    risk=abs(sig["entry"] - sig["stop"]),
                    highest=sig["entry"],
                    lowest=sig["entry"],
                    symbol=args.symbol,
                )

        curve.append(risk.state.equity)

    eq_arr = np.array(curve)
    peak = np.maximum.accumulate(eq_arr)
    dd = float(np.max((peak - eq_arr) / np.maximum(peak, 1.0)) * 100)
    winrate = round(wins / trades * 100, 1) if trades else 0.0
    ret = round((risk.state.equity / args.equity - 1) * 100, 2)

    stats = {
        "trades": trades,
        "wins": wins,
        "winrate": winrate,
        "net_pnl": round(pnl_sum, 2),
        "return_pct": ret,
        "max_drawdown": round(dd, 2),
        "final_equity": round(risk.state.equity, 2),
    }

    print(f"  Trades      : {trades}")
    print(f"  Win Rate    : {winrate}%")
    print(f"  Net PnL     : {pnl_sum:.2f}")
    print(f"  Return      : {ret:+.2f}%")
    print(f"  Max DD      : {dd:.2f}%")
    print(f"  Final Equity: {risk.state.equity:.2f}")
    print(f"{'='*64}\n")
    return stats


# ======================================================================
# CLI ENTRY POINT
# ======================================================================
def main():
    p = argparse.ArgumentParser(description="Institutional Multi-Agent Trading Engine")
    p.add_argument("mode", choices=["backtest", "live"], default="live", nargs="?")
    p.add_argument("--symbol", default=config.DEFAULT_SYMBOL)
    p.add_argument("--source", default=config.DEFAULT_DATA_SOURCE)
    p.add_argument("--exchange", default=config.DEFAULT_EXCHANGE)
    p.add_argument("--poll", type=int, default=config.POLL_INTERVAL)
    p.add_argument("--equity", type=float, default=config.INITIAL_EQUITY)
    p.add_argument("--risk", type=float, default=config.DEFAULT_RISK_PCT)
    p.add_argument("--leverage", type=float, default=config.DEFAULT_LEVERAGE, help="Leverage multiplier (1x to 500x)")
    p.add_argument("--bars", type=int, default=2000)
    p.add_argument("--news", action="store_true", default=config.NEWS_ENABLED)
    p.add_argument("--news-window", type=int, default=config.NEWS_WINDOW_MIN)
    args = p.parse_args()

    if args.mode == "backtest":
        run_backtest(args)
    else:
        run_live(args)


if __name__ == "__main__":
    main()
