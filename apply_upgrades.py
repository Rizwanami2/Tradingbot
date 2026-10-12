#!/usr/bin/env python3
"""
Apply operational upgrades 1, 2, 4, 5 in one shot:
  1) Register advanced Telegram commands in main.py
  2) Structure trailing inside execution_engine.py
  4) Prefer real market data for backtests
  5) Replace /backtest with auto full suite

Run from repo root:
    python apply_upgrades.py
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
MAIN = ROOT / "main.py"
EXEC = ROOT / "execution_engine.py"
ADV_BT = ROOT / "advanced_backtest.py"


NEW_BACKTEST_COMMAND = '''
async def backtest_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Full auto backtest suite (single + multiagent + walk-forward + Monte Carlo + sweep)."""
    args = list(ctx.args or [])
    bars = 2000
    symbol = config.DEFAULT_SYMBOL.upper()
    if args:
        try:
            bars = int(args[0])
        except (TypeError, ValueError):
            await update.message.reply_text("Usage: /backtest [bars] [SYMBOL]")
            return
    if not 100 <= bars <= 10000:
        await update.message.reply_text("Choose between 100 and 10,000 candles.")
        return
    if len(args) > 1:
        symbol = args[1].upper()

    await update.message.reply_text(
        f"⏳ **Auto backtest suite** — `{symbol}` — {bars} bars\\n"
        f"single + multiagent + walk-forward + Monte Carlo + sweep…",
        parse_mode="Markdown",
    )

    def _run():
        from auto_backtest import run_auto_backtest
        return run_auto_backtest(
            symbol=symbol,
            bars=bars,
            source=getattr(config, "DEFAULT_DATA_SOURCE", "ccxt"),
            equity=getattr(config, "INITIAL_EQUITY", 10000.0),
            risk=max(0.001, getattr(config, "DEFAULT_RISK_PCT", 0.18) / 100.0),
            mc_runs=300,
            wf_windows=4,
            do_sweep=True,
        )

    try:
        report = await asyncio.to_thread(_run)
        text = report.text(max_len=3800)
        await update.message.reply_text(f"```\\n{text}\\n```", parse_mode="Markdown")
    except Exception as exc:
        logger.warning("Auto backtest failed for %s: %s", symbol, exc)
        await update.message.reply_text(
            f"Backtest failed: `{type(exc).__name__}: {exc}`\\n"
            f"Tip: set a real data source or provide CSV; synthetic data is last resort.",
            parse_mode="Markdown",
        )
'''


STRUCTURE_TRAIL_SNIPPET = '''
            # (C2) Structure-based trailing after TP1
            if pos.partials_done >= 1 and sym in self.active_positions:
                try:
                    from execution_enhancements import apply_structure_trail
                    atr_proxy = abs((pos.executed_price or pos.entry_level) - pos.stop_loss) / 1.2
                    # Build minimal OHLC window from recent highs/lows tracked on the position
                    import pandas as _pd
                    _df = _pd.DataFrame({
                        "high": [pos.highest_price, high],
                        "low": [pos.lowest_price, low],
                        "close": [curr_price, curr_price],
                        "open": [curr_price, curr_price],
                    })
                    # Prefer external candle history if caller attached it
                    hist = candle.get("_history") if isinstance(candle, dict) else None
                    if hist is not None and len(hist) >= 20:
                        _df = hist
                    trail_ev = apply_structure_trail(pos, _df, atr_value=float(atr_proxy or 0))
                    if trail_ev:
                        events.append(trail_ev)
                        self._dispatch_event(trail_ev["event"], pos, trail_ev["message"])
                except Exception as _trail_ex:
                    pass
'''


def patch_main() -> bool:
    if not MAIN.exists():
        print("main.py not found")
        return False
    text = MAIN.read_text(encoding="utf-8")
    changed = False

    # 1) Register advanced commands
    if "register_all_advanced" not in text and "telegram_advanced" not in text:
        anchor = "app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message))"
        inject = (
            "    try:\n"
            "        from telegram_advanced import register_all_advanced\n"
            "        register_all_advanced(app)\n"
            "        print(\"Advanced commands registered\")\n"
            "    except Exception as _adv_err:\n"
            "        print(f\"[main] Advanced commands not loaded: {_adv_err}\")\n\n"
            f"    {anchor}"
        )
        if anchor in text:
            text = text.replace(anchor, inject, 1)
            changed = True
            print("[1] Registered advanced Telegram commands")
        else:
            print("[1] Could not find MessageHandler anchor")
    else:
        print("[1] Advanced commands already referenced")

    # 5) Replace /backtest with auto suite
    if "Auto backtest suite" not in text:
        pattern = r"async def backtest_command\(update: Update, ctx: ContextTypes\.DEFAULT_TYPE\):.*?(?=\nasync def |\ndef main\()"
        new_fn = NEW_BACKTEST_COMMAND.strip() + "\n\n\n"
        text2, n = re.subn(pattern, lambda _: new_fn, text, count=1, flags=re.S)
        if n:
            text = text2
            changed = True
            print("[5] Replaced /backtest with auto full suite")
        else:
            print("[5] Could not locate backtest_command to replace")
    else:
        print("[5] /backtest already auto suite")

    # Docstring hint
    if "/backtest" in text and "auto suite" not in text[:3500]:
        text = text.replace(
            "  /backtest [bars]  - Run backtest on demand and return report",
            "  /backtest [bars] [SYMBOL] - Auto full suite (WF + MC + sweep)",
            1,
        )
        changed = True

    if changed:
        MAIN.write_text(text, encoding="utf-8")
        print("main.py updated")
    return changed


def patch_execution() -> bool:
    if not EXEC.exists():
        print("execution_engine.py not found")
        return False
    text = EXEC.read_text(encoding="utf-8")
    if "Structure-based trailing after TP1" in text or "apply_structure_trail" in text:
        print("[2] Structure trailing already wired")
        return False

    # Insert after TP1 block — find TP2 section marker
    marker = "# (D) Check TP2"
    if marker not in text:
        # alternate
        marker = "partials_done == 1 and pos.tp2"
        if marker not in text:
            print("[2] Could not find TP2 marker for insertion")
            return False
        # insert before this line
        idx = text.find("# (D) Check TP2") if "# (D) Check TP2" in text else text.find(marker)
        # find line start
        line_start = text.rfind("\n", 0, idx) + 1
        text = text[:line_start] + STRUCTURE_TRAIL_SNIPPET + "\n" + text[line_start:]
    else:
        text = text.replace(marker, STRUCTURE_TRAIL_SNIPPET + "\n" + marker, 1)

    EXEC.write_text(text, encoding="utf-8")
    print("[2] Structure trailing inserted into execution_engine.py")
    return True


def patch_load_data() -> bool:
    """Prefer real data sources before synthetic (upgrade 4)."""
    if not ADV_BT.exists():
        print("advanced_backtest.py not found")
        return False
    text = ADV_BT.read_text(encoding="utf-8")
    if "REAL DATA FIRST" in text:
        print("[4] Real-data preference already present")
        return False

    new_loader = '''
def load_data(symbol: str, bars: int, source: str, csv: Optional[str]) -> pd.DataFrame:
    """REAL DATA FIRST — CSV → CCXT/data_feed → TradingView/multi-agent → synthetic last."""
    # 1) Explicit CSV
    if csv:
        df = pd.read_csv(csv)
        df.columns = [c.lower() for c in df.columns]
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp")
        elif not isinstance(df.index, pd.DatetimeIndex):
            for col in ("datetime", "date", "time"):
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], utc=True)
                    df = df.set_index(col)
                    break
        return df.tail(bars)

    errors = []

    # 2) CCXT / data_feed (real exchange history)
    try:
        from data_feed import DataFeed
        import config as _cfg
        feed = DataFeed(
            symbol=symbol,
            source="ccxt",
            exchange_id=getattr(_cfg, "DEFAULT_EXCHANGE", "binance"),
            timeframe="1m",
        )
        df = feed.fetch(limit=bars, timeframe="1m")
        if df is not None and not df.empty and len(df) >= min(100, bars // 2):
            print(f"[AdvancedBacktest] Real data via data_feed/ccxt: {len(df)} bars")
            return df.tail(bars)
    except Exception as e:
        errors.append(f"ccxt:{e}")

    # 3) Requested source if not already ccxt
    if source and source.lower() not in ("demo", "synthetic", "ccxt"):
        try:
            from data_feed import DataFeed
            feed = DataFeed(symbol=symbol, source=source)
            df = feed.fetch(limit=bars)
            if df is not None and not df.empty:
                print(f"[AdvancedBacktest] Real data via source={source}: {len(df)} bars")
                return df.tail(bars)
        except Exception as e:
            errors.append(f"source:{e}")

    # 4) Multi-agent / TradingView path
    try:
        from multi_agent_trading_bot import MarketDataFeed
        feed = MarketDataFeed(symbol=symbol, source=source if source not in ("demo", "synthetic") else "tradingview")
        df = feed.fetch(limit=bars)
        if df is not None and not getattr(df, "empty", True):
            print(f"[AdvancedBacktest] Data via MarketDataFeed: {len(df)} bars")
            return df.tail(bars)
    except Exception as e:
        errors.append(f"tv:{e}")

    # 5) Synthetic last resort (research only)
    print(f"[AdvancedBacktest] WARNING: real data unavailable ({'; '.join(errors) or 'none'}); using synthetic")
    dates = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=bars, freq="1min")
    import numpy as np
    returns = np.random.randn(bars) * 0.3
    p = 2650 + np.cumsum(returns)
    return pd.DataFrame({
        "open": p, "high": p + np.random.uniform(0.1, 1.5, bars),
        "low": p - np.random.uniform(0.1, 1.5, bars),
        "close": p + np.random.randn(bars) * 0.1,
        "volume": np.random.randint(50, 1000, bars),
    }, index=dates)
'''

    pattern = r"def load_data\(symbol: str, bars: int, source: str, csv: Optional\[str\]\) -> pd\.DataFrame:.*?(?=\n\ndef main\()"
    text2, n = re.subn(pattern, new_loader.strip() + "\n\n\n", text, count=1, flags=re.S)
    if not n:
        print("[4] Could not replace load_data")
        return False
    ADV_BT.write_text(text2, encoding="utf-8")
    print("[4] load_data now prefers real market data")
    return True


def main():
    print("Applying upgrades 1, 2, 4, 5…")
    a = patch_main()
    b = patch_execution()
    c = patch_load_data()
    if a or b or c:
        print("\nDone. Restart the bot: python main.py")
    else:
        print("\nNo file changes (already applied or anchors missing).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
