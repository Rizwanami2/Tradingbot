//+------------------------------------------------------------------+
//| TradingBot_DemoEA.mq5                                            |
//| Educational MT5 Expert Advisor - demo / Strategy Tester only     |
//| Automated trading is OFF by default. Review and test before use.  |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "Previous completed D1 high/low sweep and rejection EA."
#property description "Disabled by default; permits orders only in Tester or DEMO."

#include <Trade/Trade.mqh>

CTrade trade;

// ---- Safety switches ----
input bool            EnableTrading             = false;       // Must be enabled manually for demo/tester
input ENUM_TIMEFRAMES SignalTimeframe           = PERIOD_M1;
input ulong           MagicNumber               = 26101601;

// ---- Strategy settings ----
input double          RiskPercent               = 0.20;        // Per-trade equity risk; hard-capped at 1%
input double          RewardRiskRatio           = 2.0;
input int             StopBufferPoints          = 30;
input int             MaxSpreadPoints           = 50;
input int             SetupExpiryBars           = 3;           // Pending confirmation expires after this many bars
input bool            OnePositionPerSymbol      = true;

// ---- Risk guards ----
input double          MaxDailyDrawdownPercent   = 2.0;         // Block new orders after this daily equity loss
input double          MaxAllowedRiskPercent     = 1.0;         // Hard safety cap for RiskPercent

datetime g_lastBarTime = 0;
bool     g_pendingBuy = false;
bool     g_pendingSell = false;
double   g_buyTrigger = 0.0;
double   g_buySL      = 0.0;
double   g_sellTrigger= 0.0;
double   g_sellSL     = 0.0;
int      g_setupBarsRemaining = 0;
int      g_pendingBarsRemaining = 0;
string   g_dailyEquityKey = "";

//+------------------------------------------------------------------+
int OnInit()
{
   if(RiskPercent <= 0.0 || RiskPercent > MathMin(MaxAllowedRiskPercent, 1.0))
   {
      Print("Safety stop: RiskPercent must be > 0 and <= the 1% hard cap.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(RewardRiskRatio <= 0.0 || StopBufferPoints < 0 || MaxSpreadPoints < 0 ||
      SetupExpiryBars < 1 || MaxDailyDrawdownPercent <= 0.0 ||
      MaxDailyDrawdownPercent > 10.0)
   {
      Print("Safety stop: one or more risk/strategy inputs are invalid.");
      return INIT_PARAMETERS_INCORRECT;
   }

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(10);
   trade.SetTypeFillingBySymbol(_Symbol);

   g_lastBarTime = iTime(_Symbol, SignalTimeframe, 0);
   RefreshDailyEquityBaseline();
   Print("TradingBot_DemoEA initialized. EnableTrading=", EnableTrading,
         ". Orders are restricted to Strategy Tester or DEMO accounts.");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnTick()
{
   RefreshDailyEquityBaseline();

   // Pending confirmation can trigger intrabar, but only from a closed signal candle.
   if(g_pendingBuy || g_pendingSell)
      CheckPendingEntry();

   datetime barTime = iTime(_Symbol, SignalTimeframe, 0);
   if(barTime == 0 || barTime == g_lastBarTime)
      return;

   g_lastBarTime = barTime;
   UpdatePendingSetupLifetime();
   DetectNewSetup();
}

//+------------------------------------------------------------------+
bool IsDemoOrTester()
{
   if((bool)MQLInfoInteger(MQL_TESTER) ||
      (bool)MQLInfoInteger(MQL_OPTIMIZATION))
      return true;

   ENUM_ACCOUNT_TRADE_MODE mode =
      (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   return (mode == ACCOUNT_TRADE_MODE_DEMO);
}

//+------------------------------------------------------------------+
bool TradingSafetyChecksPass()
{
   if(!EnableTrading)
   {
      Print("Entry blocked: EnableTrading=false.");
      return false;
   }

   if(!IsDemoOrTester())
   {
      Print("Entry blocked: this EA only permits Strategy Tester or DEMO accounts.");
      return false;
   }

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      !MQLInfoInteger(MQL_TRADE_ALLOWED) ||
      !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
   {
      Print("Entry blocked: terminal, EA, or account trading permission is disabled.");
      return false;
   }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick) || tick.ask <= 0.0 || tick.bid <= 0.0)
      return false;

   double spreadPoints = (tick.ask - tick.bid) / _Point;
   if(spreadPoints > MaxSpreadPoints)
   {
      Print("Entry blocked: spread ", DoubleToString(spreadPoints, 1),
            " points exceeds limit ", MaxSpreadPoints, ".");
      return false;
   }

   if(DailyDrawdownLimitReached())
   {
      Print("Entry blocked: daily equity drawdown guard reached.");
      return false;
   }

   if(OnePositionPerSymbol && HasPositionForThisEA())
   {
      Print("Entry blocked: this EA already has a position on ", _Symbol, ".");
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
void DetectNewSetup()
{
   // Shift 1 is the most recently completed signal candle.
   MqlRates bars[];
   ArraySetAsSeries(bars, true);
   if(CopyRates(_Symbol, SignalTimeframe, 1, 2, bars) != 2)
      return;

   double priorDayHigh = iHigh(_Symbol, PERIOD_D1, 1);
   double priorDayLow  = iLow(_Symbol, PERIOD_D1, 1);
   if(priorDayHigh <= 0.0 || priorDayLow <= 0.0 || priorDayHigh <= priorDayLow)
      return;

   MqlRates signal = bars[0];

   // Bearish sweep/rejection: signal candle trades above the prior D1 high,
   // then closes back below it as a bearish candle.
   if(signal.high > priorDayHigh && signal.close < priorDayHigh &&
      signal.close < signal.open)
   {
      g_pendingSell = true;
      g_pendingBuy = false;
      g_sellTrigger = signal.low;
      g_sellSL = signal.high + StopBufferPoints * _Point;
      g_pendingBarsRemaining = SetupExpiryBars;
      Print("Bearish D1-high sweep detected. Waiting for a break below ",
            DoubleToString(g_sellTrigger, _Digits));
      return;
   }

   // Bullish sweep/rejection: signal candle trades below the prior D1 low,
   // then closes back above it as a bullish candle.
   if(signal.low < priorDayLow && signal.close > priorDayLow &&
      signal.close > signal.open)
   {
      g_pendingBuy = true;
      g_pendingSell = false;
      g_buyTrigger = signal.high;
      g_buySL = signal.low - StopBufferPoints * _Point;
      g_pendingBarsRemaining = SetupExpiryBars;
      Print("Bullish D1-low sweep detected. Waiting for a break above ",
            DoubleToString(g_buyTrigger, _Digits));
   }
}

//+------------------------------------------------------------------+
void CheckPendingEntry()
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;

   if(g_pendingSell && tick.bid <= g_sellTrigger)
   {
      double entry = tick.bid;
      double sl = NormalizeDouble(g_sellSL, _Digits);
      double riskDistance = sl - entry;
      if(riskDistance <= 0.0)
      {
         g_pendingSell = false;
         return;
      }
      double tp = NormalizeDouble(entry - riskDistance * RewardRiskRatio, _Digits);
      if(TradingSafetyChecksPass())
         SubmitMarketOrder(ORDER_TYPE_SELL, entry, sl, tp);
      g_pendingSell = false;
   }
   else if(g_pendingBuy && tick.ask >= g_buyTrigger)
   {
      double entry = tick.ask;
      double sl = NormalizeDouble(g_buySL, _Digits);
      double riskDistance = entry - sl;
      if(riskDistance <= 0.0)
      {
         g_pendingBuy = false;
         return;
      }
      double tp = NormalizeDouble(entry + riskDistance * RewardRiskRatio, _Digits);
      if(TradingSafetyChecksPass())
         SubmitMarketOrder(ORDER_TYPE_BUY, entry, sl, tp);
      g_pendingBuy = false;
   }
}

//+------------------------------------------------------------------+
void UpdatePendingSetupLifetime()
{
   if(g_pendingBuy || g_pendingSell)
   {
      g_pendingBarsRemaining--;
      if(g_pendingBarsRemaining <= 0)
      {
         g_pendingBuy = false;
         g_pendingSell = false;
         Print("Pending setup expired without confirmation.");
      }
   }
}

//+------------------------------------------------------------------+
void SubmitMarketOrder(ENUM_ORDER_TYPE orderType, double requestedEntry,
                       double sl, double tp)
{
   // Repeat account restrictions immediately before sending an order.
   if(!EnableTrading || !IsDemoOrTester())
   {
      Print("Order cancelled by final safety gate.");
      return;
   }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;

   double entry = (orderType == ORDER_TYPE_BUY) ? tick.ask : tick.bid;
   if(orderType == ORDER_TYPE_BUY && (sl >= entry || tp <= entry))
   {
      Print("Order cancelled: invalid BUY SL/TP geometry.");
      return;
   }
   if(orderType == ORDER_TYPE_SELL && (sl <= entry || tp >= entry))
   {
      Print("Order cancelled: invalid SELL SL/TP geometry.");
      return;
   }

   // Respect broker minimum stop distance.
   long stopsLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minDistance = (double)stopsLevel * _Point;
   if(orderType == ORDER_TYPE_BUY &&
      ((entry - sl) < minDistance || (tp - entry) < minDistance))
   {
      Print("Order cancelled: BUY stop/target is inside broker stop distance.");
      return;
   }
   if(orderType == ORDER_TYPE_SELL &&
      ((sl - entry) < minDistance || (entry - tp) < minDistance))
   {
      Print("Order cancelled: SELL stop/target is inside broker stop distance.");
      return;
   }

   double volume = CalculateRiskBasedVolume(orderType, entry, sl);
   if(volume <= 0.0)
   {
      Print("Order cancelled: risk-sized volume is below broker minimum or invalid.");
      return;
   }

   bool sent = false;
   if(orderType == ORDER_TYPE_BUY)
      sent = trade.Buy(volume, _Symbol, 0.0, sl, tp, "TradingBot D1 sweep BUY");
   else
      sent = trade.Sell(volume, _Symbol, 0.0, sl, tp, "TradingBot D1 sweep SELL");

   if(!sent)
      Print("Order request failed. Retcode=", trade.ResultRetcode(),
            " ", trade.ResultRetcodeDescription());
   else
      Print("Demo/tester order accepted. Volume=", DoubleToString(volume, 2),
            " SL=", DoubleToString(sl, _Digits),
            " TP=", DoubleToString(tp, _Digits));
}

//+------------------------------------------------------------------+
double CalculateRiskBasedVolume(ENUM_ORDER_TYPE orderType, double entry, double sl)
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskPct = MathMin(RiskPercent, MathMin(MaxAllowedRiskPercent, 1.0));
   double riskCash = equity * riskPct / 100.0;
   if(equity <= 0.0 || riskCash <= 0.0)
      return 0.0;

   double profitAtStop = 0.0;
   if(!OrderCalcProfit(orderType, _Symbol, 1.0, entry, sl, profitAtStop))
   {
      Print("OrderCalcProfit failed; refusing to guess position size.");
      return 0.0;
   }

   double lossPerLot = MathAbs(profitAtStop);
   if(lossPerLot <= 0.0)
      return 0.0;

   double rawVolume = riskCash / lossPerLot;
   double minVolume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxVolume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(minVolume <= 0.0 || maxVolume <= 0.0 || step <= 0.0)
      return 0.0;

   // Round DOWN only. Never round up to the broker's minimum lot.
   double volume = MathFloor(rawVolume / step) * step;
   volume = MathMin(volume, maxVolume);
   if(volume < minVolume)
   {
      Print("Risk-sized volume ", DoubleToString(volume, 4),
            " is below broker minimum ", DoubleToString(minVolume, 4),
            "; trade skipped to avoid increasing risk.");
      return 0.0;
   }

   int volumeDigits = 0;
   double stepCheck = step;
   while(volumeDigits < 8 && MathAbs(stepCheck - MathRound(stepCheck)) > 1e-8)
   {
      stepCheck *= 10.0;
      volumeDigits++;
   }
   return NormalizeDouble(volume, volumeDigits);
}

//+------------------------------------------------------------------+
bool HasPositionForThisEA()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         (ulong)PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
void RefreshDailyEquityBaseline()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   string datePart = StringFormat("%04d%02d%02d", dt.year, dt.mon, dt.day);
   g_dailyEquityKey = StringFormat("TB_D1EQ_%I64d_%s_%I64u",
                                   AccountInfoInteger(ACCOUNT_LOGIN),
                                   _Symbol, MagicNumber);
   string todayKey = g_dailyEquityKey + "_" + datePart;

   // Preserve today's baseline across terminal restarts via terminal global variables.
   if(!GlobalVariableCheck(todayKey))
      GlobalVariableSet(todayKey, AccountInfoDouble(ACCOUNT_EQUITY));

   // Keep the current key for the drawdown guard.
   g_dailyEquityKey = todayKey;
}

//+------------------------------------------------------------------+
bool DailyDrawdownLimitReached()
{
   if(g_dailyEquityKey == "" || !GlobalVariableCheck(g_dailyEquityKey))
      return false;

   double baseline = GlobalVariableGet(g_dailyEquityKey);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(baseline <= 0.0)
      return true;

   double drawdownPct = (baseline - equity) / baseline * 100.0;
   return (drawdownPct >= MaxDailyDrawdownPercent);
}
//+------------------------------------------------------------------+