#!/usr/bin/env python3
"""Apply production fixes in-place. Safe to run every boot."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def patch_file(path: Path, replacements: list) -> list:
    if not path.exists():
        return [f"missing {path.name}"]
    t = path.read_text(encoding="utf-8", errors="ignore")
    done = []
    for old, new in replacements:
        if old in t:
            t = t.replace(old, new, 1)
            done.append(f"applied:{old[:40]!r}")
        elif new.strip() and new in t:
            done.append(f"already:{old[:30]!r}")
        else:
            done.append(f"skip:{old[:30]!r}")
    path.write_text(t, encoding="utf-8")
    return done


def main() -> None:
    logs = []
    logs += patch_file(ROOT / "multi_agent_trading_bot.py", [
        ("MIN_AGREEMENT = 3", "MIN_AGREEMENT = 2"),
        ("MIN_SCORE = 1.8", "MIN_SCORE = 1.2"),
        (
            '        if _ImprovedDataFeed is not None and source not in ("demo",):\n',
            "        if _ImprovedDataFeed is not None:\n",
        ),
        (
            "        entry = float(df1[\"close\"].iloc[-1])\n"
            "        atr_val = atr(df1) or entry * 0.002\n"
            "        if direction == Dir.LONG:\n"
            "            stop = entry - atr_val * 1.2\n"
            "        else:\n"
            "            stop = entry + atr_val * 1.2\n",
            "        entry = float(df1[\"close\"].iloc[-1])\n"
            "        atr_val = atr(df1) or entry * 0.002\n"
            "        min_stop_dist = entry * 0.003\n"
            "        stop_dist = max(atr_val * 1.5, min_stop_dist)\n"
            "        if direction == Dir.LONG:\n"
            "            stop = entry - stop_dist\n"
            "        else:\n"
            "            stop = entry + stop_dist\n",
        ),
        (
            "    def _demo(self, limit: int = 600, base_price: float = 2650.0) -> pd.DataFrame:\n"
            "        dates = pd.date_range(end=datetime.now(timezone.utc), periods=limit, freq=\"1min\")\n"
            "        rng = np.random.default_rng(42)\n"
            "        rets = rng.normal(0, 0.3, limit)\n"
            "        p = base_price + np.cumsum(rets)\n"
            "        highs = p + rng.uniform(0.1, 1.5, limit)\n"
            "        lows = p - rng.uniform(0.1, 1.5, limit)\n",
            "    def _demo(self, limit: int = 600, base_price: float = None) -> pd.DataFrame:\n"
            "        if base_price is None:\n"
            "            base_price = 2650.0\n"
            "            try:\n"
            "                from realtime_prices import fetch_spot_price\n"
            "                rt = fetch_spot_price(self.symbol)\n"
            "                if rt.get(\"ok\") and float(rt.get(\"mark\") or 0) > 0:\n"
            "                    base_price = float(rt[\"mark\"])\n"
            "            except Exception:\n"
            "                sym = (self.symbol or \"\").upper()\n"
            "                if \"BTC\" in sym:\n"
            "                    base_price = 82000.0\n"
            "                elif \"XAU\" in sym or \"GOLD\" in sym:\n"
            "                    base_price = 4100.0\n"
            "        dates = pd.date_range(end=datetime.now(timezone.utc), periods=limit, freq=\"1min\")\n"
            "        rng = np.random.default_rng(42)\n"
            "        tick = max(base_price * 0.0002, 0.3)\n"
            "        rets = rng.normal(0, tick, limit)\n"
            "        p = base_price + np.cumsum(rets)\n"
            "        highs = p + rng.uniform(tick * 0.3, tick * 2, limit)\n"
            "        lows = p - rng.uniform(tick * 0.3, tick * 2, limit)\n",
        ),
        ("data = feed.multi_tf(limit_1m=700)", "data = feed.multi_tf(limit_1m=1500)"),
        ("data = feed.multi_tf(limit_1m=500)", "data = feed.multi_tf(limit_1m=1500)"),
        ("def multi_tf(self, limit_1m: int = 700)", "def multi_tf(self, limit_1m: int = 1500)"),
        ("def fetch(self, limit: int = 600", "def fetch(self, limit: int = 1500"),
    ])
    logs += patch_file(ROOT / "config.py", [
        ('DEFAULT_SYMBOL = os.getenv("BOT_SYMBOL", "XAUTUSD").upper()',
         'DEFAULT_SYMBOL = os.getenv("BOT_SYMBOL", "XAUUSD").upper()'),
        ('DEFAULT_DATA_SOURCE = os.getenv("BOT_DATA_SOURCE", "tradingview").lower()',
         'DEFAULT_DATA_SOURCE = os.getenv("BOT_DATA_SOURCE", "ccxt").lower()'),
        ('DEFAULT_DATA_SOURCE = os.getenv("BOT_DATA_SOURCE", "demo").lower()',
         'DEFAULT_DATA_SOURCE = os.getenv("BOT_DATA_SOURCE", "ccxt").lower()'),
        ('AUTOTRADE_ENABLED = os.getenv("BOT_AUTOTRADE", "false").lower() in ("true", "1", "yes", "on")',
         'AUTOTRADE_ENABLED = os.getenv("BOT_AUTOTRADE", "true").lower() in ("true", "1", "yes", "on")'),
    ])
    logs += patch_file(ROOT / "main.py", [
        (
            "engine = ExecutionEngine(risk_manager=risk_mgr)",
            "try:\n"
            "    from execution import create_broker\n"
            "    _broker = create_broker(\n"
            "        broker_mode=getattr(config, \"BROKER_MODE\", \"paper\"),\n"
            "        exchange_id=getattr(config, \"DEFAULT_EXCHANGE\", \"binance\"),\n"
            "        api_key=getattr(config, \"EXCHANGE_API_KEY\", \"\"),\n"
            "        api_secret=getattr(config, \"EXCHANGE_API_SECRET\", \"\"),\n"
            "        initial_balance=getattr(config, \"INITIAL_EQUITY\", 10000.0),\n"
            "    )\n"
            "except Exception as _broker_err:\n"
            "    print(\"[boot] broker:\", _broker_err)\n"
            "    _broker = None\n"
            "engine = ExecutionEngine(risk_manager=risk_mgr, broker=_broker)",
        ),
        (
            "async def live_monitoring_worker(app):\n"
            "    \"\"\"Background task that polls candles and tracks setups/positions.\"\"\"\n"
            "    feed = MarketDataFeed(symbol=config.DEFAULT_SYMBOL, source=config.DEFAULT_DATA_SOURCE)\n"
            "    symbol = config.DEFAULT_SYMBOL\n",
            "async def live_monitoring_worker(app):\n"
            "    \"\"\"Background task that polls candles and tracks setups/positions.\"\"\"\n"
            "    try:\n"
            "        from multi_agent_trading_bot import _norm_trade_symbol\n"
            "        symbol = _norm_trade_symbol(getattr(config, \"DEFAULT_SYMBOL\", \"XAUUSD\"))\n"
            "    except Exception:\n"
            "        symbol = str(getattr(config, \"DEFAULT_SYMBOL\", \"XAUUSD\")).upper()\n"
            "        if symbol in (\"XAUTUSD\", \"XAUT\", \"GOLD\", \"XAU\"):\n"
            "            symbol = \"XAUUSD\"\n"
            "        if symbol in (\"BTC\", \"BTCUSD\", \"XBT\"):\n"
            "            symbol = \"BTCUSDT\"\n"
            "    feed = MarketDataFeed(symbol=symbol, source=config.DEFAULT_DATA_SOURCE)\n",
        ),
    ])
    logs += patch_file(ROOT / "risk_manager.py", [
        (
            "        if symbol:\n"
            "            self.set_symbol(symbol)\n"
            "        if equity is not None:\n"
            "            self.state.equity = equity\n"
            "            self.state.peak_equity = max(self.state.peak_equity, equity)\n",
            "        if isinstance(equity, str) and symbol is None:\n"
            "            symbol, equity = equity, None\n"
            "        if symbol:\n"
            "            self.set_symbol(symbol)\n"
            "        if equity is not None:\n"
            "            try:\n"
            "                equity = float(equity)\n"
            "            except (TypeError, ValueError):\n"
            "                equity = None\n"
            "        if equity is not None:\n"
            "            self.state.equity = equity\n"
            "            try:\n"
            "                self.state.peak_equity = max(float(self.state.peak_equity or 0), float(equity))\n"
            "            except (TypeError, ValueError):\n"
            "                self.state.peak_equity = float(equity)\n",
        ),
    ])
    logs += patch_file(ROOT / "execution_engine.py", [
        (
            "            if tp1_just_hit or just_executed or stop_tightened_in_candle:\n"
            "                # Check if original stop was hit, or if price subsequently fell back to the new tightened stop\n"
            "                if is_long:\n"
            "                    stop_hit = (low <= old_stop) or (curr_price <= pos.stop_loss)\n"
            "                else:\n"
            "                    stop_hit = (high >= old_stop) or (curr_price >= pos.stop_loss)\n",
            "            if just_executed:\n"
            "                # Entry candle range must not force an instant stop-out after paper fill\n"
            "                stop_hit = (curr_price <= pos.stop_loss) if is_long else (curr_price >= pos.stop_loss)\n"
            "            elif tp1_just_hit or stop_tightened_in_candle:\n"
            "                if is_long:\n"
            "                    stop_hit = (low <= old_stop) or (curr_price <= pos.stop_loss)\n"
            "                else:\n"
            "                    stop_hit = (high >= old_stop) or (curr_price >= pos.stop_loss)\n",
        ),
        (
            'f"PAPER TRADE CONFIRMED: {setup.symbol} {setup.direction} at {exec_price:.4f}. "\n'
            '                                        "Live execution is blocked until exchange-side protection can be verified."',
            '(\n'
            '                                f"✅ **PAPER TRADE CONFIRMED**\\n"\n'
            '                                f"`{setup.symbol}` **{setup.direction}** @ `${exec_price:,.4f}`\\n"\n'
            '                                f"SL `${setup.stop_loss:,.2f}` | TP1 `${setup.tp1:,.2f}` | Lots `{setup.lots}`\\n"\n'
            '                                f"_Paper mode — no real funds risked._"\n'
            '                            )',
        ),
        (
            'self.execution_halted = True\n'
            '                        setup.status = OrderStatus.CANCELLED\n'
            '                        setup.close_reason = f"Execution halted; broker state requires reconciliation: {ex}"',
            'try:\n'
            '                            import config as _cfg_halt\n'
            '                            _hm = str(getattr(_cfg_halt, "BROKER_MODE", "paper")).lower()\n'
            '                        except Exception:\n'
            '                            _hm = "paper"\n'
            '                        if _hm == "live":\n'
            '                            self.execution_halted = True\n'
            '                        setup.status = OrderStatus.CANCELLED\n'
            '                        setup.close_reason = f"Execution failed: {ex}"',
        ),
    ])
    print("[wire_production_fixes]", "; ".join(logs))


if __name__ == "__main__":
    main()
