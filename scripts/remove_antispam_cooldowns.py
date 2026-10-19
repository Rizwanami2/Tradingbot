#!/usr/bin/env python3
"""Disable all anti-spam cooldowns and proximity blocks in anti_spam.py."""
from pathlib import Path

def main():
    p = Path("anti_spam.py")
    t = p.read_text(encoding="utf-8")
    if "cooldowns disabled" in t and "return 0  # cooldowns removed" in t:
        print("Already removed")
        return
    start = t.find("        # When mode is OFF: cooldowns and proximity checks are bypassed")
    if start < 0:
        start = t.find('        if self.mode == "off":')
    end = t.find('        return True, "OK"\n', start) if start >= 0 else -1
    if start >= 0 and end >= 0:
        end = end + len('        return True, "OK"\n')
        t = (
            t[:start]
            + "        # Cooldown / proximity anti-spam REMOVED\n"
            + '        return True, "OK (cooldowns disabled)"\n\n'
            + t[end:]
        )
    for name in ("get_entry_cooldown_duration", "get_loss_cooldown_duration", "get_global_cooldown_duration"):
        mi = t.find(f"    def {name}")
        if mi < 0:
            continue
        ni = t.find("\n    def ", mi + 5)
        line_end = t.find("\n", mi)
        if ni > line_end:
            t = t[: line_end + 1] + "        return 0  # cooldowns removed\n" + t[ni:]
    p.write_text(t, encoding="utf-8")
    print("anti_spam.py: cooldowns removed")

    cfg = Path("config.py")
    if cfg.exists():
        c = cfg.read_text(encoding="utf-8")
        for a, b in [
            ('", 45.0)', '", 0.0)  # disabled'),
        ]:
            pass
        for key in ("SCALP_ENTRY_COOLDOWN_SEC", "SCALP_LOSS_COOLDOWN_SEC", "SCALP_GLOBAL_COOLDOWN_SEC"):
            # zero common defaults
            import re
            c = re.sub(
                rf'({key} = _safe_float\(os\.getenv\("[^"]+"\), )([0-9.]+)(\))',
                r"\g<1>0.0\3  # disabled",
                c,
                count=1,
            )
        cfg.write_text(c, encoding="utf-8")
        print("config: scalp cooldown defaults -> 0")

if __name__ == "__main__":
    main()
