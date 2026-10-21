#!/usr/bin/env python3
"""Wire CombinedBotAgent into autotrade_worker.py"""
from pathlib import Path
import ast

def main():
    if not Path("combined_bot_agent.py").exists():
        raise SystemExit("combined_bot_agent.py missing — git pull origin main")

    p = Path("autotrade_worker.py")
    t = p.read_text(encoding="utf-8")
    if "COMBINED AGENT VETO" in t:
        print("already wired")
        ast.parse(t)
        return

    needle = '                            sig["fast_market_fill"] = True\n'
    insert = needle + '''
                            try:
                                size_mult
                            except NameError:
                                size_mult = 1.0

                            # CombinedBotAgent (FIT skills: session/spread/risk/structure)
                            try:
                                import os as _os_cba
                                if _os_cba.getenv("COMBINED_AGENT_ENABLED", "true").lower() not in ("true", "1", "yes", "on"):
                                    raise RuntimeError("combined agent disabled")
                                from combined_bot_agent import CombinedBotAgent
                                if not hasattr(live_monitoring_worker, "_combined_agent"):
                                    live_monitoring_worker._combined_agent = CombinedBotAgent()
                                _cba = live_monitoring_worker._combined_agent
                                _spread = float(candle_dict.get("spread") or 0.0)
                                try:
                                    if _spread <= 0 and "ask" in candle_dict and "bid" in candle_dict:
                                        _spread = abs(float(candle_dict["ask"]) - float(candle_dict["bid"]))
                                except Exception:
                                    pass
                                _ok_cba, sig, _cba_reasons = _cba.evaluate(
                                    sig,
                                    {
                                        "spread": _spread,
                                        "open_trades": open_n,
                                        "open_for_symbol": 1 if symbol in getattr(engine, "active_positions", {}) else 0,
                                        "htf_bias": str(sig.get("regime") or dname),
                                        "m1_direction": dname,
                                        "setup": str(sig.get("setup") or sig.get("reason") or "pullback"),
                                        "is_live": bool(getattr(config, "BOT_AUTOTRADE_LIVE_OPT_IN", False) or getattr(config, "AUTOTRADE_LIVE_OPT_IN", False)),
                                        "entry": float(candle_dict.get("close") or 0.0),
                                    },
                                )
                                if not _ok_cba:
                                    _why = ", ".join(_cba_reasons) if _cba_reasons else "combined agent"
                                    logger.info("Autotrade %s: COMBINED AGENT VETO — %s", symbol, _why)
                                    monitoring_health[f"{symbol}_eval"] = f"Combined veto: {_why}"
                                    continue
                                if sig.get("combined_agent_size_mult"):
                                    try:
                                        size_mult = float(size_mult) * float(sig["combined_agent_size_mult"])
                                        size_mult = max(0.25, min(2.0, size_mult))
                                    except Exception:
                                        pass
                            except Exception as _cba_err:
                                logger.debug("CombinedBotAgent note: %s", _cba_err)
'''
    if needle not in t:
        raise SystemExit("fast_market_fill needle not found")
    t = t.replace(needle, insert, 1)

    if "autotrade_close" not in t:
        t = t.replace(
            "\n\nasync def live_monitoring_worker(",
            '''
    try:
        from combined_bot_agent import CombinedBotAgent
        CombinedBotAgent().on_close(
            pnl=float(fpnl),
            r_multiple=float(r_mult),
            direction=str(getattr(pos, "direction", "") if pos else ""),
            entry=float(getattr(pos, "entry_level", 0) or 0) if pos else 0.0,
            notes="autotrade_close",
        )
    except Exception:
        pass


async def live_monitoring_worker(''',
            1,
        )

    ast.parse(t)
    p.write_text(t, encoding="utf-8")
    print("WIRED CombinedBotAgent into autotrade_worker")

if __name__ == "__main__":
    main()
