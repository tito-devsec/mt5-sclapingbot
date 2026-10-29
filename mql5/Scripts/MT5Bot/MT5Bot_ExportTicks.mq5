// mt5bot tick export: writes the chart symbol's historical ticks for a period to Common\Files\mt5bot (no trading).
#property copyright   "Copyright (c) 2026 Tito Oscar Mwaisengela"
#property link        "https://github.com/tito-devsec/mt5-sclapingbot"
#property version     "1.00"
#property description "Exports historical ticks so mt5bot backtests can settle bars that touched both stop-loss and take-profit."
#property script_show_inputs

#include <MT5Bot/Bridge.mqh>

input datetime InpFrom = D'2024.01.01 00:00';  // From (server time)
input datetime InpTo   = D'2024.01.08 00:00';  // To (server time)

void OnStart()
  {
   if(InpTo <= InpFrom)
     {
      Alert("MT5Bot_ExportTicks: 'To' must be after 'From'.");
      return;
     }
   FolderCreate("mt5bot", FILE_COMMON);
   long total = 0;
   int files = 0;
   for(datetime day = InpFrom; day < InpTo && !IsStopped(); day += 86400)
     {
      datetime day_end = (datetime)MathMin((double)(day + 86400), (double)InpTo);
      MqlTick ticks[];
      int n = CopyTicksRange(_Symbol, ticks, COPY_TICKS_INFO, (ulong)day * 1000, (ulong)day_end * 1000 - 1);
      if(n <= 0)
         continue;
      string stamp = TimeToString(day, TIME_DATE);
      StringReplace(stamp, ".", "");
      int h = FileOpen(MT5BOT_DIR + "ticks_" + _Symbol + "_hist_" + stamp + ".csv",
                       FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
      if(h == INVALID_HANDLE)
         continue;
      FileWriteString(h, "time_msc,bid,ask\r\n");
      for(int i = 0; i < n; i++)
         FileWriteString(h, IntegerToString(ticks[i].time_msc) + "," + DoubleToString(ticks[i].bid, _Digits) + "," +
                         DoubleToString(ticks[i].ask, _Digits) + "\r\n");
      FileClose(h);
      total += n;
      files++;
     }
   Alert("MT5Bot_ExportTicks: " + IntegerToString(total) + " ticks in " + IntegerToString(files) +
         " file(s). Run 'mt5bot bridge import', then 'mt5bot backtest ... --tick-resolve'.");
  }
