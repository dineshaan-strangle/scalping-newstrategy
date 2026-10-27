import os, time, hmac, hashlib, requests, json, asyncio, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

K = os.getenv("DELTA_API_KEY")
S = os.getenv("DELTA_API_SECRET")
T = os.getenv("TELEGRAM_TOKEN")
B = "https://api.india.delta.exchange"
BC = "https://cdn.india.deltaex.org"
SL = 1.2
TP = 2.0

USER_QTY = {}
USER_LEV = {}
BEST_PRICE = {}
ENTRY_PRICE = {}
LAST_POS_SIZE = {}
LAST_SL = {}
LAST_TP = {}
ENTRY_TIME = {}

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'BOT OK')
    def log_message(self, format, *args): return

def run_health_server():
    try:
        port = int(os.getenv("PORT", 10000))
        server = HTTPServer(('0.0.0.0', port), HealthHandler)
        server.serve_forever()
    except: pass

threading.Thread(target=run_health_server, daemon=True).start()

def sig(m,t,p,q,b,s): return hmac.new(s.encode(), (m+t+p+q+b).encode(), hashlib.sha256).hexdigest()
def hdr(m,p,q="",b=""):
    t=str(int(time.time()))
    return {"api-key":K,"timestamp":t,"signature":sig(m,t,p,q,b,S),"Content-Type":"application/json"}
def price():
    try: return float(requests.get(f"{B}/v2/tickers/BTCUSD", timeout=5).json()['result']['mark_price'])
    except: return 80500.0
def order(d):
    p="/v2/orders"; b=json.dumps(d, separators=(',', ':')); h=hdr("POST",p,"",b)
    r=requests.post(B+p, headers=h, data=b, timeout=10)
    print(f"ORDER {b} => {r.text}")
    return r.json()
def cancel_all_orders():
    try:
        p="/v2/orders/all"; b=json.dumps({"product_id":27,"cancel_stop_orders":True,"cancel_limit_orders":True}, separators=(',', ':'))
        h=hdr("DELETE",p,"",b); r=requests.delete(B+p, headers=h, data=b, timeout=5)
        print(f"CANCEL => {r.text}")
    except: pass
def set_lev(lev):
    try:
        p="/v2/products/27"; b=json.dumps({"leverage":str(lev)}, separators=(',', ':')); h=hdr("PUT",p,"",b)
        requests.put(B+p, headers=h, data=b, timeout=5)
    except: pass
def get_balance():
    try:
        h=hdr("GET","/v2/wallet/balances"); r=requests.get(B+"/v2/wallet/balances", headers=h, timeout=5).json()
        for x in r.get('result',[]): 
            if x.get('asset_symbol')=='USD': return float(x.get('available_balance',0))
        return 0
    except: return 0

# ===== MAIN + ISOLATED CHECK =====
def get_real_position():
    sz=0; entry=0; upnl=0
    try:
        h=hdr("GET","/v2/positions"); pos=requests.get(B+"/v2/positions", headers=h, timeout=10).json()
        for x in pos.get('result',[]):
            if int(x.get('product_id',0))==27 and float(x.get('size',0))!=0:
                sz=float(x.get('size',0)); entry=float(x.get('entry_price',0)); upnl=float(x.get('unrealized_pnl',0))
        try:
            h2=hdr("GET","/v2/positions/margined","product_id=27","")
            pos2=requests.get(B+"/v2/positions/margined?product_id=27", headers=h2, timeout=10).json()
            res2=pos2.get('result'); items=[res2] if isinstance(res2, dict) else (res2 if isinstance(res2, list) else [])
            for x in items:
                if int(x.get('product_id',0))==27 and float(x.get('size',0))!=0:
                    sz=float(x.get('size',0)); entry=float(x.get('entry_price',0)); upnl=float(x.get('unrealized_pnl',0))
        except: pass
        return sz, entry, upnl
    except: return 0,0,0

# ===== SL HIT FORCED CLOSE FORMULA - FINAL =====
async def check_positions_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        cur_p = price()
        cur_size, entry_p, upnl = get_real_position()
        all_uids = set(list(USER_QTY.keys()) + list(LAST_POS_SIZE.keys()) + list(ENTRY_TIME.keys()) + list(LAST_SL.keys()))
        
        for uid in list(all_uids):
            ent_time = ENTRY_TIME.get(uid, 0)
            if ent_time !=0 and (time.time() - ent_time) < 75:
                if cur_size !=0: LAST_POS_SIZE[uid]=cur_size
                continue
            
            last_size = LAST_POS_SIZE.get(uid, 0)
            sl_price = LAST_SL.get(uid)
            
            # ===== SL HIT FORMULA + FORCE CLOSE =====
            if cur_size !=0 and sl_price:
                force_close = False
                if cur_size < 0 and cur_p >= float(sl_price):
                    force_close = True
                    try: await context.bot.send_message(uid, f"🚨 SL HIT! SHORT SL {sl_price} hit!\nPrice {cur_p} >= SL {sl_price}\n🔥 FORCE CLOSING Anna!")
                    except: pass
                    print(f"SL HIT SHORT {cur_p} >= {sl_price}")
                elif cur_size > 0 and cur_p <= float(sl_price):
                    force_close = True
                    try: await context.bot.send_message(uid, f"🚨 SL HIT! LONG SL {sl_price} hit!\nPrice {cur_p} <= SL {sl_price}\n🔥 FORCE CLOSING Anna!")
                    except: pass
                    print(f"SL HIT LONG {cur_p} <= {sl_price}")
                
                if force_close:
                    cancel_all_orders(); await asyncio.sleep(1)
                    side = "buy" if cur_size < 0 else "sell"
                    p="/v2/orders"
                    b1=json.dumps({"product_id":27,"size":1,"side":side,"order_type":"market_order"}, separators=(',', ':'))
                    h1=hdr("POST",p,"",b1); r1=requests.post(B+p, headers=h1, data=b1, timeout=10)
                    print(f"SL FORCE CLOSE {r1.text}")
                    await asyncio.sleep(2)
                    sz_check,_,_=get_real_position()
                    if sz_check==0:
                        try: await context.bot.send_message(uid, f"✅ SL FORCE CLOSED! Positions (0) Price {cur_p}")
                        except: pass
                        cancel_all_orders()
                        BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); LAST_SL.pop(uid,None); LAST_TP.pop(uid,None); LAST_POS_SIZE[uid]=0; ENTRY_TIME.pop(uid,None)
                        continue
                    else:
                        b2=json.dumps({"product_id":27,"size":1,"side":side,"order_type":"market_order","reduce_only":True}, separators=(',', ':'))
                        h2=hdr("POST",p,"",b2); r2=requests.post(B+p, headers=h2, data=b2, timeout=10)
                        await asyncio.sleep(2)
                        sz_check2,_,_=get_real_position()
                        if sz_check2==0:
                            try: await context.bot.send_message(uid, f"✅ SL FORCE CLOSED Try2! Positions (0)")
                            except: pass
                            cancel_all_orders()
                            BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); LAST_SL.pop(uid,None); LAST_TP.pop(uid,None); LAST_POS_SIZE[uid]=0; ENTRY_TIME.pop(uid,None)
                            continue
            
            if last_size !=0 and cur_size==0:
                cancel_all_orders()
                try: await context.bot.send_message(uid, f"🚨 POSITION CLOSED!\nPrice {cur_p}\nSL {LAST_SL.get(uid)} TP {LAST_TP.get(uid)} ✅")
                except: pass
                BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); LAST_SL.pop(uid,None); LAST_TP.pop(uid,None); LAST_POS_SIZE[uid]=0; ENTRY_TIME.pop(uid,None)
            
            if cur_size !=0 and last_size !=0:
                if uid not in ENTRY_PRICE or ENTRY_PRICE[uid]==0: 
                    ENTRY_PRICE[uid]=cur_p; BEST_PRICE[uid]=cur_p
                size_abs = abs(cur_size)
                if size_abs < 1: size_abs = 1
                if cur_size < 0:
                    if BEST_PRICE.get(uid, cur_p) > cur_p:
                        BEST_PRICE[uid]=cur_p
                        new_sl=round(BEST_PRICE[uid]*(1+SL/100),1); new_tp=round(BEST_PRICE[uid]*(1-TP/100),1)
                        if new_sl < LAST_SL.get(uid, 999999):
                            cancel_all_orders(); await asyncio.sleep(0.8)
                            order({"product_id":27,"size":int(size_abs),"side":"buy","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(new_sl),"reduce_only":True})
                            order({"product_id":27,"size":int(size_abs),"side":"buy","order_type":"limit_order","limit_price":str(new_tp),"reduce_only":True})
                            LAST_SL[uid]=new_sl; LAST_TP[uid]=new_tp
                else:
                    if BEST_PRICE.get(uid, cur_p) < cur_p:
                        BEST_PRICE[uid]=cur_p
                        new_sl=round(BEST_PRICE[uid]*(1-SL/100),1); new_tp=round(BEST_PRICE[uid]*(1+TP/100),1)
                        if new_sl > LAST_SL.get(uid, 0):
                            cancel_all_orders(); await asyncio.sleep(0.8)
                            order({"product_id":27,"size":int(size_abs),"side":"sell","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(new_sl),"reduce_only":True})
                            order({"product_id":27,"size":int(size_abs),"side":"sell","order_type":"limit_order","limit_price":str(new_tp),"reduce_only":True})
                            LAST_SL[uid]=new_sl; LAST_TP[uid]=new_tp
        
        for uid in all_uids:
            if cur_size !=0: LAST_POS_SIZE[uid]=cur_size
    except Exception as e: print(f"JOB ERR {e}")

async def start(u,c):
    uid=u.effective_user.id; qty=USER_QTY.get(uid,1); lev=USER_LEV.get(uid,20)
    bal=get_balance(); cur=price(); sz,entry,upnl=get_real_position()
    txt=f"🤖 SCALPING BOT - SL FORCE CLOSE ENABLED\n💰 Bal: ${bal:.2f} BTC: {cur:.1f}\nQTY: {qty} Lot Lev: {lev}x\nPOS: {sz} Entry {entry} UPNL ${upnl:.2f}\nSL: {LAST_SL.get(uid,'N/A')} TP: {LAST_TP.get(uid,'N/A')}\n/cmds: /qty /lev /trend /long /short /close"
    await u.message.reply_text(txt)

def get_candles_detail(res="5"):
    try:
        res_map={"5":"5m","15":"15m","60":"1h"}; r_res=res_map.get(str(res),"5m")
        now=int(time.time()); frm=now-7200
        url=f"{BC}/v2/history/candles"; params={"resolution":r_res,"symbol":"BTCUSD","start":frm,"end":now}
        r=requests.get(url, params=params, timeout=8).json()
        candles=r if isinstance(r,list) else r.get('result',[])
        if not candles:
            params["symbol"]="MARK:BTCUSD"
            r=requests.get(url, params=params, timeout=8).json()
            candles=r if isinstance(r,list) else r.get('result',[])
        if candles and len(candles)>=1:
            last=candles[-1]; o=float(last.get('open',0)); cc=float(last.get('close',0))
            return ("🟢 BULLISH",cc) if cc>o else ("🔴 BEARISH",cc)
        return "⚪ SIDEWAYS",price()
    except: return "⚪ SIDEWAYS",price()

async def trend_cmd(u,c):
    e=price(); bal=get_balance(); sz,_,_=get_real_position()
    t5,c5=get_candles_detail("5"); t15,c15=get_candles_detail("15"); t60,c60=get_candles_detail("60")
    greens=sum([1 for t in [t5,t15,t60] if "BULLISH" in t]); reds=sum([1 for t in [t5,t15,t60] if "BEARISH" in t])
    final="🚀 BULLISH - LONG" if greens>=2 else "📉 BEARISH - SHORT" if reds>=2 else "⚖️ SIDEWAYS"
    txt=f"📊 TREND\nBTC: {e:.1f} Bal: ${bal:.2f}\n5M: {t5} {c5:.1f}\n15M: {t15} {c15:.1f}\n1H: {t60} {c60:.1f}\nFINAL: {final}\nPOS: {sz}"
    await u.message.reply_text(txt)

async def qty_cmd(u,c):
    lots=int(float(c.args[0])) if c.args else 1; USER_QTY[u.effective_user.id]=lots
    await u.message.reply_text(f"✅ QTY {lots} Lot")
async def lev_cmd(u,c):
    l=int(c.args[0]) if c.args else 20; USER_LEV[u.effective_user.id]=l; set_lev(l)
    await u.message.reply_text(f"✅ Lev {l}x")
async def ask_perm(u, side):
    lots=USER_QTY.get(u.effective_user.id,1); e=price()
    kb=[[InlineKeyboardButton("✅ YES", callback_data=f"{side}_yes"), InlineKeyboardButton("❌ NO", callback_data="no")]]
    await u.message.reply_text(f"{side.upper()}? Qty {lots} Price {e} SL {SL}% TP {TP}%", reply_markup=InlineKeyboardMarkup(kb))
async def short_cmd(u,c): await ask_perm(u,"short")
async def long_cmd(u,c): await ask_perm(u,"long")

async def button(u,c):
    q=u.callback_query; await q.answer()
    lots=USER_QTY.get(q.from_user.id,1); e=price()
    if q.data=="no": await q.edit_message_text("Cancelled ❌"); return
    if q.data=="short_yes":
        sl=round(e*(1+SL/100),1); tp=round(e*(1-TP/100),1)
        res=order({"product_id":27,"size":lots,"side":"sell","order_type":"market_order"})
        if res.get('success'):
            order({"product_id":27,"size":lots,"side":"buy","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(sl),"reduce_only":True})
            order({"product_id":27,"size":lots,"side":"buy","order_type":"limit_order","limit_price":str(tp),"reduce_only":True})
            ENTRY_PRICE[q.from_user.id]=e; BEST_PRICE[q.from_user.id]=e; LAST_SL[q.from_user.id]=sl; LAST_TP[q.from_user.id]=tp; LAST_POS_SIZE[q.from_user.id]= -lots; ENTRY_TIME[q.from_user.id]=time.time()
            await q.edit_message_text(f"SHORT DONE ✅ Entry {e} SL {sl} TP {tp}\nFormula: Price >= {sl} = FORCE CLOSE")
        else: await q.edit_message_text(f"FAILED: {res}")
    if q.data=="long_yes":
        sl=round(e*(1-SL/100),1); tp=round(e*(1+TP/100),1)
        res=order({"product_id":27,"size":lots,"side":"buy","order_type":"market_order"})
        if res.get('success'):
            order({"product_id":27,"size":lots,"side":"sell","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(sl),"reduce_only":True})
            order({"product_id":27,"size":lots,"side":"sell","order_type":"limit_order","limit_price":str(tp),"reduce_only":True})
            ENTRY_PRICE[q.from_user.id]=e; BEST_PRICE[q.from_user.id]=e; LAST_SL[q.from_user.id]=sl; LAST_TP[q.from_user.id]=tp; LAST_POS_SIZE[q.from_user.id]= lots; ENTRY_TIME[q.from_user.id]=time.time()
            await q.edit_message_text(f"LONG DONE ✅ Entry {e} SL {sl} TP {tp}\nFormula: Price <= {sl} = FORCE CLOSE")
        else: await q.edit_message_text(f"FAILED: {res}")

async def close_cmd(u,c):
    await u.message.reply_text("⏳ Closing Anna...")
    try:
        cancel_all_orders(); await asyncio.sleep(2)
        sz,_,_=get_real_position()
        if sz==0:
            await u.message.reply_text("✅ Already Positions (0) Anna!")
            BEST_PRICE.pop(u.effective_user.id,None); ENTRY_PRICE.pop(u.effective_user.id,None); LAST_POS_SIZE[u.effective_user.id]=0; LAST_SL.pop(u.effective_user.id,None); LAST_TP.pop(u.effective_user.id,None); ENTRY_TIME.pop(u.effective_user.id,None)
            return
        side="buy" if sz<0 else "sell"
        p="/v2/orders"
        b1=json.dumps({"product_id":27,"size":1,"side":side,"order_type":"market_order"}, separators=(',', ':'))
        h1=hdr("POST",p,"",b1); requests.post(B+p, headers=h1, data=b1, timeout=10)
        await asyncio.sleep(2)
        sz2,_,_=get_real_position()
        if sz2==0: await u.message.reply_text("✅ CLOSED Anna! Positions (0) ✅")
        else:
            b2=json.dumps({"product_id":27,"size":1,"side":side,"order_type":"market_order","reduce_only":True}, separators=(',', ':'))
            h2=hdr("POST",p,"",b2); requests.post(B+p, headers=h2, data=b2, timeout=10)
            await asyncio.sleep(2)
            sz3,_,_=get_real_position()
            if sz3==0: await u.message.reply_text("✅ CLOSED Anna! ✅")
            else: await u.message.reply_text(f"❌ Still {sz3} open")
        cancel_all_orders()
        BEST_PRICE.pop(u.effective_user.id,None); ENTRY_PRICE.pop(u.effective_user.id,None); LAST_POS_SIZE[u.effective_user.id]=0; LAST_SL.pop(u.effective_user.id,None); LAST_TP.pop(u.effective_user.id,None); ENTRY_TIME.pop(u.effective_user.id,None)
    except Exception as e: await u.message.reply_text(f"❌ {e}")

def main():
    app=Application.builder().token(T).build()
    app.add_handler(CommandHandler("start", start)); app.add_handler(CommandHandler("qty", qty_cmd)); app.add_handler(CommandHandler("lev", lev_cmd))
    app.add_handler(CommandHandler("trend", trend_cmd)); app.add_handler(CommandHandler("short", short_cmd)); app.add_handler(CommandHandler("long", long_cmd))
    app.add_handler(CommandHandler("close", close_cmd)); app.add_handler(CallbackQueryHandler(button))
    app.job_queue.run_repeating(check_positions_job, interval=30, first=15)
    print("BOT LIVE - SL FORCE CLOSE ENABLED"); app.run_polling(drop_pending_updates=True)

if __name__=="__main__": main()
