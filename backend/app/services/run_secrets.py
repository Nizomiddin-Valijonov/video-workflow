"""Run uchun maxfiy inputlar (LOGIN/PASSWORD) — faqat jarayon xotirasida.

OneID login/parolini diskka (SQLite `runs.inputs`) yozmaymiz: run
yaratilganda ular shu jarayon xotirasiga olinadi, ishchi ularni run
boshlanishida oladi va run tugagach o'chiriladi.
"""
from __future__ import annotations

import threading

SECRET_KEYS = ("LOGIN", "PASSWORD")

_lock = threading.Lock()
_store: dict[str, dict[str, str]] = {}


def split(inputs: dict | None) -> tuple[dict, dict]:
    """Inputlarni (ommaviy, maxfiy) juftlikka ajratadi."""
    data = dict(inputs or {})
    secrets = {key: data.pop(key) for key in SECRET_KEYS if key in data}
    return data, secrets


def remember(run_id: str, secrets: dict[str, str]) -> None:
    if not run_id or not secrets:
        return
    with _lock:
        _store[run_id] = dict(secrets)


def get(run_id: str) -> dict[str, str]:
    with _lock:
        return dict(_store.get(run_id) or {})


def forget(run_id: str) -> None:
    with _lock:
        _store.pop(run_id, None)
