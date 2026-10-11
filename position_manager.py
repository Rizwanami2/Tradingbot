"""
Position Manager — Time stops, scale-out, MAE/MFE, max hold
===========================================================
Manages open trade lifecycle rules independent of the broker.

Rules applied each poll:
  - Max hold bars / hours → force close or tighten
  - Scale-out at 1R / 2R (partial exit levels)
  - MAE / MFE tracking for early exit if MFE fades
  - ATR trail after scale-out
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ManagedPosition:
    trade_id: str
    symbol: str
    direction: str  # LONG / SHORT
    entry: float
    stop: float
    tp1: float
    tp2: float = 0.0
    size: float = 1.0
    original_size: float = 1.0
    risk_per_unit: float = 0.0  # |entry - stop|
    opened_at: str = ""
    bars_held: int = 0
    mae: float = 0.0  # max adverse excursion in R
    mfe: float = 0.0  # max favorable excursion in R
    scaled_1r: bool = False
    scaled_2r: bool = False
    notes: List[str] = field(default_factory=list)


class PositionManager:
    def __init__(
        self,
        max_hold_bars: int = 240,          # ~4h on 1m
        max_hold_hours: float = 12.0,
        scale_1r_pct: float = 0.40,        # close 40% at 1R
        scale_2r_pct: float = 0.30,        # close another 30% at 2R
        fade_mfe_ratio: float = 0.45,      # if current R < 45% of peak MFE → tighten
        be_after_1r: bool = True,
    ):
        self.max_hold_bars = max_hold_bars
        self.max_hold_hours = max_hold_hours
        self.scale_1r_pct = scale_1r_pct
        self.scale_2r_pct = scale_2r_pct
        self.fade_mfe_ratio = fade_mfe_ratio
        self.be_after_1r = be_after_1r
        self.positions: Dict[str, ManagedPosition] = {}

    def add(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        entry: float,
        stop: float,
        tp1: float,
        size: float = 1.0,
        tp2: float = 0.0,
    ) -> ManagedPosition:
        risk = abs(entry - stop) or 1e-9
        pos = ManagedPosition(
            trade_id=trade_id,
            symbol=symbol.upper(),
            direction=direction.upper(),
            entry=float(entry),
            stop=float(stop),
            tp1=float(tp1),
            tp2=float(tp2 or 0),
            size=float(size),
            original_size=float(size),
            risk_per_unit=risk,
            opened_at=datetime.now(timezone.utc).isoformat(),
        )
        self.positions[trade_id] = pos
        return pos

    def remove(self, trade_id: str) -> None:
        self.positions.pop(trade_id, None)

    def _r_now(self, pos: ManagedPosition, price: float) -> float:
        if pos.direction == "LONG":
            return (price - pos.entry) / pos.risk_per_unit
        return (pos.entry - price) / pos.risk_per_unit

    def update(
        self,
        trade_id: str,
        price: float,
        bar_increment: int = 1,
    ) -> Dict[str, Any]:
        """
        Call every poll / new bar.
        Returns actions: {"close": bool, "partial": float, "new_stop": float|None, "reason": str}
        """
        pos = self.positions.get(trade_id)
        if pos is None:
            return {"close": False, "partial": 0.0, "new_stop": None, "reason": "not found"}

        pos.bars_held += bar_increment
        r = self._r_now(pos, price)

        # MAE / MFE
        if r < pos.mae:
            pos.mae = r
        if r > pos.mfe:
            pos.mfe = r

        actions: Dict[str, Any] = {
            "close": False,
            "partial": 0.0,
            "new_stop": None,
            "reason": "",
            "r": round(r, 3),
            "mae": round(pos.mae, 3),
            "mfe": round(pos.mfe, 3),
        }

        # 1) Max hold time
        if pos.bars_held >= self.max_hold_bars:
            actions["close"] = True
            actions["reason"] = f"max hold bars ({self.max_hold_bars})"
            return actions

        try:
            opened = datetime.fromisoformat(pos.opened_at.replace("Z", "+00:00"))
            hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600.0
            if hours >= self.max_hold_hours:
                actions["close"] = True
                actions["reason"] = f"max hold hours ({self.max_hold_hours})"
                return actions
        except Exception:
            pass

        # 2) Scale-out at 1R
        if not pos.scaled_1r and r >= 1.0:
            partial = pos.original_size * self.scale_1r_pct
            pos.size = max(0.0, pos.size - partial)
            pos.scaled_1r = True
            actions["partial"] = partial
            actions["reason"] = "scale-out 1R"
            if self.be_after_1r:
                # move stop to break-even (+ tiny buffer)
                be = pos.entry + (pos.risk_per_unit * 0.05 if pos.direction == "LONG" else -pos.risk_per_unit * 0.05)
                if pos.direction == "LONG" and be > pos.stop:
                    pos.stop = be
                    actions["new_stop"] = be
                elif pos.direction == "SHORT" and be < pos.stop:
                    pos.stop = be
                    actions["new_stop"] = be
            pos.notes.append("scaled 1R")
            return actions

        # 3) Scale-out at 2R
        if pos.scaled_1r and not pos.scaled_2r and r >= 2.0:
            partial = pos.original_size * self.scale_2r_pct
            pos.size = max(0.0, pos.size - partial)
            pos.scaled_2r = True
            actions["partial"] = partial
            actions["reason"] = "scale-out 2R"
            pos.notes.append("scaled 2R")
            return actions

        # 4) MFE fade → tighten stop toward entry + 0.3R
        if pos.mfe >= 1.2 and r < pos.mfe * self.fade_mfe_ratio:
            target_r = max(0.3, pos.mfe * 0.35)
            if pos.direction == "LONG":
                new_stop = pos.entry + target_r * pos.risk_per_unit
                if new_stop > pos.stop:
                    pos.stop = new_stop
                    actions["new_stop"] = new_stop
                    actions["reason"] = f"MFE fade tighten (peak {pos.mfe:.1f}R)"
            else:
                new_stop = pos.entry - target_r * pos.risk_per_unit
                if new_stop < pos.stop:
                    pos.stop = new_stop
                    actions["new_stop"] = new_stop
                    actions["reason"] = f"MFE fade tighten (peak {pos.mfe:.1f}R)"

        # 5) Hit original stop?
        if pos.direction == "LONG" and price <= pos.stop:
            actions["close"] = True
            actions["reason"] = "stop hit"
        elif pos.direction == "SHORT" and price >= pos.stop:
            actions["close"] = True
            actions["reason"] = "stop hit"

        return actions

    def summary(self) -> Dict[str, Any]:
        return {
            "open": len(self.positions),
            "ids": list(self.positions.keys()),
            "details": [
                {
                    "id": p.trade_id,
                    "symbol": p.symbol,
                    "dir": p.direction,
                    "bars": p.bars_held,
                    "mae": round(p.mae, 2),
                    "mfe": round(p.mfe, 2),
                    "scaled": f"{int(p.scaled_1r)}/{int(p.scaled_2r)}",
                }
                for p in self.positions.values()
            ],
        }
