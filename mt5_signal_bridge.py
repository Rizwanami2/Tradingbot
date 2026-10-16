"""Fail-closed signal-file bridge between the Python strategy and MT5 EA.

This module does not connect to a broker and never places orders. It publishes one
validated, short-lived signal to the MetaTrader Common\Files directory. The EA
must separately be enabled and attached to an MT5 demo account to consume it.
"""
from __future__ import annotations

import csv
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SIGNAL_RELATIVE_PATH = Path("TradingBot") / "signal.csv"
SCHEMA_VERSION = "1"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


class SignalValidationError(ValueError):
    """Raised when a signal is invalid or unsafe to publish."""


class PendingSignalError(FileExistsError):
    """Raised rather than replacing a signal that the EA has not consumed."""


@dataclass(frozen=True)
class TradeSignal:
    signal_id: str
    symbol: str
    side: str
    volume: float
    entry_price: float
    stop_loss: float
    take_profit: float
    created_at: int
    expires_at: int


def _finite_positive(value: float, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SignalValidationError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise SignalValidationError(f"{field} must be finite and greater than zero")
    return parsed


def validate_signal(signal: TradeSignal, *, now: Optional[int] = None) -> TradeSignal:
    """Validate signal fields before any filesystem write."""
    if not isinstance(signal, TradeSignal):
        raise SignalValidationError("signal must be a TradeSignal")
    if not _ID_RE.fullmatch(str(signal.signal_id or "")):
        raise SignalValidationError("signal_id must contain only letters, digits, '_' or '-'")
    if not _SYMBOL_RE.fullmatch(str(signal.symbol or "")):
        raise SignalValidationError("symbol contains unsupported characters")
    side = str(signal.side or "").upper()
    if side not in {"BUY", "SELL"}:
        raise SignalValidationError("side must be BUY or SELL")

    volume = _finite_positive(signal.volume, "volume")
    entry = _finite_positive(signal.entry_price, "entry_price")
    stop = _finite_positive(signal.stop_loss, "stop_loss")
    target = _finite_positive(signal.take_profit, "take_profit")
    if side == "BUY" and not stop < entry < target:
        raise SignalValidationError("BUY signal requires stop_loss < entry_price < take_profit")
    if side == "SELL" and not target < entry < stop:
        raise SignalValidationError("SELL signal requires take_profit < entry_price < stop_loss")

    try:
        created = int(signal.created_at)
        expires = int(signal.expires_at)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SignalValidationError("created_at and expires_at must be integer Unix timestamps") from exc
    clock = int(time.time()) if now is None else int(now)
    if created <= 0 or expires <= created:
        raise SignalValidationError("timestamps must be positive and expires_at must follow created_at")
    if created > clock + 30:
        raise SignalValidationError("created_at is too far in the future")
    if expires <= clock:
        raise SignalValidationError("signal has expired")
    if expires - created > 300:
        raise SignalValidationError("signal lifetime must not exceed 300 seconds")

    return TradeSignal(
        signal_id=str(signal.signal_id),
        symbol=str(signal.symbol).upper(),
        side=side,
        volume=volume,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        created_at=created,
        expires_at=expires,
    )


def common_files_root(root: Optional[os.PathLike[str] | str] = None) -> Path:
    """Resolve MT5 Common\\Files root; tests should pass a temporary directory."""
    if root is not None:
        return Path(root).expanduser().resolve()
    override = os.getenv("MT5_COMMON_FILES_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    appdata = os.getenv("APPDATA", "").strip()
    if appdata:
        return (Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files").resolve()
    raise RuntimeError(
        "Set MT5_COMMON_FILES_DIR to the MT5 Common\\Files directory or pass common_files_dir explicitly"
    )


def publish_signal(
    signal: TradeSignal,
    common_files_dir: Optional[os.PathLike[str] | str] = None,
    *,
    now: Optional[int] = None,
) -> Path:
    """Atomically publish one signal; refuse to overwrite an unconsumed signal.

    The EA reads a pipe-delimited row with schema, id, symbol, side, volume,
    entry, stop, target, creation timestamp, and expiry timestamp.
    """
    safe = validate_signal(signal, now=now)
    root = common_files_root(common_files_dir)
    folder = root / SIGNAL_RELATIVE_PATH.parent
    folder.mkdir(parents=True, exist_ok=True)
    target = root / SIGNAL_RELATIVE_PATH

    row = [
        SCHEMA_VERSION,
        safe.signal_id,
        safe.symbol,
        safe.side,
        format(safe.volume, ".10g"),
        format(safe.entry_price, ".10g"),
        format(safe.stop_loss, ".10g"),
        format(safe.take_profit, ".10g"),
        str(safe.created_at),
        str(safe.expires_at),
    ]
    # Write to a complete temporary file, then hard-link atomically so an
    # existing pending signal is never replaced. The temp and target share a dir.
    fd, temp_name = tempfile.mkstemp(prefix=".signal-", suffix=".tmp", dir=str(folder))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="") as stream:
            writer = csv.writer(stream, delimiter="|", lineterminator="\n")
            writer.writerow(row)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp_path, target)
        except FileExistsError as exc:
            raise PendingSignalError(
                f"Pending signal already exists at {target}; wait for the MT5 EA to consume it"
            ) from exc
        return target
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
