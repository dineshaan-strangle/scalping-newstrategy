from flask import Flask
import threading, ccxt, time, requests, os
from datetime import datetime, time as dt_time
import pytz

# ===== RENDER WEB SERVER =====
app = Flask(__name__)
@app.route('/')
def home(): return "V109 Full Bot - 5min Pre-Alert + Lots/Lev ✅"
def run_flask(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
threading.Thread(target=run_flask, daemon=True).start()

# ===== CONFIG =====
IST = pytz.timezone('Asia/Kolkata')
SYMBOL = "BTC/USDT"
LOTS = 5
LEVERAGE = 15
TP_RATIO = 2.0

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
BITGET_API_KEY = os.getenv('BITGET_API_KEY')
BITGET_SECRET = os.getenv('BITGET_SECRET')
BITGET_PASSPHRASE = os.getenv('BITGET_PASSPHRASE')

exchange = ccxt.bitget({
    'apiKey': BITGET_API_KEY,
    'secret': BITGET_SECRET,
    'password': BITGET_PASSPHRASE,
    'enableRateLimit': True,
})

high_range = None
low_range = None
offset = 0
pre_alert_sent = False
trade_taken_today = False

def tg(msg):
    try:
        if not TELEGRAM_TOKEN: return
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        print(f"TG: {msg[:80]}")
    except Exception as e:
        print(f"TG Err: {e}")

def get_qty():
    return LOTS * 0.001  # 1 lot = 0.001 BTC

def get_candles():
    try:
        ohlcv = exchange.fetch_ohlcv(SYMBOL, '5m', limit=50)
        return ohlcv
    except Exception as e:
        print(f"Candle Err: {e}")
        return []

def check_cmd():
    global LOTS, LEVERAGE, offset
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=5", timeout=10).json()
        for u in r.get('result', []):
            offset = u['update_id'] + 1
            txt = u.get('message', {}).get('text', '')
            if not txt: continue
            if txt.startswith('/lots'):
                try:
                    LOTS = int(txt.split()[1])
                    tg(f"✅ Lots Updated: {LOTS}\nQty: {get_qty()} BTC")
                except: tg("Use: /lots 5")
            elif txt.startswith('/lev'):
                try:
                    LEVERAGE = int(txt.split()[1])
                    tg(f"✅ Leverage Updated: {LEVERAGE}x")
                except: tg("Use: /lev 15")
            elif txt.startswith('/status'):
                tg(f"📊 STATUS\nLots: {LOTS} ({get_qty()} BTC)\nLev: {LEVERAGE}x\nHigh: {high_range}\nLow: {low_range}\nTP: 2R | SL: Range High/Low\nTrade Taken: {trade_taken_today}")
            elif '/start' in txt:
                tg(f"🟢 Bot ON V109 ✅\nLots: {LOTS} Lev: {LEVERAGE}x\n⏰ Pre-Alert: 13:25\n🎯 Trade: 13:30-23:30")
    except Exception as e:
        print(f"Cmd Err: {e}")

def place_order(side, entry, sl, tp):
    try:
        qty = get_qty()
        # Set leverage
        try:
            exchange.set_leverage(LEVERAGE, SYMBOL)
        except: pass
        
        params = {
            'stopLoss': {'triggerPrice': sl, 'price': sl},
            'takeProfit': {'triggerPrice': tp, 'price': tp}
        }
        order = exchange.create_order(SYMBOL, 'market', side, qty, None, params)
        tg(f"🚀 TRADE TAKEN {side.upper()} ✅\nEntry: {entry}\nSL: {sl}\nTP: {tp} (2R)\nLots: {LOTS} Lev: {LEVERAGE}x\nQty: {qty}")
        print(f"Order placed: {order}")
        return True
    except Exception as e:
        tg(f"❌ Order Failed: {e}")
        print(f"Order Err: {e}")
        return False

# ===== STARTUP MESSAGE =====
tg(f"🟢 V109 FULL BOT LIVE ✅\n\nLots: {LOTS} ({get_qty()} BTC)\nLeverage: {LEVERAGE}x\nRange: 09:30-13:30\nPre-Alert: 13:25 (5min munnadi)\nTrade: 13:30-23:30\nSL: High/Low | TP: 2R\n\nCommands:\n/lots 10\n/lev 20\n/status")
print("Bot Started V109 Full Code")

# ===== MAIN LOOP =====
while True:
    try:
        check_cmd()
        now_ist = datetime.now(IST)
        hm = now_ist.strftime("%H:%M")
        curr_time = now_ist.time()

        # Reset daily at 09:30
        if hm == "09:29":
            high_range = None
            low_range = None
            trade_taken_today = False
            pre_alert_sent = False

        candles = get_candles()
        if not candles:
            time.sleep(30)
            continue
        
        last_candle = candles[-1]  # [timestamp, open, high, low, close, vol]
        c_high = last_candle[2]
        c_low = last_candle[3]
        c_close = last_candle[4]

        # 09:30 - 13:30 RANGE MARKING
        if dt_time(9,30) <= curr_time <= dt_time(13,30):
            if high_range is None:
                high_range = c_high
                low_range = c_low
            else:
                high_range = max(high_range, c_high)
                low_range = min(low_range, c_low)
            print(f"Range Marking {hm} H:{high_range} L:{low_range}")

        # 13:25 PRE-ALERT (5 min munnadi)
        if hm == "13:25" and not pre_alert_sent and high_range:
            pre_alert_sent = True
            tg(f"⚠️ 5 MIN LA TRADE START! ⚠️\n\n📦 Range (09:30-13:30)\nHigh: {high_range}\nLow: {low_range}\n\n⚙️ Settings\nLots: {LOTS} ({get_qty()} BTC)\nLeverage: {LEVERAGE}x\nSL: Range High/Low\nTP: 2R (1:2 Ratio)\n\n🎯 13:30 ku Breakout vantha alert + Trade varum! Ready ah irunga Anna!")

        # 13:30 - 23:30 TRADING (False Breakout)
        if dt_time(13,30) <= curr_time <= dt_time(23,30) and high_range and not trade_taken_today:
            # False Breakout SHORT: Break High and close back inside
            prev_high = candles[-2][2] if len(candles)>=2 else 0
            if prev_high > high_range and c_close < high_range:
                entry = c_close
                sl = c_high
                tp = entry - ((sl - entry) * TP_RATIO)
                tg(f"🔴 FALSE BREAKOUT HIGH DETECTED!\nHigh Range: {high_range}\nClose Back: {c_close}\n\nSHORT Edukka poren!")
                if place_order('sell', entry, sl, tp):
                    trade_taken_today = True

            # False Breakout LONG: Break Low and close back inside
            prev_low = candles[-2][3] if len(candles)>=2 else 999999
            if prev_low < low_range and c_close > low_range:
                entry = c_close
                sl = c_low
                tp = entry + ((entry - sl) * TP_RATIO)
                tg(f"🟢 FALSE BREAKOUT LOW DETECTED!\nLow Range: {low_range}\nClose Back: {c_close}\n\nLONG Edukka poren!")
                if place_order('buy', entry, sl, tp):
                    trade_taken_today = True

        print(f"Loop {hm} | H:{high_range} L:{low_range} | Lots:{LOTS} Lev:{LEVERAGE}x | Taken:{trade_taken_today}")
        time.sleep(60)

    except Exception as e:
        print(f"Main Loop Err: {e}")
        time.sleep(60)
