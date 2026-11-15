#!/usr/bin/env python3
"""Uzoq muddatli batch nazoratchisi.

Bir marta UI'da login qilingach `batch_session_<job>.json` saqlanadi.
Shu skript runlar tugagach, saqlangan sessiya bilan yangi run boshlaydi
va belgilangan barcha xonadonlarga odam qo'shilguncha davom etadi.
Xonadonlar soni `BATCH_HOUSEHOLDS` env yoki holatdagi maqsaddan olinadi;
`0` — cheksiz (barcha ko'chalar). Ko'chalar tugagach hisobot
BATCH_DONE.md yozadi va skript to'xtaydi.

To'xtatish:  touch storage/batch_supervisor.stop
Holat:       storage/batch_supervisor.log
Sessiya eskirsa: storage/BATCH_NEEDS_LOGIN.txt
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE = os.getenv("VW_STORAGE_DIR") or os.path.join(BASE, "storage")
JOB = os.getenv("VW_JOB_ID") or "65db58f6-078d-4154-9181-102bf765eac7"
API = (os.getenv("VW_API") or "http://localhost:8000/api").rstrip("/")
API_KEY = (os.getenv("VW_API_KEY") or "").strip()
STATE_PATH = os.path.join(STORAGE, f"batch_state_{JOB}.json")
SESSION_PATH = os.path.join(STORAGE, f"batch_session_{JOB}.json")
LOG_PATH = os.path.join(STORAGE, "batch_supervisor.log")
STOP_PATH = os.path.join(STORAGE, "batch_supervisor.stop")
DONE_PATH = os.path.join(STORAGE, "BATCH_DONE.md")
NEEDS_LOGIN_PATH = os.path.join(STORAGE, "BATCH_NEEDS_LOGIN.txt")

DEFAULT_HOUSEHOLDS = max(
    0, int(os.getenv("BATCH_HOUSEHOLDS", "0") or 0)
)
DEFAULT_PEOPLE = max(
    1, int(os.getenv("BATCH_PEOPLE_PER_HOUSEHOLD", "5") or 5)
)
SLEEP_ACTIVE = 60
SLEEP_IDLE = 90
MAX_NO_PROGRESS = 4
MAX_RUN_HOURS = 4.5
REPORT_SCRIPT = os.path.join(BASE, "scripts", "batch_report.py")
STALE_CANCEL_MARK = os.path.join(STORAGE, "batch_supervisor_cancel.mark")


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def api_json(path: str, method: str = "GET", body: dict | None = None):
    data = None
    headers = {"Accept": "application/json"}
    if API_KEY:
        headers["x-api-key"] = API_KEY
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def latest_run() -> dict:
    data = api_json(f"/jobs/{JOB}/runs")
    items = data.get("items") or []
    return items[0] if items else {}


def read_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def target_params() -> tuple[int, int]:
    """Joriy maqsadni holatdan, bo'lmasa env/standartdan oladi.

    `0` xonadon — cheksiz: ko'chalar yoki odamlar tugagach run yakunlanadi.
    """
    state = read_state()
    raw_households = state.get("target_households")
    if raw_households is None:
        households = DEFAULT_HOUSEHOLDS
    else:
        try:
            households = max(0, int(raw_households))
        except Exception:
            households = DEFAULT_HOUSEHOLDS
    try:
        people = int(state.get("target_per_household") or 0)
    except Exception:
        people = 0
    return (
        households,
        people if people > 0 else DEFAULT_PEOPLE,
    )


def progress(target_per: int = 5) -> tuple[int, int, int, int, int, int]:
    state = read_state()
    households = state.get("households") or {}
    added = 0
    completed = 0
    for h in households.values():
        n = int(h.get("added", 0) or 0)
        added += min(n, target_per)
        if n >= target_per:
            completed += 1
    known = (
        len(state.get("registered_pinpps") or [])
        + len(state.get("used_pinpps") or [])
        + len(state.get("invalid_pinpps") or [])
        + len(state.get("failed_pinpps") or [])
    )
    last_row = int(state.get("last_row", 0) or 0)
    lookups = int(state.get("pinfl_lookups", 0) or 0)
    rate_hits = int(state.get("rate_limit_hits", 0) or 0)
    return added, completed, known, last_row, lookups, rate_hits


def generate_report() -> None:
    """Yakuniy natijani CSV/JSON/Markdown qilib yozadi."""
    try:
        result = subprocess.run(
            [sys.executable, REPORT_SCRIPT],
            cwd=BASE,
            capture_output=True,
            text=True,
            timeout=90,
        )
        out = (result.stdout or "").strip()
        if out:
            log("REPORT: " + out.replace("\n", " | ")[:800])
        if result.returncode != 0:
            log(
                "REPORT XATOSI: "
                + (result.stderr or "")[-600:].replace("\n", " ")
            )
    except Exception as e:
        log(f"REPORT XATOSI: {str(e)[:300]}")


def _parse_created_at(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def needs_login(reason: str) -> None:
    try:
        with open(NEEDS_LOGIN_PATH, "w", encoding="utf-8") as f:
            f.write(
                "OneID sessiyasi eskirgan yoki login topilmadi.\n"
                f"Sabab: {reason}\n"
                "Job sahifasida LOGIN/PASSWORDni faqat UI'da kiriting va\n"
                "1 xonadon × 1 odam testini qayta boshlang.\n"
            )
    except Exception:
        pass
    log(f"TO'XTATILDI: {reason}. {NEEDS_LOGIN_PATH} ga yozildi.")


def main() -> None:
    log("Supervisor boshlandi.")
    no_progress = 0
    previous_sig: tuple | None = None
    run_count = 0

    while True:
        if os.path.exists(STOP_PATH):
            log("Stop fayli topildi — supervisor tugadi.")
            return

        if os.path.exists(DONE_PATH):
            log("BATCH_DONE.md topildi — supervisor tugadi.")
            return

        if not os.path.exists(SESSION_PATH):
            # Birinchi UI login hali qilinmagan. Kutamiz.
            log("Sessiya fayli yo'q — UI'da login qilinishini kutaman.")
            time.sleep(120)
            continue

        try:
            latest = latest_run()
        except Exception as e:
            log(f"API xatosi: {str(e)[:200]}")
            time.sleep(SLEEP_ACTIVE)
            continue

        status = str(latest.get("status") or "").lower()
        error = str(latest.get("error") or "").replace("\n", " ")
        run_id = str(latest.get("id") or "")[:8]

        target_households, target_people = target_params()
        unlimited = target_households <= 0
        target_total = 0 if unlimited else target_households * target_people
        added, completed, known, last_row, lookups, rate_hits = progress(
            target_people
        )
        sig = (added, completed, known, last_row, lookups, rate_hits)
        target_text = "cheksiz" if unlimited else str(target_total)
        household_text = "cheksiz" if unlimited else str(target_households)
        log(
            f"Run {run_id or '—'} holati={status or '—'} | "
            f"qo'shilgan={added}/{target_text} | "
            f"xonadon={completed}/{household_text} | known={known} | "
            f"last_row={last_row} | lookup={lookups} | limit={rate_hits}"
        )
        # Har bir tsiklda hisobotni yangilaymiz. Shu bilan barcha
        # xonadonlar tugagan/imkonsiz bo'lsa, BATCH_DONE.md yoziladi va
        # supervisor to'xtaydi.
        try:
            generate_report()
        except Exception as e:
            log(f"Hisobot xatosi: {str(e)[:200]}")
        if os.path.exists(DONE_PATH):
            log("BATCH_DONE.md yozildi — supervisor tugadi.")
            return

        if status in ("pending", "running"):
            # RQ worker 4 soatdan keyin runni to'xtatadi; ba'zi hollarda
            # run qatori "running" bo'lib qolishi mumkin. Uni abadiy
            # kutmaslik uchun eski runni bekor qilamiz.
            created = _parse_created_at(latest.get("created_at", ""))
            age_h = (
                (datetime.now(timezone.utc) - created).total_seconds() / 3600
                if created
                else 0.0
            )
            if age_h > MAX_RUN_HOURS:
                if not os.path.exists(STALE_CANCEL_MARK):
                    try:
                        with open(STALE_CANCEL_MARK, "w", encoding="utf-8") as f:
                            f.write(f"stale run {run_id} age={age_h:.1f}h\n")
                        api_json(f"/runs/{latest.get('id')}/cancel", "POST")
                        log(
                            f"Eski run {run_id} {age_h:.1f} soatdan beri "
                            "running — bekor qildim."
                        )
                    except Exception as e:
                        log(f"Eski runni bekor qilish xatosi: {str(e)[:200]}")
                else:
                    log(f"Eski run {run_id} bekor qilinishini kutaman...")
                time.sleep(SLEEP_ACTIVE)
                continue
            time.sleep(SLEEP_ACTIVE)
            continue

        if "bekor qilindi" in error.lower():
            if os.path.exists(STALE_CANCEL_MARK):
                try:
                    os.remove(STALE_CANCEL_MARK)
                except Exception:
                    pass
                log("Supervisor eski runni bekor qilgan — davom etaman.")
            else:
                log("Foydalanuvchi runni to'xtatgan — supervisor ham tugadi.")
                return

        low = error.lower()
        if (
            "sessiya eskirgan" in low
            or "login va password" in low
            or "login yoki parol xato" in low
            or "avtomatik qayta login" in low
        ):
            needs_login(error[:180] or "login/sessiya")
            return

        if not unlimited and (
            added >= target_total or completed >= target_households
        ):
            log(
                f"Maqsad bajarildi: {added}/{target_total}, "
                f"{completed}/{target_households} xonadon."
            )
            return

        if previous_sig == sig:
            no_progress += 1
        else:
            no_progress = 0
            previous_sig = sig
        if no_progress >= MAX_NO_PROGRESS:
            log(
                f"{MAX_NO_PROGRESS} run davomida progress yo'q — "
                "tekshirish uchun to'xtatildi."
            )
            return

        # Oldingi run terminal holatda. Joriy maqsad bilan yangi runni
        # saqlangan sessiya orqali boshlaymiz. Parol yuborilmaydi.
        state = read_state()
        dataset = str(state.get("dataset") or "").strip()
        last_row = int(state.get("last_row", 0) or 0)
        if dataset and dataset != "ХАТЛОВ":
            # Yangi yuklangan datasetda 262655 emas, davom etilgan qatordan
            # boshlaymiz. Ro'yxat tugasa batch o'zi boshidan aylanadi.
            start_row = str(max(2, last_row + 1)) if last_row else "2"
        else:
            # Eski ХАТЛОВ bazasi tarixan 262655-qatordan boshlanadi.
            start_row = (
                str(max(262655, last_row + 1)) if last_row else "262655"
            )
        start_street = str(state.get("start_street") or "").strip()
        run_inputs = {
            "BATCH_MODE": "true",
            "START_ROW": start_row,
            "HOUSEHOLDS_LIMIT": str(target_households),
            "PEOPLE_PER_HOUSEHOLD": str(target_people),
            "REUSE_SESSION": "true",
        }
        if dataset:
            run_inputs["DATASET"] = dataset
        if start_street:
            # Foydalanuvchi qo'lda tanlagan boshlanish ko'chasi keyingi
            # supervisor runlarida ham saqlanadi.
            run_inputs["START_STREET"] = start_street
        payload = {"inputs": run_inputs}
        try:
            created = api_json(f"/jobs/{JOB}/run", "POST", payload)
            run_count += 1
            log(
                f"Yangi run boshlandi: {str(created.get('id'))[:8]} "
                f"(jami supervisor runlari: {run_count})"
            )
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8")[:300]
            except Exception:
                pass
            log(f"Run yaratish xatosi {e.code}: {body}")
            if "Holat:" in body:
                # Job status analyzed emas; qo'lda tekshirish kerak.
                return
        except Exception as e:
            log(f"Run yaratish xatosi: {str(e)[:200]}")

        time.sleep(SLEEP_IDLE)


if __name__ == "__main__":
    # Supervisor kutilmagan xatoda butunlay o'lib qolmasin: 60s dan keyin
    # qayta ishga tushadi. Natija esa har doim yoziladi.
    while True:
        try:
            main()
            generate_report()
            break
        except Exception:
            log("SUPERVISOR XATOSI:\n" + traceback.format_exc()[-1600:])
            time.sleep(60)
