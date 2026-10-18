//+------------------------------------------------------------------+
//| EA_Bridge.mq5 — Python file-bridge executor for Tradingbot       |
//| Reads JSON from Common\\Files, places/closes orders in MT5         |
//+------------------------------------------------------------------+
#property copyright "Tradingbot"
#property version   "1.20"
#property strict

input long   MagicNumber = 112233;
input string CmdFile     = "ea_commands.txt";   // Common\\Files
input string RspFile     = "ea_responses.txt";  // Common\\Files
input int    PollMs      = 300;

//+------------------------------------------------------------------+
int OnInit()
{
   EventSetMillisecondTimer(PollMs);
   Print("EA_Bridge v1.20 started | CMD=", CmdFile, " | Magic=", MagicNumber);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

datetime lastTickWrite = 0;
void OnTimer()
{
   ProcessCommands();
   datetime now = TimeCurrent();
   if(now - lastTickWrite >= 2)
   {
      lastTickWrite = now;
      WriteTicksFile();
   }
}

//+------------------------------------------------------------------+
void ProcessCommands()
{
   if(!FileIsExist(CmdFile, FILE_COMMON))
      return;

   int h = FileOpen(CmdFile, FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ|FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;

   string raw = "";
   while(!FileIsEnding(h))
      raw += FileReadString(h) + "\n";
   FileClose(h);
   FileDelete(CmdFile, FILE_COMMON);

   StringTrimLeft(raw);
   StringTrimRight(raw);
   if(StringLen(raw) < 5)
      return;

   string action = JStr(raw, "action");
   string symbol = JStr(raw, "symbol");
   double volume = JNum(raw, "volume");
   double sl     = JNum(raw, "sl");
   double tp     = JNum(raw, "tp");
   long   magic  = (long)JNum(raw, "magic");
   if(magic == 0)
      magic = MagicNumber;
   ulong  ticket = (ulong)JNum(raw, "ticket");

   StringToUpper(action);

   string result;
   if(action == "BUY")
      result = DoTrade(ORDER_TYPE_BUY, symbol, volume, sl, tp, magic);
   else if(action == "SELL")
      result = DoTrade(ORDER_TYPE_SELL, symbol, volume, sl, tp, magic);
   else if(action == "CLOSE_ALL")
      result = ClosePosition("", 0, magic);
   else if(action == "CLOSE" || action == "CLOSE_POSITION")
      result = ClosePosition(symbol, ticket, magic);
   else if(action == "MODIFY" || action == "SLTP")
      result = ModifyPosition(symbol, ticket, sl, tp, magic);
   else if(action == "STATUS")
      result = StatusJSON(magic);
   else if(action == "RATES")
      result = RatesJSON(symbol, (int)volume);
   else if(action == "TICK")
      result = TickJSON(symbol);
   else
      result = "{\"ok\":false,\"error\":\"unknown action\"}";

   int w = FileOpen(RspFile, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(w != INVALID_HANDLE)
   {
      FileWriteString(w, result);
      FileClose(w);
   }
}

//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING PickFilling(const string symbol)
{
   uint filling = (uint)SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC)
      return ORDER_FILLING_IOC;
   if((filling & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK)
      return ORDER_FILLING_FOK;
   return ORDER_FILLING_RETURN;
}

//+------------------------------------------------------------------+
string DoTrade(ENUM_ORDER_TYPE type, string symbol, double volume, double sl, double tp, long magic)
{
   if(symbol == "" || volume <= 0.0)
      return "{\"ok\":false,\"error\":\"bad symbol/volume\"}";

   if(!SymbolSelect(symbol, true))
      return "{\"ok\":false,\"error\":\"symbol not found\"}";

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      return "{\"ok\":false,\"error\":\"terminal trade disabled - click AutoTrading\"}";
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
      return "{\"ok\":false,\"error\":\"EA trade disabled - allow live trading on chart\"}";
   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
      return "{\"ok\":false,\"error\":\"account trade not allowed\"}";

   long tmode = SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE);
   if(tmode == SYMBOL_TRADE_MODE_DISABLED)
      return "{\"ok\":false,\"error\":\"symbol trade disabled\"}";

   double vmin  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double vmax  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double vstep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(vstep <= 0.0) vstep = 0.01;
   volume = MathFloor(volume / vstep + 1e-12) * vstep;
   if(volume < vmin) volume = vmin;
   if(volume > vmax) volume = vmax;
   volume = NormalizeDouble(volume, 2);

   MqlTick tick;
   if(!SymbolInfoTick(symbol, tick))
      return "{\"ok\":false,\"error\":\"no tick\"}";

   double price = (type == ORDER_TYPE_BUY) ? tick.ask : tick.bid;

   MqlTradeRequest req;
   MqlTradeResult  res;
   ZeroMemory(req);
   ZeroMemory(res);

   req.action       = TRADE_ACTION_DEAL;
   req.symbol       = symbol;
   req.volume       = volume;
   req.type         = type;
   req.price        = price;
   req.sl           = sl;
   req.tp           = tp;
   req.deviation    = 50;
   req.magic        = magic;
   req.comment      = "EA_Bridge";
   req.type_filling = PickFilling(symbol);

   bool ok = OrderSend(req, res);
   if(!ok || (res.retcode != TRADE_RETCODE_DONE && res.retcode != TRADE_RETCODE_DONE_PARTIAL))
   {
      ENUM_ORDER_TYPE_FILLING modes[3];
      modes[0] = ORDER_FILLING_RETURN;
      modes[1] = ORDER_FILLING_IOC;
      modes[2] = ORDER_FILLING_FOK;

      bool done = false;
      for(int i = 0; i < 3; i++)
      {
         if(modes[i] == req.type_filling)
            continue;
         ZeroMemory(res);
         req.type_filling = modes[i];
         if(OrderSend(req, res))
         {
            if(res.retcode == TRADE_RETCODE_DONE || res.retcode == TRADE_RETCODE_DONE_PARTIAL)
            {
               done = true;
               break;
            }
         }
      }
      if(!done)
         return StringFormat(
            "{\"ok\":false,\"error\":\"%s\",\"retcode\":%d,\"volume\":%.3f,\"fill\":%d}",
            res.comment, res.retcode, volume, (int)req.type_filling
         );
   }

   return StringFormat(
      "{\"ok\":true,\"ticket\":%I64d,\"price\":%.5f,\"volume\":%.3f,\"retcode\":%d}",
      res.order, res.price, volume, res.retcode
   );
}

//+------------------------------------------------------------------+
string ClosePosition(string symbol, ulong ticket_filter, long magic)
{
   int closed = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(!PositionSelectByTicket(ticket))
         continue;
      if(magic > 0 && PositionGetInteger(POSITION_MAGIC) != magic)
         continue;
      if(ticket_filter > 0 && ticket != ticket_filter)
         continue;

      string sym   = PositionGetString(POSITION_SYMBOL);
      if(symbol != "" && sym != symbol)
         continue;

      double vol   = PositionGetDouble(POSITION_VOLUME);
      long   ptype = PositionGetInteger(POSITION_TYPE);

      MqlTick t;
      if(!SymbolInfoTick(sym, t))
         continue;

      MqlTradeRequest req;
      MqlTradeResult  res;
      ZeroMemory(req);
      ZeroMemory(res);

      req.action       = TRADE_ACTION_DEAL;
      req.position     = ticket;
      req.symbol       = sym;
      req.volume       = vol;
      req.type         = (ptype == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
      req.price        = (req.type == ORDER_TYPE_BUY) ? t.ask : t.bid;
      req.deviation    = 50;
      req.magic        = magic;
      req.comment      = "EA_Bridge_close";
      req.type_filling = PickFilling(sym);

      if(!OrderSend(req, res) ||
         (res.retcode != TRADE_RETCODE_DONE && res.retcode != TRADE_RETCODE_DONE_PARTIAL))
      {
         ENUM_ORDER_TYPE_FILLING modes[3];
         modes[0] = ORDER_FILLING_RETURN;
         modes[1] = ORDER_FILLING_IOC;
         modes[2] = ORDER_FILLING_FOK;
         for(int m = 0; m < 3; m++)
         {
            if(modes[m] == req.type_filling)
               continue;
            ZeroMemory(res);
            req.type_filling = modes[m];
            if(OrderSend(req, res) &&
               (res.retcode == TRADE_RETCODE_DONE || res.retcode == TRADE_RETCODE_DONE_PARTIAL))
            {
               closed++;
               break;
            }
         }
      }
      else
         closed++;
   }

   return StringFormat("{\"ok\":true,\"closed\":%d}", closed);
}

string CloseAll(long magic)
{
   return ClosePosition("", 0, magic);
}

string ModifyPosition(string symbol, ulong ticket_filter, double sl, double tp, long magic)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(!PositionSelectByTicket(ticket))
         continue;
      if(magic > 0 && PositionGetInteger(POSITION_MAGIC) != magic)
         continue;
      if(ticket_filter > 0 && ticket != ticket_filter)
         continue;

      string sym = PositionGetString(POSITION_SYMBOL);
      if(symbol != "" && sym != symbol)
         continue;

      MqlTradeRequest req;
      MqlTradeResult  res;
      ZeroMemory(req);
      ZeroMemory(res);

      req.action   = TRADE_ACTION_SLTP;
      req.position = ticket;
      req.symbol   = sym;
      req.sl       = sl;
      req.tp       = tp;

      if(OrderSend(req, res) && (res.retcode == TRADE_RETCODE_DONE || res.retcode == TRADE_RETCODE_DONE_PARTIAL))
         return StringFormat("{\"ok\":true,\"modified\":true,\"ticket\":%I64d}", ticket);
      else
         return StringFormat("{\"ok\":false,\"error\":\"%s\",\"retcode\":%d}", res.comment, res.retcode);
   }
   return "{\"ok\":false,\"error\":\"position not found\"}";
}

//+------------------------------------------------------------------+
string StatusJSON(long magic)
{
   int    pos = 0;
   double vol = 0.0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(!PositionSelectByTicket(ticket))
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != magic)
         continue;
      pos++;
      vol += PositionGetDouble(POSITION_VOLUME);
   }

   return StringFormat(
      "{\"ok\":true,\"positions\":%d,\"volume\":%.2f,\"login\":%d}",
      pos, vol, (int)AccountInfoInteger(ACCOUNT_LOGIN)
   );
}

string RatesJSON(string symbol, int count)
{
   if(symbol == "") symbol = _Symbol;
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int n = (count > 0 && count <= 500) ? count : 60;
   int copied = CopyRates(symbol, PERIOD_M1, 0, n, rates);
   if(copied <= 0)
      return "{\"ok\":false,\"error\":\"CopyRates failed\"}";
   string res = "{\"ok\":true,\"symbol\":\"" + symbol + "\",\"rates\":[";
   for(int i = copied - 1; i >= 0; i--)
   {
      res += StringFormat("{\"time\":%I64d,\"open\":%.2f,\"high\":%.2f,\"low\":%.2f,\"close\":%.2f,\"volume\":%I64d}",
         (long)rates[i].time, rates[i].open, rates[i].high, rates[i].low, rates[i].close, rates[i].tick_volume);
      if(i > 0) res += ",";
   }
   res += "]}";
   return res;
}

string TickJSON(string symbol)
{
   if(symbol == "") symbol = _Symbol;
   MqlTick tick;
   if(!SymbolInfoTick(symbol, tick))
      return "{\"ok\":false,\"error\":\"SymbolInfoTick failed\"}";
   double spread = (tick.ask - tick.bid);
   double mark = (tick.last > 0) ? tick.last : ((tick.bid + tick.ask) / 2.0);
   return StringFormat("{\"ok\":true,\"symbol\":\"%s\",\"bid\":%.2f,\"ask\":%.2f,\"mark\":%.2f,\"last\":%.2f,\"spread\":%.4f,\"time\":%I64d}",
      symbol, tick.bid, tick.ask, mark, tick.last, spread, (long)tick.time);
}

void WriteTicksFile()
{
   string syms[3];
   syms[0] = _Symbol;
   syms[1] = "XAUUSD";
   syms[2] = "BTCUSD";

   string json = "{";
   int added = 0;
   for(int s = 0; s < 3; s++)
   {
      string sym = syms[s];
      MqlTick tick;
      if(SymbolInfoTick(sym, tick))
      {
         if(added > 0) json += ",";
         double mark = (tick.last > 0) ? tick.last : ((tick.bid + tick.ask) / 2.0);
         json += StringFormat("\"%s\":{\"bid\":%.2f,\"ask\":%.2f,\"mark\":%.2f,\"last\":%.2f,\"spread\":%.4f,\"time\":%I64d}",
            sym, tick.bid, tick.ask, mark, tick.last, (tick.ask - tick.bid), (long)tick.time);
         added++;
      }
   }
   json += "}";

   int h = FileOpen("ea_ticks.txt", FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h != INVALID_HANDLE)
   {
      FileWriteString(h, json);
      FileClose(h);
   }
}

//+------------------------------------------------------------------+
string JStr(const string j, const string key)
{
   string pat = "\"" + key + "\"";
   int p = StringFind(j, pat);
   if(p < 0)
      return "";
   p = StringFind(j, ":", p);
   if(p < 0)
      return "";
   p++;
   while(p < StringLen(j) &&
         (StringGetCharacter(j, p) == ' ' || StringGetCharacter(j, p) == '\t'))
      p++;
   if(p >= StringLen(j) || StringGetCharacter(j, p) != '"')
      return "";
   p++;
   int e = StringFind(j, "\"", p);
   if(e < 0)
      return "";
   return StringSubstr(j, p, e - p);
}

double JNum(const string j, const string key)
{
   string pat = "\"" + key + "\"";
   int p = StringFind(j, pat);
   if(p < 0)
      return 0.0;
   p = StringFind(j, ":", p);
   if(p < 0)
      return 0.0;
   p++;
   while(p < StringLen(j) &&
         (StringGetCharacter(j, p) == ' ' || StringGetCharacter(j, p) == '\t'))
      p++;
   string num = "";
   for(int i = p; i < StringLen(j); i++)
   {
      ushort c = StringGetCharacter(j, i);
      if((c >= '0' && c <= '9') || c == '.' || c == '-')
         num += ShortToString(c);
      else
         break;
   }
   return StringToDouble(num);
}
//+------------------------------------------------------------------+
