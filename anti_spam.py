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


def canonical_currency(symbol: str) -> str:
    """Canonicalize symbol to underlying asset currency (e.g. XAU for Gold, BTC for Bitcoin)."""
    s = str(symbol or "").strip().upper().replace("/", "").replace("-", "").replace(":", "").replace("_", "")
    if any(x in s for x in ("BTC", "BITCOIN", "XBT")):
        return "BTC"
    if any(x in s for x in ("XAU", "GOLD", "PAXG", "XAUT")):
        return "XAU"
    if any(x in s for x in ("ETH", "ETHEREUM")):
        return "ETH"
    return s


class AntiSpamRateLimiter:
    """Controls frequency of autotrade executions to prevent trade spamming and SL cascades."""

    def __init__(
        self,
        entry_cooldown_sec: Optional[int] = None,
        loss_cooldown_sec: Optional[int] = None,
        global_cooldown_sec: Optional[int] = None,
        max_positions_per_symbol: Optional[int] = None,
        min_price_distance_pct: Optional[float] = None,
        mode: str = "auto",
    ):
        self._custom_entry_cd = entry_cooldown_sec is not None
        self._custom_loss_cd = loss_cooldown_sec is not None
        self._custom_global_cd = global_cooldown_sec is not None

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
            else int(os.getenv("BOT_MAX_POSITIONS_PER_SYMBOL") or os.getenv("SCALP_MAX_POSITIONS_PER_SYMBOL", "2"))
        )
        self.min_price_distance_pct = (
            min_price_distance_pct
            if min_price_distance_pct is not None
            else float(os.getenv("SCALP_MIN_PRICE_DISTANCE_PCT", "0.03"))
        )
        self.mode: str = self._normalize_mode(mode or os.getenv("ANTISPAM_MODE", "auto"))
        self.global_last_entry: float = 0.0
        self._states: Dict[str, SymbolSpamState] = {}
        self.spam_trade_enabled: bool = False
        self.spam_trade_max_trades: int = 6

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        m = str(mode or "").strip().lower()
        if m in ("auto", "adaptive", "dynamic"):
            return "auto"
        if m in ("manual", "custom"):
            return "manual"
        if m in ("on", "enable", "enabled", "strict", "1", "true", "active", "activate", "activated"):
            return "on"
        if m in ("off", "disable", "disabled", "bypass", "0", "false", "deactivate", "deactivated", "inactive"):
            return "off"
        return "auto"

    def set_mode(self, mode: str) -> str:
        self.mode = self._normalize_mode(mode)
        if self.mode == "auto":
            self._custom_entry_cd = False
            self._custom_loss_cd = False
            self._custom_global_cd = False
        elif self.mode in ("manual", "on"):
            self._custom_entry_cd = True
        logger.info("AntiSpam mode switched to: %s", self.mode)
        return self.mode

    def set_entry_cooldown(self, seconds: int) -> int:
        """Set explicit entry cooldown in seconds (enforced for both auto and manual)."""
        self.entry_cooldown_sec = max(5, int(seconds))
        self._custom_entry_cd = True
        if self.mode != "off":
            self.mode = "manual"
        logger.info("AntiSpam entry cooldown set to %ss (enforced for auto & manual)", self.entry_cooldown_sec)
        return self.entry_cooldown_sec

    def get_mode(self) -> str:
        return self.mode

    def set_spam_mode(self, enabled: bool, max_trades: int = 6, engine: Optional[Any] = None) -> bool:
        """Control spamming trade mode (max 6 without cooldown)."""
        self.spam_trade_enabled = bool(enabled)
        self.spam_trade_max_trades = max(1, int(max_trades))
        if engine is not None and hasattr(engine, "max_open_trades"):
            if self.spam_trade_enabled:
                engine.max_open_trades = max(int(engine.max_open_trades), self.spam_trade_max_trades)
            else:
                try:
                    import config as _cfg_sp
                    engine.max_open_trades = int(getattr(_cfg_sp, "MAX_OPEN_TRADES", 6) or 6)
                except Exception:
                    engine.max_open_trades = 6
        logger.info("AntiSpam: Spam trade mode set to %s (max_trades=%s)", self.spam_trade_enabled, self.spam_trade_max_trades)
        return self.spam_trade_enabled

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

    def count_currency_positions(self, engine: Any, currency: str) -> Tuple[int, List[Any]]:
        """Count active positions and pending setups matching a canonical currency."""
        curr = canonical_currency(currency)
        matching_active = []
        seen_tickets = set()
        if hasattr(engine, "active_positions") and engine.active_positions:
            for p in engine.active_positions.values():
                sym = getattr(p, "symbol", "")
                if canonical_currency(sym) == curr:
                    matching_active.append(p)
                    tkt = str(getattr(p, "ticket", "") or getattr(p, "id", "") or "").strip()
                    if tkt:
                        seen_tickets.add(tkt)

        matching_pending = []
        if hasattr(engine, "pending_setups") and engine.pending_setups:
            for p in engine.pending_setups.values():
                sym = getattr(p, "symbol", "")
                if canonical_currency(sym) == curr:
                    matching_pending.append(p)

        # Include broker positions if broker is present
        if getattr(engine, "broker", None) is not None and hasattr(engine.broker, "get_positions"):
            try:
                for bp in engine.broker.get_positions():
                    if canonical_currency(getattr(bp, "symbol", "")) == curr:
                        bp_tkt = str(getattr(bp, "ticket", "") or getattr(bp, "id", "") or "").strip()
                        if not bp_tkt or bp_tkt not in seen_tickets:
                            matching_active.append(bp)
                            if bp_tkt:
                                seen_tickets.add(bp_tkt)
            except Exception:
                pass

        total = len(matching_active) + len(matching_pending)
        return total, matching_active

    def get_entry_cooldown_duration(self, symbol: str) -> int:
        if self._custom_entry_cd:
            return int(self.entry_cooldown_sec)
        st = self._get_state(symbol)
        if self.mode == "auto":
            try:
                from strategy_profiles import get_current_profile
                p = get_current_profile()
                if getattr(p, "is_scalp", False):
                    # Scalp concession: faster entry cooldown unless on consecutive losses
                    if st.consecutive_losses == 0:
                        return 20
                    elif st.consecutive_losses == 1:
                        return 45
                    else:
                        return 75
                elif p.name == "swing":
                    return 90
            except Exception:
                pass
        return int(self.entry_cooldown_sec)

    def get_loss_cooldown_duration(self, symbol: str) -> int:
        st = self._get_state(symbol)
        streak = max(1, st.consecutive_losses)
        base_cd = self.loss_cooldown_sec
        if not self._custom_loss_cd and self.mode == "auto":
            try:
                from strategy_profiles import get_current_profile
                p = get_current_profile()
                if getattr(p, "is_scalp", False):
                    base_cd = 60
                elif p.name == "swing":
                    base_cd = 180
            except Exception:
                pass
        # Escalates cooldown with consecutive losses, max 300s (5m)
        multiplier = min(streak, 4)
        return int(base_cd * multiplier)

    def get_global_cooldown_duration(self) -> int:
        if self._custom_global_cd:
            return int(self.global_cooldown_sec)
        if self.mode == "auto":
            try:
                from strategy_profiles import get_current_profile
                p = get_current_profile()
                if getattr(p, "is_scalp", False):
                    return 5
                elif p.name == "swing":
                    return 15
            except Exception:
                pass
        return int(self.global_cooldown_sec)

    def check_can_enter(
        self,
        symbol: str,
        current_price: float,
        engine: Any,
        max_open: int = 6,
        timestamp: Optional[float] = None,
        bypass_cooldowns: bool = False,
        direction: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Evaluate if a new trade can be opened for symbol.
        Enforces:
        - Strict Single-Direction Rule: NEVER hold BUY and SELL positions on the same currency simultaneously (Auto & Manual)
        - Total max concurrent positions (default 6)
        - Max 2 trades per currency (2 Gold, 2 BTC)
        - Global, entry, and loss cooldowns (enforced on both Auto & Manual unless bypassed)
        - Price proximity distance protection
        Returns (can_enter: bool, reason: str).
        """
        now = timestamp if timestamp is not None else time.time()
        sym = str(symbol).upper()
        curr = canonical_currency(sym)
        st = self._get_state(sym)

        # 1. Strict Single-Direction Rule: NEVER hold BUY and SELL positions on the same currency at the same time.
        # This rule is absolute and applies across ALL modes (Auto, Manual, Spam, Scalp, Swing).
        curr_count, matching_active = self.count_currency_positions(engine, curr)
        if direction:
            dir_norm = str(direction).upper()
            is_incoming_buy = dir_norm in ("LONG", "BUY")
            is_incoming_sell = dir_norm in ("SHORT", "SELL")
            if is_incoming_buy or is_incoming_sell:
                all_matching = list(matching_active)
                if hasattr(engine, "pending_setups") and engine.pending_setups:
                    for p in engine.pending_setups.values():
                        if canonical_currency(getattr(p, "symbol", "")) == curr:
                            all_matching.append(p)
                for pos in all_matching:
                    pos_dir = str(getattr(pos, "direction", getattr(pos, "side", ""))).upper()
                    is_pos_buy = pos_dir in ("LONG", "BUY")
                    is_pos_sell = pos_dir in ("SHORT", "SELL")
                    if is_incoming_buy and is_pos_sell:
                        return False, f"Opposing direction: active SELL position exists on {curr}; cannot BUY and SELL at the same time"
                    if is_incoming_sell and is_pos_buy:
                        return False, f"Opposing direction: active BUY position exists on {curr}; cannot BUY and SELL at the same time"

        # Check spam trade mode (max 6 without cooldown)
        if self.spam_trade_enabled:
            eff_max_open = max(max_open, self.spam_trade_max_trades)
            open_n = len(engine.active_positions) + len(engine.pending_setups)
            if open_n >= eff_max_open:
                return False, f"Spam trade capacity reached ({open_n}/{eff_max_open})"
            return True, f"OK (Spam trade mode active: {open_n + 1}/{eff_max_open} without cooldown)"

        # 2. Total capacity check across all symbols (default max 6)
        open_n = len(engine.active_positions) + len(engine.pending_setups)
        if open_n >= max_open:
            return False, f"Total open slots full ({open_n}/{max_open})"

        # 3. Per-currency concurrency check (max 2 trades same currency, e.g. 2 Gold, 2 BTC)
        if curr_count >= self.max_positions_per_symbol:
            return False, f"Max {self.max_positions_per_symbol} positions reached for currency {curr} ({curr_count} active/pending)"

        # 4. Minimum price distance from existing active positions on this currency
        # When bypass_cooldowns=True, time cooldowns are skipped,
        # but micro price spacing is maintained so orders do not stack at the exact same price.
        eff_min_dist = min(self.min_price_distance_pct, 0.03) if bypass_cooldowns else self.min_price_distance_pct
        if eff_min_dist > 0 and matching_active and current_price > 0:
            for pos in matching_active:
                pos_entry = float(getattr(pos, "executed_price", 0.0) or getattr(pos, "entry_level", getattr(pos, "entry_price", 0.0)) or 0.0)
                if pos_entry > 0:
                    dist_pct = abs(current_price - pos_entry) / pos_entry * 100.0
                    if dist_pct < eff_min_dist:
                        return False, (
                            f"Price too close to existing {curr} position ({dist_pct:.3f}% < {eff_min_dist:.2f}% required)"
                        )

        # When bypass_cooldowns or mode is OFF: time cooldowns are bypassed
        if bypass_cooldowns:
            return True, "OK (cooldowns bypassed)"
        if self.mode == "off":
            return True, "OK (anti-spam manual OFF)"
        if self.mode == "off":
            return True, "OK (anti-spam manual OFF)"

        # 4. Symbol / currency loss cooldown (evaluated first so stop-out cooldown takes priority)
        effective_loss_cd = self.get_loss_cooldown_duration(sym)
        if effective_loss_cd > 0 and st.last_loss_time > 0:
            elapsed_loss = now - st.last_loss_time
            if elapsed_loss < effective_loss_cd:
                rem = int(effective_loss_cd - elapsed_loss)
                return False, f"Loss cooldown for {sym} ({rem}s remaining after {st.consecutive_losses} SL hit)"

        # 5. Global entry cooldown
        eff_global_cd = self.get_global_cooldown_duration()
        if eff_global_cd > 0 and self.global_last_entry > 0:
            elapsed_global = now - self.global_last_entry
            if elapsed_global < eff_global_cd:
                rem = int(eff_global_cd - elapsed_global)
                return False, f"Global cooldown active ({rem}s remaining)"

        # 6. Symbol / currency entry cooldown
        eff_entry_cd = self.get_entry_cooldown_duration(sym)
        if eff_entry_cd > 0 and st.last_entry_time > 0:
            elapsed_entry = now - st.last_entry_time
            if elapsed_entry < eff_entry_cd:
                rem = int(eff_entry_cd - elapsed_entry)
                return False, f"Entry cooldown for {sym} ({rem}s remaining)"

        return True, "OK"

    def get_status_report(self, engine: Optional[Any] = None) -> str:
        now = time.time()
        mode_icon = "🤖 AUTO (Adaptive)" if self.mode == "auto" else ("🔒 MANUAL (Fixed Cooldown)" if self.mode in ("on", "manual") else "⚠️ OFF (Bypassed)")
        spam_tag = f"⚡ ENABLED (Max {self.spam_trade_max_trades} Trades, No Cooldown)" if self.spam_trade_enabled else "🔒 DISABLED (Normal Protection)"
        max_total = self.spam_trade_max_trades if self.spam_trade_enabled else (int(getattr(engine, "max_open_trades", 6)) if engine is not None else 6)
        lines = [
            "🛡️ **Anti-Spam & Cooldown Telemetry**",
            f"• **Scope:** `Enforced on BOTH Auto & Manual trading`",
            f"• **Direction Rule:** `Strict Single-Direction (Opposing BUY/SELL blocked)`",
            f"• **Operating Mode:** `{mode_icon}`",
            f"• **Spam Trade Mode:** `{spam_tag}`",
            f"• **Concurrency Limits:** Max `{max_total}` Total | Max `{'6' if self.spam_trade_enabled else '2'}` per Currency (2 Gold, 2 BTC)",
            f"• **Base Cooldowns:** Entry `{self.entry_cooldown_sec}s` | Loss `{self.loss_cooldown_sec}s` | Global `{self.global_cooldown_sec}s`",
            f"• **Min Price Distance:** `{'0.00% (Bypassed)' if self.spam_trade_enabled else f'{self.min_price_distance_pct:.2f}%'}`",
        ]

        if engine is not None:
            open_n = len(engine.active_positions) + len(engine.pending_setups)
            gold_n, _ = self.count_currency_positions(engine, "XAU")
            btc_n, _ = self.count_currency_positions(engine, "BTC")
            curr_limit = self.spam_trade_max_trades if self.spam_trade_enabled else 2
            lines.append(f"• **Active Slots:** `{open_n}/{max_total}` (Gold: `{gold_n}/{curr_limit}`, BTC: `{btc_n}/{curr_limit}`)")

        if self.global_last_entry > 0:
            rem_g = max(0, int(self.get_global_cooldown_duration() - (now - self.global_last_entry)))
            lines.append(f"• **Global Cooldown:** `{rem_g}s active`" if rem_g > 0 else "• **Global Cooldown:** `Ready`")
        else:
            lines.append("• **Global Cooldown:** `Ready`")

        if not self._states:
            lines.append("\n_No symbol activity recorded yet._")
        else:
            for sym, st in self._states.items():
                rem_e = max(0, int(self.get_entry_cooldown_duration(sym) - (now - st.last_entry_time))) if st.last_entry_time > 0 else 0
                loss_cd = self.get_loss_cooldown_duration(sym)
                rem_l = max(0, int(loss_cd - (now - st.last_loss_time))) if st.last_loss_time > 0 else 0

                status_tag = "Ready 🟢"
                if rem_l > 0:
                    status_tag = f"Loss CD ({rem_l}s) 🔴"
                elif rem_e > 0:
                    status_tag = f"Entry CD ({rem_e}s) 🟡"

                last_p = f"  - Last Entry Price: `${st.last_entry_price:,.2f}`\n" if st.last_entry_price > 0 else ""
                lines.append(
                    f"\n**{sym}** ({canonical_currency(sym)}): {status_tag}\n"
                    f"{last_p}"
                    f"  - Loss streak: `{st.consecutive_losses}` | Losses: `{st.total_losses_recorded}` | Wins: `{st.total_wins_recorded}`"
                )

        lines.append(
            "\n💡 _Commands: /cooldown [auto|on|off|<seconds>|reset] | /spam on | /spam off_"
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
