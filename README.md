# Magyar reachability checker bot

Ez az egyszerű Telegram bot magyarországi HTTPS proxykon keresztül próbálja lekérni a megadott webhelyet. A `/check example.com` parancsra egymás után több magyar IP-ről kísérli meg az elérést, majd jelzi, hogy sikerült-e kapcsolatot létesíteni. Ha nincs elérhető proxy, a bot legalább közvetlenül, magáról a magyar Raspberry Pi-ről futtat egy ellenőrzést.

## Követelmények
- Python 3.11+
- Chromium + Playwright böngésző (telepítés: `playwright install chromium`)
- Telegram bot token (`TELEGRAM_BOT_TOKEN` környezeti változó)
- Külső HTTP elérés a `https://www.proxy-list.download` felé (alapértelmezett proxy feed)

## Telepítés
```bash
cd /mnt/ssd/apps/checkerbot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Futtatás
```bash
export TELEGRAM_BOT_TOKEN=123456:ABC...
python bot.py
```

### `.env` fájl használata
Hozz létre egy `.env` fájlt a projekt gyökerében:
```
TELEGRAM_BOT_TOKEN=123456:ABC...
HU_PROXY_FEED=https://...
```
Indításkor a bot automatikusan betölti ezt a fájlt (`python-dotenv`).

## Környezeti változók
- `TELEGRAM_BOT_TOKEN`: kötelező, a BotFather-től kapott token
- `HU_PROXY_FEED`: opcionális, saját proxy forrás URL-je
- `LOG_LEVEL`: opcionális, pl. `DEBUG`

## Parancsok
- `/check example.com` – HTTP-s elérhetőségi vizsgálat (magyar proxyk + közvetlen fallback).
- `/shot example.com` – Chromiummal tölti be az oldalt a Pi-ről, képernyőképet készít és visszaküldi a chatbe. Alias: `/screenshot`.

## Docker (opcionális)
Készíthetsz konténert a mellékelt `Dockerfile` segítségével, így a bot folyamatosan futhat háttérben.

```bash
docker build -t hu-reach-bot .
docker run -d --name hu-reach \
  -e TELEGRAM_BOT_TOKEN=123456:ABC... \
  --restart unless-stopped \
  hu-reach-bot
```

Portainerben ugyanezeket a lépéseket UI-n keresztül is elvégezheted: töltsd fel/klónozd a forráskódot, készíts image-et, majd indíts konténert környezeti változóval.

## Figyelmeztetés
A nyilvános proxyk megbízhatatlanok lehetnek, ezért az eredmény tájékoztató jellegű. Ismételt próbálkozás ajánlott, illetve érdemes saját, ellenőrzött magyar VPS-t használni a proxy lista helyett.
