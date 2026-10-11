"""
High-Conviction / Max Win-Rate Filter
=====================================
Does NOT guarantee 100% wins (impossible in real markets).
Only allows entries when multiple independent conditions align,
so the bot takes fewer, higher-quality trades.

Gate checklist (all required in STRICT mode):
  1. Agent agreement >= min_agree directional votes
  2. Weighted score >= min_score
  3. HTF bias aligned with trade direction
  4. Session tradeable (killzone preferred)
  5. SMC liquidity vote same direction (or strong)
  6. CVD proxy not opposing
  7. Minimum R:R
  8. Optional: price at/near premium-discount array (PDH/PDL / FVG)

Usage:
    from winrate_filter import HighConvictionFilter, FilterConfig
    ok, reasons = HighConvictionFilter().allow(context)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class FilterConfig:
    # Strictness
    strict: bool = True
    # Consensus
    min_directional_votes: int = 4          # of Trend/Liq/Ind/AI/News (+CVD)
    min_weighted_score: float = 3.2
    min_avg_strength: float = 0.55
    # Structure / flow
    require_htf_align: bool = True
    require_session: bool = True
    prefer_killzone: bool = True
    require_smc_same_dir: bool = True
    allow_cvd_flat: bool = True             # CVD FLAT ok; opposite blocks
    # Trade quality
    min_rr: float = 2.0
    max_stop_atr_mult: float = 2.5          # reject absurdly wide stops
    # Soft mode: only need this many hard checks
    soft_min_checks: int = 5


@dataclass
class FilterContext:
    direction: str                          # LONG / SHORT
    votes: List[Dict[str, Any]] = field(default_factory=list)
    score: float = 0.0
    htf_aligned: bool = False
    htf_reason: str = ""
    session_ok: bool = False
    session_name: str = ""
    in_killzone: bool = False
    smc_direction: str = "FLAT"
    smc_strength: float = 0.0
    cvd_direction: str = "FLAT"
    cvd_strength: float = 0.0
    rr: float = 0.0
    stop_atr_mult: float = 1.0
    regime: str = ""


class HighConvictionFilter:
    """Max-selectivity gate for higher expected win rate."""

    def __init__(self, config: Optional[FilterConfig] = None):
        self.cfg = config or FilterConfig()

    def allow(self, ctx: FilterContext) -> Tuple[bool, List[str], float]:
        """
        Returns (allowed, reason_lines, conviction_score 0-1).
        """
        cfg = self.cfg
        direction = (ctx.direction or "").upper()
        if direction not in ("LONG", "SHORT"):
            return False, ["No direction"], 0.0

        reasons: List[str] = []
        passed = 0
        total_hard = 0

        # 1) Directional agent votes
        total_hard += 1
        dir_votes = [
            v for v in ctx.votes
            if str(v.get("direction", "")).upper() == direction
            and str(v.get("agent", "")).upper() not in ("CVD",)
        ]
        n_dir = len(dir_votes)
        avg_str = (
            sum(float(v.get("strength", 0) or 0) for v in dir_votes) / n_dir
            if n_dir else 0.0
        )
        if n_dir >= cfg.min_directional_votes and avg_str >= cfg.min_avg_strength:
            passed += 1
            reasons.append(f"OK votes {n_dir} avg_str {avg_str:.2f}")
        else:
            reasons.append(
                f"FAIL votes {n_dir}/{cfg.min_directional_votes} "
                f"str {avg_str:.2f}/{cfg.min_avg_strength}"
            )

        # 2) Weighted score
        total_hard += 1
        if ctx.score >= cfg.min_weighted_score:
            passed += 1
            reasons.append(f"OK score {ctx.score:.2f}")
        else:
            reasons.append(f"FAIL score {ctx.score:.2f} < {cfg.min_weighted_score}")

        # 3) HTF
        total_hard += 1
        if not cfg.require_htf_align:
            passed += 1
            reasons.append("OK HTF not required")
        elif ctx.htf_aligned:
            passed += 1
            reasons.append(f"OK HTF ({ctx.htf_reason})")
        else:
            reasons.append(f"FAIL HTF ({ctx.htf_reason or 'misaligned'})")

        # 4) Session
        total_hard += 1
        if not cfg.require_session:
            passed += 1
            reasons.append("OK session not required")
        elif ctx.session_ok:
            if cfg.prefer_killzone and not ctx.in_killzone:
                # soft penalty in strict: still count as pass but note
                passed += 1
                reasons.append(f"OK session {ctx.session_name} (not killzone)")
            else:
                passed += 1
                reasons.append(f"OK session {ctx.session_name} kz={ctx.in_killzone}")
        else:
            reasons.append(f"FAIL session ({ctx.session_name})")

        # 5) SMC same direction
        total_hard += 1
        smc = (ctx.smc_direction or "FLAT").upper()
        if not cfg.require_smc_same_dir:
            passed += 1
            reasons.append("OK SMC not required")
        elif smc == direction and ctx.smc_strength >= 0.4:
            passed += 1
            reasons.append(f"OK SMC {smc} str {ctx.smc_strength:.2f}")
        else:
            reasons.append(f"FAIL SMC {smc} str {ctx.smc_strength:.2f}")

        # 6) CVD not opposing
        total_hard += 1
        cvd = (ctx.cvd_direction or "FLAT").upper()
        if cvd == direction:
            passed += 1
            reasons.append(f"OK CVD agrees ({cvd})")
        elif cvd == "FLAT" and cfg.allow_cvd_flat:
            passed += 1
            reasons.append("OK CVD flat")
        else:
            reasons.append(f"FAIL CVD opposing ({cvd})")

        # 7) R:R
        total_hard += 1
        if ctx.rr >= cfg.min_rr:
            passed += 1
            reasons.append(f"OK RR {ctx.rr:.2f}")
        else:
            reasons.append(f"FAIL RR {ctx.rr:.2f} < {cfg.min_rr}")

        # 8) Stop distance sanity
        total_hard += 1
        if 0 < ctx.stop_atr_mult <= cfg.max_stop_atr_mult:
            passed += 1
            reasons.append(f"OK stop {ctx.stop_atr_mult:.2f}×ATR")
        else:
            reasons.append(f"FAIL stop {ctx.stop_atr_mult:.2f}×ATR")

        conviction = passed / total_hard if total_hard else 0.0

        if cfg.strict:
            allowed = passed == total_hard
        else:
            allowed = passed >= cfg.soft_min_checks

        if allowed:
            reasons.insert(0, f"HIGH CONVICTION PASS ({passed}/{total_hard}) score={conviction:.0%}")
        else:
            reasons.insert(0, f"FILTER BLOCK ({passed}/{total_hard}) score={conviction:.0%}")

        return allowed, reasons, round(conviction, 3)


# Profile presets
STRICT_100_STYLE = FilterConfig(
    strict=True,
    min_directional_votes=4,
    min_weighted_score=3.5,
    min_avg_strength=0.60,
    require_htf_align=True,
    require_session=True,
    prefer_killzone=True,
    require_smc_same_dir=True,
    allow_cvd_flat=True,
    min_rr=2.2,
)

BALANCED_QUALITY = FilterConfig(
    strict=False,
    min_directional_votes=3,
    min_weighted_score=2.5,
    min_avg_strength=0.45,
    require_htf_align=True,
    require_session=True,
    prefer_killzone=False,
    require_smc_same_dir=False,
    allow_cvd_flat=True,
    min_rr=1.8,
    soft_min_checks=5,
)
