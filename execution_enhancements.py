"""
Execution enhancements: structure-based trailing + instrument leverage clamp on register.

Usage inside ExecutionEngine.update_market_candle after BE logic:

    from execution_enhancements import apply_structure_trail, clamp_setup_leverage
    apply_structure_trail(pos, df_window, atr_value)
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from instrument_limits import clamp_leverage
from structure_trailing import structure_trail_stop


def clamp_setup_leverage(symbol: str, requested: float) -> Tuple[float, str]:
    return clamp_leverage(symbol, requested)


def apply_structure_trail(
    position: Any,
    df: Optional[pd.DataFrame],
    atr_value: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """
    Tighten stop using swing structure after TP1 (partials_done >= 1).
    Returns event dict if stop moved, else None.
    """
    if df is None or len(df) < 20:
        return None
    if getattr(position, "partials_done", 0) < 1:
        return None  # only trail after first partial

    direction = getattr(position, "direction", "LONG")
    entry = getattr(position, "executed_price", None) or getattr(position, "entry_level", 0.0)
    current_stop = getattr(position, "stop_loss", 0.0)

    new_stop, reason = structure_trail_stop(
        direction=direction,
        entry=float(entry),
        current_stop=float(current_stop),
        df=df,
        atr_value=float(atr_value or 0.0),
    )
    if abs(new_stop - current_stop) < 1e-9:
        return None

    old = current_stop
    position.stop_loss = new_stop
    return {
        "event": "STRUCTURE_TRAIL",
        "order": position,
        "new_stop": new_stop,
        "message": (
            f"📐 **STRUCTURE TRAIL** ({position.symbol})\n"
            f"Stop `${old:,.2f}` → `${new_stop:,.2f}`\n"
            f"{reason}"
        ),
    }
