#!/usr/bin/env python3
# (main.py content truncated for brevity in this header; full logic is included below)
import os, asyncio, time, json, logging
from typing import Dict, Any, List, Tuple
import httpx
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
CHAINS = [s.strip() for s in os.getenv("CHAINS", "solana,bsc").split(",")]
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "600"))
WATCH_INTERVAL_SEC = int(os.getenv("WATCH_INTERVAL_SEC", "1800"))
TOP_K = int(os.getenv("TOP_K", "10"))
MIN_LIQ_USD = float(os.getenv("MIN_LIQ_USD", "5000"))
MIN_VOL_H1_USD = float(os.getenv("MIN_VOL_H1_USD", "20000"))
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.7"))
STATE_FILE = os.getenv("STATE_FILE", "state.json")
LOG_SIGNALS = os.getenv("LOG_SIGNALS", "1") == "1"

DEX_BASE = "https://api.dexscreener.com"
HTTP_TIMEOUT = 25.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("onchain-radar")

from honeypot import check_token_safety
from logger import log_pair
from watcher import load_watchlist, add_to_watchlist

class State:
    def __init__(self, path: str):
        self.path = path
        self.sent = {}
    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.sent = json.load(f).get("sent", {})
            except Exception as e:
                logger.warning("State load error: %s", e)
    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"sent": self.sent}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("State save error: %s", e)
STATE = State(STATE_FILE)

def ts_ms(): import time; return int(time.time()*1000)
def fmt_usd(x):
    try:
        v=float(x or 0.0)
        if v>=1_000_000: return f"${v/1_000_000:.2f}M"
        if v>=1_000: return f"${v/1_000:.1f}K"
        return f"${v:.0f}"
    except: return "$0"
def age_minutes(ms): 
    if not ms: return 999999.0
    return max(1.0,(ts_ms()-ms)/60000.0)
def buys_sells(p):
    tx=p.get("txns",{}).get("h1",{})
    return int(tx.get("buys",0) or 0), int(tx.get("sells",0) or 0)
def rough_score(p):
    liq=(p.get("liquidity") or {}).get("usd",0) or 0
    vol=(p.get("volume") or {}).get("h1",0) or 0
    b,s=buys_sells(p)
    age=age_minutes(p.get("pairCreatedAt",0))
    imb=(b-s)/max(1,b+s)
    boosts=(p.get("boosts") or {}).get("active",0) or 0
    return round((vol/50000)*0.35 + (liq/25000)*0.25 + (1/age)*0.25 + max(0,imb)*0.10 + (boosts>0)*0.05,4)
def short_pair_row(p):
    sym=p.get("baseToken",{}).get("symbol") or "?"
    price=p.get("priceUsd") or p.get("priceNative") or "?"
    vol=fmt_usd((p.get("volume") or {}).get("h1",0))
    liq=fmt_usd((p.get("liquidity") or {}).get("usd",0))
    b,s=buys_sells(p); age=int(age_minutes(p.get("pairCreatedAt",0)))
    fdv=p.get("fdv"); url=p.get("url"); sc=p.get("score",0)
    return (f"*{sym}* • 💵Price: `{price}` • 📈Vol1h: {vol} • 💧Liq: {liq}
"
            f"🕒Age: {age}m • 🛒{b}/🛍️{s} • FDV: {fmt_usd(fdv)} • ⚙️Score: *{sc:.2f}*
"
            f"[DexScreener]({url})")

async def fetch_json(client, url):
    r=await client.get(url); r.raise_for_status(); return r.json()
async def fetch_token_boosts(client):
    latest=await fetch_json(client, f"{DEX_BASE}/token-boosts/latest/v1")
    top=await fetch_json(client, f"{DEX_BASE}/token-boosts/top/v1")
    return (latest or [])+(top or [])
async def fetch_pools_by_token(client, chain, token):
    return await fetch_json(client, f"{DEX_BASE}/token-pairs/v1/{chain}/{token}")

async def scan_once():
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"Accept":"*/*"}) as client:
        boosts=await fetch_token_boosts(client)
    items=[]
    for src in boosts:
        if not isinstance(src,dict): continue
        chain=src.get("chainId"); token=src.get("tokenAddress")
        if not chain or not token or chain not in CHAINS: continue
        items.append((chain,token))
    seen=set(); uniq=[]
    for c,t in items:
        k=f"{c}:{t}"
        if k in seen: continue
        seen.add(k); uniq.append((c,t))
    cands=[]
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for chain,token in uniq:
            try:
                pools=await fetch_pools_by_token(client, chain, token)
                for p in pools or []:
                    liq=(p.get("liquidity") or {}).get("usd",0) or 0
                    vol=(p.get("volume") or {}).get("h1",0) or 0
                    if liq<MIN_LIQ_USD or vol<MIN_VOL_H1_USD: continue
                    safe=await check_token_safety(chain, p.get("baseToken",{}).get("address"))
                    if not safe.get("ok"): 
                        logging.info("Filtered by safety %s: %s", p.get("baseToken",{}).get("symbol"), safe.get("reason"))
                        continue
                    p["score"]=rough_score(p); p["chainId"]=chain; cands.append(p)
            except Exception as e:
                logging.debug("fetch pools err %s:%s -> %s", chain, token, e)
    return sorted(cands, key=lambda x:x.get("score",0), reverse=True)[:TOP_K]

async def cmd_start(u:Update,c:ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("👋 Привет! Команды: /top /status /watch <token> /help")
async def cmd_help(u:Update,c:ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("ℹ️ Boosts → pools → фильтры → honeypot-check → скоринг → алёрты.")
async def cmd_status(u:Update,c:ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(f"✅ Онлайн. Сети: {', '.join(CHAINS)}; Interval: {POLL_INTERVAL_SEC}s; Watch: {WATCH_INTERVAL_SEC}s; Score≥{SCORE_THRESHOLD}")
async def cmd_top(u:Update,c:ContextTypes.DEFAULT_TYPE):
    ranked=await scan_once()
    if not ranked: return await u.message.reply_text("Пока пусто по текущим фильтрам.")
    await u.message.reply_text("

".join(short_pair_row(p) for p in ranked), parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
async def cmd_watch(u:Update,c:ContextTypes.DEFAULT_TYPE):
    if not c.args: return await u.message.reply_text("Использование: /watch <адрес_токена>")
    add_to_watchlist(c.args[0].strip()); await u.message.reply_text(f"✅ Добавлено: `{c.args[0]}`", parse_mode=ParseMode.MARKDOWN)

async def background_scanner(app:Application):
    while True:
        try:
            ranked=await scan_once()
            alerts=[]
            for p in ranked:
                addr=p.get("pairAddress"); sc=p.get("score",0.0)
                last=STATE.sent.get(addr,0.0)
                if sc>=SCORE_THRESHOLD and (time.time()-last)>3600:
                    alerts.append(p); STATE.sent[addr]=time.time()
                    if LOG_SIGNALS:
                        try: log_pair(p)
                        except Exception: pass
            if alerts and ADMIN_CHAT_ID:
                msg="🚨 *Новые кандидаты (Solana/BSC)*

"+ "

".join(short_pair_row(p) for p in alerts)
                try: await app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
                except Exception as e: logging.warning("send err: %s", e)
            STATE.save()
        except Exception as e:
            logging.error("worker err: %s", e)
        await asyncio.sleep(POLL_INTERVAL_SEC)

async def watch_worker(app:Application):
    while True:
        try:
            tokens=load_watchlist()
            if tokens:
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                    for t in tokens:
                        for chain in CHAINS:
                            try:
                                pools=await fetch_pools_by_token(client, chain, t)
                                for p in pools or []:
                                    safe=await check_token_safety(chain, p.get("baseToken",{}).get("address"))
                                    if not safe.get("ok"): continue
                                    p["score"]=rough_score(p)
                                    if p["score"]>=SCORE_THRESHOLD:
                                        msg="👁️ *Watch alert*

"+short_pair_row(p)
                                        await app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
                                        if LOG_SIGNALS:
                                            try: log_pair(p)
                                            except Exception: pass
                            except Exception as e:
                                logging.debug("watch fetch err %s:%s", chain, e)
        except Exception as e:
            logging.error("watch worker err: %s", e)
        await asyncio.sleep(WATCH_INTERVAL_SEC)

async def on_startup(app:Application):
    STATE.load()
    app.create_task(background_scanner(app))
    app.create_task(watch_worker(app))
    logging.info("Workers started")

def main():
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is not set")
    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.post_init=on_startup
    logging.info("🚀 Starting bot...")
    app.run_polling(close_loop=False)

if __name__=="__main__":
    main()
