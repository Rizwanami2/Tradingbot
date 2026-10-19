#!/usr/bin/env python3
"""Completely remove anti-spam cooldown + proximity blocking."""
from pathlib import Path
import re

def main():
    p = Path("anti_spam.py")
    t = p.read_text(encoding="utf-8")

    # 1) Force cooldown duration methods to return 0
    for name in ("get_entry_cooldown_duration", "get_loss_cooldown_duration", "get_global_cooldown_duration"):
        t = re.sub(
            rf"    def {name}\(self[^)]*\)[\s\S]*?(?=\n    def )",
            f"    def {name}(self, *args, **kwargs):\n        return 0  # cooldowns removed\n\n",
            t,
            count=1,
        )
        # methods with only self
        t = re.sub(
            rf"    def {name}\(self\)[\s\S]*?(?=\n    def )",
            f"    def {name}(self, *args, **kwargs):\n        return 0  # cooldowns removed\n\n",
            t,
            count=1,
        )

    # get_entry has (self, symbol)
    t = re.sub(
        r"    def get_entry_cooldown_duration\(self, symbol: str\) -> int:[\s\S]*?(?=\n    def )",
        "    def get_entry_cooldown_duration(self, symbol: str = \"\") -> int:\n        return 0  # cooldowns removed\n\n",
        t,
        count=1,
    )
    t = re.sub(
        r"    def get_loss_cooldown_duration\(self, symbol: str\) -> int:[\s\S]*?(?=\n    def )",
        "    def get_loss_cooldown_duration(self, symbol: str = \"\") -> int:\n        return 0  # cooldowns removed\n\n",
        t,
        count=1,
    )
    t = re.sub(
        r"    def get_global_cooldown_duration\(self\) -> int:[\s\S]*?(?=\n    def )",
        "    def get_global_cooldown_duration(self) -> int:\n        return 0  # cooldowns removed\n\n",
        t,
        count=1,
    )

    # 2) After capacity checks in check_can_enter, always OK (no CD / proximity)
    marker = "        # When mode is OFF: cooldowns and proximity checks are bypassed"
    if marker in t:
        start = t.find(marker)
        end = t.find('        return True, "OK"\n', start)
        if end > start:
            end = end + len('        return True, "OK"\n')
            t = (
                t[:start]
                + "        # Cooldowns + price proximity REMOVED\n"
                + '        return True, "OK (cooldowns disabled)"\n\n'
                + t[end:]
            )
    elif "cooldowns disabled" not in t:
        # Insert after per-currency check block if possible
        needle = "positions reached for currency"
        idx = t.find(needle)
        if idx > 0:
            # find next blank line after this return False block - hard; force mode off default instead
            pass

    # 3) Default env cooldowns to 0 in __init__
    t = t.replace('os.getenv("SCALP_ENTRY_COOLDOWN_SEC", "45")', 'os.getenv("SCALP_ENTRY_COOLDOWN_SEC", "0")')
    t = t.replace('os.getenv("SCALP_LOSS_COOLDOWN_SEC", "90")', 'os.getenv("SCALP_LOSS_COOLDOWN_SEC", "0")')
    t = t.replace('os.getenv("SCALP_GLOBAL_COOLDOWN_SEC", "10")', 'os.getenv("SCALP_GLOBAL_COOLDOWN_SEC", "0")')
    t = t.replace('os.getenv("SCALP_MIN_PRICE_DISTANCE_PCT", "0.08")', 'os.getenv("SCALP_MIN_PRICE_DISTANCE_PCT", "0")')
    t = t.replace('os.getenv("ANTISPAM_MODE", "auto")', 'os.getenv("ANTISPAM_MODE", "off")')

    p.write_text(t, encoding="utf-8")
    print("anti_spam.py patched")

    # config defaults
    cfg = Path("config.py")
    if cfg.exists():
        c = cfg.read_text(encoding="utf-8")
        c = re.sub(
            r'(SCALP_ENTRY_COOLDOWN_SEC = _safe_float\(os\.getenv\("BOT_SCALP_ENTRY_COOLDOWN_SEC"\), )[0-9.]+(\))',
            r"\g<1>0.0\2",
            c,
        )
        c = re.sub(
            r'(SCALP_LOSS_COOLDOWN_SEC = _safe_float\(os\.getenv\("BOT_SCALP_LOSS_COOLDOWN_SEC"\), )[0-9.]+(\))',
            r"\g<1>0.0\2",
            c,
        )
        c = re.sub(
            r'(SCALP_GLOBAL_COOLDOWN_SEC = _safe_float\(os\.getenv\("BOT_SCALP_GLOBAL_COOLDOWN_SEC"\), )[0-9.]+(\))',
            r"\g<1>0.0\2",
            c,
        )
        c = re.sub(
            r'(SCALP_MIN_PRICE_DISTANCE_PCT = _safe_float\(os\.getenv\("BOT_SCALP_MIN_PRICE_DISTANCE_PCT"\), )[0-9.]+(\))',
            r"\g<1>0.0\2",
            c,
        )
        cfg.write_text(c, encoding="utf-8")
        print("config.py cooldown defaults -> 0")

    print("Done. Restart bot. Cooldowns will not block entries.")

if __name__ == "__main__":
    main()
