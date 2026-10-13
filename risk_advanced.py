"""
Risk 2.0 (52-56)
================
- Pyramid only on strength
- Correlation heat / hard block
- Equity-curve position sizing (anti-martingale)
- Overnight / weekend risk cut flag
- Max daily trade count
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, time
from typing import Dict, List, Optional, Tuple

STATE_FILE = "risk_advanced_state.json"


@dataclass
class CorrPosition:
    symbol: str
    direction: str
    sector: str = "other"


# simple static correlation groups (can be replaced by live matrix later)
CORR_GROUPS = {
    "metals": ["XAU", "GOLD", "XAG", "SILVER"],
    "crypto": ["BTC", "ETH", "SOL"],
    "fx_euro": ["EUR", "GBP", "CHF"],
    "fx_risk": ["AUD", "NZD"],
    "fx_safe": ["JPY", "USD"],
}


def _sector_of(symbol: str) -> str:
    s = symbol.upper()
    for name, keys in CORR_GROUPS.items():
        if any(k in s for k in keys):
            return name
    return "other"


class RiskAdvanced:
    def __init__(
        self,
        max_daily_trades: int = 10,
        corr_block_threshold: float = 0.75,  # conceptual; we use same-group same-dir
        pyramid_min_r: float = 0.8,
        size_mult_after_dd: float = 0.6,
        size_mult_after_high: float = 1.15,
        dd_trigger_pct: float = 4.0,
    ):
        self.max_daily_trades = max_daily_trades
        self.corr_block_threshold = corr_block_threshold
        self.pyramid_min_r = pyramid_min_r
        self.size_mult_after_dd = size_mult_after_dd
        self.size_mult_after_high = size_mult_after_high
        self.dd_trigger_pct = dd_trigger_pct
        self.open: Dict[str, CorrPosition] = {}
        self.daily_count: Dict[str, int] = {}  # YYYY-MM-DD -> count
        self.equity_peak: float = 10000.0
        self.equity: float = 10000.0
        self._load()

    def _today(self) -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.daily_count = data.get("daily_count", {})
                self.equity = float(data.get("equity", 10000))
                self.equity_peak = float(data.get("equity_peak", self.equity))
                for k, v in data.get("open", {}).items():
                    self.open[k] = CorrPosition(**v)
            except Exception:
                pass

    def _save(self):
        try:
            payload = {
                "daily_count": self.daily_count,
                "equity": self.equity,
                "equity_peak": self.equity_peak,
                "open": {k: {"symbol": v.symbol, "direction": v.direction, "sector": v.sector}
                         for k, v in self.open.items()},
            }
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    # ---- daily trade cap ----
    def can_take_trade_today(self) -> Tuple[bool, str]:
        d = self._today()
        n = self.daily_count.get(d, 0)
        if n >= self.max_daily_trades:
            return False, f"max daily trades ({self.max_daily_trades}) reached"
        return True, f"trades today {n}/{self.max_daily_trades}"

    def register_trade_today(self):
        d = self._today()
        self.daily_count[d] = self.daily_count.get(d, 0) + 1
        self._save()

    # ---- correlation ----
    def correlation_ok(self, symbol: str, direction: str) -> Tuple[bool, str]:
        sector = _sector_of(symbol)
        direction = direction.upper()
        for p in self.open.values():
            if p.sector == sector and p.direction == direction and sector != "other":
                return False, f"correlated {p.symbol} already {p.direction} in {sector}"
        return True, "corr ok"

    def add_position(self, trade_id: str, symbol: str, direction: str):
        self.open[trade_id] = CorrPosition(
            symbol=symbol.upper(), direction=direction.upper(), sector=_sector_of(symbol)
        )
        self._save()

    def remove_position(self, trade_id: str):
        self.open.pop(trade_id, None)
        self._save()

    # ---- equity-curve sizing ----
    def update_equity(self, equity: float):
        self.equity = float(equity)
        self.equity_peak = max(self.equity_peak, self.equity)
        self._save()

    def size_multiplier(self) -> float:
        if self.equity_peak <= 0:
            return 1.0
        dd = (self.equity_peak - self.equity) / self.equity_peak * 100
        if dd >= self.dd_trigger_pct:
            return self.size_mult_after_dd
        if self.equity >= self.equity_peak * 0.995:  # near high
            return self.size_mult_after_high
        return 1.0

    # ---- pyramid ----
    def allow_pyramid(self, current_r: float, structure_ok: bool = True) -> Tuple[bool, str]:
        if current_r < self.pyramid_min_r:
            return False, f"need ≥{self.pyramid_min_r}R to pyramid (have {current_r:.2f}R)"
        if not structure_ok:
            return False, "structure not intact"
        return True, "pyramid allowed"

    # ---- overnight / weekend ----
    def overnight_risk_cut_needed(self, utc_now: Optional[datetime] = None) -> Tuple[bool, str]:
        now = utc_now or datetime.now(timezone.utc)
        # Friday after 20:00 UTC → weekend risk
        if now.weekday() == 4 and now.time() >= time(20, 0):
            return True, "Friday post-20:00 UTC — reduce/close before weekend"
        # Sunday before 22:00 UTC thin liquidity
        if now.weekday() == 6 and now.time() < time(22, 0):
            return True, "Sunday pre-open thin liquidity"
        return False, "session ok"

    def gate(self, symbol: str, direction: str) -> Tuple[bool, str, float]:
        """Combined gate before new entry. Returns (ok, reason, size_mult)."""
        ok_d, msg_d = self.can_take_trade_today()
        if not ok_d:
            return False, msg_d, 0.0
        ok_c, msg_c = self.correlation_ok(symbol, direction)
        if not ok_c:
            return False, msg_c, 0.0
        cut, msg_cut = self.overnight_risk_cut_needed()
        if cut:
            return False, msg_cut, 0.0
        mult = self.size_multiplier()
        return True, f"{msg_d}; {msg_c}; size×{mult:.2f}", mult
