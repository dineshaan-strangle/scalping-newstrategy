import os
import logging
import asyncio
from functools import wraps
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import ccxt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Running OK")

def run_server():
    try:
        port = int(os.environ.get("PORT", 10000))
        HTTPServer(('0.0.0.0', port), Handler).serve_forever()
    except:
        pass

threading.Thread(target=run_server, daemon=True).start()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DELTA_API_KEY = os.environ.get("DELTA_API_KEY")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def authorized_only(func):
    @wraps(func)
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if TELEGRAM_CHAT_ID and str(update.effective_user.id) != str(TELEGRAM_CHAT_ID):
            await update.message.reply_text(f"Unauthorized! Your ID: {update.effective_user.id}")
            return
        return await func(self, update, context)
    return wrapper

class DeltaTradingBot:
    def __init__(self):
        self.exchange = ccxt.delta({
            'apiKey': DELTA_API_KEY,
            'secret': DELTA_API_SECRET,
            'enableRateLimit': True,
        })
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

    def ema(self, prices, period):
        k = 2 / (period + 1)
        ema_val = prices[0]
        for p in prices[1:]:
            ema_val = p * k + ema_val * (1 - k)
        return ema_val

    async def get_trend(self, symbol):
        try:
            o5 = self.exchange.fetch_ohlcv(symbol, '5m', limit=50)
            o15 = self.exchange.fetch_ohlcv(symbol, '15m', limit=50)
            o1h = self.exchange.fetch_ohlcv(symbol, '1h', limit=50)
            c5 = [x[4] for x in o5]
            c15 = [x[4] for x in o15]
            c1h = [x[4] for x in o1h]
            s = sum([
                c5[-1] > self.ema(c5, 20),
                c15[-1] > self.ema(c15, 20),
                c1h[-1] > self.ema(c1h, 20),
                self.ema(c5, 20) > self.ema(c5, 50)
            ])
            if s >= 3:
                return "BULLISH", f"{75+s*5}% SAFE"
            if s <= 1:
                return "BEARISH", f"{75+(4-s)*5}% SAFE"
            return "SIDEWAYS", "50% WAIT"
        except:
            return "UNKNOWN", "Error"

    @authorized_only
    async def start(self, update, context):
        await update.message.reply_text(
            f"DELTA Bot Live! ID: {update.effective_user.id}\n"
            f"/trend BTCUSD\n/long BTCUSD\n/short BTCUSD\n"
            f"/qty /lev /sl /status /close",
            parse_mode='Markdown'
        )

    @authorized_only
    async def qty(self, update, context):
        s = self.get_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current: {s['lots']} Lots")
            return
        s['lots'] = float(context.args[0])
        await update.message.reply_text(f"Qty: {s['lots']} Lots")

    @authorized_only
    async def lev(self, update, context):
        s = self.get_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current: {s['leverage']}x")
            return
        s['leverage'] = int(context.args[0])
        await update.message.reply_text(f"Lev: {s['leverage']}x")

    @authorized_only
    async def sl(self, update, context):
        s = self.get_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current SL: {s['sl_percent']}%")
            return
        s['sl_percent'] = float(context.args[0])
        await update.message.reply_text(f"SL: {s['sl_percent']}%")

    @authorized_only
    async def trend(self, update, context):
        sym = context.args[0].upper() if context.args else "BTCUSD"
        symbol = "BTCUSD" if "BTC" in sym else "ETHUSD"
        await update.message.reply_text(f"Analyzing {symbol}...")
        t, safe = await self.get_trend(symbol)
        price = self.exchange.fetch_ticker(symbol)['last']
        await update.message.reply_text(
            f"{symbol}\nPrice {price}\nTrend {t}\n{safe}",
            parse_mode='Markdown'
        )

    @authorized_only
    async def trade(self, update, context):
        uid = update.effective_user.id
        s = self.get_settings(uid)
        side = "long" if update.message.text.lower().startswith("/long") else "short"
        sym = context.args[0].upper() if context.args else "BTCUSD"
        symbol = "BTCUSD" if "BTC" in sym else "ETHUSD"
        lots = float(context.args[1]) if len(context.args) > 1 else s['lots']
        trend, safe = await self.get_trend(symbol)
        price = self.exchange.fetch_ticker(symbol)['last']
        init_sl = price * (1 - s['sl_percent']/100) if side == 'long' else price * (1 + s['sl_percent']/100)
        self.pending[uid] = {
            "side": side, "symbol": symbol, "lots": lots,
            "leverage": s['leverage'], "sl_percent": s['sl_percent'],
            "price": price, "init_sl": init_sl
        }
        kb = [[
            InlineKeyboardButton("YES", callback_data="yes"),
            InlineKeyboardButton("NO", callback_data="no")
        ]]
        await update.message.reply_text(
            f"{symbol} {side.upper()}\n"
            f"{lots} Lots | {s['leverage']}x\n"
            f"Price {price}\nSL: {init_sl:.2f} ({s['sl_percent']}%)\n"
            f"{trend} | {safe}\n\nConfirm?",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    @authorized_only
    async def confirm(self, update, context):
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if uid not in self.pending:
            await q.edit_message_text("Expired")
            return
        if q.data == "no":
            del self.pending[uid]
            await q.edit_message_text("Cancelled")
            return
        d = self.pending[uid]
        await q.edit_message_text(f"Setting {d['leverage']}x...")
        self.active_trades[uid] = {
            **d, "entry": d['price'], "sl": d['init_sl'],
            "stage": 0, "opp": 0
        }
        await context.bot.send_message(
            uid,
            f"ENTERED {d['symbol']} {d['side'].upper()}\n"
            f"Entry {d['price']}\nSL {d['init_sl']:.2f} ACTIVE"
        )
        del self.pending[uid]
        asyncio.create_task(self.monitor(uid, context))

    async def monitor(self, user_id, context):
        while user_id in self.active_trades:
            t = self.active_trades[user_id]
            try:
                cur = self.exchange.fetch_ticker(t['symbol'])['last']
            except:
                await asyncio.sleep(10)
                continue
            profit = ((cur - t['entry'])/t['entry']*100) if t['side'] == 'long' else ((t['entry'] - cur)/t['entry']*100)
            if profit >= 0.8 and t['stage'] == 0:
                t['sl'] = t['entry']
                t['stage'] = 1
                await context.bot.send_message(user_id, "BreakEven Locked!")
            elif profit >= 1.5 and t['stage'] == 1:
                t['sl'] = t['entry']*1.008 if t['side'] == 'long' else t['entry']*0.992
                t['stage'] = 2
                await context.bot.send_message(user_id, "+0.8% Locked!")
            elif profit >= 2.5 and t['stage'] == 2:
                t['sl'] = t['entry']*1.015 if t['side'] == 'long' else t['entry']*0.985
                t['stage'] = 3
                await context.bot.send_message(user_id, "+1.5% Locked!")
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
                    await context.bot.send_message(user_id, f"2 Opposite Candles! Close {cur}")
                    del self.active_trades[user_id]
                    break
            except:
                pass
            if (t['side'] == 'long' and cur <= t['sl']) or (t['side'] == 'short' and cur >= t['sl']):
                await context.bot.send_message(user_id, f"SL Hit @ {cur}")
                del self.active_trades[user_id]
                break
            await asyncio.sleep(30)

    @authorized_only
    async def close(self, update, context):
        if update.effective_user.id in self.active_trades:
            del self.active_trades[update.effective_user.id]
            await update.message.reply_text("Closed")
        else:
            await update.message.reply_text("No Trade")

    @authorized_only
    async def status(self, update, context):
        uid = update.effective_user.id
        s = self.get_settings(uid)
        if uid in self.active_trades:
            t = self.active_trades[uid]
            cur = self.exchange.fetch_ticker(t['symbol'])['last']
            await update.message.reply_text(f"{t['symbol']} {t['side'].upper()}\nEntry {t['entry']} | Now {cur}\nSL {t['sl']:.2f}")
        else:
            await update.message.reply_text(f"No Trade\nDefault: {s['lots']} Lots, {s['leverage']}x, SL {s['sl_percent']}%")

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
    print(f"DELTA Bot Started - Owner: {TELEGRAM_CHAT_ID}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
