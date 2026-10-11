"""
COMPLETE Advanced Integration Layer
====================================
Wires every advanced module into a single ready-to-use facade.

Usage from main.py / live loop:
    from advanced_integration import AdvancedEngine
    eng = AdvancedEngine(equity=10000, symbol="XAUUSD", profile="balanced")
    result = eng.evaluate()
    eng.performance_report()  # for /performance
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

import config
from risk_manager import RiskManager
from multi_agent_trading_bot import (
    Arbiter,
    Dir,
    MarketDataFeed,
    Vote,
    evaluate_symbol as _base_evaluate,
)

# Advanced modules
from volatility_sizing import adjust_risk_pct, volatility_risk_multiplier
from instrument_limits import clamp_leverage, get_max_leverage, get_max_risk_pct
from structure_trailing import structure_trail_stop
from htf_bias import allows_direction, htf_bias, Bias
from strategy_profiles import get_profile, StrategyProfile
from cvd_proxy import cvd_signal
from portfolio_risk import PortfolioRisk, Position as PortPosition, detect_sector
from kill_switch import KillSwitch
from performance_analytics import PerformanceTracker, TradeRecord
from session_filter import is_tradeable_session, get_session_info

logger = logging.getLogger("AdvancedIntegration")


class AdvancedEngine:
    """Fully wired multi-agent engine with all advanced features."""

    def __init__(
        self,
        equity: float = None,
        symbol: str = None,
        profile: str = "balanced",
        source: str = None,
    ):
        self.equity = equity or getattr(config, "INITIAL_EQUITY", 10000.0)
        self.symbol = (symbol or getattr(config, "DEFAULT_SYMBOL", "XAUUSD")).upper()
        self.source = source or getattr(config, "DEFAULT_DATA_SOURCE", "demo")
        self.profile: StrategyProfile = get_profile(profile)

        self.risk = RiskManager(equity=self.equity, symbol=self.symbol)
        # Apply instrument leverage cap
        desired_lev = min(
            getattr(config, "DEFAULT_LEVERAGE", 20.0),
            self.profile.max_leverage,
            get_max_leverage(self.symbol),
        )
        lev, lev_msg = clamp_leverage(self.symbol, desired_lev)
        self.risk.set_leverage(lev)
        logger.info("Leverage: %s", lev_msg)

        self.arbiter = Arbiter(
            require_killzone=self.profile.require_killzone,
            allow_asian=self.profile.allow_asian,
        )
        # Override weights from profile
        self.arbiter.WEIGHTS = dict(self.profile.weights)
        self.arbiter.MIN_AGREEMENT = self.profile.min_agreement
        self.arbiter.MIN_SCORE = self.profile.min_score

        self.kill = KillSwitch(
            max_drawdown_pct=8.0,
            max_consecutive_losses=5,
        )
        self.portfolio = PortfolioRisk(
            max_total_risk_pct=2.0,
            max_sector_risk_pct=1.2,
            max_positions=5,
            equity=self.equity,
        )
        self.perf = PerformanceTracker()
        self.feed = MarketDataFeed(symbol=self.symbol, source=self.source)

    def set_profile(self, name: str):
        self.profile = get_profile(name)
        self.arbiter.WEIGHTS = dict(self.profile.weights)
        self.arbiter.MIN_AGREEMENT = self.profile.min_agreement
        self.arbiter.MIN_SCORE = self.profile.min_score
        self.arbiter.require_killzone = self.profile.require_killzone
        self.arbiter.allow_asian = self.profile.allow_asian

    def set_symbol(self, symbol: str):
        self.symbol = symbol.upper()
        self.risk.set_symbol(self.symbol)
        self.feed = MarketDataFeed(symbol=self.symbol, source=self.source)
        lev, _ = clamp_leverage(self.symbol, self.risk.get_leverage())
        self.risk.set_leverage(lev)

    def evaluate(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Full evaluation with every advanced filter applied."""
        sym = (symbol or self.symbol).upper()
        if sym != self.symbol:
            self.set_symbol(sym)

        # 1. Kill switch
        active, kill_msg = self.kill.is_active(
            equity=self.risk.state.equity,
            peak=self.risk.state.peak_equity,
            consecutive_losses=self.risk.state.consecutive_losses,
        )
        if active:
            return {
                "symbol": sym,
                "signal": None,
                "status": f"KILL SWITCH: {kill_msg}",
                "votes": [],
                "blocked": True,
            }

        # 2. Session
        sess = get_session_info(allow_asian=self.profile.allow_asian)
        ok_sess, sess_reason = is_tradeable_session(
            require_killzone=self.profile.require_killzone,
            allow_asian=self.profile.allow_asian,
        )
        if not ok_sess:
            return {
                "symbol": sym,
                "signal": None,
                "status": f"SESSION VETO: {sess_reason}",
                "votes": [],
                "session": sess.name,
                "blocked": True,
            }

        # 3. Data
        data = self.feed.multi_tf(limit_1m=700)
        if not data or "1m" not in data or data["1m"].empty:
            return {"symbol": sym, "signal": None, "status": "No data", "votes": [], "blocked": True}

        df1 = data["1m"]
        daily = data.get("daily", pd.DataFrame())
        quote = self.feed.get_latest_price()

        # 4. HTF bias (pre-filter)
        htf_data = {"1h": data.get("1h"), "daily": daily, "4h": data.get("15m")}
        bias, bias_reason = htf_bias(htf_data)

        # 5. Core arbiter decision
        sig, why, votes = self.arbiter.decide(
            df1,
            data.get("5m"),
            data.get("15m"),
            data.get("1h"),
            daily,
            self.risk,
            symbol=sym,
        )

        # 6. CVD confirmation vote (append informational)
        cvd_dir, cvd_str, cvd_reason = cvd_signal(df1)
        votes_out = [
            {"agent": v.agent, "direction": v.direction.name, "strength": round(v.strength, 2), "reason": v.reason}
            for v in votes
        ]
        votes_out.append({"agent": "CVD", "direction": cvd_dir, "strength": round(cvd_str, 2), "reason": cvd_reason})

        if sig is None:
            return {
                "symbol": sym,
                "price": quote.get("mark"),
                "signal": None,
                "status": why,
                "votes": votes_out,
                "session": sess.name,
                "htf_bias": bias.name,
                "htf_reason": bias_reason,
                "blocked": False,
            }

        direction_name = sig["direction"].name if hasattr(sig["direction"], "name") else str(sig["direction"])

        # 7. HTF alignment check
        htf_ok, htf_msg = allows_direction(direction_name, htf_data, strict=self.profile.strict_htf)
        if not htf_ok:
            return {
                "symbol": sym,
                "price": quote.get("mark"),
                "signal": None,
                "status": f"HTF VETO: {htf_msg}",
                "votes": votes_out,
                "session": sess.name,
                "htf_bias": bias.name,
                "blocked": True,
            }

        # 8. Volatility-adjusted risk
        regime = sig.get("regime", "unknown")
        base_risk = self.profile.base_risk_pct
        max_risk = get_max_risk_pct(sym)
        adj_risk, vol_reason = adjust_risk_pct(min(base_risk, max_risk), df1, regime=regime)

        # 9. Build trade levels via risk manager
        entry = float(quote.get("mark") or df1["close"].iloc[-1])
        atr_val = float((df1["high"] - df1["low"]).tail(14).mean()) if len(df1) >= 14 else entry * 0.002
        if direction_name == "LONG":
            stop = entry - atr_val * 1.2
        else:
            stop = entry + atr_val * 1.2

        # Temporarily apply adjusted risk
        old_base = self.risk.config.base_risk_pct
        self.risk.config.base_risk_pct = adj_risk
        plan = self.risk.position_size(entry=entry, stop=stop, symbol=sym)
        self.risk.config.base_risk_pct = old_base

        if plan.lots <= 0:
            return {
                "symbol": sym,
                "price": entry,
                "signal": None,
                "status": f"SIZE BLOCKED: {plan.reason}",
                "votes": votes_out,
                "blocked": True,
            }

        # 10. Portfolio risk check
        port_pos = PortPosition(
            symbol=sym,
            direction=direction_name,
            risk_amount=plan.risk_amount,
            notional=plan.notional_value,
            sector=detect_sector(sym),
        )
        port_ok, port_msg = self.portfolio.add(port_pos)
        if not port_ok:
            return {
                "symbol": sym,
                "price": entry,
                "signal": None,
                "status": f"PORTFOLIO VETO: {port_msg}",
                "votes": votes_out,
                "blocked": True,
            }
        # Remove immediately if only evaluating (caller will re-add on real entry)
        self.portfolio.remove(sym)

        full_signal = {
            "direction": sig["direction"],
            "entry": entry,
            "stop": stop,
            "tp1": plan.tp1,
            "tp2": plan.tp2,
            "tp3": plan.tp3,
            "lots": plan.lots,
            "size": plan.size,
            "leverage": plan.leverage,
            "margin_required": plan.margin_required,
            "risk_amount": plan.risk_amount,
            "rr": abs(plan.tp1 - entry) / abs(entry - stop) if abs(entry - stop) > 0 else 0,
            "score": sig.get("score", 0),
            "regime": regime,
            "vol_adjust": vol_reason,
            "htf": htf_msg,
            "session": sess.name,
            "profile": self.profile.name,
        }

        return {
            "symbol": sym,
            "price": entry,
            "signal": full_signal,
            "status": f"TRADE SETUP ({direction_name}) | {why}",
            "votes": votes_out,
            "session": sess.name,
            "htf_bias": bias.name,
            "vol_reason": vol_reason,
            "blocked": False,
        }

    def trail_stop(self, direction: str, entry: float, current_stop: float, df: pd.DataFrame) -> Tuple[float, str]:
        atr_val = float((df["high"] - df["low"]).tail(14).mean()) if len(df) >= 14 else 0.0
        return structure_trail_stop(direction, entry, current_stop, df, atr_value=atr_val)

    def record_closed_trade(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        entry: float,
        exit_price: float,
        r_multiple: float,
        pnl: float,
        agents: List[str],
        regime: str = "",
        session: str = "",
    ):
        self.perf.record_trade(TradeRecord(
            id=trade_id,
            symbol=symbol,
            direction=direction,
            entry=entry,
            exit=exit_price,
            r_multiple=r_multiple,
            pnl=pnl,
            agents=agents,
            regime=regime,
            session=session,
        ))
        self.portfolio.remove(symbol)

    def performance_report(self) -> str:
        return self.perf.telegram_report()

    def kill_status(self) -> str:
        active, msg = self.kill.is_active(
            equity=self.risk.state.equity,
            peak=self.risk.state.peak_equity,
            consecutive_losses=self.risk.state.consecutive_losses,
        )
        return msg

    def activate_kill(self, reason: str = "Manual kill via command"):
        self.kill.activate_manual(reason)

    def deactivate_kill(self):
        self.kill.deactivate()


# Telegram command helpers (import these in main.py)
async def performance_command(update, ctx):
    """Telegram /performance handler."""
    try:
        eng = AdvancedEngine()
        report = eng.performance_report()
        await update.message.reply_text(report, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Performance error: {e}")


async def kill_command(update, ctx):
    """Telegram /kill on|off handler."""
    eng = AdvancedEngine()
    arg = (ctx.args[0].lower() if ctx.args else "status")
    if arg in ("on", "1", "true", "activate"):
        eng.activate_kill("Manual kill via Telegram")
        await update.message.reply_text("🛑 **KILL SWITCH ACTIVATED** — no new entries.", parse_mode="Markdown")
    elif arg in ("off", "0", "false", "deactivate"):
        eng.deactivate_kill()
        await update.message.reply_text("🟢 **Kill switch deactivated.**", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Kill status: `{eng.kill_status()}`", parse_mode="Markdown")


async def profile_command(update, ctx):
    """Telegram /profile [name] handler."""
    from strategy_profiles import load_profiles
    profiles = load_profiles()
    if not ctx.args:
        names = ", ".join(profiles.keys())
        await update.message.reply_text(f"Available profiles: `{names}`\nUsage: `/profile swing`", parse_mode="Markdown")
        return
    name = ctx.args[0].lower()
    if name not in profiles:
        await update.message.reply_text(f"Unknown profile `{name}`. Available: {', '.join(profiles.keys())}")
        return
    p = profiles[name]
    await update.message.reply_text(
        f"📋 **Profile: {p.name}**\n{p.description}\n"
        f"• Min score: `{p.min_score}` | Agreement: `{p.min_agreement}`\n"
        f"• Risk: `{p.base_risk_pct}%` | Max lev: `{p.max_leverage}x`\n"
        f"• Killzone only: `{p.require_killzone}` | Strict HTF: `{p.strict_htf}`",
        parse_mode="Markdown",
    )


def register_advanced_commands(application):
    """Call this from main.py after ApplicationBuilder to register extra commands."""
    from telegram.ext import CommandHandler
    application.add_handler(CommandHandler("performance", performance_command))
    application.add_handler(CommandHandler("kill", kill_command))
    application.add_handler(CommandHandler("profile", profile_command))
    logger.info("Advanced commands registered: /performance /kill /profile")
