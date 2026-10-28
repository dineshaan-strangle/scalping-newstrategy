import os, time, hmac, hashlib, requests, json, asyncio, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

K = os.getenv("DELTA_API_KEY")
S = os.getenv("DELTA_API_SECRET")
T = os.getenv("TELEGRAM_TOKEN")
B = "https://api.india.delta.exchange"
BC = "https://cdn.india.deltaex.org"
SL = 1.2  # SL %
TP = 2.0  # TP %
TRAIL_TP = 0.6  # TP HIT AANA APPURAM 0.6% KEEZHA VANTHA CLOSE - TRAILING!

USER_QTY = {}
USER_LEV = {}
BEST_PRICE = {}
ENTRY_PRICE = {}
LAST_POS_SIZE = {}
LAST_SL = {}
LAST_TP = {}
ENTRY_TIME = {}
IS_CLOSING = {}
LAST_ORDER_ID = {}
TRAILING_ACTIVE = {} # TP HIT AACHU NU TRACK!

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.send_header('Content-type','text/plain'); self.end_headers(); self.wfile.write(b'BOT OK V117 BOT ONLY TRAILING SL TP')
    def log_message(self, format, *args): return

def run_health_server():
    try: HTTPServer(('0.0.0.0', int(os.getenv("PORT",10000))), HealthHandler).serve_forever()
    except: pass
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
        return {"success": False}

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
            s=float(x.get('size',0))
            if abs(s)>0.0000001:
                sym = str(x.get('product_symbol','') or '').upper()
                if 'BTC' in sym:
                    sz=s; entry=float(x.get('entry_price',0)); upnl=float(x.get('unrealized_pnl',0))
        try:
            h2=hdr("GET","/v2/positions/margined","","")
            pos2=requests.get(B+"/v2/positions/margined", headers=h2, timeout=10).json()
            for x in (pos2.get('result',[]) if isinstance(pos2.get('result'), list) else []):
                s=float(x.get('size',0))
                sym = str(x.get('product_symbol','') or '').upper()
                if abs(s)>0.0000001 and 'BTC' in sym:
                    sz=s; entry=float(x.get('entry_price',0)); upnl=float(x.get('unrealized_pnl',0))
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
        all_uids = set(list(USER_QTY.keys()) + list(LAST_POS_SIZE.keys()) + list(ENTRY_PRICE.keys()) + list(LAST_ORDER_ID.keys()))
        
        for uid in list(all_uids):
            if IS_CLOSING.get(uid): continue
            if abs(cur_size) < 0.0000001: continue
            
            entry = ENTRY_PRICE.get(uid, entry_real)
            if entry == 0: entry = entry_real
            if entry == 0: continue
            
            best = BEST_PRICE.get(uid, entry)
            order_id_saved = LAST_ORDER_ID.get(uid, "No ID")
            is_trailing = TRAILING_ACTIVE.get(uid, False)
            
            # === BEST PRICE UPDATE - TRAILING LOGIC ===
            if cur_size > 0: # LONG
                if cur_p > best:
                    BEST_PRICE[uid] = cur_p
                    best = cur_p
                    print(f"LONG BEST UPDATE OrderID {order_id_saved} Best {best}")
            else: # SHORT
                if cur_p < best:
                    BEST_PRICE[uid] = cur_p
                    best = cur_p
                    print(f"SHORT BEST UPDATE OrderID {order_id_saved} Best {best}")

            close_reason = None
            side_to_close = None
            
            if cur_size > 0: # LONG POSITION
                sl_price = entry * (1 - SL/100)
                tp_price = entry * (1 + TP/100)
                # 1. SL HIT - FIXED
                if cur_p <= sl_price:
                    close_reason = f"SL HIT {SL}% Price {cur_p} <= {sl_price:.1f}"
                    side_to_close = "sell"
                # 2. TP TRAILING LOGIC
                elif cur_p >= tp_price:
                    # TP HIT - TRAILING ACTIVE AAKUTHU!
                    if not is_trailing:
                        TRAILING_ACTIVE[uid] = True
                        try: await context.bot.send_message(uid, f"🎯 TP HIT! Trailing Active! Best {best:.1f} Entry {entry:.1f} OrderID {order_id_saved}\nIpo {TRAIL_TP}% keezha vantha close aagum!")
                        except: pass
                        print(f"TP HIT LONG OrderID {order_id_saved} Best {best} Entry {entry}")
                    # TRAILING CHECK - BEST LA IRUNTHU 0.6% KEEZHA VANTHA CLOSE!
                    trail_close_price = best * (1 - TRAIL_TP/100)
                    if cur_p <= trail_close_price:
                        close_reason = f"TP TRAIL HIT Best {best:.1f} -> {cur_p:.1f} ({TRAIL_TP}% down) OrderID {order_id_saved}"
                        side_to_close = "sell"
            else: # SHORT POSITION
                sl_price = entry * (1 + SL/100)
                tp_price = entry * (1 - TP/100)
                if cur_p >= sl_price:
                    close_reason = f"SL HIT {SL}% Price {cur_p} >= {sl_price:.1f}"
                    side_to_close = "buy"
                elif cur_p <= tp_price:
                    if not is_trailing:
                        TRAILING_ACTIVE[uid] = True
                        try: await context.bot.send_message(uid, f"🎯 TP HIT! Trailing Active! Best {best:.1f} Entry {entry:.1f} OrderID {order_id_saved}\nIpo {TRAIL_TP}% mela pona close aagum!")
                        except: pass
                        print(f"TP HIT SHORT OrderID {order_id_saved} Best {best} Entry {entry}")
                    trail_close_price = best * (1 + TRAIL_TP/100)
                    if cur_p >= trail_close_price:
                        close_reason = f"TP TRAIL HIT Best {best:.1f} -> {cur_p:.1f} ({TRAIL_TP}% up) OrderID {order_id_saved}"
                        side_to_close = "buy"

            if close_reason and side_to_close:
                print(f"CLOSING {close_reason} OrderID {order_id_saved}")
                lots = size_to_lots(cur_size)
                if lots < 1: lots = 1
                order({"product_id":27,"size":lots,"side":side_to_close,"order_type":"market_order","reduce_only":"true","time_in_force":"ioc"})
                await asyncio.sleep(2)
                sz_check,_,_=get_real_position()
                if abs(sz_check) < 0.0000001:
                    try: await context.bot.send_message(uid, f"✅ CLOSED! {close_reason}\nPrice {cur_p:.1f} OrderID {order_id_saved} Product 27")
                    except: pass
                    BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); LAST_SL.pop(uid,None); LAST_TP.pop(uid,None); LAST_POS_SIZE[uid]=0; ENTRY_TIME.pop(uid,None); LAST_ORDER_ID.pop(uid,None); TRAILING_ACTIVE.pop(uid,None)
                    print(f"CLOSED {close_reason} OrderID {order_id_saved} REMOVED")
                else:
                    print(f"CLOSE FAILED STILL {sz_check}")

        for uid in all_uids:
            if cur_size != 0: LAST_POS_SIZE[uid] = cur_size
            
    except Exception as e:
        print(f"check_positions_job error {e}")

async def start(u,c):
    uid=u.effective_user.id; qty=USER_QTY.get(uid,1); lev=USER_LEV.get(uid,20)
    bal=get_balance(); cur=price(); sz,entry,upnl=get_real_position()
    order_id = LAST_ORDER_ID.get(uid, "None")
    txt=f"🤖 BOT V117 BOT TRAILING\nBal: ${bal:.2f} BTC: {cur:.1f}\nQTY: {qty} Lot Lev: {lev}x\nPOS: {sz} Entry {entry} UPNL ${upnl:.2f}\nRemember OrderID: {order_id}\nSL: {SL}% TP: {TP}% Trail: {TRAIL_TP}%\n/cmds: /qty /lev /trend /long /short /close"
    await u.message.reply_text(txt)

def get_candles_detail(res="5"):
    try:
        res_map={"5":"5m","15":"15m","60":"1h"}; r_res=res_map.get(str(res),"5m")
        now=int(time.time()); frm=now-7200
        url=f"{BC}/v2/history/candles"; params={"resolution":r_res,"symbol":"BTCUSD","start":frm,"end":now}
        r=requests.get(url, params=params, timeout=8).json()
        candles=r if isinstance(r,list) else r.get('result',[])
        if len(candles) >= 3:
            last3 = candles[-3:]; closes = [float(x.get('close',0)) for x in last3]
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
    txt=f"📊 TREND V117\nBTC: {e:.1f} Bal: ${bal:.2f}\n5M: {t5} {c5:.1f}\n15M: {t15} {c15:.1f}\n1H: {t60} {c60:.1f}\nFINAL: {final}\nPOS: {sz} OrderID: {order_id}\nSL {SL}% TP {TP}% Trail {TRAIL_TP}% BOT ONLY"
    await u.message.reply_text(txt)

async def qty_cmd(u,c):
    try: lots=int(float(c.args[0])) if c.args else 1; USER_QTY[u.effective_user.id]=lots; await u.message.reply_text(f"✅ QTY {lots} Lot = {lots*0.001} BTC")
    except: await u.message.reply_text(f"Use /qty 1 to 4")

async def lev_cmd(u,c):
    l=int(c.args[0]) if c.args else 20; USER_LEV[u.effective_user.id]=l; set_lev(l); await u.message.reply_text(f"✅ Lev {l}x")

async def ask_perm(u, side):
    sz,entry,upnl=get_real_position()
    if abs(sz)>0.0000001:
        oid = LAST_ORDER_ID.get(u.effective_user.id, "Unknown")
        await u.message.reply_text(f"❌ Already have position {sz} BTC Entry {entry} OrderID {oid}\nClose first with /close Anna!")
        return
    lots=USER_QTY.get(u.effective_user.id,1); e=price()
    kb=[[InlineKeyboardButton("✅ YES", callback_data=f"{side}_yes"), InlineKeyboardButton("❌ NO", callback_data="no")]]
    await u.message.reply_text(f"{side.upper()}? Qty {lots} Lot ({lots*0.001} BTC) Price {e} SL {SL}% TP {TP}% Trail {TRAIL_TP}% BOT ONLY!", reply_markup=InlineKeyboardMarkup(kb))

async def short_cmd(u,c): await ask_perm(u,"short")
async def long_cmd(u,c): await ask_perm(u,"long")

async def button(u,c):
    q=u.callback_query; await q.answer()
    try:
        sz_check,_,_=get_real_position()
        if abs(sz_check)>0.0000001:
            oid = LAST_ORDER_ID.get(q.from_user.id, "Unknown")
            await q.edit_message_text(f"❌ Blocked! Already have position {sz_check} BTC OrderID {oid}! Use /close first!")
            return
        lots=USER_QTY.get(q.from_user.id,1); e=price()
        if q.data=="no": await q.edit_message_text("Cancelled ❌"); return
        side = "sell" if q.data=="short_yes" else "buy"
        res=order({"product_id":27,"size":lots,"side":side,"order_type":"market_order"})
        if not res.get('success'):
            bal=get_balance(); await q.edit_message_text(f"❌ Failed! Bal ${bal:.2f} Error: {res}"); return
        try:
            order_id = res.get('result', {}).get('id', 0)
            if not order_id: order_id = int(time.time())
            LAST_ORDER_ID[q.from_user.id] = order_id
            print(f"ORDER ID SAVED: {order_id} PRODUCT 27 SIZE {lots} SIDE {side} - BOT TRAILING ONLY")
        except: LAST_ORDER_ID[q.from_user.id] = int(time.time())
        # DELTA LA SL/TP ORDER PODA VENDAM! BOT MATTUM THAN!
        ENTRY_PRICE[q.from_user.id]=e; BEST_PRICE[q.from_user.id]=e; LAST_SL[q.from_user.id]=round(e*(1+SL/100),1) if side=="sell" else round(e*(1-SL/100),1)
        LAST_TP[q.from_user.id]=round(e*(1-TP/100),1) if side=="sell" else round(e*(1+TP/100),1)
        LAST_POS_SIZE[q.from_user.id]= -lots if side=="sell" else lots; ENTRY_TIME[q.from_user.id]=time.time(); TRAILING_ACTIVE[q.from_user.id]=False
        await q.edit_message_text(f"{side.upper()} DONE ✅ BOT TRAILING ONLY!\nEntry {e} SL {SL}% TP {TP}% Trail {TRAIL_TP}%\nOrderID: {LAST_ORDER_ID[q.from_user.id]}\nBest tracking start {e}\nProduct 27")
    except Exception as e:
        print(f"button error {e}")

async def close_cmd(u,c):
    uid=u.effective_user.id; IS_CLOSING[uid]=True
    saved_id = LAST_ORDER_ID.get(uid, "No ID")
    print(f"========== CLOSE START OrderID {saved_id} ==========")
    await u.message.reply_text(f"🔍 Searching REAL Delta...\nRemember OrderID: {saved_id}")
    try:
        sz,_,_=get_real_position()
        await u.message.reply_text(f"📊 REAL Delta: {sz} BTC Remember OrderID: {saved_id}")
        if abs(sz)<0.0000001:
            await u.message.reply_text(f"✅ Already Closed! OrderID {saved_id} Already Closed!")
            BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); LAST_POS_SIZE[uid]=0; LAST_SL.pop(uid,None); LAST_TP.pop(uid,None); ENTRY_TIME.pop(uid,None); LAST_ORDER_ID.pop(uid,None); TRAILING_ACTIVE.pop(uid,None)
            IS_CLOSING[uid]=False; cancel_all_orders(); return
        lots = size_to_lots(sz); side="buy" if sz<0 else "sell"
        order({"product_id":27,"size":lots,"side":side,"order_type":"market_order","reduce_only":"true","time_in_force":"ioc"})
        await asyncio.sleep(3); cancel_all_orders()
        await u.message.reply_text(f"✅ CLOSED! Remember OrderID {saved_id} {sz} BTC Closed at {price():.1f} ✅")
        BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); LAST_POS_SIZE[uid]=0; LAST_SL.pop(uid,None); LAST_TP.pop(uid,None); ENTRY_TIME.pop(uid,None); LAST_ORDER_ID.pop(uid,None); TRAILING_ACTIVE.pop(uid,None); IS_CLOSING[uid]=False
    except Exception as e:
        await u.message.reply_text(f"❌ Error {e} OrderID {saved_id}"); IS_CLOSING[u.effective_user.id]=False

def main():
    app=Application.builder().token(T).build()
    app.add_handler(CommandHandler("start", start)); app.add_handler(CommandHandler("qty", qty_cmd)); app.add_handler(CommandHandler("lev", lev_cmd))
    app.add_handler(CommandHandler("trend", trend_cmd)); app.add_handler(CommandHandler("short", short_cmd)); app.add_handler(CommandHandler("long", long_cmd))
    app.add_handler(CommandHandler("close", close_cmd)); app.add_handler(CallbackQueryHandler(button))
    app.job_queue.run_repeating(check_positions_job, interval=15, first=10) # 15 sec ku check - trailing ku fast!
    print("BOT LIVE V117 BOT ONLY TRAILING SL TP - DELTA NO SL TP"); app.run_polling(drop_pending_updates=True)

if __name__=="__main__": main()
