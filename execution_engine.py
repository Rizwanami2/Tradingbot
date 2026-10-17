"""
Automated Trade Execution Engine & Order Manager
=================================================
Automatically executes trades when multi-agent setup levels are reached.
Features:
- Limit / Trigger setup monitoring (executes when price reaches entry level)
- Leverage System integration (1x to 500x margin management)
- Automatic Progressive Break-Even (+0.5R lock / +1.0R full BE)
- Partial Profit Taking (50% @ TP1, 30% @ TP2, trail remaining 20%)
- Stop loss, liquidation barrier, and trailing stop enforcement
- Position tracking, margin utilization, and real-time event dispatching
"""

import json
import logging
import math
import os
from pathlib import Path
import sys
import time
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from execution import OrderStateUnknownError
from risk_manager import RiskManager

logger = logging.getLogger(__name__)

_DEFAULT_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".runtime",
    "execution_state.json",
)


class OrderStatus(Enum):
    PENDING = "PENDING"      # Setup identified, waiting for price to reach entry level
    OPEN = "OPEN"            # Entry level reached, trade executed and live
    CLOSED = "CLOSED"        # Fully exited (TP / SL / BE)
    CANCELLED = "CANCELLED"  # Setup expired or invalidated


@dataclass
class TradeOrder:
    id: str
    symbol: str
    direction: str  # "LONG" or "SHORT"
    entry_level: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: Optional[float]
    lots: float
    size: float
    leverage: float
    margin_required: float
    liquidation_price: Optional[float]
    risk_amount: float
    rr_ratio: float
    setup_reason: str
    status: OrderStatus = OrderStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    executed_at: Optional[str] = None
    closed_at: Optional[str] = None
    executed_price: Optional[float] = None
    current_price: Optional[float] = None
    exit_price: Optional[float] = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    partials_done: int = 0
    remaining_size: float = 0.0
    remaining_lots: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0
    be_stage: int = 0  # 0: initial, 1: +0.1R locked, 2: full BE
    close_reason: str = ""
    is_scalp: bool = False
    fast_market_fill: bool = False
    lot_mode: str = "auto"
    ticket: Optional[Any] = None
    trailing_sl_active: bool = False
    highest_profit_usd: float = 0.0


    def to_dict(self) -> Dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TradeOrder":
        data = dict(d)
        if "status" in data and isinstance(data["status"], str):
            try:
                data["status"] = OrderStatus(data["status"])
            except ValueError:
                data["status"] = OrderStatus.OPEN
        known_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


class PositionDict(dict):
    """
    Intelligent collection for managing active trade positions and pending setups.
    Allows multiple concurrent positions per symbol (Gold and BTC) up to max slots (10).
    Seamlessly supports lookup/indexing by order ID ('ORD-...') or symbol ('XAUUSD', 'BTCUSDT').
    Maintains 100% backward compatibility with legacy dict operations and tests.
    """

    def __contains__(self, key: object) -> bool:
        if super().__contains__(key):
            return True
        key_str = str(key).upper()
        for order in super().values():
            if getattr(order, "symbol", "").upper() == key_str or getattr(order, "id", "") == key:
                return True
        return False

    def __getitem__(self, key: str) -> TradeOrder:
        if super().__contains__(key):
            return super().__getitem__(key)
        key_str = str(key).upper()
        matches = [o for o in super().values() if getattr(o, "symbol", "").upper() == key_str]
        if matches:
            return matches[-1]
        for o in super().values():
            if getattr(o, "id", "") == key:
                return o
        raise KeyError(key)

    def __setitem__(self, key: str, order: TradeOrder) -> None:
        oid = getattr(order, "id", None)
        sym = getattr(order, "symbol", None)
        if not sym and str(key).isalpha():
            try:
                order.symbol = str(key).upper()
            except Exception:
                pass
        store_key = oid if oid else str(key)
        super().__setitem__(store_key, order)

    def __delitem__(self, key: str) -> None:
        if super().__contains__(key):
            super().__delitem__(key)
            return
        key_str = str(key).upper()
        to_del = [k for k, v in super().items() if getattr(v, "symbol", "").upper() == key_str or getattr(v, "id", "") == key]
        if to_del:
            for k in to_del:
                super().__delitem__(k)
            return
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def pop(self, key: str, *args) -> Any:
        try:
            val = self[key]
            del self[key]
            return val
        except KeyError:
            if args:
                return args[0]
            raise

    def get_all(self, symbol: Optional[str] = None) -> List[TradeOrder]:
        """Return all active orders matching symbol, or all orders if symbol is None."""
        if symbol is None:
            return list(super().values())
        sym_u = str(symbol).upper()
        return [o for o in super().values() if getattr(o, "symbol", "").upper() == sym_u]

    def count_for_symbol(self, symbol: str) -> int:
        """Count active orders for a symbol."""
        sym_u = str(symbol).upper()
        return sum(1 for o in super().values() if getattr(o, "symbol", "").upper() == sym_u)


class ExecutionEngine:
    """
    Automated execution engine that monitors setup levels, triggers entries,
    and manages position lifecycles with dynamic leverage.
    """

    def __init__(
        self,
        risk_manager: RiskManager,
        event_callback: Optional[Callable] = None,
        broker: Optional[object] = None,
        max_open_trades: Optional[int] = None,
        state_file: Optional[str] = None,
    ):
        self.risk_manager = risk_manager
        self.event_callback = event_callback
        if broker is not None:
            self.broker = broker
        else:
            try:
                from execution import create_broker
                import config
                broker_mode = getattr(config, "BROKER_MODE", "mt5_live")
                self.broker = create_broker(
                    broker_mode=broker_mode,
                    mt5_mode=getattr(config, "MT5_MODE", "live"),
                    mt5_login=getattr(config, "MT5_LOGIN", 0),
                    mt5_password=getattr(config, "MT5_PASSWORD", ""),
                    mt5_server=getattr(config, "MT5_SERVER", "MetaQuotes-Demo"),
                    mt5_path=getattr(config, "MT5_PATH", ""),
                    mt5_magic=getattr(config, "MT5_MAGIC", 112233),
                    mt5_gateway_url=getattr(config, "MT5_GATEWAY_URL", ""),
                    delta_api_key=getattr(config, "DELTA_EXCHANGE_API_KEY", "") or getattr(config, "EXCHANGE_API_KEY", ""),
                    delta_api_secret=getattr(config, "DELTA_EXCHANGE_API_SECRET", "") or getattr(config, "EXCHANGE_API_SECRET", ""),
                    delta_base_url=getattr(config, "DELTA_EXCHANGE_BASE_URL", "https://api.india.delta.exchange"),
                    delta_testnet=getattr(config, "DELTA_EXCHANGE_TESTNET", False),
                    delta_leverage=getattr(config, "DELTA_EXCHANGE_DEFAULT_LEVERAGE", 20.0),
                    delta_auto_leverage=getattr(config, "DELTA_EXCHANGE_AUTO_LEVERAGE", True),
                    delta_max_leverage=getattr(config, "DELTA_EXCHANGE_MAX_LEVERAGE", 200.0),
                )
            except Exception:
                if "PYTEST_CURRENT_TEST" in os.environ or getattr(config, "TESTING", False):
                    b_m = str(getattr(config, "BROKER_MODE", "")).lower()
                    if "delta" in b_m:
                        from delta_broker import MockDeltaBroker
                        self.broker = MockDeltaBroker()
                    else:
                        from mt5_broker import MockMT5Broker
                        self.broker = MockMT5Broker()
                else:
                    self.broker = None
        self.pending_setups: PositionDict = PositionDict()
        self.active_positions: PositionDict = PositionDict()
        self.trade_history: List[TradeOrder] = []
        self._order_counter: int = 0
        # Prevent duplicate orders after any ambiguous broker response.
        self.execution_halted: bool = False
        if max_open_trades is not None:
            self.max_open_trades = int(max_open_trades)
        else:
            try:
                import config as _cfg_max
                self.max_open_trades = int(getattr(_cfg_max, "MAX_OPEN_TRADES", 2) or 2)
            except Exception:
                self.max_open_trades = 2
        self.scalp_mode: bool = False
        self.fast_market_fill: bool = False
        self.scalp_tp_usd: float = 2.50
        self.scalp_sl_usd: float = 3.00
        self.scalp_tp_pct: float = 0.10
        self.scalp_sl_pct: float = 0.12
        self.scalp_trail_enabled: bool = True
        self.scalp_trail_activation_usd: float = 1.00
        self.scalp_trail_distance_usd: float = 0.80
        self.scalp_trail_activation_pct: float = 0.04
        self.scalp_trail_distance_pct: float = 0.03
        self.scalp_be_lock_usd: float = 0.50
        try:
            import config as _cfg_tr
            self.scalp_trail_enabled = bool(getattr(_cfg_tr, "SCALP_TRAIL_ENABLED", True))
            self.scalp_trail_activation_usd = float(getattr(_cfg_tr, "SCALP_TRAIL_ACTIVATION_USD", 1.00))
            self.scalp_trail_distance_usd = float(getattr(_cfg_tr, "SCALP_TRAIL_DISTANCE_USD", 0.80))
            self.scalp_trail_activation_pct = float(getattr(_cfg_tr, "SCALP_TRAIL_ACTIVATION_PCT", 0.04))
            self.scalp_trail_distance_pct = float(getattr(_cfg_tr, "SCALP_TRAIL_DISTANCE_PCT", 0.03))
            self.scalp_be_lock_usd = float(getattr(_cfg_tr, "SCALP_BE_LOCK_USD", 0.50))
        except Exception:
            pass
        self.state_file: str = state_file or os.getenv("EXECUTION_STATE_FILE") or _DEFAULT_STATE_FILE
        if state_file or (
            "pytest" not in sys.modules
            and not os.getenv("PYTEST_CURRENT_TEST")
        ):
            if os.path.isfile(self.state_file):
                self.restore_state(self.state_file)

    def reset_halt(self) -> None:
        """Clear execution halt state so setups can be registered again."""
        self.execution_halted = False

    def _broker_close(
        self,
        symbol: str,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        ticket: Optional[Any] = None,
    ) -> Any:
        """Safely invoke broker close_position, falling back gracefully if broker does not support ticket keyword."""
        if not getattr(self, "broker", None) or not hasattr(self.broker, "close_position"):
            return True
        kwargs: Dict[str, Any] = {}
        if qty is not None:
            kwargs["qty"] = qty
        if price is not None:
            kwargs["price"] = price
        if ticket is not None:
            kwargs["ticket"] = ticket
        try:
            return self.broker.close_position(symbol, **kwargs)
        except TypeError as ex:
            if "ticket" in str(ex):
                kwargs.pop("ticket", None)
                return self.broker.close_position(symbol, **kwargs)
            raise

    def _broker_modify(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        ticket: Optional[Any] = None,
    ) -> Any:
        """Safely invoke broker modify_position, falling back gracefully if broker does not support ticket keyword."""
        if not getattr(self, "broker", None) or not hasattr(self.broker, "modify_position"):
            return True
        kwargs: Dict[str, Any] = {}
        if stop_loss is not None:
            kwargs["stop_loss"] = stop_loss
        if take_profit is not None:
            kwargs["take_profit"] = take_profit
        if ticket is not None:
            kwargs["ticket"] = ticket
        try:
            return self.broker.modify_position(symbol, **kwargs)
        except TypeError as ex:
            if "ticket" in str(ex):
                kwargs.pop("ticket", None)
                return self.broker.modify_position(symbol, **kwargs)
            raise

    def save_state(self, filepath: Optional[str] = None) -> str:
        """Persist current active positions, pending setups, and execution state to disk."""
        target = Path(filepath or self.state_file or _DEFAULT_STATE_FILE)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "execution_halted": self.execution_halted,
                "order_counter": self._order_counter,
                "active_positions": {k: pos.to_dict() for k, pos in self.active_positions.items()},
                "pending_setups": {k: pos.to_dict() for k, pos in self.pending_setups.items()},
                "trade_history": [pos.to_dict() for pos in self.trade_history[-50:]],
            }
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(target)
            return str(target)
        except Exception as e:
            logger.warning("[ExecutionEngine] Could not save execution state to %s: %s", target, e)
            return str(target)

    def restore_state(self, filepath: Optional[str] = None) -> bool:
        """Restore active positions, pending setups, and execution state from disk."""
        target = Path(filepath or self.state_file or _DEFAULT_STATE_FILE)
        if not target.is_file():
            return False
        try:
            content = json.loads(target.read_text(encoding="utf-8"))
            self.execution_halted = bool(content.get("execution_halted", False))
            self._order_counter = int(content.get("order_counter", 0))

            self.active_positions.clear()
            for k, p_data in content.get("active_positions", {}).items():
                order = TradeOrder.from_dict(p_data)
                self.active_positions[k] = order

            self.pending_setups.clear()
            for k, p_data in content.get("pending_setups", {}).items():
                order = TradeOrder.from_dict(p_data)
                self.pending_setups[k] = order

            self.trade_history = [TradeOrder.from_dict(d) for d in content.get("trade_history", [])]
            logger.info(
                "[ExecutionEngine] Restored state from %s: %d active positions, %d pending setups (halted=%s)",
                target, len(self.active_positions), len(self.pending_setups), self.execution_halted
            )
            return True
        except Exception as e:
            logger.error("[ExecutionEngine] Failed to restore state from %s: %s", target, e)
            return False

    def recover_positions_from_broker(self) -> List[TradeOrder]:
        """
        Query broker for open positions and adopt any that are not currently tracked locally.
        Fails closed by raising OrderStateUnknownError and halting execution if broker query fails.
        """
        if getattr(self, "broker", None) is None:
            return []
        try:
            broker_positions = self.broker.get_positions()
        except Exception as e:
            self.execution_halted = True
            logger.error("[ExecutionEngine] Position query failed during broker recovery: %s", e)
            if isinstance(e, OrderStateUnknownError):
                raise
            raise OrderStateUnknownError(f"Broker position query failed during recovery: {e}") from e

        adopted = []
        for bp in broker_positions:
            norm_sym = bp.symbol.upper()
            existing = [p for p in self.active_positions.values() if p.symbol.upper() == norm_sym]
            if not existing:
                self._order_counter += 1
                direction = "LONG" if bp.side.lower() in ("buy", "long") else "SHORT"
                entry_px = float(bp.entry_price or 0.0)
                sl = float(bp.stop_loss) if bp.stop_loss is not None else (entry_px * 0.98 if direction == "LONG" else entry_px * 1.02)
                tp = float(bp.take_profit) if bp.take_profit is not None else (entry_px * 1.04 if direction == "LONG" else entry_px * 0.96)
                order = TradeOrder(
                    id=f"RECOVERED-{self._order_counter}",
                    symbol=bp.symbol,
                    direction=direction,
                    entry_level=entry_px,
                    stop_loss=sl,
                    tp1=tp,
                    tp2=tp,
                    tp3=None,
                    lots=float(bp.qty),
                    size=float(bp.qty),
                    leverage=getattr(self.risk_manager, "leverage", 1.0) or 1.0,
                    margin_required=0.0,
                    liquidation_price=None,
                    risk_amount=abs(entry_px - sl) * float(bp.qty),
                    rr_ratio=2.0,
                    setup_reason="Broker Recovery",
                    status=OrderStatus.OPEN,
                    executed_price=entry_px,
                    current_price=bp.current_price or entry_px,
                    unrealized_pnl=float(bp.unrealized_pnl or 0.0),
                    remaining_size=float(bp.qty),
                    remaining_lots=float(bp.qty),
                )
                self.active_positions[order.id] = order
                adopted.append(order)
                logger.info("[ExecutionEngine] Adopted broker position: %s %s @ %s", order.direction, order.symbol, entry_px)

        if adopted:
            self.save_state()
        return adopted

    def reconcile_positions(self) -> Dict[str, Any]:
        """
        Reconcile local active positions with the broker's reported positions.
        Fails closed (halts execution and raises OrderStateUnknownError) on query failure.
        """
        if getattr(self, "broker", None) is None:
            return {"status": "skipped", "reason": "no broker"}

        try:
            broker_positions = self.broker.get_positions()
        except Exception as e:
            self.execution_halted = True
            logger.error("[ExecutionEngine] Reconcile positions query failed: %s", e)
            if isinstance(e, OrderStateUnknownError):
                raise
            raise OrderStateUnknownError(f"Reconcile failed: {e}") from e

        broker_symbols = {bp.symbol.upper() for bp in broker_positions}
        closed_locally = []
        for k, local_pos in list(self.active_positions.items()):
            if local_pos.symbol.upper() not in broker_symbols:
                local_pos.status = OrderStatus.CLOSED
                local_pos.closed_at = datetime.now(timezone.utc).isoformat()
                local_pos.close_reason = "Reconciled with broker: closed externally"
                self.trade_history.append(local_pos)
                self.active_positions.pop(k, None)
                closed_locally.append(local_pos)

        adopted = self.recover_positions_from_broker()
        self.save_state()
        return {
            "status": "ok",
            "broker_count": len(broker_positions),
            "local_count": len(self.active_positions),
            "adopted": len(adopted),
            "closed_locally": len(closed_locally),
        }

    def _notify_trade_closed(self, pos: TradeOrder, r_mult: float) -> None:
        won = pos.realized_pnl > 0
        setup = f"{pos.direction.lower()}|{pos.symbol.lower()}"
        now_iso = datetime.now(timezone.utc).isoformat()
        closed_at = getattr(pos, "closed_at", None) or now_iso
        entry_px = float(pos.executed_price or pos.entry_level or 0.0)
        exit_px = float(pos.exit_price or entry_px)

        # 1. Update Autonomous Learner
        try:
            from autonomous_learner import get_brain
            brain = get_brain(auto_bootstrap=False)
            brain.on_trade_closed(
                r_multiple=float(r_mult),
                won=bool(won),
                setup=setup,
                regime="",
                session="",
                agents=[],
                conf=0.6,
            )
        except Exception:
            pass

        # 2. Update Performance Tracker
        try:
            from performance_analytics import PerformanceTracker, TradeRecord
            tracker = PerformanceTracker()
            tracker.record_trade(TradeRecord(
                id=str(pos.id),
                symbol=str(pos.symbol),
                direction=str(pos.direction),
                entry=entry_px,
                exit=exit_px,
                r_multiple=float(r_mult),
                pnl=float(pos.realized_pnl),
                agents=[],
                regime="",
                session="",
                closed_at=closed_at,
            ))
        except Exception:
            pass

        # 3. Update Trade Journal
        try:
            from trade_journal import TradeJournal
            journal = TradeJournal()
            journal.record_close(
                trade_id=str(pos.id),
                exit_price=exit_px,
                r_multiple=float(r_mult),
                pnl=float(pos.realized_pnl),
                auto_learn=False,
            )
        except Exception:
            pass

    _notify_brain_close = _notify_trade_closed


    def open_trade_count(self) -> int:
        return len(self.active_positions) + len(self.pending_setups)

    def slots_available(self) -> int:
        return max(0, int(getattr(self, "max_open_trades", 2)) - self.open_trade_count())

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"ORD-{int(time.time())}-{self._order_counter:03d}"

    def register_setup(self, sig: Dict, reason: str = "") -> TradeOrder:
        """
        Registers a new setup order.
        If current market price is already at/past entry level, it executes immediately.
        Otherwise, it stays in PENDING state until price reaches the entry level.
        """
        if self.execution_halted:
            raise RuntimeError(
                "Execution halted after uncertain broker state; reconcile positions, then run /autotrade on or /risk restart before re-enabling."
            )
        symbol = sig["symbol"].upper()
        import config
        if hasattr(config, "is_trading_allowed") and not config.is_trading_allowed(symbol):
            raise ValueError(f"Trading {symbol} is disabled. Only Gold (XAUUSD) and Bitcoin (BTCUSDT) are permitted.")
        
        is_scalp_req = bool(sig.get("is_scalp") or getattr(self, "scalp_mode", False))
        max_trades = int(getattr(self, "max_open_trades", 2) or 2)

        # Check maximum concurrent open trades limit
        if self.open_trade_count() >= max_trades:
            if not is_scalp_req and symbol in self.pending_setups:
                # In standard mode, re-registering existing pending symbol replaces it without consuming new slot
                pass
            else:
                raise RuntimeError(
                    f"Max concurrent trades reached ({max_trades})."
                )

        if not is_scalp_req and symbol in self.pending_setups:
            try:
                del self.pending_setups[symbol]
            except Exception:
                pass

        direction = sig["direction"].name if hasattr(sig["direction"], "name") else str(sig["direction"]).upper()
        entry = float(sig["entry"])
        stop = float(sig["stop"])
        lots = float(sig.get("lots", 0.01))
        size = float(sig.get("size", 1.0))
        leverage = float(sig.get("leverage") or self.risk_manager.get_leverage())
        margin_required = float(sig.get("margin_required", 0.0))
        liq_price = sig.get("liquidation_price")
        risk_amt = float(sig.get("risk_amount", 0.0))
        rr = float(sig.get("rr", 2.0))
        tp1 = float(sig["tp1"])
        tp2 = float(sig["tp2"])
        tp3 = sig.get("tp3")

        numeric_values = [entry, stop, tp1, tp2, lots, size, leverage, margin_required, risk_amt, rr]
        if not all(math.isfinite(value) for value in numeric_values):
            raise ValueError("Setup contains a non-finite numeric value.")
        if direction not in ("LONG", "SHORT"):
            raise ValueError(f"Unsupported trade direction: {direction!r}")
        if lots <= 0 or size <= 0 or leverage < self.risk_manager.config.min_leverage:
            raise ValueError("Lots, size, and leverage must be positive and within configured limits.")
        if leverage > self.risk_manager.config.max_leverage:
            raise ValueError(f"Requested leverage {leverage:g}x exceeds configured cap {self.risk_manager.config.max_leverage:g}x.")
        if direction == "LONG" and not (stop < entry < tp1 <= tp2):
            raise ValueError("Invalid LONG levels: expected stop < entry < TP1 <= TP2.")
        if direction == "SHORT" and not (stop > entry > tp1 >= tp2):
            raise ValueError("Invalid SHORT levels: expected stop > entry > TP1 >= TP2.")

        order = TradeOrder(
            id=self._next_order_id(),
            symbol=symbol,
            direction=direction,
            entry_level=entry,
            stop_loss=stop,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            lots=lots,
            size=size,
            leverage=leverage,
            margin_required=margin_required,
            liquidation_price=liq_price,
            risk_amount=risk_amt,
            rr_ratio=rr,
            setup_reason=reason or sig.get("reason", "Multi-Agent Consensus"),
            remaining_size=size,
            remaining_lots=lots,
            highest_price=entry,
            lowest_price=entry,
            current_price=float(sig.get("current_price") or entry),
            is_scalp=bool(sig.get("is_scalp") or getattr(self, "scalp_mode", False)),
            fast_market_fill=bool(sig.get("fast_market_fill") or getattr(self, "fast_market_fill", False)),
            lot_mode=str(sig.get("lot_mode") or getattr(self.risk_manager.config, "lot_mode", "auto")),
        )

        if sig.get("current_price"):
            try:
                cp_val = float(sig["current_price"])
                if cp_val > 0 and getattr(self, "broker", None) is not None and hasattr(self.broker, "mark_price"):
                    self.broker.mark_price(symbol, cp_val)
            except Exception:
                pass

        self.pending_setups[order.id] = order
        self._dispatch_event("SETUP_REGISTERED", order, f"Setup registered @ {entry:.2f}. Waiting for trigger.")
        return order

    def cancel_setup(self, symbol: str, reason: str = "User cancelled") -> Optional[TradeOrder]:
        """Manually cancels a pending setup before it executes."""
        sym = str(symbol).strip()
        if sym in self.pending_setups:
            setup = self.pending_setups[sym]
            setup.status = OrderStatus.CANCELLED
            setup.close_reason = reason
            setup.closed_at = datetime.now(timezone.utc).isoformat()
            self.trade_history.append(setup)
            del self.pending_setups[sym]
            msg = f"🚫 **SETUP CANCELLED** ({setup.symbol}): {reason}"
            self._dispatch_event("SETUP_CANCELLED", setup, msg)
            return setup
        return None

    def update_market_candle(self, symbol: str, candle: Dict) -> List[Dict]:
        """
        Evaluates incoming candle against pending setups and active positions.
        candle dict must contain: 'high', 'low', 'close', and optional 'open'.
        Returns list of executed actions/events.
        """
        events = []
        sym = symbol.upper()
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        curr_price = close

        # ----------------------------------------------------
        # 1. Check Pending Setup Execution & Invalidation
        # ----------------------------------------------------
        matching_setups = list(self.pending_setups.get_all(sym))
        for setup in matching_setups:
            if setup.id not in self.pending_setups and setup not in self.pending_setups.values():
                continue
            setup.current_price = curr_price

            # Invalidate setup if market breaches stop loss before entry fill
            invalidated = False
            if setup.direction == "LONG" and curr_price <= setup.stop_loss:
                invalidated = True
            elif setup.direction == "SHORT" and curr_price >= setup.stop_loss:
                invalidated = True

            if invalidated:
                setup.status = OrderStatus.CANCELLED
                setup.close_reason = f"Setup invalidated: market breached stop loss ({setup.stop_loss:.2f}) prior to fill."
                setup.closed_at = datetime.now(timezone.utc).isoformat()
                self.trade_history.append(setup)
                self.pending_setups.pop(setup.id, None)
                ev = {
                    "event": "SETUP_INVALIDATED",
                    "order": setup,
                    "price": curr_price,
                    "message": (
                        f"⚠️ **SETUP INVALIDATED & CANCELLED** ({setup.symbol})\n"
                        f"Direction: {setup.direction} | Planned Entry: `${setup.entry_level:,.2f}`\n"
                        f"Market price (`${curr_price:,.2f}`) breached stop loss (`${setup.stop_loss:,.2f}`) before entry was reached."
                    ),
                }
                events.append(ev)
                self._dispatch_event(ev["event"], setup, ev["message"])
                continue

            should_execute = False
            exec_price = setup.entry_level

            if getattr(self, "scalp_mode", False) or getattr(setup, "is_scalp", False) or getattr(setup, "fast_market_fill", False):
                should_execute = True
                exec_price = curr_price
            # LONG TRIGGER: Price trades at or touches entry level
            elif setup.direction == "LONG":
                if low <= setup.entry_level <= high or curr_price <= setup.entry_level * 1.0008:
                    should_execute = True
                    exec_price = setup.entry_level if low <= setup.entry_level <= high else curr_price

            # SHORT TRIGGER: Price trades at or touches entry level
            elif setup.direction == "SHORT":
                if low <= setup.entry_level <= high or curr_price >= setup.entry_level * 0.9992:
                    should_execute = True
                    exec_price = setup.entry_level if low <= setup.entry_level <= high else curr_price

            if should_execute:
                # Never create a local OPEN position until the broker confirms a full fill.
                try:
                    if getattr(self, "broker", None) is None:
                        raise RuntimeError("No broker is configured; refusing local-only execution.")
                    side = "buy" if setup.direction == "LONG" else "sell"
                    broker_order = self.broker.place_order(
                        symbol=setup.symbol, side=side, qty=setup.lots, price=exec_price,
                        stop_loss=setup.stop_loss, take_profit=setup.tp1,
                    )
                    broker_status = str(getattr(broker_order, "status", "")).lower()
                    filled_qty = float(getattr(broker_order, "filled_qty", 0.0) or 0.0)
                    actual_price = getattr(broker_order, "filled_price", None)
                    if broker_status != "filled" or filled_qty + 1e-12 < setup.lots or actual_price is None or float(actual_price) <= 0:
                        raise RuntimeError(f"Broker fill not confirmed (status={broker_status!r}, filled_qty={filled_qty}).")
                    exec_price = float(actual_price)
                except Exception as ex:
                    try:
                        import config as _cfg_halt
                        _hm = str(getattr(_cfg_halt, "BROKER_MODE", "mt5_live")).lower()
                    except Exception:
                        _hm = "mt5_live"
                    if "live" in _hm:
                        self.execution_halted = True
                    setup.status = OrderStatus.CANCELLED
                    setup.close_reason = f"Execution failed: {ex}"
                    setup.closed_at = datetime.now(timezone.utc).isoformat()
                    self.trade_history.append(setup)
                    self.pending_setups.pop(setup.id, None)
                    ev = {
                        "event": "BROKER_EXECUTION_FAILED", "order": setup, "price": curr_price,
                        "message": ("EXECUTION HALTED: broker fill/protection was not confirmed. "
                                    "No local OPEN position was created. Reconcile exchange orders and positions before restarting. "
                                    f"Reason: {type(ex).__name__}: {ex}"),
                    }
                    events.append(ev)
                    self._dispatch_event(ev["event"], setup, ev["message"])
                else:
                    setup.status = OrderStatus.OPEN
                    setup.executed_at = datetime.now(timezone.utc).isoformat()
                    setup.executed_price = exec_price
                    setup.current_price = exec_price
                    setup.highest_price = exec_price
                    setup.lowest_price = exec_price
                    setup.remaining_size = setup.size
                    setup.remaining_lots = setup.lots
                    setup.ticket = (
                        getattr(broker_order, "ticket", None)
                        or getattr(broker_order, "order_id", None)
                        or getattr(broker_order, "id", None)
                        or setup.id
                    )
                    setup._just_executed = True
                    self.active_positions[setup.id] = setup
                    self.pending_setups.pop(setup.id, None)
                    is_delta = "delta" in type(self.broker).__name__.lower()
                    status_title = "DELTA LIVE TRADE CONFIRMED" if is_delta else "MT5 LIVE TRADE CONFIRMED"
                    footer_text = "🟢 _Delta Exchange India Live Execution_" if is_delta else "🟢 _MT5 Live Execution_"
                    ev = {
                        "event": "TRADE_EXECUTED", "order": setup, "price": exec_price,
                        "message": ((
                            f"✅ **{status_title}**\n"
                            f"`{setup.symbol}` **{setup.direction}** @ `${exec_price:,.4f}`\n"
                            f"SL `${setup.stop_loss:,.2f}` | TP1 `${setup.tp1:,.2f}` | Lots `{setup.lots}`\n"
                            f"{footer_text}"
                        )),
                    }
                    events.append(ev)
                    self._dispatch_event(ev["event"], setup, ev["message"])

        # ----------------------------------------------------
        # 2. Manage Open Positions
        # ----------------------------------------------------
        matching_positions = list(self.active_positions.get_all(sym))
        for pos in matching_positions:
            if pos.id not in self.active_positions and pos not in self.active_positions.values():
                continue
            pos.current_price = curr_price
            is_long = pos.direction == "LONG"
            entry = pos.executed_price or pos.entry_level
            initial_risk = abs(entry - pos.stop_loss)
            just_executed = getattr(pos, "_just_executed", False)
            if hasattr(pos, "_just_executed"):
                delattr(pos, "_just_executed")

            if not just_executed:
                pos.highest_price = max(pos.highest_price, high)
                pos.lowest_price = min(pos.lowest_price, low)
            else:
                pos.highest_price = max(pos.highest_price, curr_price)
                pos.lowest_price = min(pos.lowest_price, curr_price)

            # Calculate live unrealized PnL
            if is_long:
                pos.unrealized_pnl = (curr_price - entry) * pos.remaining_size
            else:
                pos.unrealized_pnl = (entry - curr_price) * pos.remaining_size

            # Sync broker mark price if supported
            if getattr(self, "broker", None) is not None:
                try:
                    if hasattr(self.broker, "update_mark_price"):
                        self.broker.update_mark_price(sym, curr_price)
                    elif hasattr(self.broker, "mark_price"):
                        self.broker.mark_price(sym, curr_price)
                except Exception:
                    pass

            try:
                import config as _cfg_sc
                scalp_cfg = bool(getattr(_cfg_sc, "SCALP_ENABLED", False))
                scalp_usd = float(getattr(self, "scalp_tp_usd", None) or getattr(_cfg_sc, "SCALP_TAKE_PROFIT_USD", 2.5) or 0)
                scalp_pct = float(getattr(self, "scalp_tp_pct", None) or getattr(_cfg_sc, "SCALP_TAKE_PROFIT_PCT", 0.10) or 0)
                scalp_sl_usd = float(getattr(self, "scalp_sl_usd", None) or getattr(_cfg_sc, "SCALP_STOP_LOSS_USD", 3.0) or 0)
                scalp_sl_pct = float(getattr(self, "scalp_sl_pct", None) or getattr(_cfg_sc, "SCALP_STOP_LOSS_PCT", 0.12) or 0)
            except Exception:
                scalp_cfg, scalp_usd, scalp_pct, scalp_sl_usd, scalp_sl_pct = False, 2.5, 0.10, 3.0, 0.12

            scalp_on = getattr(self, "scalp_mode", False) or (
                scalp_cfg and (
                    "scalp" in str(getattr(pos, "setup_reason", "")).lower()
                    or getattr(pos, "is_scalp", False)
                    or (not pos.tp1 and not pos.tp2)
                )
            )

            if scalp_on:
                pos.highest_profit_usd = max(float(getattr(pos, "highest_profit_usd", 0.0) or 0.0), pos.unrealized_pnl)
                pos_ticket = getattr(pos, "ticket", None) or pos.id

                # (1) Scalp Progressive Break-Even Lock
                be_act_usd = float(getattr(self, "scalp_be_lock_usd", 0.50))
                be_act_pct = float(getattr(self, "scalp_trail_activation_pct", 0.04))
                move_gain_pct = ((pos.highest_price - entry) / entry * 100.0) if is_long else ((entry - pos.lowest_price) / entry * 100.0)
                if pos.be_stage < 1 and (pos.highest_profit_usd >= be_act_usd or move_gain_pct >= be_act_pct):
                    pos.be_stage = 1
                    be_level = round(entry * 1.0002, 4) if is_long else round(entry * 0.9998, 4)
                    if (is_long and be_level > pos.stop_loss) or (not is_long and be_level < pos.stop_loss):
                        old_sl = pos.stop_loss
                        pos.stop_loss = be_level
                        try:
                            self._broker_modify(pos.symbol, stop_loss=pos.stop_loss, ticket=pos_ticket)
                        except Exception:
                            pass
                        be_msg = (
                            f"🛡️ **SCALP BREAK-EVEN SECURED** ({pos.symbol} [#{pos.id}])\n"
                            f"SL secured: `${old_sl:,.2f}` ➔ `${pos.stop_loss:,.2f}`\n"
                            f"Profit protected: `+${pos.highest_profit_usd:,.2f}`"
                        )
                        events.append({"event": "SCALP_BE_LOCKED", "order": pos, "new_stop": pos.stop_loss, "message": be_msg})
                        self._dispatch_event("SCALP_BE_LOCKED", pos, be_msg)

                # (2) Scalp Dynamic Trailing Stop
                trail_on = bool(getattr(self, "scalp_trail_enabled", True))
                trail_act_usd = float(getattr(self, "scalp_trail_activation_usd", 1.00))
                trail_act_pct = float(getattr(self, "scalp_trail_activation_pct", 0.04))
                trail_dist_pct = float(getattr(self, "scalp_trail_distance_pct", 0.03))
                trail_dist_usd = float(getattr(self, "scalp_trail_distance_usd", 0.80))

                trail_stop_before = pos.stop_loss

                if trail_on and (pos.highest_profit_usd >= trail_act_usd or move_gain_pct >= trail_act_pct):
                    pos.trailing_sl_active = True
                    if is_long:
                        pct_trail = pos.highest_price * (1.0 - trail_dist_pct / 100.0)
                        usd_trail = (pos.highest_price - (trail_dist_usd / pos.remaining_size)) if pos.remaining_size > 0 else pct_trail
                        new_sl = round(max(pct_trail, usd_trail), 4)
                        new_sl = max(new_sl, round(entry * 1.0002, 4))
                        if new_sl > pos.stop_loss:
                            old_sl = pos.stop_loss
                            pos.stop_loss = new_sl
                            try:
                                self._broker_modify(pos.symbol, stop_loss=pos.stop_loss, ticket=pos_ticket)
                            except Exception:
                                pass
                            tr_msg = (
                                f"📈 **SCALP TRAIL SL ADVANCED** ({pos.symbol} [#{pos.id}])\n"
                                f"Trailing Stop: `${old_sl:,.2f}` ➔ `${pos.stop_loss:,.2f}`\n"
                                f"Peak Profit: `+${pos.highest_profit_usd:,.2f}`"
                            )
                            events.append({"event": "SCALP_TRAIL_UPDATED", "order": pos, "new_stop": pos.stop_loss, "message": tr_msg})
                            self._dispatch_event("SCALP_TRAIL_UPDATED", pos, tr_msg)
                    else:
                        pct_trail = pos.lowest_price * (1.0 + trail_dist_pct / 100.0)
                        usd_trail = (pos.lowest_price + (trail_dist_usd / pos.remaining_size)) if pos.remaining_size > 0 else pct_trail
                        new_sl = round(min(pct_trail, usd_trail), 4)
                        new_sl = min(new_sl, round(entry * 0.9998, 4))
                        if new_sl < pos.stop_loss:
                            old_sl = pos.stop_loss
                            pos.stop_loss = new_sl
                            try:
                                self._broker_modify(pos.symbol, stop_loss=pos.stop_loss, ticket=pos_ticket)
                            except Exception:
                                pass
                            tr_msg = (
                                f"📈 **SCALP TRAIL SL ADVANCED** ({pos.symbol} [#{pos.id}])\n"
                                f"Trailing Stop: `${old_sl:,.2f}` ➔ `${pos.stop_loss:,.2f}`\n"
                                f"Peak Profit: `+${pos.highest_profit_usd:,.2f}`"
                            )
                            events.append({"event": "SCALP_TRAIL_UPDATED", "order": pos, "new_stop": pos.stop_loss, "message": tr_msg})
                            self._dispatch_event("SCALP_TRAIL_UPDATED", pos, tr_msg)

                # (3) Micro Scalp Hard Take Profit (Safety Ceiling)
                if pos.unrealized_pnl > 0:
                    hit_pct = False
                    if scalp_pct > 0 and entry > 0:
                        move_pct = abs(curr_price - entry) / entry * 100.0
                        hit_pct = move_pct >= scalp_pct
                    hit_usd = scalp_usd > 0 and pos.unrealized_pnl >= scalp_usd
                    if hit_usd or hit_pct:
                        broker_ok = True
                        try:
                            res = self._broker_close(sym, price=curr_price, ticket=pos_ticket)
                            if res is False:
                                broker_ok = False
                        except Exception as ex:
                            broker_ok = False
                            logger.error("[ExecutionEngine] Broker scalp close failed for %s: %s", sym, ex)
                        if not broker_ok:
                            self.execution_halted = True
                            msg = f"🚨 **CRITICAL: BROKER CLOSE FAILED** for `{sym}` [#{pos.id}]. Local position retained. Execution halted."
                            self._dispatch_event("BROKER_CLOSE_FAILED", pos, msg)
                            continue

                        pos.exit_price = curr_price
                        pos.realized_pnl = pos.unrealized_pnl
                        pos.status = OrderStatus.CLOSED
                        pos.closed_at = datetime.now(timezone.utc).isoformat()
                        pos.close_reason = f"Scalp TP (${pos.realized_pnl:+.2f})"
                        try:
                            self.risk_manager.update_after_trade(pos.realized_pnl, r_multiple=0.5)
                        except Exception:
                            pass
                        self._notify_brain_close(pos, 0.5)
                        self.trade_history.append(pos)
                        self.active_positions.pop(pos.id, None)
                        self.save_state()
                        msg = (
                            f"⚡ **SCALP AUTO-CLOSE** `{sym}` [#{pos.id}]\n"
                            f"Exit `${curr_price:,.2f}` | PnL `${pos.realized_pnl:+,.2f}`\n"
                            f"_Fast scalp profit target hit_"
                        )
                        events.append({"event": "SCALP_CLOSE", "order": pos, "price": curr_price, "message": msg})
                        self._dispatch_event("SCALP_CLOSE", pos, msg)
                        continue

                # (4) Scalp Trailing SL Exit Hit
                if pos.trailing_sl_active:
                    stop_tightened_in_candle = (pos.stop_loss != trail_stop_before)
                    if stop_tightened_in_candle:
                        trail_hit = (curr_price <= pos.stop_loss) if is_long else (curr_price >= pos.stop_loss)
                    else:
                        trail_hit = (low <= pos.stop_loss) if is_long else (high >= pos.stop_loss)

                    if trail_hit:
                        exit_price = pos.stop_loss
                        broker_ok = True
                        try:
                            res = self._broker_close(sym, price=exit_price, ticket=pos_ticket)
                            if res is False:
                                broker_ok = False
                        except Exception as ex:
                            broker_ok = False
                            logger.error("[ExecutionEngine] Broker scalp trail close failed for %s: %s", sym, ex)
                        if not broker_ok:
                            self.execution_halted = True
                            msg = f"🚨 **CRITICAL: BROKER CLOSE FAILED** for `{sym}` [#{pos.id}]. Local position retained. Execution halted."
                            self._dispatch_event("BROKER_CLOSE_FAILED", pos, msg)
                            continue

                        pos.exit_price = exit_price
                        pos.realized_pnl = (exit_price - entry) * pos.remaining_size if is_long else (entry - exit_price) * pos.remaining_size
                        pos.status = OrderStatus.CLOSED
                        pos.closed_at = datetime.now(timezone.utc).isoformat()
                        pos.close_reason = f"Scalp Trailing SL Exit (+${pos.realized_pnl:+.2f})"
                        try:
                            self.risk_manager.update_after_trade(pos.realized_pnl, r_multiple=0.5)
                        except Exception:
                            pass
                        self._notify_brain_close(pos, 0.5)
                        self.trade_history.append(pos)
                        self.active_positions.pop(pos.id, None)
                        self.save_state()
                        msg = (
                            f"🏆 **SCALP TRAIL PROFIT SECURED** `{sym}` [#{pos.id}]\n"
                            f"Exit: `${exit_price:,.2f}` | PnL: `+${pos.realized_pnl:,.2f}`\n"
                            f"_Dynamic trailing stop locked in scalp gains_"
                        )
                        events.append({"event": "SCALP_TRAIL_HIT", "order": pos, "price": exit_price, "message": msg})
                        self._dispatch_event("SCALP_TRAIL_HIT", pos, msg)
                        continue

                # (5) Micro Scalp Stop Loss (Fast Loss Cut to Preserve Capital)
                if not pos.trailing_sl_active and pos.unrealized_pnl < 0:
                    hit_sl_pct = False
                    if scalp_sl_pct > 0 and entry > 0:
                        move_pct = abs(curr_price - entry) / entry * 100.0
                        hit_sl_pct = move_pct >= scalp_sl_pct
                    hit_sl_usd = scalp_sl_usd > 0 and pos.unrealized_pnl <= -scalp_sl_usd
                    if hit_sl_usd or hit_sl_pct:
                        broker_ok = True
                        try:
                            res = self._broker_close(sym, price=curr_price, ticket=pos_ticket)
                            if res is False:
                                broker_ok = False
                        except Exception as ex:
                            broker_ok = False
                            logger.error("[ExecutionEngine] Broker scalp SL cut failed for %s: %s", sym, ex)
                        if not broker_ok:
                            self.execution_halted = True
                            msg = f"🚨 **CRITICAL: BROKER CLOSE FAILED** for `{sym}` [#{pos.id}]. Local position retained. Execution halted."
                            self._dispatch_event("BROKER_CLOSE_FAILED", pos, msg)
                            continue

                        pos.exit_price = curr_price
                        pos.realized_pnl = pos.unrealized_pnl
                        pos.status = OrderStatus.CLOSED
                        pos.closed_at = datetime.now(timezone.utc).isoformat()
                        pos.close_reason = f"Scalp SL (${pos.realized_pnl:+.2f})"
                        try:
                            self.risk_manager.update_after_trade(pos.realized_pnl, r_multiple=-1.0)
                        except Exception:
                            pass
                        self._notify_brain_close(pos, -1.0)
                        self.trade_history.append(pos)
                        self.active_positions.pop(pos.id, None)
                        self.save_state()
                        msg = (
                            f"🛡️ **SCALP STOP LOSS CUT** `{sym}` [#{pos.id}]\n"
                            f"Exit `${curr_price:,.2f}` | PnL `${pos.realized_pnl:+,.2f}`\n"
                            f"_Tight micro stop loss protection triggered_"
                        )
                        events.append({"event": "SCALP_SL", "order": pos, "price": curr_price, "message": msg})
                        self._dispatch_event("SCALP_SL", pos, msg)
                        continue

            # (A) Check Liquidation barrier for leveraged positions
            if pos.liquidation_price and pos.leverage > 1.0:
                liq_hit = (low <= pos.liquidation_price) if is_long else (high >= pos.liquidation_price)
                if liq_hit:
                    pos.exit_price = pos.liquidation_price
                    # In liquidation, entire margin is lost
                    pos.realized_pnl = -pos.margin_required
                    pos.status = OrderStatus.CLOSED
                    pos.closed_at = datetime.now(timezone.utc).isoformat()
                    pos.close_reason = f"Margin Liquidation Call ({pos.leverage:.0f}x)"
                    try:
                        self.risk_manager.update_after_trade(pos.realized_pnl, r_multiple=-1.0)
                    except Exception:
                        pass
                    self._notify_brain_close(pos, -1.0)
                    self.trade_history.append(pos)
                    self.active_positions.pop(pos.id, None)

                    ev = {
                        "event": "TRADE_LIQUIDATED",
                        "order": pos,
                        "exit_price": pos.liquidation_price,
                        "final_pnl": pos.realized_pnl,
                        "message": (
                            f"💥 **POSITION LIQUIDATED** ({pos.symbol} [#{pos.id}])\n"
                            f"Liquidation Price Hit: `${pos.liquidation_price:,.2f}`\n"
                            f"Leverage: `{pos.leverage:.0f}x` | Margin Lost: `${pos.margin_required:,.2f}`"
                        ),
                    }
                    events.append(ev)
                    self._dispatch_event(ev["event"], pos, ev["message"])
                    continue

            if scalp_on:
                # Micro-scalp positions are fully managed by the dedicated scalp lifecycle above
                continue

            # (B) Check Progressive Auto Break-Even
            old_stop = pos.stop_loss
            be_stop, be_reason = self.risk_manager.auto_breakeven_stop(
                pos.direction.lower(), entry, pos.stop_loss, pos.highest_price, pos.lowest_price, initial_risk
            )
            if be_stop != pos.stop_loss:
                pos.stop_loss = be_stop
                stage = 2 if "full" in be_reason.lower() else 1
                if stage > pos.be_stage:
                    pos.be_stage = stage
                    try:
                        self._broker_modify(pos.symbol, stop_loss=be_stop, ticket=getattr(pos, "ticket", None) or pos.id)
                    except Exception:
                        pass
                    ev = {
                        "event": "AUTO_BE_LOCKED",
                        "order": pos,
                        "new_stop": be_stop,
                        "message": (
                            f"🛡️ **AUTO BREAK-EVEN LOCKED** ({pos.symbol} [#{pos.id}])\n"
                            f"Stop Loss advanced: `${old_stop:,.2f}` ➔ `${be_stop:,.2f}`\n"
                            f"Stage: {be_reason}"
                        ),
                    }
                    events.append(ev)
                    self._dispatch_event(ev["event"], pos, ev["message"])

            # (C) Check TP1 (+1.5R)
            tp1_just_hit = False
            eval_high = curr_price if just_executed else high
            eval_low = curr_price if just_executed else low
            tp1_hit = (eval_high >= pos.tp1) if is_long else (eval_low <= pos.tp1)
            if pos.partials_done == 0 and tp1_hit:
                tp1_just_hit = True
                close_fraction = self.risk_manager.config.tp1_close_pct  # 50%
                closed_size = pos.size * close_fraction
                pos.remaining_size -= closed_size
                pos.remaining_lots = round(pos.lots * (1.0 - close_fraction), 4)
                pos.partials_done = 1

                partial_pnl = (pos.tp1 - entry) * closed_size if is_long else (entry - pos.tp1) * closed_size
                pos.realized_pnl += partial_pnl

                # Move stop to BE with buffer
                buf = initial_risk * self.risk_manager.config.be_buffer_r
                pos.stop_loss = (entry + buf) if is_long else (entry - buf)
                pos.be_stage = 2

                try:
                    self._broker_close(pos.symbol, qty=round(pos.lots * close_fraction, 4), price=pos.tp1, ticket=getattr(pos, "ticket", None) or pos.id)
                except Exception as ex:
                    print(f"[ExecutionEngine] Broker TP1 close: {ex}")
                try:
                    self._broker_modify(pos.symbol, stop_loss=pos.stop_loss, take_profit=pos.tp2, ticket=getattr(pos, "ticket", None) or pos.id)
                except Exception:
                    pass

                ev = {
                    "event": "TP1_PARTIAL_TAKEN",
                    "order": pos,
                    "exit_price": pos.tp1,
                    "realized_pnl": partial_pnl,
                    "message": (
                        f"🎯 **TARGET 1 HIT (+1.5R)** ({pos.symbol} [#{pos.id}])\n"
                        f"50% closed @ `${pos.tp1:,.2f}` | PnL: `+${partial_pnl:,.2f}`\n"
                        f"Stop Loss secured at Break-Even (`${pos.stop_loss:,.2f}`).\n"
                        f"Remaining lots: `{pos.remaining_lots}` trailing towards TP2."
                    ),
                }
                events.append(ev)
                self._dispatch_event(ev["event"], pos, ev["message"])

            # (C2) Structure-based trailing after TP1
            if pos.partials_done >= 1 and (pos.id in self.active_positions or pos in self.active_positions.values()):
                try:
                    from execution_enhancements import apply_structure_trail
                    atr_proxy = abs((pos.executed_price or pos.entry_level) - pos.stop_loss) / 1.2
                    import pandas as _pd
                    _df = _pd.DataFrame({
                        "high": [pos.highest_price, high],
                        "low": [pos.lowest_price, low],
                        "close": [curr_price, curr_price],
                        "open": [curr_price, curr_price],
                    })
                    hist = candle.get("_history") if isinstance(candle, dict) else None
                    if hist is not None and len(hist) >= 20:
                        _df = hist
                    trail_ev = apply_structure_trail(pos, _df, atr_value=float(atr_proxy or 0))
                    if trail_ev:
                        events.append(trail_ev)
                        self._dispatch_event(trail_ev["event"], pos, trail_ev["message"])
                        try:
                            self._broker_modify(pos.symbol, stop_loss=pos.stop_loss, ticket=getattr(pos, "ticket", None) or pos.id)
                        except Exception:
                            pass
                except Exception:
                    pass

            # (D) Check TP2 (+2.5R)
            tp2_hit = (eval_high >= pos.tp2) if is_long else (eval_low <= pos.tp2)
            if pos.partials_done == 1 and pos.tp2 and tp2_hit:
                close_fraction = self.risk_manager.config.tp2_close_pct  # 30%
                closed_size = pos.size * close_fraction
                pos.remaining_size -= closed_size
                pos.remaining_lots = round(pos.lots * 0.20, 4)
                pos.partials_done = 2

                partial_pnl = (pos.tp2 - entry) * closed_size if is_long else (entry - pos.tp2) * closed_size
                pos.realized_pnl += partial_pnl

                # Start dynamic ATR/R trailing for remaining 20%
                pos.stop_loss = self.risk_manager.trailing_stop(
                    pos.direction.lower(), entry, pos.stop_loss, pos.highest_price, pos.lowest_price, initial_risk, tp1_hit=True
                )

                try:
                    self._broker_close(pos.symbol, qty=round(pos.lots * close_fraction, 4), price=pos.tp2, ticket=getattr(pos, "ticket", None) or pos.id)
                except Exception as ex:
                    print(f"[ExecutionEngine] Broker TP2 close: {ex}")
                try:
                    self._broker_modify(pos.symbol, stop_loss=pos.stop_loss, ticket=getattr(pos, "ticket", None) or pos.id)
                except Exception:
                    pass

                ev = {
                    "event": "TP2_PARTIAL_TAKEN",
                    "order": pos,
                    "exit_price": pos.tp2,
                    "realized_pnl": partial_pnl,
                    "message": (
                        f"🚀 **TARGET 2 HIT (+2.5R)** ({pos.symbol} [#{pos.id}])\n"
                        f"30% closed @ `${pos.tp2:,.2f}` | PnL: `+${partial_pnl:,.2f}`\n"
                        f"Remaining 20% (`{pos.remaining_lots}` lots) dynamic trailing active."
                    ),
                }
                events.append(ev)
                self._dispatch_event(ev["event"], pos, ev["message"])

            # (E) Dynamic Trailing after TP1/TP2
            if pos.partials_done >= 1:
                trail_stop = self.risk_manager.trailing_stop(
                    pos.direction.lower(), entry, pos.stop_loss, pos.highest_price, pos.lowest_price, initial_risk, tp1_hit=True
                )
                if (is_long and trail_stop > pos.stop_loss) or (not is_long and trail_stop < pos.stop_loss):
                    pos.stop_loss = trail_stop

            # (F) Stop Loss / Break-Even / Exit Hit
            stop_tightened_in_candle = (pos.stop_loss != old_stop)
            if just_executed:
                stop_hit = (curr_price <= pos.stop_loss) if is_long else (curr_price >= pos.stop_loss)
            elif tp1_just_hit or stop_tightened_in_candle:
                if is_long:
                    stop_hit = (low <= old_stop) or (curr_price <= pos.stop_loss)
                else:
                    stop_hit = (high >= old_stop) or (curr_price >= pos.stop_loss)
            else:
                stop_hit = (low <= pos.stop_loss) if is_long else (high >= pos.stop_loss)

            if stop_hit:
                exit_price = pos.stop_loss
                broker_ok = True
                try:
                    res = self._broker_close(pos.symbol, price=exit_price, ticket=getattr(pos, "ticket", None) or pos.id)
                    if res is False:
                        broker_ok = False
                except Exception as ex:
                    broker_ok = False
                    logger.error("[ExecutionEngine] Broker exit close failed for %s: %s", pos.symbol, ex)
                if not broker_ok:
                    self.execution_halted = True
                    msg = f"🚨 **CRITICAL: BROKER CLOSE FAILED** for `{pos.symbol}` [#{pos.id}]. Local position retained. Execution halted."
                    self._dispatch_event("BROKER_CLOSE_FAILED", pos, msg)
                    continue

                final_pnl = (exit_price - entry) * pos.remaining_size if is_long else (entry - exit_price) * pos.remaining_size
                pos.realized_pnl += final_pnl
                pos.exit_price = exit_price
                pos.status = OrderStatus.CLOSED
                pos.closed_at = datetime.now(timezone.utc).isoformat()
                pos.close_reason = "Stop Loss / Break-Even Exit"

                r_mult = pos.realized_pnl / (initial_risk * pos.size) if (initial_risk * pos.size) > 0 else 0.0
                try:
                    self.risk_manager.update_after_trade(pos.realized_pnl, r_multiple=r_mult)
                except Exception:
                    pass

                self._notify_brain_close(pos, r_mult)
                self.trade_history.append(pos)
                self.active_positions.pop(pos.id, None)
                self.save_state()

                ev = {
                    "event": "TRADE_CLOSED",
                    "order": pos,
                    "exit_price": exit_price,
                    "final_pnl": pos.realized_pnl,
                    "message": (
                        f"🏁 **TRADE CLOSED** ({pos.symbol} [#{pos.id}])\n"
                        f"Exit: `${exit_price:,.2f}` | Reason: {pos.close_reason}\n"
                        f"Total Realized PnL: `+${pos.realized_pnl:,.2f}`"
                        if pos.realized_pnl >= 0
                        else f"Total Realized PnL: `-${abs(pos.realized_pnl):,.2f}`"
                    ),
                }
                events.append(ev)
                self._dispatch_event(ev["event"], pos, ev["message"])

        return events

    def close_manually(self, symbol: str, current_price: float, reason: str = "Manual close") -> Optional[TradeOrder]:
        """Manually close active position(s) immediately."""
        key = str(symbol).strip()
        if key not in self.active_positions:
            return None

        # Check if key is direct order ID in active_positions or a symbol
        if key in self.active_positions.keys():
            matching = [self.active_positions[key]]
        else:
            matching = self.active_positions.get_all(key)

        if not matching:
            return None

        last_closed = None
        for pos in list(matching):
            is_long = pos.direction == "LONG"
            entry = pos.executed_price or pos.entry_level

            # If broker attached, attempt broker close first
            if getattr(self, "broker", None):
                try:
                    res = self._broker_close(pos.symbol, price=current_price, ticket=getattr(pos, "ticket", None) or pos.id)
                    if res is False:
                        self.execution_halted = True
                        msg = f"🚨 **CRITICAL: BROKER CLOSE REJECTED** for `{pos.symbol}` [#{pos.id}]. Local position retained. Execution halted."
                        self._dispatch_event("BROKER_CLOSE_FAILED", pos, msg)
                        raise OrderStateUnknownError(f"Broker rejected close position for {pos.symbol}")
                except Exception as ex:
                    self.execution_halted = True
                    msg = f"🚨 **CRITICAL: BROKER CLOSE FAILED** for `{pos.symbol}` [#{pos.id}]: {ex}. Local position retained. Execution halted."
                    self._dispatch_event("BROKER_CLOSE_FAILED", pos, msg)
                    if isinstance(ex, OrderStateUnknownError):
                        raise
                    raise OrderStateUnknownError(f"Broker close failed for {pos.symbol}: {ex}") from ex

            final_pnl = (current_price - entry) * pos.remaining_size if is_long else (entry - current_price) * pos.remaining_size

            pos.realized_pnl += final_pnl
            pos.exit_price = current_price
            pos.status = OrderStatus.CLOSED
            pos.closed_at = datetime.now(timezone.utc).isoformat()
            pos.close_reason = reason

            initial_risk = abs(entry - pos.stop_loss)
            r_mult = pos.realized_pnl / (initial_risk * pos.size) if (initial_risk * pos.size) > 0 else 0.0
            try:
                self.risk_manager.update_after_trade(pos.realized_pnl, r_multiple=r_mult)
            except Exception:
                pass

            self._notify_brain_close(pos, r_mult)
            self.trade_history.append(pos)
            self.active_positions.pop(pos.id, None)
            self.save_state()

            msg = (
                f"⏹️ **MANUAL CLOSE EXECUTED** ({pos.symbol} [#{pos.id}])\n"
                f"Exit: `${current_price:,.2f}` | PnL: `${pos.realized_pnl:+,.2f}`\n"
                f"Reason: {reason}"
            )
            self._dispatch_event("MANUAL_CLOSE", pos, msg)
            last_closed = pos

        return last_closed

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Resolve the latest market/mark price for a symbol."""
        sym = symbol.upper()
        # 1. Check broker mark prices if available
        if getattr(self, "broker", None) is not None:
            if hasattr(self.broker, "mark_prices"):
                bp = self.broker.mark_prices.get(sym)
                if bp and float(bp) > 0:
                    return float(bp)
        # 2. Check active positions or pending setups stored current_price
        if sym in self.active_positions:
            try:
                cp = getattr(self.active_positions[sym], "current_price", None)
                if cp and float(cp) > 0:
                    return float(cp)
            except Exception:
                pass
        if sym in self.pending_setups:
            try:
                cp = getattr(self.pending_setups[sym], "current_price", None)
                if cp and float(cp) > 0:
                    return float(cp)
            except Exception:
                pass
        # 3. Fetch live spot price from realtime_prices
        try:
            from realtime_prices import fetch_spot_price
            rt = fetch_spot_price(sym)
            if rt and rt.get("ok") and float(rt.get("mark") or 0) > 0:
                price = float(rt["mark"])
                if getattr(self, "broker", None) is not None and hasattr(self.broker, "mark_price"):
                    try:
                        self.broker.mark_price(sym, price)
                    except Exception:
                        pass
                for p in self.active_positions.get_all(sym):
                    p.current_price = price
                for s in self.pending_setups.get_all(sym):
                    s.current_price = price
                return price
        except Exception:
            pass
        # 4. Fallback to executed_price or entry_level
        if sym in self.active_positions:
            try:
                pos = self.active_positions[sym]
                return float(pos.executed_price or pos.entry_level or 0.0)
            except Exception:
                pass
        if sym in self.pending_setups:
            try:
                return float(self.pending_setups[sym].entry_level or 0.0)
            except Exception:
                pass
        return None

    def close_position(self, symbol: str, current_price: Optional[float] = None, reason: str = "Manual close") -> Optional[TradeOrder]:
        """Convenience method to close an active position immediately, resolving current price if omitted."""
        key = str(symbol).strip()
        if key not in self.active_positions:
            return None
        if current_price is None:
            pos = self.active_positions[key]
            current_price = self.get_current_price(pos.symbol)
            if current_price is None or current_price <= 0:
                current_price = pos.executed_price or pos.entry_level
        return self.close_manually(key, current_price, reason=reason)

    def close_all_positions(self, reason: str = "Close all") -> list:
        closed = []
        for key in list(self.active_positions.keys()):
            try:
                pos = self.close_position(key, reason=reason)
                if pos is not None:
                    if isinstance(pos, list):
                        closed.extend(pos)
                    else:
                        closed.append(pos)
            except Exception as ex:
                print(f"[ExecutionEngine] close_all {key}: {ex}")
        return closed

    def format_positions_detail(self) -> str:
        max_trades = int(getattr(self, "max_open_trades", 2) or 2)
        slots = self.slots_available()
        lines = [f"📋 **Active Positions ({len(self.active_positions)}/{max_trades} active | {slots} slots free)**"]
        if not self.active_positions:
            lines.append("_No open active positions._")
        else:
            for key, pos in self.active_positions.items():
                sym = getattr(pos, "symbol", str(key))
                entry = float(pos.executed_price or pos.entry_level or 0)
                curr_p = self.get_current_price(sym)
                if curr_p is None or curr_p <= 0:
                    curr_p = entry
                else:
                    pos.current_price = curr_p
                    if entry > 0 and pos.remaining_size > 0:
                        if "LONG" in str(pos.direction).upper():
                            pos.unrealized_pnl = (curr_p - entry) * pos.remaining_size
                        else:
                            pos.unrealized_pnl = (entry - curr_p) * pos.remaining_size

                u = float(getattr(pos, "unrealized_pnl", 0) or 0)
                dir_icon = "🟢" if "LONG" in str(pos.direction).upper() else "🔴"
                pnl_icon = "📈" if u >= 0 else "📉"
                pnl_pct_str = ""
                if entry > 0:
                    pct = ((curr_p - entry) / entry * 100.0) if "LONG" in str(pos.direction).upper() else ((entry - curr_p) / entry * 100.0)
                    pnl_pct_str = f" ({pct:+.2f}%)"

                entry_fmt = f"${entry:,.2f}" if entry >= 1.0 else f"${entry:,.4f}"
                curr_fmt = f"${curr_p:,.2f}" if curr_p >= 1.0 else f"${curr_p:,.4f}"
                oid_str = f" [#{pos.id}]" if getattr(pos, "id", None) else ""
                tkt_str = f" | **Ticket:** `{pos.ticket}`" if getattr(pos, "ticket", None) else ""
                trail_str = ""
                if getattr(pos, "trailing_sl_active", False):
                    peak = float(getattr(pos, "highest_profit_usd", 0.0) or 0.0)
                    trail_str = f"\n• **Trail SL:** 🎯 Active (Peak: `+${peak:,.2f}` | Stop: `${float(pos.stop_loss):,.2f}`)"

                lines.append(
                    f"\n{dir_icon} `{sym}` **{pos.direction}**{oid_str}{tkt_str}\n"
                    f"• **Entry:** `{entry_fmt}` | **Current Market:** `{curr_fmt}` | **PnL:** {pnl_icon} `${u:+,.2f}`{pnl_pct_str}\n"
                    f"• **SL:** `${float(pos.stop_loss):,.2f}` | **TP1:** `${float(pos.tp1):,.2f}` | **TP2:** `${float(pos.tp2):,.2f}`\n"
                    f"• **Lots:** `{pos.lots}` | **Lev:** `{float(pos.leverage or 1):.0f}x` | **Margin:** `${float(pos.margin_required or 0):,.2f}`{trail_str}"
                )
        if self.pending_setups:
            lines.append(f"\n⏳ **Pending Setups ({len(self.pending_setups)}):**")
            for psym, pord in self.pending_setups.items():
                pdir_icon = "🟢" if "LONG" in str(pord.direction).upper() else "🔴"
                sym_to_check = getattr(pord, "symbol", str(psym))
                pcurr_p = self.get_current_price(sym_to_check)
                market_str = ""
                if pcurr_p and pcurr_p > 0:
                    pcurr_fmt = f"${pcurr_p:,.2f}" if pcurr_p >= 1.0 else f"${pcurr_p:,.4f}"
                    dist_pct = ((pord.entry_level - pcurr_p) / pcurr_p * 100.0)
                    market_str = f" | **Current Market:** `{pcurr_fmt}` ({dist_pct:+.2f}% to fill)"
                poid_str = f" [#{pord.id}]" if getattr(pord, "id", None) else ""
                lines.append(
                    f"• {pdir_icon} `{sym_to_check}` **{pord.direction}**{poid_str} @ Entry `${pord.entry_level:,.2f}`{market_str} | SL `${pord.stop_loss:,.2f}` (Lots `{pord.lots}`)"
                )

        # Current Market live quotes summary
        market_quotes = []
        for msym in ("BTCUSDT", "XAUUSD"):
            try:
                mp = self.get_current_price(msym)
                if mp and mp > 0:
                    market_quotes.append(f"`{msym}`: `${mp:,.2f}`")
            except Exception:
                pass
        if market_quotes:
            lines.append(f"\n📊 **Current Market:** {' | '.join(market_quotes)}")

        summary = self.get_portfolio_summary()
        lines.append(
            f"\n💰 **Equity:** `${summary.get('equity', 0):,.2f}` | "
            f"**Unrealized:** `${summary.get('unrealized_pnl', 0):+,.2f}` | "
            f"**Used Margin:** `${summary.get('used_margin', 0):,.2f}`"
        )
        return "\n".join(lines)


    def get_portfolio_summary(self) -> Dict:
        """Returns account equity, margin utilization, and open positions stats."""
        total_used_margin = sum(p.margin_required for p in self.active_positions.values())
        total_unrealized_pnl = sum(p.unrealized_pnl for p in self.active_positions.values())
        equity = self.risk_manager.state.equity + total_unrealized_pnl
        free_margin = max(equity - total_used_margin, 0.0)
        margin_level = (equity / total_used_margin * 100) if total_used_margin > 0 else 999.0

        return {
            "equity": round(equity, 2),
            "balance": round(self.risk_manager.state.equity, 2),
            "used_margin": round(total_used_margin, 2),
            "free_margin": round(free_margin, 2),
            "margin_level_pct": round(margin_level, 1),
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "leverage": self.risk_manager.get_leverage(),
            "active_positions_count": len(self.active_positions),
            "pending_setups_count": len(self.pending_setups),
        }

    def _dispatch_event(self, event_name: str, order: TradeOrder, message: str):
        if self.event_callback:
            try:
                self.event_callback(event_name, order, message)
            except Exception as e:
                print(f"[ExecutionEngine] Callback error ({e})")

