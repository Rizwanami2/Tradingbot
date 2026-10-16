//+------------------------------------------------------------------+
//| TradingBotHybridEA.mq5                                           |
//| Consumes short-lived Python signals through MT5 Common\\Files.   |
//| Safety: disabled by default; demo accounts only; no live orders. |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "Python signal relay for MT5 demo validation only"

#include <Trade/Trade.mqh>

input bool   InpEnableDemoExecution = false; // Must be explicitly enabled on a DEMO account
input long   InpMagicNumber = 112233;
input int    InpTimerSeconds = 1;
input string InpSignalFile = "TradingBot\\signal.csv";

CTrade g_trade;
bool g_warned_disabled = false;
bool g_warned_account = false;

string Trimmed(string value)
{
   StringTrimLeft(value);
   StringTrimRight(value);
   return value;
}

bool IsPositiveFinite(const double value)
{
   return MathIsValidNumber(value) && value > 0.0;
}

ulong SignalHash(const string signal_id)
{
   // FNV-1a 32-bit hash, stored as a terminal global variable for deduplication.
   ulong hash = 2166136261;
   for(int i = 0; i < StringLen(signal_id); i++)
   {
      hash = (hash ^ (ulong)StringGetCharacter(signal_id, i)) * 16777619;
      hash = hash & 0xFFFFFFFF;
   }
   return hash;
}

bool AlreadyProcessed(const string signal_id)
{
   string key = "TBH_SIG_" + (string)SignalHash(signal_id);
   return GlobalVariableCheck(key);
}

void MarkProcessed(const string signal_id)
{
   string key = "TBH_SIG_" + (string)SignalHash(signal_id);
   GlobalVariableSet(key, (double)TimeLocal());
}

void DiscardSignal(const string reason)
{
   Print("TradingBotHybridEA: rejecting signal: ", reason);
   FileDelete(InpSignalFile, FILE_COMMON);
}

int OnInit()
{
   if(InpTimerSeconds < 1 || InpTimerSeconds > 60)
   {
      Print("TradingBotHybridEA: InpTimerSeconds must be between 1 and 60.");
      return INIT_PARAMETERS_INCORRECT;
   }
   g_trade.SetExpertMagicNumber((ulong)InpMagicNumber);
   g_trade.SetAsyncMode(false);
   EventSetTimer(InpTimerSeconds);
   Print("TradingBotHybridEA initialized. Execution disabled by default; demo accounts only.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTick()
{
   // Polling is timer-based so a quiet symbol still checks for new files.
}

void OnTimer()
{
   if(!InpEnableDemoExecution)
   {
      if(!g_warned_disabled)
      {
         Print("TradingBotHybridEA: execution is disabled. Set InpEnableDemoExecution=true only for demo validation.");
         g_warned_disabled = true;
      }
      return;
   }

   // Strategy Tester cannot meaningfully replay a short-lived wall-clock signal file.
   // Historical strategy validation belongs in the Python backtest engine.
   if((bool)MQLInfoInteger(MQL_TESTER))
   {
      if(!g_warned_account)
      {
         Print("TradingBotHybridEA: external wall-clock signal files are disabled in Strategy Tester.");
         g_warned_account = true;
      }
      return;
   }

   long trade_mode = AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(trade_mode != ACCOUNT_TRADE_MODE_DEMO)
   {
      if(!g_warned_account)
      {
         Print("TradingBotHybridEA: blocked; only an MT5 DEMO account is allowed.");
         g_warned_account = true;
      }
      return;
   }

   if(!FileIsExist(InpSignalFile, FILE_COMMON))
      return;

   int handle = FileOpen(InpSignalFile,
                         FILE_READ | FILE_CSV | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE,
                         '|');
   if(handle == INVALID_HANDLE)
      return; // Writer may still be atomically publishing the file.

   string schema = FileReadString(handle);
   string signal_id = FileReadString(handle);
   string symbol = FileReadString(handle);
   string side = FileReadString(handle);
   double volume = StringToDouble(FileReadString(handle));
   double entry = StringToDouble(FileReadString(handle));
   double stop_loss = StringToDouble(FileReadString(handle));
   double take_profit = StringToDouble(FileReadString(handle));
   long created_at = (long)StringToInteger(FileReadString(handle));
   long expires_at = (long)StringToInteger(FileReadString(handle));
   FileClose(handle);

   schema = Trimmed(schema);
   signal_id = Trimmed(signal_id);
   symbol = Trimmed(symbol);
   StringToUpper(symbol);
   side = Trimmed(side);
   StringToUpper(side);

   if(schema != "1" || signal_id == "" || symbol == "")
   {
      DiscardSignal("malformed or unsupported signal row");
      return;
   }
   if(AlreadyProcessed(signal_id))
   {
      DiscardSignal("duplicate signal id");
      return;
   }
   if(created_at <= 0 || expires_at <= created_at || expires_at - created_at > 300 ||
      TimeGMT() > expires_at || TimeGMT() - created_at > 300 || created_at > TimeGMT() + 30)
   {
      DiscardSignal("expired or invalid timestamps");
      return;
   }
   if(!IsPositiveFinite(volume) || !IsPositiveFinite(entry) ||
      !IsPositiveFinite(stop_loss) || !IsPositiveFinite(take_profit))
   {
      DiscardSignal("non-positive or invalid numeric field");
      return;
   }
   if(side != "BUY" && side != "SELL")
   {
      DiscardSignal("side must be BUY or SELL");
      return;
   }
   if((side == "BUY" && !(stop_loss < entry && entry < take_profit)) ||
      (side == "SELL" && !(take_profit < entry && entry < stop_loss)))
   {
      DiscardSignal("stop/entry/target relationship is invalid");
      return;
   }
   if(!SymbolSelect(symbol, true))
   {
      DiscardSignal("symbol is not available in this MT5 terminal");
      return;
   }

   double min_volume = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double max_volume = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double volume_step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(min_volume <= 0.0 || max_volume < min_volume || volume_step <= 0.0 ||
      volume < min_volume - 1e-8 || volume > max_volume + 1e-8)
   {
      DiscardSignal("volume is outside symbol limits");
      return;
   }
   double step_units = volume / volume_step;
   if(MathAbs(step_units - MathRound(step_units)) > 1e-6)
   {
      DiscardSignal("volume is not aligned to symbol volume step");
      return;
   }

   MqlTick tick;
   if(!SymbolInfoTick(symbol, tick))
   {
      Print("TradingBotHybridEA: quote unavailable; signal retained for a later poll.");
      return;
   }
   if((side == "BUY" && !(stop_loss < tick.ask && tick.ask < take_profit)) ||
      (side == "SELL" && !(take_profit < tick.bid && tick.bid < stop_loss)))
   {
      DiscardSignal("stop/target no longer bracket the current quote");
      return;
   }

   // Mark before sending: if the terminal loses the response, do not blindly retry.
   // Any non-success result must be reconciled manually before a new signal is issued.
   MarkProcessed(signal_id);
   bool sent = false;
   if(side == "BUY")
      sent = g_trade.Buy(volume, symbol, 0.0, stop_loss, take_profit, "TradingBot " + signal_id);
   else
      sent = g_trade.Sell(volume, symbol, 0.0, stop_loss, take_profit, "TradingBot " + signal_id);

   uint retcode = g_trade.ResultRetcode();
   bool accepted = sent && (retcode == TRADE_RETCODE_DONE ||
                            retcode == TRADE_RETCODE_PLACED ||
                            retcode == TRADE_RETCODE_DONE_PARTIAL);
   FileDelete(InpSignalFile, FILE_COMMON);

   if(!accepted)
   {
      Print("TradingBotHybridEA: order result uncertain/failed; do not retry automatically. Signal=",
            signal_id, " retcode=", retcode, " detail=", g_trade.ResultRetcodeDescription());
      return;
   }

   Print("TradingBotHybridEA: demo order accepted. Signal=", signal_id,
         " symbol=", symbol, " side=", side, " volume=", DoubleToString(volume, 2),
         " retcode=", retcode);
}
