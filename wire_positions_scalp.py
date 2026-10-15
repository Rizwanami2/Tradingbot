#!/usr/bin/env python3
"""Paper equity $1000, rich /positions, /close all, fast scalp auto-close."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    cfg = ROOT / "config.py"
    if cfg.exists():
        t = cfg.read_text(encoding="utf-8", errors="ignore")
        changed = False
        if '_equity_default = 100.0 if BROKER_MODE' in t:
            t = t.replace(
                '_equity_default = 100.0 if BROKER_MODE == "paper" else 10000.0',
                '_equity_default = 1000.0 if BROKER_MODE == "paper" else 10000.0',
            )
            changed = True
        if "SCALP_TAKE_PROFIT_USD" not in t:
            t += (
                "\nSCALP_TAKE_PROFIT_USD = _safe_float(os.getenv(\"BOT_SCALP_TP_USD\"), 5.0)\n"
                "SCALP_TAKE_PROFIT_PCT = _safe_float(os.getenv(\"BOT_SCALP_TP_PCT\"), 0.15)\n"
                "SCALP_ENABLED = os.getenv(\"BOT_SCALP\", \"true\").lower() in (\"true\", \"1\", \"yes\", \"on\")\n"
            )
            changed = True
        if changed:
            cfg.write_text(t, encoding="utf-8")
            print("[wire_pos] config $1000 + scalp")
        else:
            print("[wire_pos] config OK")

    ee = ROOT / "execution_engine.py"
    if ee.exists():
        t = ee.read_text(encoding="utf-8", errors="ignore")
        if "def close_all_positions" not in t and "def get_portfolio_summary" in t:
            helper = (
                "\n    def close_all_positions(self, reason: str = \"Close all\") -> list:\n"
                "        closed = []\n"
                "        for sym in list(self.active_positions.keys()):\n"
                "            try:\n"
                "                pos = self.close_position(sym, reason=reason)\n"
                "                if pos is not None:\n"
                "                    closed.append(pos)\n"
                "            except Exception as ex:\n"
                "                print(f\"[ExecutionEngine] close_all {sym}: {ex}\")\n"
                "        return closed\n\n"
                "    def format_positions_detail(self) -> str:\n"
                "        lines = [\"📋 **Open Positions**\"]\n"
                "        if not self.active_positions:\n"
                "            lines.append(\"_No open positions._\")\n"
                "        else:\n"
                "            for sym, pos in self.active_positions.items():\n"
                "                entry = float(pos.executed_price or pos.entry_level or 0)\n"
                "                u = float(getattr(pos, \"unrealized_pnl\", 0) or 0)\n"
                "                lines.append(\n"
                "                    f\"\\n`{sym}` **{pos.direction}**\\n\"\n"
                "                    f\"• Entry `${entry:,.2f}` | Mark PnL `${u:+,.2f}`\\n\"\n"
                "                    f\"• SL `${float(pos.stop_loss):,.2f}` | TP1 `${float(pos.tp1):,.2f}` | TP2 `${float(pos.tp2):,.2f}`\\n\"\n"
                "                    f\"• Lots `{pos.lots}` | Lev `{float(pos.leverage or 1):.0f}x` | Margin `${float(pos.margin_required or 0):,.2f}`\"\n"
                "                )\n"
                "        summary = self.get_portfolio_summary()\n"
                "        lines.append(\n"
                "            f\"\\n💰 Equity `${summary.get('equity', 0):,.2f}` | \"\n"
                "            f\"Unrealized `${summary.get('unrealized_pnl', 0):+,.2f}` | \"\n"
                "            f\"Open `{summary.get('active_positions_count', 0)}`\"\n"
                "        )\n"
                "        return \"\\n\".join(lines)\n\n"
            )
            t = t.replace("    def get_portfolio_summary(self)", helper + "    def get_portfolio_summary(self)", 1)
            ee.write_text(t, encoding="utf-8")
            print("[wire_pos] close_all + format_positions")
        else:
            print("[wire_pos] execution_engine methods OK")

        t = ee.read_text(encoding="utf-8", errors="ignore")
        if "SCALP AUTO-CLOSE" not in t and "Calculate live unrealized PnL" in t:
            needle = (
                "            # Calculate live unrealized PnL\n"
                "            if is_long:\n"
                "                pos.unrealized_pnl = (curr_price - entry) * pos.remaining_size\n"
                "            else:\n"
                "                pos.unrealized_pnl = (entry - curr_price) * pos.remaining_size\n"
            )
            scalp = needle + (
                "\n"
                "            try:\n"
                "                import config as _cfg_sc\n"
                "                scalp_on = bool(getattr(_cfg_sc, \"SCALP_ENABLED\", True))\n"
                "                scalp_usd = float(getattr(_cfg_sc, \"SCALP_TAKE_PROFIT_USD\", 5.0) or 0)\n"
                "                scalp_pct = float(getattr(_cfg_sc, \"SCALP_TAKE_PROFIT_PCT\", 0.15) or 0)\n"
                "            except Exception:\n"
                "                scalp_on, scalp_usd, scalp_pct = True, 5.0, 0.15\n"
                "            if scalp_on and pos.unrealized_pnl > 0:\n"
                "                hit_pct = False\n"
                "                if scalp_pct > 0 and entry > 0:\n"
                "                    move_pct = abs(curr_price - entry) / entry * 100.0\n"
                "                    hit_pct = move_pct >= scalp_pct\n"
                "                hit_usd = scalp_usd > 0 and pos.unrealized_pnl >= scalp_usd\n"
                "                if hit_usd or hit_pct:\n"
                "                    pos.exit_price = curr_price\n"
                "                    pos.realized_pnl = pos.unrealized_pnl\n"
                "                    pos.status = OrderStatus.CLOSED\n"
                "                    pos.closed_at = datetime.now(timezone.utc).isoformat()\n"
                "                    pos.close_reason = f\"Scalp TP (${pos.realized_pnl:+.2f})\"\n"
                "                    try:\n"
                "                        self.risk_manager.update_after_trade(pos.realized_pnl, r_multiple=0.0)\n"
                "                    except Exception:\n"
                "                        pass\n"
                "                    self.trade_history.append(pos)\n"
                "                    del self.active_positions[sym]\n"
                "                    if getattr(self, \"broker\", None):\n"
                "                        try:\n"
                "                            self.broker.close_position(sym, price=curr_price)\n"
                "                        except Exception:\n"
                "                            pass\n"
                "                    msg = (\n"
                "                        f\"⚡ **SCALP AUTO-CLOSE** `{sym}`\\n\"\n"
                "                        f\"Exit `${curr_price:,.2f}` | PnL `${pos.realized_pnl:+,.2f}`\\n\"\n"
                "                        f\"_Fast scalp profit target hit_\"\n"
                "                    )\n"
                "                    events.append({\"event\": \"SCALP_CLOSE\", \"order\": pos, \"price\": curr_price, \"message\": msg})\n"
                "                    self._dispatch_event(\"SCALP_CLOSE\", pos, msg)\n"
                "                    return events\n"
            )
            if needle in t:
                t = t.replace(needle, scalp, 1)
                ee.write_text(t, encoding="utf-8")
                print("[wire_pos] scalp auto-close")

    main_py = ROOT / "main.py"
    if main_py.exists():
        t = main_py.read_text(encoding="utf-8", errors="ignore")
        if "format_positions_detail" not in t and "async def positions_command" in t:
            i = t.find("async def positions_command")
            k = t.find("async def cancel_command")
            if i >= 0 and k > i:
                new_block = (
                    "async def positions_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):\n"
                    "    try:\n"
                    "        if hasattr(engine, \"format_positions_detail\"):\n"
                    "            msg = engine.format_positions_detail()\n"
                    "        else:\n"
                    "            summary = engine.get_portfolio_summary()\n"
                    "            msg = f\"📋 Positions open={summary.get('active_positions_count', 0)}\"\n"
                    "        try:\n"
                    "            await update.message.reply_text(msg, parse_mode=\"Markdown\")\n"
                    "        except Exception:\n"
                    "            await update.message.reply_text(msg.replace(\"*\", \"\").replace(\"`\", \"\"))\n"
                    "    except Exception as e:\n"
                    "        await update.message.reply_text(f\"Positions error: {e}\")\n\n\n"
                    "async def close_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):\n"
                    "    arg = (ctx.args[0] if ctx.args else \"\").strip().upper()\n"
                    "    if not arg or arg in (\"ALL\", \"CLOSED\", \"*\"):\n"
                    "        if not engine.active_positions:\n"
                    "            await update.message.reply_text(\"No open positions to close.\")\n"
                    "            return\n"
                    "        closed = []\n"
                    "        if hasattr(engine, \"close_all_positions\"):\n"
                    "            closed = engine.close_all_positions(reason=\"Manual close all\")\n"
                    "        else:\n"
                    "            for sym in list(engine.active_positions.keys()):\n"
                    "                p = engine.close_position(sym, reason=\"Manual close all\")\n"
                    "                if p:\n"
                    "                    closed.append(p)\n"
                    "        lines = [f\"✅ Closed {len(closed)} position(s)\"]\n"
                    "        for p in closed:\n"
                    "            lines.append(f\"{p.symbol} PnL ${float(getattr(p, 'realized_pnl', 0) or 0):+.2f}\")\n"
                    "        await update.message.reply_text(\"\\n\".join(lines))\n"
                    "        return\n"
                    "    sym = arg if arg not in (\"BTC\", \"BTCUSD\") else \"BTCUSDT\"\n"
                    "    closed = engine.close_position(sym, reason=\"Manual close\")\n"
                    "    if closed:\n"
                    "        await update.message.reply_text(f\"✅ Closed {sym} PnL ${float(getattr(closed, 'realized_pnl', 0) or 0):+.2f}\")\n"
                    "    else:\n"
                    "        await update.message.reply_text(f\"No position for {sym}. Use /close all\")\n\n\n"
                )
                t = t[:i] + new_block + t[k:]
                main_py.write_text(t, encoding="utf-8")
                print("[wire_pos] main positions/close")
        t = main_py.read_text(encoding="utf-8", errors="ignore")
        if 'CommandHandler("closeall"' not in t and 'CommandHandler("close"' in t:
            t = t.replace(
                'CommandHandler("close", close_command)',
                'CommandHandler("close", close_command))\n'
                '    app.add_handler(CommandHandler("closeall", close_command))\n'
                '    app.add_handler(CommandHandler("closed", close_command)',
                1,
            )
            main_py.write_text(t, encoding="utf-8")
            print("[wire_pos] closeall alias")
    print("[wire_pos] done")


if __name__ == "__main__":
    main()
