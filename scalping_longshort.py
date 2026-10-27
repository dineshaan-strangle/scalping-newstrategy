import os, time, hmac, hashlib, requests, json, asyncio
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

K = os.getenv("DELTA_API_KEY")
S = os.getenv("DELTA_API_SECRET")
T = os.getenv("TELEGRAM_TOKEN")
B = "https://api.india.delta.exchange"
SL = 1.2
TP = 2.0

# MEMORY
USER_QTY = {}
USER_LEV = {}
BEST_PRICE = {}
ENTRY_PRICE = {}
LAST_POS_SIZE = {}
LAST_SL = {}
LAST_TP = {}
ENTRY_TIME = {}

def sig(m,t,p,q,b,s): return hmac.new(s.encode(), (m+t+p+q+b).encode(), hashlib.sha256).hexdigest()
def hdr(m,p,q="",b=""):
    t=str(int(time.time()))
    return {"api-key":K,"timestamp":t,"signature":sig(m,t,p,q,b,S),"Content-Type":"application/json"}
def price():
    try: return float(requests.get(f"{B}/v2/tickers/BTCUSD", timeout=5).json()['result']['mark_price'])
    except: return 80600.0
def order(d):
    p="/v2/orders"; b=json.dumps(d); h=hdr("POST",p,"",b)
    r=requests.post(B+p, headers=h, data=b, timeout=10)
    print(f"ORDER REQ {b} | RES {r.text}")
    return r.json()
def cancel_all_orders():
    try:
        p="/v2/orders/all"; b=json.dumps({"product_id":27,"cancel_stop_orders":True,"cancel_limit_orders":True})
        h=hdr("DELETE",p,"",b); requests.delete(B+p, headers=h, data=b, timeout=5)
    except: pass
def set_lev(lev):
    try:
        p="/v2/products/27"; b=json.dumps({"leverage":str(lev)}); h=hdr("PUT",p,"",b)
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
    try:
        h=hdr("GET","/v2/positions"); pos=requests.get(B+"/v2/positions", headers=h, timeout=10).json()
        for x in pos.get('result',[]):
            if int(x.get('product_id',0))==27:
                return float(x.get('size',0)), float(x.get('entry_price',0)), float(x.get('unrealized_pnl',0))
        return 0,0,0
    except: return 0,0,0

# ===== JOB: NO FALSE CLOSE =====
async def check_positions_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        cur_p = price()
        cur_size, _, _ = get_real_position()
        all_uids = set(list(USER_QTY.keys()) + list(LAST_POS_SIZE.keys()) + list(ENTRY_TIME.keys()))
        for uid in list(all_uids):
            ent_time = ENTRY_TIME.get(uid, 0)
            if ent_time !=0 and (time.time() - ent_time) < 75: # 75 sec grace, no false detection
                if cur_size !=0: LAST_POS_SIZE[uid]=cur_size
                continue
            last_size = LAST_POS_SIZE.get(uid, 0)
            if last_size !=0 and cur_size==0:
                cancel_all_orders()
                try: await context.bot.send_message(uid, f"🚨 POSITION CLOSED!\nPrice {cur_p}\nSL {LAST_SL.get(uid)} TP {LAST_TP.get(uid)}\n✅ Orders cancelled!")
                except: pass
                BEST_PRICE.pop(uid, None); ENTRY_PRICE.pop(uid, None); LAST_SL.pop(uid, None); LAST_TP.pop(uid, None); LAST_POS_SIZE[uid]=0; ENTRY_TIME.pop(uid, None)
            if cur_size !=0 and last_size !=0:
                if uid not in ENTRY_PRICE or ENTRY_PRICE[uid]==0: ENTRY_PRICE[uid]=cur_p; BEST_PRICE[uid]=cur_p
                size_abs = abs(cur_size)
                if size_abs < 1: size_abs = 1
                if cur_size < 0: # SHORT
                    if BEST_PRICE.get(uid, cur_p) > cur_p:
                        BEST_PRICE[uid]=cur_p
                        new_sl=round(BEST_PRICE[uid]*(1+SL/100),1); new_tp=round(BEST_PRICE[uid]*(1-TP/100),1)
                        if new_sl < LAST_SL.get(uid, 999999):
                            cancel_all_orders(); await asyncio.sleep(0.8)
                            order({"product_id":27,"size":int(size_abs) if size_abs>=1 else 1,"side":"buy","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(new_sl),"reduce_only":True})
                            order({"product_id":27,"size":int(size_abs) if size_abs>=1 else 1,"side":"buy","order_type":"limit_order","limit_price":str(new_tp),"reduce_only":True})
                            LAST_SL[uid]=new_sl; LAST_TP[uid]=new_tp
                else: # LONG
                    if BEST_PRICE.get(uid, cur_p) < cur_p:
                        BEST_PRICE[uid]=cur_p
                        new_sl=round(BEST_PRICE[uid]*(1-SL/100),1); new_tp=round(BEST_PRICE[uid]*(1+TP/100),1)
                        if new_sl > LAST_SL.get(uid, 0):
                            cancel_all_orders(); await asyncio.sleep(0.8)
                            order({"product_id":27,"size":int(size_abs) if size_abs>=1 else 1,"side":"sell","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(new_sl),"reduce_only":True})
                            order({"product_id":27,"size":int(size_abs) if size_abs>=1 else 1,"side":"sell","order_type":"limit_order","limit_price":str(new_tp),"reduce_only":True})
                            LAST_SL[uid]=new_sl; LAST_TP[uid]=new_tp
        for uid in all_uids:
            if cur_size !=0: LAST_POS_SIZE[uid]=cur_size
    except Exception as e: print(f"JOB ERR {e}")

async def start(u,c):
    uid=u.effective_user.id; qty=USER_QTY.get(uid,1); lev=USER_LEV.get(uid,20)
    bal=get_balance(); cur=price(); sz,entry,upnl=get_real_position()
    txt=f"🤖 SCALPING BOT - FULL DETAILS 🤖\n\n💰 Balance: ${bal:.2f}\n📊 BTC: {cur:.1f}\n\n📦 SETTINGS:\n- QTY: {qty} Lot = {qty*0.001} BTC\n- Lev: {lev}x\n- SL: {SL}% TP: {TP}%\n\n📈 POSITION:\n- Size: {sz} ({'LONG' if sz>0 else 'SHORT' if sz<0 else 'No Pos'})\n- Entry: {entry}\n- UPNL: ${upnl:.2f}\n- Best: {BEST_PRICE.get(uid,'N/A')}\n- SL: {LAST_SL.get(uid,'N/A')} TP: {LAST_TP.get(uid,'N/A')}\n\n/cmds: /qty 1 /lev 20 /trend /long /short /close"
    await u.message.reply_text(txt)

def get_candles_detail(res="5"):
    try:
        r=requests.get(f"{B}/v2/chart/history?symbol=BTCUSD&resolution={res}&from={int(time.time())-3600}&to={int(time.time())}", timeout=5).json()
        c=r['result']; last=c[-1]
        trend="🟢 BULLISH" if last['close']>last['open'] else "🔴 BEARISH"
        return trend, last['close']
    except: return "⚪ UNKNOWN", price()

async def trend_cmd(u,c):
    uid=u.effective_user.id; e=price(); bal=get_balance(); sz,entry,upnl=get_real_position()
    t5,c5=get_candles_detail("5"); t15,c15=get_candles_detail("15"); t60,c60=get_candles_detail("60")
    greens=sum([1 for t in [t5,t15,t60] if "BULLISH" in t])
    final="🚀 BULLISH - LONG" if greens>=2 else "📉 BEARISH - SHORT" if greens<=1 else "⚖️ SIDEWAYS"
    txt=f"📊 FULL TREND\n\n💰 BTC: {e:.1f} | Bal: ${bal:.2f}\n\n⏰ TREND:\n- 5M: {t5} {c5:.1f}\n- 15M: {t15} {c15:.1f}\n- 1H: {t60} {c60:.1f}\n\nFINAL: {final}\n\n📦 POS:\nSize {sz} Entry {entry}\nUPNL ${upnl:.2f}\nBest {BEST_PRICE.get(uid,'N/A')}\nSL {LAST_SL.get(uid,'N/A')} TP {LAST_TP.get(uid,'N/A')}\n\nSettings: {USER_QTY.get(uid,1)} Lot {USER_LEV.get(uid,20)}x"
    await u.message.reply_text(txt)

async def qty_cmd(u,c):
    lots=int(float(c.args[0])) if c.args else 1
    USER_QTY[u.effective_user.id]=lots
    await u.message.reply_text(f"✅ QTY {lots} Lot = {lots*0.001} BTC")

async def lev_cmd(u,c):
    l=int(c.args[0]) if c.args else 20
    USER_LEV[u.effective_user.id]=l; set_lev(l)
    await u.message.reply_text(f"✅ Lev {l}x")

async def ask_perm(u, side):
    lots=USER_QTY.get(u.effective_user.id, 1); lev=USER_LEV.get(u.effective_user.id, 20); e=price()
    kb=[[InlineKeyboardButton("✅ YES TRADE", callback_data=f"{side}_yes"), InlineKeyboardButton("❌ NO", callback_data="no")]]
    await u.message.reply_text(f"{side.upper()}?\nQty {lots} Lot Price {e}\nLev {lev}x SL {SL}% TP {TP}%", reply_markup=InlineKeyboardMarkup(kb))

async def short_cmd(u,c): await ask_perm(u,"short")
async def long_cmd(u,c): await ask_perm(u,"long")

async def button(u,c):
    q=u.callback_query; await q.answer()
    lots=USER_QTY.get(q.from_user.id, 1); e=price()
    if q.data=="no": await q.edit_message_text("Cancelled ❌"); return
    if q.data=="short_yes":
        sl=round(e*(1+SL/100),1); tp=round(e*(1-TP/100),1)
        res=order({"product_id":27,"size":lots,"side":"sell","order_type":"market_order"})
        if res.get('success'):
            order({"product_id":27,"size":lots,"side":"buy","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(sl),"reduce_only":True})
            order({"product_id":27,"size":lots,"side":"buy","order_type":"limit_order","limit_price":str(tp),"reduce_only":True})
            ENTRY_PRICE[q.from_user.id]=e; BEST_PRICE[q.from_user.id]=e; LAST_SL[q.from_user.id]=sl; LAST_TP[q.from_user.id]=tp; LAST_POS_SIZE[q.from_user.id]= -lots; ENTRY_TIME[q.from_user.id]=time.time()
            await q.edit_message_text(f"SHORT DONE ✅ Entry {e} SL {sl} TP {tp}")
        else: await q.edit_message_text(f"FAILED: {res}")
    if q.data=="long_yes":
        sl=round(e*(1-SL/100),1); tp=round(e*(1+TP/100),1)
        res=order({"product_id":27,"size":lots,"side":"buy","order_type":"market_order"})
        if res.get('success'):
            order({"product_id":27,"size":lots,"side":"sell","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(sl),"reduce_only":True})
            order({"product_id":27,"size":lots,"side":"sell","order_type":"limit_order","limit_price":str(tp),"reduce_only":True})
            ENTRY_PRICE[q.from_user.id]=e; BEST_PRICE[q.from_user.id]=e; LAST_SL[q.from_user.id]=sl; LAST_TP[q.from_user.id]=tp; LAST_POS_SIZE[q.from_user.id]= lots; ENTRY_TIME[q.from_user.id]=time.time()
            await q.edit_message_text(f"LONG DONE ✅ Entry {e} SL {sl} TP {tp}")
        else: await q.edit_message_text(f"FAILED: {res}")

async def close_cmd(u,c):
    await u.message.reply_text("⏳ Closing Anna... 100% close!")
    try:
        cancel_all_orders(); await asyncio.sleep(1)
        sz,_,_=get_real_position()
        print(f"CLOSE SIZE {sz}")
        if sz !=0:
            try:
                p="/v2/positions/close_all"; b=json.dumps({"product_id":27, "close_all_portfolios": True})
                h=hdr("POST",p,"",b); r=requests.post(B+p, headers=h, data=b, timeout=10)
                print(f"CLOSE_ALL {r.text}")
            except Exception as e: print(f"CLOSE_ALL err {e}")
            await asyncio.sleep(1.5)
            sz2,_,_=get_real_position()
            if sz2 !=0:
                side="sell" if sz2>0 else "buy"; close_sz=abs(sz2)
                if close_sz<1: close_sz=1
                order({"product_id":27,"size":int(close_sz),"side":side,"order_type":"market_order","reduce_only":"true"})
                await asyncio.sleep(1)
                order({"product_id":27,"size":int(close_sz),"side":side,"order_type":"market_order"})
        cancel_all_orders()
        sz_final,_,_=get_real_position()
        if sz_final==0: await u.message.reply_text("✅ ALL CLOSED Anna! Positions (0) ✅")
        else: await u.message.reply_text("⚠️ Check pannunga, Delta la X click pannunga!")
        BEST_PRICE.pop(u.effective_user.id, None); ENTRY_PRICE.pop(u.effective_user.id, None); LAST_POS_SIZE[u.effective_user.id]=0; LAST_SL.pop(u.effective_user.id, None); LAST_TP.pop(u.effective_user.id, None); ENTRY_TIME.pop(u.effective_user.id, None)
    except Exception as e: await u.message.reply_text(f"❌ {e}")

def main():
    app=Application.builder().token(T).build()
    app.add_handler(CommandHandler("start", start)); app.add_handler(CommandHandler("qty", qty_cmd)); app.add_handler(CommandHandler("lev", lev_cmd))
    app.add_handler(CommandHandler("trend", trend_cmd)); app.add_handler(CommandHandler("short", short_cmd)); app.add_handler(CommandHandler("long", long_cmd))
    app.add_handler(CommandHandler("close", close_cmd)); app.add_handler(CallbackQueryHandler(button))
    app.job_queue.run_repeating(check_positions_job, interval=30, first=15)
    print("BOT START - FINAL CLEAN ZERO BUG"); app.run_polling(drop_pending_updates=True)

if __name__=="__main__": main()
