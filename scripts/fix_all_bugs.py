#!/usr/bin/env python3
"""Fix critical autotrade bugs: AI gate, learn-on-close, risk vetoes, cooldowns."""
from pathlib import Path
import re
import ast

def fix_autotrade():
    p = Path("autotrade_worker.py")
    t = p.read_text(encoding="utf-8")
    if "evaluate_signal_dict" in t and "Learn+Close" in t and "RISK VETO" in t:
        print("autotrade_worker: already fixed")
        return

    if "import os" not in t[:800]:
        t = t.replace("import asyncio\n", "import asyncio\nimport os\n", 1)

    stub = (
        'def _ai_gate_signal(sig: dict, symbol: str, profile: str, dname: str = "", agree: int = 0, conv_val: float = 0.6):\n'
        '    """\n'
        '    Pure Multi-Agent & Brain mode:\n'
        '    Secondary AI gating and vetoing are disabled; consensus signals flow cleanly.\n'
        '    """\n'
        '    return True, sig\n'
    )
    gate = '''def _should_notify_veto(symbol: str, reason_key: str, cooldown_sec: float = 120.0) -> bool:
    now = time.time()
    prev = _last_ai_veto.get(symbol)
    if prev and prev[0] == reason_key and (now - prev[1]) < cooldown_sec:
        return False
    _last_ai_veto[symbol] = (reason_key, now)
    return True


def _ai_gate_signal(sig: dict, symbol: str, profile: str, dname: str = "", agree: int = 0, conv_val: float = 0.6):
    """AI Advanced Suite hard gates. Returns (allowed, sig, reasons)."""
    try:
        from ai_advanced_suite import evaluate_signal_dict, get_ai_suite
        suite = get_ai_suite()
        if not getattr(suite, "enabled", True):
            return True, sig, []
        gate = evaluate_signal_dict(sig, profile=str(profile or "scalp"))
        reasons = list(getattr(gate, "reasons", None) or [])
        if not getattr(gate, "allowed", True):
            return False, sig, reasons
        try:
            sm = float(getattr(gate, "size_mult", 1.0) or 1.0)
            if sm > 0 and sm != 1.0:
                sig = dict(sig)
                sig["ai_size_mult"] = sm
        except Exception:
            pass
        if hasattr(gate, "scorecard"):
            sig = dict(sig)
            sig["ai_scorecard"] = getattr(gate, "scorecard", None)
        return True, sig, reasons
    except Exception as e:
        logger.debug("AI suite gate error (fail-open): %s", e)
        return True, sig, []
'''
    if stub in t:
        t = t.replace(stub, gate, 1)
        print("AI gate restored")

    if "Learn+Close" not in t:
        new_close = '''def _ai_on_close(symbol: str, fpnl: float, ev: dict = None) -> str:
    """Auto-learn on every close + live analysis text."""
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
        logger.debug("suite close: %s", e)
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
        logger.debug("brain close: %s", e)
    try:
        import csv
        from pathlib import Path as _P
        from datetime import datetime, timezone
        jpath = _P("data") / "live_trade_journal.csv"
        jpath.parent.mkdir(parents=True, exist_ok=True)
        nf = not jpath.exists()
        with jpath.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if nf:
                w.writerow(["ts","symbol","direction","pnl","r","won","setup","regime","session","agents"])
            w.writerow([
                datetime.now(timezone.utc).isoformat(), symbol, direction,
                round(fpnl,4), round(r_mult,4), int(won), setup, regime, session,
                "|".join(str(a) for a in agents),
            ])
    except Exception as e:
        logger.debug("journal: %s", e)
    emoji = "✅" if won else "❌"
    lines = [
        f"{emoji} *Learn+Close* `{symbol}` {direction}",
        f"PnL: `{fpnl:+.2f}` · R: `{r_mult:+.2f}` · Live: `{live_n}`",
        f"Setup: `{setup}` · Regime: `{regime or '—'}`",
    ]
    if banned:
        lines.append(f"⚠️ Banned: `{banned}`")
    lines.append("_Learned from this close_")
    return chr(10).join(lines)


'''
        t2, n = re.subn(
            r"def _ai_on_close\(symbol: str, fpnl: float, ev: dict = None\):.*?(?=\nasync def live_monitoring_worker)",
            new_close,
            t,
            count=1,
            flags=re.S,
        )
        if n:
            t = t2
            print("_ai_on_close enhanced")

    # safe close alert wiring — only replace bare calls
    t = re.sub(
        r"(?<!_analysis = )_ai_on_close\(symbol, fpnl, ev\)",
        "_analysis = _ai_on_close(symbol, fpnl, ev)\n                                    if _analysis:\n                                        await _send_alert(_analysis)",
        t,
    )
    # fix common indent break: if analysis then elif
    t = t.replace(
        "                                    _analysis = _ai_on_close(symbol, fpnl, ev)\n                            if _analysis:\n                                await _send_alert(_analysis)\n                                elif etype",
        "                                    _analysis = _ai_on_close(symbol, fpnl, ev)\n                                    if _analysis:\n                                        await _send_alert(_analysis)\n                                elif etype",
    )

    INSERT = '''
                            try:
                                if getattr(config, "SAFETY_REQUIRE_RISK_CAN_TRADE", True):
                                    _rok, _rwhy = risk_mgr.can_trade()
                                    if not _rok:
                                        logger.info("Autotrade %s: RISK VETO — %s", symbol, _rwhy)
                                        monitoring_health[f"{symbol}_eval"] = f"Risk veto: {_rwhy}"
                                        if _should_notify_veto(symbol, f"risk:{_rwhy}"):
                                            await _send_alert(f"🛡️ *Risk veto* `{symbol}`\\n`{_rwhy}`")
                                        continue
                            except Exception as _re:
                                logger.debug("risk note: %s", _re)
                            try:
                                _prof = str(getattr(config, "CURRENT_PROFILE", "scalp") or "scalp")
                                _ok_ai, sig, _ai_reasons = _ai_gate_signal(sig, symbol, _prof, dname, agree, conv_val)
                                if not _ok_ai:
                                    _why = ", ".join(_ai_reasons) if _ai_reasons else "AI veto"
                                    logger.info("Autotrade %s: AI VETO — %s", symbol, _why)
                                    monitoring_health[f"{symbol}_eval"] = f"AI veto: {_why}"
                                    if _should_notify_veto(symbol, f"ai:{_why[:80]}"):
                                        await _send_alert(f"🤖 *AI veto* `{symbol}`\\n`{_why}`")
                                    continue
                            except Exception as _ae:
                                logger.debug("ai gate note: %s", _ae)
'''
    if "RISK VETO" not in t:
        for needle in (
            "                            can_enter_dir, dir_reason = limiter.check_can_enter",
            "                            # 1. Strict Single-Direction",
        ):
            if needle in t:
                t = t.replace(needle, INSERT + "\n" + needle, 1)
                print("risk+AI gates inserted")
                break

    if "LEARN VETO" not in t and "_, _, size_mult = brain.advise_signal" in t:
        t = t.replace("_, _, size_mult = brain.advise_signal(", "_ok_brain, _brain_why, size_mult = brain.advise_signal(", 1)
        marker = "profile=current_profile_name,\n                                )"
        if marker in t:
            t = t.replace(
                marker,
                marker + '''
                                _live_veto = str(os.getenv("BOT_LEARN_LIVE_VETO", "true")).lower() in ("true", "1", "yes", "on")
                                if not _ok_brain and _live_veto:
                                    logger.info("Autotrade %s: LEARN VETO — %s", symbol, _brain_why)
                                    monitoring_health[f"{symbol}_eval"] = f"Learn veto: {_brain_why}"
                                    if _should_notify_veto(symbol, f"learn:{str(_brain_why)[:80]}"):
                                        await _send_alert(f"🧠 *Learn veto* `{symbol}`\\n`{_brain_why}`")
                                    continue
''',
                1,
            )
            print("learn veto enabled")

    p.write_text(t, encoding="utf-8")
    ast.parse(t)
    print("autotrade_worker OK")

def fix_antispam():
    p = Path("anti_spam.py")
    a = p.read_text(encoding="utf-8")
    a = a.replace(
        "from typing import Any, Dict, Optional, Tuple",
        "from typing import Any, Dict, List, Optional, Tuple",
    )
    for name in ("get_entry_cooldown_duration", "get_loss_cooldown_duration", "get_global_cooldown_duration"):
        a = re.sub(
            rf"    def {name}\([^)]*\).*?\n(?=    def )",
            f"    def {name}(self, *args, **kwargs):\n        return 0  # cooldowns removed\n\n",
            a, count=1, flags=re.S,
        )
    if "cooldowns disabled" not in a:
        for start_s in (
            "        # When bypass_cooldowns or mode is OFF",
            "        # When mode is OFF: cooldowns",
            "        if bypass_cooldowns:",
        ):
            s = a.find(start_s)
            if s >= 0:
                e = a.find('        return True, "OK"\n', s)
                if e > 0:
                    e += len('        return True, "OK"\n')
                    a = a[:s] + '        return True, "OK (cooldowns disabled)"\n\n' + a[e:]
                    break
    p.write_text(a, encoding="utf-8")
    ast.parse(a)
    print("anti_spam OK")

def fix_config():
    p = Path("config.py")
    c = p.read_text(encoding="utf-8")
    if "SAFETY_REQUIRE_RISK_CAN_TRADE" not in c:
        c += '\nSAFETY_REQUIRE_RISK_CAN_TRADE = os.getenv("BOT_SAFETY_REQUIRE_RISK", "true").lower() in ("true", "1", "yes", "on")\n'
    if "LEARN_LIVE_VETO" not in c:
        c += 'LEARN_LIVE_VETO = os.getenv("BOT_LEARN_LIVE_VETO", "true").lower() in ("true", "1", "yes", "on")\n'
    p.write_text(c, encoding="utf-8")
    print("config OK")

def main():
    fix_autotrade()
    fix_antispam()
    fix_config()
    print("ALL CRITICAL BUGS FIXED — restart bot")

if __name__ == "__main__":
    main()
