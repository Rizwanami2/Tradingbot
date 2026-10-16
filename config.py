"""
Configuration loader for the Trading Bot.
Loads configuration from environment variables and an optional .env file.
"""

import os
import sys
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

from telegram_auth import parse_allowed_user_ids

# Empty allowlist intentionally denies all commands except /start and /help.
TELEGRAM_ALLOWED_USER_IDS = parse_allowed_user_ids(
    os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
)

# Explicit test-only mode allows mock broker fallback during pytest collection.
# Production remains fail-closed unless BOT_TESTING is deliberately set.
TESTING = os.getenv("BOT_TESTING", "false").strip().lower() in ("true", "1", "yes", "on")




GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or ""
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

DEFAULT_SYMBOL = os.getenv("BOT_SYMBOL", "XAUUSD").upper()
DEFAULT_DATA_SOURCE = os.getenv("BOT_DATA_SOURCE", "ccxt").lower()
DEFAULT_EXCHANGE = os.getenv("BOT_EXCHANGE", "binance").lower()
TRADINGVIEW_EXCHANGE = os.getenv("TRADINGVIEW_EXCHANGE", "")
TRADINGVIEW_SCREENER = os.getenv("TRADINGVIEW_SCREENER", "")
POLL_INTERVAL = _safe_int(os.getenv("BOT_POLL"), 10)
FAST_POLL_INTERVAL = _safe_int(os.getenv("BOT_FAST_POLL"), 3)
def find_default_mt5_path() -> str:
    """Auto-detect standard MetaTrader 5 desktop terminal on Windows / AWS EC2."""
    if sys.platform != "win32":
        return ""
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\MetaTrader 5\terminal64.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\MetaTrader 5\terminal64.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\MetaTrader 5\terminal64.exe"),
        r"C:\Program Files\MetaTrader 5\terminal64.exe",
        r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return ""


# MetaTrader 5 (MT5) Exclusive Live Execution Configuration
MT5_ENABLED = os.getenv("MT5_ENABLED", "false").lower() in ("true", "1", "yes", "on")
MT5_MODE = os.getenv("MT5_MODE", "demo").lower()
MT5_LOGIN = _safe_int(os.getenv("MT5_LOGIN"), 0)
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "MetaQuotes-Demo")
MT5_PATH = os.getenv("MT5_PATH") or find_default_mt5_path()
MT5_MAGIC = _safe_int(os.getenv("MT5_MAGIC"), 112233)
MT5_GATEWAY_URL = os.getenv("MT5_GATEWAY_URL", "")

# Exclusive Broker Mode: MT5 Live only. Paper trading and all other brokers removed.
BROKER_MODE = os.getenv("BROKER_MODE") or os.getenv("BOT_BROKER_MODE") or "delta_testnet"

INITIAL_EQUITY = _safe_float(os.getenv("BOT_EQUITY"), 10000.0)
DEFAULT_RISK_PCT = _safe_float(os.getenv("BOT_RISK"), 0.20)
DEFAULT_LEVERAGE = _safe_float(os.getenv("BOT_LEVERAGE"), 20.0)
AUTOTRADE_ENABLED = os.getenv("BOT_AUTOTRADE", "false").lower() in ("true", "1", "yes", "on")
AUTOTRADE_LIVE_OPT_IN = os.getenv("BOT_AUTOTRADE_LIVE_OPT_IN", "false").lower() in ("true", "1", "yes", "on")


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
EXCHANGE_PASSWORD = os.getenv("EXCHANGE_PASSWORD", "")
EXCHANGE_TESTNET = os.getenv("EXCHANGE_TESTNET", "false").lower() in ("true", "1", "yes", "on")

# Native OANDA v20 REST Settings (Deprecated - MT5 Live is exclusive broker)
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_API_TOKEN = os.getenv("OANDA_API_TOKEN", "")
OANDA_ENVIRONMENT = os.getenv("OANDA_ENVIRONMENT", "practice").lower()

# Native Alpaca REST Settings (Deprecated - MT5 Live is exclusive broker)
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET", "")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() in ("true", "1", "yes", "on")

# Native Delta Exchange India REST Broker Integration (Autotrade Enabled)
DELTA_EXCHANGE_API_KEY = os.getenv("DELTA_EXCHANGE_API_KEY") or EXCHANGE_API_KEY or ""
DELTA_EXCHANGE_API_SECRET = os.getenv("DELTA_EXCHANGE_API_SECRET") or EXCHANGE_API_SECRET or ""
DELTA_EXCHANGE_BASE_URL = os.getenv("DELTA_EXCHANGE_BASE_URL", "https://cdn-ind.testnet.deltaex.org")
DELTA_EXCHANGE_TESTNET = os.getenv("DELTA_EXCHANGE_TESTNET", "true").lower() in ("true", "1", "yes", "on")
DELTA_EXCHANGE_DEFAULT_LEVERAGE = _safe_float(os.getenv("DELTA_EXCHANGE_DEFAULT_LEVERAGE") or os.getenv("DELTA_DEFAULT_LEVERAGE"), 1.0)
DELTA_EXCHANGE_AUTO_LEVERAGE = (os.getenv("DELTA_EXCHANGE_AUTO_LEVERAGE") or os.getenv("DELTA_AUTO_LEVERAGE", "false")).lower() in ("true", "1", "yes", "on")
DELTA_EXCHANGE_MAX_LEVERAGE = _safe_float(os.getenv("DELTA_EXCHANGE_MAX_LEVERAGE") or os.getenv("DELTA_MAX_LEVERAGE"), 1.0)

MAX_OPEN_TRADES = _safe_int(os.getenv("BOT_MAX_OPEN_TRADES"), 2)
ALLOWED_TRADING_SYMBOLS = {"BTCUSDT", "XAUUSD", "BTCUSD", "XAUTUSD", "PAXGUSDT", "GOLD", "XAU", "BTC", "ETHUSD", "ETHUSDT"}


def is_trading_allowed(symbol: str) -> bool:
    """Returns True only if the symbol corresponds to Gold or Bitcoin."""
    s = (symbol or "").strip().upper().replace("/", "").replace("-", "").replace(":", "").replace("_", "")
    if any(x in s for x in ("BTC", "BITCOIN", "XBT")):
        return True
    if any(x in s for x in ("XAU", "GOLD", "PAXG", "XAUT")):
        return True
    return False


def normalize_trade_symbol(symbol: str) -> str:
    """Normalize symbol to standard pair name (BTCUSDT for Bitcoin, XAUUSD for Gold)."""
    s = (symbol or "").strip().upper().replace("/", "").replace("-", "").replace(":", "").replace("_", "")
    if any(x in s for x in ("BTC", "BITCOIN", "XBT")):
        return "BTCUSDT"
    if any(x in s for x in ("XAU", "GOLD", "PAXG", "XAUT")):
        return "XAUUSD"
    if s.endswith("USDT"):
        return s
    if s.endswith("USD"):
        return s + "T"
    return s or "BTCUSDT"


_WATCHLIST_PERSIST_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".runtime",
    "autotrade_watchlist.json",
)


def _load_persisted_watchlist() -> Optional[list]:
    try:
        p = Path(_WATCHLIST_PERSIST_FILE)
        if p.is_file():
            import json
            data = json.loads(p.read_text(encoding="utf-8"))
            syms = data.get("symbols")
            if isinstance(syms, list) and syms:
                normed = []
                for s in syms:
                    if is_trading_allowed(s):
                        n = normalize_trade_symbol(s)
                        if n not in normed:
                            normed.append(n)
                if normed:
                    return normed
    except Exception:
        pass
    return None


def _save_persisted_watchlist(symbols: list) -> None:
    try:
        p = Path(_WATCHLIST_PERSIST_FILE)
        p.parent.mkdir(parents=True, exist_ok=True)
        import json
        from datetime import datetime, timezone
        payload = {
            "symbols": symbols,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


def get_autotrade_symbols() -> list:
    """Return currently active autotrade symbols."""
    global AUTOTRADE_SYMBOLS
    return list(AUTOTRADE_SYMBOLS)


def set_autotrade_symbols(symbols, persist: bool = True) -> list:
    """
    Set active autotrade symbols (e.g. ['BTCUSDT'], ['XAUUSD'], or ['BTCUSDT', 'XAUUSD']).
    Supports string input: 'btc', 'gold', 'both', 'btc,gold'.
    Returns the normalized active symbols list.
    """
    global AUTOTRADE_SYMBOLS
    if isinstance(symbols, str):
        s_lower = symbols.strip().lower()
        if s_lower in ("btc", "bitcoin", "btcusdt", "btcusd", "xbt"):
            raw_list = ["BTCUSDT"]
        elif s_lower in ("gold", "xau", "xauusd", "xaut", "xautusd", "paxg"):
            raw_list = ["XAUUSD"]
        elif s_lower in ("both", "all", "btc,gold", "gold,btc", "btcusdt,xauusd"):
            raw_list = ["BTCUSDT", "XAUUSD"]
        else:
            raw_list = [x.strip() for x in symbols.split(",") if x.strip()]
    elif isinstance(symbols, (list, tuple, set)):
        raw_list = list(symbols)
    else:
        raw_list = ["BTCUSDT", "XAUUSD"]

    normed: list = []
    for s in raw_list:
        if is_trading_allowed(str(s)):
            n = normalize_trade_symbol(str(s))
            if n not in normed:
                normed.append(n)

    if not normed:
        normed = ["BTCUSDT", "XAUUSD"]

    AUTOTRADE_SYMBOLS = normed
    if persist:
        _save_persisted_watchlist(normed)
    return list(normed)


_AUTOTRADE_SYMBOLS_RAW = os.getenv(
    "BOT_AUTOTRADE_SYMBOLS",
    "BTCUSDT,XAUUSD",
)
AUTOTRADE_SYMBOLS = [
    normalize_trade_symbol(s)
    for s in (s.strip().upper() for s in _AUTOTRADE_SYMBOLS_RAW.split(",") if s.strip())
    if is_trading_allowed(s)
] or ["BTCUSDT", "XAUUSD"]

if "pytest" not in sys.modules and not os.getenv("PYTEST_CURRENT_TEST"):
    _persisted = _load_persisted_watchlist()
    if _persisted:
        AUTOTRADE_SYMBOLS = _persisted


SCALP_TAKE_PROFIT_USD = _safe_float(os.getenv("BOT_SCALP_TP_USD"), 2.5)
SCALP_TAKE_PROFIT_PCT = _safe_float(os.getenv("BOT_SCALP_TP_PCT"), 0.10)
SCALP_STOP_LOSS_USD = _safe_float(os.getenv("BOT_SCALP_SL_USD"), 3.0)
SCALP_STOP_LOSS_PCT = _safe_float(os.getenv("BOT_SCALP_SL_PCT"), 0.12)
SCALP_ENABLED = os.getenv("BOT_SCALP", "true").lower() in ("true", "1", "yes", "on")
CURRENT_PROFILE = os.getenv("BOT_PROFILE", "scalp").lower()
