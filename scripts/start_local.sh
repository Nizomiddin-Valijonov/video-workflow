#!/usr/bin/env bash
# VideoWorkflow Studio — Docker'siz Linux/macOS ishga tushirish.
#
# Talab: Python 3.11–3.14 va Node.js 20+ o'rnatilgan bo'lishi kerak (bir marta).
# PostgreSQL/Redis/Docker kerak emas: SQLite + jarayon ichidagi navbat.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 14) else 1)' \
    >/dev/null 2>&1
}

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3.12 python3.11 python3.14 python3.13 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && python_ok "$candidate"; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi
if [ -z "$PYTHON_BIN" ] || ! command -v "$PYTHON_BIN" >/dev/null 2>&1 || ! python_ok "$PYTHON_BIN"; then
  echo "XATOLIK: Python 3.11–3.14 topilmadi."
  echo "Ubuntu/Debian: sudo apt install python3.12 python3.12-venv"
  echo "yoki https://www.python.org dan Python 3.12 o'rnating."
  echo "Keyin qayta urinib ko'ring: bash scripts/start_local.sh"
  exit 1
fi

VENV="${VENV:-$ROOT/.venv-local}"
NODE_BIN="${NODE_BIN:-node}"

if ! command -v "$NODE_BIN" >/dev/null 2>&1; then
  echo "XATOLIK: Node.js 20+ topilmadi ($NODE_BIN)."
  echo "https://nodejs.org dan Node.js LTS o'rnating yoki NODE_BIN bilan yo'lni ko'rsating."
  exit 1
fi
NODE_VERSION="$("$NODE_BIN" --version 2>/dev/null || true)"
NODE_MAJOR="${NODE_VERSION#v}"
NODE_MAJOR="${NODE_MAJOR%%.*}"
if ! [[ "$NODE_MAJOR" =~ ^[0-9]+$ ]] || [ "$NODE_MAJOR" -lt 20 ]; then
  echo "XATOLIK: Node.js 20+ kerak (topildi: ${NODE_VERSION:-noma-lum})."
  echo "https://nodejs.org dan Node.js LTS o'rnating yoki NODE_BIN bilan yo'lni ko'rsating."
  exit 1
fi
NODE_PATH_BIN="$(command -v "$NODE_BIN")"
NODE_DIR="$(cd "$(dirname "$NODE_PATH_BIN")" && pwd)"
export PATH="$NODE_DIR:$PATH"
NPM_BIN="${NPM_BIN:-npm}"
if ! command -v "$NPM_BIN" >/dev/null 2>&1; then
  NPM_BIN="$NODE_DIR/npm"
fi
if [ ! -x "$NPM_BIN" ] && ! command -v "$NPM_BIN" >/dev/null 2>&1; then
  echo "XATOLIK: npm topilmadi. Node.js bilan birga o'rnatiladi."
  exit 1
fi

# Ko'chirilgan/buzilgan venv (boshqa kompyuterdan nusxa olingan)ni qayta yaratamiz.
# Broken symlink yoki boshqa Python versiyasiga bog'langan venv ham shu yerga tushadi.
if [ -d "$VENV" ]; then
  if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c 'import sys' >/dev/null 2>&1; then
    echo "Eski virtual muhit ishlamayapti — qayta yaratilmoqda: $VENV"
    rm -rf "$VENV"
  fi
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "Python virtual muhiti yaratilmoqda: $VENV"
  "$PYTHON_BIN" -m venv "$VENV"
fi

if ! "$VENV/bin/python" -c 'import fastapi, uvicorn, sqlalchemy, aiosqlite, playwright, cv2, openpyxl' >/dev/null 2>&1; then
  echo "Python kutubxonalari o'rnatilmoqda (bir marta)..."
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install -r backend/requirements-local.txt
fi

# Brauzerlar loyiha ichiga o'rnatiladi; tizim keshi bulg'anmaydi.
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$ROOT/.playwright-browsers}"
read -r -a browser_list <<< "${PLAYWRIGHT_BROWSERS:-chromium firefox webkit}"
if [ "${INSTALL_PLAYWRIGHT_DEPS:-0}" = "1" ]; then
  echo "Playwright tizim kutubxonalari o'rnatilmoqda (root so'ralishi mumkin)..."
  "$VENV/bin/python" -m playwright install-deps "${browser_list[@]}"
fi
if [ "${INSTALL_PLAYWRIGHT_BROWSERS:-1}" = "1" ]; then
  echo "Playwright brauzerlari tekshirilmoqda: ${browser_list[*]}"
  "$VENV/bin/python" -m playwright install "${browser_list[@]}"
fi

install_frontend_deps() {
  if [ ! -d frontend/node_modules ]; then
    if [ -f frontend/package-lock.json ]; then
      (cd frontend && "$NPM_BIN" ci)
    else
      (cd frontend && "$NPM_BIN" install)
    fi
  fi
}

# Boshqa OS/arxitekturaga ko'chirilganda eski `.next` va `node_modules`
# ishlatilmasligi uchun yig'ilish belgisi saqlanadi.
BUILD_STAMP_FILE="$ROOT/frontend/.next/.vw-build-stamp"
BUILD_STAMP="$(uname -s 2>/dev/null || echo unknown)-$(uname -m 2>/dev/null || echo unknown)-node${NODE_MAJOR}"
needs_frontend_build=0
if [ ! -f frontend/.next/standalone/server.js ]; then
  needs_frontend_build=1
fi
if [ ! -f "$BUILD_STAMP_FILE" ] || [ "$(cat "$BUILD_STAMP_FILE" 2>/dev/null)" != "$BUILD_STAMP" ]; then
  needs_frontend_build=1
fi

if [ "$needs_frontend_build" = "1" ]; then
  echo "Frontend yig'ilmoqda (bir marta, bir necha daqiqa)..."
  install_frontend_deps
  if ! (cd frontend && "$NPM_BIN" run build); then
    echo "Frontend yig'ish xato berdi. node_modules boshqa OS'dan ko'chirilgan bo'lishi mumkin — qayta o'rnatilmoqda..."
    rm -rf frontend/node_modules
    install_frontend_deps
    (cd frontend && "$NPM_BIN" run build)
  fi
  mkdir -p frontend/.next
  printf '%s' "$BUILD_STAMP" > "$BUILD_STAMP_FILE"
fi
mkdir -p frontend/.next/standalone/.next/static
cp -a frontend/.next/static/. frontend/.next/standalone/.next/static/
if [ -d frontend/public ]; then
  mkdir -p frontend/.next/standalone/public
  cp -a frontend/public/. frontend/.next/standalone/public/
fi

if [ ! -f backend/.env ]; then
  cp backend/.env.local.example backend/.env
  SECRET="$("$VENV/bin/python" - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
  SECRET="$SECRET" "$VENV/bin/python" - <<'PY'
import os
from pathlib import Path
path = Path("backend/.env")
text = path.read_text(encoding="utf-8")
path.write_text(
    text.replace("SECRET_KEY=local-change-me", "SECRET_KEY=" + os.environ["SECRET"]),
    encoding="utf-8",
)
PY
  echo "backend/.env yaratildi. AI kalitini kerak bo'lsa qo'shing."
fi

mkdir -p data storage
export LOCAL_MODE=true
export ENVIRONMENT=development
export DATABASE_URL="sqlite+aiosqlite:///$ROOT/data/app.db"
export STORAGE_DIR="$ROOT/storage"
export PYTHONPATH="$ROOT/backend"
export PYTHONDONTWRITEBYTECODE=1
export CORS_ORIGINS='["http://localhost:3000","http://127.0.0.1:3000"]'
export TRUSTED_HOSTS='["localhost","127.0.0.1","*"]'
export BROWSER_HEADLESS=true
# .env dagi Windows uchun mo'ljallangan kanal Linux/macOS'da ishlamaydi.
# Foydalanuvchi BROWSER_CHANNEL=... bilan ataylab bersa, o'sha qoladi.
if [ -z "${BROWSER_CHANNEL+x}" ]; then
  export BROWSER_CHANNEL=""
fi

# Tizimda o'rnatilgan Chrome/Chromium bo'lsa ishlatamiz.
if [ -z "${BROWSER_EXECUTABLE:-}" ]; then
  for c in google-chrome google-chrome-stable chromium chromium-browser; do
    if command -v "$c" >/dev/null 2>&1; then
      export BROWSER_EXECUTABLE="$(command -v "$c")"
      break
    fi
  done
fi
if [ -z "${BROWSER_EXECUTABLE:-}" ] && [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
  export BROWSER_EXECUTABLE="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
fi

BACK_LOG="$ROOT/data/local_backend.log"
FRONT_LOG="$ROOT/data/local_frontend.log"

http_ok() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS "$1" >/dev/null 2>&1
    return
  fi
  "$VENV/bin/python" - "$1" >/dev/null 2>&1 <<'PY'
import sys
import urllib.request

with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
    response.read(64)
PY
}

echo "Backend ishga tushirilmoqda..."
(cd backend && "$VENV/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info) >"$BACK_LOG" 2>&1 &
BACK_PID=$!
echo "Frontend ishga tushirilmoqda..."
(cd frontend/.next/standalone && PORT=3000 HOSTNAME=127.0.0.1 NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 "$NODE_BIN" server.js) >"$FRONT_LOG" 2>&1 &
FRONT_PID=$!

cleanup() {
  echo
  echo "To'xtatilmoqda..."
  kill "$FRONT_PID" "$BACK_PID" 2>/dev/null || true
  wait "$FRONT_PID" "$BACK_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

ready=0
for _ in $(seq 1 45); do
  if http_ok http://127.0.0.1:8000/health && http_ok http://127.0.0.1:3000/; then
    ready=1
    break
  fi
  sleep 2
done

if [ "$ready" != "1" ]; then
  echo "Sayt tayyor bo'lmadi. Loglar:"
  tail -30 "$BACK_LOG" || true
  tail -30 "$FRONT_LOG" || true
  exit 1
fi

echo
echo "Sayt tayyor: http://localhost:3000"
echo "Backend:     http://127.0.0.1:8000/docs"
echo "To'xtatish:  Ctrl+C"
if [ "${NO_BROWSER:-0}" != "1" ] && command -v xdg-open >/dev/null 2>&1; then
  xdg-open http://localhost:3000 >/dev/null 2>&1 || true
fi

# Sayt jarayonlari ishlashda davom etadi; Ctrl+C ularni to'xtatadi.
wait "$BACK_PID" "$FRONT_PID"
