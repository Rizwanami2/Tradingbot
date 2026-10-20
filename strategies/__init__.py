"""
Strategies Package
==================
Modularized algorithmic strategy agents for multi-agent consensus and execution.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure strategies directory is in sys.path for direct module name resolution
_dir = str(Path(__file__).resolve().parent)
if _dir not in sys.path:
    sys.path.insert(0, _dir)

try:
    from .asian_range_breakout import AsianBreakoutAgent, AsianDir
    from .auto_trend_1m_strategy import AutoTrend1mAgent, AutoTrendDir, verify_auto_trend_alignment, detect_auto_trend_1m
    from .fake_breakout_strategy import FakeBreakoutAgent, FBDir
    from .gautam_jha_strategy import GautamJhaAgent, GJDir
    from .mean_reversion_vwap_strategy import MeanReversionVWAPAgent, MRDir
    from .momentum_ribbon_scalp import RibbonScalpAgent, RibbonDir
    from .orr_strategy import ORRAgent, ORRDir
    from .ote_strategy import OTEAgent, OTEDir
    from .stoch_rsi_scalp import StochRSIScalpAgent, StochDir
    from .supply_demand_strategy import SupplyDemandAgent, SDDir
    from .volatility_squeeze_scalp import SqueezeScalpAgent, SqueezeDir
except ImportError:
    from asian_range_breakout import AsianBreakoutAgent, AsianDir
    from auto_trend_1m_strategy import AutoTrend1mAgent, AutoTrendDir, verify_auto_trend_alignment, detect_auto_trend_1m
    from fake_breakout_strategy import FakeBreakoutAgent, FBDir
    from gautam_jha_strategy import GautamJhaAgent, GJDir
    from mean_reversion_vwap_strategy import MeanReversionVWAPAgent, MRDir
    from momentum_ribbon_scalp import RibbonScalpAgent, RibbonDir
    from orr_strategy import ORRAgent, ORRDir
    from ote_strategy import OTEAgent, OTEDir
    from stoch_rsi_scalp import StochRSIScalpAgent, StochDir
    from supply_demand_strategy import SupplyDemandAgent, SDDir
    from volatility_squeeze_scalp import SqueezeScalpAgent, SqueezeDir

__all__ = [
    "AsianBreakoutAgent", "AsianDir",
    "AutoTrend1mAgent", "AutoTrendDir", "verify_auto_trend_alignment", "detect_auto_trend_1m",
    "FakeBreakoutAgent", "FBDir",
    "GautamJhaAgent", "GJDir",
    "MeanReversionVWAPAgent", "MRDir",
    "RibbonScalpAgent", "RibbonDir",
    "ORRAgent", "ORRDir",
    "OTEAgent", "OTEDir",
    "StochRSIScalpAgent", "StochDir",
    "SupplyDemandAgent", "SDDir",
    "SqueezeScalpAgent", "SqueezeDir",
]
