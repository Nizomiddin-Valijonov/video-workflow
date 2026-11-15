"""Excel/CSV ro'yxatini `people` sqlite bazasiga aylantirish.

Yuklangan fayl `storage/uploads/<dataset>.sqlite` ko'rinishida saqlanadi.
Jadval sxemasi mavjud `ХАТЛОВ.sqlite` bilan bir xil bo'lishi kerak:
    row_num, pinpp, surname, name, patronymic, full_name,
    birth_date, passport, address, district
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
import unicodedata
import uuid
from datetime import date, datetime
from typing import Iterable, Iterator

COLUMNS = (
    "row_num",
    "pinpp",
    "surname",
    "name",
    "patronymic",
    "full_name",
    "birth_date",
    "passport",
    "address",
    "district",
)

# Ustun nomlari turli Excel'larda turlicha bo'ladi. Avval aniq moslik,
# keyin qismiy moslik tekshiriladi.
HEADER_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "pinpp",
        (
            "tv_pinpp",
            "pinpp",
            "pinfl",
            "jshshir",
            "жшшир",
            "пинфл",
            "пинпп",
            "inn",
            "инн",
        ),
    ),
    (
        "surname",
        (
            "tv_surnamecirillic",
            "surname",
            "surname_cirillic",
            "familiya",
            "familiyasi",
            "фамилия",
            "фамилияси",
            "last_name",
        ),
    ),
    (
        "name",
        (
            "tv_namecirillic",
            "name",
            "name_cirillic",
            "ism",
            "ismi",
            "имя",
            "исми",
            "first_name",
        ),
    ),
    (
        "patronymic",
        (
            "tv_patronymcirillic",
            "patronymic",
            "patronymic_cirillic",
            "otchestvo",
            "otasining_ismi",
            "отчество",
            "отасининг_исми",
            "middle_name",
        ),
    ),
    (
        "full_name",
        (
            "full_name",
            "fio",
            "фио",
            "fish",
            "f_i_sh",
            "f_i_o",
            "ф_и_ш",
            "ф_и_о",
            "fio_ru",
            "fio_latin",
        ),
    ),
    (
        "birth_date",
        (
            "tv_datebirth",
            "birth_date",
            "birthdate",
            "date_birth",
            "date_of_birth",
            "dob",
            "tugilgan_sana",
            "tugilgan_sanasi",
            "tugilgan_vaqti",
            "tavallud_sanasi",
            "таваллуд_санаси",
            "таваллуд",
            "туғилган_сана",
            "туғилган_санаси",
            "туғилган_вақти",
            "тугилган_сана",
            "тугилган_санаси",
            "дата_рождения",
        ),
    ),
    (
        "passport",
        (
            "tv_serialnumber",
            "passport",
            "passport_number",
            "passport_series",
            "passport_seriyasi",
            "passport_seriyasi_va_raqami",
            "seriya_raqam",
            "seriyasi_raqami",
            "fuqarolik_pasporti",
            "паспорт",
        ),
    ),
    (
        "address",
        (
            "tv_alp_address",
            "address",
            "manzil",
            "manzili",
            "yashash_manzil",
            "yashash_manzili",
            "doimiy_yashash_manzili",
            "адрес",
            "яшаш_манзили",
        ),
    ),
    (
        "district",
        ("dec_district", "district", "tuman", "туман", "rayon", "район"),
    ),
)


def _normalize_header(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = text.replace("\ufeff", "")
    # O'zbekcha apostroflar «tug'ilgan» kabi so'zlarni buzmasin:
    # aks holda ular pastki chiziqqa aylanib, alias bilan mos kelmaydi.
    for quote in ("'", "‘", "’", "`", "ʼ", "ʻ"):
        text = text.replace(quote, "")
    return re.sub(r"[^0-9a-zа-яёўқғҳ]+", "_", text, flags=re.UNICODE).strip("_")


def _clean(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).replace("\ufeff", "").strip()


def _pinpp(value: object) -> str:
    text = _clean(value)
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return re.sub(r"\D+", "", text)


def _birth_date(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    text = _clean(value)
    if not text:
        return ""
    # ISO yoki 2020-01-31T00:00:00 ko'rinishi.
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(3)}.{m.group(2)}.{m.group(1)}"
    # 31/01/2020, 31-01-2020 va 31.01.2020.
    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text)
    if m:
        return f"{int(m.group(1)):02d}.{int(m.group(2)):02d}.{m.group(3)}"
    # Excel ba'zan sanani seriya raqami sifatida beradi.
    if re.fullmatch(r"\d{5}", text):
        try:
            from openpyxl.utils.datetime import from_excel

            dt = from_excel(int(text))
            if isinstance(dt, (datetime, date)):
                return dt.strftime("%d.%m.%Y")
        except Exception:
            pass
    return text


def _field_for_header(header: object) -> str | None:
    key = _normalize_header(header)
    if not key:
        return None
    for field, aliases in HEADER_ALIASES:
        if key in aliases:
            return field
    for field, aliases in HEADER_ALIASES:
        if any(len(alias) >= 5 and alias in key for alias in aliases):
            return field
    return None


def _map_headers(header_row: Iterable[object]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    used: set[str] = set()
    for index, value in enumerate(header_row):
        field = _field_for_header(value)
        if field and field not in used:
            mapping[index] = field
            used.add(field)
    return mapping


def _detect_csv_encoding(sample_bytes: bytes) -> str:
    """UTF-8 ni aniqlaydi; 64 KB kesim ko'p baytli belgini bo'lib qolsa ham.

    Inkremen talqinlovchi (final=False) oxiridagi chala ketma-ketlikni
    e'tiborsiz qoldiradi — aks holda butun fayl noto'g'ri cp1251 deb
    o'qilib, matn buzilardi.
    """
    import codecs

    try:
        codecs.getincrementaldecoder("utf-8-sig")().decode(sample_bytes, final=False)
        return "utf-8-sig"
    except UnicodeDecodeError:
        return "cp1251"


def _csv_rows(path: str) -> Iterator[tuple[int, list[object]]]:
    with open(path, "rb") as raw:
        sample_bytes = raw.read(65536)
    encoding = _detect_csv_encoding(sample_bytes)
    sample = sample_bytes.decode(encoding, errors="replace")
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    with open(path, "r", encoding=encoding, errors="replace", newline="") as f:
        reader = csv.reader(f, dialect)
        for row_num, row in enumerate(reader, 1):
            yield row_num, list(row)


def _xlsx_rows(path: str) -> Iterator[tuple[int, list[object]]]:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError(
            "Excel faylni o'qib bo'lmadi. Fayl buzilgan yoki .xlsx/.xlsm "
            "emas."
        ) from exc
    try:
        sheet = workbook.active
        if sheet is None:
            raise ValueError("Excelda varaq topilmadi")
        for row_num, row in enumerate(sheet.iter_rows(values_only=True), 1):
            yield row_num, list(row)
    finally:
        workbook.close()


def _iter_source(path: str) -> Iterator[tuple[int, list[object]]]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        yield from _csv_rows(path)
    elif ext in (".xlsx", ".xlsm"):
        yield from _xlsx_rows(path)
    else:
        raise ValueError("Faqat .xlsx, .xlsm yoki .csv fayl qabul qilinadi")


def _safe_dataset_id(filename: str) -> str:
    base = os.path.splitext(os.path.basename(filename or ""))[0]
    base = unicodedata.normalize("NFKC", base)
    base = re.sub(r"[^\w.-]+", "_", base, flags=re.UNICODE).strip("._-")
    return (base or "excel")[:80]


def import_people_file(path: str, filename: str, uploads_dir: str) -> dict:
    """Faylni o'qib, yangi sqlite dataset yaratadi.

    Qaytaradi: {"id", "name", "rows", "columns", "skipped"}.
    Xatolik bo'lsa `ValueError` ko'tariladi; yarim yozilgan baza qoldirilmaydi.
    """
    os.makedirs(uploads_dir, exist_ok=True)
    rows_iter = _iter_source(path)

    header_row: list[object] | None = None
    mapping: dict[int, str] = {}
    header_row_num = 0
    for row_num, row in rows_iter:
        if not any(_clean(v) for v in row):
            continue
        candidate = _map_headers(row)
        if len(candidate) >= 2:
            header_row = row
            mapping = candidate
            header_row_num = row_num
            break
        # Sarlavha topilmasa, birinchi bo'sh bo'lmagan qator sarlavha deb
        # qabul qilinadi va aniq xato qaytaramiz.
        if header_row_num == 0:
            header_row = row
            header_row_num = row_num
            mapping = candidate

    if header_row is None or not mapping:
        raise ValueError(
            "Excel sarlavhalari aniqlanmadi. Kamida JSHSHIR va F.I.Sh. "
            "ustunlari bo'lishi kerak."
        )
    required = {
        "pinpp": "JSHSHIR/PINPP",
        "birth_date": "Tug'ilgan sana",
    }
    missing = [
        label
        for field, label in required.items()
        if field not in mapping.values()
    ]
    if missing:
        raise ValueError(
            "Excel'da quyidagi ustunlar topilmadi: "
            + ", ".join(missing)
            + ". Sarlavhalarni tekshiring (masalan: JSHSHIR, "
            "Tug'ilgan sana)."
        )

    dataset_id = _safe_dataset_id(filename)
    final_path = os.path.join(uploads_dir, dataset_id + ".sqlite")
    suffix = 2
    while os.path.exists(final_path):
        dataset_id = f"{_safe_dataset_id(filename)}_{suffix}"
        final_path = os.path.join(uploads_dir, dataset_id + ".sqlite")
        suffix += 1

    tmp_path = os.path.join(uploads_dir, f".upload-{uuid.uuid4().hex}.sqlite")
    con = sqlite3.connect(tmp_path)
    inserted = 0
    skipped = 0
    try:
        con.execute(
            """
            CREATE TABLE people (
                row_num INTEGER PRIMARY KEY,
                pinpp TEXT,
                surname TEXT,
                name TEXT,
                patronymic TEXT,
                full_name TEXT,
                birth_date TEXT,
                passport TEXT,
                address TEXT,
                district TEXT
            )
            """
        )
        insert_sql = (
            f"INSERT INTO people ({','.join(COLUMNS)}) "
            f"VALUES ({','.join('?' for _ in COLUMNS)})"
        )
        batch: list[tuple] = []
        for row_num, row in rows_iter:
            if row_num <= header_row_num:
                continue
            if not any(_clean(v) for v in row):
                continue
            values = {
                field: (row[index] if index < len(row) else None)
                for index, field in mapping.items()
            }
            pinpp = _pinpp(values.get("pinpp"))
            surname = _clean(values.get("surname"))
            name = _clean(values.get("name"))
            patronymic = _clean(values.get("patronymic"))
            full_name = _clean(values.get("full_name"))
            if not full_name:
                full_name = " ".join(
                    part for part in (surname, name, patronymic) if part
                )
            if not pinpp and not full_name:
                skipped += 1
                continue
            batch.append(
                (
                    row_num,
                    pinpp,
                    surname,
                    name,
                    patronymic,
                    full_name,
                    _birth_date(values.get("birth_date")),
                    _clean(values.get("passport")).replace(" ", "").upper(),
                    _clean(values.get("address")),
                    _clean(values.get("district")),
                )
            )
            inserted += 1
            if len(batch) >= 1000:
                con.executemany(insert_sql, batch)
                batch.clear()
        if batch:
            con.executemany(insert_sql, batch)
        # Indekslarni oxirida yaratamiz — 400k qatorli fayl tezroq yuklanadi.
        con.execute("CREATE INDEX idx_people_pinpp ON people(pinpp)")
        con.execute("CREATE INDEX idx_people_address ON people(address)")
        con.commit()
        con.close()
        os.replace(tmp_path, final_path)
    except Exception:
        try:
            con.close()
        except Exception:
            pass
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise

    return {
        "id": dataset_id,
        "name": dataset_id,
        "rows": inserted,
        "columns": sorted(set(mapping.values())),
        "skipped": skipped,
    }
