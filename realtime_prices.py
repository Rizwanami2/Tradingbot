#!/usr/bin/env python3
"""
Reliable real-time price + levels for BTC and Gold
==================================================
Works even when api.binance.com is geo-blocked (451).

Priority for crypto:
  1) data-api.binance.vision (public, no geo block)
  2) api.binance.us
  3) Coinbase spot
  4) TradingView scanner
  5) CCXT / yfinance if installed

Priority for gold:
  1) TradingView OANDA:XAUUSD
  2) Binance PAXGUSDT via vision/US
  3) yfinance GC=F
"""
from __future__ import annotations

from datetime import datetime, timezone
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

UA = {"User-Agent": "Mozilla/5.0 (compatible; TradingBot/2.0)"}
TIMEOUT = 3.5

_SESSION = requests.Session()
_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=25, max_retries=1)
_SESSION.mount("http://", _adapter)
_SESSION.mount("https://", _adapter)
_SESSION.headers.update(UA)

_SPOT_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_SPOT_LOCK = threading.Lock()
_LEVELS_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_LEVELS_LOCK = threading.Lock()


def _norm_symbol(symbol: str) -> str:
    s = symbol.upper().replace("/", "").replace("-", "").replace(":", "").strip()
    if s in ("BTC", "BTCUSD", "XBT"):
        return "BTCUSDT"
    if s in ("XAU", "GOLD", "XAUTUSD", "XAUUSD", "PAXG", "PAXGUSD", "PAXGUSDT"):
        return "XAUUSD"
    if s.endswith("USD") and not s.endswith("USDT") and s not in ("XAUUSD",):
        if any(x in s for x in ("BTC", "XAU", "GOLD", "PAXG", "XAUT")):
            return s[:-3] + "USDT"
    return s


def _is_gold(sym: str) -> bool:
    s = _norm_symbol(sym)
    return s in ("XAUUSD", "XAUTUSD", "GOLD", "PAXGUSDT") or "XAU" in s or "PAXG" in s


def _is_crypto(sym: str) -> bool:
    s = _norm_symbol(sym)
    return any(x in s for x in ("BTC", "BITCOIN", "XBT")) or s.endswith("USDT")


def _binance_symbol(sym: str) -> str:
    s = _norm_symbol(sym)
    if _is_gold(s):
        return "PAXGUSDT"
    if s.endswith("USDT"):
        return s
    if s.endswith("USD"):
        return s[:-3] + "USDT"
    return s + "USDT"


def _get_json(url: str, params: Optional[dict] = None) -> Optional[Any]:
    try:
        r = _SESSION.get(url, params=params, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def fetch_spot_price(symbol: str = "BTCUSDT", max_age_seconds: float = 2.5) -> Dict[str, Any]:
    """Return unified live quote dict with ok/mark/source with in-memory TTL caching."""
    sym = _norm_symbol(symbol)
    now = time.time()
    with _SPOT_LOCK:
        if sym in _SPOT_CACHE:
            ts, val = _SPOT_CACHE[sym]
            if now - ts < max_age_seconds and val.get("ok"):
                return val.copy()

    errors: List[str] = []

    # 0. Live MT5 Broker Quote (Primary live source)
    try:
        from mt5_broker import get_active_broker
        b = get_active_broker()
        if b is not None and hasattr(b, "get_live_quote"):
            q = b.get_live_quote(sym)
            if q.get("ok") and float(q.get("mark") or 0.0) > 0.0:
                with _SPOT_LOCK:
                    _SPOT_CACHE[sym] = (time.time(), q)
                return q
    except Exception:
        pass

    if _is_gold(sym):
        q = _tv_quote("XAUUSD")
        if q and q.get("ok"):
            return q
        for base in ("https://data-api.binance.vision", "https://api.binance.us"):
            data = _get_json(f"{base}/api/v3/ticker/price", {"symbol": "PAXGUSDT"})
            if data and data.get("price"):
                px = float(data["price"])
                return _ok(sym, px, "Binance PAXG", extra={"proxy": "PAXGUSDT"})
            errors.append(f"paxg@{base}")

    if _is_crypto(sym) or sym.endswith("USDT"):
        bsym = _binance_symbol(sym)
        for base in ("https://data-api.binance.vision", "https://api.binance.us"):
            data = _get_json(f"{base}/api/v3/ticker/24hr", {"symbol": bsym})
            if data and data.get("lastPrice"):
                last = float(data["lastPrice"])
                open_p = float(data.get("openPrice") or last)
                high_p = float(data.get("highPrice") or last)
                low_p = float(data.get("lowPrice") or last)
                vol = float(data.get("volume") or 0)
                chg = float(data.get("priceChangePercent") or 0)
                return {
                    "ok": True,
                    "symbol": sym,
                    "mark": last,
                    "close": last,
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "volume": vol,
                    "change_pct": round(chg, 2),
                    "recommendation": "NEUTRAL",
                    "source": base.replace("https://", ""),
                }
            data = _get_json(f"{base}/api/v3/ticker/price", {"symbol": bsym})
            if data and data.get("price"):
                return _ok(sym, float(data["price"]), base.replace("https://", ""))
            errors.append(base)

        cb_map = {"BTCUSDT": "BTC-USD"}
        cb = cb_map.get(bsym)
        if cb:
            data = _get_json(f"https://api.coinbase.com/v2/prices/{cb}/spot")
            if data and data.get("data", {}).get("amount"):
                return _ok(sym, float(data["data"]["amount"]), "Coinbase")

    q = _tv_quote(sym if not _is_gold(sym) else "XAUUSD")
    if q and q.get("ok"):
        q["symbol"] = sym
        return q

    try:
        import yfinance as yf
        yf_map = {
            "BTCUSDT": "BTC-USD",
            "XAUUSD": "GC=F", "XAUTUSD": "GC=F", "PAXGUSDT": "PAXG-USD",
        }
        t = yf.Ticker(yf_map.get(sym, sym))
        hist = t.history(period="1d", interval="1m")
        if hist is not None and not hist.empty:
            last = float(hist["Close"].iloc[-1])
            return _ok(sym, last, "yfinance")
    except Exception as e:
        errors.append(f"yf:{e}")

    return {
        "ok": False,
        "symbol": sym,
        "mark": 0.0,
        "error": "All price sources failed: " + "; ".join(errors[:4]),
        "source": "unavailable",
    }


def _ok(symbol: str, price: float, source: str, extra: Optional[dict] = None) -> Dict[str, Any]:
    out = {
        "ok": True,
        "symbol": symbol,
        "mark": float(price),
        "close": float(price),
        "open": float(price),
        "high": float(price),
        "low": float(price),
        "volume": 0.0,
        "change_pct": 0.0,
        "recommendation": "NEUTRAL",
        "source": source,
    }
    if extra:
        out.update(extra)
    with _SPOT_LOCK:
        _SPOT_CACHE[symbol] = (time.time(), out)
    return out


def _tv_quote(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        from tradingview_feed import fetch_tradingview_quote
        q = fetch_tradingview_quote(symbol)
        if q and q.get("ok") and float(q.get("mark") or 0) > 0:
            return q
    except Exception:
        pass
    try:
        if _is_gold(symbol):
            ticker, screener = "OANDA:XAUUSD", "cfd"
        elif "BTC" in symbol.upper():
            ticker, screener = "BINANCE:BTCUSDT", "crypto"
        else:
            ticker, screener = f"BINANCE:{_binance_symbol(symbol)}", "crypto"
        url = f"https://scanner.tradingview.com/{screener}/scan"
        payload = {
            "symbols": {"tickers": [ticker]},
            "columns": ["close", "open", "high", "low", "volume", "change", "Recommend.All", "RSI"],
        }
        r = requests.post(url, json=payload, headers=UA, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json().get("data") or []
            if data and "d" in data[0]:
                vals = data[0]["d"]
                close_p = float(vals[0] or 0)
                if close_p > 0:
                    return {
                        "ok": True,
                        "symbol": symbol.upper(),
                        "mark": close_p,
                        "close": close_p,
                        "open": float(vals[1] or close_p),
                        "high": float(vals[2] or close_p),
                        "low": float(vals[3] or close_p),
                        "volume": float(vals[4] or 0),
                        "change_pct": round(float(vals[5] or 0), 2),
                        "recommendation": "NEUTRAL",
                        "rsi": float(vals[7] or 50) if len(vals) > 7 else 50.0,
                        "source": "TradingView",
                        "tv_symbol": ticker,
                    }
    except Exception:
        pass
    return None


def fetch_ohlcv(symbol: str, interval: str = "1m", limit: int = 500) -> pd.DataFrame:
    """OHLCV prioritizing live MT5 broker candles with geo-safe Binance fallback.

    For gold prefer PAXGUSDT klines (real daily/1h) over TV synthetic 1m bars if MT5 unavailable.
    """
    # 0. Live MT5 Broker Candles (Primary source of truth)
    try:
        from mt5_broker import get_active_broker
        b = get_active_broker()
        if b is not None and hasattr(b, "get_candles"):
            df_mt5 = b.get_candles(symbol, timeframe=interval, limit=limit)
            if df_mt5 is not None and not df_mt5.empty and len(df_mt5) >= 3:
                return df_mt5
    except Exception:
        pass

    bsym = _binance_symbol(symbol)
    if _is_gold(symbol):
        bsym = "PAXGUSDT"

    iv = interval.lower().replace("min", "m").replace("hour", "h").replace("day", "d")
    if iv in ("1d", "d", "1day", "daily"):
        iv = "1d"
    elif iv in ("1h", "h", "60m", "60min"):
        iv = "1h"
    elif iv in ("15m", "15min"):
        iv = "15m"
    elif iv in ("5m", "5min"):
        iv = "5m"
    elif iv in ("1m", "m", "1min"):
        iv = "1m"

    for base in ("https://data-api.binance.vision", "https://api.binance.us"):
        data = _get_json(
            f"{base}/api/v3/klines",
            {"symbol": bsym, "interval": iv, "limit": min(limit, 1000)},
        )
        if isinstance(data, list) and len(data) > 5:
            rows = []
            for k in data:
                rows.append({
                    "timestamp": pd.to_datetime(k[0], unit="ms", utc=True),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
            df = pd.DataFrame(rows).set_index("timestamp")
            return df.tail(limit)

    if _is_gold(symbol):
        try:
            from tradingview_feed import fetch_tradingview_bars
            df = fetch_tradingview_bars("XAUUSD", limit=limit)
            if df is not None and len(df) >= 10:
                return df.tail(limit)
        except Exception:
            pass
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def compute_levels(symbol: str = "BTCUSDT", max_age_seconds: float = 15.0) -> Dict[str, Any]:
    """Real-time key levels: mark, PDH/PDL, pivots, session H/L, swings with TTL cache."""
    sym = _norm_symbol(symbol)
    now = time.time()
    with _LEVELS_LOCK:
        if sym in _LEVELS_CACHE:
            ts, val = _LEVELS_CACHE[sym]
            if now - ts < max_age_seconds and val.get("ok"):
                return val.copy()

    quote = fetch_spot_price(sym)
    mark = float(quote.get("mark") or 0)

    df = fetch_ohlcv(sym, interval="1h", limit=120)
    if df.empty:
        df = fetch_ohlcv(sym, interval="15m", limit=200)
    if df.empty:
        return {
            "ok": quote.get("ok", False),
            "symbol": sym,
            "mark": mark,
            "source": quote.get("source"),
            "error": quote.get("error") or "No OHLCV for levels",
            "levels": {},
        }

    pdh = pdl = pivot = r1 = s1 = r2 = s2 = None
    daily = fetch_ohlcv(sym, interval="1d", limit=10)
    if daily.empty or len(daily) < 2:
        daily = df.resample("1D").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}
        ).dropna()
    if len(daily) >= 2:
        prev = daily.iloc[-2]
        pdh = float(prev["high"])
        pdl = float(prev["low"])
        pc = float(prev["close"])
        pivot = (pdh + pdl + pc) / 3.0
        r1 = 2 * pivot - pdl
        s1 = 2 * pivot - pdh
        r2 = pivot + (pdh - pdl)
        s2 = pivot - (pdh - pdl)

    window = df.tail(24)
    sess_high = float(window["high"].max())
    sess_low = float(window["low"].min())

    highs = df["high"].values
    lows = df["low"].values
    swing_highs = []
    swing_lows = []
    n = len(df)
    for i in range(2, n - 2):
        if highs[i] >= highs[i - 1] and highs[i] >= highs[i - 2] and highs[i] >= highs[i + 1] and highs[i] >= highs[i + 2]:
            swing_highs.append(float(highs[i]))
        if lows[i] <= lows[i - 1] and lows[i] <= lows[i - 2] and lows[i] <= lows[i + 1] and lows[i] <= lows[i + 2]:
            swing_lows.append(float(lows[i]))
    near_sh = swing_highs[-3:] if swing_highs else []
    near_sl = swing_lows[-3:] if swing_lows else []

    levels = {
        "mark": mark,
        "pdh": pdh,
        "pdl": pdl,
        "pivot": pivot,
        "r1": r1,
        "s1": s1,
        "r2": r2,
        "s2": s2,
        "session_high": sess_high,
        "session_low": sess_low,
        "swing_highs": near_sh,
        "swing_lows": near_sl,
    }

    res = {
        "ok": True,
        "symbol": sym,
        "mark": mark,
        "change_pct": quote.get("change_pct", 0),
        "source": quote.get("source"),
        "levels": levels,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
    with _LEVELS_LOCK:
        _LEVELS_CACHE[sym] = (time.time(), res)
    return res


def format_levels_message(data: Dict[str, Any]) -> str:
    if not data.get("ok"):
        return (
            f"❌ **Levels unavailable** for `{data.get('symbol')}`\n"
            f"{data.get('error') or 'No data'}\n"
            f"Source: `{data.get('source')}`"
        )
    L = data.get("levels") or {}
    sym = data["symbol"]
    mark = data["mark"]

    def fmt(x):
        if x is None:
            return "—"
        if mark >= 1000:
            return f"${x:,.2f}"
        return f"${x:,.4f}"

    dist = lambda x: "" if x is None or not mark else f" ({(x - mark) / mark * 100:+.2f}%)"

    lines = [
        f"📐 **{sym} Key Levels** (live)",
        f"Mark: **{fmt(mark)}** | Δ `{data.get('change_pct', 0):+.2f}%`",
        f"Source: `{data.get('source')}`",
        "",
        "**Prior Day**",
        f"• PDH {fmt(L.get('pdh'))}{dist(L.get('pdh'))}",
        f"• PDL {fmt(L.get('pdl'))}{dist(L.get('pdl'))}",
        "",
        "**Classic Pivots**",
        f"• R2 {fmt(L.get('r2'))}{dist(L.get('r2'))}",
        f"• R1 {fmt(L.get('r1'))}{dist(L.get('r1'))}",
        f"• P  {fmt(L.get('pivot'))}{dist(L.get('pivot'))}",
        f"• S1 {fmt(L.get('s1'))}{dist(L.get('s1'))}",
        f"• S2 {fmt(L.get('s2'))}{dist(L.get('s2'))}",
        "",
        "**Session (24h proxy)**",
        f"• High {fmt(L.get('session_high'))}{dist(L.get('session_high'))}",
        f"• Low  {fmt(L.get('session_low'))}{dist(L.get('session_low'))}",
    ]
    if L.get("swing_highs") or L.get("swing_lows"):
        lines.append("")
        lines.append("**Recent Swings**")
        if L.get("swing_highs"):
            lines.append("• SH " + ", ".join(fmt(x) for x in L["swing_highs"]))
        if L.get("swing_lows"):
            lines.append("• SL " + ", ".join(fmt(x) for x in L["swing_lows"]))
    return "\n".join(lines)


if __name__ == "__main__":
    for s in ("BTCUSDT", "XAUUSD"):
        print("PRICE", s, fetch_spot_price(s))
        print(format_levels_message(compute_levels(s)))
        print("---")
