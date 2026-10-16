"""Opt-in orchestration for Python analysis -> MT5 demo signal file.

Nothing is published unless MT5_SIGNAL_EXPORT_ENABLED=true (or the caller
explicitly passes publish_enabled=True). This module never connects to a broker.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, Optional

from mt5_signal_bridge import TradeSignal, publish_signal


def _direction_name(value: Any) -> str:
    return str(getattr(value, "name", value) or "").upper()


def run_hybrid_analysis(
    symbol: str,
    *,
    equity: float = 1000.0,
    leverage: float = 1.0,
    source: Optional[str] = None,
    publish_enabled: Optional[bool] = None,
    common_files_dir: Optional[str] = None,
    now: Optional[int] = None,
    ttl_seconds: int = 90,
) -> Dict[str, Any]:
    """Run the existing signal pipeline and optionally export one valid signal.

    Signal export is opt-in and file-only. It never calls MT5 or sends an order.
    """
    from signal_pipeline import run_signal

    result = run_signal(symbol, equity=equity, leverage=leverage, source=source)
    enabled = (
        os.getenv("MT5_SIGNAL_EXPORT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        if publish_enabled is None
        else bool(publish_enabled)
    )
    if not enabled:
        result["mt5_signal_export"] = {"published": False, "reason": "export disabled"}
        return result

    signal = result.get("signal")
    if not isinstance(signal, dict) or signal.get("vetoed"):
        result["mt5_signal_export"] = {
            "published": False,
            "reason": "no approved strategy signal",
        }
        return result
    if ttl_seconds < 1 or ttl_seconds > 300:
        result["mt5_signal_export"] = {
            "published": False,
            "reason": "ttl_seconds must be between 1 and 300",
        }
        return result

    direction = _direction_name(signal.get("direction"))
    if "LONG" in direction or direction == "BUY":
        side = "BUY"
    elif "SHORT" in direction or direction == "SELL":
        side = "SELL"
    else:
        result["mt5_signal_export"] = {
            "published": False,
            "reason": f"unsupported signal direction: {direction or 'missing'}",
        }
        return result

    try:
        entry = float(signal["entry"])
        stop = float(signal["stop"])
        tp1 = float(signal.get("tp1") or 0)
        tp2 = float(signal.get("tp2") or 0)
        volume = float(signal.get("lots") or 0)
        target = tp2
        if side == "BUY" and not stop < entry < target:
            target = tp1
        elif side == "SELL" and not target < entry < stop:
            target = tp1
        created = int(time.time()) if now is None else int(now)
        safe_signal = TradeSignal(
            signal_id=uuid.uuid4().hex,
            symbol=str(result.get("symbol") or symbol).upper(),
            side=side,
            volume=volume,
            entry_price=entry,
            stop_loss=stop,
            take_profit=target,
            created_at=created,
            expires_at=created + ttl_seconds,
        )
        path = publish_signal(safe_signal, common_files_dir, now=created)
    except Exception as exc:
        result["mt5_signal_export"] = {
            "published": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }
        return result

    result["mt5_signal_export"] = {
        "published": True,
        "signal_id": safe_signal.signal_id,
        "path": str(path),
        "expires_at": safe_signal.expires_at,
        "note": "Signal file published only; no order was placed by Python.",
    }
    return result
