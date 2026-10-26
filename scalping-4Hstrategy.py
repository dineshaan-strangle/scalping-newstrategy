import ccxt
import time
import requests
from datetime import datetime, timedelta
import pytz

# ========== CONFIG - EDIT HERE ==========
SYMBOL = "BTC/USD"
TIMEFRAME_4H = "4h"
TIMEFRAME_5M = "5m"

LOTS = 5  # 5 Lots = 0.005 BTC
LEVERAGE = 15

RANGE_START = "09:30"
RANGE_END = "13:30"
TRADE_START = "13:30"
TRADE_END = "23:30"

SL_TYPE = "BREAKOUT_EXTREME"
TP_RATIO = 2.0

TELEGRAM_TOKEN = "YOUR_TELEGRAM_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"
# ========================================

IST = pytz.timezone('Asia/Kolkata')
exchange = ccxt.bitget({
    'apiKey': 'YOUR_API_KEY',
    'secret': 'YOUR_SECRET',
    'password': 'YOUR_PASSPHRASE',
    'enableRateLimit': True,
})

high_range = None
low_range = None
current_lots = LOTS
current_leverage = LEVERAGE

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_qty_from_lots(lots):
    return lots * 0.001  # 1 Lot = 0.001 BTC

def is_trading_time():
    now_ist = datetime.now(IST).strftime("%H:%M")
    return TRADE_START <= now_ist <= TRADE_END

def is_range_time():
    now_ist = datetime.now(IST).strftime("%H:%M")
    return RANGE_START <= now_ist <= RANGE_END

def fetch_4h_range():
    global high_range, low_range
    try:
        # Fetch today 09:30-13:30 candle
        ohlcv = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME_4H, limit=1)
        if ohlcv:
            high_range = ohlcv[-1][2]
            low_range = ohlcv[-1][3]
            print(f"Range Marked: High={high_range}, Low={low_range}")
            send_telegram(f"Range Marked\nHigh: {high_range}\nLow: {low_range}\nTime: 09:30-13:30 IST")
    except Exception as e:
        print(f"Range Error: {e}")

def check_false_breakout():
    global high_range, low_range
    if not high_range or not low_range:
        return
    
    if not is_trading_time():
        print(f"Outside Trading Time: {datetime.now(IST).strftime('%H:%M')}")
        return

    try:
        candles = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME_5M, limit=10)
        prev_close = candles[-2][4]
        curr_close = candles[-1][4]
        curr_high = candles[-1][2]
        curr_low = candles[-1][3]

        qty = get_qty_from_lots(current_lots)
        
        # FALSE BREAKOUT SHORT: Break above + re-enter
        if prev_close > high_range and curr_close < high_range:
            sl = curr_high
            entry = curr_close
            tp = entry - ((sl - entry) * TP_RATIO)
            place_order("sell", qty, sl, tp, "SHORT False Breakout")

        # FALSE BREAKOUT LONG: Break below + re-enter
        elif prev_close < low_range and curr_close > low_range:
            sl = curr_low
            entry = curr_close
            tp = entry + ((entry - sl) * TP_RATIO)
            place_order("buy", qty, sl, tp, "LONG False Breakout")

    except Exception as e:
        print(f"Check Error: {e}")

def place_order(side, qty, sl, tp, reason):
    try:
        exchange.set_leverage(current_leverage, SYMBOL)
        order = exchange.create_market_order(SYMBOL, side, qty)
        
        # SL and TP
        sl_side = "buy" if side == "sell" else "sell"
        exchange.create_order(SYMBOL, 'stop', sl_side, qty, None, sl)
        
        msg = f"""Trade Executed: {reason}
Symbol: {SYMBOL}
Side: {side.upper()}
Qty: {qty} BTC ({current_lots} Lots)
Leverage: {current_leverage}x
Entry: ~Market
SL: {sl} (Breakout Extreme)
TP: {tp} (2R)
Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}"""
        
        send_telegram(msg)
        print(msg)
        
    except Exception as e:
        print(f"Order Error: {e}")
        send_telegram(f"Order Failed: {e}")

# Telegram Commands Handler
def handle_telegram_commands():
    global current_lots, current_leverage
    # Implement via webhook or polling
    # /lots 10 -> set lots
    # /leverage 20 -> set leverage
    # /status -> show status
    pass

# ========== MAIN LOOP ==========
if __name__ == "__main__":
    send_telegram(f"Bot Started V107\nLots: {current_lots} ({get_qty_from_lots(current_lots)} BTC)\nLeverage: {current_leverage}x\nRange: 09:30-13:30 IST\nTrade: 13:30-23:30 IST")
    
    last_range_date = None
    while True:
        try:
            now_ist = datetime.now(IST)
            today_str = now_ist.strftime("%Y-%m-%d")
            
            # Mark range once per day at 13:30
            if is_range_time() and last_range_date != today_str:
                if now_ist.strftime("%H:%M") >= RANGE_END:
                    fetch_4h_range()
                    last_range_date = today_str
            
            # Check for trades
            check_false_breakout()
            
            time.sleep(60)  # Check every 1 min
            
        except Exception as e:
            print(f"Main Loop Error: {e}")
            time.sleep(60)
