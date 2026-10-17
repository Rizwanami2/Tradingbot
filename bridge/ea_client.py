"""File bridge client: Python -> EA_Bridge.mq5 (MT5 Common Files)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

# MT5 reads/writes here with FILE_COMMON
BRIDGE_DIR = Path(
    os.environ.get(
        "EA_BRIDGE_DIR",
        os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal\Common\Files"),
    )
)
CMD_FILE = BRIDGE_DIR / "ea_commands.txt"
RSP_FILE = BRIDGE_DIR / "ea_responses.txt"
TIMEOUT = 20.0
DEFAULT_MAGIC = 112233


def set_bridge_dir(new_dir: Path | str) -> None:
    """Dynamically override the EA Bridge directory (useful for tests or custom MT5 setups)."""
    global BRIDGE_DIR, CMD_FILE, RSP_FILE
    BRIDGE_DIR = Path(new_dir)
    CMD_FILE = BRIDGE_DIR / "ea_commands.txt"
    RSP_FILE = BRIDGE_DIR / "ea_responses.txt"


def send_command(
    action: str,
    symbol: Optional[str] = None,
    volume: Optional[float] = None,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    magic: int = DEFAULT_MAGIC,
    ticket: Optional[int] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    if RSP_FILE.exists():
        try:
            RSP_FILE.unlink()
        except OSError:
            pass

    cmd = {
        "action": action.upper(),
        "symbol": symbol or "",
        "volume": float(volume or 0),
        "sl": float(sl or 0),
        "tp": float(tp or 0),
        "magic": int(magic),
        "ticket": int(ticket or 0),
        "ts": time.time(),
    }
    # No spaces after ':' so older EA parsers also work
    tmp = CMD_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cmd, separators=(",", ":")), encoding="utf-8")
    tmp.replace(CMD_FILE)

    effective_timeout = float(timeout) if timeout is not None else TIMEOUT
    start = time.time()
    while time.time() - start < effective_timeout:
        if RSP_FILE.exists():
            try:
                raw = RSP_FILE.read_text(encoding="utf-8").strip()
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        time.sleep(0.05 if effective_timeout < 1.0 else 0.15)

    return {
        "ok": False,
        "error": "EA timeout",
        "cmd_file": str(CMD_FILE),
        "hint": "Attach EA_Bridge on a chart, AutoTrading GREEN, FILE_COMMON paths",
    }


def buy(symbol: str, volume: float, sl: float = 0.0, tp: float = 0.0, magic: int = DEFAULT_MAGIC, timeout: Optional[float] = None) -> Dict[str, Any]:
    return send_command("BUY", symbol=symbol, volume=volume, sl=sl, tp=tp, magic=magic, timeout=timeout)


def sell(symbol: str, volume: float, sl: float = 0.0, tp: float = 0.0, magic: int = DEFAULT_MAGIC, timeout: Optional[float] = None) -> Dict[str, Any]:
    return send_command("SELL", symbol=symbol, volume=volume, sl=sl, tp=tp, magic=magic, timeout=timeout)


def close(symbol: str = "", ticket: int = 0, magic: int = DEFAULT_MAGIC, timeout: Optional[float] = None) -> Dict[str, Any]:
    return send_command("CLOSE", symbol=symbol, ticket=ticket, magic=magic, timeout=timeout)


def close_all(magic: int = DEFAULT_MAGIC, timeout: Optional[float] = None) -> Dict[str, Any]:
    return send_command("CLOSE_ALL", magic=magic, timeout=timeout)


def modify(symbol: str = "", ticket: int = 0, sl: float = 0.0, tp: float = 0.0, magic: int = DEFAULT_MAGIC, timeout: Optional[float] = None) -> Dict[str, Any]:
    return send_command("MODIFY", symbol=symbol, ticket=ticket, sl=sl, tp=tp, magic=magic, timeout=timeout)


def status(magic: int = DEFAULT_MAGIC, timeout: Optional[float] = None) -> Dict[str, Any]:
    return send_command("STATUS", magic=magic, timeout=timeout)


if __name__ == "__main__":
    print("Bridge dir:", BRIDGE_DIR)
    print(status())
