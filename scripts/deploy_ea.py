#!/usr/bin/env python3
"""
MT5 EA Auto-Deploy & Compile Script.
===================================
Automatically discovers MetaTrader 5 terminal instances and deploys
`mql5/EA_Bridge.mq5` directly to `MQL5\\Experts\\` across all found terminals.
Optionally compiles the EA to `.ex5` if `metaeditor64.exe` is available.

Usage:
  python scripts/deploy_ea.py
  python scripts/deploy_ea.py --dry-run
  python scripts/deploy_ea.py --target "C:\\path\\to\\MT5\\MQL5\\Experts"
  python scripts/deploy_ea.py --compile
  python scripts/deploy_ea.py --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_EA = REPO_ROOT / "mql5" / "EA_Bridge.mq5"


@dataclass
class TerminalInstance:
    instance_id: str
    data_dir: Path
    experts_dir: Path
    origin_exe_dir: Optional[Path] = None
    common_files_dir: Optional[Path] = None
    metaeditor_exe: Optional[Path] = None


@dataclass
class DeployResult:
    terminal_id: str
    target_file: str
    copied: bool = False
    file_size: int = 0
    sha256: str = ""
    compile_attempted: bool = False
    compile_success: bool = False
    compile_log: str = ""
    error: Optional[str] = None


def _calc_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()[:16]


def discover_terminals(custom_target: Optional[str] = None) -> List[TerminalInstance]:
    """Scans for MT5 terminal data instances across Windows, Wine, and custom paths."""
    instances: List[TerminalInstance] = []

    if custom_target:
        p = Path(custom_target).resolve()
        # User may have provided MQL5/Experts or the terminal root
        if p.name.lower() == "experts":
            experts_dir = p
            data_dir = p.parent.parent
        elif (p / "MQL5" / "Experts").exists() or p.name.lower() == "mql5":
            experts_dir = p / "Experts" if p.name.lower() == "mql5" else p / "MQL5" / "Experts"
            data_dir = p.parent if p.name.lower() == "mql5" else p
        else:
            experts_dir = p / "MQL5" / "Experts"
            data_dir = p

        instances.append(TerminalInstance(
            instance_id=data_dir.name,
            data_dir=data_dir,
            experts_dir=experts_dir,
            common_files_dir=data_dir.parent / "Common" / "Files" if (data_dir.parent / "Common").exists() else None,
        ))
        return instances

    terminal_roots: List[Path] = []

    # 1. Standard Windows %APPDATA%\MetaQuotes\Terminal
    if sys.platform == "win32":
        appdata_mq = Path(os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal"))
        if appdata_mq.is_dir():
            terminal_roots.append(appdata_mq)
    elif "APPDATA" in os.environ:
        appdata_mq = Path(os.environ["APPDATA"]) / "MetaQuotes" / "Terminal"
        if appdata_mq.is_dir():
            terminal_roots.append(appdata_mq)

    # 2. Wine on Linux / macOS
    wine_users = Path.home() / ".wine" / "drive_c" / "users"
    if wine_users.is_dir():
        for user_dir in wine_users.iterdir():
            mq_dir = user_dir / "AppData" / "Roaming" / "MetaQuotes" / "Terminal"
            if mq_dir.is_dir():
                terminal_roots.append(mq_dir)

    common_files_cand = None
    for root in terminal_roots:
        cf = root / "Common" / "Files"
        if cf.is_dir() or cf.parent.is_dir():
            common_files_cand = cf

        for item in root.iterdir():
            # MT5 data folders are 32-character hexadecimal hashes
            if item.is_dir() and len(item.name) == 32:
                origin_dir = None
                origin_file = item / "origin.txt"
                if origin_file.is_file():
                    try:
                        origin_txt = origin_file.read_text(encoding="utf-16le", errors="ignore").strip()
                        if not origin_txt:
                            origin_txt = origin_file.read_text(encoding="utf-8", errors="ignore").strip()
                        if origin_txt:
                            origin_path = Path(origin_txt)
                            if origin_path.is_dir():
                                origin_dir = origin_path
                    except Exception:
                        pass

                # Check for metaeditor64.exe / metaeditor.exe
                metaeditor = None
                if origin_dir:
                    for me_name in ("metaeditor64.exe", "metaeditor.exe"):
                        cand = origin_dir / me_name
                        if cand.is_file():
                            metaeditor = cand
                            break

                instances.append(TerminalInstance(
                    instance_id=item.name,
                    data_dir=item,
                    experts_dir=item / "MQL5" / "Experts",
                    origin_exe_dir=origin_dir,
                    common_files_dir=common_files_cand,
                    metaeditor_exe=metaeditor,
                ))

    return instances


def deploy_ea(
    source_ea: Path = SOURCE_EA,
    terminals: Optional[List[TerminalInstance]] = None,
    dry_run: bool = False,
    auto_compile: bool = True,
) -> List[DeployResult]:
    if not source_ea.is_file():
        raise FileNotFoundError(f"Source EA not found at: {source_ea}")

    src_sha = _calc_sha256(source_ea)
    src_size = source_ea.stat().st_size
    results: List[DeployResult] = []

    if not terminals:
        return results

    for term in terminals:
        dest_file = term.experts_dir / source_ea.name
        res = DeployResult(
            terminal_id=term.instance_id,
            target_file=str(dest_file),
            file_size=src_size,
            sha256=src_sha,
        )

        if dry_run:
            res.copied = True
            results.append(res)
            continue

        try:
            term.experts_dir.mkdir(parents=True, exist_ok=True)
            if term.common_files_dir:
                term.common_files_dir.mkdir(parents=True, exist_ok=True)

            shutil.copy2(source_ea, dest_file)
            res.copied = True

            # Attempt compilation if metaeditor is available and auto_compile is True
            if auto_compile and term.metaeditor_exe and term.metaeditor_exe.is_file():
                res.compile_attempted = True
                log_file = term.experts_dir / "ea_compile.log"
                cmd = [
                    str(term.metaeditor_exe),
                    f"/compile:{dest_file}",
                    f"/log:{log_file}",
                ]
                try:
                    proc = subprocess.run(cmd, capture_output=True, timeout=30, text=True)
                    log_text = ""
                    if log_file.exists():
                        log_text = log_file.read_text(encoding="utf-16le", errors="ignore").strip()
                        if not log_text:
                            log_text = log_file.read_text(encoding="utf-8", errors="ignore").strip()

                    res.compile_log = log_text or proc.stdout
                    ex5_file = dest_file.with_suffix(".ex5")
                    res.compile_success = ex5_file.exists() and proc.returncode == 0
                except Exception as compile_err:
                    res.compile_log = f"Compilation exception: {compile_err}"
                    res.compile_success = False

        except Exception as e:
            res.error = str(e)
            res.copied = False

        results.append(res)

    return results


def print_summary(source_ea: Path, results: List[DeployResult], dry_run: bool = False):
    print("\n" + "=" * 72)
    print("           MT5 EXPERT ADVISOR (EA_BRIDGE) DEPLOYMENT REPORT")
    print("=" * 72)
    print(f" Source File    : {source_ea}")
    print(f" File Size      : {source_ea.stat().st_size:,} bytes")
    print(f" SHA-256 (hash) : {_calc_sha256(source_ea)}")
    print(f" Dry Run        : {'⚠️ YES (No changes written)' if dry_run else 'NO (Live deployment)'}")
    print(f" Terminals Found: {len(results)}")
    print("-" * 72)

    if not results:
        print(" ⚠️  No MetaTrader 5 terminal instances detected.")
        print(" • If MT5 is installed in a non-standard path, deploy directly using:")
        print("   python scripts/deploy_ea.py --target \"<Your_MT5_Data_Path>\"")
        print(" • Or manually copy 'mql5/EA_Bridge.mq5' into your MT5 'MQL5\\Experts\\' folder.")
        print("=" * 72 + "\n")
        return

    for idx, r in enumerate(results, start=1):
        status_icon = "✅ DEPLOYED" if r.copied else "❌ FAILED"
        if dry_run:
            status_icon = "🔎 READY"
        print(f"\n [{idx}] Terminal Instance: {r.terminal_id}")
        print(f"     Status      : {status_icon}")
        print(f"     Target Path : {r.target_file}")
        if r.compile_attempted:
            compile_icon = "✅ Compiled (.ex5 generated)" if r.compile_success else "⚠️ Compilation Warning"
            print(f"     Compiler    : {compile_icon}")
            if r.compile_log:
                print(f"     Log         : {r.compile_log.splitlines()[-1] if r.compile_log.splitlines() else ''}")
        elif not dry_run:
            print("     Compiler    : Open MetaEditor (F4) in MT5 and press F7 to compile to .ex5")
        if r.error:
            print(f"     Error       : {r.error}")

    print("\n" + "=" * 72)
    print(" NEXT STEPS:")
    print(" 1. In MetaTrader 5, open the Navigator panel (Ctrl+N).")
    print(" 2. Right-click 'Expert Advisors' and click 'Refresh'.")
    print(" 3. Drag 'EA_Bridge' onto any chart (e.g. XAUUSD or BTCUSD).")
    print(" 4. Ensure 'Algo Trading' (AutoTrading) is toggled GREEN in the MT5 top toolbar.")
    print(" 5. Run 'python scripts/test_ea_bridge.py' to verify bridge communication.")
    print("=" * 72 + "\n")


def main():
    parser = argparse.ArgumentParser(description="MT5 EA Auto-Deploy Tool")
    parser.add_argument("--source", default=str(SOURCE_EA), help="Path to source EA_Bridge.mq5")
    parser.add_argument("--target", default=None, help="Custom MT5 terminal data or MQL5\\Experts directory")
    parser.add_argument("--dry-run", action="store_true", help="Preview discovery and targets without copying")
    parser.add_argument("--no-compile", action="store_true", help="Skip compilation even if metaeditor is found")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    args = parser.parse_args()

    src = Path(args.source).resolve()
    if not src.is_file():
        print(f"Error: Source EA file not found: {src}", file=sys.stderr)
        sys.exit(1)

    terminals = discover_terminals(custom_target=args.target)
    results = deploy_ea(
        source_ea=src,
        terminals=terminals,
        dry_run=args.dry_run,
        auto_compile=not args.no_compile,
    )

    if args.json:
        data = {
            "source_file": str(src),
            "sha256": _calc_sha256(src),
            "dry_run": args.dry_run,
            "terminals_found": len(terminals),
            "results": [asdict(r) for r in results],
        }
        print(json.dumps(data, indent=2))
    else:
        print_summary(src, results, dry_run=args.dry_run)

    sys.exit(0 if (results or args.dry_run) else 0)


if __name__ == "__main__":
    main()
