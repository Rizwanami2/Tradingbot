#!/usr/bin/env python3
"""Apply AI suite combination into autotrade_worker.py if missing."""
from pathlib import Path

def main():
    p = Path("autotrade_worker.py")
    t = p.read_text(encoding="utf-8")
    if "evaluate_signal_dict" in t:
        print("Already combined")
        return
    needle = (
        "                            except Exception as _b_err:\n"
        "                                logger.debug(\"Autotrade brain check error: %s\", _b_err)\n\n"
        "                            try:\n"
        "                                order = engine.register_setup(sig, reason=sig.get(\"reason\", \"\"))\n"
    )
    insert = (
        "                            except Exception as _b_err:\n"
        "                                logger.debug(\"Autotrade brain check error: %s\", _b_err)\n\n"
        "                            try:\n"
        "                                from ai_advanced_suite import evaluate_signal_dict, get_ai_suite\n"
        "                                _suite = get_ai_suite()\n"
        "                                if getattr(_suite, \"enabled\", True):\n"
        "                                    if not sig.get(\"direction\") and \"dname\" in dir():\n"
        "                                        sig[\"direction\"] = dname\n"
        "                                    if not sig.get(\"agreement\") and \"agree\" in dir():\n"
        "                                        sig[\"agreement\"] = agree\n"
        "                                    if not sig.get(\"conviction\") and \"conv_val\" in dir():\n"
        "                                        sig[\"conviction\"] = conv_val\n"
        "                                    sig.setdefault(\"symbol\", symbol)\n"
        "                                    gate = evaluate_signal_dict(sig, profile=str(current_profile_name or \"scalp\"))\n"
        "                                    if not gate.allow:\n"
        "                                        logger.info(\"Autotrade %s: AI suite veto — %s\", symbol, \"; \".join(gate.reasons))\n"
        "                                        continue\n"
        "                                    if gate.size_mult < 1.0 and sig.get(\"lot_mode\", \"auto\") != \"manual\":\n"
        "                                        for k in (\"lots\", \"size\"):\n"
        "                                            if sig.get(k):\n"
        "                                                sig[k] = max(0.01, round(float(sig[k]) * gate.size_mult, 4))\n"
        "                                        try:\n"
        "                                            sig[\"lots\"] = _suite.apply_lot_ceiling(float(sig.get(\"lots\") or sig.get(\"size\") or 0.01))\n"
        "                                        except Exception:\n"
        "                                            pass\n"
        "                                    sig[\"ai_scorecard\"] = gate.scorecard\n"
        "                                    sig[\"ai_gate_reasons\"] = list(gate.reasons)\n"
        "                            except Exception as _ai_err:\n"
        "                                logger.debug(\"AI suite gate error: %s\", _ai_err)\n\n"
        "                            try:\n"
        "                                order = engine.register_setup(sig, reason=sig.get(\"reason\", \"\"))\n"
    )
    if needle not in t:
        raise SystemExit("Needle not found — autotrade_worker layout changed")
    p.write_text(t.replace(needle, insert, 1), encoding="utf-8")
    print("Combined AI suite into autotrade_worker.py")

if __name__ == "__main__":
    main()
