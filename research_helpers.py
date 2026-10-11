"""
Research / Edge Discovery Helpers (67-70)
=========================================
- A/B paper book tracker
- Stress scenario multipliers for Monte Carlo
- Symbol rotation score
- Walk-forward param lock suggestion
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

AB_FILE = "ab_books.json"


@dataclass
class PaperBook:
    name: str
    conviction_threshold: float
    trades: int = 0
    wins: int = 0
    total_r: float = 0.0
    equity: float = 10000.0

    @property
    def expectancy(self) -> float:
        return self.total_r / self.trades if self.trades else 0.0

    @property
    def wr(self) -> float:
        return self.wins / self.trades * 100 if self.trades else 0.0


class ABBooks:
    def __init__(self, path: str = AB_FILE):
        self.path = path
        self.books: Dict[str, PaperBook] = {
            "strict": PaperBook("strict", conviction_threshold=0.75),
            "balanced": PaperBook("balanced", conviction_threshold=0.60),
        }
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.get("books", {}).items():
                    self.books[k] = PaperBook(**v)
            except Exception:
                pass

    def _save(self):
        try:
            payload = {
                "books": {k: {
                    "name": v.name,
                    "conviction_threshold": v.conviction_threshold,
                    "trades": v.trades,
                    "wins": v.wins,
                    "total_r": round(v.total_r, 4),
                    "equity": round(v.equity, 2),
                } for k, v in self.books.items()},
                "updated": datetime.now(timezone.utc).isoformat(),
            }
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    def record(self, book: str, won: bool, r: float, risk_amount: float = 50.0):
        if book not in self.books:
            return
        b = self.books[book]
        b.trades += 1
        if won:
            b.wins += 1
        b.total_r += float(r)
        b.equity += float(r) * risk_amount
        self._save()

    def winner(self, min_trades: int = 20) -> Optional[str]:
        eligible = [b for b in self.books.values() if b.trades >= min_trades]
        if not eligible:
            return None
        return max(eligible, key=lambda b: b.expectancy).name

    def report(self) -> str:
        lines = ["**A/B Paper Books**"]
        for b in self.books.values():
            lines.append(
                f"• `{b.name}` thr={b.conviction_threshold:.0%} n={b.trades} "
                f"WR={b.wr:.0f}% ExpR={b.expectancy:+.2f} eq=${b.equity:,.0f}"
            )
        w = self.winner()
        if w:
            lines.append(f"Leader (≥20 trades): **{w}**")
        return "\n".join(lines)


# Stress scenario multipliers for MC / backtest injection
STRESS_SCENARIOS = {
    "normal": {"spread_mult": 1.0, "slippage_mult": 1.0, "gap_prob": 0.0},
    "high_spread": {"spread_mult": 2.5, "slippage_mult": 1.8, "gap_prob": 0.0},
    "news_spike": {"spread_mult": 3.0, "slippage_mult": 2.5, "gap_prob": 0.08},
    "gap_day": {"spread_mult": 1.5, "slippage_mult": 1.2, "gap_prob": 0.15},
}


def stress_scenario(name: str = "normal") -> Dict[str, float]:
    return dict(STRESS_SCENARIOS.get(name, STRESS_SCENARIOS["normal"]))


def symbol_rotation_scores(
    stats: Dict[str, Dict[str, float]],
    top_n: int = 3,
) -> List[Tuple[str, float]]:
    """
    stats: symbol -> {expectancy, volatility, trades}
    Score = expectancy * (1 / (1 + volatility)) * log(1+trades)
    """
    import math
    scored = []
    for sym, s in stats.items():
        exp = float(s.get("expectancy", 0))
        vol = max(0.01, float(s.get("volatility", 0.2)))
        n = max(1, int(s.get("trades", 1)))
        score = exp * (1.0 / (1.0 + vol)) * math.log1p(n)
        scored.append((sym, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_n]


def param_lock_suggestion(
    current: Dict[str, float],
    candidate: Dict[str, float],
    current_oos_sqn: float,
    candidate_oos_sqn: float,
    min_improvement: float = 0.15,
) -> Tuple[bool, str, Dict[str, float]]:
    """
    Only promote candidate params if OOS SQN improves by min_improvement.
    """
    if candidate_oos_sqn >= current_oos_sqn + min_improvement:
        return True, f"Promote: OOS SQN {current_oos_sqn:.2f} → {candidate_oos_sqn:.2f}", candidate
    return False, f"Keep current (cand OOS {candidate_oos_sqn:.2f} not better enough)", current
