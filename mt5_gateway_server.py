#!/usr/bin/env python3
"""
MetaTrader 5 (MT5) Lightweight REST Gateway Server
==================================================
Runs on Windows PC or Windows VPS with MetaTrader 5 installed.
Acts as an HTTP bridge between remote Linux/Cloud TradingBot instances and the local MT5 terminal.

Usage on Windows:
    python mt5_gateway_server.py --port 5000

Endpoints:
- POST /connect      -> Initialize & login to MT5 terminal
- GET  /account      -> Telemetry: balance, equity, margin, leverage
- POST /order        -> Dispatch order_send (BUY / SELL)
- GET  /positions    -> Active open positions
- POST /close        -> Close position by symbol or ticket
- POST /modify       -> Modify SL/TP of an existing position
- GET  /health       -> Health check and terminal version
"""
from __future__ import annotations

import argparse
import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("MT5Gateway")

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None
    logger.warning("Native MetaTrader5 library not found. Running in mock/simulation mode.")


class MT5GatewayHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, data: Dict[str, Any]):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path in ("", "/health"):
            version = mt5.version() if mt5 is not None else (0, 0, "mock")
            self._send_json(200, {
                "ok": True,
                "status": "healthy",
                "has_native_mt5": mt5 is not None,
                "mt5_version": version,
            })
        elif path == "/account":
            if mt5 is None:
                self._send_json(200, {
                    "ok": True,
                    "login": 0,
                    "server": "Mock",
                    "balance": 10000.0,
                    "equity": 10000.0,
                    "margin": 0.0,
                    "free_margin": 10000.0,
                    "margin_level_pct": 999.0,
                    "leverage": 20.0,
                    "currency": "USD",
                    "company": "Mock-Server",
                })
                return
            acc = mt5.account_info()
            if not acc:
                self._send_json(400, {"ok": False, "error": f"Failed to get account_info: {mt5.last_error()}"})
                return
            mg = float(acc.margin or 0.0)
            eq = float(acc.equity or 0.0)
            lvl = (eq / mg * 100.0) if mg > 0 else 999.0
            self._send_json(200, {
                "ok": True,
                "login": int(acc.login),
                "server": str(acc.server),
                "balance": float(acc.balance),
                "equity": eq,
                "margin": mg,
                "free_margin": float(acc.margin_free or 0.0),
                "margin_level_pct": lvl,
                "leverage": float(acc.leverage or 20.0),
                "currency": str(acc.currency or "USD"),
                "company": str(acc.company or "MetaQuotes"),
            })
        elif path == "/positions":
            if mt5 is None:
                self._send_json(200, {"ok": True, "positions": []})
                return
            raw_pos = mt5.positions_get()
            out = []
            if raw_pos:
                for p in raw_pos:
                    out.append({
                        "ticket": int(p.ticket),
                        "symbol": str(p.symbol),
                        "type": "buy" if p.type == mt5.ORDER_TYPE_BUY else "sell",
                        "volume": float(p.volume),
                        "price_open": float(p.price_open),
                        "sl": float(p.sl or 0.0),
                        "tp": float(p.tp or 0.0),
                        "profit": float(p.profit or 0.0),
                    })
            self._send_json(200, {"ok": True, "positions": out})
        else:
            self._send_json(404, {"ok": False, "error": f"Unknown endpoint: {path}"})

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        data = self._read_json()

        if path == "/connect":
            login = int(data.get("login") or 0)
            password = str(data.get("password") or "")
            server = str(data.get("server") or "")
            mt5_path = str(data.get("path") or "")

            if mt5 is None:
                self._send_json(200, {
                    "ok": True,
                    "connected": True,
                    "login": login,
                    "server": server,
                    "balance": 10000.0,
                    "equity": 10000.0,
                    "leverage": 20.0,
                    "currency": "USD",
                    "company": "Mock-Server",
                })
                return

            init_kwargs = {"path": mt5_path} if mt5_path else {}
            if not mt5.initialize(**init_kwargs):
                self._send_json(400, {"ok": False, "error": f"MT5 initialize failed: {mt5.last_error()}"})
                return
            if login and password:
                if not mt5.login(login=login, password=password, server=server):
                    self._send_json(401, {"ok": False, "error": f"MT5 login failed: {mt5.last_error()}"})
                    return

            acc = mt5.account_info()
            if not acc:
                self._send_json(400, {"ok": False, "error": "MT5 account_info failed"})
                return

            self._send_json(200, {
                "ok": True,
                "connected": True,
                "login": int(acc.login),
                "server": str(acc.server),
                "balance": float(acc.balance),
                "equity": float(acc.equity),
                "leverage": float(acc.leverage or 20.0),
                "currency": str(acc.currency or "USD"),
                "company": str(acc.company or "MetaQuotes"),
            })

        elif path == "/order":
            if mt5 is None:
                self._send_json(200, {
                    "ok": True,
                    "ticket": 1234567,
                    "price": float(data.get("price") or 100.0),
                    "volume": float(data.get("qty") or 0.1),
                })
                return
            sym = str(data.get("symbol", "BTCUSD"))
            side = str(data.get("side", "buy")).lower()
            qty = float(data.get("qty", 0.01))
            price = float(data.get("price", 0.0))
            sl = float(data.get("stop_loss", 0.0) or data.get("sl", 0.0))
            tp = float(data.get("take_profit", 0.0) or data.get("tp", 0.0))
            magic = int(data.get("magic", 112233))
            comment = str(data.get("comment", "TradingBot Gateway"))

            order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": sym,
                "volume": qty,
                "type": order_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": 20,
                "magic": magic,
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            res = mt5.order_send(req)
            if not res or res.retcode != mt5.TRADE_RETCODE_DONE:
                err = getattr(res, "comment", mt5.last_error())
                retcode = getattr(res, "retcode", None)
                self._send_json(400, {"ok": False, "error": f"order_send failed: {retcode} {err}"})
                return
            self._send_json(200, {
                "ok": True,
                "ticket": int(res.order),
                "price": float(res.price or price),
                "volume": float(res.volume or qty),
            })

        elif path == "/close":
            if mt5 is None:
                self._send_json(200, {"ok": True, "closed": True})
                return
            sym = str(data.get("symbol", ""))
            ticket = int(data.get("ticket", 0) or 0)
            if ticket:
                positions = mt5.positions_get(ticket=ticket)
            elif sym:
                positions = mt5.positions_get(symbol=sym)
            else:
                positions = []
            if not positions:
                self._send_json(404, {"ok": False, "error": "Position not found"})
                return
            pos = positions[0]
            close_side = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            qty = float(data.get("qty") or pos.volume)
            price = float(data.get("price") or pos.price_current)
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "volume": qty,
                "type": close_side,
                "price": price,
                "deviation": 20,
                "magic": int(data.get("magic", 112233)),
                "comment": "Close Gateway",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            res = mt5.order_send(req)
            if not res or res.retcode != mt5.TRADE_RETCODE_DONE:
                self._send_json(400, {"ok": False, "error": f"close failed: {mt5.last_error()}"})
                return
            self._send_json(200, {"ok": True, "closed": True})

        elif path == "/modify":
            if mt5 is None:
                self._send_json(200, {"ok": True, "modified": True})
                return
            sym = str(data.get("symbol", ""))
            ticket = int(data.get("ticket", 0) or 0)
            if ticket:
                positions = mt5.positions_get(ticket=ticket)
            elif sym:
                positions = mt5.positions_get(symbol=sym)
            else:
                positions = []
            if not positions:
                self._send_json(404, {"ok": False, "error": "Position not found"})
                return
            pos = positions[0]
            sl = float(data.get("stop_loss") or pos.sl or 0.0)
            tp = float(data.get("take_profit") or pos.tp or 0.0)
            req = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "sl": sl,
                "tp": tp,
            }
            res = mt5.order_send(req)
            if not res or res.retcode != mt5.TRADE_RETCODE_DONE:
                self._send_json(400, {"ok": False, "error": f"modify failed: {mt5.last_error()}"})
                return
            self._send_json(200, {"ok": True, "modified": True})

        else:
            self._send_json(404, {"ok": False, "error": f"Unknown endpoint: {path}"})

    def log_message(self, format, *args):
        logger.debug("%s - - [%s] %s", self.client_address[0], self.log_date_time_string(), format % args)


def run_server(port: int = 5000, host: str = "0.0.0.0"):
    server = HTTPServer((host, port), MT5GatewayHandler)
    logger.info("MT5 REST Gateway Server running at http://%s:%d", host, port)
    logger.info("Ready to accept requests from remote TradingBot instances.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Gateway server stopped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MT5 REST Gateway Server")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on (default: 5000)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host interface (default: 0.0.0.0)")
    args = parser.parse_args()
    run_server(port=args.port, host=args.host)
