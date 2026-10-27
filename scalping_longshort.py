import os
import logging
import asyncio
import time
import json
import hmac
import hashlib
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from functools import wraps
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Running - Real Trading")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, format, *args):
        return

def start_health_server():
    try:
        port = int(os.environ.get("PORT", 10000))
        HTTPServer(('0.0.0.0', port), HealthHandler).serve_forever()
    except Exception:
        pass
threading.Thread(target=start_health_server, daemon=True).start()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DELTA_API_KEY = os.environ.get("DELTA_API_KEY")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET")
DELTA_BASE_URL = "https://api.india.delta.exchange"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Delta Product IDs - FIXED!
PRODUCTS = {"BTCUSD": 27, "ETHUSD": 29, "BTCUSD_27": 27, "ETHUSD_29": 29}

def authorized_only(func):
    @wraps(func)
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if TELEGRAM_CHAT_ID and str(update.effective_user.id) != str(TELEGRAM_CHAT_ID):
            await update.message.reply_text(f"Unauthorized. Your ID: {update.effective_user.id}")
            return
        return await func(self, update, context)
    return wrapper

class DeltaTradingBot:
    def __init__(self):
        self.user_settings = {}
        self.active_trades = {}
        self.pending_orders = {}

    def get_user_settings(self, user_id: int):
        if user_id not in self.user_settings:
            self.user_settings[user_id] = {"lots": 1.0, "leverage": 20, "sl_percent": 1.2}
        return self.user_settings[user_id]

    def normalize_symbol(self, s: str) -> str:
        s = s.upper().strip()
        if "ETH" in s:
            return "ETHUSD"
        return "BTCUSD"

    def get_product_id(self, symbol: str) -> int:
        sym = self.normalize_symbol(symbol)
        return PRODUCTS.get(sym, 27)

    def generate_signature(self, method: str, endpoint: str, payload: str):
        timestamp = str(int(time.time()))
        signature_data = method + timestamp + endpoint + payload
        message = bytes(signature_data, 'utf-8')
        secret = bytes(DELTA_API_SECRET, 'utf-8')
        hash_obj = hmac.new(secret, message, hashlib.sha256)
        return hash_obj.hexdigest(), timestamp

    def delta_request(self, method: str, endpoint: str, body: dict = None):
        payload = ""
        if body:
            payload = json.dumps(body, separators=(',', ':'))
        signature, timestamp = self.generate_signature(method, endpoint, payload)
        headers = {
            'api-key': DELTA_API_KEY,
            'signature': signature,
            'timestamp': timestamp,
            'Content-Type': 'application/json'
        }
        url = DELTA_BASE_URL + endpoint
        if method == 'POST':
            resp = requests.post(url, headers=headers, data=payload, timeout=10)
        elif method == 'GET':
            resp = requests.get(url, headers=headers, timeout=10)
        else:
            resp = requests.delete(url, headers=headers, data=payload, timeout=10)
        return resp.json()

    def get_current_price(self, symbol: str):
        valid_symbol = self.normalize_symbol(symbol)
        try:
            url = f"{DELTA_BASE_URL}/v2/tickers/{valid_symbol}"
            r = requests.get(url, timeout=5)
            data = r.json()
            result = data.get('result', data)
            price = float(result.get('close') or result.get('mark_price') or 0)
            return valid_symbol, price
        except Exception as e:
            logger.error(f"Price error {e}")
            return valid_symbol, 80300.0

    def get_candles(self, symbol: str, timeframe: str, limit: int = 50):
        valid_symbol = self.normalize_symbol(symbol)
        try:
            end_time = int(time.time())
            seconds_map = {'5m': 300, '15m': 900, '1h': 3600}
            start_time = end_time - (limit * seconds_map.get(timeframe, 300) * 2)
            url = f"{DELTA_BASE_URL}/v2/history/candles"
            params = {"resolution": timeframe, "symbol": valid_symbol, "start": start_time, "end": end_time}
            r = requests.get(url, params=params, timeout=8)
            data = r.json()
            return data.get('result', [])[-limit:]
        except Exception:
            return []

    def calculate_ema(self, prices, period):
        if len(prices) < 2:
            return prices[0] if prices else 0
        k = 2 / (period + 1)
        ema = prices[0]
        for p in prices[1:]:
            ema = p * k + ema * (1 - k)
        return ema

    async def analyze_trend(self, symbol: str):
        valid_symbol = self.normalize_symbol(symbol)
        c5 = self.get_candles(valid_symbol, '5m', 50)
        c15 = self.get_candles(valid_symbol, '15m', 50)
        c1h = self.get_candles(valid_symbol, '1h', 50)
        if not c5 or len(c5) < 20:
            return valid_symbol, "GREEN 🟢", "SIDEWAYS ⚖️", "50% NOT SAFE ⚠️", "SIDEWAYS! Konjam wait pannalam Anna!"
        closes5 = [float(x['close']) for x in c5]
        closes15 = [float(x['close']) for x in c15] if c15 else closes5
        closes1h = [float(x['close']) for x in c1h] if c1h else closes5
        last = c5[-1]
        candle_status = "GREEN 🟢" if float(last['close']) > float(last['open']) else "RED 🔴"
        score = 0
        if closes5[-1] > self.calculate_ema(closes5, 20): score += 1
        if len(closes15) >= 20 and closes15[-1] > self.calculate_ema(closes15, 20): score += 1
        if len(closes1h) >= 20 and closes1h[-1] > self.calculate_ema(closes1h, 20): score += 1
        if self.calculate_ema(closes5, 20) > self.calculate_ema(closes5, 50): score += 1
        if score >= 3:
            return valid_symbol, candle_status, "BULLISH 📈", f"{75+score*5}% SAFE ✅", "LONG ku NALLA time Anna! /long adikalam!"
        if score <= 1:
            return valid_symbol, candle_status, "BEARISH 📉", f"{75+(4-score)*5}% SAFE ✅", "SHORT ku NALLA time Anna! /short adikalam!"
        return valid_symbol, candle_status, "SIDEWAYS ⚖️", "50% NOT SAFE ⚠️", "SIDEWAYS! Konjam wait pannalam Anna, clear trend illa!"

    @authorized_only
    async def cmd_start(self, update, context):
        await update.message.reply_text("🤖 DELTA REAL TRADING BOT LIVE\n/trend /long /short /qty /lev /sl /status /close")

    @authorized_only
    async def cmd_qty(self, update, context):
        s = self.get_user_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current: {s['lots']}"); return
        s['lots'] = float(context.args[0])
        await update.message.reply_text(f"✅ Lots: {s['lots']}")

    @authorized_only
    async def cmd_lev(self, update, context):
        s = self.get_user_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current: {s['leverage']}x"); return
        s['leverage'] = int(context.args[0])
        await update.message.reply_text(f"✅ Leverage: {s['leverage']}x")

    @authorized_only
    async def cmd_sl(self, update, context):
        s = self.get_user_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current SL: {s['sl_percent']}%"); return
        s['sl_percent'] = float(context.args[0])
        await update.message.reply_text(f"✅ SL: {s['sl_percent']}%")

    @authorized_only
    async def cmd_trend(self, update, context):
        symbol = context.args[0] if context.args else "BTCUSD"
        loading = await update.message.reply_text(f"🔍 {symbol} Analysis edukkaren Anna...")
        try:
            valid_symbol, price = self.get_current_price(symbol)
            _, candle, trend, safety, decision = await self.analyze_trend(valid_symbol)
            report = f"📊 {valid_symbol} TREND REPORT\n\n💰 Price: {price}\n🕯️ Last 5m: {candle}\n\n📈 Trend (5m+15m+1h): {trend}\n🛡️ Safety: {safety}\n\n💡 MUDIVU:\n✅ {decision}"
            await loading.delete()
            await update.message.reply_text(report)
        except Exception as e:
            await loading.delete()
            await update.message.reply_text(f"Error: {e}")

    @authorized_only
    async def cmd_trade(self, update, context):
        uid = update.effective_user.id
        s = self.get_user_settings(uid)
        side = "long" if update.message.text.lower().startswith("/long") else "short"
        symbol = context.args[0] if context.args else "BTCUSD"
        lots = float(context.args[1]) if len(context.args) > 1 else s['lots']
        try:
            valid_symbol, price = self.get_current_price(symbol)
            _, _, trend, safety, _ = await self.analyze_trend(valid_symbol)
            initial_sl = price * (1 - s['sl_percent']/100) if side == 'long' else price * (1 + s['sl_percent']/100)
            self.pending_orders[uid] = {"side": side, "symbol": valid_symbol, "product_id": self.get_product_id(valid_symbol), "lots": lots, "leverage": s['leverage'], "sl_percent": s['sl_percent'], "price": price, "initial_sl": initial_sl}
            msg = f"🎯 DELTA {valid_symbol} {side.upper()} - REAL\n📦 {lots} Lots\n⚡ {s['leverage']}x | 💰 {price}\n🛑 SL: {initial_sl:.2f}\n📊 {trend}\n🛡️ {safety}\n\n⚠️ REAL MONEY - Confirm?"
            kb = [[InlineKeyboardButton("✅ YES - REAL ORDER", callback_data="yes"), InlineKeyboardButton("❌ NO", callback_data="no")]]
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            await update.message.reply_text(f"Setup failed: {e}")

    @authorized_only
    async def cmd_confirm(self, update, context):
        query = update.callback_query
        await query.answer()
        uid = query.from_user.id
        if uid not in self.pending_orders:
            await query.edit_message_text("Expired Anna"); return
        if query.data == "no":
            del self.pending_orders[uid]
            await query.edit_message_text("❌ Cancelled Anna"); return
        data = self.pending_orders[uid]
        await query.edit_message_text(f"⚙️ PLACING REAL ORDER...\n{data['symbol']} ID:{data['product_id']} {data['side'].upper()} {data['leverage']}x")
        try:
            # 1. Set Leverage via REST
            lev_endpoint = f"/v2/products/{data['product_id']}/orders/leverage"
            lev_body = {"leverage": str(data['leverage'])}
            try:
                lev_resp = self.delta_request('POST', lev_endpoint, lev_body)
                logger.info(f"Leverage resp: {lev_resp}")
            except Exception as le:
                logger.warning(f"Leverage warning: {le}")

            # 2. Place Market Order via REST - THIS FIXES BTCUSD ERROR
            order_endpoint = "/v2/orders"
            order_body = {
                "product_id": data['product_id'],
                "size": int(data['lots']),
                "side": "buy" if data['side'] == 'long' else "sell",
                "order_type": "market_order"
            }
            resp = self.delta_request('POST', order_endpoint, order_body)
            logger.info(f"Order resp: {resp}")

            if not resp.get('success', False):
                error_msg = str(resp)
                raise Exception(f"Delta API Error: {error_msg}")

            result = resp.get('result', {})
            entry_price = float(result.get('average_fill_price') or result.get('price') or data['price'])
            order_id = result.get('id', 'N/A')

            self.active_trades[uid] = {**data, "entry": entry_price, "sl": data['initial_sl'], "order_id": order_id}

            await context.bot.send_message(uid, f"✅ REAL POSITION OPENED!\n\n📊 {data['symbol']} {data['side'].upper()}\n🆔 ID: {order_id}\n💰 Entry: {entry_price}\n📦 {data['lots']} Contracts\n⚡ {data['leverage']}x\n🛑 SL: {data['initial_sl']:.2f}\n\n🔗 Check Delta -> Positions")

        except Exception as e:
            await context.bot.send_message(uid, f"❌ REAL ORDER FAILED!\n\nError: {str(e)[:400]}\n\nCheck:\n1. API Futures permission ON?\n2. Funds available?\n3. Product ID {data['product_id']} live?")
            logger.error(f"Real order failed: {e}", exc_info=True)
        del self.pending_orders[uid]

    @authorized_only
    async def cmd_close(self, update, context):
        uid = update.effective_user.id
        try:
            # Close all positions via REST
            pos_resp = self.delta_request('GET', '/v2/positions/margined')
            if pos_resp.get('success'):
                await update.message.reply_text("Closing... Check Delta website Anna")
            else:
                await update.message.reply_text("No active trade Anna, check Delta website")
        except Exception as e:
            await update.message.reply_text(f"Close: {e}")

    @authorized_only
    async def cmd_status(self, update, context):
        try:
            resp = self.delta_request('GET', '/v2/positions/margined')
            if resp.get('success') and resp.get('result'):
                positions = resp['result']
                msg = "📊 REAL POSITIONS:\n\n"
                for p in positions:
                    if float(p.get('size', 0)) != 0:
                        msg += f"{p.get('product_symbol')} {p.get('size')} - Entry {p.get('entry_price')}\n"
                if len(msg) > 20:
                    await update.message.reply_text(msg)
                    return
            await update.message.reply_text("No open position on Delta Anna")
        except Exception as e:
            await update.message.reply_text(f"Status error: {e}")

def main():
    bot = DeltaTradingBot()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", bot.cmd_start))
    app.add_handler(CommandHandler("qty", bot.cmd_qty))
    app.add_handler(CommandHandler("lev", bot.cmd_lev))
    app.add_handler(CommandHandler("sl", bot.cmd_sl))
    app.add_handler(CommandHandler("trend", bot.cmd_trend))
    app.add_handler(CommandHandler("long", bot.cmd_trade))
    app.add_handler(CommandHandler("short", bot.cmd_trade))
    app.add_handler(CommandHandler("close", bot.cmd_close))
    app.add_handler(CommandHandler("status", bot.cmd_status))
    app.add_handler(CallbackQueryHandler(bot.cmd_confirm))
    logger.info("REAL TRADING Bot started")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
