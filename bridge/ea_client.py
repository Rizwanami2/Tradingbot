"""File bridge client: Python -> EA_Bridge.mq5 (MT5 Common Files)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

def _default_bridge_dir() -> Path:
    if os.environ.get("EA_BRIDGE_DIR"):
        return Path(os.environ["EA_BRIDGE_DIR"]).resolve()
    try:
        import config
        cfg_dir = getattr(config, "EA_BRIDGE_DIR", "")
        if cfg_dir:
            return Path(cfg_dir).resolve()
    except Exception:
        pass
    if os.name == "nt":
        return Path(os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal\Common\Files")).resolve()
    if "APPDATA" in os.environ:
        return (Path(os.environ["APPDATA"]) / "MetaQuotes" / "Terminal" / "Common" / "Files").resolve()
    wine_common = Path.home() / ".wine" / "drive_c" / "users"
    if wine_common.exists():
        for u in wine_common.iterdir():
            cand = u / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files"
            if cand.exists():
                return cand.resolve()
    return (Path.home() / ".mt5_common_files").resolve()


# MT5 reads/writes here with FILE_COMMON
BRIDGE_DIR = _default_bridge_dir()
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
    CMD_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp = CMD_FILE.with_name(f"{CMD_FILE.stem}_{os.getpid()}_{int(time.time()*1000)}.tmp")
        tmp.write_text(json.dumps(cmd, separators=(",", ":")), encoding="utf-8")
        tmp.replace(CMD_FILE)
    except Exception:
        try:
            CMD_FILE.write_text(json.dumps(cmd, separators=(",", ":")), encoding="utf-8")
        except Exception as write_err:
            return {"ok": False, "error": f"EA write error: {write_err}"}

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


def status(magic: int = DEFAULT_MAGIC, timeout: Optional[float] = 1.0) -> Dict[str, Any]:
    return send_command("STATUS", magic=magic, timeout=timeout)


def get_rates(symbol: str, count: int = 100, magic: int = DEFAULT_MAGIC, timeout: Optional[float] = 3.0) -> Dict[str, Any]:
    return send_command("RATES", symbol=symbol, volume=float(count), magic=magic, timeout=timeout)


def get_live_tick(symbol: str, magic: int = DEFAULT_MAGIC, timeout: Optional[float] = 2.0) -> Dict[str, Any]:
    # 1. Fast path: check ea_ticks.txt written periodically by EA
    try:
        ticks_file = BRIDGE_DIR / "ea_ticks.txt"
        if ticks_file.exists():
            raw = ticks_file.read_text(encoding="utf-8").strip()
            if raw:
                data = json.loads(raw)
                sym_key = symbol.upper()
                if sym_key in data:
                    res = data[sym_key]
                    res["ok"] = True
                    return res
                for k, v in data.items():
                    if sym_key in k or k in sym_key:
                        v["ok"] = True
                        return v
    except Exception:
        pass
    # 2. Command fallback
    return send_command("TICK", symbol=symbol, magic=magic, timeout=timeout)


if __name__ == "__main__":
    print("Bridge dir:", BRIDGE_DIR)
    print(status())
