import csv
import os
from pathlib import Path

import pytest

from mt5_signal_bridge import (
    PendingSignalError,
    SignalValidationError,
    TradeSignal,
    common_files_root,
    publish_signal,
    validate_signal,
)


def make_signal(**overrides):
    values = {
        "signal_id": "unit_test_001",
        "symbol": "XAUUSD",
        "side": "BUY",
        "volume": 0.01,
        "entry_price": 2350.0,
        "stop_loss": 2340.0,
        "take_profit": 2370.0,
        "created_at": 1_800_000_000,
        "expires_at": 1_800_000_120,
    }
    values.update(overrides)
    return TradeSignal(**values)


def test_publish_signal_writes_expected_pipe_delimited_schema(tmp_path):
    signal = make_signal()
    path = publish_signal(signal, tmp_path, now=1_800_000_010)

    assert path == tmp_path / "TradingBot" / "signal.csv"
    with path.open(encoding="ascii", newline="") as stream:
        rows = list(csv.reader(stream, delimiter="|"))
    assert len(rows) == 1
    assert rows[0] == [
        "1", "unit_test_001", "XAUUSD", "BUY", "0.01",
        "2350", "2340", "2370", "1800000000", "1800000120",
    ]


def test_publish_signal_never_overwrites_pending_signal(tmp_path):
    signal = make_signal()
    publish_signal(signal, tmp_path, now=1_800_000_010)

    with pytest.raises(PendingSignalError):
        publish_signal(
            make_signal(signal_id="unit_test_002"),
            tmp_path,
            now=1_800_000_010,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"side": "HOLD"},
        {"volume": 0},
        {"volume": float("nan")},
        {"stop_loss": 2360.0},  # BUY stop must be below entry
        {"take_profit": 2340.0},  # BUY target must be above entry
        {"signal_id": "../bad"},
        {"symbol": "XAUUSD|SELL"},
        {"expires_at": 1_800_000_000},
        {"expires_at": 1_800_000_600},  # Lifetime is capped at 5 minutes
    ],
)
def test_invalid_signal_is_rejected_before_write(tmp_path, overrides):
    with pytest.raises(SignalValidationError):
        publish_signal(make_signal(**overrides), tmp_path, now=1_800_000_010)
    assert not (tmp_path / "TradingBot" / "signal.csv").exists()


def test_sell_signal_requires_target_below_entry_below_stop():
    valid = make_signal(
        side="SELL",
        entry_price=2350,
        stop_loss=2360,
        take_profit=2340,
    )
    assert validate_signal(valid, now=1_800_000_010).side == "SELL"


def test_expired_signal_is_rejected(tmp_path):
    with pytest.raises(SignalValidationError, match="expired"):
        publish_signal(make_signal(expires_at=1_800_000_009), tmp_path, now=1_800_000_010)


def test_common_files_root_requires_explicit_location_outside_windows(monkeypatch):
    monkeypatch.delenv("MT5_COMMON_FILES_DIR", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    with pytest.raises(RuntimeError, match="MT5_COMMON_FILES_DIR"):
        common_files_root()
