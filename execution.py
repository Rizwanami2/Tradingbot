#!/usr/bin/env python3
"""
Execution Layer – Paper + Live (CCXT)
=====================================
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional
import json
import os
import uuid


class ExecutionError(RuntimeError):
    """Base exception for execution safety errors."""


class BrokerConfigurationError(ExecutionError):
    """Broker configuration is invalid or unsafe."""


class UnknownSymbolError(ExecutionError):
    """Symbol is not an exact, supported unified exchange market symbol."""


class OrderStateUnknownError(ExecutionError):
    """Exchange response does not confirm the final order state."""


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    SUBMITTING = "submitting"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(frozen=True)
class OrderResult:
    order_id: Optional[str]
    status: ExecutionStatus
    symbol: str
    side: str
    requested_qty: float
    filled_qty: float
    average_price: Optional[float]
    raw: Mapping[str, Any]


@dataclass
class Order:
    id: str
    symbol: str
    side: str          # "buy" | "sell"
    order_type: str    # "market" | "limit"
    qty: float
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    status: str = "pending"
    filled_price: Optional[float] = None
    filled_qty: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Position:
    symbol: str
    side: str
    qty: float
    entry_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    unrealized_pnl: float = 0.0
    current_price: Optional[float] = None
    ticket: Optional[Any] = None
    id: Optional[str] = None
    trailing_stop: Optional[float] = None


class Broker(ABC):
    @abstractmethod
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
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        pass

    @abstractmethod
    def close_position(
        self,
        symbol: str,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        pass

class PaperBroker(Broker):
    """Deterministic paper broker with fee-aware accounting and optional durable state.

    Orders are simulated at the supplied price; this class does not fetch quotes or
    submit exchange orders. Persistence is opt-in through state_file.
    """

    def __init__(
        self,
        initial_balance: float = 10000.0,
        commission_pct: float = 0.0004,
        *,
        state_file: Optional[str] = None,
        journal_file: Optional[str] = "trades_journal.jsonl",
    ):
        raise BrokerConfigurationError(
            "PaperBroker has been removed. MetaTrader 5 Live ('mt5_live') is the sole supported broker."
        )

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
        import math

        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        symbol = symbol.strip()
        side = str(side).lower()
        if side not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'")
        if order_type not in {"market", "limit"}:
            raise ValueError("order_type must be 'market' or 'limit'")
        if price is None or not math.isfinite(float(price)) or float(price) <= 0:
            raise ValueError("Paper orders require a finite, positive fill price")
        if not math.isfinite(float(qty)) or float(qty) <= 0:
            raise ValueError("qty must be finite and greater than zero")
        qty, fill_price = float(qty), float(price)

        current = self.positions.get(symbol)
        if current and current.side != side and qty > current.qty + 1e-12:
            raise ValueError("Opposite-side order exceeds open quantity; position flips are disabled")

        order = Order(
            id=str(uuid.uuid4()),
            symbol=symbol,
            side=side,
            order_type=order_type,
            qty=qty,
            price=fill_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status="filled",
            filled_price=fill_price,
            filled_qty=qty,
        )
        fee = fill_price * qty * self.commission_pct
        self.balance -= fee

        if current is None:
            self.positions[symbol] = Position(
                symbol=symbol, side=side, qty=qty, entry_price=fill_price,
                stop_loss=stop_loss, take_profit=take_profit,
            )
            self.mark_prices[symbol] = fill_price
        elif current.side == side:
            new_qty = current.qty + qty
            current.entry_price = ((current.entry_price * current.qty) + (fill_price * qty)) / new_qty
            current.qty = new_qty
            self.mark_prices[symbol] = fill_price
            if stop_loss is not None:
                current.stop_loss = stop_loss
            if take_profit is not None:
                current.take_profit = take_profit
        else:
            closed_qty = qty
            gross_pnl = (
                (fill_price - current.entry_price) * closed_qty
                if current.side == "buy"
                else (current.entry_price - fill_price) * closed_qty
            )
            self.balance += gross_pnl
            remaining = current.qty - closed_qty
            if remaining <= 1e-12:
                del self.positions[symbol]
                self.mark_prices.pop(symbol, None)
            else:
                current.qty = remaining
                self.mark_prices[symbol] = fill_price

        self.orders.append(order)
        self._refresh_equity()
        self._persist_state()
        self._log(order)
        return order

    def get_positions(self) -> List[Position]:
        return list(self.positions.values())

    def close_position(
        self,
        symbol: str,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        position = self.positions.get(symbol)
        if position is None:
            return False
        close_qty = position.qty if qty is None else float(qty)
        if close_qty <= 0 or close_qty > position.qty + 1e-12:
            return False
        exit_price = price if price is not None else self.mark_prices.get(symbol, position.entry_price)
        if exit_price is None or exit_price <= 0:
            return False
        closing_side = "sell" if position.side == "buy" else "buy"
        self.place_order(symbol, closing_side, close_qty, price=exit_price, params=params)
        return True

    def mark_price(self, symbol: str, price: float) -> float:
        """Update mark-to-market equity without realizing P&L."""
        import math

        if not math.isfinite(float(price)) or float(price) <= 0:
            raise ValueError("mark price must be finite and greater than zero")
        if symbol not in self.positions:
            raise KeyError(f"No open paper position for {symbol}")
        self.mark_prices[symbol] = float(price)
        self._refresh_equity()
        self._persist_state()
        return self.equity

    def get_account_summary(self) -> Dict[str, Any]:
        self._refresh_equity()
        unrealized = self.equity - self.balance
        return {
            "balance": self.balance,
            "equity": self.equity,
            "unrealized_pnl": unrealized,
            "realized_pnl_net_fees": self.balance - self._initial_balance,
            "open_positions": len(self.positions),
            "open_quantity_by_symbol": {k: v.qty for k, v in self.positions.items()},
        }

    def _refresh_equity(self) -> None:
        unrealized = 0.0
        for symbol, pos in self.positions.items():
            mark = self.mark_prices.get(symbol, pos.entry_price)
            pos.current_price = mark
            unrealized += (
                (mark - pos.entry_price) * pos.qty
                if pos.side == "buy"
                else (pos.entry_price - mark) * pos.qty
            )
            pos.unrealized_pnl = (
                (mark - pos.entry_price) * pos.qty
                if pos.side == "buy"
                else (pos.entry_price - mark) * pos.qty
            )
        self.equity = self.balance + unrealized

    def _persist_state(self) -> None:
        if self.state_file is None:
            return
        import os
        payload = {
            "version": 1,
            "initial_balance": self._initial_balance,
            "balance": self.balance,
            "equity": self.equity,
            "commission_pct": self.commission_pct,
            "positions": {
                symbol: {
                    "symbol": p.symbol, "side": p.side, "qty": p.qty,
                    "entry_price": p.entry_price, "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit, "unrealized_pnl": p.unrealized_pnl,
                    "mark_price": self.mark_prices.get(symbol, p.entry_price),
                }
                for symbol, p in self.positions.items()
            },
            "orders": [
                {
                    "id": o.id, "symbol": o.symbol, "side": o.side,
                    "order_type": o.order_type, "qty": o.qty, "price": o.price,
                    "stop_loss": o.stop_loss, "take_profit": o.take_profit,
                    "status": o.status, "filled_price": o.filled_price,
                    "filled_qty": o.filled_qty, "created_at": o.created_at.isoformat(),
                } for o in self.orders
            ],
        }
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, allow_nan=False)
            handle.flush()
        os.replace(temp, self.state_file)

    def _load_state(self, initial_balance: float) -> None:
        try:
            with open(self.state_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if payload.get("version") != 1:
                raise ValueError("Unsupported paper state version")
            self._initial_balance = float(payload.get("initial_balance", initial_balance))
            self.balance = float(payload["balance"])
            self.equity = float(payload.get("equity", self.balance))
            self.commission_pct = float(payload.get("commission_pct", self.commission_pct))
            for symbol, raw in payload.get("positions", {}).items():
                self.positions[symbol] = Position(
                    symbol=raw["symbol"], side=raw["side"], qty=float(raw["qty"]),
                    entry_price=float(raw["entry_price"]), stop_loss=raw.get("stop_loss"),
                    take_profit=raw.get("take_profit"),
                    unrealized_pnl=float(raw.get("unrealized_pnl", 0.0)),
                )
                self.mark_prices[symbol] = float(raw.get("mark_price", raw["entry_price"]))
            for raw in payload.get("orders", []):
                self.orders.append(Order(
                    id=raw["id"], symbol=raw["symbol"], side=raw["side"],
                    order_type=raw["order_type"], qty=float(raw["qty"]), price=raw.get("price"),
                    stop_loss=raw.get("stop_loss"), take_profit=raw.get("take_profit"),
                    status=raw.get("status", "filled"), filled_price=raw.get("filled_price"),
                    filled_qty=float(raw.get("filled_qty", 0.0)),
                    created_at=datetime.fromisoformat(raw["created_at"]),
                ))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not safely load paper broker state: {exc}") from exc

    def _log(self, order: Order) -> None:
        if self.journal_file is None:
            return
        try:
            with open(self.journal_file, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "id": order.id, "symbol": order.symbol, "side": order.side,
                    "qty": order.qty, "price": order.filled_price,
                    "time": order.created_at.isoformat(),
                }) + "\n")
        except OSError:
            # The durable state file is the source of truth; journal is supplementary.
            pass


# ======================================================================
# EXECUTION SAFETY HELPERS
# ======================================================================
def create_broker_fail_closed(
    broker_mode: str,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
    exchange_id: str = "mt5",
    initial_balance: Optional[float] = None,
    *,
    paper_factory=None,
    live_factory=None,
    mt5_paper_factory=None,
    mt5_live_factory=None,
    oanda_factory=None,
    alpaca_factory=None,
    delta_factory=None,
):
    """
    Create the requested broker without silently falling back between modes.
    Exclusively supports MetaTrader 5 Live ('mt5_live') and Delta Exchange ('delta_live').
    All paper trading and legacy brokers have been removed.
    """
    mode = (broker_mode or "").strip().lower()

    if mode in ("paper", "mt5_paper", "mt5_demo", "sim", "demo"):
        raise BrokerConfigurationError(
            f"Broker mode {broker_mode!r} is not supported. Paper trading has been removed; "
            "MetaTrader 5 Live ('mt5_live') is the sole supported execution broker."
        )

    if mode in (
        "oanda", "oanda_practice", "oanda_live",
        "alpaca", "alpaca_paper", "alpaca_live",
        "ccxt", "ccxt_live", "live",
    ):
        raise BrokerConfigurationError(
            f"Broker mode {broker_mode!r} is not supported. Legacy broker has been removed; "
            "MetaTrader 5 Live ('mt5_live') and Delta Exchange ('delta_live') are supported."
        )

    if mode in ("delta", "delta_live", "delta_india", "delta_india_live", "delta_testnet"):
        if delta_factory is not None:
            return delta_factory()
        from delta_broker import DeltaBroker
        try:
            import config as _cfg
        except Exception:
            _cfg = None
        key = api_key or getattr(_cfg, "DELTA_EXCHANGE_API_KEY", "") or getattr(_cfg, "EXCHANGE_API_KEY", "") or os.getenv("DELTA_EXCHANGE_API_KEY") or os.getenv("EXCHANGE_API_KEY") or ""
        sec = api_secret or getattr(_cfg, "DELTA_EXCHANGE_API_SECRET", "") or getattr(_cfg, "EXCHANGE_API_SECRET", "") or os.getenv("DELTA_EXCHANGE_API_SECRET") or os.getenv("EXCHANGE_API_SECRET") or ""
        testnet = mode == "delta_testnet" or getattr(_cfg, "DELTA_EXCHANGE_TESTNET", False) or (os.getenv("DELTA_EXCHANGE_TESTNET", "false").lower() in ("true", "1", "yes", "on"))
        base_url = getattr(_cfg, "DELTA_EXCHANGE_BASE_URL", "") or os.getenv("DELTA_EXCHANGE_BASE_URL", "https://api.india.delta.exchange")
        lev = float(getattr(_cfg, "DELTA_EXCHANGE_DEFAULT_LEVERAGE", 20.0) or os.getenv("DELTA_EXCHANGE_DEFAULT_LEVERAGE", "20.0"))
        auto_lev = bool(getattr(_cfg, "DELTA_EXCHANGE_AUTO_LEVERAGE", True) if getattr(_cfg, "DELTA_EXCHANGE_AUTO_LEVERAGE", None) is not None else (os.getenv("DELTA_EXCHANGE_AUTO_LEVERAGE", "true").lower() in ("true", "1", "yes", "on")))
        max_lev = float(getattr(_cfg, "DELTA_EXCHANGE_MAX_LEVERAGE", 200.0) or os.getenv("DELTA_EXCHANGE_MAX_LEVERAGE", "200.0"))
        return DeltaBroker(
            api_key=key,
            api_secret=sec,
            base_url=base_url,
            is_testnet=testnet,
            leverage=lev,
            auto_leverage=auto_lev,
            max_leverage=max_lev,
        )

    if mode in ("mt5", "mt5_live"):
        if mt5_live_factory is not None:
            return mt5_live_factory()
        try:
            import config
            login = getattr(config, "MT5_LOGIN", 0) or 0
            password = getattr(config, "MT5_PASSWORD", "") or api_secret or ""
            server = getattr(config, "MT5_SERVER", "") or exchange_id or ""
            backend = getattr(config, "MT5_BACKEND", "auto")
            if str(backend).strip().lower() not in ("ea_bridge", "bridge", "ea"):
                if not login or not password or not server:
                    raise BrokerConfigurationError(
                        "MT5 Live mode requested, but credentials (MT5_LOGIN, MT5_PASSWORD, MT5_SERVER) are missing."
                    )
            from mt5_broker import MT5Broker
            return MT5Broker(
                mode="live",
                login=login,
                password=password,
                server=server,
                path=getattr(config, "MT5_PATH", ""),
                magic=getattr(config, "MT5_MAGIC", 112233),
                gateway_url=getattr(config, "MT5_GATEWAY_URL", ""),
                backend=backend,
                initial_balance=initial_balance,
            )
        except Exception as exc:
            if isinstance(exc, BrokerConfigurationError):
                raise
            raise BrokerConfigurationError(f"MT5 Live broker initialization failed: {exc}") from exc

    raise BrokerConfigurationError(
        f"Unsupported broker mode {broker_mode!r}; MetaTrader 5 Live ('mt5_live') and Delta Exchange ('delta_live') are supported."
    )


def resolve_ccxt_symbol(exchange: Any, symbol: str) -> str:
    """
    Validate and resolve an exact CCXT unified market symbol against loaded markets.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise UnknownSymbolError("A non-empty unified symbol is required.")

    requested = symbol.strip()
    markets = getattr(exchange, "markets", None)
    if not isinstance(markets, dict) or not markets:
        raise BrokerConfigurationError(
            "Exchange markets are not loaded. Call exchange.load_markets() first."
        )

    if requested not in markets:
        candidate = requested
        if "/" not in candidate:
            if candidate.endswith("USDT"):
                candidate = f"{candidate[:-4]}/USDT"
            elif candidate.endswith("USD"):
                candidate = f"{candidate[:-3]}/USDT"
        if candidate in markets:
            requested = candidate
        else:
            examples = ", ".join(list(markets.keys())[:8])
            raise UnknownSymbolError(
                f"{requested!r} is not an exact market symbol on this exchange. "
                f"Example available symbols: {examples}"
            )

    market = markets[requested]
    if market.get("active") is False:
        raise UnknownSymbolError(f"Market {requested!r} is marked inactive.")

    return requested


def classify_order_response(
    raw: Mapping[str, Any],
    *,
    symbol: str,
    side: str,
    requested_qty: float,
) -> OrderResult:
    """
    Convert a CCXT-like order response into a conservative execution status.
    Never treats a missing or unknown status as filled.
    """
    if requested_qty <= 0:
        raise ValueError("requested_qty must be greater than zero.")
    if side.lower() not in {"buy", "sell"}:
        raise ValueError("side must be 'buy' or 'sell'.")

    raw_status = str(raw.get("status") or "").lower()
    filled_value = raw.get("filled")
    try:
        filled_qty = max(0.0, float(filled_value or 0.0))
    except (TypeError, ValueError):
        filled_qty = 0.0

    try:
        average_price = (
            float(raw["average"])
            if raw.get("average") is not None
            else None
        )
    except (TypeError, ValueError):
        average_price = None

    if raw_status in {"closed", "filled"}:
        status = (
            ExecutionStatus.FILLED
            if filled_qty >= requested_qty
            else ExecutionStatus.PARTIALLY_FILLED
            if filled_qty > 0
            else ExecutionStatus.UNKNOWN
        )
    elif raw_status in {"canceled", "cancelled", "rejected", "expired"}:
        status = ExecutionStatus.REJECTED
    elif raw_status in {"open", "new"}:
        status = (
            ExecutionStatus.PARTIALLY_FILLED
            if filled_qty > 0
            else ExecutionStatus.PENDING
        )
    else:
        status = ExecutionStatus.UNKNOWN

    return OrderResult(
        order_id=str(raw["id"]) if raw.get("id") is not None else None,
        status=status,
        symbol=symbol,
        side=side.lower(),
        requested_qty=float(requested_qty),
        filled_qty=filled_qty,
        average_price=average_price,
        raw=raw,
    )


def fetch_positions_or_raise(exchange: Any, *args, **kwargs) -> List[Any]:
    """
    Fetch positions without hiding API errors.
    An empty list means the exchange reported no open positions;
    None or exceptions indicate an uncertain state and raise OrderStateUnknownError.
    """
    try:
        positions = exchange.fetch_positions(*args, **kwargs)
    except Exception as e:
        raise OrderStateUnknownError(f"Exchange fetch_positions failed: {e}") from e

    if positions is None:
        raise OrderStateUnknownError(
            "Exchange returned None for positions; position state is unknown."
        )
    return list(positions)


def close_order_params(exchange: Any) -> Dict[str, Any]:
    """
    Return reduce-only parameters if supported by the exchange, failing closed if unverifiable.
    """
    features = getattr(exchange, "features", None)
    supports_reduce_only = False

    if isinstance(features, dict):
        spot = features.get("spot", {})
        future = features.get("future", {})
        swap = features.get("swap", {})
        for section in (spot, future, swap):
            if isinstance(section, dict):
                create_order = section.get("createOrder", {})
                if isinstance(create_order, dict) and create_order.get("reduceOnly") is True:
                    supports_reduce_only = True

    if not supports_reduce_only:
        raise BrokerConfigurationError(
            "Reduce-only support could not be confirmed from exchange features."
        )

    return {"reduceOnly": True}


def require_confirmed_fill(result: OrderResult) -> OrderResult:
    """
    Guard code that must only proceed after a fully confirmed fill.
    """
    if result.status != ExecutionStatus.FILLED:
        raise OrderStateUnknownError(
            f"Order {result.order_id or '<unknown id>'} is {result.status.value}; "
            "reconcile with the exchange before changing position state."
        )
    if result.filled_qty <= 0 or result.average_price is None:
        raise OrderStateUnknownError(
            "Order is marked filled but lacks confirmed quantity or average price."
        )
    return result


def create_broker(
    broker_mode: str = "mt5_live",
    exchange_id: str = "mt5",
    api_key: str = "",
    api_secret: str = "",
    api_password: str = "",
    is_testnet: bool = False,
    initial_balance: float = 10000.0,
    mt5_mode: str = "live",
    mt5_login: int = 0,
    mt5_password: str = "",
    mt5_server: str = "MetaQuotes-Demo",
    mt5_path: str = "",
    mt5_magic: int = 112233,
    mt5_gateway_url: str = "",
    oanda_account_id: str = "",
    oanda_api_token: str = "",
    oanda_env: str = "practice",
    alpaca_api_key: str = "",
    alpaca_api_secret: str = "",
    alpaca_paper: bool = True,
    delta_api_key: str = "",
    delta_api_secret: str = "",
    delta_base_url: str = "",
    delta_testnet: bool = False,
    delta_leverage: float = 20.0,
    delta_auto_leverage: bool = True,
    delta_max_leverage: float = 200.0,
    **kwargs,
) -> Broker:
    """Factory creating exclusive MT5 Live broker with fail-closed safety."""
    from mt5_broker import MT5Broker

    try:
        import config as _cfg
    except Exception:
        _cfg = None

    effective_mode = (broker_mode or "").strip().lower()
    if effective_mode == "mt5":
        effective_mode = "mt5_live"

    def _make_delta():
        from delta_broker import DeltaBroker
        k = delta_api_key or api_key or getattr(_cfg, "DELTA_EXCHANGE_API_KEY", "") or getattr(_cfg, "EXCHANGE_API_KEY", "")
        s = delta_api_secret or api_secret or getattr(_cfg, "DELTA_EXCHANGE_API_SECRET", "") or getattr(_cfg, "EXCHANGE_API_SECRET", "")
        t = delta_testnet or (effective_mode == "delta_testnet") or getattr(_cfg, "DELTA_EXCHANGE_TESTNET", False)
        u = delta_base_url or getattr(_cfg, "DELTA_EXCHANGE_BASE_URL", "https://api.india.delta.exchange")
        lev = float(delta_leverage or getattr(_cfg, "DELTA_EXCHANGE_DEFAULT_LEVERAGE", 20.0))
        auto_lev = bool(delta_auto_leverage if delta_auto_leverage is not None else getattr(_cfg, "DELTA_EXCHANGE_AUTO_LEVERAGE", True))
        max_lev = float(delta_max_leverage or getattr(_cfg, "DELTA_EXCHANGE_MAX_LEVERAGE", 200.0))
        return DeltaBroker(
            api_key=k,
            api_secret=s,
            base_url=u,
            is_testnet=t,
            leverage=lev,
            auto_leverage=auto_lev,
            max_leverage=max_lev,
        )

    return create_broker_fail_closed(
        broker_mode=effective_mode,
        api_key=api_key,
        api_secret=api_secret,
        exchange_id=exchange_id,
        initial_balance=initial_balance,
        mt5_live_factory=lambda: MT5Broker(
            mode="live",
            login=mt5_login,
            password=mt5_password,
            server=mt5_server,
            magic=mt5_magic,
            path=mt5_path,
            gateway_url=mt5_gateway_url,
            backend=getattr(_cfg, "MT5_BACKEND", "auto") if _cfg else "auto",
            initial_balance=initial_balance,
        ),
        delta_factory=_make_delta,
    )


@dataclass
class CCXTAccountInfo:
    exchange_id: str
    connected: bool
    balance: float
    equity: float
    margin: float
    free_margin: float
    currency: str = "USDT"
    open_position_count: int = 0
    is_testnet: bool = False

    def summary(self) -> str:
        icon = "🟢" if self.connected else "⚠️"
        net_icon = "🧪 TESTNET" if self.is_testnet else "⚡ LIVE"
        return (
            f"⚡ **CCXT Exchange Info** ({icon} `{self.exchange_id.upper()}` | `{net_icon}`)\n"
            f"• **Status:** {'Connected' if self.connected else 'Disconnected'}\n"
            f"• **Free Balance:** `${self.free_margin:,.2f}` {self.currency}\n"
            f"• **Total Equity:** `${self.equity:,.2f}` {self.currency}\n"
            f"• **Used Margin:** `${self.margin:,.2f}` {self.currency}\n"
            f"• **Open Positions:** `{self.open_position_count}`\n"
            f"• **Engine:** Native CCXT (100+ Exchanges, Zero Desktop Dependencies)"
        )


class CCXTBroker(Broker):
    def __init__(
        self,
        exchange_id: str = "binance",
        api_key: str = "",
        api_secret: str = "",
        password: str = "",
        is_testnet: bool = False,
    ):
        raise BrokerConfigurationError(
            "CCXTBroker has been removed. MetaTrader 5 Live ('mt5_live') is the sole supported broker."
        )

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def balance(self) -> float:
        return self._cached_balance

    @property
    def equity(self) -> float:
        return self._cached_equity or self._cached_balance

    @property
    def margin(self) -> float:
        return self._cached_margin

    @property
    def free_margin(self) -> float:
        return self._cached_free or self._cached_balance

    @property
    def mode(self) -> str:
        return "testnet" if self.is_testnet else "live"

    def ensure_markets(self):
        if not self._markets_loaded:
            try:
                self.exchange.load_markets()
                self._markets_loaded = True
            except Exception as e:
                raise BrokerConfigurationError(f"Failed to load exchange markets: {e}") from e

    def get_account_info(self) -> CCXTAccountInfo:
        positions = self.get_positions()
        try:
            bal = self.exchange.fetch_balance()
            total = bal.get("total", {})
            free = bal.get("free", {})
            used = bal.get("used", {})

            curr = "USDT" if "USDT" in total else ("USD" if "USD" in total else next(iter(total.keys()), "USD"))
            self.currency = curr
            self._cached_balance = float(total.get(curr, 0.0) or 0.0)
            self._cached_free = float(free.get(curr, 0.0) or 0.0)
            self._cached_margin = float(used.get(curr, 0.0) or 0.0)

            unrealized = sum(p.unrealized_pnl for p in positions)
            self._cached_equity = round(self._cached_balance + unrealized, 2)
            self._connected = True
        except Exception:
            pass

        return CCXTAccountInfo(
            exchange_id=self.exchange_id,
            connected=self._connected,
            balance=self.balance,
            equity=self.equity,
            margin=self.margin,
            free_margin=self.free_margin,
            currency=self.currency,
            open_position_count=len(positions),
            is_testnet=self.is_testnet,
        )

    def get_account_summary(self) -> Dict[str, Any]:
        info = self.get_account_info()
        return {
            "broker": f"CCXT ({self.exchange_id.upper()})",
            "mode": "testnet" if self.is_testnet else "live",
            "exchange": self.exchange_id,
            "connected": info.connected,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.free_margin,
            "open_positions": info.open_position_count,
        }

    def modify_position(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> bool:
        return True

    def update_mark_price(self, symbol: str, price: float) -> None:
        pass

    def mark_price(self, symbol: str, price: float) -> float:
        return self.equity

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
        if getattr(self.exchange, "markets", None):
            resolved_symbol = resolve_ccxt_symbol(self.exchange, symbol)
        else:
            resolved_symbol = symbol

        order_params = dict(params or {})
        if stop_loss:
            order_params["stopLoss"] = {"triggerPrice": stop_loss}
        if take_profit:
            order_params["takeProfit"] = {"triggerPrice": take_profit}

        try:
            if order_type == "market":
                raw = self.exchange.create_order(resolved_symbol, "market", side, qty, params=order_params)
            else:
                raw = self.exchange.create_order(resolved_symbol, "limit", side, qty, price, params=order_params)

            classified = classify_order_response(raw, symbol=resolved_symbol, side=side, requested_qty=qty)

            return Order(
                id=str(raw.get("id", classified.order_id or uuid.uuid4().hex[:8])),
                symbol=resolved_symbol,
                side=side,
                order_type=order_type,
                qty=qty,
                price=price,
                status=classified.status.value,
                filled_price=classified.average_price or raw.get("average") or price,
                filled_qty=classified.filled_qty,
            )
        except Exception as e:
            print(f"[CCXTBroker] Order failed: {e}")
            raise

    def get_positions(self) -> List[Position]:
        try:
            raw = fetch_positions_or_raise(self.exchange)
        except Exception:
            return []
        positions = []
        for p in raw:
            if float(p.get("contracts", 0)) > 0:
                positions.append(Position(
                    symbol=p.get("symbol", ""),
                    side="buy" if p.get("side") == "long" else "sell",
                    qty=float(p.get("contracts", 0)),
                    entry_price=float(p.get("entryPrice", 0.0)),
                    unrealized_pnl=float(p.get("unrealizedPnl", 0.0)),
                ))
        return positions

    def close_position(
        self,
        symbol: str,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        positions = self.get_positions()
        for pos in positions:
            if pos.symbol == symbol or pos.symbol.replace("/", "") == symbol:
                side = "sell" if pos.side == "buy" else "buy"
                close_qty = qty or pos.qty
                close_params = dict(params or {})
                try:
                    close_params.update(close_order_params(self.exchange))
                except BrokerConfigurationError:
                    pass
                self.place_order(symbol, side, close_qty, order_type="market", price=price, params=close_params)
                return True
        return False
