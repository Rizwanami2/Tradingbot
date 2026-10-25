"""
Trader Skills Package for nimble-darwin
========================================
Modular investment intelligence and risk gating suite (11 skills):
1.  copycat   (13F institutional reverse engineering)
2.  pelosi    (US House Congressional PTR disclosures)
3.  news      (SEC 8-K filings & Yahoo Finance RSS)
4.  analyst   (SEC XBRL corporate financial statements)
5.  screener  (Rule-based quantitative stock screener)
6.  budget    (Capital allocation, cash buffer & caps)
7.  risk      (Pre-trade compliance & order firewall)
8.  broker    (Approval tickets & execution gateway)
9.  tax       (Tax lots, wash sales & harvest candidates)
10. alerts    (NYSE market clock & price alert rules)
11. rebalance (Portfolio drift & rebalancing proposals)
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
from typing import Any, Dict, List, Optional

SKILLS_ROOT = os.path.dirname(os.path.abspath(__file__))

SKILL_MODULE_MAP = {
    "copycat": ("trader-copycat", "edgar13f"),
    "edgar13f": ("trader-copycat", "edgar13f"),
    "pelosi": ("trader-pelosi", "ptr"),
    "ptr": ("trader-pelosi", "ptr"),
    "news": ("trader-news", "news"),
    "secnews": ("trader-news", "news"),
    "analyst": ("trader-analyst", "facts"),
    "facts": ("trader-analyst", "facts"),
    "screener": ("trader-screener", "screen"),
    "screen": ("trader-screener", "screen"),
    "budget": ("trader-budget", "size"),
    "size": ("trader-budget", "size"),
    "risk": ("trader-risk", "check"),
    "check": ("trader-risk", "check"),
    "broker": ("trader-broker", "ticket"),
    "ticket": ("trader-broker", "ticket"),
    "tax": ("trader-tax", "lots"),
    "lots": ("trader-tax", "lots"),
    "alerts": ("trader-alerts", "alerts"),
    "rebalance": ("trader-rebalance", "rebalance"),
}

_LOADED_MODULES: Dict[str, Any] = {}


def get_skill_module(name: str):
    """Dynamically load and return the Python module for the requested skill."""
    key = name.lower().strip().replace("/", "").replace("-", "")
    for map_key, (folder, script_name) in SKILL_MODULE_MAP.items():
        if key == map_key or key == f"trader{map_key}":
            if (folder, script_name) in _LOADED_MODULES:
                return _LOADED_MODULES[(folder, script_name)]
            path = os.path.join(SKILLS_ROOT, folder, f"{script_name}.py")
            if not os.path.exists(path):
                raise FileNotFoundError(f"Skill file not found at: {path}")
            spec = importlib.util.spec_from_file_location(f"trader_{script_name}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _LOADED_MODULES[(folder, script_name)] = mod
            return mod

    raise KeyError(f"Unknown trader skill: '{name}'. Available: {list(SKILL_MODULE_MAP.keys())}")


def run_skill(name: str, args: Optional[List[str]] = None, capture: bool = True) -> Dict[str, Any]:
    """Execute a skill's main(argv) safely with optional stdout/stderr capture."""
    mod = get_skill_module(name)
    argv = list(args) if args is not None else []

    if not capture:
        code = mod.main(argv)
        return {"exit_code": code or 0, "output": "", "error": "", "ok": code == 0}

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        try:
            res = mod.main(argv)
            code = res if isinstance(res, int) else 0
        except SystemExit as se:
            code = se.code if isinstance(se.code, int) else (0 if se.code is None else 1)
        except Exception as ex:
            err_buf.write(f"\nExecution error: {ex}")
            code = 1

    stdout_text = out_buf.getvalue().strip()
    stderr_text = err_buf.getvalue().strip()
    return {
        "exit_code": code,
        "output": stdout_text,
        "error": stderr_text,
        "ok": code == 0,
    }


# Convenience wrappers
def copycat(args: Optional[List[str]] = None) -> Dict[str, Any]:
    return run_skill("copycat", args)


def pelosi(args: Optional[List[str]] = None) -> Dict[str, Any]:
    return run_skill("pelosi", args)


def sec_news(args: Optional[List[str]] = None) -> Dict[str, Any]:
    return run_skill("news", args)


def analyst(args: Optional[List[str]] = None) -> Dict[str, Any]:
    return run_skill("analyst", args)


def screener(args: Optional[List[str]] = None) -> Dict[str, Any]:
    return run_skill("screener", args)


def budget(args: Optional[List[str]] = None) -> Dict[str, Any]:
    return run_skill("budget", args)


def risk_check(args: Optional[List[str]] = None) -> Dict[str, Any]:
    return run_skill("risk", args)


def broker_ticket(args: Optional[List[str]] = None) -> Dict[str, Any]:
    return run_skill("broker", args)


def tax(args: Optional[List[str]] = None) -> Dict[str, Any]:
    return run_skill("tax", args)


def alerts(args: Optional[List[str]] = None) -> Dict[str, Any]:
    return run_skill("alerts", args)


def rebalance(args: Optional[List[str]] = None) -> Dict[str, Any]:
    return run_skill("rebalance", args)
