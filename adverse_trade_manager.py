#!/usr/bin/env python3
"""
Adverse Trade Manager — Automated Position Health Diagnosis & Trade Rescue
=============================================================================
Monitors open trades on every candle/tick. When a position is "running not well"
(adverse drawdown, momentum stall, thesis invalidation, profit reversal, or
time stagnation in loss), the bot automatically decides the optimal risk mitigation:

1. EMERGENCY_EXIT (Thesis Invalidation):
   Cuts trade early when market consensus/indicators flip opposing while in loss,
   saving up to 60-75% of capital risk before full -1.0R stop loss is hit.

2. DE_RISK_50 (Adverse Stall Defense):
   Automatically trims 50% of the position when drawdown reaches -0.50R, halving
   active risk exposure while leaving a runner with a tightened stop.

3. BREAKEVEN_SNAP / PROFIT_FADE_PROTECT:
   Shields capital when a trade that reached +0.65R+ starts bleeding back toward entry.
   Locks stop at Break-Even or scratches the trade, preventing winners from becoming losers.

4. STAGNATION_SCRATCH (Time-Decay Exit):
   Scratches positions stuck in consolidation chop for 30+ bars while in negative territory,
   freeing up margin and capital for fresh high-probability setups.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("adverse_trade_manager")


class TradeHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    PROFIT_FADING = "PROFIT_FADING"
    STAGNANT = "STAGNANT"
    ADVERSE_STALL = "ADVERSE_STALL"
    THESIS_INVALIDATED = "THESIS_INVALIDATED"


class AdverseAction(str, Enum):
    HOLD = "HOLD"
    DE_RISK_50 = "DE_RISK_50"
    BREAKEVEN_SNAP = "BREAKEVEN_SNAP"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"
    STAGNATION_SCRATCH = "STAGNATION_SCRATCH"
    TIGHTEN_STOP = "TIGHTEN_STOP"


@dataclass
class AdverseTradeDecision:
    action: AdverseAction
    health: TradeHealthStatus
    r_multiple: float
    peak_r: float
    reason: str
    new_stop: Optional[float] = None
    close_ratio: float = 0.0  # 0.50 for 50% de-risk, 1.0 for full close
    saved_risk_pct: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


class AdverseTradeManager:
    """
    Intelligent autonomous trade doctor evaluating open positions and executing
    defensive risk-mitigation decisions when trades run poorly.
    """

    def __init__(
        self,
        enabled: bool = True,
        de_risk_r: float = -0.50,            # Trigger 50% cut when drawdown reaches -0.50R
        de_risk_stop_r: float = -0.75,       # Tighten stop to -0.75R on remaining 50%
        profit_fade_peak_r: float = 0.65,    # Peak gain required to activate profit fade protection
        profit_fade_trigger_r: float = 0.12, # Fade threshold where BE / protect triggers
        max_stagnant_bars: int = 30,         # Max bars in drawdown before stagnation scratch
        max_stagnant_minutes: float = 45.0,  # Max minutes in drawdown before stagnation scratch
        stagnant_max_r: float = -0.10,       # Must be below this R to be considered stagnant loss
        thesis_flip_r: float = -0.20,        # Minimum drawdown before opposing consensus triggers early exit
    ):
        self.enabled = enabled
        self.de_risk_r = de_risk_r
        self.de_risk_stop_r = de_risk_stop_r
        self.profit_fade_peak_r = profit_fade_peak_r
        self.profit_fade_trigger_r = profit_fade_trigger_r
        self.max_stagnant_bars = max_stagnant_bars
        self.max_stagnant_minutes = max_stagnant_minutes
        self.stagnant_max_r = stagnant_max_r
        self.thesis_flip_r = thesis_flip_r

    def diagnose(
        self,
        pos: Any,
        current_price: float,
        high: Optional[float] = None,
        low: Optional[float] = None,
        candle: Optional[Dict[str, Any]] = None,
        df: Optional[Any] = None,
        opposing_consensus: bool = False,
    ) -> AdverseTradeDecision:
        """
        Diagnose the health of an open trade and decide what action to take.
        """
        if not self.enabled or pos is None:
            return AdverseTradeDecision(
                action=AdverseAction.HOLD,
                health=TradeHealthStatus.HEALTHY,
                r_multiple=0.0,
                peak_r=0.0,
                reason="Adverse trade manager disabled or position empty",
            )

        entry = float(getattr(pos, "executed_price", None) or getattr(pos, "entry_level", 0.0) or current_price)
        stop = float(getattr(pos, "stop_loss", 0.0) or 0.0)
        initial_risk = abs(entry - stop) or (entry * 0.003)
        direction_str = str(getattr(pos, "direction", "LONG")).upper()
        is_long = direction_str in ("LONG", "BUY", "1")

        # 1. Calculate live R-multiple
        if is_long:
            r_multiple = (current_price - entry) / initial_risk
        else:
            r_multiple = (entry - current_price) / initial_risk

        # 2. Track Peak R & Bars Held
        current_peak_r = getattr(pos, "peak_r", None)
        if current_peak_r is None:
            # Reconstruct from highest_price / lowest_price
            hp = getattr(pos, "highest_price", entry) or entry
            lp = getattr(pos, "lowest_price", entry) or entry
            historical_peak = ((hp - entry) / initial_risk) if is_long else ((entry - lp) / initial_risk)
            pos.peak_r = max(0.0, historical_peak, r_multiple)
        else:
            pos.peak_r = max(current_peak_r, r_multiple)
        peak_r = pos.peak_r

        # Increment bars held
        pos.bars_held = getattr(pos, "bars_held", 0) + 1
        bars_held = pos.bars_held

        # Check elapsed minutes from executed_at
        minutes_held = 0.0
        exec_at = getattr(pos, "executed_at", None)
        if exec_at:
            try:
                dt_exec = datetime.fromisoformat(str(exec_at).replace("Z", "+00:00"))
                if dt_exec.tzinfo is None:
                    dt_exec = dt_exec.replace(tzinfo=timezone.utc)
                minutes_held = max(0.0, (datetime.now(timezone.utc) - dt_exec).total_seconds() / 60.0)
            except Exception:
                minutes_held = float(bars_held)

        details = {
            "r_multiple": round(r_multiple, 3),
            "peak_r": round(peak_r, 3),
            "bars_held": bars_held,
            "minutes_held": round(minutes_held, 1),
            "symbol": getattr(pos, "symbol", ""),
            "direction": direction_str,
            "entry": entry,
            "current_price": current_price,
            "initial_risk": initial_risk,
        }

        # -------------------------------------------------------------------
        # DECISION 1: THESIS INVALIDATION (Emergency Early Cut)
        # -------------------------------------------------------------------
        # If the trade is in negative territory and opposing consensus/momentum fired
        if opposing_consensus and r_multiple <= self.thesis_flip_r:
            saved_pct = max(0.0, min(100.0, (1.0 + r_multiple) * 100.0))
            return AdverseTradeDecision(
                action=AdverseAction.EMERGENCY_EXIT,
                health=TradeHealthStatus.THESIS_INVALIDATED,
                r_multiple=r_multiple,
                peak_r=peak_r,
                reason=(
                    f"Opposing market consensus fired while in loss ({r_multiple:+.2f}R). "
                    f"Auto-exiting early to prevent full -1.0R stop loss hit (saving ~{saved_pct:.0f}% risk)."
                ),
                close_ratio=1.0,
                saved_risk_pct=saved_pct,
                details=details,
            )

        # -------------------------------------------------------------------
        # DECISION 2: PROFIT FADE DEFENSE (Protect Big Gain From Turning Negative)
        # -------------------------------------------------------------------
        # If the trade was significantly green (peak >= 0.65R) but collapsed to near-flat (<= 0.12R)
        # and partials have not been taken yet
        partials_done = getattr(pos, "partials_done", 0)
        if peak_r >= self.profit_fade_peak_r and r_multiple <= self.profit_fade_trigger_r and partials_done == 0:
            buffer_price = initial_risk * 0.05
            target_be = (entry + buffer_price) if is_long else (entry - buffer_price)

            # If current price is still safely above entry (for long) or below entry (for short):
            if (is_long and current_price > entry) or (not is_long and current_price < entry):
                return AdverseTradeDecision(
                    action=AdverseAction.BREAKEVEN_SNAP,
                    health=TradeHealthStatus.PROFIT_FADING,
                    r_multiple=r_multiple,
                    peak_r=peak_r,
                    reason=(
                        f"Profit fade detected: Trade peaked at +{peak_r:.2f}R but faded to {r_multiple:+.2f}R. "
                        f"Snapping stop loss to Break-Even (${target_be:,.2f}) to protect capital."
                    ),
                    new_stop=target_be,
                    close_ratio=0.0,
                    saved_risk_pct=100.0,
                    details=details,
                )
            else:
                # Already slipping under entry: emergency scratch
                return AdverseTradeDecision(
                    action=AdverseAction.EMERGENCY_EXIT,
                    health=TradeHealthStatus.PROFIT_FADING,
                    r_multiple=r_multiple,
                    peak_r=peak_r,
                    reason=(
                        f"Profit fade reversal: Trade peaked at +{peak_r:.2f}R and slipped below entry ({r_multiple:+.2f}R). "
                        f"Auto-exited at scratch/breakeven to prevent winning trade from becoming a loser."
                    ),
                    close_ratio=1.0,
                    saved_risk_pct=90.0,
                    details=details,
                )

        # -------------------------------------------------------------------
        # DECISION 3: ADVERSE STALL DEFENSE (50% De-Risk & Tighten Stop)
        # -------------------------------------------------------------------
        # If the trade crosses deep drawdown threshold (-0.50R) and hasn't been de-risked yet
        already_derisked = getattr(pos, "_adverse_derisked", False)
        if r_multiple <= self.de_risk_r and not already_derisked:
            # Tighten remaining stop to -0.75R
            tighter_stop = (entry + self.de_risk_stop_r * initial_risk) if is_long else (entry - self.de_risk_stop_r * initial_risk)
            saved_pct = 50.0  # Immediate 50% size reduction saves 50% remaining risk
            return AdverseTradeDecision(
                action=AdverseAction.DE_RISK_50,
                health=TradeHealthStatus.ADVERSE_STALL,
                r_multiple=r_multiple,
                peak_r=peak_r,
                reason=(
                    f"Adverse stall detected: Trade deep in drawdown ({r_multiple:+.2f}R). "
                    f"Auto-trimming 50% size to halve capital exposure and tightening stop to -0.75R (${tighter_stop:,.2f})."
                ),
                new_stop=tighter_stop,
                close_ratio=0.50,
                saved_risk_pct=saved_pct,
                details=details,
            )

        # -------------------------------------------------------------------
        # DECISION 4: TIME STAGNATION IN DRAWDOWN ("Dead Trade" Scratch)
        # -------------------------------------------------------------------
        # If held for extended duration (30+ bars or 45+ min) in negative territory with no progress
        if (bars_held >= self.max_stagnant_bars or minutes_held >= self.max_stagnant_minutes) and r_multiple <= self.stagnant_max_r and peak_r < 0.30:
            saved_pct = max(0.0, (1.0 + r_multiple) * 100.0)
            return AdverseTradeDecision(
                action=AdverseAction.STAGNATION_SCRATCH,
                health=TradeHealthStatus.STAGNANT,
                r_multiple=r_multiple,
                peak_r=peak_r,
                reason=(
                    f"Time stagnation in drawdown: Position held {bars_held} bars ({minutes_held:.0f}m) in loss ({r_multiple:+.2f}R) "
                    f"without progression. Auto-scratching trade to free margin for high-conviction opportunities."
                ),
                close_ratio=1.0,
                saved_risk_pct=saved_pct,
                details=details,
            )

        # -------------------------------------------------------------------
        # DEFAULT: HEALTHY / HOLD
        # -------------------------------------------------------------------
        return AdverseTradeDecision(
            action=AdverseAction.HOLD,
            health=TradeHealthStatus.HEALTHY,
            r_multiple=r_multiple,
            peak_r=peak_r,
            reason="Trade running within normal parameters",
            details=details,
        )

    def get_health_summary(self, active_positions: List[Any], current_prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """
        Generate diagnostic health summary across all open positions.
        """
        prices = current_prices or {}
        summary = {
            "total_open": len(active_positions),
            "healthy": 0,
            "fading": 0,
            "stagnant": 0,
            "adverse": 0,
            "positions": [],
        }

        for pos in active_positions:
            sym = getattr(pos, "symbol", "")
            curr = prices.get(sym) or getattr(pos, "current_price", None) or getattr(pos, "executed_price", None) or getattr(pos, "entry_level", 0.0)
            diag = self.diagnose(pos, float(curr))

            if diag.health == TradeHealthStatus.HEALTHY:
                summary["healthy"] += 1
            elif diag.health == TradeHealthStatus.PROFIT_FADING:
                summary["fading"] += 1
            elif diag.health == TradeHealthStatus.STAGNANT:
                summary["stagnant"] += 1
            else:
                summary["adverse"] += 1

            summary["positions"].append({
                "id": getattr(pos, "id", "N/A"),
                "symbol": sym,
                "direction": getattr(pos, "direction", "N/A"),
                "r_multiple": diag.r_multiple,
                "peak_r": diag.peak_r,
                "health": diag.health.value,
                "action": diag.action.value,
                "reason": diag.reason,
            })

        return summary


_adverse_manager_instance: Optional[AdverseTradeManager] = None


def get_adverse_trade_manager() -> AdverseTradeManager:
    global _adverse_manager_instance
    if _adverse_manager_instance is None:
        _adverse_manager_instance = AdverseTradeManager()
    return _adverse_manager_instance
