#!/usr/bin/env python3
"""Merge CombinedBotAgent into multi-agent Arbiter.decide."""
from pathlib import Path
import ast

MERGE_BLOCK = '''
        # === MERGED: CombinedBotAgent FIT skills + multi-agent consensus ===
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
                    votes.append(Vote("CombinedFIT", direction, 1.0, "FIT:" + ",".join((_cba_why or [])[:4])))
                except Exception:
                    pass
                sig["combined_merged"] = True
                sig["combined_reasons"] = list(_cba_why or [])
                agents = list(sig.get("agents") or [])
                if "CombinedFIT" not in agents:
                    agents.append("CombinedFIT")
                sig["agents"] = agents
                status_msg += " | CombinedFIT OK"
        except Exception as _merge_err:
            try:
                logger.debug("Combined+Multi merge note: %s", _merge_err)
            except Exception:
                pass
'''

def fix_cba_singleton():
    p = Path("combined_bot_agent.py")
    if not p.exists():
        print("WARN: combined_bot_agent.py missing")
        return
    t = p.read_text(encoding="utf-8")
    if "def get_combined_agent" not in t:
        t += "\n\n_AGENT_SINGLETON = None\n\ndef get_combined_agent():\n    global _AGENT_SINGLETON\n    if _AGENT_SINGLETON is None:\n        _AGENT_SINGLETON = CombinedBotAgent()\n    return _AGENT_SINGLETON\n"
        p.write_text(t, encoding="utf-8")
    ast.parse(p.read_text(encoding="utf-8"))
    print("combined_bot_agent singleton OK")

def fix_arbiter():
    p = Path("multi_agent_trading_bot.py")
    t = p.read_text(encoding="utf-8")
    if "Combined+Multi FIT veto" in t:
        print("arbiter already merged")
        return
    if "logger = " not in t[:4000] and "logging.getLogger" not in t[:4000]:
        t = t.replace(
            "from __future__ import annotations\n",
            "from __future__ import annotations\n\nimport logging\nlogger = logging.getLogger(\"multi_agent\")\n",
            1,
        )
    needle = "        return sig, status_msg, votes\n"
    # insert before last decide return — find after status_msg scalp block
    key = "        if session_warn:\n            status_msg += f\" ({session_warn})\"\n        return sig, status_msg, votes\n"
    if key in t:
        t = t.replace(
            key,
            "        if session_warn:\n            status_msg += f\" ({session_warn})\"\n"
            + MERGE_BLOCK
            + "\n        return sig, status_msg, votes\n",
            1,
        )
    else:
        # fallback: before first bare return in decide end
        idx = t.rfind("return sig, status_msg, votes")
        if idx < 0:
            raise SystemExit("cannot find return sig, status_msg, votes")
        t = t[:idx] + MERGE_BLOCK + "\n        " + t[idx:]
    p.write_text(t, encoding="utf-8")
    ast.parse(t)
    print("arbiter merged OK")

def fix_worker():
    p = Path("autotrade_worker.py")
    t = p.read_text(encoding="utf-8")
    if "already merged in Arbiter" in t:
        print("worker already skips premerged")
        return
    # ensure skip when combined_merged
    if "get_combined_agent" in t or "CombinedBotAgent" in t:
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
        if "already merged in Arbiter" not in t:
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
    print("worker OK")

def main():
    fix_cba_singleton()
    fix_arbiter()
    fix_worker()
    print("MERGE COMPLETE — restart bot")

if __name__ == "__main__":
    main()
