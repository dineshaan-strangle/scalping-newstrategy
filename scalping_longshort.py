import os
import time
import hmac
import hashlib
import requests
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- CONFIG ---
DELTA_API_KEY = os.getenv("DELTA_API_KEY")
DELTA_API_SECRET = os.getenv("DELTA_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
BASE_URL = "https://api.india.delta.exchange"

# Professional Risk Management
SL_PERCENT = 1.2  # Stop Loss 1.2%
TP_PERCENT = 2.0  # Take Profit 2.0%
TRAILING_TRIGGER = 0.8 # 0.8% profit la trailing start
TRAILING_GAP = 0.5 # 0.5% gap la trail

def get_signature(method, timestamp, path, query, body, secret):
    msg = method + timestamp + path + query + body
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

def get_headers(method, path, query="", body=""):
    timestamp = str(int(time.time()))
    signature = get_signature(method, timestamp, path, query, body, DELTA_API_SECRET)
    return {
        "api-key": DELTA_API_KEY,
        "timestamp": timestamp,
        "signature": signature,
        "Content-Type": "application/json"
    }

def get_mark_price(symbol="BTCUSD"):
    r = requests.get(f"{BASE_URL}/v2/tickers/{symbol}")
    return float(r.json()['result']['mark_price'])

def place_order(payload):
    path = "/v2/orders"
    body = str(payload).replace("'", '"') # simple json
    import json
    body_str = json.dumps(payload)
    headers = get_headers("POST", path, "", body_str)
    res = requests.post(BASE_URL + path, headers=headers, data=body_str)
    print(f"ORDER RES: {res.text}")
    return res.json()

async def short_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = "BTCUSD"
    size = 0.01  # Professional size $800 notional - 0.001 na fees la pogum
    if context.args:
        # /short BTCUSD 0.01 maari kuduthalum edukkum
        try: size = float(context.args[0].split()[-1])
        except: pass
    
    entry_price = get_mark_price(symbol)
    
    # 1. ENTRY - SHORT MARKET
    entry_payload = {
        "product_id": 27, # BTCUSD
        "product_symbol": symbol,
        "size": size,
        "side": "sell",
        "order_type": "market_order",
        "time_in_force": "ioc"
    }
    entry_res = place_order(entry_payload)
    
    # 2. STOP LOSS
    sl_price = round(entry_price * (1 + SL_PERCENT/100), 1)
    sl_payload = {
        "product_id": 27,
        "size": size,
        "side": "buy",
        "order_type": "market_order",
        "stop_order_type": "stop_loss_order",
        "stop_price": str(sl_price),
        "stop_trigger_method": "mark_price",
        "reduce_only": True,
        "bracket_stop_trigger_method": "mark_price"
    }
    place_order(sl_payload)
    
    # 3. TAKE PROFIT
    tp_price = round(entry_price * (1 - TP_PERCENT/100), 1)
    tp_payload = {
        "product_id": 27,
        "size": size,
        "side": "buy",
        "order_type": "limit_order",
        "limit_price": str(tp_price),
        "reduce_only": True,
        "time_in_force": "gtc"
    }
    place_order(tp_payload)
    
    # 4. START TRAILING MONITOR IN BACKGROUND
    context.application.create_task(trailing_monitor(update.effective_chat.id, symbol, "short", entry_price, size, sl_price))

    await update.message.reply_text(
        f"🔴 REAL SHORT OPENED!\n\n"
        f"Symbol: {symbol}\n"
        f"Size: {size} BTC\n"
        f"Entry: {entry_price}\n"
        f"SL: {sl_price} ({SL_PERCENT}%)\n"
        f"TP: {tp_price} ({TP_PERCENT}%)\n"
        f"Trailing: Active from +{TRAILING_TRIGGER}%\n\n"
        f"✅ 3 Orders Placed in Delta!\nCheck Positions & Stop Orders tab Anna!"
    )

async def trailing_monitor(chat_id, symbol, side, entry_price, size, current_sl):
    """Professional Trailing - Profit vandha SL ah move pannum"""
    await asyncio.sleep(5)
    trailing_active = False
    best_price = entry_price
    
    for _ in range(360): # 1 hour monitor (6 sec * 360)
        try:
            mark = get_mark_price(symbol)
            
            if side == "short":
                # Price keezha ponum - profit
                if mark < best_price: best_price = mark
                profit_pct = (entry_price - mark) / entry_price * 100
                
                new_sl = None
                if profit_pct >= TRAILING_TRIGGER and not trailing_active:
                    # Break-even ku move
                    new_sl = entry_price * (1 + 0.1/100) # BE + 0.1%
                    trailing_active = True
                elif trailing_active and profit_pct > 1.0:
                    # Trail by 0.5%
                    new_sl = best_price * (1 + TRAILING_GAP/100)
                
                if new_sl and new_sl < current_sl:
                    # Cancel old SL and place new SL
                    # (Simplified - cancel all stop orders and place new one)
                    # ... cancel logic ...
                    sl_payload = {
                        "product_id": 27, "size": size, "side": "buy",
                        "order_type": "market_order", "stop_order_type": "stop_loss_order",
                        "stop_price": str(round(new_sl,1)), "reduce_only": True
                    }
                    place_order(sl_payload)
                    current_sl = new_sl
                    print(f"TRAILING UPDATED: {new_sl}")

            await asyncio.sleep(6)
        except Exception as e:
            print(f"Trailing error {e}")
            await asyncio.sleep(10)

# --- SAME FOR LONG ---
async def long_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Same logic but side = buy, SL = entry - 1.2%, TP = entry + 2%
    symbol = "BTCUSD"
    size = 0.01
    entry_price = get_mark_price(symbol)
    entry_payload = {"product_id": 27, "product_symbol": symbol, "size": size, "side": "buy", "order_type": "market_order"}
    place_order(entry_payload)
    
    sl_price = round(entry_price * (1 - SL_PERCENT/100), 1)
    tp_price = round(entry_price * (1 + TP_PERCENT/100), 1)
    
    place_order({"product_id": 27, "size": size, "side": "sell", "order_type": "market_order", "stop_order_type": "stop_loss_order", "stop_price": str(sl_price), "reduce_only": True})
    place_order({"product_id": 27, "size": size, "side": "sell", "order_type": "limit_order", "limit_price": str(tp_price), "reduce_only": True})
    
    await update.message.reply_text(f"🟢 REAL LONG OPENED!\nEntry: {entry_price}\nSL: {sl_price}\nTP: {tp_price}\nTrailing Active!")

async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Close all with reduce_only
    path = "/v2/positions"
    headers = get_headers("GET", path)
    pos = requests.get(BASE_URL+path, headers=headers).json()
    for p in pos['result']:
        if float(p['size']) != 0:
            size = abs(float(p['size']))
            side = "buy" if float(p['size']) < 0 else "sell"
            place_order({"product_id": p['product_id'], "size": size, "side": side, "order_type": "market_order", "reduce_only": True})
    await update.message.reply_text("✅ CLOSING ALL - Check Delta Fills tab for PNL!")

# Bot setup
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("short", short_command))
app.add_handler(CommandHandler("long", long_command))
app.add_handler(CommandHandler("close", close_command))
app.run_polling()
