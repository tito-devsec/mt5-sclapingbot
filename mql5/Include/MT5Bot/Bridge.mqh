// mt5bot bridge helpers: read and write exchange files in Common\Files\mt5bot and draw chart objects (no trading).
#ifndef MT5BOT_BRIDGE_MQH
#define MT5BOT_BRIDGE_MQH

#define MT5BOT_DIR "mt5bot\\"
#define MT5BOT_FLAGS_READ (FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE)

// Read every line of a bridge file; returns the number of lines or -1 if the file cannot be opened.
int MB_ReadLines(const string name, string &lines[])
  {
   ArrayResize(lines, 0);
   ResetLastError();
   int h = FileOpen(MT5BOT_DIR + name, MT5BOT_FLAGS_READ);
   if(h == INVALID_HANDLE)
      return(-1);
   int n = 0;
   while(!FileIsEnding(h))
     {
      string s = FileReadString(h);
      if(StringLen(s) == 0 && FileIsEnding(h))
         break;
      ArrayResize(lines, n + 1);
      lines[n] = s;
      n++;
     }
   FileClose(h);
   return(n);
  }

// Last modification time of a bridge file, or 0 when it does not exist.
datetime MB_FileModified(const string name)
  {
   if(!FileIsExist(MT5BOT_DIR + name, FILE_COMMON))
      return(0);
   int h = FileOpen(MT5BOT_DIR + name, MT5BOT_FLAGS_READ);
   if(h == INVALID_HANDLE)
      return(0);
   datetime t = (datetime)FileGetInteger(h, FILE_MODIFY_DATE);
   FileClose(h);
   return(t);
  }

// Append one line, writing the header first when the file is new.
bool MB_AppendLine(const string name, const string header, const string line)
  {
   int h = FileOpen(MT5BOT_DIR + name, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
   if(h == INVALID_HANDLE)
      return(false);
   if(FileSize(h) == 0)
      FileWriteString(h, header + "\r\n");
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, line + "\r\n");
   FileClose(h);
   return(true);
  }

// Value of key=value in a set of lines, or the fallback.
string MB_Value(const string &lines[], const string key, const string fallback)
  {
   string prefix = key + "=";
   for(int i = 0; i < ArraySize(lines); i++)
      if(StringFind(lines[i], prefix) == 0)
         return(StringSubstr(lines[i], StringLen(prefix)));
   return(fallback);
  }

// Name used by Python for the supported timeframes; empty for other timeframes.
string MB_TfName(const ENUM_TIMEFRAMES tf)
  {
   switch(tf)
     {
      case PERIOD_M1:
         return("M1");
      case PERIOD_M5:
         return("M5");
      case PERIOD_M15:
         return("M15");
      case PERIOD_H1:
         return("H1");
      default:
         return("");
     }
  }

// Horizontal segment between two times at one price.
void MB_Segment(const string name, const datetime t1, const datetime t2, const double price, const color clr,
                const ENUM_LINE_STYLE style, const int width, const string tip)
  {
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_TREND, 0, t1, price, t2, price);
   else
     {
      ObjectMove(0, name, 0, t1, price);
      ObjectMove(0, name, 1, t2, price);
     }
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, tip);
  }

// Arrow-type object (buy/sell/check/stop arrows or a Wingdings code for OBJ_ARROW).
void MB_Arrow(const string name, const ENUM_OBJECT kind, const datetime t, const double price, const color clr,
              const string tip, const int code)
  {
   ObjectDelete(0, name);
   if(!ObjectCreate(0, name, kind, 0, t, price))
      return;
   if(kind == OBJ_ARROW)
      ObjectSetInteger(0, name, OBJPROP_ARROWCODE, code);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, tip);
  }

// Small text label anchored to a time and price.
void MB_Text(const string name, const datetime t, const double price, const string text, const color clr)
  {
   ObjectDelete(0, name);
   if(!ObjectCreate(0, name, OBJ_TEXT, 0, t, price))
      return;
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

#endif
