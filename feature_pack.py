"""
Feature Pack v3 — Central wiring of all new modules
===================================================
Import once and get a fully upgraded engine surface.

    from feature_pack import FeaturePack
    pack = FeaturePack(equity=10000)
    allowed, reason, size_mult = pack.gate_new_entry()
    tid = pack.on_open(...)
    pack.on_bar(tid, price)
    pack.on_close(tid, exit_price, features=...)
    print(pack.board())
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from trade_journal import TradeJournal
from position_manager import PositionManager
from risk_tiers import RiskTiers, RiskTiersConfig
from agent_stats import AgentStats
from state_persistence import StateStore


class FeaturePack:
    def __init__(self, equity: float = 10000.0, paper_mode: bool = True):
        self.journal = TradeJournal()
        self.positions = PositionManager()
        self.risk_tiers = RiskTiers()
        self.agent_stats = AgentStats()
        self.state = StateStore()
        self.state.snapshot(equity=equity, paper_mode=paper_mode)
        self.equity = float(equity)
        self.paper_mode = paper_mode

    # ---------- entry gate ----------
    def gate_new_entry(self) -> tuple:
        """Returns (allowed: bool, reason: str, size_multiplier: float)"""
        return self.risk_tiers.allow_new_entry()

    # ---------- open / close lifecycle ----------
    def on_open(
        self,
        symbol: str,
        direction: str,
        entry: float,
        stop: float,
        tp1: float,
        size: float,
        risk_amount: float,
        agents: Optional[List[str]] = None,
        regime: str = "",
        session: str = "",
        score: float = 0.0,
        conviction: float = 0.0,
        tp2: float = 0.0,
    ) -> str:
        tid = self.journal.record_open(
            symbol=symbol,
            direction=direction,
            entry=entry,
            stop=stop,
            tp1=tp1,
            size=size,
            risk_amount=risk_amount,
            agents=agents,
            regime=regime,
            session=session,
            score=score,
            conviction=conviction,
        )
        self.positions.add(
            trade_id=tid,
            symbol=symbol,
            direction=direction,
            entry=entry,
            stop=stop,
            tp1=tp1,
            size=size,
            tp2=tp2,
        )
        self.risk_tiers.add_open_risk(tid, risk_r=1.0)
        self._sync_state()
        return tid

    def on_bar(self, trade_id: str, price: float, bar_increment: int = 1) -> Dict[str, Any]:
        actions = self.positions.update(trade_id, price, bar_increment=bar_increment)
        return actions

    def on_close(
        self,
        trade_id: str,
        exit_price: float,
        r_multiple: Optional[float] = None,
        pnl: Optional[float] = None,
        features=None,
        conf: float = 0.6,
        notes: str = "",
    ) -> Optional[Dict[str, Any]]:
        pos = self.positions.positions.get(trade_id)
        mae = pos.mae if pos else None
        mfe = pos.mfe if pos else None
        hold = pos.bars_held if pos else None

        rec = self.journal.record_close(
            trade_id=trade_id,
            exit_price=exit_price,
            r_multiple=r_multiple,
            pnl=pnl,
            mae=mae,
            mfe=mfe,
            hold_bars=hold,
            features=features,
            conf=conf,
            notes=notes,
            auto_learn=True,
        )
        if rec:
            self.risk_tiers.record_closed(
                r_multiple=float(rec.get("r_multiple") or 0),
                won=bool(rec.get("won")),
            )
            # update equity if paper
            if self.paper_mode and rec.get("pnl") is not None:
                self.equity += float(rec["pnl"])
                self.state.set_equity(self.equity)

        self.positions.remove(trade_id)
        self.risk_tiers.remove_open_risk(trade_id)
        self._sync_state()
        return rec

    def _sync_state(self):
        opens = []
        for p in self.positions.positions.values():
            opens.append({
                "id": p.trade_id,
                "symbol": p.symbol,
                "direction": p.direction,
                "entry": p.entry,
                "stop": p.stop,
                "size": p.size,
                "bars": p.bars_held,
                "mae": p.mae,
                "mfe": p.mfe,
            })
        self.state.set_open_positions(opens)

    # ---------- reporting ----------
    def board(self) -> str:
        """One-command status board for Telegram /board"""
        exp = self.journal.expectancy()
        tier_line = self.risk_tiers.telegram_line()
        agent_line = self.agent_stats.telegram_report()
        pos = self.positions.summary()
        mode = "PAPER" if self.paper_mode else "LIVE"

        lines = [
            f"📡 **Status Board**  [{mode}]",
            f"Equity: `${self.equity:,.2f}` | Peak: `${self.state.data.get('peak_equity', self.equity):,.2f}`",
            tier_line,
            f"Open positions: `{pos['open']}`",
        ]
        for d in pos.get("details", []):
            lines.append(
                f"  • `{d['symbol']}` {d['dir']} bars={d['bars']} "
                f"MAE={d['mae']} MFE={d['mfe']} scale={d['scaled']}"
            )
        lines.append(
            f"Journal: trades=`{exp['trades']}` WR=`{exp['winrate']}%` "
            f"ExpR=`{exp['expectancy_r']}` PnL=`{exp.get('total_pnl', 0):+.2f}`"
        )
        lines.append(agent_line)
        return "\n".join(lines)

    def adapted_weights(self, base: Dict[str, float]) -> Dict[str, float]:
        return self.agent_stats.suggested_weights(base)
