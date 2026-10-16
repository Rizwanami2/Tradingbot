from pathlib import Path

import signal_pipeline

from mt5_hybrid import run_hybrid_analysis


def test_hybrid_export_is_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("MT5_SIGNAL_EXPORT_ENABLED", raising=False)
    monkeypatch.setattr(
        signal_pipeline,
        "run_signal",
        lambda *args, **kwargs: {"symbol": "XAUUSD", "signal": None},
    )

    result = run_hybrid_analysis("XAUUSD", common_files_dir=str(tmp_path), now=1_800_000_010)

    assert result["mt5_signal_export"]["published"] is False
    assert result["mt5_signal_export"]["reason"] == "export disabled"
    assert not (tmp_path / "TradingBot" / "signal.csv").exists()


def test_hybrid_export_publishes_approved_signal_only_to_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        signal_pipeline,
        "run_signal",
        lambda *args, **kwargs: {
            "symbol": "XAUUSD",
            "signal": {
                "direction": "LONG",
                "entry": 2350,
                "stop": 2340,
                "tp1": 2370,
                "tp2": 2380,
                "lots": 0.01,
            },
        },
    )

    result = run_hybrid_analysis(
        "XAUUSD",
        publish_enabled=True,
        common_files_dir=str(tmp_path),
        now=1_800_000_010,
    )

    export = result["mt5_signal_export"]
    assert export["published"] is True
    assert export["expires_at"] == 1_800_000_100
    assert "no order was placed" in export["note"]
    assert (tmp_path / "TradingBot" / "signal.csv").is_file()


def test_hybrid_export_refuses_vetoed_signal(monkeypatch, tmp_path):
    monkeypatch.setattr(
        signal_pipeline,
        "run_signal",
        lambda *args, **kwargs: {
            "symbol": "XAUUSD",
            "signal": {
                "direction": "LONG",
                "entry": 2350,
                "stop": 2340,
                "tp1": 2370,
                "tp2": 2380,
                "lots": 0.01,
                "vetoed": True,
            },
        },
    )

    result = run_hybrid_analysis(
        "XAUUSD",
        publish_enabled=True,
        common_files_dir=str(tmp_path),
        now=1_800_000_010,
    )

    assert result["mt5_signal_export"]["published"] is False
    assert result["mt5_signal_export"]["reason"] == "no approved strategy signal"
    assert not (tmp_path / "TradingBot" / "signal.csv").exists()
