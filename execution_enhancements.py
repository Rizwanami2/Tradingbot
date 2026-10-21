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
from structure_trailing import structure_trail_stop, hybrid_trail_stop


def clamp_setup_leverage(symbol: str, requested: float) -> Tuple[float, str]:
    return clamp_leverage(symbol, requested)


def apply_structure_trail(
    position: Any,
    df: Optional[pd.DataFrame] = None,
    atr_value: float = 0.0,
    current_price: Optional[float] = None,
    allow_pre_be: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Tighten stop using swing structure / ATR trailing after TP1 (partials_done >= 1),
    or before TP1 if allow_pre_be is True and position is in favorable territory.
    Works with either dataframe history or internal rolling swing history.
    Returns event dict if stop moved in favorable direction, else None.
    """
    if getattr(position, "partials_done", 0) < 1 and not allow_pre_be:
        return None  # only trail after first partial unless allow_pre_be

    has_df = df is not None and len(df) >= 5
    recent_l = getattr(position, "recent_lows", None)
    recent_h = getattr(position, "recent_highs", None)
    has_rolling = (recent_l and len(recent_l) >= 3) or (recent_h and len(recent_h) >= 3)

    if not has_df and not has_rolling:
        return None

    direction = getattr(position, "direction", "LONG")
    entry = getattr(position, "executed_price", None) or getattr(position, "entry_level", 0.0)
    current_stop = getattr(position, "stop_loss", 0.0)
    highest_p = getattr(position, "highest_price", entry)
    lowest_p = getattr(position, "lowest_price", entry)
    curr_p = current_price or getattr(position, "current_price", None) or entry
    initial_risk = abs(entry - current_stop) or (entry * 0.003)

    new_stop, reason = hybrid_trail_stop(
        direction=direction,
        entry=float(entry),
        current_stop=float(current_stop),
        highest_price=float(highest_p),
        lowest_price=float(lowest_p),
        current_price=float(curr_p),
        df=df if has_df else None,
        atr_value=float(atr_value or 0.0),
        initial_risk=float(initial_risk),
        recent_lows=recent_l,
        recent_highs=recent_h,
    )

    is_long = str(direction).upper() in ("LONG", "BUY", "1")
    if is_long and new_stop <= current_stop + 1e-6:
        return None
    if not is_long and new_stop >= current_stop - 1e-6:
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
