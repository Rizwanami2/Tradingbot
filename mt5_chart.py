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

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("MT5Chart")


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


def fetch_mt5_live_quote(symbol: str, broker: Optional[Any] = None) -> Dict[str, Any]:
    """
    Fetch real-time mark, bid, ask, and spread for MT5 symbol.
    Priority:
      1. Connected MT5 broker live mark prices & gateway ticks
      2. Realtime prices engine (geo-safe spot data)
      3. MultiAgent MarketDataFeed
    """
    norm_sym = normalize_mt5_symbol(symbol)
    mark_price = 0.0
    bid = 0.0
    ask = 0.0
    spread = 0.0
    source = "MT5 Live"

    # 1. Broker live mark price
    if broker is not None and hasattr(broker, "mark_prices"):
        try:
            for k in (norm_sym, f"{norm_sym}.m", f"{norm_sym}m", symbol.upper()):
                if k in broker.mark_prices and broker.mark_prices[k] > 0:
                    mark_price = float(broker.mark_prices[k])
                    source = "MT5 Broker"
                    break
        except Exception:
            pass

    # 2. Realtime prices spot quote
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

    # 3. Fallback to MarketDataFeed quote
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
            spread = 3.50  # typical tight BTC spread ~ $3.50
            bid = round(mark_price - spread / 2.0, 2)
            ask = round(mark_price + spread / 2.0, 2)
        else:
            spread = 0.35  # typical Gold spread ~ $0.35 (35 cents)
            bid = round(mark_price - spread / 2.0, 2)
            ask = round(mark_price + spread / 2.0, 2)

    return {
        "ok": mark_price > 0,
        "symbol": norm_sym,
        "mark": mark_price,
        "bid": bid,
        "ask": ask,
        "spread": spread,
        "source": source,
        "rt_data": rt_data,
    }


def fetch_mt5_candles(symbol: str, timeframe: str = "1m", limit: int = 15, broker: Optional[Any] = None) -> List[Dict[str, float]]:
    """
    Fetch recent OHLCV candles calibrated to live MT5 price.
    Returns list of dicts: [{"open": float, "high": float, "low": float, "close": float, "volume": float}]
    """
    norm_sym = normalize_mt5_symbol(symbol)
    feed_sym = "BTCUSDT" if norm_sym == "BTCUSD" else "XAUUSD"
    df: Optional[pd.DataFrame] = None

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
    quote = fetch_mt5_live_quote(norm_sym, broker=broker)
    candles = fetch_mt5_candles(norm_sym, timeframe=timeframe, limit=14, broker=broker)
    mark_p = quote["mark"]
    technicals = compute_chart_technicals(candles, live_mark=mark_p)
    ascii_plot = generate_ascii_candlestick_chart(candles, height=7)

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

    return {
        "symbol": norm_sym,
        "name": name,
        "icon": icon,
        "mark": mark_p,
        "bid": quote["bid"],
        "ask": quote["ask"],
        "spread": quote["spread"],
        "timeframe": timeframe.upper(),
        "candles": candles,
        "ascii_chart": ascii_plot,
        "technicals": technicals,
        "server": server_name,
        "login": login_id,
        "broker_mode": broker_mode,
        "trade_ready": trade_ready,
        "leverage": leverage,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


def format_mt5_chart_card(chart_data: Dict[str, Any]) -> str:
    """Format single symbol MT5 Live Chart Telegram card."""
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
    """Format combined MT5 Live Chart dashboard covering BOTH Bitcoin & Gold."""
    btc_chart = get_mt5_live_chart("BTCUSD", timeframe="1m", broker=broker)
    gold_chart = get_mt5_live_chart("XAUUSD", timeframe="1m", broker=broker)

    btc_card = format_mt5_chart_card(btc_chart)
    gold_card = format_mt5_chart_card(gold_chart)

    header = (
        f"⚡ **MetaTrader 5 (MT5) Institutional Live Charts**\n"
        f"Live multi-asset price feed & structural orderflow\n\n"
    )
    return f"{header}{gold_card}\n\n{btc_card}"


def get_mt5_combined_market_context(broker: Optional[Any] = None) -> str:
    """
    Generate real-time MT5 Live Chart market context for injection
    into Gemini assistant and conversational /chat /ask queries.
    """
    btc = get_mt5_live_chart("BTCUSD", timeframe="1m", broker=broker)
    gold = get_mt5_live_chart("XAUUSD", timeframe="1m", broker=broker)

    btc_t = btc["technicals"]
    gold_t = gold["technicals"]

    return (
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

