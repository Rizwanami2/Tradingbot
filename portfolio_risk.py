"""
Multi-Symbol Portfolio Risk
===========================
Tracks open positions, correlation proxy, and max sector exposure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass
class Position:
    symbol: str
    direction: str  # LONG / SHORT
    risk_amount: float
    notional: float
    sector: str = "other"

    def __post_init__(self):
        self.symbol = str(self.symbol).upper().replace("/", "").replace("-", "")
        self.direction = str(self.direction).upper()
        if self.direction in ("BUY", "1"):
            self.direction = "LONG"
        elif self.direction in ("SELL", "-1"):
            self.direction = "SHORT"


SECTOR_MAP = {
    "XAU": "metals", "GOLD": "metals", "XAUT": "metals",
    "BTC": "crypto", "ETH": "crypto", "SOL": "crypto",
    "EUR": "fx", "GBP": "fx", "JPY": "fx", "AUD": "fx", "CAD": "fx", "CHF": "fx",
}


def detect_sector(symbol: str) -> str:
    s = str(symbol).upper()
    for key, sector in SECTOR_MAP.items():
        if key in s:
            return sector
    return "other"


class PortfolioRisk:
    def __init__(
        self,
        max_total_risk_pct: float = 2.0,
        max_sector_risk_pct: float = 1.2,
        max_positions: int = 5,
        equity: float = 10000.0,
    ):
        self.max_total_risk_pct = max_total_risk_pct
        self.max_sector_risk_pct = max_sector_risk_pct
        self.max_positions = max_positions
        self.equity = float(equity) if equity else 10000.0
        self.positions: Dict[str, Position] = {}

    def add(self, pos: Position) -> Tuple[bool, str]:
        # Normalize via dataclass post_init
        if not isinstance(pos, Position):
            return False, "Invalid position object"
        sym = pos.symbol
        if sym in self.positions:
            return False, f"Already have position in {sym}"
        if len(self.positions) >= self.max_positions:
            return False, f"Max positions ({self.max_positions}) reached"

        total_risk = sum(p.risk_amount for p in self.positions.values()) + pos.risk_amount
        if self.equity > 0 and (total_risk / self.equity * 100) > self.max_total_risk_pct:
            return False, f"Total portfolio risk would exceed {self.max_total_risk_pct}%"

        sector = pos.sector if pos.sector and pos.sector != "other" else detect_sector(sym)
        sector_risk = (
            sum(p.risk_amount for p in self.positions.values() if p.sector == sector) + pos.risk_amount
        )
        if self.equity > 0 and (sector_risk / self.equity * 100) > self.max_sector_risk_pct:
            return False, f"Sector '{sector}' risk would exceed {self.max_sector_risk_pct}%"

        for p in self.positions.values():
            if p.sector == sector and p.direction == pos.direction:
                return False, (
                    f"Correlated same-direction exposure already open in {p.symbol} ({sector})"
                )

        pos.sector = sector
        self.positions[sym] = pos
        return True, "OK"

    def remove(self, symbol: str) -> None:
        key = str(symbol).upper().replace("/", "").replace("-", "")
        self.positions.pop(key, None)

    def summary(self) -> Dict:
        total_risk = sum(p.risk_amount for p in self.positions.values())
        by_sector: Dict[str, float] = {}
        for p in self.positions.values():
            by_sector[p.sector] = by_sector.get(p.sector, 0.0) + p.risk_amount
        return {
            "positions": len(self.positions),
            "total_risk": round(total_risk, 2),
            "total_risk_pct": round(total_risk / self.equity * 100, 2) if self.equity else 0,
            "by_sector": {k: round(v, 2) for k, v in by_sector.items()},
            "symbols": list(self.positions.keys()),
        }
