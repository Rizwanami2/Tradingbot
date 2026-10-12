#!/usr/bin/env python3
"""Reliable signal + paper autotrade pipeline (leverage, $100 micro size safe)."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _norm_symbol(sym: str) -> str:
    s = str(sym or "").strip().upper()
    aliases = {
        "BTC": "BTCUSDT", "BITCOIN": "BTCUSDT", "BTCUSD": "BTCUSDT", "XBT": "BTCUSDT",
        "ETH": "ETHUSDT", "ETHEREUM": "ETHUSDT", "ETHUSD": "ETHUSDT",
        "GOLD": "XAUUSD", "XAU": "XAUUSD", "XAUT": "XAUUSD", "XAUTUSD": "XAUUSD",
        "SOL": "SOLUSDT", "BNB": "BNBUSDT",
    }
    return aliases.get(s, s)


def run_signal(
    symbol: str,
    *,
    equity: float = 100.0,
    leverage: float = 20.0,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    import config
    from multi_agent_trading_bot import evaluate_symbol, Dir
    from risk_manager import RiskManager

    sym = _norm_symbol(symbol)
    src = (source or getattr(config, "DEFAULT_DATA_SOURCE", "ccxt")).lower()
    eq = float(equity if equity is not None else getattr(config, "INITIAL_EQUITY", 100.0))
    lev = float(leverage if leverage is not None else getattr(config, "DEFAULT_LEVERAGE", 20.0))

    risk = RiskManager(equity=eq, symbol=sym)
    try:
        risk.set_leverage(lev)
    except Exception:
        pass

    try:
        try:
            res = evaluate_symbol(
                symbol=sym, source=src, leverage=risk.get_leverage(),
                on_demand=True, equity=eq,
            )
        except TypeError:
            res = evaluate_symbol(
                symbol=sym, source=src, leverage=risk.get_leverage(), on_demand=True,
            )
    except Exception as e:
        return {
            "symbol": sym,
            "price": 0.0,
            "status": f"Eval error: {type(e).__name__}: {e}",
            "signal": None,
            "votes": [],
            "error": str(e),
        }

    sig = res.get("signal")
    if sig:
        try:
            if not sig.get("leverage"):
                sig["leverage"] = risk.get_leverage()
            lots = float(sig.get("lots") or 0)
            size = float(sig.get("size") or 0)
            if lots <= 0:
                plan = risk.position_size(
                    entry=float(sig["entry"]),
                    stop=float(sig["stop"]),
                    equity=eq,
                    symbol=sym,
                )
                if plan.lots > 0:
                    sig["lots"] = plan.lots
                    sig["size"] = max(plan.size, plan.lots)
                    sig["margin_required"] = plan.margin_required
                    sig["risk_amount"] = plan.risk_amount
                    sig["leverage"] = plan.leverage or lev
                    lots, size = float(sig["lots"]), float(sig["size"])
            if size <= 0 and lots > 0:
                sig["size"] = lots
            d = sig.get("direction")
            if isinstance(d, str):
                u = d.upper()
                sig["direction"] = Dir.LONG if "LONG" in u else (Dir.SHORT if "SHORT" in u else Dir.FLAT)
        except Exception as e:
            res["status"] = f"{res.get('status','')} | size-fix: {e}"
            res["signal"] = None
            sig = None

        if sig:
            try:
                entry = float(sig["entry"])
                stop = float(sig["stop"])
                tp1 = float(sig["tp1"])
                tp2 = float(sig["tp2"])
                dname = sig["direction"].name if hasattr(sig["direction"], "name") else str(sig["direction"])
                risk_dist = abs(entry - stop) or entry * 0.003
                if "LONG" in dname.upper():
                    if not (stop < entry):
                        sig["stop"] = entry - risk_dist
                    if tp1 <= entry:
                        sig["tp1"] = entry + risk_dist * 1.5
                        tp1 = sig["tp1"]
                    if tp2 < tp1:
                        sig["tp2"] = tp1 + risk_dist * 0.5
                elif "SHORT" in dname.upper():
                    if not (stop > entry):
                        sig["stop"] = entry + risk_dist
                    if tp1 >= entry:
                        sig["tp1"] = entry - risk_dist * 1.5
                        tp1 = sig["tp1"]
                    if tp2 > tp1:
                        sig["tp2"] = tp1 - risk_dist * 0.5
            except Exception:
                pass

    res["symbol"] = sym
    res["equity"] = eq
    res["leverage"] = risk.get_leverage()
    return res


def format_signal_message(res: Dict[str, Any]) -> str:
    sym = res.get("symbol", "?")
    price_val = float(res.get("price") or 0)
    votes = res.get("votes") or []
    sig = res.get("signal")
    status = res.get("status", "No consensus")
    lev = res.get("leverage", 20)

    vote_lines = []
    for v in votes:
        d = str(v.get("direction", "FLAT")).upper()
        icon = "🟢" if d == "LONG" else ("🔴" if d == "SHORT" else "⚪")
        reason = str(v.get("reason", ""))[:80].replace("`", "'")
        vote_lines.append(f"{icon} **{v.get('agent','?')}:** {d} ({reason})")

    if sig:
        dname = sig["direction"].name if hasattr(sig["direction"], "name") else str(sig["direction"])
        dir_icon = "🟢 LONG" if "LONG" in dname.upper() else "🔴 SHORT"
        try:
            entry = float(sig["entry"]); stop = float(sig["stop"])
            tp1 = float(sig["tp1"]); tp2 = float(sig["tp2"])
            lots = float(sig.get("lots") or 0)
            margin = float(sig.get("margin_required") or 0)
            rr = float(sig.get("rr") or 0)
            slev = float(sig.get("leverage") or lev)
        except Exception:
            entry = stop = tp1 = tp2 = lots = margin = rr = slev = 0.0
        sig_box = (
            f"\n🎯 **TRADE SETUP ({dir_icon}):**\n"
            f"• **Entry:** `${entry:,.2f}`\n"
            f"• **Stop Loss:** `${stop:,.2f}`\n"
            f"• **Target 1:** `${tp1:,.2f}` | **Target 2:** `${tp2:,.2f}`\n"
            f"• **Lots:** `{lots}` | **Lev:** `{slev:.0f}x`\n"
            f"• **Margin:** `${margin:,.2f}` | **R:R:** `1:{rr:.1f}`\n"
            f"• **Equity:** `${float(res.get('equity') or 0):,.2f}` (paper)\n"
        )
    else:
        sig_box = "\n🛡️ **Stay Flat.** Waiting for confluence.\n"

    votes_txt = "\n".join(vote_lines) if vote_lines else "_No votes_"
    return (
        f"📊 **Multi-Agent Evaluation for {sym}**\n"
        f"Mark: `${price_val:,.2f}` | Lev `{float(lev):.0f}x`\n\n"
        f"**Agent Votes:**\n{votes_txt}\n\n"
        f"⚡ **Consensus:** `{status}`"
        f"{sig_box}"
    )


def try_paper_execute(engine, sig: Dict[str, Any], candle_price: Optional[float] = None) -> Tuple[bool, str]:
    if not sig or not engine:
        return False, "No signal or engine"
    try:
        order = engine.register_setup(sig, reason=sig.get("reason", "Multi-Agent Consensus"))
    except Exception as e:
        return False, f"Register failed: {e}"
    px = float(candle_price if candle_price is not None else sig["entry"])
    try:
        events = engine.update_market_candle(
            order.symbol,
            {"open": px, "high": px * 1.0005, "low": px * 0.9995, "close": px},
        )
    except Exception as e:
        return True, f"Setup registered (fill pending): {e}"
    msgs = [ev.get("message") for ev in events if ev.get("message")]
    if order.symbol in getattr(engine, "active_positions", {}):
        return True, msgs[-1] if msgs else f"PAPER filled {order.symbol} {order.direction}"
    return True, msgs[-1] if msgs else f"Setup pending {order.symbol} @ {order.entry_level}"
