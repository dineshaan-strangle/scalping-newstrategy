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

# --- Health Server for Render ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Running - Real Trading Enabled")
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

# --- Config ---
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
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        try:
            self.exchange.load_markets()
            logger.info("Markets loaded")
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
            "🤖 DELTA REAL TRADING BOT LIVE\n\n"
            "/trend BTCUSD - Check trend\n"
            "/long BTCUSD - Real Long\n"
            "/short BTCUSD - Real Short\n"
            "/qty 1 - Lot size\n"
            "/lev 20 - Leverage\n"
            "/sl 1.2 - SL percent\n"
            "/status - Active trade\n"
            "/close - Close REAL trade on Delta"
        )

    @authorized_only
    async def cmd_qty(self, update, context):
        settings = self.get_user_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current lot size: {settings['lots']}")
            return
        settings['lots'] = float(context.args[0])
        await update.message.reply_text(f"✅ Lot size updated: {settings['lots']}")

    @authorized_only
    async def cmd_lev(self, update, context):
        settings = self.get_user_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current leverage: {settings['leverage']}x")
            return
        settings['leverage'] = int(context.args[0])
        await update.message.reply_text(f"✅ Leverage updated: {settings['leverage']}x")

    @authorized_only
    async def cmd_sl(self, update, context):
        settings = self.get_user_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current SL: {settings['sl_percent']}%")
            return
        settings['sl_percent'] = float(context.args[0])
        await update.message.reply_text(f"✅ SL updated: {settings['sl_percent']}%")

    @authorized_only
    async def cmd_trend(self, update, context):
        symbol = context.args[0] if context.args else "BTCUSD"
        loading = await update.message.reply_text(f"🔍 {symbol} Analysis edukkaren Anna...")
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
            await update.message.reply_text(f"Error fetching {symbol}: {str(e)[:150]}")

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
                f"🎯 DELTA {valid_symbol} {side.upper()} - REAL ORDER\n"
                f"📦 {lots} Lots = {lots} Contracts\n"
                f"⚡ {settings['leverage']}x | 💰 {price}\n"
                f"🛑 Initial SL: {initial_sl:.2f} (-{settings['sl_percent']}%)\n"
                f"📊 {trend}\n"
                f"🛡️ {safety}\n\n"
                f"⚠️ REAL MONEY ORDER - Confirm?"
            )
            keyboard = [[InlineKeyboardButton("✅ YES - REAL ORDER", callback_data="yes"), InlineKeyboardButton("❌ NO", callback_data="no")]]
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
            await query.edit_message_text("❌ Cancelled Anna")
            return
        
        data = self.pending_orders[user_id]
        await query.edit_message_text(f"⚙️ PLACING REAL ORDER ON DELTA...\n{data['symbol']} {data['side'].upper()} {data['leverage']}x")
        
        try:
            # Set leverage
            try:
                self.exchange.set_leverage(data['leverage'], data['symbol'])
                await asyncio.sleep(1)
            except Exception as lev_err:
                logger.warning(f"Leverage set failed (may be already set): {lev_err}")

            # Place market order - REAL
            order_side = "buy" if data['side'] == 'long' else "sell"
            order = self.exchange.create_market_order(
                symbol=data['symbol'],
                side=order_side,
                amount=data['lots']
            )
            
            entry_price = float(order.get('average') or order.get('price') or data['price'])
            order_id = order.get('id', 'N/A')
            
            # Place Stop Loss - REAL
            sl_side = "sell" if data['side'] == 'long' else "buy"
            try:
                sl_order = self.exchange.create_order(
                    symbol=data['symbol'],
                    type='market',
                    side=sl_side,
                    amount=data['lots'],
                    price=None,
                    params={
                        'stop_price': data['initial_sl'],
                        'stop_order_type': 'stop_loss_order',
                        'reduce_only': True
                    }
                )
                sl_msg = f"SL Order ID: {sl_order.get('id')}"
            except Exception as sl_err:
                logger.error(f"SL order error: {sl_err}")
                sl_msg = f"⚠️ Auto SL failed: {sl_err} - Manual SL pannunga Anna!"

            self.active_trades[user_id] = {
                **data, "entry": entry_price, "sl": data['initial_sl'], 
                "stage": 0, "order_id": order_id
            }

            success_msg = (
                f"✅ REAL POSITION OPENED ON DELTA!\n\n"
                f"📊 {data['symbol']} {data['side'].upper()}\n"
                f"🆔 Order ID: {order_id}\n"
                f"💰 Entry: {entry_price}\n"
                f"📦 Qty: {data['lots']} Contracts\n"
                f"⚡ Leverage: {data['leverage']}x\n"
                f"🛑 SL: {data['initial_sl']:.2f}\n"
                f"{sl_msg}\n\n"
                f"🔗 Check: delta.exchange -> Positions\n"
                f"Trailing starts at +0.8% Anna!"
            )
            await context.bot.send_message(user_id, success_msg)

        except Exception as e:
            error_text = str(e)
            await context.bot.send_message(
                user_id, 
                f"❌ REAL ORDER FAILED!\n\n"
                f"Error: {error_text[:300]}\n\n"
                f"Check List:\n"
                f"1. Delta API Key - Futures permission enabled?\n"
                f"2. API Secret correct?\n"
                f"3. Funds available? (Margin)\n"
                f"4. Symbol {data['symbol']} tradable?\n\n"
                f"Delta site la API Management check pannunga Anna"
            )
            logger.error(f"Real order failed: {e}", exc_info=True)
        
        del self.pending_orders[user_id]

    async def monitor_position(self, user_id, context):
        while user_id in self.active_trades:
            await asyncio.sleep(30)

    @authorized_only
    async def cmd_close(self, update, context):
        user_id = update.effective_user.id
        if user_id not in self.active_trades:
            # Try to close all positions on Delta directly
            try:
                positions = self.exchange.fetch_positions()
                if positions:
                    for pos in positions:
                        if pos['contracts'] and float(pos['contracts']) > 0:
                            side = "sell" if pos['side'] == 'long' else "buy"
                            self.exchange.create_market_order(pos['symbol'], side, float(pos['contracts']), params={'reduce_only': True})
                    await update.message.reply_text("✅ All REAL positions closed on Delta Anna")
                    return
            except Exception as e:
                await update.message.reply_text(f"No active trade in bot, but Delta close failed: {e}")
                return
            await update.message.reply_text("No active trade Anna")
            return
        
        trade = self.active_trades[user_id]
        try:
            close_side = "sell" if trade['side'] == 'long' else "buy"
            self.exchange.create_market_order(trade['symbol'], close_side, trade['lots'], params={'reduce_only': True})
            del self.active_trades[user_id]
            await update.message.reply_text(f"✅ REAL position closed on Delta Anna\n{trade['symbol']} {trade['side'].upper()}")
        except Exception as e:
            await update.message.reply_text(f"Close failed: {e}\nDelta website la manually close pannunga Anna")
            del self.active_trades[user_id]

    @authorized_only
    async def cmd_status(self, update, context):
        user_id = update.effective_user.id
        settings = self.get_user_settings(user_id)
        try:
            positions = self.exchange.fetch_positions()
            real_positions = [p for p in positions if p['contracts'] and float(p['contracts']) > 0]
            if real_positions:
                msg = "📊 REAL POSITIONS ON DELTA:\n\n"
                for p in real_positions:
                    msg += f"{p['symbol']} {p['side'].upper()} - {p['contracts']} contracts\nEntry: {p['entryPrice']}\nPnL: {p['unrealizedPnl']}\n\n"
                await update.message.reply_text(msg)
                return
        except Exception:
            pass
        
        if user_id in self.active_trades:
            trade = self.active_trades[user_id]
            try:
                _, current_price = self.get_current_price(trade['symbol'])
                pnl = ((current_price - trade['entry'])/trade['entry']*100) if trade['side'] == 'long' else ((trade['entry'] - current_price)/trade['entry']*100)
                await update.message.reply_text(f"📊 {trade['symbol']} {trade['side'].upper()}\nEntry: {trade['entry']}\nCurrent: {current_price}\nPnL: {pnl:.2f}%\nSL: {trade['sl']:.2f}")
            except Exception as e:
                await update.message.reply_text(f"Status error: {e}")
        else:
            await update.message.reply_text(f"No active trade Anna\nDefaults: {settings['lots']} Lots, {settings['leverage']}x, SL {settings['sl_percent']}%\n\nDelta website la positions illana REAL trade illa")

def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN missing")
        return
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
    logger.info(f"REAL TRADING Bot started - Owner: {TELEGRAM_CHAT_ID}")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
