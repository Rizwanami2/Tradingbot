"""Local operations dashboard and health endpoint for TradingBot.

The dashboard is read-only. Bind to loopback by default; a non-loopback bind
requires a bearer token. It never controls the execution engine.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Optional


class StatusReadError(RuntimeError):
    pass


def is_authorized_user(user_id: Optional[int], allowed_user_ids: set[int]) -> bool:
    """Fail closed when no explicit Telegram control allowlist is configured."""
    return user_id is not None and bool(allowed_user_ids) and int(user_id) in allowed_user_ids


class StatusStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def write(self, snapshot: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(snapshot)
        payload.setdefault("schema_version", 1)
        payload.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, allow_nan=False, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": 1,
                "service": "TradingBot",
                "state": "unknown",
                "reason": "No status snapshot has been written yet.",
            }
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict) or payload.get("schema_version", 1) != 1:
                raise ValueError("Unsupported status snapshot schema")
            return payload
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise StatusReadError(f"Cannot read status snapshot: {exc}") from exc


def health_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
    stale_after_seconds: int = 90,
) -> dict[str, Any]:
    if stale_after_seconds < 1:
        raise ValueError("stale_after_seconds must be positive")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    reasons = []
    last_success = snapshot.get("last_success_at")
    age_seconds = None
    if not last_success:
        reasons.append("No successful market-data poll recorded")
    else:
        try:
            stamp = datetime.fromisoformat(str(last_success).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            age_seconds = max(0.0, (now - stamp.astimezone(timezone.utc)).total_seconds())
            if age_seconds > stale_after_seconds:
                reasons.append(f"Last successful poll is stale ({int(age_seconds)}s)")
        except (TypeError, ValueError):
            reasons.append("Invalid last_success_at timestamp")

    try:
        errors = int(snapshot.get("consecutive_errors", 0) or 0)
        if errors < 0:
            raise ValueError("negative error count")
    except (TypeError, ValueError):
        errors = 1
        reasons.append("Invalid consecutive error count")
    if errors > 0:
        reasons.append(f"{errors} consecutive monitoring error(s)")
    if snapshot.get("last_error") and errors > 0:
        reasons.append("Recent monitoring error(s) recorded")

    healthy = not reasons
    return {
        "status": "ok" if healthy else "degraded",
        "healthy": healthy,
        "checked_at": now.astimezone(timezone.utc).isoformat(),
        "last_success_age_seconds": age_seconds,
        "reasons": reasons,
    }


_DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TradingBot Operations</title>
<style>
body{font:16px system-ui,sans-serif;max-width:980px;margin:2rem auto;padding:0 1rem;background:#f6f7f9;color:#20242a}
h1{font-size:1.6rem}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.card{background:white;border:1px solid #dce0e5;border-radius:12px;padding:16px;overflow-wrap:anywhere}
.label{color:#606975;font-size:.85rem}.value{font-size:1.35rem;font-weight:650;margin-top:6px}
input,button{font:inherit;padding:8px;border-radius:8px;border:1px solid #b9c0c9}pre{white-space:pre-wrap;overflow-wrap:anywhere}
</style></head><body>
<h1>TradingBot Operations</h1>
<p>Read-only health and status. This page does not submit or control trades.</p>
<label>Dashboard token (if configured): <input id="token" type="password" autocomplete="current-password"></label>
<button id="refresh">Refresh</button><span id="updated" class="label"></span>
<div class="grid" id="cards"></div><h2>Health details</h2><pre id="health">Waiting for status…</pre>
<script>
async function load(){const token=document.getElementById('token').value;let headers={};
if(token)headers.Authorization='Bearer '+token;
try{const r=await fetch('/api/status',{headers,cache:'no-store'});const j=await r.json();
if(!r.ok)throw new Error(j.error||('HTTP '+r.status));
const items=[['Health',j.health?.status||'unknown'],['Symbol',j.symbol||'—'],['Autotrade monitoring',j.autotrade_active?'ON':'OFF'],['Configured broker mode',j.configured_broker_mode||'unknown'],['Equity',j.equity==null?'—':Number(j.equity).toLocaleString()],['Open positions',j.active_positions??'—'],['Pending setups',j.pending_setups??'—'],['Consecutive errors',j.consecutive_errors??0]];
const cards=document.getElementById('cards');cards.replaceChildren();for(const [k,v] of items){const card=document.createElement('div');card.className='card';const label=document.createElement('div');label.className='label';label.textContent=k;const value=document.createElement('div');value.className='value';value.textContent=String(v);card.append(label,value);cards.append(card);}
document.getElementById('health').textContent=JSON.stringify({health:j.health,last_poll_at:j.last_poll_at,last_success_at:j.last_success_at,last_error:j.last_error},null,2);
document.getElementById('updated').textContent=' Updated '+new Date().toLocaleTimeString();
}catch(e){document.getElementById('health').textContent=String(e);}}
document.getElementById('refresh').addEventListener('click',load);load();setInterval(load,5000);
</script></body></html>"""


def make_handler(store: StatusStore, token: str = ""):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: Mapping[str, Any]):
            body = json.dumps(payload, allow_nan=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if not token:
                return True
            supplied = self.headers.get("Authorization", "")
            return hmac.compare_digest(supplied, f"Bearer {token}")

        def do_GET(self):
            if self.path == "/health":
                try:
                    snapshot = store.read()
                    health = health_from_snapshot(snapshot)
                    self._json(200 if health["healthy"] else 503, health)
                except StatusReadError as exc:
                    self._json(503, {"status": "error", "healthy": False, "error": str(exc)})
                return
            if self.path == "/" or self.path == "/index.html":
                body = _DASHBOARD_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; connect-src 'self'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/status":
                if not self._authorized():
                    self._json(401, {"error": "Unauthorized"})
                    return
                try:
                    snapshot = store.read()
                    snapshot["health"] = health_from_snapshot(snapshot)
                    self._json(200, snapshot)
                except StatusReadError as exc:
                    self._json(503, {"error": str(exc)})
                return
            self._json(404, {"error": "Not found"})

        def log_message(self, fmt, *args):
            return

    return Handler


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    status_file: str | Path = ".runtime/bot_status.json",
    token: str = "",
) -> ThreadingHTTPServer:
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if not is_loopback and not token:
        raise ValueError("A dashboard token is required when binding beyond loopback")
    if not 0 <= int(port) <= 65535:
        raise ValueError("port must be between 0 and 65535")
    return ThreadingHTTPServer((host, int(port)), make_handler(StatusStore(status_file), token))


def main():
    import config
    server = create_server(
        host=getattr(config, "DASHBOARD_HOST", "127.0.0.1"),
        port=getattr(config, "DASHBOARD_PORT", 8765),
        status_file=getattr(config, "STATUS_FILE", ".runtime/bot_status.json"),
        token=getattr(config, "DASHBOARD_TOKEN", ""),
    )
    print(f"TradingBot read-only dashboard: http://{server.server_address[0]}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
