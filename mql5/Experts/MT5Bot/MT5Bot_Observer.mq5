// mt5bot observer: demo-only, observe-only Expert Advisor that records ticks and resolves stop/target first-touch.
#property copyright   "Copyright (c) 2026 Tito Oscar Mwaisengela"
#property link        "https://github.com/tito-devsec/mt5-sclapingbot"
#property version     "1.00"
#property description "Observe-only: records every tick and decides tick by tick whether stop-loss or take-profit is hit first."
#property description "Runs on DEMO accounts only and never places, modifies, closes or cancels orders."

#include <MT5Bot/Bridge.mqh>

input bool InpRecordTicks           = true;  // Record every tick to Common\Files\mt5bot
input bool InpWatchLevels           = true;  // Resolve stop-loss / take-profit first touch for watched decisions
input bool InpDrawLevels            = true;  // Draw fill-anchored stop-loss / take-profit lines
input int  InpReloadSeconds         = 5;     // Reload watch list and write heartbeat (seconds)
input int  InpLateToleranceSeconds  = 60;    // Started later than this after an entry: mark LATE, not resolved

#define EAPFX      "MT5BotEA_"
#define EA_VERSION "1.00"
#define RESOLVED_HEADER "id,entry_msc,entry_price,first_hit,hit_msc,hit_price,ticks"

struct WatchItem
  {
   long              id;
   int               dir;
   datetime          start;
   double            stop_dist;
   double            target_dist;
   datetime          expiry;
   double            slip_pts;
   bool              entered;
   bool              done;
   double            entry;
   double            sl;
   double            tp;
   long              entry_msc;
   int               ticks;
  };

WatchItem g_items[];
long      g_done_ids[];
int       g_tick_handle    = INVALID_HANDLE;
string    g_tick_day       = "";
datetime  g_watch_mod      = 0;
long      g_last_msc       = 0;
long      g_started_msc    = 0;
long      g_ticks_recorded = 0;
int       g_resolved       = 0;

int OnInit()
  {
   if((ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE) != ACCOUNT_TRADE_MODE_DEMO)
     {
      Alert("MT5Bot Observer runs on DEMO accounts only and has been stopped.");
      return(INIT_FAILED);
     }
   FolderCreate("mt5bot", FILE_COMMON);
   g_started_msc = (long)TimeCurrent() * 1000;
   LoadDoneIds();
   LoadWatch(true);
   EventSetTimer(MathMax(1, InpReloadSeconds));
   WriteHeartbeat();
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   if(g_tick_handle != INVALID_HANDLE)
     {
      FileClose(g_tick_handle);
      g_tick_handle = INVALID_HANDLE;
     }
   ObjectsDeleteAll(0, EAPFX);
   Comment("");
  }

void OnTimer()
  {
   LoadWatch(false);
   ExpireItems();
   if(g_tick_handle != INVALID_HANDLE)
      FileFlush(g_tick_handle);
   WriteHeartbeat();
   Comment("MT5Bot Observer (observe-only, demo)  " + _Symbol + "\n" +
           "watching: " + IntegerToString(ActiveCount()) + "   resolved: " + IntegerToString(g_resolved) +
           "   ticks recorded: " + IntegerToString(g_ticks_recorded));
  }

void OnTick()
  {
   MqlTick last;
   if(!SymbolInfoTick(_Symbol, last))
      return;
   if(g_last_msc == 0 || last.time_msc <= g_last_msc)
     {
      if(last.time_msc > g_last_msc)
        {
         ProcessTick(last);
         g_last_msc = last.time_msc;
        }
      return;
     }
   // CopyTicksRange catches every tick since the previous event, so none is skipped between OnTick calls.
   MqlTick batch[];
   int n = CopyTicksRange(_Symbol, batch, COPY_TICKS_INFO, (ulong)(g_last_msc + 1), (ulong)last.time_msc);
   if(n <= 0)
     {
      ProcessTick(last);
      g_last_msc = last.time_msc;
      return;
     }
   for(int i = 0; i < n; i++)
      ProcessTick(batch[i]);
   g_last_msc = batch[n - 1].time_msc;
  }

void ProcessTick(const MqlTick &t)
  {
   if(InpRecordTicks)
      RecordTick(t);
   if(InpWatchLevels)
      CheckLevels(t);
  }

void RecordTick(const MqlTick &t)
  {
   string day = TimeToString(t.time, TIME_DATE);
   StringReplace(day, ".", "");
   if(day != g_tick_day || g_tick_handle == INVALID_HANDLE)
     {
      if(g_tick_handle != INVALID_HANDLE)
         FileClose(g_tick_handle);
      string name = MT5BOT_DIR + "ticks_" + _Symbol + "_" + day + ".csv";
      g_tick_handle = FileOpen(name, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
      if(g_tick_handle == INVALID_HANDLE)
         return;
      if(FileSize(g_tick_handle) == 0)
         FileWriteString(g_tick_handle, "time_msc,bid,ask\r\n");
      FileSeek(g_tick_handle, 0, SEEK_END);
      g_tick_day = day;
     }
   FileWriteString(g_tick_handle, IntegerToString(t.time_msc) + "," + DoubleToString(t.bid, _Digits) + "," +
                   DoubleToString(t.ask, _Digits) + "\r\n");
   g_ticks_recorded++;
  }

bool IsKnown(const long id)
  {
   for(int i = 0; i < ArraySize(g_items); i++)
      if(g_items[i].id == id)
         return(true);
   for(int j = 0; j < ArraySize(g_done_ids); j++)
      if(g_done_ids[j] == id)
         return(true);
   return(false);
  }

void LoadDoneIds()
  {
   string lines[];
   int n = MB_ReadLines("resolved_" + _Symbol + ".csv", lines);
   for(int i = 1; i < n; i++)
     {
      string f[];
      if(StringSplit(lines[i], ',', f) < 7)
         continue;
      int k = ArraySize(g_done_ids);
      ArrayResize(g_done_ids, k + 1);
      g_done_ids[k] = StringToInteger(f[0]);
     }
  }

void LoadWatch(const bool force)
  {
   string name = "watch_" + _Symbol + ".csv";
   datetime mod = MB_FileModified(name);
   if(!force && mod == g_watch_mod)
      return;
   g_watch_mod = mod;
   string lines[];
   int n = MB_ReadLines(name, lines);
   for(int i = 1; i < n; i++)
     {
      string f[];
      if(StringSplit(lines[i], ',', f) < 7)
         continue;
      long id = StringToInteger(f[0]);
      datetime expiry = (datetime)StringToInteger(f[5]);
      if(id <= 0 || IsKnown(id) || expiry <= TimeCurrent())
         continue;
      int k = ArraySize(g_items);
      ArrayResize(g_items, k + 1);
      g_items[k].id          = id;
      g_items[k].dir         = (f[1] == "LONG") ? 1 : -1;
      g_items[k].start       = (datetime)StringToInteger(f[2]);
      g_items[k].stop_dist   = StringToDouble(f[3]);
      g_items[k].target_dist = StringToDouble(f[4]);
      g_items[k].expiry      = expiry;
      g_items[k].slip_pts    = StringToDouble(f[6]);
      g_items[k].entered     = false;
      g_items[k].done        = false;
      g_items[k].entry       = 0.0;
      g_items[k].sl          = 0.0;
      g_items[k].tp          = 0.0;
      g_items[k].entry_msc   = 0;
      g_items[k].ticks       = 0;
     }
  }

int ActiveCount()
  {
   int c = 0;
   for(int i = 0; i < ArraySize(g_items); i++)
      if(!g_items[i].done)
         c++;
   return(c);
  }

void CheckLevels(const MqlTick &t)
  {
   for(int i = 0; i < ArraySize(g_items); i++)
     {
      if(g_items[i].done)
         continue;
      long start_msc = (long)g_items[i].start * 1000;
      if(!g_items[i].entered)
        {
         if(t.time_msc < start_msc)
            continue;
         if(g_started_msc > start_msc + (long)InpLateToleranceSeconds * 1000)
           {
            Resolve(i, "LATE", 0, 0.0);
            continue;
           }
         double slip = g_items[i].slip_pts * _Point;
         g_items[i].entry = (g_items[i].dir > 0) ? t.ask + slip : t.bid - slip;
         g_items[i].sl = g_items[i].entry - g_items[i].dir * g_items[i].stop_dist;
         g_items[i].tp = g_items[i].entry + g_items[i].dir * g_items[i].target_dist;
         g_items[i].entry_msc = t.time_msc;
         g_items[i].entered = true;
         if(InpDrawLevels)
            DrawItem(i);
         continue;
        }
      if(t.time >= g_items[i].expiry)
        {
         Resolve(i, "NONE", t.time_msc, 0.0);
         continue;
        }
      g_items[i].ticks++;
      double px = (g_items[i].dir > 0) ? t.bid : t.ask;
      bool hit_sl = (g_items[i].dir > 0) ? (px <= g_items[i].sl) : (px >= g_items[i].sl);
      bool hit_tp = (g_items[i].dir > 0) ? (px >= g_items[i].tp) : (px <= g_items[i].tp);
      if(hit_sl)
         Resolve(i, "SL", t.time_msc, px);
      else
         if(hit_tp)
            Resolve(i, "TP", t.time_msc, px);
     }
  }

void ExpireItems()
  {
   datetime now = TimeCurrent();
   for(int i = 0; i < ArraySize(g_items); i++)
      if(!g_items[i].done && now >= g_items[i].expiry)
         Resolve(i, g_items[i].entered ? "NONE" : "LATE", (long)now * 1000, 0.0);
  }

void Resolve(const int i, const string hit, const long msc, const double px)
  {
   g_items[i].done = true;
   g_resolved++;
   int k = ArraySize(g_done_ids);
   ArrayResize(g_done_ids, k + 1);
   g_done_ids[k] = g_items[i].id;
   string line = IntegerToString(g_items[i].id) + "," + IntegerToString(g_items[i].entry_msc) + "," +
                 DoubleToString(g_items[i].entry, _Digits) + "," + hit + "," + IntegerToString(msc) + "," +
                 DoubleToString(px, _Digits) + "," + IntegerToString(g_items[i].ticks);
   MB_AppendLine("resolved_" + _Symbol + ".csv", RESOLVED_HEADER, line);
   if(InpDrawLevels && (hit == "TP" || hit == "SL"))
     {
      bool tp = (hit == "TP");
      MB_Arrow(EAPFX + IntegerToString(g_items[i].id) + "_HIT", tp ? OBJ_ARROW_CHECK : OBJ_ARROW_STOP,
               (datetime)(msc / 1000), px, tp ? clrLimeGreen : clrRed,
               (tp ? "take-profit" : "stop-loss") + " touched first (live ticks), decision " +
               IntegerToString(g_items[i].id), 0);
      ChartRedraw(0);
     }
  }

void DrawItem(const int i)
  {
   string base = EAPFX + IntegerToString(g_items[i].id) + "_";
   datetime t1 = (datetime)(g_items[i].entry_msc / 1000);
   string id = IntegerToString(g_items[i].id);
   MB_Segment(base + "SL", t1, g_items[i].expiry, g_items[i].sl, clrRed, STYLE_SOLID, 2,
              "live stop-loss " + DoubleToString(g_items[i].sl, _Digits) + " (decision " + id + ")");
   MB_Segment(base + "TP", t1, g_items[i].expiry, g_items[i].tp, clrLimeGreen, STYLE_SOLID, 2,
              "live take-profit " + DoubleToString(g_items[i].tp, _Digits) + " (decision " + id + ")");
   MB_Segment(base + "E", t1, g_items[i].expiry, g_items[i].entry, clrDodgerBlue, STYLE_DOT, 1,
              "simulated fill " + DoubleToString(g_items[i].entry, _Digits) + " (decision " + id + ")");
   ChartRedraw(0);
  }

void WriteHeartbeat()
  {
   int h = FileOpen(MT5BOT_DIR + "heartbeat_" + _Symbol + ".txt", FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;
   FileWriteString(h, "server_time=" + IntegerToString((long)TimeCurrent()) + "\r\n");
   FileWriteString(h, "trade_mode=DEMO\r\n");
   FileWriteString(h, "symbol=" + _Symbol + "\r\n");
   FileWriteString(h, "spread_points=" + IntegerToString(SymbolInfoInteger(_Symbol, SYMBOL_SPREAD)) + "\r\n");
   FileWriteString(h, "watching=" + IntegerToString(ActiveCount()) + "\r\n");
   FileWriteString(h, "resolved=" + IntegerToString(g_resolved) + "\r\n");
   FileWriteString(h, "ticks_recorded=" + IntegerToString(g_ticks_recorded) + "\r\n");
   FileWriteString(h, "version=" + EA_VERSION + "\r\n");
   FileClose(h);
  }
