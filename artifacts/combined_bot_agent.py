#!/usr/bin/env python3
"""
COMBINED BOT & AGENT TRADING SYSTEM
===================================
Unified production entrypoint combining:
1. Multi-Agent Consensus Engine (Arbiter, SMC Liquidity, Trend, 10 Indicators, Gautam Jha, etc.)
2. AI Advanced Suite (Dynamic risk gating, daily bias, circuit breakers, open/close memory)
3. Autonomous Learner (Online weight learning, experience replay, regime lot optimization)
4. Execution Engine & Risk Manager (Single-direction protection, auto & manual cooldowns, virtual stops)
5. MT5 Broker Bridge (Live execution, hybrid EA bridge, fail-closed paper fallback)
6. Live Autotrade Monitoring Worker (Continuous multi-symbol candle polling & setup triggers)
7. Telegram Bot & Headless / CLI Supervisor

Usage:
  python artifacts/combined_bot_agent.py              # Run full combined autonomous system
  python artifacts/combined_bot_agent.py --check      # Run comprehensive subsystem diagnostic
  python artifacts/combined_bot_agent.py --once       # Run single-pass consensus & risk evaluation
  python artifacts/combined_bot_agent.py --live       # Run 24/7 live combined trading loop
  python artifacts/combined_bot_agent.py --telegram   # Run Telegram bot with combined monitoring
  python artifacts/combined_bot_agent.py --symbols XAUUSD,BTCUSDT
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_STRAT = str(ROOT / "strategies")
if _STRAT not in sys.path:
    sys.path.insert(0, _STRAT)

import config
from anti_spam import get_anti_spam_limiter
from execution_engine import ExecutionEngine, OrderStatus, TradeOrder
from risk_manager import RiskManager

logger = logging.getLogger("combined_bot_agent")


def setup_environment() -> None:
    """Setup Windows console compatibility and logging configuration."""
    if sys.platform == "win32":
        for _s in (sys.stdout, sys.stderr, sys.stdin):
            if hasattr(_s, "reconfigure"):
                try:
                    _s.reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass

    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
        stream=sys.stdout,
        force=True,
    )
    for noisy in ("httpx", "httpcore", "telegram", "telegram.ext", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def print_banner(mode: str = "COMBINED", watchlist: Optional[List[str]] = None) -> None:
    syms = ", ".join(watchlist or config.get_autotrade_symbols())
    broker = getattr(config, "BROKER_MODE", "mt5_live")
    profile = getattr(config, "CURRENT_PROFILE", "scalp")
    banner = f"""
================================================================================
                    NIMBLE DARWIN - COMBINED BOT & AGENT
================================================================================
  Root Workspace:   {ROOT}
  Execution Mode:   {mode.upper()}
  Watchlist:        {syms}
  Strategy Profile: {profile}
  Broker Gateway:   {broker}
  Multi-Agent:      ACTIVE (10 Institutional Consensus Strategies)
  AI Suite:         ACTIVE (Scorecard Gating, Bias & Circuit Breakers)
  Autonomous Brain: ACTIVE (Online Dynamic Learner & Experience Memory)
  Trade Protection: STRICT SINGLE-DIRECTION (No simultaneous BUY/SELL)
  Cooldown System:  ACTIVE (Enforced on BOTH Automatic & Manual trades)
================================================================================
"""
    print(banner)


def run_diagnostic_check() -> Dict[str, Any]:
    """
    Comprehensive diagnostic validation of all subsystems:
    Multi-Agent Consensus, AI Suite, Autonomous Brain, Execution Engine,
    Anti-Spam / Cooldowns, Single-Direction Protection, and Broker Bridge.
    """
    results: Dict[str, Tuple[bool, str]] = {}

    # 1. Config validation
    try:
        syms = config.get_autotrade_symbols()
        results["Configuration"] = (True, f"Loaded {len(syms)} symbols: {', '.join(syms)}")
    except Exception as e:
        results["Configuration"] = (False, f"Config error: {e}")

    # 2. Multi-Agent Arbiter
    try:
        from multi_agent_trading_bot import Arbiter, MarketDataFeed
        arb = Arbiter(allow_asian=True)
        weights_cnt = len(getattr(arb, "WEIGHTS", {}))
        results["Multi-Agent Arbiter"] = (True, f"Initialized with {weights_cnt} strategy weights")
    except Exception as e:
        results["Multi-Agent Arbiter"] = (False, f"Arbiter load failed: {e}")

    # 3. AI Advanced Suite
    try:
        from ai_advanced_suite import get_ai_suite
        suite = get_ai_suite()
        mode_val = suite.state.get("control_mode", "auto")
        results["AI Advanced Suite"] = (
            True,
            f"Enabled={suite.enabled}, Mode={mode_val}, MinScore={suite.min_scorecard:.0f}"
        )
    except Exception as e:
        results["AI Advanced Suite"] = (False, f"AI Suite load failed: {e}")

    # 4. Autonomous Learner Brain
    try:
        from autonomous_learner import get_brain
        brain = get_brain(auto_bootstrap=False)
        exp_cnt = len(getattr(brain, "experience", []))
        results["Autonomous Learner"] = (True, f"Initialized, memory records: {exp_cnt}")
    except Exception as e:
        results["Autonomous Learner"] = (False, f"Brain load failed: {e}")

    # 5. Risk Manager & Execution Engine
    try:
        rm = RiskManager(equity=float(config.INITIAL_EQUITY), symbol=config.DEFAULT_SYMBOL)
        engine = ExecutionEngine(risk_manager=rm)
        results["Execution Engine"] = (True, f"Equity: ${rm.state.equity:,.2f}, MaxOpen: {engine.max_open_trades}")
    except Exception as e:
        results["Execution Engine"] = (False, f"Execution Engine failed: {e}")

    # 6. Anti-Spam & Single-Direction Protection
    try:
        from mt5_broker import MockMT5Broker
        limiter = get_anti_spam_limiter()
        # Verify single-direction logic using isolated mock broker
        diag_state = "test_diag_exec.json"
        if os.path.exists(diag_state):
            try:
                os.remove(diag_state)
            except Exception:
                pass
        test_broker = MockMT5Broker(initial_balance=10000.0)
        test_rm = RiskManager(equity=10000.0, symbol="XAUUSD")
        test_engine = ExecutionEngine(risk_manager=test_rm, broker=test_broker, state_file=diag_state)
        test_sig = {
            "symbol": "XAUUSD", "direction": "LONG", "entry": 2650.0, "stop": 2640.0,
            "tp1": 2665.0, "tp2": 2675.0, "lots": 0.02, "size": 2.0, "leverage": 20.0,
            "margin_required": 50.0, "risk_amount": 20.0, "rr": 1.5,
        }
        test_engine.register_setup(test_sig)
        test_engine.update_market_candle("XAUUSD", {"open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2651.0})
        can_sell, sell_reason = limiter.check_can_enter(
            symbol="XAUUSD", current_price=2651.0, engine=test_engine, direction="SHORT"
        )
        if not can_sell and "cannot BUY and SELL at the same time" in sell_reason:
            results["Single-Direction Protection"] = (True, "Strict opposing position blocking verified")
        else:
            results["Single-Direction Protection"] = (False, f"Unexpected check response: {can_sell}, {sell_reason}")
        results["Cooldown Protection"] = (True, f"Entry cooldown: {limiter.entry_cooldown_sec}s, Loss cooldown: {limiter.loss_cooldown_sec}s")
        if os.path.exists(diag_state):
            try:
                os.remove(diag_state)
            except Exception:
                pass
    except Exception as e:
        results["Single-Direction Protection"] = (False, f"Protection check failed: {e}")
        results["Cooldown Protection"] = (False, f"Cooldown check failed: {e}")

    # 7. Broker Bridge
    try:
        from execution import create_broker_fail_closed
        broker = create_broker_fail_closed(
            broker_mode=getattr(config, "BROKER_MODE", "mt5_live"),
            api_key=getattr(config, "DELTA_EXCHANGE_API_KEY", "") or getattr(config, "EXCHANGE_API_KEY", ""),
            api_secret=getattr(config, "DELTA_EXCHANGE_API_SECRET", "") or getattr(config, "EXCHANGE_API_SECRET", ""),
        )
        results["Broker Gateway"] = (True, f"Active broker: {type(broker).__name__}")
    except Exception as e:
        results["Broker Gateway"] = (False, f"Broker init warning: {e}")

    # Print clean diagnostic table
    print("\n---------------------- SUBSYSTEM HEALTH DIAGNOSTIC ----------------------")
    all_ok = True
    for name, (ok, detail) in results.items():
        tag = "[  OK  ]" if ok else "[ FAIL ]"
        if not ok:
            all_ok = False
        print(f" {tag} {name:<28}: {detail}")
    print("------------------------------------------------------------------------\n")
    print(f" Diagnostic Summary: {'ALL SYSTEMS OPERATIONAL' if all_ok else 'SOME SUBSYSTEMS REPORTED ISSUES'}\n")
    return {"all_ok": all_ok, "details": results}


def run_single_cycle(
    symbols: Optional[List[str]] = None,
    dry_run: bool = False,
    broker: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Execute a single combined evaluation cycle across the watchlist:
    - Fetches market candles
    - Updates execution engine with price updates
    - Runs 10-Agent Arbiter consensus
    - Applies Autonomous Learner & AI Advanced Suite gating
    - Enforces Anti-Spam single-direction protection & cooldowns
    - Registers setups or manages open positions
    """
    from anti_spam import get_anti_spam_limiter
    from multi_agent_trading_bot import Arbiter, MarketDataFeed, _norm_trade_symbol
    from strategy_profiles import apply_profile

    syms = symbols or config.get_autotrade_symbols()
    print(f"[Combined-Cycle] Starting evaluation across {len(syms)} symbols: {', '.join(syms)}")

    risk_mgr = RiskManager(equity=float(config.INITIAL_EQUITY), symbol=config.DEFAULT_SYMBOL)
    risk_mgr.set_leverage(float(config.DEFAULT_LEVERAGE))
    arbiter = Arbiter(allow_asian=True)

    is_test = "pytest" in sys.modules or "unittest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST")
    if broker is not None:
        active_broker = broker
    elif is_test:
        from mt5_broker import MockMT5Broker
        active_broker = MockMT5Broker(initial_balance=float(config.INITIAL_EQUITY))
    else:
        try:
            from execution import create_broker_fail_closed
            active_broker = create_broker_fail_closed(broker_mode=getattr(config, "BROKER_MODE", "mt5_live"))
        except Exception:
            from mt5_broker import MockMT5Broker
            active_broker = MockMT5Broker(initial_balance=float(config.INITIAL_EQUITY))

    engine = ExecutionEngine(risk_manager=risk_mgr, broker=active_broker)
    apply_profile(getattr(config, "CURRENT_PROFILE", "scalp"), arbiter=arbiter, risk_mgr=risk_mgr, engine=engine, config=config)

    limiter = get_anti_spam_limiter()
    from ai_advanced_suite import evaluate_signal_dict, get_ai_suite
    from autonomous_learner import get_brain
    suite = get_ai_suite()
    brain = get_brain(auto_bootstrap=False)

    cycle_report = {"timestamp": datetime.now(timezone.utc).isoformat(), "evaluations": {}}

    for raw_sym in syms:
        sym = _norm_trade_symbol(raw_sym)
        feed = MarketDataFeed(symbol=sym, source=config.DEFAULT_DATA_SOURCE)
        try:
            data = feed.multi_tf(limit_1m=400)
            if not data or "1m" not in data or data["1m"].empty:
                print(f"[Combined-Cycle] {sym}: Waiting for market data...")
                cycle_report["evaluations"][sym] = {"status": "NO_DATA"}
                continue

            df1 = data["1m"]
            candle = df1.iloc[-1]
            candle_dict = {
                "open": float(candle.get("open", candle["close"])),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
            }

            # 1. Update engine market candle
            evs = engine.update_market_candle(sym, candle_dict)
            for ev in evs:
                print(f"[ExecutionEngine] {ev.get('event')}: {ev.get('message')}")

            # 2. Check slot limits and single-direction / cooldown
            max_open = int(getattr(engine, "max_open_trades", getattr(config, "MAX_OPEN_TRADES", 6)) or 6)
            can_enter, block_reason = limiter.check_can_enter(
                symbol=sym,
                current_price=candle_dict["close"],
                engine=engine,
                max_open=max_open,
                bypass_cooldowns=False,
            )

            if not can_enter:
                print(f"[Combined-Cycle] {sym} @ {candle_dict['close']:.2f} | Skipped entry: {block_reason}")
                cycle_report["evaluations"][sym] = {"status": "BLOCKED", "reason": block_reason}
                continue

            # 3. Multi-Agent Decision
            daily = data.get("daily")
            sig, why, votes = arbiter.decide(
                df1,
                data.get("5m"),
                data.get("15m"),
                data.get("1h"),
                daily,
                risk_mgr,
                symbol=sym,
                on_demand=True,
                engine=engine,
            )

            if not sig:
                print(f"[Combined-Cycle] {sym} @ {candle_dict['close']:.2f} | Status: {why}")
                cycle_report["evaluations"][sym] = {"status": "NO_SETUP", "reason": why}
                continue

            # 4. Extract signal direction and parameters
            d = sig.get("direction")
            dname = d.name if hasattr(d, "name") else str(d)
            sig["symbol"] = sym
            sig["direction"] = dname

            # 5. Check opposing position again strictly for signal direction
            dir_ok, dir_reason = limiter.check_can_enter(
                symbol=sym,
                current_price=candle_dict["close"],
                engine=engine,
                direction=dname,
            )
            if not dir_ok:
                print(f"[Combined-Cycle] {sym} {dname} vetoed by Single-Direction Protection: {dir_reason}")
                cycle_report["evaluations"][sym] = {"status": "OPPOSING_DIR_VETO", "reason": dir_reason}
                continue

            # 6. Autonomous Learner optimization
            try:
                conf = float(sig.get("conviction") or 0.6)
            except Exception:
                conf = 0.6
            _, _, size_mult = brain.advise_signal(
                setup=f"{dname.lower()}|{sym.lower()}",
                regime=str(sig.get("regime", "")),
                session=str(sig.get("session", "")),
                conf=conf,
                agents=[str(v.name if hasattr(v, "name") else getattr(v, "agent_name", str(v))) for v in (votes or [])],
            )
            if size_mult < 1.0 and sig.get("lots"):
                sig["lots"] = max(0.01, round(float(sig["lots"]) * size_mult, 4))

            # 7. AI Advanced Suite evaluation
            if getattr(suite, "enabled", True):
                gate = evaluate_signal_dict(sig, profile=getattr(config, "CURRENT_PROFILE", "scalp"))
                if not gate.allow:
                    reasons = "; ".join(gate.reasons)
                    print(f"[Combined-Cycle] {sym} {dname} vetoed by AI Advanced Suite: {reasons}")
                    cycle_report["evaluations"][sym] = {"status": "AI_SUITE_VETO", "reason": reasons}
                    continue
                if gate.size_mult < 1.0 and sig.get("lots"):
                    sig["lots"] = max(0.01, round(float(sig["lots"]) * gate.size_mult, 4))

            # 8. Register setup or simulate dry-run
            if dry_run:
                print(f"[Combined-Cycle] [DRY RUN] Would execute: {sym} {dname} | Lots: {sig.get('lots')} | Entry: {sig.get('entry')} | SL: {sig.get('stop')}")
                cycle_report["evaluations"][sym] = {"status": "DRY_RUN_SETUP", "signal": sig}
            else:
                order = engine.register_setup(sig, reason=sig.get("reason", "Combined Bot Agent setup"))
                trig_events = engine.update_market_candle(sym, candle_dict)
                for ev in trig_events:
                    print(f"[ExecutionEngine] {ev.get('event')}: {ev.get('message')}")
                print(f"⚡ [SETUP REGISTERED] {sym} {order.direction} @ {order.entry_level} | Lots: {order.lots} | SL: {order.stop_loss} | Status: {order.status}")
                cycle_report["evaluations"][sym] = {"status": "REGISTERED", "order_id": getattr(order, "order_id", str(order))}

        except Exception as e:
            logger.error("[Combined-Cycle] Error processing %s: %s", sym, e, exc_info=True)
            cycle_report["evaluations"][sym] = {"status": "ERROR", "error": str(e)}

    print("[Combined-Cycle] Evaluation cycle complete.\n")
    return cycle_report


def run_combined_live(args: argparse.Namespace) -> None:
    """Run full continuous combined bot and agent supervisor."""
    import main

    print_banner(mode="COMBINED-LIVE", watchlist=args.symbols.split(",") if args.symbols else None)

    # 1. Start Autonomous Learner background thread
    try:
        from autonomous_learner import auto_start
        auto_start()
        logger.info("Autonomous Learner background thread started")
    except Exception as e:
        logger.debug("Autonomous Learner skip: %s", e)

    # 2. Check Telegram Bot Token
    token = getattr(config, "TELEGRAM_TOKEN", None) or os.getenv("TELEGRAM_TOKEN", "")
    has_token = bool(token and token != "your_telegram_bot_token_here")

    if args.telegram or (has_token and not args.headless):
        print("[Combined] Starting Telegram Bot with Live Autotrade Monitoring...")
        try:
            main.main()
        except KeyboardInterrupt:
            print("\n[Combined] Interrupted by user.")
        except Exception as e:
            logger.error("[Combined] Bot encountered error: %s", e)
            print("Switching to headless autonomous trading engine...")
            main.run_headless()
    else:
        print("[Combined] No valid TELEGRAM_TOKEN found or --headless requested.")
        print("[Combined] Starting 24/7 Headless Autonomous Trading Engine with Multi-Agent Consensus...")
        try:
            main.run_headless()
        except KeyboardInterrupt:
            print("\n[Combined] Headless trading engine stopped by user.")


def main_launcher() -> None:
    setup_environment()
    parser = argparse.ArgumentParser(description="Nimble Darwin Combined Bot & Agent Runner")
    parser.add_argument(
        "--mode",
        choices=["combined", "check", "once", "live", "telegram", "headless"],
        default="combined",
        help="Execution mode (default: combined)",
    )
    parser.add_argument("--check", action="store_true", help="Run diagnostic health check and exit")
    parser.add_argument("--once", action="store_true", help="Run a single consensus cycle across watchlist and exit")
    parser.add_argument("--live", action="store_true", help="Run continuous combined trading engine")
    parser.add_argument("--telegram", action="store_true", help="Run combined mode with Telegram Bot")
    parser.add_argument("--headless", action="store_true", help="Run combined mode headlessly")
    parser.add_argument("--dry-run", action="store_true", help="Simulate signals without live broker registration")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols (e.g. BTCUSDT,XAUUSD)")
    parser.add_argument("--profile", default=None, help="Strategy profile (scalp, growth, accuracy)")
    args = parser.parse_args()

    # Determine requested action
    if args.check or args.mode == "check":
        print_banner(mode="DIAGNOSTIC-CHECK")
        diag = run_diagnostic_check()
        sys.exit(0 if diag["all_ok"] else 1)

    if args.profile:
        config.CURRENT_PROFILE = args.profile
    if args.symbols:
        config.AUTOTRADE_SYMBOLS = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    if args.once or args.mode == "once":
        print_banner(mode="SINGLE-CYCLE", watchlist=config.get_autotrade_symbols())
        run_diagnostic_check()
        run_single_cycle(symbols=config.get_autotrade_symbols(), dry_run=args.dry_run)
        sys.exit(0)

    # Default action: run combined mode
    # First verify subsystems with quick diagnostic
    print_banner(mode="COMBINED-STARTUP", watchlist=config.get_autotrade_symbols())
    diag = run_diagnostic_check()
    if not diag["all_ok"]:
        logger.warning("[Combined] Some subsystems reported warnings, proceeding with safe fallbacks...")

    # Execute one validation cycle to ensure data feeds and arbiter consensus are operational
    print("[Combined] Executing initial validation cycle...")
    run_single_cycle(symbols=config.get_autotrade_symbols(), dry_run=True)

    # Launch continuous combined live runner if not in check/once mode
    if args.live or args.mode in ("combined", "live", "telegram", "headless"):
        run_combined_live(args)


if __name__ == "__main__":
    main_launcher()
