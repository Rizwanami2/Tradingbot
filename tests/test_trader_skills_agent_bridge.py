#!/usr/bin/env python3
"""
Tests for Trader Skills Agent Bridge (trader_skills_agent_bridge.py)
"""
from datetime import datetime, timedelta, timezone
import pytest

from trader_skills_agent_bridge import (
    FundamentalSkillAgent,
    SkillRiskGate,
    SkillPortfolioCoordinator,
    SkillMarketHoursGuard,
    TraderSkillsBridge,
    get_skills_bridge,
    Dir,
    Vote,
)


def test_fundamental_agent_crypto():
    agent = FundamentalSkillAgent()
    vote = agent.vote("BTCUSDT")
    assert isinstance(vote, Vote)
    assert vote.direction == Dir.FLAT
    assert "Macro/Forex" in vote.reason or "fundamentals not applicable" in vote.reason


def test_fundamental_agent_equity():
    agent = FundamentalSkillAgent()
    vote = agent.vote("AAPL")
    assert isinstance(vote, Vote)
    assert vote.direction == Dir.LONG
    assert vote.strength > 0.0
    assert "Fundamental Synergy" in vote.reason


def test_risk_gate_order_ceiling():
    gate = SkillRiskGate(max_order_usd=5000.0)
    ok, reason, meta = gate.evaluate_order(
        symbol="AAPL",
        side="BUY",
        amount_usd=10000.0,
        price=150.0,
        current_cash=20000.0,
    )
    assert not ok
    assert "exceeds max order ceiling" in reason


def test_risk_gate_insufficient_cash():
    gate = SkillRiskGate(max_order_usd=10000.0, margin_buffer_pct=0.20)
    ok, reason, meta = gate.evaluate_order(
        symbol="AAPL",
        side="BUY",
        amount_usd=1000.0,
        price=150.0,
        current_cash=1100.0,  # Needs 1200 with 20% margin
    )
    assert not ok
    assert "Insufficient cash" in reason


def test_risk_gate_wash_sale_blocked():
    gate = SkillRiskGate()
    # Loss closed 10 days ago
    loss_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    recent_losses = [{"symbol": "AAPL", "closed_at": loss_date, "loss": -50.0}]

    ok, reason, meta = gate.evaluate_order(
        symbol="AAPL",
        side="BUY",
        amount_usd=1000.0,
        price=150.0,
        current_cash=5000.0,
        recent_closed_losses=recent_losses,
    )
    assert not ok
    assert "Wash sale rule triggered" in reason
    assert meta.get("wash_sale_days_ago") == 10


def test_risk_gate_wash_sale_expired_ok():
    gate = SkillRiskGate()
    # Loss closed 40 days ago (> 30 days IRS wash window)
    loss_date = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    recent_losses = [{"symbol": "AAPL", "closed_at": loss_date, "loss": -50.0}]

    ok, reason, meta = gate.evaluate_order(
        symbol="AAPL",
        side="BUY",
        amount_usd=1000.0,
        price=150.0,
        current_cash=5000.0,
        recent_closed_losses=recent_losses,
    )
    assert ok
    assert "Risk Gate Passed" in reason
    assert meta["symbol"] == "AAPL"


def test_portfolio_coordinator_budget():
    coord = SkillPortfolioCoordinator()
    targets = {"AAPL": 0.5, "MSFT": 0.5}
    res = coord.calculate_allocation(total_budget_usd=1000.0, targets=targets, max_single_stock_pct=0.6)
    assert res["ok"]
    assert "allocations" in res
    assert len(res["allocations"]) == 2


def test_portfolio_coordinator_rebalance():
    coord = SkillPortfolioCoordinator()
    holdings = {"AAPL": 10.0, "MSFT": 5.0}
    weights = {"AAPL": 0.5, "MSFT": 0.5}
    prices = {"AAPL": 150.0, "MSFT": 300.0}
    res = coord.detect_rebalance_drift(holdings, weights, prices)
    assert res["ok"]
    assert "plan" in res


def test_market_hours_guard_crypto():
    guard = SkillMarketHoursGuard()
    open_ok, reason = guard.is_market_open("BTCUSDT")
    assert open_ok
    assert "24/7" in reason


def test_market_hours_guard_nyse():
    guard = SkillMarketHoursGuard()
    # Sunday at 18:00 UTC
    sun_dt = datetime(2026, 10, 25, 18, 0, tzinfo=timezone.utc)
    open_ok, reason = guard.is_market_open("AAPL", ts=sun_dt)
    assert not open_ok
    assert "weekend" in reason.lower() or "closed" in reason.lower()


def test_unified_skills_bridge():
    bridge = get_skills_bridge()
    assert isinstance(bridge, TraderSkillsBridge)

    vote = bridge.get_fundamental_vote("AAPL")
    assert isinstance(vote, Vote)

    ok, reason, meta = bridge.verify_order_safety(
        symbol="BTCUSDT",
        side="BUY",
        amount_usd=500.0,
        price=60000.0,
        current_cash=2000.0,
    )
    assert ok
    assert "Risk Gate Passed" in reason
