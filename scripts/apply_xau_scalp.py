#!/usr/bin/env python3
"""
Apply XAUUSD Scalp Profile to .env configuration.
Optimized for London / NY liquidity sessions, positive mathematical expectancy (TP $3.00 / SL $2.50),
tight risk limits (max 2 open trades, 1 per symbol), and accuracy filtering.
Reference: docs/XAU_SCALP.md
"""
from pathlib import Path
import sys

SETTINGS = {
    "BOT_PROFILE": "xau_scalp",
    "BOT_SCALP": "true",
    "BOT_SYMBOL": "XAUUSD",
    "BOT_AUTOTRADE_SYMBOLS": "XAUUSD",
    "BOT_MAX_OPEN_TRADES": "2",
    "BOT_SCALP_TP_USD": "3.0",
    "BOT_SCALP_SL_USD": "2.5",
    "BOT_SCALP_ENTRY_COOLDOWN_SEC": "60",
    "BOT_SCALP_LOSS_COOLDOWN_SEC": "120",
    "BOT_ACCURACY_MODE": "true",
    "BOT_ACCURACY_MIN_QUALITY": "0.45",
    "MT5_BACKEND": "ea_bridge",
    "BOT_AUTOTRADE": "false",
    "BOT_NEWS": "true",
}


def apply_env(env_path: Path = Path(".env")) -> None:
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    updated = set()
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in SETTINGS:
                new_lines.append(f"{key}={SETTINGS[key]}")
                updated.add(key)
                continue
        new_lines.append(line)

    for k, v in SETTINGS.items():
        if k not in updated:
            new_lines.append(f"{k}={v}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"[XAU_SCALP] Successfully applied XAUUSD scalp configuration to {env_path.resolve()}")
    for k, v in SETTINGS.items():
        print(f"  • {k}={v}")


if __name__ == "__main__":
    apply_env()
