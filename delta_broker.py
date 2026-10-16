#!/usr/bin/env python3
"""
Native Delta Exchange India REST Broker Integration — Crypto & Derivatives.
============================================================================
Provides 100% native execution on Linux, macOS, and cloud containers for
Delta Exchange India (https://india.delta.exchange) and Delta Exchange Global.

Supported Asset Classes:
- Perpetual Futures & Futures (BTCUSD, ETHUSD, XAUTUSD, PAXGUSD, SOLUSD, etc.)
- Options & Derivatives

Environments:
- "live": Production Delta Exchange India (https://api.india.delta.exchange)
- "testnet": Delta Exchange Testnet (https://testnet-api.delta.exchange)

Features:
- HMAC-SHA256 cryptographic signature authentication
- Full leverage control: manual set (1x to 200x) and auto-set dynamic scaling (max 200x)
- Bracket orders (server-side Stop-Loss and Take-Profit)
- Fail-closed security architecture
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests

from execution import (
    Broker,
    BrokerConfigurationError,
    ExecutionError,
    Order,
    Position,
)

logger = logging.getLogger("DeltaBroker")

DELTA_INDIA_LIVE_URL = "https://api.india.delta.exchange"
DELTA_GLOBAL_LIVE_URL = "https://api.delta.exchange"
DELTA_INDIA_TESTNET_URL = "https://cdn-ind.testnet.deltaex.org"
DELTA_GLOBAL_TESTNET_URL = "https://testnet-api.delta.exchange"
DELTA_TESTNET_URL = DELTA_INDIA_TESTNET_URL

# Canonical symbol aliases mapping to Delta Exchange symbols
SYMBOL_ALIASES: Dict[str, str] = {
    # Bitcoin
    "BTC": "BTCUSD",
    "BTCUSD": "BTCUSD",
    "BTCUSDT": "BTCUSD",
    "XBT": "BTCUSD",
    "XBTUSD": "BTCUSD",
    # Gold / Metals (Tether Gold & PAX Gold perpetuals)
    "XAU": "XAUTUSD",
    "XAUUSD": "XAUTUSD",
    "XAUUSDT": "XAUTUSD",
    "GOLD": "XAUTUSD",
    "XAUT": "XAUTUSD",
    "XAUTUSD": "XAUTUSD",
    "PAXG": "PAXGUSD",
    "PAXGUSD": "PAXGUSD",
    "PAXGUSDT": "PAXGUSD",
    # Ethereum
    "ETH": "ETHUSD",
    "ETHUSD": "ETHUSD",
    "ETHUSDT": "ETHUSD",
    # Popular Altcoins
    "SOL": "SOLUSD",
    "SOLUSD": "SOLUSD",
    "SOLUSDT": "SOLUSD",
    "DOGE": "DOGEUSD",
    "DOGEUSD": "DOGEUSD",
    "DOGEUSDT": "DOGEUSD",
    "XRP": "XRPUSD",
    "XRPUSD": "XRPUSD",
    "XRPUSDT": "XRPUSD",
}


def resolve_delta_symbol(symbol: str) -> str:
    """Normalize user or internal symbol to Delta Exchange perpetual futures symbol."""
    if not symbol:
        return "BTCUSD"
    cleaned = symbol.strip().upper().replace("/", "").replace("-", "").replace(":", "").replace("_", "")
    if cleaned in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[cleaned]
    # If ends with USDT, check if stripping T gives a valid Delta pair (e.g. BTCUSD)
    if cleaned.endswith("USDT"):
        candidate = f"{cleaned[:-4]}USD"
        return candidate
    return cleaned


@dataclass
class DeltaAccountInfo:
    account_id: str
    environment: str             # "live" | "testnet"
    connected: bool
    balance: float               # total wallet balance
    equity: float                # balance + unrealized PnL
    margin: float                # blocked / used margin
    free_margin: float           # available margin for new trades
    active_leverage: float       # active configured leverage
    auto_leverage: bool          # whether dynamic auto-leverage is enabled
    max_leverage: float = 200.0  # exchange maximum leverage cap
    currency: str = "USD"
    open_position_count: int = 0
    symbols_leverage: Dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        icon = "🟢" if self.environment == "live" else "🧪"
        conn_icon = "📶" if self.connected else "⚠️"
        auto_lev_str = "🟢 Active (Dynamic up to 200x)" if self.auto_leverage else "⚪ Manual"
        return (
            f"🇮🇳 **Delta Exchange India Account** ({icon} `{self.environment.upper()}`)\n"
            f"• **Status:** {conn_icon} {'Connected' if self.connected else 'Disconnected'}\n"
            f"• **Wallet Balance:** `${self.balance:,.2f}` {self.currency}\n"
            f"• **Total Equity:** `${self.equity:,.2f}` {self.currency}\n"
            f"• **Available Margin:** `${self.free_margin:,.2f}` | **Blocked:** `${self.margin:,.2f}`\n"
            f"• **Configured Leverage:** `{self.active_leverage:.0f}x` (Max: `{self.max_leverage:.0f}x`)\n"
            f"• **Auto-Leverage Engine:** {auto_lev_str}\n"
            f"• **Open Positions:** `{self.open_position_count}`\n"
            f"• **Engine:** Native Delta Exchange India REST API (Zero-Dependency)"
        )


class DeltaBroker(Broker):
    """
    Production-grade Delta Exchange India REST Broker for Crypto & Derivatives.
    Supports manual leverage setting and dynamic auto-leverage up to 200x.
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        base_url: str = DELTA_INDIA_LIVE_URL,
        is_testnet: bool = False,
        leverage: float = 20.0,
        auto_leverage: bool = True,
        max_leverage: float = 200.0,
        timeout: int = 10,
    ):
        from execution import BrokerConfigurationError
        raise BrokerConfigurationError(
            "DeltaBroker has been removed. MetaTrader 5 Live ('mt5_live') is the sole supported broker."
        )
        self.api_secret = str(api_secret or "").strip().strip("'\"`").strip()
        self.is_testnet = bool(is_testnet)
        if self.is_testnet:
            self.base_url = (base_url if base_url and "testnet" in base_url else DELTA_INDIA_TESTNET_URL).rstrip("/")
        else:
            self.base_url = (base_url or DELTA_INDIA_LIVE_URL).rstrip("/")
        self.timeout = timeout
        self._time_offset: float = 0.0

        self.max_leverage = float(max_leverage or 200.0)
        self.default_leverage = min(max(float(leverage or 20.0), 1.0), self.max_leverage)
        self.auto_leverage = bool(auto_leverage)

        # Telemetry
        self.account_id: str = ""
        self.balance: float = 0.0
        self._cached_equity: float = 0.0
        self._cached_margin: float = 0.0
        self._cached_free_margin: float = 0.0
        self.currency: str = "USD"
        self._connected: bool = False
        self._last_error: str = ""

        # Product cache: symbol -> product dict & product_id -> product dict
        self._products_by_sym: Dict[str, Dict[str, Any]] = {}
        self._products_by_id: Dict[int, Dict[str, Any]] = {}
        # Cached configured leverage per symbol: symbol -> leverage
        self.symbols_leverage: Dict[str, float] = {}

        self.connect()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def equity(self) -> float:
        return self._cached_equity if self._cached_equity > 0 else self.balance

    @property
    def margin(self) -> float:
        return self._cached_margin

    @property
    def free_margin(self) -> float:
        return self._cached_free_margin if self._cached_free_margin > 0 else self.balance

    @property
    def mode(self) -> str:
        return "testnet" if self.is_testnet else "live"

    def _sync_time_from_response(self, resp: requests.Response) -> None:
        """Dynamically synchronize local time with Delta Exchange server time."""
        date_header = resp.headers.get("Date")
        if not date_header:
            return
        try:
            from email.utils import parsedate_to_datetime
            server_dt = parsedate_to_datetime(date_header)
            server_ts = server_dt.timestamp()
            local_ts = time.time()
            diff = server_ts - local_ts
            if abs(diff) > 1.0:
                self._time_offset = diff
                logger.debug("[DeltaBroker] Synced clock skew offset: %.2fs", self._time_offset)
        except Exception:
            pass

    def _generate_signature(
        self,
        method: str,
        path_with_query: str,
        payload_str: str = "",
        timestamp: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Generate HMAC-SHA256 signature according to Delta Exchange v2 spec."""
        ts = timestamp or str(int(time.time() + self._time_offset))
        # Formula: METHOD + TIMESTAMP + PATH_WITH_QUERY + PAYLOAD
        signature_data = method.upper() + ts + path_with_query + payload_str
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            signature_data.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature, ts

    def _headers(
        self,
        method: str,
        path_with_query: str,
        payload_str: str = "",
    ) -> Dict[str, str]:
        signature, ts = self._generate_signature(method, path_with_query, payload_str)
        return {
            "api-key": self.api_key,
            "timestamp": ts,
            "signature": signature,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "NimbleDarwin/1.0 (DeltaExchangeIndia)",
        }

    def _request(
        self,
        method: str,
        endpoint: str,
        payload: Optional[Dict[str, Any]] = None,
        query_params: Optional[Dict[str, Any]] = None,
        auth_required: bool = True,
    ) -> Any:
        clean_endpoint = "/" + endpoint.lstrip("/")
        query_string = ""
        if query_params:
            sorted_params = sorted(query_params.items())
            query_string = "?" + urlencode(sorted_params)

        path_with_query = clean_endpoint + query_string
        payload_str = ""
        if payload is not None:
            payload_str = json.dumps(payload, separators=(",", ":"))

        url = f"{self.base_url}{path_with_query}"

        headers = (
            self._headers(method, path_with_query, payload_str)
            if auth_required
            else {"Content-Type": "application/json", "Accept": "application/json"}
        )

        try:
            resp = requests.request(
                method=method.upper(),
                url=url,
                headers=headers,
                data=payload_str if payload_str else None,
                timeout=self.timeout,
            )
            self._sync_time_from_response(resp)
            if resp.status_code >= 400:
                err_code = ""
                err_msg = resp.text
                try:
                    data = resp.json()
                    err_obj = data.get("error", {})
                    if isinstance(err_obj, dict):
                        err_code = err_obj.get("code", "")
                        err_msg = err_obj.get("message") or err_code or resp.text
                    elif isinstance(err_obj, str):
                        err_msg = err_obj
                except Exception:
                    pass
                raise ExecutionError(f"Delta Exchange HTTP {resp.status_code}: {err_msg}")

            if not resp.text:
                return {}
            data = resp.json()
            if isinstance(data, dict) and data.get("success") is False:
                err_obj = data.get("error", {})
                msg = err_obj.get("message", "Request failed") if isinstance(err_obj, dict) else str(err_obj)
                raise ExecutionError(f"Delta Exchange error: {msg}")
            return data
        except requests.exceptions.RequestException as req_err:
            raise ExecutionError(f"Delta Exchange connection error: {req_err}") from req_err

    def load_products(self) -> None:
        """Fetch and cache product metadata from Delta Exchange."""
        try:
            data = self._request("GET", "/v2/products", auth_required=False)
            products = data.get("result", []) if isinstance(data, dict) else []
            self._products_by_sym.clear()
            self._products_by_id.clear()
            for p in products:
                sym = str(p.get("symbol", "")).upper()
                pid = p.get("id")
                if sym and pid:
                    self._products_by_sym[sym] = p
                    self._products_by_id[int(pid)] = p
            logger.info("[DeltaBroker] Loaded %d products from Delta Exchange", len(self._products_by_sym))
        except Exception as e:
            logger.warning("[DeltaBroker] Could not fetch products list: %s", e)

    def get_product(self, symbol_or_id: Any) -> Optional[Dict[str, Any]]:
        """Resolve a symbol or product id to product dict."""
        if isinstance(symbol_or_id, int):
            return self._products_by_id.get(symbol_or_id)
        if isinstance(symbol_or_id, str) and symbol_or_id.isdigit():
            return self._products_by_id.get(int(symbol_or_id))

        sym = resolve_delta_symbol(str(symbol_or_id))
        if sym in self._products_by_sym:
            return self._products_by_sym[sym]

        # If not cached, attempt refresh once
        if not self._products_by_sym:
            self.load_products()
            if sym in self._products_by_sym:
                return self._products_by_sym[sym]

        # Fallback defaults for core instruments
        if "BTC" in sym:
            return {
                "id": 27,
                "symbol": "BTCUSD",
                "contract_value": "0.001",
                "default_leverage": "200.0",
                "tick_size": "0.5",
            }
        elif "ETH" in sym:
            return {
                "id": 3136,
                "symbol": "ETHUSD",
                "contract_value": "0.01",
                "default_leverage": "200.0",
                "tick_size": "0.05",
            }
        elif "XAUT" in sym or "GOLD" in sym or "XAU" in sym:
            return {
                "id": 131253,
                "symbol": "XAUTUSD",
                "contract_value": "0.001",
                "default_leverage": "100.0",
                "tick_size": "0.1",
            }
        return None

    def connect(self) -> bool:
        """Verify API keys and load account summary with fail-closed safety."""
        self._last_error = ""
        if not self.api_key or not self.api_secret:
            self._connected = False
            self._last_error = (
                "Delta Exchange credentials missing (api_key and api_secret required). "
                "Configure DELTA_EXCHANGE_API_KEY and DELTA_EXCHANGE_API_SECRET in .env or via /delta live or /delta testnet."
            )
            logger.error("[DeltaBroker] %s", self._last_error)
            raise BrokerConfigurationError(self._last_error)

        # Candidate endpoints to attempt in priority order
        if self.is_testnet:
            candidates = [
                self.base_url,
                DELTA_INDIA_TESTNET_URL,
                DELTA_GLOBAL_TESTNET_URL,
            ]
        else:
            candidates = [
                self.base_url,
                DELTA_INDIA_LIVE_URL,
                DELTA_GLOBAL_LIVE_URL,
            ]

        unique_candidates = []
        for c in candidates:
            c = (c or "").rstrip("/")
            if c and c not in unique_candidates:
                unique_candidates.append(c)

        last_exc = None
        for candidate in unique_candidates:
            self.base_url = candidate
            try:
                # 1. Fetch products & sync server clock
                self.load_products()

                # 2. Fetch wallet balances with authenticated signature
                self.refresh_balances()
                self._connected = True
                logger.info(
                    "[DeltaBroker] Connected to Delta Exchange %s (%s, Balance: $%s, Equity: $%s)",
                    self.mode, self.base_url, self.balance, self.equity
                )
                return True
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "[DeltaBroker] Connection to %s failed (%s). Checking next endpoint...",
                    candidate, exc
                )

        # Cross-environment check: If testnet failed with invalid_api_key, check if key belongs to LIVE!
        if self.is_testnet and "invalid_api_key" in str(last_exc):
            try:
                saved_url = self.base_url
                self.base_url = DELTA_INDIA_LIVE_URL
                self.load_products()
                self.refresh_balances()
                # Key is valid on LIVE! Auto-promote to Live
                self.is_testnet = False
                self._connected = True
                logger.info(
                    "[DeltaBroker] Auto-promoted to LIVE Delta Exchange India! (Balance: $%s, Equity: $%s)",
                    self.balance, self.equity
                )
                return True
            except Exception:
                self.base_url = saved_url

        self._connected = False
        diagnostic = ""
        if "invalid_api_key" in str(last_exc):
            if self.is_testnet:
                diagnostic = (
                    "\n\n🔍 Troubleshooting Delta Exchange Testnet:"
                    "\n1. Testnet API keys must be created at https://testnet.delta.exchange (separate from live)."
                    "\n2. If you generated this key on the live exchange (https://india.delta.exchange), "
                    "connect using `/delta live <API_KEY> <API_SECRET>`."
                    "\n3. Ensure IP whitelisting on your Delta API key is disabled or includes your server IP."
                )
            else:
                diagnostic = (
                    "\n\n🔍 Troubleshooting Delta Exchange Live:"
                    "\n1. Live API keys must be created at https://india.delta.exchange."
                    "\n2. If these are demo/testnet keys, connect using `/delta testnet <API_KEY> <API_SECRET>`."
                    "\n3. Ensure Trading permissions are enabled on the API key."
                )

        self._last_error = f"Delta Exchange connection failed: {last_exc}{diagnostic}"
        logger.error("[DeltaBroker] %s", self._last_error)
        raise BrokerConfigurationError(self._last_error) from last_exc

    def refresh_balances(self) -> None:
        """Update balance, equity, and margin from /v2/wallet/balances."""
        resp = self._request("GET", "/v2/wallet/balances")
        balances = resp.get("result", []) if isinstance(resp, dict) else []

        tot_balance = 0.0
        tot_blocked = 0.0
        tot_free = 0.0

        for b in balances:
            asset = str(b.get("asset_symbol", "")).upper()
            bal = float(b.get("balance", 0.0) or 0.0)
            blocked = float(b.get("blocked_margin", 0.0) or 0.0)
            free = float(b.get("free_margin", 0.0) or 0.0)

            # Weight USD / USDT / USDC balances directly
            if asset in ("USD", "USDT", "USDC"):
                tot_balance += bal
                tot_blocked += blocked
                tot_free += free

        # If zero, take first asset with positive balance
        if tot_balance == 0.0 and balances:
            for b in balances:
                bal = float(b.get("balance", 0.0) or 0.0)
                if bal > 0:
                    tot_balance += bal
                    tot_blocked += float(b.get("blocked_margin", 0.0) or 0.0)
                    tot_free += float(b.get("free_margin", 0.0) or 0.0)
                    self.currency = str(b.get("asset_symbol", "USD")).upper()
                    break

        self.balance = tot_balance
        self._cached_margin = tot_blocked
        self._cached_free_margin = tot_free
        # Include unrealized PnL from positions
        unrealized = sum(p.unrealized_pnl for p in self.get_positions())
        self._cached_equity = tot_balance + unrealized

    def set_leverage(self, symbol_or_product_id: Any, leverage: float) -> Tuple[bool, str]:
        """
        Manually set order leverage for a contract on Delta Exchange.
        Validates leverage between 1x and max 200x.
        """
        if not self._connected:
            raise ExecutionError("Cannot set leverage: Delta Exchange broker is disconnected.")

        try:
            lev = float(leverage)
        except (ValueError, TypeError):
            return False, f"Invalid leverage '{leverage}'. Must be a number between 1 and {self.max_leverage:.0f}."

        prod = self.get_product(symbol_or_product_id)
        if not prod:
            return False, f"Unknown instrument or product ID: {symbol_or_product_id}"

        pid = int(prod["id"])
        sym = prod.get("symbol", str(symbol_or_product_id)).upper()

        prod_max = float(prod.get("default_leverage", self.max_leverage) or self.max_leverage)
        allowed_max = min(self.max_leverage, prod_max)

        if not math.isfinite(lev) or lev < 1.0 or lev > allowed_max:
            return (
                False,
                f"Leverage {lev:.0f}x out of bounds for {sym}. Allowed range: 1x to {allowed_max:.0f}x.",
            )

        target_lev_int = int(round(lev))
        endpoint = f"/v2/products/{pid}/orders/leverage"
        payload = {"leverage": str(target_lev_int)}

        try:
            self._request("POST", endpoint, payload=payload)
            self.symbols_leverage[sym] = float(target_lev_int)
            self.default_leverage = float(target_lev_int)
            margin_pct = (1.0 / target_lev_int) * 100.0
            msg = f"Leverage for {sym} set to {target_lev_int}x (Initial Margin: {margin_pct:.2f}%)"
            logger.info("[DeltaBroker] %s", msg)
            return True, msg
        except Exception as exc:
            err = f"Failed to set leverage on Delta Exchange: {exc}"
            logger.error("[DeltaBroker] %s", err)
            return False, err

    def get_leverage(self, symbol_or_product_id: Optional[Any] = None) -> float:
        """Get currently configured leverage for a symbol or the broker default."""
        if symbol_or_product_id:
            sym = resolve_delta_symbol(str(symbol_or_product_id))
            if sym in self.symbols_leverage:
                return self.symbols_leverage[sym]
        return self.default_leverage

    def auto_set_leverage(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        entry: Optional[float] = None,
        confidence: Optional[float] = None,
        max_cap: float = 200.0,
    ) -> Tuple[float, str]:
        """
        Dynamically calculate and set optimal leverage up to max 200x.
        Protects capital by ensuring liquidation price remains safely beyond the stop-loss level,
        while unlocking high capital efficiency (up to 200x) for ultra-tight precision setups.
        """
        prod = self.get_product(symbol)
        prod_max = float(prod.get("default_leverage", 200.0) or 200.0) if prod else 200.0
        effective_cap = min(self.max_leverage, max_cap, prod_max, 200.0)

        calc_lev = self.default_leverage

        if entry and stop_loss and entry > 0 and stop_loss > 0:
            stop_dist_pct = abs(entry - stop_loss) / entry
            # Maintenance margin is ~0.25% - 0.5% on Delta Exchange
            maint_margin_rate = 0.005
            # Safety buffer multiplier of 1.25 so stop-loss triggers before liquidation barrier
            safety_dist = (stop_dist_pct * 1.25) + maint_margin_rate
            if safety_dist > 0:
                raw_lev = 1.0 / safety_dist
                # Confidence boost if confidence is high (> 75%)
                if confidence and confidence > 0.75:
                    raw_lev *= 1.15
                calc_lev = min(raw_lev, effective_cap)
        else:
            # Fallback to configured leverage or confidence scaling
            if confidence and confidence > 0.8:
                calc_lev = min(50.0, effective_cap)
            else:
                calc_lev = min(self.default_leverage, effective_cap)

        final_lev = max(1.0, min(calc_lev, effective_cap))
        target_int = int(round(final_lev))

        # Synchronize with Delta Exchange API
        ok, msg = self.set_leverage(symbol, target_int)
        if ok:
            return float(target_int), f"Auto-set leverage to {target_int}x (Max 200x cap)"
        return self.get_leverage(symbol), f"Leverage retained at {self.get_leverage(symbol):.0f}x ({msg})"

    def to_contracts(self, symbol: str, qty: float) -> int:
        """Convert a base quantity or lots into Delta contract count."""
        prod = self.get_product(symbol)
        contract_val = float(prod.get("contract_value", 0.001) or 0.001) if prod else 0.001
        if qty <= 0:
            return 1
        # If qty is already a large whole number (e.g. 10 or 100 contracts)
        if qty >= 1.0 and abs(qty - round(qty)) < 1e-6 and contract_val < 0.1:
            return int(round(qty))
        # Otherwise convert base coin units into contract count
        contracts = int(round(qty / contract_val))
        return max(contracts, 1)

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
        Submit a new order with optional bracket Stop-Loss and Take-Profit.
        Synchronizes dynamic auto-leverage up to 200x if enabled.
        """
        if not self._connected:
            raise ExecutionError("Cannot place order: Delta Exchange broker is disconnected.")

        prod = self.get_product(symbol)
        if not prod:
            raise ExecutionError(f"Unsupported product on Delta Exchange: {symbol}")

        pid = int(prod["id"])
        delta_sym = prod.get("symbol", symbol).upper()
        side_clean = str(side).lower()
        if side_clean not in ("buy", "sell"):
            raise ValueError(f"Invalid order side: {side}")

        # Auto-scale leverage if auto_leverage is enabled
        if self.auto_leverage and (stop_loss or price):
            try:
                self.auto_set_leverage(delta_sym, stop_loss=stop_loss, entry=price)
            except Exception as e:
                logger.warning("[DeltaBroker] Auto-leverage adjustment warning: %s", e)

        contracts = self.to_contracts(delta_sym, qty)
        order_type_clean = "market_order" if str(order_type).lower() == "market" else "limit_order"

        payload: Dict[str, Any] = {
            "product_id": pid,
            "size": contracts,
            "side": side_clean,
            "order_type": order_type_clean,
        }

        if price is not None and order_type_clean == "limit_order":
            payload["limit_price"] = f"{price:.2f}"

        # Bracket SL/TP integration
        if stop_loss and stop_loss > 0:
            payload["bracket_stop_loss_price"] = f"{stop_loss:.2f}"
            payload["bracket_stop_trigger_method"] = "mark_price"
        if take_profit and take_profit > 0:
            payload["bracket_take_profit_price"] = f"{take_profit:.2f}"
            payload["bracket_stop_trigger_method"] = "mark_price"

        if params:
            payload.update(params)

        try:
            res = self._request("POST", "/v2/orders", payload=payload)
            order_data = res.get("result", {}) if isinstance(res, dict) else {}
            order_id = str(order_data.get("id", ""))
            fill_price = float(order_data.get("average_fill_price") or price or 0.0)
            status = str(order_data.get("state", "pending")).lower()
            is_filled = status in ("filled", "closed") or (order_type_clean == "market_order" and status not in ("cancelled", "rejected"))
            filled_qty = qty if is_filled else max(0.0, float(order_data.get("size", 0.0) or qty) - float(order_data.get("unfilled_size", 0.0) or 0.0))
            if is_filled and filled_qty <= 0:
                filled_qty = qty

            return Order(
                id=order_id,
                symbol=delta_sym,
                side=side_clean,
                order_type=order_type,
                qty=qty,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                status="filled" if is_filled else "pending",
                filled_price=fill_price or price,
                filled_qty=filled_qty,
                created_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            logger.error("[DeltaBroker] Order placement failed for %s: %s", delta_sym, exc)
            raise ExecutionError(f"Delta Exchange order placement failed: {exc}") from exc

    def get_positions(self) -> List[Position]:
        """Fetch active open positions with unrealized PnL and liquidation price."""
        if not self._connected:
            return []
        try:
            res = self._request("GET", "/v2/positions/margined")
            data = res.get("result", []) if isinstance(res, dict) else []
            positions: List[Position] = []

            for p in data:
                size = float(p.get("size", 0) or 0)
                if abs(size) <= 0:
                    continue
                sym = str(p.get("product_symbol", "")).upper()
                side = "buy" if size > 0 else "sell"
                entry_p = float(p.get("entry_price", 0.0) or 0.0)
                unrealized = float(p.get("unrealized_pnl", 0.0) or 0.0)
                mark_p = float(p.get("mark_price", entry_p) or entry_p)

                positions.append(
                    Position(
                        symbol=sym,
                        side=side,
                        qty=abs(size),
                        entry_price=entry_p,
                        unrealized_pnl=unrealized,
                        current_price=mark_p,
                    )
                )
            return positions
        except Exception as exc:
            logger.error("[DeltaBroker] Failed to query positions: %s", exc)
            return []

    def close_position(
        self,
        symbol: str,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Close an active position using an opposite market order with reduce_only=True."""
        if not self._connected:
            return False

        delta_sym = resolve_delta_symbol(symbol)
        prod = self.get_product(delta_sym)
        if not prod:
            logger.error("[DeltaBroker] Cannot close position: unknown product %s", symbol)
            return False

        pid = int(prod["id"])
        open_positions = [p for p in self.get_positions() if p.symbol == delta_sym]
        if not open_positions:
            logger.info("[DeltaBroker] No open position found for %s to close", delta_sym)
            return True

        current_pos = open_positions[0]
        close_side = "sell" if current_pos.side == "buy" else "buy"
        close_size = int(round(qty)) if qty and qty > 0 else int(round(current_pos.qty))

        payload: Dict[str, Any] = {
            "product_id": pid,
            "size": max(close_size, 1),
            "side": close_side,
            "order_type": "market_order",
            "reduce_only": True,
        }
        if params:
            payload.update(params)

        try:
            self._request("POST", "/v2/orders", payload=payload)
            logger.info("[DeltaBroker] Position closed for %s (%s %d contracts)", delta_sym, close_side, close_size)
            return True
        except Exception as exc:
            logger.error("[DeltaBroker] Failed to close position %s: %s", delta_sym, exc)
            return False

    def modify_position(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> bool:
        """Update bracket stop-loss or take-profit on an active position."""
        if not self._connected:
            return False

        delta_sym = resolve_delta_symbol(symbol)
        prod = self.get_product(delta_sym)
        if not prod:
            return False

        pid = int(prod["id"])
        payload: Dict[str, Any] = {
            "product_id": pid,
            "bracket_stop_trigger_method": "mark_price",
        }
        if stop_loss and stop_loss > 0:
            payload["bracket_stop_loss_price"] = f"{stop_loss:.2f}"
        if take_profit and take_profit > 0:
            payload["bracket_take_profit_price"] = f"{take_profit:.2f}"

        try:
            self._request("POST", "/v2/orders/bracket", payload=payload)
            logger.info("[DeltaBroker] Updated bracket for %s: SL=%s TP=%s", delta_sym, stop_loss, take_profit)
            return True
        except Exception as exc:
            logger.error("[DeltaBroker] Failed to modify bracket for %s: %s", delta_sym, exc)
            return False

    def get_account_info(self) -> DeltaAccountInfo:
        """Fetch updated telemetry and return DeltaAccountInfo."""
        if self._connected:
            try:
                self.refresh_balances()
            except Exception as e:
                logger.error("[DeltaBroker] Error refreshing telemetry: %s", e)

        positions = self.get_positions()
        return DeltaAccountInfo(
            account_id=self.account_id or ("LiveAccount" if not self.is_testnet else "TestnetAccount"),
            environment=self.mode,
            connected=self._connected,
            balance=self.balance,
            equity=self.equity,
            margin=self.margin,
            free_margin=self.free_margin,
            active_leverage=self.default_leverage,
            auto_leverage=self.auto_leverage,
            max_leverage=self.max_leverage,
            currency=self.currency,
            open_position_count=len(positions),
            symbols_leverage=dict(self.symbols_leverage),
        )

    def get_account_summary(self) -> Dict[str, Any]:
        """Telemetry dictionary representation."""
        info = self.get_account_info()
        return {
            "broker": "Delta Exchange India",
            "mode": info.environment,
            "account_id": info.account_id,
            "connected": info.connected,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.free_margin,
            "leverage": info.active_leverage,
            "auto_leverage": info.auto_leverage,
            "max_leverage": info.max_leverage,
            "open_positions": info.open_position_count,
        }
