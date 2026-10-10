"""
Configuration loader for the Trading Bot.
Loads configuration from environment variables and an optional .env file.
"""

import os
from pathlib import Path


def load_dotenv(env_path: str = ".env"):
    """Simple, dependency-free .env file loader."""
    path = Path(env_path)
    if not path.is_file():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception as e:
        print(f"[Config] Warning: could not read {env_path}: {e}")


# Load .env file if present in workspace root
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Telegram settings
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or ""

# Gemini AI settings
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or ""
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Trading engine settings
DEFAULT_SYMBOL = os.getenv("BOT_SYMBOL", "XAUTUSD").upper()
DEFAULT_DATA_SOURCE = os.getenv("BOT_DATA_SOURCE", "tradingview").lower()
DEFAULT_EXCHANGE = os.getenv("BOT_EXCHANGE", "binance").lower()
TRADINGVIEW_EXCHANGE = os.getenv("TRADINGVIEW_EXCHANGE", "")
TRADINGVIEW_SCREENER = os.getenv("TRADINGVIEW_SCREENER", "")
POLL_INTERVAL = int(os.getenv("BOT_POLL", "30"))
INITIAL_EQUITY = float(os.getenv("BOT_EQUITY", "10000.0"))
DEFAULT_RISK_PCT = float(os.getenv("BOT_RISK", "0.18"))
DEFAULT_LEVERAGE = float(os.getenv("BOT_LEVERAGE", "100.0"))
AUTOTRADE_ENABLED = os.getenv("BOT_AUTOTRADE", "true").lower() in ("true", "1", "yes", "on")

# News settings
NEWS_ENABLED = os.getenv("BOT_NEWS", "true").lower() in ("true", "1", "yes", "on")
NEWS_WINDOW_MIN = int(os.getenv("BOT_NEWS_WINDOW", "30"))

# Exchange APIs
DELTA_API = os.getenv("DELTA_API", "https://api.india.delta.exchange/v2/tickers")
EXCHANGE_API_KEY = os.getenv("EXCHANGE_API_KEY", "")
EXCHANGE_API_SECRET = os.getenv("EXCHANGE_API_SECRET", "")
