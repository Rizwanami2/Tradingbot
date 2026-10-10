class NewsAgent:
    name = "NEWS"

    def __init__(self, symbol: str = "XAUUSD", window_min: int = 30, enabled: bool = True):
        self.symbol = symbol.upper()
        self.window = timedelta(minutes=window_min)
        self.enabled = enabled
        self._cal_cache = None
        self._cal_ts = 0
        self._head_cache = None
        self._head_ts = 0

    def get_calendar(self) -> List[Dict]:
        if time.time() - self._cal_ts < 900 and self._cal_cache:
            return self._cal_cache
        try:
            r = requests.get(
                "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=10
            )
            events = []
            now = datetime.now(timezone.utc)
            for e in r.json():
                if str(e.get("impact", "")).lower() != "high":
                    continue
                title = e.get("title", "").lower()
                # Only relevant currencies
                if self.symbol.startswith(("XAU", "GOLD")) and "usd" not in title and "gold" not in title:
                    continue
                try:
                    dt = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
                    if abs((dt - now).total_seconds()) < 86400 * 2:
                        events.append({"dt": dt, "title": e["title"], "impact": "high"})
                except Exception:
                    continue
            self._cal_cache = events
            self._cal_ts = time.time()
            return events
        except Exception:
            return self._cal_cache or []

    def blackout(self, ts: datetime) -> Tuple[bool, str]:
        if not self.enabled:
            return False, ""
        for ev in self.get_calendar():
            delta = abs((ev["dt"] - ts).total_seconds())
            if delta <= self.window.total_seconds():
                return True, f"High-impact: {ev['title']}"
        return False, ""

    def sentiment_score(self) -> Tuple[Dir, float, bool]:
        """Returns direction, strength (0-1), shock flag"""
        if time.time() - self._head_ts < 600 and self._head_cache:
            heads = self._head_cache
        else:
            heads = []
            for url in [
                "https://www.coindesk.com/arc/outboundfeeds/rss/",
                "https://cointelegraph.com/rss",
            ]:
                try:
                    r = requests.get(url, timeout=8)
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(r.content)
                    for item in root.iter("item"):
                        t = item.findtext("title")
                        if t:
                            heads.append(t.lower())
                except Exception:
                    pass
            self._head_cache = heads[:50]
            self._head_ts = time.time()

        bull_words = ["etf inflow", "approval", "adoption", "rally", "surge", "partnership", "accumulation"]
        bear_words = ["hack", "exploit", "ban", "lawsuit", "sec charges", "outflow", "crash", "liquidation"]
        shock_words = ["hack", "exploit", "insolvency", "bankruptcy", "emergency"]

        bull = sum(1 for h in heads for w in bull_words if w in h)
        bear = sum(1 for h in heads for w in bear_words if w in h)
        shock = any(w in h for h in heads for w in shock_words)

        if shock:
            return Dir.FLAT, 0.0, True

        net = bull - bear
        if net >= 3:
            return Dir.LONG, min(0.55, 0.2 + net * 0.06), False
        if net <= -3:
            return Dir.SHORT, min(0.55, 0.2 + abs(net) * 0.06), False
        return Dir.FLAT, 0.0, False

    def vote(self) -> Vote:
        d, strength, shock = self.sentiment_score()
        if shock:
            return Vote(self.name, Dir.FLAT, 0.0, "Shock event detected → FLAT")
        if d == Dir.FLAT:
            return Vote(self.name, Dir.FLAT, 0.0, "Neutral news flow")
        label = "Bullish" if d == Dir.LONG else "Bearish"
        return Vote(self.name, d, strength, f"{label} news sentiment")
