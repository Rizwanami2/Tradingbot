#!/usr/bin/env python3
"""Anti-spam rate limiter and risk protection for autotrading execution."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("anti_spam")


@dataclass
class SymbolSpamState:
    symbol: str
    last_entry_time: float = 0.0
    last_entry_price: float = 0.0
    last_loss_time: float = 0.0
    consecutive_losses: int = 0
    last_loss_amount: float = 0.0
    total_losses_recorded: int = 0
    total_wins_recorded: int = 0


class AntiSpamRateLimiter:
    """Controls frequency of autotrade executions to prevent trade spamming and SL cascades."""

    def __init__(
        self,
        entry_cooldown_sec: Optional[int] = None,
        loss_cooldown_sec: Optional[int] = None,
        global_cooldown_sec: Optional[int] = None,
        max_positions_per_symbol: Optional[int] = None,
        min_price_distance_pct: Optional[float] = None,
    ):
        self.entry_cooldown_sec = (
            entry_cooldown_sec
            if entry_cooldown_sec is not None
            else int(os.getenv("SCALP_ENTRY_COOLDOWN_SEC", "45"))
        )
        self.loss_cooldown_sec = (
            loss_cooldown_sec
            if loss_cooldown_sec is not None
            else int(os.getenv("SCALP_LOSS_COOLDOWN_SEC", "90"))
        )
        self.global_cooldown_sec = (
            global_cooldown_sec
            if global_cooldown_sec is not None
            else int(os.getenv("SCALP_GLOBAL_COOLDOWN_SEC", "10"))
        )
        self.max_positions_per_symbol = (
            max_positions_per_symbol
            if max_positions_per_symbol is not None
            else int(os.getenv("SCALP_MAX_POSITIONS_PER_SYMBOL", "2"))
        )
        self.min_price_distance_pct = (
            min_price_distance_pct
            if min_price_distance_pct is not None
            else float(os.getenv("SCALP_MIN_PRICE_DISTANCE_PCT", "0.08"))
        )
        self.global_last_entry: float = 0.0
        self._states: Dict[str, SymbolSpamState] = {}

    def _get_state(self, symbol: str) -> SymbolSpamState:
        s = str(symbol).upper()
        if s not in self._states:
            self._states[s] = SymbolSpamState(symbol=s)
        return self._states[s]

    def record_entry(self, symbol: str, entry_price: float = 0.0, timestamp: Optional[float] = None) -> None:
        ts = timestamp if timestamp is not None else time.time()
        st = self._get_state(symbol)
        st.last_entry_time = ts
        st.last_entry_price = float(entry_price or 0.0)
        self.global_last_entry = ts
        logger.info(
            "AntiSpam: Entry recorded for %s @ %s (global_last=%s)",
            symbol, entry_price, ts
        )

    def record_loss(self, symbol: str, loss_amount: float = 0.0, timestamp: Optional[float] = None) -> None:
        ts = timestamp if timestamp is not None else time.time()
        st = self._get_state(symbol)
        st.last_loss_time = ts
        st.last_loss_amount = abs(float(loss_amount or 0.0))
        st.consecutive_losses += 1
        st.total_losses_recorded += 1
        logger.warning(
            "AntiSpam: Loss #%s recorded for %s (amount=$%.2f). Loss cooldown active for %ss",
            st.consecutive_losses, symbol, st.last_loss_amount, self.get_loss_cooldown_duration(symbol)
        )

    def record_win(self, symbol: str) -> None:
        st = self._get_state(symbol)
        st.consecutive_losses = 0
        st.total_wins_recorded += 1
        logger.info("AntiSpam: Win recorded for %s. Consecutive loss streak reset to 0.", symbol)

    def get_loss_cooldown_duration(self, symbol: str) -> int:
        st = self._get_state(symbol)
        streak = max(1, st.consecutive_losses)
        # Escalates cooldown with consecutive losses, max 300s (5m)
        multiplier = min(streak, 3)
        return int(self.loss_cooldown_sec * multiplier)

    def check_can_enter(
        self,
        symbol: str,
        current_price: float,
        engine: Any,
        max_open: int,
        timestamp: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Evaluate if a new trade can be opened for symbol.
        Returns (can_enter: bool, reason: str).
        """
        now = timestamp if timestamp is not None else time.time()
        sym = str(symbol).upper()
        st = self._get_state(sym)

        # 1. Total capacity check
        open_n = len(engine.active_positions) + len(engine.pending_setups)
        if open_n >= max_open:
            return False, f"Total open slots full ({open_n}/{max_open})"

        # 2. Global entry cooldown
        if self.global_cooldown_sec > 0 and self.global_last_entry > 0:
            elapsed_global = now - self.global_last_entry
            if elapsed_global < self.global_cooldown_sec:
                rem = int(self.global_cooldown_sec - elapsed_global)
                return False, f"Global cooldown active ({rem}s remaining)"

        # 3. Symbol entry cooldown
        if self.entry_cooldown_sec > 0 and st.last_entry_time > 0:
            elapsed_entry = now - st.last_entry_time
            if elapsed_entry < self.entry_cooldown_sec:
                rem = int(self.entry_cooldown_sec - elapsed_entry)
                return False, f"Entry cooldown for {sym} ({rem}s remaining)"

        # 4. Symbol loss cooldown
        effective_loss_cd = self.get_loss_cooldown_duration(sym)
        if effective_loss_cd > 0 and st.last_loss_time > 0:
            elapsed_loss = now - st.last_loss_time
            if elapsed_loss < effective_loss_cd:
                rem = int(effective_loss_cd - elapsed_loss)
                return False, f"Loss cooldown for {sym} ({rem}s remaining after {st.consecutive_losses} SL hit)"

        # 5. Max concurrent positions per symbol
        matching_active = []
        if hasattr(engine.active_positions, "get_all"):
            matching_active = engine.active_positions.get_all(sym)
        else:
            matching_active = [p for p in engine.active_positions.values() if getattr(p, "symbol", "") == sym]

        matching_pending = [p for p in getattr(engine, "pending_setups", {}).values() if getattr(p, "symbol", "") == sym]
        sym_count = len(matching_active) + len(matching_pending)

        if sym_count >= self.max_positions_per_symbol:
            return False, f"Max {self.max_positions_per_symbol} positions reached for {sym} ({sym_count} active/pending)"

        # 6. Minimum price distance from existing active positions on this symbol
        if self.min_price_distance_pct > 0 and matching_active and current_price > 0:
            for pos in matching_active:
                pos_entry = float(getattr(pos, "executed_price", 0.0) or getattr(pos, "entry_level", 0.0) or 0.0)
                if pos_entry > 0:
                    dist_pct = abs(current_price - pos_entry) / pos_entry * 100.0
                    if dist_pct < self.min_price_distance_pct:
                        return False, (
                            f"Price too close to existing position ({dist_pct:.3f}% < {self.min_price_distance_pct:.2f}% required)"
                        )

        return True, "OK"

    def get_status_report(self) -> str:
        now = time.time()
        lines = ["🛡️ **Anti-Spam & Cooldown Telemetry**"]
        if self.global_last_entry > 0:
            rem_g = max(0, int(self.global_cooldown_sec - (now - self.global_last_entry)))
            lines.append(f"• **Global Cooldown:** `{self.global_cooldown_sec}s` ({rem_g}s active)" if rem_g > 0 else f"• **Global Cooldown:** `Ready` ({self.global_cooldown_sec}s cfg)")
        else:
            lines.append(f"• **Global Cooldown:** `Ready` ({self.global_cooldown_sec}s cfg)")

        lines.append(f"• **Per-Symbol Limits:** Max `{self.max_positions_per_symbol}` positions | Min Dist: `{self.min_price_distance_pct:.2f}%`")
        lines.append(f"• **Cooldowns:** Entry `{self.entry_cooldown_sec}s` | Loss `{self.loss_cooldown_sec}s`")

        if not self._states:
            lines.append("\n_No symbol activity recorded yet._")
        else:
            for sym, st in self._states.items():
                rem_e = max(0, int(self.entry_cooldown_sec - (now - st.last_entry_time))) if st.last_entry_time > 0 else 0
                loss_cd = self.get_loss_cooldown_duration(sym)
                rem_l = max(0, int(loss_cd - (now - st.last_loss_time))) if st.last_loss_time > 0 else 0

                status_tag = "Ready 🟢"
                if rem_l > 0:
                    status_tag = f"Loss CD ({rem_l}s) 🔴"
                elif rem_e > 0:
                    status_tag = f"Entry CD ({rem_e}s) 🟡"

                last_p = f"  - Last Entry Price: `${st.last_entry_price:,.2f}`\n" if st.last_entry_price > 0 else ""
                lines.append(
                    f"\n**{sym}**: {status_tag}\n"
                    f"{last_p}"
                    f"  - Loss streak: `{st.consecutive_losses}` | Losses: `{st.total_losses_recorded}` | Wins: `{st.total_wins_recorded}`"
                )

        return "\n".join(lines)

    def reset(self, symbol: Optional[str] = None) -> None:
        if symbol:
            sym = str(symbol).upper()
            if sym in self._states:
                self._states[sym] = SymbolSpamState(symbol=sym)
        else:
            self._states.clear()
            self.global_last_entry = 0.0


_default_limiter = AntiSpamRateLimiter()


def get_anti_spam_limiter() -> AntiSpamRateLimiter:
    return _default_limiter
