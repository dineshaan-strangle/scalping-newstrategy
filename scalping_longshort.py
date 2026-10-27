import os
import time
import hmac
import hashlib
import requests
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- CONFIG ---
K = os.getenv("DELTA_API_KEY")
S = os.getenv("DELTA_API_SECRET")
T = os.getenv("TELEGRAM_TOKEN")
B = "https://api.india.delta.exchange"

SL = 1.2
TP = 2.0
USER_QTY = {}
USER_LEV = {}

# --- DELTA HELPERS ---
def sig(m, t, p, q, b, s):
    return hmac.new(s.encode(), (m + t + p + q + b).encode(), hashlib.sha256).hexdigest()

def hdr(m, p, q="", b=""):
    t = str(int(time.time()))
    return {"api-key": K, "timestamp": t, "signature": sig(m, t, p, q, b, S), "Content-Type": "application/json"}

def price(s="BTCUSD"):
    return float(requests.get(f"{B}/v2/tickers/{s}").json()['result']['mark_price'])

def order(d):
    p = "/v2/orders"
    b = json.dumps(d)
    h = hdr("POST", p, "", b)
    r = requests.post(B + p, headers=h, data=b)
    print(r.text)
    return r.json()

def get_candles(resolution="5"):
    try:
        r = requests.get(f"{B}/v2/chart/history?symbol=BTCUSD&resolution={resolution}&from=0&to=9999999999").json()
        c = r['result']
        last = c[-1]
        return last['close'] > last['open']
    except:
        return False

def set_lev(lev=20):
    p = "/v2/products/27"
    b = json.dumps({"leverage": str(lev)})
    h = hdr("PUT", p, "", b)
    requests.put(B + p, headers=h, data=b)

# --- COMMANDS ---
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    txt = (
        "DELTA PRO BOT\n\n"
        "/start - Menu\n"
        "/trend - Market Trend Report\n"
        "/qty 0.01 - Set Quantity\n"
        "/lev 20 - Set Leverage\n"
        "/long - Long with Permission\n"
        "/short - Short with Permission\n"
        "/close - Close All"
    )
    await u.message.reply_text(txt)

async def trend(u: Update, c: ContextTypes.DEFAULT_TYPE):
    e = price()
    is_5m = get_candles("5")
    is_15m = get_candles("15")
    is_1h = get_candles("60")
    greens = sum([is_5m, is_15m, is_1h])

    if greens >= 2:
        trend_text = "BULLISH"
        safety = "90% SAFE" if greens == 3 else "70% SAFE"
        last5m = "GREEN" if is_5m else "RED"
        decision = "LONG is good - /long"
    else:
        trend_text = "BEARISH"
        safety = "90% SAFE" if greens == 0 else "70% SAFE"
        last5m = "GREEN" if is_5m else "RED"
        decision = "SHORT is good - /short"

    txt = (
        f"BTCUSD TREND REPORT\n\n"
        f"Price: {e}\n"
        f"Last 5m: {last5m}\n\n"
        f"Trend (5m+15m+1h): {trend_text}\n"
        f"Safety: {safety}\n\n"
        f"DECISION:\n{decision}"
    )
    await u.message.reply_text(txt)

async def qty_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = float(c.args[0]) if c.args else 0.01
    USER_QTY[u.effective_user.id] = q
    await u.message.reply_text(f"Qty Set: {q} BTC")

async def lev_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    l = int(c.args[0]) if c.args else 20
    USER_LEV[u.effective_user.id] = l
    set_lev(l)
    await u.message.reply_text(f"Leverage Set: {l}x")

async def ask_perm(u: Update, side):
    q = USER_QTY.get(u.effective_user.id, 0.01)
    l = USER_LEV.get(u.effective_user.id, 20)
    e = price()
    kb = [[InlineKeyboardButton("YES TRADE", callback_data=f"{side}_yes"), InlineKeyboardButton("NO", callback_data="no")]]
    txt = f"{side.upper()} Permission?\n\nPrice: {e}\nQty: {q}\nLev: {l}x\nSL: {SL}% TP: {TP}%\n\nConfirm?"
    await u.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))

async def short_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await ask_perm(u, "short")

async def long_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await ask_perm(u, "long")

async def button(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    await q.answer()
    uid = q.from_user.id
    qty = USER_QTY.get(uid, 0.01)
    e = price()

    if q.data == "no":
        await q.edit_message_text("Cancelled")
        return

    if q.data == "short_yes":
        sl = round(e * (1 + SL / 100), 1)
        tp = round(e * (1 - TP / 100), 1)
        order({"product_id": 27, "size": qty, "side": "sell", "order_type": "market_order"})
        order({"product_id": 27, "size": qty, "side": "buy", "order_type": "market_order", "stop_order_type": "stop_loss_order", "stop_price": str(sl), "reduce_only": True})
        order({"product_id": 27, "size": qty, "side": "buy", "order_type": "limit_order", "limit_price": str(tp), "reduce_only": True})
        await q.edit_message_text(f"SHORT DONE\nEntry {e}\nSL {sl}\nTP {tp}\nQty {qty}")

    if q.data == "long_yes":
        sl = round(e * (1 - SL / 100), 1)
        tp = round(e * (1 + TP / 100), 1)
        order({"product_id": 27, "size": qty, "side": "buy", "order_type": "market_order"})
        order({"product_id": 27, "size": qty, "side": "sell", "order_type": "market_order", "stop_order_type": "stop_loss_order", "stop_price": str(sl), "reduce_only": True})
        order({"product_id": 27, "size": qty, "side": "sell", "order_type": "limit_order", "limit_price": str(tp), "reduce_only": True})
        await q.edit_message_text(f"LONG DONE\nEntry {e}\nSL {sl}\nTP {tp}\nQty {qty}")

async def close_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    p = "/v2/orders/all"
    b = json.dumps({"product_id": 27, "cancel_stop_orders": True, "cancel_limit_orders": True})
    h = hdr("DELETE", p, "", b)
    requests.delete(B + p, headers=h, data=b)

    h2 = hdr("GET", "/v2/positions")
    pos = requests.get(B + "/v2/positions", headers=h2).json()
    for x in pos.get('result', []):
        if float(x['size']) != 0:
            order({"product_id": x['product_id'], "size": abs(float(x['size'])), "side": "buy" if float(x['size']) < 0 else "sell", "order_type": "market_order"})
    await u.message.reply_text("CLOSED ALL")

# --- BOT SETUP ---
app = Application.builder().token(T).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("trend", trend))
app.add_handler(CommandHandler("qty", qty_cmd))
app.add_handler(CommandHandler("lev", lev_cmd))
app.add_handler(CommandHandler("short", short_cmd))
app.add_handler(CommandHandler("long", long_cmd))
app.add_handler(CommandHandler("close", close_cmd))
app.add_handler(CallbackQueryHandler(button))
app.run_polling()
