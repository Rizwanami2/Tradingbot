#!/usr/bin/env python3
"""Apply XAU dual-profile upgrade + CombinedFIT/anti-spam fixes. Idempotent.

FILES THIS SCRIPT UPDATES (all required):
  1. combined_bot_agent.py
  2. autotrade_worker.py
  3. anti_spam.py
  4. strategy_profiles.py

After git pull, run once:
  python scripts/apply_dual_profile_upgrade.py
"""
from pathlib import Path
import re
import ast

BALANCED_BLOCK = (
    '    "balanced": StrategyProfile(\n'
    '        name="balanced",\n'
    '        description="XAUUSD balanced: HTF + liquidity, London/NY, RR~1.9 (not micro-scalp)",\n'
    "        weights={\n"
    '            "TREND": 1.35, "LIQUIDITY": 1.55, "INDICATORS": 1.05, "AI-LEARNER": 0.90,\n'
    '            "NEWS": 0.85, "GAUTAM-JHA": 1.35, "MEAN-REVERSION": 1.15, "FAKE-BREAKOUT": 1.40,\n'
    '            "MTF-CONFLUENCE": 1.50, "ASIAN-BREAKOUT": 0.70, "MT5-CHART": 1.40, "ORR-STRATEGY": 1.45,\n'
    '            "SUPPLY-DEMAND": 1.50, "OTE-STRATEGY": 1.50, "VOLATILITY-SQUEEZE": 0.80,\n'
    '            "RIBBON-SCALP": 0.70, "STOCH-RSI-SCALP": 0.70, "AUTO-TREND-1M": 0.75,\n'
    "        },\n"
    "        min_agreement=2, min_score=1.35, min_rr=1.9, require_killzone=True, allow_asian=False,\n"
    "        strict_htf=True, base_risk_pct=0.20, max_leverage=20.0, is_scalp=False,\n"
    "        max_open_trades=3, scalp_max_positions_per_symbol=2,\n"
    "    ),\n"
)

NEW_STRUCT = (
    '        su = (setup or "pullback").lower().strip()\n'
    "        ok_keywords = (\n"
    '            "pull", "sweep", "liquid", "trend", "scalp", "consensus", "score",\n'
    '            "long", "short", "agent", "arbiter", "swarm", "break", "fvg", "ob",\n'
    '            "ote", "bos", "choch", "smc", "ict", "pivot", "fib", "balanced",\n'
    '            "perfect", "entry",\n'
    "        )\n"
    "        allowed = {\n"
    '            "pullback", "sweep_reclaim", "sweep", "liquidity", "consensus",\n'
    '            "multi_agent", "swarm", "scalp", "break_retest", "breakout",\n'
    '            "balanced", "",\n'
    "        }\n"
    "        if self.cfg.allow_break_retest:\n"
    '            allowed |= {"break_retest", "breakout"}\n'
    "        if not su or su in allowed:\n"
    '            return True, "structure_ok"\n'
    "        if any(k in su for k in ok_keywords):\n"
    '            return True, "structure_ok"\n'
    '        if "score" in su or "=" in su or len(su) > 24:\n'
    '            return True, "structure_ok"\n'
    '        return False, f"setup_blocked:{su[:40]}"\n'
)

def fix_combined():
    p = Path("combined_bot_agent.py")
    t = p.read_text(encoding="utf-8")
    if "ok_keywords = (" not in t:
        t2, n = re.subn(
            r"        su = \(setup or \"pullback\"\)\.lower\(\).*?return True, \"structure_ok\"\n",
            NEW_STRUCT, t, count=1, flags=re.S,
        )
        if not n:
            t2, n = re.subn(
                r"        su = \(setup or \"pullback\"\)\.lower\(\).*?return False, f\"setup_blocked:\{su\[:40\]\}\"\n        return True, \"structure_ok\"\n",
                NEW_STRUCT, t, count=1, flags=re.S,
            )
        t = t2 if n else t
    if "def get_combined_agent" not in t:
        t += "\n\n_AGENT_SINGLETON = None\n\ndef get_combined_agent():\n    global _AGENT_SINGLETON\n    if _AGENT_SINGLETON is None:\n        _AGENT_SINGLETON = CombinedBotAgent()\n    return _AGENT_SINGLETON\n"
    if "session_filter_enabled" not in t:
        t = t.replace("    require_m1_align: bool = True\n", "    require_m1_align: bool = True\n    session_filter_enabled: bool = False\n")
    if "all_sessions" not in t:
        t = t.replace(
            '        if s == "off":\n            return False, "session_off"\n        return True, s\n',
            '        if not getattr(self.cfg, "session_filter_enabled", False):\n'
            '            return True, s if s != "off" else "all_sessions"\n'
            '        if s == "off":\n            return False, "session_off"\n        return True, s\n')
    if "COMBINED_AGENT_SESSION_FILTER" not in t:
        t = t.replace(
            "        self.cfg = cfg or AgentConfig()\n        self.state = state or AgentState()\n",
            "        import os\n        self.cfg = cfg or AgentConfig()\n        self.state = state or AgentState()\n"
            '        self.cfg.session_filter_enabled = os.getenv("COMBINED_AGENT_SESSION_FILTER", "false").lower() in ("true", "1", "yes", "on")\n'
            '        self._disabled = os.getenv("COMBINED_AGENT_ENABLED", "true").lower() in ("false", "0", "no", "off")\n')
    if "if spread > 0:" not in t:
        t = t.replace(
            '        spread = float(ctx.get("spread") or sig.get("spread") or 0.0)\n        ok, why = self._spread_ok(spread)\n        if not ok:\n            return False, sig, [why]\n        reasons.append(why)\n',
            '        spread = float(ctx.get("spread") or sig.get("spread") or 0.0)\n        if spread > 0:\n            ok, why = self._spread_ok(spread)\n            if not ok:\n                return False, sig, [why]\n            reasons.append(why)\n')
    p.write_text(t, encoding="utf-8"); ast.parse(t); print("OK combined_bot_agent")

def fix_worker():
    p = Path("autotrade_worker.py")
    t = p.read_text(encoding="utf-8")
    t2 = t.replace(
        '"setup": str(sig.get("setup") or sig.get("reason") or "pullback"),',
        '"setup": str(sig.get("setup") or "consensus"),',
    )
    t2 = re.sub(
        r'"setup":\s*str\(sig\.get\("setup"\)\s*or\s*sig\.get\("reason"\)\s*or\s*"[^"]*"\),',
        '"setup": str(sig.get("setup") or "consensus"),', t2, count=5)
    p.write_text(t2, encoding="utf-8"); ast.parse(t2); print("OK autotrade_worker")

def fix_antispam():
    p = Path("anti_spam.py")
    t = p.read_text(encoding="utf-8")
    t = t.replace("self.entry_cooldown_sec = max(5, int(seconds))", "self.entry_cooldown_sec = max(0, int(seconds))")
    t = re.sub(r"(self\.loss_cooldown_sec = )max\(5, int\(seconds\)\)", r"\1max(0, int(seconds))", t)
    t = re.sub(r"(self\.global_cooldown_sec = )max\(5, int\(seconds\)\)", r"\1max(0, int(seconds))", t)
    needle = '            return True, f"OK (Spam trade mode active: {open_n + 1}/{eff_max_open} without cooldown)"\n'
    if "cooldowns skipped" not in t and needle in t:
        t = t.replace(needle, needle + (
            "\n        if str(getattr(self, \"mode\", \"auto\")).lower() in (\"off\", \"disabled\", \"none\", \"0\"):\n"
            "            open_n = len(engine.active_positions) + len(engine.pending_setups)\n"
            "            if open_n >= max_open:\n"
            "                return False, f\"Total open slots full ({open_n}/{max_open})\"\n"
            "            if curr_count >= self.max_positions_per_symbol:\n"
            "                return False, f\"Max {self.max_positions_per_symbol} positions for {curr}\"\n"
            "            return True, \"OK (antispam mode off — cooldowns skipped)\"\n"
        ), 1)
    p.write_text(t, encoding="utf-8"); ast.parse(t); print("OK anti_spam")

def fix_profiles():
    p = Path("strategy_profiles.py")
    t = p.read_text(encoding="utf-8")
    m = re.search(r'    "balanced": StrategyProfile\([\s\S]*?\n    \),', t)
    if m:
        t = t[:m.start()] + BALANCED_BLOCK + t[m.end():]
        print("balanced replaced")
    scalp_m = re.search(r'    "scalp": StrategyProfile\([\s\S]*?\n    \),', t)
    if scalp_m:
        sb = scalp_m.group(0)
        sb = re.sub(r"min_score=\d+\.?\d*", "min_score=1.1", sb, count=1)
        sb = re.sub(r"min_rr=\d+\.?\d*", "min_rr=1.15", sb, count=1)
        sb = re.sub(r'description="[^"]*"', 'description="XAUUSD micro-scalp: fast momentum, tight TP/SL, spread-aware"', sb, count=1)
        sb = sb.replace("is_scalp=False", "is_scalp=True")
        t = t[:scalp_m.start()] + sb + t[scalp_m.end():]
        print("scalp updated")
    xau_m = re.search(r'    "xau_scalp": StrategyProfile\([\s\S]*?\n    \),', t)
    if xau_m:
        xb = xau_m.group(0)
        xb = re.sub(r"min_score=\d+\.?\d*", "min_score=1.15", xb, count=1)
        xb = re.sub(r"min_rr=\d+\.?\d*", "min_rr=1.20", xb, count=1)
        xb = re.sub(r'description="[^"]*"', 'description="XAUUSD-optimized micro-scalp: liquidity+MTF, London/NY, tight risk, spread filter"', xb, count=1)
        xb = xb.replace("is_scalp=False", "is_scalp=True")
        t = t[:xau_m.start()] + xb + t[xau_m.end():]
        print("xau_scalp updated")
    p.write_text(t, encoding="utf-8"); ast.parse(t); print("OK strategy_profiles")

def main():
    fix_combined(); fix_worker(); fix_antispam(); fix_profiles()
    print("ALL DUAL-PROFILE UPGRADES APPLIED — restart bot")
    print("/profile balanced|scalp -> /bridge -> /cooldown 0 -> /autotrade on")

if __name__ == "__main__":
    main()
