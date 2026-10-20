#!/usr/bin/env python3
"""Ensure /signal and /asignal use robust signal_pipeline."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    main_py = ROOT / "main.py"
    if not main_py.exists():
        print("[wire_signal] main missing")
        return
    t = main_py.read_text(encoding="utf-8", errors="ignore")
    changed = False

    if "from signal_pipeline import run_signal" not in t:
        marker = "async def signal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):"
        if marker in t:
            i = t.find(marker)
            j = t.find("\nasync def ", i + 10)
            if j < 0:
                j = t.find("\ndef ", i + 10)
            if j > i:
                new_cmd = (
                    "async def signal_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):\n"
                    '    """High-reliability /signal — leverage + paper micro size safe."""\n'
                    "    raw = (ctx.args[0] if ctx.args else config.DEFAULT_SYMBOL)\n"
                    "    try:\n"
                    "        from signal_pipeline import run_signal, format_signal_message, try_paper_execute, _norm_symbol\n"
                    "        sym = _norm_symbol(raw)\n"
                    "        await update.message.reply_text(\n"
                    '            f"⏳ Evaluating **{sym}** (lev `{risk_mgr.get_leverage():.0f}x`)...",\n'
                    '            parse_mode="Markdown",\n'
                    "        )\n"
                    "        res = run_signal(\n"
                    "            sym,\n"
                    '            equity=float(getattr(risk_mgr.state, "equity", None) or config.INITIAL_EQUITY),\n'
                    "            leverage=float(risk_mgr.get_leverage()),\n"
                    "            source=config.DEFAULT_DATA_SOURCE,\n"
                    "        )\n"
                    "        msg = format_signal_message(res)\n"
                    "        try:\n"
                    '            await update.message.reply_text(msg, parse_mode="Markdown")\n'
                    "        except Exception:\n"
                    '            await update.message.reply_text(msg.replace("*", "").replace("`", ""))\n'
                    "        sig = res.get(\"signal\")\n"
                    "        if sig and autotrade_active:\n"
                    "            ok, note = try_paper_execute(engine, sig, candle_price=res.get(\"price\") or sig.get(\"entry\"))\n"
                    '            tag = "✅" if ok else "⚠️"\n'
                    "            try:\n"
                    '                await update.message.reply_text(f"{tag} Autotrade: {note}", parse_mode="Markdown")\n'
                    "            except Exception:\n"
                    '                await update.message.reply_text(f"{tag} Autotrade: {note}")\n'
                    "    except Exception as e:\n"
                    '        logger.exception("signal_command failed")\n'
                    '        await update.message.reply_text(f"Signal error: `{type(e).__name__}: {e}`", parse_mode="Markdown")\n'
                    "\n\n"
                )
                t = t[:i] + new_cmd + t[j:]
                changed = True
                print("[wire_signal] signal_command replaced")

    # Fix NameError: must use app not application
    if "application.add_handler" in t:
        t = t.replace("application.add_handler", "app.add_handler")
        changed = True
        print("[wire_signal] fixed application->app")

    if 'CommandHandler("asignal"' not in t and 'CommandHandler("signal"' in t:
        t = t.replace(
            'CommandHandler("signal", signal_command)',
            'CommandHandler("signal", signal_command))\n    app.add_handler(CommandHandler("asignal", signal_command)',
            1,
        )
        changed = True
        print("[wire_signal] asignal alias")

    if changed:
        main_py.write_text(t, encoding="utf-8")
        print("[wire_signal] main.py updated")
    else:
        print("[wire_signal] main already OK")
    print("[wire_signal] done")


if __name__ == "__main__":
    main()
