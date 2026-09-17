# Maigret Telegram Bot

Telegram bot based on Maigret and designed for a Render Web Service.

## Render

Build command:
```text
sh build.sh
```

Start command:
```text
python bot.py
```

Health check:
```text
/health
```

## Environment variables

Required:
```text
BOT_TOKEN
```

Optional:
```text
WEBHOOK_SECRET
TOP_SITES_COUNT
MAIGRET_TIMEOUT
```

The public Render URL is read automatically from:
```text
RENDER_EXTERNAL_URL
```

The bot searches public profile pages by username. Results come from Maigret's public site database.
