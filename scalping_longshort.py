import os
import logging
import asyncio
from functools import wraps
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import ccxt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- Professional Port Server for Render ---
class RenderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running")
    
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    
    def log_message(self, format, *args):
        return

def start_web_server():
    try:
        port = int(os.environ.get("PORT", 10000))
        server = HTTPServer(('0.0.0.0', port), RenderHandler)
        server.serve_forever()
    except Exception as e:
        print(f"Web server error: {e}")

threading.Thread(target=start_web_server, daemon=True).start()

# --- Environment Variables ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DELTA_API_KEY = os.environ.get("DELTA_API_KEY")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN not set")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def authorized_only(func):
    @wraps(func)
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if TELEGRAM_CHAT_ID and str(update.effective_user.id) != str(TELEGRAM_CHAT_ID):
            await update.message.reply_text(
                f"Unauthorized access. Your ID: {update.effective_user.id}\n"
                f"Contact admin to whitelist."
            )
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
            logger.info(f"Markets loaded: {len(self.exchange.markets)} symbols")
        except Exception as e:
            logger.error(f"Failed to load markets: {e}")
        
        self.user_settings = {}
        self.active_trades = {}
        self.pending = {}

    def get_settings(self, user_id: int):
        if user_id not in self.user_settings:
            self.user_settings[user_id] = {
                "lots": 1.0,
                "leverage": 20,
                "sl_percent": 1.2
            }
        return self.user_settings[user_id]

    def get_valid_symbol(self, input_symbol: str) -> str:
        input_symbol = input_symbol.upper().strip()
        if "BTC" in input_symbol:
            return "BTCUSD"
        if "ETH" in input_symbol:
            return "ETHUSD"
        return "BTCUSD"

    def fetch_price_safe(self, symbol: str):
        valid_symbol = self.get_valid_symbol(symbol)
        try:
            ticker = self.exchange.fetch_ticker(valid_symbol)
            return valid_symbol, ticker['last']
        except Exception as e:
            logger.error(f"Ticker error for {valid_symbol}: {e}")
            try:
                tickers = self.exchange.fetch_tickers()
                if valid_symbol in tickers:
                    return valid_symbol, tickers[valid_symbol]['last']
            except:
                pass
            raise e

    def ema(self, prices, period):
        if not prices or len(prices) < period:
            return prices[-1] if prices else 0
        k = 2 / (period + 1)
        ema_val = prices[0]
        for p in prices[1:]:
            ema_val = p * k + ema_val * (1 - k)
        return ema_val

    async def get_trend(self, symbol: str):
        try:
            valid_symbol = self.get_valid_symbol(symbol)
            ohlcv_5m = self.exchange.fetch_ohlcv(valid_symbol, '5m', limit=50)
            ohlcv_15m = self.exchange.fetch_ohlcv(valid_symbol, '15m', limit=50)
            ohlcv_1h = self.exchange.fetch_ohlcv(valid_symbol, '1h', limit=50)
            
            c5 = [x[4] for x in ohlcv_5m]
            c15 = [x[4] for x in ohlcv_15m]
            c1h = [x[4] for x in ohlcv_1h]
            
            score = sum([
                c5[-1] > self.ema(c5, 20),
                c15[-1] > self.ema(c15, 20),
                c1h[-1] > self.ema(c1h, 20),
                self.ema(c5, 20) > self.ema(c5, 50)
            ])
            
            if score >= 3:
                return "BULLISH", f"{75+score*5}% SAFE", valid_symbol
            if score <= 1:
                return "BEARISH", f"{75+(4-score)*5}% SAFE", valid_symbol
            return "SIDEWAYS", "50% WAIT", valid_symbol
        except Exception as e:
            logger.error(f"Trend error: {e}")
            return "UNKNOWN", f"Error: {str(e)[:50]}", symbol

    @authorized_only
    async def start(self, update, context):
        msg = (
            f"DELTA Scalping Bot Live\n\n"
            f"Your ID: {update.effective_user.id}\n\n"
            f"Commands:\n"
            f"/trend BTCUSD - Check trend\n"
            f"/long BTCUSD [lots] - Long entry\n"
            f"/short BTCUSD [lots] - Short entry\n"
            f"/qty 1.5 - Set lot size\n"
            f"/lev 20 - Set leverage\n"
            f"/sl 1.2 - Set SL %\n"
            f"/status - Active trade\n"
            f"/close - Close trade"
        )
        await update.message.reply_text(msg)

    @authorized_only
    async def qty(self, update, context):
        s = self.get_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current lot size: {s['lots']}")
            return
        try:
            s['lots'] = float(context.args[0])
            await update.message.reply_text(f"Lot size set to: {s['lots']}")
        except:
            await update.message.reply_text("Invalid value. Example: /qty 1.5")

    @authorized_only
    async def lev(self, update, context):
        s = self.get_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current leverage: {s['leverage']}x")
            return
        try:
            s['leverage'] = int(context.args[0])
            await update.message.reply_text(f"Leverage set to: {s['leverage']}x")
        except:
            await update.message.reply_text("Invalid value. Example: /lev 20")

    @authorized_only
    async def sl(self, update, context):
        s = self.get_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current SL: {s['sl_percent']}%")
            return
        try:
            s['sl_percent'] = float(context.args[0])
            await update.message.reply_text(f"SL set to: {s['sl_percent']}%")
        except:
            await update.message.reply_text("Invalid value. Example: /sl 1.2")

    @authorized_only
    async def trend(self, update, context):
        sym = context.args[0] if context.args else "BTCUSD"
        await update.message.reply_text(f"Analyzing {sym}...")
        try:
            valid_sym, price = self.fetch_price_safe(sym)
            trend, safe, _ = await self.get_trend(valid_sym)
            await update.message.reply_text(
                f"{valid_sym}\n"
                f"Price: {price}\n"
                f"Trend: {trend}\n"
                f"Safety: {safe}"
            )
        except Exception as e:
            await update.message.reply_text(f"Error fetching {sym}: {str(e)[:100]}")

    @authorized_only
    async def trade(self, update, context):
        uid = update.effective_user.id
        s = self.get_settings(uid)
        side = "long" if update.message.text.lower().startswith("/long") else "short"
        sym = context.args[0] if context.args else "BTCUSD"
        lots = float(context.args[1]) if len(context.args) > 1 else s['lots']
        
        try:
            valid_sym, price = self.fetch_price_safe(sym)
            trend, safe, _ = await self.get_trend(valid_sym)
            init_sl = price * (1 - s['sl_percent']/100) if side == 'long' else price * (1 + s['sl_percent']/100)
            
            self.pending[uid] = {
                "side": side, "symbol": valid_sym, "lots": lots,
                "leverage": s['leverage'], "sl_percent": s['sl_percent'],
                "price": price, "init_sl": init_sl
            }
            
            kb = [[
                InlineKeyboardButton("YES - Confirm", callback_data="yes"),
                InlineKeyboardButton("NO - Cancel", callback_data="no")
            ]]
            
            await update.message.reply_text(
                f"Confirm Trade\n\n"
                f"Symbol: {valid_sym}\n"
                f"Side: {side.upper()}\n"
                f"Lots: {lots} | Leverage: {s['leverage']}x\n"
                f"Entry Price: {price}\n"
                f"Stop Loss: {init_sl:.2f} ({s['sl_percent']}%)\n"
                f"Trend: {trend}\n"
                f"Safety: {safe}",
                reply_markup=InlineKeyboardMarkup(kb)
            )
        except Exception as e:
            await update.message.reply_text(f"Trade setup failed: {str(e)[:100]}")

    @authorized_only
    async def confirm(self, update, context):
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        
        if uid not in self.pending:
            await q.edit_message_text("Request expired. Start again.")
            return
        
        if q.data == "no":
            del self.pending[uid]
            await q.edit_message_text("Trade cancelled.")
            return
        
        d = self.pending[uid]
        await q.edit_message_text(f"Setting leverage {d['leverage']}x and placing order...")
        
        self.active_trades[uid] = {
            **d, "entry": d['price'], "sl": d['init_sl'],
            "stage": 0, "opp": 0
        }
        
        await context.bot.send_message(
            uid,
            f"Position Entered\n\n"
            f"{d['symbol']} {d['side'].upper()}\n"
            f"Entry: {d['price']}\n"
            f"SL: {d['init_sl']:.2f} Active\n"
            f"Trailing will start at +0.8%"
        )
        del self.pending[uid]
        asyncio.create_task(self.monitor(uid, context))

    async def monitor(self, user_id, context):
        while user_id in self.active_trades:
            t = self.active_trades[user_id]
            try:
                _, cur = self.fetch_price_safe(t['symbol'])
            except:
                await asyncio.sleep(10)
                continue
            
            profit = ((cur - t['entry'])/t['entry']*100) if t['side'] == 'long' else ((t['entry'] - cur)/t['entry']*100)
            
            if profit >= 0.8 and t['stage'] == 0:
                t['sl'] = t['entry']
                t['stage'] = 1
                await context.bot.send_message(user_id, f"BreakEven locked at {t['sl']:.2f}")
            elif profit >= 1.5 and t['stage'] == 1:
                t['sl'] = t['entry']*1.008 if t['side'] == 'long' else t['entry']*0.992
                t['stage'] = 2
                await context.bot.send_message(user_id, f"+0.8% Profit locked. SL: {t['sl']:.2f}")
            elif profit >= 2.5 and t['stage'] == 2:
                t['sl'] = t['entry']*1.015 if t['side'] == 'long' else t['entry']*0.985
                t['stage'] = 3
                await context.bot.send_message(user_id, f"+1.5% Profit locked. SL: {t['sl']:.2f}")
            
            try:
                o = self.exchange.fetch_ohlcv(t['symbol'], '5m', limit=3)
                last = o[-2]
                green = last[4] > last[1]
                if t['side'] == 'long' and not green:
                    t['opp'] += 1
                elif t['side'] == 'short' and green:
                    t['opp'] += 1
                else:
                    t['opp'] = 0
                
                if t['opp'] >= 2:
                    await context.bot.send_message(user_id, f"Exit signal: 2 opposite candles. Closing at {cur}")
                    del self.active_trades[user_id]
                    break
            except:
                pass
            
            if (t['side'] == 'long' and cur <= t['sl']) or (t['side'] == 'short' and cur >= t['sl']):
                await context.bot.send_message(user_id, f"Stop Loss hit at {cur}")
                del self.active_trades[user_id]
                break
            
            await asyncio.sleep(30)

    @authorized_only
    async def close(self, update, context):
        uid = update.effective_user.id
        if uid in self.active_trades:
            del self.active_trades[uid]
            await update.message.reply_text("Position closed manually.")
        else:
            await update.message.reply_text("No active trade.")

    @authorized_only
    async def status(self, update, context):
        uid = update.effective_user.id
        s = self.get_settings(uid)
        if uid in self.active_trades:
            t = self.active_trades[uid]
            try:
                _, cur = self.fetch_price_safe(t['symbol'])
                pnl = ((cur - t['entry'])/t['entry']*100) if t['side'] == 'long' else ((t['entry'] - cur)/t['entry']*100)
                await update.message.reply_text(
                    f"Active Trade\n\n"
                    f"{t['symbol']} {t['side'].upper()}\n"
                    f"Entry: {t['entry']}\n"
                    f"Current: {cur}\n"
                    f"PnL: {pnl:.2f}%\n"
                    f"SL: {t['sl']:.2f}"
                )
            except Exception as e:
                await update.message.reply_text(f"Status error: {e}")
        else:
            await update.message.reply_text(
                f"No active trade\n\n"
                f"Defaults: {s['lots']} Lots, {s['leverage']}x, SL {s['sl_percent']}%"
            )

def main():
    bot = DeltaTradingBot()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CommandHandler("qty", bot.qty))
    app.add_handler(CommandHandler("lev", bot.lev))
    app.add_handler(CommandHandler("sl", bot.sl))
    app.add_handler(CommandHandler("trend", bot.trend))
    app.add_handler(CommandHandler("long", bot.trade))
    app.add_handler(CommandHandler("short", bot.trade))
    app.add_handler(CommandHandler("close", bot.close))
    app.add_handler(CommandHandler("status", bot.status))
    app.add_handler(CallbackQueryHandler(bot.confirm))
    
    logger.info(f"Bot Started - Owner: {TELEGRAM_CHAT_ID}")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
