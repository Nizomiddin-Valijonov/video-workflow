"""Excel (xlsx -> sqlite) ro'yxatidan bitta shaxsni qidirish.

Ro'yxatlar `storage/uploads/*.sqlite` ko'rinishida saqlanadi. Har bir bazada
`people` jadvali bo'ladi:
    row_num, pinpp, surname, name, patronymic, full_name,
    birth_date, passport, address, district
"""
import asyncio
import os
import re
import sqlite3
import tempfile

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.core.config import settings
from app.services.excel_import import import_people_file

router = APIRouter(tags=["excel"])

EXCEL_DIR = os.path.join(settings.STORAGE_DIR, "uploads")


def _dataset_path(dataset: str) -> str:
    """Dataset nomini oq ro'yxatdan tekshirib, xavfsiz yo'l qaytaradi."""
    if not dataset or "/" in dataset or "\\" in dataset or ".." in dataset:
        raise HTTPException(400, "Noto'g'ri dataset nomi")
    path = os.path.join(EXCEL_DIR, dataset + ".sqlite")
    if not os.path.isfile(path):
        raise HTTPException(404, "Excel bazasi topilmadi")
    return path


def _connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    # Faqat o'qish — tasodifan ma'lumot o'zgartirilmasin.
    con.execute("PRAGMA query_only=ON")
    return con


@router.post("/excel/upload")
async def upload_excel(file: UploadFile = File(...)):
    """Excel (.xlsx/.xlsm) yoki CSV'ni sqlite datasetga aylantiradi."""
    filename = os.path.basename(file.filename or "")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".xlsx", ".xlsm", ".csv"):
        raise HTTPException(
            400, "Faqat .xlsx, .xlsm yoki .csv fayl yuklash mumkin"
        )

    os.makedirs(EXCEL_DIR, exist_ok=True)
    max_bytes = max(1, settings.MAX_UPLOAD_MB) * 1024 * 1024
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".upload-", suffix=ext, dir=EXCEL_DIR, delete=False
        ) as tmp:
            tmp_path = tmp.name
            total = 0
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        413,
                        f"Fayl juda katta (limit: {settings.MAX_UPLOAD_MB} MB)",
                    )
                tmp.write(chunk)
        if total == 0:
            raise HTTPException(400, "Bo'sh fayl yuklandi")

        try:
            result = await asyncio.to_thread(
                import_people_file, tmp_path, filename, EXCEL_DIR
            )
        except ImportError as exc:
            raise HTTPException(
                503,
                "Excel o'qish uchun openpyxl o'rnatilmagan "
                "(pip install openpyxl)",
            ) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return result
    finally:
        await file.close()
        if tmp_path:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass
            except Exception:
                pass


@router.get("/excel/datasets")
def list_datasets():
    items = []
    if os.path.isdir(EXCEL_DIR):
        for name in sorted(os.listdir(EXCEL_DIR)):
            if not name.lower().endswith(".sqlite"):
                continue
            path = os.path.join(EXCEL_DIR, name)
            try:
                con = _connect(path)
                total = con.execute("SELECT count(*) FROM people").fetchone()[0]
                con.close()
            except Exception:
                continue
            items.append({"id": name[:-7], "name": name[:-7], "rows": total})
    return {"items": items}


@router.get("/excel/lookup")
def lookup(dataset: str, row: int | None = None, pinpp: str | None = None):
    if row is None and not pinpp:
        raise HTTPException(400, "row yoki pinpp kerak")
    path = _dataset_path(dataset)
    con = _connect(path)
    try:
        if pinpp:
            clean = re.sub(r"\s+", "", pinpp)
            cur = con.execute("SELECT * FROM people WHERE pinpp = ? LIMIT 1", (clean,))
        else:
            cur = con.execute("SELECT * FROM people WHERE row_num = ? LIMIT 1", (row,))
        found = cur.fetchone()
        if not found:
            raise HTTPException(404, "Ma'lumot topilmadi")
        return dict(found)
    finally:
        con.close()


@router.get("/excel/search")
def search(dataset: str, q: str, limit: int = Query(20, ge=1, le=50)):
    q = (q or "").strip()
    if not q:
        return {"items": []}
    path = _dataset_path(dataset)
    con = _connect(path)
    try:
        items: list[dict] = []
        clean = re.sub(r"\s+", "", q)

        # 1) JSHSHIR bo'yicha aniq moslik
        if clean.isdigit() and len(clean) >= 10:
            cur = con.execute("SELECT * FROM people WHERE pinpp = ? LIMIT ?", (clean, limit))
            items = [dict(r) for r in cur.fetchall()]

        # 2) Excel qator raqami bo'yicha
        if not items and clean.isdigit():
            cur = con.execute("SELECT * FROM people WHERE row_num = ? LIMIT 1", (int(clean),))
            items = [dict(r) for r in cur.fetchall()]

        # 3) F.I.Sh., JSHSHIR yoki pasport bo'yicha qismiy qidiruv.
        # Ma'lumotlar asosan katta harflarda — kichik harfda kiritilsa ham
        # topish uchun UPPER variantini ham qidiramiz.
        if not items:
            variants = {q, q.upper()}
            clauses = []
            params: list = []
            for term in variants:
                esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                like = f"%{esc}%"
                clauses.append("(full_name LIKE ? ESCAPE '\\' OR pinpp LIKE ? ESCAPE '\\' OR passport LIKE ? ESCAPE '\\')")
                params.extend([like, like, like])
            params.append(limit)
            cur = con.execute(
                "SELECT * FROM people WHERE " + " OR ".join(clauses) +
                " ORDER BY row_num LIMIT ?",
                params,
            )
            items = [dict(r) for r in cur.fetchall()]
        return {"items": items}
    finally:
        con.close()
