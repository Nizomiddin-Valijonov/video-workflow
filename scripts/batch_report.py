#!/usr/bin/env python3
"""Host'dan batch natijasini yaratish.

Ishlatish:
    python3 scripts/batch_report.py
"""
from __future__ import annotations

import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# `backend/workers/__init__.py` analyzer (cv2) import qiladi; shuning uchun
# host'da paketni chetlab o'tib, modulni to'g'ridan-to'g'ri yuklaymiz.
sys.path.insert(0, os.path.join(BASE, "backend", "workers"))

from batch_report import write_report  # noqa: E402

JOB = "65db58f6-078d-4154-9181-102bf765eac7"
STORAGE = os.path.join(BASE, "storage")
DEFAULT_EXCEL_DB = os.path.join(STORAGE, "uploads", "ХАТЛОВ.sqlite")


def excel_db_path() -> str:
    """Hisobot uchun oxirgi run ishlatgan datasetni tanlaydi."""
    state_path = os.path.join(STORAGE, f"batch_state_{JOB}.json")
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        dataset = str((state or {}).get("dataset") or "").strip()
    except Exception:
        dataset = ""
    if dataset and "/" not in dataset and "\\" not in dataset and ".." not in dataset:
        candidate = os.path.join(STORAGE, "uploads", dataset + ".sqlite")
        if os.path.isfile(candidate):
            return candidate
    return DEFAULT_EXCEL_DB


def main() -> None:
    report = write_report(JOB, STORAGE, excel_db_path())
    target = (
        "cheksiz"
        if report.get("unlimited")
        else f"{report['target_total']} ta"
    )
    households = (
        "cheksiz"
        if report.get("unlimited")
        else str(report["target_households"])
    )
    print(
        f"NATIJA: {report['added_total']}/{target} | "
        f"to'liq xonadon: {report['complete_households']}/{households} | "
        f"lookup: {report['pinfl_lookups']} | "
        f"rate-limit: {report['rate_limit_hits']}"
    )
    for kind, path in report.get("paths", {}).items():
        print(f"{kind}: {path}")


if __name__ == "__main__":
    main()
