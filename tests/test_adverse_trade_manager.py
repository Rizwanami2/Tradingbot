#!/usr/bin/env python3
"""
Tests for Adverse Trade Manager (adverse_trade_manager.py)
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import pytest

from adverse_trade_manager import (
    AdverseAction,
    AdverseTradeDecision,
    AdverseTradeManager,
    TradeHealthStatus,
    get_adverse_trade_manager,
)


@dataclass
class DummyPosition:
    id: str = "ORD-TEST-1"
    symbol: str = "XAUUSD"
    direction: str = "LONG"
    executed_price: float = 2700.0
    entry_level: float = 2700.0
    stop_loss: float = 2690.0  # Initial risk = $10.0
    tp1: float = 2715.0        # +1.5R
    tp2: float = 2725.0
    size: float = 1.0
    lots: float = 0.10
    remaining_lots: float = 0.10
    remaining_size: float = 1.0
    highest_price: float = 2700.0
    lowest_price: float = 2700.0
    peak_r: float = 0.0
    bars_held: int = 0
    partials_done: int = 0
    executed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    _adverse_derisked: bool = False


def test_adverse_healthy():
    mgr = AdverseTradeManager()
    pos = DummyPosition()
    # Trade is slightly green: +$3.0 = +0.3R
    decision = mgr.diagnose(pos, current_price=2703.0)
    assert decision.action == AdverseAction.HOLD
    assert decision.health == TradeHealthStatus.HEALTHY
    assert decision.r_multiple == pytest.approx(0.3, rel=1e-2)


def test_adverse_stall_de_risk_50():
    mgr = AdverseTradeManager()
    pos = DummyPosition()
    # Trade drops to $2694.0: -$6.0 = -0.6R (worse than -0.50R threshold)
    decision = mgr.diagnose(pos, current_price=2694.0)
    assert decision.action == AdverseAction.DE_RISK_50
    assert decision.health == TradeHealthStatus.ADVERSE_STALL
    assert decision.close_ratio == 0.50
    assert decision.saved_risk_pct == 50.0
    assert decision.new_stop is not None
    # Tightened stop to -0.75R: 2700 - 0.75 * 10 = 2692.5
    assert decision.new_stop == pytest.approx(2692.5, rel=1e-2)


def test_adverse_stall_not_repeated_when_already_derisked():
    mgr = AdverseTradeManager()
    pos = DummyPosition()
    pos._adverse_derisked = True
    # Trade drops to $2694.0 but was already de-risked
    decision = mgr.diagnose(pos, current_price=2694.0)
    assert decision.action == AdverseAction.HOLD


def test_adverse_thesis_invalidation_emergency_exit():
    mgr = AdverseTradeManager()
    pos = DummyPosition()
    # Trade is in mild loss: $2697.0 = -0.3R, and opposing consensus fires
    decision = mgr.diagnose(pos, current_price=2697.0, opposing_consensus=True)
    assert decision.action == AdverseAction.EMERGENCY_EXIT
    assert decision.health == TradeHealthStatus.THESIS_INVALIDATED
    assert decision.close_ratio == 1.0
    assert decision.saved_risk_pct >= 60.0


def test_adverse_profit_fade_be_snap():
    mgr = AdverseTradeManager()
    pos = DummyPosition()
    # First, trade rallies up to $2708.0 = +0.8R
    mgr.diagnose(pos, current_price=2708.0)
    assert pos.peak_r >= 0.80

    # Then momentum collapses back to $2700.50 (+0.05R, under 0.12R threshold)
    decision = mgr.diagnose(pos, current_price=2700.50)
    assert decision.action == AdverseAction.BREAKEVEN_SNAP
    assert decision.health == TradeHealthStatus.PROFIT_FADING
    assert decision.new_stop is not None
    assert decision.new_stop > 2700.0  # Snapped to entry + buffer


def test_adverse_profit_fade_reversal_exit():
    mgr = AdverseTradeManager()
    pos = DummyPosition()
    # Trade peaked at +0.8R
    pos.peak_r = 0.80
    # Then slipped below entry to $2699.0 (-0.10R)
    decision = mgr.diagnose(pos, current_price=2699.0)
    assert decision.action == AdverseAction.EMERGENCY_EXIT
    assert decision.health == TradeHealthStatus.PROFIT_FADING
    assert decision.close_ratio == 1.0


def test_adverse_time_stagnation_scratch():
    mgr = AdverseTradeManager(max_stagnant_bars=25)
    pos = DummyPosition()
    pos.bars_held = 30  # Held for 30 bars
    pos.peak_r = 0.10   # Never took off
    # Price stuck at $2698.0 (-0.2R)
    decision = mgr.diagnose(pos, current_price=2698.0)
    assert decision.action == AdverseAction.STAGNATION_SCRATCH
    assert decision.health == TradeHealthStatus.STAGNANT
    assert decision.close_ratio == 1.0


def test_adverse_health_summary():
    mgr = AdverseTradeManager()
    p1 = DummyPosition(id="P1", executed_price=2700.0)
    p2 = DummyPosition(id="P2", executed_price=2700.0)
    summary = mgr.get_health_summary([p1, p2], current_prices={"XAUUSD": 2702.0})
    assert summary["total_open"] == 2
    assert summary["healthy"] == 2
    assert len(summary["positions"]) == 2


def test_execution_engine_adverse_de_risk():
    from execution_engine import ExecutionEngine
    from risk_manager import RiskManager

    rm = RiskManager(equity=10000.0, symbol="XAUUSD")
    events = []
    engine = ExecutionEngine(
        risk_manager=rm,
        event_callback=lambda ev, ord, msg: events.append({"event": ev, "order": ord, "message": msg}),
    )
    sig = {
        "symbol": "XAUUSD",
        "direction": "LONG",
        "entry": 2700.0,
        "stop": 2690.0,
        "tp1": 2715.0,
        "tp2": 2725.0,
        "lots": 0.10,
        "size": 10.0,
        "leverage": 20.0,
        "margin_required": 135.0,
        "risk_amount": 100.0,
        "rr": 1.5,
    }
    order = engine.register_setup(sig, reason="Test Setup")
    # Trigger execution fill @ 2700.0
    engine.update_market_candle("XAUUSD", {"high": 2701.0, "low": 2699.0, "close": 2700.0})
    assert order.id in engine.active_positions

    # Candle drops to 2694.0 (-0.6R) -> triggers ADVERSE_DE_RISK
    engine.update_market_candle("XAUUSD", {"high": 2695.0, "low": 2694.0, "close": 2694.0})
    derisk_events = [e for e in events if e["event"] == "ADVERSE_DE_RISK"]
    assert len(derisk_events) == 1
    assert order.remaining_lots == pytest.approx(0.05, rel=1e-2)  # 50% closed
    assert order.stop_loss == pytest.approx(2692.5, rel=1e-2)     # tightened to -0.75R

