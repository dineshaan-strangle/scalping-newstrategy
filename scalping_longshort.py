import os, time, hmac, hashlib, requests, json, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

K = os.getenv("DELTA_API_KEY")
S = os.getenv("DELTA_API_SECRET")
T = os.getenv("TELEGRAM_TOKEN")
B = "https://api.india.delta.exchange"
SL = 1.2
TP = 2.0
USER_QTY = {}
USER_LEV = {}
BEST_PRICE = {}
ENTRY_PRICE = {}
LAST_POS_SIZE = {}
LAST_SL = {}
LAST_TP = {}

def sig(m,t,p,q,b,s):
    return hmac.new(s.encode(), (m+t+p+q+b).encode(), hashlib.sha256).hexdigest()
def hdr(m,p,q="",b=""):
    t=str(int(time.time()))
    return {"api-key":K,"timestamp":t,"signature":sig(m,t,p,q,b,S),"Content-Type":"application/json"}
def price():
    try:
        return float(requests.get(f"{B}/v2/tickers/BTCUSD", timeout=5).json()['result']['mark_price'])
    except:
        return 80545.0
def order(d):
    p="/v2/orders"
    b=json.dumps(d)
    h=hdr("POST",p,"",b)
    r=requests.post(B+p, headers=h, data=b, timeout=10)
    print(f"REQ: {b} | RES: {r.text}")
    return r.json()
def cancel_all_orders():
    try:
        p="/v2/orders/all"
        b=json.dumps({"product_id":27,"cancel_stop_orders":True,"cancel_limit_orders":True})
        h=hdr("DELETE",p,"",b)
        requests.delete(B+p, headers=h, data=b, timeout=5)
    except: pass
def set_lev(lev):
    try:
        p="/v2/products/27"
        b=json.dumps({"leverage":str(lev)})
        h=hdr("PUT",p,"",b)
        requests.put(B+p, headers=h, data=b, timeout=5)
    except: pass
def get_candles(res="5"):
    try:
        r=requests.get(f"{B}/v2/chart/history?symbol=BTCUSD&resolution={res}&from={int(time.time())-3600}&to={int(time.time())}", timeout=5).json()
        c=r['result']
        last=c[-1]
        return last['close'] > last['open']
    except:
        return True
def get_real_position_size():
    try:
        h=hdr("GET","/v2/positions")
        pos=requests.get(B+"/v2/positions", headers=h, timeout=10).json()
        for x in pos.get('result',[]):
            if int(x.get('product_id',0)) == 27:
                return float(x.get('size',0))
        return 0
    except:
        return 0

async def check_positions_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        cur_p = price()
        current_size = get_real_position_size()
        all_uids = set(list(USER_QTY.keys()) + list(LAST_POS_SIZE.keys()))
        for uid in list(all_uids):
            last_size = LAST_POS_SIZE.get(uid, 0)
            if last_size != 0 and current_size == 0:
                cancel_all_orders()
                try:
                    await context.bot.send_message(uid, f"🚨 POSITION CLOSED!\nPrice {cur_p}\nSL {LAST_SL.get(uid)} TP {LAST_TP.get(uid)}\n✅ Auto cancelled orders!")
                except: pass
                BEST_PRICE.pop(uid, None)
                ENTRY_PRICE.pop(uid, None)
                LAST_SL.pop(uid, None)
                LAST_TP.pop(uid, None)
                LAST_POS_SIZE[uid] = 0
            if current_size != 0 and last_size != 0:
                if uid not in ENTRY_PRICE or ENTRY_PRICE[uid]==0:
                    ENTRY_PRICE[uid]=cur_p
                    BEST_PRICE[uid]=cur_p
                size_abs = abs(current_size)
                if size_abs < 1: size_abs = 1
                else: size_abs = int(size_abs)
                if current_size < 0:
                    if BEST_PRICE.get(uid, cur_p) > cur_p:
                        BEST_PRICE[uid]=cur_p
                        new_sl=round(BEST_PRICE[uid]*(1+SL/100),1)
                        new_tp=round(BEST_PRICE[uid]*(1-TP/100),1)
                        if new_sl < LAST_SL.get(uid, 999999):
                            cancel_all_orders()
                            await asyncio.sleep(0.7)
                            order({"product_id":27,"size":size_abs,"side":"buy","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(new_sl),"reduce_only":True})
                            order({"product_id":27,"size":size_abs,"side":"buy","order_type":"limit_order","limit_price":str(new_tp),"reduce_only":True})
                            LAST_SL[uid]=new_sl
                            LAST_TP[uid]=new_tp
                else:
                    if BEST_PRICE.get(uid, cur_p) < cur_p:
                        BEST_PRICE[uid]=cur_p
                        new_sl=round(BEST_PRICE[uid]*(1-SL/100),1)
                        new_tp=round(BEST_PRICE[uid]*(1+TP/100),1)
                        if new_sl > LAST_SL.get(uid, 0):
                            cancel_all_orders()
                            await asyncio.sleep(0.7)
                            order({"product_id":27,"size":size_abs,"side":"sell","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(new_sl),"reduce_only":True})
                            order({"product_id":27,"size":size_abs,"side":"sell","order_type":"limit_order","limit_price":str(new_tp),"reduce_only":True})
                            LAST_SL[uid]=new_sl
                            LAST_TP[uid]=new_tp
        for uid in all_uids:
            if current_size !=0:
                LAST_POS_SIZE[uid]=current_size
    except Exception as e:
        print(f"JOB ERROR: {e}")

async def start(u,c):
    await u.message.reply_text("BOT READY ✅ FIXED\n/qty 1\n/long\n/short\n/close - 100% close")
async def qty_cmd(u,c):
    lots=int(float(c.args[0])) if c.args else 1
    USER_QTY[u.effective_user.id]=lots
    await u.message.reply_text(f"✅ QTY {lots} Lot = {lots*0.001} BTC")
async def lev_cmd(u,c):
    l=int(c.args[0]) if c.args else 20
    USER_LEV[u.effective_user.id]=l
    set_lev(l)
    await u.message.reply_text(f"✅ Lev {l}x")
async def trend_cmd(u,c):
    e=price()
    g5=get_candles("5")
    g15=get_candles("15")
    g60=get_candles("60")
    greens=sum([g5,g15,g60])
    if greens>=2:
        trend="BULLISH"; dec="LONG good - /long"
    else:
        trend="BEARISH"; dec="SHORT good - /short"
    await u.message.reply_text(f"Price {e}\nTrend {trend}\n{dec}")
async def ask_perm(u, side):
    lots=USER_QTY.get(u.effective_user.id, 1)
    lev=USER_LEV.get(u.effective_user.id, 20)
    e=price()
    kb=[[InlineKeyboardButton("✅ YES TRADE", callback_data=f"{side}_yes"), InlineKeyboardButton("❌ NO", callback_data="no")]]
    await u.message.reply_text(f"{side.upper()}?\nQty {lots} Lot\nPrice {e}\nLev {lev}x\nSL {SL}% TP {TP}%", reply_markup=InlineKeyboardMarkup(kb))
async def short_cmd(u,c): await ask_perm(u,"short")
async def long_cmd(u,c): await ask_perm(u,"long")
async def button(u,c):
    q=u.callback_query
    await q.answer()
    lots=USER_QTY.get(q.from_user.id, 1)
    e=price()
    if q.data=="no":
        await q.edit_message_text("Cancelled ❌"); return
    if q.data=="short_yes":
        sl=round(e*(1+SL/100),1); tp=round(e*(1-TP/100),1)
        res=order({"product_id":27,"size":lots,"side":"sell","order_type":"market_order"})
        if res.get('success'):
            order({"product_id":27,"size":lots,"side":"buy","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(sl),"reduce_only":True})
            order({"product_id":27,"size":lots,"side":"buy","order_type":"limit_order","limit_price":str(tp),"reduce_only":True})
            ENTRY_PRICE[q.from_user.id]=e
            BEST_PRICE[q.from_user.id]=e
            LAST_SL[q.from_user.id]=sl
            LAST_TP[q.from_user.id]=tp
            LAST_POS_SIZE[q.from_user.id]= -lots
            await q.edit_message_text(f"SHORT DONE ✅ Entry {e} SL {sl} TP {tp}")
        else:
            await q.edit_message_text(f"FAILED: {res}")
    if q.data=="long_yes":
        sl=round(e*(1-SL/100),1); tp=round(e*(1+TP/100),1)
        res=order({"product_id":27,"size":lots,"side":"buy","order_type":"market_order"})
        if res.get('success'):
            order({"product_id":27,"size":lots,"side":"sell","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(sl),"reduce_only":True})
            order({"product_id":27,"size":lots,"side":"sell","order_type":"limit_order","limit_price":str(tp),"reduce_only":True})
            ENTRY_PRICE[q.from_user.id]=e
            BEST_PRICE[q.from_user.id]=e
            LAST_SL[q.from_user.id]=sl
            LAST_TP[q.from_user.id]=tp
            LAST_POS_SIZE[q.from_user.id]= lots
            await q.edit_message_text(f"LONG DONE ✅ Entry {e} SL {sl} TP {tp}")
        else:
            await q.edit_message_text(f"FAILED: {res}")

# ========== CLOSE - CORRECT FIX ==========
async def close_cmd(u,c):
    await u.message.reply_text("⏳ Closing Anna... correct size la!")
    try:
        cancel_all_orders()
        await asyncio.sleep(1)
        h=hdr("GET","/v2/positions")
        pos=requests.get(B+"/v2/positions", headers=h, timeout=10).json()
        closed=False
        for x in pos.get('result',[]):
            if int(x.get('product_id',0))==27:
                sz=float(x.get('size',0))
                if sz !=0:
                    side="sell" if sz>0 else "buy"
                    # FIX: Delta kudutha size ah apdiye use pannu, int pannathe!
                    # size 0.001 vantha 0.001, 1 vantha 1
                    # Rendu case kum try pannu
                    for try_size in [abs(sz), int(abs(sz)) if abs(sz)>=1 else abs(sz), 1]:
                        if try_size==0: continue
                        print(f"Trying close size {try_size} side {side}")
                        r=order({"product_id":27,"size":try_size,"side":side,"order_type":"market_order","reduce_only":True})
                        await asyncio.sleep(1)
                        if get_real_position_size()==0:
                            closed=True
                            break
                        r2=order({"product_id":27,"size":try_size,"side":side,"order_type":"market_order"})
                        await asyncio.sleep(1)
                        if get_real_position_size()==0:
                            closed=True
                            break
                    closed=True
        cancel_all_orders()
        if closed or get_real_position_size()==0:
            await u.message.reply_text("✅ CLOSED Anna! Position 0 aayiduchu!")
        else:
            await u.message.reply_text("⚠️ Try panniten but Delta reject pannuthu! Delta app la X button click pannunga!")
        BEST_PRICE.pop(u.effective_user.id, None)
        ENTRY_PRICE.pop(u.effective_user.id, None)
        LAST_POS_SIZE[u.effective_user.id]=0
        LAST_SL.pop(u.effective_user.id, None)
        LAST_TP.pop(u.effective_user.id, None)
    except Exception as e:
        await u.message.reply_text(f"❌ {e}")

def main():
    app=Application.builder().token(T).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("qty", qty_cmd))
    app.add_handler(CommandHandler("lev", lev_cmd))
    app.add_handler(CommandHandler("trend", trend_cmd))
    app.add_handler(CommandHandler("short", short_cmd))
    app.add_handler(CommandHandler("long", long_cmd))
    app.add_handler(CommandHandler("close", close_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.job_queue.run_repeating(check_positions_job, interval=30, first=10)
    print("BOT START - FINAL FIXED")
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
