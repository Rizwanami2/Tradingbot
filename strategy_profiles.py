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
        "AI-LEARNER": 0.90, "NEWS": 0.70, "GAUTAM-JHA": 1.30, "MEAN-REVERSION": 1.20,
    })
    min_agreement: int = 2
    min_score: float = 1.2
    min_rr: float = 1.9
    require_killzone: bool = False
    allow_asian: bool = False
    base_risk_pct: float = 0.25
    max_leverage: float = 20.0
    strict_htf: bool = True
    # Scalp & rapid execution attributes
    is_scalp: bool = False
    fast_market_fill: bool = False
    scalp_tp_pct: float = 0.10      # ~0.10% price move profit
    scalp_sl_pct: float = 0.12      # ~0.12% tight micro stop loss
    scalp_tp_usd: float = 2.50      # $2.50 fast profit target in USD
    scalp_sl_usd: float = 3.00      # $3.00 tight risk cut in USD
    poll_interval: int = 2          # Fast 2s polling in autotrade
    fast_poll_interval: int = 1     # 1s poll when monitoring positions
    max_open_trades: int = 2        # Up to 2 slots


DEFAULT_PROFILES = {
    "balanced": StrategyProfile(
        name="balanced",
        description="Default multi-agent balance",
    ),
    "scalp": StrategyProfile(
        name="scalp",
        description="High-frequency micro-scalp: micro SL/TP, rapid buying/selling, fast capital accumulation",
        weights={
            "TREND": 1.2,
            "LIQUIDITY": 1.4,
            "INDICATORS": 1.5,
            "AI-LEARNER": 1.0,
            "NEWS": 0.6,
            "GAUTAM-JHA": 1.3,
            "MEAN-REVERSION": 1.5,
        },
        min_agreement=2,
        min_score=1.0,
        min_rr=1.0,
        require_killzone=False,
        allow_asian=True,
        base_risk_pct=0.20,
        max_leverage=20.0,
        strict_htf=False,
        is_scalp=True,
        fast_market_fill=True,
        scalp_tp_pct=0.10,
        scalp_sl_pct=0.12,
        scalp_tp_usd=2.50,
        scalp_sl_usd=3.00,
        poll_interval=2,
        fast_poll_interval=1,
        max_open_trades=2,
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

CURRENT_PROFILE: str = "scalp"


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
    return profiles.get(name.lower(), profiles.get("balanced", DEFAULT_PROFILES["balanced"]))


def get_current_profile() -> StrategyProfile:
    global CURRENT_PROFILE
    return get_profile(CURRENT_PROFILE)


def apply_profile(
    name: str = "scalp",
    arbiter=None,
    risk_mgr=None,
    engine=None,
    config=None,
) -> Tuple[StrategyProfile, str]:
    """Applies a strategy profile across arbiter, risk manager, execution engine, and config in runtime."""
    global CURRENT_PROFILE
    profiles = load_profiles()
    name = str(name).lower().strip()
    if name not in profiles:
        raise ValueError(f"Unknown strategy profile '{name}'. Available: {', '.join(profiles.keys())}")
    p = profiles[name]
    CURRENT_PROFILE = p.name

    if arbiter is not None:
        arbiter.WEIGHTS = dict(p.weights)
        arbiter.MIN_AGREEMENT = p.min_agreement
        arbiter.MIN_SCORE = p.min_score
        arbiter.require_killzone = p.require_killzone
        arbiter.allow_asian = p.allow_asian
        arbiter.strict_htf = p.strict_htf
        arbiter.is_scalp = getattr(p, "is_scalp", False)
        arbiter.scalp_sl_pct = getattr(p, "scalp_sl_pct", 0.12)
        arbiter.scalp_tp_pct = getattr(p, "scalp_tp_pct", 0.10)
        arbiter.scalp_tp_usd = getattr(p, "scalp_tp_usd", 2.50)
        arbiter.scalp_sl_usd = getattr(p, "scalp_sl_usd", 3.00)
        arbiter.fast_market_fill = getattr(p, "fast_market_fill", False)
        arbiter.poll_interval = getattr(p, "poll_interval", 2)
        arbiter.fast_poll_interval = getattr(p, "fast_poll_interval", 1)

    if risk_mgr is not None:
        risk_mgr.config.base_risk_pct = p.base_risk_pct
        risk_mgr.config.min_rr = p.min_rr
        if getattr(p, "is_scalp", False):
            risk_mgr.config.tp1_r = 1.0
            risk_mgr.config.tp2_r = 1.5
            risk_mgr.set_leverage(min(getattr(p, "max_leverage", 20.0), getattr(risk_mgr.config, "max_leverage", 200.0)))
        else:
            risk_mgr.config.tp1_r = 1.5
            risk_mgr.config.tp2_r = 2.5
            risk_mgr.config.tp3_r = 4.0
            risk_mgr.set_leverage(min(getattr(p, "max_leverage", 20.0), getattr(risk_mgr.config, "max_leverage", 200.0)))

    if engine is not None:
        engine.scalp_mode = getattr(p, "is_scalp", False)
        engine.fast_market_fill = getattr(p, "fast_market_fill", False)
        engine.max_open_trades = getattr(p, "max_open_trades", 2)
        if getattr(p, "is_scalp", False):
            engine.scalp_tp_usd = getattr(p, "scalp_tp_usd", 2.50)
            engine.scalp_sl_usd = getattr(p, "scalp_sl_usd", 3.00)
            engine.scalp_tp_pct = getattr(p, "scalp_tp_pct", 0.10)
            engine.scalp_sl_pct = getattr(p, "scalp_sl_pct", 0.12)

    if config is not None:
        config.CURRENT_PROFILE = p.name
        config.MAX_OPEN_TRADES = getattr(p, "max_open_trades", 2)
        if getattr(p, "is_scalp", False):
            config.SCALP_ENABLED = True
            config.SCALP_TAKE_PROFIT_USD = getattr(p, "scalp_tp_usd", 2.50)
            config.SCALP_TAKE_PROFIT_PCT = getattr(p, "scalp_tp_pct", 0.10)
            config.SCALP_STOP_LOSS_USD = getattr(p, "scalp_sl_usd", 3.00)
            config.SCALP_STOP_LOSS_PCT = getattr(p, "scalp_sl_pct", 0.12)

    summary = (
        f"⚡ **STRATEGY PROFILE ACTIVATED: {p.name.upper()}**\n"
        f"_{p.description}_\n"
        f"• Agreement ≥ `{p.min_agreement}` agents | Min Score ≥ `{p.min_score}` | Min RR `{p.min_rr}:1`\n"
        f"• Risk `{p.base_risk_pct}%` | Max Lev `{p.max_leverage:.0f}x` | Asian Session `{'Allowed' if p.allow_asian else 'Blocked'}`\n"
    )
    if getattr(p, "is_scalp", False):
        summary += (
            f"• ⚡ **Rapid Micro-Scalping Mode**: ACTIVE\n"
            f"  - Micro TP: `{p.scalp_tp_pct:.2f}%` move or `${p.scalp_tp_usd:.2f}` USD profit\n"
            f"  - Micro SL: `{p.scalp_sl_pct:.2f}%` move or `${p.scalp_sl_usd:.2f}` USD loss\n"
            f"  - Polling: `{p.poll_interval}s` fast frequency | Market Fills: `Instant`\n"
            f"  - Max Open Slots: `{p.max_open_trades}` concurrent trades (Multi-position Gold & BTC)"
        )
    return p, summary
