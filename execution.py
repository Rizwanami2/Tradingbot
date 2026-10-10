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
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, runtime_checkable
import json
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
    def __init__(self, initial_balance: float = 10000.0, commission_pct: float = 0.0004):
        self.balance = initial_balance
        self.equity = initial_balance
        self.commission_pct = commission_pct
        self.positions: Dict[str, Position] = {}
        self.orders: List[Order] = []
        self.journal_file = Path("trades_journal.jsonl")

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
        if side not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'.")
        if not isinstance(qty, (int, float)) or qty <= 0:
            raise ValueError("qty must be greater than zero.")
        if order_type not in {"market", "limit"}:
            raise ValueError("order_type must be 'market' or 'limit'.")
        if order_type == "limit" and (price is None or price <= 0):
            raise ValueError("A positive price is required for limit orders.")
        order = Order(
            id=str(uuid.uuid4())[:8],
            symbol=symbol,
            side=side,
            order_type=order_type,
            qty=qty,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status="filled",
            filled_price=price or 0.0,  # caller should pass current price
            filled_qty=qty,
        )
        # Apply commission
        cost = order.filled_price * qty * self.commission_pct
        self.balance -= cost

        # Update or create position
        key = symbol
        if key in self.positions:
            pos = self.positions[key]
            if pos.side == side:
                # Average in
                total_qty = pos.qty + qty
                pos.entry_price = (pos.entry_price * pos.qty + order.filled_price * qty) / total_qty
                pos.qty = total_qty
            else:
                # Reduce / close
                if qty >= pos.qty:
                    del self.positions[key]
                else:
                    pos.qty -= qty
        else:
            self.positions[key] = Position(
                symbol=symbol,
                side=side,
                qty=qty,
                entry_price=order.filled_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

        self.orders.append(order)
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
        if symbol not in self.positions:
            return False
        pos = self.positions[symbol]
        close_qty = pos.qty if qty is None else qty
        if not isinstance(close_qty, (int, float)) or close_qty <= 0:
            raise ValueError("close qty must be greater than zero.")
        if close_qty > pos.qty:
            raise ValueError("close qty cannot exceed the open position quantity.")
        exit_price = pos.entry_price if price is None else price
        if not isinstance(exit_price, (int, float)) or exit_price <= 0:
            raise ValueError("exit price must be greater than zero.")
        side = "sell" if pos.side == "buy" else "buy"

        # Calculate realized PnL
        pnl = (exit_price - pos.entry_price) * close_qty if pos.side == "buy" else (pos.entry_price - exit_price) * close_qty
        self.balance += pnl
        self.equity = self.balance

        self.place_order(symbol, side, close_qty, price=exit_price, params=params)
        return True

    def _log(self, order: Order):
        try:
            with open(self.journal_file, "a") as f:
                f.write(json.dumps({
                    "id": order.id, "symbol": order.symbol, "side": order.side,
                    "qty": order.qty, "price": order.filled_price,
                    "time": order.created_at.isoformat()
                }) + "\n")
        except Exception:
            pass


# ======================================================================
# EXECUTION SAFETY HELPERS
# ======================================================================
def create_broker_fail_closed(
    broker_mode: str,
    api_key: Optional[str],
    api_secret: Optional[str],
    exchange_id: str,
    *,
    paper_factory,
    live_factory,
):
    """
    Create the requested broker without silently falling back between modes.
    If live mode is requested, missing credentials or initialization failure raises
    BrokerConfigurationError instead of returning a paper broker.
    """
    mode = (broker_mode or "").strip().lower()

    if mode == "paper":
        return paper_factory()

    if mode != "live":
        raise BrokerConfigurationError(
            f"Unsupported broker mode {broker_mode!r}; expected 'paper' or 'live'."
        )

    if not api_key or not api_secret:
        raise BrokerConfigurationError(
            "Live mode requested, but API credentials are missing. "
            "No broker was created."
        )

    try:
        broker = live_factory(exchange_id, api_key, api_secret)
    except Exception as exc:
        raise BrokerConfigurationError(
            f"Live broker initialization failed for {exchange_id!r}; "
            "refusing to fall back to paper mode."
        ) from exc

    if broker is None:
        raise BrokerConfigurationError(
            "Live broker factory returned None; refusing to continue."
        )

    return broker


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
        # A canceled order may still have executed part of its quantity.
        status = (
            ExecutionStatus.PARTIALLY_FILLED
            if filled_qty > 0
            else ExecutionStatus.REJECTED
        )
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
    broker_mode: str = "paper",
    exchange_id: str = "binance",
    api_key: str = "",
    api_secret: str = "",
    initial_balance: float = 10000.0,
) -> Broker:
    """Factory creating PaperBroker or live CCXTBroker with fail-closed safety."""
    return create_broker_fail_closed(
        broker_mode=broker_mode,
        api_key=api_key,
        api_secret=api_secret,
        exchange_id=exchange_id,
        paper_factory=lambda: PaperBroker(initial_balance=initial_balance),
        live_factory=lambda eid, k, s: CCXTBroker(exchange_id=eid, api_key=k, api_secret=s),
    )


class CCXTBroker(Broker):
    def __init__(self, exchange_id: str = "binance", api_key: str = "", api_secret: str = ""):
        import ccxt
        exchange_class = getattr(ccxt, exchange_id, None)
        if exchange_class is None:
            raise BrokerConfigurationError(f"Unsupported exchange '{exchange_id}'.")
        self.exchange_id = exchange_id
        self.exchange = exchange_class({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        self._markets_loaded = False

    def ensure_markets(self):
        if not self._markets_loaded:
            try:
                self.exchange.load_markets()
                self._markets_loaded = True
            except Exception as e:
                raise BrokerConfigurationError(f"Failed to load exchange markets: {e}") from e

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
        if side not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'.")
        if not isinstance(qty, (int, float)) or qty <= 0:
            raise ValueError("qty must be greater than zero.")
        if order_type not in {"market", "limit"}:
            raise ValueError("order_type must be 'market' or 'limit'.")
        if order_type == "limit" and (price is None or price <= 0):
            raise ValueError("A positive price is required for limit orders.")

        # Always load market metadata before validating or submitting an order.
        self.ensure_markets()
        resolved_symbol = resolve_ccxt_symbol(self.exchange, symbol)

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
        raw = fetch_positions_or_raise(self.exchange)
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
                close_qty = pos.qty if qty is None else qty
                if not isinstance(close_qty, (int, float)) or close_qty <= 0:
                    raise ValueError("close qty must be greater than zero.")
                if close_qty > pos.qty:
                    raise ValueError("close qty cannot exceed the open position quantity.")

                # Do not submit a close order unless reduce-only is explicitly confirmed.
                close_params = dict(params or {})
                close_params.update(close_order_params(self.exchange))
                self.place_order(
                    pos.symbol,
                    side,
                    close_qty,
                    order_type="market",
                    price=price,
                    params=close_params,
                )
                return True
        return False
