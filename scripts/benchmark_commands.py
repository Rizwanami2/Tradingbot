"""Benchmark command response times and verify zero errors."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import config
import main
from telegram_advanced import asignal_command, abacktest_command
from telegram_brain import train_command, relearn_command, brain_command, learner_command


async def run_benchmarks():
    mock_msg = AsyncMock()
    mock_msg.text = "/test"
    mock_update = MagicMock()
    mock_update.message = mock_msg
    mock_update.effective_chat = MagicMock(id=12345)
    mock_ctx = MagicMock()

    benchmarks = [
        ("help", main.help_command, []),
        ("balance", main.balance_command, []),
        ("broker", main.broker_command, []),
        ("status", main.status_command, []),
        ("positions", main.positions_command, []),
        ("chart_gold", main.chart_command, ["XAUUSD"]),
        ("chart_btc", main.chart_command, ["BTCUSD"]),
        ("price", main.price_command, ["BTCUSDT"]),
        ("levels", main.levels_command, ["XAUUSD"]),
        ("news", main.news_command, []),
        ("bridge", main.bridge_command, []),
        ("brain", brain_command, []),
        ("learner", learner_command, []),
        ("train_status", train_command, ["status"]),
        ("relearn_fast", relearn_command, ["XAUUSD", "0.01"]),
        ("asignal", asignal_command, ["XAUUSD"]),
    ]

    print("================================================================")
    print("TELEGRAM COMMAND PERFORMANCE BENCHMARK (ms)")
    print("================================================================")
    results = []
    for name, cmd, args in benchmarks:
        mock_ctx.args = list(args)
        t0 = time.perf_counter()
        try:
            await cmd(mock_update, mock_ctx)
            elapsed = (time.perf_counter() - t0) * 1000.0
            status = "OK"
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000.0
            status = f"ERR: {e}"
        print(f"• /{name:<15} : {elapsed:8.2f} ms | Status: {status}")
        results.append((name, elapsed, status))

    print("================================================================")
    all_ok = all(s == "OK" for _, _, s in results)
    avg_latency = sum(e for _, e, _ in results) / len(results)
    print(f"Total commands tested: {len(results)}")
    print(f"Average command latency: {avg_latency:.2f} ms")
    print(f"All commands passed cleanly: {all_ok}")
    print("================================================================")
    assert all_ok, "Some benchmarked commands failed!"


if __name__ == "__main__":
    asyncio.run(run_benchmarks())
