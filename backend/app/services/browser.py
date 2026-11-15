"""Playwright brauzerlarini yagona usulda ishga tushirish.

Chromium oilasi (Chromium, Chrome, Edge), Firefox va WebKit qo'llanadi.
Chrome/Edge tizimda o'rnatilgan brauzerni, qolganlari esa Playwright
brauzerlarini ishlatadi. WebKit Safari yadrosidir, lekin Safari ilovasining
o'zi emas.
"""
from __future__ import annotations

import os

from app.core.config import settings


SUPPORTED_BROWSERS = ("chromium", "chrome", "msedge", "firefox", "webkit")
BROWSER_LABELS = {
    "chromium": "Chromium",
    "chrome": "Google Chrome",
    "msedge": "Microsoft Edge",
    "firefox": "Firefox",
    "webkit": "WebKit (Safari yadrosi)",
}

_CHROMIUM_BROWSERS = frozenset(("chromium", "chrome", "msedge"))
_BROWSER_ALIASES = {
    "edge": "msedge",
    "google-chrome": "chrome",
    "safari": "webkit",
}
_CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    # Fon tab / oyna yashirilganda taymerlar sekinlashmasin.
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=CalculateNativeWinOcclusion",
]
_CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def normalize_browser(value: str | None = None) -> str:
    """Brauzer nomini kanonik ko'rinishga keltiradi va tekshiradi."""
    configured = getattr(settings, "BROWSER_TYPE", "chromium")
    candidate = value if value and value.strip() else configured
    name = str(candidate or "chromium").strip().lower()
    name = _BROWSER_ALIASES.get(name, name)
    if name not in SUPPORTED_BROWSERS:
        supported = ", ".join(SUPPORTED_BROWSERS)
        raise ValueError(f"Noto'g'ri brauzer: {name}. Mavjud: {supported}")
    return name


def browser_engine(value: str | None = None) -> str:
    """Playwright'dagi chromium/firefox/webkit engine nomini qaytaradi."""
    name = normalize_browser(value)
    return "chromium" if name in _CHROMIUM_BROWSERS else name


def launch_options(value: str | None = None, headless: bool | None = None) -> dict:
    """Tanlangan brauzer uchun Playwright launch parametrlarini yasaydi.

    ``headless`` berilsa, umumiy sozlamadan ustun turadi (masalan,
    harakatlarni yozib olish uchun ko'rinadigan oyna kerak).
    """
    name = normalize_browser(value)
    options: dict = {
        "headless": settings.BROWSER_HEADLESS if headless is None else bool(headless),
        "slow_mo": settings.BROWSER_SLOW_MO,
    }

    # Chromium flaglarini Firefox/WebKit'ga uzatib bo'lmaydi.
    if browser_engine(name) == "chromium":
        options["args"] = list(_CHROMIUM_ARGS)

    if name in ("chrome", "msedge"):
        options["channel"] = name
    elif name == "chromium":
        executable = (settings.BROWSER_EXECUTABLE or "").strip()
        if executable and os.path.exists(executable):
            options["executable_path"] = executable
        elif (settings.BROWSER_CHANNEL or "").strip():
            options["channel"] = settings.BROWSER_CHANNEL.strip()
    return options


def context_options(value: str | None = None) -> dict:
    """Brauzer engine'iga mos yangi context parametrlarini qaytaradi."""
    name = normalize_browser(value)
    options: dict = {
        "viewport": {
            "width": settings.VIEWPORT_WIDTH,
            "height": settings.VIEWPORT_HEIGHT,
        },
        "locale": "ru-RU",
        "timezone_id": "Asia/Tashkent",
    }
    # Firefox/WebKit'ga Chrome user-agent berish sayt mosligini buzadi.
    if browser_engine(name) == "chromium":
        options["user_agent"] = _CHROME_USER_AGENT
    return options


def _launch_error(name: str, exc: Exception) -> RuntimeError:
    if "missing dependencies" in str(exc).lower():
        hint = (
            "Tizim kutubxonalarini o'rnating: "
            f"python -m playwright install-deps {name}"
        )
    elif name == "chrome":
        hint = "Google Chrome'ni o'rnating yoki Chromium variantini tanlang."
    elif name == "msedge":
        hint = "Microsoft Edge'ni o'rnating yoki Chromium variantini tanlang."
    else:
        hint = f"Brauzerni o'rnating: python -m playwright install {name}"
    return RuntimeError(
        f"{BROWSER_LABELS[name]} ishga tushmadi. {hint} Texnik sabab: {exc}"
    )


async def _launch_default_chromium(
    playwright, headless: bool | None = None
) -> object:
    """Chromium uchun eski Chrome/Edge va bundled fallback'ni saqlaydi."""
    options = launch_options("chromium", headless=headless)
    try:
        return await playwright.chromium.launch(**options)
    except Exception as original:
        # Windows'da biriktirilgan Chrome ishlamasa, o'rnatilgan Edge yoki
        # Chrome orqali urinib ko'ramiz.
        if options.get("executable_path"):
            for channel in ("msedge", "chrome"):
                alt = {
                    k: v
                    for k, v in options.items()
                    if k not in ("channel", "executable_path")
                }
                alt["channel"] = channel
                try:
                    return await playwright.chromium.launch(**alt)
                except Exception:
                    continue

        # Kanal/executable bilan urinilgan bo'lsa, Playwright Chromium'iga
        # qaytamiz. Standart urinishning o'zi xato bergan bo'lsa takrorlamaymiz.
        if options.get("channel") or options.get("executable_path"):
            fallback = {
                k: v
                for k, v in options.items()
                if k not in ("channel", "executable_path")
            }
            try:
                return await playwright.chromium.launch(**fallback)
            except Exception as exc:
                raise _launch_error("chromium", exc) from original
        raise _launch_error("chromium", original) from original


async def launch_browser(
    playwright, value: str | None = None, headless: bool | None = None
) -> object:
    """Tanlangan Chromium/Chrome/Edge/Firefox/WebKit brauzerini ochadi."""
    name = normalize_browser(value)
    if name == "chromium":
        return await _launch_default_chromium(playwright, headless=headless)

    try:
        browser_type = getattr(playwright, browser_engine(name))
        return await browser_type.launch(
            **launch_options(name, headless=headless)
        )
    except Exception as exc:
        raise _launch_error(name, exc) from exc


async def launch_chromium(playwright) -> object:
    """Eski importlar uchun moslik wrapper'i."""
    return await launch_browser(playwright, "chromium")
