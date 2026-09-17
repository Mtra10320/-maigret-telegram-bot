# Maigret Telegram Bot for Render

Version adaptée au déploiement Render Free.

## Variables Render

BOT_TOKEN = token fourni par @BotFather

WEBHOOK_URL = URL publique Render, par exemple :
https://maigret-tg-bot.onrender.com

WEBHOOK_SECRET = chaîne aléatoire facultative

TOP_SITES_COUNT = 1500
MAIGRET_TIMEOUT = 30

## Déploiement

Build command:
pip install -r requirements.txt

Start command:
python bot.py

Le service doit utiliser le plan Free.

## Important

Le service utilise le webhook Telegram. Le plan Free de Render peut mettre le service en veille après 15 minutes sans trafic HTTP. Telegram réveille le service lorsqu'un webhook arrive, avec un délai de démarrage possible.

Le dépôt ne contient aucun token Telegram.
