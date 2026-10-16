"""
Self-Learning Edge Layer (57-61)
================================
- Setup tagging
- Time-of-day / day-of-week expectancy map
- Negative example ban-list
- Confidence calibration tracker
- Agent veto power helper
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

EDGE_FILE = "learning_edge.json"


@dataclass
class SetupStats:
    tag: str
    trades: int = 0
    wins: int = 0
    total_r: float = 0.0

    @property
    def wr(self) -> float:
        return self.wins / self.trades * 100 if self.trades else 0.0

    @property
    def exp(self) -> float:
        return self.total_r / self.trades if self.trades else 0.0


class LearningEdge:
    def __init__(self, path: str = EDGE_FILE, ban_after_losses: int = 3):
        self.path = path
        self.ban_after_losses = ban_after_losses
        self.setups: Dict[str, SetupStats] = {}
        # key: "dow:hour" -> {trades, wins, total_r}
        self.time_map: Dict[str, Dict[str, float]] = {}
        # pattern key -> consecutive losses
        self.neg_streak: Dict[str, int] = {}
        self.banned: Dict[str, str] = {}  # pattern -> reason
        # conf bin -> {n, wins}
        self.calibration: Dict[str, Dict[str, float]] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.get("setups", {}).items():
                    self.setups[k] = SetupStats(**v)
                self.time_map = data.get("time_map", {})
                self.neg_streak = data.get("neg_streak", {})
                self.banned = data.get("banned", {})
                self.calibration = data.get("calibration", {})
            except Exception:
                pass

    def _save(self):
        try:
            payload = {
                "setups": {k: {"tag": v.tag, "trades": v.trades, "wins": v.wins, "total_r": round(v.total_r, 4)}
                           for k, v in self.setups.items()},
                "time_map": self.time_map,
                "neg_streak": self.neg_streak,
                "banned": self.banned,
                "calibration": self.calibration,
                "updated": datetime.now(timezone.utc).isoformat(),
            }
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    # ---- setup tagging ----
    @staticmethod
    def auto_tag(signal_meta: Dict[str, Any]) -> str:
        """
        Derive a simple setup tag from signal metadata.
        """
        parts = []
        regime = str(signal_meta.get("regime", "unk")).lower()
        session = str(signal_meta.get("session", "unk")).lower()
        parts.append(regime)
        parts.append(session)
        if signal_meta.get("smc"):
            parts.append("smc")
        if signal_meta.get("bos"):
            parts.append("bos")
        if signal_meta.get("fvg"):
            parts.append("fvg")
        if signal_meta.get("sweep"):
            parts.append("sweep")
        direction = str(signal_meta.get("direction", "")).upper()
        if direction:
            parts.append(direction.lower())
        return "|".join(parts) if parts else "generic"

    def record(
        self,
        tag: str,
        won: bool,
        r: float,
        conf: float = 0.5,
        when: Optional[datetime] = None,
    ):
        when = when or datetime.now(timezone.utc)
        # setup
        if tag not in self.setups:
            self.setups[tag] = SetupStats(tag=tag)
        s = self.setups[tag]
        s.trades += 1
        if won:
            s.wins += 1
        s.total_r += float(r)

        # time map
        key = f"{when.weekday()}:{when.hour:02d}"
        bucket = self.time_map.setdefault(key, {"trades": 0, "wins": 0, "total_r": 0.0})
        bucket["trades"] += 1
        if won:
            bucket["wins"] += 1
        bucket["total_r"] += float(r)

        # negative streak / ban
        if won:
            self.neg_streak[tag] = 0
            self.banned.pop(tag, None)
        else:
            self.neg_streak[tag] = self.neg_streak.get(tag, 0) + 1
            if self.neg_streak[tag] >= self.ban_after_losses:
                self.banned[tag] = f"{self.neg_streak[tag]} consecutive losses"

        # calibration
        bin_key = f"{round(conf * 10) / 10:.1f}"
        cal = self.calibration.setdefault(bin_key, {"n": 0, "wins": 0})
        cal["n"] += 1
        if won:
            cal["wins"] += 1

        self._save()

    def is_banned(self, tag: str) -> Tuple[bool, str]:
        if tag in self.banned:
            return True, f"BAN: {self.banned[tag]}"
        return False, "ok"

    def time_window_ok(self, when: Optional[datetime] = None, min_trades: int = 8) -> Tuple[bool, str]:
        when = when or datetime.now(timezone.utc)
        key = f"{when.weekday()}:{when.hour:02d}"
        b = self.time_map.get(key)
        if not b or b["trades"] < min_trades:
            return True, f"time {key} insufficient samples"
        exp = b["total_r"] / b["trades"]
        if exp < -0.15:
            return False, f"time {key} expectancy {exp:.2f}R — blocked"
        return True, f"time {key} exp {exp:+.2f}R"

    def calibration_report(self) -> str:
        if not self.calibration:
            return "Calibration: no data"
        lines = ["**Confidence Calibration** (predicted → realized)"]
        for k in sorted(self.calibration.keys()):
            c = self.calibration[k]
            if c["n"] < 3:
                continue
            realized = c["wins"] / c["n"] * 100
            lines.append(f"• conf≈{k}: n={int(c['n'])} realized WR={realized:.0f}%")
        return "\n".join(lines) if len(lines) > 1 else "Calibration: need more samples"

    def setup_report(self, top: int = 8) -> str:
        if not self.setups:
            return "Setups: no data"
        ranked = sorted(self.setups.values(), key=lambda s: s.exp, reverse=True)
        lines = ["**Setup Expectancy**"]
        for s in ranked[:top]:
            lines.append(f"• `{s.tag}` n={s.trades} WR={s.wr:.0f}% ExpR={s.exp:+.2f}")
        if self.banned:
            lines.append("\n**Banned patterns:**")
            for k, v in self.banned.items():
                lines.append(f"• `{k}` — {v}")
        return "\n".join(lines)

    def agent_veto_names(self, agent_stats: Dict[str, Any], min_trades: int = 15) -> List[str]:
        """
        Return agent names that should have hard veto (worst expectancy).
        agent_stats: name -> object with .trades and .expectancy
        """
        weak = []
        for name, a in agent_stats.items():
            trades = getattr(a, "trades", 0)
            exp = getattr(a, "expectancy", 0.0)
            if trades >= min_trades and exp < -0.2:
                weak.append(name)
        return weak
