"""
State Persistence — equity + open positions snapshot
====================================================
Survives Railway restarts. Call snapshot() after any equity or position change.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

STATE_FILE = "bot_state.json"


class StateStore:
    def __init__(self, path: str = STATE_FILE):
        self.path = path
        self.data: Dict[str, Any] = {
            "equity": 10000.0,
            "peak_equity": 10000.0,
            "consecutive_losses": 0,
            "open_positions": [],
            "paper_mode": True,
            "updated": "",
        }
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self.data.update(loaded)
            except Exception:
                pass

    def save(self):
        self.data["updated"] = datetime.now(timezone.utc).isoformat()
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            print(f"[StateStore] save error: {e}")

    def set_equity(self, equity: float, peak: Optional[float] = None):
        self.data["equity"] = float(equity)
        if peak is not None:
            self.data["peak_equity"] = float(peak)
        else:
            self.data["peak_equity"] = max(float(self.data.get("peak_equity", equity)), float(equity))
        self.save()

    def set_consecutive_losses(self, n: int):
        self.data["consecutive_losses"] = int(n)
        self.save()

    def set_open_positions(self, positions: List[Dict[str, Any]]):
        self.data["open_positions"] = positions
        self.save()

    def set_paper_mode(self, paper: bool):
        self.data["paper_mode"] = bool(paper)
        self.save()

    def snapshot(
        self,
        equity: Optional[float] = None,
        peak: Optional[float] = None,
        consecutive_losses: Optional[int] = None,
        open_positions: Optional[List[Dict[str, Any]]] = None,
        paper_mode: Optional[bool] = None,
    ):
        if equity is not None:
            self.set_equity(equity, peak)
        if consecutive_losses is not None:
            self.set_consecutive_losses(consecutive_losses)
        if open_positions is not None:
            self.set_open_positions(open_positions)
        if paper_mode is not None:
            self.set_paper_mode(paper_mode)
        self.save()

    def get(self) -> Dict[str, Any]:
        return dict(self.data)
