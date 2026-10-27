import os
import logging
import asyncio
from typing import Dict, Tuple
from functools import wraps
import ccxt
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ================= RENDER ENV - DELTA =================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DELTA_API_KEY = os.environ.get("DELTA_API_KEY")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET")

# Bitget old keys iruntha ignore pannum
# BITGET_API_KEY = os.environ.get("BITGET_API_KEY") - DELETE IT

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

if not TELEGRAM_TOKEN: logger.error("TELEGRAM_TOKEN missing in Render!")
if not DELTA_API_KEY: logger.error("DELTA_API_KEY missing in Render!")

# ================= AUTH - ONLY YOU =================
def authorized_only(func):
    @wraps(func)
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if TELEGRAM_CHAT_ID and user_id != TELEGRAM_CHAT_ID:
            await update.message.reply_text(f"❌ Unauthorized! Your ID: {user_id}\nOwner only!")
            logger.warning(f"Blocked: {user_id}")
            return
        return await func(self, update, context)
    return wrapper

class Config:
    DEFAULT_LOTS = 1.0  # 1 Lot = 1 Contract = 0.001 BTC
    DEFAULT_LEVERAGE = 20
    DEFAULT_SL_PERCENT = 1.2
    PRODUCT_IDS = {"BTCUSD": 27, "BTCUSDT": 27, "ETHUSD": 139, "ETHUSDT": 139}
    SYMBOL_MAP = {"BTC": "BTCUSD", "ETH": "ETHUSD", "BTCUSDT": "BTCUSD", "ETHUSDT": "ETHUSD"}

class DeltaTradingBot:
    def __init__(self):
        self.exchange = ccxt.delta({
            'apiKey': DELTA_API_KEY,
            'secret': DELTA_API_SECRET,
            'enableRateLimit': True,
        })
        self.user_settings: Dict[int, Dict] = {}
        self.active_trades: Dict[int, Dict] = {}
        self.pending: Dict[int, Dict] = {}
        logger.info(f"DELTA Bot Started - Owner Chat ID: {TELEGRAM_CHAT_ID}")

    def get_settings(self, user_id: int) -> Dict:
        if user_id not in self.user_settings:
            self.user_settings[user_id] = {"lots": Config.DEFAULT_LOTS, "leverage": Config.DEFAULT_LEVERAGE, "sl_percent": Config.DEFAULT_SL_PERCENT}
        return self.user_settings[user_id]

    def normalize_symbol(self, sym: str) -> str:
        sym = sym.upper()
        return Config.SYMBOL_MAP.get(sym, "BTCUSD" if "BTC" in sym else "ETHUSD" if "ETH" in sym else "BTCUSD")

    async def get_market_trend(self, symbol: str) -> Tuple[str, str]:
        try:
            # Delta symbols: BTCUSD, ETHUSD
            ohlcv_5m = self.exchange.fetch_ohlcv(symbol, '5m', limit=50)
            ohlcv_15m = self.exchange.fetch_ohlcv(symbol, '15m', limit=50)
            ohlcv_1h = self.exchange.fetch_ohlcv(symbol, '1h', limit=50)
            def calc(data):
                df = pd.DataFrame(data, columns=['t','o','h','l','c','v'])
                df['ema20'] = df['c'].ewm(span=20).mean(); df['ema50'] = df['c'].ewm(span=50).mean(); return df
            df_5, df_15, df_1h = calc(ohlcv_5m), calc(ohlcv_15m), calc(ohlcv_1h)
            score = sum([df_5['c'].iloc[-1] > df_5['ema20'].iloc[-1], df_15['c'].iloc[-1] > df_15['ema20'].iloc[-1], df_1h['c'].iloc[-1] > df_1h['ema20'].iloc[-1], df_5['ema20'].iloc[-1] > df_5['ema50'].iloc[-1]])
            if score >= 3: return "BULLISH 📈", f"{75+score*5}% SAFE ✅"
            if score <= 1: return "BEARISH 📉", f"{75+(4-score)*5}% SAFE ✅"
            return "SIDEWAYS ⚖️", "50% NOT SAFE ⚠️"
        except Exception as e: logger.error(f"Trend Error {e}"); return "UNKNOWN", "Check"

    async def set_leverage_delta(self, symbol: str, leverage: int) -> bool:
        try:
            pid = Config.PRODUCT_IDS.get(symbol, 27)
            self.exchange.private_post_v2_products_product_id_orders_leverage({
                'product_id': pid, 'leverage': leverage, 'margin_mode': 'isolated'
            })
            logger.info(f"Delta Leverage {symbol} -> {leverage}x")
            return True
        except Exception as e: logger.error(f"Delta Lev Error: {e}"); return False

    @authorized_only
    async def start_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"👋 **DELTA Scalp Bot Ready!**\n🆔 Chat ID: `{update.effective_user.id}`\n\n📋 Commands:\n/trend BTCUSD - Trend Check\n/long BTCUSD - Long\n/short BTCUSD - Short\n/qty 2 - Set Lots (1 Lot = 0.001 BTC)\n/lev 20 - Set Leverage\n/sl 1.2 - Set Initial SL %\n/status - Active Trade\n/close - Close Trade", parse_mode='Markdown')

    @authorized_only
    async def qty_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        s = self.get_settings(update.effective_user.id)
        if not context.args: await update.message.reply_text(f"📦 Current: {s['lots']} Lots (1 Lot = 0.001 BTC = 1 Contract)\nUse: /qty 5"); return
        s['lots'] = float(context.args[0]); await update.message.reply_text(f"✅ Qty: {s['lots']} Lots = {int(s['lots'])} Contracts")

    @authorized_only
    async def lev_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        s = self.get_settings(update.effective_user.id)
        if not context.args: await update.message.reply_text(f"⚡ Current: {s['leverage']}x\nUse: /lev 20"); return
        s['leverage'] = int(context.args[0]); await update.message.reply_text(f"✅ Leverage: {s['leverage']}x (Will set in Delta)")

    @authorized_only
    async def sl_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        s = self.get_settings(update.effective_user.id)
        if not context.args: await update.message.reply_text(f"🛑 Current Initial SL: {s['sl_percent']}%\nUse: /sl 1.2"); return
        s['sl_percent'] = float(context.args[0]); await update.message.reply_text(f"✅ Initial SL: {s['sl_percent']}%")

    @authorized_only
    async def trend_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        symbol = self.normalize_symbol(context.args[0] if context.args else "BTCUSD")
        await update.message.reply_text(f"🔍 Analyzing Delta {symbol}...")
        trend, safe = await self.get_market_trend(symbol); price = self.exchange.fetch_ticker(symbol)['last']
        dec = "✅ LONG ku /long" if "BULLISH" in trend and "SAFE" in safe else "✅ SHORT ku /short" if "BEARISH" in trend and "SAFE" in safe else "⚠️ Wait - Sideways"
        await update.message.reply_text(f"📊 **DELTA {symbol}**\n💰 Price: {price}\n📈 Trend: {trend}\n🛡️ Safety: {safe}\n\n💡 {dec}", parse_mode='Markdown')

    @authorized_only
    async def trade_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id; s = self.get_settings(uid)
        side = "long" if update.message.text.lower().startswith("/long") else "short"
        symbol = self.normalize_symbol(context.args[0] if context.args else "BTCUSD")
        lots = float(context.args[1]) if len(context.args) > 1 else s['lots']
        
        trend, safe = await self.get_market_trend(symbol)
        price = self.exchange.fetch_ticker(symbol)['last']
        init_sl = price * (1 - s['sl_percent']/100) if side == 'long' else price * (1 + s['sl_percent']/100)

        self.pending[uid] = {"side": side, "symbol": symbol, "lots": lots, "contracts": int(lots), "leverage": s['leverage'], "sl_percent": s['sl_percent'], "price": price, "init_sl": init_sl}
        kb = [[InlineKeyboardButton("✅ YES", callback_data="yes"), InlineKeyboardButton("❌ NO", callback_data="no")]]
        await update.message.reply_text(f"🎯 **DELTA {symbol} {side.upper()}**\n📦 {lots} Lots = {int(lots)} Contracts (0.00{lots} BTC)\n⚡ {s['leverage']}x | 💰 {price}\n🛑 Initial SL: {init_sl:.2f} ({s['sl_percent']}%)\n\n📊 {trend}\n🛡️ {safe}\n\nConfirm?", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    @authorized_only
    async def confirm_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer(); uid = q.from_user.id
        if str(uid) != str(TELEGRAM_CHAT_ID) and TELEGRAM_CHAT_ID: return
        if uid not in self.pending: await q.edit_message_text("Expired"); return
        if q.data == "no": del self.pending[uid]; await q.edit_message_text("❌ Cancelled"); return
        d = self.pending[uid]; await q.edit_message_text(f"⚙️ Setting Delta {d['leverage']}x leverage..."); await self.set_leverage_delta(d['symbol'], d['leverage'])
        
        # LIVE DELTA ORDER (Uncomment for real trading)
        # side_ccxt = 'buy' if d['side']=='long' else 'sell'
        # amount = d['contracts']  # Delta la contracts
        # self.exchange.create_market_order(d['symbol'], side_ccxt, amount)

        self.active_trades[uid] = {**d, "entry": d['price'], "sl": d['init_sl'], "stage": 0, "opp": 0}
        await context.bot.send_message(uid, f"✅ ENTERED DELTA {d['symbol']} {d['side'].upper()}\nEntry {d['price']}\nInitial SL {d['init_sl']:.2f} ({d['sl_percent']}%)\n{d['lots']} Lots | {d['leverage']}x\nTrailing Active 🔒")
        del self.pending[uid]; asyncio.create_task(self.monitor_trade(uid, context))

    async def monitor_trade(self, user_id: int, context: ContextTypes.DEFAULT_TYPE):
        while user_id in self.active_trades:
            t = self.active_trades[user_id]
            try: cur = self.exchange.fetch_ticker(t['symbol'])['last']
            except: await asyncio.sleep(10); continue
            profit = ((cur - t['entry'])/t['entry']*100) if t['side']=='long' else ((t['entry'] - cur)/t['entry']*100)
            if profit >=0.8 and t['stage']==0: t['sl']=t['entry']; t['stage']=1; await context.bot.send_message(user_id, f"🔒 {t['symbol']} BreakEven! Entry ku SL move")
            elif profit >=1.5 and t['stage']==1: t['sl']=t['entry']*1.008 if t['side']=='long' else t['entry']*0.992; t['stage']=2; await context.bot.send_message(user_id, f"🔒 +0.8% Locked!")
            elif profit >=2.5 and t['stage']==2: t['sl']=t['entry']*1.015 if t['side']=='long' else t['entry']*0.985; t['stage']=3; await context.bot.send_message(user_id, f"🔒 +1.5% Locked!")
            try:
                ohlcv = self.exchange.fetch_ohlcv(t['symbol'], '5m', limit=3); last = ohlcv[-2]; green = last[4] > last[1]
                if t['side']=='long' and not green: t['opp']+=1
                elif t['side']=='short' and green: t['opp']+=1
                else: t['opp']=0
                if t['opp']>=2: await context.bot.send_message(user_id, f"⚠️ {t['symbol']} 2 Opposite 5m Candles! Auto Close @ {cur}"); del self.active_trades[user_id]; break
            except: pass
            if t['sl']!=0 and ((t['side']=='long' and cur<=t['sl']) or (t['side']=='short' and cur>=t['sl'])):
                await context.bot.send_message(user_id, f"✅ {t['symbol']} SL Hit @ {cur}"); del self.active_trades[user_id]; break
            await asyncio.sleep(30)

    @authorized_only
    async def close_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id in self.active_trades: del self.active_trades[update.effective_user.id]; await update.message.reply_text("✅ Trade Closed Manually")
        else: await update.message.reply_text("No Active Trade")

    @authorized_only
    async def status_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id; s = self.get_settings(uid)
        if uid in self.active_trades: t = self.active_trades[uid]; cur = self.exchange.fetch_ticker(t['symbol'])['last']; await update.message.reply_text(f"📊 DELTA {t['symbol']} {t['side'].upper()}\nEntry {t['entry']} | Now {cur}\nSL {t['sl']:.2f} | {t['lots']} Lots")
        else: await update.message.reply_text(f"📦 No Active Trade\nDefault: {s['lots']} Lots, {s['leverage']}x, SL {s['sl_percent']}%")

def main():
    if not TELEGRAM_TOKEN: logger.error("Set TELEGRAM_TOKEN!"); return
    if not DELTA_API_KEY: logger.error("Set DELTA_API_KEY & DELTA_API_SECRET!"); return
    bot = DeltaTradingBot()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", bot.start_handler))
    app.add_handler(CommandHandler("qty", bot.qty_handler)); app.add_handler(CommandHandler("lev", bot.lev_handler))
    app.add_handler(CommandHandler("sl", bot.sl_handler)); app.add_handler(CommandHandler("trend", bot.trend_handler))
    app.add_handler(CommandHandler("long", bot.trade_handler)); app.add_handler(CommandHandler("short", bot.trade_handler))
    app.add_handler(CommandHandler("close", bot.close_handler)); app.add_handler(CommandHandler("status", bot.status_handler))
    app.add_handler(CallbackQueryHandler(bot.confirm_handler))
    logger.info(f"DELTA Bot Running on Render - Chat ID: {TELEGRAM_CHAT_ID}"); app.run_polling()

if __name__ == "__main__": main()
