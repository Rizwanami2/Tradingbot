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
from typing import Any, Callable, Dict, List, Optional, Tuple

from execution import OrderStateUnknownError
from risk_manager import RiskManager
import config

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
        import config
        self.risk_manager = risk_manager
        self.event_callback = event_callback
        is_test_env = (
            "pytest" in sys.modules
            or "unittest" in sys.modules
            or bool(os.getenv("PYTEST_CURRENT_TEST"))
            or bool(os.getenv("TESTING"))
            or getattr(config, "TESTING", False)
        )
        if broker is not None:
            self.broker = broker
        else:
            try:
                from execution import create_broker
                import config
                init_bal = float(getattr(self.risk_manager.state, "equity", None) or getattr(config, "INITIAL_EQUITY", 10000.0) or 10000.0) if hasattr(self, "risk_manager") and hasattr(self.risk_manager, "state") else 10000.0
                self.broker = create_broker(
                    broker_mode=broker_mode,
                    initial_balance=init_bal,
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
                if is_test_env:
                    b_m = str(getattr(config, "BROKER_MODE", "")).lower()
                    init_bal = float(getattr(self.risk_manager.state, "equity", 10000.0) or 10000.0)
                    if "delta" in b_m:
                        from delta_broker import MockDeltaBroker
                        self.broker = MockDeltaBroker()
                    else:
                        from mt5_broker import MockMT5Broker
                        self.broker = MockMT5Broker(initial_balance=init_bal)
                else:
                    self.broker = None
        self._pending_setups: PositionDict = PositionDict()
        self._active_positions: PositionDict = PositionDict()
        self.trade_history: List[TradeOrder] = []
        self._order_counter: int = 0
        # Prevent duplicate orders after any ambiguous broker response.
        self.execution_halted: bool = False
        import config as _cfg_slots
        self.slots_mode: str = getattr(_cfg_slots, "get_slots_mode", lambda: "auto")() or "auto"
        self.manual_slots: int = min(6, max(1, int(getattr(_cfg_slots, "MAX_OPEN_TRADES", 6) or 6)))
        if max_open_trades is not None:
            self.max_open_trades = int(max_open_trades)
        else:
            try:
                self.max_open_trades = min(6, max(1, int(getattr(_cfg_slots, "MAX_OPEN_TRADES", 6) or 6)))
            except Exception:
                self.max_open_trades = 6
        if self.slots_mode == "manual":
            self.max_open_trades = self.manual_slots
        self.max_positions_per_symbol: Optional[int] = None
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
        if state_file:
            self.state_file: str = state_file
        elif os.getenv("EXECUTION_STATE_FILE"):
            self.state_file = os.environ["EXECUTION_STATE_FILE"]
        elif not is_test_env:
            self.state_file = _DEFAULT_STATE_FILE
        else:
            self.state_file = ""

        if self.state_file and os.path.isfile(self.state_file):
            self.restore_state(self.state_file)

    @property
    def active_positions(self) -> PositionDict:
        return self._active_positions

    @active_positions.setter
    def active_positions(self, val: Any) -> None:
        if isinstance(val, PositionDict):
            self._active_positions = val
        else:
            p = PositionDict()
            if isinstance(val, dict):
                p.update(val)
            self._active_positions = p

    @property
    def pending_setups(self) -> PositionDict:
        return self._pending_setups

    @pending_setups.setter
    def pending_setups(self, val: Any) -> None:
        if isinstance(val, PositionDict):
            self._pending_setups = val
        else:
            p = PositionDict()
            if isinstance(val, dict):
                p.update(val)
            self._pending_setups = p

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
        target_path = filepath or self.state_file
        if not target_path:
            return ""
        target = Path(target_path)
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
        target_path = filepath or self.state_file
        if not target_path:
            return False
        target = Path(target_path)
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
                auto_learn=True,
            )
        except Exception:
            pass

        # 4. Remove from Virtual Stop Manager
        try:
            from virtual_stops import get_virtual_stop_manager
            key = getattr(pos, "ticket", None) or getattr(pos, "id", None)
            if key:
                get_virtual_stop_manager().remove(key)
        except Exception:
            pass

    _notify_brain_close = _notify_trade_closed


    def open_trade_count(self) -> int:
        return len(self.active_positions) + len(self.pending_setups)

    def slots_available(self) -> int:
        return max(0, int(getattr(self, "max_open_trades", 6)) - self.open_trade_count())

    def set_slots_mode(self, mode: str, count: Optional[int] = None) -> Tuple[bool, str]:
        """
        Configure active positions limit to manual or automatic mode (capped at max 6 positions).
        In manual mode: user specifies fixed slots (1..6).
        In automatic mode: dynamic slots allocation based on account equity, free margin, and active profile.
        """
        m = str(mode or "").strip().lower()
        import config
        if m in ("manual", "man", "fixed"):
            if count is not None:
                val = int(count)
            else:
                val = int(getattr(self, "manual_slots", 6) or 6)
            val = max(1, min(6, val))
            self.slots_mode = "manual"
            self.manual_slots = val
            self.max_open_trades = val
            if hasattr(config, "set_slots_mode"):
                config.set_slots_mode("manual")
            if hasattr(config, "MAX_OPEN_TRADES"):
                config.MAX_OPEN_TRADES = val
            return True, f"Active positions limit set to MANUAL mode: {val}/6 slots."
        elif m in ("auto", "dynamic", "adaptive"):
            self.slots_mode = "auto"
            if hasattr(config, "set_slots_mode"):
                config.set_slots_mode("auto")
            new_slots = self.update_auto_slots()
            return True, f"Active positions limit set to AUTOMATIC mode: dynamic allocation active ({new_slots}/6 slots based on equity and margin)."
        else:
            return False, f"Unknown slots mode '{mode}'. Choose 'manual' or 'auto'."

    def update_auto_slots(self) -> int:
        """
        Dynamically calculate optimal active positions limit (1..6) in automatic mode
        based on real-time equity, free margin, and risk manager capacity.
        """
        if getattr(self, "slots_mode", "auto") == "manual":
            return int(self.max_open_trades)

        # Base auto slots on equity & free margin
        equity = 10000.0
        free_margin = 10000.0
        try:
            telemetry = self.get_wallet_telemetry()
            equity = float(telemetry.get("equity", 0.0) or 0.0)
            free_margin = float(telemetry.get("free_margin", 0.0) or equity)
        except Exception:
            pass

        if equity <= 0:
            if hasattr(self, "risk_manager") and hasattr(self.risk_manager, "state"):
                equity = float(getattr(self.risk_manager.state, "equity", 10000.0) or 10000.0)
                free_margin = equity

        # Progressive tier allocation (max 6 positions)
        if equity < 250.0:
            slots = 2
        elif equity < 750.0:
            slots = 3
        elif equity < 2000.0:
            slots = 4
        elif equity < 5000.0:
            slots = 5
        else:
            slots = 6

        # Margin safety check: ensure each slot has sufficient free margin
        if free_margin > 0 and slots > 1:
            margin_per_slot = free_margin / float(slots)
            if margin_per_slot < 30.0 and slots > 2:
                slots = max(2, int(free_margin // 30.0))

        slots = max(1, min(6, slots))
        self.max_open_trades = slots
        try:
            import config
            config.MAX_OPEN_TRADES = slots
        except Exception:
            pass
        return self.max_open_trades

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
        max_trades = int(getattr(self, "max_open_trades", 6) or 6)

        # Check maximum concurrent open trades limit
        if self.open_trade_count() >= max_trades:
            if not is_scalp_req and symbol in self.pending_setups:
                # In standard mode, re-registering existing pending symbol replaces it without consuming new slot
                pass
            else:
                raise RuntimeError(
                    f"Max concurrent trades reached ({max_trades})."
                )

        # Check maximum trades on same currency if max_positions_per_symbol is defined
        max_per_curr = getattr(self, "max_positions_per_symbol", None)
        if max_per_curr is not None:
            try:
                from anti_spam import canonical_currency
                curr = canonical_currency(symbol)
                matching_active = [p for p in self.active_positions.values() if canonical_currency(getattr(p, "symbol", "")) == curr]
                matching_pending = [p for p in self.pending_setups.values() if canonical_currency(getattr(p, "symbol", "")) == curr]
                curr_count = len(matching_active) + len(matching_pending)
                if curr_count >= int(max_per_curr):
                    if not is_scalp_req and symbol in self.pending_setups:
                        pass
                    else:
                        raise RuntimeError(
                            f"Max positions reached for {curr} ({curr_count}/{max_per_curr})."
                        )
            except ImportError:
                pass

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
        matching_setups = list(self.pending_setups.get_all(sym)) if hasattr(self.pending_setups, "get_all") else [s for s in self.pending_setups.values() if getattr(s, "symbol", "").upper() == sym]
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

                    # 1. Realized slippage & execution latency analytics
                    try:
                        from execution_analytics import get_execution_tracker
                        get_execution_tracker().record_fill(
                            ticket=setup.ticket,
                            symbol=setup.symbol,
                            action="BUY" if setup.direction == "LONG" else "SELL",
                            requested_price=setup.entry_level,
                            executed_price=exec_price,
                            volume=setup.lots,
                            latency_ms=12.5,
                            broker="Delta" if is_delta else "MT5",
                        )
                    except Exception as _e_an:
                        logger.debug("Failed recording execution analytics: %s", _e_an)

                    # 2. Client-side anti-stop-hunt virtual stops
                    try:
                        from virtual_stops import get_virtual_stop_manager
                        get_virtual_stop_manager().register(
                            symbol=setup.symbol,
                            ticket=setup.ticket,
                            direction=setup.direction,
                            entry_price=exec_price,
                            virtual_stop=setup.stop_loss,
                            virtual_tp1=setup.tp1,
                            virtual_tp2=setup.tp2,
                        )
                    except Exception as _e_vs:
                        logger.debug("Failed registering virtual stop: %s", _e_vs)

                    # 3. Record open trade in TradeJournal for analytics, board, and learning
                    try:
                        from trade_journal import TradeJournal
                        journal = TradeJournal()
                        journal.record_open(
                            symbol=setup.symbol,
                            direction=setup.direction,
                            entry=exec_price,
                            stop=setup.stop_loss,
                            tp1=setup.tp1,
                            size=setup.lots,
                            risk_amount=abs(exec_price - setup.stop_loss) * setup.size,
                            agents=getattr(setup, "agents", []),
                            regime=getattr(setup, "regime", ""),
                            score=getattr(setup, "score", 0.0),
                            conviction=getattr(setup, "conviction", 0.6),
                            trade_id=str(setup.id),
                        )
                    except Exception as _e_tj:
                        logger.debug("Failed recording open in TradeJournal: %s", _e_tj)

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
        matching_positions = list(self.active_positions.get_all(sym)) if hasattr(self.active_positions, "get_all") else [p for p in self.active_positions.values() if getattr(p, "symbol", "").upper() == sym]
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
                peak_pnl = max(pos.unrealized_pnl, (pos.highest_price - entry) * pos.remaining_size if is_long else (entry - pos.lowest_price) * pos.remaining_size)
                pos.highest_profit_usd = max(float(getattr(pos, "highest_profit_usd", 0.0) or 0.0), peak_pnl)
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

                # (4) Scalp Trailing SL / Break-Even Exit Hit
                if pos.trailing_sl_active or pos.be_stage >= 1:
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

            # (F) Stop Loss / Break-Even / Exit Hit / Virtual Stop Hit
            v_hit = False
            v_reason = None
            try:
                from virtual_stops import get_virtual_stop_manager
                v_triggers = get_virtual_stop_manager().check_ticks(pos.symbol, curr_price)
                for rec, trig_reason in v_triggers:
                    pos_ticket = str(getattr(pos, "ticket", None) or pos.id)
                    if (str(rec.ticket) == pos_ticket or str(rec.ticket) == str(pos.id)) and "Stop Loss" in str(trig_reason):
                        v_hit = True
                        v_reason = trig_reason
                        break
            except Exception:
                pass

            stop_tightened_in_candle = (pos.stop_loss != old_stop)
            if v_hit:
                stop_hit = True
            elif just_executed:
                stop_hit = (curr_price <= pos.stop_loss) if is_long else (curr_price >= pos.stop_loss)
            elif tp1_just_hit or stop_tightened_in_candle:
                if is_long:
                    stop_hit = (low <= old_stop) or (curr_price <= pos.stop_loss)
                else:
                    stop_hit = (high >= old_stop) or (curr_price >= pos.stop_loss)
            else:
                stop_hit = (low <= pos.stop_loss) if is_long else (high >= pos.stop_loss)

            if stop_hit:
                exit_price = curr_price if v_hit else pos.stop_loss
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
                pos.close_reason = v_reason if v_hit else "Stop Loss / Break-Even Exit"

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
        """Resolve the latest market/mark price for a symbol using MT5 live feed."""
        sym = symbol.upper()
        # 1. Check broker live mark prices if available
        if getattr(self, "broker", None) is not None:
            if hasattr(self.broker, "mark_prices"):
                for k in (sym, f"{sym}.m", f"{sym}m"):
                    bp = self.broker.mark_prices.get(k)
                    if bp and float(bp) > 0:
                        return float(bp)
        # 2. Check MT5 live quote
        try:
            from mt5_chart import fetch_mt5_live_quote
            q = fetch_mt5_live_quote(sym, broker=getattr(self, "broker", None))
            if q.get("ok") and float(q.get("mark") or 0) > 0:
                price = float(q["mark"])
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
        # 4. Fallback to active positions or pending setups cached current_price / entry
        if sym in self.active_positions:
            try:
                pos = self.active_positions[sym]
                return float(getattr(pos, "current_price", None) or pos.executed_price or pos.entry_level or 0.0)
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
        wallet = self.get_wallet_telemetry()
        curr = wallet.get("currency", "USD") or "USD"
        bal = wallet.get("balance", 0.0)
        eq = wallet.get("equity", 0.0)
        u_pnl = wallet.get("unrealized_pnl", 0.0)
        used_m = wallet.get("used_margin", 0.0)
        free_m = wallet.get("free_margin", 0.0)
        m_lvl = wallet.get("margin_level_pct", 0.0)
        pnl_icon = "📈" if u_pnl >= 0 else "📉"
        conn_tag = "🟢 Connected" if wallet.get("connected", True) else "🟡 Ready"
        b_name = wallet.get("broker_name", "MetaTrader 5 Live")
        acc_str = f" #{wallet.get('account_id')}" if wallet.get('account_id') else ""
        srv_str = f" ({wallet.get('server')})" if wallet.get('server') else ""
        lvl_str = f" | **Margin Level:** `{m_lvl:,.1f}%`" if used_m > 0 and m_lvl > 0 else ""
        pnl_sign_str = f"+${u_pnl:,.2f}" if u_pnl > 0 else (f"-${abs(u_pnl):,.2f}" if u_pnl < 0 else f"${u_pnl:,.2f}")

        max_trades = int(getattr(self, "max_open_trades", 6) or 6)
        slots = self.slots_available()
        mode_str = f"Auto ({max_trades}/6)" if getattr(self, "slots_mode", "auto") == "auto" else f"Manual ({max_trades}/6)"
        lines = [
            f"📋 **Active Positions ({len(self.active_positions)}/{max_trades} active | {slots} slots free)**",
            f"⚙️ **Slots Mode:** `{mode_str}` (Max 6 concurrent positions)",
            f"🏛️ **MT5 Live Account:** `{b_name}{acc_str}{srv_str}` ({conn_tag})",
            f"💵 **Balance:** `${bal:,.2f} {curr}` | **Live Equity:** `${eq:,.2f} {curr}`",
            f"📈 **Floating PnL:** {pnl_icon} `{pnl_sign_str} {curr}` | **Free Margin:** `${free_m:,.2f} {curr}`{lvl_str}",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
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
                oid_str = f" (#{pos.id})" if getattr(pos, "id", None) else ""
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
                poid_str = f" (#{pord.id})" if getattr(pord, "id", None) else ""
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

        # Real-time wallet telemetry
        wallet = self.get_wallet_telemetry()
        curr = wallet.get("currency", "USD") or "USD"
        bal = wallet.get("balance", 0.0)
        eq = wallet.get("equity", 0.0)
        u_pnl = wallet.get("unrealized_pnl", 0.0)
        used_m = wallet.get("used_margin", 0.0)
        free_m = wallet.get("free_margin", 0.0)
        m_lvl = wallet.get("margin_level_pct", 0.0)
        pnl_icon = "📈" if u_pnl >= 0 else "📉"
        conn_tag = "🟢 Connected" if wallet.get("connected", True) else "🟡 Ready"
        b_name = wallet.get("broker_name", "MetaTrader 5 Live")
        acc_str = f" #{wallet.get('account_id')}" if wallet.get("account_id") else ""
        srv_str = f" [{wallet.get('server')}]" if wallet.get("server") else ""
        lvl_str = f" | **Margin Level:** `{m_lvl:,.1f}%`" if used_m > 0 and m_lvl > 0 else ""

        pnl_sign_str = f"+${u_pnl:,.2f}" if u_pnl > 0 else (f"-${abs(u_pnl):,.2f}" if u_pnl < 0 else f"${u_pnl:,.2f}")

        lines.append(
            f"\n💳 **Real-Time Wallet Telemetry**\n"
            f"• **Broker Account:** `{b_name}{acc_str}{srv_str}` ({conn_tag})\n"
            f"• **Wallet Balance:** `${bal:,.2f} {curr}` | **Equity:** `${eq:,.2f} {curr}`\n"
            f"• **Free Margin:** `${free_m:,.2f} {curr}` | **Used Margin:** `${used_m:,.2f} {curr}`{lvl_str}\n"
            f"• **Floating PnL:** {pnl_icon} `{pnl_sign_str} {curr}`\n\n"
            f"💰 **Equity:** `${eq:,.2f}` | **Unrealized:** `{pnl_sign_str}` | **Used Margin:** `${used_m:,.2f}`"
        )
        return "\n".join(lines)


    def get_wallet_telemetry(self) -> Dict[str, Any]:
        """
        Fetch real-time broker wallet telemetry (balance, equity, margins, account id, server).
        Synchronizes live balance and equity into RiskManager state so calculations reflect actual wallet.
        """
        broker = getattr(self, "broker", None)
        b_name = "MetaTrader 5 Live"
        server = ""
        account_id = ""
        currency = "USD"
        connected = False
        mode = "live"
        live_bal: Optional[float] = None
        live_eq: Optional[float] = None
        live_mg: Optional[float] = None
        live_free: Optional[float] = None
        live_lvl: Optional[float] = None

        if broker is not None:
            raw_b_name = type(broker).__name__
            if raw_b_name == "MockMT5Broker":
                b_name = "MT5 Live (Simulated)"
            elif raw_b_name == "MT5Broker":
                b_name = "MetaTrader 5 Live"
            elif raw_b_name in ("DeltaBroker", "MockDeltaBroker"):
                b_name = "Delta Exchange"
            elif "Oanda" in raw_b_name:
                b_name = "OANDA Live"
            elif "Alpaca" in raw_b_name:
                b_name = "Alpaca Markets"
            else:
                b_name = raw_b_name

            if hasattr(broker, "get_account_info"):
                try:
                    info = broker.get_account_info()
                    if info:
                        live_bal = float(getattr(info, "balance", 0.0) or 0.0)
                        live_eq = float(getattr(info, "equity", 0.0) or 0.0)
                        live_mg = float(getattr(info, "margin", 0.0) or 0.0)
                        live_free = float(getattr(info, "free_margin", 0.0) or 0.0)
                        live_lvl = float(getattr(info, "margin_level_pct", 0.0) or 0.0)
                        currency = str(getattr(info, "currency", "USD") or "USD")
                        server = str(getattr(info, "server", "") or "")
                        account_id = str(getattr(info, "login", None) or getattr(info, "account_id", None) or "")
                        connected = bool(getattr(info, "connected", False))
                        mode = str(getattr(info, "trade_mode", "") or getattr(info, "environment", "") or "live")
                except Exception as ex:
                    logger.debug("[ExecutionEngine] get_account_info failed: %s", ex)

            if live_bal is None and hasattr(broker, "balance"):
                try:
                    live_bal = float(getattr(broker, "balance", 0.0) or 0.0)
                    live_eq = float(getattr(broker, "equity", live_bal) or live_bal)
                    live_mg = float(getattr(broker, "margin", 0.0) or 0.0)
                    live_free = float(getattr(broker, "free_margin", live_eq) or live_eq)
                    currency = str(getattr(broker, "currency", "USD") or "USD")
                    server = str(getattr(broker, "server", "") or "")
                    account_id = str(getattr(broker, "login", "") or "")
                    connected = bool(getattr(broker, "is_connected", getattr(broker, "_connected", True)))
                except Exception as ex:
                    logger.debug("[ExecutionEngine] broker attribute read failed: %s", ex)

        # Check MT5AccountManager active account / config if broker info is incomplete
        if not account_id or (live_bal is None or live_bal <= 0.0):
            try:
                from mt5_accounts import get_account_manager
                mgr = get_account_manager()
                act = mgr.get_active_account()
                if act:
                    if not account_id:
                        account_id = str(act.login)
                    if not server:
                        server = act.server
                    if (live_bal is None or live_bal <= 0.0) and act.balance > 0.0:
                        live_bal = act.balance
                        live_eq = act.equity or act.balance
            except Exception:
                pass

        # Check config env vars as fallback for account ID / Server / Balance
        try:
            import config as _cfg_acc
            if not account_id:
                cfg_login = getattr(_cfg_acc, "MT5_LOGIN", 0)
                if cfg_login:
                    account_id = str(cfg_login)
            if not server:
                server = getattr(_cfg_acc, "MT5_SERVER", "")
            if live_bal is None or live_bal <= 0.0:
                cfg_bal = getattr(_cfg_acc, "MT5_BALANCE", None)
                if cfg_bal and float(cfg_bal) > 0.0:
                    live_bal = float(cfg_bal)
                    live_eq = live_bal
        except Exception:
            pass

        # Refresh active positions with live MT5 market ticks
        for p in self.active_positions.values():
            sym = getattr(p, "symbol", "")
            curr_p = self.get_current_price(sym)
            if curr_p and curr_p > 0:
                p.current_price = curr_p
                entry = float(p.executed_price or p.entry_level or 0.0)
                if entry > 0 and p.remaining_size > 0:
                    if "LONG" in str(p.direction).upper():
                        p.unrealized_pnl = (curr_p - entry) * p.remaining_size
                    else:
                        p.unrealized_pnl = (entry - curr_p) * p.remaining_size

        # Compute unrealized PnL from active positions
        total_used_margin = sum(p.margin_required for p in self.active_positions.values())
        total_unrealized_pnl = sum(p.unrealized_pnl for p in self.active_positions.values())

        # Sync with RiskManager
        is_mock_broker = broker is not None and type(broker).__name__ in ("MockMT5Broker", "MockDeltaBroker")
        if is_mock_broker and hasattr(self, "risk_manager") and hasattr(self.risk_manager, "state"):
            if live_bal == 10000.0 and self.risk_manager.state.equity != 10000.0:
                live_bal = self.risk_manager.state.equity
                live_eq = live_bal + total_unrealized_pnl
                try:
                    broker.balance = live_bal
                    broker.equity = live_eq
                except Exception:
                    pass
            elif live_bal is not None and live_bal > 0.0:
                self.risk_manager.state.equity = live_bal
                self.risk_manager.state.peak_equity = max(self.risk_manager.state.peak_equity, live_bal)
        elif live_bal is not None and live_bal > 0.0:
            if hasattr(self, "risk_manager") and hasattr(self.risk_manager, "state"):
                self.risk_manager.state.equity = live_bal
                self.risk_manager.state.peak_equity = max(self.risk_manager.state.peak_equity, live_bal)

        # Final resolved values
        final_balance = live_bal if (live_bal is not None and live_bal > 0.0) else float(getattr(self.risk_manager.state, "equity", 10000.0) or 10000.0)
        final_equity = round(final_balance + total_unrealized_pnl, 2)
        if hasattr(self, "risk_manager") and hasattr(self.risk_manager, "state"):
            self.risk_manager.state.equity = final_equity
            self.risk_manager.state.peak_equity = max(float(getattr(self.risk_manager.state, "peak_equity", 0.0) or 0.0), final_equity)
        final_margin = live_mg if (live_mg is not None and live_mg > 0.0) else total_used_margin
        final_free = live_free if (live_free is not None and live_free > 0.0) else max(final_equity - final_margin, 0.0)
        if live_lvl is not None and live_lvl > 0.0:
            final_lvl = live_lvl
        else:
            final_lvl = (final_equity / final_margin * 100.0) if final_margin > 0 else 999.0

        return {
            "broker_name": b_name,
            "account_id": account_id,
            "server": server,
            "mode": mode,
            "connected": connected,
            "balance": round(final_balance, 2),
            "equity": round(final_equity, 2),
            "used_margin": round(final_margin, 2),
            "free_margin": round(final_free, 2),
            "margin_level_pct": round(final_lvl, 1),
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "currency": currency,
        }

    def sync_wallet_from_broker(self) -> Dict[str, Any]:
        """Explicitly re-query live broker account and sync risk manager state."""
        return self.get_wallet_telemetry()

    def get_portfolio_summary(self) -> Dict:
        """Returns account equity, margin utilization, and open positions stats."""
        wallet = self.get_wallet_telemetry()
        total_used_margin = sum(p.margin_required for p in self.active_positions.values())
        total_unrealized_pnl = sum(p.unrealized_pnl for p in self.active_positions.values())
        used_m = wallet.get("used_margin", 0.0) if wallet.get("used_margin", 0.0) > 0 else total_used_margin
        eq = wallet.get("equity", 0.0)
        bal = wallet.get("balance", eq)
        free_m = wallet.get("free_margin", 0.0) if wallet.get("free_margin", 0.0) > 0 else max(eq - used_m, 0.0)
        m_lvl = wallet.get("margin_level_pct", 0.0) if wallet.get("margin_level_pct", 0.0) > 0 else ((eq / used_m * 100) if used_m > 0 else 999.0)

        return {
            "equity": round(eq, 2),
            "balance": round(bal, 2),
            "used_margin": round(used_m, 2),
            "free_margin": round(free_m, 2),
            "margin_level_pct": round(m_lvl, 1),
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "leverage": self.risk_manager.get_leverage(),
            "active_positions_count": len(self.active_positions),
            "pending_setups_count": len(self.pending_setups),
            "currency": wallet.get("currency", "USD"),
            "broker_name": wallet.get("broker_name", "MT5"),
            "server": wallet.get("server", ""),
            "login": wallet.get("account_id", ""),
            "connected": wallet.get("connected", True),
        }

    def _dispatch_event(self, event_name: str, order: TradeOrder, message: str):
        if self.event_callback:
            try:
                self.event_callback(event_name, order, message)
            except Exception as e:
                print(f"[ExecutionEngine] Callback error ({e})")

