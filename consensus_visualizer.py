#!/usr/bin/env python3
"""
Multi-Agent Voting Consensus Visualizer.
=======================================
Renders an institutional visual matrix of all voting agents, their strengths,
weights, agreement counts, and score totals for rich Telegram and CLI display.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


DEFAULT_WEIGHTS = {
    "TREND": 1.25,
    "LIQUIDITY": 1.40,
    "INDICATORS": 1.00,
    "AI-LEARNER": 0.90,
    "NEWS": 0.70,
    "GAUTAM-JHA": 1.55,
    "MEAN-REVERSION": 1.35,
    "FAKE-BREAKOUT": 1.35,
    "MTF-CONFLUENCE": 1.40,
    "ASIAN-BREAKOUT": 1.25,
    "MT5-CHART": 1.35,
    "ORR-STRATEGY": 1.45,
    "SUPPLY-DEMAND": 1.45,
    "OTE-STRATEGY": 1.45,
    "VOLATILITY-SQUEEZE": 1.50,
    "RIBBON-SCALP": 1.45,
}


def _strength_bar(val: float, width: int = 5) -> str:
    filled = int(round(max(0.0, min(1.0, val)) * width))
    return "█" * filled + "░" * (width - filled)


def format_agent_consensus_matrix(
    votes: List[Any],
    weights: Optional[Dict[str, float]] = None,
    min_agreement: int = 2,
    min_score: float = 1.2,
) -> str:
    """
    Renders a rich Markdown voting matrix from agent votes.
    Accepts both list of Vote objects and list of vote dicts.
    """
    w_map = dict(weights or DEFAULT_WEIGHTS)
    long_score = 0.0
    short_score = 0.0
    long_n = 0
    short_n = 0

    lines = [
        "🏛️ **MULTI-AGENT CONSENSUS MATRIX**",
        "━" * 32,
    ]

    for v in votes:
        agent = getattr(v, "agent", None) or (v.get("agent") if isinstance(v, dict) else "AGENT")
        d_raw = getattr(v, "direction", None) or (v.get("direction") if isinstance(v, dict) else "FLAT")
        d_name = getattr(d_raw, "name", str(d_raw)).upper()
        strength = float(getattr(v, "strength", None) or (v.get("strength", 0.0) if isinstance(v, dict) else 0.0) or 0.0)

        w = w_map.get(agent, 1.0)
        bar = _strength_bar(strength, width=4)

        if "LONG" in d_name or "BUY" in d_name:
            icon = "🟢"
            score_contrib = w * strength
            long_score += score_contrib
            long_n += 1
            score_str = f"+{score_contrib:.2f}"
            dir_str = "LONG "
        elif "SHORT" in d_name or "SELL" in d_name:
            icon = "🔴"
            score_contrib = w * strength
            short_score += score_contrib
            short_n += 1
            score_str = f"-{score_contrib:.2f}"
            dir_str = "SHORT"
        else:
            icon = "⚪"
            score_str = " 0.00"
            dir_str = "FLAT "

        agent_pad = f"{agent:<14}"[:14]
        lines.append(f"{icon} `{agent_pad}` {dir_str} `{bar}` `{score_str}`")

    lines.append("━" * 32)

    # Consensus determination
    if long_n >= min_agreement and long_score >= min_score and long_score > short_score:
        verdict = f"🟢 **CONSENSUS BUY** (`+{long_score:.2f}` / {min_score:.2f} | {long_n}/{min_agreement} agreed)"
    elif short_n >= min_agreement and short_score >= min_score and short_score > long_score:
        verdict = f"🔴 **CONSENSUS SELL** (`+{short_score:.2f}` / {min_score:.2f} | {short_n}/{min_agreement} agreed)"
    else:
        verdict = f"⚪ **NO CONSENSUS** (L:{long_score:.2f}/{long_n} S:{short_score:.2f}/{short_n})"

    lines.append(f"🏆 {verdict}")
    return "\n".join(lines)
