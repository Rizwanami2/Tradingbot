"""
PRACTICAL RISK MANAGER – AUTOTRADE READY
=======================================
Risk + lot-size module for automated trading.
import math

Features:
- Auto-adjust risk (drawdown, loss streak, expectancy)
- RF (Risk Factor) auto-adjust – scales risk & break-even thresholds
- Progressive auto break-even (lock profit → full BE → trail)
- Minimum risk floor + maximum risk cap
- Lot size calculation (standard / mini / micro)
- Instrument profiles (XAUUSD, Forex, Crypto)
- Practical TP ladder (1.5R / 2.5R / 4R)
- Partial TP + breakeven + trailing
- Daily & weekly loss limits
- No max-trades limit
- Autotrade helpers: can_trade, size, lots, TPs, trail, BE, update

Educational only. Not financial advice.
"""

from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Tuple, Any
from enum import Enum
from datetime import date
import json
import os
import math


# ============================================================
# INSTRUMENT PROFILES (for lot sizing)
# ============================================================

INSTRUMENTS = {
    "XAUUSD": {"contract_size": 100, "pip_size": 0.01, "pip_value_per_lot": 1.0, "min_lot": 0.01, "lot_step": 0.01},
    "GOLD":   {"contract_size": 100, "pip_size": 0.01, "pip_value_per_lot": 1.0, "min_lot": 0.01, "lot_step": 0.01},
    "GC=F":   {"contract_size": 100, "pip_size": 0.01, "pip_value_per_lot": 1.0, "min_lot": 0.01, "lot_step": 0.01},
    "XAUTUSD": {"contract_size": 1, "pip_size": 0.01, "pip_value_per_lot": 0.01, "min_lot": 0.01, "lot_step": 0.01},
    "PAXGUSDT": {"contract_size": 1, "pip_size": 0.01, "pip_value_per_lot": 0.01, "min_lot": 0.001, "lot_step": 0.001},
    "PAXG/USDT": {"contract_size": 1, "pip_size": 0.01, "pip_value_per_lot": 0.01, "min_lot": 0.001, "lot_step": 0.001},
    "EURUSD": {"contract_size": 100000, "pip_size": 0.0001, "pip_value_per_lot": 10.0, "min_lot": 0.01, "lot_step": 0.01},
    "GBPUSD": {"contract_size": 100000, "pip_size": 0.0001, "pip_value_per_lot": 10.0, "min_lot": 0.01, "lot_step": 0.01},
    "USDJPY": {"contract_size": 100000, "pip_size": 0.01,   "pip_value_per_lot": 9.0,  "min_lot": 0.01, "lot_step": 0.01},
    "AUDUSD": {"contract_size": 100000, "pip_size": 0.0001, "pip_value_per_lot": 10.0, "min_lot": 0.01, "lot_step": 0.01},
    "USDCAD": {"contract_size": 100000, "pip_size": 0.0001, "pip_value_per_lot": 10.0, "min_lot": 0.01, "lot_step": 0.01},
    "USDCHF": {"contract_size": 100000, "pip_size": 0.0001, "pip_value_per_lot": 10.0, "min_lot": 0.01, "lot_step": 0.01},
    "BTCUSDT": {"contract_size": 1, "pip_size": 0.1, "pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},
    "BTCUSD":  {"contract_size": 1, "pip_size": 0.1, "pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},
    "BTC":     {"contract_size": 1, "pip_size": 0.1, "pip_value_per_lot": 0.1, "min_lot": 0.001, "lot_step": 0.001},
    "XAUUSDT": {"contract_size": 1, "pip_size": 0.01, "pip_value_per_lot": 0.01, "min_lot": 0.01, "lot_step": 0.01},
    "DEFAULT": {"contract_size": 100000, "pip_size": 0.0001, "pip_value_per_lot": 10.0, "min_lot": 0.01, "lot_step": 0.01},
}


class RiskMode(Enum):
    NORMAL = "normal"
    REDUCED = "reduced"
    RECOVERY = "recovery"
    AGGRESSIVE = "aggressive"


@dataclass
class RiskConfig:
    base_risk_pct: float = 0.20
    min_risk_pct: float = 0.10
    max_risk_pct: float = 0.50
    max_daily_loss_pct: float = 3.0
    max_weekly_loss_pct: float = 6.0
    max_consecutive_losses: int = 4
    tp1_r: float = 1.5
    tp2_r: float = 2.5
    tp3_r: float = 4.0
    tp1_close_pct: float = 0.50
    tp2_close_pct: float = 0.30
    trail_after_tp1: bool = True
    trail_atr_mult: float = 2.0
    trail_r_mult: float = 1.5
    # Auto break-even (progressive)
    auto_be: bool = True
    be_at_r: float = 1.0          # move SL to exact BE when price reaches +1R
    be_lock_r: float = 0.5        # early lock: move SL to +0.1R when price reaches +0.5R
    be_lock_offset_r: float = 0.1 # small profit locked at early stage
    be_buffer_r: float = 0.25     # buffer past entry at full BE stage (higher than early lock)
    be_close_pct: float = 0.33    # book 33% profit when SL is moved to break-even
    auto_be_take_profit: bool = True  # book partial profit when auto BE activates
    be_close_stage: int = 1       # stage to book profit (1 = early BE lock, 2 = full BE)
    dd_reduce_at: float = 0.05
    dd_reduce_factor: float = 0.55
    dd_hard_at: float = 0.12
    dd_hard_factor: float = 0.30
    win_streak_boost: float = 1.12
    max_boost_streak: int = 3
    expectancy_boost_above: float = 0.25
    expectancy_cut_below: float = 0.05
    min_rr: float = 1.5
    max_lot: float = 10.0
    min_lot_override: Optional[float] = None
    # Configured leverage cap; supports up to 200x for brokers like Delta Exchange India.
    leverage: float = 5.0
    min_leverage: float = 1.0
    max_leverage: float = 200.0
    max_margin_utilization_pct: float = 85.0
    # RF (Risk Factor) – master multiplier for risk & BE sensitivity
    # 1.0 = normal | <1 safer | >1 more aggressive
    rf: float = 1.0
    rf_min: float = 0.50
    rf_max: float = 1.50
    rf_auto: bool = True              # auto-adjust RF from performance
    rf_up_on_exp: float = 0.05        # raise RF when expectancy is good
    rf_down_on_loss: float = 0.08     # lower RF after consecutive losses
    # Lot Sizing Mode: "auto" (dynamic risk-based) or "manual" (fixed lot size)
    lot_mode: str = "auto"
    manual_lot_size: float = 0.01
    manual_lot_sizes: Dict[str, float] = field(default_factory=dict)


@dataclass
class TradeRiskResult:
    size: float
    lots: float
    risk_amount: float
    risk_pct_used: float
    stop_distance: float
    stop_pips: float
    tp1: float
    tp2: float
    tp3: Optional[float]
    mode: str
    reason: str
    symbol: str = ""
    pip_value: float = 0.0
    leverage: float = 20.0
    margin_required: float = 0.0
    notional_value: float = 0.0
    liquidation_price: Optional[float] = None
    lot_mode: str = "auto"


@dataclass
class RiskState:
    equity: float = 10000.0
    peak_equity: float = 10000.0
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    consecutive_losses: int = 0
    win_streak: int = 0
    last_day: Optional[str] = None
    last_week: Optional[str] = None
    expectancy: float = 0.0
    recent_winrate: float = 0.5
    total_trades: int = 0
    total_wins: int = 0
    mode: str = "normal"
    rf: float = 1.0
    leverage: float = 20.0
    lot_mode: str = "auto"
    manual_lot_size: float = 0.01



_global_risk_manager: Optional["RiskManager"] = None


def get_risk_manager(equity: float = 10000.0, symbol: str = "XAUUSD") -> "RiskManager":
    """Return the global RiskManager singleton instance."""
    global _global_risk_manager
    if _global_risk_manager is None:
        _global_risk_manager = RiskManager(equity=equity, symbol=symbol)
    return _global_risk_manager


class RiskManager:
    def __init__(
        self,
        equity: float = 10000.0,
        symbol: str = "XAUUSD",
        config: Optional[RiskConfig] = None,
        state_file: str = "risk_state.json",
    ):
        self.config = config or RiskConfig()
        self.set_symbol(symbol)
        self.state_file = state_file
        global _global_risk_manager
        _global_risk_manager = self
        try:
            import config
            if hasattr(config, "get_lot_size_mode"):
                self.config.lot_mode = config.get_lot_size_mode()
            if hasattr(config, "get_manual_lot_size"):
                self.config.manual_lot_size = config.get_manual_lot_size()
            if hasattr(config, "MANUAL_LOT_SIZES") and isinstance(config.MANUAL_LOT_SIZES, dict):
                self.config.manual_lot_sizes.update(config.MANUAL_LOT_SIZES)
        except Exception:
            pass

        self.state = RiskState(
            equity=equity,
            peak_equity=equity,
            leverage=self.config.leverage,
            lot_mode=self.config.lot_mode,
            manual_lot_size=self.config.manual_lot_size,
        )
        self._load_state()
        # Clamp persisted settings too: older state files may contain leverage
        # values above the current configured ceiling.
        try:
            persisted_leverage = float(self.state.leverage)
        except (TypeError, ValueError):
            persisted_leverage = self.config.leverage
        if not math.isfinite(persisted_leverage):
            persisted_leverage = self.config.leverage
        self.state.leverage = min(max(persisted_leverage, self.config.min_leverage), self.config.max_leverage)
        self.config.leverage = self.state.leverage
        self.state.equity = equity
        self.state.peak_equity = max(self.state.peak_equity, equity)
        if not getattr(self.state, "rf", None):
            self.state.rf = self.config.rf
        if not getattr(self.state, "leverage", None):
            self.state.leverage = self.config.leverage
        else:
            self.config.leverage = self.state.leverage

    @property
    def equity(self) -> float:
        return float(getattr(self.state, "equity", 10000.0) or 10000.0)

    @equity.setter
    def equity(self, val: float) -> None:
        v = float(val)
        self.state.equity = v
        if v > getattr(self.state, "peak_equity", 0.0):
            self.state.peak_equity = v

    @property
    def initial_equity(self) -> float:
        return float(getattr(self.state, "initial_equity", self.state.equity) or self.state.equity)

    @initial_equity.setter
    def initial_equity(self, val: float) -> None:
        self.state.initial_equity = float(val)

    def sync_equity(self, new_equity: float, new_balance: Optional[float] = None) -> None:
        """Synchronize live broker wallet equity and balance with risk state."""
        eq = float(new_equity)
        self.state.equity = eq
        if eq > getattr(self.state, "peak_equity", 0.0):
            self.state.peak_equity = eq
        try:
            self._save_state()
        except Exception:
            pass

    def set_symbol(self, symbol: str):
        s = (symbol or "").upper().replace("/", "").replace("-", "").strip()
        if s in ("BTC", "BTCUSD", "XBT", "XBTUSD"):
            s = "BTCUSDT"
        elif s in ("GOLD", "XAU", "XAUT", "XAUTUSD", "PAXG", "PAXGUSDT"):
            s = "XAUUSD"
        self.symbol = s
        self.profile = INSTRUMENTS.get(self.symbol, INSTRUMENTS["DEFAULT"])
        if self.symbol not in INSTRUMENTS:
            if any(x in self.symbol for x in ("BTC", "BITCOIN", "XBT")):
                self.profile = INSTRUMENTS.get("BTCUSDT", INSTRUMENTS["DEFAULT"])
            elif "XAU" in self.symbol or "GOLD" in self.symbol or "PAXG" in self.symbol or "XAUT" in self.symbol:
                self.profile = INSTRUMENTS.get("XAUUSD", INSTRUMENTS["DEFAULT"])

    def set_leverage(self, leverage: float) -> Tuple[bool, str]:
        """Set leverage within the configured bounds (1x to 200x)."""
        try:
            lev = float(leverage)
        except (ValueError, TypeError):
            return False, f"Invalid leverage '{leverage}'. Must be a finite number within the configured leverage range."
        if not math.isfinite(lev) or lev < self.config.min_leverage or lev > self.config.max_leverage:
            return False, f"Leverage {lev:.0f}x out of bounds. Must be between {self.config.min_leverage:.0f}x and {self.config.max_leverage:.0f}x."
        self.config.leverage = lev
        self.state.leverage = lev
        self._save_state()
        warn = " ⚠️ High leverage increases liquidation risk; ensure tight stop-loss is placed." if lev > 25.0 else ""
        return True, f"Leverage set to {lev:.0f}x{warn}"

    def auto_set_leverage(
        self,
        symbol: str = "",
        stop_dist_pct: Optional[float] = None,
        max_cap: float = 200.0,
    ) -> Tuple[float, str]:
        """Auto calculate and set leverage up to max 200x based on stop distance and liquidation safety."""
        cap = min(self.config.max_leverage, max_cap, 200.0)
        if stop_dist_pct and stop_dist_pct > 0:
            safety_dist = (stop_dist_pct * 1.25) + 0.005
            target_lev = min(cap, max(1.0, 1.0 / safety_dist))
        else:
            target_lev = min(cap, getattr(self.state, "leverage", self.config.leverage))
        final_lev = round(target_lev)
        self.set_leverage(final_lev)
        return float(final_lev), f"Auto-set leverage to {final_lev:.0f}x (Max {cap:.0f}x)"

    def get_leverage(self) -> float:
        return getattr(self.state, "leverage", self.config.leverage)

    def get_lot_mode(self, symbol: Optional[str] = None) -> Tuple[str, float]:
        """Returns (mode, effective_manual_lot_size). Mode is 'auto' or 'manual'."""
        mode = getattr(self.config, "lot_mode", "auto")
        lot = self.get_manual_lot(symbol)
        return mode, lot

    def get_manual_lot(self, symbol: Optional[str] = None) -> float:
        """Returns manual lot for a given symbol or global default."""
        sym = (symbol or self.symbol or "").upper().replace("/", "").replace("-", "")
        if hasattr(self.config, "manual_lot_sizes") and isinstance(self.config.manual_lot_sizes, dict):
            if sym in self.config.manual_lot_sizes:
                return float(self.config.manual_lot_sizes[sym])
        return float(getattr(self.config, "manual_lot_size", 0.01) or 0.01)

    def set_lot_mode(
        self,
        mode: str,
        lot_size: Optional[float] = None,
        symbol: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Set lot size mode ('auto' or 'manual') and optional manual lot size."""
        m = (mode or "").strip().lower()
        if m in ("auto", "dynamic", "risk"):
            self.config.lot_mode = "auto"
            self.state.lot_mode = "auto"
            self._save_state()
            return True, "Lot sizing mode set to AUTO (dynamic risk-based calculation)."
        elif m in ("manual", "fixed", "man"):
            self.config.lot_mode = "manual"
            self.state.lot_mode = "manual"
            if lot_size is not None:
                ok, msg = self.set_manual_lot(lot_size, symbol=symbol)
                if not ok:
                    return False, msg
            self._save_state()
            curr_lot = self.get_manual_lot(symbol)
            target_str = f" for {symbol.upper()}" if symbol else ""
            return True, f"Lot sizing mode set to MANUAL ({curr_lot:.4f} lots{target_str})."
        else:
            return False, f"Unknown lot mode '{mode}'. Choose 'auto' or 'manual'."

    def set_manual_lot(self, lot: float, symbol: Optional[str] = None) -> Tuple[bool, str]:
        """Set fixed manual lot size (and switch lot mode to manual)."""
        try:
            val = float(lot)
        except (ValueError, TypeError):
            return False, f"Invalid lot size '{lot}'. Must be a positive number."
        if not math.isfinite(val) or val <= 0:
            return False, f"Lot size must be greater than 0 (got {val})."

        if symbol:
            sym_clean = symbol.upper().replace("/", "").replace("-", "")
            p = INSTRUMENTS.get(sym_clean, INSTRUMENTS.get("DEFAULT", self.profile))
            min_lot = p.get("min_lot", 0.01)
            step = p.get("lot_step", 0.01)
            target_str = f" for {sym_clean}"
            target_name = sym_clean
        else:
            sym_clean = None
            p = self.profile
            min_lot = self.config.min_lot_override or p.get("min_lot", 0.01)
            step = p.get("lot_step", 0.01)
            target_str = " globally"
            target_name = self.symbol

        max_lot = self.config.max_lot
        if val < min_lot:
            return False, f"Lot size {val} is below minimum allowed lot ({min_lot}) for {target_name}."
        if val > max_lot:
            return False, f"Lot size {val} exceeds maximum allowed lot ({max_lot})."

        quantized = round(math.floor(val / step) * step, 4)
        if quantized < min_lot:
            quantized = min_lot

        if sym_clean:
            if not hasattr(self.config, "manual_lot_sizes") or not isinstance(self.config.manual_lot_sizes, dict):
                self.config.manual_lot_sizes = {}
            self.config.manual_lot_sizes[sym_clean] = quantized
        else:
            self.config.manual_lot_size = quantized
            self.state.manual_lot_size = quantized

        self.config.lot_mode = "manual"
        self.state.lot_mode = "manual"
        self._save_state()
        return True, f"Manual lot size set to {quantized:.4f} lots{target_str}."

    def set_auto_lot(self) -> Tuple[bool, str]:
        """Switch lot mode to auto."""
        return self.set_lot_mode("auto")


    def calc_margin(self, notional_value: float, leverage: Optional[float] = None) -> float:
        lev = leverage or self.get_leverage()
        return notional_value / max(lev, 1.0)

    def calc_liquidation_price(
        self,
        direction: str,
        entry: float,
        leverage: Optional[float] = None,
        maintenance_margin_pct: float = 0.005,
        slippage_buffer_pct: float = 0.002,
    ) -> float:
        lev = leverage or self.get_leverage()
        if lev <= 1.0:
            return 0.0 if direction.lower() in ("long", "buy", "1") else entry * 2.0

        # Scale maintenance margin and slippage buffer so they do not exceed initial margin capacity
        init_rate = 1.0 / lev
        mm_rate = min(maintenance_margin_pct, init_rate * 0.5)
        buffer_rate = min(slippage_buffer_pct, init_rate * 0.1)

        is_long = direction.lower() in ("long", "buy", "1")
        if is_long:
            liq = entry * (1.0 - init_rate + mm_rate + buffer_rate)
            return max(round(liq, 4), 0.0)
        else:
            liq = entry * (1.0 + init_rate - mm_rate - buffer_rate)
            return round(liq, 4)

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.items():
                    if hasattr(self.state, k):
                        setattr(self.state, k, v)
            except Exception:
                pass
        if not hasattr(self.state, "total_wins") or self.state.total_wins is None:
            self.state.total_wins = 0
        if not hasattr(self.state, "total_trades") or self.state.total_trades is None:
            self.state.total_trades = 0
        if getattr(self.state, "lot_mode", None):
            self.config.lot_mode = self.state.lot_mode
        if getattr(self.state, "manual_lot_size", None):
            try:
                self.config.manual_lot_size = float(self.state.manual_lot_size)
            except (ValueError, TypeError):
                pass


    def _save_state(self):
        try:
            tmp = f"{self.state_file}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(asdict(self.state), f, indent=2)
            os.replace(tmp, self.state_file)
        except Exception:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(asdict(self.state), f, indent=2)

    def _check_period_reset(self):
        today = date.today().isoformat()
        week = date.today().strftime("%Y-W%W")
        if self.state.last_day != today:
            self.state.last_day = today
            self.state.daily_pnl = 0.0
        if self.state.last_week != week:
            self.state.last_week = week
            self.state.weekly_pnl = 0.0

    def current_risk_pct(self) -> Tuple[float, str]:
        self._check_period_reset()
        cfg = self.config
        s = self.state
        risk = cfg.base_risk_pct
        reasons = []

        if s.peak_equity > 0:
            dd = (s.peak_equity - s.equity) / s.peak_equity
            if dd >= cfg.dd_hard_at:
                risk *= cfg.dd_hard_factor
                reasons.append(f"hard-DD {dd:.1%}")
                s.mode = RiskMode.RECOVERY.value
            elif dd >= cfg.dd_reduce_at:
                risk *= cfg.dd_reduce_factor
                reasons.append(f"soft-DD {dd:.1%}")
                s.mode = RiskMode.REDUCED.value
            else:
                s.mode = RiskMode.NORMAL.value

        if s.consecutive_losses >= 2:
            risk *= 0.70
            reasons.append(f"{s.consecutive_losses} losses")
            s.mode = RiskMode.RECOVERY.value

        if s.win_streak > 0 and s.mode == RiskMode.NORMAL.value:
            boost = cfg.win_streak_boost ** min(s.win_streak, cfg.max_boost_streak)
            risk *= boost
            reasons.append(f"streak x{boost:.2f}")

        if s.expectancy >= cfg.expectancy_boost_above and s.mode == RiskMode.NORMAL.value:
            risk *= 1.10
            reasons.append("good-exp")
            s.mode = RiskMode.AGGRESSIVE.value
        elif s.expectancy < cfg.expectancy_cut_below:
            risk *= 0.75
            reasons.append("low-exp")

        # Apply RF (Risk Factor)
        rf = getattr(s, "rf", cfg.rf) if cfg.rf_auto else cfg.rf
        rf = max(cfg.rf_min, min(cfg.rf_max, rf))
        s.rf = rf
        risk *= rf
        if abs(rf - 1.0) > 0.01:
            reasons.append(f"RF×{rf:.2f}")

        risk = max(cfg.min_risk_pct, min(cfg.max_risk_pct, risk))
        return round(risk, 4), (" | ".join(reasons) if reasons else "base")

    def can_trade(self) -> Tuple[bool, str]:
        self._check_period_reset()
        s = self.state
        cfg = self.config
        if s.consecutive_losses >= cfg.max_consecutive_losses:
            return False, f"{s.consecutive_losses} consecutive losses – pause"
        if s.equity > 0 and (-s.daily_pnl / s.equity * 100) >= cfg.max_daily_loss_pct:
            return False, "daily loss limit hit"
        if s.equity > 0 and (-s.weekly_pnl / s.equity * 100) >= cfg.max_weekly_loss_pct:
            return False, "weekly loss limit hit"
        return True, "ok"

    def reset_daily_risk(self) -> Dict[str, Any]:
        """
        Manually reset daily loss counter, consecutive losses, and restore trading status.
        """
        self._check_period_reset()
        self.state.daily_pnl = 0.0
        self.state.consecutive_losses = 0
        if self.state.peak_equity > 0:
            dd = (self.state.peak_equity - self.state.equity) / self.state.peak_equity
            if dd >= self.config.dd_hard_at:
                self.state.peak_equity = max(self.state.equity, 0.0)
                self.state.mode = RiskMode.NORMAL.value
            elif dd >= self.config.dd_reduce_at:
                self.state.mode = RiskMode.REDUCED.value
            else:
                self.state.mode = RiskMode.NORMAL.value
        else:
            self.state.peak_equity = max(self.state.equity, 0.0)
            self.state.mode = RiskMode.NORMAL.value
        if getattr(self.state, "rf", 1.0) < 1.0:
            self.state.rf = 1.0
        self._save_state()
        can, why = self.can_trade()
        return {
            "daily_pnl": self.state.daily_pnl,
            "weekly_pnl": self.state.weekly_pnl,
            "consecutive_losses": self.state.consecutive_losses,
            "equity": self.state.equity,
            "peak_equity": self.state.peak_equity,
            "mode": self.state.mode,
            "rf": getattr(self.state, "rf", 1.0),
            "can_trade": can,
            "reason": why,
        }

    def reset_weekly_risk(self) -> Dict[str, Any]:
        """
        Manually reset weekly loss counter, daily loss counter, consecutive losses, and restore trading status.
        """
        self._check_period_reset()
        self.state.daily_pnl = 0.0
        self.state.weekly_pnl = 0.0
        self.state.consecutive_losses = 0
        if self.state.peak_equity > 0:
            dd = (self.state.peak_equity - self.state.equity) / self.state.peak_equity
            if dd >= self.config.dd_hard_at:
                self.state.peak_equity = max(self.state.equity, 0.0)
                self.state.mode = RiskMode.NORMAL.value
            elif dd >= self.config.dd_reduce_at:
                self.state.mode = RiskMode.REDUCED.value
            else:
                self.state.mode = RiskMode.NORMAL.value
        else:
            self.state.peak_equity = max(self.state.equity, 0.0)
            self.state.mode = RiskMode.NORMAL.value
        if getattr(self.state, "rf", 1.0) < 1.0:
            self.state.rf = 1.0
        self._save_state()
        can, why = self.can_trade()
        return {
            "daily_pnl": self.state.daily_pnl,
            "weekly_pnl": self.state.weekly_pnl,
            "consecutive_losses": self.state.consecutive_losses,
            "equity": self.state.equity,
            "peak_equity": self.state.peak_equity,
            "mode": self.state.mode,
            "rf": getattr(self.state, "rf", 1.0),
            "can_trade": can,
            "reason": why,
        }

    def reset_all_risk(self, new_equity: Optional[float] = None) -> Dict[str, Any]:
        """
        Full manual risk reset: clears daily & weekly losses, consecutive losses,
        resets peak_equity to current equity (clearing historical drawdown lock),
        sets mode to normal, and resets RF to 1.0.
        """
        self._check_period_reset()
        if new_equity is not None and new_equity > 0:
            self.state.equity = float(new_equity)
        self.state.peak_equity = max(self.state.equity, 0.0)
        self.state.daily_pnl = 0.0
        self.state.weekly_pnl = 0.0
        self.state.consecutive_losses = 0
        self.state.win_streak = 0
        self.state.mode = RiskMode.NORMAL.value
        self.state.rf = 1.0
        self._save_state()
        can, why = self.can_trade()
        return {
            "equity": self.state.equity,
            "peak_equity": self.state.peak_equity,
            "daily_pnl": self.state.daily_pnl,
            "weekly_pnl": self.state.weekly_pnl,
            "consecutive_losses": self.state.consecutive_losses,
            "mode": self.state.mode,
            "rf": self.state.rf,
            "can_trade": can,
            "reason": why,
        }

    def calc_lots(self, risk_amount: float, stop_distance: float) -> Tuple[float, float, float]:
        p = self.profile
        pip_size = p["pip_size"]
        pip_value = p["pip_value_per_lot"]
        min_lot = self.config.min_lot_override or p["min_lot"]
        step = p["lot_step"]
        max_lot = self.config.max_lot

        if stop_distance <= 0 or pip_size <= 0:
            return 0.0, 0.0, pip_value

        stop_pips = stop_distance / pip_size
        if stop_pips <= 0 or pip_value <= 0:
            return 0.0, stop_pips, pip_value

        if getattr(self.config, "lot_mode", "auto") == "manual":
            raw_lot = self.get_manual_lot(self.symbol)
            lots = math.floor(raw_lot / step) * step
            lots = max(min_lot, min(max_lot, lots))
            if lots < min_lot:
                lots = 0.0
            return round(lots, 4), round(stop_pips, 2), pip_value

        lots = risk_amount / (stop_pips * pip_value)
        lots = math.floor(lots / step) * step
        lots = max(min_lot, min(max_lot, lots))
        if lots < min_lot:
            lots = 0.0
        return round(lots, 4), round(stop_pips, 2), pip_value

    def _adapt_profile_for_equity(self) -> None:
        """Scale instrument profile for small paper accounts (e.g. $100)."""
        eq = float(self.state.equity or 0)
        p = dict(self.profile)
        sym = (self.symbol or "").upper()
        if eq <= 0:
            return
        if eq < 500:
            if any(x in sym for x in ("XAU", "GOLD", "GC")):
                p["contract_size"] = 1
                p["pip_value_per_lot"] = 0.01
                p["min_lot"] = 0.01
                p["lot_step"] = 0.01
            if any(x in sym for x in ("BTC", "BITCOIN", "XBT")):
                p["min_lot"] = min(float(p.get("min_lot", 0.001)), 0.001)
                p["lot_step"] = min(float(p.get("lot_step", 0.001)), 0.001)
        if eq < 200 and p.get("contract_size", 100) == 1:
            p["min_lot"] = min(float(p.get("min_lot", 0.01)), 0.001)
            p["lot_step"] = min(float(p.get("lot_step", 0.01)), 0.001)
        self.profile = p

    def position_size(
        self,
        entry: float,
        stop: float,
        equity: Optional[float] = None,
        symbol: Optional[str] = None,
    ) -> TradeRiskResult:
        if isinstance(equity, str) and symbol is None:
            symbol, equity = equity, None
        if symbol:
            self.set_symbol(symbol)
        if equity is not None:
            try:
                equity = float(equity)
            except (TypeError, ValueError):
                equity = None
        if equity is not None:
            self.state.equity = equity
            try:
                self.state.peak_equity = max(float(self.state.peak_equity or 0), float(equity))
            except (TypeError, ValueError):
                self.state.peak_equity = float(equity)

        self._adapt_profile_for_equity()
        ok, why = self.can_trade()
        if not ok:
            return TradeRiskResult(
                size=0.0, lots=0.0, risk_amount=0.0, risk_pct_used=0.0,
                stop_distance=0.0, stop_pips=0.0, tp1=0.0, tp2=0.0, tp3=None,
                mode=self.state.mode, reason=f"blocked: {why}", symbol=self.symbol
            )

        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            return TradeRiskResult(
                size=0.0, lots=0.0, risk_amount=0.0, risk_pct_used=0.0,
                stop_distance=0.0, stop_pips=0.0, tp1=0.0, tp2=0.0, tp3=None,
                mode=self.state.mode, reason="invalid stop", symbol=self.symbol
            )

        is_manual = getattr(self.config, "lot_mode", "auto") == "manual"
        risk_pct, reason = self.current_risk_pct()
        if is_manual:
            reason = f"manual lot ({self.get_manual_lot(self.symbol)})"
        risk_amount = self.state.equity * (risk_pct / 100.0)
        lots, stop_pips, pip_value = self.calc_lots(risk_amount, stop_dist)
        if is_manual:
            risk_amount = round(lots * stop_pips * pip_value, 2)
            risk_pct = round((risk_amount / self.state.equity) * 100.0, 2) if self.state.equity > 0 else 0.0

        contract_size = self.profile.get("contract_size", 100)
        unit_size = round(lots * contract_size, 6) if (lots > 0 and contract_size > 0) else ((risk_amount / stop_dist) if stop_dist > 0 else 0.0)
        direction = "long" if entry > stop else "short"
        tps = self.take_profits(entry, stop, direction)

        lev = self.get_leverage()
        notional_value = round(lots * contract_size * entry, 2)
        margin_required = round(self.calc_margin(notional_value, lev), 2)

        # Enforce leverage margin capacity
        max_margin = self.state.equity * (self.config.max_margin_utilization_pct / 100.0)
        if margin_required > max_margin and entry > 0 and contract_size > 0:
            affordable_notional = max_margin * lev
            affordable_lots = affordable_notional / (contract_size * entry)
            step = self.profile["lot_step"]
            min_lot = self.config.min_lot_override or self.profile["min_lot"]
            if affordable_lots < min_lot:
                import math as _m
                micro_step = min(step, 0.001)
                if affordable_lots < micro_step:
                    return TradeRiskResult(
                        size=0.0, lots=0.0, risk_amount=0.0, risk_pct_used=0.0,
                        stop_distance=round(stop_dist, 6), stop_pips=stop_pips,
                        tp1=tps["tp1"], tp2=tps["tp2"], tp3=tps.get("tp3"),
                        mode=self.state.mode, reason=f"blocked: required margin exceeds capacity (min {min_lot} lots)",
                        symbol=self.symbol, pip_value=pip_value, leverage=lev,
                        margin_required=margin_required, notional_value=notional_value,
                    )
                lots = max(micro_step, _m.floor(max(affordable_lots, 0) / micro_step) * micro_step)
                lots = round(lots, 6)
                reason = (reason + " | micro-size").strip(" |")
            else:
                lots = max(min_lot, math.floor(affordable_lots / step) * step)
                lots = round(lots, 4)
            notional_value = round(lots * contract_size * entry, 2)
            margin_required = round(self.calc_margin(notional_value, lev), 2)
            if contract_size > 0 and lots > 0:
                unit_size = round(lots * contract_size, 6)

        liq_price = self.calc_liquidation_price(direction, entry, lev)
        if direction == "long" and liq_price > 0 and stop <= liq_price:
            reason += f" | SL ({stop}) <= Liq ({liq_price:.2f})"
        elif direction == "short" and stop >= liq_price:
            reason += f" | SL ({stop}) >= Liq ({liq_price:.2f})"

        return TradeRiskResult(
            size=round(unit_size, 6),
            lots=lots,
            risk_amount=round(risk_amount, 2),
            risk_pct_used=risk_pct,
            stop_distance=round(stop_dist, 6),
            stop_pips=stop_pips,
            tp1=tps["tp1"],
            tp2=tps["tp2"],
            tp3=tps.get("tp3"),
            mode=self.state.mode,
            reason=reason,
            symbol=self.symbol,
            pip_value=pip_value,
            leverage=lev,
            margin_required=margin_required,
            notional_value=notional_value,
            liquidation_price=liq_price,
            lot_mode="manual" if is_manual else "auto",
        )

    def take_profits(self, entry: float, stop: float, direction: str = "long") -> Dict[str, float]:
        risk = abs(entry - stop)
        cfg = self.config
        if direction.lower() in ("long", "buy", "1"):
            return {
                "tp1": round(entry + risk * cfg.tp1_r, 6),
                "tp2": round(entry + risk * cfg.tp2_r, 6),
                "tp3": round(entry + risk * cfg.tp3_r, 6),
                "tp1_r": cfg.tp1_r, "tp2_r": cfg.tp2_r, "tp3_r": cfg.tp3_r,
                "tp1_close_pct": cfg.tp1_close_pct, "tp2_close_pct": cfg.tp2_close_pct,
            }
        return {
            "tp1": round(entry - risk * cfg.tp1_r, 6),
            "tp2": round(entry - risk * cfg.tp2_r, 6),
            "tp3": round(entry - risk * cfg.tp3_r, 6),
            "tp1_r": cfg.tp1_r, "tp2_r": cfg.tp2_r, "tp3_r": cfg.tp3_r,
            "tp1_close_pct": cfg.tp1_close_pct, "tp2_close_pct": cfg.tp2_close_pct,
        }

    def meets_min_rr(self, entry: float, stop: float, tp: float) -> bool:
        risk = abs(entry - stop)
        reward = abs(tp - entry)
        if risk <= 0:
            return False
        return (reward / risk) >= self.config.min_rr

    def auto_breakeven_stop(
        self,
        direction: str,
        entry: float,
        current_stop: float,
        highest: float,
        lowest: float,
        initial_risk: float,
    ) -> Tuple[float, str]:
        """
        Progressive auto break-even:
          1) At +be_lock_r (default 0.5R) → lock small profit (entry ± be_lock_offset_r)
          2) At +be_at_r (default 1.0R)   → full BE with small buffer
        Returns (new_stop, reason). Never loosens the stop.
        """
        if not self.config.auto_be or initial_risk <= 0:
            return current_stop, ""

        cfg = self.config
        is_long = direction.lower() in ("long", "buy", "1")
        # Favourable excursion in R
        if is_long:
            mfe = (highest - entry) / initial_risk
        else:
            mfe = (entry - lowest) / initial_risk

        # RF scales BE sensitivity: higher RF → earlier BE (more protective when aggressive)
        rf = getattr(self.state, "rf", cfg.rf)
        rf = max(cfg.rf_min, min(cfg.rf_max, rf))
        be_at = cfg.be_at_r / max(rf, 0.5)          # aggressive RF → BE sooner
        be_lock = cfg.be_lock_r / max(rf, 0.5)

        new_stop = current_stop
        reason = ""

        # Stage 3: secure profit lock when MFE >= 1.4R (approaching or reaching TP1)
        if mfe >= 1.4:
            lock_profit_r = max(0.5, cfg.be_buffer_r + 0.25)
            if is_long:
                target = entry + initial_risk * lock_profit_r
                if target > new_stop:
                    new_stop = target
                    reason = f"Auto BE profit lock @ {mfe:.2f}R (+{lock_profit_r}R)"
            else:
                target = entry - initial_risk * lock_profit_r
                if target < new_stop:
                    new_stop = target
                    reason = f"Auto BE profit lock @ {mfe:.2f}R (+{lock_profit_r}R)"

        # Stage 2: full breakeven (+ buffer) when MFE >= be_at (RF-adjusted)
        elif mfe >= be_at:
            if is_long:
                target = entry + initial_risk * cfg.be_buffer_r
                if target > new_stop:
                    new_stop = target
                    reason = f"Auto BE @ {mfe:.2f}R (full)"
            else:
                target = entry - initial_risk * cfg.be_buffer_r
                if target < new_stop:
                    new_stop = target
                    reason = f"Auto BE @ {mfe:.2f}R (full)"

        # Stage 1: early lock when MFE >= be_lock_r (only if not already past full BE)
        elif mfe >= be_lock:
            if is_long:
                target = entry + initial_risk * cfg.be_lock_offset_r
                if target > new_stop:
                    new_stop = target
                    reason = f"Auto BE lock @ {mfe:.2f}R (+{cfg.be_lock_offset_r}R)"
            else:
                target = entry - initial_risk * cfg.be_lock_offset_r
                if target < new_stop:
                    new_stop = target
                    reason = f"Auto BE lock @ {mfe:.2f}R (+{cfg.be_lock_offset_r}R)"

        return new_stop, reason

    def trailing_stop(
        self, direction: str, entry: float, current_stop: float,
        highest: float, lowest: float, initial_risk: float,
        atr: Optional[float] = None, tp1_hit: bool = False,
    ) -> float:
        # Always apply progressive auto-BE first
        be_stop, _ = self.auto_breakeven_stop(
            direction, entry, current_stop, highest, lowest, initial_risk
        )
        current_stop = be_stop

        if not tp1_hit and not self.config.trail_after_tp1:
            return current_stop

        cfg = self.config
        dist = (atr * cfg.trail_atr_mult) if (atr and atr > 0) else (initial_risk * cfg.trail_r_mult)
        if direction.lower() in ("long", "buy", "1"):
            new_stop = highest - dist
            # After TP1, guarantee floor is at least full BE with buffer
            floor = (entry + initial_risk * cfg.be_buffer_r) if tp1_hit else current_stop
            mfe_r = (highest - entry) / initial_risk if initial_risk > 0 else 0.0
            if mfe_r >= 2.0:
                floor = max(floor, entry + initial_risk * (mfe_r * 0.50))
            return max(current_stop, floor, new_stop)
        new_stop = lowest + dist
        ceiling = (entry - initial_risk * cfg.be_buffer_r) if tp1_hit else current_stop
        mfe_r = (entry - lowest) / initial_risk if initial_risk > 0 else 0.0
        if mfe_r >= 2.0:
            ceiling = min(ceiling, entry - initial_risk * (mfe_r * 0.50))
        return min(current_stop, ceiling, new_stop)

    def manage_position(
        self, direction: str, entry: float, stop: float, tp1: float, tp2: Optional[float],
        remaining_pct: float, highest: float, lowest: float, high: float, low: float,
        initial_risk: float, atr: Optional[float] = None, partials_done: int = 0,
    ) -> Dict:
        actions = {
            "close_pct": 0.0, "exit_price": 0.0, "new_stop": stop,
            "partials_done": partials_done, "fully_closed": False, "reason": "",
            "highest": max(highest, high), "lowest": min(lowest, low),
            "be_applied": False,
        }
        is_long = direction.lower() in ("long", "buy", "1")
        highest = actions["highest"]
        lowest = actions["lowest"]

        # --- Progressive auto break-even (runs every tick/candle) ---
        be_stop, be_reason = self.auto_breakeven_stop(
            direction, entry, stop, highest, lowest, initial_risk
        )
        if be_reason:
            stop = be_stop
            actions["new_stop"] = stop
            actions["be_applied"] = True
            actions["reason"] = be_reason

        # TP1
        if partials_done == 0:
            hit = (high >= tp1) if is_long else (low <= tp1)
            if hit:
                actions["close_pct"] = self.config.tp1_close_pct
                actions["exit_price"] = tp1
                # Move to BE with buffer on TP1
                buf = initial_risk * self.config.be_buffer_r
                actions["new_stop"] = (entry + buf) if is_long else (entry - buf)
                actions["partials_done"] = 1
                actions["be_applied"] = True
                actions["reason"] = "TP1 hit → partial + auto BE"
                return actions

        # TP2
        if partials_done == 1 and tp2 is not None:
            hit = (high >= tp2) if is_long else (low <= tp2)
            if hit:
                actions["close_pct"] = self.config.tp2_close_pct
                actions["exit_price"] = tp2
                actions["partials_done"] = 2
                actions["new_stop"] = self.trailing_stop(
                    direction, entry, stop, highest, lowest, initial_risk, atr, True
                )
                actions["reason"] = "TP2 hit → partial + trail"
                return actions

        # Trail after TP1 / after full BE
        if partials_done >= 1 or actions["be_applied"]:
            stop = self.trailing_stop(
                direction, entry, stop, highest, lowest, initial_risk, atr,
                tp1_hit=(partials_done >= 1),
            )
            actions["new_stop"] = stop

        # Stop hit
        hit_stop = (low <= stop) if is_long else (high >= stop)
        if hit_stop:
            actions["close_pct"] = remaining_pct
            actions["exit_price"] = stop
            actions["fully_closed"] = True
            actions["reason"] = "Stop hit"
            return actions

        actions["new_stop"] = stop
        return actions

    def update_after_trade(self, pnl: float, r_multiple: float = 0.0):
        self._check_period_reset()
        s = self.state
        s.equity += pnl
        s.peak_equity = max(s.peak_equity, s.equity)
        s.daily_pnl += pnl
        s.weekly_pnl += pnl
        s.total_trades += 1
        if pnl > 0:
            s.consecutive_losses = 0
            s.win_streak += 1
            s.total_wins += 1
        else:
            s.consecutive_losses += 1
            s.win_streak = 0
        if s.total_trades > 0:
            s.recent_winrate = s.total_wins / s.total_trades
            s.expectancy = (r_multiple * 0.3 + s.expectancy * 0.7) if s.expectancy else r_multiple * 0.5

        # Auto-adjust RF (Risk Factor)
        cfg = self.config
        if cfg.rf_auto:
            rf = getattr(s, "rf", cfg.rf)
            if pnl > 0 and s.expectancy >= cfg.expectancy_boost_above:
                rf = min(cfg.rf_max, rf + cfg.rf_up_on_exp)
            elif s.consecutive_losses >= 2:
                rf = max(cfg.rf_min, rf - cfg.rf_down_on_loss)
            elif pnl < 0:
                rf = max(cfg.rf_min, rf - cfg.rf_down_on_loss * 0.5)
            s.rf = round(rf, 3)

        self._save_state()

    def autotrade_plan(
        self, entry: float, stop: float, direction: str = "long",
        equity: Optional[float] = None, symbol: Optional[str] = None,
    ) -> Dict:
        res = self.position_size(entry, stop, equity=equity, symbol=symbol)
        if res.lots <= 0 and res.size <= 0:
            return {"ok": False, "reason": res.reason, "lots": 0.0, "size": 0.0}
        if not self.meets_min_rr(entry, stop, res.tp1):
            return {"ok": False, "reason": f"TP1 RR < {self.config.min_rr}", "lots": 0.0, "size": 0.0}
        if res.liquidation_price and res.liquidation_price > 0:
            if direction.lower() in ("long", "buy", "1") and stop <= res.liquidation_price:
                return {
                    "ok": False,
                    "reason": f"Stop Loss (${stop:,.2f}) is below Liquidation Price (${res.liquidation_price:,.2f})",
                    "lots": 0.0,
                    "size": 0.0,
                }
            elif direction.lower() in ("short", "sell", "-1") and stop >= res.liquidation_price:
                return {
                    "ok": False,
                    "reason": f"Stop Loss (${stop:,.2f}) is above Liquidation Price (${res.liquidation_price:,.2f})",
                    "lots": 0.0,
                    "size": 0.0,
                }
        return {
            "ok": True,
            "symbol": res.symbol,
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "lots": res.lots,
            "size": res.size,
            "risk_amount": res.risk_amount,
            "risk_pct": res.risk_pct_used,
            "stop_pips": res.stop_pips,
            "leverage": res.leverage,
            "margin_required": res.margin_required,
            "notional_value": res.notional_value,
            "liquidation_price": res.liquidation_price,
            "tp1": res.tp1,
            "tp2": res.tp2,
            "tp3": res.tp3,
            "tp1_close_pct": self.config.tp1_close_pct,
            "tp2_close_pct": self.config.tp2_close_pct,
            "mode": res.mode,
            "reason": res.reason,
            "lot_mode": getattr(res, "lot_mode", "auto"),
        }

    def summary(self) -> str:
        risk, reason = self.current_risk_pct()
        ok, why = self.can_trade()
        s = self.state
        dd = (s.peak_equity - s.equity) / s.peak_equity * 100 if s.peak_equity else 0
        rf = getattr(s, "rf", self.config.rf)
        lev = getattr(s, "leverage", self.config.leverage)
        lot_str = f"Lot:{self.config.lot_mode.upper()}"
        if self.config.lot_mode == "manual":
            lot_str += f"({self.get_manual_lot():.2f})"
        return (
            f"Eq:{s.equity:.2f} Peak:{s.peak_equity:.2f} DD:{dd:.1f}% | "
            f"Risk:{risk:.2f}% RF×{rf:.2f} Lev:{lev:.0f}x ({reason}) {lot_str} Mode:{s.mode} | "
            f"DayPnL:{s.daily_pnl:.2f} | CanTrade:{ok} ({why}) | Symbol:{self.symbol}"
        )

    def kelly_position_size(
        self,
        entry: float,
        stop: float,
        equity: Optional[float] = None,
        symbol: Optional[str] = None,
        fraction_mult: float = 0.25,
    ) -> Any:
        """Calculates dynamic lot size using the Fractional Kelly Criterion."""
        from kelly_criterion import calculate_kelly_lot_size
        eq = float(equity if equity is not None else self.state.equity)
        win_rate = float(getattr(self.state, "recent_winrate", 0.55) or 0.55)
        if hasattr(self, "win_rate"):
            try:
                wr = self.win_rate()
                if wr and wr > 0:
                    win_rate = float(wr)
            except Exception:
                pass
        win_rate = max(0.10, min(win_rate, 0.95))
        payoff = float(getattr(self.config, "tp1_r", 1.80) or 1.80)
        return calculate_kelly_lot_size(
            equity=eq,
            entry_price=entry,
            stop_loss_price=stop,
            win_rate=win_rate,
            payoff_ratio=payoff,
            fraction_mult=fraction_mult,
            max_leverage=self.get_leverage(),
        )

    def portfolio_var(self, returns: Optional[Any] = None, confidence_level: float = 0.99, horizon_days: int = 1) -> Any:
        """Calculates 95% or 99% portfolio Value at Risk (VaR) and CVaR."""
        from kelly_criterion import compute_value_at_risk
        if returns is None:
            returns = [t.get("pnl", 0.0) / max(self.state.equity, 1.0) for t in getattr(self, "trades", [])]
        return compute_value_at_risk(
            returns=returns,
            portfolio_equity=self.state.equity,
            confidence_level=confidence_level,
            horizon_days=horizon_days,
        )


def lots_for_risk(equity: float, risk_pct: float, entry: float, stop: float, symbol: str = "XAUUSD") -> float:
    if equity <= 0 or risk_pct <= 0:
        return 0.0
    profile = INSTRUMENTS.get(symbol.upper(), INSTRUMENTS["DEFAULT"])
    risk_amount = equity * (risk_pct / 100.0)
    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return 0.0
    stop_pips = stop_dist / profile["pip_size"]
    if stop_pips <= 0 or profile["pip_value_per_lot"] <= 0:
        return 0.0
    raw_lots = risk_amount / (stop_pips * profile["pip_value_per_lot"])
    step = profile["lot_step"]
    lots = math.floor(raw_lots / step) * step
    if lots < profile["min_lot"]:
        return profile["min_lot"] if raw_lots > 0 else 0.0
    return min(10.0, round(lots, 4))


if __name__ == "__main__":
    print("=" * 62)
    print("  RISK MANAGER – Auto BE + RF + Lot Size")
    print("=" * 62)

    rm = RiskManager(equity=10000.0, symbol="XAUUSD")
    plan = rm.autotrade_plan(entry=2650.0, stop=2640.0, direction="long")
    print("\n--- Autotrade Plan (XAUUSD) ---")
    for k, v in plan.items():
        print(f"  {k:<16}: {v}")

    print("\n--- State ---")
    print(rm.summary())

    # Demo progressive auto break-even
    print("\n--- Auto Break-Even demo (long, risk=10) ---")
    entry, stop, risk = 2650.0, 2640.0, 10.0
    # price moves to +0.5R
    be1, r1 = rm.auto_breakeven_stop("long", entry, stop, highest=2655.0, lowest=2640.0, initial_risk=risk)
    print(f"  At +0.5R (high=2655): stop → {be1:.2f}  [{r1}]")
    # price moves to +1.0R
    be2, r2 = rm.auto_breakeven_stop("long", entry, be1, highest=2660.0, lowest=2640.0, initial_risk=risk)
    print(f"  At +1.0R (high=2660): stop → {be2:.2f}  [{r2}]")

    rm.update_after_trade(pnl=37.5, r_multiple=1.5)
    print("\n--- After +1.5R win (RF may rise) ---")
    print(rm.summary())

    rm.update_after_trade(pnl=-25.0, r_multiple=-1.0)
    rm.update_after_trade(pnl=-25.0, r_multiple=-1.0)
    print("\n--- After 2 losses (RF & risk auto-reduced) ---")
    print(rm.summary())
    plan2 = rm.autotrade_plan(entry=2650.0, stop=2640.0)
    print(f"  New lots: {plan2.get('lots')} | risk %: {plan2.get('risk_pct')} | {plan2.get('reason')}")

    print("\nRisk manager ready for autotrade.")
