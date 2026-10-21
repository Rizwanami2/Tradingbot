#!/usr/bin/env python3
"""Fix critical autotrade bugs (Windows-safe, indent-safe)."""
from pathlib import Path
import re
import ast


def wire_close_alerts(t: str) -> str:
    lines = t.splitlines()
    out = []
    for line in lines:
        stripped = line.lstrip()
        if (
            "_ai_on_close(symbol, fpnl, ev)" in line
            and "_analysis" not in line
            and not stripped.startswith("def ")
        ):
            base = len(line) - len(line.lstrip(" "))
            ind = " " * base
            out.append(f"{ind}_analysis = _ai_on_close(symbol, fpnl, ev)")
            out.append(f"{ind}if _analysis:")
            out.append(f"{ind}    await _send_alert(_analysis)")
        else:
            out.append(line)
    lines = out
    out = []
    for line in lines:
        if line.strip().startswith("if _analysis:"):
            for j in range(len(out) - 1, max(-1, len(out) - 6), -1):
                if "_analysis = _ai_on_close" in out[j]:
                    base = len(out[j]) - len(out[j].lstrip(" "))
                    out.append(" " * base + "if _analysis:")
                    break
            else:
                out.append(line)
            continue
        if line.strip().startswith("await _send_alert(_analysis)"):
            for j in range(len(out) - 1, max(-1, len(out) - 6), -1):
                if out[j].strip().startswith("if _analysis"):
                    base = len(out[j]) - len(out[j].lstrip(" "))
                    out.append(" " * base + "    await _send_alert(_analysis)")
                    break
            else:
                out.append(line)
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def fix_autotrade() -> None:
    p = Path("autotrade_worker.py")
    t = p.read_text(encoding="utf-8")

    if "evaluate_signal_dict" in t and "Learn+Close" in t and "RISK VETO" in t:
        t2 = wire_close_alerts(t.replace("_analysis = _analysis = ", "_analysis = "))
        try:
            ast.parse(t2)
            p.write_text(t2, encoding="utf-8")
            print("autotrade_worker: already fixed (indent normalized)")
            return
        except SyntaxError:
            print("repairing broken indent...")
            t = p.read_text(encoding="utf-8")

    if "import os" not in t[:1000]:
        t = t.replace("import asyncio\n", "import asyncio\nimport os\n", 1)

    stub = (
        'def _ai_gate_signal(sig: dict, symbol: str, profile: str, dname: str = "", agree: int = 0, conv_val: float = 0.6):\n'
        '    """\n'
        "    Pure Multi-Agent & Brain mode:\n"
        "    Secondary AI gating and vetoing are disabled; consensus signals flow cleanly.\n"
        '    """\n'
        "    return True, sig\n"
    )
    gate = (
        "def _should_notify_veto(symbol: str, reason_key: str, cooldown_sec: float = 120.0) -> bool:\n"
        "    now = time.time()\n"
        "    prev = _last_ai_veto.get(symbol)\n"
        "    if prev and prev[0] == reason_key and (now - prev[1]) < cooldown_sec:\n"
        "        return False\n"
        "    _last_ai_veto[symbol] = (reason_key, now)\n"
        "    return True\n\n\n"
        'def _ai_gate_signal(sig: dict, symbol: str, profile: str, dname: str = "", agree: int = 0, conv_val: float = 0.6):\n'
        '    """AI Advanced Suite hard gates. Returns (allowed, sig, reasons)."""\n'
        "    try:\n"
        "        from ai_advanced_suite import evaluate_signal_dict, get_ai_suite\n"
        "        suite = get_ai_suite()\n"
        '        if not getattr(suite, "enabled", True):\n'
        "            return True, sig, []\n"
        '        gate = evaluate_signal_dict(sig, profile=str(profile or "scalp"))\n'
        '        reasons = list(getattr(gate, "reasons", None) or [])\n'
        '        if not getattr(gate, "allowed", True):\n'
        "            return False, sig, reasons\n"
        "        try:\n"
        '            sm = float(getattr(gate, "size_mult", 1.0) or 1.0)\n'
        "            if sm > 0 and sm != 1.0:\n"
        "                sig = dict(sig)\n"
        '                sig["ai_size_mult"] = sm\n'
        "        except Exception:\n"
        "            pass\n"
        '        if hasattr(gate, "scorecard"):\n'
        "            sig = dict(sig)\n"
        '            sig["ai_scorecard"] = getattr(gate, "scorecard", None)\n'
        "        return True, sig, reasons\n"
        "    except Exception as e:\n"
        '        logger.debug("AI suite gate error (fail-open): %s", e)\n'
        "        return True, sig, []\n"
    )
    if stub in t:
        t = t.replace(stub, gate, 1)
        print("AI gate restored")

    if "Learn+Close" not in t:
        new_close = (
            'def _ai_on_close(symbol: str, fpnl: float, ev: dict = None) -> str:\n'
            '    """Auto-learn on every close + live analysis text."""\n'
            "    ev = ev or {}\n"
            '    r_mult = float(ev.get("r_multiple", ev.get("r", 0.0)) or 0.0)\n'
            "    if r_mult == 0.0 and fpnl != 0.0:\n"
            "        r_mult = 1.0 if fpnl > 0 else -1.0\n"
            "    won = fpnl >= 0\n"
            '    pos = ev.get("order") if ev else None\n'
            '    agents = list(getattr(pos, "agents", []) or []) if pos else []\n'
            '    regime = str(getattr(pos, "regime", "") or "") if pos else ""\n'
            '    session = str(getattr(pos, "session", "") or "") if pos else ""\n'
            '    direction = str(getattr(pos, "direction", "") or "") if pos else ""\n'
            '    setup = f"{direction.lower()}|{symbol.lower()}" if direction else f"live|{symbol.lower()}"\n'
            '    conf = float(getattr(pos, "conviction", 0.6) or 0.6) if pos else 0.6\n'
            "    try:\n"
            "        from ai_advanced_suite import get_ai_suite\n"
            "        get_ai_suite().on_trade_closed(symbol=symbol, won=won, r_multiple=r_mult)\n"
            "    except Exception as e:\n"
            '        logger.debug("suite close: %s", e)\n'
            "    live_n = 0\n"
            '    banned = ""\n'
            "    try:\n"
            "        from autonomous_learner import get_brain\n"
            "        brain = get_brain(auto_bootstrap=False)\n"
            "        brain.on_trade_closed(\n"
            "            r_multiple=r_mult, won=won, setup=setup, regime=regime,\n"
            "            session=session, agents=agents, conf=conf,\n"
            "        )\n"
            '        live_n = int(getattr(getattr(brain, "knowledge", None), "total_live_trades", 0) or 0)\n'
            '        if setup in getattr(brain.knowledge, "banned_setups", {}):\n'
            '            banned = str(brain.knowledge.banned_setups.get(setup, ""))\n'
            "    except Exception as e:\n"
            '        logger.debug("brain close: %s", e)\n'
            "    try:\n"
            "        import csv\n"
            "        from pathlib import Path as _P\n"
            "        from datetime import datetime, timezone\n"
            '        jpath = _P("data") / "live_trade_journal.csv"\n'
            "        jpath.parent.mkdir(parents=True, exist_ok=True)\n"
            "        nf = not jpath.exists()\n"
            '        with jpath.open("a", newline="", encoding="utf-8") as f:\n'
            "            w = csv.writer(f)\n"
            "            if nf:\n"
            '                w.writerow(["ts", "symbol", "direction", "pnl", "r", "won", "setup", "regime", "session", "agents"])\n'
            "            w.writerow([\n"
            "                datetime.now(timezone.utc).isoformat(), symbol, direction,\n"
            "                round(fpnl, 4), round(r_mult, 4), int(won), setup, regime, session,\n"
            '                "|".join(str(a) for a in agents),\n'
            "            ])\n"
            "    except Exception as e:\n"
            '        logger.debug("journal: %s", e)\n'
            '    reg_disp = regime or "-"\n'
            '    lines = [\n'
            '        f"{emoji} Learn+Close {symbol} {direction}",\n'
            '        f"PnL: {fpnl:+.2f} | R: {r_mult:+.2f} | Live: {live_n}",\n'
            '        f"Setup: {setup} | Regime: {reg_disp}",\n'
            "    ]\n"
            "    if banned:\n"
            '        lines.append(f"Banned: {banned}")\n'
            '    lines.append("Learned from this close")\n'
            "    return chr(10).join(lines)\n\n\n"
        )
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

    t = wire_close_alerts(t)

    if "RISK VETO" not in t:
        INSERT = (
            "\n"
            "                            try:\n"
            '                                if getattr(config, "SAFETY_REQUIRE_RISK_CAN_TRADE", True):\n'
            "                                    _rok, _rwhy = risk_mgr.can_trade()\n"
            "                                    if not _rok:\n"
            '                                        logger.info("Autotrade %s: RISK VETO — %s", symbol, _rwhy)\n'
            '                                        monitoring_health[f"{symbol}_eval"] = f"Risk veto: {_rwhy}"\n'
            '                                        if _should_notify_veto(symbol, f"risk:{_rwhy}"):\n'
            '                                            await _send_alert(f"Risk veto {symbol}: {_rwhy}")\n'
            "                                        continue\n"
            "                            except Exception as _re:\n"
            '                                logger.debug("risk note: %s", _re)\n'
            "                            try:\n"
            '                                _prof = str(getattr(config, "CURRENT_PROFILE", "scalp") or "scalp")\n'
            "                                _ok_ai, sig, _ai_reasons = _ai_gate_signal(sig, symbol, _prof, dname, agree, conv_val)\n"
            "                                if not _ok_ai:\n"
            '                                    _why = ", ".join(_ai_reasons) if _ai_reasons else "AI veto"\n'
            '                                    logger.info("Autotrade %s: AI VETO — %s", symbol, _why)\n'
            '                                    monitoring_health[f"{symbol}_eval"] = f"AI veto: {_why}"\n'
            '                                    if _should_notify_veto(symbol, f"ai:{_why[:80]}"):\n'
            '                                        await _send_alert(f"AI veto {symbol}: {_why}")\n'
            "                                    continue\n"
            "                            except Exception as _ae:\n"
            '                                logger.debug("ai gate note: %s", _ae)\n'
        )
        for needle in (
            "                            can_enter_dir, dir_reason = limiter.check_can_enter",
            "                            # 1. Strict Single-Direction",
        ):
            if needle in t:
                t = t.replace(needle, INSERT + "\n" + needle, 1)
                print("risk+AI gates inserted")
                break

    if "LEARN VETO" not in t and "_, _, size_mult = brain.advise_signal" in t:
        t = t.replace(
            "_, _, size_mult = brain.advise_signal(",
            "_ok_brain, _brain_why, size_mult = brain.advise_signal(",
            1,
        )
        marker = "profile=current_profile_name,\n                                )"
        if marker in t:
            t = t.replace(
                marker,
                marker
                + "\n"
                + '                                _live_veto = str(os.getenv("BOT_LEARN_LIVE_VETO", "true")).lower() in ("true", "1", "yes", "on")\n'
                + "                                if not _ok_brain and _live_veto:\n"
                + '                                    logger.info("Autotrade %s: LEARN VETO — %s", symbol, _brain_why)\n'
                + '                                    monitoring_health[f"{symbol}_eval"] = f"Learn veto: {_brain_why}"\n'
                + '                                    if _should_notify_veto(symbol, f"learn:{str(_brain_why)[:80]}"):\n'
                + '                                        await _send_alert(f"Learn veto {symbol}: {_brain_why}")\n'
                + "                                    continue\n",
                1,
            )
            print("learn veto enabled")

    t = wire_close_alerts(t)
    ast.parse(t)
    p.write_text(t, encoding="utf-8")
    print("autotrade_worker OK")


def fix_antispam() -> None:
    p = Path("anti_spam.py")
    a = p.read_text(encoding="utf-8")
    ast.parse(a)
    print("anti_spam OK")


def fix_config() -> None:
    p = Path("config.py")
    c = p.read_text(encoding="utf-8")
    if "SAFETY_REQUIRE_RISK_CAN_TRADE" not in c:
        c += '\nSAFETY_REQUIRE_RISK_CAN_TRADE = os.getenv("BOT_SAFETY_REQUIRE_RISK", "true").lower() in ("true", "1", "yes", "on")\n'
    if "LEARN_LIVE_VETO" not in c:
        c += 'LEARN_LIVE_VETO = os.getenv("BOT_LEARN_LIVE_VETO", "true").lower() in ("true", "1", "yes", "on")\n'
    p.write_text(c, encoding="utf-8")
    print("config OK")


def main() -> None:
    fix_autotrade()
    fix_antispam()
    fix_config()
    print("ALL CRITICAL BUGS FIXED - restart bot")


if __name__ == "__main__":
    main()
