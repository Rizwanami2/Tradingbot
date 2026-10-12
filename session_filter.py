"""
Session & Killzone Filter
=========================
Restricts trading to high-probability windows (London / New York killzones)
and optionally blocks low-liquidity periods (Asian range, weekends).
All times are UTC.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Optional, Tuple


@dataclass
class SessionInfo:
    name: str
    is_killzone: bool
    is_tradeable: bool
    reason: str


LONDON_OPEN = (time(7, 0), time(10, 0))
NY_OPEN = (time(12, 30), time(15, 30))
LONDON_CLOSE = (time(15, 0), time(17, 0))
LONDON_SESSION = (time(7, 0), time(16, 0))
NY_SESSION = (time(12, 0), time(21, 0))
ASIAN_SESSION = (time(0, 0), time(7, 0))


FOREX_FIAT_SYMBOLS = ("XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF")
CRYPTO_MARKERS = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "DOT", "LINK", "USDT")


def is_crypto_symbol(symbol: Optional[str]) -> bool:
    if not symbol:
        return False
    sym = symbol.upper().replace("/", "").replace("-", "").replace("_", "")
    if sym in FOREX_FIAT_SYMBOLS:
        return False
    return any(m in sym for m in CRYPTO_MARKERS)


def _in_window(t: time, window: Tuple[time, time]) -> bool:
    start, end = window
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


def get_session_info(
    ts: Optional[datetime] = None,
    allow_asian: bool = False,
    symbol: Optional[str] = None,
) -> SessionInfo:
    if ts is None:
        ts = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    t = ts.time()
    weekday = ts.weekday()
    if weekday >= 5:
        if is_crypto_symbol(symbol):
            return SessionInfo("CRYPTO_24_7", False, True, "Crypto 24/7 weekend trading active")
        return SessionInfo("WEEKEND", False, False, "Weekend – market closed or low liquidity")
    if _in_window(t, LONDON_OPEN):
        return SessionInfo("LONDON_KZ", True, True, "London Open Killzone")
    if _in_window(t, NY_OPEN):
        return SessionInfo("NY_KZ", True, True, "New York Open Killzone")
    if _in_window(t, LONDON_CLOSE):
        return SessionInfo("LONDON_CLOSE", True, True, "London Close / Overlap")
    if _in_window(t, LONDON_SESSION):
        return SessionInfo("LONDON", False, True, "London session")
    if _in_window(t, NY_SESSION):
        return SessionInfo("NEW_YORK", False, True, "New York session")
    if _in_window(t, ASIAN_SESSION):
        if allow_asian or is_crypto_symbol(symbol):
            return SessionInfo("ASIAN", False, True, "Asian session (allowed)")
        return SessionInfo("ASIAN", False, False, "Asian range – low liquidity (blocked)")
    if is_crypto_symbol(symbol):
        return SessionInfo("CRYPTO_24_7", False, True, "Crypto 24/7 session active")
    return SessionInfo("OFF_HOURS", False, False, "Outside major sessions")


def is_tradeable_session(
    ts: Optional[datetime] = None,
    require_killzone: bool = False,
    allow_asian: bool = False,
    symbol: Optional[str] = None,
) -> Tuple[bool, str]:
    info = get_session_info(ts, allow_asian=allow_asian, symbol=symbol)
    if require_killzone and not info.is_killzone:
        return False, f"Killzone required – current: {info.name} ({info.reason})"
    if not info.is_tradeable:
        return False, info.reason
    return True, info.reason
