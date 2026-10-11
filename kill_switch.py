"""
Multi-Layer Kill Switch
=======================
Halts all new entries (and optionally closes positions) when triggered.
Triggers: manual flag, equity drawdown, consecutive losses, file flag.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

KILL_FLAG_FILE = ".kill_switch"


@dataclass
class KillState:
    active: bool = False
    reason: str = ""
    triggered_at: str = ""
    source: str = ""  # manual | drawdown | losses | file


class KillSwitch:
    def __init__(self, max_drawdown_pct: float = 8.0, max_consecutive_losses: int = 5,
                 flag_file: str = KILL_FLAG_FILE):
        self.max_drawdown_pct = max_drawdown_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.flag_file = flag_file
        self.state = KillState()

    def _set(self, reason: str, source: str):
        self.state = KillState(
            active=True,
            reason=reason,
            triggered_at=datetime.now(timezone.utc).isoformat(),
            source=source,
        )

    def check_file_flag(self) -> bool:
        if os.path.exists(self.flag_file):
            self._set(f"Flag file present: {self.flag_file}", "file")
            return True
        return False

    def check_drawdown(self, equity: float, peak_equity: float) -> bool:
        if peak_equity <= 0:
            return False
        dd = (peak_equity - equity) / peak_equity * 100
        if dd >= self.max_drawdown_pct:
            self._set(f"Drawdown {dd:.1f}% >= limit {self.max_drawdown_pct}%", "drawdown")
            return True
        return False

    def check_losses(self, consecutive_losses: int) -> bool:
        if consecutive_losses >= self.max_consecutive_losses:
            self._set(f"{consecutive_losses} consecutive losses", "losses")
            return True
        return False

    def activate_manual(self, reason: str = "Manual kill"):
        self._set(reason, "manual")
        try:
            with open(self.flag_file, "w") as f:
                f.write(reason)
        except Exception:
            pass

    def deactivate(self):
        self.state = KillState()
        if os.path.exists(self.flag_file):
            try:
                os.remove(self.flag_file)
            except Exception:
                pass

    def is_active(self, equity: Optional[float] = None, peak: Optional[float] = None,
                  consecutive_losses: int = 0) -> Tuple[bool, str]:
        if self.state.active:
            return True, f"KILL ACTIVE ({self.state.source}): {self.state.reason}"
        if self.check_file_flag():
            return True, f"KILL ACTIVE (file): {self.state.reason}"
        if equity is not None and peak is not None and self.check_drawdown(equity, peak):
            return True, f"KILL ACTIVE (drawdown): {self.state.reason}"
        if self.check_losses(consecutive_losses):
            return True, f"KILL ACTIVE (losses): {self.state.reason}"
        return False, "ok"
