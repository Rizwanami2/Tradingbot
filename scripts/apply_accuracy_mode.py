#!/usr/bin/env python3
"""
Apply Accuracy Mode to .env configuration.
Tightens signal filtering (composite quality >= 0.45, AI scorecard >= 60, min R:R >= 1.25).
Reference: docs/ACCURACY.md
"""
from pathlib import Path
import sys

SETTINGS = {
    "BOT_ACCURACY_MODE": "true",
    "BOT_ACCURACY_MIN_QUALITY": "0.45",
    "AI_MIN_SCORECARD": "60",
    "BOT_PROFILE": "scalp",
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
    print(f"[ACCURACY] Successfully applied Accuracy Mode configuration to {env_path.resolve()}")
    for k, v in SETTINGS.items():
        print(f"  • {k}={v}")


if __name__ == "__main__":
    apply_env()
