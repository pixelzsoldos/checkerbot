"""Telegram bot magyarországi elérhetőség ellenőrzésére."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger(__name__)

PROXY_FEED_URL = os.environ.get(
    "HU_PROXY_FEED",
    "https://www.proxy-list.download/api/v1/get?type=https&country=HU",
)


def normalize_target(user_input: str) -> Tuple[str, str]:
    cleaned = user_input.strip()
    if not cleaned:
        raise ValueError("Üres címet adtál meg")

    if not re.match(r"^[a-z]+://", cleaned, re.IGNORECASE):
        cleaned = f"https://{cleaned}"

    parsed = urlparse(cleaned)
    if not parsed.netloc:
        raise ValueError("Érvénytelen URL")

    return cleaned, parsed.netloc.lower()


@dataclass
class CheckResult:
    reachable: bool
    message: str
    proxy_ip: Optional[str] = None
    status_code: Optional[int] = None
    elapsed_s: Optional[float] = None
    attempts: int = 0
    errors: Optional[Sequence[str]] = None


class HungarianReachabilityChecker:
    def __init__(self, proxy_feed_url: str, cache_ttl: int = 900, max_proxies: int = 8):
        self._proxy_feed_url = proxy_feed_url
        self._cache_ttl = cache_ttl
        self._max_proxies = max_proxies
        self._cached_proxies: List[str] = []
        self._last_fetch = 0.0

    def _fetch_proxies(self) -> List[str]:
        now = time.time()
        if self._cached_proxies and (now - self._last_fetch) < self._cache_ttl:
            return self._cached_proxies

        try:
            response = requests.get(self._proxy_feed_url, timeout=10)
            response.raise_for_status()
            proxies = [line.strip() for line in response.text.splitlines() if line.strip()]
            self._cached_proxies = proxies
            self._last_fetch = now
            logger.info("%d proxy beolvasva a feedből", len(proxies))
        except requests.RequestException as exc:
            logger.warning("Proxy lista letöltése sikertelen: %s", exc)
            self._cached_proxies = []
        return self._cached_proxies

    def _iter_proxies(self) -> List[str]:
        proxies = self._fetch_proxies()
        trimmed = proxies[: self._max_proxies]
        if not trimmed:
            logger.warning("Nincs proxy elérhető a vizsgálathoz")
        return trimmed

    def _direct_attempt(self, target_url: str) -> CheckResult:
        try:
            start = time.perf_counter()
            resp = requests.get(target_url, timeout=12, allow_redirects=True)
            elapsed = time.perf_counter() - start
            status = resp.status_code
            if 200 <= status < 500:
                return CheckResult(
                    reachable=True,
                    message="Proxy lista nélkül, közvetlen magyar szerverről elérhető volt a webhely.",
                    status_code=status,
                    elapsed_s=elapsed,
                    attempts=0,
                )
            return CheckResult(
                reachable=False,
                message=f"Közvetlen teszt sikertelen (HTTP {status}).",
                status_code=status,
                errors=[f"közvetlen: HTTP {status}"],
                attempts=0,
            )
        except requests.RequestException as exc:
            return CheckResult(
                reachable=False,
                message="Közvetlen lekérés közben hiba történt.",
                errors=[f"közvetlen: {exc.__class__.__name__} – {exc}"],
                attempts=0,
            )

    def check(self, target_url: str) -> CheckResult:
        proxies = self._iter_proxies()
        if not proxies:
            direct = self._direct_attempt(target_url)
            if direct.reachable:
                return direct
            return CheckResult(
                reachable=False,
                message=(
                    "Nem sikerült magyar proxykat találni, és a közvetlen lekérés sem járt sikerrel."
                ),
                errors=(direct.errors or []) + ["proxy lista üres"],
            )

        errors: List[str] = []
        for idx, proxy in enumerate(proxies, start=1):
            proxy_address = f"http://{proxy}"
            logger.debug("%s vizsgálata a %s proxyval", target_url, proxy_address)
            try:
                start = time.perf_counter()
                resp = requests.get(
                    target_url,
                    proxies={"http": proxy_address, "https": proxy_address},
                    timeout=12,
                    allow_redirects=True,
                )
                elapsed = time.perf_counter() - start
                status = resp.status_code
                if 200 <= status < 500:
                    return CheckResult(
                        reachable=True,
                        message="A webhely magyarországi proxykról elérhetőnek tűnik.",
                        proxy_ip=proxy,
                        status_code=status,
                        elapsed_s=elapsed,
                        attempts=idx,
                    )
                errors.append(f"{proxy} → HTTP {status}")
            except requests.RequestException as exc:
                errors.append(f"{proxy}: {exc.__class__.__name__} – {exc}")

        return CheckResult(
            reachable=False,
            message="Egyik teszt-proxy sem tudta elérni a webhelyet.",
            errors=errors,
            attempts=len(proxies),
        )


checker = HungarianReachabilityChecker(PROXY_FEED_URL)


def capture_screenshot(target_url: str) -> Tuple[bytes, str]:
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            context = browser.new_context(
                locale="hu-HU",
                timezone_id="Europe/Budapest",
                viewport={"width": 1280, "height": 720},
            )
            page = context.new_page()
            page.goto(target_url, wait_until="networkidle", timeout=20000)
            title = page.title() or target_url
            screenshot = page.screenshot(full_page=True, type="png")
            context.close()
            browser.close()
            return screenshot, title
    except PlaywrightTimeoutError as exc:
        raise RuntimeError("Időtúllépés történt a képernyőkép készítése közben.") from exc
    except PlaywrightError as exc:
        raise RuntimeError(f"Playwright hiba: {exc}") from exc


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    if not context.args:
        await update.message.reply_text("Használat: /check <domain vagy URL>")
        return

    try:
        url, hostname = normalize_target(context.args[0])
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    await update.message.reply_text(f"🔎 Ellenőrzöm: {hostname} ...")

    result = await asyncio.to_thread(checker.check, url)

    if result.reachable:
        msg = (
            "✅ {host} elérhető magyarországról.\n"
            "Proxy: {proxy}\n"
            "HTTP állapot: {status}\n"
            "Próbálkozások: {attempts}\n"
            "Válaszidő: {elapsed:.1f} mp"
        ).format(
            host=hostname,
            proxy=result.proxy_ip or "ismeretlen",
            status=result.status_code or "n/a",
            attempts=result.attempts,
            elapsed=result.elapsed_s or 0.0,
        )
    else:
        error_lines = "\n".join(result.errors or [])
        msg = (
            "⚠️ {host} nem volt elérhető magyar proxykról.\n"
            "{reason}\n\n"
            "Utolsó hibák:\n{errors}"
        ).format(host=hostname, reason=result.message, errors=error_lines or "(nincs további információ)")

    await update.message.reply_text(msg)


async def screenshot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    if not context.args:
        await update.message.reply_text("Használat: /shot <domain vagy URL>")
        return

    try:
        url, hostname = normalize_target(context.args[0])
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    status_message = await update.message.reply_text("📸 Képernyőkép készítése folyamatban...")

    try:
        screenshot_bytes, title = await asyncio.to_thread(capture_screenshot, url)
    except RuntimeError as exc:
        await status_message.edit_text(f"Nem sikerült a képernyőkép: {exc}")
        return

    caption = (
        "🖼 {host}\n"
        "Oldalcím: {title}\n"
        "Forrás: magyarországi Raspberry Pi"
    ).format(host=hostname, title=title.strip() or hostname)

    await status_message.delete()
    await update.message.reply_photo(photo=BytesIO(screenshot_bytes), caption=caption)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "Szia! Írd be, hogy /check <domain>, és megnézem elérhető-e magyarországról."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text("/check <domain> — elérhetőségi teszt magyar proxykon keresztül.")


def build_application(token: str) -> Application:
    return Application.builder().token(token).build()


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Hiányzik a TELEGRAM_BOT_TOKEN környezeti változó")

    application = build_application(token)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler(["shot", "screenshot"], screenshot_command))

    logger.info("Bot indul...")
    application.run_polling()


if __name__ == "__main__":
    main()
