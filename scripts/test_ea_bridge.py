#!/usr/bin/env python3
"""
EA Bridge Self-Diagnostic & Latency Verification Tool.
=====================================================
Validates connectivity, file permissions, roundtrip latency, and EA health
between the Python Tradingbot and MT5 EA_Bridge.mq5.

Usage:
  python scripts/test_ea_bridge.py
  python scripts/test_ea_bridge.py --timeout 3.0 --magic 112233
  python scripts/test_ea_bridge.py --dir "C:\\path\\to\\Common\\Files"
  python scripts/test_ea_bridge.py --sim       # Test bridge protocol using simulated EA
  python scripts/test_ea_bridge.py --json      # Output machine-readable JSON
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bridge import ea_client


@dataclass
class DiagnosticResult:
    timestamp: float = field(default_factory=time.time)
    bridge_dir: str = ""
    dir_exists: bool = False
    dir_writable: bool = False
    write_latency_ms: float = 0.0
    cmd_file: str = ""
    rsp_file: str = ""
    ea_connected: bool = False
    ea_roundtrip_ms: float = 0.0
    magic: int = 112233
    account_info: Dict[str, Any] = field(default_factory=dict)
    open_positions: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    hints: List[str] = field(default_factory=list)
    status: str = "UNKNOWN"  # PASS, WARN, FAIL


def _test_directory_permissions(bridge_dir: Path) -> tuple[bool, bool, float, Optional[str]]:
    """Tests if directory can be created/read/written, and measures write latency."""
    try:
        bridge_dir.mkdir(parents=True, exist_ok=True)
        exists = bridge_dir.is_dir()
    except Exception as e:
        return False, False, 0.0, f"Cannot create bridge directory: {e}"

    probe_file = bridge_dir / f".probe_{os.getpid()}_{int(time.time()*1000)}.tmp"
    try:
        t0 = time.perf_counter()
        probe_file.write_text("EA_BRIDGE_DIAGNOSTIC_PROBE", encoding="utf-8")
        read_back = probe_file.read_text(encoding="utf-8")
        t1 = time.perf_counter()
        probe_file.unlink(missing_ok=True)
        if read_back != "EA_BRIDGE_DIAGNOSTIC_PROBE":
            return exists, False, 0.0, "Probe file read mismatch"
        write_lat_ms = (t1 - t0) * 1000.0
        return True, True, round(write_lat_ms, 2), None
    except Exception as e:
        probe_file.unlink(missing_ok=True)
        return exists, False, 0.0, f"Directory write error: {e}"


def _run_simulated_ea(bridge_dir: Path, stop_evt: threading.Event, magic: int):
    """Background simulator thread answering STATUS commands for testing."""
    cmd_file = bridge_dir / "ea_commands.txt"
    rsp_file = bridge_dir / "ea_responses.txt"
    while not stop_evt.is_set():
        if cmd_file.exists():
            try:
                raw = cmd_file.read_text(encoding="utf-8").strip()
                if raw:
                    data = json.loads(raw)
                    action = data.get("action", "")
                    cmd_file.unlink(missing_ok=True)
                    if action == "STATUS":
                        response = {
                            "ok": True,
                            "ea": "EA_Bridge_v1.20_SIMULATED",
                            "server_time": time.strftime("%Y.%m.%d %H:%M:%S"),
                            "account": 99887766,
                            "balance": 10540.25,
                            "equity": 10595.80,
                            "free_margin": 9950.10,
                            "leverage": 100,
                            "currency": "USD",
                            "open_positions": [
                                {"ticket": 123456, "symbol": "XAUUSD", "type": "BUY", "volume": 0.05, "pnl": 55.55}
                            ],
                            "magic": magic,
                        }
                        rsp_file.write_text(json.dumps(response), encoding="utf-8")
            except Exception:
                pass
        time.sleep(0.02)


def resolve_bridge_dir(custom_dir: Optional[str] = None, simulate: bool = False) -> Path:
    if custom_dir:
        return Path(custom_dir).resolve()
    if os.environ.get("EA_BRIDGE_DIR"):
        return Path(os.environ["EA_BRIDGE_DIR"]).resolve()
    if sys.platform == "win32":
        return Path(os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal\Common\Files")).resolve()
    if "APPDATA" in os.environ:
        return (Path(os.environ["APPDATA"]) / "MetaQuotes" / "Terminal" / "Common" / "Files").resolve()
    # Check Wine
    wine_common = Path.home() / ".wine" / "drive_c" / "users"
    if wine_common.exists():
        for u in wine_common.iterdir():
            cand = u / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files"
            if cand.exists():
                return cand.resolve()
    if simulate:
        import tempfile
        return Path(tempfile.gettempdir()) / f"mt5_sim_common_files_{os.getpid()}"
    return Path.home() / ".mt5_common_files"


def run_diagnostics(
    custom_dir: Optional[str] = None,
    magic: int = ea_client.DEFAULT_MAGIC,
    timeout: float = 5.0,
    simulate: bool = False,
) -> DiagnosticResult:
    result = DiagnosticResult(magic=magic)
    target_dir = resolve_bridge_dir(custom_dir, simulate=simulate)
    ea_client.set_bridge_dir(target_dir)

    result.bridge_dir = str(target_dir)
    result.cmd_file = str(target_dir / "ea_commands.txt")
    result.rsp_file = str(target_dir / "ea_responses.txt")

    # 1. Directory permissions & I/O latency
    exists, writable, write_lat, err = _test_directory_permissions(target_dir)
    result.dir_exists = exists
    result.dir_writable = writable
    result.write_latency_ms = write_lat
    if err:
        result.errors.append(err)
        result.status = "FAIL"
        return result

    # Optional simulated responder
    sim_thread = None
    stop_evt = threading.Event()
    if simulate:
        sim_thread = threading.Thread(target=_run_simulated_ea, args=(target_dir, stop_evt, magic), daemon=True)
        sim_thread.start()

    try:
        # 2. Ping EA status
        t0 = time.perf_counter()
        rsp = ea_client.status(magic=magic, timeout=timeout)
        t1 = time.perf_counter()
        roundtrip_ms = round((t1 - t0) * 1000.0, 2)
        result.ea_roundtrip_ms = roundtrip_ms

        if rsp.get("ok"):
            result.ea_connected = True
            result.status = "PASS"
            result.account_info = {
                "ea": rsp.get("ea", "EA_Bridge"),
                "account": rsp.get("account", 0),
                "balance": rsp.get("balance", 0.0),
                "equity": rsp.get("equity", 0.0),
                "free_margin": rsp.get("free_margin", 0.0),
                "leverage": rsp.get("leverage", 0),
                "currency": rsp.get("currency", "USD"),
                "server_time": rsp.get("server_time", ""),
            }
            result.open_positions = rsp.get("open_positions", [])
        else:
            result.ea_connected = False
            result.status = "WARN"
            err_msg = rsp.get("error", "EA timeout / no response")
            result.warnings.append(f"{err_msg} (after {roundtrip_ms:.1f}ms)")
            result.hints.extend([
                "Confirm MetaTrader 5 is running.",
                "Ensure 'EA_Bridge.mq5' is compiled and attached to an active chart.",
                "Verify 'Algo Trading' button in MT5 toolbar is toggled GREEN.",
                "In EA Properties -> Common tab, ensure 'Allow Algo Trading' is checked.",
                f"Verify MT5 Common\\Files points to: {target_dir}",
            ])
    finally:
        if simulate and stop_evt:
            stop_evt.set()
            if sim_thread:
                sim_thread.join(timeout=1.0)

    return result


def print_dashboard(res: DiagnosticResult):
    status_badge = {
        "PASS": "\033[92m[ PASS: BRIDGE OPERATIONAL ]\033[0m",
        "WARN": "\033[93m[ WARN: EA NOT RESPONDING ]\033[0m",
        "FAIL": "\033[91m[ FAIL: I/O OR DIRECTORY ERROR ]\033[0m",
    }.get(res.status, f"[{res.status}]")

    print("\n" + "=" * 68)
    print(f"       EA BRIDGE SELF-DIAGNOSTIC & HEALTH DASHBOARD")
    print("=" * 68)
    print(f" Status               : {status_badge}")
    print(f" Bridge Directory     : {res.bridge_dir}")
    print(f" Directory Exists     : {'✅ Yes' if res.dir_exists else '❌ No'}")
    print(f" Directory Writable   : {'✅ Yes' if res.dir_writable else '❌ No'}")
    print(f" Disk I/O Latency     : {res.write_latency_ms:.2f} ms")
    print(f" Magic Number Target  : {res.magic}")
    print(f" EA Roundtrip Ping    : {res.ea_roundtrip_ms:.2f} ms")
    print(f" EA Responded         : {'✅ Online' if res.ea_connected else '❌ No Response'}")

    if res.account_info:
        print("\n" + "-" * 68)
        print(" MT5 ACCOUNT TELEMETRY")
        print("-" * 68)
        acc = res.account_info
        print(f" EA Version / Server  : {acc.get('ea')} | {acc.get('server_time')}")
        print(f" Account Number       : {acc.get('account')}")
        print(f" Balance / Equity     : ${acc.get('balance', 0):,.2f} / ${acc.get('equity', 0):,.2f} {acc.get('currency', 'USD')}")
        print(f" Free Margin          : ${acc.get('free_margin', 0):,.2f} (1:{acc.get('leverage')})")
        print(f" Open Positions Count : {len(res.open_positions)}")
        for pos in res.open_positions[:5]:
            print(f"   • Ticket #{pos.get('ticket')}: {pos.get('type')} {pos.get('volume')} {pos.get('symbol')} | PnL: ${pos.get('pnl', 0):+.2f}")

    if res.warnings:
        print("\n" + "-" * 68)
        print(" ⚠️  WARNINGS")
        print("-" * 68)
        for w in res.warnings:
            print(f" • {w}")

    if res.errors:
        print("\n" + "-" * 68)
        print(" ❌ ERRORS")
        print("-" * 68)
        for e in res.errors:
            print(f" • {e}")

    if res.hints:
        print("\n" + "-" * 68)
        print(" 💡 TROUBLESHOOTING CHECKLIST")
        print("-" * 68)
        for h in res.hints:
            print(f" [ ] {h}")

    print("=" * 68 + "\n")


def main():
    parser = argparse.ArgumentParser(description="MT5 EA Bridge Self-Diagnostic Tool")
    parser.add_argument("--dir", default=None, help="Custom MT5 Common\\Files directory path")
    parser.add_argument("--magic", type=int, default=ea_client.DEFAULT_MAGIC, help="Magic number to test")
    parser.add_argument("--timeout", type=float, default=5.0, help="Response timeout in seconds")
    parser.add_argument("--sim", "--simulate", dest="sim", action="store_true", help="Run with simulated EA in background")
    parser.add_argument("--json", action="store_true", help="Print result as JSON")
    args = parser.parse_args()

    res = run_diagnostics(
        custom_dir=args.dir,
        magic=args.magic,
        timeout=args.timeout,
        simulate=args.sim,
    )

    if args.json:
        print(json.dumps(asdict(res), indent=2))
    else:
        print_dashboard(res)

    sys.exit(0 if res.status == "PASS" else (2 if res.status == "WARN" else 1))


if __name__ == "__main__":
    main()
