#!/usr/bin/env python3
"""AI Advanced Suite — modular filters, risk, exits, explainability."""
from __future__ import annotations
import json, logging, os, time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
logger = logging.getLogger("AIAdvanced")
STATE_PATH = Path(os.getenv("AI_ADVANCED_STATE", "ai_advanced_state.json"))

def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None: return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")
def _env_float(name: str, default: float) -> float:
    try: return float(os.getenv(name, str(default)))
    except Exception: return default
def _env_int(name: str, default: int) -> int:
    try: return int(float(os.getenv(name, str(default))))
    except Exception: return default

@dataclass
class SignalContext:
    symbol: str
    direction: str
    score: float = 0.0
    conviction: float = 0.6
    rr: float = 0.0
    agreement: int = 0
    regime: str = "unknown"
    setup: str = ""
    agents: List[str] = field(default_factory=list)
    agent_dirs: Dict[str, str] = field(default_factory=dict)
    features: Optional[List[float]] = None
    entry_price: float = 0.0
    atr: float = 0.0
    spread: float = 0.0
    session: str = ""
    hour_utc: Optional[int] = None
    is_scalp: bool = False
    profile: str = "scalp"
    signal_ts: float = field(default_factory=time.time)
    news_risk: float = 0.0
    daily_open: float = 0.0
    structure_ok: bool = True
    htf_bias: str = ""

@dataclass
class GateResult:
    allow: bool
    reasons: List[str] = field(default_factory=list)
    size_mult: float = 1.0
    scorecard: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    def veto(self, reason: str) -> "GateResult":
        self.allow = False
        self.reasons.append(reason)
        self.size_mult = 0.0
        return self

class AIAdvancedSuite:
    def __init__(self, state_path: Optional[Path] = None):
        self.state_path = Path(state_path or STATE_PATH)
        self.enabled = _env_bool("AI_ADVANCED_ENABLED", True)
        self.balance_mode_only_scorecard = _env_bool("AI_SCORECARD_BALANCE_ONLY", True)
        self.live_health = _env_bool("AI_HEALTH_LIVE", True)
        self.min_scorecard = _env_float("AI_MIN_SCORECARD", 55.0)
        self.max_daily_trades = _env_int("AI_MAX_DAILY_TRADES", 12)
        self.max_consec_losses = _env_int("AI_MAX_CONSEC_LOSSES", 4)
        self.revenge_window_sec = _env_float("AI_REVENGE_WINDOW_SEC", 900.0)
        self.fomo_atr_mult = _env_float("AI_FOMO_ATR_MULT", 1.5)
        self.stale_sec = _env_float("AI_STALE_SIGNAL_SEC", 180.0)
        self.daily_loss_r = _env_float("AI_DAILY_LOSS_R_LIMIT", 3.0)
        self.lot_ceiling = _env_float("AI_LOT_CEILING", 1.0)
        self.learn_freeze = _env_bool("AI_LEARN_FREEZE", False)
        self.require_schema = _env_bool("AI_REQUIRE_JSON_SCHEMA", True)
        self.state = self._load_state()
        if "suite_enabled" in self.state:
            self.enabled = bool(self.state["suite_enabled"])
        if "learn_frozen" in self.state:
            self.learn_freeze = bool(self.state["learn_frozen"])
        if "min_scorecard" in self.state:
            self.min_scorecard = float(self.state["min_scorecard"])
        if "lot_ceiling" in self.state:
            self.lot_ceiling = float(self.state["lot_ceiling"])
        if "balance_mode_only_scorecard" in self.state:
            self.balance_mode_only_scorecard = bool(self.state["balance_mode_only_scorecard"])

    def _load_state(self) -> Dict[str, Any]:
        default = {
            "daily": {"date": "", "trades": 0, "realized_r": 0.0, "wins": 0, "losses": 0},
            "consec_losses": 0,
            "last_loss_ts": 0.0,
            "narratives": [],
            "mistakes": {},
            "agent_trust": {},
            "fill_rejects": 0,
            "equity_peak": 0.0,
            "equity": 0.0,
            "milestones_hit": [],
            "shadow_log": [],
            "learn_frozen": False,
            "risk_persona": "med",
            "bias_day": "",
            "control_mode": "auto",
            "ui_mode": "classic",
            "override_next": False,
            "min_scorecard": 55.0,
            "lot_ceiling": 1.0,
            "balance_mode_only_scorecard": True,
            "audit": [],
            "version": 1,
        }
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict): default.update(raw)
            except Exception as e:
                logger.warning("AI state load: %s", e)
        return default

    def _save(self) -> None:
        try: self.state_path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        except Exception as e: logger.warning("AI state save: %s", e)

    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state.get("daily", {}).get("date") != today:
            self.state["daily"] = {"date": today, "trades": 0, "realized_r": 0.0, "wins": 0, "losses": 0}

    def _audit(self, event: str, detail: Dict[str, Any]) -> None:
        log = self.state.setdefault("audit", [])
        log.append({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **detail})
        self.state["audit"] = log[-500:]
        self._save()

    def _scorecard(self, ctx: SignalContext) -> float:
        s = 40.0 + min(25.0, max(0.0, ctx.conviction * 25.0)) + min(15.0, max(0.0, ctx.agreement * 4.0)) + min(10.0, max(0.0, ctx.rr * 3.0))
        if ctx.structure_ok: s += 5.0
        if ctx.news_risk > 0.4: s -= 15.0 * ctx.news_risk
        regime = (ctx.regime or "").lower()
        if "trend" in regime: s += 5.0
        if "range" in regime and ctx.is_scalp: s += 2.0
        if "range" in regime and not ctx.is_scalp: s -= 5.0
        return float(np.clip(s, 0.0, 100.0))

    def is_manual_mode(self) -> bool:
        return str(self.state.get("control_mode") or "auto").lower() in ("manual", "supervised", "advisory")

    def evaluate_entry(self, ctx: SignalContext) -> GateResult:
        res = GateResult(allow=True, size_mult=1.0)
        if not self.enabled:
            res.metadata["skipped"] = "AI_ADVANCED_ENABLED=false"
            return res
        self._roll_day()
        direction = (ctx.direction or "FLAT").upper()
        if direction in ("FLAT", "NONE", ""): return res.veto("flat direction")

        # 1-trade manual override check
        if self.state.get("override_next"):
            self.state["override_next"] = False
            self._save()
            res.metadata["manual_override"] = True
            res.reasons.append("manual override consumed (trade allowed)")
            self._audit("entry_manual_override", {"symbol": ctx.symbol, "direction": direction})
            return res

        is_manual = self.is_manual_mode()
        if is_manual:
            res.metadata["manual_mode"] = True

        def _veto_or_advise(reason: str) -> bool:
            """In manual control mode, flag advisory warnings without hard blocking.
            In auto mode, enforce strict algorithmic veto."""
            if is_manual:
                res.metadata.setdefault("advisory_vetoes", []).append(reason)
                res.reasons.append(f"[MANUAL ADVISORY] {reason}")
                return False
            else:
                res.veto(reason)
                return True

        if self.state.get("last_loss_ts"):
            dt = time.time() - float(self.state["last_loss_ts"])
            if dt < self.revenge_window_sec:
                if _veto_or_advise(f"revenge-trade lock ({int(self.revenge_window_sec - dt)}s left)"):
                    return res
        if int(self.state["daily"].get("trades", 0)) >= self.max_daily_trades:
            if _veto_or_advise(f"max daily trades ({self.max_daily_trades})"):
                return res
        if int(self.state.get("consec_losses", 0)) >= self.max_consec_losses:
            if _veto_or_advise(f"max consecutive losses ({self.max_consec_losses})"):
                return res
        if float(self.state["daily"].get("realized_r", 0.0)) <= -abs(self.daily_loss_r):
            if _veto_or_advise(f"daily loss brake ({self.daily_loss_r}R)"):
                return res
        age = time.time() - float(ctx.signal_ts or time.time())
        if age > self.stale_sec:
            if _veto_or_advise(f"stale signal ({int(age)}s)"):
                return res
        regime = (ctx.regime or "unknown").lower()
        if "chaos" in regime or "shock" in regime:
            if _veto_or_advise(f"regime veto ({regime})"):
                return res
        persona = str(self.state.get("risk_persona") or "med").lower()
        if persona == "low":
            res.size_mult *= 0.5
            if ctx.conviction < 0.65:
                if _veto_or_advise("low-risk persona: conviction < 0.65"):
                    return res
        elif persona == "high":
            res.size_mult *= 1.15
        bias = str(self.state.get("bias_day") or "").lower()
        if bias == "long" and direction != "LONG":
            if _veto_or_advise("bias-day LONG only"):
                return res
        if bias == "short" and direction != "SHORT":
            if _veto_or_advise("bias-day SHORT only"):
                return res
        profile = (ctx.profile or "").lower()
        scorecard = self._scorecard(ctx)
        res.scorecard = scorecard
        balance_profiles = ("balance", "balanced", "protect", "conservative")
        use_scorecard = (not self.balance_mode_only_scorecard) or (profile in balance_profiles or os.getenv("AI_MODE", "").lower() in balance_profiles)
        if use_scorecard and scorecard < self.min_scorecard:
            if _veto_or_advise(f"scorecard {scorecard:.0f} < {self.min_scorecard:.0f}"):
                return res
        if ctx.agent_dirs:
            longs = sum(1 for d in ctx.agent_dirs.values() if str(d).upper() in ("LONG", "BUY"))
            shorts = sum(1 for d in ctx.agent_dirs.values() if str(d).upper() in ("SHORT", "SELL"))
            if longs and shorts and abs(longs - shorts) <= 1 and len(ctx.agent_dirs) >= 3:
                if _veto_or_advise(f"agent conflict L{longs}/S{shorts}"):
                    return res
        if ctx.news_risk >= 0.75:
            if _veto_or_advise(f"news risk {ctx.news_risk:.2f}"):
                return res
        if ctx.news_risk >= 0.5:
            res.size_mult *= 0.5
            res.reasons.append("news risk size cut")
        if ctx.atr > 0 and ctx.spread > 0 and ctx.spread > 0.35 * ctx.atr:
            if _veto_or_advise("spread too wide vs ATR"):
                return res
        if ctx.atr > 0 and ctx.entry_price > 0 and ctx.daily_open > 0:
            move = abs(ctx.entry_price - ctx.daily_open) / ctx.atr
            if move >= self.fomo_atr_mult and ctx.conviction < 0.75:
                if _veto_or_advise(f"FOMO distance {move:.1f} ATR from daily open"):
                    return res
        if not ctx.structure_ok:
            if _veto_or_advise("structure gate failed"):
                return res
        if ctx.htf_bias:
            hb = ctx.htf_bias.upper()
            if hb in ("LONG", "BULL") and direction == "SHORT":
                res.size_mult *= 0.6
            if hb in ("SHORT", "BEAR") and direction == "LONG":
                res.size_mult *= 0.6
        sym = ctx.symbol.upper()
        hour = ctx.hour_utc if ctx.hour_utc is not None else datetime.now(timezone.utc).hour
        if ("XAU" in sym or "GOLD" in sym) and 0 <= hour < 6:
            res.size_mult *= 0.7
        if "BTC" in sym and datetime.now(timezone.utc).weekday() >= 5:
            res.size_mult *= 0.75
        eq = float(self.state.get("equity") or 0.0)
        peak = float(self.state.get("equity_peak") or eq or 1.0)
        if peak > 0 and eq > 0:
            dd = (peak - eq) / peak
            if dd >= 0.10:
                if _veto_or_advise("recovery mode: DD>=10%"):
                    return res
            if dd >= 0.06: res.size_mult *= 0.5
            if dd >= 0.03: res.size_mult *= 0.75
        res.size_mult = float(max(0.0, min(res.size_mult, 1.5)))
        if res.size_mult <= 0.05:
            if _veto_or_advise("size mult near zero"):
                return res
        res.reasons.append(f"scorecard={scorecard:.0f}")
        self._audit("entry_allow" if res.allow else "entry_veto", {"symbol": ctx.symbol, "direction": direction, "scorecard": scorecard, "size_mult": res.size_mult, "reasons": list(res.reasons)})
        return res

    def on_trade_opened(self, symbol: str, direction: str, meta: Optional[Dict] = None) -> None:
        self._roll_day()
        self.state["daily"]["trades"] = int(self.state["daily"].get("trades", 0)) + 1
        self._audit("opened", {"symbol": symbol, "direction": direction, **(meta or {})})
        self._save()

    def on_trade_closed(self, symbol: str, won: bool, r_multiple: float, features: Optional[List[float]] = None, setup: str = "", regime: str = "", agents: Optional[List[str]] = None, conf: float = 0.6, narrative: str = "", mistake_tag: str = "") -> None:
        self._roll_day()
        self.state["daily"]["realized_r"] = float(self.state["daily"].get("realized_r", 0.0)) + float(r_multiple)
        if won:
            self.state["daily"]["wins"] = int(self.state["daily"].get("wins", 0)) + 1
            self.state["consec_losses"] = 0
        else:
            self.state["daily"]["losses"] = int(self.state["daily"].get("losses", 0)) + 1
            self.state["consec_losses"] = int(self.state.get("consec_losses", 0)) + 1
            self.state["last_loss_ts"] = time.time()
            if mistake_tag:
                m = self.state.setdefault("mistakes", {})
                m[mistake_tag] = int(m.get(mistake_tag, 0)) + 1
        self.state.setdefault("narratives", []).append({"ts": datetime.now(timezone.utc).isoformat(), "symbol": symbol, "won": won, "r": r_multiple, "setup": setup, "regime": regime, "agents": agents or [], "conf": conf, "narrative": narrative[:500], "features": (features or [])[:20]})
        self.state["narratives"] = self.state["narratives"][-200:]
        if not self.state.get("learn_frozen") and not self.learn_freeze:
            try:
                from ai_learner import AILearnerAgent
                feats = np.array(features, dtype=float) if features else None
                AILearnerAgent().learn(direction_correct=bool(won), conf=float(conf), won=bool(won), pnl=float(r_multiple), features=feats, r_multiple=float(r_multiple), regime=regime or None)
            except Exception as e: logger.debug("learner feed: %s", e)
        self._audit("closed", {"symbol": symbol, "won": won, "r": r_multiple, "mistake": mistake_tag})
        self._save()

    def on_reject(self, reason: str = "") -> None:
        self.state["fill_rejects"] = int(self.state.get("fill_rejects", 0)) + 1
        self._save()

    def update_equity(self, equity: float) -> None:
        self.state["equity"] = float(equity)
        peak = float(self.state.get("equity_peak") or 0.0)
        if equity > peak: self.state["equity_peak"] = float(equity)
        self._save()

    def advise_exit(self, direction: str, r_now: float, bars_held: int, regime: str = "", exhaustion: bool = False, news_risk: float = 0.0) -> Dict[str, Any]:
        out: Dict[str, Any] = {"action": "hold", "reasons": []}
        if news_risk >= 0.8: return {"action": "flatten", "reasons": ["news flatten"]}
        if exhaustion and r_now > 0.3: return {"action": "take_partial", "reasons": ["exhaustion"]}
        if bars_held >= 40 and abs(r_now) < 0.25: return {"action": "time_stop", "reasons": ["time stop"]}
        if r_now >= 1.0: out = {"action": "scale_partial", "reasons": ["1R scale-out"]}
        if "range" in (regime or "").lower() and r_now >= 0.8: out = {"action": "tighten_trail", "reasons": ["range tighten"]}
        return out

    def status_text(self) -> str:
        self._roll_day()
        d = self.state.get("daily", {})
        mode = str(self.state.get("control_mode") or "auto").upper()
        mode_desc = "MANUAL (Trader Advisory — no hard veto)" if mode == "MANUAL" else "AUTO (Strict Algorithmic Gating)"
        frozen = bool(self.state.get("learn_frozen") or self.learn_freeze)
        lines = [
            "AI Advanced Suite",
            f"- Enabled: {self.enabled}",
            f"- Control Mode: {mode_desc}",
            f"- Persona: {self.state.get('risk_persona', 'med').upper()} | Bias: {self.state.get('bias_day') or 'none'}",
            f"- Today trades: {d.get('trades', 0)}/{self.max_daily_trades} | R: {float(d.get('realized_r', 0)):.2f}",
            f"- Consec losses: {self.state.get('consec_losses', 0)}/{self.max_consec_losses}",
            f"- Learn frozen: {frozen}",
            f"- Fill rejects: {self.state.get('fill_rejects', 0)}",
            f"- Equity: ${float(self.state.get('equity') or 0):,.2f} peak ${float(self.state.get('equity_peak') or 0):,.2f}",
            f"- Scorecard min: {self.min_scorecard:.0f} (balance-only={self.balance_mode_only_scorecard})",
            f"- Lot ceiling: {self.lot_ceiling:.2f} lots",
            f"- Health live: {self.live_health}",
        ]
        return "\n".join(lines)

    def get_summary_dict(self) -> Dict[str, Any]:
        """Return rich dictionary of current AI suite telemetry and manual controls."""
        self._roll_day()
        d = self.state.get("daily", {})
        dt = time.time() - float(self.state.get("last_loss_ts") or 0.0)
        revenge_active = (float(self.state.get("last_loss_ts") or 0.0) > 0) and (dt < self.revenge_window_sec)
        revenge_left = int(self.revenge_window_sec - dt) if revenge_active else 0
        loss_brake_tripped = float(d.get("realized_r", 0.0)) <= -abs(self.daily_loss_r)
        return {
            "enabled": bool(self.enabled),
            "control_mode": str(self.state.get("control_mode") or "auto").lower(),
            "is_manual": self.is_manual_mode(),
            "override_next": bool(self.state.get("override_next")),
            "risk_persona": str(self.state.get("risk_persona") or "med").lower(),
            "bias_day": str(self.state.get("bias_day") or ""),
            "learn_frozen": bool(self.state.get("learn_frozen") or self.learn_freeze),
            "trades_today": int(d.get("trades", 0)),
            "max_daily_trades": self.max_daily_trades,
            "realized_r": float(d.get("realized_r", 0.0)),
            "wins": int(d.get("wins", 0)),
            "losses": int(d.get("losses", 0)),
            "consec_losses": int(self.state.get("consec_losses", 0)),
            "max_consec_losses": self.max_consec_losses,
            "revenge_active": revenge_active,
            "revenge_left_sec": revenge_left,
            "loss_brake_tripped": loss_brake_tripped,
            "fill_rejects": int(self.state.get("fill_rejects", 0)),
            "equity": float(self.state.get("equity") or 0.0),
            "equity_peak": float(self.state.get("equity_peak") or 0.0),
            "min_scorecard": float(self.min_scorecard),
            "balance_only_scorecard": bool(self.balance_mode_only_scorecard),
            "lot_ceiling": float(self.lot_ceiling),
            "live_health": bool(self.live_health),
            "ui_mode": str(self.state.get("ui_mode", "classic")),
        }

    def set_ui_mode(self, mode: str) -> str:
        m = "classic" if str(mode or "").strip().lower() in ("classic", "simple", "clean") else "advanced"
        self.state["ui_mode"] = m
        self._save()
        return f"Command UI mode set to {m.upper()}"

    def is_classic_mode(self) -> bool:
        return self.state.get("ui_mode", "classic") == "classic"

    def set_enabled(self, on: bool) -> str:
        self.enabled = bool(on)
        self.state["suite_enabled"] = bool(on)
        self._save()
        return f"AI suite {'ON' if on else 'OFF'}"

    def set_control_mode(self, mode: str) -> str:
        m = str(mode or "").strip().lower()
        if m in ("manual", "supervise", "supervised", "advisory", "trader"):
            self.state["control_mode"] = "manual"
            self._save()
            return "Control mode set to MANUAL (Trader Supervised / Advisory — gates advise without hard veto)"
        elif m in ("auto", "autonomous", "strict", "strict_auto"):
            self.state["control_mode"] = "auto"
            self._save()
            return "Control mode set to AUTO (Strict Autonomous Gating — all algorithmic gates enforced)"
        return "Use: /ai mode auto | manual"

    def set_manual_mode(self, manual: bool) -> str:
        return self.set_control_mode("manual" if manual else "auto")

    def set_override_next(self, override: bool = True) -> str:
        self.state["override_next"] = bool(override)
        self._save()
        st = "ACTIVATED — next signal will bypass gate vetoes" if override else "DEACTIVATED"
        return f"Manual 1-trade override {st}"

    def reset_safety_locks(self) -> str:
        cleared = []
        if self.state.get("last_loss_ts"):
            self.state["last_loss_ts"] = 0.0
            cleared.append("Revenge Lock")
        if int(self.state.get("consec_losses", 0)) > 0:
            cleared.append(f"Consec Losses ({self.state['consec_losses']})")
            self.state["consec_losses"] = 0
        if int(self.state.get("fill_rejects", 0)) > 0:
            cleared.append(f"Fill Rejects ({self.state['fill_rejects']})")
            self.state["fill_rejects"] = 0
        if float(self.state.get("daily", {}).get("realized_r", 0.0)) <= -abs(self.daily_loss_r):
            cleared.append("Daily Loss Brake")
            self.state.setdefault("daily", {})["realized_r"] = 0.0
        self.state["override_next"] = False
        self._audit("manual_safety_reset", {"cleared": cleared})
        self._save()
        details = ", ".join(cleared) if cleared else "No active locks found"
        return f"Safety locks reset successfully ({details})"

    def set_min_scorecard(self, score: float) -> str:
        val = float(np.clip(score, 0.0, 100.0))
        self.min_scorecard = val
        self.state["min_scorecard"] = val
        self._save()
        return f"Minimum signal scorecard set to {val:.0f}"

    def set_lot_ceiling(self, ceiling: float) -> str:
        val = float(max(0.01, ceiling))
        self.lot_ceiling = val
        self.state["lot_ceiling"] = val
        self._save()
        return f"Lot size ceiling set to {val:.2f} lots"

    def set_scorecard_balance_only(self, balance_only: bool) -> str:
        self.balance_mode_only_scorecard = bool(balance_only)
        self.state["balance_mode_only_scorecard"] = bool(balance_only)
        self._save()
        st = "Balance/Conservative profiles ONLY" if balance_only else "ALL strategy profiles"
        return f"Scorecard gate applies to: {st}"

    def set_persona(self, persona: str) -> str:
        p = persona.strip().lower()
        if p not in ("low", "med", "high"): return "Use: low | med | high"
        self.state["risk_persona"] = p; self._save(); return f"Risk persona set to {p}"

    def set_bias(self, bias: str) -> str:
        b = bias.strip().lower()
        if b in ("", "none", "off", "clear"): self.state["bias_day"] = ""; self._save(); return "Day bias cleared"
        if b not in ("long", "short"): return "Use: long | short | clear"
        self.state["bias_day"] = b; self._save(); return f"Day bias set to {b.upper()}"

    def set_learn_freeze(self, frozen: bool) -> str:
        self.learn_freeze = bool(frozen)
        self.state["learn_frozen"] = bool(frozen)
        self._save()
        return f"Learn freeze = {frozen}"

    def health_ok(self, ea_ok: Optional[bool] = None, ticks_ok: Optional[bool] = None) -> Tuple[bool, str]:
        if not self.live_health: return True, "health checks disabled"
        problems = []
        if ea_ok is None:
            try:
                from bridge import ea_client
                st = ea_client.status()
                ea_ok = bool(st.get("ok", False))
            except Exception:
                ea_ok = True
        if ticks_ok is None:
            ticks_ok = True
        if not ea_ok: problems.append("EA bridge not connected")
        if not ticks_ok: problems.append("no ticks")
        if int(self.state.get("fill_rejects", 0)) >= 8: problems.append(f"fill rejects elevated ({self.state.get('fill_rejects', 0)})")
        if int(self.state.get("consec_losses", 0)) >= self.max_consec_losses: problems.append(f"consecutive losses limit reached ({self.state.get('consec_losses')})")
        return (False, "; ".join(problems)) if problems else (True, "ok")

    def weekly_critique_prompt(self) -> str:
        narr = self.state.get("narratives", [])[-20:]
        mistakes = self.state.get("mistakes", {})
        return (
            "You are a trading coach. Given recent trades JSON and mistake counts, "
            "return 3 concrete rule changes. Be brief.\n"
            f"TRADES={json.dumps(narr)[:3000]}\nMISTAKES={json.dumps(mistakes)}"
        )

    def apply_lot_ceiling(self, lots: float) -> float:
        return float(min(max(lots, 0.0), self.lot_ceiling))

_SUITE: Optional[AIAdvancedSuite] = None

def get_ai_suite() -> AIAdvancedSuite:
    global _SUITE
    if _SUITE is None: _SUITE = AIAdvancedSuite()
    return _SUITE

def evaluate_signal_dict(sig: Dict[str, Any], profile: str = "scalp") -> GateResult:
    suite = get_ai_suite()
    agents = sig.get("agents") or []
    if isinstance(agents, dict):
        agent_dirs = {str(k): str(v) for k, v in agents.items()}
        agent_list = list(agent_dirs.keys())
    else:
        agent_list = [str(a) for a in agents]
        agent_dirs = {}
    ctx = SignalContext(symbol=str(sig.get("symbol") or ""), direction=str(sig.get("direction") or sig.get("side") or "FLAT").upper(), score=float(sig.get("score") or 0.0), conviction=float(sig.get("conviction") or 0.6), rr=float(sig.get("rr") or 0.0), agreement=int(sig.get("agreement") or sig.get("agree") or 0), regime=str(sig.get("regime") or ""), setup=str(sig.get("setup") or ""), agents=agent_list, agent_dirs=agent_dirs, features=sig.get("features"), entry_price=float(sig.get("entry") or sig.get("entry_level") or 0.0), atr=float(sig.get("atr") or 0.0), spread=float(sig.get("spread") or 0.0), session=str(sig.get("session") or ""), is_scalp=bool(sig.get("is_scalp")), profile=profile or str(sig.get("profile") or "scalp"), news_risk=float(sig.get("news_risk") or 0.0), daily_open=float(sig.get("daily_open") or 0.0), structure_ok=bool(sig.get("structure_ok", True)), htf_bias=str(sig.get("htf_bias") or ""), signal_ts=float(sig.get("signal_ts") or time.time()))
    return suite.evaluate_entry(ctx)
