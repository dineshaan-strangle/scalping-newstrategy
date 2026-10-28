import os, time, hmac, hashlib, requests, json, asyncio, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
K=os.getenv("DELTA_API_KEY")
S=os.getenv("DELTA_API_SECRET")
T=os.getenv("TELEGRAM_TOKEN")
B="https://api.india.delta.exchange"
BC="https://cdn.india.deltaex.org"
SL=1.2
TP=2.0
TRAIL_TP=0.6
USER_QTY={}
USER_LEV={}
ALERT_USERS=set()
BEST_PRICE={}
ENTRY_PRICE={}
LAST_POS_SIZE={}
LAST_SL={}
LAST_TP={}
ENTRY_TIME={}
IS_CLOSING={}
LAST_ORDER_ID={}
TRAILING_ACTIVE={}
LAST_ALERT_TIME=0
LAST_ALERT_SIGNAL=""
CONFIRM_PENDING={}
LAST_BEST_ALERT={}
class HealthHandler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200)
  self.send_header('Content-type','text/plain')
  self.end_headers()
  self.wfile.write(b'BOT OK V120 ALL 7 FEATURES')
 def do_HEAD(self):
  self.send_response(200)
  self.send_header('Content-type','text/plain')
  self.end_headers()
 def log_message(self, format, *args):
  return
def run_health_server():
 try:
  HTTPServer(('0.0.0.0', int(os.getenv("PORT",10000))), HealthHandler).serve_forever()
 except:
  pass
threading.Thread(target=run_health_server, daemon=True).start()
def sig(m,t,p,q,b,s):
 return hmac.new(s.encode(), (m+t+p+q+b).encode(), hashlib.sha256).hexdigest()
def hdr(m,p,q="",b=""):
 t=str(int(time.time()))
 return {"api-key":K,"timestamp":t,"signature":sig(m,t,p,q,b,S),"Content-Type":"application/json"}
def price():
 try:
  return float(requests.get(f"{B}/v2/tickers/BTCUSD", timeout=5).json()['result']['mark_price'])
 except:
  return 80600.0
def get_ticker_info():
 try:
  r=requests.get(f"{B}/v2/tickers/BTCUSD", timeout=5).json()
  res=r.get('result',{})
  return {"oi":float(res.get('open_interest',0)),"vol":float(res.get('volume',0)),"mark":float(res.get('mark_price',0))}
 except:
  return {"oi":0,"vol":0,"mark":price()}
def order(d):
 try:
  p="/v2/orders"
  b=json.dumps(d, separators=(',', ':'))
  h=hdr("POST",p,"",b)
  r=requests.post(B+p, headers=h, data=b, timeout=10)
  print(f"ORDER {b} => {r.text}")
  return r.json()
 except Exception as ex:
  print(f"ORDER EXCEPTION {ex}")
  return {"success": False}
def cancel_all_orders():
 try:
  p="/v2/orders/all"
  b=json.dumps({"product_id":27,"cancel_stop_orders":True,"cancel_limit_orders":True}, separators=(',', ':'))
  h=hdr("DELETE",p,"",b)
  requests.delete(B+p, headers=h, data=b, timeout=5)
 except:
  pass
def set_lev(lev):
 try:
  p="/v2/products/27"
  b=json.dumps({"leverage":str(lev)}, separators=(',', ':'))
  h=hdr("PUT",p,"",b)
  requests.put(B+p, headers=h, data=b, timeout=5)
 except:
  pass
def get_balance():
 try:
  h=hdr("GET","/v2/wallet/balances")
  r=requests.get(B+"/v2/wallet/balances", headers=h, timeout=5).json()
  for x in r.get('result',[]):
   if x.get('asset_symbol')=='USD':
    return float(x.get('available_balance',0))
  return 0
 except:
  return 0
def get_real_position_full():
 sz=0
 entry=0
 upnl=0
 liq=0
 margin=0
 try:
  h=hdr("GET","/v2/positions")
  pos=requests.get(B+"/v2/positions", headers=h, timeout=10).json()
  for x in pos.get('result',[]):
   s=float(x.get('size',0))
   if abs(s)>0.0000001 and 'BTC' in str(x.get('product_symbol','')).upper():
    sz=s
    entry=float(x.get('entry_price',0))
    upnl=float(x.get('unrealized_pnl',0))
    liq=float(x.get('liquidation_price',0) or 0)
    margin=float(x.get('margin',0) or 0)
  try:
   h2=hdr("GET","/v2/positions/margined","","")
   pos2=requests.get(B+"/v2/positions/margined", headers=h2, timeout=10).json()
   for x in (pos2.get('result',[]) if isinstance(pos2.get('result'), list) else []):
    s=float(x.get('size',0))
    if abs(s)>0.0000001 and 'BTC' in str(x.get('product_symbol','')).upper():
     sz=s
     entry=float(x.get('entry_price',0))
     upnl=float(x.get('unrealized_pnl',0))
     liq=float(x.get('liquidation_price',0) or 0)
     margin=float(x.get('margin',0) or 0)
  except:
   pass
  return sz, entry, upnl, liq, margin
 except:
  return 0,0,0,0,0
def get_real_position():
 sz,e,up,_,_=get_real_position_full()
 return sz,e,up
def get_latest_order_id_from_api():
 try:
  try:
   h=hdr("GET","/v2/fills?product_id=27&page_size=5","","")
   r=requests.get(B+"/v2/fills?product_id=27&page_size=5", headers=h, timeout=5).json()
   fills=r.get('result',[]) if isinstance(r, dict) else r
   if fills:
    for f in fills:
     oid=f.get('order_id') or f.get('orderId')
     if oid:
      return str(oid)
  except:
   pass
  h=hdr("GET","/v2/orders?product_id=27&state=open","","")
  r=requests.get(B+"/v2/orders?product_id=27&state=open", headers=h, timeout=5).json()
  orders=r.get('result',[])
  if orders:
   return str(orders[0].get('id',''))
  h2=hdr("GET","/v2/orders?product_id=27&state=closed&page_size=5","","")
  r2=requests.get(B+"/v2/orders?product_id=27&state=closed&page_size=5", headers=h2, timeout=5).json()
  orders2=r2.get('result',[])
  if orders2:
   return str(orders2[0].get('id',''))
  return None
 except:
  return None
def size_to_lots(real_size):
 return int(abs(real_size)*1000) if abs(real_size)<1 else int(abs(real_size))
def fetch_candles_full(res="5m", count=100):
 closes=[]
 highs=[]
 lows=[]
 vols=[]
 cur_live=price()
 try:
  now=int(time.time())
  try:
   url=f"{B}/v2/history/candles"
   params={"resolution":res,"symbol":"MARK:BTCUSD","start":now-86400*3,"end":now}
   r=requests.get(url, params=params, timeout=8).json()
   candles=r.get('result',[]) if isinstance(r, dict) else r
   if len(candles)>10:
    closes=[float(c.get('close',0)) for c in candles if float(c.get('close',0))>0]
    highs=[float(c.get('high',0)) for c in candles if float(c.get('high',0))>0]
    lows=[float(c.get('low',0)) for c in candles if float(c.get('low',0))>0]
    vols=[float(c.get('volume',0)) for c in candles]
  except:
   pass
  if len(closes)<20:
   try:
    secs={"1m":60,"5m":300,"15m":900,"1h":3600}
    frm=now-(secs.get(res.lower(),300)*count)-1000
    url=f"{BC}/v2/history/candles"
    params={"resolution":res.lower(),"symbol":"BTCUSD","start":frm,"end":now}
    r=requests.get(url, params=params, timeout=8).json()
    candles=r if isinstance(r,list) else r.get('result',[])
    if len(candles)>20:
     closes=[float(c.get('close',0)) for c in candles if float(c.get('close',0))>0]
     highs=[float(c.get('high',0)) for c in candles if float(c.get('high',0))>0]
     lows=[float(c.get('low',0)) for c in candles if float(c.get('low',0))>0]
     vols=[float(c.get('volume',0)) for c in candles]
   except:
    pass
  if closes and abs(closes[-1]-cur_live)/cur_live>0.008:
   closes[-1]=cur_live
   highs[-1]=max(highs[-1],cur_live)
   lows[-1]=min(lows[-1],cur_live)
  return closes[-count:], highs[-count:], lows[-count:], vols[-count:]
 except:
  return [],[],[],[]
def calc_ema(prices, period):
 if len(prices)<period:
  return prices[-1] if prices else 0
 k=2/(period+1)
 ema=sum(prices[:period])/period
 for p in prices[period:]:
  ema=p*k+ema*(1-k)
 return ema
def calc_rsi(prices, period=14):
 if len(prices)<period+1:
  return 50
 gains=0
 losses=0
 for i in range(1,period+1):
  diff=prices[-i]-prices[-i-1]
  if diff>0:
   gains+=diff
  else:
   losses+=-diff
 if losses==0:
  return 70 if gains>0 else 50
 rs=(gains/period)/(losses/period)
 return round(100-(100/(1+rs)),1)
def calc_atr(highs,lows,closes,period=14):
 try:
  trs=[]
  for i in range(1,len(closes)):
   trs.append(max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])))
  return sum(trs[-period:])/period if len(trs)>=period else sum(trs)/len(trs) if trs else 100
 except:
  return 100
def calc_vwap(closes,vols):
 try:
  if len(closes)<20 or not vols or sum(vols[-20:])==0:
   return closes[-1] if closes else price()
  pv=sum(closes[i]*vols[i] for i in range(-20,0))
  v=sum(vols[-20:])
  return pv/v if v!=0 else closes[-1]
 except:
  return closes[-1] if closes else price()
def calc_vol_pct(vols):
 try:
  if len(vols)<21:
   return 100
  avg=sum(vols[-20:-1])/19
  cur=vols[-1] if vols[-1]!=0 else avg
  return int(cur/avg*100) if avg!=0 else 100
 except:
  return 100
def calc_supertrend(closes,highs,lows,period=10,mult=3):
 try:
  if len(closes)<period:
   return "SIDE", closes[-1] if closes else 0
  tr_list=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(1,len(closes))]
  atr=sum(tr_list[-period:])/period if len(tr_list)>=period else sum(tr_list)/len(tr_list)
  hl2=(highs[-1]+lows[-1])/2
  upper=hl2+mult*atr
  lower=hl2-mult*atr
  if closes[-1]>upper:
   return "GREEN BULLISH", lower
  elif closes[-1]<lower:
   return "RED BEARISH", upper
  else:
   return ("GREEN BULLISH", lower) if closes[-1]>closes[-2] else ("RED BEARISH", upper)
 except:
  return "SIDE",0
def get_pro_signal(res_label):
 closes,highs,lows,vols=fetch_candles_full(res_label,100)
 if len(closes)<25:
  return {"ema9":0,"ema21":0,"ema_sig":"SIDEWAYS","rsi":50,"st":"SIDE","st_val":0,"price":price(),"atr":100,"vwap":price(),"vol_pct":100}
 ema9=calc_ema(closes,9)
 ema21=calc_ema(closes,21)
 rsi=calc_rsi(closes,14)
 st_trend,st_val=calc_supertrend(closes,highs,lows,10,3)
 atr=calc_atr(highs,lows,closes,14)
 vwap=calc_vwap(closes,vols)
 vol_pct=calc_vol_pct(vols)
 cur=closes[-1]
 ema_sig="BULLISH" if ema9>ema21 else "BEARISH" if ema9<ema21 else "SIDEWAYS"
 return {"ema9":ema9,"ema21":ema21,"ema_sig":ema_sig,"rsi":rsi,"st":st_trend,"st_val":st_val,"price":cur,"atr":atr,"vwap":vwap,"vol_pct":vol_pct}
def calc_score(s5,s15,s60):
 long_score=0
 short_score=0
 if s5['ema_sig']=="BULLISH":
  long_score+=20
 elif s5['ema_sig']=="BEARISH":
  short_score+=20
 if s5['rsi']<35:
  long_score+=15
 elif s5['rsi']>65:
  short_score+=15
 if "GREEN" in s5['st']:
  long_score+=15
 elif "RED" in s5['st']:
  short_score+=15
 if s15['ema_sig']=="BULLISH":
  long_score+=12
 elif s15['ema_sig']=="BEARISH":
  short_score+=12
 if "GREEN" in s15['st']:
  long_score+=18
 elif "RED" in s15['st']:
  short_score+=18
 if s60['ema_sig']=="BULLISH":
  long_score+=10
 elif s60['ema_sig']=="BEARISH":
  short_score+=10
 if "GREEN" in s60['st']:
  long_score+=10
 elif "RED" in s60['st']:
  short_score+=10
 total=long_score+short_score
 if total==0:
  return 50,50
 long_pct=int(long_score/total*100)
 return long_pct, 100-long_pct
async def auto_alert_job(context: ContextTypes.DEFAULT_TYPE):
 global LAST_ALERT_TIME, LAST_ALERT_SIGNAL
 try:
  live_sz,_,_=get_real_position()
  if abs(live_sz)>0.0000001:
   return
  if time.time()-LAST_ALERT_TIME<180:
   return
  s5=get_pro_signal("5m")
  s15=get_pro_signal("15m")
  s60=get_pro_signal("1h")
  ticker=get_ticker_info()
  long_pct, short_pct=calc_score(s5,s15,s60)
  cur=price()
  vwap_status="ABOVE ✅" if cur>s5['vwap'] else "BELOW ❌"
  vol_str=f"{s5['vol_pct']}% {'🔥' if s5['vol_pct']>120 else '✅' if s5['vol_pct']>80 else '⚠️'}"
  signal=""
  kb=None
  if long_pct>=70:
   signal=f"LONG_{long_pct}"
   msg=f"🔥 CONFIRMATION NEEDED - LONG {long_pct}%\n\nBTC: ${cur:.1f} | EMA9>21 ✅\nRSI: {s5['rsi']} | Vol: {vol_str}\nVWAP: {vwap_status} ({s5['vwap']:.1f}) | ATR: {s5['atr']:.1f}\nOI: {ticker['oi']:.0f} | 5M {s5['ema_sig']} 15M {s15['ema_sig']} 1H {s60['ema_sig']}\n\nQTY: 1 Lot | Lev: 20x\nSL: ATR x1.5 = {cur - s5['atr']*1.5:.1f} | TP: +2%\n\nTake LONG? 👇"
   kb=[[InlineKeyboardButton("✅ YES LONG", callback_data=f"confirm_long_{long_pct}"), InlineKeyboardButton("❌ NO SKIP", callback_data="confirm_no")]]
  elif short_pct>=70:
   signal=f"SHORT_{short_pct}"
   msg=f"🔻 CONFIRMATION NEEDED - SHORT {short_pct}%\n\nBTC: ${cur:.1f} | EMA9<21 ✅\nRSI: {s5['rsi']} | Vol: {vol_str}\nVWAP: {vwap_status} ({s5['vwap']:.1f}) | ATR: {s5['atr']:.1f}\nOI: {ticker['oi']:.0f} | 5M {s5['ema_sig']} 15M {s15['ema_sig']} 1H {s60['ema_sig']}\n\nQTY: 1 Lot | Lev: 20x\nSL: ATR x1.5 = {cur + s5['atr']*1.5:.1f} | TP: -2%\n\nTake SHORT? 👇"
   kb=[[InlineKeyboardButton("✅ YES SHORT", callback_data=f"confirm_short_{short_pct}"), InlineKeyboardButton("❌ NO SKIP", callback_data="confirm_no")]]
  if signal and signal!=LAST_ALERT_SIGNAL:
   all_users=ALERT_USERS.union(set(USER_QTY.keys()))
   for uid in list(all_users):
    try:
     if kb:
      await context.bot.send_message(uid, msg, reply_markup=InlineKeyboardMarkup(kb))
     else:
      await context.bot.send_message(uid, msg)
     CONFIRM_PENDING[uid]=signal
    except:
     pass
   LAST_ALERT_TIME=time.time()
   LAST_ALERT_SIGNAL=signal
 except Exception as e:
  print(f"auto_alert error {e}")
async def check_positions_job(context: ContextTypes.DEFAULT_TYPE):
 try:
  if any(IS_CLOSING.values()):
   return
  cur_p=price()
  cur_size, entry_real, upnl_real, liq_real, margin_real=get_real_position_full()
  s5_live=get_pro_signal("5m")
  vwap_live=s5_live['vwap']
  vol_live=s5_live['vol_pct']
  atr_live=s5_live['atr']
  all_uids=set(list(USER_QTY.keys())+list(LAST_POS_SIZE.keys())+list(ENTRY_PRICE.keys())+list(LAST_ORDER_ID.keys())+list(ALERT_USERS))
  for uid in list(all_uids):
   if IS_CLOSING.get(uid):
    continue
   if abs(cur_size)<0.0000001:
    continue
   entry=ENTRY_PRICE.get(uid, entry_real)
   if entry==0:
    entry=entry_real
   if entry==0:
    continue
   best=BEST_PRICE.get(uid, entry)
   order_id_saved=LAST_ORDER_ID.get(uid, "No ID")
   if not order_id_saved or order_id_saved=="None" or "LIVE-" in str(order_id_saved):
    f=get_latest_order_id_from_api()
    if f:
     order_id_saved=f
     LAST_ORDER_ID[uid]=f
   liq_safe_pct=((cur_p-liq_real)/cur_p*100) if liq_real!=0 and cur_size>0 else ((liq_real-cur_p)/cur_p*100) if liq_real!=0 else 0
   is_trailing=TRAILING_ACTIVE.get(uid, False)
   last_best_alert=LAST_BEST_ALERT.get(uid, entry)
   should_alert_trail=False
   if cur_size>0:
    if cur_p>best:
     BEST_PRICE[uid]=cur_p
     best=cur_p
     if abs(best-last_best_alert)>=10:
      should_alert_trail=True
      LAST_BEST_ALERT[uid]=best
     sl_now=best-(atr_live*1.5)
     if should_alert_trail:
      try:
       pnl_dollar=(cur_p-entry)*abs(cur_size)
       await context.bot.send_message(uid, f"🔒 LONG SL TRAIL\n\nBest: {best:.1f} (+{(best-entry):.1f}$)\nSL: -> {sl_now:.1f} 🔼 LOCK\nEntry: {entry:.1f} | PnL: +${pnl_dollar:.2f} UPNL ${upnl_real:.2f}\nLiq: {liq_real:.0f} ({liq_safe_pct:.2f}% Safe ✅)\n\nVWAP: {vwap_live:.1f} {'ABOVE ✅' if cur_p>vwap_live else 'BELOW'} | Vol: {vol_live}% {'🔥' if vol_live>120 else '✅'}\nATR SL: {sl_now:.1f} (Dynamic)\nOrderID: {order_id_saved}\n\nHold - Profit Locked!")
      except:
       pass
   else:
    if cur_p<best:
     BEST_PRICE[uid]=cur_p
     best=cur_p
     if abs(best-last_best_alert)>=10:
      should_alert_trail=True
      LAST_BEST_ALERT[uid]=best
     sl_now=best+(atr_live*1.5)
     if should_alert_trail:
      try:
       pnl_dollar=(entry-cur_p)*abs(cur_size)
       await context.bot.send_message(uid, f"🔒 SHORT SL TRAIL\n\nBest: {best:.1f} (+{(entry-best):.1f}$)\nSL: -> {sl_now:.1f} 🔽 LOCK\nEntry: {entry:.1f} | PnL: +${pnl_dollar:.2f} UPNL ${upnl_real:.2f}\nLiq: {liq_real:.0f} ({liq_safe_pct:.2f}% Safe ✅)\nVWAP: {vwap_live:.1f} | Vol: {vol_live}%\nATR SL: {sl_now:.1f} (Dynamic)\nOrderID: {order_id_saved}")
      except:
       pass
   close_reason=None
   side_to_close=None
   if cur_size>0:
    sl_price=best-(atr_live*1.5)
    tp_price=entry*(1+TP/100)
    if cur_p<=sl_price:
     close_reason=f"SL ATR Best {best:.1f} SL {sl_price:.1f} <= {cur_p:.1f} OrderID {order_id_saved} UPNL ${upnl_real:.2f}"
     side_to_close="sell"
    elif cur_p>=tp_price:
     if not is_trailing:
      TRAILING_ACTIVE[uid]=True
      try:
       await context.bot.send_message(uid, f"🎯 TP HIT! Trailing Active! Best {best:.1f} Entry {entry:.1f} UPNL ${upnl_real:.2f} OrderID {order_id_saved}\nATR {atr_live:.1f} | {TRAIL_TP}% reverse la close!")
      except:
       pass
     trail_close_price=best*(1-TRAIL_TP/100)
     if cur_p<=trail_close_price:
      close_reason=f"TP TRAIL Best {best:.1f} -> {cur_p:.1f} OrderID {order_id_saved} Profit ${upnl_real:.2f}"
      side_to_close="sell"
   else:
    sl_price=best+(atr_live*1.5)
    tp_price=entry*(1-TP/100)
    if cur_p>=sl_price:
     close_reason=f"SL ATR Best {best:.1f} SL {sl_price:.1f} >= {cur_p:.1f} OrderID {order_id_saved}"
     side_to_close="buy"
    elif cur_p<=tp_price:
     if not is_trailing:
      TRAILING_ACTIVE[uid]=True
      try:
       await context.bot.send_message(uid, f"🎯 TP HIT! Trailing Active! Best {best:.1f} Entry {entry:.1f} UPNL ${upnl_real:.2f} OrderID {order_id_saved}\nATR {atr_live:.1f} | {TRAIL_TP}% reverse la close!")
      except:
       pass
     trail_close_price=best*(1+TRAIL_TP/100)
     if cur_p>=trail_close_price:
      close_reason=f"TP TRAIL Best {best:.1f} -> {cur_p:.1f} OrderID {order_id_saved}"
      side_to_close="buy"
   if close_reason and side_to_close:
    lots=size_to_lots(cur_size)
    if lots<1:
     lots=1
    order({"product_id":27,"size":lots,"side":side_to_close,"order_type":"market_order","reduce_only":"true","time_in_force":"ioc"})
    await asyncio.sleep(2)
    sz_check,_,_,_,_=get_real_position_full()
    if abs(sz_check)<0.0000001:
     profit_dollar=(cur_p-entry)*cur_size if cur_size>0 else (entry-cur_p)*abs(cur_size)
     profit_pct=((cur_p-entry)/entry*100) if cur_size>0 else ((entry-cur_p)/entry*100)
     duration=""
     if ENTRY_TIME.get(uid):
      duration=f"{int((time.time()-ENTRY_TIME.get(uid,0))/60)}m"
     try:
      await context.bot.send_message(uid, f"✅ LONG CLOSED - PROFIT!\n\nEntry: {entry:.1f} | Exit: {cur_p:.1f}\nProfit: {profit_dollar:+.1f}$ ({profit_pct:+.2f}%) = ${profit_dollar:.2f}\nDuration: {duration} | Best: {best:.1f}\n\nReason: {close_reason.split('OrderID')[0]}\nVWAP Exit: {'Above ✅' if cur_p>vwap_live else 'Below'} | Vol: {vol_live}%\nBal: ${get_balance():.2f} | Next Signal Wait...\nOrderID: {order_id_saved}")
     except:
      pass
     BEST_PRICE.pop(uid,None)
     ENTRY_PRICE.pop(uid,None)
     LAST_SL.pop(uid,None)
     LAST_TP.pop(uid,None)
     LAST_POS_SIZE[uid]=0
     ENTRY_TIME.pop(uid,None)
     LAST_ORDER_ID.pop(uid,None)
     TRAILING_ACTIVE.pop(uid,None)
     LAST_BEST_ALERT.pop(uid,None)
  for uid in all_uids:
   if cur_size!=0:
    LAST_POS_SIZE[uid]=cur_size
 except Exception as e:
  print(f"check_positions_job error {e}")
async def start(u,c):
 ALERT_USERS.add(u.effective_user.id)
 uid=u.effective_user.id
 qty=USER_QTY.get(uid,1)
 lev=USER_LEV.get(uid,20)
 bal=get_balance()
 cur=price()
 sz,entry,upnl,liq,margin=get_real_position_full()
 order_id=LAST_ORDER_ID.get(uid)
 if not order_id and abs(sz)>0.0000001:
  fetched=get_latest_order_id_from_api()
  if fetched:
   order_id=fetched
   LAST_ORDER_ID[uid]=fetched
   ENTRY_PRICE[uid]=entry if entry!=0 else ENTRY_PRICE.get(uid,cur)
   BEST_PRICE[uid]=BEST_PRICE.get(uid,cur)
  else:
   order_id="Fetching..."
 if not order_id:
  order_id="None"
 liq_safe=((cur-liq)/cur*100) if liq!=0 and sz>0 else ((liq-cur)/cur*100) if liq!=0 else 0
 txt=f"🤖 BOT V120 - 7 FEATURES LIVE ✅\nBal: ${bal:.2f} BTC: {cur:.1f}\nQTY: {qty} Lot Lev: {lev}x\nPOS: {sz} Entry {entry} UPNL ${upnl:.2f}\nLiq: {liq:.0f} ({liq_safe:.2f}% Safe ✅)\nMargin: ${margin:.2f} | OrderID: {order_id}\nATR SL: Dynamic x1.5 | TP: {TP}% Trail: {TRAIL_TP}%\nVWAP+Vol+OI | Confirmation Mode ON ✅\nLive la Alert OFF, Close ayiducha Alert ON ✅\n/cmds: /qty /lev /trend /long /short /close"
 await u.message.reply_text(txt)
async def trend_cmd(u,c):
 ALERT_USERS.add(u.effective_user.id)
 try:
  e=price()
  bal=get_balance()
  sz,entry_real,upnl_real,liq_real,margin_real=get_real_position_full()
  ticker=get_ticker_info()
  uid=u.effective_user.id
  order_id=LAST_ORDER_ID.get(uid)
  if not order_id and abs(sz)>0.0000001:
   fetched=get_latest_order_id_from_api()
   if fetched:
    order_id=fetched
    LAST_ORDER_ID[uid]=fetched
   else:
    order_id="Fetching..."
  if not order_id or "LIVE-" in str(order_id):
   fetched=get_latest_order_id_from_api()
   if fetched:
    order_id=fetched
    LAST_ORDER_ID[uid]=fetched
  if not order_id:
   order_id="None"
  await u.message.reply_text("📊 Loading V120 PRO Indicators... 5m/15m/1h VWAP Vol OI ATR")
  s5=get_pro_signal("5m")
  s15=get_pro_signal("15m")
  s60=get_pro_signal("1h")
  long_pct, short_pct=calc_score(s5,s15,s60)
  if long_pct>=70:
   final=f"🚀 STRONG BULLISH - LONG {long_pct}%"
  elif short_pct>=70:
   final=f"📉 STRONG BEARISH - SHORT {short_pct}%"
  elif long_pct>=55:
   final=f"🟢 BULLISH - LONG Fav {long_pct}%"
  elif short_pct>=55:
   final=f"🔴 BEARISH - SHORT Fav {short_pct}%"
  else:
   final=f"⚖️ SIDEWAYS - WAIT {long_pct}%/{short_pct}%"
  def icon(sig):
   return "🟢" if "BULL" in sig else "🔴" if "BEAR" in sig else "⚪"
  def st_icon(st):
   return "🟢" if "GREEN" in st else "🔴" if "RED" in st else "⚪"
  liq_safe=((e-liq_real)/e*100) if liq_real!=0 and sz>0 else ((liq_real-e)/e*100) if liq_real!=0 else 0
  vwap_status="ABOVE ✅" if e>s5['vwap'] else "BELOW ❌"
  txt=f"""📊 TREND V120 - 7 FEATURES
BTC: {e:.1f} Bal: ${bal:.2f} 
POS: {sz} UPNL ${upnl_real:.2f} Entry {entry_real:.1f}
Liq: {liq_real:.0f} ({liq_safe:.2f}% Safe) Margin ${margin_real:.2f}
5M: {icon(s5['ema_sig'])} {s5['ema_sig']} 
   EMA9 {s5['ema9']:.1f} vs EMA21 {s5['ema21']:.1f}
   RSI {s5['rsi']} {st_icon(s5['st'])} ST {s5['st']}
   VWAP {s5['vwap']:.1f} {vwap_status} | Vol {s5['vol_pct']}% {'🔥' if s5['vol_pct']>120 else '✅'} | ATR {s5['atr']:.1f}
15M: {icon(s15['ema_sig'])} {s15['ema_sig']}
   EMA9 {s15['ema9']:.1f} vs EMA21 {s15['ema21']:.1f}
   RSI {s15['rsi']} {st_icon(s15['st'])} ST {s15['st']}
   VWAP {s15['vwap']:.1f} | ATR {s15['atr']:.1f}
1H: {icon(s60['ema_sig'])} {s60['ema_sig']}
   RSI {s60['rsi']} {st_icon(s60['st'])} ST {s60['st']}
   VWAP {s60['vwap']:.1f}
OI: {ticker['oi']:.0f} | Vol Ticker: {ticker['vol']:.2f}
FINAL: {final}
LONG {long_pct}% | SHORT {short_pct}%
OrderID: {order_id}
ATR SL: {s5['atr']*1.5:.1f} Dynamic | TP {TP}% Trail {TRAIL_TP}%
"""
  await u.message.reply_text(txt)
 except Exception as ex:
  await u.message.reply_text(f"Trend error {ex}")
async def qty_cmd(u,c):
 ALERT_USERS.add(u.effective_user.id)
 try:
  lots=int(float(c.args[0])) if c.args else 1
  USER_QTY[u.effective_user.id]=lots
  await u.message.reply_text(f"✅ QTY {lots} Lot = {lots*0.001} BTC")
 except:
  await u.message.reply_text(f"Use /qty 1 to 4")
async def lev_cmd(u,c):
 ALERT_USERS.add(u.effective_user.id)
 l=int(c.args[0]) if c.args else 20
 USER_LEV[u.effective_user.id]=l
 set_lev(l)
 await u.message.reply_text(f"✅ Lev {l}x")
async def ask_perm(u, side):
 ALERT_USERS.add(u.effective_user.id)
 sz,entry,upnl,liq,margin=get_real_position_full()
 if abs(sz)>0.0000001:
  oid=LAST_ORDER_ID.get(u.effective_user.id, "Unknown")
  if oid=="None" or not oid or "LIVE-" in str(oid):
   f=get_latest_order_id_from_api()
   oid=f if f else oid
  liq_safe=((price()-liq)/price()*100) if liq!=0 else 0
  await u.message.reply_text(f"❌ Already have position {sz} BTC Entry {entry} UPNL ${upnl:.2f} Liq {liq:.0f} ({liq_safe:.2f}% Safe) OrderID {oid}\nClose first with /close Anna!")
  return
 lots=USER_QTY.get(u.effective_user.id,1)
 e=price()
 s5=get_pro_signal("5m")
 vwap_status="ABOVE ✅" if e>s5['vwap'] else "BELOW ❌"
 kb=[[InlineKeyboardButton("✅ YES", callback_data=f"{side}_yes"), InlineKeyboardButton("❌ NO", callback_data="no")]]
 await u.message.reply_text(f"🔥 CONFIRM {side.upper()}?\n\nQty {lots} Lot ({lots*0.001} BTC) Price {e:.1f}\nVWAP: {s5['vwap']:.1f} {vwap_status} | Vol: {s5['vol_pct']}% | ATR: {s5['atr']:.1f}\nSL ATR x1.5 = {e - s5['atr']*1.5 if side=='long' else e + s5['atr']*1.5:.1f} | TP {TP}% Trail {TRAIL_TP}%\nLiq Safe Calc Included ✅", reply_markup=InlineKeyboardMarkup(kb))
async def short_cmd(u,c):
 await ask_perm(u,"short")
async def long_cmd(u,c):
 await ask_perm(u,"long")
async def button(u,c):
 q=u.callback_query
 await q.answer()
 try:
  ALERT_USERS.add(q.from_user.id)
  data=q.data
  if data.startswith("confirm_long_") or data.startswith("confirm_short_"):
   is_long="long" in data
   sz_check,_,_,_,_=get_real_position_full()
   if abs(sz_check)>0.0000001:
    oid=LAST_ORDER_ID.get(q.from_user.id, "Unknown")
    await q.edit_message_text(f"❌ Blocked! Already have position {sz_check} BTC OrderID {oid}! Use /close first!")
    return
   lots=USER_QTY.get(q.from_user.id,1)
   e=price()
   side="buy" if is_long else "sell"
   res=order({"product_id":27,"size":lots,"side":side,"order_type":"market_order"})
   if not res.get('success'):
    bal=get_balance()
    await q.edit_message_text(f"❌ Failed! Bal ${bal:.2f} Error: {res}")
    return
   try:
    order_id=res.get('result', {}).get('id', 0)
    if not order_id:
     order_id=int(time.time())
    LAST_ORDER_ID[q.from_user.id]=order_id
   except:
    LAST_ORDER_ID[q.from_user.id]=int(time.time())
   ENTRY_PRICE[q.from_user.id]=e
   BEST_PRICE[q.from_user.id]=e
   LAST_BEST_ALERT[q.from_user.id]=e
   LAST_POS_SIZE[q.from_user.id]=lots if is_long else -lots
   ENTRY_TIME[q.from_user.id]=time.time()
   TRAILING_ACTIVE[q.from_user.id]=False
   s5=get_pro_signal("5m")
   await q.edit_message_text(f"✅ {side.upper()} CONFIRMED & DONE!\n\nEntry: {e:.1f} | OrderID: {LAST_ORDER_ID[q.from_user.id]}\nVWAP: {s5['vwap']:.1f} | Vol: {s5['vol_pct']}% | ATR SL: {s5['atr']*1.5:.1f}\nBest tracking start {e}\nNo Alert While LIVE ✅\n\nV120: UPNL + Liq Safe + VWAP will show on next trail!")
   CONFIRM_PENDING.pop(q.from_user.id,None)
   return
  if data=="confirm_no":
   await q.edit_message_text("❌ Skipped! Next signal wait...")
   CONFIRM_PENDING.pop(q.from_user.id,None)
   return
  sz_check,_,_,_,_=get_real_position_full()
  if abs(sz_check)>0.0000001:
   oid=LAST_ORDER_ID.get(q.from_user.id, "Unknown")
   await q.edit_message_text(f"❌ Blocked! Already have position {sz_check} BTC OrderID {oid}! Use /close first!")
   return
  lots=USER_QTY.get(q.from_user.id,1)
  e=price()
  if q.data=="no":
   await q.edit_message_text("Cancelled ❌")
   return
  side="sell" if q.data=="short_yes" else "buy"
  res=order({"product_id":27,"size":lots,"side":side,"order_type":"market_order"})
  if not res.get('success'):
   bal=get_balance()
   await q.edit_message_text(f"❌ Failed! Bal ${bal:.2f} Error: {res}")
   return
  try:
   order_id=res.get('result', {}).get('id', 0)
   if not order_id:
    order_id=int(time.time())
   LAST_ORDER_ID[q.from_user.id]=order_id
  except:
   LAST_ORDER_ID[q.from_user.id]=int(time.time())
  ENTRY_PRICE[q.from_user.id]=e
  BEST_PRICE[q.from_user.id]=e
  LAST_BEST_ALERT[q.from_user.id]=e
  LAST_SL[q.from_user.id]=round(e*(1+SL/100),1) if side=="sell" else round(e*(1-SL/100),1)
  LAST_TP[q.from_user.id]=round(e*(1-TP/100),1) if side=="sell" else round(e*(1+TP/100),1)
  LAST_POS_SIZE[q.from_user.id]=-lots if side=="sell" else lots
  ENTRY_TIME[q.from_user.id]=time.time()
  TRAILING_ACTIVE[q.from_user.id]=False
  s5=get_pro_signal("5m")
  await q.edit_message_text(f"{side.upper()} DONE ✅ SL TRAIL START V120!\nEntry {e} ATR SL {s5['atr']*1.5:.1f} Dynamic TP {TP}% Trail {TRAIL_TP}%\nOrderID: {LAST_ORDER_ID[q.from_user.id]}\nVWAP {s5['vwap']:.1f} Vol {s5['vol_pct']}% ATR {s5['atr']:.1f}\nBest tracking start {e}\nNo Alert While LIVE ✅")
 except Exception as e:
  print(f"button error {e}")
async def close_cmd(u,c):
 ALERT_USERS.add(u.effective_user.id)
 uid=u.effective_user.id
 IS_CLOSING[uid]=True
 saved_id=LAST_ORDER_ID.get(uid, "No ID")
 if saved_id=="None" or not saved_id or "LIVE-" in str(saved_id):
  f=get_latest_order_id_from_api()
  saved_id=f if f else saved_id
 await u.message.reply_text(f"🔍 Searching REAL Delta...\nRemember OrderID: {saved_id}")
 try:
  sz,_,up,liq,margin=get_real_position_full()
  await u.message.reply_text(f"📊 REAL Delta: {sz} BTC UPNL ${up:.2f} Liq {liq:.0f} Remember OrderID: {saved_id}")
  if abs(sz)<0.0000001:
   await u.message.reply_text(f"✅ Already Closed! OrderID {saved_id} Already Closed! Alert Resume ✅")
   BEST_PRICE.pop(uid,None)
   ENTRY_PRICE.pop(uid,None)
   LAST_POS_SIZE[uid]=0
   LAST_SL.pop(uid,None)
   LAST_TP.pop(uid,None)
   ENTRY_TIME.pop(uid,None)
   LAST_ORDER_ID.pop(uid,None)
   TRAILING_ACTIVE.pop(uid,None)
   LAST_BEST_ALERT.pop(uid,None)
   IS_CLOSING[uid]=False
   cancel_all_orders()
   return
  lots=size_to_lots(sz)
  side="buy" if sz<0 else "sell"
  order({"product_id":27,"size":lots,"side":side,"order_type":"market_order","reduce_only":"true","time_in_force":"ioc"})
  await asyncio.sleep(3)
  cancel_all_orders()
  await u.message.reply_text(f"✅ CLOSED! Remember OrderID {saved_id} {sz} BTC Closed at {price():.1f} UPNL ${up:.2f} ✅ Alert Resume!")
  BEST_PRICE.pop(uid,None)
  ENTRY_PRICE.pop(uid,None)
  LAST_POS_SIZE[uid]=0
  LAST_SL.pop(uid,None)
  LAST_TP.pop(uid,None)
  ENTRY_TIME.pop(uid,None)
  LAST_ORDER_ID.pop(uid,None)
  TRAILING_ACTIVE.pop(uid,None)
  LAST_BEST_ALERT.pop(uid,None)
  IS_CLOSING[uid]=False
 except Exception as e:
  await u.message.reply_text(f"❌ Error {e} OrderID {saved_id}")
  IS_CLOSING[u.effective_user.id]=False
def main():
 try:
  requests.get(f"https://api.telegram.org/bot{T}/deleteWebhook?drop_pending_updates=true", timeout=10)
  print("Webhook Deleted ✅")
  time.sleep(2)
 except:
  pass
 app=Application.builder().token(T).build()
 app.add_handler(CommandHandler("start", start))
 app.add_handler(CommandHandler("qty", qty_cmd))
 app.add_handler(CommandHandler("lev", lev_cmd))
 app.add_handler(CommandHandler("trend", trend_cmd))
 app.add_handler(CommandHandler("short", short_cmd))
 app.add_handler(CommandHandler("long", long_cmd))
 app.add_handler(CommandHandler("close", close_cmd))
 app.add_handler(CallbackQueryHandler(button))
 app.job_queue.run_repeating(check_positions_job, interval=15, first=10)
 app.job_queue.run_repeating(auto_alert_job, interval=120, first=30)
 print("BOT LIVE V120 ALL 7 FEATURES")
 app.run_polling(drop_pending_updates=True, allowed_updates=["message","callback_query"])
if __name__=="__main__":
 main()
