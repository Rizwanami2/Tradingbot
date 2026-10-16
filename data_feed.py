#!/usr/bin/env python3
"""
Robust Multi-Source Market Data Feed with Caching
=================================================
Priority: realtime_prices (geo-safe) → CCXT → yfinance → Demo
Caches candles in Parquet for speed and reliability.
"""

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional
import pandas as pd
import numpy as np

CACHE_DIR = Path("data_cache")
CACHE_DIR.mkdir(exist_ok=True)

class DataFeed:
    def __init__(
        self,
        symbol: str = "XAUUSD",
        source: str = "ccxt",
        exchange_id: str = "binance",
        timeframe: str = "1m",
        api_key: str = "",
        api_secret: str = "",
    ):
        self.symbol = symbol.upper()
        self.source = source.lower()
        self.exchange_id = exchange_id.lower()
        self.timeframe = timeframe
        self.api_key = api_key
        self.api_secret = api_secret
        self._ex = None
        self._init_exchange()

    def _init_exchange(self):
        if self.source in ("ccxt", "binance", "bybit", "okx"):
            try:
                import ccxt
                exchange_class = getattr(ccxt, self.exchange_id)
                self._ex = exchange_class({
                    "apiKey": self.api_key,
                    "secret": self.api_secret,
                    "enableRateLimit": True,
                    "options": {"defaultType": "future"},
                })
                print(f"[DataFeed] Connected to {self.exchange_id}")
            except Exception as e:
                print(f"[DataFeed] CCXT failed: {e} → falling back")
                self.source = "yfinance"

    def _symbol_map(self) -> str:
        s = self.symbol
        if "/" in s:
            return s
        if s in ("XAUUSD", "XAUTUSD", "GOLD"):
            return "PAXG/USDT" if self.exchange_id == "binance" else ("XAU/USDT" if self.exchange_id == "bybit" else "XAUUSD")
        if s.endswith("USDT"):
            return f"{s[:-4]}/USDT"
        if s.endswith("USD"):
            return f"{s[:-3]}/USDT"
        return f"{s}/USDT"

    def _cache_path(self, tf: str, limit: int) -> Path:
        safe_sym = self.symbol.replace("/", "_")
        return CACHE_DIR / f"{safe_sym}_{tf}_{limit}.parquet"

    def fetch(self, limit: int = 1000, timeframe: Optional[str] = None) -> pd.DataFrame:
        tf = timeframe or self.timeframe
        cache_file = self._cache_path(tf, limit)

        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            max_age = 120 if tf == "1m" else 900
            if age < max_age:
                try:
                    df = pd.read_parquet(cache_file)
                    if len(df) >= limit * 0.9:
                        return df.tail(limit)
                except Exception:
                    pass

        df = self._fetch_real(limit, tf)
        if not df.empty:
            try:
                df.to_parquet(cache_file)
            except Exception:
                pass
        return df

    def _fetch_real(self, limit: int, tf: str) -> pd.DataFrame:
        # 0. Geo-safe Binance mirrors / realtime_prices OHLCV
        try:
            from realtime_prices import fetch_ohlcv
            iv = tf if tf in ("1m", "5m", "15m", "1h", "4h", "1d") else "1m"
            df = fetch_ohlcv(self.symbol, interval=iv, limit=limit)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            print(f"[DataFeed] realtime ohlcv: {e}")

        # 1. CCXT
        if self._ex:
            try:
                sym = self._symbol_map()
                ohlcv = self._ex.fetch_ohlcv(sym, timeframe=tf, limit=limit)
                df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]].astype(float)
                return df
            except Exception as e:
                print(f"[DataFeed] CCXT error: {e}")

        # 2. yfinance
        try:
            import yfinance as yf
            yf_map = {
                "XAUUSD": "GC=F", "XAUTUSD": "GC=F", "GOLD": "GC=F", "PAXGUSDT": "PAXG-USD",
                "BTCUSD": "BTC-USD", "BTCUSDT": "BTC-USD",
            }
            yf_sym = yf_map.get(self.symbol, self.symbol)
            period = "7d" if tf in ("1m", "5m") else "60d"
            t = yf.Ticker(yf_sym)
            df = t.history(period=period, interval=tf)
            if not df.empty:
                df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].tail(limit)
                df.index = pd.to_datetime(df.index, utc=True)
                return df.astype(float)
        except Exception as e:
            print(f"[DataFeed] yfinance error: {e}")

        if self.source == "demo":
            return self._demo(limit)
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def _demo(self, limit: int = 600, base: float = 2650.0) -> pd.DataFrame:
        dates = pd.date_range(end=datetime.now(timezone.utc), periods=limit, freq="1min")
        rets = np.random.randn(limit) * 0.0008
        p = base * np.cumprod(1 + rets)
        return pd.DataFrame({
            "open": p, "high": p * 1.0008, "low": p * 0.9992,
            "close": p, "volume": np.random.randint(100, 2000, limit)
        }, index=dates)

    def multi_tf(self, limit_1m: int = 1500) -> Dict[str, pd.DataFrame]:
        df1 = self.fetch(limit=limit_1m, timeframe="1m")
        if df1.empty:
            return {}
        out = {"1m": df1}
        for tf, rule in [("5m", "5min"), ("15m", "15min"), ("1h", "1h"), ("4h", "4h")]:
            try:
                out[tf] = df1.resample(rule).agg({
                    "open": "first", "high": "max", "low": "min",
                    "close": "last", "volume": "sum"
                }).dropna()
            except Exception:
                out[tf] = pd.DataFrame()
        out["daily"] = df1.resample("1D").agg({
            "open": "first", "high": "max", "low": "min", "close": "last"
        }).dropna()
        return out

    def get_latest_price(self) -> Dict:
        df = self.fetch(limit=5)
        if not df.empty:
            c = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else c
            open_p = float(prev["close"]) if float(prev["close"]) > 0 else float(c["open"])
            chg = ((float(c["close"]) - open_p) / open_p * 100) if open_p > 0 else 0.0
            return {
                "ok": True,
                "symbol": self.symbol,
                "mark": float(c["close"]),
                "close": float(c["close"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "open": float(c["open"]),
                "change_pct": round(chg, 2),
                "recommendation": "NEUTRAL",
                "source": self.source,
            }
        return {
            "ok": False,
            "symbol": self.symbol,
            "error": "Market data unavailable; no synthetic price was substituted.",
            "source": "unavailable",
        }
