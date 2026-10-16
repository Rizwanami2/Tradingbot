#!/usr/bin/env python3
"""
Upgraded AI-Learner Agent
=========================
Feature-based adaptive predictor with online weight updates
and persistent memory.

This module can be imported into multi_agent_trading_bot.py
or used standalone.
"""

import json
import os
from typing import Tuple, Optional
import numpy as np
import pandas as pd
from enum import Enum
from dataclasses import dataclass


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
    Adaptive momentum + multi-feature predictor.
    Learns online from trade outcomes and adjusts weights + confidence threshold.
    """

    name = "AI-LEARNER"
    MEMORY_FILE = "multi_agent_memory.json"

    def __init__(self, path: Optional[str] = None):
        self.path = path or self.MEMORY_FILE
        self.memory = {
            "trades": 0,
            "wins": 0,
            "pnl": 0.0,
            "min_conf": 0.62,
            "model_weights": None,
            "feature_importance": {},
            "calibration": {},
        }
        self.feature_names = [
            "ret_5", "ret_15", "ret_60",
            "slope_25", "atr_pct",
            "rsi_norm", "adx_norm",
            "volume_z", "ema_dist", "hh_ll",
        ]
        self._load()

        if self.memory["model_weights"] is None:
            self.memory["model_weights"] = [
                2.8, 1.9, 0.8,
                3.5, -1.2,
                1.1, 1.4,
                0.6, 1.8, 1.3,
            ]

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.memory.update(data)
            except Exception as e:
                print(f"[AI-Learner] Memory load error: {e}")

    def _save(self):
        try:
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

        feats = np.array([
            safe_ret(5), safe_ret(15), safe_ret(60),
            slope, atr_pct, rsi_norm, adx_proxy,
            volume_z, ema_dist, hh - ll,
        ], dtype=float)

        return np.nan_to_num(feats, nan=0.0, posinf=0.0, neginf=0.0)

    def predict(self, df: pd.DataFrame) -> Tuple[Dir, float, float]:
        feats = self._extract_features(df)
        weights = np.array(self.memory["model_weights"], dtype=float)

        if len(weights) != len(feats):
            weights = np.ones(len(feats))

        raw_score = float(np.dot(feats, weights))
        conf = 1.0 / (1.0 + np.exp(-raw_score * 3.2))

        key = f"{round(conf * 20) / 20:.2f}"
        if key in self.memory.get("calibration", {}) and self.memory["trades"] > 20:
            hist = self.memory["calibration"][key]
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

    def vote(self, df: pd.DataFrame) -> Vote:
        direction, conf, _ = self.predict(df)
        min_conf = self.memory.get("min_conf", 0.62)

        if direction == Dir.FLAT or conf < min_conf:
            return Vote(
                self.name, Dir.FLAT, conf,
                f"AI confidence {conf:.0%} < threshold {min_conf:.0%}",
            )
        return Vote(
            self.name, direction, conf,
            f"AI Ensemble signal (conf {conf:.0%})",
        )

    def learn(self, direction_correct: bool, conf: float, won: bool, pnl: float,
              features: Optional[np.ndarray] = None):
        m = self.memory
        m["trades"] += 1
        m["wins"] += 1 if won else 0
        m["pnl"] = round(m["pnl"] + pnl, 4)

        key = f"{round(conf * 20) / 20:.2f}"
        old = m.get("calibration", {}).get(key, 0.5)
        m.setdefault("calibration", {})
        m["calibration"][key] = round(old * 0.88 + (1.0 if won else 0.0) * 0.12, 4)

        if features is not None and m["model_weights"] is not None:
            w = np.array(m["model_weights"], dtype=float)
            if len(w) == len(features):
                target = 1.0 if won else -1.0
                pred = np.tanh(np.dot(features, w))
                error = target - pred
                w += 0.008 * error * features
                w *= 0.999
                m["model_weights"] = w.tolist()

        if won and conf > 0.72:
            m["min_conf"] = round(min(0.80, m["min_conf"] + 0.004), 3)
        elif not won and conf < 0.60:
            m["min_conf"] = round(max(0.54, m["min_conf"] - 0.007), 3)

        self._save()

    def summary(self) -> str:
        m = self.memory
        wr = (m["wins"] / m["trades"] * 100) if m["trades"] > 0 else 0.0
        return (
            f"AI-Learner | Trades: {m['trades']} | "
            f"WinRate: {wr:.1f}% | PnL: {m['pnl']:+.2f} | "
            f"MinConf: {m['min_conf']:.0%}"
        )


if __name__ == "__main__":
    agent = AILearnerAgent()
    print(agent.summary())

    dates = pd.date_range("2025-01-01", periods=200, freq="1min")
    np.random.seed(42)
    price = 2650 + np.cumsum(np.random.randn(200) * 0.5)
    df = pd.DataFrame({
        "open": price, "high": price + 1.2, "low": price - 1.2,
        "close": price, "volume": np.random.randint(100, 1000, 200),
    }, index=dates)

    direction, conf, score = agent.predict(df)
    print(f"Predict → {direction.name} | conf={conf:.2f} | score={score:.3f}")
    print(agent.vote(df))
