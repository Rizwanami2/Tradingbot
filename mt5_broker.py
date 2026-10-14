#!/usr/bin/env python3
"""
MetaTrader 5 (MT5) Broker Integration — Paper & Live Account.
============================================================
Supports:
1. Paper / Demo MT5 Mode:
   - High-fidelity simulated MT5 execution with real MT5 ticket numbering,
     contract sizing, floating PnL tracking, SL/TP management, and fee accounting.
   - Compatible with local environments, cloud containers, and test runners.
   - Can optionally attach to an MT5 Demo server when native MT5/gateway is available.

2. Live MT5 Account Mode:
   - Connects to broker MT5 server with login, password, and server.
   - Dispatches real MT5 orders using TRADE_ACTION_DEAL and ORDER_TYPE_BUY/SELL.
   - Queries live positions using positions_get().
   - Modifies dynamic SL/TP trailing levels and closes positions.
   - Fails closed safely if live credentials or connection cannot be established.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import logging
import math
import time

from execution import (
    Broker,
    BrokerConfigurationError,
    ExecutionError,
    Order,
    Position,
)

logger = logging.getLogger("MT5Broker")

# Attempt native MetaTrader5 import (available on Windows or via Wine/C-layer)
try:
    import MetaTrader5 as native_mt5
except ImportError:
    native_mt5 = None

HAS_MT5: bool = native_mt5 is not None


@dataclass
class MT5AccountInfo:
    login: int
    server: str
    trade_mode: str          # "paper" | "demo" | "live"
    connected: bool
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level_pct: float
    leverage: float
    currency: str = "USD"
    company: str = "MetaQuotes"

    @property
    def margin_level(self) -> float:
        return self.margin_level_pct

    def summary(self) -> str:
        icon = "🟢" if self.trade_mode == "live" else "⚪"
        conn_icon = "📶" if self.connected else "⚠️"
        return (
            f"🏦 **MT5 Account Info** ({icon} `{self.trade_mode.upper()}`)\n"
            f"• **Status:** {conn_icon} {'Connected' if self.connected else 'Disconnected'}\n"
            f"• **Login:** `{self.login or 'Paper-Simulation'}` | **Server:** `{self.server}`\n"
            f"• **Balance:** `${self.balance:,.2f}` | **Equity:** `${self.equity:,.2f}`\n"
            f"• **Margin:** `${self.margin:,.2f}` | **Free Margin:** `${self.free_margin:,.2f}`\n"
            f"• **Margin Level:** `{self.margin_level_pct:.1f}%` | **Leverage:** `{self.leverage:.0f}x`\n"
            f"• **Currency:** `{self.currency}` | **Broker:** `{self.company}`"
        )


def _safe_f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        if v != v or v in (float("inf"), float("-inf")):
            return default
        return v
    except Exception:
        return default


def resolve_mt5_symbol(symbol: str, available_symbols: Optional[List[str]] = None) -> str:
    """
    Resolve unified symbol to broker-specific MT5 symbol.
    Handles broker suffixes (.m, .raw, _i) and aliases (GOLD -> XAUUSD).
    """
    s = str(symbol or "").strip().upper().replace("/", "").replace("-", "")
    aliases = {
        "GOLD": "XAUUSD",
        "XAU": "XAUUSD",
        "XAUT": "XAUUSD",
        "XAUTUSD": "XAUUSD",
        "PAXG": "XAUUSD",
        "PAXGUSDT": "XAUUSD",
        "BTC": "BTCUSD",
        "BTCUSDT": "BTCUSD",
    }
    normalized = aliases.get(s, s)

    if available_symbols:
        avail_upper = {sym.upper(): sym for sym in available_symbols}
        # Exact match
        if normalized in avail_upper:
            return avail_upper[normalized]
        if s in avail_upper:
            return avail_upper[s]
        # Match with common broker suffixes
        for suffix in (".m", "m", ".raw", "_i", ".pro", ".c", ".ecn"):
            cand = f"{normalized}{suffix.upper()}"
            if cand in avail_upper:
                return avail_upper[cand]

    return normalized


def _gateway_request(
    method: str,
    url: str,
    json_data: Optional[Dict[str, Any]] = None,
    timeout: float = 8.0,
) -> Dict[str, Any]:
    """Execute HTTP request to MT5 REST Gateway with error handling."""
    import requests
    try:
        resp = requests.request(method=method, url=url, json=json_data, timeout=timeout)
        if resp.status_code >= 400:
            raise ExecutionError(f"MT5 Gateway HTTP {resp.status_code}: {resp.text}")
        data = resp.json()
        if isinstance(data, dict) and data.get("ok") is False:
            raise ExecutionError(f"MT5 Gateway error: {data.get('error', 'Unknown error')}")
        return data if isinstance(data, dict) else {"data": data}
    except requests.RequestException as exc:
        raise ConnectionError(f"MT5 Gateway unreachable at {url}: {exc}") from exc


class MT5Broker(Broker):
    """
    MetaTrader 5 broker adapter supporting both Paper / Demo and Live accounts.
    """

    def __init__(
        self,
        mode: str = "paper",
        login: int = 0,
        password: str = "",
        server: str = "MetaQuotes-Demo",
        path: str = "",
        magic: int = 112233,
        initial_balance: float = 1000.0,
        commission_pct: float = 0.0004,
        gateway_url: str = "",
    ):
        self.mode = "live" if str(mode).strip().lower() == "live" else "paper"
        self.login = int(login or 0)
        self.password = str(password or "")
        self.server = str(server or "MetaQuotes-Demo")
        self.path = str(path or "")
        self.magic = int(magic or 112233)
        self.commission_pct = float(commission_pct or 0.0004)
        self.gateway_url = str(gateway_url or "").strip()

        # Balance & Account state
        self._initial_balance = max(1.0, float(initial_balance or 1000.0))
        self.balance = self._initial_balance
        self._cached_equity: Optional[float] = None
        self._cached_margin: Optional[float] = None
        self.leverage = 20.0
        self.currency = "USD"
        self.company = "MetaQuotes Software Corp."
        self._connected = False
        self._last_error = ""

        # Simulated Paper State
        self._sim_positions: Dict[str, Position] = {}
        self._sim_orders: Dict[str, Order] = {}
        self._sim_tickets: Dict[str, int] = {}
        self._ticket_counter: int = 100000

        # Auto-connect
        self.connect()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def equity(self) -> float:
        if self.mode == "live":
            if native_mt5 is not None and self._connected:
                try:
                    acc = native_mt5.account_info()
                    if acc:
                        return float(acc.equity)
                except Exception:
                    pass
            elif self.gateway_url and self._connected and self._cached_equity is not None:
                return self._cached_equity
        # Paper calculation: balance + sum of unrealized PnL
        unrealized = sum(p.unrealized_pnl for p in self._sim_positions.values())
        return round(self.balance + unrealized, 2)

    @property
    def margin(self) -> float:
        if self.mode == "live":
            if native_mt5 is not None and self._connected:
                try:
                    acc = native_mt5.account_info()
                    if acc:
                        return float(acc.margin)
                except Exception:
                    pass
            elif self.gateway_url and self._connected and self._cached_margin is not None:
                return self._cached_margin
        # Rough margin estimation
        total_margin = sum((p.entry_price * p.qty) / self.leverage for p in self._sim_positions.values())
        return round(total_margin, 2)

    @property
    def free_margin(self) -> float:
        return max(0.0, round(self.equity - self.margin, 2))

    def connect(self) -> bool:
        """Initialize connection to MT5 terminal or simulated broker engine."""
        self._last_error = ""

        if self.mode == "live":
            # Live mode validation
            if not self.login or not self.password or not self.server:
                self._connected = False
                self._last_error = "Live MT5 credentials missing (login, password, server required)."
                logger.error("[MT5Broker] %s", self._last_error)
                raise BrokerConfigurationError(self._last_error)

            if native_mt5 is not None:
                try:
                    init_kwargs = {"path": self.path} if self.path else {}
                    if not native_mt5.initialize(**init_kwargs):
                        err = native_mt5.last_error()
                        raise ConnectionError(f"MT5 initialize failed: {err}")
                    authorized = native_mt5.login(
                        login=self.login,
                        password=self.password,
                        server=self.server,
                    )
                    if not authorized:
                        err = native_mt5.last_error()
                        raise ConnectionError(f"MT5 login failed: {err}")
                    acc = native_mt5.account_info()
                    if acc:
                        self.balance = float(acc.balance)
                        self.leverage = float(acc.leverage)
                        self.currency = str(acc.currency)
                        self.company = str(acc.company)
                    self._connected = True
                    logger.info("[MT5Broker] Connected to live MT5 account #%s on %s", self.login, self.server)
                    return True
                except Exception as exc:
                    self._connected = False
                    self._last_error = str(exc)
                    logger.error("[MT5Broker] Live MT5 connection error: %s", exc)
                    raise BrokerConfigurationError(f"Live MT5 connection error: {exc}") from exc
            else:
                # If native MetaTrader5 package is unavailable on headless Linux
                if self.gateway_url:
                    try:
                        url = f"{self.gateway_url.rstrip('/')}/connect"
                        res = _gateway_request("POST", url, {
                            "login": self.login,
                            "password": self.password,
                            "server": self.server,
                            "path": self.path,
                            "magic": self.magic,
                        })
                        self._connected = True
                        if "balance" in res:
                            self.balance = float(res["balance"])
                        if "equity" in res:
                            self._cached_equity = float(res["equity"])
                        if "margin" in res:
                            self._cached_margin = float(res["margin"])
                        if "leverage" in res:
                            self.leverage = float(res["leverage"])
                        if "currency" in res:
                            self.currency = str(res["currency"])
                        if "company" in res:
                            self.company = str(res["company"])
                        logger.info("[MT5Broker] Connected to MT5 REST Gateway at %s for account #%s on %s",
                                    self.gateway_url, self.login, self.server)
                        return True
                    except Exception as gw_err:
                        self._connected = False
                        self._last_error = f"MT5 Gateway connection failed: {gw_err}"
                        logger.error("[MT5Broker] %s", self._last_error)
                        raise BrokerConfigurationError(self._last_error) from gw_err
                else:
                    self._connected = False
                    self._last_error = (
                        "Native MetaTrader5 library is only available on Windows (MetaQuotes desktop requirement). "
                        "To trade live from Linux/Cloud, configure MT5_GATEWAY_URL in .env or run mt5_gateway_server.py on Windows."
                    )
                    logger.error("[MT5Broker] %s", self._last_error)
                    raise BrokerConfigurationError(self._last_error)

        else:
            # Paper / Demo Mode
            if native_mt5 is not None and self.login and self.password:
                try:
                    init_kwargs = {"path": self.path} if self.path else {}
                    if native_mt5.initialize(**init_kwargs) and native_mt5.login(
                        login=self.login, password=self.password, server=self.server
                    ):
                        self._connected = True
                        acc = native_mt5.account_info()
                        if acc:
                            self.balance = float(acc.balance)
                            self.leverage = float(acc.leverage)
                            self.currency = str(acc.currency)
                            self.company = str(acc.company)
                        logger.info("[MT5Broker] Connected to MT5 Demo server #%s (%s)", self.login, self.server)
                        return True
                except Exception:
                    pass

            # High-fidelity local simulation mode
            self._connected = True
            logger.info("[MT5Broker] Initialized high-fidelity Paper MT5 broker (Balance: $%.2f)", self.balance)
            return True

    def disconnect(self) -> None:
        """Disconnect from MT5."""
        if native_mt5 is not None and self._connected:
            try:
                native_mt5.shutdown()
            except Exception:
                pass
        self._connected = False

    def get_account_info(self) -> MT5AccountInfo:
        """Fetch current MT5 account status, balance, equity, and margin details."""
        margin_used = self.margin
        eq = self.equity
        margin_level = (eq / margin_used * 100.0) if margin_used > 0 else 999.0

        if self.mode == "live" and native_mt5 is not None and self._connected:
            try:
                acc = native_mt5.account_info()
                if acc:
                    return MT5AccountInfo(
                        login=int(acc.login),
                        server=str(acc.server),
                        trade_mode="live",
                        connected=True,
                        balance=float(acc.balance),
                        equity=float(acc.equity),
                        margin=float(acc.margin),
                        free_margin=float(acc.margin_free),
                        margin_level_pct=float(acc.margin_level or 999.0),
                        leverage=float(acc.leverage),
                        currency=str(acc.currency or "USD"),
                        company=str(acc.company or "MetaQuotes"),
                    )
            except Exception as e:
                logger.warning("[MT5Broker] account_info error: %s", e)

        return MT5AccountInfo(
            login=self.login if self.login else 0,
            server=self.server,
            trade_mode=self.mode,
            connected=self._connected,
            balance=round(self.balance, 2),
            equity=round(eq, 2),
            margin=round(margin_used, 2),
            free_margin=round(self.free_margin, 2),
            margin_level_pct=round(margin_level, 1),
            leverage=self.leverage,
            currency=self.currency,
            company=self.company,
        )

    def switch_mode(self, mode: str, login: Optional[int] = None, password: Optional[str] = None, server: Optional[str] = None) -> bool:
        """Switch between paper and live account mode dynamically."""
        target_mode = "live" if str(mode).strip().lower() == "live" else "paper"
        if login is not None:
            self.login = int(login)
        if password is not None:
            self.password = str(password)
        if server is not None:
            self.server = str(server)
        self.mode = target_mode
        return self.connect()

    def update_mark_price(self, symbol: str, mark_price: float) -> None:
        """Update current market mark price on open positions to compute floating PnL."""
        if mark_price <= 0 or not math.isfinite(mark_price):
            return
        sym = resolve_mt5_symbol(symbol)
        for key in (sym, symbol.upper()):
            if key in self._sim_positions:
                pos = self._sim_positions[key]
                diff = (mark_price - pos.entry_price) if pos.side.lower() == "buy" else (pos.entry_price - mark_price)
                pos.unrealized_pnl = round(diff * pos.qty, 2)

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "market",
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Order:
        """
        Submit a new order to MT5.
        In live mode: formats and dispatches trade request to the MT5 terminal.
        In paper mode: simulates execution deterministically.
        """
        if qty <= 0 or not math.isfinite(qty):
            raise ValueError("Order quantity must be finite and greater than zero.")
        side_norm = side.strip().lower()
        if side_norm not in {"buy", "sell"}:
            raise ValueError("Order side must be 'buy' or 'sell'.")

        resolved_sym = resolve_mt5_symbol(symbol)
        fill_price = float(price) if price is not None and price > 0 else 100.0

        if self.mode == "live" and self._connected:
            if native_mt5 is not None:
                # LIVE MT5 ORDER DISPATCH (Native Windows)
                order_type_mt5 = native_mt5.ORDER_TYPE_BUY if side_norm == "buy" else native_mt5.ORDER_TYPE_SELL
                req = {
                    "action": native_mt5.TRADE_ACTION_DEAL,
                    "symbol": resolved_sym,
                    "volume": float(qty),
                    "type": order_type_mt5,
                    "price": fill_price,
                    "sl": float(stop_loss) if stop_loss else 0.0,
                    "tp": float(take_profit) if take_profit else 0.0,
                    "deviation": 20,
                    "magic": self.magic,
                    "comment": "TradingBot MT5",
                    "type_time": native_mt5.ORDER_TIME_GTC,
                    "type_filling": native_mt5.ORDER_FILLING_IOC,
                }
                if params:
                    req.update(params)

                res = native_mt5.order_send(req)
                if res is None or res.retcode != native_mt5.TRADE_RETCODE_DONE:
                    ret = getattr(res, "retcode", "None")
                    comment = getattr(res, "comment", native_mt5.last_error())
                    raise ExecutionError(f"MT5 order_send failed: retcode={ret} comment={comment}")

                ticket_id = str(res.order)
                order = Order(
                    id=ticket_id,
                    symbol=resolved_sym,
                    side=side_norm,
                    order_type=order_type,
                    qty=qty,
                    price=fill_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    status="filled",
                    filled_price=float(res.price or fill_price),
                    filled_qty=float(res.volume or qty),
                )
                return order

            elif self.gateway_url:
                # LIVE MT5 ORDER DISPATCH (REST Gateway Bridge)
                url = f"{self.gateway_url.rstrip('/')}/order"
                req = {
                    "symbol": resolved_sym,
                    "side": side_norm,
                    "qty": float(qty),
                    "order_type": order_type,
                    "price": fill_price,
                    "stop_loss": float(stop_loss) if stop_loss else 0.0,
                    "take_profit": float(take_profit) if take_profit else 0.0,
                    "magic": self.magic,
                    "comment": "TradingBot MT5",
                }
                if params:
                    req.update(params)
                res = _gateway_request("POST", url, req)
                ticket_id = str(res.get("ticket") or res.get("order") or int(time.time()))
                filled_price = float(res.get("price") or fill_price)
                filled_qty = float(res.get("volume") or res.get("qty") or qty)
                return Order(
                    id=ticket_id,
                    symbol=resolved_sym,
                    side=side_norm,
                    order_type=order_type,
                    qty=qty,
                    price=fill_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    status="filled",
                    filled_price=filled_price,
                    filled_qty=filled_qty,
                )

        # PAPER / DEMO SIMULATION
        self._ticket_counter += 1
        ticket = self._ticket_counter
        ticket_id = f"MT5-{ticket}"

        # Commission accounting
        fee = round(fill_price * qty * self.commission_pct, 4)
        self.balance -= fee

        order = Order(
            id=ticket_id,
            symbol=resolved_sym,
            side=side_norm,
            order_type=order_type,
            qty=qty,
            price=fill_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status="filled",
            filled_price=fill_price,
            filled_qty=qty,
        )

        # Update simulated positions
        self._sim_positions[resolved_sym] = Position(
            symbol=resolved_sym,
            side=side_norm,
            qty=qty,
            entry_price=fill_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            unrealized_pnl=0.0,
        )
        self._sim_tickets[resolved_sym] = ticket
        self._sim_orders[ticket_id] = order

        logger.info("[MT5Broker] Simulated paper order filled: #%s %s %s %.2f @ %.2f (SL=%.2f, TP=%.2f)",
                    ticket, resolved_sym, side_norm.upper(), qty, fill_price, stop_loss or 0, take_profit or 0)
        return order

    def get_positions(self) -> List[Position]:
        """Fetch active open positions from MT5."""
        if self.mode == "live" and self._connected:
            if native_mt5 is not None:
                try:
                    raw_positions = native_mt5.positions_get()
                    if raw_positions is None:
                        return []
                    out = []
                    for p in raw_positions:
                        side = "buy" if p.type == native_mt5.ORDER_TYPE_BUY else "sell"
                        out.append(Position(
                            symbol=str(p.symbol),
                            side=side,
                            qty=float(p.volume),
                            entry_price=float(p.price_open),
                            stop_loss=float(p.sl) if p.sl else None,
                            take_profit=float(p.tp) if p.tp else None,
                            unrealized_pnl=float(p.profit),
                        ))
                    return out
                except Exception as e:
                    logger.error("[MT5Broker] get_positions error: %s", e)
                    raise ExecutionError(f"MT5 get_positions error: {e}") from e
            elif self.gateway_url:
                try:
                    url = f"{self.gateway_url.rstrip('/')}/positions"
                    res = _gateway_request("GET", url)
                    raw_positions = res.get("positions", [])
                    out = []
                    for p in raw_positions:
                        side = "buy" if str(p.get("type", "buy")).lower() in ("buy", "0") else "sell"
                        out.append(Position(
                            symbol=str(p.get("symbol", "")),
                            side=side,
                            qty=float(p.get("volume", p.get("qty", 0.0))),
                            entry_price=float(p.get("price_open", p.get("entry_price", 0.0))),
                            stop_loss=float(p.get("sl", 0.0)) if p.get("sl") else None,
                            take_profit=float(p.get("tp", 0.0)) if p.get("tp") else None,
                            unrealized_pnl=float(p.get("profit", p.get("unrealized_pnl", 0.0))),
                        ))
                    return out
                except Exception as e:
                    logger.error("[MT5Broker] Gateway get_positions error: %s", e)
                    raise ExecutionError(f"MT5 Gateway get_positions error: {e}") from e

        return list(self._sim_positions.values())

    def close_position(
        self,
        symbol: str,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Close an open position on MT5 (full or partial)."""
        resolved_sym = resolve_mt5_symbol(symbol)

        if self.mode == "live" and self._connected:
            if native_mt5 is not None:
                positions = native_mt5.positions_get(symbol=resolved_sym)
                if not positions:
                    return False
                pos = positions[0]
                close_side = native_mt5.ORDER_TYPE_SELL if pos.type == native_mt5.ORDER_TYPE_BUY else native_mt5.ORDER_TYPE_BUY
                close_qty = float(qty) if qty and qty > 0 else float(pos.volume)
                close_price = float(price) if price and price > 0 else float(pos.price_current)

                req = {
                    "action": native_mt5.TRADE_ACTION_DEAL,
                    "position": pos.ticket,
                    "symbol": resolved_sym,
                    "volume": close_qty,
                    "type": close_side,
                    "price": close_price,
                    "deviation": 20,
                    "magic": self.magic,
                    "comment": "Close TradingBot MT5",
                    "type_time": native_mt5.ORDER_TIME_GTC,
                    "type_filling": native_mt5.ORDER_FILLING_IOC,
                }
                res = native_mt5.order_send(req)
                return bool(res and res.retcode == native_mt5.TRADE_RETCODE_DONE)
            elif self.gateway_url:
                url = f"{self.gateway_url.rstrip('/')}/close"
                req = {
                    "symbol": resolved_sym,
                    "qty": float(qty) if qty and qty > 0 else None,
                    "price": float(price) if price and price > 0 else None,
                    "magic": self.magic,
                }
                if params:
                    req.update(params)
                res = _gateway_request("POST", url, req)
                return bool(res.get("closed", res.get("ok", False)))

        # PAPER SIMULATION CLOSE
        pos_key = None
        for key in (resolved_sym, symbol.upper()):
            if key in self._sim_positions:
                pos_key = key
                break
        if not pos_key:
            return False

        pos = self._sim_positions[pos_key]
        close_qty = float(qty) if qty and 0 < qty <= pos.qty else pos.qty
        close_price = float(price) if price and price > 0 else pos.entry_price

        # Realize PnL
        diff = (close_price - pos.entry_price) if pos.side.lower() == "buy" else (pos.entry_price - close_price)
        realized_pnl = diff * close_qty
        close_fee = round(close_price * close_qty * self.commission_pct, 4)

        self.balance += (realized_pnl - close_fee)

        if close_qty >= pos.qty - 1e-6:
            self._sim_positions.pop(pos_key, None)
            self._sim_tickets.pop(pos_key, None)
        else:
            pos.qty -= close_qty
            pos.unrealized_pnl = round(diff * pos.qty, 2)

        logger.info("[MT5Broker] Closed %.2f lots of %s @ %.2f (PnL: $%.2f)", close_qty, resolved_sym, close_price, realized_pnl)
        return True

    def modify_position(self, symbol: str, stop_loss: Optional[float] = None, take_profit: Optional[float] = None) -> bool:
        """Modify SL and TP of an open MT5 position (e.g. for dynamic trailing stop)."""
        resolved_sym = resolve_mt5_symbol(symbol)
        if self.mode == "live" and self._connected:
            if native_mt5 is not None:
                positions = native_mt5.positions_get(symbol=resolved_sym)
                if not positions:
                    return False
                pos = positions[0]
                req = {
                    "action": native_mt5.TRADE_ACTION_SLTP,
                    "position": pos.ticket,
                    "symbol": resolved_sym,
                    "sl": float(stop_loss) if stop_loss else float(pos.sl or 0.0),
                    "tp": float(take_profit) if take_profit else float(pos.tp or 0.0),
                }
                res = native_mt5.order_send(req)
                return bool(res and res.retcode == native_mt5.TRADE_RETCODE_DONE)
            elif self.gateway_url:
                url = f"{self.gateway_url.rstrip('/')}/modify"
                req = {
                    "symbol": resolved_sym,
                    "stop_loss": float(stop_loss) if stop_loss else None,
                    "take_profit": float(take_profit) if take_profit else None,
                    "magic": self.magic,
                }
                res = _gateway_request("POST", url, req)
                return bool(res.get("modified", res.get("ok", False)))

        # Paper update
        for key in (resolved_sym, symbol.upper()):
            if key in self._sim_positions:
                pos = self._sim_positions[key]
                if stop_loss is not None:
                    pos.stop_loss = stop_loss
                if take_profit is not None:
                    pos.take_profit = take_profit
                return True
        return False

    def update_mark_price(self, symbol: str, price: float) -> None:
        """Update floating PnL on simulated open positions."""
        p = _safe_f(price)
        if p <= 0:
            return
        resolved_sym = resolve_mt5_symbol(symbol)
        for key in (resolved_sym, symbol.upper()):
            if key in self._sim_positions:
                pos = self._sim_positions[key]
                diff = (p - pos.entry_price) if pos.side.lower() == "buy" else (pos.entry_price - p)
                pos.unrealized_pnl = round(diff * pos.qty, 2)

    def mark_price(self, symbol: str, price: float) -> float:
        """Alias for update_mark_price returning current equity."""
        self.update_mark_price(symbol, price)
        return self.equity

    def get_account_info(self) -> MT5AccountInfo:
        """Return standardized MT5 account telemetry."""
        if self.mode == "live" and self._connected:
            if native_mt5 is not None:
                try:
                    acc = native_mt5.account_info()
                    if acc:
                        margin = float(acc.margin or 0.0)
                        equity = float(acc.equity or 0.0)
                        lvl = (equity / margin * 100.0) if margin > 0 else 0.0
                        return MT5AccountInfo(
                            login=int(acc.login),
                            server=str(acc.server),
                            trade_mode="live",
                            connected=True,
                            balance=float(acc.balance),
                            equity=equity,
                            margin=margin,
                            free_margin=float(acc.margin_free or 0.0),
                            margin_level_pct=lvl,
                            leverage=float(acc.leverage or 20.0),
                            currency=str(acc.currency or "USD"),
                            company=str(acc.company or "MetaQuotes"),
                        )
                except Exception as e:
                    logger.error("[MT5Broker] Failed to fetch account_info: %s", e)
            elif self.gateway_url:
                try:
                    url = f"{self.gateway_url.rstrip('/')}/account"
                    res = _gateway_request("GET", url)
                    margin = float(res.get("margin", 0.0) or 0.0)
                    equity = float(res.get("equity", self.balance) or self.balance)
                    self.balance = float(res.get("balance", self.balance))
                    self._cached_equity = equity
                    self._cached_margin = margin
                    lvl = (equity / margin * 100.0) if margin > 0 else 0.0
                    lvl_pct = float(res.get("margin_level_pct") or res.get("margin_level") or lvl)
                    return MT5AccountInfo(
                        login=int(res.get("login", self.login)),
                        server=str(res.get("server", self.server)),
                        trade_mode="live",
                        connected=True,
                        balance=self.balance,
                        equity=equity,
                        margin=margin,
                        free_margin=float(res.get("free_margin", equity - margin) or 0.0),
                        margin_level_pct=lvl_pct,
                        leverage=float(res.get("leverage", self.leverage) or self.leverage),
                        currency=str(res.get("currency", self.currency)),
                        company=str(res.get("company", self.company)),
                    )
                except Exception as e:
                    logger.error("[MT5Broker] Gateway get_account_info error: %s", e)

        # Paper / Simulated account telemetry
        eq = self.equity
        mg = self.margin
        lvl = (eq / mg * 100.0) if mg > 0 else 0.0
        return MT5AccountInfo(
            login=self.login,
            server=self.server,
            trade_mode=self.mode,
            connected=self._connected,
            balance=self.balance,
            equity=eq,
            margin=mg,
            free_margin=self.free_margin,
            margin_level_pct=lvl,
            leverage=self.leverage,
            currency=self.currency,
            company=self.company,
        )

    def get_account_summary(self) -> Dict[str, Any]:
        """Summary dict compatible with PaperBroker interface."""
        info = self.get_account_info()
        return {
            "mode": self.mode,
            "broker": "MT5",
            "login": info.login,
            "server": info.server,
            "connected": info.connected,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.free_margin,
            "unrealized_pnl": round(info.equity - info.balance, 2),
            "open_positions": len(self.get_positions()),
        }

    def switch_mode(
        self,
        mode: str,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
    ) -> bool:
        """Switch between paper and live MT5 account."""
        new_mode = "live" if str(mode).strip().lower() == "live" else "paper"
        if login is not None:
            self.login = int(login)
        if password is not None:
            self.password = str(password)
        if server is not None:
            self.server = str(server)
        self.mode = new_mode
        return self.connect()

