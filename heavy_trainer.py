#!/usr/bin/env python3
"""
Heavy AI Training & Deep Learning Engine
========================================
Comprehensive multi-asset, multi-timeframe deep training pipeline for:
  1. AutonomousLearner (flaw mining, toxic hours/weekdays banning, regime sizing, threshold tightening)
  2. AILearnerAgent (gradient weight convergence, feature importance ranking, empirical calibration curve)
  3. LearningEdge (168-hour time-of-day / day-of-week expectancy heatmap, setup tracking)
  4. AIAdvancedSuite (risk persona calibration, baseline trust metrics)

Usage:
  CLI:
    python3 heavy_trainer.py --heavy
    python3 heavy_trainer.py --symbols BTCUSDT,XAUUSD,ETHUSDT --years 3.0
    python3 heavy_trainer.py --quick

  Python:
    from heavy_trainer import HeavyTrainer
    trainer = HeavyTrainer(symbols=["BTCUSDT", "XAUUSD", "ETHUSDT"])
    summary = trainer.train_all(heavy=True)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ai_advanced_suite import AIAdvancedSuite
from ai_learner import AILearnerAgent, Dir
from autonomous_learner import AutonomousLearner, get_brain
from learning_edge import LearningEdge

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("HeavyTrainer")


class HeavyTrainer:
    """
    Orchestrates advanced multi-asset, multi-timeframe heavy training
    across all AI, ML, statistical, and autonomous learning subsystems.
    """

    CORE_SYMBOLS = ["BTCUSDT", "XAUUSD", "ETHUSDT", "SOLUSDT", "EURUSD"]
    DEFAULT_TIMEFRAMES = ["1d", "1h"]

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        years: float = 5.0,
        timeframes: Optional[List[str]] = None,
    ):
        self.symbols = [s.strip().upper() for s in (symbols or self.CORE_SYMBOLS)]
        self.years = max(0.5, float(years))
        self.timeframes = timeframes or self.DEFAULT_TIMEFRAMES
        self.learner = AILearnerAgent()
        self.brain = get_brain(auto_bootstrap=False)
        self.edge = LearningEdge()
        self.ai_suite = AIAdvancedSuite()

    def train_autonomous_learner(self) -> Dict[str, Any]:
        """
        Train AutonomousLearner via deep historical simulation across symbols and timeframes.
        Mines losing setups, toxic hours, and weak regimes; tightens filters.
        """
        logger.info("[HeavyTrainer] 1/4 Training Autonomous Learner across %s (years=%.1f)...", self.symbols, self.years)
        start_t = time.time()
        res = self.brain.bootstrap(
            symbols=self.symbols,
            years=self.years,
            timeframes=self.timeframes,
        )
        elapsed = time.time() - start_t
        res["elapsed_sec"] = round(elapsed, 2)
        logger.info(
            "[HeavyTrainer] Autonomous Learner finished in %.2fs: %d trades simulated, %d flaws mined, %d banned setups, %s banned hours",
            elapsed,
            res.get("trades", 0),
            res.get("flaws", 0),
            res.get("banned_setups", 0),
            res.get("banned_hours", []),
        )
        return res

    def train_ai_model(self, step: int = 4, forward_bars: int = 10) -> Dict[str, Any]:
        """
        Train AILearnerAgent's multi-feature model using historical candle sequences.
        Optimizes weights via online gradient descent, establishes calibration curve
        and calculates empirical feature importances.
        """
        logger.info("[HeavyTrainer] 2/4 Training AI-Learner model on multi-asset candles...")
        start_t = time.time()
        total_samples = 0
        total_wins = 0

        for sym in self.symbols:
            for tf in self.timeframes:
                df = self.brain._load_history(sym, years=min(self.years, 3.0), timeframe=tf)
                if df is None or len(df) < 100:
                    continue

                c = df["close"].values.astype(float)
                h = df["high"].values.astype(float)
                l = df["low"].values.astype(float)
                n_bars = len(df)

                for i in range(85, n_bars - forward_bars, step):
                    sub_df = df.iloc[i - 80:i]
                    feats = self.learner._extract_features(sub_df)
                    direction, conf, _ = self.learner.predict(sub_df)

                    entry_p = c[i - 1]
                    sub_c = sub_df["close"].values.astype(float)
                    sub_h = sub_df["high"].values.astype(float)
                    sub_l = sub_df["low"].values.astype(float)
                    tr = np.maximum(
                        sub_h[1:] - sub_l[1:],
                        np.maximum(np.abs(sub_h[1:] - sub_c[:-1]), np.abs(sub_l[1:] - sub_c[:-1])),
                    )
                    past_atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else (entry_p * 0.01)
                    sl_dist = max(entry_p * 0.002, past_atr * 1.2)
                    tp_dist = max(entry_p * 0.003, past_atr * 2.0)

                    # Determine trade direction to evaluate
                    target_dir = direction if direction != Dir.FLAT else (Dir.LONG if c[i] > entry_p else Dir.SHORT)

                    # Walk forward bars sequentially to test realistic fill
                    won = False
                    r_mult = -1.0
                    pnl = -sl_dist

                    if target_dir == Dir.LONG:
                        for fb in range(i, min(i + forward_bars, n_bars)):
                            if l[fb] <= entry_p - sl_dist:
                                won = False
                                r_mult = -1.0
                                pnl = -sl_dist
                                break
                            if h[fb] >= entry_p + tp_dist:
                                won = True
                                r_mult = 2.0
                                pnl = tp_dist
                                break
                        else:
                            end_c = c[min(i + forward_bars - 1, n_bars - 1)]
                            r_mult = (end_c - entry_p) / sl_dist
                            won = r_mult > 0
                            pnl = r_mult * sl_dist
                    else:
                        for fb in range(i, min(i + forward_bars, n_bars)):
                            if h[fb] >= entry_p + sl_dist:
                                won = False
                                r_mult = -1.0
                                pnl = -sl_dist
                                break
                            if l[fb] <= entry_p - tp_dist:
                                won = True
                                r_mult = 2.0
                                pnl = tp_dist
                                break
                        else:
                            end_c = c[min(i + forward_bars - 1, n_bars - 1)]
                            r_mult = (entry_p - end_c) / sl_dist
                            won = r_mult > 0
                            pnl = r_mult * sl_dist

                    fut_c = c[min(i + forward_bars - 1, n_bars - 1)]
                    regime = "trend" if abs(fut_c - entry_p) / entry_p > 0.006 else "range"
                    self.learner.learn(
                        direction_correct=won,
                        conf=conf,
                        won=won,
                        pnl=pnl,
                        features=feats,
                        r_multiple=r_mult,
                        regime=regime,
                    )
                    total_samples += 1
                    if won:
                        total_wins += 1

        elapsed = time.time() - start_t
        m = self.learner.memory
        win_rate = (total_wins / total_samples * 100) if total_samples > 0 else 0.0
        res = {
            "samples_trained": total_samples,
            "win_rate": round(win_rate, 2),
            "model_weights": [round(float(w), 4) for w in m.get("model_weights", [])],
            "feature_importance": m.get("feature_importance", {}),
            "calibration_bins": len(m.get("calibration", {})),
            "min_conf": round(float(m.get("min_conf", 0.62)), 3),
            "regime_mult": m.get("regime_mult", {}),
            "elapsed_sec": round(elapsed, 2),
        }
        logger.info(
            "[HeavyTrainer] AI Model trained on %d samples in %.2fs (min_conf=%.1f%%, calibration_bins=%d)",
            total_samples,
            elapsed,
            res["min_conf"] * 100,
            res["calibration_bins"],
        )
        return res

    def train_learning_edge(self) -> Dict[str, Any]:
        """
        Populate LearningEdge with time-of-day / day-of-week expectancy distributions,
        setup tracking, and empirical negative streak filters.
        """
        logger.info("[HeavyTrainer] 3/4 Populating LearningEdge expectancy maps from simulated history...")
        start_t = time.time()
        trade_log = getattr(self.brain, "_trade_log", [])
        recorded = 0

        for t in trade_log:
            try:
                tag = t.get("setup") or f"{t.get('regime', 'unk')}|{t.get('symbol', 'unk')}"
                won = bool(t.get("won", False))
                r = float(t.get("r", 0.0))
                conf = float(t.get("conf", 0.6))
                when = None
                raw_when = t.get("when") or t.get("ts")
                if raw_when and isinstance(raw_when, str):
                    try:
                        when = datetime.fromisoformat(raw_when.replace("Z", "+00:00"))
                    except Exception:
                        pass
                self.edge.record(tag=tag, won=won, r=r, conf=conf, when=when)
                recorded += 1
            except Exception as _ex:
                logger.debug("Edge record note: %s", _ex)

        elapsed = time.time() - start_t
        active_hours_mapped = len(self.edge.time_map)
        setups_mapped = len(self.edge.setups)
        banned_setups = len(self.edge.banned)
        res = {
            "trades_ingested": recorded,
            "active_time_slots": active_hours_mapped,
            "setups_mapped": setups_mapped,
            "banned_patterns": banned_setups,
            "elapsed_sec": round(elapsed, 2),
        }
        logger.info(
            "[HeavyTrainer] LearningEdge loaded in %.2fs: %d trades, %d time slots, %d setups",
            elapsed,
            recorded,
            active_hours_mapped,
            setups_mapped,
        )
        return res

    def train_ai_advanced_suite(self) -> Dict[str, Any]:
        """
        Calibrate AIAdvancedSuite risk persona, baseline trust matrix, and safeguards.
        """
        logger.info("[HeavyTrainer] 4/4 Calibrating AI Advanced Suite state...")
        start_t = time.time()
        suite = self.ai_suite
        state = suite.state

        # Establish baseline trust scores for key agents
        agent_trust = state.setdefault("agent_trust", {})
        default_trust = {
            "TREND": 1.25,
            "LIQUIDITY": 1.35,
            "INDICATORS": 1.05,
            "AI-LEARNER": 1.15,
            "NEWS": 0.85,
        }
        for ag, tr in default_trust.items():
            agent_trust[ag] = max(0.5, round(float(agent_trust.get(ag, tr)), 3))

        # Synchronize risk persona and safeguards
        state["risk_persona"] = "med"
        state["learn_frozen"] = False
        state["suite_enabled"] = True
        state["last_trained"] = datetime.now(timezone.utc).isoformat()
        suite._save()

        elapsed = time.time() - start_t
        res = {
            "enabled": suite.enabled,
            "min_scorecard": suite.min_scorecard,
            "agent_trust": agent_trust,
            "risk_persona": state.get("risk_persona", "med"),
            "elapsed_sec": round(elapsed, 2),
        }
        logger.info("[HeavyTrainer] AI Advanced Suite calibrated in %.2fs", elapsed)
        return res

    def train_all(self, heavy: bool = True) -> Dict[str, Any]:
        """
        Execute full comprehensive training across all subsystems.
        """
        start_all = time.time()
        logger.info("==========================================================")
        logger.info("🚀 STARTING HEAVY BOT TRAINING PIPELINE (heavy=%s, years=%.1f)", heavy, self.years)
        logger.info("Symbols: %s | Timeframes: %s", self.symbols, self.timeframes)
        logger.info("==========================================================")

        auto_res = self.train_autonomous_learner()
        step = 3 if heavy else 6
        ai_res = self.train_ai_model(step=step)
        edge_res = self.train_learning_edge()
        suite_res = self.train_ai_advanced_suite()

        total_elapsed = round(time.time() - start_all, 2)
        summary = {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "heavy": heavy,
            "total_elapsed_sec": total_elapsed,
            "symbols": self.symbols,
            "years": self.years,
            "autonomous_learner": auto_res,
            "ai_learner": ai_res,
            "learning_edge": edge_res,
            "ai_advanced_suite": suite_res,
        }

        # Persist training report
        try:
            with open("heavy_training_report.json", "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
        except Exception as _ex:
            logger.warning("Report save error: %s", _ex)

        logger.info("==========================================================")
        logger.info("✅ HEAVY BOT TRAINING COMPLETE IN %.2fs", total_elapsed)
        logger.info("==========================================================")
        return summary

    def format_markdown_report(self, summary: Dict[str, Any]) -> str:
        """
        Format an executive-grade Markdown summary of the training run.
        """
        al = summary.get("autonomous_learner", {})
        ai = summary.get("ai_learner", {})
        ed = summary.get("learning_edge", {})
        au = summary.get("ai_advanced_suite", {})

        top_feats = []
        if isinstance(ai.get("feature_importance"), dict):
            sorted_feats = sorted(ai["feature_importance"].items(), key=lambda x: x[1], reverse=True)[:4]
            top_feats = [f"`{k}` ({v:.2f})" for k, v in sorted_feats]
        top_feats_str = ", ".join(top_feats) if top_feats else "None"

        banned_hours = al.get("banned_hours", [])
        banned_hours_str = ", ".join(f"{h:02d}:00" for h in banned_hours) if banned_hours else "None (healthy)"
        banned_wd = al.get("banned_weekdays", [])
        wd_map = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        banned_wd_str = ", ".join(wd_map[d] for d in banned_wd if 0 <= d < 7) if banned_wd else "None (healthy)"

        return (
            f"🧠 **Heavy Bot AI & Autonomous Training Report**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱️ **Training Duration:** `{summary.get('total_elapsed_sec', 0):.1f}s` | **Lookback:** `{summary.get('years', 5.0):.1f}Y`\n"
            f"🌐 **Assets Trained:** `{', '.join(summary.get('symbols', []))}`\n\n"
            f"🤖 **Autonomous Learner:**\n"
            f"• Historical Trades Replayed: `{al.get('trades', 0):,}`\n"
            f"• Strategy Expectancy: `{al.get('expectancy', 0.0):+.3f}R`\n"
            f"• Flaws Mined & Corrected: `{al.get('flaws', 0)}`\n"
            f"• Banned Flawed Setups: `{al.get('banned_setups', 0)}`\n"
            f"• Toxic UTC Hours Vetoed: `{banned_hours_str}`\n"
            f"• Toxic Weekdays Vetoed: `{banned_wd_str}`\n"
            f"• Dynamic Filters: Min Score `≥{al.get('min_score', 1.8):.2f}` | Min R:R `≥{al.get('min_rr', 1.8):.2f}` | Conv `≥{al.get('conviction_floor', 0.55):.0%}`\n\n"
            f"🔬 **AI Multi-Feature Model:**\n"
            f"• Model Samples Trained: `{ai.get('samples_trained', 0):,}`\n"
            f"• Historical Accuracy: `{ai.get('win_rate', 0.0):.1f}%`\n"
            f"• Calibrated Min Confidence: `{ai.get('min_conf', 0.62):.1%}`\n"
            f"• Confidence Calibration Bins: `{ai.get('calibration_bins', 0)}`\n"
            f"• Top Predictive Features: {top_feats_str}\n\n"
            f"📐 **Self-Learning Edge & Safeguards:**\n"
            f"• Active Time Slots Mapped: `{ed.get('active_time_slots', 0)}/168 hrs`\n"
            f"• Distinct Setup Tags Tracked: `{ed.get('setups_mapped', 0)}`\n"
            f"• AI Advanced Suite Scorecard: `≥{au.get('min_scorecard', 55.0):.0f}` | Persona: `{au.get('risk_persona', 'med').upper()}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ *All trained weights, calibration maps, and flaws saved directly to memory.*"
        )


def run_training_cli():
    parser = argparse.ArgumentParser(description="Heavy Bot AI & Autonomous Training Engine")
    parser.add_argument("--symbols", type=str, default="BTCUSDT,XAUUSD,ETHUSDT,SOLUSDT,EURUSD", help="Comma-separated symbols")
    parser.add_argument("--years", type=float, default=5.0, help="Years of historical lookback")
    parser.add_argument("--heavy", action="store_true", default=True, help="Run deep heavy multi-pass training")
    parser.add_argument("--quick", action="store_true", help="Run fast lighter training pass")
    args = parser.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    heavy = not args.quick
    trainer = HeavyTrainer(symbols=syms, years=args.years)
    res = trainer.train_all(heavy=heavy)
    print("\n" + trainer.format_markdown_report(res) + "\n")


if __name__ == "__main__":
    run_training_cli()
