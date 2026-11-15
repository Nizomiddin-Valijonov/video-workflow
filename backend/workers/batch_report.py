"""Batch natijalarini JSON, CSV va Markdown fayllariga yozadi.

Bu modul faqat stdlib'ga tayanadi — uni runner ichida ham, host'dagi
`scripts/batch_report.py` orqali ham ishlatish mumkin.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
from datetime import datetime

TARGET_PER_HOUSEHOLD = 5
TARGET_HOUSEHOLDS = 32
TARGET_TOTAL = TARGET_PER_HOUSEHOLD * TARGET_HOUSEHOLDS


def _phone_for_row(row_num: int) -> str:
    row = max(2, int(row_num or 2))
    a = 10 + (row // 100) % 90
    b = row % 100
    return f"+998(99)240-{a:02d}-{b:02d}"


def _state_path(storage_dir: str, job_id: str) -> str:
    return os.path.join(storage_dir, f"batch_state_{job_id}.json")


def _load_state(storage_dir: str, job_id: str) -> dict:
    try:
        with open(_state_path(storage_dir, job_id), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_people(excel_db: str, pinpps: list[str]) -> dict[str, dict]:
    """PINPP bo'yicha Excel ma'lumotlarini oladi (400 tadan bo'lib)."""
    pinpps = [p for p in dict.fromkeys(pinpps) if p]
    if not pinpps or not os.path.exists(excel_db):
        return {}
    out: dict[str, dict] = {}
    con = sqlite3.connect(excel_db)
    try:
        for i in range(0, len(pinpps), 400):
            chunk = pinpps[i:i + 400]
            placeholders = ",".join("?" for _ in chunk)
            q = (
                "SELECT row_num, pinpp, full_name, birth_date, passport, "
                f"address, district FROM people WHERE pinpp IN ({placeholders})"
            )
            for row in con.execute(q, chunk):
                out[str(row[1])] = {
                    "row_num": int(row[0] or 0),
                    "pinpp": str(row[1] or ""),
                    "full_name": str(row[2] or ""),
                    "birth_date": str(row[3] or ""),
                    "passport": str(row[4] or ""),
                    "address": str(row[5] or ""),
                    "district": str(row[6] or ""),
                }
    finally:
        con.close()
    return out


def _atomic_write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def build_report(
    job_id: str,
    storage_dir: str,
    excel_db: str,
    run_id: str | None = None,
    status: str | None = None,
) -> dict:
    state = _load_state(storage_dir, job_id)
    # Maqsad joriy run parametrlaridan olinadi. Eski holat fayllarida bu
    # maydonlar bo'lmasa, avvalgi 32×5 qiymatlar ishlatiladi.
    try:
        target_per = max(
            1, int(state.get("target_per_household") or TARGET_PER_HOUSEHOLD)
        )
    except Exception:
        target_per = TARGET_PER_HOUSEHOLD
    raw_households = state.get("target_households")
    if raw_households is None:
        target_households = TARGET_HOUSEHOLDS
    else:
        try:
            target_households = max(0, int(raw_households))
        except Exception:
            target_households = TARGET_HOUSEHOLDS
    unlimited = target_households <= 0
    target_total = 0 if unlimited else target_households * target_per
    households: dict = state.get("households") or {}
    explicit_results = state.get("results") or []
    if not households:
        # Runner holat faylini yozayotganda yoki hali boshlanmaganida
        # bo'sh hisobot bilan mavjud natijani ustidan yozib yubormaymiz.
        return {
            "job_id": job_id,
            "run_id": run_id,
            "status": status,
            "added_total": 0,
            "target_total": target_total,
            "target_households": target_households,
            "unlimited": unlimited,
            "paths": {},
            "error": "state not ready",
        }

    # Aniq saqlangan natijalarni xonadon kaliti bo'yicha indekslaymiz.
    explicit: dict[str, list[dict]] = {}
    for item in explicit_results:
        if not isinstance(item, dict):
            continue
        key = str(item.get("household_key") or item.get("href") or "")
        explicit.setdefault(key, []).append(item)

    all_pinpps: list[str] = []
    for hs in households.values():
        for p in hs.get("pinpps") or []:
            all_pinpps.append(str(p))
    for items in explicit.values():
        for item in items:
            all_pinpps.append(str(item.get("pinpp") or ""))
    people = _load_people(excel_db, all_pinpps)

    entries: list[dict] = []
    household_rows: list[dict] = []
    for idx, (key, hs) in enumerate(households.items(), 1):
        added = int(hs.get("added", 0) or 0)
        pinpps = [str(p) for p in (hs.get("pinpps") or []) if p]
        label = str(hs.get("label") or f"#{idx}")
        href = str(hs.get("href") or key)
        url = (
            "https://online-mahalla.uz" + href
            if href.startswith("/")
            else href
        )
        explicit_items = explicit.get(key, [])
        by_pinpp = {
            str(item.get("pinpp") or ""): item for item in explicit_items
        }
        for pinpp in pinpps:
            item = by_pinpp.get(pinpp, {})
            person = people.get(pinpp, {})
            row_num = int(item.get("row_num") or person.get("row_num") or 0)
            entries.append(
                {
                    "household_index": idx,
                    "household_label": label,
                    "household_href": href,
                    "household_url": url,
                    "pinpp": pinpp,
                    "full_name": item.get("full_name")
                    or person.get("full_name")
                    or "",
                    "birth_date": item.get("birth_date")
                    or person.get("birth_date")
                    or "",
                    "passport": item.get("passport")
                    or person.get("passport")
                    or "",
                    "phone": item.get("phone")
                    or (_phone_for_row(row_num) if row_num else ""),
                    "row_num": row_num,
                    "added_at": item.get("added_at")
                    or hs.get("completed_at")
                    or hs.get("incomplete_at")
                    or "",
                }
            )
        household_rows.append(
            {
                "index": idx,
                "label": label,
                "href": href,
                "url": url,
                "added": added,
                "target": target_per,
                "complete": added >= target_per,
                "impossible": bool(hs.get("impossible")),
                "reason": str(hs.get("reason") or ""),
                "initial_members": hs.get("initial_members"),
                "completed_at": hs.get("completed_at"),
                "incomplete_at": hs.get("incomplete_at"),
            }
        )

    added_total = len(entries)
    complete_households = sum(1 for h in household_rows if h["complete"])
    impossible_rows = [h for h in household_rows if h["impossible"]]
    incomplete = [
        {
            "index": h["index"],
            "label": h["label"],
            "added": h["added"],
            "need": max(0, target_per - h["added"]),
            "reason": h["reason"],
        }
        for h in household_rows
        if not h["complete"] and not h["impossible"]
    ]
    max_possible_total = (
        None
        if unlimited
        else target_total
        - sum(max(0, target_per - h["added"]) for h in impossible_rows)
    )

    report = {
        "job_id": job_id,
        "run_id": run_id,
        "status": status,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target_total": target_total,
        "target_per_household": target_per,
        "target_households": target_households,
        "unlimited": unlimited,
        "added_total": added_total,
        "max_possible_total": max_possible_total,
        "complete_households": complete_households,
        "impossible_households": impossible_rows,
        "households_seen": len(household_rows),
        "incomplete_households": incomplete,
        "registered_skipped": len(state.get("registered_pinpps") or []),
        "invalid_skipped": len(state.get("invalid_pinpps") or []),
        "failed_skipped": len(state.get("failed_pinpps") or []),
        "pinfl_lookups": int(state.get("pinfl_lookups", 0) or 0),
        "rate_limit_hits": int(state.get("rate_limit_hits", 0) or 0),
        "last_row": int(state.get("last_row", 0) or 0),
        "households": household_rows,
        "people": entries,
    }

    base = os.path.join(storage_dir, f"batch_result_{job_id}")
    _atomic_write(
        base + ".json",
        json.dumps(report, ensure_ascii=False, indent=2),
    )

    csv_path = base + ".csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "№",
                "Xonadon",
                "Xonadon URL",
                "F.I.Sh.",
                "JSHSHIR",
                "Tug'ilgan sana",
                "Pasport",
                "Telefon",
                "Excel qatori",
                "Qo'shilgan vaqt",
            ]
        )
        for i, e in enumerate(entries, 1):
            writer.writerow(
                [
                    i,
                    e["household_label"],
                    e["household_url"],
                    e["full_name"],
                    e["pinpp"],
                    e["birth_date"],
                    e["passport"],
                    e["phone"],
                    e["row_num"] or "",
                    e["added_at"],
                ]
            )

    target_goal = (
        f"barcha ko'chalar (cheksiz xonadon × {target_per})"
        if unlimited
        else f"{target_total} ta yangi odam "
        f"({target_households} xonadon × {target_per})"
    )
    added_goal = (
        str(added_total) if unlimited else f"{added_total}/{target_total}"
    )
    complete_goal = (
        str(complete_households)
        if unlimited
        else f"{complete_households}/{target_households}"
    )
    max_note = (
        "" if unlimited else f" (maksimal mumkin: {max_possible_total})"
    )
    lines = [
        "# Batch natijasi",
        "",
        f"- **Job:** `{job_id}`",
        f"- **Run:** `{run_id or '—'}`",
        f"- **Yaratilgan:** {report['generated_at']}",
        f"- **Maqsad:** {target_goal}",
        f"- **Qo'shilgan:** {added_goal}{max_note}",
        f"- **To'liq xonadonlar:** {complete_goal}",
        f"- **Imkonsiz xonadonlar:** {len(impossible_rows)}",
        f"- **PINFL qidiruvlari:** {report['pinfl_lookups']}",
        f"- **Rate-limit javoblari:** {report['rate_limit_hits']}",
        "",
        "## Xonadonlar",
        "",
        "| # | Xonadon | Qo'shilgan | Holat |",
        "|---:|---|---:|---|",
    ]
    for h in household_rows:
        if h["complete"]:
            state_txt = "✅ to'liq"
        elif h["impossible"]:
            state_txt = f"⛔ imkonsiz: {h['reason'] or '20+ a\'zo'}"
        else:
            state_txt = (
                f"⏳ yana {max(0, target_per - h['added'])} ta"
            )
        lines.append(
            f"| {h['index']} | {h['label']} | {h['added']}/"
            f"{target_per} | {state_txt} |"
        )
    lines += [
        "",
        "## Qo'shilgan odamlar",
        "",
        "| # | Xonadon | F.I.Sh. | JSHSHIR | Tug'ilgan | Telefon | Excel |",
        "|---:|---|---|---|---|---|---:|",
    ]
    for i, e in enumerate(entries, 1):
        name = (e["full_name"] or "").replace("|", "\\|")
        lines.append(
            f"| {i} | {e['household_label']} | {name} | {e['pinpp']} | "
            f"{e['birth_date']} | {e['phone']} | {e['row_num'] or ''} |"
        )
    if not entries:
        lines.append("| — | — | Hali odam qo'shilmagan | — | — | — | — |")
    if incomplete:
        lines += ["", "## To'liq bo'lmagan xonadonlar", ""]
        for h in incomplete:
            lines.append(
                f"- {h['label']}: {h['added']}/{target_per} "
                f"(yana {h['need']} ta)"
            )
    if impossible_rows:
        lines += ["", "## Imkonsiz xonadonlar", ""]
        for h in impossible_rows:
            lines.append(
                f"- {h['label']}: {h['added']}/{target_per} — "
                f"{h['reason'] or 'sayt 20 a\'zo limiti'}"
            )
    lines += [
        "",
        "---",
        f"CSV: `{csv_path}`",
        f"JSON: `{base}.json`",
    ]
    md_path = base + ".md"
    _atomic_write(md_path, "\n".join(lines) + "\n")

    enough_households = bool(state.get("all_streets_done")) or (
        not unlimited and len(household_rows) >= target_households
    )
    all_accounted = enough_households and all(
        h["complete"] or h["impossible"] for h in household_rows
    )
    if (not unlimited and added_total >= target_total) or all_accounted:
        _atomic_write(
            os.path.join(storage_dir, "BATCH_DONE.md"),
            "# Batch yakunlandi\n\n"
            f"- Qo'shilgan: {added_goal}{max_note}\n"
            f"- To'liq xonadonlar: {complete_goal}\n"
            f"- Imkonsiz xonadonlar: {len(impossible_rows)}\n"
            f"- Hisobot: `{md_path}`\n",
        )

    report["paths"] = {
        "json": base + ".json",
        "csv": csv_path,
        "markdown": md_path,
    }
    return report


def write_report(
    job_id: str,
    storage_dir: str,
    excel_db: str,
    run_id: str | None = None,
    status: str | None = None,
) -> dict:
    return build_report(job_id, storage_dir, excel_db, run_id, status)
