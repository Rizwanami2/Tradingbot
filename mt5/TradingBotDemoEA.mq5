//+------------------------------------------------------------------+
//| TradingBotDemoEA.mq5                                             |
//| Native MQL5 demo/tester EA; no live-account execution permitted.  |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "TradingBot daily liquidity sweep confirmation EA (demo/tester only)"

#include <Trade/Trade.mqh>

input bool   EnableTrading = false;       // Must be explicitly enabled for demo/tester
input double RiskPercent = 0.20;          // Percent of equity at risk per trade
input double MaxDailyLossPercent = 2.0;   // Halt new entries after this daily equity drawdown
input double RewardRisk = 1.5;
input int    MaxSpreadPoints = 35;
input int    StopBufferPoints = 10;
input ulong  MagicNumber = 112233;
input ENUM_TIMEFRAMES SignalTimeframe = PERIOD_M1;
input bool   OnePositionPerSymbol = true;

CTrade trade;
datetime last_bar_time = 0;
datetime day_start_time = 0;
double day_start_equity = 0.0;
bool daily_halt = false;

bool IsDemoOrTester()
{
   if (MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_OPTIMIZATION))
      return true;
   return ((ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO);
}

void RefreshDailyGuard()
{
   MqlDateTime now_parts;
   TimeToStruct(TimeCurrent(), now_parts);
   now_parts.hour = 0; now_parts.min = 0; now_parts.sec = 0;
   datetime today = StructToTime(now_parts);
   if (today != day_start_time)
   {
      day_start_time = today;
      day_start_equity = AccountInfoDouble(ACCOUNT_EQUITY);
      daily_halt = false;
   }
   if (day_start_equity > 0.0)
   {
      double drawdown_pct = 100.0 * (day_start_equity - AccountInfoDouble(ACCOUNT_EQUITY)) / day_start_equity;
      if (drawdown_pct >= MaxDailyLossPercent)
         daily_halt = true;
   }
}

bool IsNewBar()
{
   datetime bar_time = iTime(_Symbol, SignalTimeframe, 0);
   if (bar_time <= 0 || bar_time == last_bar_time) return false;
   last_bar_time = bar_time;
   return true;
}

bool HasOurPosition()
{
   for (int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if (ticket == 0) continue;
      if (PositionGetString(POSITION_SYMBOL) == _Symbol &&
          (ulong)PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         return true;
   }
   return false;
}

double NormalizeVolume(double volume)
{
   double min_volume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_volume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if (step <= 0.0) return 0.0;
   volume = MathFloor(volume / step) * step;
   volume = MathMin(volume, max_volume);
   if (volume < min_volume) return 0.0; // Never round risk up to broker minimum.
   int digits = 0;
   double tmp = step;
   while (digits < 8 && MathAbs(tmp - MathRound(tmp)) > 1e-8) { tmp *= 10.0; digits++; }
   return NormalizeDouble(volume, digits);
}

double CalculateVolume(ENUM_ORDER_TYPE order_type, double entry, double stop)
{
   double risk_money = AccountInfoDouble(ACCOUNT_EQUITY) * RiskPercent / 100.0;
   double loss_for_one_lot = 0.0;
   if (risk_money <= 0.0 || !OrderCalcProfit(order_type, _Symbol, 1.0, entry, stop, loss_for_one_lot))
      return 0.0;
   loss_for_one_lot = MathAbs(loss_for_one_lot);
   if (loss_for_one_lot <= 0.0) return 0.0;
   return NormalizeVolume(risk_money / loss_for_one_lot);
}

bool SpreadAllowed()
{
   MqlTick tick;
   if (!SymbolInfoTick(_Symbol, tick)) return false;
   if (_Point <= 0.0) return false;
   return ((tick.ask - tick.bid) / _Point <= MaxSpreadPoints);
}

void SubmitDemoOrder(ENUM_ORDER_TYPE side, double stop, double target)
{
   if (!EnableTrading || !IsDemoOrTester() || daily_halt || !SpreadAllowed()) return;
   if (OnePositionPerSymbol && HasOurPosition()) return;

   MqlTick tick;
   if (!SymbolInfoTick(_Symbol, tick)) return;
   double entry = (side == ORDER_TYPE_BUY) ? tick.ask : tick.bid;
   stop = NormalizeDouble(stop, _Digits);
   target = NormalizeDouble(target, _Digits);
   if ((side == ORDER_TYPE_BUY && (stop >= entry || target <= entry)) ||
       (side == ORDER_TYPE_SELL && (stop <= entry || target >= entry))) return;

   double volume = CalculateVolume(side, entry, stop);
   if (volume <= 0.0)
   {
      Print("EA safety block: calculated volume below broker minimum or invalid risk inputs.");
      return;
   }

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(10);
   bool sent = false;
   if (side == ORDER_TYPE_BUY)
      sent = trade.Buy(volume, _Symbol, 0.0, stop, target, "TradingBot demo sweep long");
   else
      sent = trade.Sell(volume, _Symbol, 0.0, stop, target, "TradingBot demo sweep short");

   if (!sent)
      PrintFormat("Order rejected/failed. retcode=%u, %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
   else
      PrintFormat("Demo/tester order submitted. ticket=%I64u volume=%.4f", trade.ResultOrder(), volume);
}

int OnInit()
{
   if (RiskPercent <= 0.0 || RiskPercent > 1.0 || MaxDailyLossPercent <= 0.0 ||
       RewardRisk <= 0.0 || MaxSpreadPoints <= 0 || StopBufferPoints < 0)
      return INIT_PARAMETERS_INCORRECT;
   if (EnableTrading && !IsDemoOrTester())
   {
      Print("SAFETY BLOCK: This EA permits order submission only in Strategy Tester or an MT5 DEMO account.");
      return INIT_FAILED;
   }
   trade.SetExpertMagicNumber(MagicNumber);
   day_start_time = 0;
   RefreshDailyGuard();
   Print("TradingBotDemoEA initialized. EnableTrading defaults to false; live accounts are prohibited.");
   return INIT_SUCCEEDED;
}

void OnTick()
{
   RefreshDailyGuard();
   if (!IsNewBar()) return;

   // Use only completed bars. A sweep of yesterday's high/low must close back
   // inside the level before a confirmation signal is considered.
   MqlRates bars[3];
   ArraySetAsSeries(bars, true);
   if (CopyRates(_Symbol, SignalTimeframe, 0, 3, bars) != 3) return;

   double prev_day_high = iHigh(_Symbol, PERIOD_D1, 1);
   double prev_day_low  = iLow(_Symbol, PERIOD_D1, 1);
   if (prev_day_high <= 0.0 || prev_day_low <= 0.0) return;

   MqlRates signal = bars[1];
   double buffer = StopBufferPoints * _Point;

   // Bearish rejection after sweeping the previous completed daily high.
   if (signal.high > prev_day_high && signal.close < prev_day_high && signal.close < signal.open)
   {
      double stop = signal.high + buffer;
      MqlTick tick;
      if (!SymbolInfoTick(_Symbol, tick)) return;
      double entry = tick.bid;
      double risk = stop - entry;
      if (risk > 0.0)
         SubmitDemoOrder(ORDER_TYPE_SELL, stop, entry - RewardRisk * risk);
      return;
   }

   // Bullish rejection after sweeping the previous completed daily low.
   if (signal.low < prev_day_low && signal.close > prev_day_low && signal.close > signal.open)
   {
      double stop = signal.low - buffer;
      MqlTick tick;
      if (!SymbolInfoTick(_Symbol, tick)) return;
      double entry = tick.ask;
      double risk = entry - stop;
      if (risk > 0.0)
         SubmitDemoOrder(ORDER_TYPE_BUY, stop, entry + RewardRisk * risk);
   }
}

void OnDeinit(const int reason)
{
   PrintFormat("TradingBotDemoEA stopped. reason=%d", reason);
}
