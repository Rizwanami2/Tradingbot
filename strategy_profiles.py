"""
Configurable Strategy Profiles
==============================
JSON-serializable profiles that change agent weights, RR, session rules, etc.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, Optional

PROFILES_FILE = "strategy_profiles.json"


@dataclass
class StrategyProfile:
    name: str
    description: str = ""
    weights: Dict[str, float] = field(default_factory=lambda: {
        "TREND": 1.25, "LIQUIDITY": 1.40, "INDICATORS": 1.00,
        "AI-LEARNER": 0.90, "NEWS": 0.70,
    })
    min_agreement: int = 2
    min_score: float = 1.2
    min_rr: float = 1.9
    require_killzone: bool = False
    allow_asian: bool = False
    base_risk_pct: float = 0.25
    max_leverage: float = 20.0
    strict_htf: bool = True


DEFAULT_PROFILES = {
    "balanced": StrategyProfile(
        name="balanced",
        description="Default multi-agent balance",
    ),
    "scalp": StrategyProfile(
        name="scalp",
        description="Fast entries, killzone only, tighter RR",
        weights={"TREND": 1.0, "LIQUIDITY": 1.5, "INDICATORS": 1.2, "AI-LEARNER": 0.7, "NEWS": 0.5},
        min_agreement=3,
        min_score=2.0,
        min_rr=1.5,
        require_killzone=True,
        base_risk_pct=0.15,
        max_leverage=15.0,
        strict_htf=False,
    ),
    "swing": StrategyProfile(
        name="swing",
        description="Higher TF focus, wider targets",
        weights={"TREND": 1.5, "LIQUIDITY": 1.3, "INDICATORS": 0.9, "AI-LEARNER": 1.0, "NEWS": 0.8},
        min_agreement=3,
        min_score=1.6,
        min_rr=2.5,
        require_killzone=False,
        base_risk_pct=0.30,
        max_leverage=10.0,
        strict_htf=True,
    ),
    "gold_only": StrategyProfile(
        name="gold_only",
        description="Optimized for XAU / Gold",
        weights={"TREND": 1.2, "LIQUIDITY": 1.6, "INDICATORS": 1.0, "AI-LEARNER": 0.85, "NEWS": 1.0},
        min_score=1.9,
        min_rr=2.0,
        base_risk_pct=0.22,
        max_leverage=40.0,
    ),
    "crypto": StrategyProfile(
        name="crypto",
        description="Crypto-focused, lower leverage",
        weights={"TREND": 1.3, "LIQUIDITY": 1.2, "INDICATORS": 1.1, "AI-LEARNER": 1.0, "NEWS": 1.1},
        min_agreement=2,
        min_score=1.2,
        min_rr=2.0,
        base_risk_pct=0.18,
        max_leverage=15.0,
        allow_asian=True,
    ),
}


def load_profiles(path: str = PROFILES_FILE) -> Dict[str, StrategyProfile]:
    profiles = {k: v for k, v in DEFAULT_PROFILES.items()}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for name, data in raw.items():
                profiles[name] = StrategyProfile(**data)
        except Exception:
            pass
    return profiles


def save_profiles(profiles: Dict[str, StrategyProfile], path: str = PROFILES_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({k: asdict(v) for k, v in profiles.items()}, f, indent=2)


def get_profile(name: str = "balanced") -> StrategyProfile:
    profiles = load_profiles()
    return profiles.get(name, profiles["balanced"])
