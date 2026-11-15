"""VideoWorkflow Studio uchun toza (shaxsiy ma'lumotsiz) release yig'adi.

Nima qiladi
-----------
1. Oxirgi portable paketdan (`dist/VideoWorkflow-Windows-x64-NoDocker.zip`)
   runtime, browsers va VideoWorkflow.exe ni oladi.
2. Ustiga repo'dagi JORIY kodni qo'yadi:
     backend/      -> app/backend
     frontend/out  -> app/frontend/out
     backend/seed  -> seed/ va storage/job_..._backup.json
     packaging/    -> Install.cmd, Uninstall.cmd, README-Windows.txt
3. storage/ va data/ ni tozalaydi (faqat seed shabloni qoladi).
4. Har qanday shaxsiy fayl (batch_session, batch_result, users.json, .env,
   .sqlite, .log) topsa — YIG'ISHNI TO'XTATADI.
5. Yangi ZIP yozadi va SHA256 beradi.
6. Go bo'lsa, `VideoWorkflow-Setup.exe` ni ham qayta yig'adi.

Ishlatish (faqat standart kutubxona ishlatiladi)
----------------------------------------------
    BUILD-HANDOVER.cmd ni ikki marta bosing
yoki qo'lda:

    "%LOCALAPPDATA%\VideoWorkflow\runtime\python\python.exe" -I tools/build_release.py
    ... -I tools/build_release.py --zip-only      # faqat ZIP
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
BASE_ZIP = os.path.join(DIST, "VideoWorkflow-Windows-x64-NoDocker.zip")
PREFIX = "vw-win-bundle/"

# Bu yo'llar hech qachon paketga tushmasligi kerak.
FORBIDDEN_PARTS = (
    "batch_session", "batch_result", "batch_state", "users.json",
    "batch_supervisor", "diag_", "downloads/",
)
FORBIDDEN_SUFFIX = (".log", ".pid", ".sqlite", ".db", ".bak", ".pyc")

KEEP_FROM_BASE = ("runtime/", "browsers/", "VideoWorkflow.exe")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace("\\", "/")


def fail(msg: str) -> None:
    print("\nXATO: " + msg)
    raise SystemExit(1)


def warn(msg: str) -> None:
    print("  ! " + msg)


def is_forbidden(name: str) -> str | None:
    low = name.replace("\\", "/").lower()
    base = low.rsplit("/", 1)[-1]
    for part in FORBIDDEN_PARTS:
        if part in low:
            return part
    if base == ".env" or (base.startswith(".env.") and not base.endswith(".example")):
        return ".env"
    for suf in FORBIDDEN_SUFFIX:
        if base.endswith(suf):
            return suf
    return None


def iter_repo_backend():
    base = os.path.join(ROOT, "backend")
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [
            d for d in dirnames
            if d not in ("__pycache__", "tests", ".pytest_cache", "storage", "data")
        ]
        for fname in filenames:
            if fname.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(dirpath, fname)
            arc = PREFIX + "app/backend/" + os.path.relpath(full, base).replace("\\", "/")
            if is_forbidden(arc):
                continue
            yield full, arc


def iter_repo_frontend():
    base = os.path.join(ROOT, "frontend", "out")
    if not os.path.isfile(os.path.join(base, "index.html")):
        fail("frontend/out/index.html yo'q — `cd frontend && npm run build` bajaring.")
    for dirpath, _dirnames, filenames in os.walk(base):
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            arc = PREFIX + "app/frontend/out/" + os.path.relpath(full, base).replace("\\", "/")
            yield full, arc


def iter_packaging():
    base = os.path.join(ROOT, "packaging")
    mapping = (
        ("Install.cmd", "Install.cmd"),
        ("Uninstall.cmd", "Uninstall.cmd"),
        ("Sozlamalar.cmd", "Sozlamalar.cmd"),
        ("README-Windows.txt", "README-Windows.txt"),
        # Paket ildizidagi qisqa qo'llanma ham yangilanadi.
        ("README-Windows.txt", "README.txt"),
    )
    for src, arc in mapping:
        full = os.path.join(base, src)
        if os.path.isfile(full):
            yield full, PREFIX + arc


SEED_NAME = "job_65db58f6_steps_save_backup.json"


def add_seed(zout: zipfile.ZipFile) -> int:
    """30 qadamli shablonni toza holatda qo'shadi.

    Skrinshotlarga havolalar olib tashlanadi: ular eski kompyuterning
    `storage/screenshots` papkasiga ishora qiladi va yangi o'rnatishda
    mavjud bo'lmaydi (aks holda UI'da singan rasm chiqadi).
    """
    seed = os.path.join(ROOT, "backend", "seed", SEED_NAME)
    if not os.path.isfile(seed):
        return 0
    with open(seed, "r", encoding="utf-8") as fh:
        steps = json.load(fh)
    cleared = 0
    for item in steps if isinstance(steps, list) else []:
        if isinstance(item, dict) and item.get("screenshot_path"):
            item["screenshot_path"] = None
            cleared += 1
    data = json.dumps(steps, ensure_ascii=False, indent=2).encode("utf-8")
    # Uch joyga yozamiz:
    #   storage/  — seed_local() birinchi qidiradigan joy (o'rnatilgandan keyin)
    #   app/seed/ — seed_local() zaxira yo'li (pp backend'ning ../seed papkasi)
    #   seed/     — qo'lda ko'rish uchun paket ildizida
    for arc in (
        "storage/" + SEED_NAME,
        "app/seed/" + SEED_NAME,
        "seed/" + SEED_NAME,
    ):
        zout.writestr(PREFIX + arc, data, compress_type=zipfile.ZIP_DEFLATED)
    if cleared:
        print("  shablon: %d skrinshot havolasi tozalandi" % cleared)
    return 2


def newest_source_mtime() -> float:
    front = os.path.join(ROOT, "frontend")
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(front):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", "out", ".next", ".git")]
        for fname in filenames:
            if fname.endswith((".tsx", ".ts", ".jsx", ".js", ".css", ".json")):
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(dirpath, fname)))
                except OSError:
                    pass
    return newest


def add_file(zout: zipfile.ZipFile, full: str, arc: str, compress=zipfile.ZIP_DEFLATED):
    with open(full, "rb") as fh:
        data = fh.read()
    zout.writestr(arc, data, compress_type=compress)


def base_entry_allowed(stripped: str) -> bool:
    """Baza arxividan faqat runtime/browsers/exe olinadi.

    Bu yerda faqat shaxsiy ma'lumot bo'lishi mumkin bo'lgan nomlar
    filtrlanadi — runtime ichidagi `.pyc` kabi fayllar saqlanadi
    (aks holda birinchi ishga tushish sekinlashadi).
    """
    low = stripped.replace("\\", "/").lower()
    for part in FORBIDDEN_PARTS:
        if part in low:
            return False
    base = low.rsplit("/", 1)[-1]
    if base == ".env":
        return False
    if base.startswith(".env.") and not base.endswith(".example"):
        return False
    return True


def copy_base_entries(zout: zipfile.ZipFile, zin: zipfile.ZipFile) -> int:
    kept = 0
    for info in zin.infolist():
        name = info.filename
        if name.endswith("/"):
            continue
        stripped = name[len(PREFIX):] if name.startswith(PREFIX) else name
        if not base_entry_allowed(stripped):
            continue
        if not stripped.startswith(KEEP_FROM_BASE):
            continue
        # Yangi ZipInfo yasaymiz: eski bayroqlar (data descriptor) ko'chib
        # qolsa, ayrim arxiv ochuvchilar faylni buzuq deb hisoblaydi.
        dst = zipfile.ZipInfo(stripped, date_time=info.date_time)
        dst.compress_type = info.compress_type
        dst.external_attr = info.external_attr
        zout.writestr(dst, zin.read(name))
        kept += 1
        if kept % 1000 == 0:
            # Uzoq jim turmasin — foydalanuvchi jarayon davom etayotganini ko'rsin.
            print("    … %d fayl ko'chirildi" % kept, flush=True)
    return kept


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip-only", action="store_true", help="Setup.exe ni yig'maslik")
    ap.add_argument("--base", default=BASE_ZIP, help="runtime/browsers olinadigan zip")
    ap.add_argument("--allow-stale-frontend", action="store_true",
                    help="frontend/out manbadan eski bo'lsa ham davom etish")
    ap.add_argument("--smoke", action="store_true",
                    help="yig'ilgandan keyin paketni sinovdan o'tkazish")
    args = ap.parse_args()

    if not os.path.isfile(args.base):
        fail("baza ZIP topilmadi: " + args.base)

    # 1) Tarqatiladigan kodda login-kutish tuzatishi borligini tekshiramiz.
    runner_path = os.path.join(ROOT, "backend", "workers", "runner.py")
    with open(runner_path, "r", encoding="utf-8") as fh:
        runner_src = fh.read()
    if "_wait_after_navigation" not in runner_src:
        fail("backend/workers/runner.py'da login-kutish tuzatishi yo'q — "
             "avval kodni tuzating, keyin paket yig'ing.")
    print("Tekshiruv: runner.py tuzatishi joyida.")

    # 2) Frontend yig'ilmasi manba kodidan eski bo'lmasligi kerak.
    out_index = os.path.join(ROOT, "frontend", "out", "index.html")
    if not os.path.isfile(out_index):
        fail("frontend/out/index.html yo'q — `cd frontend && npm run build` bajaring.")
    src_mtime = newest_source_mtime()
    if src_mtime > os.path.getmtime(out_index) + 1:
        msg = ("frontend/out manba fayllardan eski — avval "
               "`cd frontend && npm run build` bajaring.")
        if not args.allow_stale_frontend:
            fail(msg)
        warn(msg + " (--allow-stale-frontend)")
    print("Tekshiruv: frontend yig'ilmasi manbaga mos.")
    stamp = datetime.now().strftime("%Y-%m-%d")
    out_zip = os.path.join(DIST, "VideoWorkflow-Windows-x64-%s.zip" % stamp)

    print("Baza:", rel(args.base), "(%.0f MB)" % (os.path.getsize(args.base) / 1048576))
    print("Natija:", rel(out_zip))

    tmp = out_zip + ".part"
    if os.path.exists(tmp):
        os.remove(tmp)

    written = []
    t0 = time.time()
    with zipfile.ZipFile(args.base) as zin, zipfile.ZipFile(
        tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6
    ) as zout:
        kept = copy_base_entries(zout, zin)
        print("Bazadan ko'chirildi: %d fayl" % kept)

        groups = [
            ("backend", iter_repo_backend()),
            ("frontend/out", iter_repo_frontend()),
            ("packaging", iter_packaging()),
        ]
        for label, items in groups:
            count = 0
            for full, arc in items:
                add_file(zout, full, arc)
                written.append(arc)
                count += 1
            print("  %-14s %5d fayl" % (label, count))

        add_seed_count = add_seed(zout)
        if add_seed_count:
            print("  %-14s %5d fayl" % ("seed", add_seed_count))

        # Toza storage/data: faqat papka belgilari.
        for extra in ("storage/.keep", "storage/uploads/.keep", "data/.keep"):
            zout.writestr(PREFIX + extra, b"")

        # Yangi kodning tarkibida ruxsat etilmagan narsa yo'qligini tekshiramiz.
        for arc in written:
            bad = is_forbidden(arc)
            if bad:
                os.remove(tmp)
                fail("taqiqlangan fayl: %s (%s)" % (arc, bad))

    os.replace(tmp, out_zip)
    size_mb = os.path.getsize(out_zip) / 1048576
    digest = sha256(out_zip)
    print("\nTAYYOR: %s (%.0f MB, %.0f soniya)" % (rel(out_zip), size_mb, time.time() - t0))
    print("SHA256:", digest)

    # Oldingi ZIP o'rnida qulay nom ham qoldiramiz (README havolalari uchun).
    latest = os.path.join(DIST, "VideoWorkflow-Windows-x64-NoDocker.zip")
    if os.path.abspath(out_zip) != os.path.abspath(latest):
        shutil.copy2(out_zip, latest)
        print("Nusxa:", rel(latest))

    # Setup.exe (agar Go bo'lsa) nazorat summasidan oldin yig'iladi —
    # aks holda SHA256SUMS eski faylni ko'rsatib qoladi.
    setup_ok = False
    if not args.zip_only:
        setup_ok = build_setup(out_zip)

    write_checksums()
    write_dist_readme(out_zip, setup_ok)

    if args.smoke:
        run_smoke(out_zip)
    return 0


def run_smoke(zip_path: str) -> None:
    import sys as _sys
    script = os.path.join(ROOT, "tools", "smoke_test.py")
    print("\n=== SINOV ===")
    res = subprocess.run([_sys.executable, "-I", script, zip_path])
    if res.returncode != 0:
        fail("sinovdan o'tmadi — paketni tarqatmang.")


def write_checksums() -> None:
    path = os.path.join(DIST, "SHA256SUMS-NoDocker.txt")
    lines = []
    for fname in sorted(os.listdir(DIST)):
        # Faqat tarqatiladigan ikki fayl: portable ZIP va Setup.exe.
        # Eski "Update-*"/"Excel-Fix" arxivlari summaga kirmaydi.
        is_release_zip = fname.startswith("VideoWorkflow-Windows-x64-") and fname.endswith(".zip")
        is_setup = fname == "VideoWorkflow-Setup.exe"
        if not (is_release_zip or is_setup):
            continue
        full = os.path.join(DIST, fname)
        if os.path.getsize(full) > 0:
            lines.append("%s *%s" % (sha256(full), fname))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print("Nazorat summalari:", rel(path))


def find_go() -> str | None:
    for exe in ("go.exe", "go"):
        found = shutil.which(exe)
        if found:
            return found
    for candidate in (
        r"C:\Program Files\Go\bin\go.exe",
        r"C:\Go\bin\go.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Go", "bin", "go.exe"),
        os.path.join(os.environ.get("USERPROFILE", ""), "go", "bin", "go.exe"),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def write_dist_readme(zip_path: str, setup_ok: bool) -> None:
    """Tarqatish papkasiga qaysi fayl yuborilishini aniq yozib qo'yadi."""
    zip_name = os.path.basename(zip_path)
    digest = sha256(zip_path)
    lines = [
        "VideoWorkflow Studio — tarqatiladigan paket",
        "=" * 46,
        "",
        "Sana: " + datetime.now().strftime("%Y-%m-%d %H:%M"),
        "",
    ]
    if setup_ok:
        lines += [
            "KIMGADIR YUBORILADIGAN FAYL:",
            "  VideoWorkflow-Setup.exe",
            "",
            "Bu bitta fayl yetarli: ikki marta bosilsa, dastur",
            "%LOCALAPPDATA%\\VideoWorkflow ga o'rnatiladi.",
            "",
            "Zaxira variant: " + zip_name,
        ]
    else:
        lines += [
            "KIMGADIR YUBORILADIGAN FAYL:",
            "  " + zip_name,
            "",
            "Bu ZIP ichida hamma narsa bor (Python runtime, Chromium,",
            "VideoWorkflow.exe). Yuborilgandan keyin qabul qiluvchi:",
            "  1) ZIP'ni ochadi;",
            "  2) Install.cmd ni ikki marta bosadi;",
            "  3) dastur avtomatik ochiladi.",
            "",
            "DIQQAT: papkadagi eski VideoWorkflow-Setup.exe (agar bo'lsa)",
            "eski kodni o'z ichiga oladi — uni YUBORMANG.",
        ]
    lines += [
        "",
        "SHA256 (" + zip_name + "):",
        "  " + digest,
        "",
        "Tekshirish (PowerShell):",
        '  Get-FileHash "' + zip_name + '" -Algorithm SHA256',
        "",
        "To'liq qo'llanma: README-Windows.txt (ZIP ichida ham bor).",
        "",
    ]
    path = os.path.join(DIST, "README.txt")
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("\n".join(lines))
    print("Tarqatish yo'riqnomasi:", rel(path))


def build_setup(portable_zip: str) -> bool:
    go = find_go()
    payload = os.path.join(ROOT, "desktop", "setup", "payload.zip")
    if not go:
        warn("Go topilmadi — Setup.exe qayta yig'ilmadi.")
        warn("Portable ZIP tarqating: ichida Install.cmd bor.")
        stale = os.path.join(DIST, "VideoWorkflow-Setup.exe")
        if os.path.isfile(stale):
            stamp = datetime.fromtimestamp(os.path.getmtime(stale)).strftime("%Y-%m-%d")
            renamed = os.path.join(DIST, "VideoWorkflow-Setup-ESKI-%s.exe" % stamp)
            if not os.path.exists(renamed):
                os.replace(stale, renamed)
                print("  Eski Setup.exe nomlandi (yubormang):", rel(renamed))
        return False

    shutil.copy2(portable_zip, payload)
    print("payload.zip yangilandi (%.0f MB)" % (os.path.getsize(payload) / 1048576))
    out = os.path.join(DIST, "VideoWorkflow-Setup.exe")
    env = os.environ.copy()
    env["GOOS"] = "windows"
    env["GOARCH"] = "amd64"
    cmd = [go, "build", "-tags", "installer", "-ldflags", "-s -w",
           "-o", out, "./setup"]
    print("go build:", " ".join(cmd))
    res = subprocess.run(cmd, cwd=os.path.join(ROOT, "desktop"), env=env,
                         capture_output=True, text=True)
    if res.returncode != 0:
        warn("go build xato berdi:")
        print(res.stdout[-2000:])
        print(res.stderr[-2000:])
        return False
    print("TAYYOR: %s (%.0f MB)" % (rel(out), os.path.getsize(out) / 1048576))
    print("SHA256:", sha256(out))
    return True


if __name__ == "__main__":
    raise SystemExit(main())
