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

# NEW GLOBALS FOR TRAILING & ALERT
BEST_PRICE = {}  # user_id -> best price
ENTRY_PRICE = {} # user_id -> entry
LAST_POS_SIZE = {} # user_id -> last size
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

# ========== NEW: BACKGROUND JOB FOR TRAILING + SL HIT ==========
async def check_positions_job(context: ContextTypes.DEFAULT_TYPE):
    global BEST_PRICE, ENTRY_PRICE, LAST_POS_SIZE
    try:
        h=hdr("GET","/v2/positions")
        pos=requests.get(B+"/v2/positions", headers=h, timeout=10).json()
        cur_p = price()
        
        current_size = 0
        entry_p = 0
        for x in pos.get('result',[]):
            if int(x.get('product_id',0)) == 27:
                current_size = float(x.get('size',0))
                entry_p = float(x.get('entry_price',0))
                break
        
        # Check for all users who traded
        for uid in list(USER_QTY.keys()):
            last_size = LAST_POS_SIZE.get(uid, 0)
            
            # --- 2. SL HIT ALERT ---
            if last_size != 0 and current_size == 0:
                # Position closed - check if SL hit
                last_sl = LAST_SL.get(uid, 0)
                # Simple logic: if closed, send alert
                try:
                    await context.bot.send_message(uid, f"🚨 POSITION CLOSED!\nLast size {last_size}\nPrice {cur_p}\nEntry {entry_p}\n\nSL was {last_sl} TP was {LAST_TP.get(uid,0)}\n\nCheck Delta app!")
                except: pass
                BEST_PRICE.pop(uid, None)
                ENTRY_PRICE.pop(uid, None)
            
            # --- 3. TRAILING SL + TP ---
            if current_size != 0:
                # Set entry if new
                if uid not in ENTRY_PRICE or ENTRY_PRICE[uid]==0:
                    ENTRY_PRICE[uid] = entry_p
                    BEST_PRICE[uid] = entry_p
                
                # Update BEST PRICE
                if current_size < 0: # SHORT - best is lowest price
                    if BEST_PRICE.get(uid, cur_p) > cur_p:
                        BEST_PRICE[uid] = cur_p
                        # TRAILING - move SL & TP
                        new_sl = round(BEST_PRICE[uid] * (1 + SL/100), 1)
                        new_tp = round(BEST_PRICE[uid] * (1 - TP/100), 1)
                        
                        # Only trail if better than old SL (lower for short)
                        if new_sl < LAST_SL.get(uid, 999999):
                            print(f"TRAILING SHORT: New SL {new_sl} TP {new_tp} Best {BEST_PRICE[uid]}")
                            cancel_all_orders()
                            await asyncio.sleep(0.5)
                            # Place new SL/TP
                            order({"product_id":27,"size":abs(int(current_size)),"side":"buy","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(new_sl),"reduce_only":True})
                            order({"product_id":27,"size":abs(int(current_size)),"side":"buy","order_type":"limit_order","limit_price":str(new_tp),"reduce_only":True})
                            LAST_SL[uid] = new_sl
                            LAST_TP[uid] = new_tp
                            try:
                                await context.bot.send_message(uid, f"📈 TRAILING UPDATE (SHORT)\nBest Price: {BEST_PRICE[uid]}\nNew SL: {new_sl} (locked profit!)\nNew TP: {new_tp}")
                            except: pass
                
                else: # LONG - best is highest price
                    if BEST_PRICE.get(uid, cur_p) < cur_p:
                        BEST_PRICE[uid] = cur_p
                        new_sl = round(BEST_PRICE[uid] * (1 - SL/100), 1)
                        new_tp = round(BEST_PRICE[uid] * (1 + TP/100), 1)
                        
                        if new_sl > LAST_SL.get(uid, 0):
                            print(f"TRAILING LONG: New SL {new_sl} TP {new_tp} Best {BEST_PRICE[uid]}")
                            cancel_all_orders()
                            await asyncio.sleep(0.5)
                            order({"product_id":27,"size":abs(int(current_size)),"side":"sell","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(new_sl),"reduce_only":True})
                            order({"product_id":27,"size":abs(int(current_size)),"side":"sell","order_type":"limit_order","limit_price":str(new_tp),"reduce_only":True})
                            LAST_SL[uid] = new_sl
                            LAST_TP[uid] = new_tp
                            try:
                                await context.bot.send_message(uid, f"📈 TRAILING UPDATE (LONG)\nBest Price: {BEST_PRICE[uid]}\nNew SL: {new_sl} (locked profit!)\nNew TP: {new_tp}")
                            except: pass
            
            LAST_POS_SIZE[uid] = current_size
            
    except Exception as e:
        print(f"JOB ERROR: {e}")

# COMMANDS
async def start(u,c):
    await u.message.reply_text("BOT READY ✅\n\n1 Lot = 0.001 BTC\n\n/qty 1 = 0.001 BTC\n/qty 5 = 0.005 BTC\n/lev 20\n/trend - Trend paarkka\n/long - Long\n/short - Short\n/close - All close\n\nNEW: Trailing SL/TP ON + SL Hit Alert ON")

async def qty_cmd(u,c):
    lots = int(float(c.args[0])) if c.args else 1
    USER_QTY[u.effective_user.id] = lots
    btc = lots * 0.001
    await u.message.reply_text(f"✅ QTY SET\n\n{lots} Lot = {btc} BTC")

async def lev_cmd(u,c):
    l=int(c.args[0]) if c.args else 20
    USER_LEV[u.effective_user.id]=l
    set_lev(l)
    await u.message.reply_text(f"✅ Leverage: {l}x set")

async def trend_cmd(u,c):
    e=price()
    g5=get_candles("5")
    g15=get_candles("15")
    g60=get_candles("60")
    greens=sum([g5,g15,g60])
    if greens>=2:
        trend="BULLISH"; safe="90%" if greens==3 else "70%"; dec="LONG is good - /long"
    else:
        trend="BEARISH"; safe="90%" if greens==0 else "70%"; dec="SHORT is good - /short"
    col="GREEN" if g5 else "RED"
    await u.message.reply_text(f"BTCUSD TREND REPORT\n\nPrice: {e}\nLast 5m: {col}\n\nTrend: {trend}\nSafety: {safe} SAFE\n\nDECISION:\n{dec}")

async def ask_perm(u, side):
    lots=USER_QTY.get(u.effective_user.id, 1)
    lev=USER_LEV.get(u.effective_user.id, 20)
    e=price()
    btc = lots * 0.001
    kb=[[InlineKeyboardButton("✅ YES TRADE", callback_data=f"{side}_yes"), InlineKeyboardButton("❌ NO", callback_data="no")]]
    txt=f"{side.upper()} Permission?\n\nQty: {lots} Lot\n= {btc} BTC\nPrice: {e}\nLev: {lev}x\nSL {SL}% TP {TP}% + TRAILING ON"
    await u.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))

async def short_cmd(u,c): await ask_perm(u,"short")
async def long_cmd(u,c): await ask_perm(u,"long")

async def button(u,c):
    q=u.callback_query
    await q.answer()
    lots=USER_QTY.get(q.from_user.id, 1)
    btc = lots * 0.001
    e=price()
    if q.data=="no":
        await q.edit_message_text("Cancelled ❌"); return
    if q.data=="short_yes":
        sl=round(e*(1+SL/100),1); tp=round(e*(1-TP/100),1)
        res=order({"product_id":27,"size":lots,"side":"sell","order_type":"market_order"})
        if res.get('success'):
            order({"product_id":27,"size":lots,"side":"buy","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(sl),"reduce_only":True})
            order({"product_id":27,"size":lots,"side":"buy","order_type":"limit_order","limit_price":str(tp),"reduce_only":True})
            # SAVE FOR TRAILING
            ENTRY_PRICE[q.from_user.id]=e
            BEST_PRICE[q.from_user.id]=e
            LAST_SL[q.from_user.id]=sl
            LAST_TP[q.from_user.id]=tp
            LAST_POS_SIZE[q.from_user.id]= -lots
            await q.edit_message_text(f"SHORT DONE ✅\n{lots} Lot = {btc} BTC\nEntry {e}\nSL {sl}\nTP {tp}\n\n🔄 Trailing ON - Profit lock aagum!")
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
            await q.edit_message_text(f"LONG DONE ✅\n{lots} Lot = {btc} BTC\nEntry {e}\nSL {sl}\nTP {tp}\n\n🔄 Trailing ON - Profit lock aagum!")
        else:
            await q.edit_message_text(f"FAILED: {res}")

# ========== 1. CLOSE FIX ==========
async def close_cmd(u,c):
    await u.message.reply_text("⏳ Closing... (Cancel orders first)")
    try:
        # STEP 1: Cancel SL/TP first
        cancel_all_orders()
        await asyncio.sleep(1)
        
        # STEP 2: Close position with market + reduce_only
        h2=hdr("GET","/v2/positions")
        pos=requests.get(B+"/v2/positions", headers=h2, timeout=5).json()
        closed=False
        for x in pos.get('result',[]):
            if float(x.get('size',0))!=0 and int(x.get('product_id',0))==27:
                sz=float(x['size'])
                side="buy" if sz<0 else "sell"
                res=order({"product_id":27,"size":abs(int(sz)),"side":side,"order_type":"market_order","reduce_only":True})
                print(f"CLOSE RES: {res}")
                closed=True
        
        if closed:
            await u.message.reply_text("✅ ALL CLOSED - Position + Orders closed!")
            # Reset
            BEST_PRICE.pop(u.effective_user.id, None)
            ENTRY_PRICE.pop(u.effective_user.id, None)
        else:
            await u.message.reply_text("No position to close Anna!")
    except Exception as e:
        await u.message.reply_text(f"❌ Close Error: {e}")

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
    
    # START JOB - 30 sec check
    app.job_queue.run_repeating(check_positions_job, interval=30, first=10)
    
    print("BOT START - 1 Lot = 0.001 BTC - TRAILING + SL ALERT ON")
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
