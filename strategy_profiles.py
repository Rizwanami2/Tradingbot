"""
Configurable Strategy Profiles
==============================
JSON-serializable profiles that change agent weights, RR, session rules, etc.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, Tuple

PROFILES_FILE = "strategy_profiles.json"


@dataclass
class StrategyProfile:
    name: str
    description: str = ""
    weights: Dict[str, float] = field(default_factory=lambda: {
        "TREND": 1.25, "LIQUIDITY": 1.40, "INDICATORS": 1.00,
        "AI-LEARNER": 0.90, "NEWS": 0.70, "GAUTAM-JHA": 1.30, "MEAN-REVERSION": 1.20,
        "FAKE-BREAKOUT": 1.35, "MTF-CONFLUENCE": 1.40, "ASIAN-BREAKOUT": 1.25,
        "MT5-CHART": 1.35, "ORR-STRATEGY": 1.50,
        "SUPPLY-DEMAND": 1.45, "OTE-STRATEGY": 1.45,
        "VOLATILITY-SQUEEZE": 1.55, "RIBBON-SCALP": 1.50, "STOCH-RSI-SCALP": 1.50,
    })
    min_agreement: int = 2
    min_score: float = 1.2
    min_rr: float = 1.9
    require_killzone: bool = False
    allow_asian: bool = True
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
    max_open_trades: int = 6        # Up to 6 slots (3 Gold, 3 BTC)
    # Scalp Trailing Stop Loss & Position Controls
    scalp_trail_enabled: bool = True
    scalp_trail_activation_usd: float = 2.00
    scalp_trail_distance_usd: float = 2.00
    scalp_trail_activation_pct: float = 0.06
    scalp_trail_distance_pct: float = 0.06
    scalp_trail_buffer_usd: float = 1.50
    scalp_trail_buffer_pct: float = 0.05
    scalp_be_lock_usd: float = 0.50
    scalp_min_trail_profit_usd: float = 1.50
    scalp_profit_retention_pct: float = 0.50
    scalp_let_profit_run: bool = True
    scalp_entry_cooldown_sec: float = 45.0
    scalp_loss_cooldown_sec: float = 90.0
    scalp_global_cooldown_sec: float = 10.0
    scalp_max_positions_per_symbol: int = 3
    scalp_min_price_distance_pct: float = 0.03


DEFAULT_PROFILES = {
    "balanced": StrategyProfile(
        name="balanced",
        description="XAUUSD balanced: HTF + liquidity, London/NY, RR~1.9 (not micro-scalp)",
        weights={
            "TREND": 1.35, "LIQUIDITY": 1.55, "INDICATORS": 1.05, "AI-LEARNER": 0.90,
            "NEWS": 0.85, "GAUTAM-JHA": 1.35, "MEAN-REVERSION": 1.15, "FAKE-BREAKOUT": 1.40,
            "MTF-CONFLUENCE": 1.50, "ASIAN-BREAKOUT": 0.70, "MT5-CHART": 1.40, "ORR-STRATEGY": 1.45,
            "SUPPLY-DEMAND": 1.50, "OTE-STRATEGY": 1.50, "VOLATILITY-SQUEEZE": 0.80,
            "RIBBON-SCALP": 0.70, "STOCH-RSI-SCALP": 0.70, "AUTO-TREND-1M": 0.75,
        },
        min_agreement=2, min_score=1.35, min_rr=1.9, require_killzone=True, allow_asian=False,
        strict_htf=True, base_risk_pct=0.20, max_leverage=20.0, is_scalp=False,
        max_open_trades=3, scalp_max_positions_per_symbol=2,
    ),



    "scalp": StrategyProfile(
        name="scalp",
        description="XAUUSD micro-scalp: fast momentum, tight TP/SL, spread-aware",
        weights={
            "TREND": 1.10,
            "LIQUIDITY": 1.50,
            "INDICATORS": 1.40,
            "AI-LEARNER": 1.00,
            "NEWS": 0.50,
            "GAUTAM-JHA": 1.20,
            "MEAN-REVERSION": 1.30,
            "FAKE-BREAKOUT": 1.35,
            "MTF-CONFLUENCE": 1.40,
            "ASIAN-BREAKOUT": 0.80,
            "MT5-CHART": 1.35,
            "ORR-STRATEGY": 1.50,
            "SUPPLY-DEMAND": 1.20,
            "OTE-STRATEGY": 1.20,
            "VOLATILITY-SQUEEZE": 1.60,
            "RIBBON-SCALP": 1.55,
            "STOCH-RSI-SCALP": 1.60,
            "AUTO-TREND-1M": 1.60,
        },
        min_agreement=2,
        min_score=1.1,
        min_rr=1.15,
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
        max_open_trades=6,
        scalp_trail_enabled=True,
        scalp_trail_activation_usd=2.00,
        scalp_trail_distance_usd=2.00,
        scalp_trail_activation_pct=0.06,
        scalp_trail_distance_pct=0.06,
        scalp_trail_buffer_usd=1.50,
        scalp_trail_buffer_pct=0.05,
        scalp_be_lock_usd=0.50,
        scalp_min_trail_profit_usd=1.50,
        scalp_profit_retention_pct=0.50,
        scalp_let_profit_run=True,
        scalp_entry_cooldown_sec=45.0,
        scalp_loss_cooldown_sec=90.0,
        scalp_global_cooldown_sec=10.0,
        scalp_max_positions_per_symbol=3,
        scalp_min_price_distance_pct=0.03,
    ),
    "swing": StrategyProfile(
        name="swing",
        description="Higher TF focus, tight high-conviction setup, wider targets",
        weights={"TREND": 1.5, "LIQUIDITY": 1.3, "INDICATORS": 0.9, "AI-LEARNER": 1.0, "NEWS": 0.8, "FAKE-BREAKOUT": 1.25, "MTF-CONFLUENCE": 1.50, "ASIAN-BREAKOUT": 1.20, "MT5-CHART": 1.40, "ORR-STRATEGY": 1.45, "SUPPLY-DEMAND": 1.50, "OTE-STRATEGY": 1.50, "VOLATILITY-SQUEEZE": 1.40, "RIBBON-SCALP": 1.35, "STOCH-RSI-SCALP": 1.35, "AUTO-TREND-1M": 1.55},
        min_agreement=3,
        min_score=1.70,
        min_rr=2.5,
        require_killzone=False,
        base_risk_pct=0.30,
        max_leverage=10.0,
        strict_htf=True,
        max_open_trades=6,
        scalp_max_positions_per_symbol=3,
    ),
    "gold_only": StrategyProfile(
        name="gold_only",
        description="Optimized for XAU / Gold",
        weights={"TREND": 1.2, "LIQUIDITY": 1.6, "INDICATORS": 1.0, "AI-LEARNER": 0.85, "NEWS": 1.0, "FAKE-BREAKOUT": 1.45, "MTF-CONFLUENCE": 1.45, "ASIAN-BREAKOUT": 1.35, "MT5-CHART": 1.45, "ORR-STRATEGY": 1.55, "SUPPLY-DEMAND": 1.55, "OTE-STRATEGY": 1.55, "VOLATILITY-SQUEEZE": 1.55, "RIBBON-SCALP": 1.50, "STOCH-RSI-SCALP": 1.50, "AUTO-TREND-1M": 1.60},
        min_agreement=2,
        min_score=1.20,
        min_rr=1.5,
        require_killzone=False,
        allow_asian=True,
        strict_htf=False,
        base_risk_pct=0.22,
        max_leverage=40.0,
        max_open_trades=6,
        scalp_max_positions_per_symbol=6,
    ),
    "crypto": StrategyProfile(
        name="crypto",
        description="Crypto-focused, lower leverage",
        weights={"TREND": 1.3, "LIQUIDITY": 1.2, "INDICATORS": 1.1, "AI-LEARNER": 1.0, "NEWS": 1.1, "FAKE-BREAKOUT": 1.30, "MTF-CONFLUENCE": 1.35, "ASIAN-BREAKOUT": 1.25, "MT5-CHART": 1.30, "ORR-STRATEGY": 1.45, "SUPPLY-DEMAND": 1.45, "OTE-STRATEGY": 1.45, "VOLATILITY-SQUEEZE": 1.55, "RIBBON-SCALP": 1.50, "STOCH-RSI-SCALP": 1.50, "AUTO-TREND-1M": 1.60},
        min_agreement=2,
        min_score=1.25,
        min_rr=2.0,
        base_risk_pct=0.18,
        max_leverage=15.0,
        allow_asian=True,
        max_open_trades=6,
        scalp_max_positions_per_symbol=6,
    ),
    "xau_scalp": StrategyProfile(
        name="xau_scalp",
        description="XAUUSD-optimized micro-scalp: liquidity+MTF, London/NY, tight risk, spread filter",
        weights={
            "TREND": 1.25,
            "LIQUIDITY": 1.55,
            "INDICATORS": 1.25,
            "AI-LEARNER": 1.05,
            "NEWS": 0.85,
            "GAUTAM-JHA": 1.40,
            "MEAN-REVERSION": 1.15,
            "FAKE-BREAKOUT": 1.45,
            "MTF-CONFLUENCE": 1.50,
            "ASIAN-BREAKOUT": 0.90,
            "MT5-CHART": 1.40,
            "ORR-STRATEGY": 1.50,
            "SUPPLY-DEMAND": 1.50,
            "OTE-STRATEGY": 1.50,
            "VOLATILITY-SQUEEZE": 1.60,
            "RIBBON-SCALP": 1.55,
            "STOCH-RSI-SCALP": 1.60,
            "AUTO-TREND-1M": 1.60,
        },
        min_agreement=2,
        min_score=1.15,
        min_rr=1.20,
        require_killzone=False,
        allow_asian=False,
        base_risk_pct=0.15,
        max_leverage=15.0,
        strict_htf=False,
        is_scalp=True,
        fast_market_fill=True,
        scalp_tp_pct=0.08,
        scalp_sl_pct=0.10,
        scalp_tp_usd=3.00,
        scalp_sl_usd=2.50,
        poll_interval=2,
        fast_poll_interval=1,
        max_open_trades=2,
        scalp_trail_enabled=True,
        scalp_trail_activation_usd=2.00,
        scalp_trail_distance_usd=2.00,
        scalp_trail_activation_pct=0.06,
        scalp_trail_distance_pct=0.06,
        scalp_trail_buffer_usd=1.50,
        scalp_trail_buffer_pct=0.05,
        scalp_be_lock_usd=0.50,
        scalp_min_trail_profit_usd=1.50,
        scalp_profit_retention_pct=0.55,
        scalp_let_profit_run=True,
        scalp_entry_cooldown_sec=60.0,
        scalp_loss_cooldown_sec=120.0,
        scalp_global_cooldown_sec=15.0,
        scalp_max_positions_per_symbol=1,
        scalp_min_price_distance_pct=0.10,
    ),
}

CURRENT_PROFILE: str = os.getenv("BOT_PROFILE", "balanced").lower()


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
    lock: bool = False,
) -> Tuple[StrategyProfile, str]:
    """Applies a strategy profile across arbiter, risk manager, execution engine, and config in runtime."""
    global CURRENT_PROFILE
    profiles = load_profiles()
    name = str(name).lower().strip()
    if name not in profiles:
        raise ValueError(f"Unknown strategy profile '{name}'. Available: {', '.join(profiles.keys())}")
    p = profiles[name]
    CURRENT_PROFILE = p.name

    if lock:
        if config is not None:
            config.PROFILE_LOCKED = True
        if arbiter is not None:
            arbiter.profile_locked = True

    if arbiter is not None:
        arbiter.WEIGHTS = dict(p.weights)
        arbiter.MIN_AGREEMENT = p.min_agreement
        arbiter.MIN_SCORE = p.min_score
        arbiter.min_rr = p.min_rr
        acc_on = str(getattr(config, "ACCURACY_MODE", "") or os.getenv("BOT_ACCURACY_MODE", "")).lower() in ("true", "1", "yes", "on")
        if getattr(p, "is_scalp", False):
            arbiter.min_effective_rr = 1.15 if acc_on else max(0.85, float(getattr(p, "min_rr", 1.0) or 1.0) - 0.15)
        else:
            arbiter.min_effective_rr = 1.4
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
            risk_mgr.config.tp1_r = max(1.0, float(getattr(p, "min_rr", 1.0) or 1.0))
            risk_mgr.config.tp2_r = max(1.5, float(getattr(p, "min_rr", 1.0) or 1.0) * 1.5)
            risk_mgr.set_leverage(min(getattr(p, "max_leverage", 20.0), getattr(risk_mgr.config, "max_leverage", 200.0)))
        else:
            risk_mgr.config.tp1_r = 1.5
            risk_mgr.config.tp2_r = 2.5
            risk_mgr.config.tp3_r = 4.0
            risk_mgr.set_leverage(min(getattr(p, "max_leverage", 20.0), getattr(risk_mgr.config, "max_leverage", 200.0)))

    if engine is not None:
        engine.scalp_mode = getattr(p, "is_scalp", False)
        engine.fast_market_fill = getattr(p, "fast_market_fill", False)
        if getattr(engine, "slots_mode", "auto") == "manual":
            engine.max_open_trades = min(6, max(1, getattr(engine, "manual_slots", 6)))
        else:
            engine.max_open_trades = min(6, max(1, getattr(p, "max_open_trades", 6)))
        engine.max_positions_per_symbol = getattr(p, "scalp_max_positions_per_symbol", 3)
        if getattr(p, "is_scalp", False):
            engine.scalp_tp_usd = getattr(p, "scalp_tp_usd", 2.50)
            engine.scalp_sl_usd = getattr(p, "scalp_sl_usd", 3.00)
            engine.scalp_tp_pct = getattr(p, "scalp_tp_pct", 0.10)
            engine.scalp_sl_pct = getattr(p, "scalp_sl_pct", 0.12)
            engine.scalp_trail_enabled = getattr(p, "scalp_trail_enabled", True)
            engine.scalp_trail_activation_usd = getattr(p, "scalp_trail_activation_usd", 2.00)
            engine.scalp_trail_distance_usd = getattr(p, "scalp_trail_distance_usd", 2.00)
            engine.scalp_trail_activation_pct = getattr(p, "scalp_trail_activation_pct", 0.06)
            engine.scalp_trail_distance_pct = getattr(p, "scalp_trail_distance_pct", 0.06)
            engine.scalp_trail_buffer_usd = getattr(p, "scalp_trail_buffer_usd", 1.50)
            engine.scalp_trail_buffer_pct = getattr(p, "scalp_trail_buffer_pct", 0.05)
            engine.scalp_be_lock_usd = getattr(p, "scalp_be_lock_usd", 0.50)
            engine.scalp_min_trail_profit_usd = getattr(p, "scalp_min_trail_profit_usd", 1.50)
            engine.scalp_profit_retention_pct = getattr(p, "scalp_profit_retention_pct", 0.50)
            engine.scalp_let_profit_run = getattr(p, "scalp_let_profit_run", True)

    if config is not None:
        config.CURRENT_PROFILE = p.name
        config.MAX_OPEN_TRADES = getattr(p, "max_open_trades", 6)
        config.MAX_POSITIONS_PER_SYMBOL = getattr(p, "scalp_max_positions_per_symbol", 3)
        if getattr(p, "is_scalp", False):
            config.SCALP_ENABLED = True
            config.SCALP_TAKE_PROFIT_USD = getattr(p, "scalp_tp_usd", 2.50)
            config.SCALP_TAKE_PROFIT_PCT = getattr(p, "scalp_tp_pct", 0.10)
            config.SCALP_STOP_LOSS_USD = getattr(p, "scalp_sl_usd", 3.00)
            config.SCALP_STOP_LOSS_PCT = getattr(p, "scalp_sl_pct", 0.12)
            config.SCALP_TRAIL_ENABLED = getattr(p, "scalp_trail_enabled", True)
            config.SCALP_TRAIL_ACTIVATION_USD = getattr(p, "scalp_trail_activation_usd", 2.00)
            config.SCALP_TRAIL_DISTANCE_USD = getattr(p, "scalp_trail_distance_usd", 2.00)
            config.SCALP_TRAIL_ACTIVATION_PCT = getattr(p, "scalp_trail_activation_pct", 0.06)
            config.SCALP_TRAIL_DISTANCE_PCT = getattr(p, "scalp_trail_distance_pct", 0.06)
            config.SCALP_TRAIL_BUFFER_USD = getattr(p, "scalp_trail_buffer_usd", 1.50)
            config.SCALP_TRAIL_BUFFER_PCT = getattr(p, "scalp_trail_buffer_pct", 0.05)
            config.SCALP_BE_LOCK_USD = getattr(p, "scalp_be_lock_usd", 0.50)
            config.SCALP_MIN_TRAIL_PROFIT_USD = getattr(p, "scalp_min_trail_profit_usd", 1.50)
            config.SCALP_PROFIT_RETENTION_PCT = getattr(p, "scalp_profit_retention_pct", 0.50)
            config.SCALP_LET_PROFIT_RUN = getattr(p, "scalp_let_profit_run", True)
            config.SCALP_ENTRY_COOLDOWN_SEC = getattr(p, "scalp_entry_cooldown_sec", 45.0)
            config.SCALP_LOSS_COOLDOWN_SEC = getattr(p, "scalp_loss_cooldown_sec", 90.0)
            config.SCALP_GLOBAL_COOLDOWN_SEC = getattr(p, "scalp_global_cooldown_sec", 10.0)
            config.SCALP_MAX_POSITIONS_PER_SYMBOL = getattr(p, "scalp_max_positions_per_symbol", 3)
            config.SCALP_MIN_PRICE_DISTANCE_PCT = getattr(p, "scalp_min_price_distance_pct", 0.03)
        else:
            config.SCALP_ENABLED = False

    try:
        from anti_spam import get_anti_spam_limiter
        limiter = get_anti_spam_limiter()
        limiter.entry_cooldown_sec = int(getattr(p, "scalp_entry_cooldown_sec", 45))
        limiter.loss_cooldown_sec = int(getattr(p, "scalp_loss_cooldown_sec", 90))
        limiter.global_cooldown_sec = int(getattr(p, "scalp_global_cooldown_sec", 10))
        limiter.max_positions_per_symbol = int(getattr(p, "scalp_max_positions_per_symbol", 3))
        limiter.min_price_distance_pct = float(getattr(p, "scalp_min_price_distance_pct", 0.03))
    except Exception:
        pass

    summary = (
        f"⚡ **STRATEGY PROFILE ACTIVATED: {p.name.upper()}**\n"
        f"_{p.description}_\n"
        f"• Agreement ≥ `{p.min_agreement}` agents | Min Score ≥ `{p.min_score}` | Min RR `{p.min_rr}:1`\n"
        f"• Risk `{p.base_risk_pct}%` | Max Lev `{p.max_leverage:.0f}x` | Asian Session `{'Allowed' if p.allow_asian else 'Blocked'}`\n"
        f"• Concurrency: Max `{getattr(p, 'max_open_trades', 6)}` positions (Max {getattr(p, 'scalp_max_positions_per_symbol', 3)} Gold, Max {getattr(p, 'scalp_max_positions_per_symbol', 3)} BTC)\n"
    )
    if getattr(p, "is_scalp", False):
        summary += (
            f"• ⚡ **Rapid Micro-Scalping Mode**: ACTIVE\n"
            f"  - Micro TP: `{p.scalp_tp_pct:.2f}%` move or `${p.scalp_tp_usd:.2f}` USD profit\n"
            f"  - Micro SL: `{p.scalp_sl_pct:.2f}%` move or `${p.scalp_sl_usd:.2f}` USD loss\n"
            f"  - 🛡️ Dynamic Trailing SL: ACTIVE (Trigger `${getattr(p, 'scalp_trail_activation_usd', 2.0):.2f}` / Trail `${getattr(p, 'scalp_trail_distance_usd', 2.0):.2f}` | Buffer `${getattr(p, 'scalp_trail_buffer_usd', 1.5):.2f}` | Min Floor `+${getattr(p, 'scalp_min_trail_profit_usd', 1.5):.2f}`)\n"
            f"  - 🚀 Run-to-TP Expansion: `{'ACTIVE (holds on strong momentum)' if getattr(p, 'scalp_let_profit_run', True) else 'DISABLED'}`\n"
            f"  - 🎯 High-Accuracy Scalp Filters: MTF trend alignment & structure confirmation enabled\n"
            f"  - Polling: `{p.poll_interval}s` fast frequency | Market Fills: `Instant`\n"
            f"  - Max Open Slots: `{p.max_open_trades}` concurrent trades (Multi-position Gold & BTC)"
        )
    return p, summary
