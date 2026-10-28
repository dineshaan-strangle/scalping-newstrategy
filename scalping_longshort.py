import os, time, hmac, hashlib, requests, json, asyncio, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

K = os.getenv("DELTA_API_KEY")
S = os.getenv("DELTA_API_SECRET")
T = os.getenv("TELEGRAM_TOKEN")
B = "https://api.india.delta.exchange"
BC = "https://cdn.india.deltaex.org"
SL = 1.2
TP = 2.0
TRAIL_TP = 0.6

USER_QTY = {}
USER_LEV = {}
ALERT_USERS = set()
BEST_PRICE = {}
ENTRY_PRICE = {}
LAST_POS_SIZE = {}
LAST_SL = {}
LAST_TP = {}
ENTRY_TIME = {}
IS_CLOSING = {}
LAST_ORDER_ID = {}
TRAILING_ACTIVE = {}
LAST_ALERT_TIME = 0
LAST_ALERT_SIGNAL = ""

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self): 
        self.send_response(200); self.send_header('Content-type','text/plain'); self.end_headers(); self.wfile.write(b'BOT OK V119.5 SL TRAIL + NO ALERT WHEN LIVE')
    def do_HEAD(self): 
        self.send_response(200); self.send_header('Content-type','text/plain'); self.end_headers()
    def log_message(self, format, *args): return

def run_health_server():
    try: HTTPServer(('0.0.0.0', int(os.getenv("PORT",10000))), HealthHandler).serve_forever()
    except: pass
threading.Thread(target=run_health_server, daemon=True).start()

def sig(m,t,p,q,b,s): return hmac.new(s.encode(), (m+t+p+q+b).encode(), hashlib.sha256).hexdigest()
def hdr(m,p,q="",b=""):
    t=str(int(time.time())); return {"api-key":K,"timestamp":t,"signature":sig(m,t,p,q,b,S),"Content-Type":"application/json"}
def price():
    try: return float(requests.get(f"{B}/v2/tickers/BTCUSD", timeout=5).json()['result']['mark_price'])
    except: return 80600.0

def order(d):
    try:
        p="/v2/orders"; b=json.dumps(d, separators=(',', ':')); h=hdr("POST",p,"",b)
        r=requests.post(B+p, headers=h, data=b, timeout=10)
        print(f"ORDER {b} => {r.text}")
        return r.json()
    except Exception as ex:
        print(f"ORDER EXCEPTION {ex}")
        return {"success": False}

def cancel_all_orders():
    try:
        p="/v2/orders/all"; b=json.dumps({"product_id":27,"cancel_stop_orders":True,"cancel_limit_orders":True}, separators=(',', ':'))
        h=hdr("DELETE",p,"",b); requests.delete(B+p, headers=h, data=b, timeout=5)
    except: pass

def set_lev(lev):
    try: p="/v2/products/27"; b=json.dumps({"leverage":str(lev)}, separators=(',', ':')); h=hdr("PUT",p,"",b); requests.put(B+p, headers=h, data=b, timeout=5)
    except: pass

def get_balance():
    try:
        h=hdr("GET","/v2/wallet/balances"); r=requests.get(B+"/v2/wallet/balances", headers=h, timeout=5).json()
        for x in r.get('result',[]):
            if x.get('asset_symbol')=='USD': return float(x.get('available_balance',0))
        return 0
    except: return 0

def get_real_position():
    sz=0; entry=0; upnl=0
    try:
        h=hdr("GET","/v2/positions"); pos=requests.get(B+"/v2/positions", headers=h, timeout=10).json()
        for x in pos.get('result',[]):
            s=float(x.get('size',0))
            if abs(s)>0.0000001:
                sym = str(x.get('product_symbol','') or '').upper()
                if 'BTC' in sym:
                    sz=s; entry=float(x.get('entry_price',0)); upnl=float(x.get('unrealized_pnl',0))
        try:
            h2=hdr("GET","/v2/positions/margined","","")
            pos2=requests.get(B+"/v2/positions/margined", headers=h2, timeout=10).json()
            for x in (pos2.get('result',[]) if isinstance(pos2.get('result'), list) else []):
                s=float(x.get('size',0))
                sym = str(x.get('product_symbol','') or '').upper()
                if abs(s)>0.0000001 and 'BTC' in sym:
                    sz=s; entry=float(x.get('entry_price',0)); upnl=float(x.get('unrealized_pnl',0))
        except: pass
        return sz, entry, upnl
    except: return 0,0,0

def size_to_lots(real_size):
    if abs(real_size) < 1: return int(abs(real_size)*1000)
    else: return int(abs(real_size))

def fetch_candles_full(res="5m", count=100):
    closes, highs, lows = [], [], []
    cur_live = price()
    try:
        now=int(time.time())
        try:
            url=f"{B}/v2/history/candles"
            params={"resolution":res,"symbol":"MARK:BTCUSD","start":now-86400*3,"end":now}
            r=requests.get(url, params=params, timeout=8).json()
            candles=r.get('result',[]) if isinstance(r, dict) else r
            if len(candles)>10:
                closes = [float(c.get('close',0)) for c in candles if float(c.get('close',0))>0]
                highs = [float(c.get('high',0)) for c in candles if float(c.get('high',0))>0]
                lows = [float(c.get('low',0)) for c in candles if float(c.get('low',0))>0]
        except: pass
        if len(closes)<20 or (closes and abs(closes[-1]-cur_live)/cur_live>0.015):
            try:
                secs = {"1m":60,"5m":300,"15m":900,"1h":3600}
                frm = now - (secs.get(res.lower(),300)*count) - 1000
                url=f"{BC}/v2/history/candles"; params={"resolution":res.lower(),"symbol":"BTCUSD","start":frm,"end":now}
                r=requests.get(url, params=params, timeout=8).json()
                candles=r if isinstance(r,list) else r.get('result',[])
                if len(candles)>20:
                    closes = [float(c.get('close',0)) for c in candles if float(c.get('close',0))>0]
                    highs = [float(c.get('high',0)) for c in candles if float(c.get('high',0))>0]
                    lows = [float(c.get('low',0)) for c in candles if float(c.get('low',0))>0]
            except: pass
        if closes and abs(closes[-1]-cur_live)/cur_live > 0.008:
            closes[-1] = cur_live
            highs[-1] = max(highs[-1], cur_live)
            lows[-1] = min(lows[-1], cur_live)
        return closes[-count:], highs[-count:], lows[-count:]
    except:
        return [],[],[]

def calc_ema(prices, period):
    if len(prices) < period: return prices[-1] if prices else 0
    k = 2/(period+1)
    ema = sum(prices[:period])/period
    for p in prices[period:]:
        ema = p*k + ema*(1-k)
    return ema

def calc_rsi(prices, period=14):
    if len(prices) < period+1: return 50
    gains=0; losses=0
    for i in range(1, period+1):
        diff = prices[-i] - prices[-i-1]
        if diff>0: gains+=diff
        else: losses+=-diff
    if losses==0: return 70 if gains>0 else 50
    rs = (gains/period)/(losses/period)
    rsi = 100 - (100/(1+rs))
    return round(rsi,1)

def calc_supertrend(closes, highs, lows, period=10, mult=3):
    try:
        if len(closes)<period: return "SIDE", closes[-1] if closes else 0
        tr_list=[]
        for i in range(1,len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            tr_list.append(tr)
        atr = sum(tr_list[-period:])/period if len(tr_list)>=period else sum(tr_list)/len(tr_list) if tr_list else 0
        hl2 = (highs[-1]+lows[-1])/2
        upper = hl2 + mult*atr
        lower = hl2 - mult*atr
        if closes[-1] > upper: return "GREEN BULLISH", lower
        elif closes[-1] < lower: return "RED BEARISH", upper
        else:
            if closes[-1] > closes[-2]: return "GREEN BULLISH", lower
            else: return "RED BEARISH", upper
    except: return "SIDE", 0

def get_pro_signal(res_label):
    closes,highs,lows = fetch_candles_full(res_label, 100)
    if len(closes)<25:
        return {"ema9":0,"ema21":0,"ema_sig":"SIDEWAYS","rsi":50,"st":"SIDE","st_val":0,"price":price()}
    ema9 = calc_ema(closes,9)
    ema21 = calc_ema(closes,21)
    rsi = calc_rsi(closes,14)
    st_trend, st_val = calc_supertrend(closes,highs,lows,10,3)
    cur = closes[-1]
    ema_sig = "BULLISH" if ema9>ema21 else "BEARISH" if ema9<ema21 else "SIDEWAYS"
    return {"ema9":ema9,"ema21":ema21,"ema_sig":ema_sig,"rsi":rsi,"st":st_trend,"st_val":st_val,"price":cur}

def calc_score(s5,s15,s60):
    long_score=0; short_score=0
    if s5['ema_sig']=="BULLISH": long_score+=20
    elif s5['ema_sig']=="BEARISH": short_score+=20
    if s5['rsi']<35: long_score+=15
    elif s5['rsi']>65: short_score+=15
    if "GREEN" in s5['st']: long_score+=15
    elif "RED" in s5['st']: short_score+=15
    if s15['ema_sig']=="BULLISH": long_score+=12
    elif s15['ema_sig']=="BEARISH": short_score+=12
    if "GREEN" in s15['st']: long_score+=18
    elif "RED" in s15['st']: short_score+=18
    if s60['ema_sig']=="BULLISH": long_score+=10
    elif s60['ema_sig']=="BEARISH": short_score+=10
    if "GREEN" in s60['st']: long_score+=10
    elif "RED" in s60['st']: short_score+=10
    total=long_score+short_score
    if total==0: return 50,50
    long_pct=int(long_score/total*100)
    return long_pct, 100-long_pct

async def auto_alert_job(context: ContextTypes.DEFAULT_TYPE):
    global LAST_ALERT_TIME, LAST_ALERT_SIGNAL
    try:
        # FIX 2: ORDER LIVE LA IRUKKA ALERT VARA KUDATHU
        live_sz,_,_=get_real_position()
        if abs(live_sz)>0.0000001:
            return  # Position open -> No alert
        if time.time() - LAST_ALERT_TIME < 180: return
        s5 = get_pro_signal("5m")
        s15 = get_pro_signal("15m")
        s60 = get_pro_signal("1h")
        long_pct, short_pct = calc_score(s5,s15,s60)
        cur = price()
        signal=""; msg=""
        if long_pct>=70:
            signal=f"LONG_{long_pct}"
            msg=f"🚀 AUTO ALERT V119.5\nBTC: {cur:.1f}\nLONG {long_pct}% STRONG! 🔥\n5M: {s5['ema_sig']} RSI {s5['rsi']} ST {s5['st']}\n15M: {s15['ema_sig']} ST {s15['st']}\n1H: {s60['ema_sig']} RSI {s60['rsi']}\n\n💡 /long adikkalam Anna!"
        elif short_pct>=70:
            signal=f"SHORT_{short_pct}"
            msg=f"📉 AUTO ALERT V119.5\nBTC: {cur:.1f}\nSHORT {short_pct}% STRONG! 🔥\n5M: {s5['ema_sig']} RSI {s5['rsi']} ST {s5['st']}\n15M: {s15['ema_sig']} ST {s15['st']}\n1H: {s60['ema_sig']} RSI {s60['rsi']}\n\n💡 /short adikkalam Anna!"
        if signal and signal != LAST_ALERT_SIGNAL:
            all_users = ALERT_USERS.union(set(USER_QTY.keys()))
            for uid in list(all_users):
                try: await context.bot.send_message(uid, msg)
                except: pass
            LAST_ALERT_TIME=time.time()
            LAST_ALERT_SIGNAL=signal
            print(f"ALERT SENT {signal} to {len(all_users)} users")
    except Exception as e:
        print(f"auto_alert error {e}")

async def check_positions_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        if any(IS_CLOSING.values()): return
        cur_p = price()
        cur_size, entry_real, _ = get_real_position()
        all_uids = set(list(USER_QTY.keys()) + list(LAST_POS_SIZE.keys()) + list(ENTRY_PRICE.keys()) + list(LAST_ORDER_ID.keys()) + list(ALERT_USERS))
        for uid in list(all_uids):
            if IS_CLOSING.get(uid): continue
            if abs(cur_size) < 0.0000001: continue
            entry = ENTRY_PRICE.get(uid, entry_real)
            if entry == 0: entry = entry_real
            if entry == 0: continue
            best = BEST_PRICE.get(uid, entry)
            order_id_saved = LAST_ORDER_ID.get(uid, "No ID")
            is_trailing = TRAILING_ACTIVE.get(uid, False)
            
            # FIX 1: SL TRAILING FROM ENTRY - BEST UPDATE FROM START
            if cur_size > 0:  # LONG
                if cur_p > best:
                    BEST_PRICE[uid] = cur_p
                    best = cur_p
                    try:
                        sl_now = best * (1 - SL/100)
                        if int(cur_p) % 80 < 15:
                            await context.bot.send_message(uid, f"📈 LONG SL TRAIL\nBest {best:.1f} SL {sl_now:.1f} (+{(best-entry):.1f}$)\nEntry {entry:.1f} Order {order_id_saved}")
                    except: pass
            else:  # SHORT
                if cur_p < best:
                    BEST_PRICE[uid] = cur_p
                    best = cur_p
                    try:
                        sl_now = best * (1 + SL/100)
                        if int(cur_p) % 80 < 15:
                            await context.bot.send_message(uid, f"📉 SHORT SL TRAIL\nBest {best:.1f} SL {sl_now:.1f} (+{(entry-best):.1f}$)\nEntry {entry:.1f} Order {order_id_saved}")
                    except: pass
            
            close_reason = None
            side_to_close = None
            if cur_size > 0:  # LONG
                sl_price = best * (1 - SL/100)  # TRAILING SL
                tp_price = entry * (1 + TP/100)
                if cur_p <= sl_price:
                    close_reason = f"SL TRAIL Best {best:.1f} SL {sl_price:.1f} <= {cur_p:.1f} OrderID {order_id_saved}"
                    side_to_close = "sell"
                elif cur_p >= tp_price:
                    if not is_trailing:
                        TRAILING_ACTIVE[uid] = True
                        try: await context.bot.send_message(uid, f"🎯 TP HIT! Trailing Active! Best {best:.1f} Entry {entry:.1f} OrderID {order_id_saved}\n{TRAIL_TP}% reverse la close!")
                        except: pass
                    trail_close_price = best * (1 - TRAIL_TP/100)
                    if cur_p <= trail_close_price:
                        close_reason = f"TP TRAIL Best {best:.1f} -> {cur_p:.1f} OrderID {order_id_saved}"
                        side_to_close = "sell"
            else:  # SHORT
                sl_price = best * (1 + SL/100)
                tp_price = entry * (1 - TP/100)
                if cur_p >= sl_price:
                    close_reason = f"SL TRAIL Best {best:.1f} SL {sl_price:.1f} >= {cur_p:.1f} OrderID {order_id_saved}"
                    side_to_close = "buy"
                elif cur_p <= tp_price:
                    if not is_trailing:
                        TRAILING_ACTIVE[uid] = True
                        try: await context.bot.send_message(uid, f"🎯 TP HIT! Trailing Active! Best {best:.1f} Entry {entry:.1f} OrderID {order_id_saved}\n{TRAIL_TP}% reverse la close!")
                        except: pass
                    trail_close_price = best * (1 + TRAIL_TP/100)
                    if cur_p >= trail_close_price:
                        close_reason = f"TP TRAIL Best {best:.1f} -> {cur_p:.1f} OrderID {order_id_saved}"
                        side_to_close = "buy"
            
            if close_reason and side_to_close:
                lots = size_to_lots(cur_size)
                if lots < 1: lots = 1
                order({"product_id":27,"size":lots,"side":side_to_close,"order_type":"market_order","reduce_only":"true","time_in_force":"ioc"})
                await asyncio.sleep(2)
                sz_check,_,_=get_real_position()
                if abs(sz_check) < 0.0000001:
                    try: await context.bot.send_message(uid, f"✅ CLOSED! {close_reason}\nPrice {cur_p:.1f} OrderID {order_id_saved} Product 27\nAuto Alert Resume ✅")
                    except: pass
                    BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); LAST_SL.pop(uid,None); LAST_TP.pop(uid,None); LAST_POS_SIZE[uid]=0; ENTRY_TIME.pop(uid,None); LAST_ORDER_ID.pop(uid,None); TRAILING_ACTIVE.pop(uid,None)
                else:
                    print(f"CLOSE FAILED STILL {sz_check}")
        for uid in all_uids:
            if cur_size != 0: LAST_POS_SIZE[uid] = cur_size
    except Exception as e:
        print(f"check_positions_job error {e}")

async def start(u,c):
    ALERT_USERS.add(u.effective_user.id)
    uid=u.effective_user.id; qty=USER_QTY.get(uid,1); lev=USER_LEV.get(uid,20)
    bal=get_balance(); cur=price(); sz,entry,upnl=get_real_position()
    order_id = LAST_ORDER_ID.get(uid, "None")
    txt=f"🤖 BOT V119.5 SL TRAIL + NO ALERT LIVE\nBal: ${bal:.2f} BTC: {cur:.1f}\nQTY: {qty} Lot Lev: {lev}x\nPOS: {sz} Entry {entry} UPNL ${upnl:.2f}\nOrderID: {order_id}\nSL: {SL}% Trail from Best TP: {TP}% Trail: {TRAIL_TP}%\nLive la Alert OFF, Close ayiducha Alert ON ✅\n/cmds: /qty /lev /trend /long /short /close"
    await u.message.reply_text(txt)

async def trend_cmd(u,c):
    ALERT_USERS.add(u.effective_user.id)
    try:
        e=price(); bal=get_balance(); sz,_,_=get_real_position()
        order_id = LAST_ORDER_ID.get(u.effective_user.id, "None")
        await u.message.reply_text("📊 Loading PRO Indicators... 5m/15m/1h")
        s5 = get_pro_signal("5m")
        s15 = get_pro_signal("15m")
        s60 = get_pro_signal("1h")
        long_pct, short_pct = calc_score(s5,s15,s60)
        if long_pct>=70: final=f"🚀 STRONG BULLISH - LONG {long_pct}%"
        elif short_pct>=70: final=f"📉 STRONG BEARISH - SHORT {short_pct}%"
        elif long_pct>=55: final=f"🟢 BULLISH - LONG Fav {long_pct}%"
        elif short_pct>=55: final=f"🔴 BEARISH - SHORT Fav {short_pct}%"
        else: final=f"⚖️ SIDEWAYS - WAIT {long_pct}%/{short_pct}%"
        def icon(sig): return "🟢" if "BULL" in sig else "🔴" if "BEAR" in sig else "⚪"
        def st_icon(st): return "🟢" if "GREEN" in st else "🔴" if "RED" in st else "⚪"
        txt=f"""📊 TREND V119.5 PRO FIXED
BTC: {e:.1f} Bal: ${bal:.2f} POS: {sz}

5M: {icon(s5['ema_sig'])} {s5['ema_sig']} 
   EMA9 {s5['ema9']:.1f} vs EMA21 {s5['ema21']:.1f}
   RSI {s5['rsi']} {st_icon(s5['st'])} ST {s5['st']}

15M: {icon(s15['ema_sig'])} {s15['ema_sig']}
   EMA9 {s15['ema9']:.1f} vs EMA21 {s15['ema21']:.1f}
   RSI {s15['rsi']} {st_icon(s15['st'])} ST {s15['st']}

1H: {icon(s60['ema_sig'])} {s60['ema_sig']}
   RSI {s60['rsi']} {st_icon(s60['st'])} ST {s60['st']}

FINAL: {final}
LONG {long_pct}% | SHORT {short_pct}%
OrderID: {order_id}
SL {SL}% Trail TP {TP}% Trail {TRAIL_TP}%
"""
        await u.message.reply_text(txt)
    except Exception as ex:
        await u.message.reply_text(f"Trend error {ex}")

async def qty_cmd(u,c):
    ALERT_USERS.add(u.effective_user.id)
    try: lots=int(float(c.args[0])) if c.args else 1; USER_QTY[u.effective_user.id]=lots; await u.message.reply_text(f"✅ QTY {lots} Lot = {lots*0.001} BTC")
    except: await u.message.reply_text(f"Use /qty 1 to 4")

async def lev_cmd(u,c):
    ALERT_USERS.add(u.effective_user.id)
    l=int(c.args[0]) if c.args else 20; USER_LEV[u.effective_user.id]=l; set_lev(l); await u.message.reply_text(f"✅ Lev {l}x")

async def ask_perm(u, side):
    ALERT_USERS.add(u.effective_user.id)
    sz,entry,upnl=get_real_position()
    if abs(sz)>0.0000001:
        oid = LAST_ORDER_ID.get(u.effective_user.id, "Unknown")
        await u.message.reply_text(f"❌ Already have position {sz} BTC Entry {entry} OrderID {oid}\nClose first with /close Anna!")
        return
    lots=USER_QTY.get(u.effective_user.id,1); e=price()
    kb=[[InlineKeyboardButton("✅ YES", callback_data=f"{side}_yes"), InlineKeyboardButton("❌ NO", callback_data="no")]]
    await u.message.reply_text(f"{side.upper()}? Qty {lots} Lot ({lots*0.001} BTC) Price {e} SL {SL}% Trail TP {TP}% Trail {TRAIL_TP}% BOT ONLY!", reply_markup=InlineKeyboardMarkup(kb))

async def short_cmd(u,c): await ask_perm(u,"short")
async def long_cmd(u,c): await ask_perm(u,"long")

async def button(u,c):
    q=u.callback_query; await q.answer()
    try:
        ALERT_USERS.add(q.from_user.id)
        sz_check,_,_=get_real_position()
        if abs(sz_check)>0.0000001:
            oid = LAST_ORDER_ID.get(q.from_user.id, "Unknown")
            await q.edit_message_text(f"❌ Blocked! Already have position {sz_check} BTC OrderID {oid}! Use /close first!")
            return
        lots=USER_QTY.get(q.from_user.id,1); e=price()
        if q.data=="no": await q.edit_message_text("Cancelled ❌"); return
        side = "sell" if q.data=="short_yes" else "buy"
        res=order({"product_id":27,"size":lots,"side":side,"order_type":"market_order"})
        if not res.get('success'):
            bal=get_balance(); await q.edit_message_text(f"❌ Failed! Bal ${bal:.2f} Error: {res}"); return
        try:
            order_id = res.get('result', {}).get('id', 0)
            if not order_id: order_id = int(time.time())
            LAST_ORDER_ID[q.from_user.id] = order_id
        except: LAST_ORDER_ID[q.from_user.id] = int(time.time())
        ENTRY_PRICE[q.from_user.id]=e; BEST_PRICE[q.from_user.id]=e; LAST_SL[q.from_user.id]=round(e*(1+SL/100),1) if side=="sell" else round(e*(1-SL/100),1)
        LAST_TP[q.from_user.id]=round(e*(1-TP/100),1) if side=="sell" else round(e*(1+TP/100),1)
        LAST_POS_SIZE[q.from_user.id]= -lots if side=="sell" else lots; ENTRY_TIME[q.from_user.id]=time.time(); TRAILING_ACTIVE[q.from_user.id]=False
        await q.edit_message_text(f"{side.upper()} DONE ✅ SL TRAIL START!\nEntry {e} SL {SL}% Trail TP {TP}% Trail {TRAIL_TP}%\nOrderID: {LAST_ORDER_ID[q.from_user.id]}\nBest tracking start {e}\nNo Alert While LIVE ✅")
    except Exception as e:
        print(f"button error {e}")

async def close_cmd(u,c):
    ALERT_USERS.add(u.effective_user.id)
    uid=u.effective_user.id; IS_CLOSING[uid]=True
    saved_id = LAST_ORDER_ID.get(uid, "No ID")
    await u.message.reply_text(f"🔍 Searching REAL Delta...\nRemember OrderID: {saved_id}")
    try:
        sz,_,_=get_real_position()
        await u.message.reply_text(f"📊 REAL Delta: {sz} BTC Remember OrderID: {saved_id}")
        if abs(sz)<0.0000001:
            await u.message.reply_text(f"✅ Already Closed! OrderID {saved_id} Already Closed! Alert Resume ✅")
            BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); LAST_POS_SIZE[uid]=0; LAST_SL.pop(uid,None); LAST_TP.pop(uid,None); ENTRY_TIME.pop(uid,None); LAST_ORDER_ID.pop(uid,None); TRAILING_ACTIVE.pop(uid,None)
            IS_CLOSING[uid]=False; cancel_all_orders(); return
        lots = size_to_lots(sz); side="buy" if sz<0 else "sell"
        order({"product_id":27,"size":lots,"side":side,"order_type":"market_order","reduce_only":"true","time_in_force":"ioc"})
        await asyncio.sleep(3); cancel_all_orders()
        await u.message.reply_text(f"✅ CLOSED! Remember OrderID {saved_id} {sz} BTC Closed at {price():.1f} ✅ Alert Resume!")
        BEST_PRICE.pop(uid,None); ENTRY_PRICE.pop(uid,None); LAST_POS_SIZE[uid]=0; LAST_SL.pop(uid,None); LAST_TP.pop(uid,None); ENTRY_TIME.pop(uid,None); LAST_ORDER_ID.pop(uid,None); TRAILING_ACTIVE.pop(uid,None); IS_CLOSING[uid]=False
    except Exception as e:
        await u.message.reply_text(f"❌ Error {e} OrderID {saved_id}"); IS_CLOSING[u.effective_user.id]=False

def main():
    app=Application.builder().token(T).build()
    app.add_handler(CommandHandler("start", start)); app.add_handler(CommandHandler("qty", qty_cmd)); app.add_handler(CommandHandler("lev", lev_cmd))
    app.add_handler(CommandHandler("trend", trend_cmd)); app.add_handler(CommandHandler("short", short_cmd)); app.add_handler(CommandHandler("long", long_cmd))
    app.add_handler(CommandHandler("close", close_cmd)); app.add_handler(CallbackQueryHandler(button))
    app.job_queue.run_repeating(check_positions_job, interval=15, first=10)
    app.job_queue.run_repeating(auto_alert_job, interval=120, first=30)
    print("BOT LIVE V119.5 SL TRAIL + NO ALERT WHEN LIVE"); app.run_polling(drop_pending_updates=True)

if __name__=="__main__": main()
