"""Yangi paketni «boshqa kompyuter» sharoitida sinab ko'radi.

Paketni vaqtinchalik papkaga ochadi, ichidagi Python runtime bilan
backend'ni ko'taradi (port 8123), /health va bosh sahifani tekshiradi,
so'ng 30 qadamli shablon bazaga yozilganini tasdiqlaydi.
Bu ishlab turgan ilovaga (port 8000) tegmaydi.

Ishlatish (faqat standart kutubxona):
    "%LOCALAPPDATA%\VideoWorkflow\runtime\python\python.exe" -I tools/smoke_test.py dist/<yangi>.zip
    ... -I tools/smoke_test.py <papka>
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, ".build")
PORT = 8123


def log(msg: str) -> None:
    print(msg, flush=True)


def fail(msg: str) -> None:
    log("\nYIQILDI: " + msg)
    raise SystemExit(1)


def free_port(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def prepare(src: str) -> str:
    os.makedirs(BUILD, exist_ok=True)
    dest = os.path.join(BUILD, "smoke")
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    if os.path.isdir(src):
        shutil.copytree(src, dest)
        return dest
    if not os.path.isfile(src):
        fail("manba topilmadi: " + src)
    log("Arxiv ochilmoqda: %s" % os.path.basename(src))
    with zipfile.ZipFile(src) as z:
        z.extractall(dest)
    inner = os.path.join(dest, "vw-win-bundle")
    return inner if os.path.isdir(inner) else dest


def find_python(bundle: str) -> str:
    for rel in ("runtime/python/python.exe", "runtime/python/bin/python3"):
        p = os.path.join(bundle, *rel.split("/"))
        if os.path.isfile(p):
            return p
    fail("paketda Python runtime topilmadi")


def check_files(bundle: str) -> None:
    must = [
        "app/backend/app/main.py",
        "app/backend/workers/runner.py",
        "app/frontend/out/index.html",
        "VideoWorkflow.exe",
    ]
    for rel in must:
        if not os.path.isfile(os.path.join(bundle, *rel.split("/"))):
            fail("paketda yo'q: " + rel)
    runner = os.path.join(bundle, "app", "backend", "workers", "runner.py")
    with open(runner, "r", encoding="utf-8") as fh:
        src = fh.read()
    if "_wait_after_navigation" not in src:
        fail("runner.py'da login-kutish tuzatishi yo'q")
    if "batch_session" in src and "storage" not in src:
        log("  (diqqat: runner.py'da batch_session havolasi bor)")
    log("Fayllar: OK")


def check_no_personal(bundle: str) -> None:
    """Paketdagi kod va ma'lumot papkalarini tekshiradi.

    `runtime/` va `browsers/` chetlab o'tiladi: ular tashqi dasturlar
    (Python, Chromium) va ichida tasodifiy `.db`/`.log` fayllar bo'lishi
    mumkin — bular bizning ma'lumot emas.
    """
    bad = []
    for sub in ("app", "storage", "data", "seed"):
        root_dir = os.path.join(bundle, sub)
        if not os.path.isdir(root_dir):
            continue
        for dirpath, _dirnames, filenames in os.walk(root_dir):
            for name in filenames:
                low = name.lower()
                if (
                    low == ".env"
                    or low.startswith("batch_session")
                    or low.startswith("batch_result")
                    or low.startswith("batch_state")
                    or low == "users.json"
                    or low.endswith((".sqlite", ".db", ".log", ".pid"))
                ):
                    bad.append(os.path.join(dirpath, name))
    if bad:
        fail("shaxsiy/ishchi fayllar paketda: " + ", ".join(bad[:5]))
    log("Shaxsiy ma'lumot: yo'q")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        log(__doc__)
        return 2
    if not free_port(PORT):
        fail("port %d band — avval boshqa sinov jarayonini to'xtating" % PORT)

    bundle = prepare(argv[1])
    log("Paket papkasi: " + bundle)
    check_files(bundle)
    check_no_personal(bundle)

    python = find_python(bundle)
    data = os.path.join(BUILD, "smoke-data")
    storage = os.path.join(BUILD, "smoke-storage")
    # Har safar «toza kompyuter» holatini yaratamiz: eski baza qolsa,
    # sinov yolg'on o'tib ketishi mumkin.
    for stale in (data, storage):
        shutil.rmtree(stale, ignore_errors=True)
    os.makedirs(os.path.join(data, "logs"), exist_ok=True)
    os.makedirs(os.path.join(storage, "uploads"), exist_ok=True)

    env = os.environ.copy()
    env.update({
        "LOCAL_MODE": "true",
        "ENVIRONMENT": "development",
        "DATABASE_URL": "sqlite+aiosqlite:///" + os.path.join(data, "app.db").replace("\\", "/"),
        "STORAGE_DIR": storage,
        "PLAYWRIGHT_BROWSERS_PATH": os.path.join(bundle, "browsers"),
        "PYTHONPATH": os.path.join(bundle, "app", "backend"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "FRONTEND_DIST": os.path.join(bundle, "app", "frontend", "out"),
        "BROWSER_HEADLESS": "true",
        "CORS_ORIGINS": '["http://127.0.0.1:%d"]' % PORT,
        "TRUSTED_HOSTS": '["localhost","127.0.0.1"]',
    })
    log_file = os.path.join(BUILD, "smoke-backend.log")
    log("Backend ko'tarilmoqda (port %d)…" % PORT)
    with open(log_file, "wb") as out:
        proc = subprocess.Popen(
            [python, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
            cwd=os.path.join(bundle, "app", "backend"),
            env=env, stdout=out, stderr=subprocess.STDOUT,
        )
    try:
        health = None
        deadline = time.time() + 120
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:%d/health" % PORT, timeout=3
                ) as resp:
                    health = json.loads(resp.read().decode("utf-8"))
                    break
            except Exception:
                time.sleep(1.0)
        if not health or not health.get("ok"):
            tail = ""
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as fh:
                    tail = fh.read()[-2500:]
            except OSError:
                pass
            fail("backend javob bermadi.\n--- log ---\n" + tail)
        log("Health: %s" % json.dumps(health, ensure_ascii=False))

        with urllib.request.urlopen("http://127.0.0.1:%d/" % PORT, timeout=10) as resp:
            html = resp.read(4000).decode("utf-8", "replace")
        if "<html" not in html.lower():
            fail("bosh sahifa HTML qaytarmadi (FRONTEND_DIST ishlamayapti)")
        log("Bosh sahifa: OK (statik frontend xizmat qilinyapti)")

        db_path = os.path.join(data, "app.db")
        steps = -1
        for _ in range(20):
            try:
                with sqlite3.connect(db_path) as con:
                    steps = con.execute("select count(*) from workflow_steps").fetchone()[0]
                break
            except sqlite3.Error:
                time.sleep(0.5)
        if steps < 1:
            fail("shablon qadamlari bazaga yozilmadi (workflow_steps bo'sh)")
        log("Shablon: %d qadam bazada" % steps)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            proc.kill()

    log("\nBARCHA TEKSHIRUVLAR O'TDI.")
    log("Sinov loglari: " + log_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
