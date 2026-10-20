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
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("MT5Gateway")

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None
    logger.warning("Native MetaTrader5 library not found. Running in mock/simulation mode.")


_GW_RESOLVED_CACHE: Dict[str, str] = {}


def format_mt5_error(err: Any, login: Optional[int] = None, server: Optional[str] = None) -> str:
    """Format raw MT5 error into clear, actionable diagnosis."""
    raw_str = str(err)
    err_code = None
    if isinstance(err, (tuple, list)) and len(err) > 0:
        try:
            err_code = int(err[0])
        except (ValueError, TypeError):
            pass
    elif "-6" in raw_str:
        err_code = -6

    login_label = f"#{login}" if login else "account"
    srv_label = f"'{server}'" if server else "broker server"

    if err_code == -6 or "Authorization failed" in raw_str:
        broker_name = server.split("-")[0] if (server and "-" in server) else (server or "your broker")
        return (
            f"MT5 Authorization Failed: (-6, 'Terminal: Authorization failed') for {login_label} on {srv_label}.\n"
            f"Troubleshooting:\n"
            f"1. In MT5 Desktop: click 'File' > 'Open an Account' > search '{broker_name}' > click 'Find your broker' so MT5 downloads the '{server}.srv' server file.\n"
            f"2. Password: confirm you entered the MT5 Trading/Master password from broker email (not website login).\n"
            f"3. Account Type: confirm account #{login} is an MT5 account (not MT4)."
        )
    if err_code in (-10001, -10004, -10005, -10000) or "IPC" in raw_str:
        return (
            f"MT5 Terminal IPC Error ({raw_str}). "
            f"MetaTrader 5 terminal is not responding or not running. "
            f"Launch MetaTrader 5 on your Windows machine."
        )
    return f"MT5 connection failed: {raw_str}"

def get_candidate_symbols(symbol: str) -> List[str]:
    s = str(symbol or "").strip().upper().replace("/", "").replace("-", "")
    is_gold = any(x in s for x in ("XAU", "GOLD", "PAXG", "XAUT"))
    is_btc = any(x in s for x in ("BTC", "BITCOIN", "XBT"))
    suffixes = [
        "", ".m", "m", ".raw", "_raw", ".pro", "_pro", ".c", ".ecn", "_ecn",
        "_i", ".s", "_s", "_sb", ".std", ".a", ".z", "micro", "mini", "+", "#", "b", "."
    ]
    cands: List[str] = []
    base_names = ["XAUUSD", "GOLD", "XAUTUSD", "PAXGUSD", "PAXGUSDT", "XAU"] if is_gold else (
        ["BTCUSD", "BTCUSDT", "BITCOIN", "XBTUSD", "XBTUSDT", "BTC"] if is_btc else [s]
    )
    for b in base_names:
        for suf in suffixes:
            cand = f"{b}{suf}"
            if cand not in cands:
                cands.append(cand)
    return cands

def select_and_resolve_symbol(symbol: str) -> Tuple[str, Optional[Any]]:
    s = str(symbol or "").strip().upper().replace("/", "").replace("-", "")
    if s in _GW_RESOLVED_CACHE:
        cached = _GW_RESOLVED_CACHE[s]
        return cached, (mt5.symbol_info(cached) if mt5 is not None else None)
    if mt5 is None:
        return s, None
    try:
        # Check if already selectable
        if mt5.symbol_select(s, True):
            sinfo = mt5.symbol_info(s)
            if sinfo is not None:
                _GW_RESOLVED_CACHE[s] = s
                return s, sinfo
        # Check all candidates
        for cand in get_candidate_symbols(s):
            if mt5.symbol_select(cand, True):
                sinfo = mt5.symbol_info(cand)
                if sinfo is not None:
                    _GW_RESOLVED_CACHE[s] = cand
                    return cand, sinfo
            sinfo = mt5.symbol_info(cand)
            if sinfo is not None:
                mt5.symbol_select(cand, True)
                _GW_RESOLVED_CACHE[s] = cand
                return cand, sinfo
        # Query all symbols from MT5 server
        all_syms = mt5.symbols_get()
        if all_syms:
            is_gold = any(x in s for x in ("XAU", "GOLD", "PAXG"))
            is_btc = any(x in s for x in ("BTC", "BITCOIN", "XBT"))
            for item in all_syms:
                s_up = item.name.upper()
                if is_gold and ("XAU" in s_up or "GOLD" in s_up):
                    mt5.symbol_select(item.name, True)
                    sinfo = mt5.symbol_info(item.name)
                    if sinfo is not None:
                        _GW_RESOLVED_CACHE[s] = item.name
                        return item.name, sinfo
                elif is_btc and ("BTC" in s_up or "BITCOIN" in s_up):
                    mt5.symbol_select(item.name, True)
                    sinfo = mt5.symbol_info(item.name)
                    if sinfo is not None:
                        _GW_RESOLVED_CACHE[s] = item.name
                        return item.name, sinfo
    except Exception as e:
        logger.debug("Gateway select_and_resolve_symbol notice: %s", e)
    return s, (mt5.symbol_info(s) if mt5 is not None else None)


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
            term = mt5.terminal_info() if mt5 is not None else None
            term_trade = bool(getattr(term, "trade_allowed", True)) if term else True
            acc_trade = bool(getattr(acc, "trade_allowed", True))
            acc_expert = bool(getattr(acc, "trade_expert", True))
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
                "trade_allowed": acc_trade,
                "trade_expert": acc_expert,
                "terminal_trade_allowed": term_trade,
            })
        elif path == "/check":
            if mt5 is None:
                self._send_json(200, {
                    "ok": True,
                    "connected": True,
                    "terminal_algo_trading": True,
                    "account_trading_allowed": True,
                    "broker_expert_allowed": True,
                    "ready": True,
                    "symbols": {
                        "XAUUSD": {"ok": True, "trade_mode": 4, "filling_mode": "FOK/IOC"},
                        "BTCUSD": {"ok": True, "trade_mode": 4, "filling_mode": "FOK/IOC"},
                    },
                    "blockers": [],
                    "warnings": [],
                })
                return
            term = mt5.terminal_info()
            acc = mt5.account_info()
            term_trade = bool(getattr(term, "trade_allowed", True)) if term else True
            acc_trade = bool(getattr(acc, "trade_allowed", True)) if acc else True
            acc_expert = bool(getattr(acc, "trade_expert", True)) if acc else True
            blockers = []
            if not term_trade:
                blockers.append("MT5 Algo Trading is DISABLED in MetaTrader 5 toolbar. Click 'Algo Trading' button in MT5 to turn it GREEN.")
            if not acc_trade:
                blockers.append("Trading is DISABLED on account. Check if logged in with read-only Investor password.")
            if not acc_expert:
                blockers.append("Broker has disabled automated/EA trading for this account.")
            sym_checks = {}
            for sym in ("XAUUSD", "BTCUSD"):
                target_sym, sinfo = select_and_resolve_symbol(sym)
                if sinfo:
                    tmode = getattr(sinfo, "trade_mode", 4)
                    fmode = getattr(sinfo, "filling_mode", 0)
                    sym_checks[sym] = {
                        "ok": tmode != getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", 0),
                        "resolved": target_sym,
                        "trade_mode": tmode,
                        "filling_mode": "IOC" if (fmode & 2) else ("FOK" if (fmode & 1) else "RETURN"),
                        "min_volume": getattr(sinfo, "volume_min", 0.01),
                        "volume_step": getattr(sinfo, "volume_step", 0.01),
                    }
                else:
                    sym_checks[sym] = {"ok": False, "error": "Symbol not found in Market Watch"}
            self._send_json(200, {
                "ok": len(blockers) == 0,
                "connected": bool(acc),
                "terminal_algo_trading": term_trade,
                "account_trading_allowed": acc_trade,
                "broker_expert_allowed": acc_expert,
                "ready": len(blockers) == 0,
                "symbols": sym_checks,
                "blockers": blockers,
                "warnings": [],
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
        elif path == "/rates":
            parsed_url = urllib.parse.urlparse(self.path)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            sym = query_params.get("symbol", ["XAUUSD"])[0]
            tf_str = query_params.get("timeframe", ["1m"])[0].lower()
            limit = int(query_params.get("limit", [100])[0])
            if mt5 is None:
                now_ts = int(time.time())
                mock_rates = []
                base = 2700.0 if "XAU" in sym.upper() else 68000.0
                for i in range(min(limit, 100)):
                    t = now_ts - (min(limit, 100) - i) * 60
                    p = base + (i - 50) * 0.2
                    mock_rates.append({
                        "time": t,
                        "open": round(p, 2),
                        "high": round(p + 1.0, 2),
                        "low": round(p - 1.0, 2),
                        "close": round(p + 0.5, 2),
                        "volume": 100.0,
                    })
                self._send_json(200, {"ok": True, "symbol": sym, "rates": mock_rates})
                return

            tf_map = {
                "1m": getattr(mt5, "TIMEFRAME_M1", 1),
                "5m": getattr(mt5, "TIMEFRAME_M5", 5),
                "15m": getattr(mt5, "TIMEFRAME_M15", 15),
                "1h": getattr(mt5, "TIMEFRAME_H1", 60),
                "4h": getattr(mt5, "TIMEFRAME_H4", 240),
                "1d": getattr(mt5, "TIMEFRAME_D1", 1440),
            }
            tf = tf_map.get(tf_str, getattr(mt5, "TIMEFRAME_M1", 1))
            target_sym, _ = select_and_resolve_symbol(sym)
            rates = mt5.copy_rates_from_pos(target_sym, tf, 0, min(limit, 1000))
            if rates is None or len(rates) == 0:
                self._send_json(404, {"ok": False, "error": f"Failed to copy rates for {sym}: {mt5.last_error()}"})
                return

            out_rates = []
            for r in rates:
                out_rates.append({
                    "time": int(r["time"]),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": float(r.get("tick_volume", r.get("real_volume", 0.0))),
                })
            self._send_json(200, {"ok": True, "symbol": target_sym, "rates": out_rates})

        elif path == "/tick":
            parsed_url = urllib.parse.urlparse(self.path)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            sym = query_params.get("symbol", ["XAUUSD"])[0]
            if mt5 is None:
                base = 2700.0 if "XAU" in sym.upper() else 68000.0
                spread = 0.35 if "XAU" in sym.upper() else 3.50
                self._send_json(200, {
                    "ok": True,
                    "symbol": sym,
                    "bid": round(base - spread / 2.0, 2),
                    "ask": round(base + spread / 2.0, 2),
                    "mark": base,
                    "last": base,
                    "spread": spread,
                    "time": int(time.time()),
                })
                return

            target_sym, _ = select_and_resolve_symbol(sym)
            tick = mt5.symbol_info_tick(target_sym)
            if tick is None:
                self._send_json(404, {"ok": False, "error": f"No tick data for {sym}"})
                return
            bid = float(tick.bid)
            ask = float(tick.ask)
            last = float(tick.last or ((bid + ask) / 2.0))
            self._send_json(200, {
                "ok": True,
                "symbol": target_sym,
                "bid": bid,
                "ask": ask,
                "mark": last,
                "last": last,
                "spread": round(ask - bid, 4),
                "time": int(getattr(tick, "time", time.time())),
            })
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

            authorized = False
            init_err = None
            login_err = None

            # 1. If terminal is already running, try logging in directly
            try:
                if mt5.terminal_info() is not None:
                    ainfo = mt5.account_info()
                    if ainfo is not None and int(getattr(ainfo, "login", 0)) == login and login > 0:
                        authorized = True
                    elif login and password:
                        authorized = bool(mt5.login(login=login, password=password, server=server, timeout=60000))
                        if not authorized:
                            login_err = mt5.last_error()
            except Exception as e:
                logger.debug("Gateway direct login on running terminal failed: %s", e)

            # 2. If not yet authorized, initialize with target credentials
            if not authorized:
                init_kwargs = {}
                if mt5_path:
                    init_kwargs["path"] = mt5_path
                if login > 0:
                    init_kwargs["login"] = login
                if password:
                    init_kwargs["password"] = password
                if server:
                    init_kwargs["server"] = server
                init_kwargs["timeout"] = 60000

                init_ok = False
                try:
                    init_ok = bool(mt5.initialize(**init_kwargs))
                    if not init_ok:
                        init_err = mt5.last_error()
                except Exception as e:
                    init_err = str(e)

                if init_ok:
                    ainfo = mt5.account_info()
                    if ainfo is not None and int(getattr(ainfo, "login", 0)) == login and login > 0:
                        authorized = True
                    elif login and password:
                        authorized = bool(mt5.login(login=login, password=password, server=server, timeout=60000))
                        if not authorized:
                            login_err = mt5.last_error()
                else:
                    # 3. Fallback: initialize with path only and attempt login
                    fallback_kwargs = {"path": mt5_path} if mt5_path else {}
                    try:
                        mt5.initialize(**fallback_kwargs)
                    except Exception:
                        pass
                    if login and password:
                        try:
                            authorized = bool(mt5.login(login=login, password=password, server=server, timeout=60000))
                            if not authorized:
                                login_err = mt5.last_error()
                        except Exception as e:
                            login_err = str(e)

            if not authorized and (login > 0 or password):
                err = login_err or init_err or mt5.last_error()
                diag = format_mt5_error(err, login=login, server=server)
                self._send_json(401, {"ok": False, "error": diag})
                return

            acc = mt5.account_info()
            if not acc:
                self._send_json(400, {"ok": False, "error": "MT5 account_info failed"})
                return

            term = mt5.terminal_info() if mt5 is not None else None
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
                "trade_allowed": bool(getattr(acc, "trade_allowed", True)),
                "trade_expert": bool(getattr(acc, "trade_expert", True)),
                "terminal_trade_allowed": bool(getattr(term, "trade_allowed", True)) if term else True,
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
            magic = int(data.get("magic", 112233))
            comment = str(data.get("comment", "TradingBot Gateway"))

            # 0. Pre-flight checks on terminal & account permissions
            term = mt5.terminal_info()
            if term and not getattr(term, "trade_allowed", True):
                self._send_json(400, {
                    "ok": False,
                    "error": "MT5 Algo Trading is DISABLED in MetaTrader 5 toolbar! Click the 'Algo Trading' button in MT5 to turn it green, and verify Tools -> Options -> Expert Advisors -> 'Allow Algorithmic Trading' is checked."
                })
                return
            acc = mt5.account_info()
            if acc and not getattr(acc, "trade_allowed", True):
                self._send_json(400, {
                    "ok": False,
                    "error": "Trading is DISABLED for this MT5 account. If you logged in with an Investor (read-only) password, please log in with your Master/Trading password."
                })
                return
            if acc and not getattr(acc, "trade_expert", True):
                self._send_json(400, {
                    "ok": False,
                    "error": "Automated/EA trading is DISABLED by broker for this MT5 account."
                })
                return

            # 1. Market Watch symbol selection with comprehensive candidate fallbacks
            sym, sym_info = select_and_resolve_symbol(sym)
            if sym_info is None:
                self._send_json(400, {"ok": False, "error": f"Symbol '{sym}' not found in MT5 Market Watch."})
                return

            if getattr(sym_info, "trade_mode", 4) == getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", 0):
                self._send_json(400, {"ok": False, "error": f"Trading for '{sym}' is currently DISABLED by the broker (market closed)." })
                return

            # 2. Live Tick Price
            tick = mt5.symbol_info_tick(sym)
            order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
            if tick is not None:
                order_price = float(tick.ask) if side == "buy" else float(tick.bid)
            else:
                order_price = float(data.get("price", 0.0) or 100.0)

            # 3. Volume step & min/max normalization
            v_min = float(getattr(sym_info, "volume_min", 0.01) or 0.01)
            v_max = float(getattr(sym_info, "volume_max", 100.0) or 100.0)
            v_step = float(getattr(sym_info, "volume_step", 0.01) or 0.01)
            order_volume = max(v_min, min(v_max, qty))
            if v_step > 0:
                order_volume = round(round(order_volume / v_step) * v_step, 4)

            sl = float(data.get("stop_loss", 0.0) or data.get("sl", 0.0))
            tp = float(data.get("take_profit", 0.0) or data.get("tp", 0.0))

            # 4. Dynamic Filling Mode Auto-Negotiation (IOC / FOK / RETURN)
            filling_flags = getattr(sym_info, "filling_mode", 0) or 0
            modes_to_try = []
            if filling_flags & 2:
                modes_to_try.append(getattr(mt5, "ORDER_FILLING_IOC", 1))
            if filling_flags & 1:
                modes_to_try.append(getattr(mt5, "ORDER_FILLING_FOK", 0))
            modes_to_try.append(getattr(mt5, "ORDER_FILLING_RETURN", 2))
            for m in (getattr(mt5, "ORDER_FILLING_IOC", 1), getattr(mt5, "ORDER_FILLING_FOK", 0)):
                if m not in modes_to_try:
                    modes_to_try.append(m)

            res = None
            last_err = ""
            for fm in modes_to_try:
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": sym,
                    "volume": order_volume,
                    "type": order_type,
                    "price": order_price,
                    "sl": sl,
                    "tp": tp,
                    "deviation": 50,
                    "magic": magic,
                    "comment": comment,
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": fm,
                }
                res = mt5.order_send(req)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    break
                retcode = getattr(res, "retcode", None)
                err_cmt = getattr(res, "comment", mt5.last_error())
                last_err = f"{retcode} {err_cmt}"
                if retcode not in (getattr(mt5, "TRADE_RETCODE_UNSUPPORTED_FILLING_MODE", 10030), getattr(mt5, "TRADE_RETCODE_INVALID_REQUEST", 10013), 10030, 10013):
                    break

            if not res or res.retcode != mt5.TRADE_RETCODE_DONE:
                self._send_json(400, {"ok": False, "error": f"order_send failed: {last_err}"})
                return

            self._send_json(200, {
                "ok": True,
                "ticket": int(res.order),
                "price": float(res.price or order_price),
                "volume": float(res.volume or order_volume),
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

            tick = mt5.symbol_info_tick(pos.symbol)
            if tick is not None:
                close_price = float(tick.bid) if close_side == mt5.ORDER_TYPE_SELL else float(tick.ask)
            else:
                close_price = float(data.get("price") or pos.price_current)

            sym_info = mt5.symbol_info(pos.symbol)
            filling_flags = getattr(sym_info, "filling_mode", 0) or 0
            modes_to_try = []
            if filling_flags & 2:
                modes_to_try.append(getattr(mt5, "ORDER_FILLING_IOC", 1))
            if filling_flags & 1:
                modes_to_try.append(getattr(mt5, "ORDER_FILLING_FOK", 0))
            modes_to_try.append(getattr(mt5, "ORDER_FILLING_RETURN", 2))
            for m in (getattr(mt5, "ORDER_FILLING_IOC", 1), getattr(mt5, "ORDER_FILLING_FOK", 0)):
                if m not in modes_to_try:
                    modes_to_try.append(m)

            res = None
            for fm in modes_to_try:
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "position": pos.ticket,
                    "symbol": pos.symbol,
                    "volume": qty,
                    "type": close_side,
                    "price": close_price,
                    "deviation": 50,
                    "magic": int(data.get("magic", 112233)),
                    "comment": "Close Gateway",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": fm,
                }
                res = mt5.order_send(req)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    break
                retcode = getattr(res, "retcode", None)
                if retcode not in (getattr(mt5, "TRADE_RETCODE_UNSUPPORTED_FILLING_MODE", 10030), getattr(mt5, "TRADE_RETCODE_INVALID_REQUEST", 10013), 10030, 10013):
                    break

            if not res or res.retcode != mt5.TRADE_RETCODE_DONE:
                self._send_json(400, {"ok": False, "error": f"close failed: {mt5.last_error()}"})
                return
            self._send_json(200, {
                "ok": True,
                "closed": True,
                "price": float(res.price or close_price),
                "volume": float(res.volume or qty),
            })

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
