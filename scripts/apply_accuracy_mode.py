#!/usr/bin/env python3
"""Apply accuracy-mode patches to local Tradingbot clone."""
from pathlib import Path

def patch_scalp_profile():
    p = Path("strategy_profiles.py")
    t = p.read_text(encoding="utf-8")
    idx = t.find('"scalp": StrategyProfile')
    if idx < 0:
        print("scalp profile not found")
        return
    chunk = t[idx:idx+900]
    chunk2 = chunk.replace("min_score=1.0,", "min_score=1.25,  # accuracy", 1)
    chunk2 = chunk2.replace("min_rr=1.0,", "min_rr=1.25,  # accuracy", 1)
    if chunk2 == chunk:
        if "min_score=1.25" in chunk:
            print("strategy_profiles: already accurate")
        else:
            print("strategy_profiles: could not patch scalp scores")
        return
    t = t[:idx] + chunk2 + t[idx+900:]
    p.write_text(t, encoding="utf-8")
    print("strategy_profiles: scalp min_score/rr -> 1.25")

def patch_signal_quality():
    p = Path("signal_quality.py")
    t = p.read_text(encoding="utf-8")
    if "BOT_ACCURACY_MIN_QUALITY" in t:
        print("signal_quality: already patched")
        return
    if "import os" not in t:
        t = t.replace(
            "from dataclasses import dataclass\nfrom typing import Dict, List, Tuple\n",
            "from dataclasses import dataclass\nfrom typing import Dict, List, Tuple\nimport os\n",
        )
    old = "    final = float(np.mean(scores)) if scores else 0.5\n    return QualityResult(True, final, reasons)\n"
    new = (
        "    final = float(np.mean(scores)) if scores else 0.5\n"
        "    try:\n"
        "        min_q = float(os.getenv(\"BOT_ACCURACY_MIN_QUALITY\", \"0.45\") or 0.45)\n"
        "    except Exception:\n"
        "        min_q = 0.45\n"
        "    accuracy_on = os.getenv(\"BOT_ACCURACY_MODE\", \"true\").lower() in (\"true\", \"1\", \"yes\", \"on\")\n"
        "    if accuracy_on and final < min_q:\n"
        "        reasons.append(f\"quality {final:.2f} < min {min_q:.2f}\")\n"
        "        return QualityResult(False, final, reasons)\n"
        "    return QualityResult(True, final, reasons)\n"
    )
    if old not in t:
        print("signal_quality: tail not found")
        return
    p.write_text(t.replace(old, new, 1), encoding="utf-8")
    print("signal_quality: min quality gate added")

def patch_ai():
    p = Path("ai_advanced_suite.py")
    t = p.read_text(encoding="utf-8")
    t2 = t.replace('_env_float("AI_MIN_SCORECARD", 55.0)', '_env_float("AI_MIN_SCORECARD", 60.0)')
    if t2 != t:
        p.write_text(t2, encoding="utf-8")
        print("ai_advanced_suite: min scorecard 60")
    else:
        print("ai_advanced_suite: no change")

def patch_autotrade():
    p = Path("autotrade_worker.py")
    t = p.read_text(encoding="utf-8")
    t = t.replace(
        'arbiter.min_effective_rr = 0.85 if getattr(_active_prof, "is_scalp", False) else 1.4',
        'arbiter.min_effective_rr = (1.15 if getattr(config, "ACCURACY_MODE", True) else 0.85) if getattr(_active_prof, "is_scalp", False) else 1.4',
    )
    p.write_text(t, encoding="utf-8")
    print("autotrade_worker: effective RR accuracy path")

def patch_config():
    p = Path("config.py")
    t = p.read_text(encoding="utf-8")
    if "ACCURACY_MODE" in t:
        print("config: already has ACCURACY_MODE")
        return
    t += (
        "\n# Signal accuracy filters\n"
        "ACCURACY_MODE = os.getenv(\"BOT_ACCURACY_MODE\", \"true\").lower() in (\"true\", \"1\", \"yes\", \"on\")\n"
        "ACCURACY_MIN_QUALITY = _safe_float(os.getenv(\"BOT_ACCURACY_MIN_QUALITY\"), 0.45)\n"
        "ACCURACY_MIN_SCORECARD = _safe_float(os.getenv(\"BOT_ACCURACY_MIN_SCORECARD\"), 60.0)\n"
    )
    p.write_text(t, encoding="utf-8")
    print("config: ACCURACY_MODE added")

def main():
    patch_scalp_profile()
    patch_signal_quality()
    patch_ai()
    patch_autotrade()
    patch_config()
    print("Done. Set BOT_ACCURACY_MODE=true in .env and restart.")

if __name__ == "__main__":
    main()
