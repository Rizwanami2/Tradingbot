"""
Gemini AI Client for Trading Assistant.
Uses Google's official Gemini REST API directly with zero SDK version issues.
Includes intelligent fallback trading assistant if GEMINI_API_KEY is not configured.
"""

import re
import requests
from typing import Any, Dict, List, Optional
import config

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

DEFAULT_SYSTEM_INSTRUCTION = """You are an elite institutional trading assistant on Telegram.
Focus on price action, SMC (Smart Money Concepts), liquidity sweeps, market structure (BOS/ChoCH),
and technical indicator confluence (RSI divergence, EMAs, MACD, ADX, Supertrend).
Emphasize strict risk management: position sizing, stop loss placement, progressive break-even,
and minimum 1:2 risk-to-reward ratio.
Keep replies concise, clear, and mobile-friendly using bullet points and bold headers.
When asked for entries, always state:
- Direction (Long / Short)
- Exact Entry Price
- Invalidation / Stop Loss
- Take Profit Targets (TP1, TP2)
- Risk:Reward Ratio
- Assumptions and key risk factors.
Always remind the user that markets carry risk and you are not providing financial advice."""


class GeminiClient:
    """Client for Google Gemini API with fallback trading intelligence."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or config.GEMINI_API_KEY
        self.model = model or config.GEMINI_MODEL
        self.histories: Dict[int, List[Dict[str, str]]] = {}

    def reset(self, chat_id: int):
        """Clear conversation history for a chat."""
        self.histories.pop(chat_id, None)

    def get_history(self, chat_id: int) -> List[Dict[str, str]]:
        """Get conversation history for a chat."""
        return self.histories.setdefault(chat_id, [])

    def ask(
        self,
        chat_id: int,
        user_text: str,
        market_context: str = "",
        system_instruction: str = DEFAULT_SYSTEM_INSTRUCTION,
    ) -> str:
        """
        Send a message to Gemini and return the assistant response.
        Appends to conversational history.
        """
        history = self.get_history(chat_id)

        # Prepend market context to user message if provided
        full_user_content = f"{market_context}\n\n{user_text}".strip() if market_context else user_text
        history.append({"role": "user", "text": full_user_content})

        # Keep rolling window of last 20 messages
        if len(history) > 20:
            history[:] = history[-20:]

        reply = ""
        if self.api_key:
            reply = self._call_gemini_api(history, system_instruction)

        # If API call failed or no API key, use trading intelligence fallback
        if not reply:
            reply = self._generate_fallback_response(user_text, market_context)

        history.append({"role": "model", "text": reply})
        return reply

    def generate_trade_rationale(
        self,
        trade_data: Dict[str, Any],
        market_context: str = "",
    ) -> str:
        """
        Generate an institutional AI trade rationale when an order fills.
        Uses Gemini API if configured, or deterministic technical intelligence.
        """
        sym = trade_data.get("symbol", "ASSET")
        dir_val = str(trade_data.get("direction", "BUY")).upper()
        entry = float(trade_data.get("entry", trade_data.get("entry_level", trade_data.get("price", 0.0))) or 0.0)
        sl = float(trade_data.get("stop_loss", trade_data.get("sl", 0.0)) or 0.0)
        tp = float(trade_data.get("take_profit", trade_data.get("tp1", trade_data.get("tp", 0.0))) or 0.0)
        lots = float(trade_data.get("lots", trade_data.get("size", trade_data.get("volume", 0.0))) or 0.0)
        reason = str(trade_data.get("reason", trade_data.get("setup_reason", "")) or "Multi-Agent Consensus")
        regime = str(trade_data.get("regime", "") or "Market Expansion")

        # Try real Gemini API if key is present
        if self.api_key:
            prompt = (
                f"You are a Senior Institutional Trade Execution Analyst. An automated trading order has just filled.\n"
                f"Trade Details:\n"
                f"- Symbol: {sym}\n"
                f"- Direction: {dir_val}\n"
                f"- Fill Price: {entry}\n"
                f"- Stop Loss: {sl}\n"
                f"- Take Profit (TP1): {tp}\n"
                f"- Size: {lots} lots\n"
                f"- Strategy Reason / Agent Setup: {reason}\n"
                f"- Market Regime: {regime}\n\n"
                f"Write a crisp, institutional trade fill rationale (under 120 words) for the client on Telegram.\n"
                f"Use bullet points:\n"
                f"• **Thesis:** core technical rationale & market structure\n"
                f"• **Confluence:** alignment across signals / institutional patterns\n"
                f"• **Invalidation:** exact price level where thesis is invalidated\n"
                f"• **Management Plan:** risk reduction (e.g. break-even threshold) and target path\n"
            )
            try:
                reply = self._call_gemini_api(
                    [{"role": "user", "text": prompt}],
                    system_instruction="You are an elite quantitative trading execution analyst providing real-time fill rationales.",
                )
                if reply:
                    return f"🧠 **GEMINI AI TRADE RATIONALE**\n\n{reply.strip()}"
            except Exception:
                pass

        # High quality technical fallback rationale
        risk_dist = abs(entry - sl) if sl and entry else 0.0
        tp_dist = abs(tp - entry) if tp and entry else 0.0
        rr_ratio = round(tp_dist / risk_dist, 2) if risk_dist > 0 else 2.0

        is_long = any(x in dir_val for x in ("BUY", "LONG", "1"))
        invalidation_note = f"Break below ${sl:,.2f}" if is_long else f"Break above ${sl:,.2f}"
        if not sl:
            invalidation_note = "Tight trailing micro-stop threshold"

        be_level = round(entry + (0.5 * (tp - entry) if tp else (risk_dist if is_long else -risk_dist)), 2) if entry else 0.0

        tp_str = f"`${tp:,.2f}`" if tp else "Dynamic trailing TP"
        return (
            f"🧠 **AI TRADE RATIONALE: {sym} {dir_val}**\n\n"
            f"• **Thesis:** Institutional entry executed at `${entry:,.2f}` based on {reason}.\n"
            f"• **Confluence:** Setup aligned with prevailing {regime} regime, offering favorable asymmetry ({rr_ratio}R).\n"
            f"• **Invalidation:** {invalidation_note} structural boundary.\n"
            f"• **Management Plan:** Lock Break-Even at `${be_level:,.2f}`, primary objective at {tp_str}.\n"
        )

    def _call_gemini_api(self, history: List[Dict[str, str]], system_instruction: str) -> str:
        """Call Google Gemini REST API."""
        models_to_try = [self.model, "gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash"]
        # Deduplicate while preserving order
        seen = set()
        unique_models = []
        for m in models_to_try:
            if m and m not in seen:
                seen.add(m)
                unique_models.append(m)

        # Build contents payload
        contents = []
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": msg["text"]}]
            })

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 900,
            }
        }
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        for model_name in unique_models:
            url = f"{GEMINI_API_BASE}/{model_name}:generateContent?key={self.api_key}"
            try:
                resp = requests.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts and "text" in parts[0]:
                            return parts[0]["text"].strip()
                elif resp.status_code == 404:
                    # Model name not found on this endpoint, try next model
                    continue
                else:
                    print(f"[Gemini] Error {resp.status_code}: {resp.text[:120]}")
            except Exception as e:
                print(f"[Gemini] Request failed: {e}")

        return ""

    def _generate_fallback_response(self, user_text: str, market_context: str) -> str:
        """
        Rule-based trading assistant fallback when Gemini API key is missing or offline.
        Provides high-quality, actionable technical analysis.
        """
        text_lower = user_text.lower()

        # Extract current price if in market context
        price_match = re.search(r"(?:mark|last|price)[:\s]+([0-9,.]+)", market_context, re.IGNORECASE)
        current_price = None
        if price_match:
            try:
                current_price = float(price_match.group(1).replace(",", ""))
            except ValueError:
                pass

        if any(w in text_lower for w in ["buy", "sell", "entry", "setup", "signal", "trade"]):
            direction = "LONG" if any(w in text_lower for w in ["buy", "long"]) else "SHORT"
            if current_price:
                if direction == "LONG":
                    sl = round(current_price * 0.994, 2)
                    risk = current_price - sl
                    tp1 = round(current_price + risk * 1.5, 2)
                    tp2 = round(current_price + risk * 2.5, 2)
                else:
                    sl = round(current_price * 1.006, 2)
                    risk = sl - current_price
                    tp1 = round(current_price - risk * 1.5, 2)
                    tp2 = round(current_price - risk * 2.5, 2)

                return (
                    f"📊 **Market Setup Analysis**\n\n"
                    f"• **Asset Price:** `{current_price}`\n"
                    f"• **Bias:** {direction}\n"
                    f"• **Entry Zone:** `{current_price}`\n"
                    f"• **Stop Loss:** `{sl}` (Risk: `{risk:.2f}` pts)\n"
                    f"• **Target 1 (1.5R):** `{tp1}` (Move SL to Break-Even)\n"
                    f"• **Target 2 (2.5R):** `{tp2}` (Trail remainder)\n"
                    f"• **R:R Ratio:** 1:2.0+\n\n"
                    f"💡 *Tip: Configure GEMINI_API_KEY in `.env` for deep AI multi-timeframe reasoning!*"
                )
            return (
                "📈 **Trade Setup Guidance:**\n"
                "1. Wait for liquidity sweeps at Key Highs (PDH) or Lows (PDL).\n"
                "2. Confirm with Break of Structure (BOS) and RSI/MACD confluence.\n"
                "3. Keep Risk per trade under 0.25% - 0.5% with minimum 1:2 R:R.\n\n"
                "Use `/price` to check live quotes or `/signal` to run multi-agent voting."
            )

        if any(w in text_lower for w in ["risk", "size", "lot", "leverage", "management"]):
            return (
                "🛡️ **Institutional Risk Rules:**\n"
                "• **Base Risk:** 0.18% - 0.25% per trade.\n"
                "• **Auto Break-Even:** Lock +0.1R profit at +0.5R, full BE at +1.0R.\n"
                "• **Daily Loss Limit:** Maximum 1.5% daily drawdown before pause.\n"
                "• **Cooldown:** 3 consecutive losses = 75-minute circuit breaker.\n"
                "• **Take Profit Ladder:** 50% at 1.5R, 30% at 2.5R, trail remainder.\n\n"
                "Use `/risk` to view your current live equity, mode, and lot sizing."
            )

        if any(w in text_lower for w in ["hello", "hi", "start", "hey", "who are you"]):
            return (
                "👋 **Welcome to the Multi-Agent Trading Assistant!**\n\n"
                "I monitor market structure, technical indicators, economic news, and risk.\n\n"
                "Commands you can use:\n"
                "• `/price [SYMBOL]` — Live ticker & 24h market stats\n"
                "• `/signal [SYMBOL]` — Run 5-agent consensus analysis\n"
                "• `/status` — View bot state, equity, and active mode\n"
                "• `/risk` — Lot-sizing and risk parameters\n"
                "• `/news` — Economic calendar & crypto sentiment\n"
                "• `/autotrade [on|off]` — Live automated signal alerts\n\n"
                "Or simply ask me questions about setups, risk, or technical levels!"
            )

        # Default general response
        context_hint = f"\n\n**Market Context:** {market_context}" if market_context else ""
        return (
            f"🤖 **Trading Assistant:**\n"
            f"Received: \"{user_text}\"\n{context_hint}\n\n"
            f"• Market analysis: Maintain discipline and wait for confluence before entry.\n"
            f"• Check live market signals with `/signal`.\n"
            f"• Check prices with `/price XAUTUSD`.\n\n"
            f"💡 *Add `GEMINI_API_KEY` to `.env` to unlock full Gemini conversational capabilities.*"
        )
