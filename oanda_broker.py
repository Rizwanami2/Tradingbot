#!/usr/bin/env python3
"""
Native OANDA v20 REST Broker Integration — Pure Linux / Cloud Live Trading.
==========================================================================
Provides 100% native execution on Linux, macOS, and cloud containers with
ZERO desktop application, Wine, or MetaTrader requirements.

Supported Asset Classes:
- Forex Majors & Minors (EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, etc.)
- Commodities & Metals (Gold XAU/USD, Silver XAG/USD, Crude Oil BRENT/WTI)
- Global Indices (SPX500, NAS100, US30, UK100, DE30)
- Cryptocurrencies (BTC/USD, ETH/USD)

Environments:
- "practice": Free OANDA fxPractice demo environment (https://api-fxpractice.oanda.com/v3)
- "live": Production OANDA fxTrade live environment (https://api-fxtrade.oanda.com/v3)

Protocol: Pure HTTP/REST v20 via requests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import logging
import math
import requests

from execution import (
    Broker,
    BrokerConfigurationError,
    ExecutionError,
    Order,
    Position,
)

logger = logging.getLogger("OANDABroker")

PRACTICE_BASE_URL = "https://api-fxpractice.oanda.com/v3"
LIVE_BASE_URL = "https://api-fxtrade.oanda.com/v3"

# Common symbol aliases mapping to unified OANDA v20 instruments
OANDA_SYMBOL_MAP: Dict[str, str] = {
    # Metals / Commodities
    "XAUUSD": "XAU_USD",
    "GOLD": "XAU_USD",
    "XAU": "XAU_USD",
    "XAGUSD": "XAG_USD",
    "SILVER": "XAG_USD",
    "WTICOUSD": "WTICO_USD",
    "USOIL": "WTICO_USD",
    "BCOUSD": "BCO_USD",
    "UKOIL": "BCO_USD",
    # Forex Majors & Crosses
    "EURUSD": "EUR_USD",
    "GBPUSD": "GBP_USD",
    "USDJPY": "USD_JPY",
    "AUDUSD": "AUD_USD",
    "USDCAD": "USD_CAD",
    "USDCHF": "USD_CHF",
    "NZDUSD": "NZD_USD",
    "EURGBP": "EUR_GBP",
    "EURJPY": "EUR_JPY",
    "GBPJPY": "GBP_JPY",
    # Crypto
    "BTCUSD": "BTC_USD",
    "BTCUSDT": "BTC_USD",
    "ETHUSD": "ETH_USD",
    "ETHUSDT": "ETH_USD",
    # Indices
    "SPX500": "SPX500_USD",
    "US500": "SPX500_USD",
    "NAS100": "NAS100_USD",
    "US100": "NAS100_USD",
    "US30": "US30_USD",
}


def resolve_oanda_symbol(symbol: str) -> str:
    """Normalize trading symbol to OANDA v20 instrument format (e.g., XAU_USD)."""
    if not symbol or not isinstance(symbol, str):
        return "EUR_USD"
    cleaned = symbol.strip().upper().replace("/", "").replace("-", "")
    if cleaned in OANDA_SYMBOL_MAP:
        return OANDA_SYMBOL_MAP[cleaned]
    # If already has underscore (e.g. EUR_USD), return as is
    if "_" in symbol:
        return symbol.strip().upper()
    # If 6-character forex pair (e.g. EURUSD -> EUR_USD)
    if len(cleaned) == 6 and cleaned.isalpha():
        return f"{cleaned[:3]}_{cleaned[3:]}"
    return cleaned


@dataclass
class OANDAAccountInfo:
    account_id: str
    environment: str         # "practice" | "live"
    connected: bool
    balance: float
    equity: float            # Net Asset Value (NAV)
    margin_used: float
    margin_available: float
    margin_rate: float
    margin_level_pct: float
    unrealized_pnl: float
    currency: str = "USD"
    open_position_count: int = 0

    @property
    def margin_level(self) -> float:
        return self.margin_level_pct

    def summary(self) -> str:
        icon = "🟢" if self.environment == "live" else "⚪"
        conn_icon = "📶" if self.connected else "⚠️"
        return (
            f"🌐 **OANDA v20 Account Info** ({icon} `{self.environment.upper()}`)\n"
            f"• **Status:** {conn_icon} {'Connected' if self.connected else 'Disconnected'}\n"
            f"• **Account ID:** `{self.account_id}`\n"
            f"• **Balance:** `${self.balance:,.2f}` | **NAV / Equity:** `${self.equity:,.2f}`\n"
            f"• **Margin Used:** `${self.margin_used:,.2f}` | **Free Margin:** `${self.margin_available:,.2f}`\n"
            f"• **Margin Level:** `{self.margin_level_pct:.1f}%` | **Margin Rate:** `{self.margin_rate * 100:.1f}%`\n"
            f"• **Unrealized PnL:** `${self.unrealized_pnl:+,.2f}` | **Open Positions:** `{self.open_position_count}`\n"
            f"• **Currency:** `{self.currency}` | **Engine:** Native REST v20 (Linux-Ready)"
        )


class OANDABroker(Broker):
    """
    Production-grade OANDA v20 REST Broker.
    Executes live deals directly through OANDA's institutional v20 REST API.
    """

    def __init__(
        self,
        account_id: str = "",
        api_token: str = "",
        environment: str = "practice",
        timeout: int = 10,
    ):
        self.account_id = str(account_id or "").strip()
        self.api_token = str(api_token or "").strip()
        self.environment = "live" if str(environment).strip().lower() == "live" else "practice"
        self.base_url = LIVE_BASE_URL if self.environment == "live" else PRACTICE_BASE_URL
        self.timeout = timeout

        self.balance: float = 0.0
        self._cached_equity: float = 0.0
        self._cached_margin_used: float = 0.0
        self._cached_margin_avail: float = 0.0
        self._cached_margin_rate: float = 0.02
        self._cached_unrealized_pnl: float = 0.0
        self.currency: str = "USD"
        self._connected: bool = False
        self._last_error: str = ""

        self.connect()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def equity(self) -> float:
        return self._cached_equity or self.balance

    @property
    def margin(self) -> float:
        return self._cached_margin_used

    @property
    def free_margin(self) -> float:
        return self._cached_margin_avail or max(0.0, self.equity - self.margin)

    @property
    def mode(self) -> str:
        return self.environment

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339",
        }

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            resp = requests.request(
                method=method.upper(),
                url=url,
                headers=self._headers(),
                json=payload if payload is not None else None,
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                err_body = resp.text
                try:
                    err_json = resp.json()
                    err_body = err_json.get("errorMessage") or err_json.get("message") or resp.text
                except Exception:
                    pass
                raise ExecutionError(f"OANDA API HTTP {resp.status_code}: {err_body}")
            return resp.json() if resp.text else {}
        except requests.exceptions.RequestException as req_err:
            raise ExecutionError(f"OANDA connection error: {req_err}") from req_err

    def connect(self) -> bool:
        """Validate credentials and fetch initial account state (fail-closed)."""
        self._last_error = ""
        if not self.account_id or not self.api_token:
            self._connected = False
            self._last_error = (
                "OANDA credentials missing (account_id and api_token required). "
                "Configure OANDA_ACCOUNT_ID and OANDA_API_TOKEN in .env or via /oanda live."
            )
            logger.error("[OANDABroker] %s", self._last_error)
            raise BrokerConfigurationError(self._last_error)

        try:
            data = self._request("GET", f"accounts/{self.account_id}/summary")
            acc = data.get("account", {})
            self.balance = float(acc.get("balance", 0.0))
            self._cached_equity = float(acc.get("NAV", self.balance))
            self._cached_margin_used = float(acc.get("marginUsed", 0.0))
            self._cached_margin_avail = float(acc.get("marginAvailable", self.balance))
            self._cached_margin_rate = float(acc.get("marginRate", 0.02))
            self._cached_unrealized_pnl = float(acc.get("unrealizedPL", 0.0))
            self.currency = str(acc.get("currency", "USD"))
            self._connected = True
            logger.info(
                "[OANDABroker] Connected to OANDA %s account %s (NAV: $%s %s)",
                self.environment, self.account_id, self.equity, self.currency
            )
            return True
        except Exception as exc:
            self._connected = False
            self._last_error = f"OANDA connection failed: {exc}"
            logger.error("[OANDABroker] %s", self._last_error)
            raise BrokerConfigurationError(self._last_error) from exc

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
        """Place market or limit order with server-side SL/TP protection."""
        if not self._connected:
            raise ExecutionError("Cannot place order: OANDA broker is disconnected.")

        instrument = resolve_oanda_symbol(symbol)
        side_clean = str(side).lower()
        if side_clean not in ("buy", "sell"):
            raise ValueError(f"Invalid order side: {side}")

        # OANDA units: positive for long/buy, negative for short/sell
        # Round units to integer (standard for Forex/Metals in OANDA v20)
        raw_units = max(1, int(round(qty)))
        units = raw_units if side_clean == "buy" else -raw_units

        order_spec: Dict[str, Any] = {
            "type": "MARKET" if order_type.lower() == "market" else "LIMIT",
            "instrument": instrument,
            "units": str(units),
            "timeInForce": "FOK" if order_type.lower() == "market" else "GTC",
            "positionFill": "DEFAULT",
        }
        if price is not None and order_type.lower() != "market":
            order_spec["price"] = f"{price:.5f}"

        if stop_loss is not None and stop_loss > 0:
            order_spec["stopLossOnFill"] = {
                "price": f"{stop_loss:.5f}",
                "timeInForce": "GTC",
            }
        if take_profit is not None and take_profit > 0:
            order_spec["takeProfitOnFill"] = {
                "price": f"{take_profit:.5f}",
                "timeInForce": "GTC",
            }

        try:
            res = self._request("POST", f"accounts/{self.account_id}/orders", {"order": order_spec})
            fill_tx = res.get("orderFillTransaction", {})
            create_tx = res.get("orderCreateTransaction", {})

            order_id = str(fill_tx.get("id") or create_tx.get("id") or "oanda_unknown")
            fill_price = float(fill_tx.get("price") or price or 0.0)
            filled_qty = abs(float(fill_tx.get("units") or qty))
            status = "filled" if fill_tx else "pending"

            order = Order(
                id=order_id,
                symbol=instrument,
                side=side_clean,
                order_type=order_type,
                qty=filled_qty,
                price=fill_price or price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                status=status,
                filled_price=fill_price,
                filled_qty=filled_qty,
                created_at=datetime.now(timezone.utc),
            )
            # Refresh telemetry in background
            self._update_cached_telemetry(res)
            return order
        except Exception as exc:
            logger.error("[OANDABroker] Order submission failed for %s %s: %s", side_clean, instrument, exc)
            raise ExecutionError(f"OANDA order failed: {exc}") from exc

    def get_positions(self) -> List[Position]:
        """Fetch all active open positions from OANDA."""
        if not self._connected:
            return []
        try:
            data = self._request("GET", f"accounts/{self.account_id}/openPositions")
            positions = []
            for p in data.get("positions", []):
                instr = p.get("instrument", "")
                long_pos = p.get("long", {})
                short_pos = p.get("short", {})

                long_units = float(long_pos.get("units", 0.0))
                short_units = float(short_pos.get("units", 0.0))

                if abs(long_units) > 0:
                    positions.append(Position(
                        symbol=instr,
                        side="buy",
                        qty=abs(long_units),
                        entry_price=float(long_pos.get("averagePrice", 0.0)),
                        unrealized_pnl=float(long_pos.get("unrealizedPL", 0.0)),
                    ))
                if abs(short_units) > 0:
                    positions.append(Position(
                        symbol=instr,
                        side="sell",
                        qty=abs(short_units),
                        entry_price=float(short_pos.get("averagePrice", 0.0)),
                        unrealized_pnl=float(short_pos.get("unrealizedPL", 0.0)),
                    ))
            return positions
        except Exception as exc:
            logger.error("[OANDABroker] Failed to query positions: %s", exc)
            return []

    def close_position(
        self,
        symbol: str,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Close long and/or short position for instrument."""
        if not self._connected:
            return False
        instrument = resolve_oanda_symbol(symbol)
        try:
            close_payload: Dict[str, Any] = {}
            if qty is not None and qty > 0:
                units_str = str(int(round(qty)))
                close_payload["longUnits"] = units_str
                close_payload["shortUnits"] = units_str
            else:
                close_payload["longUnits"] = "ALL"
                close_payload["shortUnits"] = "ALL"

            self._request("PUT", f"accounts/{self.account_id}/positions/{instrument}/close", close_payload)
            logger.info("[OANDABroker] Closed position for %s", instrument)
            return True
        except Exception as exc:
            logger.error("[OANDABroker] Failed to close position %s: %s", instrument, exc)
            return False

    def modify_position(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> bool:
        """Update Stop Loss and Take Profit orders on open position."""
        if not self._connected:
            return False
        instrument = resolve_oanda_symbol(symbol)
        try:
            payload: Dict[str, Any] = {}
            if stop_loss is not None and stop_loss > 0:
                payload["stopLoss"] = {"price": f"{stop_loss:.5f}", "timeInForce": "GTC"}
            if take_profit is not None and take_profit > 0:
                payload["takeProfit"] = {"price": f"{take_profit:.5f}", "timeInForce": "GTC"}
            if not payload:
                return True
            self._request("PUT", f"accounts/{self.account_id}/positions/{instrument}/orders", payload)
            logger.info("[OANDABroker] Modified SL/TP for %s: SL=%s TP=%s", instrument, stop_loss, take_profit)
            return True
        except Exception as exc:
            logger.error("[OANDABroker] Failed to modify %s: %s", instrument, exc)
            return False

    def get_account_info(self) -> OANDAAccountInfo:
        """Query real-time margin, balance, and equity telemetry."""
        if self._connected:
            try:
                data = self._request("GET", f"accounts/{self.account_id}/summary")
                acc = data.get("account", {})
                self.balance = float(acc.get("balance", self.balance))
                self._cached_equity = float(acc.get("NAV", self.equity))
                self._cached_margin_used = float(acc.get("marginUsed", self.margin))
                self._cached_margin_avail = float(acc.get("marginAvailable", self.free_margin))
                self._cached_margin_rate = float(acc.get("marginRate", self._cached_margin_rate))
                self._cached_unrealized_pnl = float(acc.get("unrealizedPL", 0.0))
                pos_count = int(acc.get("openPositionCount", 0))

                lvl = (self.equity / self.margin * 100.0) if self.margin > 0 else 999.0
                return OANDAAccountInfo(
                    account_id=self.account_id,
                    environment=self.environment,
                    connected=True,
                    balance=self.balance,
                    equity=self.equity,
                    margin_used=self.margin,
                    margin_available=self.free_margin,
                    margin_rate=self._cached_margin_rate,
                    margin_level_pct=lvl,
                    unrealized_pnl=self._cached_unrealized_pnl,
                    currency=self.currency,
                    open_position_count=pos_count,
                )
            except Exception as e:
                logger.error("[OANDABroker] Error refreshing account summary: %s", e)

        lvl = (self.equity / self.margin * 100.0) if self.margin > 0 else 999.0
        return OANDAAccountInfo(
            account_id=self.account_id,
            environment=self.environment,
            connected=self._connected,
            balance=self.balance,
            equity=self.equity,
            margin_used=self.margin,
            margin_available=self.free_margin,
            margin_rate=self._cached_margin_rate,
            margin_level_pct=lvl,
            unrealized_pnl=self._cached_unrealized_pnl,
            currency=self.currency,
            open_position_count=len(self.get_positions()),
        )

    def get_account_summary(self) -> Dict[str, Any]:
        info = self.get_account_info()
        return {
            "broker": "OANDA",
            "mode": self.environment,
            "account_id": info.account_id,
            "connected": info.connected,
            "balance": info.balance,
            "equity": info.equity,
            "margin_used": info.margin_used,
            "free_margin": info.margin_available,
            "unrealized_pnl": info.unrealized_pnl,
            "open_positions": info.open_position_count,
        }

    def _update_cached_telemetry(self, res: Dict[str, Any]) -> None:
        """Extract quick account balance update from transaction responses."""
        fill_tx = res.get("orderFillTransaction", {})
        if "accountBalance" in fill_tx:
            try:
                self.balance = float(fill_tx["accountBalance"])
            except (ValueError, TypeError):
                pass
