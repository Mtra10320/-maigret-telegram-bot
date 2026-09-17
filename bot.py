import asyncio, html, logging, os, re
from urllib.parse import urlparse
from quart import Quart, Response, request
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
import maigret

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("maigret")
TOKEN=os.environ.get("BOT_TOKEN","").strip()
URL=os.environ.get("RENDER_EXTERNAL_URL","").rstrip("/")
SECRET=os.environ.get("WEBHOOK_SECRET","").strip()
TOP=int(os.environ.get("TOP_SITES_COUNT","500"))
TIMEOUT=int(os.environ.get("MAIGRET_TIMEOUT","20"))
PAGE=10
app=Quart(__name__)
tg=None
lock=asyncio.Semaphore(1)
states={}
rx=re.compile(r"^[A-Za-z0-9_.-]{3,64}$")

async def search_maigret(username):
    db=maigret.MaigretDatabase().load_from_file(os.path.join(os.path.dirname(maigret.__file__),"resources","data.json"))
    sites=db.ranked_sites_dict(top=TOP)
    return await maigret.search(username=username,site_dict=sites,timeout=TIMEOUT,logger=log,
        max_connections=25,no_progressbar=True,is_parsing_enabled=False,is_enrich_enabled=False)

def found_items(results):
    out=[]
    for name,r in results.items():
        try: status=r.get("status")
        except AttributeError: continue
        if status is None or not status.is_found(): continue
        url=r.get("url_user") or r.get("site_url_user")
        if not url:
            site=r.get("site")
            if site:
                try: url=site.url.format(username=r.get("username") or "")
                except Exception: pass
        if url: out.append((str(name),str(url)))
    return sorted(out,key=lambda x:x[0].lower())

def category(url):
    h=urlparse(url).netloc.lower()
    if any(x in h for x in ("instagram.","facebook.","tiktok.","x.com","twitter.","threads.")): return "Social"
    if any(x in h for x in ("github.","gitlab.","bitbucket.","codepen.","stackoverflow.")): return "Dev"
    if any(x in h for x in ("youtube.","twitch.","vimeo.","dailymotion.","soundcloud.","bandcamp.")): return "Media"
    return "Web"

def markup(page,total):
    last=max(1,(total+PAGE-1)//PAGE)
    nav=[]
    if page: nav.append(InlineKeyboardButton("◀️",callback_data=f"p:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{last}",callback_data="noop"))
    if page<last-1: nav.append(InlineKeyboardButton("▶️",callback_data=f"p:{page+1}"))
    return InlineKeyboardMarkup([nav,[InlineKeyboardButton("🔎 Nouvelle recherche",callback_data="new")]])

def render(user,items,page):
    start=page*PAGE
    lines=[f"🔎 <b>Résultats Maigret</b>",f"Utilisateur : <code>{html.escape(user)}</code>",
           f"Profils trouvés : <b>{len(items)}</b>",f"Page : <b>{page+1}/{max(1,(len(items)+PAGE-1)//PAGE)}</b>",""]
    for i,(name,url) in enumerate(items[start:start+PAGE],start+1):
        lines.append(f'{i}. <a href="{html.escape(url,quote=True)}">{html.escape(name)}</a> · {category(url)}')
    return "\n".join(lines)

async def start(u,c):
    await u.effective_message.reply_text(
        "🕵️ <b>Maigret OSINT Bot</b>\n\nRecherche de profils publics par nom d'utilisateur.\n\nEnvoie <code>@username</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔎 Nouvelle recherche",callback_data="new")],
                                            [InlineKeyboardButton("ℹ️ Aide",callback_data="help")]]))

async def help_cmd(u,c):
    await u.effective_message.reply_text(
        "ℹ️ <b>Aide</b>\n\nEnvoie un nom d'utilisateur public.\nExemple : <code>@username</code>\n\nLes résultats proviennent de la base publique Maigret.",
        parse_mode=ParseMode.HTML)

async def search_cmd(u,c):
    if not c.args:
        await u.effective_message.reply_text("Utilisation : <code>/search username</code>",parse_mode=ParseMode.HTML); return
    await do_search(u," ".join(c.args))

async def text_cmd(u,c):
    if u.effective_message and u.effective_message.text: await do_search(u,u.effective_message.text)

async def do_search(u,username):
    username=username.strip().lstrip("@")
    if not rx.fullmatch(username):
        await u.effective_message.reply_text("Nom d'utilisateur invalide. Exemple : <code>@username</code>",parse_mode=ParseMode.HTML); return
    msg=await u.effective_message.reply_text(f"🔎 Recherche en cours pour <b>@{html.escape(username)}</b>...",parse_mode=ParseMode.HTML)
    try:
        async with lock: results=await search_maigret(username)
        items=found_items(results); states[u.effective_user.id]=(username,items)
        await msg.edit_text(render(username,items,0),parse_mode=ParseMode.HTML,disable_web_page_preview=True,reply_markup=markup(0,len(items)))
    except Exception:
        log.exception("search failed")
        await msg.edit_text("❌ Erreur pendant la recherche. Consulte les logs Render.")

async def callbacks(u,c):
    q=u.callback_query; await q.answer()
    if q.data=="noop": return
    if q.data=="help":
        await q.edit_message_text("ℹ️ Envoie un username public, par exemple <code>@username</code>.",parse_mode=ParseMode.HTML); return
    if q.data=="new":
        await q.edit_message_text("🔎 Envoie maintenant le nom d'utilisateur à rechercher.\nExemple : <code>@username</code>",parse_mode=ParseMode.HTML); return
    if q.data.startswith("p:"):
        state=states.get(q.from_user.id)
        if not state: await q.edit_message_text("Recherche expirée. Lance une nouvelle recherche."); return
        page=int(q.data.split(":")[1]); user,items=state
        await q.edit_message_text(render(user,items,page),parse_mode=ParseMode.HTML,disable_web_page_preview=True,reply_markup=markup(page,len(items)))

@app.get("/")
async def home(): return Response("Maigret Telegram Bot is running.",200)
@app.get("/health")
async def health(): return Response("ok",200)

@app.post("/telegram-webhook")
async def webhook():
    if SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token","")!=SECRET: return Response("forbidden",403)
    data=await request.get_json()
    if not data: return Response("bad request",400)
    await tg.update_queue.put(Update.de_json(data=data,bot=tg.bot))
    return Response("ok",200)

async def main():
    global tg
    if not TOKEN: raise RuntimeError("BOT_TOKEN is missing.")
    if not URL: raise RuntimeError("RENDER_EXTERNAL_URL is missing.")
    tg=Application.builder().token(TOKEN).updater(None).build()
    tg.add_handler(CommandHandler("start",start)); tg.add_handler(CommandHandler("help",help_cmd))
    tg.add_handler(CommandHandler("search",search_cmd)); tg.add_handler(CallbackQueryHandler(callbacks))
    tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_cmd))
    async with tg:
        await tg.bot.set_webhook(f"{URL}/telegram-webhook",allowed_updates=Update.ALL_TYPES,secret_token=SECRET or None)
        await tg.start()
        server=uvicorn.Server(uvicorn.Config(app,host="0.0.0.0",port=int(os.environ.get("PORT","10000")),log_level="info"))
        log.info("Maigret Telegram Bot v2 started")
        await server.serve()
        await tg.stop()

if __name__=="__main__": asyncio.run(main())
