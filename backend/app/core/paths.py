"""Saqlash yo'llarini Docker, desktop va ko'chirilgan nusxalar orasida moslashtirish."""
from __future__ import annotations

import os

from app.core.config import settings

_LEGACY_PREFIX = "/data/storage"
_STORAGE_SEGMENT = "/storage/"


def _join_storage(relative: str) -> str:
    return os.path.join(settings.STORAGE_DIR, *relative.split("/"))


def resolve_storage_path(path: str | None) -> str | None:
    """Eski yoki boshqa kompyuterdagi storage yo'lini joriy STORAGE_DIR ga ko'chiradi.

    Baza yoki qadamlar fayli bir kompyuterdan boshqasiga ko'chirilganda
    skrinshot/video yo'llari eski mutlaq yo'lda qolib ketmasligi uchun
    `/data/storage/...`, `C:\\...\\storage\\...` va shu kabi yo'llarning
    `storage` qismidan keyingi nisbiy yo'li joriy papkaga ulanadi.
    """
    if not path:
        return path
    raw = str(path)
    normalized = raw.replace("\\", "/")
    lowered = normalized.lower()

    current = os.path.abspath(settings.STORAGE_DIR).replace("\\", "/").rstrip("/")
    if current and lowered == current.lower():
        return settings.STORAGE_DIR
    if current and lowered.startswith(current.lower() + "/"):
        relative = normalized[len(current) + 1:]
        return _join_storage(relative)

    if lowered == _LEGACY_PREFIX:
        return settings.STORAGE_DIR
    if lowered.startswith(_LEGACY_PREFIX + "/"):
        return _join_storage(normalized[len(_LEGACY_PREFIX) + 1:])

    index = lowered.rfind(_STORAGE_SEGMENT)
    if index != -1:
        relative = normalized[index + len(_STORAGE_SEGMENT):]
        if relative:
            return _join_storage(relative)

    return raw
