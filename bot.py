import asyncio
import html
import logging
import os
import re

from quart import Quart, Response, request
import uvicorn

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import maigret

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("maigret_tg_bot")

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
PUBLIC_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()

TOP_SITES_COUNT = int(os.environ.get("TOP_SITES_COUNT", "500"))
TIMEOUT = int(os.environ.get("MAIGRET_TIMEOUT", "20"))

app = Quart(__name__)
telegram_app = None
search_lock = asyncio.Semaphore(1)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


def valid_username(value: str) -> bool:
    return bool(USERNAME_RE.fullmatch(value.strip().lstrip("@")))


async def run_maigret(username: str):
    """Run Maigret with its database bundled in the installed package."""
    db = maigret.MaigretDatabase().load_from_file(
        os.path.join(os.path.dirname(maigret.__file__), "resources", "data.json")
    )
    sites = db.ranked_sites_dict(top=TOP_SITES_COUNT)

    results = await maigret.search(
        username=username,
        site_dict=sites,
        timeout=TIMEOUT,
        logger=logger,
        max_connections=25,
        no_progressbar=True,
        is_parsing_enabled=False,
        is_enrich_enabled=False,
    )
    return results


def format_results(username: str, results: dict) -> list[str]:
    found = []

    for site_name, result in results.items():
        status = result.get("status")
        if status is None or not status.is_found():
            continue

        url = result.get("url_user") or result.get("site_url_user")
        if not url:
            site = result.get("site")
            if site is not None:
                try:
                    url = site.url.format(username=username)
                except Exception:
                    url = None

        if url:
            found.append((site_name, url))

    found.sort(key=lambda item: item[0].lower())

    lines = [
        f"<b>Maigret</b>",
        f"Utilisateur : <code>{html.escape(username)}</code>",
        f"Résultats trouvés : <b>{len(found)}</b>",
        "",
    ]

    for site_name, url in found:
        lines.append(
            f'• <a href="{html.escape(url, quote=True)}">{html.escape(site_name)}</a>'
        )

    # Telegram messages are limited in size. Split before sending.
    chunks = []
    current = ""
    for line in lines:
        candidate = line if not current else current + "\n" + line
        if len(candidate) > 3900:
            chunks.append(current)
            current = line
        else:
            current = candidate

    if current:
        chunks.append(current)

    if not found:
        return [
            f"<b>Maigret</b>\n"
            f"Utilisateur : <code>{html.escape(username)}</code>\n\n"
            "Aucun profil revendiqué n'a été trouvé dans les sites analysés."
        ]

    return chunks


async def perform_search(update: Update, username: str):
    username = username.strip().lstrip("@")

    if not valid_username(username):
        await update.effective_message.reply_text(
            "Nom d'utilisateur invalide. Exemple : @username"
        )
        return

    await update.effective_message.reply_text(
        f"🔎 Recherche Maigret en cours pour @{username}..."
    )

    try:
        async with search_lock:
            results = await run_maigret(username)

        for chunk in format_results(username, results):
            await update.effective_message.reply_text(
                chunk,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

    except Exception:
        logger.exception("Maigret search failed")
        await update.effective_message.reply_text(
            "La recherche a rencontré une erreur. Consulte les logs Render."
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🕵️ Maigret Telegram Bot\n\n"
        "Envoie un nom d'utilisateur pour rechercher ses profils publics.\n\n"
        "Exemple :\n"
        "@username\n\n"
        "Commande : /search username"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text(
            "Utilisation : /search username"
        )
        return

    await perform_search(update, " ".join(context.args))


async def text_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message and update.effective_message.text:
        await perform_search(update, update.effective_message.text)


@app.get("/")
async def home():
    return Response("Maigret Telegram Bot is running.", status=200)


@app.get("/health")
async def health():
    return Response("ok", status=200)


@app.post("/telegram-webhook")
async def telegram_webhook():
    if WEBHOOK_SECRET:
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if supplied != WEBHOOK_SECRET:
            return Response("forbidden", status=403)

    data = await request.get_json()
    if not data:
        return Response("bad request", status=400)

    update = Update.de_json(data=data, bot=telegram_app.bot)
    await telegram_app.update_queue.put(update)
    return Response("ok", status=200)


async def main():
    global telegram_app

    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing.")

    if not PUBLIC_URL:
        raise RuntimeError(
            "RENDER_EXTERNAL_URL is missing. This service must run as a Render Web Service."
        )

    telegram_app = (
        Application.builder()
        .token(TOKEN)
        .updater(None)
        .build()
    )

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("help", help_command))
    telegram_app.add_handler(CommandHandler("search", search_command))
    telegram_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_search)
    )

    async with telegram_app:
        await telegram_app.bot.set_webhook(
            url=f"{PUBLIC_URL}/telegram-webhook",
            allowed_updates=Update.ALL_TYPES,
            secret_token=WEBHOOK_SECRET or None,
        )

        await telegram_app.start()

        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=int(os.environ.get("PORT", "10000")),
            log_level="info",
        )
        server = uvicorn.Server(config)

        logger.info("Maigret Telegram Bot started.")
        logger.info("Webhook: %s/telegram-webhook", PUBLIC_URL)

        await server.serve()

        await telegram_app.stop()


if __name__ == "__main__":
    asyncio.run(main())
