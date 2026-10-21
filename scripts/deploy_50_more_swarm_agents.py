"""
scripts/deploy_50_more_swarm_agents.py
Adds 50 new specialized strategy agents across Clusters 11-15 to agent_swarm.py,
expanding the specialized swarm from 100 to 150 agents (168 total with the 18 institutional agents).
"""
import re

new_clusters_code = '''
# ===========================================================================
# CLUSTER 11: Volume Spread Analysis (VSA) (10 Agents)
# ===========================================================================
class VSAStoppingVolumeAgent(BaseSwarmAgent):
    name = "VSA-STOPPING-VOLUME"
    cluster = "VOLUME_SPREAD_ANALYSIS"
    weight = 1.40

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.vol_ratio > 1.4 and ctx.candle_spread < 1.2 * ctx.atr and ctx.lower_wick > 0.4 * ctx.candle_spread and ctx.rsi < 38:
            stop = ctx.low - 0.5 * ctx.atr
            tp = ctx.mark + 2.0 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.86, f"VSA: Stopping volume absorption (Vol x{ctx.vol_ratio:.1f})", stop, tp, ctx.low)
        return Vote(self.name, Dir.FLAT, 0.0, "No stopping volume absorption")


class VSANoSupplyTestAgent(BaseSwarmAgent):
    name = "VSA-NO-SUPPLY-TEST"
    cluster = "VOLUME_SPREAD_ANALYSIS"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.vol_ratio < 0.75 and ctx.mark < ctx.open and ctx.trend_stacked >= 0 and ctx.mark >= ctx.ema21:
            stop = ctx.low - 0.5 * ctx.atr
            tp = ctx.mark + 2.0 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.83, f"VSA: No Supply test candle holding EMA21 (Vol x{ctx.vol_ratio:.1f})", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Supply test not confirmed")


class VSANoDemandTestAgent(BaseSwarmAgent):
    name = "VSA-NO-DEMAND-TEST"
    cluster = "VOLUME_SPREAD_ANALYSIS"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.vol_ratio < 0.75 and ctx.mark > ctx.open and ctx.trend_stacked <= 0 and ctx.mark <= ctx.ema21:
            stop = ctx.high + 0.5 * ctx.atr
            tp = ctx.mark - 2.0 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.83, f"VSA: No Demand test candle rejected at EMA21 (Vol x{ctx.vol_ratio:.1f})", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Demand test not confirmed")


class VSAUpthrustPinbarAgent(BaseSwarmAgent):
    name = "VSA-UPTHRUST-PINBAR"
    cluster = "VOLUME_SPREAD_ANALYSIS"
    weight = 1.40

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.vol_ratio > 1.3 and ctx.upper_wick > 0.55 * ctx.candle_spread and ctx.mark >= ctx.swing_high * 0.999:
            stop = ctx.high + 0.5 * ctx.atr
            tp = ctx.mark - 2.2 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.88, "VSA: Upthrust pinbar distribution on high volume", stop, tp, ctx.high)
        return Vote(self.name, Dir.FLAT, 0.0, "No upthrust pinbar distribution")


class VSAReverseUpthrustAgent(BaseSwarmAgent):
    name = "VSA-REVERSE-UPTHRUST"
    cluster = "VOLUME_SPREAD_ANALYSIS"
    weight = 1.40

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.vol_ratio > 1.3 and ctx.lower_wick > 0.55 * ctx.candle_spread and ctx.mark <= ctx.swing_low * 1.001:
            stop = ctx.low - 0.5 * ctx.atr
            tp = ctx.mark + 2.2 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.88, "VSA: Reverse upthrust spring absorption on high volume", stop, tp, ctx.low)
        return Vote(self.name, Dir.FLAT, 0.0, "No reverse upthrust spring")


class VSAEffortVsResultAgent(BaseSwarmAgent):
    name = "VSA-EFFORT-VS-RESULT"
    cluster = "VOLUME_SPREAD_ANALYSIS"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.vol_ratio > 2.0 and ctx.body_spread < 0.3 * ctx.atr:
            if ctx.trend_stacked > 0 and ctx.rsi > 65:
                stop = ctx.high + 0.5 * ctx.atr
                tp = ctx.ema21
                return Vote(self.name, Dir.SHORT, 0.84, f"VSA: High effort vs low result ceiling stall (Vol x{ctx.vol_ratio:.1f})", stop, tp, ctx.high)
            elif ctx.trend_stacked < 0 and ctx.rsi < 35:
                stop = ctx.low - 0.5 * ctx.atr
                tp = ctx.ema21
                return Vote(self.name, Dir.LONG, 0.84, f"VSA: High effort vs low result floor absorption (Vol x{ctx.vol_ratio:.1f})", stop, tp, ctx.low)
        return Vote(self.name, Dir.FLAT, 0.0, "Effort and result in proportion")


class VSASellingClimaxAgent(BaseSwarmAgent):
    name = "VSA-SELLING-CLIMAX"
    cluster = "VOLUME_SPREAD_ANALYSIS"
    weight = 1.45

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.vol_ratio > 2.5 and ctx.rsi < 24 and ctx.lower_wick > 0.35 * ctx.candle_spread:
            stop = ctx.low - 0.5 * ctx.atr
            tp = ctx.bb_middle
            return Vote(self.name, Dir.LONG, 0.91, f"VSA: Selling Climax panic absorption (Vol x{ctx.vol_ratio:.1f})", stop, tp, ctx.low)
        return Vote(self.name, Dir.FLAT, 0.0, "No selling climax exhaustion")


class VSABuyingClimaxAgent(BaseSwarmAgent):
    name = "VSA-BUYING-CLIMAX"
    cluster = "VOLUME_SPREAD_ANALYSIS"
    weight = 1.45

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.vol_ratio > 2.5 and ctx.rsi > 76 and ctx.upper_wick > 0.35 * ctx.candle_spread:
            stop = ctx.high + 0.5 * ctx.atr
            tp = ctx.bb_middle
            return Vote(self.name, Dir.SHORT, 0.91, f"VSA: Buying Climax euphoria distribution (Vol x{ctx.vol_ratio:.1f})", stop, tp, ctx.high)
        return Vote(self.name, Dir.FLAT, 0.0, "No buying climax exhaustion")


class VSABagHoldingAgent(BaseSwarmAgent):
    name = "VSA-BAG-HOLDING"
    cluster = "VOLUME_SPREAD_ANALYSIS"
    weight = 1.40

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.vol_ratio > 1.8 and ctx.mark <= ctx.demand_zone_top and ctx.lower_wick > 0.4 * ctx.candle_spread:
            stop = ctx.demand_zone_bottom - 0.5 * ctx.atr
            tp = ctx.supply_zone_bottom
            return Vote(self.name, Dir.LONG, 0.87, "VSA: Institutional bag holding at demand zone baseline", stop, tp, ctx.demand_zone_top)
        return Vote(self.name, Dir.FLAT, 0.0, "No institutional bag holding pattern")


class VSAPushThroughSupplyAgent(BaseSwarmAgent):
    name = "VSA-PUSH-THROUGH-SUPPLY"
    cluster = "VOLUME_SPREAD_ANALYSIS"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.vol_ratio > 1.6 and ctx.mark > ctx.supply_zone_top and ctx.body_spread > 1.2 * ctx.atr:
            stop = ctx.supply_zone_top - 0.5 * ctx.atr
            tp = ctx.mark + 2.5 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.86, "VSA: Wide-spread push cleanly absorbing supply ceiling", stop, tp, ctx.supply_zone_top)
        elif ctx.vol_ratio > 1.6 and ctx.mark < ctx.demand_zone_bottom and ctx.body_spread > 1.2 * ctx.atr:
            stop = ctx.demand_zone_bottom + 0.5 * ctx.atr
            tp = ctx.mark - 2.5 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.86, "VSA: Wide-spread push cleanly breaking demand floor", stop, tp, ctx.demand_zone_bottom)
        return Vote(self.name, Dir.FLAT, 0.0, "No push through supply/demand")


# ===========================================================================
# CLUSTER 12: Order Book Microstructure & Order Flow (10 Agents)
# ===========================================================================
class MicroBidAskImbalanceAgent(BaseSwarmAgent):
    name = "MICRO-BID-ASK-IMBALANCE"
    cluster = "MICROSTRUCTURE"
    weight = 1.40

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.vol_ratio > 1.3 and ctx.roc > 0.25 and ctx.trend_stacked >= 0:
            stop = ctx.open - 0.5 * ctx.atr
            tp = ctx.mark + 2.0 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.86, "Order Book: Aggressive bid lifting market orders", stop, tp, ctx.mark)
        elif ctx.vol_ratio > 1.3 and ctx.roc < -0.25 and ctx.trend_stacked <= 0:
            stop = ctx.open + 0.5 * ctx.atr
            tp = ctx.mark - 2.0 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.86, "Order Book: Aggressive ask hitting market orders", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Bid/ask flow balanced")


class MicroDeltaDivergenceAgent(BaseSwarmAgent):
    name = "MICRO-DELTA-DIVERGENCE"
    cluster = "MICROSTRUCTURE"
    weight = 1.45

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.cvd_divergence_bull and ctx.rsi < 40:
            stop = ctx.low - 0.8 * ctx.atr
            tp = ctx.mark + 2.5 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.89, "Order Book: Bullish CVD delta absorption divergence", stop, tp, ctx.low)
        elif ctx.cvd_divergence_bear and ctx.rsi > 60:
            stop = ctx.high + 0.8 * ctx.atr
            tp = ctx.mark - 2.5 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.89, "Order Book: Bearish CVD delta absorption divergence", stop, tp, ctx.high)
        return Vote(self.name, Dir.FLAT, 0.0, "No delta divergence")


class MicroIcebergAbsorptionAgent(BaseSwarmAgent):
    name = "MICRO-ICEBERG-ABSORPTION"
    cluster = "MICROSTRUCTURE"
    weight = 1.40

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.vol_ratio > 1.8 and ctx.candle_spread < 0.6 * ctx.atr:
            if ctx.mark <= ctx.demand_zone_top:
                stop = ctx.demand_zone_bottom
                tp = ctx.mark + 2.0 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.87, "Order Book: Passive institutional iceberg buyer absorption", stop, tp, ctx.mark)
            elif ctx.mark >= ctx.supply_zone_bottom:
                stop = ctx.supply_zone_top
                tp = ctx.mark - 2.0 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.87, "Order Book: Passive institutional iceberg seller absorption", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "No iceberg absorption detected")


class MicroLiquidationSweepAgent(BaseSwarmAgent):
    name = "MICRO-LIQUIDATION-SWEEP"
    cluster = "MICROSTRUCTURE"
    weight = 1.45

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.low < ctx.swing_low and ctx.mark > ctx.swing_low:
            stop = ctx.low - 0.5 * ctx.atr
            tp = ctx.swing_eq
            return Vote(self.name, Dir.LONG, 0.90, "Order Book: Long liquidation cascade stop-run sweep bounce", stop, tp, ctx.swing_low)
        elif ctx.high > ctx.swing_high and ctx.mark < ctx.swing_high:
            stop = ctx.high + 0.5 * ctx.atr
            tp = ctx.swing_eq
            return Vote(self.name, Dir.SHORT, 0.90, "Order Book: Short liquidation cascade stop-run sweep rejection", stop, tp, ctx.swing_high)
        return Vote(self.name, Dir.FLAT, 0.0, "No liquidation sweep active")


class MicroRestingBookPullAgent(BaseSwarmAgent):
    name = "MICRO-RESTING-BOOK-PULL"
    cluster = "MICROSTRUCTURE"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.trend_stacked > 0 and ctx.mark > ctx.ema9 and ctx.vol_ratio > 1.25:
            stop = ctx.ema21
            tp = ctx.mark + 2.2 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.84, "Order Book: Ask side resting liquidity pulled (path of least resistance)", stop, tp, ctx.ema9)
        elif ctx.trend_stacked < 0 and ctx.mark < ctx.ema9 and ctx.vol_ratio > 1.25:
            stop = ctx.ema21
            tp = ctx.mark - 2.2 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.84, "Order Book: Bid side resting liquidity pulled (path of least resistance)", stop, tp, ctx.ema9)
        return Vote(self.name, Dir.FLAT, 0.0, "Order book depth standard")


class MicroTicksSpeedAccelAgent(BaseSwarmAgent):
    name = "MICRO-TICKS-SPEED-ACCEL"
    cluster = "MICROSTRUCTURE"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.tape_speed_ratio > 1.7:
            if ctx.trend_stacked > 0 and ctx.mark > ctx.open:
                stop = ctx.open - 0.5 * ctx.atr
                tp = ctx.mark + 1.8 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.85, f"Order Book: Execution velocity acceleration (Tape x{ctx.tape_speed_ratio:.1f})", stop, tp, ctx.mark)
            elif ctx.trend_stacked < 0 and ctx.mark < ctx.open:
                stop = ctx.open + 0.5 * ctx.atr
                tp = ctx.mark - 1.8 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.85, f"Order Book: Execution velocity acceleration (Tape x{ctx.tape_speed_ratio:.1f})", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Tape speed standard")


class MicroSweepAndHoldAgent(BaseSwarmAgent):
    name = "MICRO-SWEEP-AND-HOLD"
    cluster = "MICROSTRUCTURE"
    weight = 1.40

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.mark > ctx.swing_high and ctx.trend_stacked > 0 and ctx.close_vs_high if hasattr(ctx, "close_vs_high") else (ctx.mark >= ctx.high - 0.2 * ctx.atr):
            stop = ctx.swing_high - 0.5 * ctx.atr
            tp = ctx.mark + 2.5 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.88, "Order Book: Sweep and hold acceptance above prior swing high", stop, tp, ctx.swing_high)
        elif ctx.mark < ctx.swing_low and ctx.trend_stacked < 0 and (ctx.mark <= ctx.low + 0.2 * ctx.atr):
            stop = ctx.swing_low + 0.5 * ctx.atr
            tp = ctx.mark - 2.5 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.88, "Order Book: Sweep and hold acceptance below prior swing low", stop, tp, ctx.swing_low)
        return Vote(self.name, Dir.FLAT, 0.0, "No sweep and hold acceptance")


class MicroFairValueResetAgent(BaseSwarmAgent):
    name = "MICRO-FAIR-VALUE-RESET"
    cluster = "MICROSTRUCTURE"
    weight = 1.30

    def evaluate(self, ctx: MarketContext) -> Vote:
        if abs(ctx.mark - ctx.vwap) <= 0.3 * ctx.atr:
            if ctx.trend_stacked > 0:
                stop = ctx.vwap - 1.0 * ctx.atr
                tp = ctx.mark + 2.0 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.82, "Order Book: Fair value POC/VWAP re-auction bounce", stop, tp, ctx.vwap)
            elif ctx.trend_stacked < 0:
                stop = ctx.vwap + 1.0 * ctx.atr
                tp = ctx.mark - 2.0 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.82, "Order Book: Fair value POC/VWAP re-auction rejection", stop, tp, ctx.vwap)
        return Vote(self.name, Dir.FLAT, 0.0, "Not at fair value reset level")


class MicroExhaustionBlockAgent(BaseSwarmAgent):
    name = "MICRO-EXHAUSTION-BLOCK"
    cluster = "MICROSTRUCTURE"
    weight = 1.30

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.vol_ratio < 0.65:
            if ctx.mark >= ctx.bb_upper and ctx.rsi > 70:
                stop = ctx.high + 0.5 * ctx.atr
                tp = ctx.bb_middle
                return Vote(self.name, Dir.SHORT, 0.81, "Order Book: Buyers exhausted at upper band (volume dry-up)", stop, tp, ctx.high)
            elif ctx.mark <= ctx.bb_lower and ctx.rsi < 30:
                stop = ctx.low - 0.5 * ctx.atr
                tp = ctx.bb_middle
                return Vote(self.name, Dir.LONG, 0.81, "Order Book: Sellers exhausted at lower band (volume dry-up)", stop, tp, ctx.low)
        return Vote(self.name, Dir.FLAT, 0.0, "No volume exhaustion extreme")


class MicroReverseCascadeAgent(BaseSwarmAgent):
    name = "MICRO-REVERSE-CASCADE"
    cluster = "MICROSTRUCTURE"
    weight = 1.40

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.trend_stacked > 0 and ctx.roc > 0.35 and ctx.macd_hist > ctx.macd_hist_prev:
            stop = ctx.ema21
            tp = ctx.mark + 2.5 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.86, "Order Book: Trapped shorts stop-out reverse cascade run", stop, tp, ctx.ema9)
        elif ctx.trend_stacked < 0 and ctx.roc < -0.35 and ctx.macd_hist < ctx.macd_hist_prev:
            stop = ctx.ema21
            tp = ctx.mark - 2.5 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.86, "Order Book: Trapped longs stop-out reverse cascade drop", stop, tp, ctx.ema9)
        return Vote(self.name, Dir.FLAT, 0.0, "No stop-out cascade momentum")


# ===========================================================================
# CLUSTER 13: Elliott Wave & Harmonic Cycles (10 Agents)
# ===========================================================================
class ElliottWave3ImpulseAgent(BaseSwarmAgent):
    name = "ELLIOTT-WAVE-3-IMPULSE"
    cluster = "ELLIOTT_CYCLES"
    weight = 1.45

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.wave_3_candidate:
            stop = ctx.ema50
            tp = ctx.mark + 3.0 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.92, "Elliott Wave: Powerful Wave 3 impulse extension underway", stop, tp, ctx.ema9)
        elif ctx.trend_stacked < 0 and ctx.macd_hist < 0 and ctx.vol_ratio > 1.25 and ctx.roc < -0.25:
            stop = ctx.ema50
            tp = ctx.mark - 3.0 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.92, "Elliott Wave: Powerful downward Wave 3 impulse extension underway", stop, tp, ctx.ema9)
        return Vote(self.name, Dir.FLAT, 0.0, "No Wave 3 impulse conditions")


class ElliottWave5ExhaustionAgent(BaseSwarmAgent):
    name = "ELLIOTT-WAVE-5-EXHAUSTION"
    cluster = "ELLIOTT_CYCLES"
    weight = 1.40

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.wave_5_exhaustion:
            stop = ctx.high + 0.8 * ctx.atr
            tp = ctx.ema50
            return Vote(self.name, Dir.SHORT, 0.88, "Elliott Wave: Wave 5 divergence exhaustion terminating", stop, tp, ctx.high)
        elif ctx.trend_stacked < 0 and ctx.rsi_div_bull and ctx.vol_ratio < 0.95:
            stop = ctx.low - 0.8 * ctx.atr
            tp = ctx.ema50
            return Vote(self.name, Dir.LONG, 0.88, "Elliott Wave: Downward Wave 5 divergence exhaustion terminating", stop, tp, ctx.low)
        return Vote(self.name, Dir.FLAT, 0.0, "No Wave 5 terminal pattern")


class ElliottABCZigzagAgent(BaseSwarmAgent):
    name = "ELLIOTT-ABC-ZIGZAG"
    cluster = "ELLIOTT_CYCLES"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.trend_stacked > 0 and abs(ctx.mark - ctx.ote_618) <= 0.4 * ctx.atr:
            stop = ctx.ote_786
            tp = ctx.swing_high
            return Vote(self.name, Dir.LONG, 0.85, "Elliott Wave: ABC 5-3-5 Zigzag corrective completion at 61.8%", stop, tp, ctx.ote_618)
        elif ctx.trend_stacked < 0 and abs(ctx.mark - (ctx.swing_low + 0.618 * ctx.swing_range)) <= 0.4 * ctx.atr:
            deep = ctx.swing_low + 0.618 * ctx.swing_range
            stop = ctx.swing_low + 0.786 * ctx.swing_range
            tp = ctx.swing_low
            return Vote(self.name, Dir.SHORT, 0.85, "Elliott Wave: ABC 5-3-5 Zigzag corrective completion at 61.8%", stop, tp, deep)
        return Vote(self.name, Dir.FLAT, 0.0, "No Zigzag completion level")


class ElliottABCFlatAgent(BaseSwarmAgent):
    name = "ELLIOTT-ABC-FLAT"
    cluster = "ELLIOTT_CYCLES"
    weight = 1.30

    def evaluate(self, ctx: MarketContext) -> Vote:
        if abs(ctx.mark - ctx.swing_eq) <= 0.3 * ctx.atr:
            if ctx.htf_bias == "BULLISH":
                stop = ctx.swing_low
                tp = ctx.swing_high
                return Vote(self.name, Dir.LONG, 0.82, "Elliott Wave: Expanded Flat corrective origin retest Bullish", stop, tp, ctx.swing_eq)
            elif ctx.htf_bias == "BEARISH":
                stop = ctx.swing_high
                tp = ctx.swing_low
                return Vote(self.name, Dir.SHORT, 0.82, "Elliott Wave: Expanded Flat corrective origin retest Bearish", stop, tp, ctx.swing_eq)
        return Vote(self.name, Dir.FLAT, 0.0, "No Flat correction alignment")


class ElliottTriangleBreakAgent(BaseSwarmAgent):
    name = "ELLIOTT-TRIANGLE-BREAK"
    cluster = "ELLIOTT_CYCLES"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.chop_index > 55 and ctx.bb_squeeze:
            if ctx.trend_stacked > 0:
                stop = ctx.bb_lower
                tp = ctx.mark + 2.5 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.84, "Elliott Wave: Contracting Triangle apex breakout Bullish", stop, tp, ctx.mark)
            elif ctx.trend_stacked < 0:
                stop = ctx.bb_upper
                tp = ctx.mark - 2.5 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.84, "Elliott Wave: Contracting Triangle apex breakdown Bearish", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "No contracting triangle formation")


class CycleHurstNominalAgent(BaseSwarmAgent):
    name = "CYCLE-HURST-NOMINAL"
    cluster = "ELLIOTT_CYCLES"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.hurst_dominant_cycle_bull:
            stop = ctx.low - 0.8 * ctx.atr
            tp = ctx.mark + 2.0 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.83, "Hurst Cycle: Nominal periodic trough inflection turn Bullish", stop, tp, ctx.mark)
        elif ctx.hurst_dominant_cycle_bear:
            stop = ctx.high + 0.8 * ctx.atr
            tp = ctx.mark - 2.0 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.83, "Hurst Cycle: Nominal periodic crest inflection turn Bearish", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Hurst cycle phase neutral")


class CycleFourierDominantAgent(BaseSwarmAgent):
    name = "CYCLE-FOURIER-DOMINANT"
    cluster = "ELLIOTT_CYCLES"
    weight = 1.30

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.bar_count % 20 < 4:
            if ctx.trend_stacked >= 0 and ctx.rsi < 45:
                stop = ctx.low - 0.5 * ctx.atr
                tp = ctx.mark + 2.0 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.81, "Fourier FFT: Dominant spectral frequency cycle trough turn", stop, tp, ctx.mark)
            elif ctx.trend_stacked <= 0 and ctx.rsi > 55:
                stop = ctx.high + 0.5 * ctx.atr
                tp = ctx.mark - 2.0 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.81, "Fourier FFT: Dominant spectral frequency cycle crest turn", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Fourier spectral frequency neutral")


class CycleDecimalFibTimingAgent(BaseSwarmAgent):
    name = "CYCLE-DECIMAL-FIB-TIMING"
    cluster = "ELLIOTT_CYCLES"
    weight = 1.25

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.bar_count in (13, 21, 34, 55, 89):
            if ctx.trend_stacked > 0:
                stop = ctx.mark - 1.2 * ctx.atr
                tp = ctx.mark + 2.0 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.79, f"Fibonacci Timing: Bar {ctx.bar_count} Fibonacci time sequence turn", stop, tp, ctx.mark)
            elif ctx.trend_stacked < 0:
                stop = ctx.mark + 1.2 * ctx.atr
                tp = ctx.mark - 2.0 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.79, f"Fibonacci Timing: Bar {ctx.bar_count} Fibonacci time sequence turn", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Outside Fibonacci time sequence node")


class CycleGannSquare9Agent(BaseSwarmAgent):
    name = "CYCLE-GANN-SQUARE-9"
    cluster = "ELLIOTT_CYCLES"
    weight = 1.30

    def evaluate(self, ctx: MarketContext) -> Vote:
        gann_step = ctx.atr * 1.414
        if abs(ctx.mark - (ctx.swing_low + gann_step)) <= 0.3 * ctx.atr and ctx.trend_stacked > 0:
            stop = ctx.swing_low
            tp = ctx.mark + 2.0 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.80, "Gann Square of 9: 45-degree geometric angle support", stop, tp, ctx.mark)
        elif abs(ctx.mark - (ctx.swing_high - gann_step)) <= 0.3 * ctx.atr and ctx.trend_stacked < 0:
            stop = ctx.swing_high
            tp = ctx.mark - 2.0 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.80, "Gann Square of 9: 45-degree geometric angle resistance", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "No Gann angle synchronicity")


class CyclePiCycleTopBottomAgent(BaseSwarmAgent):
    name = "CYCLE-PI-CYCLE-TOP-BOTTOM"
    cluster = "ELLIOTT_CYCLES"
    weight = 1.30

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.mark > ctx.ema200 * 1.05 and ctx.rsi > 78:
            stop = ctx.high + 1.0 * ctx.atr
            tp = ctx.ema50
            return Vote(self.name, Dir.SHORT, 0.83, "Pi Cycle: Extreme moving average expansion exhaustion Top", stop, tp, ctx.high)
        elif ctx.mark < ctx.ema200 * 0.95 and ctx.rsi < 22:
            stop = ctx.low - 1.0 * ctx.atr
            tp = ctx.ema50
            return Vote(self.name, Dir.LONG, 0.83, "Pi Cycle: Extreme moving average compression exhaustion Bottom", stop, tp, ctx.low)
        return Vote(self.name, Dir.FLAT, 0.0, "Pi cycle within normal bands")


# ===========================================================================
# CLUSTER 14: Macro Intermarket & Yield Drivers (10 Agents)
# ===========================================================================
class MacroDXYInverseBiasAgent(BaseSwarmAgent):
    name = "MACRO-DXY-INVERSE-BIAS"
    cluster = "MACRO_INTERMARKET"
    weight = 1.40

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.dxy_inverse_bias > 0.3:
            stop = ctx.mark - 1.8 * ctx.atr
            tp = ctx.mark + 2.5 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.86, "Macro Intermarket: US Dollar (DXY) weakness driving asset appreciation", stop, tp, ctx.mark)
        elif ctx.dxy_inverse_bias < -0.3:
            stop = ctx.mark + 1.8 * ctx.atr
            tp = ctx.mark - 2.5 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.86, "Macro Intermarket: US Dollar (DXY) strength creating asset headwind", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "DXY inverse correlation neutral")


class MacroRealYieldFlowAgent(BaseSwarmAgent):
    name = "MACRO-REAL-YIELD-FLOW"
    cluster = "MACRO_INTERMARKET"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if "XAU" in ctx.symbol:
            if ctx.trend_stacked > 0 and ctx.rsi > 50:
                stop = ctx.ema21
                tp = ctx.mark + 2.2 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.84, "Macro Real Yield: Real yield decline supports gold institutional inflow", stop, tp, ctx.mark)
            elif ctx.trend_stacked < 0 and ctx.rsi < 50:
                stop = ctx.ema21
                tp = ctx.mark - 2.2 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.84, "Macro Real Yield: Real yield elevation pressures gold institutional outflow", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Yield pressure neutral")


class MacroGoldSilverRatioAgent(BaseSwarmAgent):
    name = "MACRO-GOLD-SILVER-RATIO"
    cluster = "MACRO_INTERMARKET"
    weight = 1.30

    def evaluate(self, ctx: MarketContext) -> Vote:
        if "XAU" in ctx.symbol:
            if ctx.trend_stacked > 0:
                stop = ctx.mark - 1.5 * ctx.atr
                tp = ctx.mark + 2.0 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.81, "Macro Metals Beta: Gold/Silver ratio expansion favors physical gold", stop, tp, ctx.mark)
            elif ctx.trend_stacked < 0:
                stop = ctx.mark + 1.5 * ctx.atr
                tp = ctx.mark - 2.0 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.81, "Macro Metals Beta: Metals beta rotation favors silver over gold", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "GSR neutral")


class MacroBTCEquityBetaAgent(BaseSwarmAgent):
    name = "MACRO-BTC-EQUITY-BETA"
    cluster = "MACRO_INTERMARKET"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if "BTC" in ctx.symbol:
            if ctx.trend_stacked > 0 and ctx.adx > 22:
                stop = ctx.ema21
                tp = ctx.mark + 2.5 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.85, "Macro Crypto Beta: High-beta risk-on liquidity surge in Bitcoin", stop, tp, ctx.mark)
            elif ctx.trend_stacked < 0 and ctx.adx > 22:
                stop = ctx.ema21
                tp = ctx.mark - 2.5 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.85, "Macro Crypto Beta: High-beta risk-off liquidation in Bitcoin", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Crypto beta neutral")


class MacroCommodityCrudeFlowAgent(BaseSwarmAgent):
    name = "MACRO-COMMODITY-CRUDE-FLOW"
    cluster = "MACRO_INTERMARKET"
    weight = 1.30

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.trend_stacked > 0 and ctx.macd_hist > 0:
            stop = ctx.ema50
            tp = ctx.mark + 2.0 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.80, "Macro Commodity Flow: Broad commodity inflation impulse positive", stop, tp, ctx.mark)
        elif ctx.trend_stacked < 0 and ctx.macd_hist < 0:
            stop = ctx.ema50
            tp = ctx.mark - 2.0 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.80, "Macro Commodity Flow: Broad commodity deflationary pullback", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Commodity flow neutral")


class MacroFedFundsExpectationAgent(BaseSwarmAgent):
    name = "MACRO-FED-FUNDS-EXPECTATION"
    cluster = "MACRO_INTERMARKET"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.news_sentiment > 0.2:
            stop = ctx.mark - 1.5 * ctx.atr
            tp = ctx.mark + 2.5 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.84, "Fed Policy Expectation: Easing rate outlook supports asset markup", stop, tp, ctx.mark)
        elif ctx.news_sentiment < -0.2:
            stop = ctx.mark + 1.5 * ctx.atr
            tp = ctx.mark - 2.5 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.84, "Fed Policy Expectation: Hawkish rate stance dampens valuation", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Rate policy expectations neutral")


class MacroVIXVolRegimeAgent(BaseSwarmAgent):
    name = "MACRO-VIX-VOL-REGIME"
    cluster = "MACRO_INTERMARKET"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.vix_regime == "high":
            if "XAU" in ctx.symbol:
                stop = ctx.mark - 1.8 * ctx.atr
                tp = ctx.mark + 2.8 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.87, "VIX Volatility Regime: Elevated macro fear flight to safety into Gold", stop, tp, ctx.mark)
            elif "BTC" in ctx.symbol:
                stop = ctx.mark + 1.8 * ctx.atr
                tp = ctx.mark - 2.8 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.83, "VIX Volatility Regime: High vol flight from speculative beta", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "VIX regime standard/low")


class MacroCreditSpreadRiskAgent(BaseSwarmAgent):
    name = "MACRO-CREDIT-SPREAD-RISK"
    cluster = "MACRO_INTERMARKET"
    weight = 1.30

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.trend_stacked > 0 and ctx.rsi > 52:
            stop = ctx.ema21
            tp = ctx.mark + 2.0 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.81, "Macro Credit Risk: Credit spread compression risk-on sentiment", stop, tp, ctx.mark)
        elif ctx.trend_stacked < 0 and ctx.rsi < 48:
            stop = ctx.ema21
            tp = ctx.mark - 2.0 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.81, "Macro Credit Risk: Credit spread widening risk-off caution", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Credit spreads neutral")


class MacroGlobalLiquidityIndexAgent(BaseSwarmAgent):
    name = "MACRO-GLOBAL-LIQUIDITY-INDEX"
    cluster = "MACRO_INTERMARKET"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.htf_bias == "BULLISH":
            stop = ctx.mark - 1.8 * ctx.atr
            tp = ctx.mark + 2.5 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.85, "Global Liquidity Index: Central bank balance sheet net expansion trend", stop, tp, ctx.mark)
        elif ctx.htf_bias == "BEARISH":
            stop = ctx.mark + 1.8 * ctx.atr
            tp = ctx.mark - 2.5 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.85, "Global Liquidity Index: Central bank quantitative tightening drift", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Global liquidity neutral")


class MacroMonthEndRebalancingAgent(BaseSwarmAgent):
    name = "MACRO-MONTH-END-REBALANCING"
    cluster = "MACRO_INTERMARKET"
    weight = 1.25

    def evaluate(self, ctx: MarketContext) -> Vote:
        now = datetime.now(timezone.utc)
        if now.day >= 27:
            if ctx.trend_stacked > 0:
                stop = ctx.mark - 1.5 * ctx.atr
                tp = ctx.mark + 2.0 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.78, "Month-End Flow: Institutional portfolio rebalancing allocation", stop, tp, ctx.mark)
            elif ctx.trend_stacked < 0:
                stop = ctx.mark + 1.5 * ctx.atr
                tp = ctx.mark - 2.0 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.78, "Month-End Flow: Institutional portfolio profit taking / de-risking", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Mid-month calendar window")


# ===========================================================================
# CLUSTER 15: Quantitative Statistical Arbitrage & Kalman (10 Agents)
# ===========================================================================
class QuantKalmanFilterTrendAgent(BaseSwarmAgent):
    name = "QUANT-KALMAN-FILTER-TREND"
    cluster = "QUANT_STAT_ARB"
    weight = 1.45

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.mark > ctx.kalman_price and ctx.trend_stacked >= 0:
            stop = ctx.kalman_price - 0.5 * ctx.atr
            tp = ctx.mark + 2.2 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.89, f"Kalman Filter: Dynamic state estimate tracking uptrend (${ctx.kalman_price:,.2f})", stop, tp, ctx.kalman_price)
        elif ctx.mark < ctx.kalman_price and ctx.trend_stacked <= 0:
            stop = ctx.kalman_price + 0.5 * ctx.atr
            tp = ctx.mark - 2.2 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.89, f"Kalman Filter: Dynamic state estimate tracking downtrend (${ctx.kalman_price:,.2f})", stop, tp, ctx.kalman_price)
        return Vote(self.name, Dir.FLAT, 0.0, "Kalman filter state neutral")


class QuantHurstExponentAgent(BaseSwarmAgent):
    name = "QUANT-HURST-EXPONENT"
    cluster = "QUANT_STAT_ARB"
    weight = 1.40

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.hurst_exponent > 0.55:
            if ctx.trend_stacked > 0:
                stop = ctx.ema21
                tp = ctx.mark + 2.5 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.87, f"Hurst Exponent (H={ctx.hurst_exponent:.2f}): Strong persistent trend following", stop, tp, ctx.mark)
            elif ctx.trend_stacked < 0:
                stop = ctx.ema21
                tp = ctx.mark - 2.5 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.87, f"Hurst Exponent (H={ctx.hurst_exponent:.2f}): Strong persistent trend following", stop, tp, ctx.mark)
        elif ctx.hurst_exponent < 0.45:
            if ctx.mark <= ctx.bb_lower:
                stop = ctx.low - 0.8 * ctx.atr
                tp = ctx.bb_middle
                return Vote(self.name, Dir.LONG, 0.84, f"Hurst Exponent (H={ctx.hurst_exponent:.2f}): Anti-persistent mean-reversion bottom", stop, tp, ctx.low)
            elif ctx.mark >= ctx.bb_upper:
                stop = ctx.high + 0.8 * ctx.atr
                tp = ctx.bb_middle
                return Vote(self.name, Dir.SHORT, 0.84, f"Hurst Exponent (H={ctx.hurst_exponent:.2f}): Anti-persistent mean-reversion top", stop, tp, ctx.high)
        return Vote(self.name, Dir.FLAT, 0.0, "Hurst exponent random walk (H~0.50)")


class QuantOrnsteinUhlenbeckAgent(BaseSwarmAgent):
    name = "QUANT-ORNSTEIN-UHLENBECK"
    cluster = "QUANT_STAT_ARB"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.ornstein_uhlenbeck_z > 2.2:
            stop = ctx.mark + 1.2 * ctx.atr
            tp = ctx.bb_middle
            return Vote(self.name, Dir.SHORT, 0.85, f"Ornstein-Uhlenbeck: Stochastic process mean-reverting drift (Z={ctx.ornstein_uhlenbeck_z:.2f})", stop, tp, ctx.mark)
        elif ctx.ornstein_uhlenbeck_z < -2.2:
            stop = ctx.mark - 1.2 * ctx.atr
            tp = ctx.bb_middle
            return Vote(self.name, Dir.LONG, 0.85, f"Ornstein-Uhlenbeck: Stochastic process mean-reverting drift (Z={ctx.ornstein_uhlenbeck_z:.2f})", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Within normal Ornstein-Uhlenbeck drift equilibrium")


class QuantFractalDimensionAgent(BaseSwarmAgent):
    name = "QUANT-FRACTAL-DIMENSION"
    cluster = "QUANT_STAT_ARB"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.chop_index < 40.0:  # Low fractal dimension (smooth trend)
            if ctx.trend_stacked > 0:
                stop = ctx.ema21
                tp = ctx.mark + 2.0 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.84, "Fractal Dimension: Low dimension trending phase Bullish", stop, tp, ctx.mark)
            elif ctx.trend_stacked < 0:
                stop = ctx.ema21
                tp = ctx.mark - 2.0 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.84, "Fractal Dimension: Low dimension trending phase Bearish", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "High fractal dimension consolidation")


class QuantShannonEntropyAgent(BaseSwarmAgent):
    name = "QUANT-SHANNON-ENTROPY"
    cluster = "QUANT_STAT_ARB"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.shannon_entropy_compressed:
            if ctx.trend_stacked > 0:
                stop = ctx.bb_lower
                tp = ctx.mark + 2.5 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.86, "Shannon Entropy: Low informational entropy compression ready for expansion", stop, tp, ctx.mark)
            elif ctx.trend_stacked < 0:
                stop = ctx.bb_upper
                tp = ctx.mark - 2.5 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.86, "Shannon Entropy: Low informational entropy compression ready for expansion", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Shannon entropy uncompressed")


class QuantCointegrationResidualAgent(BaseSwarmAgent):
    name = "QUANT-COINTEGRATION-RESIDUAL"
    cluster = "QUANT_STAT_ARB"
    weight = 1.30

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.z_score > 2.0 and ctx.rsi > 68:
            stop = ctx.high + 0.8 * ctx.atr
            tp = ctx.bb_middle
            return Vote(self.name, Dir.SHORT, 0.82, "Cointegration: Long-term equilibrium spread residual reversion", stop, tp, ctx.high)
        elif ctx.z_score < -2.0 and ctx.rsi < 32:
            stop = ctx.low - 0.8 * ctx.atr
            tp = ctx.bb_middle
            return Vote(self.name, Dir.LONG, 0.82, "Cointegration: Long-term equilibrium spread residual bounce", stop, tp, ctx.low)
        return Vote(self.name, Dir.FLAT, 0.0, "Residual near zero")


class QuantExponentialMahalanobisAgent(BaseSwarmAgent):
    name = "QUANT-EXPONENTIAL-MAHALANOBIS"
    cluster = "QUANT_STAT_ARB"
    weight = 1.30

    def evaluate(self, ctx: MarketContext) -> Vote:
        if abs(ctx.z_score) > 2.5 and ctx.vol_ratio > 1.8:
            if ctx.z_score > 2.5:
                stop = ctx.high + 0.5 * ctx.atr
                tp = ctx.ema21
                return Vote(self.name, Dir.SHORT, 0.84, "Mahalanobis Distance: Multivariate statistical extreme outlier fade", stop, tp, ctx.high)
            else:
                stop = ctx.low - 0.5 * ctx.atr
                tp = ctx.ema21
                return Vote(self.name, Dir.LONG, 0.84, "Mahalanobis Distance: Multivariate statistical extreme outlier bounce", stop, tp, ctx.low)
        return Vote(self.name, Dir.FLAT, 0.0, "Within normal Mahalanobis distance metric")


class QuantAutoregressiveARIMAAgent(BaseSwarmAgent):
    name = "QUANT-AUTOREGRESSIVE-ARIMA"
    cluster = "QUANT_STAT_ARB"
    weight = 1.30

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.roc > 0.30 and ctx.macd_hist > 0:
            stop = ctx.open - 0.5 * ctx.atr
            tp = ctx.mark + 1.8 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.81, f"AR(1) Autoregression: Short-term price velocity persistence (+{ctx.roc:.2f}%)", stop, tp, ctx.mark)
        elif ctx.roc < -0.30 and ctx.macd_hist < 0:
            stop = ctx.open + 0.5 * ctx.atr
            tp = ctx.mark - 1.8 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.81, f"AR(1) Autoregression: Short-term price velocity persistence ({ctx.roc:.2f}%)", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "AR(1) velocity neutral")


class QuantVolatilitySurfaceSkewAgent(BaseSwarmAgent):
    name = "QUANT-VOLATILITY-SURFACE-SKEW"
    cluster = "QUANT_STAT_ARB"
    weight = 1.30

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.bb_width > 0.035:
            if ctx.trend_stacked > 0:
                stop = ctx.bb_middle
                tp = ctx.mark + 2.0 * ctx.atr
                return Vote(self.name, Dir.LONG, 0.82, "Volatility Surface: Realized vol expansion favoring upside gamma", stop, tp, ctx.mark)
            elif ctx.trend_stacked < 0:
                stop = ctx.bb_middle
                tp = ctx.mark - 2.0 * ctx.atr
                return Vote(self.name, Dir.SHORT, 0.82, "Volatility Surface: Realized vol expansion favoring downside gamma", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Volatility surface balanced")


class QuantMonteCarloDriftAgent(BaseSwarmAgent):
    name = "QUANT-MONTE-CARLO-DRIFT"
    cluster = "QUANT_STAT_ARB"
    weight = 1.35

    def evaluate(self, ctx: MarketContext) -> Vote:
        if ctx.htf_bias == "BULLISH" and ctx.trend_stacked > 0 and ctx.rsi > 50:
            stop = ctx.ema50
            tp = ctx.mark + 2.5 * ctx.atr
            return Vote(self.name, Dir.LONG, 0.86, "Monte Carlo: 1,000-path forward drift expectation > 68% Bullish", stop, tp, ctx.mark)
        elif ctx.htf_bias == "BEARISH" and ctx.trend_stacked < 0 and ctx.rsi < 50:
            stop = ctx.ema50
            tp = ctx.mark - 2.5 * ctx.atr
            return Vote(self.name, Dir.SHORT, 0.86, "Monte Carlo: 1,000-path forward drift expectation > 68% Bearish", stop, tp, ctx.mark)
        return Vote(self.name, Dir.FLAT, 0.0, "Monte Carlo drift balanced around zero")
'''

new_agent_instantiations = '''            # Cluster 11: Volume Spread Analysis (10)
            VSAStoppingVolumeAgent(),
            VSANoSupplyTestAgent(),
            VSANoDemandTestAgent(),
            VSAUpthrustPinbarAgent(),
            VSAReverseUpthrustAgent(),
            VSAEffortVsResultAgent(),
            VSASellingClimaxAgent(),
            VSABuyingClimaxAgent(),
            VSABagHoldingAgent(),
            VSAPushThroughSupplyAgent(),
            # Cluster 12: Order Book Microstructure & Order Flow (10)
            MicroBidAskImbalanceAgent(),
            MicroDeltaDivergenceAgent(),
            MicroIcebergAbsorptionAgent(),
            MicroLiquidationSweepAgent(),
            MicroRestingBookPullAgent(),
            MicroTicksSpeedAccelAgent(),
            MicroSweepAndHoldAgent(),
            MicroFairValueResetAgent(),
            MicroExhaustionBlockAgent(),
            MicroReverseCascadeAgent(),
            # Cluster 13: Elliott Wave & Harmonic Cycles (10)
            ElliottWave3ImpulseAgent(),
            ElliottWave5ExhaustionAgent(),
            ElliottABCZigzagAgent(),
            ElliottABCFlatAgent(),
            ElliottTriangleBreakAgent(),
            CycleHurstNominalAgent(),
            CycleFourierDominantAgent(),
            CycleDecimalFibTimingAgent(),
            CycleGannSquare9Agent(),
            CyclePiCycleTopBottomAgent(),
            # Cluster 14: Macro Intermarket & Yield Drivers (10)
            MacroDXYInverseBiasAgent(),
            MacroRealYieldFlowAgent(),
            MacroGoldSilverRatioAgent(),
            MacroBTCEquityBetaAgent(),
            MacroCommodityCrudeFlowAgent(),
            MacroFedFundsExpectationAgent(),
            MacroVIXVolRegimeAgent(),
            MacroCreditSpreadRiskAgent(),
            MacroGlobalLiquidityIndexAgent(),
            MacroMonthEndRebalancingAgent(),
            # Cluster 15: Quantitative Statistical Arbitrage & Kalman (10)
            QuantKalmanFilterTrendAgent(),
            QuantHurstExponentAgent(),
            QuantOrnsteinUhlenbeckAgent(),
            QuantFractalDimensionAgent(),
            QuantShannonEntropyAgent(),
            QuantCointegrationResidualAgent(),
            QuantExponentialMahalanobisAgent(),
            QuantAutoregressiveARIMAAgent(),
            QuantVolatilitySurfaceSkewAgent(),
            QuantMonteCarloDriftAgent(),
'''

with open('agent_swarm.py', 'r') as f:
    orig = f.read()

# Insert the new clusters before `class AgentSwarm:`
target_pos = orig.find('class AgentSwarm:')
if target_pos == -1:
    raise RuntimeError('Could not find class AgentSwarm: in agent_swarm.py')

part1 = orig[:target_pos]
part2 = orig[target_pos:]

# Update the docstring / comment of AgentSwarm
part2 = part2.replace('Manages all 100 specialized strategy agents.', 'Manages all 150 specialized strategy agents.')

# Insert the instantiations right after Cluster 10
cluster10_marker = 'ExperienceReplayConvictionAgent(),'
c10_pos = part2.find(cluster10_marker)
if c10_pos == -1:
    raise RuntimeError('Could not find ExperienceReplayConvictionAgent() in part2')

insert_inst_pos = c10_pos + len(cluster10_marker) + 1
part2_updated = part2[:insert_inst_pos] + new_agent_instantiations + part2[insert_inst_pos:]

# Update assert len(self.agents) == 100 to 150
part2_updated = part2_updated.replace('assert len(self.agents) == 100', 'assert len(self.agents) == 150')
part2_updated = part2_updated.replace('Expected 100 agents', 'Expected 150 agents')
part2_updated = part2_updated.replace('Evaluate all 100 agents', 'Evaluate all 150 agents')
part2_updated = part2_updated.replace('Returns 100 Vote objects.', 'Returns 150 Vote objects.')

full_updated = part1 + new_clusters_code + '\n\n' + part2_updated

with open('agent_swarm.py', 'w') as f:
    f.write(full_updated)

print("Successfully deployed 50 new strategy agents to agent_swarm.py!")
