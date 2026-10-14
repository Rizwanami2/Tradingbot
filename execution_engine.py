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

import time
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from risk_manager import RiskManager


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

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


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
    ):
        self.risk_manager = risk_manager
        self.event_callback = event_callback
        if broker is not None:
            self.broker = broker
        else:
            try:
                from execution import create_broker
                import config
                self.broker = create_broker(
                    broker_mode=getattr(config, "BROKER_MODE", "paper"),
                    exchange_id=getattr(config, "DEFAULT_EXCHANGE", "binance"),
                    api_key=getattr(config, "EXCHANGE_API_KEY", ""),
                    api_secret=getattr(config, "EXCHANGE_API_SECRET", ""),
                    initial_balance=self.risk_manager.state.equity,
                    mt5_mode=getattr(config, "MT5_MODE", "paper"),
                    mt5_login=getattr(config, "MT5_LOGIN", 0),
                    mt5_password=getattr(config, "MT5_PASSWORD", ""),
                    mt5_server=getattr(config, "MT5_SERVER", "MetaQuotes-Demo"),
                    mt5_path=getattr(config, "MT5_PATH", ""),
                    mt5_magic=getattr(config, "MT5_MAGIC", 112233),
                    mt5_gateway_url=getattr(config, "MT5_GATEWAY_URL", ""),
                )
            except Exception as e:
                import config
                mode = getattr(config, "BROKER_MODE", "paper").lower()
                if mode in ("live", "mt5_live"):
                    raise
                try:
                    from execution import PaperBroker
                    self.broker = PaperBroker(initial_balance=self.risk_manager.state.equity)
                except Exception:
                    self.broker = None
        self.pending_setups: Dict[str, TradeOrder] = {}
        self.active_positions: Dict[str, TradeOrder] = {}
        self.trade_history: List[TradeOrder] = []
        self._order_counter: int = 0
        # Prevent duplicate orders after any ambiguous broker response.
        self.execution_halted: bool = False
        try:
            import config as _cfg_max
            self.max_open_trades = int(getattr(_cfg_max, "MAX_OPEN_TRADES", 5) or 5)
        except Exception:
            self.max_open_trades = 5
        self.scalp_mode: bool = False
        self.fast_market_fill: bool = False
        self.scalp_tp_usd: float = 2.50
        self.scalp_sl_usd: float = 3.00
        self.scalp_tp_pct: float = 0.10
        self.scalp_sl_pct: float = 0.12

    def reset_halt(self) -> None:
        """Clear execution halt state so setups can be registered again."""
        self.execution_halted = False

    def open_trade_count(self) -> int:
        return len(self.active_positions) + len(self.pending_setups)

    def slots_available(self) -> int:
        return max(0, int(getattr(self, "max_open_trades", 5)) - self.open_trade_count())

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
            raise RuntimeError("Execution halted after uncertain broker state; reconcile positions before re-enabling.")
        symbol = sig["symbol"].upper()
        import config
        if hasattr(config, "is_trading_allowed") and not config.is_trading_allowed(symbol):
            raise ValueError(f"Trading {symbol} is disabled. Only Gold (XAUUSD) and Bitcoin (BTCUSDT) are permitted.")
        already = symbol in self.active_positions or symbol in self.pending_setups
        if not already and self.open_trade_count() >= int(getattr(self, "max_open_trades", 5)):
            raise RuntimeError(
                f"Max concurrent trades reached ({getattr(self, 'max_open_trades', 5)})."
            )
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
        )

        self.pending_setups[symbol] = order
        self._dispatch_event("SETUP_REGISTERED", order, f"Setup registered @ {entry:.2f}. Waiting for trigger.")
        return order

    def cancel_setup(self, symbol: str, reason: str = "User cancelled") -> Optional[TradeOrder]:
        """Manually cancels a pending setup before it executes."""
        sym = symbol.upper()
        if sym in self.pending_setups:
            setup = self.pending_setups[sym]
            setup.status = OrderStatus.CANCELLED
            setup.close_reason = reason
            setup.closed_at = datetime.now(timezone.utc).isoformat()
            self.trade_history.append(setup)
            del self.pending_setups[sym]
            msg = f"🚫 **SETUP CANCELLED** ({sym}): {reason}"
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
        just_executed = False
        if sym in self.pending_setups:
            setup = self.pending_setups[sym]
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
                del self.pending_setups[sym]
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
            else:
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
                        import config
                        if getattr(config, "BROKER_MODE", "paper").lower() == "live":
                            raise RuntimeError("Live entries are blocked until exchange-side SL/TP placement and verification are implemented.")
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
                            _hm = str(getattr(_cfg_halt, "BROKER_MODE", "paper")).lower()
                        except Exception:
                            _hm = "paper"
                        if _hm == "live":
                            self.execution_halted = True
                        setup.status = OrderStatus.CANCELLED
                        setup.close_reason = f"Execution failed: {ex}"
                        setup.closed_at = datetime.now(timezone.utc).isoformat()
                        self.trade_history.append(setup)
                        self.pending_setups.pop(sym, None)
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
                        self.active_positions[sym] = setup
                        del self.pending_setups[sym]
                        just_executed = True
                        ev = {
                            "event": "TRADE_EXECUTED", "order": setup, "price": exec_price,
                            "message": ((
                                f"✅ **PAPER TRADE CONFIRMED**\n"
                                f"`{setup.symbol}` **{setup.direction}** @ `${exec_price:,.4f}`\n"
                                f"SL `${setup.stop_loss:,.2f}` | TP1 `${setup.tp1:,.2f}` | Lots `{setup.lots}`\n"
                                f"_Paper mode — no real funds risked._"
                            )),
                        }
                        events.append(ev)
                        self._dispatch_event(ev["event"], setup, ev["message"])
        # ----------------------------------------------------
        # 2. Manage Open Positions
        # ----------------------------------------------------
        if sym in self.active_positions:
            pos = self.active_positions[sym]
            pos.current_price = curr_price
            is_long = pos.direction == "LONG"
            entry = pos.executed_price or pos.entry_level
            initial_risk = abs(entry - pos.stop_loss)

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
            # 1. Micro Scalp Take Profit (Fast Capital Accumulation)
            if scalp_on and pos.unrealized_pnl > 0:
                hit_pct = False
                if scalp_pct > 0 and entry > 0:
                    move_pct = abs(curr_price - entry) / entry * 100.0
                    hit_pct = move_pct >= scalp_pct
                hit_usd = scalp_usd > 0 and pos.unrealized_pnl >= scalp_usd
                if hit_usd or hit_pct:
                    pos.exit_price = curr_price
                    pos.realized_pnl = pos.unrealized_pnl
                    pos.status = OrderStatus.CLOSED
                    pos.closed_at = datetime.now(timezone.utc).isoformat()
                    pos.close_reason = f"Scalp TP (${pos.realized_pnl:+.2f})"
                    try:
                        self.risk_manager.update_after_trade(pos.realized_pnl, r_multiple=0.0)
                    except Exception:
                        pass
                    self.trade_history.append(pos)
                    del self.active_positions[sym]
                    if getattr(self, "broker", None):
                        try:
                            self.broker.close_position(sym, price=curr_price)
                        except Exception:
                            pass
                    msg = (
                        f"⚡ **SCALP AUTO-CLOSE** `{sym}`\n"
                        f"Exit `${curr_price:,.2f}` | PnL `${pos.realized_pnl:+,.2f}`\n"
                        f"_Fast scalp profit target hit_"
                    )
                    events.append({"event": "SCALP_CLOSE", "order": pos, "price": curr_price, "message": msg})
                    self._dispatch_event("SCALP_CLOSE", pos, msg)
                    return events

            # 2. Micro Scalp Stop Loss (Fast Loss Cut to Preserve Capital)
            if scalp_on and pos.unrealized_pnl < 0:
                hit_sl_pct = False
                if scalp_sl_pct > 0 and entry > 0:
                    move_pct = abs(curr_price - entry) / entry * 100.0
                    hit_sl_pct = move_pct >= scalp_sl_pct
                hit_sl_usd = scalp_sl_usd > 0 and pos.unrealized_pnl <= -scalp_sl_usd
                if hit_sl_usd or hit_sl_pct:
                    pos.exit_price = curr_price
                    pos.realized_pnl = pos.unrealized_pnl
                    pos.status = OrderStatus.CLOSED
                    pos.closed_at = datetime.now(timezone.utc).isoformat()
                    pos.close_reason = f"Scalp SL (${pos.realized_pnl:+.2f})"
                    try:
                        self.risk_manager.update_after_trade(pos.realized_pnl, r_multiple=-1.0)
                    except Exception:
                        pass
                    self.trade_history.append(pos)
                    del self.active_positions[sym]
                    if getattr(self, "broker", None):
                        try:
                            self.broker.close_position(sym, price=curr_price)
                        except Exception:
                            pass
                    msg = (
                        f"🛡️ **SCALP STOP LOSS CUT** `{sym}`\n"
                        f"Exit `${curr_price:,.2f}` | PnL `${pos.realized_pnl:+,.2f}`\n"
                        f"_Tight micro stop loss protection triggered_"
                    )
                    events.append({"event": "SCALP_SL", "order": pos, "price": curr_price, "message": msg})
                    self._dispatch_event("SCALP_SL", pos, msg)
                    return events

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
                    self.risk_manager.update_after_trade(pos.realized_pnl, r_multiple=-1.0)
                    self.trade_history.append(pos)
                    del self.active_positions[sym]

                    ev = {
                        "event": "TRADE_LIQUIDATED",
                        "order": pos,
                        "exit_price": pos.liquidation_price,
                        "final_pnl": pos.realized_pnl,
                        "message": (
                            f"💥 **POSITION LIQUIDATED** ({pos.symbol})\n"
                            f"Liquidation Price Hit: `${pos.liquidation_price:,.2f}`\n"
                            f"Leverage: `{pos.leverage:.0f}x` | Margin Lost: `${pos.margin_required:,.2f}`"
                        ),
                    }
                    events.append(ev)
                    self._dispatch_event(ev["event"], pos, ev["message"])
                    return events

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
                    ev = {
                        "event": "AUTO_BE_LOCKED",
                        "order": pos,
                        "new_stop": be_stop,
                        "message": (
                            f"🛡️ **AUTO BREAK-EVEN LOCKED** ({pos.symbol})\n"
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

                if getattr(self, "broker", None):
                    try:
                        self.broker.close_position(pos.symbol, qty=round(pos.lots * close_fraction, 4), price=pos.tp1)
                    except Exception as ex:
                        print(f"[ExecutionEngine] Broker TP1 close: {ex}")

                ev = {
                    "event": "TP1_PARTIAL_TAKEN",
                    "order": pos,
                    "exit_price": pos.tp1,
                    "realized_pnl": partial_pnl,
                    "message": (
                        f"🎯 **TARGET 1 HIT (+1.5R)** ({pos.symbol})\n"
                        f"50% closed @ `${pos.tp1:,.2f}` | PnL: `+${partial_pnl:,.2f}`\n"
                        f"Stop Loss secured at Break-Even (`${pos.stop_loss:,.2f}`).\n"
                        f"Remaining lots: `{pos.remaining_lots}` trailing towards TP2."
                    ),
                }
                events.append(ev)
                self._dispatch_event(ev["event"], pos, ev["message"])

            
            # (C2) Structure-based trailing after TP1
            if pos.partials_done >= 1 and sym in self.active_positions:
                try:
                    from execution_enhancements import apply_structure_trail
                    atr_proxy = abs((pos.executed_price or pos.entry_level) - pos.stop_loss) / 1.2
                    # Build minimal OHLC window from recent highs/lows tracked on the position
                    import pandas as _pd
                    _df = _pd.DataFrame({
                        "high": [pos.highest_price, high],
                        "low": [pos.lowest_price, low],
                        "close": [curr_price, curr_price],
                        "open": [curr_price, curr_price],
                    })
                    # Prefer external candle history if caller attached it
                    hist = candle.get("_history") if isinstance(candle, dict) else None
                    if hist is not None and len(hist) >= 20:
                        _df = hist
                    trail_ev = apply_structure_trail(pos, _df, atr_value=float(atr_proxy or 0))
                    if trail_ev:
                        events.append(trail_ev)
                        self._dispatch_event(trail_ev["event"], pos, trail_ev["message"])
                except Exception as _trail_ex:
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

                if getattr(self, "broker", None):
                    try:
                        self.broker.close_position(pos.symbol, qty=round(pos.lots * close_fraction, 4), price=pos.tp2)
                    except Exception as ex:
                        print(f"[ExecutionEngine] Broker TP2 close: {ex}")

                ev = {
                    "event": "TP2_PARTIAL_TAKEN",
                    "order": pos,
                    "exit_price": pos.tp2,
                    "realized_pnl": partial_pnl,
                    "message": (
                        f"🚀 **TARGET 2 HIT (+2.5R)** ({pos.symbol})\n"
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
                # Entry candle range must not force an instant stop-out after paper fill
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
                final_pnl = (exit_price - entry) * pos.remaining_size if is_long else (entry - exit_price) * pos.remaining_size
                pos.realized_pnl += final_pnl
                pos.exit_price = exit_price
                pos.status = OrderStatus.CLOSED
                pos.closed_at = datetime.now(timezone.utc).isoformat()
                pos.close_reason = "Stop Loss / Break-Even Exit"

                if getattr(self, "broker", None):
                    try:
                        self.broker.close_position(pos.symbol, price=exit_price)
                    except Exception as ex:
                        print(f"[ExecutionEngine] Broker exit close: {ex}")

                r_mult = pos.realized_pnl / (initial_risk * pos.size) if (initial_risk * pos.size) > 0 else 0.0
                self.risk_manager.update_after_trade(pos.realized_pnl, r_multiple=r_mult)

                self.trade_history.append(pos)
                del self.active_positions[sym]

                ev = {
                    "event": "TRADE_CLOSED",
                    "order": pos,
                    "exit_price": exit_price,
                    "final_pnl": pos.realized_pnl,
                    "message": (
                        f"🏁 **TRADE CLOSED** ({pos.symbol})\n"
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
        """Manually close an active position immediately."""
        sym = symbol.upper()
        if sym not in self.active_positions:
            return None

        pos = self.active_positions[sym]
        is_long = pos.direction == "LONG"
        entry = pos.executed_price or pos.entry_level
        final_pnl = (current_price - entry) * pos.remaining_size if is_long else (entry - current_price) * pos.remaining_size

        pos.realized_pnl += final_pnl
        pos.exit_price = current_price
        pos.status = OrderStatus.CLOSED
        pos.closed_at = datetime.now(timezone.utc).isoformat()
        pos.close_reason = reason

        initial_risk = abs(entry - pos.stop_loss)
        r_mult = pos.realized_pnl / (initial_risk * pos.size) if (initial_risk * pos.size) > 0 else 0.0
        self.risk_manager.update_after_trade(pos.realized_pnl, r_multiple=r_mult)

        self.trade_history.append(pos)
        del self.active_positions[sym]

        if getattr(self, "broker", None):
            try:
                self.broker.close_position(sym, price=current_price)
            except Exception as ex:
                print(f"[ExecutionEngine] Broker manual close: {ex}")

        msg = (
            f"⏹️ **MANUAL CLOSE EXECUTED** ({sym})\n"
            f"Exit: `${current_price:,.2f}` | PnL: `${pos.realized_pnl:+,.2f}`\n"
            f"Reason: {reason}"
        )
        self._dispatch_event("MANUAL_CLOSE", pos, msg)
        return pos

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
            cp = getattr(self.active_positions[sym], "current_price", None)
            if cp and float(cp) > 0:
                return float(cp)
        if sym in self.pending_setups:
            cp = getattr(self.pending_setups[sym], "current_price", None)
            if cp and float(cp) > 0:
                return float(cp)
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
                if sym in self.active_positions:
                    self.active_positions[sym].current_price = price
                if sym in self.pending_setups:
                    self.pending_setups[sym].current_price = price
                return price
        except Exception:
            pass
        # 4. Fallback to executed_price or entry_level
        if sym in self.active_positions:
            pos = self.active_positions[sym]
            return float(pos.executed_price or pos.entry_level or 0.0)
        if sym in self.pending_setups:
            return float(self.pending_setups[sym].entry_level or 0.0)
        return None

    def close_position(self, symbol: str, current_price: Optional[float] = None, reason: str = "Manual close") -> Optional[TradeOrder]:
        """Convenience method to close an active position immediately, resolving current price if omitted."""
        sym = symbol.upper()
        if sym not in self.active_positions:
            return None
        if current_price is None:
            current_price = self.get_current_price(sym)
            if current_price is None or current_price <= 0:
                pos = self.active_positions[sym]
                current_price = pos.executed_price or pos.entry_level
        return self.close_manually(sym, current_price, reason=reason)


    def close_all_positions(self, reason: str = "Close all") -> list:
        closed = []
        for sym in list(self.active_positions.keys()):
            try:
                pos = self.close_position(sym, reason=reason)
                if pos is not None:
                    closed.append(pos)
            except Exception as ex:
                print(f"[ExecutionEngine] close_all {sym}: {ex}")
        return closed

    def format_positions_detail(self) -> str:
        max_trades = int(getattr(self, "max_open_trades", 5) or 5)
        slots = self.slots_available()
        lines = [f"📋 **Active Positions ({len(self.active_positions)}/{max_trades} active | {slots} slots free)**"]
        if not self.active_positions:
            lines.append("_No open active positions._")
        else:
            for sym, pos in self.active_positions.items():
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

                lines.append(
                    f"\n{dir_icon} `{sym}` **{pos.direction}**\n"
                    f"• **Entry:** `{entry_fmt}` | **Current Market:** `{curr_fmt}` | **PnL:** {pnl_icon} `${u:+,.2f}`{pnl_pct_str}\n"
                    f"• **SL:** `${float(pos.stop_loss):,.2f}` | **TP1:** `${float(pos.tp1):,.2f}` | **TP2:** `${float(pos.tp2):,.2f}`\n"
                    f"• **Lots:** `{pos.lots}` | **Lev:** `{float(pos.leverage or 1):.0f}x` | **Margin:** `${float(pos.margin_required or 0):,.2f}`"
                )
        if self.pending_setups:
            lines.append(f"\n⏳ **Pending Setups ({len(self.pending_setups)}):**")
            for psym, pord in self.pending_setups.items():
                pdir_icon = "🟢" if "LONG" in str(pord.direction).upper() else "🔴"
                pcurr_p = self.get_current_price(psym)
                market_str = ""
                if pcurr_p and pcurr_p > 0:
                    pcurr_fmt = f"${pcurr_p:,.2f}" if pcurr_p >= 1.0 else f"${pcurr_p:,.4f}"
                    dist_pct = ((pord.entry_level - pcurr_p) / pcurr_p * 100.0)
                    market_str = f" | **Current Market:** `{pcurr_fmt}` ({dist_pct:+.2f}% to fill)"
                lines.append(
                    f"• {pdir_icon} `{psym}` **{pord.direction}** @ Entry `${pord.entry_level:,.2f}`{market_str} | SL `${pord.stop_loss:,.2f}` (Lots `{pord.lots}`)"
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
