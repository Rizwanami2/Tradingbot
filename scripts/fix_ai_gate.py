#!/usr/bin/env python3
"""Restore AI Advanced Suite gate in autotrade_worker + risk halt + Telegram veto alerts."""
from pathlib import Path

NEW_GATE = '''def _ai_gate_signal(sig: dict, symbol: str, profile: str, dname: str = "", agree: int = 0, conv_val: float = 0.6):
    """
    AI Advanced Suite gate: hard risk vetoes (daily loss, revenge, consec losses, scorecard).
    Returns (allowed: bool, sig_or_updated, reasons: list[str]).
    """
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
                if "lot" in sig:
                    try:
                        sig["lot"] = float(sig["lot"]) * sm
                    except Exception:
                        pass
        except Exception:
            pass
        if hasattr(gate, "scorecard"):
            sig = dict(sig)
            sig["ai_scorecard"] = getattr(gate, "scorecard", None)
        return True, sig, reasons
    except Exception as e:
        logger.debug("AI suite gate error (fail-open): %s", e)
        return True, sig, []


def _should_notify_veto(symbol: str, reason_key: str, cooldown_sec: float = 120.0) -> bool:
    now = time.time()
    prev = _last_ai_veto.get(symbol)
    if prev and prev[0] == reason_key and (now - prev[1]) < cooldown_sec:
        return False
    _last_ai_veto[symbol] = (reason_key, now)
    return True
'''

INSERT = '''
                            # --- Risk manager hard halt ---
                            try:
                                if getattr(config, "SAFETY_REQUIRE_RISK_CAN_TRADE", True):
                                    _rok, _rwhy = risk_mgr.can_trade()
                                    if not _rok:
                                        logger.info("Autotrade %s: RISK VETO — %s", symbol, _rwhy)
                                        monitoring_health[f"{symbol}_eval"] = f"Risk veto: {_rwhy}"
                                        if _should_notify_veto(symbol, f"risk:{_rwhy}"):
                                            await _send_alert(f"🛡️ *Risk veto* `{symbol}`\\n`{_rwhy}`")
                                        continue
                            except Exception as _risk_e:
                                logger.debug("Risk can_trade note: %s", _risk_e)

                            # --- AI Advanced Suite gate ---
                            try:
                                _prof = str(getattr(config, "CURRENT_PROFILE", "scalp") or "scalp")
                                _ok_ai, sig, _ai_reasons = _ai_gate_signal(sig, symbol, _prof, dname, agree, conv_val)
                                if not _ok_ai:
                                    _why = ", ".join(_ai_reasons) if _ai_reasons else "AI suite veto"
                                    logger.info("Autotrade %s: AI VETO — %s", symbol, _why)
                                    monitoring_health[f"{symbol}_eval"] = f"AI veto: {_why}"
                                    if _should_notify_veto(symbol, f"ai:{_why[:80]}"):
                                        await _send_alert(f"🤖 *AI veto* `{symbol}` {dname}\\n`{_why}`")
                                    continue
                                if sig.get("ai_size_mult"):
                                    try:
                                        size_mult = float(size_mult) * float(sig["ai_size_mult"])
                                        size_mult = max(0.25, min(2.0, size_mult))
                                    except Exception:
                                        pass
                            except Exception as _ai_e:
                                logger.debug("AI gate note: %s", _ai_e)
'''

def main():
    p = Path("autotrade_worker.py")
    t = p.read_text(encoding="utf-8")

    if "AI Advanced Suite gate: hard risk vetoes" in t and "RISK VETO" in t:
        print("Already fixed")
        return

    # Replace stub gate
    import re
    t2 = re.sub(
        r"def _ai_gate_signal\(sig: dict.*?(?=\ndef _ai_on_open)",
        NEW_GATE + "\n\n",
        t,
        count=1,
        flags=re.S,
    )
    if t2 == t:
        # try exact stub
        stub = (
            'def _ai_gate_signal(sig: dict, symbol: str, profile: str, dname: str = "", agree: int = 0, conv_val: float = 0.6):\n'
            '    """\n'
            '    Pure Multi-Agent & Brain mode:\n'
            '    Secondary AI gating and vetoing are disabled; consensus signals flow cleanly.\n'
            '    """\n'
            '    return True, sig\n'
        )
        if stub in t:
            t2 = t.replace(stub, NEW_GATE + "\n", 1)
        else:
            print("WARN: could not replace _ai_gate_signal — insert gates only")
            t2 = t
    t = t2

    if "RISK VETO" not in t:
        # insert before direction check
        for needle in (
            "                            can_enter_dir, dir_reason = limiter.check_can_enter",
            "                            # 1. Strict Single-Direction",
            "                            # 1. Directional",
        ):
            if needle in t:
                t = t.replace(needle, INSERT + "\n" + needle, 1)
                print("inserted risk+AI gates")
                break
        else:
            raise SystemExit("insert point not found")

    p.write_text(t, encoding="utf-8")
    import ast
    ast.parse(t)
    print("DONE: AI gate + risk veto + Telegram alerts")

if __name__ == "__main__":
    main()
