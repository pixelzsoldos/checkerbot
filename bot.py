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

DEFAULT_LANG = os.environ.get("DEFAULT_LANG", "hu").lower()

SUPPORTED_LANGS = ("hu", "en")

MESSAGES = {
    "empty_url": {
        "hu": "Üres címet adtál meg",
        "en": "You provided an empty address",
    },
    "invalid_url": {
        "hu": "Érvénytelen URL",
        "en": "Invalid URL",
    },
    "direct_ok": {
        "hu": "Proxy lista nélkül, közvetlen magyar szerverről elérhető volt a webhely.",
        "en": "The site was reachable directly from the Hungarian server without using the proxy list.",
    },
    "direct_fail": {
        "hu": "Közvetlen teszt sikertelen (HTTP {status}).",
        "en": "Direct test failed (HTTP {status}).",
    },
    "direct_error": {
        "hu": "Közvetlen lekérés közben hiba történt.",
        "en": "An error occurred during the direct request.",
    },
    "no_proxies_and_direct_fail": {
        "hu": "Nem sikerült magyar proxykat találni, és a közvetlen lekérés sem járt sikerrel.",
        "en": "No Hungarian proxies were found and the direct request also failed.",
    },
    "no_proxies_available": {
        "hu": "Nincs proxy elérhető a vizsgálathoz",
        "en": "No proxy available for the check",
    },
    "check_usage": {
        "hu": "Használat: /check <domain vagy URL>",
        "en": "Usage: /check <domain or URL>",
    },
    "shot_usage": {
        "hu": "Használat: /shot <domain vagy URL>",
        "en": "Usage: /shot <domain or URL>",
    },
    "checking_host": {
        "hu": "🔎 Ellenőrzöm: {hostname} ...",
        "en": "🔎 Checking: {hostname} ...",
    },
    "reachable": {
        "hu": (
            "✅ {host} elérhető magyarországról.\n"
            "Proxy: {proxy}\n"
            "HTTP állapot: {status}\n"
            "Próbálkozások: {attempts}\n"
            "Válaszidő: {elapsed:.1f} mp"
        ),
        "en": (
            "✅ {host} is reachable from Hungary.\n"
            "Proxy: {proxy}\n"
            "HTTP status: {status}\n"
            "Attempts: {attempts}\n"
            "Response time: {elapsed:.1f} s"
        ),
    },
    "unreachable": {
        "hu": (
            "⚠️ {host} nem volt elérhető magyar proxykról.\n"
            "{reason}\n\n"
            "Utolsó hibák:\n{errors}"
        ),
        "en": (
            "⚠️ {host} was not reachable from Hungarian proxies.\n"
            "{reason}\n\n"
            "Last errors:\n{errors}"
        ),
    },
    "screenshot_in_progress": {
        "hu": "📸 Képernyőkép készítése folyamatban...",
        "en": "📸 Taking screenshot, please wait...",
    },
    "screenshot_failed": {
        "hu": "Nem sikerült a képernyőkép: {error}",
        "en": "Failed to create screenshot: {error}",
    },
    "screenshot_caption": {
        "hu": (
            "🖼 {host}\n"
            "Oldalcím: {title}\n"
            "Forrás: magyarországi Raspberry Pi"
        ),
        "en": (
            "🖼 {host}\n"
            "Page title: {title}\n"
            "Source: Raspberry Pi in Hungary"
        ),
    },
    "start": {
        "hu": "Szia! Írd be, hogy /check <domain>, és megnézem elérhető-e magyarországról.",
        "en": "Hi! Send /check <domain> and I will check if it is reachable from Hungary.",
    },
    "help": {
        "hu": "/check <domain> — elérhetőségi teszt magyar proxykon keresztül.",
        "en": "/check <domain> — reachability test via Hungarian proxies.",
    },
    "missing_token": {
        "hu": "Hiányzik a TELEGRAM_BOT_TOKEN környezeti változó",
        "en": "Missing TELEGRAM_BOT_TOKEN environment variable",
    },
    "lang_usage": {
        "hu": "Használat: /lang <hu|en>\nAktuális nyelv: {lang}",
        "en": "Usage: /lang <hu|en>\nCurrent language: {lang}",
    },
    "lang_set": {
        "hu": "Nyelv beállítva: magyar",
        "en": "Language set to: English",
    },
    "lang_invalid": {
        "hu": "Ismeretlen nyelv. Támogatott: hu, en",
        "en": "Unknown language. Supported: hu, en",
    },
    "proxy_all_failed": {
        "hu": "Egyik vizsgált proxy sem tudta elérni a webhelyet.",
        "en": "None of the tested proxies could reach the site.",
    },
}


class UserError(Exception):
    """Felhasználónak szánt, lokalizálható hiba."""

    def __init__(self, key: str):
        self.key = key
        super().__init__(key)


def normalize_lang(lang: str) -> str:
    lang = (lang or "").lower()
    if lang in SUPPORTED_LANGS:
        return lang
    return "hu"


def get_chat_lang(context: ContextTypes.DEFAULT_TYPE, chat_id: Optional[int]) -> str:
    store = getattr(context.application, "lang_store", None)
    if not isinstance(store, dict) or chat_id is None:
        return DEFAULT_LANG
    return normalize_lang(store.get(chat_id, DEFAULT_LANG))


def set_chat_lang(context: ContextTypes.DEFAULT_TYPE, chat_id: Optional[int], lang: str) -> None:
    if chat_id is None:
        return
    if not hasattr(context.application, "lang_store") or not isinstance(
        context.application.lang_store, dict
    ):
        context.application.lang_store = {}
    context.application.lang_store[chat_id] = normalize_lang(lang)


def t(key: str, lang: str, **kwargs: object) -> str:
    variants = MESSAGES.get(key) or {}
    template = variants.get(lang) or variants.get("hu") or ""
    try:
        return template.format(**kwargs)
    except Exception:
        return template


def normalize_target(user_input: str) -> Tuple[str, str]:
    cleaned = user_input.strip()
    if not cleaned:
        raise UserError("empty_url")

    if not re.match(r"^[a-z]+://", cleaned, re.IGNORECASE):
        cleaned = f"https://{cleaned}"

    parsed = urlparse(cleaned)
    if not parsed.netloc:
        raise UserError("invalid_url")

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
            logger.warning("no_proxies_available")
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
                    message="direct_ok",
                    status_code=status,
                    elapsed_s=elapsed,
                    attempts=0,
                )
            return CheckResult(
                reachable=False,
                message="direct_fail",
                status_code=status,
                errors=[f"közvetlen: HTTP {status}"],
                attempts=0,
            )
        except requests.RequestException as exc:
            return CheckResult(
                reachable=False,
                message="direct_error",
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
                message="no_proxies_and_direct_fail",
                errors=(direct.errors or []) + ["no_proxies_available"],
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
                        message="direct_ok",
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
            message="proxy_all_failed",
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

    chat = update.effective_chat
    chat_id = chat.id if chat else None
    lang = get_chat_lang(context, chat_id)

    if not context.args:
        await update.message.reply_text(t("check_usage", lang))
        return

    try:
        url, hostname = normalize_target(context.args[0])
    except UserError as exc:
        await update.message.reply_text(t(exc.key, lang))
        return
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    await update.message.reply_text(t("checking_host", lang, hostname=hostname))

    result = await asyncio.to_thread(checker.check, url)

    if result.reachable:
        proxy_label = result.proxy_ip or ("ismeretlen" if lang == "hu" else "unknown")
        msg = t(
            "reachable",
            lang,
            host=hostname,
            proxy=proxy_label,
            status=result.status_code or "n/a",
            attempts=result.attempts,
            elapsed=result.elapsed_s or 0.0,
        )
    else:
        error_lines = "\n".join(result.errors or [])
        reason_key = result.message
        reason = t(reason_key, lang) if reason_key in MESSAGES else reason_key
        default_errors = "(nincs további információ)" if lang == "hu" else "(no further information)"
        msg = t(
            "unreachable",
            lang,
            host=hostname,
            reason=reason,
            errors=error_lines or default_errors,
        )

    await update.message.reply_text(msg)


async def screenshot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    chat = update.effective_chat
    chat_id = chat.id if chat else None
    lang = get_chat_lang(context, chat_id)

    if not context.args:
        await update.message.reply_text(t("shot_usage", lang))
        return

    try:
        url, hostname = normalize_target(context.args[0])
    except UserError as exc:
        await update.message.reply_text(t(exc.key, lang))
        return
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    status_message = await update.message.reply_text(t("screenshot_in_progress", lang))

    try:
        screenshot_bytes, title = await asyncio.to_thread(capture_screenshot, url)
    except RuntimeError as exc:
        await status_message.edit_text(t("screenshot_failed", lang, error=exc))
        return

    caption = t(
        "screenshot_caption",
        lang,
        host=hostname,
        title=title.strip() or hostname,
    )

    await status_message.delete()
    await update.message.reply_photo(photo=BytesIO(screenshot_bytes), caption=caption)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    chat = update.effective_chat
    chat_id = chat.id if chat else None
    lang = get_chat_lang(context, chat_id)
    await update.message.reply_text(t("start", lang))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    chat = update.effective_chat
    chat_id = chat.id if chat else None
    lang = get_chat_lang(context, chat_id)
    await update.message.reply_text(t("help", lang))


async def lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    chat = update.effective_chat
    chat_id = chat.id if chat else None
    current_lang = get_chat_lang(context, chat_id)

    if not context.args:
        await update.message.reply_text(t("lang_usage", current_lang, lang=current_lang))
        return

    requested = normalize_lang(context.args[0])
    if requested not in SUPPORTED_LANGS:
        await update.message.reply_text(t("lang_invalid", current_lang))
        return

    set_chat_lang(context, chat_id, requested)
    await update.message.reply_text(t("lang_set", requested))


def build_application(token: str) -> Application:
    return Application.builder().token(token).build()


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(MESSAGES["missing_token"]["hu"])

    application = build_application(token)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler(["shot", "screenshot"], screenshot_command))
    application.add_handler(CommandHandler("lang", lang_command))

    logger.info("Bot indul...")
    application.run_polling()


if __name__ == "__main__":
    main()
