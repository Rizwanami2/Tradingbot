"""
Best-tier Telegram command pack
===============================
Drop-in handlers for advanced features. Register with:

    from telegram_advanced import register_all_advanced
    register_all_advanced(app)

Commands:
  /performance
  /kill [on|off]
  /profile [name]
  /asignal [SYMBOL]     — full AdvancedEngine evaluation
  /abacktest [bars] [mode] [symbol]
        modes: single | walkforward | montecarlo | multiagent | sweep
"""
from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import config

logger = logging.getLogger("TelegramAdvanced")


async def performance_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        from advanced_integration import AdvancedEngine
        eng = AdvancedEngine()
        await update.message.reply_text(eng.performance_report(), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Performance error: `{e}`", parse_mode="Markdown")


async def kill_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        from advanced_integration import AdvancedEngine
        eng = AdvancedEngine()
        arg = (ctx.args[0].lower() if ctx.args else "status")
        if arg in ("on", "1", "true", "activate"):
            eng.activate_kill("Manual kill via Telegram")
            await update.message.reply_text("🛑 **KILL SWITCH ON** — no new entries.", parse_mode="Markdown")
        elif arg in ("off", "0", "false", "deactivate"):
            eng.deactivate_kill()
            await update.message.reply_text("🟢 **Kill switch OFF.**", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"Status: `{eng.kill_status()}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Kill error: `{e}`", parse_mode="Markdown")


async def profile_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        from strategy_profiles import load_profiles
        profiles = load_profiles()
        if not ctx.args:
            await update.message.reply_text(
                f"Profiles: `{', '.join(profiles.keys())}`\nUsage: `/profile swing`", parse_mode="Markdown")
            return
        name = ctx.args[0].lower()
        if name not in profiles:
            await update.message.reply_text(f"Unknown `{name}`. Available: {', '.join(profiles.keys())}")
            return
        p = profiles[name]
        await update.message.reply_text(
            f"📋 **{p.name}**\n{p.description}\n"
            f"• Score ≥ `{p.min_score}` | Agree ≥ `{p.min_agreement}` | RR ≥ `{p.min_rr}`\n"
            f"• Risk `{p.base_risk_pct}%` | Max lev `{p.max_leverage}x`\n"
            f"• Killzone `{p.require_killzone}` | Strict HTF `{p.strict_htf}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"Profile error: `{e}`", parse_mode="Markdown")


async def asignal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Full AdvancedEngine evaluation."""
    sym = ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL
    profile = ctx.args[1].lower() if len(ctx.args) > 1 else "balanced"
    await update.message.reply_text(f"⏳ Advanced eval **{sym}** (profile `{profile}`)...", parse_mode="Markdown")
    try:
        from advanced_integration import AdvancedEngine
        eng = AdvancedEngine(symbol=sym, profile=profile)
        res = eng.evaluate(sym)
    except Exception as e:
        await update.message.reply_text(f"Eval failed: `{e}`", parse_mode="Markdown")
        return

    price = float(res.get("price") or 0)
    votes = res.get("votes") or []
    lines = []
    for v in votes:
        d = v.get("direction", "FLAT")
        icon = "🟢" if d == "LONG" else ("🔴" if d == "SHORT" else "⚪")
        lines.append(f"{icon} **{v.get('agent')}:** {d} — {v.get('reason', '')}")

    meta = []
    if res.get("session"):
        meta.append(f"Session `{res['session']}`")
    if res.get("htf_bias"):
        meta.append(f"HTF `{res['htf_bias']}`")
    if res.get("vol_reason"):
        meta.append(f"Vol `{res['vol_reason']}`")

    sig = res.get("signal")
    if sig and not res.get("blocked"):
        direction = sig.get("direction")
        dname = direction.name if hasattr(direction, "name") else str(direction)
        box = (
            f"\n🎯 **SETUP {dname}**\n"
            f"• Entry `${sig.get('entry', 0):,.2f}` | SL `${sig.get('stop', 0):,.2f}`\n"
            f"• TP1 `${sig.get('tp1', 0):,.2f}` | TP2 `${sig.get('tp2', 0):,.2f}`\n"
            f"• Lots `{sig.get('lots')}` | Lev `{sig.get('leverage', 1):.0f}x` | Margin `${sig.get('margin_required', 0):,.2f}`\n"
            f"• R:R `1:{sig.get('rr', 0):.1f}` | Profile `{sig.get('profile')}` | Regime `{sig.get('regime')}`\n"
        )
    else:
        box = "\n🛡️ **No trade** — blocked or no consensus.\n"

    msg = (
        f"📊 **Advanced Signal — {sym}**\n"
        f"Mark `${price:,.2f}`\n"
        + (" | ".join(meta) + "\n" if meta else "")
        + "\n**Votes:**\n" + "\n".join(lines)
        + f"\n\n⚡ `{res.get('status')}`\n"
        + box
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def abacktest_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /abacktest [bars] [mode] [symbol]
    modes: single | walkforward | montecarlo | multiagent | sweep
    """
    bars = 1500
    mode = "single"
    symbol = config.DEFAULT_SYMBOL
    args = list(ctx.args or [])
    if args and args[0].isdigit():
        bars = int(args.pop(0))
    if args and args[0].lower() in ("single", "walkforward", "montecarlo", "multiagent", "sweep", "wf", "mc"):
        mode = args.pop(0).lower()
        if mode == "wf":
            mode = "walkforward"
        if mode == "mc":
            mode = "montecarlo"
    if args:
        symbol = args[0].upper()

    await update.message.reply_text(
        f"⏳ Advanced backtest `{mode}` — {symbol} — {bars} bars...\n_This may take a moment._",
        parse_mode="Markdown",
    )

    try:
        from advanced_backtest import (
            load_data, SimConfig, AdvancedSimulator, ema_cross_signal,
            multiagent_proxy_signal, walk_forward, monte_carlo_from_trades, parameter_sweep,
        )
        df = load_data(symbol, bars, getattr(config, "DEFAULT_DATA_SOURCE", "demo"), None)
        cfg = SimConfig(
            initial_equity=getattr(config, "INITIAL_EQUITY", 10000.0),
            risk_per_trade=max(0.001, getattr(config, "DEFAULT_RISK_PCT", 0.18) / 100.0),
        )
        sim = AdvancedSimulator(cfg)

        if mode == "walkforward":
            wf = walk_forward(df, ema_cross_signal, n_windows=4, cfg=cfg, symbol=symbol)
            text = wf.summary()
        elif mode == "montecarlo":
            m, trades, _ = sim.run(df, ema_cross_signal, symbol)
            mc = monte_carlo_from_trades(trades, initial_equity=cfg.initial_equity, n_runs=400)
            text = m.summary() + mc.summary()
        elif mode == "multiagent":
            m, trades, _ = sim.run(df, multiagent_proxy_signal, symbol)
            text = m.summary()
        elif mode == "sweep":
            table = parameter_sweep(df, cfg=cfg, symbol=symbol)
            text = "Parameter sweep (top 10 by Sharpe):\n```\n" + table.head(10).to_string(index=False) + "\n```"
        else:
            m, trades, _ = sim.run(df, ema_cross_signal, symbol)
            text = m.summary()
            if trades:
                text += "\nRecent trades:\n"
                for t in trades[-5:]:
                    text += f"• {t.direction} R={t.r_multiple:+.2f} PnL={t.pnl:+.2f} ({t.reason})\n"

        # Telegram message limit ~4096
        if len(text) > 4000:
            text = text[:3900] + "\n…(truncated)"
        await update.message.reply_text(f"```\n{text}\n```", parse_mode="Markdown")
    except Exception as e:
        logger.exception("abacktest failed")
        await update.message.reply_text(f"Backtest error: `{type(e).__name__}: {e}`", parse_mode="Markdown")


def register_all_advanced(application) -> None:
    application.add_handler(CommandHandler("performance", performance_command))
    application.add_handler(CommandHandler("kill", kill_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("asignal", asignal_command))
    application.add_handler(CommandHandler("abacktest", abacktest_command))
    logger.info("Registered: /performance /kill /profile /asignal /abacktest")
