import os
import subprocess
import sys
from pathlib import Path


def test_mt5_and_broker_defaults_are_not_live():
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    for key in (
        "MT5_ENABLED",
        "MT5_MODE",
        "BROKER_MODE",
        "BOT_BROKER_MODE",
    ):
        env.pop(key, None)
    env["PYTHONPATH"] = str(repo_root)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import config; print(str(config.MT5_ENABLED).lower() + '|' + config.MT5_MODE + '|' + config.BROKER_MODE)",
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip().endswith("false|demo|disabled")
