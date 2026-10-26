from flask import Flask
import threading
import ccxt
import time
import requests
from datetime import datetime
import pytz
import os

# ========== FAKE WEB SERVER FOR RENDER ==========
app = Flask(__name__)

@app.route('/')
def home():
    return f"""
    <h2>V107 Bot Running ✅</h2>
    <p>Range: 09:30-13:30 IST</p>
    <p>Trade: 13:30-23:30 IST</p>
    <p>Time Now: {datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S IST')}</p>
    <p>Status: Active</p>
    """

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()
print("Flask server started on port 10000")
# ================================================

# ========== YOUR EXISTING BOT CODE ==========
IST = pytz.timezone('Asia/Kolkata')

SYMBOL = "BTC/USD"
LOTS = 5
LEVERAGE = 15
RANGE_START = "09:30"
RANGE_END = "13:30"
TRADE_START = "13:30"
TRADE_END = "23:30"
TP_RATIO = 2.0

TELEGRAM_TOKEN = "YOUR_TELEGRAM_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

exchange = ccxt.bitget({
    'apiKey': 'YOUR_API_KEY',
    'secret': 'YOUR_SECRET',
    'password': 'YOUR_PASSPHRASE',
    'enableRateLimit': True,
})

high_range = None
low_range = None

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_qty_from_lots(lots):
    return lots * 0.001

def is_trading_time():
    now_ist = datetime.now(IST).strftime("%H:%M")
    return TRADE_START <= now_ist <= TRADE_END

def fetch_4h_range():
    global high_range, low_range
    try:
        ohlcv = exchange.fetch_ohlcv(SYMBOL, "4h", limit=1)
        if ohlcv:
            high_range = ohlcv[-1][2]
            low_range = ohlcv[-1][3]
            print(f"Range Marked: High={high_range}, Low={low_range}")
            send_telegram(f"Range Marked\nHigh: {high_range}\nLow: {low_range}")
    except Exception as e:
        print(f"Range Error: {e}")

def check_false_breakout():
    global high_range, low_range
    if not high_range or not low_range:
        return
    if not is_trading_time():
        return
    try:
        candles = exchange.fetch_ohlcv(SYMBOL, "5m", limit=10)
        prev_close = candles[-2][4]
        curr_close = candles[-1][4]
        curr_high = candles[-1][2]
        curr_low = candles[-1][3]
        qty = get_qty_from_lots(LOTS)
        
        if prev_close > high_range and curr_close < high_range:
            sl = curr_high
            entry = curr_close
            tp = entry - ((sl - entry) * TP_RATIO)
            print(f"SHORT Signal - Entry {entry} SL {sl} TP {tp}")
            # place_order here
            
        elif prev_close < low_range and curr_close > low_range:
            sl = curr_low
            entry = curr_close
            tp = entry + ((entry - sl) * TP_RATIO)
            print(f"LONG Signal - Entry {entry} SL {sl} TP {tp}")
            # place_order here
    except Exception as e:
        print(f"Check Error: {e}")

# Main loop
if __name__ == "__main__":
    send_telegram(f"Bot Started V107 Web Service Mode")
    last_range_date = None
    while True:
        try:
            now_ist = datetime.now(IST)
            today_str = now_ist.strftime("%Y-%m-%d")
            if now_ist.strftime("%H:%M") >= RANGE_END and last_range_date != today_str:
                fetch_4h_range()
                last_range_date = today_str
            check_false_breakout()
            time.sleep(60)
        except Exception as e:
            print(f"Main Loop Error: {e}")
            time.sleep(60)
