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
    def do_GET(self): self.send_response(200); self.send_header('Content-type','text/plain'); self.end_headers(); self.wfile.write(b'BOT OK')
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
    except: return 80500.0
def order(d):
    p="/v2/orders"; b=json.dumps(d, separators=(',', ':')); h=hdr("POST",p,"",b)
    r=requests.post(B+p, headers=h, data=b, timeout=10)
    print(f"ORDER {b} => {r.text}"); return r.json(), r.text
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

def get_real_position():
    sz=0; entry=0; upnl=0
    try:
        h=hdr("GET","/v2/positions"); pos=requests.get(B+"/v2/positions", headers=h, timeout=10).json()
        print(f"MAIN POS: {pos}")
        for x in pos.get('result',[]):
            if int(x.get('product_id',0))==27:
                s=float(x.get('size',0))
                if abs(s)>0: sz=s; entry=float(x.get('entry_price',0)); upnl=float(x.get('unrealized_pnl',0))
        try:
            h2=hdr("GET","/v2/positions/margined","product_id=27","")
            pos2=requests.get(B+"/v2/positions/margined?product_id=27", headers=h2, timeout=10).json()
            print(f"ISOLATED POS: {pos2}")
            res2=pos2.get('result')
            items=[res2] if isinstance(res2, dict) else (res2 if isinstance(res2, list) else [])
            for x in items:
                if int(x.get('product_id',0))==27:
                    s=float(x.get('size',0))
                    if abs(s)>0: sz=s; entry=float(x.get('entry_price',0)); upnl=float(x.get('unrealized_pnl',0))
        except Exception as e: print(f"ISO ERR {e}")
        return sz, entry, upnl
    except: return 0,0,0

async def check_positions_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        cur_p=price(); cur_size,_,_=get_real_position()
        all_uids=set(list(USER_QTY.keys())+list(LAST_POS_SIZE.keys())+list(ENTRY_TIME.keys()))
        for uid in list(all_uids):
            ent_time=ENTRY_TIME.get(uid,0)
            if ent_time!=0 and (time.time()-ent_time)<75:
                if cur_size!=0: LAST_POS_SIZE[uid]=cur_size
                continue
            last_size=LAST_POS_SIZE.get(uid,0)
            if last_size!=0 and cur_size==0:
                cancel_all_orders()
                try: await context.bot.send_message(uid, f"🚨 POSITION CLOSED! Price {cur_p}")
                except: pass
                BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); LAST_SL.pop(uid,None); LAST_TP.pop(uid,None); LAST_POS_SIZE[uid]=0; ENTRY_TIME.pop(uid,None)
        for uid in all_uids:
            if cur_size!=0: LAST_POS_SIZE[uid]=cur_size
    except Exception as e: print(f"JOB ERR {e}")

async def start(u,c):
    uid=u.effective_user.id; qty=USER_QTY.get(uid,1); lev=USER_LEV.get(uid,20)
    bal=get_balance(); cur=price(); sz,entry,upnl=get_real_position()
    txt=f"🤖 BOT\nBal: ${bal:.2f} BTC: {cur:.1f}\nQTY: {qty} Lev: {lev}x\nPOS: {sz} Entry {entry}\n/cmds: /qty /lev /trend /long /short /close"
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
            if cc>o: return "🟢 BULLISH",cc
            else: return "🔴 BEARISH",cc
        return "⚪ SIDEWAYS",price()
    except: return "⚪ SIDEWAYS",price()

async def trend_cmd(u,c):
    e=price(); bal=get_balance(); sz,entry,upnl=get_real_position()
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
    await u.message.reply_text(f"{side.upper()}? Qty {lots} Price {e}", reply_markup=InlineKeyboardMarkup(kb))
async def short_cmd(u,c): await ask_perm(u,"short")
async def long_cmd(u,c): await ask_perm(u,"long")

async def button(u,c):
    q=u.callback_query; await q.answer()
    lots=USER_QTY.get(q.from_user.id,1); e=price()
    if q.data=="no": await q.edit_message_text("Cancelled ❌"); return
    if q.data=="short_yes":
        sl=round(e*(1+SL/100),1); tp=round(e*(1-TP/100),1)
        res,txt=order({"product_id":27,"size":lots,"side":"sell","order_type":"market_order"})
        if res.get('success'):
            order({"product_id":27,"size":lots,"side":"buy","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(sl),"reduce_only":True})
            order({"product_id":27,"size":lots,"side":"buy","order_type":"limit_order","limit_price":str(tp),"reduce_only":True})
            ENTRY_PRICE[q.from_user.id]=e; BEST_PRICE[q.from_user.id]=e; LAST_SL[q.from_user.id]=sl; LAST_TP[q.from_user.id]=tp; LAST_POS_SIZE[q.from_user.id]= -lots; ENTRY_TIME[q.from_user.id]=time.time()
            await q.edit_message_text(f"SHORT DONE ✅ Entry {e}")
        else: await q.edit_message_text(f"FAILED: {txt[:300]}")
    if q.data=="long_yes":
        sl=round(e*(1-SL/100),1); tp=round(e*(1+TP/100),1)
        res,txt=order({"product_id":27,"size":lots,"side":"buy","order_type":"market_order"})
        if res.get('success'):
            order({"product_id":27,"size":lots,"side":"sell","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(sl),"reduce_only":True})
            order({"product_id":27,"size":lots,"side":"sell","order_type":"limit_order","limit_price":str(tp),"reduce_only":True})
            ENTRY_PRICE[q.from_user.id]=e; BEST_PRICE[q.from_user.id]=e; LAST_SL[q.from_user.id]=sl; LAST_TP[q.from_user.id]=tp; LAST_POS_SIZE[q.from_user.id]= lots; ENTRY_TIME[q.from_user.id]=time.time()
            await q.edit_message_text(f"LONG DONE ✅ Entry {e}")
        else: await q.edit_message_text(f"FAILED: {txt[:300]}")

# ===== FORCE CLOSE - SIZE CHECK PANNAMA DIRECT CLOSE =====
async def close_cmd(u,c):
    await u.message.reply_text("⏳ FORCE CLOSING Anna... Cancel + Buy + Sell try pannuren!")
    try:
        # STEP 1: Cancel SL/TP
        cancel_all_orders()
        await asyncio.sleep(2)
        
        # STEP 2: FORCE CLOSE - No size check early return!
        p="/v2/orders"
        
        # Try BUY 1 lot (to close SHORT -0.001)
        d1={"product_id":27,"size":1,"side":"buy","order_type":"market_order"}
        b1=json.dumps(d1, separators=(',', ':')); h1=hdr("POST",p,"",b1)
        r1=requests.post(B+p, headers=h1, data=b1, timeout=10)
        print(f"FORCE BUY1 {r1.text}")
        await u.message.reply_text(f"Try BUY 1 to close SHORT:\n{r1.text[:400]}")
        await asyncio.sleep(3)
        
        # Try BUY 0.001 lot (if size is BTC)
        d2={"product_id":27,"size":0.001,"side":"buy","order_type":"market_order"}
        b2=json.dumps(d2, separators=(',', ':')); h2=hdr("POST",p,"",b2)
        r2=requests.post(B+p, headers=h2, data=b2, timeout=10)
        print(f"FORCE BUY0.001 {r2.text}")
        await asyncio.sleep(2)
        
        # Check after buy tries
        sz,_,_=get_real_position()
        if sz==0:
            await u.message.reply_text("✅ CLOSED Anna! Positions (0) ✅")
        else:
            # If still SHORT, try again BUY 1 with reduce_only
            await u.message.reply_text(f"Still {sz} BTC open, trying BUY 1 with reduce_only...")
            d3={"product_id":27,"size":1,"side":"buy","order_type":"market_order","reduce_only":True}
            b3=json.dumps(d3, separators=(',', ':')); h3=hdr("POST",p,"",b3)
            r3=requests.post(B+p, headers=h3, data=b3, timeout=10)
            print(f"FORCE BUY1 RO {r3.text}")
            await u.message.reply_text(f"Try3 Res: {r3.text[:400]}")
            await asyncio.sleep(3)
            sz2,_,_=get_real_position()
            if sz2==0:
                await u.message.reply_text("✅ CLOSED Anna! ✅")
            else:
                # Last try: SELL 1 to close if it was LONG (just in case)
                await u.message.reply_text(f"Still {sz2} BTC, trying SELL 1 also...")
                d4={"product_id":27,"size":1,"side":"sell","order_type":"market_order"}
                b4=json.dumps(d4, separators=(',', ':')); h4=hdr("POST",p,"",b4)
                r4=requests.post(B+p, headers=h4, data=b4, timeout=10)
                print(f"FORCE SELL1 {r4.text}")
                await asyncio.sleep(3)
                sz3,_,_=get_real_position()
                if sz3==0: await u.message.reply_text("✅ CLOSED Anna! ✅")
                else: await u.message.reply_text(f"❌ Still {sz3} open!\nBUY1: {r1.text[:200]}\nBUY RO: {r3.text[:200]}\n\n**Delta website la X button click panni manual close pannunga Anna!**")

        cancel_all_orders()
        BEST_PRICE.pop(u.effective_user.id,None); ENTRY_PRICE.pop(u.effective_user.id,None); LAST_POS_SIZE[u.effective_user.id]=0; LAST_SL.pop(u.effective_user.id,None); LAST_TP.pop(u.effective_user.id,None); ENTRY_TIME.pop(u.effective_user.id,None)
    except Exception as e:
        await u.message.reply_text(f"❌ Error: {e}")

def main():
    app=Application.builder().token(T).build()
    app.add_handler(CommandHandler("start", start)); app.add_handler(CommandHandler("qty", qty_cmd)); app.add_handler(CommandHandler("lev", lev_cmd))
    app.add_handler(CommandHandler("trend", trend_cmd)); app.add_handler(CommandHandler("short", short_cmd)); app.add_handler(CommandHandler("long", long_cmd))
    app.add_handler(CommandHandler("close", close_cmd)); app.add_handler(CallbackQueryHandler(button))
    app.job_queue.run_repeating(check_positions_job, interval=30, first=15)
    print("BOT START - FORCE CLOSE LIVE"); app.run_polling(drop_pending_updates=True)

if __name__=="__main__": main()
