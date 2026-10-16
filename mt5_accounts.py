#!/usr/bin/env python3
"""
MetaTrader 5 Account Management & Live Switching Engine.
=========================================================
Provides multi-account storage, credential encryption/masking, connection
verification, and seamless runtime switching between Live and Paper modes.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("MT5Accounts")

ACCOUNTS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".runtime",
    "mt5_accounts.json",
)


@dataclass
class MT5Account:
    login: int
    password: str
    server: str
    alias: str = ""
    gateway_url: str = ""
    is_live: bool = True
    added_at: str = ""
    last_used_at: str = ""

    @property
    def masked_password(self) -> str:
        if not self.password:
            return "(none)"
        if len(self.password) <= 4:
            return "••••"
        return f"{self.password[:2]}••••{self.password[-2:]}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MT5Account:
        return cls(
            login=int(data.get("login") or 0),
            password=str(data.get("password") or ""),
            server=str(data.get("server") or "MetaQuotes-Demo"),
            alias=str(data.get("alias") or ""),
            gateway_url=str(data.get("gateway_url") or "").strip(),
            is_live=bool(data.get("is_live", True)),
            added_at=str(data.get("added_at") or ""),
            last_used_at=str(data.get("last_used_at") or ""),
        )


class MT5AccountManager:
    """Manages saved MT5 live & demo accounts with disk persistence in .runtime/."""

    def __init__(self, storage_path: Optional[str] = None, storage_file: Optional[str] = None):
        target = storage_path or storage_file or ACCOUNTS_FILE
        self.storage_path = Path(target)
        self._accounts: Dict[int, MT5Account] = {}
        self._active_login: Optional[int] = None
        self._load()

    def _load(self) -> None:
        self._accounts.clear()
        self._active_login = None

        if self.storage_path.is_file():
            try:
                data = json.loads(self.storage_path.read_text(encoding="utf-8"))
                for acc_data in data.get("accounts", []):
                    try:
                        acc = MT5Account.from_dict(acc_data)
                        if acc.login > 0:
                            self._accounts[acc.login] = acc
                    except Exception:
                        pass
                self._active_login = data.get("active_login")
            except Exception as e:
                logger.warning("Could not load MT5 accounts file %s: %s", self.storage_path, e)

        # Seed from config / .env if no accounts are present yet and using default production file
        try:
            is_default_file = self.storage_path.resolve() == Path(ACCOUNTS_FILE).resolve()
        except Exception:
            is_default_file = str(self.storage_path) == str(ACCOUNTS_FILE)

        if not self._accounts and is_default_file:
            try:
                import config
                cfg_login = int(getattr(config, "MT5_LOGIN", 0) or 0)
                cfg_pass = str(getattr(config, "MT5_PASSWORD", "") or "")
                cfg_server = str(getattr(config, "MT5_SERVER", "") or "")
                cfg_gw = str(getattr(config, "MT5_GATEWAY_URL", "") or "")
                if cfg_login > 0 and cfg_pass and cfg_server:
                    acc = MT5Account(
                        login=cfg_login,
                        password=cfg_pass,
                        server=cfg_server,
                        alias="default",
                        gateway_url=cfg_gw,
                        is_live=getattr(config, "MT5_MODE", "paper") == "live",
                        added_at=datetime.now(timezone.utc).isoformat(),
                    )
                    self._accounts[acc.login] = acc
                    self._active_login = acc.login
            except Exception:
                pass

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "active_login": self._active_login,
                "accounts": [acc.to_dict() for acc in self._accounts.values()],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            tmp = self.storage_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.storage_path)
        except Exception as e:
            logger.warning("Could not save MT5 accounts file: %s", e)

    def add_account(
        self,
        login: Union[int, str],
        password: str,
        server: str,
        alias: str = "",
        gateway_url: str = "",
        is_live: bool = True,
        set_active: bool = True,
    ) -> MT5Account:
        """Add or update an MT5 trading account."""
        try:
            log_num = int(str(login).strip())
        except (ValueError, TypeError):
            raise ValueError(f"Invalid MT5 login: '{login}'. Must be a positive integer account number.")

        if log_num <= 0:
            raise ValueError(f"Invalid MT5 login: '{login}'. Must be greater than 0.")
        if not password or not str(password).strip():
            raise ValueError("MT5 password cannot be empty.")
        if not server or not str(server).strip():
            raise ValueError("MT5 broker server cannot be empty.")

        clean_alias = str(alias or "").strip().lower()
        if not clean_alias:
            clean_alias = f"acct_{log_num}"

        # If alias conflicts with another account, remove alias from the old one
        for existing in self._accounts.values():
            if existing.login != log_num and existing.alias.lower() == clean_alias:
                existing.alias = f"acct_{existing.login}"

        acc = MT5Account(
            login=log_num,
            password=str(password).strip(),
            server=str(server).strip(),
            alias=clean_alias,
            gateway_url=str(gateway_url or "").strip(),
            is_live=bool(is_live),
            added_at=datetime.now(timezone.utc).isoformat(),
            last_used_at=datetime.now(timezone.utc).isoformat() if set_active else "",
        )
        self._accounts[log_num] = acc

        if set_active or len(self._accounts) == 1:
            self._active_login = log_num
            self._sync_config(acc)

        self._save()
        return acc

    def remove_account(self, identifier: Union[int, str]) -> Optional[MT5Account]:
        """Remove account by login number or alias."""
        acc = self.get_account(identifier)
        if not acc:
            return None
        del self._accounts[acc.login]
        if self._active_login == acc.login:
            self._active_login = next(iter(self._accounts.keys())) if self._accounts else None
            if self._active_login:
                self._sync_config(self._accounts[self._active_login])
        self._save()
        return acc

    def get_account(self, identifier: Union[int, str, None]) -> Optional[MT5Account]:
        """Find account by login integer or alias string."""
        if identifier is None:
            return self.get_active_account()

        s_id = str(identifier).strip()
        if s_id.isdigit():
            log_num = int(s_id)
            if log_num in self._accounts:
                return self._accounts[log_num]

        s_lower = s_id.lower()
        for acc in self._accounts.values():
            if acc.alias.lower() == s_lower:
                return acc

        return None

    def get_active_account(self) -> Optional[MT5Account]:
        """Return the currently selected active account."""
        if self._active_login and self._active_login in self._accounts:
            return self._accounts[self._active_login]
        if self._accounts:
            first = next(iter(self._accounts.values()))
            self._active_login = first.login
            return first
        return None

    def set_active(self, identifier: Union[int, str]) -> Optional[MT5Account]:
        """Mark account as currently active and sync to config."""
        acc = self.get_account(identifier)
        if not acc:
            return None
        self._active_login = acc.login
        acc.last_used_at = datetime.now(timezone.utc).isoformat()
        self._sync_config(acc)
        self._save()
        return acc

    def list_accounts(self) -> List[MT5Account]:
        """Return all saved accounts."""
        return list(self._accounts.values())

    def format_account_list(self) -> str:
        """Return formatted Markdown listing of all accounts."""
        if not self._accounts:
            return (
                "ℹ️ **No MT5 accounts saved.**\n"
                "Add an account with:\n"
                "`/mt5 add <login> <password> <server> [alias]`"
            )

        active = self.get_active_account()
        active_login = active.login if active else None

        lines = ["📋 **Saved MetaTrader 5 Accounts:**"]
        for acc in sorted(self._accounts.values(), key=lambda a: (a.login != active_login, a.alias)):
            is_active = (acc.login == active_login)
            status_dot = "🟢" if is_active else "⚪"
            mode_tag = "LIVE" if acc.is_live else "DEMO"
            active_tag = " **(Active)**" if is_active else ""
            via_tag = f" | Gateway: `{acc.gateway_url}`" if acc.gateway_url else ""
            lines.append(
                f"• {status_dot} **{acc.alias}** (`{acc.login}`) — `{acc.server}` [{mode_tag}]{active_tag}\n"
                f"  Password: `{acc.masked_password}`{via_tag}"
            )

        lines.append(
            "\n💡 **Switch account:** `/mt5 switch <alias|login>`\n"
            "💡 **Connect live:** `/mt5 switch live`\n"
            "💡 **Remove account:** `/mt5 remove <alias|login>`"
        )
        return "\n".join(lines)

    def _sync_config(self, acc: MT5Account) -> None:
        """Update in-memory config variables."""
        try:
            import config
            config.MT5_LOGIN = acc.login
            config.MT5_PASSWORD = acc.password
            config.MT5_SERVER = acc.server
            config.MT5_GATEWAY_URL = acc.gateway_url
        except Exception:
            pass


# Global singleton manager instance
_default_manager: Optional[MT5AccountManager] = None


def get_account_manager() -> MT5AccountManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = MT5AccountManager()
    return _default_manager


def connect_and_verify(
    account: MT5Account,
    mode: str = "live",
    path: str = "",
) -> Tuple[bool, str, Optional[Any]]:
    """
    Attempt connection with MT5Broker.
    Returns (success, message, broker_instance).
    """
    from mt5_broker import MT5Broker
    try:
        broker = MT5Broker(
            mode=mode,
            login=account.login,
            password=account.password,
            server=account.server,
            path=path,
            gateway_url=account.gateway_url,
        )
        if not broker.is_connected and mode == "live":
            return False, "Failed to connect to MT5 terminal or server.", None
        return True, "Connected successfully", broker
    except Exception as e:
        return False, str(e), None


def switch_broker_mode(
    target_mode: str = "live",
    identifier: Optional[Union[int, str]] = None,
    engine: Optional[Any] = None,
    config_mod: Optional[Any] = None,
    account_mgr: Optional[MT5AccountManager] = None,
) -> Tuple[bool, str, Optional[Any]]:
    """
    Switch broker mode to 'live' or 'paper' with fail-closed validation.
    Returns (success, message, broker_instance).
    """
    mgr = account_mgr or get_account_manager()
    cfg = config_mod
    if cfg is None:
        import config as _cfg
        cfg = _cfg

    target = target_mode.strip().lower()

    if target in ("paper", "demo", "sim"):
        return (
            False,
            "🚫 **Paper Trading Removed**\n"
            "Paper broker simulation has been removed. MetaTrader 5 Live is the exclusive broker engine.\n\n"
            "• To switch live account: `/switch <alias|login>` or `/mt5 switch live`\n"
            "• To add an account: `/mt5 add <login> <password> <server> [alias]`",
            None,
        )

    elif target in ("live", "real"):
        # Resolve target account
        acc = mgr.get_account(identifier) if identifier else mgr.get_active_account()

        if not acc:
            try:
                is_default_file = mgr.storage_path.resolve() == Path(ACCOUNTS_FILE).resolve()
            except Exception:
                is_default_file = str(mgr.storage_path) == str(ACCOUNTS_FILE)

            if is_default_file:
                # Fall back to config if present in production
                cfg_log = int(getattr(cfg, "MT5_LOGIN", 0) or 0)
                cfg_pass = str(getattr(cfg, "MT5_PASSWORD", "") or "")
                cfg_srv = str(getattr(cfg, "MT5_SERVER", "") or "")
                if cfg_log and cfg_pass and cfg_srv:
                    acc = mgr.add_account(cfg_log, cfg_pass, cfg_srv, alias="default", set_active=True)

        if not acc:
            return (
                False,
                "❌ **No MT5 live account registered or configured.**\n\n"
                "Add an account first with:\n"
                "`/mt5 add <login> <password> <server> [alias]`\n"
                "Example:\n"
                "`/mt5 add 10054330 MyPassword Exness-Real primary`",
                None,
            )

        # Attempt live connection (Fail-Closed)
        path = getattr(cfg, "MT5_PATH", "")
        ok, err_msg, live_broker = connect_and_verify(acc, mode="live", path=path)

        if not ok or live_broker is None:
            return (
                False,
                f"🚫 **MT5 Live Connection Rejected (Fail-Closed)**\n"
                f"• **Target Account:** `{acc.login}` ({acc.alias}) on `{acc.server}`\n"
                f"• **Reason:** `{err_msg}`\n\n"
                f"_Live mode aborted safely. Current broker remains unchanged._",
                None,
            )

        # Attach live broker
        if engine is not None:
            engine.broker = live_broker
        cfg.BROKER_MODE = "mt5_live"
        cfg.MT5_MODE = "live"
        cfg.MT5_ENABLED = True
        mgr.set_active(acc.login)

        via_str = f"Gateway (`{acc.gateway_url}`)" if acc.gateway_url else "Native MetaTrader 5 Terminal"

        bal = float(getattr(live_broker, "balance", 0.0) or 0.0)
        eq = float(getattr(live_broker, "equity", bal) or bal)
        lev = float(getattr(live_broker, "leverage", 100.0) or 100.0)
        free_m = float(getattr(live_broker, "free_margin", eq) or eq)

        return (
            True,
            f"🟢 ⚠️ **LIVE MT5 EXECUTION ACTIVATED**\n"
            f"• **Account #:** `{acc.login}` (Alias: `{acc.alias}`)\n"
            f"• **Server:** `{acc.server}`\n"
            f"• **Bridge:** {via_str}\n"
            f"• **Balance:** `${bal:,.2f}` | **Equity:** `${eq:,.2f}`\n"
            f"• **Leverage:** `{lev:.0f}x` | **Free Margin:** `${free_m:,.2f}`\n"
            f"• **Protection:** Server-side SL/TP + Emergency Kill Switch Active\n\n"
            f"⚠️ _Real capital is at risk. Automated order execution is enabled._",
            live_broker,
        )

    else:
        # Check if identifier or target is an account alias or login number to switch to
        acc = mgr.get_account(target) or (mgr.get_account(identifier) if identifier else None)
        if acc:
            mgr.set_active(acc.login)
            # Switch to it in current live/paper mode
            mode_to_use = "live" if acc.is_live else "paper"
            return switch_broker_mode(target_mode=mode_to_use, identifier=acc.login, engine=engine, config_mod=cfg)

        return (
            False,
            f"❌ Unknown switch target: '{target}'. Use `/mt5 switch live`, `/mt5 switch paper`, or `/mt5 switch <alias|login>`.",
            None,
        )
