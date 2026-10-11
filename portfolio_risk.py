"""
Multi-Symbol Portfolio Risk
===========================
Tracks open positions, correlation proxy, and max sector exposure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Position:
    symbol: str
    direction: str  # LONG / SHORT
    risk_amount: float
    notional: float
    sector: str = "other"


SECTOR_MAP = {
    "XAU": "metals", "GOLD": "metals", "XAUT": "metals",
    "BTC": "crypto", "ETH": "crypto", "SOL": "crypto",
    "EUR": "fx", "GBP": "fx", "JPY": "fx", "AUD": "fx", "CAD": "fx", "CHF": "fx",
}


def detect_sector(symbol: str) -> str:
    s = symbol.upper()
    for key, sector in SECTOR_MAP.items():
        if key in s:
            return sector
    return "other"


class PortfolioRisk:
    def __init__(self, max_total_risk_pct: float = 2.0, max_sector_risk_pct: float = 1.2,
                 max_positions: int = 5, equity: float = 10000.0):
        self.max_total_risk_pct = max_total_risk_pct
        self.max_sector_risk_pct = max_sector_risk_pct
        self.max_positions = max_positions
        self.equity = equity
        self.positions: Dict[str, Position] = {}

    def add(self, pos: Position) -> Tuple[bool, str]:
        if pos.symbol in self.positions:
            return False, f"Already have position in {pos.symbol}"
        if len(self.positions) >= self.max_positions:
            return False, f"Max positions ({self.max_positions}) reached"

        total_risk = sum(p.risk_amount for p in self.positions.values()) + pos.risk_amount
        if self.equity > 0 and (total_risk / self.equity * 100) > self.max_total_risk_pct:
            return False, f"Total portfolio risk would exceed {self.max_total_risk_pct}%"

        sector = pos.sector or detect_sector(pos.symbol)
        sector_risk = sum(p.risk_amount for p in self.positions.values() if p.sector == sector) + pos.risk_amount
        if self.equity > 0 and (sector_risk / self.equity * 100) > self.max_sector_risk_pct:
            return False, f"Sector '{sector}' risk would exceed {self.max_sector_risk_pct}%"

        # Simple same-direction correlation block for same sector
        for p in self.positions.values():
            if p.sector == sector and p.direction == pos.direction:
                return False, f"Correlated same-direction exposure already open in {p.symbol} ({sector})"

        pos.sector = sector
        self.positions[pos.symbol] = pos
        return True, "OK"

    def remove(self, symbol: str):
        self.positions.pop(symbol.upper(), None)

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
