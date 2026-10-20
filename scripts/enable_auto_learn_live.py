#!/usr/bin/env python3
"""Enable auto-learn on close, Telegram analysis, journal, optional learn veto."""
from pathlib import Path
import re
import ast

NEW_CLOSE = '''def _ai_on_close(symbol: str, fpnl: float, ev: dict = None) -> str:
    """Auto-learn on every closed trade + return short live analysis for Telegram."""
    ev = ev or {}
    r_mult = float(ev.get("r_multiple", ev.get("r", 0.0)) or 0.0)
    if r_mult == 0.0 and fpnl != 0.0:
        r_mult = 1.0 if fpnl > 0 else -1.0
    won = fpnl >= 0
    pos = ev.get("order") if ev else None
    agents = list(getattr(pos, "agents", []) or []) if pos else []
    regime = str(getattr(pos, "regime", "") or "") if pos else ""
    session = str(getattr(pos, "session", "") or "") if pos else ""
    direction = str(getattr(pos, "direction", "") or "") if pos else ""
    setup = f"{direction.lower()}|{symbol.lower()}" if direction else f"live|{symbol.lower()}"
    conf = float(getattr(pos, "conviction", 0.6) or 0.6) if pos else 0.6
    try:
        from ai_advanced_suite import get_ai_suite
        get_ai_suite().on_trade_closed(symbol=symbol, won=won, r_multiple=r_mult)
    except Exception as e:
        logger.debug("suite on_trade_closed: %s", e)
    live_n = 0
    banned = ""
    try:
        from autonomous_learner import get_brain
        brain = get_brain(auto_bootstrap=False)
        brain.on_trade_closed(
            r_multiple=r_mult, won=won, setup=setup, regime=regime,
            session=session, agents=agents, conf=conf,
        )
        live_n = int(getattr(getattr(brain, "knowledge", None), "total_live_trades", 0) or 0)
        if setup in getattr(brain.knowledge, "banned_setups", {}):
            banned = str(brain.knowledge.banned_setups.get(setup, ""))
    except Exception as e:
        logger.debug("brain on_trade_closed: %s", e)
    try:
        import csv
        from pathlib import Path as _P
        from datetime import datetime, timezone
        jpath = _P("data") / "live_trade_journal.csv"
        jpath.parent.mkdir(parents=True, exist_ok=True)
        new_file = not jpath.exists()
        with jpath.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["ts", "symbol", "direction", "pnl", "r", "won", "setup", "regime", "session", "agents"])
            w.writerow([
                datetime.now(timezone.utc).isoformat(), symbol, direction,
                round(fpnl, 4), round(r_mult, 4), int(won), setup, regime, session,
                "|".join(str(a) for a in agents),
            ])
    except Exception as e:
        logger.debug("journal write: %s", e)
    emoji = "✅" if won else "❌"
    lines = [
        f"{emoji} *Learn+Close* `{symbol}` {direction}",
        f"PnL: `{fpnl:+.2f}` · R: `{r_mult:+.2f}` · Live trades: `{live_n}`",
        f"Setup: `{setup}` · Regime: `{regime or '—'}`",
    ]
    if agents:
        lines.append("Agents: `" + ", ".join(str(a) for a in agents[:6]) + "`")
    if banned:
        lines.append(f"⚠️ Setup banned: `{banned}`")
    lines.append("_Weights & filters updated from this close_")
    return "\\n".join(lines)
'''

def main():
    p = Path("autotrade_worker.py")
    t = p.read_text(encoding="utf-8")
    if "Learn+Close" in t and "LEARN VETO" in t:
        print("Already enabled")
        return

    t2 = re.sub(
        r"def _ai_on_close\(symbol: str, fpnl: float, ev: dict = None\).*?(?=\nasync def live_monitoring_worker|\ndef _ai_on_open|\nasync def )",
        NEW_CLOSE + "\n\n",
        t,
        count=1,
        flags=re.S,
    )
    if t2 == t:
        t2 = re.sub(
            r"def _ai_on_close\(symbol: str, fpnl: float, ev: dict = None\).*?(?=\n\nasync def live_monitoring_worker)",
            NEW_CLOSE + "\n\n",
            t,
            count=1,
            flags=re.S,
        )
    t = t2

    # Wire Telegram after close calls
    if "Learn+Close" in t or "_analysis = _ai_on_close" not in t:
        t = t.replace(
            "_ai_on_close(symbol, fpnl, ev)",
            "_analysis = _ai_on_close(symbol, fpnl, ev)\n                            if _analysis:\n                                await _send_alert(_analysis)",
        )

    if "LEARN VETO" not in t and "_, _, size_mult = brain.advise_signal" in t:
        t = t.replace(
            "_, _, size_mult = brain.advise_signal(",
            "_ok_brain, _brain_why, size_mult = brain.advise_signal(",
            1,
        )
        # insert veto after advise call closing - fragile; user may need manual
        marker = "profile=current_profile_name,\n                                )"
        insert = (
            marker
            + """\n                                _live_veto = str(__import__("os").getenv("BOT_LEARN_LIVE_VETO", "true")).lower() in ("true", "1", "yes", "on")\n                                if not _ok_brain and _live_veto:\n                                    logger.info("Autotrade %s: LEARN VETO — %s", symbol, _brain_why)\n                                    monitoring_health[f"{symbol}_eval"] = f"Learn veto: {_brain_why}"\n                                    try:\n                                        if _should_notify_veto(symbol, f"learn:{str(_brain_why)[:80]}"):\n                                            await _send_alert(f"🧠 *Learn veto* `{symbol}`\\n`{_brain_why}`")\n                                    except Exception:\n                                        pass\n                                    continue\n"""
        )
        if marker in t:
            t = t.replace(marker, insert, 1)

    if "def _should_notify_veto" not in t:
        t = t.replace(
            "_last_ai_veto: Dict[str, Tuple[str, float]] = {}",
            '_last_ai_veto: Dict[str, Tuple[str, float]] = {}\n\n\ndef _should_notify_veto(symbol: str, reason_key: str, cooldown_sec: float = 120.0) -> bool:\n    now = time.time()\n    prev = _last_ai_veto.get(symbol)\n    if prev and prev[0] == reason_key and (now - prev[1]) < cooldown_sec:\n        return False\n    _last_ai_veto[symbol] = (reason_key, now)\n    return True\n',
            1,
        )

    p.write_text(t, encoding="utf-8")
    ast.parse(t)
    print("DONE: auto-learn live enabled")

if __name__ == "__main__":
    main()
