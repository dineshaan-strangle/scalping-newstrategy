import os, time, hmac, hashlib, requests, json, asyncio, threading, datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

K=os.getenv("DELTA_API_KEY")
S=os.getenv("DELTA_API_SECRET")
T=os.getenv("TELEGRAM_TOKEN")
B="https://api.india.delta.exchange"
BC="https://cdn.india.deltaex.org"
PRODUCT_ID=27
MRG_START, MRG_END = 9, 16
MAX_TRADES=2
BE_PROFIT_DOLLAR=3
USER_QTY={}; USER_LEV={}; ALERT_USERS=set()
BEST_PRICE={}; ENTRY_PRICE={}; IS_CLOSING={}; BE_DONE={}; ENTRY_TIME={}
trades_today=0

class HealthHandler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.send_header('Content-type','text/plain'); self.end_headers()
  self.wfile.write(b'BOT OK V128 MRG NO PANDAS')
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
 except Exception as ex: print(f"ORDER EX {ex}"); return {"success": False}
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

# === V128 MRG LOGIC - NO PANDAS NO PYTZ ===
def is_mrg():
    # IST = UTC+5:30
    now_utc=datetime.datetime.utcnow()
    ist_hour=(now_utc.hour+5)%24
    # Simple: UTC+5:30 approx
    if now_utc.minute>=30: ist_hour=(ist_hour+1)%24 if now_utc.hour+5>=23 else ist_hour
    # Use local hour for safety - Render IST? We use UTC+5:30 calc
    utc = datetime.datetime.utcnow()
    ist = utc + datetime.timedelta(hours=5, minutes=30)
    return MRG_START <= ist.hour < MRG_END

def fetch_candles(res="15m", count=100):
    try:
        now=int(time.time()); secs={"15m":900,"1h":3600}
        frm=now-(secs.get(res.lower(),900)*count)-1000
        url=f"{BC}/v2/history/candles"; params={"resolution":res.lower(),"symbol":"BTCUSD","start":frm,"end":now}
        r=requests.get(url, params=params, timeout=8).json()
        candles=r if isinstance(r,list) else r.get('result',[])
        if len(candles)>20:
            closes=[float(c.get('close',0)) for c in candles]
            highs=[float(c.get('high',0)) for c in candles]
            lows=[float(c.get('low',0)) for c in candles]
            vols=[float(c.get('volume',0)) for c in candles]
            return closes[-count:], highs[-count:], lows[-count:], vols[-count:]
    except: pass
    return [],[],[],[]

def ema(arr, period):
    try:
        if len(arr)<period: return arr[-1] if arr else 0
        k=2/(period+1); ema_val=sum(arr[:period])/period
        for p in arr[period:]: ema_val=p*k+ema_val*(1-k)
        return ema_val
    except: return arr[-1] if arr else 0

def rsi(arr, p=14):
    try:
        if len(arr)<p+1: return 50
        deltas=[arr[i]-arr[i-1] for i in range(1,len(arr))]
        gains=[d if d>0 else 0 for d in deltas[-p:]]
        losses=[-d if d<0 else 0 for d in deltas[-p:]]
        ag=sum(gains)/p; al=sum(losses)/p
        if al==0: return 70
        rs=ag/al; return 100 - (100/(1+rs))
    except: return 50

def get_signal():
    try:
        c15,h15,l15,v15=fetch_candles('15m',100)
        c1h,h1h,l1h,v1h=fetch_candles('1h',100)
        if len(c15)<50 or len(c1h)<50: return None
        ema50_1h=ema(c1h,50); rsi1h=rsi(c1h)
        cur=c15[-1]
        # VWAP simple
        typical=[(h15[i]+l15[i]+c15[i])/3 for i in range(len(c15))]
        pv=sum(typical[-20:][i]*v15[-20:][i] for i in range(min(20,len(v15)))) if len(v15)>=20 else cur
        vv=sum(v15[-20:]) if len(v15)>=20 else 1
        vwap=pv/vv if vv!=0 else cur
        e9=ema(c15,9); e20=ema(c15,20); e50=ema(c15,50); r15=rsi(c15)
        vol_avg=sum(v15[-20:-1])/19 if len(v15)>=21 else 1
        vol_ok=v15[-1] > vol_avg*1.2 if len(v15)>=1 else False
        if c1h[-1] > ema50_1h and rsi1h > 50:
            if cur > vwap and e9 > e20 and cur > e50 and 55 <= r15 <= 70 and vol_ok:
                return "LONG", cur, vwap, r15, rsi1h
        if c1h[-1] < ema50_1h and rsi1h < 50:
            if cur < vwap and e9 < e20 and cur < e50 and 30 <= r15 <= 45 and vol_ok:
                return "SHORT", cur, vwap, r15, rsi1h
    except Exception as e: print(f"signal err {e}")
    return None

async def auto_alert_job(context: ContextTypes.DEFAULT_TYPE):
    global trades_today
    try:
        if not is_mrg(): return
        sz,_,_,_,_=get_real_position_full()
        if abs(sz)>0.0000001: return
        if trades_today>=MAX_TRADES: return
        sigd=get_signal()
        if not sigd: return
        side,cur_p,vwap,rsi15,rsi1h=sigd
        msg=f"📢 V128 MRG {side} BOOM\nBTC {cur_p:.1f} VWAP {vwap:.1f} RSI15 {rsi15:.1f}\nProduct ID {PRODUCT_ID} FULL CLOSE"
        kb=[[InlineKeyboardButton(f"✅ YES {side}", callback_data=f"confirm_{side.lower()}"), InlineKeyboardButton("❌ NO", callback_data="confirm_no")]]
        for uid in ALERT_USERS.union(set(USER_QTY.keys())):
            try: await context.bot.send_message(uid, msg, reply_markup=InlineKeyboardMarkup(kb))
            except: pass
    except Exception as e: print(f"alert err {e}")

async def check_positions_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        if any(IS_CLOSING.values()): return
        cur_p=price(); cur_size, entry_real, upnl_real, liq_real, _=get_real_position_full()
        if abs(cur_size)<0.0000001: return
        for uid in list(USER_QTY.keys()):
            if IS_CLOSING.get(uid): continue
            entry=ENTRY_PRICE.get(uid, entry_real)
            if entry==0: continue
            pnl_dollar=(cur_p-entry)*abs(cur_size) if cur_size>0 else (entry-cur_p)*abs(cur_size)
            profit_pct=(cur_p-entry)/entry*100 if cur_size>0 else (entry-cur_p)/entry*100
            if not BE_DONE.get(uid, False) and pnl_dollar >= BE_PROFIT_DOLLAR:
                BE_DONE[uid]=True
                try: await context.bot.send_message(uid, f"🛡️ BE +${pnl_dollar:.2f}")
                except: pass
            if profit_pct >= 0.8:
                lots=size_to_lots(cur_size); side_to_close="sell" if cur_size>0 else "buy"
                order({"product_id":PRODUCT_ID,"size":lots,"side":side_to_close,"order_type":"market_order","reduce_only":"true","time_in_force":"ioc"})
                await asyncio.sleep(2)
                sz_check,_,_,_,_=get_real_position_full()
                if abs(sz_check)<0.0000001:
                    try: await context.bot.send_message(uid, f"🎯 TP 0.8% FULL CLOSE {profit_pct:.2f}% Product {PRODUCT_ID}")
                    except: pass
                    ENTRY_PRICE.pop(uid,None); BE_DONE.pop(uid,None); ENTRY_TIME.pop(uid,None)
    except Exception as e: print(f"check err {e}")

async def start(u,c):
 ALERT_USERS.add(u.effective_user.id); uid=u.effective_user.id; qty=USER_QTY.get(uid,1); cur=price(); sz,entry,upnl,liq,_=get_real_position_full(); bal=get_balance()
 txt=f"🤖 V128 MRG NO PANDAS ✅\nBal ${bal:.2f} BTC {cur:.1f}\nQTY {qty} POS {sz} Entry {entry}\nMRG {is_mrg()} 9-4 Product {PRODUCT_ID}\nLogic: 1H EMA50+RSI + 15M VWAP+EMA+RSI+Vol\n/qty /trend /close"
 await u.message.reply_text(txt)

async def trend_cmd(u,c):
 ALERT_USERS.add(u.effective_user.id)
 try:
  await u.message.reply_text("📊 Loading V128...")
  sigd=get_signal()
  if not sigd: await u.message.reply_text(f"⚖️ WAIT No Signal MRG:{is_mrg()} BTC:{price():.1f}"); return
  side,cur_p,vwap,rsi15,rsi1h=sigd
  await u.message.reply_text(f"📊 V128 MRG\nBTC {cur_p:.1f} VWAP {vwap:.1f}\nRSI15 {rsi15:.1f} RSI1H {rsi1h:.1f}\nFINAL 🚀 {side} BOOM Product {PRODUCT_ID}")
 except Exception as ex: await u.message.reply_text(f"Trend err {ex}")

async def qty_cmd(u,c):
 ALERT_USERS.add(u.effective_user.id)
 try: lots=int(float(c.args[0])) if c.args else 1; USER_QTY[u.effective_user.id]=lots; await u.message.reply_text(f"✅ QTY {lots} LOCKED Product {PRODUCT_ID}")
 except: await u.message.reply_text(f"Use /qty 1")

async def lev_cmd(u,c):
 ALERT_USERS.add(u.effective_user.id); l=int(c.args[0]) if c.args else 20; USER_LEV[u.effective_user.id]=l; await u.message.reply_text(f"✅ Lev {l}x")

async def button(u,c):
 q=u.callback_query; await q.answer()
 try:
  data=q.data
  if data=="confirm_no": await q.edit_message_text("❌ Skipped"); return
  if "confirm_" in data:
   side="buy" if "long" in data else "sell"; sz_check,_,_,_,_=get_real_position_full()
   if abs(sz_check)>0.0000001: await q.edit_message_text(f"❌ Already have {sz_check}! /close first!"); return
   lots=USER_QTY.get(q.from_user.id,1); e=price()
   res=order({"product_id":PRODUCT_ID,"size":lots,"side":side,"order_type":"market_order"})
   if not res.get('success'): await q.edit_message_text(f"❌ Failed {res}"); return
   ENTRY_PRICE[q.from_user.id]=e; BE_DONE[q.from_user.id]=False; ENTRY_TIME[q.from_user.id]=time.time()
   await q.edit_message_text(f"✅ {side.upper()} DONE Product {PRODUCT_ID}\nEntry {e:.1f} QTY {lots} BE ${BE_PROFIT_DOLLAR} TP 0.8% FULL")
 except Exception as e: print(f"btn err {e}")

async def close_cmd(u,c):
 ALERT_USERS.add(u.effective_user.id); uid=u.effective_user.id; IS_CLOSING[uid]=True
 try:
  sz,_,up,liq,_=get_real_position_full()
  if abs(sz)<0.0000001: await u.message.reply_text(f"✅ Already Closed Product {PRODUCT_ID}"); IS_CLOSING[uid]=False; return
  lots=size_to_lots(sz); side="buy" if sz<0 else "sell"
  order({"product_id":PRODUCT_ID,"size":lots,"side":side,"order_type":"market_order","reduce_only":"true","time_in_force":"ioc"})
  await asyncio.sleep(2); cancel_all_orders()
  await u.message.reply_text(f"✅ CLOSED Product {PRODUCT_ID} {sz} @ {price():.1f} UPNL ${up:.2f} FULL")
  ENTRY_PRICE.pop(uid,None); BE_DONE.pop(uid,None); ENTRY_TIME.pop(uid,None); IS_CLOSING[uid]=False
 except Exception as e: await u.message.reply_text(f"❌ Err {e}"); IS_CLOSING[u.effective_user.id]=False

def main():
 try: requests.get(f"https://api.telegram.org/bot{T}/deleteWebhook?drop_pending_updates=true", timeout=10); time.sleep(2)
 except: pass
 app=Application.builder().token(T).build()
 app.add_handler(CommandHandler("start", start)); app.add_handler(CommandHandler("qty", qty_cmd)); app.add_handler(CommandHandler("lev", lev_cmd)); app.add_handler(CommandHandler("trend", trend_cmd)); app.add_handler(CommandHandler("close", close_cmd)); app.add_handler(CallbackQueryHandler(button))
 app.job_queue.run_repeating(check_positions_job, interval=15, first=10)
 app.job_queue.run_repeating(auto_alert_job, interval=60, first=30)
 print("BOT LIVE V128 MRG NO PANDAS - PRODUCT ID")
 app.run_polling(drop_pending_updates=True, allowed_updates=["message","callback_query"])
if __name__=="__main__": main()
