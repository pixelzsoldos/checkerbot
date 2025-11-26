# Hungarian reachability checker bot

This is a simple Telegram bot that checks whether a given website is reachable from Hungary.  
On `/check example.com` it tries to reach the site via several Hungarian HTTPS proxies.  
If there are no proxies available, it performs at least one direct check from the Raspberry Pi in Hungary.

## Requirements
- Python 3.11+
- Chromium + Playwright browser (install with `playwright install chromium`)
- Telegram bot token (`TELEGRAM_BOT_TOKEN` environment variable)
- Outbound HTTP access to `https://www.proxy-list.download` (default proxy feed)
- Python packages:
  - `python-telegram-bot==21.6`
  - `requests>=2.31.0`
  - `python-dotenv>=1.0.0`
  - `playwright>=1.48.0`

## Installation
```bash
cd /mnt/ssd/apps/checkerbot
python3 -m venv .venv
source .venv/bin/activate
pip install python-telegram-bot==21.6 requests>=2.31.0 python-dotenv>=1.0.0 playwright>=1.48.0
playwright install chromium
```

## Running
```bash
export TELEGRAM_BOT_TOKEN=123456:ABC...
python bot.py
```

### Using a `.env` file
Create a `.env` file in the project root:
```bash
TELEGRAM_BOT_TOKEN=123456:ABC...
HU_PROXY_FEED=https://...
LOG_LEVEL=INFO
DEFAULT_LANG=hu
```
The bot automatically loads this file on startup via `python-dotenv`.

## Environment variables
- `TELEGRAM_BOT_TOKEN` (required): token from BotFather
- `HU_PROXY_FEED` (optional): custom proxy list URL
- `LOG_LEVEL` (optional): e.g. `DEBUG`, `INFO`
- `DEFAULT_LANG` (optional): default bot language, `hu` or `en` (default: `hu`)

## Commands
- `/check example.com` – HTTP reachability test from Hungarian proxies (with direct fallback).
- `/shot example.com` – loads the page with Chromium on the Pi, takes a screenshot and sends it back. Alias: `/screenshot`.
- `/lang hu` or `/lang en` – sets the language for the current chat (Hungarian or English).

## Docker (optional)
You can build a container with the provided `Dockerfile` so the bot can run continuously in the background.

```bash
docker build -t hu-reach-bot .
docker run -d --name hu-reach \
  -e TELEGRAM_BOT_TOKEN=123456:ABC... \
  --restart unless-stopped \
  hu-reach-bot
```

The same steps can be performed via Portainer: upload/clone the source code, build the image, then start a container with the required environment variables.

## Disclaimer
Public proxies can be unreliable, so the result is for information purposes only.  
You may want to repeat the check, or use your own verified Hungarian VPS instead of the public proxy list.
