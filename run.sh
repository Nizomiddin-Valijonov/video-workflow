#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "==> VideoWorkflow Studio ishga tushirilmoqda..."

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker topilmadi. Docker Engine yoki Docker Desktop o'rnating."
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 topilmadi ('docker compose' buyrug'i ishlamayapti)."
  exit 1
fi

if [[ ! -f backend/.env ]]; then
  cp backend/.env.example backend/.env
  echo "backend/.env yaratildi. API kalitlarini tekshirib, qayta ishga tushiring."
fi

docker compose up --build -d

echo ""
echo "==> Tayyor!"
echo "   Frontend: http://localhost:3000"
echo "   Backend API: http://localhost:8000"
echo "   API Docs: http://localhost:8000/docs"
echo ""
echo "Loglar ko'rish:   docker compose logs -f"
echo "To'xtatish:       docker compose down"
