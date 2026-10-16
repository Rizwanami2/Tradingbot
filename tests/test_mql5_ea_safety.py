from pathlib import Path
import re

EA = Path(__file__).resolve().parents[1] / "mt5" / "TradingBotDemoEA.mq5"
SOURCE = EA.read_text(encoding="utf-8")


def test_order_submission_disabled_by_default():
    assert re.search(r"input\s+bool\s+EnableTrading\s*=\s*false\s*;", SOURCE, re.I)


def test_live_account_order_submission_is_blocked():
    assert "ACCOUNT_TRADE_MODE_DEMO" in SOURCE
    assert "MQLInfoInteger(MQL_TESTER)" in SOURCE
    assert "if (!EnableTrading || !IsDemoOrTester()" in SOURCE
    assert "live accounts are prohibited" in SOURCE.lower()


def test_risk_and_daily_loss_guards_exist():
    assert "OrderCalcProfit" in SOURCE
    assert "MaxDailyLossPercent" in SOURCE
    assert "daily_halt" in SOURCE
    assert "volume < min_volume" in SOURCE


def test_signal_uses_completed_daily_bar_and_completed_signal_bar():
    assert 'iHigh(_Symbol, PERIOD_D1, 1)' in SOURCE
    assert 'iLow(_Symbol, PERIOD_D1, 1)' in SOURCE
    assert "MqlRates signal = bars[1]" in SOURCE


def test_stop_and_target_are_attached_to_orders():
    assert "trade.Buy(volume, _Symbol, 0.0, stop, target" in SOURCE
    assert "trade.Sell(volume, _Symbol, 0.0, stop, target" in SOURCE
