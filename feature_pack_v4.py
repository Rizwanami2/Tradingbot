"""
Feature Pack v4 — Unified surface for suggestions 47-70
=======================================================
    from feature_pack_v4 import FeaturePackV4
    pack = FeaturePackV4()
    ok, reasons, size_mult = pack.full_gate(symbol, direction, df, spread=0.3)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from signal_quality import evaluate_signal_quality
from risk_advanced import RiskAdvanced
from learning_edge import LearningEdge
from ops_advanced import HealthMonitor, build_daily_digest, format_trade_replay, journal_to_csv
from research_helpers import ABBooks

# reuse v3 pieces if present
try:
    from feature_pack import FeaturePack as FeaturePackV3
except Exception:
    FeaturePackV3 = None


class FeaturePackV4:
    def __init__(self, equity: float = 10000.0, paper_mode: bool = True):
        self.risk2 = RiskAdvanced()
        self.edge = LearningEdge()
        self.health = HealthMonitor()
        self.ab = ABBooks()
        self.v3 = FeaturePackV3(equity=equity, paper_mode=paper_mode) if FeaturePackV3 else None
        self.equity = equity
        self.paper_mode = paper_mode
        self.risk2.update_equity(equity)

    def full_gate(
        self,
        symbol: str,
        direction: str,
        df: Optional[pd.DataFrame] = None,
        spread: float = 0.0,
        expected_rr: float = 2.0,
        signal_meta: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, List[str], float]:
        """
        Combined gate: risk tiers (v3) + risk2 + signal quality + time window + ban list.
        Returns (allowed, reasons, size_multiplier)
        """
        reasons: List[str] = []
        size_mult = 1.0

        # v3 risk tiers
        if self.v3:
            ok, reason, mult = self.v3.gate_new_entry()
            if not ok:
                return False, [reason], 0.0
            size_mult *= mult
            reasons.append(reason)

        # risk 2.0
        ok2, msg2, mult2 = self.risk2.gate(symbol, direction)
        if not ok2:
            return False, [msg2], 0.0
        size_mult *= mult2
        reasons.append(msg2)

        # signal quality
        if df is not None:
            q = evaluate_signal_quality(df, direction, spread=spread, expected_rr=expected_rr)
            if not q.allowed:
                return False, q.reasons, 0.0
            reasons.extend(q.reasons)

        # learning edge: time + ban
        meta = signal_meta or {}
        tag = self.edge.auto_tag({**meta, "direction": direction})
        banned, ban_msg = self.edge.is_banned(tag)
        if banned:
            return False, [ban_msg], 0.0
        ok_t, msg_t = self.edge.time_window_ok()
        if not ok_t:
            return False, [msg_t], 0.0
        reasons.append(msg_t)

        return True, reasons, size_mult

    def on_open(self, **kwargs) -> str:
        tid = ""
        if self.v3:
            tid = self.v3.on_open(**kwargs)
        symbol = kwargs.get("symbol", "")
        direction = kwargs.get("direction", "")
        if tid:
            self.risk2.add_position(tid, symbol, direction)
            self.risk2.register_trade_today()
        return tid

    def on_close(
        self,
        trade_id: str,
        exit_price: float,
        r_multiple: Optional[float] = None,
        pnl: Optional[float] = None,
        features=None,
        conf: float = 0.6,
        signal_meta: Optional[Dict] = None,
        notes: str = "",
    ):
        rec = None
        if self.v3:
            rec = self.v3.on_close(
                trade_id, exit_price, r_multiple=r_multiple, pnl=pnl,
                features=features, conf=conf, notes=notes,
            )
        self.risk2.remove_position(trade_id)
        if rec:
            tag = self.edge.auto_tag(signal_meta or {
                "regime": rec.get("regime"), "session": rec.get("session"),
                "direction": rec.get("direction"),
            })
            self.edge.record(
                tag=tag,
                won=bool(rec.get("won")),
                r=float(rec.get("r_multiple") or 0),
                conf=conf,
            )
            # A/B books (both get the outcome for research; in live you'd branch at entry)
            for book in self.ab.books:
                self.ab.record(book, bool(rec.get("won")), float(rec.get("r_multiple") or 0))
            if rec.get("pnl") is not None:
                self.equity += float(rec["pnl"])
                self.risk2.update_equity(self.equity)
        return rec

    def on_bar(self, trade_id: str, price: float, bar_increment: int = 1):
        if self.v3:
            return self.v3.on_bar(trade_id, price, bar_increment)
        return {}

    def digest(self) -> str:
        daily_r = self.v3.risk_tiers.daily_pnl_r() if self.v3 else 0.0
        weekly_r = self.v3.risk_tiers.weekly_pnl_r() if self.v3 else 0.0
        open_n = len(self.v3.positions.positions) if self.v3 else 0
        exp = self.v3.journal.expectancy() if self.v3 else {}
        tier = self.v3.risk_tiers.telegram_line() if self.v3 else ""
        peak = self.v3.state.data.get("peak_equity", self.equity) if self.v3 else self.equity
        try:
            from ai_learner import AILearnerAgent
            learner = AILearnerAgent().summary()
        except Exception:
            learner = ""
        return build_daily_digest(
            equity=self.equity,
            peak=peak,
            daily_r=daily_r,
            weekly_r=weekly_r,
            open_positions=open_n,
            journal_exp=exp,
            tier_line=tier,
            learner_summary=learner,
            health=self.health.status(),
        )

    def replay(self, trade_id: str) -> str:
        if not self.v3:
            return "No journal"
        for e in self.v3.journal.entries:
            if e.get("id") == trade_id or e.get("symbol") == trade_id.upper():
                return format_trade_replay(e)
        return f"Trade `{trade_id}` not found"

    def export_csv(self) -> str:
        if not self.v3:
            return "id,symbol\n"
        return journal_to_csv(self.v3.journal.entries)

    def board(self) -> str:
        base = self.v3.board() if self.v3 else "No v3 pack"
        extra = [
            "",
            self.edge.setup_report(5),
            "",
            self.ab.report(),
            "",
            self.health.status(),
        ]
        return base + "\n" + "\n".join(extra)
