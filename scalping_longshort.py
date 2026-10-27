import os
import logging
import asyncio
from functools import wraps
import ccxt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ================= ENV VARIABLES FROM RENDER =================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DELTA_API_KEY = os.environ.get("DELTA_API_KEY")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET")

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= SECURITY DECORATOR =================
def authorized_only(func):
    @wraps(func)
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if TELEGRAM_CHAT_ID and user_id != TELEGRAM_CHAT_ID:
            await update.message.reply_text(f"❌ Unauthorized! Your ID: {user_id}")
            return
        return await func(self, update, context)
    return wrapper

# ================= MAIN BOT CLASS =================
class DeltaTradingBot:
    def __init__(self):
        # Delta Exchange Connection
        self.exchange = ccxt.delta({
            'apiKey': DELTA_API_KEY,
            'secret': DELTA_API_SECRET,
            'enableRateLimit': True,
        })
        self.user_settings = {}
        self.active_trades = {}
        self.pending = {}
        logger.info(f"DELTA Bot Started - Owner: {TELEGRAM_CHAT_ID}")

    def get_settings(self, user_id: int):
        # Default Settings
        if user_id not in self.user_settings:
            self.user_settings[user_id] = {
                "lots": 1.0,
                "leverage": 20,
                "sl_percent": 1.2
            }
        return self.user_settings[user_id]

    def ema(self, prices, period):
        # EMA Calculation Without Pandas - Fast Method
        k = 2 / (period + 1)
        ema_val = prices[0]
        for price in prices[1:]:
            ema_val = price * k + ema_val * (1 - k)
        return ema_val

    async def get_trend(self, symbol: str):
        # Multi-Timeframe Trend Analysis
        try:
            ohlcv_5m = self.exchange.fetch_ohlcv(symbol, '5m', limit=50)
            ohlcv_15m = self.exchange.fetch_ohlcv(symbol, '15m', limit=50)
            ohlcv_1h = self.exchange.fetch_ohlcv(symbol, '1h', limit=50)
            
            c5 = [x[4] for x in ohlcv_5m]
            c15 = [x[4] for x in ohlcv_15m]
            c1h = [x[4] for x in ohlcv_1h]
            
            e20_5 = self.ema(c5, 20)
            e50_5 = self.ema(c5, 50)
            e20_15 = self.ema(c15, 20)
            e20_1h = self.ema(c1h, 20)
            
            score = sum([
                c5[-1] > e20_5,
                c15[-1] > e20_15,
                c1h[-1] > e20_1h,
                e20_5 > e50_5
            ])
            
            if score >= 3:
                return "BULLISH 📈", f"{75+score*5}% SAFE ✅"
            if score <= 1:
                return "BEARISH 📉", f"{75+(4-score)*5}% SAFE ✅"
            return "SIDEWAYS ⚖️", "50% NOT SAFE ⚠️"
        except Exception as e:
            logger.error(f"Trend Error {e}")
            return "UNKNOWN", "Check"

    @authorized_only
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            f"👋 **DELTA Bot Ready Anna!**\n"
            f"🆔 ID: {update.effective_user.id}\n"
            f"/trend BTCUSD\n/long BTCUSD\n/short BTCUSD\n"
            f"/qty /lev /sl /status /close",
            parse_mode='Markdown'
        )

    @authorized_only
    async def qty(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        s = self.get_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current: {s['lots']} Lots")
            return
        s['lots'] = float(context.args[0])
        await update.message.reply_text(f"✅ Qty: {s['lots']} Lots")

    @authorized_only
    async def lev(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        s = self.get_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current: {s['leverage']}x")
            return
        s['leverage'] = int(context.args[0])
        await update.message.reply_text(f"✅ Lev: {s['leverage']}x")

    @authorized_only
    async def sl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        s = self.get_settings(update.effective_user.id)
        if not context.args:
            await update.message.reply_text(f"Current SL: {s['sl_percent']}%")
            return
        s['sl_percent'] = float(context.args[0])
        await update.message.reply_text(f"✅ SL: {s['sl_percent']}%")

    @authorized_only
    async def trend(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        sym = context.args[0].upper() if context.args else "BTCUSD"
        symbol = "BTCUSD" if "BTC" in sym else "ETHUSD"
        await update.message.reply_text(f"🔍 Analyzing {symbol}...")
        trend, safe = await self.get_trend(symbol)
        price = self.exchange.fetch_ticker(symbol)['last']
        await update.message.reply_text(
            f"📊 **{symbol}**\n💰 {price}\n📈 {trend}\n🛡️ {safe}",
            parse_mode='Markdown'
        )

    @authorized_only
    async def trade(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        s = self.get_settings(uid)
        side = "long" if update.message.text.lower().startswith("/long") else "short"
        sym = context.args[0].upper() if context.args else "BTCUSD"
        symbol = "BTCUSD" if "BTC" in sym else "ETHUSD"
        lots = float(context.args[1]) if len(context.args) > 1 else s['lots']
        
        # Get Trend and Price
        trend, safe = await self.get_trend(symbol)
        price = self.exchange.fetch_ticker(symbol)['last']
        
        # Calculate Initial SL - Trade edutha udane SL Active
        init_sl = price * (1 - s['sl_percent']/100) if side == 'long' else price * (1 + s['sl_percent']/100)
        
        self.pending[uid] = {
            "side": side, "symbol": symbol, "lots": lots,
            "leverage": s['leverage'], "sl_percent": s['sl_percent'],
            "price": price, "init_sl": init_sl
        }
        
        kb = [[
            InlineKeyboardButton("✅ YES", callback_data="yes"),
            InlineKeyboardButton("❌ NO", callback_data="no")
        ]]
        
        await update.message.reply_text(
            f"🎯 **DELTA {symbol} {side.upper()}**\n"
            f"📦 {lots} Lots | ⚡ {s['leverage']}x\n"
            f"💰 {price}\n🛑 SL: {init_sl:.2f} ({s['sl_percent']}%)\n"
            f"📊 {trend}\n🛡️ {safe}\n\nConfirm?",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    @authorized_only
    async def confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        uid = q.from_user.id
        if uid not in self.pending:
            await q.edit_message_text("Expired")
            return
        if q.data == "no":
            del self.pending[uid]
            await q.edit_message_text("❌ Cancelled")
            return
        
        d = self.pending[uid]
        await q.edit_message_text(f"⚙️ Setting {d['leverage']}x...")
        
        # Active Trade Start - SL Immediately Active
        self.active_trades[uid] = {
            **d, "entry": d['price'], "sl": d['init_sl'],
            "stage": 0, "opp": 0
        }
        
        await context.bot.send_message(
            uid,
            f"✅ ENTERED {d['symbol']} {d['side'].upper()}\n"
            f"Entry {d['price']}\n🛑 SL {d['init_sl']:.2f} ACTIVE ✅"
        )
        del self.pending[uid]
        asyncio.create_task(self.monitor(uid, context))

    async def monitor(self, user_id: int, context: ContextTypes.DEFAULT_TYPE):
        # Trailing SL + Opposite Candle Monitor
        while user_id in self.active_trades:
            t = self.active_trades[user_id]
            try:
                cur = self.exchange.fetch_ticker(t['symbol'])['last']
            except:
                await asyncio.sleep(10)
                continue
            
            profit = ((cur - t['entry'])/t['entry']*100) if t['side']=='long' else ((t['entry'] - cur)/t['entry']*100)
            
            if profit >=0.8 and t['stage']==0:
                t['sl']=t['entry']; t['stage']=1
                await context.bot.send_message(user_id, f"🔒 BreakEven!")
            elif profit >=1.5 and t['stage']==1:
                t['sl']=t['entry']*1.008 if t['side']=='long' else t['entry']*0.992
                t['stage']=2
                await context.bot.send_message(user_id, f"🔒 +0.8% Locked!")
            elif profit >=2.5 and t['stage']==2:
                t['sl']=t['entry']*1.015 if t['side']=='long' else t['entry']*0.985
                t['stage']=3
                await context.bot.send_message(user_id, f"🔒 +1.5% Locked!")
            
            try:
                ohlcv = self.exchange.fetch_ohlcv(t['symbol'], '5m', limit=3)
                last = ohlcv[-2]
                green = last[4] > last[1]
                if t['side']=='long' and not green:
                    t['opp']+=1
                elif t['side']=='short' and green:
                    t['opp']+=1
                else:
                    t['opp']=0
                if t['opp']>=2:
                    await context.bot.send_message(user_id, f"⚠️ 2 Opposite Candles! Close @ {cur}")
                    del self.active_trades[user_id]
                    break
            except:
                pass
            
            if (t['side']=='long' and cur<=t['sl']) or (t['side']=='short' and cur>=t['sl']):
                await context.bot.send_message(user_id, f"✅ SL Hit @ {cur}")
                del self.active_trades[user_id]
                break
            
            await asyncio.sleep(30)

    @authorized_only
    async def close(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id in self.active_trades:
            del self.active_trades[update.effective_user.id]
            await update.message.reply_text("✅ Closed")
        else:
            await update.message.reply_text("No Active Trade")

    @authorized_only
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        s = self.get_settings(uid)
        if uid in self.active_trades:
            t = self.active_trades[uid]
            cur = self.exchange.fetch_ticker(t['symbol'])['last']
            await update.message.reply_text(f"📊 {t['symbol']} {t['side'].upper()}\nEntry {t['entry']} | Now {cur}\nSL {t['sl']:.2f}")
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
    app.run_polling()

if __name__ == "__main__":
    main()
