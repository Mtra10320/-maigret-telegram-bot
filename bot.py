import logging
import os
import re
import tempfile
from pathlib import Path

from flask import Flask
from threading import Thread

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import maigret
from maigret.report import generate_report_context, save_pdf_report
from maigret.result import QueryStatus
from maigret.sites import MaigretDatabase

BOT_TOKEN = os.environ.get("BOT_TOKEN")
TOP_SITES_COUNT = int(os.environ.get("TOP_SITES_COUNT", "1500"))
TIMEOUT = int(os.environ.get("MAIGRET_TIMEOUT", "30"))
USERNAME_REGEXP = re.compile(r"^[a-zA-Z0-9_.-]{5,}$")

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "data.json"
COOKIES_FILE = BASE_DIR / "cookies.txt"

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("maigret-tg-bot")

app = Flask(__name__)

@app.get("/")
def health():
    return "Maigret Telegram bot is running", 200

@app.get("/health")
def health_check():
    return "ok", 200

def run_web_server():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

async def maigret_search(username: str):
    db = MaigretDatabase().load_from_path(str(DB_FILE))
    sites = db.ranked_sites_dict(top=TOP_SITES_COUNT)
    return await maigret.search(
        username=username,
        site_dict=sites,
        timeout=TIMEOUT,
        logger=logger,
        id_type="username",
        cookies=str(COOKIES_FILE),
    )

def split_messages(items, limit=4000):
    chunks = []
    current = ""
    for item in items:
        candidate = item if not current else current + "\n" + item
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            current = item
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks

async def run_search(username: str):
    results = await maigret_search(username)
    found = []

    for site, data in results.items():
        status = data.get("status")
        if status and status.status == QueryStatus.CLAIMED and not data.get("is_similar"):
            url = data.get("url_user")
            if url:
                found.append(f"[{site}]({url})")

    messages = split_messages(found)
    return messages, results

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Maigret OSINT Bot\n\n"
        "Envoie-moi un pseudonyme public d'au moins 5 caractères.\n"
        "Exemple : username123"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start pour commencer\n"
        "/help pour afficher cette aide\n\n"
        "Puis envoie un username public."
    )

async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    username = update.message.text.strip().lstrip("@")

    if not USERNAME_REGEXP.fullmatch(username):
        await update.message.reply_text(
            "Username invalide. Utilise au moins 5 caractères, "
            "avec lettres, chiffres, point, tiret ou underscore."
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    status_message = await update.message.reply_text(
        f"Recherche Maigret en cours pour : {username}"
    )

    try:
        messages, results = await run_search(username)

        claimed = sum(
            1
            for data in results.values()
            if data.get("status") and data["status"].status == QueryStatus.CLAIMED
        )

        if not messages:
            await status_message.edit_text(
                f"Aucun compte public trouvé pour {username}."
            )
            return

        await status_message.edit_text(
            f"{claimed} résultat(s) trouvé(s) pour {username}."
        )

        for message in messages:
            await update.message.reply_text(
                message,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )

        # Generate a temporary PDF report and send it to the user.
        try:
            general_results = [(username, "username", results)]
            report_context = generate_report_context(general_results)

            with tempfile.TemporaryDirectory() as tmp:
                pdf_path = Path(tmp) / f"{username}_report.pdf"
                save_pdf_report(str(pdf_path), report_context)
                if pdf_path.exists():
                    with pdf_path.open("rb") as pdf:
                        await update.message.reply_document(
                            document=pdf,
                            filename=pdf_path.name,
                            caption=f"Rapport Maigret : {username}",
                        )
        except Exception:
            logger.exception("PDF generation failed")

    except Exception:
        logger.exception("Search failed")
        await status_message.edit_text(
            "Erreur pendant la recherche. Réessaie avec ce username."
        )

async def post_init(application: Application):
    # Render's free web service uses the public URL as Telegram's webhook.
    webhook_url = os.environ.get("WEBHOOK_URL")
    if webhook_url:
        webhook_url = webhook_url.rstrip("/") + "/telegram-webhook"
        await application.bot.set_webhook(
            url=webhook_url,
            secret_token=os.environ.get("WEBHOOK_SECRET"),
        )
        logger.info("Telegram webhook configured: %s", webhook_url)
    else:
        logger.warning("WEBHOOK_URL is not set.")

async def post_shutdown(application: Application):
    try:
        await application.bot.delete_webhook()
    except Exception:
        logger.exception("Could not delete webhook")

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing.")

    Thread(target=run_web_server, daemon=True).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username)
    )

    webhook_path = "telegram-webhook"
    port = int(os.environ.get("PORT", "10000"))
    webhook_url = os.environ.get("WEBHOOK_URL")

    if not webhook_url:
        raise RuntimeError("WEBHOOK_URL is missing.")

    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=webhook_path,
        webhook_url=webhook_url.rstrip("/") + "/" + webhook_path,
        secret_token=os.environ.get("WEBHOOK_SECRET"),
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )

if __name__ == "__main__":
    main()
