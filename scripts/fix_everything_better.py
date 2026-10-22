#!/usr/bin/env python3
"""One-shot: cooldown, CombinedFIT, multi-agent merge, swarm log, singleton."""
from pathlib import Path
import re
import ast

def fix_combined():
    p = Path("combined_bot_agent.py")
    t = p.read_text(encoding="utf-8")
    if "session_filter_enabled" not in t:
        t = t.replace("    require_m1_align: bool = True\n",
                      "    require_m1_align: bool = True\n    session_filter_enabled: bool = False\n")
    if "all_sessions" not in t:
        t = t.replace(
            '        if s == "off":\n            return False, "session_off"\n        return True, s\n',
            '        if not getattr(self.cfg, "session_filter_enabled", False):\n'
            '            return True, s if s != "off" else "all_sessions"\n'
            '        if s == "off":\n            return False, "session_off"\n        return True, s\n',
        )
    if "COMBINED_AGENT_SESSION_FILTER" not in t:
        t = t.replace(
            "        self.cfg = cfg or AgentConfig()\n        self.state = state or AgentState()\n",
            "        import os\n        self.cfg = cfg or AgentConfig()\n        self.state = state or AgentState()\n"
            '        self.cfg.session_filter_enabled = os.getenv("COMBINED_AGENT_SESSION_FILTER", "false").lower() in ("true", "1", "yes", "on")\n'
            '        self._disabled = os.getenv("COMBINED_AGENT_ENABLED", "true").lower() in ("false", "0", "no", "off")\n',
        )
    if "combined_disabled" not in t:
        t = t.replace(
            "        reasons: List[str] = []\n        sig = dict(sig or {})\n        ctx = context or {}\n",
            "        reasons: List[str] = []\n        sig = dict(sig or {})\n        ctx = context or {}\n"
            "        if getattr(self, \"_disabled\", False):\n            return True, sig, [\"combined_disabled\"]\n",
            1,
        )
    if "if spread > 0:" not in t:
        t = t.replace(
            '        spread = float(ctx.get("spread") or sig.get("spread") or 0.0)\n'
            "        ok, why = self._spread_ok(spread)\n"
            "        if not ok:\n"
            "            return False, sig, [why]\n"
            "        reasons.append(why)\n",
            '        spread = float(ctx.get("spread") or sig.get("spread") or 0.0)\n'
            "        if spread > 0:\n"
            "            ok, why = self._spread_ok(spread)\n"
            "            if not ok:\n"
            "                return False, sig, [why]\n"
            "            reasons.append(why)\n",
        )
    if "def get_combined_agent" not in t:
        t += "\n\n_AGENT_SINGLETON = None\n\ndef get_combined_agent():\n    global _AGENT_SINGLETON\n    if _AGENT_SINGLETON is None:\n        _AGENT_SINGLETON = CombinedBotAgent()\n    return _AGENT_SINGLETON\n"
    p.write_text(t, encoding="utf-8")
    ast.parse(t)
    print("OK combined_bot_agent")

def fix_antispam():
    p = Path("anti_spam.py")
    t = p.read_text(encoding="utf-8")
    t = t.replace("self.entry_cooldown_sec = max(5, int(seconds))", "self.entry_cooldown_sec = max(0, int(seconds))")
    t = re.sub(r"(self\.loss_cooldown_sec = )max\(5, int\(seconds\)\)", r"\1max(0, int(seconds))", t)
    t = re.sub(r"(self\.global_cooldown_sec = )max\(5, int\(seconds\)\)", r"\1max(0, int(seconds))", t)
    needle = '            return True, f"OK (Spam trade mode active: {open_n + 1}/{eff_max_open} without cooldown)"\n'
    if "antispam mode off" not in t and needle in t:
        t = t.replace(needle, needle + '''
        if str(getattr(self, "mode", "auto")).lower() in ("off", "disabled", "none", "0"):
            open_n = len(engine.active_positions) + len(engine.pending_setups)
            if open_n >= max_open:
                return False, f"Total open slots full ({open_n}/{max_open})"
            if curr_count >= self.max_positions_per_symbol:
                return False, f"Max {self.max_positions_per_symbol} positions for {curr}"
            return True, "OK (antispam mode off — cooldowns skipped)"
''', 1)
    p.write_text(t, encoding="utf-8")
    ast.parse(t)
    print("OK anti_spam")

def fix_arbiter():
    p = Path("multi_agent_trading_bot.py")
    t = p.read_text(encoding="utf-8")
    if "logger = " not in t[:4000] and "logging.getLogger" not in t[:4000]:
        t = t.replace("from __future__ import annotations\n",
                      "from __future__ import annotations\n\nimport logging\nlogger = logging.getLogger(\"multi_agent\")\n", 1)
    if "Swarm votes for" not in t:
        t = t.replace(
            "                swarm_votes = self.swarm.evaluate(ctx)\n                for sv in swarm_votes:",
            "                swarm_votes = self.swarm.evaluate(ctx)\n"
            "                logger.info(\"Swarm votes for %s: %d\", symbol, len(swarm_votes or []))\n"
            "                for sv in swarm_votes:",
            1,
        )
    if "Combined+Multi FIT veto" not in t:
        MERGE = '''
        try:
            import os as _os_merge
            if _os_merge.getenv("COMBINED_AGENT_ENABLED", "true").lower() in ("true", "1", "yes", "on"):
                from combined_bot_agent import get_combined_agent
                _cba = get_combined_agent()
                _dname = direction.name if hasattr(direction, "name") else str(direction)
                _entry = float(sig.get("entry") or sig.get("entry_level") or 0.0)
                _ok_cba, sig, _cba_why = _cba.evaluate(
                    sig,
                    {
                        "spread": float(sig.get("spread") or 0.0),
                        "open_trades": 0,
                        "open_for_symbol": 0,
                        "htf_bias": str(sig.get("regime") or _dname),
                        "m1_direction": _dname,
                        "setup": str(sig.get("setup") or sig.get("reason") or "pullback"),
                        "entry": _entry,
                        "is_live": False,
                    },
                )
                if not _ok_cba:
                    _why = ", ".join(_cba_why) if _cba_why else "combined FIT"
                    return None, f"Combined+Multi FIT veto: {_why}", votes
                try:
                    votes.append(Vote("CombinedFIT", direction, 1.0, "FIT:" + ",".join((_cba_why or [])[:4]))
                except Exception:
                    pass
                sig["combined_merged"] = True
                sig["combined_reasons"] = list(_cba_why or [])
                _agents = list(sig.get("agents") or [])
                if "CombinedFIT" not in _agents:
                    _agents.append("CombinedFIT")
                sig["agents"] = _agents
                status_msg += " | CombinedFIT OK"
        except Exception as _merge_err:
            try:
                logger.debug("Combined+Multi merge note: %s", _merge_err)
            except Exception:
                pass

'''
        # fix typo - missing closing paren on votes.append in MERGE - fix here
        MERGE = MERGE.replace(
            'votes.append(Vote("CombinedFIT", direction, 1.0, "FIT:" + ",".join((_cba_why or [])[:4]))',
            'votes.append(Vote("CombinedFIT", direction, 1.0, "FIT:" + ",".join((_cba_why or [])[:4])))',
        )
        key = "        if session_warn:\n            status_msg += f\" ({session_warn})\"\n        return sig, status_msg, votes\n"
        if key in t:
            t = t.replace(key, "        if session_warn:\n            status_msg += f\" ({session_warn})\"\n" + MERGE + "        return sig, status_msg, votes\n", 1)
        else:
            idx = t.rfind("return sig, status_msg, votes")
            if idx < 0:
                raise SystemExit("arbiter return not found")
            t = t[:idx] + MERGE + "        " + t[idx:]
    p.write_text(t, encoding="utf-8")
    ast.parse(t)
    print("OK multi_agent_trading_bot")

def fix_worker():
    p = Path("autotrade_worker.py")
    t = p.read_text(encoding="utf-8")
    if "already merged in Arbiter" not in t:
        t = t.replace(
            "from combined_bot_agent import CombinedBotAgent\n"
            "                                if not hasattr(live_monitoring_worker, \"_combined_agent\"):\n"
            "                                    live_monitoring_worker._combined_agent = CombinedBotAgent()\n"
            "                                _cba = live_monitoring_worker._combined_agent",
            "if sig.get(\"combined_merged\"):\n"
            "                                    raise RuntimeError(\"already merged in Arbiter\")\n"
            "                                from combined_bot_agent import get_combined_agent\n"
            "                                _cba = get_combined_agent()",
        )
        if "already merged in Arbiter" not in t and "get_combined_agent()" in t:
            t = t.replace(
                "from combined_bot_agent import get_combined_agent\n                                _cba = get_combined_agent()",
                "if sig.get(\"combined_merged\"):\n"
                "                                    raise RuntimeError(\"already merged in Arbiter\")\n"
                "                                from combined_bot_agent import get_combined_agent\n"
                "                                _cba = get_combined_agent()",
                1,
            )
    p.write_text(t, encoding="utf-8")
    ast.parse(t)
    print("OK autotrade_worker")

def fix_main():
    p = Path("main.py")
    t = p.read_text(encoding="utf-8")
    t2 = t.replace(
        "from combined_bot_agent import CombinedBotAgent\n        cba = CombinedBotAgent()",
        "from combined_bot_agent import get_combined_agent\n        cba = get_combined_agent()",
    )
    if t2 != t:
        p.write_text(t2, encoding="utf-8")
        print("OK main.py")
    else:
        print("main.py pattern skip (ok)")

def main():
    fix_combined()
    fix_antispam()
    fix_arbiter()
    fix_worker()
    fix_main()
    print("ALL FIXES APPLIED — restart bot")
    print("env: COMBINED_AGENT_ENABLED=true COMBINED_AGENT_SESSION_FILTER=false ANTISPAM_MODE=off")

if __name__ == "__main__":
    main()
