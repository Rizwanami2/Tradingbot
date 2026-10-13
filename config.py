"""
Configuration loader for the Trading Bot.
Loads configuration from environment variables and an optional .env file.
"""

import os
from pathlib import Path
from typing import Optional


def load_dotenv(env_path: str = ".env"):
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


def _safe_int(val: Optional[str], default: int) -> int:
    if val is None or str(val).strip() == "":
        return default
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return default


def _safe_float(val: Optional[str], default: float) -> float:
    if val is None or str(val).strip() == "":
        return default
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return default


load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or ""

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or ""
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

DEFAULT_SYMBOL = os.getenv("BOT_SYMBOL", "XAUUSD").upper()
DEFAULT_DATA_SOURCE = os.getenv("BOT_DATA_SOURCE", "ccxt").lower()
DEFAULT_EXCHANGE = os.getenv("BOT_EXCHANGE", "binance").lower()
TRADINGVIEW_EXCHANGE = os.getenv("TRADINGVIEW_EXCHANGE", "")
TRADINGVIEW_SCREENER = os.getenv("TRADINGVIEW_SCREENER", "")
POLL_INTERVAL = _safe_int(os.getenv("BOT_POLL"), 10)
FAST_POLL_INTERVAL = _safe_int(os.getenv("BOT_FAST_POLL"), 3)
# MetaTrader 5 (MT5) Configuration
MT5_ENABLED = os.getenv("MT5_ENABLED", "false").lower() in ("true", "1", "yes", "on")
MT5_MODE = os.getenv("MT5_MODE", "paper").lower()  # "paper" or "live"
MT5_LOGIN = _safe_int(os.getenv("MT5_LOGIN"), 0)
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "MetaQuotes-Demo")
MT5_PATH = os.getenv("MT5_PATH", "")
MT5_MAGIC = _safe_int(os.getenv("MT5_MAGIC"), 112233)
MT5_GATEWAY_URL = os.getenv("MT5_GATEWAY_URL", "")

BROKER_MODE = os.getenv("BOT_BROKER_MODE", "paper").lower()
if MT5_ENABLED and BROKER_MODE in ("paper", "mt5"):
    BROKER_MODE = "mt5_live" if MT5_MODE == "live" else "mt5_paper"

_equity_default = 1000.0 if BROKER_MODE in ("paper", "mt5_paper") else 10000.0
INITIAL_EQUITY = _safe_float(os.getenv("BOT_EQUITY"), _equity_default)
DEFAULT_RISK_PCT = _safe_float(os.getenv("BOT_RISK"), 0.20)
DEFAULT_LEVERAGE = _safe_float(os.getenv("BOT_LEVERAGE"), 20.0)
AUTOTRADE_ENABLED = os.getenv("BOT_AUTOTRADE", "true").lower() in ("true", "1", "yes", "on")


STATUS_FILE = os.getenv(
    "BOT_STATUS_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".runtime", "bot_status.json"),
)
DASHBOARD_HOST = os.getenv("BOT_DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = _safe_int(os.getenv("BOT_DASHBOARD_PORT"), 8765)
DASHBOARD_TOKEN = os.getenv("BOT_DASHBOARD_TOKEN", "")

NEWS_ENABLED = os.getenv("BOT_NEWS", "true").lower() in ("true", "1", "yes", "on")
NEWS_WINDOW_MIN = _safe_int(os.getenv("BOT_NEWS_WINDOW"), 30)

DELTA_API = os.getenv("DELTA_API", "https://api.india.delta.exchange/v2/tickers")
EXCHANGE_API_KEY = os.getenv("EXCHANGE_API_KEY", "")
EXCHANGE_API_SECRET = os.getenv("EXCHANGE_API_SECRET", "")

MAX_OPEN_TRADES = _safe_int(os.getenv("BOT_MAX_OPEN_TRADES"), 5)
_AUTOTRADE_SYMBOLS_RAW = os.getenv(
    "BOT_AUTOTRADE_SYMBOLS",
    "BTCUSDT,XAUUSD,ETHUSDT,SOLUSDT,BNBUSDT",
)
AUTOTRADE_SYMBOLS = [
    s.strip().upper() for s in _AUTOTRADE_SYMBOLS_RAW.split(",") if s.strip()
] or ["BTCUSDT", "XAUUSD", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

SCALP_TAKE_PROFIT_USD = _safe_float(os.getenv("BOT_SCALP_TP_USD"), 5.0)
SCALP_TAKE_PROFIT_PCT = _safe_float(os.getenv("BOT_SCALP_TP_PCT"), 0.15)
SCALP_ENABLED = os.getenv("BOT_SCALP", "true").lower() in ("true", "1", "yes", "on")
