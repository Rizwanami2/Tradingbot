#!/usr/bin/env python3
"""
Improved News Agent
===================
- ForexFactory high-impact calendar with blackout
- Crypto RSS sentiment with shock detection
- Caching to avoid rate limits
"""

import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Tuple, Optional
from enum import Enum
from dataclasses import dataclass
import requests
import xml.etree.ElementTree as ET


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


class NewsAgent:
    name = "NEWS"
    FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    BTC_FEEDS = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
    ]

    CAL_KEYWORDS = [
        "fomc", "federal funds", "fed chair", "powell", "cpi", "consumer price",
        "non-farm", "nonfarm", "nfp", "unemployment", "ppi", "producer price",
        "retail sales", "gdp", "pce", "interest rate", "fomc member", "treasury",
        "jackson hole", "beige book",
    ]

    BULL_WORDS = [
        "etf inflow", "record inflow", "rate cut", "rate cuts", "adoption",
        "surge", "rally", "soars", "breakout", "all-time high", "ath",
        "approved", "accumulation", "bullish", "partnership", "institutional",
    ]

    BEAR_WORDS = [
        "etf outflow", "rate hike", "hack", "hacked", "exploit", "ban",
        "banned", "lawsuit", "sec charges", "collapse", "liquidation", "crash",
        "fraud", "downgrade", "outflow", "bearish", "selloff", "insolvency",
        "bankruptcy", "probe",
    ]

    SHOCK_WORDS = [
        "hack", "hacked", "exploit", "ban", "collapse", "sec charges",
        "insolvency", "emergency", "default", "bankruptcy", "halt",
    ]

    def __init__(self, symbol: str = "XAUUSD", window_min: int = 30, enabled: bool = True):
        self.symbol = symbol.upper()
        self.window = timedelta(minutes=window_min)
        self.enabled = enabled
        self._cal: Optional[List[Dict]] = None
        self._cal_ts: float = 0.0
        self._heads: Optional[List[str]] = None
        self._heads_ts: float = 0.0
        self.last_msg: str = ""

    def get_calendar(self) -> List[Dict]:
        if time.time() - self._cal_ts < 900 and self._cal is not None:
            return self._cal

        events = []
        try:
            resp = requests.get(
                self.FF_URL,
                headers={"User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)"},
                timeout=10,
            )
            if resp.status_code == 200:
                now = datetime.now(timezone.utc)
                for e in resp.json():
                    if str(e.get("impact", "")).lower() != "high":
                        continue
                    title = str(e.get("title", "")).lower()
                    if not any(k in title for k in self.CAL_KEYWORDS):
                        continue

                    if self.symbol.startswith(("XAU", "GOLD", "XAG")):
                        if not any(x in title for x in ("usd", "gold", "fed", "cpi", "nfp", "fomc")):
                            continue

                    try:
                        dt = datetime.fromisoformat(str(e["date"]).replace("Z", "+00:00"))
                    except Exception:
                        continue

                    if dt > now - timedelta(days=1):
                        events.append({
                            "dt": dt,
                            "title": e.get("title", ""),
                            "impact": "high",
                            "currency": e.get("country", ""),
                        })
                self._cal = events
                self._cal_ts = time.time()
        except Exception as ex:
            self.last_msg = f"Calendar feed notice: {ex}"
            if self._cal is None:
                self._cal = []

        return self._cal or []

    def blackout(self, ts: datetime) -> Tuple[bool, str]:
        if not self.enabled:
            return False, ""

        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        for ev in self.get_calendar():
            delta_sec = abs((ev["dt"] - ts).total_seconds())
            if delta_sec <= self.window.total_seconds():
                msg = f"News blackout: {ev['title']}"
                self.last_msg = msg
                return True, msg
        return False, ""

    def crypto_headlines(self) -> List[str]:
        if time.time() - self._heads_ts < 600 and self._heads is not None:
            return self._heads

        heads: List[str] = []
        for url in self.BTC_FEEDS:
            try:
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
                if r.status_code == 200:
                    root = ET.fromstring(r.content)
                    for item in root.iter("item"):
                        title = item.findtext("title")
                        if title:
                            heads.append(title.lower())
                        if len(heads) >= 40:
                            break
            except Exception:
                continue

        self._heads = heads[:60]
        self._heads_ts = time.time()
        return self._heads

    def sentiment(self) -> Tuple[Dir, float, bool]:
        if not self.enabled:
            return Dir.FLAT, 0.0, False

        heads = self.crypto_headlines()
        if not heads:
            return Dir.FLAT, 0.0, False

        bull = sum(1 for h in heads for w in self.BULL_WORDS if w in h)
        bear = sum(1 for h in heads for w in self.BEAR_WORDS if w in h)
        shock = any(any(w in h for w in self.SHOCK_WORDS) for h in heads)

        if shock:
            return Dir.FLAT, 0.0, True

        net = bull - bear
        if net >= 3:
            strength = min(0.55, 0.18 + net * 0.05)
            return Dir.LONG, strength, False
        if net <= -3:
            strength = min(0.55, 0.18 + abs(net) * 0.05)
            return Dir.SHORT, strength, False

        return Dir.FLAT, 0.0, False

    def vote(self) -> Vote:
        direction, strength, shock = self.sentiment()

        if shock:
            return Vote(self.name, Dir.FLAT, 0.0, "Shock headlines detected → Stay FLAT")
        if direction == Dir.FLAT:
            return Vote(self.name, Dir.FLAT, 0.0, "Neutral news sentiment")

        label = "Bullish" if direction == Dir.LONG else "Bearish"
        return Vote(
            self.name, direction, strength,
            f"{label} headline sentiment (strength {strength:.0%})",
        )

    def summary(self) -> str:
        events = self.get_calendar()
        upcoming = [e for e in events if e["dt"] > datetime.now(timezone.utc)]
        return (
            f"NewsAgent | Enabled: {self.enabled} | "
            f"Upcoming high-impact: {len(upcoming)} | "
            f"Last: {self.last_msg or '—'}"
        )


if __name__ == "__main__":
    agent = NewsAgent(symbol="XAUUSD", window_min=30)
    print(agent.summary())
    print("Blackout now?", agent.blackout(datetime.now(timezone.utc)))
    print("Vote:", agent.vote())
