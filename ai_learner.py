#!/usr/bin/env python3
"""
Upgraded AI-Learner Agent
=========================
Feature-based adaptive predictor with online weight updates
and persistent memory.

Improvements (stable online learning):
- R-multiple / expectancy-aware targets (not only win/loss)
- Learning-rate schedule + L2 weight decay + hard weight clips
- Minimum sample size before min_conf moves
- Calibration-driven confidence threshold
- Optional regime multipliers (trend vs range)
- Backward compatible with multi_agent_memory.json
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd


class Dir(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class Vote:
    agent: str
    direction: Dir
    strength: float
    reason: str = ""


class AILearnerAgent:
    """
    Adaptive multi-feature predictor.
    Learns online from trade outcomes and adjusts weights + confidence threshold.
    """

    name = "AI-LEARNER"
    MEMORY_FILE = "multi_agent_memory.json"

    BASE_LR = 0.006
    WEIGHT_DECAY = 0.001
    WEIGHT_CLIP = 8.0
    FEATURE_CLIP = 5.0
    MIN_TRADES_FOR_CONF = 12
    MIN_CONF_FLOOR = 0.54
    MIN_CONF_CEIL = 0.80
    DEFAULT_MIN_CONF = 0.62

    def __init__(self, path: Optional[str] = None):
        self.path = path or self.MEMORY_FILE
        self.memory: Dict[str, Any] = {
            "trades": 0,
            "wins": 0,
            "pnl": 0.0,
            "sum_r": 0.0,
            "min_conf": self.DEFAULT_MIN_CONF,
            "model_weights": None,
            "feature_importance": {},
            "calibration": {},
            "regime_mult": {"trend": 1.0, "range": 1.0, "unknown": 1.0},
            "version": 2,
        }
        self.feature_names = [
            "ret_5", "ret_15", "ret_60",
            "slope_25", "atr_pct",
            "rsi_norm", "adx_norm",
            "volume_z", "ema_dist", "hh_ll",
        ]
        self._load()
        self._migrate_memory()

        if self.memory["model_weights"] is None:
            self.memory["model_weights"] = [
                2.8, 1.9, 0.8,
                3.5, -1.2,
                1.1, 1.4,
                0.6, 1.8, 1.3,
            ]
        w = list(self.memory["model_weights"])
        if len(w) != len(self.feature_names):
            w = (w + [1.0] * len(self.feature_names))[: len(self.feature_names)]
            self.memory["model_weights"] = w

    def _migrate_memory(self) -> None:
        m = self.memory
        m.setdefault("sum_r", 0.0)
        m.setdefault("regime_mult", {"trend": 1.0, "range": 1.0, "unknown": 1.0})
        m.setdefault("calibration", {})
        m.setdefault("feature_importance", {})
        m.setdefault("version", 1)
        if "min_conf" not in m or m["min_conf"] is None:
            m["min_conf"] = self.DEFAULT_MIN_CONF
        try:
            m["min_conf"] = float(
                np.clip(float(m["min_conf"]), self.MIN_CONF_FLOOR, self.MIN_CONF_CEIL)
            )
        except Exception:
            m["min_conf"] = self.DEFAULT_MIN_CONF

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.memory.update(data)
            except Exception as e:
                print(f"[AI-Learner] Memory load error: {e}")

    def _save(self) -> None:
        try:
            parent = os.path.dirname(os.path.abspath(self.path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.memory, f, indent=2)
        except Exception as e:
            print(f"[AI-Learner] Memory save error: {e}")

    def _extract_features(self, df: pd.DataFrame) -> np.ndarray:
        n_features = len(self.feature_names)
        if df is None or len(df) < 80:
            return np.zeros(n_features, dtype=float)

        c = df["close"].values.astype(float)
        h = df["high"].values.astype(float)
        l = df["low"].values.astype(float)
        v = df["volume"].values.astype(float) if "volume" in df.columns else np.ones(len(c))

        def safe_ret(n: int) -> float:
            if len(c) <= n:
                return 0.0
            return (c[-1] - c[-n - 1]) / (c[-n - 1] + 1e-12)

        tr = np.maximum(
            h[1:] - l[1:],
            np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])),
        )
        atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else 0.0
        atr_pct = atr / (c[-1] + 1e-12)

        delta = np.diff(c[-16:])
        gains = delta[delta > 0]
        losses = -delta[delta < 0]
        avg_gain = float(np.mean(gains)) if len(gains) else 0.0
        avg_loss = float(np.mean(losses)) if len(losses) else 1e-9
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        rsi_norm = rsi / 100.0

        adx_proxy = min(abs(safe_ret(10)) * 400.0, 60.0) / 100.0

        vol_window = v[-20:] if len(v) >= 20 else v
        vol_mean = float(np.mean(vol_window))
        vol_std = float(np.std(vol_window)) + 1e-9
        volume_z = (v[-1] - vol_mean) / vol_std

        ema21 = pd.Series(c).ewm(span=21, adjust=False).mean().iloc[-1]
        ema_dist = (c[-1] - ema21) / (c[-1] + 1e-12)

        if len(h) > 11:
            hh = float(np.mean(h[-10:] > h[-11:-1]))
            ll = float(np.mean(l[-10:] < l[-11:-1]))
        else:
            hh, ll = 0.5, 0.5

        if len(c) >= 25:
            slope = np.polyfit(np.arange(25), c[-25:], 1)[0] / (c[-1] + 1e-12)
        else:
            slope = 0.0

        feats = np.array(
            [
                safe_ret(5),
                safe_ret(15),
                safe_ret(60),
                slope,
                atr_pct,
                rsi_norm,
                adx_proxy,
                volume_z,
                ema_dist,
                hh - ll,
            ],
            dtype=float,
        )
        feats = np.nan_to_num(feats, nan=0.0, posinf=0.0, neginf=0.0)
        return np.clip(feats, -self.FEATURE_CLIP, self.FEATURE_CLIP)

    def _regime_key(self, regime: Optional[str]) -> str:
        r = (regime or "unknown").strip().lower()
        if "trend" in r or r in ("bull", "bear", "momentum"):
            return "trend"
        if "range" in r or "chop" in r or "mean" in r:
            return "range"
        return "unknown"

    def predict(
        self, df: pd.DataFrame, regime: Optional[str] = None
    ) -> Tuple[Dir, float, float]:
        feats = self._extract_features(df)
        weights = np.array(self.memory["model_weights"], dtype=float)

        if len(weights) != len(feats):
            weights = np.ones(len(feats))

        raw_score = float(np.dot(feats, weights))
        mult = float(self.memory.get("regime_mult", {}).get(self._regime_key(regime), 1.0))
        raw_score *= float(np.clip(mult, 0.5, 1.5))

        conf = 1.0 / (1.0 + np.exp(-raw_score * 3.2))

        key = f"{round(conf * 20) / 20:.2f}"
        cal = self.memory.get("calibration", {})
        if key in cal and int(self.memory.get("trades", 0)) > 20:
            hist = float(cal[key])
            conf = 0.65 * conf + 0.35 * hist

        if conf >= 0.58:
            direction = Dir.LONG
            final_conf = conf
        elif conf <= 0.42:
            direction = Dir.SHORT
            final_conf = 1.0 - conf
        else:
            direction = Dir.FLAT
            final_conf = max(conf, 1.0 - conf)

        return direction, float(final_conf), raw_score

    def vote(self, df: pd.DataFrame, regime: Optional[str] = None) -> Vote:
        direction, conf, _ = self.predict(df, regime=regime)
        min_conf = float(self.memory.get("min_conf", self.DEFAULT_MIN_CONF))

        if direction == Dir.FLAT or conf < min_conf:
            return Vote(
                self.name,
                Dir.FLAT,
                conf,
                f"AI confidence {conf:.0%} < threshold {min_conf:.0%}",
            )
        return Vote(
            self.name,
            direction,
            conf,
            f"AI Ensemble signal (conf {conf:.0%})",
        )

    def _learning_rate(self) -> float:
        n = max(1, int(self.memory.get("trades", 0)))
        return float(self.BASE_LR * (1.0 / (1.0 + 0.015 * n)) ** 0.5)

    def _target_from_outcome(
        self,
        won: bool,
        r_multiple: Optional[float],
        direction_correct: Optional[bool],
    ) -> float:
        if r_multiple is not None:
            r = float(np.clip(r_multiple, -3.0, 3.0))
            return float(np.tanh(r / 1.25))
        if direction_correct is not None:
            return 1.0 if direction_correct else -1.0
        return 1.0 if won else -1.0

    def _update_min_conf_from_calibration(self, conf: float, won: bool) -> None:
        m = self.memory
        trades = int(m.get("trades", 0))
        if trades < self.MIN_TRADES_FOR_CONF:
            return

        wr = (float(m["wins"]) / trades) if trades else 0.5
        cur = float(m.get("min_conf", self.DEFAULT_MIN_CONF))

        if wr < 0.40 and trades >= self.MIN_TRADES_FOR_CONF:
            cur = min(self.MIN_CONF_CEIL, cur + 0.003)
        elif wr > 0.55 and trades >= self.MIN_TRADES_FOR_CONF * 2:
            cur = max(self.MIN_CONF_FLOOR, cur - 0.002)

        if (not won) and conf >= 0.70:
            cur = min(self.MIN_CONF_CEIL, cur + 0.004)
        elif won and conf <= 0.58:
            cur = max(self.MIN_CONF_FLOOR, cur - 0.003)

        m["min_conf"] = round(float(np.clip(cur, self.MIN_CONF_FLOOR, self.MIN_CONF_CEIL)), 3)

    def _update_regime_mult(self, regime: Optional[str], r_multiple: float) -> None:
        key = self._regime_key(regime)
        mults = self.memory.setdefault(
            "regime_mult", {"trend": 1.0, "range": 1.0, "unknown": 1.0}
        )
        cur = float(mults.get(key, 1.0))
        if r_multiple > 0.3:
            cur = min(1.35, cur * 1.01 + 0.005)
        elif r_multiple < -0.3:
            cur = max(0.65, cur * 0.99 - 0.005)
        mults[key] = round(cur, 3)

    def learn(
        self,
        direction_correct: bool,
        conf: float,
        won: bool,
        pnl: float,
        features: Optional[np.ndarray] = None,
        r_multiple: Optional[float] = None,
        regime: Optional[str] = None,
    ) -> None:
        m = self.memory
        m["trades"] = int(m.get("trades", 0)) + 1
        m["wins"] = int(m.get("wins", 0)) + (1 if won else 0)
        m["pnl"] = round(float(m.get("pnl", 0.0)) + float(pnl), 4)

        r_eff = r_multiple
        if r_eff is None:
            if abs(float(pnl)) <= 5.0:
                r_eff = float(pnl)
            else:
                r_eff = 1.0 if won else -1.0
        m["sum_r"] = round(float(m.get("sum_r", 0.0)) + float(r_eff), 4)

        conf_f = float(np.clip(conf, 0.0, 1.0))
        key = f"{round(conf_f * 20) / 20:.2f}"
        old = float(m.get("calibration", {}).get(key, 0.5))
        m.setdefault("calibration", {})
        m["calibration"][key] = round(old * 0.88 + (1.0 if won else 0.0) * 0.12, 4)

        if features is not None and m.get("model_weights") is not None:
            feat = np.asarray(features, dtype=float).reshape(-1)
            feat = np.nan_to_num(feat, nan=0.0, posinf=0.0, neginf=0.0)
            feat = np.clip(feat, -self.FEATURE_CLIP, self.FEATURE_CLIP)
            w = np.array(m["model_weights"], dtype=float)
            if len(w) == len(feat):
                target = self._target_from_outcome(won, r_eff, direction_correct)
                pred = float(np.tanh(np.dot(feat, w)))
                error = target - pred
                lr = self._learning_rate()
                scale = float(np.clip(abs(r_eff), 0.25, 2.0))
                w = w + (lr * scale * error) * feat
                w = w * (1.0 - self.WEIGHT_DECAY)
                w = np.clip(w, -self.WEIGHT_CLIP, self.WEIGHT_CLIP)
                m["model_weights"] = w.tolist()

                imp = m.setdefault("feature_importance", {})
                for name, val in zip(self.feature_names, np.abs(w)):
                    prev = float(imp.get(name, val))
                    imp[name] = round(0.9 * prev + 0.1 * float(val), 5)

        self._update_min_conf_from_calibration(conf_f, won)
        if regime is not None or r_eff is not None:
            self._update_regime_mult(regime, float(r_eff))

        m["version"] = 2
        self._save()

    def summary(self) -> str:
        m = self.memory
        trades = int(m.get("trades", 0))
        wr = (m["wins"] / trades * 100) if trades > 0 else 0.0
        avg_r = (float(m.get("sum_r", 0.0)) / trades) if trades > 0 else 0.0
        return (
            f"AI-Learner | Trades: {trades} | "
            f"WinRate: {wr:.1f}% | PnL: {m.get('pnl', 0):+.2f} | "
            f"AvgR: {avg_r:+.2f} | MinConf: {float(m.get('min_conf', 0.62)):.0%}"
        )


if __name__ == "__main__":
    agent = AILearnerAgent()
    print(agent.summary())

    dates = pd.date_range("2025-01-01", periods=200, freq="1min")
    np.random.seed(42)
    price = 2650 + np.cumsum(np.random.randn(200) * 0.5)
    df = pd.DataFrame(
        {
            "open": price,
            "high": price + 1.2,
            "low": price - 1.2,
            "close": price,
            "volume": np.random.randint(100, 1000, 200),
        },
        index=dates,
    )

    direction, conf, score = agent.predict(df)
    print(f"Predict → {direction.name} | conf={conf:.2f} | score={score:.3f}")
    print(agent.vote(df))
