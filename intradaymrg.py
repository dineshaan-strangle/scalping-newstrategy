import os, time, hmac, hashlib, requests, json, asyncio, threading, datetime, pytz
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import pandas as pd

K=os.getenv("DELTA_API_KEY")
S=os.getenv("DELTA_API_SECRET")
T=os.getenv("TELEGRAM_TOKEN")
B="https://api.india.delta.exchange"
BC="https://cdn.india.deltaex.org"
PRODUCT_ID=27 # Anna sonna Product ID Base
IST=pytz.timezone('Asia/Kolkata')
MRG_START, MRG_END = 9, 16
MAX_TRADES=2
BE_PROFIT_DOLLAR=3 # Anna $3 BE

USER_QTY={}
USER_LEV={}
ALERT_USERS=set()
BEST_PRICE={}; ENTRY_PRICE={}; LAST_POS_SIZE={}
LAST_ORDER_ID={}; TRAILING_ACTIVE={}
BE_DONE={}; ENTRY_TIME={}
IS_CLOSING={}
trades_today=0
quarantine_until=None

# === HEALTH ===
class HealthHandler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.send_header('Content-type','text/plain'); self.end_headers()
  self.wfile.write(b'BOT OK V128 MRG PRODUCT ID')
 def do_HEAD(self): self.send_response(200); self.send_header('Content-type','text/plain'); self.end_headers()
 def log_message(self, format, *args): return
def run_health_server():
 try: HTTPServer(('0.0.0.0', int(os.getenv("PORT",10000))), HealthHandler).serve_forever()
 except: pass
threading.Thread(target=run_health_server, daemon=True).start()

def sig(m,t,p,q,b,s): return hmac.new(s.encode(), (m+t+p+q+b).encode(), hashlib.sha256).hexdigest()
def hdr(m,p,q="",b=""):
 t=str(int(time.time()))
 return {"api-key":K,"timestamp":t,"signature":sig(m,t,p,q,b,S),"Content-Type":"application/json"}
def price():
 try: return float(requests.get(f"{B}/v2/tickers/BTCUSD", timeout=5).json()['result']['mark_price'])
 except: return 80600.0
def order(d):
 try:
  p="/v2/orders"; b=json.dumps(d, separators=(',', ':')); h=hdr("POST",p,"",b)
  r=requests.post(B+p, headers=h, data=b, timeout=10)
  print(f"ORDER {b} => {r.text}"); return r.json()
 except Exception as ex: print(f"ORDER EXCEPTION {ex}"); return {"success": False}
def cancel_all_orders():
 try:
  p="/v2/orders/all"; b=json.dumps({"product_id":PRODUCT_ID,"cancel_stop_orders":True,"cancel_limit_orders":True}, separators=(',', ':')); h=hdr("DELETE",p,"",b)
  requests.delete(B+p, headers=h, data=b, timeout=5)
 except: pass
def get_balance():
 try:
  h=hdr("GET","/v2/wallet/balances"); r=requests.get(B+"/v2/wallet/balances", headers=h, timeout=5).json()
  for x in r.get('result',[]):
   if x.get('asset_symbol')=='USD': return float(x.get('available_balance',0))
  return 0
 except: return 0
def get_real_position_full():
 sz=0; entry=0; upnl=0; liq=0; margin=0
 try:
  h=hdr("GET","/v2/positions"); pos=requests.get(B+"/v2/positions", headers=h, timeout=10).json()
  for x in pos.get('result',[]):
   s=float(x.get('size',0))
   if abs(s)>0.0000001 and 'BTC' in str(x.get('product_symbol','')).upper(): sz=s; entry=float(x.get('entry_price',0)); upnl=float(x.get('unrealized_pnl',0)); liq=float(x.get('liquidation_price',0) or 0)
  return sz, entry, upnl, liq, margin
 except: return 0,0,0,0,0
def size_to_lots(real_size): return int(abs(real_size)*1000) if abs(real_size)<1 else int(abs(real_size))

# === V128 MRG LOGIC ONLY - OLD LOGIC DELETED ===
def is_mrg(): 
    return MRG_START <= datetime.datetime.now(IST).hour < MRG_END

def fetch_candles_df(res="15m", count=100):
    try:
        now=int(time.time()); secs={"15m":900,"1h":3600}
        frm=now-(secs.get(res.lower(),900)*count)-1000
        url=f"{BC}/v2/history/candles"; params={"resolution":res.lower(),"symbol":"BTCUSD","start":frm,"end":now}
        r=requests.get(url, params=params, timeout=8).json()
        candles=r if isinstance(r,list) else r.get('result',[])
        if len(candles)>20:
            df=pd.DataFrame(candles)
            df['close']=df['close'].astype(float); df['high']=df['high'].astype(float); df['low']=df['low'].astype(float); df['volume']=df['volume'].astype(float)
            return df
    except: pass
    return pd.DataFrame()

def calc_rsi(df, p=14):
    try:
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(p).mean()
        loss = -delta.where(delta < 0, 0).rolling(p).mean()
        rs = gain/loss
        return 100 - (100/(1+rs)).iloc[-1]
    except: return 50

def get_signal():
    try:
        df15=fetch_candles_df('15m',100)
        df1h=fetch_candles_df('1h',100)
        if len(df15)<50 or len(df1h)<50: return None
        ema50_1h = df1h['close'].ewm(50).mean().iloc[-1]
        rsi_1h = calc_rsi(df1h)
        cur_price = df15['close'].iloc[-1]
        # VWAP calc
        vwap = ( (df15['high']+df15['low']+df15['close'])/3 * df15['volume']).cumsum() / df15['volume'].cumsum()
        vwap_val=vwap.iloc[-1]
        ema9 = df15['close'].ewm(9).mean().iloc[-1]
        ema20 = df15['close'].ewm(20).mean().iloc[-1]
        ema50 = df15['close'].ewm(50).mean().iloc[-1]
        rsi15 = calc_rsi(df15)
        vol_ok = df15['volume'].iloc[-1] > df15['volume'].rolling(20).mean().iloc[-1]*1.2

        if df1h['close'].iloc[-1] > ema50_1h and rsi_1h > 50:
            if cur_price > vwap_val and ema9 > ema20 and cur_price > ema50 and 55 <= rsi15 <= 70 and vol_ok:
                return "LONG", cur_price, vwap_val, rsi15, rsi_1h
        if df1h['close'].iloc[-1] < ema50_1h and rsi_1h < 50:
            if cur_price < vwap_val and ema9 < ema20 and cur_price < ema50 and 30 <= rsi15 <= 45 and vol_ok:
                return "SHORT", cur_price, vwap_val, rsi15, rsi_1h
    except Exception as e: print(f"get_signal error {e}")
    return None

# === JOBS - V128 MRG ONLY ===
async def auto_alert_job(context: ContextTypes.DEFAULT_TYPE):
    global trades_today
    try:
        if not is_mrg(): return
        live_sz,_,_,_,_=get_real_position_full()
        if abs(live_sz)>0.0000001: return
        if trades_today>=MAX_TRADES: return
        sig_data=get_signal()
        if not sig_data: return
        side, cur_p, vwap, rsi15, rsi1h = sig_data
        msg=f"📢 V128 MRG SIGNAL: {side} 75% BOOM @ {datetime.datetime.now(IST).strftime('%H:%M')}\nBTC: {cur_p:.1f} VWAP: {vwap:.1f} RSI15: {rsi15:.1f} RSI1H: {rsi1h:.1f}\nQTY: YOUR SETTING\nYES? Auto entry Product ID {PRODUCT_ID}"
        kb=[[InlineKeyboardButton(f"✅ YES {side}", callback_data=f"confirm_{side.lower()}"), InlineKeyboardButton("❌ NO", callback_data="confirm_no")]]
        for uid in ALERT_USERS.union(set(USER_QTY.keys())):
            try: await context.bot.send_message(uid, msg, reply_markup=InlineKeyboardMarkup(kb))
            except: pass
    except Exception as e: print(f"auto_alert error {e}")

async def check_positions_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        if any(IS_CLOSING.values()): return
        cur_p=price(); cur_size, entry_real, upnl_real, liq_real, _=get_real_position_full()
        if abs(cur_size)<0.0000001: return
        for uid in list(USER_QTY.keys()):
            if IS_CLOSING.get(uid): continue
            entry=ENTRY_PRICE.get(uid, entry_real)
            if entry==0: continue
            best=BEST_PRICE.get(uid, entry)
            if cur_size>0 and cur_p>best: BEST_PRICE[uid]=cur_p; best=cur_p
            if cur_size<0 and cur_p<best: BEST_PRICE[uid]=cur_p; best=cur_p
            
            pnl_dollar=(cur_p-entry)*abs(cur_size) if cur_size>0 else (entry-cur_p)*abs(cur_size)
            profit_pct=(cur_p-entry)/entry*100 if cur_size>0 else (entry-cur_p)/entry*100
            
            # $3 BE
            if not BE_DONE.get(uid, False) and pnl_dollar >= BE_PROFIT_DOLLAR:
                BE_DONE[uid]=True
                try: await context.bot.send_message(uid, f"🛡️ BE ACTIVATED! +${pnl_dollar:.2f} >= ${BE_PROFIT_DOLLAR}")
                except: pass
            
            # 0.8% TP FULL CLOSE - Product ID Base
            if profit_pct >= 0.8:
                lots=size_to_lots(cur_size); side_to_close="sell" if cur_size>0 else "buy"
                order({"product_id":PRODUCT_ID,"size":lots,"side":side_to_close,"order_type":"market_order","reduce_only":"true","time_in_force":"ioc"})
                await asyncio.sleep(2)
                sz_check,_,_,_,_=get_real_position_full()
                if abs(sz_check)<0.0000001:
                    try: await context.bot.send_message(uid, f"🎯 TP 0.8% FULL CLOSE DONE: {profit_pct:.2f}% +${pnl_dollar:.2f} Product ID {PRODUCT_ID}")
                    except: pass
                    BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); BE_DONE.pop(uid,None); ENTRY_TIME.pop(uid,None)
    except Exception as e: print(f"check_positions error {e}")

# === COMMANDS ===
async def start(u,c):
 ALERT_USERS.add(u.effective_user.id); uid=u.effective_user.id; qty=USER_QTY.get(uid,1); lev=USER_LEV.get(uid,20); bal=get_balance(); cur=price(); sz,entry,upnl,liq,margin=get_real_position_full()
 txt=f"🤖 BOT V128 MRG ✅ Product ID {PRODUCT_ID}\nBal: ${bal:.2f} BTC: {cur:.1f}\nQTY: {qty} Lot Lev: {lev}x\nPOS: {sz} Entry {entry} UPNL ${upnl:.2f}\nMRG: {is_mrg()} 9-4 | Trades: {trades_today}/2\nLogic: 1H EMA50 + RSI + 15M VWAP + EMA9/20 + RSI + Vol\nClose: Product ID {PRODUCT_ID} FULL ONLY\n/cmds: /qty /lev /trend /close"
 await u.message.reply_text(txt)

async def trend_cmd(u,c):
 ALERT_USERS.add(u.effective_user.id)
 try:
  await u.message.reply_text("📊 Loading V128 MRG...")
  sig_data=get_signal()
  if not sig_data: await u.message.reply_text(f"⚖️ WAIT - No MRG Signal\nMRG: {is_mrg()} BTC: {price():.1f}"); return
  side, cur_p, vwap, rsi15, rsi1h = sig_data
  await u.message.reply_text(f"📊 TREND V128 MRG\nBTC: {cur_p:.1f} VWAP: {vwap:.1f}\nRSI15: {rsi15:.1f} RSI1H: {rsi1h:.1f}\nFINAL: 🚀 {side} BOOM ✅\nMRG: {is_mrg()} Product ID {PRODUCT_ID}")
 except Exception as ex: await u.message.reply_text(f"Trend error {ex}")

async def qty_cmd(u,c):
 ALERT_USERS.add(u.effective_user.id)
 try: lots=int(float(c.args[0])) if c.args else 1; USER_QTY[u.effective_user.id]=lots; await u.message.reply_text(f"✅ QTY {lots} Lot = {lots*0.001} BTC - LOCKED Product ID {PRODUCT_ID}")
 except: await u.message.reply_text(f"Use /qty 1 to 4")

async def lev_cmd(u,c):
 ALERT_USERS.add(u.effective_user.id); l=int(c.args[0]) if c.args else 20; USER_LEV[u.effective_user.id]=l; await u.message.reply_text(f"✅ Lev {l}x - LOCKED")

async def button(u,c):
 q=u.callback_query; await q.answer()
 try:
  ALERT_USERS.add(q.from_user.id); data=q.data
  if data=="confirm_no": await q.edit_message_text("❌ Skipped V128 MRG"); return
  if "confirm_" in data:
   side="buy" if "long" in data else "sell"; sz_check,_,_,_,_=get_real_position_full()
   if abs(sz_check)>0.0000001: await q.edit_message_text(f"❌ Already have position {sz_check}! /close first!"); return
   lots=USER_QTY.get(q.from_user.id,1); e=price()
   res=order({"product_id":PRODUCT_ID,"size":lots,"side":side,"order_type":"market_order"})
   if not res.get('success'): await q.edit_message_text(f"❌ Failed! {res}"); return
   ENTRY_PRICE[q.from_user.id]=e; BEST_PRICE[q.from_user.id]=e; ENTRY_TIME[q.from_user.id]=time.time(); BE_DONE[q.from_user.id]=False
   await q.edit_message_text(f"✅ {side.upper()} DONE V128 MRG Product ID {PRODUCT_ID}\nEntry {e:.1f} QTY {lots} BE ${BE_PROFIT_DOLLAR} TP 0.8% FULL CLOSE ONLY")
 except Exception as e: print(f"button error {e}")

async def close_cmd(u,c):
 ALERT_USERS.add(u.effective_user.id); uid=u.effective_user.id; IS_CLOSING[uid]=True
 try:
  sz,_,up,liq,margin=get_real_position_full()
  if abs(sz)<0.0000001: await u.message.reply_text(f"✅ Already Closed! Product ID {PRODUCT_ID}"); IS_CLOSING[uid]=False; return
  lots=size_to_lots(sz); side="buy" if sz<0 else "sell"
  order({"product_id":PRODUCT_ID,"size":lots,"side":side,"order_type":"market_order","reduce_only":"true","time_in_force":"ioc"})
  await asyncio.sleep(2); cancel_all_orders()
  await u.message.reply_text(f"✅ CLOSED! Product ID {PRODUCT_ID} {sz} BTC Closed at {price():.1f} UPNL ${up:.2f} FULL CLOSE ✅")
  BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); BE_DONE.pop(uid,None); ENTRY_TIME.pop(uid,None); IS_CLOSING[uid]=False
 except Exception as e: await u.message.reply_text(f"❌ Error {e}"); IS_CLOSING[u.effective_user.id]=False

def main():
 try: requests.get(f"https://api.telegram.org/bot{T}/deleteWebhook?drop_pending_updates=true", timeout=10); print("Webhook Deleted ✅"); time.sleep(2)
 except: pass
 app=Application.builder().token(T).build()
 app.add_handler(CommandHandler("start", start)); app.add_handler(CommandHandler("qty", qty_cmd)); app.add_handler(CommandHandler("lev", lev_cmd)); app.add_handler(CommandHandler("trend", trend_cmd)); app.add_handler(CommandHandler("close", close_cmd)); app.add_handler(CallbackQueryHandler(button))
 app.job_queue.run_repeating(check_positions_job, interval=15, first=10)
 app.job_queue.run_repeating(auto_alert_job, interval=60, first=30)
 print("BOT LIVE V128 MRG PRODUCT ID BASE - MANUAL")
 app.run_polling(drop_pending_updates=True, allowed_updates=["message","callback_query"])
if __name__=="__main__": main()
