// mt5bot chart view: research decisions, stop-loss / take-profit levels and tick verdicts drawn from Python output.
#property copyright   "Copyright (c) 2026 Tito Oscar Mwaisengela"
#property link        "https://github.com/tito-devsec/mt5-sclapingbot"
#property version     "1.00"
#property description "Shows mt5bot research decisions, stop-loss and take-profit levels and which level ticks hit first."
#property description "Research display only: this indicator never places, modifies or closes orders."
#property indicator_chart_window
#property indicator_plots   0
#property indicator_buffers 0

#include <MT5Bot/Bridge.mqh>

input int    InpRefreshSeconds = 5;              // Refresh interval (seconds)
input int    InpMaxDrawn       = 150;            // Most recent decisions to draw
input bool   InpShowNoTrade    = false;          // Also mark NO_TRADE / UNCERTAIN bars
input bool   InpShowPanel      = true;           // Show the status panel
input color  InpLongColor      = clrDodgerBlue;  // Long recommendation colour
input color  InpShortColor     = clrOrangeRed;   // Short recommendation colour
input color  InpStopColor      = clrRed;         // Stop-loss line colour
input color  InpTargetColor    = clrLimeGreen;   // Take-profit line colour
input color  InpRejectColor    = clrDarkGray;    // Risk-rejected decision colour

#define PFX "MT5Bot_"

datetime g_dec_mod = 0;
datetime g_res_mod = 0;
datetime g_status_mod = 0;
string   g_last = "no decision yet";
int      g_counts[4];
int      g_tp_first = 0;
int      g_sl_first = 0;

string DecisionFile() { return("decisions_" + _Symbol + "_" + MB_TfName((ENUM_TIMEFRAMES)Period()) + ".csv"); }
string ResolvedFile() { return("resolved_" + _Symbol + ".csv"); }

int OnInit()
  {
   if(MB_TfName((ENUM_TIMEFRAMES)Period()) == "")
      Print("MT5Bot_Decisions: mt5bot exports M1, M5, M15 and H1 only; switch the chart timeframe to see decisions.");
   EventSetTimer(MathMax(1, InpRefreshSeconds));
   Refresh(true);
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   ObjectsDeleteAll(0, PFX);
   Comment("");
  }

void OnTimer()
  {
   Refresh(false);
  }

int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[], const double &open[],
                const double &high[], const double &low[], const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
  {
   return(rates_total);
  }

void Refresh(const bool force)
  {
   datetime dm = MB_FileModified(DecisionFile());
   datetime rm = MB_FileModified(ResolvedFile());
   datetime sm = MB_FileModified("status.txt");
   if(force || dm != g_dec_mod)
     {
      g_dec_mod = dm;
      DrawDecisions();
     }
   if(force || rm != g_res_mod)
     {
      g_res_mod = rm;
      DrawResolutions();
     }
   g_status_mod = sm;
   if(InpShowPanel)
      DrawPanel();
   ChartRedraw(0);
  }

void DrawDecisions()
  {
   ObjectsDeleteAll(0, PFX + "D");
   ArrayInitialize(g_counts, 0);
   string lines[];
   int n = MB_ReadLines(DecisionFile(), lines);
   if(n < 0)
     {
      g_last = "no decision file yet (" + DecisionFile() + ")";
      return;
     }
   int first = MathMax(1, n - InpMaxDrawn);
   for(int i = first; i < n; i++)
     {
      string f[];
      if(StringSplit(lines[i], ',', f) < 14)
         continue;
      long     id       = StringToInteger(f[0]);
      datetime bar      = (datetime)StringToInteger(f[1]);
      string   status   = f[3];
      string   regime   = f[4];
      string   strategy = f[5];
      string   dir      = f[6];
      double   entry    = StringToDouble(f[7]);
      bool     levels   = (StringLen(f[8]) > 0 && StringLen(f[9]) > 0);
      double   sl       = StringToDouble(f[8]);
      double   tp       = StringToDouble(f[9]);
      int      hold     = (int)StringToInteger(f[10]);
      int      secs     = (int)StringToInteger(f[11]) * 60;
      string   rej      = f[12];
      string   why      = f[13];
      string   base     = PFX + "D" + IntegerToString(id) + "_";
      bool     is_long  = (dir == "LONG");
      string   tip      = status + " " + strategy + " " + dir + " | regime " + regime + " | " + why;

      if(status == "RECOMMEND")
        {
         g_counts[0]++;
         MB_Arrow(base + "A", is_long ? OBJ_ARROW_BUY : OBJ_ARROW_SELL, bar, entry,
                  is_long ? InpLongColor : InpShortColor, tip, 0);
        }
      else
         if(status == "REJECTED")
           {
            g_counts[1]++;
            MB_Arrow(base + "A", OBJ_ARROW, bar, entry, InpRejectColor, "REJECTED " + rej + " | " + why, 251);
           }
         else
           {
            if(status == "NO_TRADE")
               g_counts[2]++;
            else
               g_counts[3]++;
            if(InpShowNoTrade)
               MB_Arrow(base + "A", OBJ_ARROW, bar, entry, InpRejectColor, tip, 159);
           }

      if(levels && hold > 0 && secs > 0 && (status == "RECOMMEND" || status == "REJECTED"))
        {
         datetime t1 = bar + secs;
         datetime t2 = t1 + hold * secs;
         ENUM_LINE_STYLE style = (status == "RECOMMEND") ? STYLE_DASH : STYLE_DOT;
         MB_Segment(base + "SL", t1, t2, sl, InpStopColor, style, 1, "stop-loss " + DoubleToString(sl, _Digits));
         MB_Segment(base + "TP", t1, t2, tp, InpTargetColor, style, 1, "take-profit " + DoubleToString(tp, _Digits));
         MB_Segment(base + "E", t1, t2, entry, is_long ? InpLongColor : InpShortColor, STYLE_DOT, 1,
                    "reference entry " + DoubleToString(entry, _Digits));
         if(status == "RECOMMEND")
           {
            MB_Text(base + "SLT", t2, sl, " SL " + DoubleToString(sl, _Digits), InpStopColor);
            MB_Text(base + "TPT", t2, tp, " TP " + DoubleToString(tp, _Digits), InpTargetColor);
           }
        }
      g_last = status + " " + strategy + " " + dir + " at " + TimeToString(bar, TIME_DATE | TIME_MINUTES) +
               " (" + regime + ")";
      if(status == "REJECTED")
         g_last = g_last + " - " + rej;
     }
  }

void DrawResolutions()
  {
   ObjectsDeleteAll(0, PFX + "R");
   g_tp_first = 0;
   g_sl_first = 0;
   string lines[];
   int n = MB_ReadLines(ResolvedFile(), lines);
   for(int i = 1; i < n; i++)
     {
      string f[];
      if(StringSplit(lines[i], ',', f) < 7)
         continue;
      string hit = f[3];
      long   msc = StringToInteger(f[4]);
      if((hit != "TP" && hit != "SL") || msc <= 0)
         continue;
      datetime t = (datetime)(msc / 1000);
      double   px = StringToDouble(f[5]);
      bool     tp = (hit == "TP");
      if(tp)
         g_tp_first++;
      else
         g_sl_first++;
      MB_Arrow(PFX + "R" + f[0], tp ? OBJ_ARROW_CHECK : OBJ_ARROW_STOP, t, px, tp ? InpTargetColor : InpStopColor,
               (tp ? "take-profit" : "stop-loss") + " touched first by ticks - decision " + f[0] + " (" + f[6] + " ticks)",
               0);
     }
  }

void DrawPanel()
  {
   string st[];
   MB_ReadLines("status.txt", st);
   long updated = StringToInteger(MB_Value(st, "updated_server", "0"));
   string age = (updated > 0) ? IntegerToString((long)(TimeCurrent() - (datetime)updated)) + " s ago" : "never";
   string stale = (updated > 0 && TimeCurrent() - (datetime)updated > 300) ? "  [STALE - is the Python monitor running?]" : "";
   Comment("mt5bot research view (no trading)  " + _Symbol + " " + MB_TfName((ENUM_TIMEFRAMES)Period()) + "\n" +
           "updated: " + age + stale + "   mode: " + MB_Value(st, "mode", "?") + "\n" +
           "champion: " + MB_Value(st, "champion", "?") + "   news: " + MB_Value(st, "news", "?") +
           "   open alerts: " + MB_Value(st, "open_alerts", "?") + "\n" +
           "drawn: " + IntegerToString(g_counts[0]) + " recommend, " + IntegerToString(g_counts[1]) + " rejected, " +
           IntegerToString(g_counts[2]) + " no-trade, " + IntegerToString(g_counts[3]) + " uncertain\n" +
           "ticks hit first: TP " + IntegerToString(g_tp_first) + " / SL " + IntegerToString(g_sl_first) + "\n" +
           "last: " + g_last + "\n" +
           "execution: " + MB_Value(st, "execution", "DISABLED"));
  }
