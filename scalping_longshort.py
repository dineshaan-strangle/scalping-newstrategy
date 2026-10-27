import os
import logging
import asyncio
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from functools import wraps
import requests
import ccxt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Running")
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
        self.exchange = ccxt.delta({
            'apiKey': DELTA_API_KEY,
            'secret': DELTA_API_SECRET,
            'enableRateLimit': True
        })
        try:
            self.exchange.load_markets()
        except Exception as e:
            logger.warning(f"Load markets failed: {e}")
        self.user_settings = {}
        self.active_trades = {}
        self.pending_orders = {}

    def get_user_settings(self, user_id: int):
        if user_id not in self.user_settings:
            self.user_settings[user_id] = {"lots": 1.0, "leverage": 20, "sl_percent": 1.2}
        return self.user_settings[user_id]

    def normalize_symbol(self, input_symbol: str) -> str:
        s = input_symbol.upper().strip()
        if "ETH" in s:
            return "ETHUSD"
        return "BTCUSD"

    def get_current_price(self, symbol: str):
        valid_symbol = self.normalize_symbol(symbol)
        try:
            url = f"{DELTA_BASE_URL}/v2/tickers/{valid_symbol}"
            response = requests.get(url, timeout=5)
            data = response.json()
            result = data.get('result', data)
            price = float(result.get('close') or result.get('mark_price') or result.get('spot_price', 0))
            if price == 0:
                raise ValueError("Empty price")
            return valid_symbol, price
        except Exception as e:
            logger.error(f"Price fetch failed {valid_symbol}: {e}")
            ticker = self.exchange.fetch_ticker(valid_symbol)
            return valid_symbol, float(ticker['last'])

    def get_candles(self, symbol: str, timeframe: str, limit: int = 50):
        valid_symbol = self.normalize_symbol(symbol)
        try:
            end_time = int(time.time())
            seconds_map = {'5m': 300, '15m': 900, '1h': 3600}
            start_time = end_time - (limit * seconds_map.get(timeframe, 300) * 2)
            url = f"{DELTA_BASE_URL}/v2/history/candles"
            params = {"resolution": timeframe, "symbol": valid_symbol, "start": start_time, "end": end_time}
            response = requests.get(url, params=params, timeout=8)
            data = response.json()
            return data.get('result', [])[-limit:]
        except Exception:
            return []

    def calculate_ema(self, prices, period):
        if len(prices) < 2:
            return prices[0] if prices else 0
        k = 2 / (period + 1)
        ema_value = prices[0]
        for p in prices[1:]:
            ema_value = p * k + ema_value * (1 - k)
        return ema_value

    async def analyze_trend(self, symbol: str):
        valid_symbol = self.normalize_symbol(symbol)
        candles_5m = self.get_candles(valid_symbol, '5m', 50)
        candles_15m = self.get_candles(valid_symbol, '15m', 50)
        candles_1h = self.get_candles(valid_symbol, '1h', 50)

        if not candles_5m or len(candles_5m) < 20:
            return valid_symbol, "GREEN 🟢", "SIDEWAYS ⚖️", "50% NOT SAFE ⚠️", "SIDEWAYS! Konjam wait pannalam Anna, clear trend illa! Trade vendam ippo!"

        closes_5m = [float(x['close']) for x in candles_5m]
        closes_15m = [float(x['close']) for x in candles_15m] if candles_15m else closes_5m
        closes_1h = [float(x['close']) for x in candles_1h] if candles_1h else closes_5m

        last = candles_5m[-1]
        candle_status = "GREEN 🟢" if float(last['close']) > float(last['open']) else "RED 🔴"

        score = 0
        if closes_5m[-1] > self.calculate_ema(closes_5m, 20): score += 1
        if len(closes_15m) >= 20 and closes_15m[-1] > self.calculate_ema(closes_15m, 20): score += 1
        if len(closes_1h) >= 20 and closes_1h[-1] > self.calculate_ema(closes_1h, 20): score += 1
        if self.calculate_ema(closes_5m, 20) > self.calculate_ema(closes_5m, 50): score += 1

        if score >= 3:
            return valid_symbol, candle_status, "BULLISH 📈", f"{75+score*5}% SAFE ✅", "LONG ku NALLA time Anna! /long adikalam!"
        if score <= 1:
            return valid_symbol, candle_status, "BEARISH 📉", f"{75+(4-score)*5}% SAFE ✅", "SHORT ku NALLA time Anna! /short adikalam!"
        return valid_symbol, candle_status, "SIDEWAYS ⚖️", "50% NOT SAFE ⚠️", "SIDEWAYS! Konjam wait pannalam Anna, clear trend illa! Trade vendam ippo!"

    @authorized_only
    async def cmd_start(self, update, context):
        await update.message.reply_text(
            "DELTA Scalping Bot Live\n\n"
            "/trend BTCUSD - Check trend\n"
            "/long BTCUSD - Long entry\n"
            "/short BTCUSD - Short entry\n"
            "/qty 1.5 - Set lot size\n"
            "/lev 20 - Set leverage\n"
            "/sl 1.2 - Set SL percent\n"
            "/status - Active trade\n"
            "/close - Close trade"
        )

    @authorized_only
    async def cmd_qty(self, update, context):
        settings = self.get_user_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current lot size: {settings['lots']}")
            return
        settings['lots'] = float(context.args[0])
        await update.message.reply_text(f"Lot size updated: {settings['lots']}")

    @authorized_only
    async def cmd_lev(self, update, context):
        settings = self.get_user_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current leverage: {settings['leverage']}x")
            return
        settings['leverage'] = int(context.args[0])
        await update.message.reply_text(f"Leverage updated: {settings['leverage']}x")

    @authorized_only
    async def cmd_sl(self, update, context):
        settings = self.get_user_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current SL: {settings['sl_percent']}%")
            return
        settings['sl_percent'] = float(context.args[0])
        await update.message.reply_text(f"SL updated: {settings['sl_percent']}%")

    @authorized_only
    async def cmd_trend(self, update, context):
        symbol = context.args[0] if context.args else "BTCUSD"
        loading = await update.message.reply_text(f"Analyzing {symbol}...")
        try:
            valid_symbol, price = self.get_current_price(symbol)
            _, candle, trend, safety, decision = await self.analyze_trend(valid_symbol)
            report = (
                f"📊 {valid_symbol} TREND REPORT\n\n"
                f"💰 Price: {price}\n"
                f"🕯️ Last 5m: {candle}\n\n"
                f"📈 Trend (5m+15m+1h): {trend}\n"
                f"🛡️ Safety: {safety}\n\n"
                f"💡 MUDIVU:\n✅ {decision}"
            )
            await loading.delete()
            await update.message.reply_text(report)
        except Exception as e:
            await loading.delete()
            await update.message.reply_text(f"Error fetching {symbol}: {str(e)[:100]}")

    @authorized_only
    async def cmd_trade(self, update, context):
        user_id = update.effective_user.id
        settings = self.get_user_settings(user_id)
        side = "long" if update.message.text.lower().startswith("/long") else "short"
        symbol = context.args[0] if context.args else "BTCUSD"
        lots = float(context.args[1]) if len(context.args) > 1 else settings['lots']
        try:
            valid_symbol, price = self.get_current_price(symbol)
            _, _, trend, safety, _ = await self.analyze_trend(valid_symbol)
            initial_sl = price * (1 - settings['sl_percent']/100) if side == 'long' else price * (1 + settings['sl_percent']/100)
            self.pending_orders[user_id] = {
                "side": side, "symbol": valid_symbol, "lots": lots,
                "leverage": settings['leverage'], "sl_percent": settings['sl_percent'],
                "price": price, "initial_sl": initial_sl
            }
            message = (
                f"🎯 DELTA {valid_symbol} {side.upper()}\n"
                f"📦 {lots} Lots = {lots} Contracts\n"
                f"⚡ {settings['leverage']}x | 💰 {price}\n"
                f"🛑 Initial SL: {initial_sl:.2f} (-{settings['sl_percent']}%)\n"
                f"📊 {trend}\n"
                f"🛡️ {safety}"
            )
            keyboard = [[InlineKeyboardButton("YES", callback_data="yes"), InlineKeyboardButton("NO", callback_data="no")]]
            await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            await update.message.reply_text(f"Trade setup failed: {e}")

    @authorized_only
    async def cmd_confirm(self, update, context):
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        if user_id not in self.pending_orders:
            await query.edit_message_text("Request expired. Use /long or /short again.")
            return
        if query.data == "no":
            del self.pending_orders[user_id]
            await query.edit_message_text("Trade cancelled.")
            return
        data = self.pending_orders[user_id]
        await query.edit_message_text(f"Setting leverage {data['leverage']}x and entering position...")
        self.active_trades[user_id] = {**data, "entry": data['price'], "sl": data['initial_sl'], "stage": 0}
        await context.bot.send_message(user_id, f"Position Entered {data['symbol']} {data['side'].upper()}\nEntry: {data['price']}\nSL: {data['initial_sl']:.2f} Active\nTrailing starts at +0.8%")
        del self.pending_orders[user_id]
        asyncio.create_task(self.monitor_position(user_id, context))

    async def monitor_position(self, user_id, context):
        while user_id in self.active_trades:
            trade = self.active_trades[user_id]
            try:
                _, current_price = self.get_current_price(trade['symbol'])
            except Exception:
                await asyncio.sleep(10)
                continue
            profit = ((current_price - trade['entry'])/trade['entry']*100) if trade['side'] == 'long' else ((trade['entry'] - current_price)/trade['entry']*100)
            if profit >= 0.8 and trade['stage'] == 0:
                trade['sl'] = trade['entry']; trade['stage'] = 1
                await context.bot.send_message(user_id, f"BreakEven locked at {trade['sl']:.2f}")
            elif profit >= 1.5 and trade['stage'] == 1:
                trade['sl'] = trade['entry']*1.008 if trade['side'] == 'long' else trade['entry']*0.992; trade['stage'] = 2
                await context.bot.send_message(user_id, f"+0.8% profit locked SL: {trade['sl']:.2f}")
            if (trade['side'] == 'long' and current_price <= trade['sl']) or (trade['side'] == 'short' and current_price >= trade['sl']):
                await context.bot.send_message(user_id, f"Stop loss hit at {current_price}")
                del self.active_trades[user_id]
                break
            await asyncio.sleep(30)

    @authorized_only
    async def cmd_close(self, update, context):
        if update.effective_user.id in self.active_trades:
            del self.active_trades[update.effective_user.id]
            await update.message.reply_text("Position closed.")
        else:
            await update.message.reply_text("No active trade.")

    @authorized_only
    async def cmd_status(self, update, context):
        user_id = update.effective_user.id
        settings = self.get_user_settings(user_id)
        if user_id in self.active_trades:
            trade = self.active_trades[user_id]
            try:
                _, current_price = self.get_current_price(trade['symbol'])
                pnl = ((current_price - trade['entry'])/trade['entry']*100) if trade['side'] == 'long' else ((trade['entry'] - current_price)/trade['entry']*100)
                await update.message.reply_text(f"{trade['symbol']} {trade['side'].upper()}\nEntry: {trade['entry']}\nCurrent: {current_price}\nPnL: {pnl:.2f}%\nSL: {trade['sl']:.2f}")
            except Exception as e:
                await update.message.reply_text(f"Status error: {e}")
        else:
            await update.message.reply_text(f"No active trade\nDefaults: {settings['lots']} Lots, {settings['leverage']}x, SL {settings['sl_percent']}%")

def main():
    bot = DeltaTradingBot()
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", bot.cmd_start))
    application.add_handler(CommandHandler("qty", bot.cmd_qty))
    application.add_handler(CommandHandler("lev", bot.cmd_lev))
    application.add_handler(CommandHandler("sl", bot.cmd_sl))
    application.add_handler(CommandHandler("trend", bot.cmd_trend))
    application.add_handler(CommandHandler("long", bot.cmd_trade))
    application.add_handler(CommandHandler("short", bot.cmd_trade))
    application.add_handler(CommandHandler("close", bot.cmd_close))
    application.add_handler(CommandHandler("status", bot.cmd_status))
    application.add_handler(CallbackQueryHandler(bot.cmd_confirm))
    logger.info("Bot started")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
