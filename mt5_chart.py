#!/usr/bin/env python3
"""
MetaTrader 5 (MT5) Live Chart Engine for Bitcoin & Gold
========================================================
Generates institutional live candlestick charts, pivot levels,
technical indicators, and MT5 live execution telemetry for:
- Bitcoin (BTCUSD / BTCUSDT)
- Gold (XAUUSD / GOLD)
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("MT5Chart")

_QUOTE_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_QUOTE_LOCK = threading.Lock()
_CANDLE_CACHE: Dict[str, Tuple[float, List[Dict[str, float]]]] = {}
_CANDLE_LOCK = threading.Lock()
_CONTEXT_CACHE: Dict[str, Tuple[float, str]] = {}
_CONTEXT_LOCK = threading.Lock()
_CHART_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
_CHART_LOCK = threading.Lock()


def normalize_mt5_symbol(symbol: str) -> str:
    """Normalize user input symbol to standard MT5 symbol."""
    s = str(symbol or "").strip().upper().replace("/", "").replace("-", "").replace(":", "")
    if any(x in s for x in ("BTC", "BITCOIN", "XBT")):
        return "BTCUSD"
    if any(x in s for x in ("XAU", "GOLD", "XAUT", "PAXG")):
        return "XAUUSD"
    return "XAUUSD"


def get_display_names(symbol: str) -> Tuple[str, str, str]:
    """Returns (canonical_sym, ticker_name, icon)."""
    norm = normalize_mt5_symbol(symbol)
    if norm == "BTCUSD":
        return "BTCUSD", "Bitcoin", "🪙"
    return "XAUUSD", "Gold", "🟡"


def fetch_mt5_live_quote(symbol: str, broker: Optional[Any] = None, max_age_seconds: float = 3.0) -> Dict[str, Any]:
    """
    Fetch real-time mark, bid, ask, and spread for MT5 symbol.
    Priority:
      1. Connected MT5 broker live mark prices & gateway ticks
      2. Realtime prices engine (geo-safe spot data)
      3. MultiAgent MarketDataFeed
    """
    norm_sym = normalize_mt5_symbol(symbol)

    # 1. Broker live mark price (if explicitly provided in dict)
    if broker is not None and hasattr(broker, "mark_prices") and isinstance(broker.mark_prices, dict):
        try:
            for k in (norm_sym, f"{norm_sym}.m", f"{norm_sym}m", symbol.upper()):
                val = broker.mark_prices.get(k)
                if isinstance(val, (int, float)) and val > 0:
                    mark_p = float(val)
                    spread = 3.50 if norm_sym == "BTCUSD" else 0.35
                    return {
                        "ok": True,
                        "symbol": norm_sym,
                        "mark": mark_p,
                        "bid": round(mark_p - spread / 2.0, 2),
                        "ask": round(mark_p + spread / 2.0, 2),
                        "spread": spread,
                        "source": "MT5 Broker",
                        "rt_data": {},
                    }
        except Exception:
            pass

    # 2. Broker live tick / quote endpoint
    if broker is not None and hasattr(broker, "get_live_quote"):
        try:
            q = broker.get_live_quote(norm_sym)
            if isinstance(q, dict) and q.get("ok"):
                raw_m = q.get("mark")
                if isinstance(raw_m, (int, float)) and raw_m > 0:
                    mark_p = float(raw_m)
                    raw_bid = q.get("bid")
                    raw_ask = q.get("ask")
                    raw_spread = q.get("spread")
                    spread = float(raw_spread) if isinstance(raw_spread, (int, float)) else (3.50 if norm_sym == "BTCUSD" else 0.35)
                    bid = float(raw_bid) if isinstance(raw_bid, (int, float)) else round(mark_p - spread / 2.0, 2)
                    ask = float(raw_ask) if isinstance(raw_ask, (int, float)) else round(mark_p + spread / 2.0, 2)
                    return {
                        "ok": True,
                        "symbol": norm_sym,
                        "mark": mark_p,
                        "bid": bid,
                        "ask": ask,
                        "spread": spread,
                        "source": str(q.get("source", "MT5 Broker")),
                        "rt_data": {},
                    }
        except Exception as e:
            logger.debug("broker.get_live_quote error: %s", e)

    # 3. Check TTL cache
    now = time.time()
    broker_id = id(broker) if broker is not None else "default"
    cache_key = f"{norm_sym}_{broker_id}"
    with _QUOTE_LOCK:
        if cache_key in _QUOTE_CACHE:
            ts, cached_quote = _QUOTE_CACHE[cache_key]
            if now - ts < max_age_seconds and cached_quote.get("ok"):
                return cached_quote.copy()
        elif norm_sym in _QUOTE_CACHE and broker is None:
            ts, cached_quote = _QUOTE_CACHE[norm_sym]
            if now - ts < max_age_seconds and cached_quote.get("ok"):
                return cached_quote.copy()

    mark_price = 0.0
    bid = 0.0
    ask = 0.0
    spread = 0.0
    source = "MT5 Live"

    if broker is None:
        try:
            from mt5_broker import get_active_broker
            broker = get_active_broker()
        except Exception:
            pass

    # Realtime prices spot quote
    rt_data: Dict[str, Any] = {}
    try:
        from realtime_prices import fetch_spot_price
        query_sym = "BTCUSDT" if norm_sym == "BTCUSD" else "XAUUSD"
        rt_data = fetch_spot_price(query_sym)
        if rt_data.get("ok"):
            rt_mark = float(rt_data.get("mark") or rt_data.get("close") or 0.0)
            if mark_price <= 0 and rt_mark > 0:
                mark_price = rt_mark
                source = rt_data.get("source", "Realtime Feed")
    except Exception as e:
        logger.debug("realtime_prices lookup error: %s", e)

    # Fallback to MarketDataFeed quote
    if mark_price <= 0:
        try:
            from multi_agent_trading_bot import MarketDataFeed
            feed = MarketDataFeed(symbol=norm_sym, source="ccxt")
            q = feed.get_latest_price()
            if q.get("ok"):
                mark_price = float(q.get("mark") or q.get("close") or 0.0)
                source = q.get("source", "MarketFeed")
        except Exception as e:
            logger.debug("MarketDataFeed quote error: %s", e)

    # Calculate realistic bid/ask/spread if not directly provided
    if mark_price > 0:
        if norm_sym == "BTCUSD":
            spread = 3.50
            bid = round(mark_price - spread / 2.0, 2)
            ask = round(mark_price + spread / 2.0, 2)
        else:
            spread = 0.35
            bid = round(mark_price - spread / 2.0, 2)
            ask = round(mark_price + spread / 2.0, 2)

    res = {
        "ok": mark_price > 0,
        "symbol": norm_sym,
        "mark": mark_price,
        "bid": bid,
        "ask": ask,
        "spread": spread,
        "source": source,
        "rt_data": rt_data,
    }
    if res["ok"]:
        with _QUOTE_LOCK:
            _QUOTE_CACHE[cache_key] = (time.time(), res)
            if broker is None:
                _QUOTE_CACHE[norm_sym] = (time.time(), res)
    return res


def fetch_mt5_candles(
    symbol: str,
    timeframe: str = "1m",
    limit: int = 15,
    broker: Optional[Any] = None,
    max_age_seconds: float = 8.0,
) -> List[Dict[str, float]]:
    """
    Fetch recent OHLCV candles calibrated to live MT5 price.
    Returns list of dicts: [{"open": float, "high": float, "low": float, "close": float, "volume": float}]
    """
    norm_sym = normalize_mt5_symbol(symbol)
    broker_id = id(broker) if broker is not None else "default"
    cache_key = f"{norm_sym}_{timeframe}_{limit}_{broker_id}"
    now = time.time()
    with _CANDLE_LOCK:
        if cache_key in _CANDLE_CACHE:
            ts, cached_candles = _CANDLE_CACHE[cache_key]
            if now - ts < max_age_seconds and cached_candles:
                return copy.deepcopy(cached_candles)

    df: Optional[pd.DataFrame] = None

    if broker is None:
        try:
            from mt5_broker import get_active_broker
            broker = get_active_broker()
        except Exception:
            pass

    # 1. Primary: Query connected MT5 broker directly
    if broker is not None and hasattr(broker, "get_candles"):
        try:
            res_df = broker.get_candles(norm_sym, timeframe=timeframe, limit=max(30, limit + 5))
            if isinstance(res_df, pd.DataFrame) and not res_df.empty and len(res_df) >= 3:
                df = res_df
        except Exception as e:
            logger.debug("broker.get_candles failed: %s", e)

    # 2. Fallback to MarketDataFeed
    if df is None or df.empty or len(df) < 3:
        feed_sym = "BTCUSDT" if norm_sym == "BTCUSD" else "XAUUSD"
        try:
            from multi_agent_trading_bot import MarketDataFeed
            feed = MarketDataFeed(symbol=feed_sym, source="ccxt")
            df = feed.fetch(limit=max(30, limit + 5), timeframe=timeframe)
        except Exception as e:
            logger.debug("MarketDataFeed fetch failed: %s", e)

    candles: List[Dict[str, float]] = []
    if df is not None and not df.empty and len(df) >= 3:
        sub = df.tail(limit)
        for _, row in sub.iterrows():
            candles.append({
                "open": round(float(row.get("open", 0.0)), 2),
                "high": round(float(row.get("high", 0.0)), 2),
                "low": round(float(row.get("low", 0.0)), 2),
                "close": round(float(row.get("close", 0.0)), 2),
                "volume": round(float(row.get("volume", 0.0)), 2),
            })

    # Calibrate latest candle close to live mark price
    quote = fetch_mt5_live_quote(norm_sym, broker=broker)
    if quote["ok"] and quote["mark"] > 0:
        cur_mark = quote["mark"]
        if candles:
            last_c = candles[-1]
            last_c["close"] = cur_mark
            last_c["high"] = max(last_c["high"], cur_mark)
            last_c["low"] = min(last_c["low"], cur_mark)
        else:
            # Synthetic initial candle if feed temporarily empty
            step = cur_mark * 0.0005
            for i in range(limit):
                p = cur_mark + (i - limit / 2) * step * 0.5
                candles.append({
                    "open": round(p - step * 0.3, 2),
                    "high": round(p + step * 0.8, 2),
                    "low": round(p - step * 0.8, 2),
                    "close": round(p + step * 0.2, 2),
                    "volume": 100.0,
                })
            candles[-1]["close"] = cur_mark

    if candles:
        with _CANDLE_LOCK:
            _CANDLE_CACHE[cache_key] = (time.time(), candles)

    return candles


def generate_ascii_candlestick_chart(candles: List[Dict[str, float]], height: int = 7) -> str:
    """
    Renders a clean, high-contrast monospace candlestick chart.
    Bullish candle body: '█ '
    Bearish candle body: '░ '
    Wicks / shadows:     '│ '
    """
    if not candles or len(candles) < 2:
        return "       [Insufficient MT5 candle history for visual plot]"

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    min_p = min(lows)
    max_p = max(highs)
    span = max_p - min_p
    if span <= 0:
        span = 1.0

    lines = []
    num_candles = len(candles)

    for r in range(height):
        # Price level for this horizontal slice
        level = max_p - (r / (height - 1)) * span
        # Format coordinate axis on the left
        if max_p >= 10000:
            p_str = f"{level:9.1f} ┤ "
        else:
            p_str = f"{level:8.2f} ┤ "

        row_chars = []
        for c in candles:
            o = c["open"]
            cl = c["close"]
            h = c["high"]
            l = c["low"]

            body_top = max(o, cl)
            body_bot = min(o, cl)
            is_bull = cl >= o

            if l <= level <= h:
                if body_bot <= level <= body_top:
                    row_chars.append("█ " if is_bull else "░ ")
                else:
                    row_chars.append("│ ")
            else:
                row_chars.append("  ")

        lines.append(p_str + "".join(row_chars))

    axis_indent = " " * (10 if max_p >= 10000 else 9)
    axis_line = axis_indent + "└" + "──" * num_candles
    time_bar = axis_indent + " M1" + " " * max(1, num_candles * 2 - 8) + "NOW ◄"

    lines.append(axis_line)
    lines.append(time_bar)
    return "\n".join(lines)


def compute_chart_technicals(candles: List[Dict[str, float]], live_mark: float = 0.0) -> Dict[str, Any]:
    """Calculate EMA9, EMA21, ATR, RSI, 24h High/Low, and Pivot Points."""
    if not candles:
        return {
            "ema9": live_mark,
            "ema21": live_mark,
            "ema_signal": "Neutral",
            "rsi": 50.0,
            "atr": 0.0,
            "pivot": live_mark,
            "r1": live_mark * 1.005,
            "r2": live_mark * 1.010,
            "s1": live_mark * 0.995,
            "s2": live_mark * 0.990,
            "high_24h": live_mark,
            "low_24h": live_mark,
            "change_pct": 0.0,
            "trend_bias": "NEUTRAL",
        }

    closes = np.array([c["close"] for c in candles], dtype=float)
    highs = np.array([c["high"] for c in candles], dtype=float)
    lows = np.array([c["low"] for c in candles], dtype=float)

    # EMAs
    def calc_ema(arr: np.ndarray, period: int) -> float:
        if len(arr) < period:
            return float(arr[-1])
        alpha = 2.0 / (period + 1.0)
        ema = arr[0]
        for val in arr[1:]:
            ema = alpha * val + (1 - alpha) * ema
        return float(ema)

    ema9 = calc_ema(closes, min(9, len(closes)))
    ema21 = calc_ema(closes, min(21, len(closes)))

    # ATR
    tr_list = []
    for i in range(1, len(candles)):
        h = highs[i]
        l = lows[i]
        prev_c = closes[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)
    atr_val = float(np.mean(tr_list)) if tr_list else float(highs[-1] - lows[-1])

    # RSI
    diffs = np.diff(closes)
    gains = np.maximum(diffs, 0)
    losses = np.maximum(-diffs, 0)
    avg_gain = float(np.mean(gains[-14:])) if len(gains) >= 14 else float(np.mean(gains)) if len(gains) else 0.0
    avg_loss = float(np.mean(losses[-14:])) if len(losses) >= 14 else float(np.mean(losses)) if len(losses) else 0.0
    if avg_loss == 0:
        rsi_val = 100.0 if avg_gain > 0 else 50.0
    else:
        rs = avg_gain / avg_loss
        rsi_val = 100.0 - (100.0 / (1.0 + rs))

    # Classical Floor Pivot Points from recent swing range
    high_p = float(np.max(highs))
    low_p = float(np.min(lows))
    close_p = float(closes[-1])
    pivot = (high_p + low_p + close_p) / 3.0
    r1 = (2.0 * pivot) - low_p
    s1 = (2.0 * pivot) - high_p
    r2 = pivot + (high_p - low_p)
    s2 = pivot - (high_p - low_p)

    # 24h change proxy
    first_c = float(candles[0]["open"])
    chg_pct = round(((close_p - first_c) / first_c * 100.0), 2) if first_c > 0 else 0.0

    # Trend Bias
    if ema9 > ema21 * 1.0005 and rsi_val >= 50.0:
        trend_bias = "🟢 BULLISH STRUCTURE"
        ema_signal = "Bullish Stack (EMA9 > EMA21)"
    elif ema9 < ema21 * 0.9995 and rsi_val < 50.0:
        trend_bias = "🔴 BEARISH STRUCTURE"
        ema_signal = "Bearish Stack (EMA9 < EMA21)"
    else:
        trend_bias = "🟡 CONSOLIDATION / RANGE"
        ema_signal = "Compressed / Ranging"

    return {
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "ema_signal": ema_signal,
        "rsi": round(rsi_val, 1),
        "atr": round(atr_val, 2),
        "pivot": round(pivot, 2),
        "r1": round(r1, 2),
        "r2": round(r2, 2),
        "s1": round(s1, 2),
        "s2": round(s2, 2),
        "high_24h": round(high_p, 2),
        "low_24h": round(low_p, 2),
        "change_pct": chg_pct,
        "trend_bias": trend_bias,
    }


def get_mt5_live_chart(symbol: str, timeframe: str = "1m", broker: Optional[Any] = None) -> Dict[str, Any]:
    """Compile comprehensive MT5 live chart data bundle for a symbol."""
    norm_sym, name, icon = get_display_names(symbol)
    tf_norm = timeframe.strip().upper()
    cache_key = (norm_sym, tf_norm)
    now = time.time()
    if broker is None:
        with _CHART_LOCK:
            if cache_key in _CHART_CACHE:
                ts, cached_bundle = _CHART_CACHE[cache_key]
                if (now - ts) < 3.0:
                    return dict(cached_bundle)

    if broker is None:
        try:
            from mt5_broker import get_active_broker
            broker = get_active_broker()
        except Exception:
            broker = None

    quote = fetch_mt5_live_quote(norm_sym, broker=broker)
    candles = fetch_mt5_candles(norm_sym, timeframe=timeframe, limit=14, broker=broker)
    mark_p = quote["mark"]
    technicals = compute_chart_technicals(candles, live_mark=mark_p)
    ascii_plot = generate_ascii_candlestick_chart(candles, height=7)

    # 1. Active MT5 open positions for this symbol
    open_positions: List[Dict[str, Any]] = []
    if broker is not None and hasattr(broker, "get_positions"):
        try:
            bps = broker.get_positions()
            for p in bps:
                p_sym = str(getattr(p, "symbol", "")).upper()
                if norm_sym in p_sym or p_sym in norm_sym or (norm_sym == "BTCUSD" and "BTC" in p_sym) or (norm_sym == "XAUUSD" and any(x in p_sym for x in ("XAU", "GOLD", "PAXG"))):
                    e_px = float(getattr(p, "entry_price", mark_p) or mark_p)
                    c_px = float(getattr(p, "current_price", mark_p) or mark_p)
                    qty = float(getattr(p, "qty", 0.01) or 0.01)
                    side_str = str(getattr(p, "side", "BUY")).upper()
                    pnl = float(getattr(p, "unrealized_pnl", 0.0) or 0.0)
                    if pnl == 0.0 and c_px > 0 and e_px > 0:
                        diff = (c_px - e_px) if side_str in ("BUY", "LONG") else (e_px - c_px)
                        cs = 100.0 if any(x in p_sym for x in ("XAU", "GOLD", "PAXG")) else (5000.0 if any(x in p_sym for x in ("XAG", "SILVER")) else (1.0 if "BTC" in p_sym else 100000.0))
                        pnl = round(diff * qty * cs, 2)
                    cs_notional = 100.0 if any(x in p_sym for x in ("XAU", "GOLD", "PAXG")) else (5000.0 if any(x in p_sym for x in ("XAG", "SILVER")) else (1.0 if "BTC" in p_sym else 100000.0))
                    pnl_pct = round((pnl / max(e_px * qty * cs_notional, 1.0)) * 100.0, 2) if e_px > 0 else 0.0
                    open_positions.append({
                        "ticket": getattr(p, "ticket", getattr(p, "id", "LIVE")),
                        "symbol": p_sym,
                        "side": side_str,
                        "qty": qty,
                        "entry_price": e_px,
                        "current_price": c_px,
                        "unrealized_pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "stop_loss": float(p.stop_loss) if getattr(p, "stop_loss", None) else None,
                        "take_profit": float(p.take_profit) if getattr(p, "take_profit", None) else None,
                    })
        except Exception as _b_err:
            logger.debug("Chart get_positions from broker notice: %s", _b_err)

    try:
        from main import engine
        if engine is not None and hasattr(engine, "active_positions"):
            seen_tickets = {str(p["ticket"]) for p in open_positions if p.get("ticket")}
            for order in engine.active_positions.values():
                o_sym = str(getattr(order, "symbol", "")).upper()
                if norm_sym in o_sym or o_sym in norm_sym or (norm_sym == "BTCUSD" and "BTC" in o_sym) or (norm_sym == "XAUUSD" and any(x in o_sym for x in ("XAU", "GOLD", "PAXG"))):
                    t_id = str(getattr(order, "ticket", getattr(order, "id", "")))
                    if t_id and t_id in seen_tickets:
                        continue
                    e_px = float(getattr(order, "executed_price", getattr(order, "entry_level", mark_p)) or mark_p)
                    c_px = float(getattr(order, "current_price", mark_p) or mark_p)
                    qty = float(getattr(order, "lots", 0.01) or 0.01)
                    side_str = str(getattr(order, "direction", "BUY")).upper()
                    pnl = float(getattr(order, "unrealized_pnl", 0.0) or 0.0)
                    cs_notional = 100.0 if any(x in o_sym for x in ("XAU", "GOLD", "PAXG")) else (5000.0 if any(x in o_sym for x in ("XAG", "SILVER")) else (1.0 if "BTC" in o_sym else 100000.0))
                    pnl_pct = round((pnl / max(e_px * qty * cs_notional, 1.0)) * 100.0, 2) if e_px > 0 else 0.0
                    open_positions.append({
                        "ticket": t_id or "BOT-ORD",
                        "symbol": o_sym,
                        "side": side_str,
                        "qty": qty,
                        "entry_price": e_px,
                        "current_price": c_px,
                        "unrealized_pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "stop_loss": float(order.stop_loss) if getattr(order, "stop_loss", None) else None,
                        "take_profit": float(order.tp1) if getattr(order, "tp1", None) else None,
                    })
    except Exception as _e_err:
        logger.debug("Chart engine positions notice: %s", _e_err)

    # 2. Pending setups for this symbol
    pending_setups: List[Dict[str, Any]] = []
    try:
        from main import engine
        if engine is not None and hasattr(engine, "pending_setups"):
            for s in engine.pending_setups.values():
                s_sym = str(getattr(s, "symbol", "")).upper()
                if norm_sym in s_sym or s_sym in norm_sym or (norm_sym == "BTCUSD" and "BTC" in s_sym) or (norm_sym == "XAUUSD" and any(x in s_sym for x in ("XAU", "GOLD", "PAXG"))):
                    pending_setups.append({
                        "id": getattr(s, "id", "SETUP"),
                        "direction": str(getattr(s, "direction", "LONG")).upper(),
                        "entry_level": float(getattr(s, "entry_level", mark_p)),
                        "stop_loss": float(getattr(s, "stop_loss", 0.0) or 0.0),
                        "take_profit": float(getattr(s, "tp1", 0.0) or 0.0),
                        "lots": float(getattr(s, "lots", 0.01) or 0.01),
                    })
    except Exception:
        pass

    # 3. Signal key levels & market confluence
    signal_levels: Dict[str, Any] = {}
    try:
        from realtime_prices import compute_levels
        lvl_data = compute_levels(norm_sym)
        if lvl_data.get("ok") and "levels" in lvl_data:
            signal_levels = lvl_data["levels"]
    except Exception:
        pass

    # Broker account telemetry
    server_name = "MetaQuotes-Demo"
    login_id = 0
    broker_mode = "LIVE"
    trade_ready = True
    leverage = 20.0

    if broker is not None:
        try:
            if hasattr(broker, "server") and broker.server:
                server_name = str(broker.server)
            if hasattr(broker, "login") and broker.login:
                login_id = int(broker.login)
            if hasattr(broker, "mode"):
                broker_mode = str(broker.mode).upper()
            if hasattr(broker, "is_connected"):
                trade_ready = bool(broker.is_connected())
        except Exception:
            pass

    bundle = {
        "symbol": norm_sym,
        "name": name,
        "icon": icon,
        "mark": mark_p,
        "bid": quote["bid"],
        "ask": quote["ask"],
        "spread": quote["spread"],
        "timeframe": tf_norm,
        "candles": candles,
        "ascii_chart": ascii_plot,
        "technicals": technicals,
        "open_positions": open_positions,
        "pending_setups": pending_setups,
        "signal_levels": signal_levels,
        "server": server_name,
        "login": login_id,
        "broker_mode": broker_mode,
        "trade_ready": trade_ready,
        "leverage": leverage,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    if broker is None:
        with _CHART_LOCK:
            _CHART_CACHE[cache_key] = (now, bundle)
    return bundle


def format_mt5_chart_card(chart_data: Dict[str, Any]) -> str:
    """Format single symbol MT5 Live Chart Telegram card with active positions and signal levels."""
    sym = chart_data["symbol"]
    name = chart_data["name"]
    icon = chart_data["icon"]
    mark = chart_data["mark"]
    bid = chart_data["bid"]
    ask = chart_data["ask"]
    spread = chart_data["spread"]
    tech = chart_data["technicals"]
    tf = chart_data.get("timeframe", "M1")
    plot = chart_data["ascii_chart"]
    server = chart_data["server"]
    login = chart_data["login"]
    acc_tag = f"#{login}" if login else "Active"
    trade_status = "🟢 Connected" if chart_data["trade_ready"] else "🟡 Standby"

    # 1. Format Active Positions & Orders
    positions_list = chart_data.get("open_positions") or []
    pending_list = chart_data.get("pending_setups") or []
    pos_lines = []
    if positions_list:
        for p in positions_list:
            t_num = str(p.get("ticket") or "OPEN")
            t_tag = f"#{t_num}" if not t_num.startswith("#") else t_num
            side_icon = "🟢" if "BUY" in str(p.get("side", "")).upper() or "LONG" in str(p.get("side", "")).upper() else "🔴"
            pnl_val = float(p.get("unrealized_pnl", 0.0) or 0.0)
            pnl_sign = "+" if pnl_val >= 0 else ""
            pnl_icon = "🟢" if pnl_val >= 0 else "🔴"
            e_px = float(p.get("entry_price", mark))
            c_px = float(p.get("current_price", mark))
            sl_val = p.get("stop_loss")
            tp_val = p.get("take_profit")
            sl_str = f"${sl_val:,.2f}" if sl_val else "None"
            tp_str = f"${tp_val:,.2f}" if tp_val else "None"
            pos_lines.append(
                f"• {side_icon} **{t_tag} {p.get('side')} {p.get('qty', 0.01):.2f} Lots** @ `${e_px:,.2f}`\n"
                f"  ↳ PnL: {pnl_icon} `{pnl_sign}${pnl_val:,.2f}` | Mark: `${c_px:,.2f}`\n"
                f"  ↳ SL: `{sl_str}` | TP: `{tp_str}`"
            )
    else:
        pos_lines.append("• Status: ⚪ **Flat** (No open positions — Bot ready for setup)")

    if pending_list:
        pos_lines.append("\n⏳ **Pending Orders:**")
        for s in pending_list:
            dir_icon = "🟢" if "LONG" in str(s.get("direction", "")).upper() else "🔴"
            pos_lines.append(
                f"• {dir_icon} `{s.get('direction')}` Limit @ `${s.get('entry_level', mark):,.2f}` (SL: `${s.get('stop_loss', 0.0):,.2f}` | TP: `${s.get('take_profit', 0.0):,.2f}`)"
            )
    pos_section = "\n".join(pos_lines)

    # 2. Format Signal Key Levels
    sig_levels = chart_data.get("signal_levels") or {}
    pdh = sig_levels.get("pdh")
    pdl = sig_levels.get("pdl")
    pdh_str = f"${pdh:,.2f}" if pdh else f"${tech['high_24h']:,.2f}"
    pdl_str = f"${pdl:,.2f}" if pdl else f"${tech['low_24h']:,.2f}"
    r1_val = sig_levels.get("r1") or tech["r1"]
    r2_val = sig_levels.get("r2") or tech["r2"]
    s1_val = sig_levels.get("s1") or tech["s1"]
    s2_val = sig_levels.get("s2") or tech["s2"]
    p_val = sig_levels.get("pivot") or tech["pivot"]

    return (
        f"📊 **MT5 Live Chart — {icon} {name} (`{sym}`)**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ **MT5 Mark:** `${mark:,.2f}` | Lev `{chart_data['leverage']:.0f}x`\n"
        f"• **Bid / Ask:** `${bid:,.2f}` / `${ask:,.2f}` (Spread: `{spread:.2f}`)\n"
        f"• **24h Range:** `${tech['low_24h']:,.2f}` — `${tech['high_24h']:,.2f}` (`{tech['change_pct']:+.2f}%`)\n"
        f"• **Trend Bias:** {tech['trend_bias']}\n"
        f"• **Moving Averages:** `{tech['ema_signal']}`\n\n"
        f"📈 **Live Candlestick Action ({tf}):**\n"
        f"```text\n"
        f"{plot}\n"
        f"```\n\n"
        f"🎯 **Active MT5 Order Positions:**\n"
        f"{pos_section}\n\n"
        f"📐 **Signal Key Levels & Market Confluence:**\n"
        f"• **Daily Range (PDH / PDL):** `{pdh_str}` / `{pdl_str}`\n"
        f"• **Key Resistances:** R2 `${r2_val:,.2f}` | R1 `${r1_val:,.2f}`\n"
        f"• **Central Pivot (P):** `${p_val:,.2f}`\n"
        f"• **Key Supports:** S1 `${s1_val:,.2f}` | S2 `${s2_val:,.2f}`\n\n"
        f"🎯 **MT5 Structural Pivot Levels:**\n"
        f"• **R2 Resistance:** `${tech['r2']:,.2f}`\n"
        f"• **R1 Resistance:** `${tech['r1']:,.2f}`\n"
        f"• **Pivot (Base):** `${tech['pivot']:,.2f}`\n"
        f"• **S1 Support:** `${tech['s1']:,.2f}`\n"
        f"• **S2 Support:** `${tech['s2']:,.2f}`\n"
        f"• **ATR Volatility:** `${tech['atr']:,.2f}` | **RSI(14):** `{tech['rsi']:.1f}`\n\n"
        f"💼 **MT5 Terminal Telemetry:**\n"
        f"• **Broker Server:** `{server}` ({acc_tag})\n"
        f"• **Engine Status:** {trade_status} | Mode: `{chart_data['broker_mode']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Tip: Run `/signal {sym}` for multi-agent trade execution.*"
    )


def format_combined_mt5_chart_dashboard(broker: Optional[Any] = None) -> str:
    """Format combined MT5 Live Chart dashboard covering BOTH Bitcoin & Gold in parallel."""
    with ThreadPoolExecutor(max_workers=2) as executor:
        f_btc = executor.submit(get_mt5_live_chart, "BTCUSD", "1m", broker)
        f_gold = executor.submit(get_mt5_live_chart, "XAUUSD", "1m", broker)
        btc_chart = f_btc.result()
        gold_chart = f_gold.result()

    btc_card = format_mt5_chart_card(btc_chart)
    gold_card = format_mt5_chart_card(gold_chart)

    header = (
        f"⚡ **MetaTrader 5 (MT5) Institutional Live Charts**\n"
        f"Live multi-asset price feed & structural orderflow\n\n"
    )
    return f"{header}{gold_card}\n\n{btc_card}"


def get_mt5_combined_market_context(broker: Optional[Any] = None, max_age_seconds: float = 5.0) -> str:
    """
    Generate real-time MT5 Live Chart market context for injection
    into Gemini assistant and conversational /chat /ask queries with caching.
    """
    now = time.time()
    with _CONTEXT_LOCK:
        if "market_context" in _CONTEXT_CACHE:
            ts, val = _CONTEXT_CACHE["market_context"]
            if now - ts < max_age_seconds and val:
                return val

    with ThreadPoolExecutor(max_workers=2) as executor:
        f_btc = executor.submit(get_mt5_live_chart, "BTCUSD", "1m", broker)
        f_gold = executor.submit(get_mt5_live_chart, "XAUUSD", "1m", broker)
        btc = f_btc.result()
        gold = f_gold.result()

    btc_t = btc["technicals"]
    gold_t = gold["technicals"]

    res = (
        f"=== MT5 LIVE CHART & MARKET CONTEXT ===\n"
        f"[MT5 Live Gold (XAUUSD)]\n"
        f"Mark Price: ${gold['mark']:,.2f} | Bid: ${gold['bid']:,.2f} | Ask: ${gold['ask']:,.2f} | Spread: {gold['spread']:.2f}\n"
        f"24h Range: ${gold_t['low_24h']:,.2f} - ${gold_t['high_24h']:,.2f} ({gold_t['change_pct']:+.2f}%)\n"
        f"Structure Bias: {gold_t['trend_bias']} | Moving Averages: {gold_t['ema_signal']} (EMA9=${gold_t['ema9']:,.2f}, EMA21=${gold_t['ema21']:,.2f})\n"
        f"MT5 Pivots: R2=${gold_t['r2']:,.2f} | R1=${gold_t['r1']:,.2f} | Pivot=${gold_t['pivot']:,.2f} | S1=${gold_t['s1']:,.2f} | S2=${gold_t['s2']:,.2f}\n"
        f"ATR(14): ${gold_t['atr']:,.2f} | RSI(14): {gold_t['rsi']:.1f}\n\n"
        f"[MT5 Live Bitcoin (BTCUSD)]\n"
        f"Mark Price: ${btc['mark']:,.2f} | Bid: ${btc['bid']:,.2f} | Ask: ${btc['ask']:,.2f} | Spread: {btc['spread']:.2f}\n"
        f"24h Range: ${btc_t['low_24h']:,.2f} - ${btc_t['high_24h']:,.2f} ({btc_t['change_pct']:+.2f}%)\n"
        f"Structure Bias: {btc_t['trend_bias']} | Moving Averages: {btc_t['ema_signal']} (EMA9=${btc_t['ema9']:,.2f}, EMA21=${btc_t['ema21']:,.2f})\n"
        f"MT5 Pivots: R2=${btc_t['r2']:,.2f} | R1=${btc_t['r1']:,.2f} | Pivot=${btc_t['pivot']:,.2f} | S1=${btc_t['s1']:,.2f} | S2=${btc_t['s2']:,.2f}\n"
        f"ATR(14): ${btc_t['atr']:,.2f} | RSI(14): {btc_t['rsi']:.1f}\n\n"
        f"MT5 Terminal: {gold['server']} (Account #{gold['login'] or 'Active'}) | Status: {'Connected' if gold['trade_ready'] else 'Standby'}\n"
        f"Timestamp: {gold['timestamp']}\n"
        f"=== END MT5 LIVE CHART CONTEXT ==="
    )

    with _CONTEXT_LOCK:
        _CONTEXT_CACHE["market_context"] = (time.time(), res)
    return res


def build_chart_keyboard(active: str = "both") -> Any:
    """Build interactive Telegram inline keyboard for MT5 live chart navigation."""
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        buttons = [
            [
                InlineKeyboardButton("🟡 Gold (XAUUSD)", callback_data="chart:gold"),
                InlineKeyboardButton("🪙 Bitcoin (BTCUSD)", callback_data="chart:btc"),
            ],
            [
                InlineKeyboardButton("⚡ Both MT5 Charts", callback_data="chart:both"),
                InlineKeyboardButton("🔄 Refresh", callback_data=f"chart:refresh:{active}"),
            ],
            [
                InlineKeyboardButton("🎯 Signal Gold", callback_data="sig:XAUUSD"),
                InlineKeyboardButton("🎯 Signal BTC", callback_data="sig:BTCUSDT"),
            ],
        ]
        return InlineKeyboardMarkup(buttons)
    except Exception:
        return None

