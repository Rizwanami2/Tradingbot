#!/usr/bin/env python3
"""
Virtual / Hidden Stop Loss Manager (Anti-Stop-Hunting).
======================================================
Hides exact stop loss and take profit orders from broker order books.
Protects against B-book spread widening, wick targeting, and stop hunting.
The engine tracks virtual stops client-side and triggers instant market exits
upon true market price breaches.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("VirtualStops")
VIRTUAL_STOPS_FILE = Path("virtual_stops_state.json")


@dataclass
class VirtualStopRecord:
    symbol: str
    ticket: str
    direction: str  # "BUY" / "LONG" or "SELL" / "SHORT"
    entry_price: float
    virtual_stop: float
    virtual_tp1: Optional[float] = None
    virtual_tp2: Optional[float] = None
    emergency_broker_sl: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    active: bool = True
    trigger_reason: Optional[str] = None


class VirtualStopManager:
    """Manages client-side hidden stops and monitors live price ticks."""

    def __init__(self, state_file: Path = VIRTUAL_STOPS_FILE):
        self.state_file = state_file
        self.records: Dict[str, VirtualStopRecord] = {}
        self._load()

    def _load(self):
        if not self.state_file.is_file():
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    if v.get("active", True):
                        self.records[k] = VirtualStopRecord(**v)
        except Exception as e:
            logger.warning("Error loading virtual stops state: %s", e)

    def _save(self):
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump({k: asdict(v) for k, v in self.records.items()}, f, indent=2)
        except Exception as e:
            logger.warning("Error saving virtual stops state: %s", e)

    def register(
        self,
        symbol: str,
        ticket: str | int,
        direction: str,
        entry_price: float,
        virtual_stop: float,
        virtual_tp1: Optional[float] = None,
        virtual_tp2: Optional[float] = None,
        emergency_broker_sl: Optional[float] = None,
    ) -> VirtualStopRecord:
        key = str(ticket) if ticket else symbol
        rec = VirtualStopRecord(
            symbol=symbol,
            ticket=str(ticket),
            direction=direction.upper(),
            entry_price=entry_price,
            virtual_stop=virtual_stop,
            virtual_tp1=virtual_tp1,
            virtual_tp2=virtual_tp2,
            emergency_broker_sl=emergency_broker_sl,
        )
        self.records[key] = rec
        self._save()
        logger.info("Registered virtual stop for %s (SL: $%.2f, TP: $%.2f)", symbol, virtual_stop, virtual_tp1 or 0.0)
        return rec

    def update_stop(self, key: str | int, new_stop: float) -> bool:
        k = str(key)
        if k in self.records and self.records[k].active:
            self.records[k].virtual_stop = new_stop
            self._save()
            return True
        return False

    def remove(self, key: str | int) -> bool:
        k = str(key)
        if k in self.records:
            self.records[k].active = False
            self._save()
            return True
        return False

    def check_ticks(
        self,
        symbol: str,
        current_price: float,
    ) -> List[Tuple[VirtualStopRecord, str]]:
        """
        Evaluates current market price against all active virtual stops for the symbol.
        Returns list of (record, reason) tuples for any triggered stops.
        """
        triggered: List[Tuple[VirtualStopRecord, str]] = []
        for k, rec in list(self.records.items()):
            if not rec.active or rec.symbol != symbol:
                continue

            is_long = any(x in rec.direction for x in ("BUY", "LONG"))
            sl = rec.virtual_stop
            tp1 = rec.virtual_tp1

            # Check Stop Loss breach
            if is_long and current_price <= sl:
                rec.active = False
                rec.trigger_reason = f"Virtual Stop Loss triggered @ ${current_price:.2f} (SL: ${sl:.2f})"
                triggered.append((rec, rec.trigger_reason))
            elif not is_long and current_price >= sl:
                rec.active = False
                rec.trigger_reason = f"Virtual Stop Loss triggered @ ${current_price:.2f} (SL: ${sl:.2f})"
                triggered.append((rec, rec.trigger_reason))

            # Check Take Profit 1 breach
            elif tp1 is not None and tp1 > 0:
                if is_long and current_price >= tp1:
                    rec.active = False
                    rec.trigger_reason = f"Virtual Take Profit triggered @ ${current_price:.2f} (TP: ${tp1:.2f})"
                    triggered.append((rec, rec.trigger_reason))
                elif not is_long and current_price <= tp1:
                    rec.active = False
                    rec.trigger_reason = f"Virtual Take Profit triggered @ ${current_price:.2f} (TP: ${tp1:.2f})"
                    triggered.append((rec, rec.trigger_reason))

        if triggered:
            self._save()

        return triggered

    def active_stops(self) -> List[VirtualStopRecord]:
        return [r for r in self.records.values() if r.active]


_GLOBAL_VIRTUAL_STOPS: Optional[VirtualStopManager] = None


def get_virtual_stop_manager() -> VirtualStopManager:
    global _GLOBAL_VIRTUAL_STOPS
    if _GLOBAL_VIRTUAL_STOPS is None:
        _GLOBAL_VIRTUAL_STOPS = VirtualStopManager()
    return _GLOBAL_VIRTUAL_STOPS
