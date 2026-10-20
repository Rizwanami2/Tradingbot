#!/usr/bin/env python3
"""Production entrypoint: restore modules, apply fixes, start Telegram bot."""
from __future__ import annotations

import os
import runpy
import sys
import time
import traceback
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_STRAT = str(ROOT / "strategies")
if _STRAT not in sys.path:
    sys.path.insert(0, _STRAT)

def _ensure_execution_engine() -> None:
    """Restore execution_engine.py if missing/empty without exiting the process."""
    target = ROOT / "execution_engine.py"
    restore = ROOT / "scripts" / "restore_execution_engine.py"
    try:
        ok = target.is_file() and target.stat().st_size > 1000
        if ok:
            text = target.read_text(encoding="utf-8", errors="replace")
            ok = "class ExecutionEngine" in text or "def register_setup" in text
        if ok:
            return
        if not restore.is_file():
            print("[run_bot] WARNING: execution_engine.py missing and no restore script found")
            return
        # Import module and call main() — do NOT use run_name=__main__ (that sys.exit kills the bot)
        import importlib.util
        spec = importlib.util.spec_from_file_location("_restore_execution_engine", restore)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        if hasattr(mod, "main"):
            mod.main()
        print("[run_bot] execution_engine restored; continuing startup...")
    except SystemExit:
        # restore scripts sometimes call sys.exit(0); ignore so bot keeps running
        print("[run_bot] execution_engine restored; continuing startup...")
    except Exception as _heal_err:
        print(f"[run_bot] execution_engine self-heal failed: {_heal_err}")

_ensure_execution_engine()

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*cryptography.*")


def _start_railway_health_server() -> None:
    """Start lightweight HTTP health server for Railway web deployment."""
    port_str = os.environ.get("PORT")
    if not port_str:
        return
    try:
        port = int(port_str)
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import threading

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok","app":"nimble-darwin-trading-bot"}\n')

            def log_message(self, format, *args):
                pass  # suppress access logs to keep log stream clean

        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        print(f"[run_bot] Railway HTTP health server listening on 0.0.0.0:{port}")
    except Exception as e:
        print(f"[run_bot] Warning: Could not start health server: {e}")


def _safe_wire(name: str) -> None:
    try:
        mod = __import__(name)
        if hasattr(mod, "main"):
            mod.main()
        elif hasattr(mod, "restore"):
            mod.restore()
    except Exception as e:
        print(f"[run_bot] {name}: {type(e).__name__}: {e}")


def _setup_windows_compatibility() -> None:
    """Ensure UTF-8 console output and event loop compatibility on Windows 11 / AWS."""
    if sys.platform == "win32":
        for stream_name in ("stdout", "stderr", "stdin"):
            s = getattr(sys, stream_name, None)
            if s and hasattr(s, "reconfigure"):
                try:
                    s.reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass
        try:
            import asyncio
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass


def main() -> None:
    _setup_windows_compatibility()
    sys.path.insert(0, str(ROOT))

    import logging
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        stream=sys.stdout,
        force=True,
    )
    for noisy in ("httpx", "httpcore", "telegram", "telegram.ext", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        from ai_historical_predictor import patch_into_indicators_module
        patch_into_indicators_module()
    except Exception as e:
        print("[run_bot] ai_historical:", e)

    main_py = ROOT / "main.py"
    if not main_py.exists():
        print("[run_bot] FATAL: main.py missing")
        sys.exit(1)

    _start_railway_health_server()

    max_restarts = 50
    restart_count = 0
    while restart_count < max_restarts:
        print(f"[run_bot] starting main.py (attempt {restart_count + 1})...")
        sys.argv[0] = str(main_py)
        try:
            runpy.run_path(str(main_py), run_name="__main__")
            break
        except SystemExit as se:
            if se.code == 0:
                print("[run_bot] Clean exit.")
                break
            print(f"[run_bot] SystemExit with code {se.code}. Restarting in 5s...")
            time.sleep(5)
            restart_count += 1
        except KeyboardInterrupt:
            print("[run_bot] Interrupted by user.")
            break
        except Exception:
            restart_count += 1
            print(f"[run_bot] Crash in main.py (attempt {restart_count}/{max_restarts}):")
            traceback.print_exc()
            print("[run_bot] Auto-recovering in 5 seconds...")
            time.sleep(5)


if __name__ == "__main__":
    main()
