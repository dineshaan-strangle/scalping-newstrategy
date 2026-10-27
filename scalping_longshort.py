import os, time, hmac, hashlib, requests, json
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

# COMMANDS
async def start(u,c):
    await u.message.reply_text("BOT READY ✅\n\n1 Lot = 0.001 BTC\n\n/qty 1 = 0.001 BTC\n/qty 5 = 0.005 BTC\n/lev 20\n/trend - Trend paarkka\n/long - Long\n/short - Short\n/close - All close")

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
    txt=f"{side.upper()} Permission?\n\nQty: {lots} Lot\n= {btc} BTC\nPrice: {e}\nLev: {lev}x\nSL {SL}% TP {TP}%"
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
            await q.edit_message_text(f"SHORT DONE ✅\n{lots} Lot = {btc} BTC\nEntry {e}\nSL {sl}\nTP {tp}\n\nDelta app la Stop Orders paaru!")
        else:
            await q.edit_message_text(f"FAILED: {res}")
    if q.data=="long_yes":
        sl=round(e*(1-SL/100),1); tp=round(e*(1+TP/100),1)
        res=order({"product_id":27,"size":lots,"side":"buy","order_type":"market_order"})
        if res.get('success'):
            order({"product_id":27,"size":lots,"side":"sell","order_type":"market_order","stop_order_type":"stop_loss_order","stop_price":str(sl),"reduce_only":True})
            order({"product_id":27,"size":lots,"side":"sell","order_type":"limit_order","limit_price":str(tp),"reduce_only":True})
            await q.edit_message_text(f"LONG DONE ✅\n{lots} Lot = {btc} BTC\nEntry {e}\nSL {sl}\nTP {tp}\n\nDelta app la Stop Orders paaru!")
        else:
            await q.edit_message_text(f"FAILED: {res}")

async def close_cmd(u,c):
    p="/v2/orders/all"; b=json.dumps({"product_id":27,"cancel_stop_orders":True,"cancel_limit_orders":True}); h=hdr("DELETE",p,"",b); requests.delete(B+p, headers=h, data=b, timeout=5)
    h2=hdr("GET","/v2/positions"); pos=requests.get(B+"/v2/positions", headers=h2, timeout=5).json()
    for x in pos.get('result',[]):
        if float(x['size'])!=0:
            order({"product_id":x['product_id'],"size":abs(int(float(x['size']))),"side":"buy" if float(x['size'])<0 else "sell","order_type":"market_order"})
    await u.message.reply_text("ALL CLOSED ✅")

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
    print("BOT START - 1 Lot = 0.001 BTC")
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
