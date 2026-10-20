#!/usr/bin/env python3
"""Improved News Agent - word-boundary matching, calendar, RSS sentiment."""

import time
import re
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


_GLOBAL_HEADS_CACHE: Optional[List[str]] = None
_GLOBAL_HEADS_TS: float = 0.0
_GLOBAL_CAL_CACHE: Optional[List[Dict]] = None
_GLOBAL_CAL_TS: float = 0.0


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
        "retail sales", "gdp", "pce", "interest rate", "treasury", "jackson hole",
    ]
    BULL_WORDS = [
        "etf inflow", "record inflow", "rate cut", "rate cuts", "adoption",
        "surge", "rally", "soars", "breakout", "all-time high", "ath",
        "approved", "accumulation", "bullish", "partnership", "institutional",
    ]
    BEAR_WORDS = [
        "etf outflow", "rate hike", "hacked", "exploit", "lawsuit",
        "sec charges", "collapse", "liquidation", "crash", "fraud", "downgrade",
        "outflow", "bearish", "selloff", "insolvency", "bankruptcy", "probe",
    ]
    SHOCK_WORDS = [
        "hacked", "exploit", "collapse", "sec charges", "insolvency",
        "emergency patch", "default", "bankruptcy", "trading halt",
        "exchange halt", "withdrawals halted",
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
        global _GLOBAL_CAL_CACHE, _GLOBAL_CAL_TS
        now = time.time()
        if _GLOBAL_CAL_CACHE is not None and (now - _GLOBAL_CAL_TS < 1800):
            return list(_GLOBAL_CAL_CACHE)
        if self._cal is not None and (now - self._cal_ts < 1800):
            return self._cal
        events = []
        try:
            resp = requests.get(self.FF_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=3.5)
            if resp.status_code == 200:
                data = resp.json()
                for item in data if isinstance(data, list) else []:
                    title = str(item.get("title") or "")
                    impact = str(item.get("impact") or "").lower()
                    if impact not in ("high", "red"):
                        continue
                    dt_raw = item.get("date") or item.get("date_utc") or ""
                    try:
                        dt = datetime.fromisoformat(str(dt_raw).replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        continue
                    events.append({"dt": dt, "title": title, "impact": impact,
                                   "currency": item.get("country") or item.get("currency") or ""})
        except Exception as e:
            self.last_msg = f"calendar error: {e}"
        self._cal = events
        self._cal_ts = now
        _GLOBAL_CAL_CACHE = events
        _GLOBAL_CAL_TS = now
        return self._cal

    def blackout(self, ts: datetime) -> Tuple[bool, str]:
        if not self.enabled:
            return False, "disabled"
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        for ev in self.get_calendar():
            dt = ev.get("dt")
            if dt is None:
                continue
            if abs((dt - ts).total_seconds()) <= self.window.total_seconds():
                msg = f"News blackout: {ev['title']}"
                self.last_msg = msg
                return True, msg
        return False, ""

    def crypto_headlines(self) -> List[str]:
        global _GLOBAL_HEADS_CACHE, _GLOBAL_HEADS_TS
        now = time.time()
        if _GLOBAL_HEADS_CACHE is not None and (now - _GLOBAL_HEADS_TS < 600):
            return list(_GLOBAL_HEADS_CACHE)
        if self._heads is not None and (now - self._heads_ts < 600):
            return self._heads

        heads: List[str] = []
        from concurrent.futures import ThreadPoolExecutor

        def _fetch_feed(url: str) -> List[str]:
            res = []
            try:
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=2.5)
                if r.status_code == 200:
                    root = ET.fromstring(r.content)
                    for item in root.iter("item"):
                        title = item.findtext("title")
                        if title:
                            res.append(title.lower())
                        if len(res) >= 30:
                            break
            except Exception:
                pass
            return res

        try:
            with ThreadPoolExecutor(max_workers=min(4, len(self.BTC_FEEDS))) as ex:
                futures = [ex.submit(_fetch_feed, u) for u in self.BTC_FEEDS]
                for f in futures:
                    try:
                        heads.extend(f.result())
                    except Exception:
                        pass
        except Exception:
            pass

        self._heads = heads[:60]
        self._heads_ts = now
        _GLOBAL_HEADS_CACHE = self._heads
        _GLOBAL_HEADS_TS = now
        return self._heads

    @staticmethod
    def _word_hit(text: str, phrase: str) -> bool:
        if not phrase or not text:
            return False
        pat = r"(?<![a-z0-9])" + re.escape(phrase.lower()) + r"(?![a-z0-9])"
        return re.search(pat, text.lower()) is not None

    def sentiment(self) -> Tuple[Dir, float, bool]:
        if not self.enabled:
            return Dir.FLAT, 0.0, False
        heads = self.crypto_headlines()
        if not heads:
            return Dir.FLAT, 0.0, False
        bull = sum(1 for h in heads for w in self.BULL_WORDS if self._word_hit(h, w))
        bear = sum(1 for h in heads for w in self.BEAR_WORDS if self._word_hit(h, w))
        shock_hits = [w for h in heads for w in self.SHOCK_WORDS if self._word_hit(h, w)]
        if len(set(shock_hits)) >= 2:
            return Dir.FLAT, 0.0, True
        net = bull - bear
        if net >= 2:
            return Dir.LONG, min(0.55, 0.18 + net * 0.05), False
        if net <= -2:
            return Dir.SHORT, min(0.55, 0.18 + abs(net) * 0.05), False
        return Dir.FLAT, 0.0, False

    def vote(self) -> Vote:
        direction, strength, shock = self.sentiment()
        if shock:
            return Vote(self.name, Dir.FLAT, 0.0, "Shock headlines detected → Stay FLAT")
        if direction == Dir.FLAT:
            n = len(self.crypto_headlines() or [])
            return Vote(self.name, Dir.FLAT, 0.0, f"Neutral news sentiment ({n} headlines scanned)")
        label = "Bullish" if direction == Dir.LONG else "Bearish"
        return Vote(self.name, direction, strength, f"{label} headline sentiment (strength {strength:.0%})")

    def report(self) -> str:
        lines = ["📰 **News Agent Report**"]
        try:
            cal = self.get_calendar()
            now = datetime.now(timezone.utc)
            upcoming = [e for e in cal if e.get("dt") and e["dt"] > now][:5]
            if upcoming:
                lines.append("\n**Upcoming high-impact:**")
                for e in upcoming:
                    dt = e["dt"].strftime("%a %H:%M UTC") if hasattr(e["dt"], "strftime") else str(e["dt"])
                    lines.append(f"• `{dt}` {e.get('currency','')} {e.get('title','')}")
            else:
                lines.append("\nNo high-impact events in calendar window.")
        except Exception as ex:
            lines.append(f"\nCalendar error: `{ex}`")
        try:
            v = self.vote()
            icon = "🟢" if v.direction == Dir.LONG else ("🔴" if v.direction == Dir.SHORT else "⚪")
            lines.append(f"\n**Sentiment:** {icon} `{v.direction.name}` — {v.reason}")
        except Exception as ex:
            lines.append(f"\nSentiment error: `{ex}`")
        try:
            heads = self.crypto_headlines() or []
            if heads:
                lines.append("\n**Top headlines:**")
                for h in heads[:5]:
                    lines.append(f"• {h[:120]}")
        except Exception:
            pass
        blk, why = self.blackout(datetime.now(timezone.utc))
        lines.append(f"\n⛔ **Blackout active:** {why}" if blk else "\n✅ No news blackout right now.")
        return "\n".join(lines)

    def summary(self) -> str:
        events = self.get_calendar()
        upcoming = [e for e in events if e["dt"] > datetime.now(timezone.utc)]
        return f"NewsAgent | Enabled: {self.enabled} | Upcoming high-impact: {len(upcoming)} | Last: {self.last_msg or '—'}"


if __name__ == "__main__":
    agent = NewsAgent(symbol="XAUUSD", window_min=30)
    print(agent.summary())
    print("Blackout now?", agent.blackout(datetime.now(timezone.utc)))
    print("Vote:", agent.vote())
    print(agent.report())
