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
IS_CLOSING = {}
LAST_ORDER_ID = {}  # ORDER ID REMEMBER

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.send_header('Content-type','text/plain'); self.end_headers(); self.wfile.write(b'BOT OK V114 ORDER ID VISIBLE')
    def log_message(self, format, *args): return

def run_health_server():
    try: 
        HTTPServer(('0.0.0.0', int(os.getenv("PORT",10000))), HealthHandler).serve_forever()
    except Exception as e:
        print(f"Health server error {e}")
threading.Thread(target=run_health_server, daemon=True).start()

def sig(m,t,p,q,b,s): return hmac.new(s.encode(), (m+t+p+q+b).encode(), hashlib.sha256).hexdigest()
def hdr(m,p,q="",b=""):
    t=str(int(time.time())); return {"api-key":K,"timestamp":t,"signature":sig(m,t,p,q,b,S),"Content-Type":"application/json"}
def price():
    try: return float(requests.get(f"{B}/v2/tickers/BTCUSD", timeout=5).json()['result']['mark_price'])
    except: return 80600.0

def order(d):
    try:
        p="/v2/orders"; b=json.dumps(d, separators=(',', ':')); h=hdr("POST",p,"",b)
        r=requests.post(B+p, headers=h, data=b, timeout=10)
        print(f"ORDER {b} => {r.text}")
        return r.json()
    except Exception as ex:
        print(f"ORDER EXCEPTION {ex}")
        return {"success": False, "error": {"code":"exception","msg":str(ex)}}

def cancel_all_orders():
    try:
        p="/v2/orders/all"; b=json.dumps({"product_id":27,"cancel_stop_orders":True,"cancel_limit_orders":True}, separators=(',', ':'))
        h=hdr("DELETE",p,"",b); requests.delete(B+p, headers=h, data=b, timeout=5)
    except: pass

def set_lev(lev):
    try: p="/v2/products/27"; b=json.dumps({"leverage":str(lev)}, separators=(',', ':')); h=hdr("PUT",p,"",b); requests.put(B+p, headers=h, data=b, timeout=5)
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
        for x in pos.get('result',[]):
            if int(x.get('product_id',0))==27:
                s=float(x.get('size',0))
                if abs(s)>0.0000001: sz=s; entry=float(x.get('entry_price',0)); upnl=float(x.get('unrealized_pnl',0))
        try:
            h2=hdr("GET","/v2/positions/margined","product_id=27","")
            pos2=requests.get(B+"/v2/positions/margined?product_id=27", headers=h2, timeout=10).json()
            res2=pos2.get('result'); items=[res2] if isinstance(res2, dict) else (res2 if isinstance(res2, list) else [])
            for x in items:
                if int(x.get('product_id',0))==27:
                    s=float(x.get('size',0))
                    if abs(s)>0.0000001: sz=s; entry=float(x.get('entry_price',0)); upnl=float(x.get('unrealized_pnl',0))
        except: pass
        return sz, entry, upnl
    except: return 0,0,0

def size_to_lots(real_size):
    if abs(real_size) < 1: return int(abs(real_size)*1000)
    else: return int(abs(real_size))

async def check_positions_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        if any(IS_CLOSING.values()): return
        cur_p = price()
        cur_size, entry_real, _ = get_real_position()
        await asyncio.sleep(1)
        sz2, _, _ = get_real_position()
        cur_size = sz2

        all_uids = set(list(USER_QTY.keys()) + list(LAST_POS_SIZE.keys()) + list(ENTRY_PRICE.keys()) + list(LAST_ORDER_ID.keys()))
        
        for uid in list(all_uids):
            if IS_CLOSING.get(uid): continue
            last_size = LAST_POS_SIZE.get(uid, 0)
            entry = ENTRY_PRICE.get(uid, entry_real)
            sl_price = LAST_SL.get(uid)
            order_id_saved = LAST_ORDER_ID.get(uid, "No ID")
            
            if cur_size != 0 and entry != 0 and sl_price:
                force = False
                if cur_size < 0 and cur_p >= float(sl_price): force = True
                elif cur_size > 0 and cur_p <= float(sl_price): force = True
                if force:
                    side = "buy" if cur_size < 0 else "sell"
                    lots = size_to_lots(cur_size)
                    if lots < 1: lots = 1
                    print(f"FORCE CLOSE OrderID {order_id_saved} Product 27 Size {lots}")
                    order({"product_id":27,"size":lots,"side":side,"order_type":"market_order","reduce_only":"true","time_in_force":"ioc"})
                    await asyncio.sleep(3)
                    sz_check,_,_=get_real_position()
                    if abs(sz_check) < 0.0000001:
                        try: await context.bot.send_message(uid, f"✅ SL HIT CLOSED! Price {cur_p} OrderID {order_id_saved} Product 27")
                        except: pass
                        BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); LAST_SL.pop(uid,None); LAST_TP.pop(uid,None); LAST_POS_SIZE[uid]=0; ENTRY_TIME.pop(uid,None); LAST_ORDER_ID.pop(uid,None)
                        print(f"SL CLOSED OrderID {order_id_saved} REMOVED FROM REMEMBER")
                        continue
            
            if last_size != 0 and abs(cur_size) < 0.0000001:
                await asyncio.sleep(3)
                verify_size,_,_ = get_real_position()
                if abs(verify_size) < 0.0000001:
                    cancel_all_orders()
                    try: await context.bot.send_message(uid, f"🚨 POSITION CLOSED! {cur_p} ✅ OrderID {order_id_saved} Product 27 Verified")
                    except: pass
                    print(f"POSITION CLOSED OrderID {order_id_saved} REMOVED")
                    BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); LAST_SL.pop(uid,None); LAST_TP.pop(uid,None); LAST_POS_SIZE[uid]=0; ENTRY_TIME.pop(uid,None); LAST_ORDER_ID.pop(uid,None)
                else:
                    LAST_POS_SIZE[uid] = verify_size

        for uid in all_uids:
            if cur_size != 0: LAST_POS_SIZE[uid] = cur_size
            
    except Exception as e:
        print(f"check_positions_job error {e}")

async def start(u,c):
    uid=u.effective_user.id; qty=USER_QTY.get(uid,1); lev=USER_LEV.get(uid,20)
    bal=get_balance(); cur=price(); sz,entry,upnl=get_real_position()
    order_id = LAST_ORDER_ID.get(uid, "None - No Trade Yet")
    txt=f"🤖 BOT V114 ORDER ID VISIBLE\nBal: ${bal:.2f} BTC: {cur:.1f}\nQTY: {qty} Lot Lev: {lev}x\nPOS: {sz} Entry {entry} UPNL ${upnl:.2f}\nRemember OrderID: {order_id} Product 27\n/cmds: /qty /lev /trend /long /short /close"
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
        if len(candles) >= 3:
            last3 = candles[-3:]
            closes = [float(x.get('close',0)) for x in last3]
            if closes[0] < closes[1] < closes[2]: return "🟢 BULLISH", closes[2]
            elif closes[0] > closes[1] > closes[2]: return "🔴 BEARISH", closes[2]
            else: return "⚪ SIDEWAYS", closes[2]
        return "⚪ SIDEWAYS",price()
    except: return "⚪ SIDEWAYS",price()

async def trend_cmd(u,c):
    e=price(); bal=get_balance(); sz,_,_=get_real_position()
    t5,c5=get_candles_detail("5"); t15,c15=get_candles_detail("15"); t60,c60=get_candles_detail("60")
    greens=sum([1 for t in [t5,t15,t60] if "BULLISH" in t]); reds=sum([1 for t in [t5,t15,t60] if "BEARISH" in t])
    final="🚀 BULLISH - LONG" if greens>=2 else "📉 BEARISH - SHORT" if reds>=2 else "⚖️ SIDEWAYS"
    order_id = LAST_ORDER_ID.get(u.effective_user.id, "None")
    txt=f"📊 TREND V114\nBTC: {e:.1f} Bal: ${bal:.2f}\n5M: {t5} {c5:.1f}\n15M: {t15} {c15:.1f}\n1H: {t60} {c60:.1f}\nFINAL: {final}\nPOS: {sz} Remember OrderID: {order_id} Product 27"
    await u.message.reply_text(txt)

async def qty_cmd(u,c):
    try:
        lots=int(float(c.args[0])) if c.args else 1
        if lots<1: lots=1
        USER_QTY[u.effective_user.id]=lots
        await u.message.reply_text(f"✅ QTY {lots} Lot = {lots*0.001} BTC")
    except:
        await u.message.reply_text(f"Use /qty 1 to 4")

async def lev_cmd(u,c):
    l=int(c.args[0]) if c.args else 20; USER_LEV[u.effective_user.id]=l; set_lev(l)
    await u.message.reply_text(f"✅ Lev {l}x")

async def ask_perm(u, side):
    sz,entry,upnl=get_real_position()
    if abs(sz)>0.0000001:
        oid = LAST_ORDER_ID.get(u.effective_user.id, "Unknown")
        await u.message.reply_text(f"❌ Already have position {sz} BTC Entry {entry} Remember OrderID {oid} Product 27\nClose first with /close Anna!")
        return
    lots=USER_QTY.get(u.effective_user.id,1); e=price()
    kb=[[InlineKeyboardButton("✅ YES", callback_data=f"{side}_yes"), InlineKeyboardButton("❌ NO", callback_data="no")]]
    await u.message.reply_text(f"{side.upper()}? Qty {lots} Lot ({lots*0.001} BTC) Price {e} SL {SL}% TP {TP}% Product 27", reply_markup=InlineKeyboardMarkup(kb))

async def short_cmd(u,c): await ask_perm(u,"short")
async def long_cmd(u,c): await ask_perm(u,"long")

async def button(u,c):
    q=u.callback_query; await q.answer()
    try:
        sz_check,_,_=get_real_position()
        if abs(sz_check)>0.0000001:
            oid = LAST_ORDER_ID.get(q.from_user.id, "Unknown")
            await q.edit_message_text(f"❌ Blocked! Already have position {sz_check} BTC OrderID {oid} Product 27! Use /close first!")
            return
        lots=USER_QTY.get(q.from_user.id,1); e=price()
        if q.data=="no": await q.edit_message_text("Cancelled ❌"); return
        
        side = "sell" if q.data=="short_yes" else "buy"
        sl=round(e*(1+SL/100),1) if side=="sell" else round(e*(1-SL/100),1)
        tp=round(e*(1-TP/100),1) if side=="sell" else round(e*(1+TP/100),1)

        res=order({"product_id":27,"size":lots,"side":side,"order_type":"market_order"})
        
        if not res.get('success'):
            err_str = str(res).lower()
            if 'insufficient_margin' in err_str or 'insufficient' in err_str:
                bal=get_balance()
                await q.edit_message_text(f"❌ Margin Pathala Anna! Qty {lots} Lot ku Balance pathala. Bal ${bal:.2f}\n/qty 1 ku maathi try pannunga!\nError: {res}")
                return
            else:
                await q.edit_message_text(f"FAILED: {res}")
                return

        # === V114 ORDER ID RENDER LOG + BOT + REMEMBER - UNGA IDEA! ===
        try:
            order_id = res.get('result', {}).get('id', 0)
            if not order_id:
                order_id = int(time.time())
            LAST_ORDER_ID[q.from_user.id] = order_id
            # RENDER LOG LA VARUM!
            print(f"========================================")
            print(f"ORDER ID SAVED: {order_id} PRODUCT 27 SIZE {lots} SIDE {side}")
            print(f"REMEMBER DICT: {LAST_ORDER_ID}")
            print(f"========================================")
        except Exception as ex:
            print(f"ORDER ID SAVE ERROR {ex}")
            LAST_ORDER_ID[q.from_user.id] = int(time.time())
            print(f"ORDER ID FALLBACK SAVED: {LAST_ORDER_ID[q.from_user.id]}")

        try:
            if side=="sell":
                order({"product_id":27,"size":lots,"side":"buy","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(sl),"reduce_only":True})
                order({"product_id":27,"size":lots,"side":"buy","order_type":"limit_order","limit_price":str(tp),"reduce_only":True})
            else:
                order({"product_id":27,"size":lots,"side":"sell","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(sl),"reduce_only":True})
                order({"product_id":27,"size":lots,"side":"sell","order_type":"limit_order","limit_price":str(tp),"reduce_only":True})
        except: pass

        ENTRY_PRICE[q.from_user.id]=e; BEST_PRICE[q.from_user.id]=e; LAST_SL[q.from_user.id]=sl; LAST_TP[q.from_user.id]=tp
        LAST_POS_SIZE[q.from_user.id]= -lots if side=="sell" else lots
        ENTRY_TIME[q.from_user.id]=time.time()
        # BOT LA SHOW AGUM!
        await q.edit_message_text(f"{side.upper()} DONE ✅\nEntry {e} SL {sl} TP {tp} Qty {lots} Lot\nOrderID: {LAST_ORDER_ID[q.from_user.id]} Remembered!\nProduct 27 Bot Trade")

    except Exception as e:
        print(f"button error {e}")
        try: await q.edit_message_text(f"❌ Error: {e}")
        except: pass

async def close_cmd(u,c):
    uid=u.effective_user.id
    IS_CLOSING[uid]=True
    saved_id = LAST_ORDER_ID.get(uid, "No ID - Remember Empty")
    # RENDER LOG + BOT RENDUME ORDER ID!
    print(f"========== CLOSE START OrderID {saved_id} Product 27 ==========")
    await u.message.reply_text(f"🔍 Searching REAL Delta Product 27...\nRemember OrderID: {saved_id} Product 27")
    try:
        sz,_,_=get_real_position()

        await u.message.reply_text(f"📊 REAL Delta: {sz} BTC (Product 27) Remember OrderID: {saved_id}")

        if abs(sz)<0.0000001:
            await u.message.reply_text(f"✅ Already Positions (0) - OrderID {saved_id} Product 27 Already Closed!")
            print(f"CLOSE ALREADY 0 OrderID {saved_id} REMOVED")
            BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); LAST_POS_SIZE[uid]=0; LAST_SL.pop(uid,None); LAST_TP.pop(uid,None); ENTRY_TIME.pop(uid,None); LAST_ORDER_ID.pop(uid,None)
            IS_CLOSING[uid]=False
            cancel_all_orders()
            return

        lots = size_to_lots(sz)
        if lots<1: lots=1
        side="buy" if sz<0 else "sell"
        await u.message.reply_text(f"Found Remember OrderID {saved_id}: {sz} BTC = {lots} lots, Closing {side} Product ID 27...")
        print(f"CLOSING OrderID {saved_id} Product 27 Size {sz} Lots {lots} Side {side}")

        res=order({"product_id":27,"size":lots,"side":side,"order_type":"market_order","reduce_only":"true","time_in_force":"ioc"})
        await asyncio.sleep(3)
        cancel_all_orders()
        await asyncio.sleep(1)

        sz_check,_,_=get_real_position()
        if abs(sz_check)<0.0000001:
            await u.message.reply_text(f"✅ CLOSED! Remember OrderID {saved_id} Product 27 {sz} BTC Closed at {price():.1f} ✅")
            print(f"========== CLOSED SUCCESS OrderID {saved_id} REMOVED FROM REMEMBER ==========")
            LAST_ORDER_ID.pop(uid,None)
        else:
            lots2=size_to_lots(sz_check)
            side2="buy" if sz_check<0 else "sell"
            order({"product_id":27,"size":lots2,"side":side2,"order_type":"market_order","reduce_only":"true","time_in_force":"ioc"})
            await asyncio.sleep(2)
            cancel_all_orders()
            await u.message.reply_text(f"⚠️ Retry done OrderID {saved_id}, checking...")
            print(f"RETRY CLOSE OrderID {saved_id} Still {sz_check}")
        
        BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); LAST_POS_SIZE[uid]=0; LAST_SL.pop(uid,None); LAST_TP.pop(uid,None); ENTRY_TIME.pop(uid,None); LAST_ORDER_ID.pop(uid,None)
        IS_CLOSING[uid]=False

    except Exception as e:
        await u.message.reply_text(f"❌ Error {e} OrderID {saved_id}")
        print(f"CLOSE ERROR OrderID {saved_id} {e}")
        IS_CLOSING[u.effective_user.id]=False
        cancel_all_orders()

def main():
    app=Application.builder().token(T).build()
    app.add_handler(CommandHandler("start", start)); app.add_handler(CommandHandler("qty", qty_cmd)); app.add_handler(CommandHandler("lev", lev_cmd))
    app.add_handler(CommandHandler("trend", trend_cmd)); app.add_handler(CommandHandler("short", short_cmd)); app.add_handler(CommandHandler("long", long_cmd))
    app.add_handler(CommandHandler("close", close_cmd)); app.add_handler(CallbackQueryHandler(button))
    app.job_queue.run_repeating(check_positions_job, interval=30, first=15)
    print("BOT LIVE V114 ORDER ID VISIBLE IN LOG + BOT + CLOSE - PRODUCT 27"); app.run_polling(drop_pending_updates=True)

if __name__=="__main__": main()
