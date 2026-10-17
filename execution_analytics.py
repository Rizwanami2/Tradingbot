#!/usr/bin/env python3
"""
Realized Slippage & Execution Latency Analytics Engine.
======================================================
Monitors institutional direct-execution quality:
- Roundtrip execution latency (order send -> broker ack -> trade fill).
- Realized slippage (requested vs executed fill price) in points, %, and USD.
- Broker Execution Quality Index (A+ to F rating).
- Adverse slippage bias detection (flags asymmetric broker execution).
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ExecutionAnalytics")
ANALYTICS_FILE = Path("execution_analytics.json")


@dataclass
class ExecutionRecord:
    ticket: str
    symbol: str
    action: str  # "BUY" / "SELL"
    requested_price: float
    executed_price: float
    volume: float
    slippage_pts: float
    slippage_pct: float
    slippage_usd: float
    latency_ms: float
    is_adverse: bool
    timestamp: float = field(default_factory=time.time)
    broker: str = "MT5"


@dataclass
class BrokerQualityReport:
    total_executions: int = 0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    avg_slippage_pts: float = 0.0
    total_slippage_usd: float = 0.0
    adverse_fills_pct: float = 0.0
    favorable_fills_pct: float = 0.0
    execution_rating: str = "A"  # A, B, C, D, F
    recommendation: str = ""


class ExecutionAnalyticsTracker:
    """Tracks latency, slippage, and broker execution health."""

    def __init__(self, storage_file: Path = ANALYTICS_FILE):
        self.storage_file = storage_file
        self.records: List[ExecutionRecord] = []
        self._load()

    def _load(self):
        if not self.storage_file.is_file():
            return
        try:
            with open(self.storage_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for d in data[-500:]:
                        self.records.append(ExecutionRecord(**d))
        except Exception as e:
            logger.warning("Error loading execution analytics: %s", e)

    def _save(self):
        try:
            with open(self.storage_file, "w", encoding="utf-8") as f:
                json.dump([asdict(r) for r in self.records[-500:]], f, indent=2)
        except Exception as e:
            logger.warning("Error saving execution analytics: %s", e)

    def record_fill(
        self,
        ticket: str | int,
        symbol: str,
        action: str,
        requested_price: float,
        executed_price: float,
        volume: float,
        latency_ms: float,
        broker: str = "MT5",
        point_multiplier: float = 1.0,
    ) -> ExecutionRecord:
        action_upper = action.upper()
        req = float(requested_price)
        fill = float(executed_price)
        vol = float(volume)

        # Slippage points calculation:
        # BUY: fill > req means adverse (paid more)
        # SELL: fill < req means adverse (sold for less)
        is_buy = any(x in action_upper for x in ("BUY", "LONG"))
        if is_buy:
            slip_pts = fill - req
            is_adverse = fill > req
        else:
            slip_pts = req - fill
            is_adverse = fill < req

        slip_pct = (abs(fill - req) / max(req, 1e-9)) * 100.0 if req > 0 else 0.0
        # Financial impact in USD (approximate: slip_pts * volume * point_multiplier)
        slip_usd = slip_pts * vol * point_multiplier

        rec = ExecutionRecord(
            ticket=str(ticket),
            symbol=symbol,
            action=action_upper,
            requested_price=req,
            executed_price=fill,
            volume=vol,
            slippage_pts=round(slip_pts, 4),
            slippage_pct=round(slip_pct, 4),
            slippage_usd=round(slip_usd, 2),
            latency_ms=round(float(latency_ms), 2),
            is_adverse=is_adverse,
            broker=broker,
        )

        self.records.append(rec)
        self._save()
        logger.info(
            "Execution filled: %s %s @ $%.2f (slip: %+.2f pts, latency: %.1fms, adverse: %s)",
            symbol, action_upper, fill, slip_pts, latency_ms, is_adverse
        )
        return rec

    def generate_report(self) -> BrokerQualityReport:
        if not self.records:
            return BrokerQualityReport(recommendation="No executions recorded yet")

        n = len(self.records)
        latencies = [r.latency_ms for r in self.records]
        slippages = [r.slippage_pts for r in self.records]
        total_usd = sum(r.slippage_usd for r in self.records)
        adverse_count = sum(1 for r in self.records if r.is_adverse)
        favorable_count = sum(1 for r in self.records if not r.is_adverse and r.slippage_pts < 0)

        avg_lat = float(sum(latencies) / n)
        p95_lat = float(sorted(latencies)[int(n * 0.95)] if n >= 20 else max(latencies))
        avg_slip = float(sum(slippages) / n)
        adverse_pct = round((adverse_count / n) * 100.0, 1)
        favorable_pct = round((favorable_count / n) * 100.0, 1)

        # Rate broker execution quality
        if avg_lat < 80 and adverse_pct <= 55 and abs(avg_slip) < 0.5:
            rating = "A+"
            rec = "Institutional Direct Market Access grade execution"
        elif avg_lat < 180 and adverse_pct <= 65:
            rating = "B"
            rec = "Good retail STP execution; within acceptable bounds"
        elif avg_lat < 400:
            rating = "C"
            rec = "Moderate latency detected; consider checking server ping or VPS proximity"
        else:
            rating = "F"
            rec = "Excessive latency or asymmetric slippage; suspect B-book broker dealer intervention"

        return BrokerQualityReport(
            total_executions=n,
            avg_latency_ms=round(avg_lat, 2),
            p95_latency_ms=round(p95_lat, 2),
            avg_slippage_pts=round(avg_slip, 4),
            total_slippage_usd=round(total_usd, 2),
            adverse_fills_pct=adverse_pct,
            favorable_fills_pct=favorable_pct,
            execution_rating=rating,
            recommendation=rec,
        )


_GLOBAL_TRACKER: Optional[ExecutionAnalyticsTracker] = None


def get_execution_tracker() -> ExecutionAnalyticsTracker:
    global _GLOBAL_TRACKER
    if _GLOBAL_TRACKER is None:
        _GLOBAL_TRACKER = ExecutionAnalyticsTracker()
    return _GLOBAL_TRACKER
