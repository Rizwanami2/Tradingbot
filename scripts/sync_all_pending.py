#!/usr/bin/env python3
"""One-shot: apply all pending source patches (idempotent)."""
from pathlib import Path
import re
import ast

def patch_antispam():
    p = Path("anti_spam.py")
    t = p.read_text(encoding="utf-8")
    t = re.sub(
        r"    def get_entry_cooldown_duration\(self.*?\n(?=    def )",
        "    def get_entry_cooldown_duration(self, symbol: str = \"\") -> int:\n        return 0  # cooldowns removed\n\n",
        t, count=1, flags=re.S,
    )
    t = re.sub(
        r"    def get_loss_cooldown_duration\(self.*?\n(?=    def )",
        "    def get_loss_cooldown_duration(self, symbol: str = \"\") -> int:\n        return 0  # cooldowns removed\n\n",
        t, count=1, flags=re.S,
    )
    t = re.sub(
        r"    def get_global_cooldown_duration\(self.*?\n(?=    def )",
        "    def get_global_cooldown_duration(self) -> int:\n        return 0  # cooldowns removed\n\n",
        t, count=1, flags=re.S,
    )
    if "cooldowns disabled" not in t:
        for start_s in (
            "        # When bypass_cooldowns or mode is OFF: cooldowns and proximity checks are bypassed",
            "        # When mode is OFF: cooldowns and proximity checks are bypassed",
            "        if bypass_cooldowns:",
        ):
            start = t.find(start_s)
            if start >= 0:
                end = t.find('        return True, "OK"\n', start)
                if end > 0:
                    end += len('        return True, "OK"\n')
                    t = t[:start] + '        # Cooldowns + proximity fully disabled\n        return True, "OK (cooldowns disabled)"\n\n' + t[end:]
                    break
    t = t.replace('os.getenv("SCALP_ENTRY_COOLDOWN_SEC", "45")', 'os.getenv("SCALP_ENTRY_COOLDOWN_SEC", "0")')
    t = t.replace('os.getenv("SCALP_LOSS_COOLDOWN_SEC", "90")', 'os.getenv("SCALP_LOSS_COOLDOWN_SEC", "0")')
    t = t.replace('os.getenv("SCALP_GLOBAL_COOLDOWN_SEC", "10")', 'os.getenv("SCALP_GLOBAL_COOLDOWN_SEC", "0")')
    t = t.replace('os.getenv("SCALP_MIN_PRICE_DISTANCE_PCT", "0.08")', 'os.getenv("SCALP_MIN_PRICE_DISTANCE_PCT", "0")')
    t = t.replace('os.getenv("ANTISPAM_MODE", "auto")', 'os.getenv("ANTISPAM_MODE", "off")')
    p.write_text(t, encoding="utf-8")
    print("anti_spam: OK")

def patch_xau_scalp():
    p = Path("strategy_profiles.py")
    t = p.read_text(encoding="utf-8")
    if "XAUUSD-optimized micro-scalp" in t:
        print("strategy_profiles: already XAU")
        return
    i = t.find('"scalp": StrategyProfile')
    j = t.find('"swing": StrategyProfile')
    if i < 0 or j < 0:
        print("strategy_profiles: markers missing")
        return
    i = t.rfind("\n", 0, i) + 1
    block = '''    "scalp": StrategyProfile(
        name="scalp",
        description="XAUUSD-optimized micro-scalp: liquidity+MTF focus, London/NY friendly, tight risk",
        weights={
            "TREND": 1.25, "LIQUIDITY": 1.55, "INDICATORS": 1.25, "AI-LEARNER": 1.05,
            "NEWS": 0.85, "GAUTAM-JHA": 1.40, "MEAN-REVERSION": 1.15, "FAKE-BREAKOUT": 1.45,
            "MTF-CONFLUENCE": 1.50, "ASIAN-BREAKOUT": 0.90, "MT5-CHART": 1.40,
        },
        min_agreement=2, min_score=1.20, min_rr=1.20, require_killzone=False, allow_asian=False,
        base_risk_pct=0.15, max_leverage=15.0, strict_htf=False, is_scalp=True, fast_market_fill=True,
        scalp_tp_pct=0.08, scalp_sl_pct=0.10, scalp_tp_usd=3.00, scalp_sl_usd=2.50,
        poll_interval=2, fast_poll_interval=1, max_open_trades=2, scalp_trail_enabled=True,
        scalp_trail_activation_usd=1.20, scalp_trail_distance_usd=0.70,
        scalp_trail_activation_pct=0.04, scalp_trail_distance_pct=0.025, scalp_be_lock_usd=0.60,
        scalp_min_trail_profit_usd=1.20, scalp_profit_retention_pct=0.55, scalp_let_profit_run=True,
        scalp_entry_cooldown_sec=0.0, scalp_loss_cooldown_sec=0.0, scalp_global_cooldown_sec=0.0,
        scalp_max_positions_per_symbol=1, scalp_min_price_distance_pct=0.0,
    ),
'''
    p.write_text(t[:i] + block + t[j:], encoding="utf-8")
    print("strategy_profiles: XAU scalp")

def patch_config():
    p = Path("config.py")
    t = p.read_text(encoding="utf-8")
    if "SAFETY_MAX_OPEN_TRADES" not in t:
        t = t.replace(
            'MAX_OPEN_TRADES = _safe_int(os.getenv("BOT_MAX_OPEN_TRADES"), 6)',
            'MAX_OPEN_TRADES = _safe_int(os.getenv("BOT_MAX_OPEN_TRADES"), 2)\n'
            'SAFETY_MAX_OPEN_TRADES = _safe_int(os.getenv("BOT_SAFETY_MAX_OPEN"), 3)\n'
            'SAFETY_REQUIRE_RISK_CAN_TRADE = os.getenv("BOT_SAFETY_REQUIRE_RISK", "true").lower() in ("true", "1", "yes", "on")',
        )
    t = t.replace(
        'MAX_POSITIONS_PER_SYMBOL = _safe_int(os.getenv("BOT_MAX_POSITIONS_PER_SYMBOL") or os.getenv("SCALP_MAX_POSITIONS_PER_SYMBOL"), 3)',
        'MAX_POSITIONS_PER_SYMBOL = _safe_int(os.getenv("BOT_MAX_POSITIONS_PER_SYMBOL") or os.getenv("SCALP_MAX_POSITIONS_PER_SYMBOL"), 1)',
    )
    if "ACCURACY_MODE" not in t:
        t += '\nACCURACY_MODE = os.getenv("BOT_ACCURACY_MODE", "true").lower() in ("true", "1", "yes", "on")\n'
        t += 'ACCURACY_MIN_QUALITY = _safe_float(os.getenv("BOT_ACCURACY_MIN_QUALITY"), 0.45)\n'
    for key, val in [("SCALP_ENTRY_COOLDOWN_SEC", "0.0"), ("SCALP_LOSS_COOLDOWN_SEC", "0.0"), ("SCALP_GLOBAL_COOLDOWN_SEC", "0.0")]:
        t = re.sub(rf'({key} = _safe_float\(os\.getenv\("[^"]+"\), )[0-9.]+(\))', rf'\g<1>{val}\2', t)
    p.write_text(t, encoding="utf-8")
    print("config: OK")

def patch_risk():
    p = Path("risk_manager.py")
    t = p.read_text(encoding="utf-8")
    t = t.replace("max_daily_loss_pct: float = 3.0", "max_daily_loss_pct: float = 2.0")
    t = t.replace("max_consecutive_losses: int = 4", "max_consecutive_losses: int = 3")
    t = t.replace("base_risk_pct: float = 0.20", "base_risk_pct: float = 0.15")
    p.write_text(t, encoding="utf-8")
    print("risk_manager: OK")

def patch_ai():
    p = Path("ai_advanced_suite.py")
    t = p.read_text(encoding="utf-8")
    t = t.replace('_env_float("AI_MIN_SCORECARD", 55.0)', '_env_float("AI_MIN_SCORECARD", 60.0)')
    t = t.replace('_env_int("AI_MAX_DAILY_TRADES", 12)', '_env_int("AI_MAX_DAILY_TRADES", 6)')
    t = t.replace('_env_int("AI_MAX_CONSEC_LOSSES", 4)', '_env_int("AI_MAX_CONSEC_LOSSES", 3)')
    p.write_text(t, encoding="utf-8")
    print("ai_advanced_suite: OK")

def patch_autotrade():
    p = Path("autotrade_worker.py")
    t = p.read_text(encoding="utf-8")
    if "Risk halt:" not in t:
        needle = "\n                    if can_open:"
        insert = (
            "\n                    if can_open and getattr(config, \"SAFETY_REQUIRE_RISK_CAN_TRADE\", True):"
            "\n                        try:"
            "\n                            _ok_risk, _risk_why = risk_mgr.can_trade()"
            "\n                        except Exception:"
            "\n                            _ok_risk, _risk_why = True, \"\""
            "\n                        if not _ok_risk:"
            "\n                            can_open = False"
            "\n                            monitoring_health[f\"{symbol}_eval\"] = f\"Risk halt: {_risk_why}\""
            "\n                            logger.info(\"Autotrade %s blocked by risk_mgr: %s\", symbol, _risk_why)"
        )
        if needle in t:
            t = t.replace(needle, insert + needle, 1)
    t = t.replace(
        'arbiter.min_effective_rr = 0.85 if getattr(_active_prof, "is_scalp", False) else 1.4',
        'arbiter.min_effective_rr = (1.15 if getattr(config, "ACCURACY_MODE", True) else 0.85) if getattr(_active_prof, "is_scalp", False) else 1.4',
    )
    p.write_text(t, encoding="utf-8")
    print("autotrade_worker: OK")

def patch_signal_quality():
    p = Path("signal_quality.py")
    if not p.exists():
        return
    t = p.read_text(encoding="utf-8")
    if "BOT_ACCURACY_MIN_QUALITY" in t:
        print("signal_quality: already")
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
    if old in t:
        p.write_text(t.replace(old, new, 1), encoding="utf-8")
        print("signal_quality: OK")
    else:
        print("signal_quality: skip")

def main():
    patch_antispam()
    patch_xau_scalp()
    patch_config()
    patch_risk()
    patch_ai()
    patch_autotrade()
    patch_signal_quality()
    for f in ["anti_spam.py", "strategy_profiles.py", "config.py", "risk_manager.py", "autotrade_worker.py", "ai_advanced_suite.py"]:
        if Path(f).exists():
            ast.parse(Path(f).read_text(encoding="utf-8"))
    print("SYNC COMPLETE — restart bot.")

if __name__ == "__main__":
    main()
