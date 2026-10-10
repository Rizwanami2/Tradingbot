#!/usr/bin/env python3
"""
Execution Layer – Paper + Live (CCXT)
=====================================
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
import json
import uuid
from pathlib import Path

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
    def place_order(self, symbol: str, side: str, qty: float, 
                    order_type: str = "market", price: float = None,
                    stop_loss: float = None, take_profit: float = None) -> Order:
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        pass

    @abstractmethod
    def close_position(self, symbol: str, qty: float = None) -> bool:
        pass

class PaperBroker(Broker):
    def __init__(self, initial_balance: float = 10000.0, commission_pct: float = 0.0004):
        self.balance = initial_balance
        self.equity = initial_balance
        self.commission_pct = commission_pct
        self.positions: Dict[str, Position] = {}
        self.orders: List[Order] = []
        self.journal_file = Path("trades_journal.jsonl")

    def place_order(self, symbol: str, side: str, qty: float,
                    order_type: str = "market", price: float = None,
                    stop_loss: float = None, take_profit: float = None) -> Order:
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
                symbol=symbol, side=side, qty=qty,
                entry_price=order.filled_price,
                stop_loss=stop_loss, take_profit=take_profit
            )

        self.orders.append(order)
        self._log(order)
        return order

    def get_positions(self) -> List[Position]:
        return list(self.positions.values())

    def close_position(self, symbol: str, qty: float = None, price: float = None) -> bool:
        if symbol not in self.positions:
            return False
        pos = self.positions[symbol]
        close_qty = qty or pos.qty
        exit_price = price or pos.entry_price
        side = "sell" if pos.side == "buy" else "buy"

        # Calculate realized PnL
        pnl = (exit_price - pos.entry_price) * close_qty if pos.side == "buy" else (pos.entry_price - exit_price) * close_qty
        self.balance += pnl
        self.equity = self.balance

        self.place_order(symbol, side, close_qty, price=exit_price)
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


def create_broker(
    broker_mode: str = "paper",
    exchange_id: str = "binance",
    api_key: str = "",
    api_secret: str = "",
    initial_balance: float = 10000.0,
) -> Broker:
    """Factory creating PaperBroker or live CCXTBroker based on configuration."""
    if broker_mode.lower() == "live" and api_key and api_secret:
        try:
            return CCXTBroker(exchange_id=exchange_id, api_key=api_key, api_secret=api_secret)
        except Exception as e:
            print(f"[Broker] Warning: Could not initialize live {exchange_id} broker ({e}) -> falling back to PaperBroker")
    return PaperBroker(initial_balance=initial_balance)


class CCXTBroker(Broker):
    def __init__(self, exchange_id: str = "binance", api_key: str = "", api_secret: str = ""):
        import ccxt
        exchange_class = getattr(ccxt, exchange_id)
        self.exchange = exchange_class({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })

    def place_order(self, symbol: str, side: str, qty: float,
                    order_type: str = "market", price: float = None,
                    stop_loss: float = None, take_profit: float = None) -> Order:
        # Normalize symbol
        if "/" not in symbol:
            symbol = symbol.replace("USD", "/USDT") if symbol.endswith("USD") else f"{symbol}/USDT"

        params = {}
        if stop_loss:
            params["stopLoss"] = {"triggerPrice": stop_loss}
        if take_profit:
            params["takeProfit"] = {"triggerPrice": take_profit}

        try:
            if order_type == "market":
                raw = self.exchange.create_order(symbol, "market", side, qty, params=params)
            else:
                raw = self.exchange.create_order(symbol, "limit", side, qty, price, params=params)

            return Order(
                id=raw["id"],
                symbol=symbol,
                side=side,
                order_type=order_type,
                qty=qty,
                price=price,
                status=raw.get("status", "open"),
                filled_price=raw.get("average") or price,
                filled_qty=raw.get("filled", 0.0),
            )
        except Exception as e:
            print(f"[CCXTBroker] Order failed: {e}")
            raise

    def get_positions(self) -> List[Position]:
        positions = []
        try:
            raw = self.exchange.fetch_positions()
            for p in raw:
                if float(p.get("contracts", 0)) > 0:
                    positions.append(Position(
                        symbol=p["symbol"],
                        side="buy" if p["side"] == "long" else "sell",
                        qty=float(p["contracts"]),
                        entry_price=float(p["entryPrice"]),
                        unrealized_pnl=float(p.get("unrealizedPnl", 0)),
                    ))
        except Exception as e:
            print(f"[CCXTBroker] Fetch positions error: {e}")
        return positions

    def close_position(self, symbol: str, qty: float = None) -> bool:
        positions = self.get_positions()
        for pos in positions:
            if pos.symbol == symbol or pos.symbol.replace("/", "") == symbol:
                side = "sell" if pos.side == "buy" else "buy"
                close_qty = qty or pos.qty
                self.place_order(symbol, side, close_qty, order_type="market")
                return True
        return False
