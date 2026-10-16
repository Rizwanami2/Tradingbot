"""
Best-tier Telegram command pack
===============================
Register:
    from telegram_advanced import register_all_advanced
    register_all_advanced(app)

Also starts AutonomousLearner (5y history self-correction) automatically.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import config

logger = logging.getLogger("TelegramAdvanced")

_MODES = {"single", "walkforward", "montecarlo", "multiagent", "sweep", "wf", "mc", "auto", "full", "all"}

from telegram_brain import safe_reply


async def performance_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        from performance_analytics import PerformanceTracker
        perf = PerformanceTracker()
        args = list(getattr(ctx, "args", []) or [])
        sub = args[0].lower() if args else "summary"
        if sub in ("help", "commands"):
            msg = (
                "📊 *Performance Commands:*\n"
                "• `/performance` — Detailed trade metrics, win rate & Sharpe ratio\n"
                "• `/performance agents` — Agent contribution rankings\n"
                "• `/performance trades` — Recent closed trades log\n"
                "• `/performance reset` — Clear performance history"
            )
            await safe_reply(update, msg, parse_mode="Markdown")
        elif sub in ("agents", "ranking", "rankings"):
            await safe_reply(update, perf.agent_report(), parse_mode="Markdown")
        elif sub in ("trades", "history", "recent", "closed"):
            await safe_reply(update, perf.recent_trades_report(), parse_mode="Markdown")
        elif sub in ("reset", "clear"):
            perf.reset()
            await safe_reply(update, "🧹 *Performance Stats Reset*\nAll closed trade history has been cleared.", parse_mode="Markdown")
        else:
            await safe_reply(update, perf.telegram_report(), parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /performance command: %s", e)
        await safe_reply(update, f"Performance error: {e}", parse_mode=None)



async def kill_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user if update else None
        user_id = user.id if user else None
        import config
        from ops_dashboard import is_authorized_user
        if not is_authorized_user(user_id, config.TELEGRAM_ALLOWED_USER_IDS):
            await safe_reply(update, "⛔ *Access Denied*: You are not authorized to use the kill switch.", parse_mode="Markdown")
            return

        from kill_switch import KillSwitch
        ks = KillSwitch()
        arg = (ctx.args[0].lower() if ctx.args else "status")
        if arg in ("on", "1", "true", "activate"):
            ks.activate_manual("Manual kill via Telegram")
            await safe_reply(update, "🛑 *KILL SWITCH ON* — no new entries.", parse_mode="Markdown")
        elif arg in ("off", "0", "false", "deactivate"):
            ks.deactivate()
            await safe_reply(update, "🟢 *Kill switch OFF.*", parse_mode="Markdown")
        else:
            _, msg = ks.is_active()
            await safe_reply(update, f"Status: `{msg}`", parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /kill command: %s", e)
        await safe_reply(update, f"Kill error: {e}", parse_mode=None)


async def profile_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        from strategy_profiles import load_profiles, apply_profile, CURRENT_PROFILE
        profiles = load_profiles()
        if not ctx.args:
            await safe_reply(
                update,
                f"📋 *Strategy Profiles* (Active: `{CURRENT_PROFILE}`)\n"
                f"• Available: `{', '.join(profiles.keys())}`\n"
                f"• Usage: `/profile scalp` or `/profile balanced`\n"
                f"💡 _Tip: Use `/profile scalp` for rapid micro-scalping with small SL/TP & fast capital gains._",
                parse_mode="Markdown",
            )
            return
        name = ctx.args[0].lower()
        if name not in profiles:
            await safe_reply(update, f"Unknown `{name}`. Available: {', '.join(profiles.keys())}", parse_mode="Markdown")
            return
        try:
            from main import arbiter, risk_mgr, engine
            import config
            p, summary = apply_profile(name, arbiter=arbiter, risk_mgr=risk_mgr, engine=engine, config=config)
        except Exception:
            p, summary = apply_profile(name)
        await safe_reply(update, summary, parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /profile command: %s", e)
        await safe_reply(update, f"Profile error: {e}", parse_mode=None)


async def asignal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym = ctx.args[0].upper() if ctx.args else config.DEFAULT_SYMBOL
    profile = ctx.args[1].lower() if len(ctx.args) > 1 else "balanced"
    await safe_reply(update, f"⏳ Advanced eval *{sym}* (`{profile}`)...", parse_mode="Markdown")
    try:
        from advanced_integration import AdvancedEngine
        eng = AdvancedEngine(symbol=sym, profile=profile)
        res = eng.evaluate(sym)
        # Autonomous learned filter (automatic)
        try:
            from autonomous_learner import get_brain
            brain = get_brain(auto_bootstrap=False)
            sig = res.get("signal") or {}
            direction = sig.get("direction")
            dname = direction.name if hasattr(direction, "name") else str(direction or "")
            is_scalp_sig = bool(sig.get("is_scalp", False)) or getattr(eng, "is_scalp", False) or getattr(getattr(eng, "profile", None), "is_scalp", False)
            ok, reason, mult = brain.advise_signal(
                direction=dname,
                score=float(sig.get("score") or 0),
                rr=float(sig.get("rr") or 0),
                agreement=len([v for v in (res.get("votes") or []) if str(v.get("direction", "")).upper() in ("LONG", "SHORT")]),
                conviction=float((sig.get("conviction") or {}).get("score") or 0) if isinstance(sig.get("conviction"), dict) else 0.0,
                regime=str(sig.get("regime") or ""),
                setup=f"{dname}|{sig.get('regime')}|{res.get('session')}",
                is_scalp=is_scalp_sig,
            )
            if not ok:
                res["blocked"] = True
                res["status"] = reason
                res["signal"] = None
            elif sig and mult != 1.0 and sig.get("lots") is not None:
                try:
                    sig["lots"] = float(sig["lots"]) * mult
                    sig["auto_size_mult"] = mult
                except Exception:
                    pass
        except Exception:
            pass
    except Exception as e:
        logger.exception("Error in /asignal command: %s", e)
        await safe_reply(update, f"Eval failed: {e}", parse_mode=None)
        return

    price = float(res.get("price") or 0)
    votes = res.get("votes") or []
    lines = []
    for v in votes:
        d = v.get("direction", "FLAT")
        icon = "🟢" if d == "LONG" else ("🔴" if d == "SHORT" else "⚪")
        lines.append(f"{icon} *{v.get('agent')}:* {d} — {v.get('reason', '')}")

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
            f"\n🎯 *SETUP {dname}*\n"
            f"• Entry `${sig.get('entry', 0):,.2f}` | SL `${sig.get('stop', 0):,.2f}`\n"
            f"• TP1 `${sig.get('tp1', 0):,.2f}` | TP2 `${sig.get('tp2', 0):,.2f}`\n"
            f"• Lots `{sig.get('lots')}` | Lev `{sig.get('leverage', 1):.0f}x`\n"
            f"• R:R `1:{sig.get('rr', 0):.1f}` | `{sig.get('profile')}` / `{sig.get('regime')}`\n"
        )
    else:
        box = "\n🛡️ *No trade* — blocked or no consensus.\n"

    msg = (
        f"📊 *Advanced Signal — {sym}*\nMark `${price:,.2f}`\n"
        + (" | ".join(meta) + "\n" if meta else "")
        + "\n*Votes:*\n" + "\n".join(lines)
        + f"\n\n⚡ `{res.get('status')}`\n" + box
    )
    await safe_reply(update, msg, parse_mode="Markdown")


async def abacktest_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bars = 2000
    mode = "auto"
    symbol = config.DEFAULT_SYMBOL
    args = list(ctx.args or [])

    if args and args[0].isdigit():
        bars = int(args.pop(0))
    if args and args[0].lower() in _MODES:
        mode = args.pop(0).lower()
        if mode in ("wf",):
            mode = "walkforward"
        if mode in ("mc",):
            mode = "montecarlo"
        if mode in ("full", "all"):
            mode = "auto"
    if args:
        symbol = args[0].upper()

    if mode == "auto":
        await safe_reply(
            update,
            f"⏳ *Auto backtest suite* — `{symbol}` — {bars} bars\n"
            f"Running: single + multiagent + walk-forward + Monte Carlo + sweep…",
            parse_mode="Markdown",
        )
        try:
            from auto_backtest import run_auto_backtest
            report = run_auto_backtest(
                symbol=symbol,
                bars=bars,
                source=getattr(config, "DEFAULT_DATA_SOURCE", "demo"),
                equity=getattr(config, "INITIAL_EQUITY", 10000.0),
                risk=max(0.001, getattr(config, "DEFAULT_RISK_PCT", 0.18) / 100.0),
                mc_runs=300,
                wf_windows=4,
                do_sweep=True,
            )
            text = report.text(max_len=3800)
            await safe_reply(update, f"```\n{text}\n```", parse_mode="Markdown")
        except Exception as e:
            logger.exception("auto backtest failed")
            await safe_reply(update, f"Auto backtest error: {type(e).__name__}: {e}", parse_mode=None)
        return

    await safe_reply(
        update,
        f"⏳ Backtest `{mode}` — {symbol} — {bars} bars…", parse_mode="Markdown")
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
            if len(df) < 300:
                text = f"Walk-forward requires at least 300 bars (got {len(df)}). Try `/abacktest 500 wf {symbol}`."
            else:
                text = walk_forward(df, ema_cross_signal, n_windows=4, cfg=cfg, symbol=symbol).summary()
        elif mode == "montecarlo":
            m, trades, _ = sim.run(df, ema_cross_signal, symbol)
            if len(trades) < 5:
                text = m.summary() + f"\n\n⚠️ Monte Carlo requires at least 5 simulated trades (got {len(trades)}). Try running with more bars: `/abacktest 500 mc {symbol}`."
            else:
                mc = monte_carlo_from_trades(trades, initial_equity=cfg.initial_equity, n_runs=400)
                text = m.summary() + mc.summary()
        elif mode == "multiagent":
            text = sim.run(df, multiagent_proxy_signal, symbol)[0].summary()
        elif mode == "sweep":
            table = parameter_sweep(df, cfg=cfg, symbol=symbol)
            text = "Sweep top 10:\n" + table.head(10).to_string(index=False)
        else:
            m, trades, _ = sim.run(df, ema_cross_signal, symbol)
            text = m.summary()

        if len(text) > 4000:
            text = text[:3900] + "\n…(truncated)"
        await safe_reply(update, f"```\n{text}\n```", parse_mode="Markdown")
    except Exception as e:
        logger.exception("abacktest failed")
        await safe_reply(update, f"Backtest error: {type(e).__name__}: {e}", parse_mode=None)


def register_all_advanced(application) -> None:
    # Start autonomous brain (5y learn + auto-correct) — no user command needed
    try:
        from autonomous_learner import auto_start
        auto_start()
        logger.info("Autonomous learner auto-started")
    except Exception as e:
        logger.debug("Autonomous learner start skip: %s", e)

    existing_cmds = set()
    for h in application.handlers.get(0, []):
        if hasattr(h, "commands") and isinstance(h.commands, (set, frozenset, list, tuple)):
            existing_cmds.update(h.commands)

    def _safe_add(cmd: str, handler):
        if cmd not in existing_cmds:
            application.add_handler(CommandHandler(cmd, handler))
            existing_cmds.add(cmd)

    _safe_add("performance", performance_command)
    _safe_add("perf", performance_command)
    _safe_add("kill", kill_command)
    _safe_add("profile", profile_command)
    _safe_add("asignal", asignal_command)
    _safe_add("abacktest", abacktest_command)
    _safe_add("backtestall", abacktest_command)
    try:
        from telegram_feature_pack import register_feature_pack
        register_feature_pack(application)
    except Exception as e:
        logger.debug("Feature Pack v3 not registered: %s", e)
    try:
        from telegram_feature_pack_v4 import register_feature_pack_v4
        register_feature_pack_v4(application)
    except Exception as e:
        logger.debug("Feature Pack v4 not registered: %s", e)
    try:
        from telegram_brain import register_brain
        register_brain(application)
    except Exception as e:
        logger.debug("Brain commands not registered: %s", e)
    logger.info(
        "Registered advanced + FP3 + FP4 + /brain /relearn; autonomous learner active"
    )
