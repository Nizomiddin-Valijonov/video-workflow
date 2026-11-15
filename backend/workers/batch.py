"""Batch rejimi: har bir xonadonga Excel'dan 5 tadan YANGI a'zo qo'shish.

Bu modul `workers.runner`dagi Playwright yordamchilaridan foydalanadi.
Bitta run ichida:
  1) OneID orqali kirish;
  2) «Хонадонлар хатлови» -> hisobga biriktirilgan ko'cha jadvalini
     ochish (video'dagi qotib qolgan ko'cha ishlatilmaydi);
  3) jadvaldagi xonadonlarni ketma-ket ochish; ko'cha tugagach keyingi
     ko'cha avtomatik ochiladi;
  4) har biriga `PEOPLE_PER_HOUSEHOLD` (standart 5) ta yangi odamni
     Excel'dan olib, modal formani to'ldirib SAQLASH.

Holat `storage/batch_state_<job_id>.json` faylida saqlanadi — run
to'xtab qolsa, qayta ishga tushirilganda allaqachon qo'shilgan odamlar
qayta qo'shilmaydi.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import threading
import time
import traceback
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from playwright.async_api import Page, async_playwright

from app.core.config import settings
from app.models import JobStatus, Run, VideoJob, WorkflowStep
from app.services.browser import context_options, launch_browser
from app.services.sleep_inhibitor import prevent_sleep
from workers.batch_report import write_report as write_batch_report
from workers.runner import (
    RateLimitError,
    _choose_oneid_individual,
    _click_smart,
    _execute_step,
    _log,
    _screenshot,
    _select2,
    _setvalue,
    _status,
)

DEFAULT_PHONE = "+998(99)000-00-00"
EXCEL_DB = os.path.join(settings.STORAGE_DIR, "uploads", "ХАТЛОВ.sqlite")
STATE_DIR = settings.STORAGE_DIR
HOME_URL = "https://online-mahalla.uz/"
STREET_LIST_URL = "https://online-mahalla.uz/tables/survey_homes_street"
# Har bir xonadon uchun asosiy `need` tadan tashqari qo'shimcha urinishlar.
# Excel'da ko'p odam allaqachon boshqa xonadonda bo'lgani uchun 60 ta.
MAX_EXTRA_ATTEMPTS = 60


class BatchLoginError(RuntimeError):
    """Avtomatik qayta login ham amalga oshmadi — qo'lda login kerak."""


# LOGIN/PASSWORD faqat jarayon xotirasida saqlanadi (diskka yozilmaydi).
# Sayt har bir odamdan keyin sessiyani yopsa, supervisor boshlagan keyingi
# runlar ham shu kesh orqali avtomatik qayta login qiladi.
_CREDENTIALS: dict[str, dict[str, str]] = {}
_CREDENTIALS_LOCK = threading.Lock()


def _remember_credentials(job_id: str, login: str, password: str) -> None:
    if login and password:
        with _CREDENTIALS_LOCK:
            _CREDENTIALS[job_id] = {
                "LOGIN": login,
                "PASSWORD": password,
            }


def _cached_credentials(job_id: str) -> dict[str, str]:
    with _CREDENTIALS_LOCK:
        return dict(_CREDENTIALS.get(job_id) or {})


def forget_credentials(job_id: str) -> None:
    """Parolni xotiradan o'chirish (masalan, foydalanuvchi so'rasa)."""
    with _CREDENTIALS_LOCK:
        _CREDENTIALS.pop(job_id, None)

# Saytning o'z PINFL limiti: 90 soniyada 3 ta qidiruv. Bu limit
# frontend JavaScript'ida (sessionStorage) tekshiriladi. Undan oshsak
# "So'rovlar ko'payib ketdi" toasti chiqadi va qidiruv bajarilmaydi.
# Shuning uchun qidiruvlarni 31 soniyada bittadan (3/93s) yuboramiz.
try:
    PINFL_MAX_REQUESTS = max(1, int(os.getenv("PINFL_MAX_REQUESTS", "3")))
except ValueError:
    PINFL_MAX_REQUESTS = 3
PINFL_WINDOW_SECONDS = float(os.getenv("PINFL_WINDOW_SECONDS", "90"))
PINFL_SPACING_SECONDS = max(
    1.0, PINFL_WINDOW_SECONDS / PINFL_MAX_REQUESTS + 1.0
)


class PinflLimiter:
    """Saytning PINFL qidiruv limitini hurmat qiladigan navbat.

    Sayt 3 ta qidiruvni 90 soniyada bajaradi. Biz ularni teng
    taqsimlaymiz: har 31 soniyada bitta. Bu CPU/RAMni ko'proq ishlatish
    bilan tezlashmaydi — serverdagi limit shunday; parallel tablar
    ochish limitni chetlab o'tib, akkauntni bloklash xavfini tug'diradi.
    """

    def __init__(self, state: dict):
        self.state = state
        self.max_requests = PINFL_MAX_REQUESTS
        self.window = PINFL_WINDOW_SECONDS
        self.spacing = PINFL_SPACING_SECONDS
        raw_times = state.get("pinfl_times") or []
        self.times: list[float] = []
        for value in raw_times:
            try:
                self.times.append(float(value))
            except Exception:
                continue
        now = time.time()
        self.times = [t for t in self.times if 0 <= now - t < self.window]
        self.last = max(self.times) if self.times else 0.0
        self.lookups = _as_int(state.get("pinfl_lookups"), 0)
        self.hits = _as_int(state.get("rate_limit_hits"), 0)

    def _prune(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.times = [t for t in self.times if 0 <= now - t < self.window]
        if self.times:
            self.last = max(self.last, max(self.times))

    async def _sync_from_page(self, page: Page | None) -> None:
        """Brauzerdagi sessionStorage vaqtlarini ham hisobga olamiz."""
        if page is None:
            return
        try:
            raw = await page.evaluate(
                "() => sessionStorage.getItem('survey_homes_pinfl_request_times')"
            )
            if not raw:
                return
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                return
            now = time.time()
            for ms in parsed:
                try:
                    t = float(ms) / 1000.0
                except Exception:
                    continue
                if 0 <= now - t < self.window:
                    self.times.append(t)
            # Takrorlarni olib tashlaymiz.
            self.times = sorted({round(t, 3) for t in self.times})
            if self.times:
                self.last = max(self.last, max(self.times))
        except Exception:
            return

    def _save(self) -> None:
        self.state["pinfl_times"] = [round(t, 3) for t in self.times[-20:]]
        self.state["pinfl_lookups"] = self.lookups
        self.state["rate_limit_hits"] = self.hits

    async def before_lookup(self, page: Page, job_id: str) -> None:
        await self._sync_from_page(page)
        self._prune()
        now = time.time()
        wait = 0.0
        if self.last:
            wait = max(wait, self.last + self.spacing - now)
        if len(self.times) >= self.max_requests:
            wait = max(
                wait,
                self.times[-self.max_requests] + self.window + 1.5 - now,
            )
        if wait > 0.5:
            await _log(
                job_id,
                f"⏳ PINFL limiti: {wait:.0f}s kutaman "
                f"({self.max_requests}/{int(self.window)}s)...",
            )
            await asyncio.sleep(wait)
        now = time.time()
        self._prune(now)
        self.times.append(now)
        self.last = now
        self.lookups += 1
        self._save()
        # Run to'satdan o'chsa ham limit vaqtlari yo'qolmasin.
        _save_state(job_id, self.state)

    def on_rate_limit(self, wait_seconds: int) -> None:
        self.hits += 1
        wait_seconds = max(1, int(wait_seconds))
        # Keyingi qidiruv sayt aytgan vaqtdan keyin bo'lsin; spacing
        # hisobida ham hisobga olinadi.
        self.last = max(
            self.last,
            time.time() + wait_seconds + 2.0 - self.spacing,
        )
        self._save()

    def on_success(self) -> None:
        # Muvaffaqiyatli javobdan keyin intervalni sun'iy ravishda
        # qisqartirmaymiz: 3/90s — qat'iy limit.
        self._save()


def _as_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _dataset_db(dataset: str) -> str:
    """Run inputidagi dataset nomini xavfsiz sqlite yo'liga aylantiradi."""
    name = (dataset or "").strip()
    if not name:
        return EXCEL_DB
    if "/" in name or "\\" in name or ".." in name:
        raise RuntimeError("Noto'g'ri Excel dataset nomi")
    path = os.path.join(settings.STORAGE_DIR, "uploads", name + ".sqlite")
    if not os.path.isfile(path):
        raise RuntimeError(f"Excel dataset topilmadi: {name}")
    return path


def _state_path(job_id: str) -> str:
    return os.path.join(STATE_DIR, f"batch_state_{job_id}.json")


def _session_path(job_id: str) -> str:
    """OneID sessiyasi (cookie/localStorage) saqlanadigan fayl.

    Bu faylda parol yo'q — faqat brauzer sessiyasi. Ruxsatlar 600.
    """
    return os.path.join(STATE_DIR, f"batch_session_{job_id}.json")


def _phone_for(person: dict) -> str:
    """Har bir odam uchun alohida telefon raqami.

    Sayt bitta raqamni 5 va undan ortiq xonadon a'zosiga biriktirishga
    ruxsat bermaydi (400 xatosi). Shuning uchun Excel qatoridan barqaror,
    lekin har xil raqam yasaymiz: 99-240-XX-YY. Ketma-ket 160 ta odam
    uchun raqamlar takrorlanmaydi.
    """
    row = max(2, _as_int(person.get("row_num"), 2))
    a = 10 + (row // 100) % 90
    b = row % 100
    return f"+998(99)240-{a:02d}-{b:02d}"


def _load_state(job_id: str) -> dict:
    try:
        with open(_state_path(job_id), "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("households", {})
                data.setdefault("used_pinpps", [])
                data.setdefault("registered_pinpps", [])
                data.setdefault("invalid_pinpps", [])
                data.setdefault("failed_pinpps", [])
                data.setdefault("results", [])
                return data
    except Exception:
        pass
    return {
        "households": {},
        "used_pinpps": [],
        "registered_pinpps": [],
        "invalid_pinpps": [],
        "failed_pinpps": [],
        "results": [],
    }


def _save_state(job_id: str, state: dict) -> None:
    path = _state_path(job_id)
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[BATCH] state yozilmadi: {e}", flush=True)


def _cadastre(text: str) -> str:
    m = re.search(r"\d{2}-\d{2}-\d{2}-\d{2}-\d{2}:\d{9}:\d{3}", text or "")
    return m.group(0) if m else ""


class ExcelPeople:
    """Excel SQLite bazasidan ketma-ket odamlarni beradi."""

    def __init__(self, start_row: int, db_path: str = EXCEL_DB):
        self.start_row = max(2, start_row)
        self.db_path = db_path
        self.con = sqlite3.connect(db_path)
        self.cur = self.con.execute(
            "SELECT row_num, pinpp, full_name, birth_date, passport "
            "FROM people WHERE row_num >= ? ORDER BY row_num",
            (self.start_row,),
        )
        self.wrapped = False
        # Diagnostika: nechta qator bor va nechtasi batch uchun yaroqli.
        try:
            self.total = int(
                self.con.execute(
                    "SELECT count(*) FROM people WHERE row_num >= ?",
                    (self.start_row,),
                ).fetchone()[0]
            )
            self.valid = int(
                self.con.execute(
                    "SELECT count(*) FROM people WHERE row_num >= ? "
                    "AND length(trim(coalesce(pinpp, ''))) = 14 "
                    "AND trim(coalesce(pinpp, '')) NOT GLOB '*[^0-9]*' "
                    "AND trim(coalesce(birth_date, '')) GLOB "
                    "'[0-9][0-9].[0-9][0-9].[0-9][0-9][0-9][0-9]' "
                    "AND upper(coalesce(full_name, '')) NOT LIKE '%XXX%'",
                    (self.start_row,),
                ).fetchone()[0]
            )
        except Exception:
            self.total = 0
            self.valid = 0

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass

    @staticmethod
    def _make(row) -> dict | None:
        row_num, pinpp, full_name, birth_date, passport = row
        pinpp = (pinpp or "").strip()
        full_name = (full_name or "").strip()
        birth_date = (birth_date or "").strip()
        passport = (passport or "").strip()
        if not re.fullmatch(r"\d{14}", pinpp):
            return None
        # «XXX» kabi to'liq bo'lmagan ismlar saytda qidirilmaydi.
        if "XXX" in full_name.upper():
            return None
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", birth_date):
            return None
        return {
            "row_num": int(row_num),
            "pinpp": pinpp,
            "full_name": full_name,
            "birth_date": birth_date,
            "passport": passport,
        }

    def next_person(self, skip: set[str]) -> dict | None:
        """Keyingi mos odamni qaytaradi. Ro'yxat tugasa, boshidan aylanadi."""
        while True:
            row = self.cur.fetchone()
            if row is None:
                if self.wrapped:
                    return None
                self.wrapped = True
                self.cur = self.con.execute(
                    "SELECT row_num, pinpp, full_name, birth_date, passport "
                    "FROM people WHERE row_num >= 2 ORDER BY row_num"
                )
                continue
            person = self._make(row)
            if person is None or person["pinpp"] in skip:
                continue
            return person

    def priority_persons(
        self,
        member_pinpps: set[str],
        skip: set[str],
        limit: int = 4,
    ) -> list[dict]:
        """Xonadon a'zolarining aniq manzili bo'yicha nomzodlar.

        Faqat aniq manzil mosligini ishlatamiz: familiya bo'yicha
        qidiruv juda keng (butun viloyat) va PINFL limitini behuda
        sarflaydi. Har bir xonadonda ko'pi bilan bir nechta shunday
        nomzod sinaladi, keyin oddiy Excel oqimiga qaytamiz.
        """
        members = [
            p for p in member_pinpps if re.fullmatch(r"\d{14}", p or "")
        ]
        if not members:
            return []
        try:
            placeholders = ",".join("?" for _ in members)
            member_rows = self.con.execute(
                "SELECT address FROM people "
                f"WHERE pinpp IN ({placeholders})",
                members,
            ).fetchall()
        except Exception:
            return []

        addresses = sorted(
            {str(a).strip() for (a,) in member_rows if a and str(a).strip()}
        )
        if not addresses:
            return []

        conds = ["address IN (" + ",".join("?" for _ in addresses) + ")"]
        params: list[str] = list(addresses)
        sql = (
            "SELECT row_num, pinpp, full_name, birth_date, passport "
            "FROM people WHERE (" + " OR ".join(conds) + ") "
            "ORDER BY row_num LIMIT ?"
        )
        try:
            rows = self.con.execute(
                sql, [*params, max(1, int(limit))]
            ).fetchall()
        except Exception:
            return []

        out: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            person = self._make(row)
            if person is None:
                continue
            pinpp = person["pinpp"]
            if pinpp in skip or pinpp in seen:
                continue
            seen.add(pinpp)
            out.append(person)
        return out


async def _exec_follow(page: Page, ctx, step: WorkflowStep, inputs: dict, job_id: str) -> Page:
    """Qadamni bajaradi; yangi tab ochilsa, o'sha tabni qaytaradi."""
    before = set(ctx.pages)
    await _execute_step(page, step, inputs, job_id)
    await page.wait_for_timeout(200)
    new_pages = [p for p in ctx.pages if p not in before]
    if new_pages:
        page = new_pages[-1]
        page.set_default_timeout(settings.STEP_TIMEOUT_SEC * 1000)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        await _log(job_id, f"🪟 Yangi tab: {page.url[:90]}")
    return page


def _url_param(url: str, name: str) -> str:
    """URL query parametrini xavfsiz o'qiydi."""
    try:
        values = parse_qs(urlsplit(url).query).get(name) or []
        return str(values[0]).strip() if values else ""
    except Exception:
        return ""


def _street_id_from_url(url: str) -> str:
    return _url_param(url, "street_id")


def _street_list_url(url: str) -> str:
    """Xonadonlar jadvali URL'idan ko'chalar jamlanmasi URL'ini yasaydi.

    Saytda ikkalasi bitta `survey_homes_street` jadvali: `street_id`
    bo'lsa xonadonlar, bo'lmasa ko'chalar ro'yxati ko'rsatiladi.
    """
    try:
        parts = urlsplit(url)
        query = parse_qs(parts.query, keep_blank_values=True)
        query.pop("street_id", None)
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(query, doseq=True),
                parts.fragment,
            )
        )
    except Exception:
        parts = urlsplit(STREET_LIST_URL)
        query = parse_qs(parts.query, keep_blank_values=True)
        query.pop("street_id", None)
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(query, doseq=True),
                parts.fragment,
            )
        )


def _street_list_url_for(
    page_url: str, account_meta: dict | None = None
) -> str:
    """Sahifa URL'idan ko'chalar ro'yxati URL'ini yasaydi.

    Sahifadagi `_mid`, `obl_id`, `area_id`, `district_id` parametrlari
    saqlanadi. Ular bo'lmasa, hisob ma'lumotlaridan to'ldiriladi.
    """
    list_url = _street_list_url(page_url)
    base = urlsplit(list_url)
    if "survey_homes_street" not in base.path:
        base = urlsplit(STREET_LIST_URL)
        query = parse_qs(base.query, keep_blank_values=True)
    else:
        query = parse_qs(base.query, keep_blank_values=True)
    page_query = parse_qs(
        urlsplit(page_url).query, keep_blank_values=True
    )
    for key in ("obl_id", "area_id", "district_id", "_mid"):
        if page_query.get(key):
            query[key] = page_query[key]
        elif (
            account_meta
            and key != "_mid"
            and account_meta.get(key)
        ):
            query[key] = [str(account_meta[key])]
    return urlunsplit(
        (
            base.scheme,
            base.netloc,
            base.path,
            urlencode(query, doseq=True),
            "",
        )
    )


def _street_meta_from_mapping(
    value: dict, require_location: bool = True
) -> dict[str, str]:
    """Xarita/obyektdan hisobga biriktirilgan ko'cha ma'lumotini oladi."""
    if not isinstance(value, dict):
        return {}

    def pick(*keys: str) -> str:
        for key in keys:
            item = value.get(key)
            if item not in (None, ""):
                return str(item).strip()
        return ""

    street_id = pick(
        "street_id", "streetId", "streetID", "defStreetId", "def_street_id"
    )
    street = pick(
        "street",
        "street_name",
        "streetName",
        "street_title",
        "defStreetName",
        "def_street_name",
    )
    area_id = pick(
        "area_id", "areaId", "areaID", "defAreaId", "def_area_id"
    )
    district_id = pick(
        "district_id", "districtId", "districtID", "defDistrictId",
        "def_district_id",
    )
    if not street_id and not street:
        return {}
    if require_location and not (area_id or district_id):
        return {}
    return {
        "street_id": street_id,
        "street": street,
        "area_id": area_id,
        "district_id": district_id,
        "obl_id": pick(
            "obl_id", "oblId", "oblID", "defOblId", "def_obl_id"
        ),
        "area_name": pick("area_name", "areaName"),
        "district": pick("district", "district_name", "districtName"),
    }


def _find_account_street(
    value: Any, depth: int = 0, allow_direct: bool = True
) -> dict[str, str]:
    """API javobi yoki storage ichidan `params.street_id`ni qidiradi.

    Ro'yxat elementlaridagi `street_id` hisob ko'chasi emas, jadval
    qatori bo'lishi mumkin. Shuning uchun ro'yxat ichida faqat aniq
    `params` bloklari qabul qilinadi.
    """
    if depth > 7:
        return {}
    if isinstance(value, dict):
        params = value.get("params")
        if isinstance(params, dict):
            found = _street_meta_from_mapping(params, require_location=False)
            if found:
                return found
        if allow_direct:
            found = _street_meta_from_mapping(value)
            if found:
                return found
        for key in (
            "data",
            "result",
            "project",
            "user",
            "profile",
            "meta",
            "state",
            "props",
        ):
            child = value.get(key)
            if isinstance(child, (dict, list)):
                found = _find_account_street(
                    child,
                    depth + 1,
                    allow_direct and not isinstance(child, list),
                )
                if found:
                    return found
        for child in value.values():
            if isinstance(child, (dict, list)):
                found = _find_account_street(
                    child,
                    depth + 1,
                    allow_direct and not isinstance(child, list),
                )
                if found:
                    return found
    elif isinstance(value, list):
        for child in value[:50]:
            found = _find_account_street(child, depth + 1, False)
            if found:
                return found
    return {}


def _norm_street_name(value: str) -> str:
    low = str(value or "").lower().replace("ё", "е")
    low = re.sub(
        r"\b(ул|улица|кўча|куча|ko'cha|kocha|street)\b\.?",
        " ",
        low,
    )
    low = re.sub(r"[^0-9a-zа-яўқғҳ]+", " ", low)
    return re.sub(r"\s+", " ", low).strip()


def _street_matches_meta(street: dict, account_meta: dict) -> bool:
    """Tanlangan ko'cha hisob ma'lumotiga mos kelishini tekshiradi."""
    meta_id = str(account_meta.get("street_id") or "").strip()
    street_id = str(street.get("id") or "").strip()
    if meta_id and street_id:
        return meta_id == street_id
    meta_name = _norm_street_name(str(account_meta.get("street") or ""))
    street_name = _norm_street_name(str(street.get("name") or ""))
    if not meta_name or not street_name:
        return False
    return (
        meta_name == street_name
        or _street_name_contains(street_name, meta_name)
        or _street_name_contains(meta_name, street_name)
    )


def _street_name_contains(haystack: str, needle: str) -> bool:
    """So'z chegarasini hisobga olib nom ichidan qidiradi."""
    if not haystack or not needle:
        return False
    return bool(
        re.search(
            rf"(?<!\w){re.escape(needle)}(?!\w)", haystack
        )
    )


def _norm_street_query(value: str) -> str:
    """Qo'lda kiritilgan ko'cha nomi uchun yumshoqroq normalizatsiya.

    O'zbek kirillchasidagi ҳ/қ/ғ/ў harflari lotin yoki oddiy kirill
    yozilishiga moslashtiriladi: «Навбаҳор» va «Навбахор» bir xil
    hisoblanadi.
    """
    low = _norm_street_name(value)
    return (
        low.replace("ҳ", "х")
        .replace("қ", "к")
        .replace("ғ", "г")
        .replace("ў", "у")
    )


def _find_streets(streets: list[dict], query: str) -> list[dict]:
    """Ko'chani ID yoki nom bo'yicha topadi.

    Avval aniq moslik, keyin qisman moslik qaytariladi. Bir nechta
    ko'cha mos kelsa, chaqiruvchi birinchisini tanlaydi va ogohlantiradi.
    """
    needle_raw = str(query or "").strip()
    if not needle_raw:
        return []
    if needle_raw.isdigit():
        by_id = [
            street
            for street in streets
            if str(street.get("id") or "") == needle_raw
        ]
        if by_id:
            return by_id
    needle = _norm_street_query(needle_raw)
    if not needle:
        return []
    exact = [
        street
        for street in streets
        if _norm_street_query(str(street.get("name") or "")) == needle
    ]
    if exact:
        return exact
    return [
        street
        for street in streets
        if (
            (candidate := _norm_street_query(str(street.get("name") or "")))
            and (needle in candidate or candidate in needle)
        )
    ]


async def _account_street_from_storage(page: Page) -> dict[str, str]:
    """Brauzer storage'idagi hisob ma'lumotlaridan ko'chani topadi."""
    try:
        entries = await page.evaluate(
            """() => {
                const out = [];
                for (const store of [window.localStorage, window.sessionStorage]) {
                    try {
                        for (let i = 0; i < store.length; i++) {
                            const key = store.key(i) || "";
                            const value = store.getItem(key) || "";
                            if (value.length > 1000000) continue;
                            if (
                                /street|district|project|user|profile|auth|pinfl/i.test(key) ||
                                /street_id|streetId/i.test(value)
                            ) {
                                out.push([key, value]);
                            }
                        }
                    } catch (e) {}
                }
                return out.slice(0, 200);
            }"""
        )
    except Exception:
        return {}
    for entry in entries or []:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        raw = entry[1]
        if not isinstance(raw, str):
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        found = _find_account_street(data)
        if found:
            return found
    return {}


def _known_street_name(
    url: str, state: dict, account_meta: dict
) -> str:
    """Ochiq URL uchun ishonchli ko'cha nomini qaytaradi."""
    state_url = str(state.get("current_street_url") or "").strip()
    state_name = str(state.get("current_street_name") or "").strip()
    if state_name and state_url == str(url).strip():
        return state_name
    if str(account_meta.get("street_id") or "") == _street_id_from_url(url):
        return str(account_meta.get("street") or "").strip()
    return ""


async def _ensure_street_list(
    page: Page,
    job_id: str,
    account_meta: dict,
    preferred_url: str = "",
) -> list[dict]:
    """Ko'chalar ro'yxatini ochib, barcha ko'chalarni qaytaradi.

    `preferred_url` — joriy hisob/mahallaga tegishli ro'yxat URL'i.
    Eski batch holatidagi boshqa tumanga oid URL'ga qaytib qolmaslik
    uchun ishlatiladi.
    """
    streets: list[dict] = []
    page_path = urlsplit(page.url).path
    if (
        "survey_homes" in page_path
        and not _street_id_from_url(page.url)
    ):
        try:
            streets = await _read_streets(page, job_id)
        except Exception as exc:
            await _log(
                job_id,
                f"⚠️ Ko'chalar ro'yxati o'qilmadi: {str(exc)[:160]}",
                level="error",
            )
    if not streets:
        list_url = preferred_url or _street_list_url_for(
            page.url, account_meta
        )
        try:
            await page.goto(
                list_url,
                wait_until="domcontentloaded",
                timeout=45000,
            )
            streets = await _read_streets(page, job_id)
        except Exception as exc:
            raise RuntimeError(
                "Ko'chalar ro'yxati topilmadi. Hisobga ko'cha "
                f"biriktirilganini tekshiring: {str(exc)[:200]}"
            ) from exc
    if not streets:
        raise RuntimeError(
            "Ko'chalar ro'yxati bo'sh. Hisobga ko'cha biriktirilganini "
            "tekshiring."
        )
    return streets


async def _open_initial_street(
    page: Page,
    job_id: str,
    state: dict,
    account_meta: dict,
    start_street: str = "",
) -> tuple[str, str]:
    """Qo'lda kiritilgan ko'chani yoki birinchi ko'chani ochadi.

    `start_street` berilmasa, avvalgi run qolgan ko'chadan davom etadi;
    davom etish ma'lumoti bo'lmasa ro'yxatdagi birinchi ko'cha ochiladi.
    """
    if not account_meta:
        await page.wait_for_timeout(600)
        account_meta.update(await _account_street_from_storage(page))
    # Joriy hisob/mahallaga tegishli ro'yxat URL'i. Eski state boshqa
    # tumanga oid bo'lsa, shu URL'ga qaytamiz.
    base_list_url = _street_list_url_for(page.url, account_meta)
    manual = str(start_street or "").strip()

    if manual:
        streets = await _ensure_street_list(
            page, job_id, account_meta, base_list_url
        )
        matches = _find_streets(streets, manual)
        if not matches:
            examples = ", ".join(
                str(street.get("name") or street.get("id") or "")
                for street in streets[:8]
            )
            # Noto'g'ri nom supervisor tomonidan qayta-qayta urinilmasin.
            state.pop("start_street", None)
            _save_state(job_id, state)
            raise RuntimeError(
                f"Boshlanish ko'chasi topilmadi: «{manual}». "
                f"Mavjud ko'chalar (birinchi 8 tasi): {examples}"
            )
        target = matches[0]
        if len(matches) > 1:
            names = ", ".join(
                str(street.get("name") or street.get("id") or "")
                for street in matches[:5]
            )
            await _log(
                job_id,
                f"⚠️ «{manual}» bo'yicha bir nechta ko'cha topildi; "
                f"birinchisi tanlandi: {names}",
                level="error",
            )
        target_name = str(target.get("name") or manual).strip()
        state["start_street"] = target_name
        await _log(
            job_id,
            f"🎯 Qo'lda tanlangan ko'cha: {target_name}",
        )
        await _goto_street(page, target, job_id)
        return page.url, target_name

    resume_url = str(state.get("current_street_url") or "").strip()
    resume_id = _street_id_from_url(resume_url)

    def _location(url: str, name: str) -> str:
        return _url_param(url, name) or str(
            account_meta.get(name) or ""
        ).strip()

    if resume_url and resume_id:
        resume_district = _location(resume_url, "district_id")
        current_district = _location(base_list_url, "district_id")
        resume_area = _location(resume_url, "area_id")
        current_area = _location(base_list_url, "area_id")
        resume_obl = _location(resume_url, "obl_id")
        current_obl = _location(base_list_url, "obl_id")
        if (
            (
                resume_district
                and current_district
                and resume_district != current_district
            )
            or (
                resume_area
                and current_area
                and resume_area != current_area
            )
            or (
                resume_obl
                and current_obl
                and resume_obl != current_obl
            )
        ):
            await _log(
                job_id,
                "⚠️ State boshqa hudud/mahallaga tegishli — joriy "
                "hududdagi ko'chalar ro'yxatidan boshlayman",
                level="error",
            )
            resume_url = ""
            state.pop("current_street_url", None)
            state.pop("current_street_name", None)
            state.pop("current_street_id", None)
            _save_state(job_id, state)
    if resume_url and resume_id:
        try:
            await page.goto(
                resume_url,
                wait_until="domcontentloaded",
                timeout=45000,
            )
            await page.wait_for_timeout(100)
            await _log(
                job_id,
                "↩️ Oldingi ko'chadan davom etaman: "
                f"{state.get('current_street_name') or _street_id_from_url(resume_url)}",
            )
            await _wait_street_table(page, job_id)
            return page.url, _known_street_name(
                page.url, state, account_meta
            )
        except Exception as exc:
            await _log(
                job_id,
                f"⚠️ Oldingi ko'cha ochilmadi — joriy hududdagi birinchi "
                f"ko'chadan boshlayman: {str(exc)[:160]}",
                level="error",
            )
            state.pop("current_street_url", None)
            state.pop("current_street_name", None)
            state.pop("current_street_id", None)
            _save_state(job_id, state)

    streets = await _ensure_street_list(
        page, job_id, account_meta, base_list_url
    )
    target = streets[0]
    target_name = str(target.get("name") or target.get("id") or "").strip()
    await _log(
        job_id,
        f"🎯 Boshlanish ko'chasi (birinchi): {target_name}",
    )
    await _goto_street(page, target, job_id)
    return page.url, target_name


NEXT_PAGE_SELECTORS = (
    # Platon jadvali BootstrapVue <b-pagination> ishlatadi:
    # <li class="page-item"><button class="page-link"
    #   aria-label="Go to next page">›</button></li>
    # Oxirgi sahifada tugma o'rniga <span class="page-link"> qoladi —
    # u avtomatik ravishda "disabled" hisoblanadi.
    "button.page-link[aria-label='Go to next page']",
    "a.page-link[aria-label='Go to next page']",
    "button.page-link[aria-label*='next page' i]",
    "a.page-link[aria-label*='next page' i]",
    "button.page-link[aria-label*='next' i]",
    "a.page-link[aria-label*='next' i]",
    "button.page-link[aria-label*='кейинги' i]",
    "a.page-link[aria-label*='кейинги' i]",
    "button.page-link[aria-label*='следующ' i]",
    "a.page-link[aria-label*='следующ' i]",
    "button.page-link[aria-label*='keyingi' i]",
    "a.page-link[aria-label*='keyingi' i]",
    # Element UI va boshqa jadvallar uchun zaxira variantlar.
    "button.btn-next",
    ".btn-next",
    "a[rel='next']",
    "button[aria-label='Next page']",
    "a[aria-label='Next page']",
    "button[aria-label='Keyingi sahifa']",
    "a[aria-label='Keyingi sahifa']",
    "button:has-text('Кейинги')",
    "a:has-text('Кейинги')",
    "button:has-text('Следующая')",
    "a:has-text('Следующая')",
    "button:has-text('Next')",
    "a:has-text('Next')",
    "button:has-text('›')",
    "a:has-text('›')",
)


async def _has_pagination(page: Page) -> bool:
    """Jadvalda pagination bormi (infinite-scroll bilan aralashtirmaslik uchun)."""
    loc = page.locator(
        ".table-pagination ul.pagination, ul.pagination.b-pagination, "
        ".b-pagination, .el-pagination"
    )
    try:
        count = await loc.count()
    except Exception:
        return False
    for i in range(min(count, 6)):
        try:
            if await loc.nth(i).is_visible():
                return True
        except Exception:
            continue
    return False


async def _active_table_page(page: Page) -> str:
    """Jadval pagination'idagi joriy sahifa raqamini qaytaradi."""
    try:
        value = await page.evaluate(
            """() => {
                const selectors = [
                    ".table-pagination .page-link[aria-current='page']",
                    "ul.pagination .page-link[aria-current='page']",
                    ".b-pagination .page-link[aria-current='page']",
                    ".el-pager li.active",
                ];
                for (const selector of selectors) {
                    for (const el of document.querySelectorAll(selector)) {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        if (
                            style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && rect.width > 0
                            && rect.height > 0
                        ) {
                            const value = el.getAttribute('aria-posinset')
                                || (el.innerText || '').trim();
                            if (value) return value;
                        }
                    }
                }
                return '';
            }"""
        )
        return str(value or "").strip()
    except Exception:
        return ""


async def _table_links(table) -> list[dict]:
    """Jadvaldagi barcha qator havolalari va matnlarini bir marta o'qiydi.

    Har bir qator uchun alohida Playwright so'rovi yuborish sekin; shu
    sababli butun jadval brauzer ichida bir marta skanerlanadi.
    """
    try:
        return await table.evaluate(
            """(table) => Array.from(table.querySelectorAll('tbody tr'))
                .map((row) => {
                    const anchors = Array.from(row.querySelectorAll('a'));
                    const usable = anchors.filter((a) => {
                        const href = a.getAttribute('href') || '';
                        return href
                            && !href.startsWith('#')
                            && !/^javascript:/i.test(href);
                    });
                    const isModal = (a) => /[_?&]target=modal/i.test(
                        a.getAttribute('href') || ''
                    );
                    const pick = usable.find(
                        (a) => a.classList.contains('link') && !isModal(a)
                    ) || usable.find((a) => a.classList.contains('link'))
                        || usable.find(
                            (a) => (a.getAttribute('href') || '')
                                .includes('/survey_homes/')
                        ) || usable[0];
                    return {
                        href: pick ? (pick.getAttribute('href') || '') : '',
                        text: row.innerText || '',
                    };
                })"""
        )
    except Exception:
        return []


async def _table_row_links(table, selector: str) -> list[list[dict]]:
    """Har bir qatordagi barcha havolalarni bitta brauzer so'rovida oladi."""
    try:
        return await table.evaluate(
            """(table, selector) =>
                Array.from(table.querySelectorAll('tbody tr')).map((row) =>
                    Array.from(row.querySelectorAll(selector)).map((link) => ({
                        href: link.getAttribute('href') || '',
                        text: link.innerText || '',
                    }))
                )""",
            selector,
        )
    except Exception:
        return []


async def _table_signature(table) -> tuple[int, str, str]:
    try:
        result = await table.evaluate(
            """(table) => {
                const rows = table.querySelectorAll('tbody tr');
                if (!rows.length) return [0, '', ''];
                const first = (rows[0].innerText || '').trim().slice(0, 300);
                const last = (rows[rows.length - 1].innerText || '')
                    .trim().slice(0, 300);
                return [rows.length, first, last];
            }"""
        )
        if isinstance(result, (list, tuple)) and len(result) == 3:
            return int(result[0] or 0), str(result[1] or ""), str(result[2] or "")
        return 0, "", ""
    except Exception:
        return 0, "", ""


async def _element_disabled(el) -> bool:
    try:
        tag = (await el.evaluate("(e) => e.tagName.toLowerCase()")) or ""
        # BootstrapVue oxirgi sahifada next/prev'ni <span class="page-link">
        # qilib qo'yadi — u bosilmaydi.
        if tag == "span":
            return True
        if (await el.get_attribute("disabled")) is not None:
            return True
        if (await el.get_attribute("aria-disabled") or "").lower() == "true":
            return True
        return bool(
            await el.evaluate(
                "(e) => !!e.closest('.disabled, .is-disabled, "
                "li.page-item.disabled, [disabled], "
                "[aria-disabled=\"true\"]')"
            )
        )
    except Exception:
        return True


async def _find_next_page_control(page: Page):
    containers = page.locator(
        ".table-pagination, ul.pagination.b-pagination, .b-pagination, "
        ".el-pagination, ul.pagination, nav[aria-label*='pag'], "
        "[class*='pagination']"
    )
    try:
        container_count = await containers.count()
    except Exception:
        container_count = 0
    for c in range(min(container_count, 8)):
        container = containers.nth(c)
        try:
            if not await container.is_visible():
                continue
        except Exception:
            continue
        for selector in NEXT_PAGE_SELECTORS:
            loc = container.locator(selector)
            try:
                count = await loc.count()
            except Exception:
                continue
            for i in range(min(count, 6)):
                el = loc.nth(i)
                try:
                    if await el.is_visible() and not await _element_disabled(el):
                        return el
                except Exception:
                    continue

    # Ba'zi jadvallarda pagination konteyneri klasssiz bo'ladi.
    for selector in (
        "button.page-link[aria-label='Go to next page']",
        "button.page-link[aria-label*='next' i]",
        "button.page-link[aria-label*='кейинги' i]",
        "a[rel='next']",
        "button[aria-label='Next page']",
        "a[aria-label='Next page']",
    ):
        loc = page.locator(selector)
        try:
            count = await loc.count()
        except Exception:
            continue
        for i in range(min(count, 6)):
            el = loc.nth(i)
            try:
                if await el.is_visible() and not await _element_disabled(el):
                    return el
            except Exception:
                continue
    return None


async def _click_numbered_table_page(
    page: Page, table, number: int, job_id: str
) -> bool:
    """Pagination'dagi raqamli sahifa tugmasini bosadi (zaxira usul).

    Ba'zi jadvallarda «keyingi sahifa» strelkasi ko'rinmaydi yoki
    bosilmaydi, lekin sahifa raqamlari ishlaydi. Shu holatda ro'yxat
    1-sahifada "qotib qolmasligi" uchun aniq raqamni bosamiz.
    """
    before = await _table_signature(table)
    before_page = await _active_table_page(page)
    for selector in (
        f".table-pagination .page-link:text-is('{number}')",
        f"ul.pagination .page-link:text-is('{number}')",
        f".b-pagination .page-link:text-is('{number}')",
        f".el-pager li:text-is('{number}')",
        f"[class*='pagination'] .page-link:text-is('{number}')",
    ):
        loc = page.locator(selector)
        try:
            count = await loc.count()
        except Exception:
            continue
        for i in range(min(count, 4)):
            el = loc.nth(i)
            try:
                if not await el.is_visible():
                    continue
                if await _element_disabled(el):
                    continue
                await el.scroll_into_view_if_needed(timeout=2000)
                await el.click(timeout=5000)
            except Exception:
                continue
            for _ in range(80):
                await page.wait_for_timeout(100)
                after = await _table_signature(table)
                after_page = await _active_table_page(page)
                if (after != before and after[0] > 0) or (
                    before_page and after_page and after_page != before_page
                ):
                    await _log(
                        job_id,
                        f"📄 Jadval {number}-sahifasiga o'tildi",
                    )
                    return True
            return False
    return False


async def _numbered_next_table_page(
    page: Page, table, active_page: str, job_id: str
) -> bool:
    """Joriy sahifa raqamidan keyingisini bosishga urinadi."""
    try:
        current = int(str(active_page or "").strip())
    except Exception:
        return False
    if current <= 0 or current >= 5000:
        return False
    await page.wait_for_timeout(150)
    return await _click_numbered_table_page(
        page, table, current + 1, job_id
    )


async def _next_table_page(page: Page, table, job_id: str) -> bool:
    """Jadvaldagi «keyingi sahifa» tugmasini bosadi va o'zgarishni kutadi.

    Bosish ba'zan overlay/preloader tufayli "yutiladi" (click xatosiz
    o'tadi, lekin sahifa almashmaydi). Shu holatda bu fon "oxirgi
    sahifa" deb talqin qilinib, qolgan xonadonlar o'tkazib yuborilardi
    — shuning uchun bitta qayta urinish va raqamli sahifa zaxirasi
    qo'llanadi.
    """
    for attempt in range(2):
        before = await _table_signature(table)
        before_page = await _active_table_page(page)
        control = await _find_next_page_control(page)
        if control is None:
            # Tugma vaqtincha ko'rinmasligi mumkin (jadval yangilanmoqda).
            if attempt == 0 and await _has_pagination(page):
                fallback = await _numbered_next_table_page(
                    page, table, before_page, job_id
                )
                if fallback:
                    return True
                await page.wait_for_timeout(600)
                continue
            return False
        try:
            await control.scroll_into_view_if_needed(timeout=2000)
            await control.click(timeout=5000)
        except Exception:
            try:
                await control.evaluate("(e) => e.click()")
            except Exception:
                if attempt == 0:
                    fallback = await _numbered_next_table_page(
                        page, table, before_page, job_id
                    )
                    if fallback:
                        return True
                    await page.wait_for_timeout(600)
                    continue
                return False
        # BootstrapVue jadval ma'lumotni qayta yuklaydi. Sahifa raqami
        # darhol o'zgaradi, lekin qatorlar kechroq keladi — shuning uchun
        # asosiy signal qatorlar imzosining o'zgarishi.
        page_changed = False
        for _ in range(80):
            await page.wait_for_timeout(100)
            after_page = await _active_table_page(page)
            if before_page and after_page and after_page != before_page:
                page_changed = True
            after = await _table_signature(table)
            if after != before and after[0] > 0:
                await _log(
                    job_id,
                    "📄 Jadval keyingi sahifasiga o'tildi"
                    + (f" (sahifa {after_page})" if after_page else ""),
                )
                return True
        if page_changed:
            # Ma'lumot bir xil ko'rinsa ham sahifa almashgan.
            await _log(job_id, "📄 Jadval keyingi sahifasiga o'tildi")
            return True
        if attempt == 0:
            await page.wait_for_timeout(600)
    return False


async def _load_more_table_rows(page: Page, table, job_id: str) -> bool:
    """Infinite-scroll jadvallari uchun pastga surib, yangi qatorlarni yuklaydi."""
    before = await _table_signature(table)
    for _ in range(3):
        try:
            rows = table.locator("tbody tr")
            count = await rows.count()
            if count:
                await rows.nth(count - 1).scroll_into_view_if_needed(timeout=3000)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        await page.wait_for_timeout(600)
        after = await _table_signature(table)
        if after[0] > before[0]:
            await _log(
                job_id,
                f"📜 Jadvalga {after[0] - before[0]} ta yangi qator qo'shildi",
            )
            return True
    return False


async def _iter_street_rows(page: Page, job_id: str):
    """Xonadonlar jadvali qatorlarini barcha sahifalar bo'ylab beradi.

    Har bir sahifa tugagach «keyingi sahifa» bosiladi; pagination bo'lmasa
    infinite-scroll uchun pastga suriladi. Takroriy qatorlarni chaqiruvchi
    tomon `seen` to'plami bilan filtrlaydi.
    """
    for page_no in range(1, 501):
        table = await _wait_street_table(page, job_id)
        rows = table.locator("tbody tr")
        total = await rows.count()
        if total == 0:
            return
        if page_no > 1:
            await _log(
                job_id,
                f"📄 Xonadonlar {page_no}-sahifasi: {total} qator",
            )
        for i in range(total):
            yield i, rows.nth(i)
        if await _next_table_page(page, table, job_id):
            await page.wait_for_timeout(150)
            continue
        if await _has_pagination(page):
            return
        if await _load_more_table_rows(page, table, job_id):
            continue
        return


async def _collect_households(
    page: Page, job_id: str, street_url: str
) -> list[dict]:
    """Ko'chadagi barcha xonadon havolalarini BARCHA sahifalardan yig'adi.

    100 tadan ortiq xonadon bo'lsa ham keyingi sahifa bosiladi. Yig'ilgan
    ro'yxat bo'yicha xonadonlar keyin to'g'ridan-to'g'ri URL orqali
    ochiladi — shunda har safar jadvalga qaytish va pagination'ni
    yo'qotish muammosi bo'lmaydi.
    """
    households: list[dict] = []
    seen: set[str] = set()
    page_no = 1
    table = await _wait_street_table(page, job_id)
    while page_no <= 1000:
        data = await _table_links(table)
        if not data:
            break
        before_count = len(households)
        for item in data:
            href = str(item.get("href") or "").strip()
            low_href = href.lower()
            if (
                not href
                or href.startswith("#")
                or low_href.startswith("javascript:")
                or "forms/survey_homes/" not in low_href
                or href in seen
            ):
                continue
            seen.add(href)
            text = str(item.get("text") or "")
            households.append(
                {
                    "href": href,
                    "url": urljoin(street_url, href),
                    "label": _cadastre(text) or f"#{len(households) + 1}",
                    "index": len(households) + 1,
                }
            )
        await _log(
            job_id,
            f"📄 Xonadonlar {page_no}-sahifasi: {len(data)} qator "
            f"(jami {len(households)})",
        )
        if page_no > 1 and len(households) == before_count:
            await _log(
                job_id,
                "⚠️ Keyingi sahifada yangi xonadon yo'q — skanerlash "
                "to'xtatildi",
                level="error",
            )
            break
        if await _next_table_page(page, table, job_id):
            page_no += 1
            await page.wait_for_timeout(120)
            continue
        if await _has_pagination(page):
            active = await _active_table_page(page)
            if await _numbered_next_table_page(
                page, table, active, job_id
            ):
                page_no += 1
                await page.wait_for_timeout(120)
                continue
            break
        if await _load_more_table_rows(page, table, job_id):
            continue
        break
    if households:
        await _log(job_id, f"🗂 {len(households)} ta xonadon yig'ildi")
    else:
        await _log(
            job_id,
            "🗂 Bu ko'chada xonadon topilmadi — keyingi ko'chaga o'taman",
        )
    return households


async def _wait_streets_table(page: Page, job_id: str):
    """Ko'chalar jamlanma jadvalini kutadi.

    Jamlanmada har bir ko'cha nomi `street_id` parametrli havola bo'ladi.
    Shu havolalar orqali jadval tayyorligini aniqlaymiz.
    """
    selectors = (
        # Avval sarlavhasi ko'cha jadvaliga o'xshaganini tanlaymiz.
        "table:has(thead:has-text('Кўча')):has(a[href*='street_id'])",
        "table:has(th:has-text('Кўча')):has(a[href*='street_id'])",
        # Zaxira: street_id havolali birinchi jadval.
        "table:has(a[href*='street_id'])",
    )
    for i in range(100):
        for selector in selectors:
            try:
                table = page.locator(selector).first
                if await table.count():
                    rows = table.locator("tbody tr")
                    count = await rows.count()
                    if count:
                        await _log(
                            job_id, f"📋 Ko'chalar jadvali: {count} qator"
                        )
                        return table
            except Exception:
                pass
        if i in (25, 50):
            await _log(job_id, "⏳ Ko'chalar jadvali kutilmoqda…")
        await page.wait_for_timeout(200)
    raise RuntimeError(
        "Ko'chalar jadvali topilmadi — login yoki sahifa xatosi"
    )


async def _read_streets(page: Page, job_id: str) -> list[dict]:
    """Ko'chalar jamlanmasini barcha sahifalari bilan o'qiydi.

    Ro'yxat bir necha sahifadan iborat bo'lishi mumkin (odatda 10–20
    ko'cha/sahifa). Har bir sahifa ochiladi va qo'shiladi; takroriy
    ko'chalar tashlab yuboriladi. Shu tufayli keyingi ko'chaga o'tish
    ro'yxatning birinchi sahifasi bilan cheklanib qolmaydi.
    """
    streets: list[dict] = []
    seen: set[str] = set()
    empty_pages = 0
    for page_no in range(1, 201):
        table = await _wait_streets_table(page, job_id)
        row_links = await _table_row_links(table, "a[href*='street_id']")
        before_count = len(streets)
        for links in row_links:
            best: dict | None = None
            for item in links:
                href = str(item.get("href") or "").strip()
                if not href:
                    continue
                url = urljoin(page.url, href)
                sid = _street_id_from_url(url)
                if not sid or sid == "0":
                    continue
                # Faqat ko'chalar jadvalining o'ziga olib boradigan
                # havolalarni olamiz (tahrirlash/o'chirish havolalarini
                # tasodifan ko'cha deb olmaymiz).
                if "survey_homes_street" not in urlsplit(url).path:
                    continue
                text = str(item.get("text") or "").strip()
                if best is None or len(text) > len(best["name"]):
                    best = {
                        "id": sid,
                        "name": text or f"ko'cha {sid}",
                        "url": url,
                    }
            if best is None or best["id"] in seen:
                continue
            seen.add(best["id"])
            streets.append(best)
        if len(row_links) > 0:
            await _log(
                job_id,
                f"📄 Ko'chalar {page_no}-sahifasi: {len(row_links)} qator "
                f"(jami {len(streets)})",
            )
        if page_no > 1 and len(streets) == before_count:
            empty_pages += 1
            if empty_pages >= 2:
                await _log(
                    job_id,
                    "⚠️ Ko'chalar ro'yxatining keyingi sahifalarida yangi "
                    "ko'cha yo'q — ro'yxat tugadi",
                    level="error",
                )
                break
        else:
            empty_pages = 0
        if await _next_table_page(page, table, job_id):
            await page.wait_for_timeout(150)
            continue
        if await _has_pagination(page):
            # Pagination bor, lekin keyingi sahifa ochilmadi: raqamli
            # fallback yana bir marta sinaladi.
            active = await _active_table_page(page)
            if await _numbered_next_table_page(
                page, table, active, job_id
            ):
                await page.wait_for_timeout(150)
                continue
            break
        if await _load_more_table_rows(page, table, job_id):
            continue
        break
    if not streets:
        raise RuntimeError("Ko'chalar ro'yxati bo'sh — sahifa tuzilishi o'zgargan")
    await _log(job_id, f"🛣 {len(streets)} ta ko'cha topildi")
    return streets


def _street_index(streets: list[dict], url: str) -> int:
    sid = _street_id_from_url(url)
    if sid:
        for i, street in enumerate(streets):
            if street.get("id") == sid:
                return i
    return -1


async def _goto_street(page: Page, street: dict, job_id: str):
    """Keyingi ko'cha jadvalini ochadi."""
    label = street.get("name") or street.get("id") or "?"
    await _log(job_id, f"➡️ Keyingi ko'cha: {label}")
    try:
        await page.goto(
            street["url"],
            wait_until="domcontentloaded",
            timeout=45000,
        )
    except Exception as e:
        raise RuntimeError(f"Ko'cha ochilmadi ({label}): {str(e)[:180]}")
    low = page.url.lower()
    if "login" in low or "oneid" in low:
        raise RuntimeError("Sessiya tugagan — qayta login kerak")
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception:
        pass
    await page.wait_for_timeout(100)
    opened_id = _street_id_from_url(page.url)
    expected_id = str(street.get("id") or "")
    if opened_id and expected_id and opened_id != expected_id:
        raise RuntimeError(
            f"Noto'g'ri ko'cha ochildi: kutilgan {expected_id}, "
            f"ochilgan {opened_id}"
        )
    return await _wait_street_table(page, job_id)


async def _wait_street_table(page: Page, job_id: str):
    loop = asyncio.get_event_loop()
    empty_since: float | None = None
    for i in range(100):
        try:
            # Xonadonlar jadvalida «Кадастр» ustuni yoki
            # `forms/survey_homes/<id>` havolasi bo'ladi; ko'chalar
            # jamlanmasida esa `street_id` havolalari bo'ladi. Shu bilan
            # noto'g'ri jadvalni tanlamaymiz.
            table = None
            for sel in (
                "table:has(thead:has-text('адастр'))",
                "table:has(th:has-text('адастр'))",
                "table:has(a[href*='forms/survey_homes/'])",
            ):
                tables = page.locator(sel)
                if await tables.count():
                    table = tables.first
                    break
            if table is not None:
                rows = await table.locator("tbody tr").count()
                if rows:
                    await _log(
                        job_id, f"📋 Xonadonlar jadvali: {rows} qator"
                    )
                    return table
                # Jadval sarlavhasi bor, lekin qatorlar hali kelmagan
                # yoki ko'cha bo'sh. Qisqa kutamiz va bo'sh qaytaramiz —
                # shunda run to'xtab qolmay keyingi ko'chaga o'tadi.
                if empty_since is None:
                    empty_since = loop.time()
                elif loop.time() - empty_since >= 8:
                    await _log(
                        job_id,
                        "📋 Xonadonlar jadvali bo'sh — keyingi ko'chaga "
                        "o'taman",
                    )
                    return table
        except Exception:
            pass
        if i in (25, 50):
            await _log(job_id, "⏳ Xonadonlar jadvali kutilmoqda…")
        await page.wait_for_timeout(200)
    raise RuntimeError("Xonadonlar jadvali topilmadi — login yoki sahifa xatosi")


async def _is_cancelled(db, run: Run) -> bool:
    """Foydalanuvchi «To'xtatish» bosganini tekshiradi."""
    try:
        await db.refresh(run)
    except Exception:
        return False
    status = str(run.status).lower()
    error = str(run.error or "").lower()
    return status.endswith("failed") and "bekor qilindi" in error


async def _read_members(
    hp: Page, timeout_ms: int = 8000
) -> tuple[set[str], int]:
    """Xonadon a'zolari jadvalidan JSHSHIRlarni va a'zolar sonini o'qiydi."""
    loop = asyncio.get_event_loop()
    started = loop.time()
    deadline = started + timeout_ms / 1000
    empty_seen_at = 0.0
    while True:
        try:
            tables = hp.locator("table").filter(has_text="Қариндошлиги")
            if not await tables.count():
                tables = hp.locator("table").filter(has_text="ЖШШИР")
            if await tables.count():
                table = tables.first
                rows = table.locator("tbody tr")
                if await rows.count():
                    texts = await rows.all_inner_texts()
                    pinpps: set[str] = set()
                    for text in texts:
                        # Jadvalda JSHSHIR 14 xonali, ba'zan bo'shliqlar bilan.
                        compact = (text or "").replace(" ", "")
                        for m in re.finditer(
                            r"(?<!\d)(\d{14})(?!\d)", compact
                        ):
                            pinpps.add(m.group(1))
                    return pinpps, len(pinpps)
                if not empty_seen_at:
                    empty_seen_at = loop.time()
        except Exception:
            pass
        # Jadval bor, lekin a'zolari yo'q — 2s kutib, 0 deb qaytamiz.
        if empty_seen_at and loop.time() - empty_seen_at >= 2:
            return set(), 0
        if loop.time() >= deadline:
            return set(), 0
        await hp.wait_for_timeout(250)


async def _close_modal(hp: Page) -> None:
    try:
        modal = hp.locator("div.form-modal").first
        if await modal.count() and await modal.is_visible():
            close = hp.locator("div.form-modal button.close-btn")
            if await close.count():
                try:
                    await close.first.click(timeout=7000)
                except Exception:
                    await hp.keyboard.press("Escape")
            else:
                await hp.keyboard.press("Escape")
            await hp.wait_for_timeout(300)
    except Exception:
        pass


async def _modal_text(hp: Page) -> str:
    """Modal ichidagi qizil xato matnini o'qiydi."""
    try:
        modal = hp.locator("div.form-modal").first
        if await modal.count():
            return (await modal.inner_text())[:4000]
    except Exception:
        pass
    return ""


def _classify_error(text: str) -> str:
    """Sayt xatosini turga ajratadi: full / already / phone / not_found."""
    low = (text or "").lower()
    if "20 тадан зиёд" in low or "20 tadan" in low:
        return "full"
    if (
        "қайта киритиб бўлмайди" in low
        or "қайта киритиш" in low
        or ("мавжуд" in low and "хонадон" in low)
    ):
        return "already"
    if "5 ёки ундан кўп" in low or "бириктирилган" in low:
        return "phone"
    if "топилмади" in low or "маълумот йўқ" in low or "doc_status" in low:
        return "not_found"
    return ""


async def _add_person(
    hp: Page,
    modal_steps: list[WorkflowStep],
    save_step: WorkflowStep,
    verify_step: WorkflowStep | None,
    person: dict,
    phone: str,
    base_inputs: dict,
    job_id: str,
) -> None:
    """Bitta odamni modal forma orqali qo'shadi va saqlaydi."""
    # «Қўшиш» tugmasi — jadval ostidagi ko'k tugma.
    await _click_smart(hp, "button:has-text('Қўшиш')", 45000, 15000)
    modal = hp.locator("div.form-modal").first
    await modal.wait_for(state="visible", timeout=25000)
    await hp.wait_for_timeout(300)

    person_inputs = {
        **(base_inputs or {}),
        "JSHSHIR": person["pinpp"],
        "BIRTH_DATE": person["birth_date"],
        "PHONE": phone,
    }

    for step in modal_steps:
        await _execute_step(
            hp, step, person_inputs, job_id, settle_ms=120
        )

    # Excel'dagi pasport seriyasi/raqamini, agar qidiruv ularni to'ldirmagan
    # bo'lsa, qo'shimcha ravishda yozamiz. Sayt ba'zan bu maydonlarni bo'sh
    # qoldiradi va «Сақлаш» tugmasi faol bo'lmaydi.
    try:
        passport = (person.get("passport") or "").replace(" ", "").upper()
        m = re.fullmatch(r"([A-Z]{2})(\d{7})", passport)
        if m:
            series_el = hp.locator(
                "div.form-modal input.form-control"
            ).nth(0)
            number_el = hp.locator(
                "div.form-modal input.form-control"
            ).nth(1)
            if not (await series_el.input_value()).strip():
                await _setvalue(
                    hp, "div.form-modal input.form-control >> nth=0", m.group(1)
                )
            if not (await number_el.input_value()).strip():
                await _setvalue(
                    hp, "div.form-modal input.form-control >> nth=1", m.group(2)
                )
    except Exception:
        pass

    # Videoda «Маълумот: Ўрта» tanlangan. Bu maydon majburiy emas, lekin
    # formani to'liqroq qiladi. Xato bo'lsa, saqlashga xalal bermaymiz.
    try:
        await _select2(hp, "div.form-modal .select2-selection >> nth=2", "Ўрта")
    except Exception:
        pass

    try:
        await _execute_step(
            hp, save_step, person_inputs, job_id, settle_ms=100
        )
    except Exception:
        # Selector o'zgargan bo'lsa, matn bo'yicha «Сақлаш» tugmasini bosamiz.
        clicked = False
        for label in ("Сақлаш", "Саклаш", "Save"):
            try:
                await _click_smart(
                    hp, f"button:has-text('{label}')", 20000, 8000
                )
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            raise
    await hp.wait_for_timeout(300)
    if verify_step is not None:
        await _execute_step(
            hp, verify_step, person_inputs, job_id, settle_ms=100
        )


async def _visible_text_count(
    page: Page, text: str, limit: int = 5
) -> int:
    """Sahifadagi ko'rinadigan matn nusxalarini sanaydi."""
    try:
        locator = page.get_by_text(text, exact=True)
        total = await locator.count()
    except Exception:
        return 0
    visible = 0
    for index in range(min(total, limit)):
        try:
            if await locator.nth(index).is_visible():
                visible += 1
        except Exception:
            pass
    return visible


async def _batch_session_alive(page: Page) -> bool:
    """Sayt sessiyasi tirikmi yoki akkauntni chiqarib yuborganmi?"""
    try:
        url = (page.url or "").lower()
    except Exception:
        return False
    if any(marker in url for marker in ("/login", "egov.uz", "oneid")):
        return False
    try:
        field = page.locator("input[name='login']").first
        if await field.count() and await field.is_visible():
            return False
    except Exception:
        pass
    try:
        if await _visible_text_count(page, "Тизимга кириш") > 0:
            return False
        if await _visible_text_count(page, "Чиқиш") > 0:
            return True
        if await _visible_text_count(page, "Профиль") > 0:
            return True
    except Exception:
        pass
    return "online-mahalla.uz" in url


async def _store_batch_session(ctx, session_path: str) -> None:
    await ctx.storage_state(path=session_path)
    try:
        os.chmod(session_path, 0o600)
    except Exception:
        pass


async def _relogin_batch(
    page: Page,
    ctx,
    steps: list[WorkflowStep],
    inputs: dict,
    job_id: str,
    session_path: str,
) -> Page:
    """Sayt sessiyani yopganda LOGIN/PASSWORD bilan avtomatik qayta kiradi."""
    login_value = str(inputs.get("LOGIN", "")).strip()
    password_value = str(inputs.get("PASSWORD", ""))
    if not (login_value and password_value):
        raise BatchLoginError(
            "Sessiya eskirgan, LOGIN/PASSWORD esa kiritilmagan. "
            "Job sahifasida ularni qayta kiriting."
        )

    login_steps = [s for s in steps if s.order_index <= 6]
    if not login_steps:
        raise BatchLoginError(
            "Sessiya eskirgan, qayta login qadamlari topilmadi."
        )

    if page.is_closed():
        page = await ctx.new_page()
        page.set_default_timeout(settings.STEP_TIMEOUT_SEC * 1000)

    await _log(
        job_id,
        "🔁 Sessiya tugadi — LOGIN/PASSWORD bilan avtomatik qayta "
        "login boshlandi",
    )
    nav_step = next(
        (s for s in login_steps if s.action == "navigate"), None
    )
    if nav_step is not None:
        page = await _exec_follow(page, ctx, nav_step, inputs, job_id)
    else:
        await page.goto(
            HOME_URL, wait_until="domcontentloaded", timeout=45000
        )
        await page.wait_for_timeout(500)
    if await _batch_session_alive(page):
        return page

    oneid_step = next(
        (
            s
            for s in login_steps
            if s.action == "click"
            and "oneid" in (s.selector or "").lower()
        ),
        None,
    )
    if oneid_step is None:
        oneid_step = next(
            (
                s
                for s in login_steps
                if s.action == "click" and s.order_index == 1
            ),
            None,
        )
    if oneid_step is not None:
        try:
            page = await _exec_follow(
                page, ctx, oneid_step, inputs, job_id
            )
        except Exception as exc:
            await _log(
                job_id,
                "ℹ️ OneID tugmasi bosilmadi "
                f"({str(exc)[:120]}) — SSO davom etadi",
            )

    login_step = next(
        (
            s
            for s in login_steps
            if s.action == "type" and s.input_key == "LOGIN"
        ),
        None,
    )
    password_step = next(
        (
            s
            for s in login_steps
            if s.action == "type" and s.input_key == "PASSWORD"
        ),
        None,
    )
    submit_step = next(
        (s for s in login_steps if s.action == "submit"), None
    )
    if submit_step is None:
        submit_step = next(
            (
                s
                for s in login_steps
                if s.action == "click" and s.order_index == 5
            ),
            None,
        )

    loop = asyncio.get_running_loop()
    form_ready = False
    deadline = loop.time() + 30
    while loop.time() < deadline:
        if await _batch_session_alive(page):
            break
        if login_step is not None:
            try:
                field = page.locator(
                    login_step.selector or "input[name='login']"
                )
                if await field.count() and await field.first.is_visible():
                    form_ready = True
                    break
            except Exception:
                pass
        await page.wait_for_timeout(500)

    if form_ready:
        for step in (login_step, password_step, submit_step):
            if step is None:
                continue
            page = await _exec_follow(page, ctx, step, inputs, job_id)
            if step.order_index == 5:
                await _choose_oneid_individual(page, job_id, 8000)
    else:
        # OneID sessiyasi saqlangan bo'lsa, login formasi chiqmaydi.
        await _choose_oneid_individual(page, job_id, 4000)

    deadline = loop.time() + 45
    while loop.time() < deadline:
        if await _batch_session_alive(page):
            break
        try:
            body_low = (await page.inner_text("body")).lower()
        except Exception:
            body_low = ""
        if any(
            k in body_low
            for k in (
                "логин ёки парол",
                "login yoki parol",
                "нотўғри",
                "noto'g'ri",
            )
        ):
            raise BatchLoginError(
                "OneID login yoki parol xato — qayta tekshiring."
            )
        await page.wait_for_timeout(500)

    if not await _batch_session_alive(page):
        raise BatchLoginError(
            "Sessiya eskirgan, avtomatik qayta login amalga oshmadi. "
            "LOGIN/PASSWORD yoki sayt holatini tekshiring."
        )
    try:
        await _store_batch_session(ctx, session_path)
        await _log(job_id, "💾 Sessiya yangilandi")
    except Exception as exc:
        await _log(
            job_id,
            f"⚠️ Sessiya saqlanmadi: {str(exc)[:160]}",
            level="error",
        )
    await _log(job_id, "✅ Qayta login muvaffaqiyatli")
    return page


async def _restore_household(
    page: Page, url: str, job_id: str
) -> Page:
    """Qayta logindan keyin o'sha xonadon sahifasini qayta ochadi."""
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(600)
    await _click_smart(
        page,
        "button:has-text('Хонадон аъзолари'), "
        "a:has-text('Хонадон аъзолари'), "
        "[role='button']:has-text('Хонадон аъзолари')",
        first_timeout=30000,
        other_timeout=12000,
    )
    await page.wait_for_timeout(500)
    return page


async def _ensure_batch_session(
    page: Page,
    ctx,
    steps: list[WorkflowStep],
    inputs: dict,
    job_id: str,
    session_path: str,
    restore_url: str | None = None,
) -> Page:
    """Sessiya tirik bo'lsa o'zini, aks holda qayta login qilingan sahifani qaytaradi."""
    if await _batch_session_alive(page):
        return page
    page = await _relogin_batch(
        page, ctx, steps, inputs, job_id, session_path
    )
    if restore_url:
        try:
            page = await _restore_household(page, restore_url, job_id)
        except Exception as exc:
            raise BatchLoginError(
                "Qayta login bo'ldi, lekin xonadon sahifasini tiklab "
                f"bo'lmadi: {str(exc)[:180]}"
            ) from exc
    return page


async def _run_batch(
    run: Run,
    job: VideoJob,
    steps: list[WorkflowStep],
    db,
    inputs: dict | None = None,
) -> None:
    # LOGIN/PASSWORD run.inputs'da emas — runner ularni jarayon
    # xotirasidan qo'shib beradi.
    inputs = inputs if inputs is not None else (run.inputs or {})
    login = str(inputs.get("LOGIN", "")).strip()
    password = str(inputs.get("PASSWORD", ""))
    reuse_session = str(inputs.get("REUSE_SESSION", "")).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    session_path = _session_path(job.id)
    session_available = os.path.exists(session_path)
    if login and password:
        _remember_credentials(job.id, login, password)
    else:
        cached = _cached_credentials(job.id)
        if cached:
            login = cached.get("LOGIN", "")
            password = cached.get("PASSWORD", "")
            await _log(
                job.id,
                "🔐 Login ma'lumotlari jarayon xotirasidan olindi "
                "(sayt sessiyani yopsa avtomatik qayta kiriladi)",
            )
    if not ((reuse_session and session_available) or (login and password)):
        raise RuntimeError(
            "LOGIN va PASSWORD kiritilishi shart (yoki saqlangan sessiya)"
        )

    dataset_id = str(inputs.get("DATASET") or "").strip()
    dataset_db = _dataset_db(dataset_id)
    if not os.path.isfile(dataset_db):
        raise RuntimeError(
            f"Excel dataset fayli topilmadi: {dataset_db}. "
            f"Iltimos, avval Excel faylni yuklang va datasetni tanlang."
        )
    # Yangi yuklangan dataset uchun 2-qatordan boshlash tabiiy. Eski
    # ХАТЛОВ bazasi uchun avvalgi 262655 fallback saqlanadi.
    start_row = max(
        2,
        _as_int(inputs.get("START_ROW"), 2 if dataset_id else 262655),
    )
    await _log(
        job.id,
        f"📊 Excel dataset: {os.path.basename(dataset_db)} "
        f"(dataset_id={dataset_id or 'standart XATLOV'}, boshlanish qatori: {start_row})",
    )
    # Xonadonlar soni endi 32 bilan cheklanmaydi. `0` — cheksiz:
    # ko'chalar yoki Excel'dagi odamlar tugagach run o'zi yakunlanadi.
    households_limit = max(0, _as_int(inputs.get("HOUSEHOLDS_LIMIT"), 1))
    unlimited = households_limit <= 0
    per_household = max(1, _as_int(inputs.get("PEOPLE_PER_HOUSEHOLD"), 5))

    save_step = next((s for s in steps if "save-btn" in (s.selector or "")), None)
    if save_step is None:
        raise RuntimeError(
            "Bu jobda saqlash (save-btn) qadami yo'q. 30 qadamli saqlash "
            "variantini tanlang."
        )
    verify_step = next((s for s in steps if s.action == "verify_saved"), None)

    # Modal qadamlar: «Қўшиш» clickidan keyin, «Сақлаш» clickidan oldin.
    add_idx = next(
        (
            i
            for i, s in enumerate(steps)
            if s.action == "click" and "Қўшиш" in (s.selector or "")
        ),
        None,
    )
    if add_idx is None:
        raise RuntimeError("A'zo qo'shish («Қўшиш») qadami topilmadi")
    save_idx = next((i for i, s in enumerate(steps) if s is save_step), len(steps))
    # Qidiruvdan keyingi 8s kutish shart emas: `verify_filled` o'zi
    # natijani poll qiladi. Modal ochilgandan keyingi 3s kutish esa
    # select2 maydonlari tayyor bo'lishi uchun qoldiriladi.
    modal_steps = [
        s
        for s in steps[add_idx + 1:save_idx]
        if not (s.action == "wait" and _as_int(s.value_template, 0) >= 5000)
    ]
    if not modal_steps:
        raise RuntimeError("Modal formani to'ldirish qadamlari topilmadi")

    login_steps = [s for s in steps if s.order_index <= 9]
    if len(login_steps) < 10:
        raise RuntimeError("Kirish qadamlari to'liq emas (0–9 qadamlar kerak)")
    reauth_steps = [s for s in login_steps if s.order_index <= 6]
    # Parol DB'ga va diskka yozilmaydi; sessiya har odamdan keyin
    # yopilishi mumkinligi uchun backend jarayoni xotirasida saqlanadi.
    reauth_inputs = {**inputs, "LOGIN": login, "PASSWORD": password}

    # Holatni yuklash. Boshqa dataset tanlansa, eski datasetning
    # taraqqiyoti yangi ro'yxatga aralashmasligi uchun arxivlab, yangidan
    # boshlaymiz. Xuddi shu dataset qayta tanlansa, run resume bo'ladi.
    state = _load_state(job.id)
    legacy_dataset = os.path.splitext(os.path.basename(EXCEL_DB))[0]
    previous_dataset = str(state.get("dataset") or legacy_dataset).strip()
    if dataset_id and previous_dataset != dataset_id:
        try:
            state_path = _state_path(job.id)
            if os.path.exists(state_path):
                safe_old = re.sub(r"[^A-Za-z0-9._-]", "_", previous_dataset)[:80]
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup = f"{state_path}.{safe_old}.{stamp}.bak"
                os.replace(state_path, backup)
                await _log(
                    job.id,
                    f"♻️ Dataset o'zgardi ({previous_dataset} -> "
                    f"{dataset_id}); eski holat arxivlandi: "
                    f"{os.path.basename(backup)}",
                )
        except Exception as exc:
            await _log(
                job.id,
                f"⚠️ Eski batch holatini arxivlash imkoni bo'lmadi: "
                f"{str(exc)[:160]}",
                level="error",
            )
        state = {"dataset": dataset_id}

    # Boshlanish ko'chasi: foydalanuvchi qo'lda kiritgan bo'lsa o'sha,
    # aks holda avvalgi holatdagi ko'cha yoki ro'yxatdagi birinchi ko'cha.
    if "START_STREET" in inputs:
        start_street = str(inputs.get("START_STREET") or "").strip()
        if start_street:
            state["start_street"] = start_street
        else:
            state.pop("start_street", None)
    else:
        start_street = str(state.get("start_street") or "").strip()
    await _log(
        job.id,
        (
            f"🎯 Boshlanish ko'chasi (qo'lda): {start_street}"
            if start_street
            else "🎯 Boshlanish ko'chasi qo'lda kiritilmagan — "
            "avvalgi holat yoki birinchi ko'cha"
        ),
    )

    households_state: dict = state.setdefault("households", {})
    used_pinpps: set[str] = set(state.get("used_pinpps", []) or [])
    registered_pinpps: set[str] = set(
        state.get("registered_pinpps", []) or []
    )
    invalid_pinpps: set[str] = set(
        state.get("invalid_pinpps", []) or []
    )
    failed_pinpps: set[str] = set(
        state.get("failed_pinpps", []) or []
    )
    for hs in households_state.values():
        for p in hs.get("pinpps", []) or []:
            used_pinpps.add(str(p))
    attempted_pinpps: set[str] = set()
    pinfl_limiter = PinflLimiter(state)

    saved_before = sum(
        min(_as_int(hs.get("added"), 0), per_household)
        for hs in households_state.values()
    )

    # Hisobot qaysi maqsadga nisbatan yozilishini bilishi uchun joriy
    # run parametrlarini holatga yozamiz. Keyingi run kattaroq son bilan
    # boshlansa, hisobot ham yangilanadi.
    state["target_households"] = households_limit
    state["target_per_household"] = per_household
    state["all_streets_done"] = False
    state["dataset"] = dataset_id or os.path.splitext(os.path.basename(EXCEL_DB))[0]
    _save_state(job.id, state)
    try:
        done_path = os.path.join(STATE_DIR, "BATCH_DONE.md")
        if os.path.exists(done_path):
            os.remove(done_path)
    except Exception:
        pass

    run.status = JobStatus.RUNNING
    run.total_steps = 0 if unlimited else households_limit * per_household
    run.current_step = (
        saved_before
        if unlimited
        else min(saved_before, run.total_steps)
    )
    run.error = None
    run.finished_at = None
    await db.commit()
    await _status(
        job.id, run.id, "running", step=run.current_step
    )
    limit_label = "cheksiz" if unlimited else str(households_limit)
    await _log(
        job.id,
        f"🚀 Batch boshlandi: {limit_label} xonadon × {per_household} "
        f"yangi odam (Excel #{start_row}dan, har biriga alohida telefon). "
        f"Dataset: {os.path.basename(dataset_db)}. "
        "Ko'chalar va jadval sahifalari avtomatik almashadi. "
        f"PINFL limiti: {PINFL_MAX_REQUESTS}/{int(PINFL_WINDOW_SECONDS)}s "
        f"→ har {PINFL_SPACING_SECONDS:.0f}s.",
    )
    if saved_before:
        await _log(
            job.id,
            f"♻️ Avval saqlangan: {saved_before} ta odam hisobga olindi",
        )

    excel = ExcelPeople(start_row, dataset_db)
    await _log(
        job.id,
        f"📊 Excel: {excel.total} qator, {excel.valid} ta yaroqli "
        "(JSHSHIR 14 xonali, tug'ilgan sana DD.MM.YYYY)",
    )
    await _log(
        job.id,
        f"🚫 Oldin urinilgan: ishlatilgan={len(used_pinpps)}, "
        f"ro'yxatga olingan={len(registered_pinpps)}, "
        f"yaroqsiz={len(invalid_pinpps)}, xato={len(failed_pinpps)}",
    )
    if excel.valid <= 0:
        raise RuntimeError(
            "Excel'da yaroqli odam yo'q: JSHSHIR 14 xonali va tug'ilgan "
            "sana DD.MM.YYYY bo'lishi kerak. Excelni qayta yuklang yoki "
            "ustun nomlarini tekshiring."
        )
    added_total = 0
    errors_total = 0
    skipped_total = 0
    incomplete_total = 0
    processed = 0

    async with async_playwright() as p:
        browser_name = run.browser or settings.BROWSER_TYPE
        browser = await launch_browser(p, browser_name)
        await _log(job.id, f"Brauzer: {browser_name}")
        context_kwargs = context_options(browser_name)
        session_loaded = False
        if reuse_session and session_available:
            try:
                ctx = await browser.new_context(
                    storage_state=session_path, **context_kwargs
                )
                session_loaded = True
                await _log(job.id, "🔐 Saqlangan sessiya yuklandi")
            except Exception as e:
                await _log(
                    job.id,
                    f"⚠️ Sessiya fayli o'qilmadi: {str(e)[:160]}",
                    level="error",
                )
                ctx = await browser.new_context(**context_kwargs)
        else:
            ctx = await browser.new_context(**context_kwargs)
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        # Sayt sekinligining asosiy sababi — notification.js va analitika.
        await ctx.route("**/notification.js", lambda r: r.abort())
        await ctx.route("**/google-tag-manager-head.js", lambda r: r.abort())
        await ctx.route("**/googletagmanager.com/**", lambda r: r.abort())
        await ctx.route("**/mc.yandex**", lambda r: r.abort())

        page = await ctx.new_page()
        page.set_default_timeout(settings.STEP_TIMEOUT_SEC * 1000)
        account_meta: dict[str, str] = {}
        auth_ready = False

        # Diagnostika: sayt API'larining URL/status/keys'larini logga yozamiz.
        # Bu qaysi odam allaqachon ro'yxatda ekanini tezroq aniqlash uchun
        # foydali bo'lishi mumkin. Login/parol URL'lari filtrlanadi.
        loop = asyncio.get_running_loop()

        async def _api_response(resp) -> None:
            try:
                url = resp.url
                low = url.lower()
                if any(k in low for k in ("login", "oneid", "password", "auth")):
                    return
                if not any(
                    k in low
                    for k in (
                        "/api/",
                        "survey",
                        "home",
                        "member",
                        "citizen",
                        "pinpp",
                        "jshshir",
                        "save",
                        "project",
                        "table",
                        "user",
                        "profile",
                        "street",
                    )
                ):
                    return
                status = resp.status
                short = url.split("?")[0][:140]
                if status >= 400:
                    try:
                        body = (await resp.text())[:400]
                    except Exception:
                        body = ""
                    await _log(job.id, f"🌐 API {status} {short} | {body}")
                elif (
                    auth_ready
                    and not account_meta
                    and any(
                        k in low
                        for k in (
                            "project",
                            "profile",
                            "user",
                            "home",
                            "table",
                            "survey",
                            "meta",
                        )
                    )
                    and "survey_homes_street" not in low
                    and "/save" not in low
                    and "/search" not in low
                    and "pinpp" not in low
                    and "jshshir" not in low
                    and "citizen" not in low
                ):
                    try:
                        data = await resp.json()
                    except Exception:
                        data = None
                    found = _find_account_street(data)
                    if auth_ready and found and not account_meta:
                        account_meta.update(found)
                        await _log(
                            job.id,
                            "🎯 Hisobdagi ko'cha aniqlandi: "
                            f"{found.get('street') or found.get('street_id')}",
                        )
                elif any(
                    k in low
                    for k in ("save", "search", "pinpp", "jshshir", "citizen")
                ):
                    try:
                        data = await resp.json()
                    except Exception:
                        data = None
                    if isinstance(data, dict):
                        keys = ",".join(list(data.keys())[:15])
                        await _log(
                            job.id, f"🌐 API {status} {short} | keys: {keys}"
                        )
                    elif (
                        isinstance(data, list)
                        and data
                        and isinstance(data[0], dict)
                    ):
                        keys = ",".join(list(data[0].keys())[:15])
                        await _log(
                            job.id,
                            f"🌐 API {status} {short} | "
                            f"list[{len(data)}] keys: {keys}",
                        )
                    else:
                        await _log(job.id, f"🌐 API {status} {short}")
            except Exception:
                pass

        page.on(
            "response",
            lambda resp: loop.create_task(_api_response(resp)),
        )

        try:
            # 1) Kirish va ko'cha jadvalini ochish
            try:
                # 8–9 qadamlar video'dagi bitta ko'chaga bog'langan. Batch
                # rejimida ko'chani hisob ma'lumotlaridan o'zimiz tanlaymiz.
                auth_steps = [
                    s for s in login_steps if s.order_index not in (8, 9)
                ]
                if len(auth_steps) < len(login_steps):
                    await _log(
                        job.id,
                        "ℹ️ Video'dagi ko'cha tanlash qadamlari o'tkazib "
                        "yuborildi — hisobdagi ko'cha ishlatiladi",
                    )
                if session_loaded:
                    session_ok = False
                    try:
                        await page.goto(
                            HOME_URL,
                            wait_until="domcontentloaded",
                            timeout=45000,
                        )
                        await page.wait_for_timeout(1200)
                        url_low = page.url.lower()
                        logged_in = await _visible_text_count(
                            page, "Чиқиш"
                        )
                        profile = await _visible_text_count(
                            page, "Профиль"
                        )
                        login_button = await _visible_text_count(
                            page, "Тизимга кириш"
                        )
                        session_ok = (
                            "login" not in url_low
                            and "oneid" not in url_low
                            and login_button == 0
                            and (logged_in > 0 or profile > 0)
                        )
                        if session_ok:
                            auth_ready = True
                            menu_step = next(
                                (
                                    s
                                    for s in login_steps
                                    if s.order_index == 7
                                ),
                                None,
                            )
                            if menu_step is not None:
                                page = await _exec_follow(
                                    page, ctx, menu_step, inputs, job.id
                                )
                            auth_steps = []
                            await _log(
                                job.id,
                                "🔐 Sessiya amal qilmoqda — login o'tkazib "
                                "yuborildi, ko'chalar ro'yxati ochildi",
                            )
                    except Exception:
                        session_ok = False
                    if not session_ok:
                        auth_ready = False
                        account_meta.clear()
                        await _log(
                            job.id,
                            "⚠️ Saqlangan sessiya eskirgan — qayta login kerak",
                            level="error",
                        )
                        if not (login and password):
                            raise RuntimeError(
                                "Sessiya eskirgan. LOGIN/PASSWORD kiriting."
                            )

                for step in auth_steps:
                    await _log(
                        job.id,
                        f"[{step.order_index + 1}/{len(steps)}] "
                        f"{step.description or step.action}",
                    )
                    page = await _exec_follow(
                        page, ctx, step, reauth_inputs, job.id
                    )

                    if step.order_index == 5:
                        # Login submitdan keyin OneID «жисмоний/юридик шахс»
                        # so'rasa, jismoniy shaxsni tanlaymiz.
                        auth_ready = True
                        await _choose_oneid_individual(page, job.id, 8000)
                    elif step.order_index == 6:
                        auth_ready = True
                        await _choose_oneid_individual(page, job.id, 4000)

                    if step.order_index == 6:
                        try:
                            body_low = (await page.inner_text("body")).lower()
                        except Exception:
                            body_low = ""
                        if any(
                            k in body_low
                            for k in (
                                "логин ёки парол",
                                "login yoki parol",
                                "нотўғри",
                                "noto'g'ri",
                            )
                        ):
                            raise RuntimeError(
                                "OneID login yoki parol xato — qayta tekshiring"
                            )
            except Exception:
                shot = await _screenshot(page, run.id, "batch_login_fail")
                if shot:
                    await _log(job.id, f"📸 {shot}", level="error")
                raise

            auth_ready = True
            try:
                opened_url, opened_name = await _open_initial_street(
                    page, job.id, state, account_meta, start_street
                )
                state["current_street_url"] = opened_url
                if opened_name:
                    state["current_street_name"] = opened_name
                list_url = _street_list_url(opened_url)
                if "survey_homes_street" in urlsplit(list_url).path:
                    state["street_list_url"] = list_url
                else:
                    state["street_list_url"] = STREET_LIST_URL
                _save_state(job.id, state)
            except Exception:
                shot = await _screenshot(page, run.id, "batch_street_fail")
                if shot:
                    await _log(job.id, f"📸 {shot}", level="error")
                raise

            # Sessiyani saqlaymiz: keyingi runlar parolsiz davom etadi.
            try:
                await ctx.storage_state(path=session_path)
                try:
                    os.chmod(session_path, 0o600)
                except Exception:
                    pass
                await _log(
                    job.id,
                    "💾 Sessiya saqlandi — keyingi runlar parolsiz ishlaydi",
                )
            except Exception as e:
                await _log(
                    job.id,
                    f"⚠️ Sessiya saqlanmadi: {str(e)[:160]}",
                    level="error",
                )

            # LOGIN/PASSWORD hech qachon bazaga yozilmasin: run.inputs'da
            # faqat maxfiy bo'lmagan kalitlar qoladi.
            if inputs.get("PASSWORD") or inputs.get("LOGIN"):
                try:
                    cleaned = {
                        k: v
                        for k, v in inputs.items()
                        if k not in ("LOGIN", "PASSWORD")
                    }
                    run.inputs = cleaned
                    await db.commit()
                except Exception:
                    pass

            # 2) Ko'chalar bo'ylab xonadonlarni ketma-ket yuritish.
            # Joriy ko'cha tugagach, ko'chalar jamlanmasidan keyingi ko'cha
            # avtomatik ochiladi. Shu tarzda bitta ko'chada qotib qolmaydi.
            streets_cache: list[dict] | None = None
            current_index = -1
            visited_streets: set[str] = set()
            current_street_url = page.url
            current_street_name = str(
                state.get("current_street_name") or ""
            )
            # Sayt bir xil ko'chaga qayta-qayta qaytarsa, cheksiz
            # aylanib qolmaslik uchun hisoblagich.
            repeat_count = 0
            # Ro'yxat URL'i holatdan olinadi; sahifa boshqa hududga
            # o'tsa yangilanadi.
            street_list_url_current = str(
                state.get("street_list_url") or ""
            ).strip()
            if "survey_homes_street" not in urlsplit(
                street_list_url_current
            ).path:
                street_list_url_current = STREET_LIST_URL
            while True:
                if await _is_cancelled(db, run):
                    raise RuntimeError("Foydalanuvchi tomonidan bekor qilindi")
                if not await _batch_session_alive(page):
                    page = await _ensure_batch_session(
                        page,
                        ctx,
                        reauth_steps,
                        reauth_inputs,
                        job.id,
                        session_path,
                    )
                    resume_url = str(
                        state.get("current_street_url") or ""
                    ).strip()
                    if resume_url:
                        await page.goto(
                            resume_url,
                            wait_until="domcontentloaded",
                            timeout=45000,
                        )
                        await page.wait_for_timeout(150)
                current_id = _street_id_from_url(page.url)
                repeated_street = bool(
                    current_id and current_id in visited_streets
                )
                if repeated_street:
                    repeat_count += 1
                    await _log(
                        job.id,
                        f"⚠️ Ko'cha {current_id} allaqachon ko'rilgan — "
                        "qaytadan ochildi, keyingi ko'chaga o'taman",
                        level="error",
                    )
                    if repeat_count >= 3:
                        await _log(
                            job.id,
                            "⚠️ Sayt bir xil ko'chaga qayta-qayta "
                            "qaytarmoqda — aylanishni to'xtataman",
                            level="error",
                        )
                        break
                else:
                    repeat_count = 0
                    if current_id:
                        visited_streets.add(current_id)
                current_street_url = page.url
                state["current_street_id"] = current_id
                state["current_street_url"] = current_street_url
                if current_street_name:
                    state["current_street_name"] = current_street_name
                _save_state(job.id, state)

                # Jadvalning BARCHA sahifalari (100 tadan ko'p bo'lsa ham)
                # oldindan yig'iladi. Keyin xonadonlar to'g'ridan-to'g'ri
                # URL orqali ochiladi — qaytish paytida jadval 1-sahifaga
                # tushib qolmaydi va keyingi ko'chaga o'tish ishonchli.
                street_url = page.url
                if repeated_street:
                    households = []
                else:
                    await _log(
                        job.id,
                        f"🛣 Ko'cha {current_street_name or current_id or '?'}: "
                        "xonadonlar sahifalari skanerlanadi",
                    )
                    households = await _collect_households(
                        page, job.id, street_url
                    )
                for i, household in enumerate(households):
                    if not unlimited and processed >= households_limit:
                        break
                    if await _is_cancelled(db, run):
                        raise RuntimeError("Foydalanuvchi tomonidan bekor qilindi")

                    href = str(household.get("href") or "").strip()
                    label = str(household.get("label") or f"#{i + 1}")
                    key = href or label or f"row-{i}"
                    hstate = households_state.setdefault(
                        key, {"added": 0, "pinpps": []}
                    )
                    hstate["label"] = label
                    hstate["href"] = key
                    hstate["index"] = int(household.get("index") or i + 1)
                    already = _as_int(hstate.get("added"), 0)
                    if already >= per_household:
                        skipped_total += 1
                        await _log(
                            job.id,
                            f"⏩ {label}: avval {already} ta qo'shilgan — o'tkazildi",
                        )
                        continue
                    if hstate.get("impossible"):
                        skipped_total += 1
                        await _log(
                            job.id,
                            f"⏩ {label}: ilgari imkonsiz deb belgilangan — "
                            "o'tkazildi",
                        )
                        continue

                    need = per_household - already
                    await _log(
                        job.id,
                        f"🏠 {label}: oldin {already}/{per_household} ta qo'shilgan; "
                        f"yana {need} ta qo'shaman",
                    )

                    hp = page
                    household_url = str(household.get("url") or "")
                    if not household_url:
                        continue
                    try:
                        # Sessiya tirikligini tekshiramiz; kerak bo'lsa
                        # avtomatik qayta login qilinadi.
                        hp = await _ensure_batch_session(
                            hp,
                            ctx,
                            reauth_steps,
                            reauth_inputs,
                            job.id,
                            session_path,
                        )
                        for goto_attempt in range(2):
                            try:
                                await hp.goto(
                                    household_url,
                                    wait_until="domcontentloaded",
                                    timeout=45000,
                                )
                                break
                            except Exception:
                                if goto_attempt:
                                    raise
                                await hp.wait_for_timeout(300)
                                hp = await _ensure_batch_session(
                                    hp,
                                    ctx,
                                    reauth_steps,
                                    reauth_inputs,
                                    job.id,
                                    session_path,
                                )
                        try:
                            await hp.wait_for_load_state(
                                "domcontentloaded", timeout=10000
                            )
                        except Exception:
                            pass

                        low_url = hp.url.lower()
                        if "login" in low_url or "oneid" in low_url:
                            hp = await _ensure_batch_session(
                                hp,
                                ctx,
                                reauth_steps,
                                reauth_inputs,
                                job.id,
                                session_path,
                                restore_url=household_url,
                            )
                        else:
                            await _click_smart(
                                hp,
                                "button:has-text('Хонадон аъзолари'), "
                                "a:has-text('Хонадон аъзолари'), "
                                "[role='button']:has-text('Хонадон аъзолари')",
                                first_timeout=30000,
                                other_timeout=12000,
                            )

                        pinpps, member_count = await _read_members(hp)
                        hstate["initial_members"] = member_count
                        household_url = hp.url
                        await _log(
                            job.id,
                            f"👥 {label}: jadvalda {member_count} ta a'zo bor",
                        )
                        # Sayt bir xonadonga 20 tadan ortiq a'zo qo'shishga
                        # ruxsat bermaydi. Limitga yetgan xonadonni PINFL
                        # qidiruvlarini behuda sarflamasdan o'tkazib yuboramiz.
                        impossible_household = False
                        if member_count + need > 20:
                            impossible_household = True
                            hstate["impossible"] = True
                            hstate["reason"] = (
                                f"xonadonda {member_count} a'zo; sayt limiti 20"
                            )
                            hstate["incomplete_at"] = datetime.utcnow().isoformat()
                            _save_state(job.id, state)
                            await _log(
                                job.id,
                                f"⛔ {label}: {member_count} ta a'zo bor — "
                                "20 tadan oshirib bo'lmaydi, keyingi xonadonga "
                                "o'taman",
                                level="error",
                            )
                            need = 0
                        # Bu xonadondagi mavjud a'zolarni boshqa xonadonlarga
                        # nomzod sifatida qayta urinmaslik uchun eslab qolamiz.
                        if pinpps - registered_pinpps:
                            registered_pinpps.update(pinpps)
                            state["registered_pinpps"] = sorted(registered_pinpps)
                            _save_state(job.id, state)

                        # Qarindoshlarni birinchi tekshiramiz: ularning
                        # manzili/familiyasi xonadon a'zolariga yaqin bo'lsa,
                        # yangi odam bo'lish ehtimoli yuqori. Bu PINFL
                        # qidiruvlarini behuda sarflamaslikka yordam beradi.
                        priority_candidates = excel.priority_persons(
                            pinpps,
                            pinpps
                            | used_pinpps
                            | registered_pinpps
                            | invalid_pinpps
                            | failed_pinpps,
                        )
                        if priority_candidates:
                            await _log(
                                job.id,
                                f"🎯 {label}: {len(priority_candidates)} ta "
                                "yaqin nomzod (manzil/familiya) topildi",
                            )

                        added_here = 0
                        attempts = 0
                        max_attempts = need + MAX_EXTRA_ATTEMPTS

                        while added_here < need and attempts < max_attempts:
                            if await _is_cancelled(db, run):
                                raise RuntimeError(
                                    "Foydalanuvchi tomonidan bekor qilindi"
                                )
                            attempts += 1
                            skip_now = (
                                pinpps
                                | used_pinpps
                                | registered_pinpps
                                | invalid_pinpps
                                | failed_pinpps
                                | attempted_pinpps
                            )
                            person = None
                            # Avval qarindosh/manzil nomzodlarini tekshiramiz.
                            while priority_candidates:
                                candidate = priority_candidates.pop(0)
                                if candidate["pinpp"] in skip_now:
                                    continue
                                person = candidate
                                break
                            if person is None:
                                person = excel.next_person(skip_now)
                            if person is None:
                                raise RuntimeError(
                                    "Excel'da mos odam qolmadi: "
                                    f"{excel.valid} ta yaroqli qator bor, "
                                    "lekin ularning hammasi oldin urinilgan "
                                    "yoki o'tkazib yuborilgan "
                                    f"(ishlatilgan={len(used_pinpps)}, "
                                    f"ro'yxatga olingan={len(registered_pinpps)}, "
                                    f"yaroqsiz={len(invalid_pinpps)}, "
                                    f"xato={len(failed_pinpps)}). "
                                    "Yangi Excel yuklang yoki batch holatini "
                                    "yangilang."
                                )

                            person_phone = _phone_for(person)
                            await _log(
                                job.id,
                                f"👤 {label} {added_here + 1}/{need} · "
                                f"urinish {attempts}/{max_attempts}: "
                                f"Excel #{person['row_num']} — "
                                f"{person['full_name']} · {person_phone}",
                            )
                            # Supervisor qaysi qatorgacha borilganini ko'rishi uchun.
                            state["last_row"] = person["row_num"]
                            _save_state(job.id, state)

                            # Sayt har bir odamdan keyin sessiyani yopadi.
                            # Keyingi odamga o'tishdan oldin avtomatik
                            # login qilib, xonadon sahifasini tiklaymiz.
                            hp = await _ensure_batch_session(
                                hp,
                                ctx,
                                reauth_steps,
                                reauth_inputs,
                                job.id,
                                session_path,
                                restore_url=household_url,
                            )

                            saved = False
                            full_household = False
                            rate_retries = 0
                            while True:
                                if await _is_cancelled(db, run):
                                    raise RuntimeError(
                                        "Foydalanuvchi tomonidan bekor qilindi"
                                    )
                                # PINFL 3/90s limitini hurmat qilamiz: navbat
                                # kerak bo'lsa, shu joyda kutadi.
                                await pinfl_limiter.before_lookup(hp, job.id)
                                # Kutish vaqtida sayt sessiyani yopgan bo'lishi
                                # mumkin — formani ochishdan oldin tiklaymiz.
                                hp = await _ensure_batch_session(
                                    hp,
                                    ctx,
                                    reauth_steps,
                                    reauth_inputs,
                                    job.id,
                                    session_path,
                                    restore_url=household_url,
                                )
                                try:
                                    await _add_person(
                                        hp,
                                        modal_steps,
                                        save_step,
                                        verify_step,
                                        person,
                                        person_phone,
                                        inputs,
                                        job.id,
                                    )
                                    saved = True
                                    break
                                except RateLimitError as e:
                                    # Limit tufayli qidiruv bajarilmadi. Bu
                                    # odamni "xato" deb belgilamaymiz — sayt
                                    # aytgan vaqtdan keyin aynan shu odamni
                                    # qayta tekshiramiz.
                                    rate_retries += 1
                                    pinfl_limiter.on_rate_limit(e.wait_seconds)
                                    state["rate_limit_hits"] = pinfl_limiter.hits
                                    _save_state(job.id, state)
                                    msg = str(e).replace("\n", " ")[:220]
                                    await _log(
                                        job.id,
                                        f"⏳ Sayt limiti: {msg} — "
                                        "o'sha odam qayta tekshiriladi",
                                        level="error",
                                    )
                                    await _close_modal(hp)
                                    await hp.wait_for_timeout(300)
                                    if rate_retries >= 6:
                                        await _log(
                                            job.id,
                                            "⏳ Limit takrorlanmoqda — 60s "
                                            "qo'shimcha pauza",
                                            level="error",
                                        )
                                        await asyncio.sleep(60)
                                        rate_retries = 0
                                    continue
                                except Exception as e:
                                    errors_total += 1
                                    attempted_pinpps.add(person["pinpp"])
                                    modal_text = await _modal_text(hp)
                                    kind = _classify_error(modal_text)
                                    msg = str(e).replace("\n", " ")[:300]
                                    if kind == "full":
                                        # Sayt 20 tadan ortiq a'zoga ruxsat
                                        # bermaydi. Bu odamning aybi emas —
                                        # xonadonni imkonsiz deb belgilaymiz.
                                        impossible_household = True
                                        full_household = True
                                        hstate["impossible"] = True
                                        hstate["reason"] = "sayt limiti: 20 a'zo"
                                        hstate["incomplete_at"] = (
                                            datetime.utcnow().isoformat()
                                        )
                                        reason = (
                                            "xonadonda 20+ a'zo — qo'shib "
                                            "bo'lmaydi"
                                        )
                                    elif kind == "already":
                                        # Boshqa xonadonda ro'yxatga olingan —
                                        # keyingi runlarda qayta urinmaymiz.
                                        registered_pinpps.add(person["pinpp"])
                                        state["registered_pinpps"] = sorted(
                                            registered_pinpps
                                        )
                                        reason = (
                                            "boshqa xonadonda allaqachon "
                                            "ro'yxatga olingan"
                                        )
                                    elif kind == "phone":
                                        failed_pinpps.add(person["pinpp"])
                                        state["failed_pinpps"] = sorted(
                                            failed_pinpps
                                        )
                                        reason = (
                                            "telefon raqami 5+ a'zoga "
                                            "biriktirilgan"
                                        )
                                    elif kind == "not_found":
                                        invalid_pinpps.add(person["pinpp"])
                                        state["invalid_pinpps"] = sorted(
                                            invalid_pinpps
                                        )
                                        reason = "JSHSHIR/hujjat saytda topilmadi"
                                    else:
                                        failed_pinpps.add(person["pinpp"])
                                        state["failed_pinpps"] = sorted(
                                            failed_pinpps
                                        )
                                        reason = msg
                                    _save_state(job.id, state)
                                    await _log(
                                        job.id,
                                        f"⚠️ #{person['row_num']} o'tkazib "
                                        f"yuborildi — {reason}",
                                        level="error",
                                    )
                                    shot = await _screenshot(
                                        hp,
                                        run.id,
                                        f"person_fail_{person['row_num']}",
                                    )
                                    if shot:
                                        await _log(
                                            job.id, f"📸 {shot}", level="error"
                                        )
                                    await _close_modal(hp)
                                    await hp.wait_for_timeout(300)
                                    break

                            if not saved:
                                if full_household:
                                    break
                                continue

                            # Muvaffaqiyatli saqlandi
                            pinfl_limiter.on_success()
                            added_here += 1
                            added_total += 1
                            pinpps.add(person["pinpp"])
                            used_pinpps.add(person["pinpp"])
                            invalid_pinpps.discard(person["pinpp"])
                            failed_pinpps.discard(person["pinpp"])
                            hstate["added"] = _as_int(hstate.get("added"), 0) + 1
                            hstate.setdefault("pinpps", []).append(person["pinpp"])
                            state["invalid_pinpps"] = sorted(invalid_pinpps)
                            state["failed_pinpps"] = sorted(failed_pinpps)
                            state.setdefault("results", []).append(
                                {
                                    "household_key": key,
                                    "household_label": label,
                                    "href": key,
                                    "pinpp": person["pinpp"],
                                    "full_name": person["full_name"],
                                    "birth_date": person["birth_date"],
                                    "passport": person["passport"],
                                    "phone": person_phone,
                                    "row_num": person["row_num"],
                                    "added_at": datetime.utcnow().isoformat(),
                                }
                            )
                            # Har bir odamdan keyin holatni yozamiz — to'xtab
                            # qolsa, qayta ishga tushirishda takrorlanmaydi.
                            state["used_pinpps"] = sorted(used_pinpps)
                            _save_state(job.id, state)

                            if run.total_steps > 0:
                                run.current_step = min(
                                    run.current_step + 1, run.total_steps
                                )
                            else:
                                run.current_step += 1
                            await db.commit()
                            await _status(
                                job.id, run.id, "running", step=run.current_step
                            )
                            await _log(
                                job.id,
                                f"✅ Saqlandi: {person['full_name']} "
                                f"({added_here}/{need})",
                            )
                            await hp.wait_for_timeout(150)
                            # Har bir saqlashdan keyin sayt akkauntni
                            # chiqarib yuborishi mumkin. Keyingi odamga
                            # tayyorlanish uchun darhol qayta login qilamiz.
                            hp = await _ensure_batch_session(
                                hp,
                                ctx,
                                reauth_steps,
                                reauth_inputs,
                                job.id,
                                session_path,
                                restore_url=household_url,
                            )

                        if impossible_household:
                            errors_total += 1
                            incomplete_total += 1
                            await _log(
                                job.id,
                                f"⛔ {label}: imkonsiz (20+ a'zo) — "
                                "keyingi xonadonga o'taman",
                                level="error",
                            )
                        elif added_here < need:
                            # Bitta xonadon "qotib" qolsa, butun runni
                            # to'xtatmaymiz: keyingi xonadonga o'tamiz.
                            # Qisman natija state'da saqlanadi va keyingi run
                            # shu xonadondan davom etadi.
                            errors_total += 1
                            incomplete_total += 1
                            hstate["incomplete_at"] = datetime.utcnow().isoformat()
                            await _log(
                                job.id,
                                f"⚠️ {label}: faqat {added_here}/{need} ta "
                                f"qo'shildi — keyingi xonadonga o'tamiz",
                                level="error",
                            )
                        else:
                            hstate["completed_at"] = datetime.utcnow().isoformat()

                        processed += 1
                        state["used_pinpps"] = sorted(used_pinpps)
                        _save_state(job.id, state)
                        await _log(
                            job.id,
                            f"🏁 {label}: {added_here} ta yangi odam qo'shildi",
                        )
                    except BatchLoginError:
                        raise
                    except Exception as e:
                        errors_total += 1
                        msg = str(e).replace("\n", " ")[:350]
                        await _log(job.id, f"❌ {label}: {msg}", level="error")
                        shot = await _screenshot(hp, run.id, f"hh_fail_{i}")
                        if shot:
                            await _log(job.id, f"📸 {shot}", level="error")
                        processed += 1
                        await _close_modal(hp)
                    finally:
                        # Xonadon alohida URL orqali ochildi — jadvalga
                        # qaytish shart emas. Keyingi xonadon shu sahifada
                        # davom etadi.
                        if hp is not None and not hp.is_closed():
                            page = hp


                if not unlimited and processed >= households_limit:
                    break

                if not repeated_street:
                    await _log(
                        job.id,
                        f"✅ Ko'cha {current_street_name or current_id or '?'} "
                        f"tugadi ({len(households)} xonadon) — keyingi "
                        "ko'chaga avtomatik o'taman",
                    )

                # Joriy ko'cha tugadi. Keyingi ko'chani avtomatik ochamiz.
                if streets_cache is None:
                    list_url = street_list_url_current
                    if "survey_homes_street" not in urlsplit(list_url).path:
                        list_url = _street_list_url(page.url)
                    if "survey_homes_street" not in urlsplit(list_url).path:
                        # Xonadon sahifasidan jadvalga qaytish uddalangan
                        # bo'lmasa, umumiy ko'chalar ro'yxatiga qaytamiz.
                        list_url = STREET_LIST_URL
                    await _log(job.id, "🗺 Ko'chalar ro'yxatiga o'taman…")
                    streets_cache = None
                    for load_attempt in range(3):
                        try:
                            page = await _ensure_batch_session(
                                page,
                                ctx,
                                reauth_steps,
                                reauth_inputs,
                                job.id,
                                session_path,
                            )
                            await page.goto(
                                list_url,
                                wait_until="domcontentloaded",
                                timeout=45000,
                            )
                            await page.wait_for_timeout(100)
                            streets_cache = await _read_streets(page, job.id)
                            break
                        except Exception as e:
                            await _log(
                                job.id,
                                f"❌ Ko'chalar ro'yxati ochilmadi "
                                f"({load_attempt + 1}/3): {str(e)[:200]}",
                                level="error",
                            )
                            try:
                                page = await _ensure_batch_session(
                                    page,
                                    ctx,
                                    reauth_steps,
                                    reauth_inputs,
                                    job.id,
                                    session_path,
                                )
                            except Exception:
                                pass
                            await page.wait_for_timeout(1000)
                    if streets_cache is None:
                        break
                    street_list_url_current = list_url
                    state["street_list_url"] = list_url
                    state["streets"] = streets_cache
                    _save_state(job.id, state)
                    current_index = _street_index(
                        streets_cache, current_street_url
                    )
                    if current_index < 0:
                        # Run qayta boshlangan bo'lsa, avvalgi kursordan
                        # davom etamiz — ro'yxat boshidan takrorlanmaydi.
                        cursor = max(
                            0, _as_int(state.get("street_cursor"), 0)
                        )
                        current_index = min(
                            cursor - 1, len(streets_cache) - 1
                        )
                    await _log(
                        job.id,
                        f"🗺 Ro'yxatda {len(streets_cache)} ta ko'cha; "
                        f"{max(0, current_index + 1)} tasi ko'rilgan — "
                        "keyingisidan davom etaman",
                    )

                next_index = current_index + 1
                advanced = False
                refreshed_list = False
                while True:
                    while next_index < len(streets_cache):
                        candidate = streets_cache[next_index]
                        next_index += 1
                        candidate_id = str(candidate.get("id") or "")
                        if candidate_id and candidate_id in visited_streets:
                            continue
                        try:
                            await _goto_street(page, candidate, job.id)
                        except Exception as e:
                            msg = str(e).replace("\n", " ")[:250]
                            await _log(
                                job.id,
                                f"❌ Keyingi ko'cha ochilmadi: {msg}",
                                level="error",
                            )
                            if (
                                "sessiya" in msg.lower()
                                or "login" in msg.lower()
                            ):
                                # Uzoq runlarda sayt sessiyasi tugashi
                                # mumkin. Qayta login qilib, o'sha ko'chani
                                # yana ochamiz — batch to'xtab qolmaydi.
                                await _log(
                                    job.id,
                                    "🔐 Sessiya tugagan — qayta login qilib, "
                                    "ko'chani yana ochaman",
                                )
                                page = await _ensure_batch_session(
                                    page,
                                    ctx,
                                    reauth_steps,
                                    reauth_inputs,
                                    job.id,
                                    session_path,
                                )
                                try:
                                    await _goto_street(
                                        page, candidate, job.id
                                    )
                                except Exception as retry_exc:
                                    retry_msg = str(retry_exc).replace(
                                        "\n", " "
                                    )[:250]
                                    await _log(
                                        job.id,
                                        f"❌ Ko'cha qayta login'dan keyin ham "
                                        f"ochilmadi: {retry_msg}",
                                        level="error",
                                    )
                                    if candidate_id:
                                        visited_streets.add(candidate_id)
                                    continue
                            else:
                                if candidate_id:
                                    visited_streets.add(candidate_id)
                                continue
                        current_index = next_index - 1
                        current_street_url = str(
                            candidate.get("url") or page.url
                        )
                        current_street_name = str(
                            candidate.get("name") or candidate_id
                        )
                        state["current_street_id"] = candidate_id
                        state["current_street_name"] = current_street_name
                        state["current_street_url"] = current_street_url
                        state["street_cursor"] = current_index + 1
                        _save_state(job.id, state)
                        advanced = True
                        break
                    if advanced or refreshed_list:
                        break
                    # Ro'yxatda yangi ko'cha qolmadi. Sayt ro'yxati bir
                    # necha sahifadan iborat bo'lib, avvalgi o'qish
                    # to'liq bo'lmagan bo'lishi mumkin — bir marta
                    # qayta o'qib, listdan listga o'tishni ta'minlaymiz.
                    refreshed_list = True
                    await _log(
                        job.id,
                        "🔄 Ko'chalar ro'yxati qayta tekshiriladi — "
                        "o'qilmagan sahifalar bor-yo'qligini ko'raman…",
                    )
                    try:
                        page = await _ensure_batch_session(
                            page,
                            ctx,
                            reauth_steps,
                            reauth_inputs,
                            job.id,
                            session_path,
                        )
                        await page.goto(
                            street_list_url_current,
                            wait_until="domcontentloaded",
                            timeout=45000,
                        )
                        await page.wait_for_timeout(100)
                        refreshed = await _read_streets(page, job.id)
                    except Exception as e:
                        await _log(
                            job.id,
                            f"⚠️ Ro'yxatni qayta o'qish imkoni bo'lmadi: "
                            f"{str(e)[:200]}",
                            level="error",
                        )
                        refreshed = []
                    fresh = [
                        street
                        for street in refreshed
                        if str(street.get("id") or "")
                        not in visited_streets
                    ]
                    if fresh:
                        streets_cache = refreshed
                        state["streets"] = refreshed
                        _save_state(job.id, state)
                        next_index = 0
                        await _log(
                            job.id,
                            f"🆕 Ro'yxatda {len(fresh)} ta ko'cha "
                            "topildi — avtomatik davom etaman",
                        )
                        continue
                    break

                if not advanced:
                    await _log(
                        job.id,
                        "🏁 Barcha ko'chalar ko'rib chiqildi — yangi "
                        "xonadon qolmadi.",
                    )
                    state["all_streets_done"] = True
                    state["street_cursor"] = len(streets_cache)
                    _save_state(job.id, state)
                    break
            # 3) Yakun
            run.finished_at = datetime.utcnow()
            if incomplete_total > 0 and added_total > 0:
                run.status = JobStatus.FAILED
                run.error = (
                    f"Batch qisman: {added_total} ta yangi odam qo'shildi, "
                    f"{incomplete_total} ta xonadon to'liq emas. "
                    "Supervisor/keyingi run davom ettiradi."
                )
                await db.commit()
                await _status(
                    job.id,
                    run.id,
                    "failed",
                    step=run.current_step,
                    error=run.error,
                )
                await _log(job.id, f"⚠️ {run.error}", level="error")
            elif added_total > 0 or (processed == 0 and skipped_total > 0):
                run.status = JobStatus.COMPLETED
                run.error = None
                await db.commit()
                await _status(
                    job.id, run.id, "completed", step=run.current_step
                )
                summary = (
                    f"✅ Batch tugadi: {added_total} ta yangi odam qo'shildi, "
                    f"{errors_total} ta xato, {skipped_total} ta xonadon "
                    f"avval bajarilgan."
                )
                await _log(job.id, summary)
                if errors_total:
                    await _log(
                        job.id,
                        "⚠️ Ba'zi odamlar/xonadonlar o'tkazib yuborildi — "
                        "loglardagi xatolarni tekshiring.",
                        level="error",
                    )
            else:
                run.status = JobStatus.FAILED
                run.error = (
                    f"Batchda hech qanday odam qo'shilmadi "
                    f"({errors_total} xato). Loglarni tekshiring."
                )
                await db.commit()
                await _status(
                    job.id,
                    run.id,
                    "failed",
                    step=run.current_step,
                    error=run.error,
                )
                await _log(job.id, f"❌ {run.error}", level="error")

            # Har bir run oxirida natijani CSV/JSON/Markdown qilib yozamiz.
            try:
                status_value = (
                    run.status.value
                    if hasattr(run.status, "value")
                    else str(run.status)
                )
                report = await asyncio.to_thread(
                    write_batch_report,
                    job.id,
                    settings.STORAGE_DIR,
                    dataset_db,
                    run.id,
                    status_value,
                )
                target_text = (
                    "cheksiz"
                    if report.get("unlimited")
                    else f"{report.get('target_total', 160)} ta"
                )
                household_text = (
                    "cheksiz"
                    if report.get("unlimited")
                    else str(report.get("target_households", 32))
                )
                await _log(
                    job.id,
                    f"📄 Natija: {report.get('added_total', 0)}/"
                    f"{target_text} | to'liq xonadon: "
                    f"{report.get('complete_households', 0)}/"
                    f"{household_text} | "
                    f"PINFL: {report.get('pinfl_lookups', 0)} | "
                    f"limit: {report.get('rate_limit_hits', 0)}",
                )
                paths = report.get("paths") or {}
                if paths.get("markdown"):
                    await _log(
                        job.id,
                        f"📄 Markdown: {paths['markdown']}",
                    )
                if paths.get("csv"):
                    await _log(job.id, f"📊 CSV: {paths['csv']}")
            except Exception as e:
                print(f"[BATCH] report xatosi: {e}", flush=True)
        finally:
            excel.close()
            await browser.close()


# Bir job uchun bir vaqtda faqat bitta batch run ishlashi kerak — aks
# holda ikkala run bir xil holat faylini va Excel qatorlarini o'zaro
# bosib ketadi (dublikat odamlar, chalkash progress).
_ACTIVE_BATCH_JOBS: set[str] = set()


async def run_batch(
    run: Run,
    job: VideoJob,
    steps: list[WorkflowStep],
    db,
    inputs: dict | None = None,
) -> None:
    """`workers.runner._run`dan chaqiriladigan batch kirish nuqtasi."""
    inputs = inputs if inputs is not None else (run.inputs or {})
    dataset_id = str(inputs.get("DATASET") or "").strip()
    dataset_db = _dataset_db(dataset_id)
    if not os.path.isfile(dataset_db):
        raise RuntimeError(
            f"Excel dataset fayli topilmadi: {dataset_db}. "
            f"Iltimos, avval Excel faylni yuklang va datasetni tanlang."
        )
    if job.id in _ACTIVE_BATCH_JOBS:
        raise RuntimeError(
            "Bu job uchun batch allaqachon ishlamoqda — ikkinchi run "
            "boshlanmadi."
        )
    _ACTIVE_BATCH_JOBS.add(job.id)
    try:
        with prevent_sleep(f"VideoWorkflow batch {job.id[:8]}"):
            await _run_batch(run, job, steps, db, inputs=inputs)
    except Exception as e:
        traceback.print_exc()
        msg = str(e).replace("\n", " ")[:500] or e.__class__.__name__
        run.status = JobStatus.FAILED
        run.error = f"Batch xatosi: {msg}"
        run.finished_at = datetime.utcnow()
        try:
            await db.commit()
        except Exception:
            pass
        try:
            await _status(
                job.id,
                run.id,
                "failed",
                step=run.current_step or 0,
                error=run.error,
            )
        except Exception:
            pass
        try:
            await _log(job.id, f"❌ {run.error}", level="error")
        except Exception:
            pass
        try:
            report_db = _dataset_db(
                str((run.inputs or {}).get("DATASET") or "")
            )
            report = await asyncio.to_thread(
                write_batch_report,
                job.id,
                settings.STORAGE_DIR,
                report_db,
                run.id,
                "failed",
            )
            await _log(
                job.id,
                f"📄 Natija yangilandi: "
                f"{report.get('added_total', 0)}/"
                f"{report.get('target_total', 160)} ta | "
                f"{report.get('paths', {}).get('markdown', '')}",
            )
        except Exception:
            pass
    finally:
        _ACTIVE_BATCH_JOBS.discard(job.id)
        # Run tugagach parolni jarayon xotirasida qoldirmaymiz.
        forget_credentials(job.id)
