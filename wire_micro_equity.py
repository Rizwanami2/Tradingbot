#!/usr/bin/env python3
"""Enable $100 paper accounts: micro sizing + paper equity default."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    cfg = ROOT / "config.py"
    if cfg.exists():
        t = cfg.read_text(errors="ignore")
        if "_equity_default" not in t and 'INITIAL_EQUITY = _safe_float(os.getenv("BOT_EQUITY"), 10000.0)' in t:
            t = t.replace(
                'INITIAL_EQUITY = _safe_float(os.getenv("BOT_EQUITY"), 10000.0)',
                '_equity_default = 100.0 if os.getenv("BOT_BROKER_MODE", "paper").lower() == "paper" else 10000.0\n'
                'INITIAL_EQUITY = _safe_float(os.getenv("BOT_EQUITY"), _equity_default)',
                1,
            )
            cfg.write_text(t)
            print("[wire_micro_equity] paper equity default 100")
        else:
            print("[wire_micro_equity] config OK")

    rm_path = ROOT / "risk_manager.py"
    if not rm_path.exists():
        print("[wire_micro_equity] done")
        return
    t = rm_path.read_text(errors="ignore")
    changed = False

    if "_adapt_profile_for_equity" not in t:
        needle = "    def position_size(\n"
        helper = (
            "    def _adapt_profile_for_equity(self) -> None:\n"
            '        """Scale instrument profile for small paper accounts (e.g. $100)."""\n'
            "        eq = float(self.state.equity or 0)\n"
            "        p = dict(self.profile)\n"
            '        sym = (self.symbol or "").upper()\n'
            "        if eq <= 0:\n"
            "            return\n"
            "        if eq < 500:\n"
            '            if any(x in sym for x in ("XAU", "GOLD", "GC")):\n'
            '                p["contract_size"] = 1\n'
            '                p["pip_value_per_lot"] = 0.01\n'
            '                p["min_lot"] = 0.01\n'
            '                p["lot_step"] = 0.01\n'
            '            if any(x in sym for x in ("BTC", "ETH", "SOL", "BNB")):\n'
            '                p["min_lot"] = min(float(p.get("min_lot", 0.001)), 0.001)\n'
            '                p["lot_step"] = min(float(p.get("lot_step", 0.001)), 0.001)\n'
            '        if eq < 200 and p.get("contract_size", 100) == 1:\n'
            '            p["min_lot"] = min(float(p.get("min_lot", 0.01)), 0.001)\n'
            '            p["lot_step"] = min(float(p.get("lot_step", 0.01)), 0.001)\n'
            "        self.profile = p\n\n"
        )
        if needle in t:
            t = t.replace(needle, helper + needle, 1)
            changed = True
            print("[wire_micro_equity] adapt helper")

    if "self._adapt_profile_for_equity()" not in t and "_adapt_profile_for_equity" in t:
        needle = "        ok, why = self.can_trade()\n"
        if needle in t:
            t = t.replace(needle, "        self._adapt_profile_for_equity()\n" + needle, 1)
            changed = True
            print("[wire_micro_equity] adapt call")

    if 'blocked: required margin exceeds capacity' in t and "micro-size" not in t:
        old = (
            "            if affordable_lots < min_lot:\n"
            "                return TradeRiskResult(\n"
            "                    size=0.0, lots=0.0, risk_amount=0.0, risk_pct_used=0.0,\n"
            "                    stop_distance=round(stop_dist, 6), stop_pips=stop_pips,\n"
        )
        new = (
            "            if affordable_lots < min_lot:\n"
            "                import math as _m\n"
            "                micro_step = min(step, 0.001)\n"
            "                lots = max(micro_step, _m.floor(max(affordable_lots, 0) / micro_step) * micro_step)\n"
            "                if lots <= 0:\n"
            "                    lots = micro_step\n"
            "                lots = round(lots, 6)\n"
            '                reason = (reason + " | micro-size").strip(" |")\n'
            "            elif False:\n"
            "                return TradeRiskResult(\n"
            "                    size=0.0, lots=0.0, risk_amount=0.0, risk_pct_used=0.0,\n"
            "                    stop_distance=round(stop_dist, 6), stop_pips=stop_pips,\n"
        )
        if old in t:
            t = t.replace(old, new, 1)
            changed = True
            print("[wire_micro_equity] margin micro-size")

    if changed:
        rm_path.write_text(t)
        print("[wire_micro_equity] risk_manager patched")
    else:
        print("[wire_micro_equity] risk_manager OK")
    print("[wire_micro_equity] done")


if __name__ == "__main__":
    main()
