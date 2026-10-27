import os
import logging
import asyncio
from functools import wraps
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import ccxt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Bot Running")
    def do_HEAD(self): self.send_response(200); self.end_headers()
    def log_message(self, format, *args): return

def run_server():
    try:
        port = int(os.environ.get("PORT", 10000))
        HTTPServer(('0.0.0.0', port), Handler).serve_forever()
    except: pass

threading.Thread(target=run_server, daemon=True).start()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DELTA_API_KEY = os.environ.get("DELTA_API_KEY")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DELTA_BASE_URL = "https://api.india.delta.exchange"

def authorized_only(func):
    @wraps(func)
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if TELEGRAM_CHAT_ID and str(update.effective_user.id) != str(TELEGRAM_CHAT_ID):
            await update.message.reply_text(f"Unauthorized ID: {update.effective_user.id}")
            return
        return await func(self, update, context)
    return wrapper

class DeltaTradingBot:
    def __init__(self):
        self.exchange = ccxt.delta({'apiKey': DELTA_API_KEY, 'secret': DELTA_API_SECRET, 'enableRateLimit': True})
        try:
            self.exchange.load_markets()
        except: pass
        self.user_settings = {}; self.active_trades = {}; self.pending = {}

    def get_settings(self, user_id: int):
        if user_id not in self.user_settings:
            self.user_settings[user_id] = {"lots": 1.0, "leverage": 20, "sl_percent": 1.2}
        return self.user_settings[user_id]

    def get_symbol(self, inp: str):
        inp = inp.upper()
        if "ETH" in inp: return "ETHUSD"
        return "BTCUSD"

    def fetch_price(self, symbol):
        sym = self.get_symbol(symbol)
        try:
            url = f"{DELTA_BASE_URL}/v2/tickers/{sym}"
            r = requests.get(url, timeout=5)
            data = r.json()
            price = float(data['result']['close'] if 'result' in data else data['close'])
            return sym, price
        except Exception as e:
            logger.error(f"Price fetch error: {e}")
            # Fallback to ccxt
            try:
                ticker = self.exchange.fetch_ticker(sym)
                return sym, ticker['last']
            except:
                raise Exception(f"Cannot fetch {sym}")

    def fetch_ohlcv(self, symbol, timeframe, limit=50):
        sym = self.get_symbol(symbol)
        try:
            # Map timeframe
            tf_map = {'5m': '5m', '15m': '15m', '1h': '1h'}
            tf = tf_map.get(timeframe, '5m')
            url = f"{DELTA_BASE_URL}/v2/history/candles"
            params = {"resolution": tf, "symbol": sym, "start": int((__import__('time').time()-limit*900)*1000), "end": int(__import__('time').time()*1000)}
            r = requests.get(url, params=params, timeout=5)
            data = r.json()
            if 'result' in data:
                closes = [float(x['close']) for x in data['result'][-limit:]]
                return closes
            return []
        except:
            try:
                ohlcv = self.exchange.fetch_ohlcv(sym, timeframe, limit=limit)
                return [x[4] for x in ohlcv]
            except:
                return [50000]*limit

    def ema(self, prices, period):
        if len(prices) < 2: return prices[0] if prices else 0
        k = 2 / (period + 1); ema_val = prices[0]
        for p in prices[1:]: ema_val = p * k + ema_val * (1 - k)
        return ema_val

    async def get_trend(self, symbol):
        try:
            sym = self.get_symbol(symbol)
            c5 = self.fetch_ohlcv(sym, '5m', 50)
            c15 = self.fetch_ohlcv(sym, '15m', 50)
            c1h = self.fetch_ohlcv(sym, '1h', 50)
            if not c5 or len(c5)<20:
                return "SIDEWAYS", "60% WAIT", sym
            s = sum([c5[-1]>self.ema(c5,20), c15[-1]>self.ema(c15,20), c1h[-1]>self.ema(c1h,20), self.ema(c5,20)>self.ema(c5,50)])
            if s>=3: return "BULLISH", f"{75+s*5}% SAFE", sym
            if s<=1: return "BEARISH", f"{75+(4-s)*5}% SAFE", sym
            return "SIDEWAYS", "50% WAIT", sym
        except Exception as e:
            return "UNKNOWN", f"Error", symbol

    @authorized_only
    async def start(self, update, context):
        await update.message.reply_text(f"DELTA Bot Live ID: {update.effective_user.id}\n/trend BTCUSD\n/long BTCUSD\n/short BTCUSD\n/qty /lev /sl /status /close")

    @authorized_only
    async def qty(self, update, context):
        s=self.get_settings(update.effective_user.id)
        if not context.args: await update.message.reply_text(f"Current: {s['lots']} Lots"); return
        s['lots']=float(context.args[0]); await update.message.reply_text(f"Qty: {s['lots']} Lots")

    @authorized_only
    async def lev(self, update, context):
        s=self.get_settings(update.effective_user.id)
        if not context.args: await update.message.reply_text(f"Current: {s['leverage']}x"); return
        s['leverage']=int(context.args[0]); await update.message.reply_text(f"Lev: {s['leverage']}x")

    @authorized_only
    async def sl(self, update, context):
        s=self.get_settings(update.effective_user.id)
        if not context.args: await update.message.reply_text(f"Current SL: {s['sl_percent']}%"); return
        s['sl_percent']=float(context.args[0]); await update.message.reply_text(f"SL: {s['sl_percent']}%")

    @authorized_only
    async def trend(self, update, context):
        sym = context.args[0] if context.args else "BTCUSD"
        await update.message.reply_text(f"Analyzing {sym}...")
        try:
            valid_sym, price = self.fetch_price(sym)
            t, safe, _ = await self.get_trend(valid_sym)
            await update.message.reply_text(f"{valid_sym}\nPrice: {price}\nTrend: {t}\nSafety: {safe}")
        except Exception as e:
            await update.message.reply_text(f"Error: {str(e)[:150]}")

    @authorized_only
    async def trade(self, update, context):
        uid=update.effective_user.id; s=self.get_settings(uid)
        side="long" if update.message.text.lower().startswith("/long") else "short"
        sym=context.args[0] if context.args else "BTCUSD"
        lots=float(context.args[1]) if len(context.args)>1 else s['lots']
        try:
            valid_sym, price = self.fetch_price(sym)
            trend, safe, _ = await self.get_trend(valid_sym)
            init_sl=price*(1-s['sl_percent']/100) if side=='long' else price*(1+s['sl_percent']/100)
            self.pending[uid]={"side":side,"symbol":valid_sym,"lots":lots,"leverage":s['leverage'],"sl_percent":s['sl_percent'],"price":price,"init_sl":init_sl}
            kb=[[InlineKeyboardButton("YES", callback_data="yes"), InlineKeyboardButton("NO", callback_data="no")]]
            await update.message.reply_text(f"{valid_sym} {side.upper()}\n{lots} Lots | {s['leverage']}x\nPrice {price}\nSL {init_sl:.2f} ({s['sl_percent']}%)\nTrend {trend} | {safe}\n\nConfirm?", reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            await update.message.reply_text(f"Setup failed: {e}")

    @authorized_only
    async def confirm(self, update, context):
        q=update.callback_query; await q.answer(); uid=q.from_user.id
        if uid not in self.pending: await q.edit_message_text("Expired"); return
        if q.data=="no": del self.pending[uid]; await q.edit_message_text("Cancelled"); return
        d=self.pending[uid]; await q.edit_message_text(f"Setting {d['leverage']}x...")
        self.active_trades[uid]={**d,"entry":d['price'],"sl":d['init_sl'],"stage":0,"opp":0}
        await context.bot.send_message(uid, f"ENTERED {d['symbol']} {d['side'].upper()}\nEntry {d['price']}\nSL {d['init_sl']:.2f} ACTIVE"); del self.pending[uid]; asyncio.create_task(self.monitor(uid, context))

    async def monitor(self, user_id, context):
        while user_id in self.active_trades:
            t=self.active_trades[user_id]
            try: _, cur = self.fetch_price(t['symbol'])
            except: await asyncio.sleep(10); continue
            profit=((cur-t['entry'])/t['entry']*100) if t['side']=='long' else ((t['entry']-cur)/t['entry']*100)
            if profit>=0.8 and t['stage']==0: t['sl']=t['entry']; t['stage']=1; await context.bot.send_message(user_id,"BreakEven locked")
            elif profit>=1.5 and t['stage']==1: t['sl']=t['entry']*1.008 if t['side']=='long' else t['entry']*0.992; t['stage']=2; await context.bot.send_message(user_id,"+0.8% locked")
            elif profit>=2.5 and t['stage']==2: t['sl']=t['entry']*1.015 if t['side']=='long' else t['entry']*0.985; t['stage']=3; await context.bot.send_message(user_id,"+1.5% locked")
            if (t['side']=='long' and cur<=t['sl']) or (t['side']=='short' and cur>=t['sl']): await context.bot.send_message(user_id,f"SL Hit @ {cur}"); del self.active_trades[user_id]; break
            await asyncio.sleep(30)

    @authorized_only
    async def close(self, update, context):
        if update.effective_user.id in self.active_trades: del self.active_trades[update.effective_user.id]; await update.message.reply_text("Closed")
        else: await update.message.reply_text("No Trade")

    @authorized_only
    async def status(self, update, context):
        uid=update.effective_user.id; s=self.get_settings(uid)
        if uid in self.active_trades:
            t=self.active_trades[uid]
            try:
                _, cur = self.fetch_price(t['symbol']); pnl=((cur-t['entry'])/t['entry']*100) if t['side']=='long' else ((t['entry']-cur)/t['entry']*100)
                await update.message.reply_text(f"{t['symbol']} {t['side'].upper()}\nEntry {t['entry']} | Now {cur}\nPnL {pnl:.2f}%\nSL {t['sl']:.2f}")
            except Exception as e: await update.message.reply_text(f"Error {e}")
        else: await update.message.reply_text(f"No Trade\nDefaults: {s['lots']} Lots, {s['leverage']}x, SL {s['sl_percent']}%")

def main():
    bot=DeltaTradingBot()
    app=Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", bot.start)); app.add_handler(CommandHandler("qty", bot.qty)); app.add_handler(CommandHandler("lev", bot.lev)); app.add_handler(CommandHandler("sl", bot.sl)); app.add_handler(CommandHandler("trend", bot.trend)); app.add_handler(CommandHandler("long", bot.trade)); app.add_handler(CommandHandler("short", bot.trade)); app.add_handler(CommandHandler("close", bot.close)); app.add_handler(CommandHandler("status", bot.status)); app.add_handler(CallbackQueryHandler(bot.confirm))
    print(f"DELTA Bot Started - Owner: {TELEGRAM_CHAT_ID}")
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__": main()
