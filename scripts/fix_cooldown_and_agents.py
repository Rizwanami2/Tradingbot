#!/usr/bin/env python3
"""Fix cooldown zero, CombinedAgent session filter, shared singleton."""
from pathlib import Path
import re
import ast

def fix_combined():
    p = Path("combined_bot_agent.py")
    if not p.exists():
        print("WARN: combined_bot_agent.py missing")
        return
    t = p.read_text(encoding="utf-8")
    if "session_filter_enabled" not in t:
        t = t.replace(
            "    require_m1_align: bool = True\n",
            "    require_m1_align: bool = True\n    session_filter_enabled: bool = False\n",
        )
    t = t.replace(
        "def _session_ok(self, now: Optional[datetime]) -> Tuple[bool, str]:\n"
        "        s = _session(now, self.cfg)\n"
        "        if s == \"off\":\n"
        "            return False, \"session_off\"\n"
        "        return True, s\n",
        "def _session_ok(self, now: Optional[datetime]) -> Tuple[bool, str]:\n"
        "        s = _session(now, self.cfg)\n"
        "        if not getattr(self.cfg, \"session_filter_enabled\", False):\n"
        "            return True, s if s != \"off\" else \"all_sessions\"\n"
        "        if s == \"off\":\n"
        "            return False, \"session_off\"\n"
        "        return True, s\n",
    )
    if "def get_combined_agent" not in t:
        t += "\n\n_AGENT_SINGLETON = None\n\n\ndef get_combined_agent():\n    global _AGENT_SINGLETON\n    if _AGENT_SINGLETON is None:\n        from combined_bot_agent import CombinedBotAgent as _C\n        _AGENT_SINGLETON = CombinedBotAgent()\n    return _AGENT_SINGLETON\n"
        # fix circular - better inline
        t = t.replace(
            "def get_combined_agent():\n    global _AGENT_SINGLETON\n    if _AGENT_SINGLETON is None:\n        from combined_bot_agent import CombinedBotAgent as _C\n        _AGENT_SINGLETON = CombinedBotAgent()\n    return _AGENT_SINGLETON\n",
            "def get_combined_agent():\n    global _AGENT_SINGLETON\n    if _AGENT_SINGLETON is None:\n        _AGENT_SINGLETON = CombinedBotAgent()\n    return _AGENT_SINGLETON\n",
        )
    if "session_filter_enabled = sf" not in t and "COMBINED_AGENT_SESSION_FILTER" not in t:
        t = t.replace(
            "        self.cfg = cfg or AgentConfig()\n        self.state = state or AgentState()\n",
            "        import os\n        self.cfg = cfg or AgentConfig()\n        self.state = state or AgentState()\n"
            "        sf = os.getenv(\"COMBINED_AGENT_SESSION_FILTER\", \"false\").lower() in (\"true\", \"1\", \"yes\", \"on\")\n"
            "        self.cfg.session_filter_enabled = sf\n"
            "        self._disabled = os.getenv(\"COMBINED_AGENT_ENABLED\", \"true\").lower() in (\"false\", \"0\", \"no\", \"off\")\n",
        )
    if "if getattr(self, \"_disabled\"" not in t and "combined_disabled" not in t:
        t = t.replace(
            "        reasons: List[str] = []\n        sig = dict(sig or {})\n        ctx = context or {}\n",
            "        reasons: List[str] = []\n        sig = dict(sig or {})\n        ctx = context or {}\n"
            "        if getattr(self, \"_disabled\", False):\n"
            "            return True, sig, [\"combined_disabled\"]\n",
        )
    # Load env on init if not present
    if "COMBINED_AGENT_SESSION_FILTER" not in t:
        print("WARN: could not fully patch session env")
    p.write_text(t, encoding="utf-8")
    ast.parse(t)
    print("combined_bot_agent OK")

def fix_antispam():
    p = Path("anti_spam.py")
    t = p.read_text(encoding="utf-8")
    t = t.replace(
        "self.entry_cooldown_sec = max(5, int(seconds))",
        "self.entry_cooldown_sec = max(0, int(seconds))",
    )
    t = re.sub(
        r"(self\.loss_cooldown_sec = )max\(5, int\(seconds\)\)",
        r"\1max(0, int(seconds))",
        t,
    )
    t = re.sub(
        r"(self\.global_cooldown_sec = )max\(5, int\(seconds\)\)",
        r"\1max(0, int(seconds))",
        t,
    )
    needle = '            return True, f"OK (Spam trade mode active: {open_n + 1}/{eff_max_open} without cooldown)"\n'
    if needle in t and "antispam mode off" not in t:
        t = t.replace(
            needle,
            needle
            + '''
        if str(getattr(self, "mode", "auto")).lower() in ("off", "disabled", "none", "0"):
            open_n = len(engine.active_positions) + len(engine.pending_setups)
            if open_n >= max_open:
                return False, f"Total open slots full ({open_n}/{max_open})"
            if curr_count >= self.max_positions_per_symbol:
                return False, f"Max {self.max_positions_per_symbol} positions for {curr}"
            return True, "OK (antispam mode off — cooldowns skipped)"
''',
            1,
        )
    p.write_text(t, encoding="utf-8")
    ast.parse(t)
    print("anti_spam OK")

def fix_worker():
    p = Path("autotrade_worker.py")
    t = p.read_text(encoding="utf-8")
    if "get_combined_agent" in t:
        print("worker already uses singleton")
        return
    t = t.replace(
        "from combined_bot_agent import CombinedBotAgent",
        "from combined_bot_agent import get_combined_agent",
    )
    t = re.sub(
        r"if not hasattr\(live_monitoring_worker, \"_combined_agent\"\):\n\s+live_monitoring_worker\._combined_agent = CombinedBotAgent\(\)\n\s+_cba = live_monitoring_worker\._combined_agent",
        "_cba = get_combined_agent()",
        t,
        count=1,
    )
    if "_cba = get_combined_agent()" not in t and "CombinedBotAgent()" in t:
        t = t.replace(
            "live_monitoring_worker._combined_agent = CombinedBotAgent()\n"
            "                                _cba = live_monitoring_worker._combined_agent",
            "_cba = get_combined_agent()",
        )
    p.write_text(t, encoding="utf-8")
    ast.parse(t)
    print("autotrade_worker OK")

def fix_main():
    p = Path("main.py")
    t = p.read_text(encoding="utf-8")
    t2 = t.replace(
        "from combined_bot_agent import CombinedBotAgent\n        cba = CombinedBotAgent()",
        "from combined_bot_agent import get_combined_agent\n        cba = get_combined_agent()",
    )
    if t2 == t:
        print("main: combined singleton pattern not found or already fixed")
    else:
        p.write_text(t2, encoding="utf-8")
        print("main.py combined_command uses singleton")

def main():
    fix_combined()
    fix_antispam()
    fix_worker()
    fix_main()
    print("ALL FIXES APPLIED — restart bot")
    print("Then: /cooldown 0   /combined   set ANTISPAM_MODE=off")

if __name__ == "__main__":
    main()
