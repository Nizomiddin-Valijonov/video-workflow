"""Xavfsizroq so'rovlar bilan ishlash: API kalit, rate limit, request id."""
import hmac
import time
import uuid
from collections import defaultdict, deque
from fastapi import Request, HTTPException, status
from app.auth import decode_access_token
from app.core.config import settings

# Oddiy in-memory rate limiter (prod'da Redis + slowapi tavsiya etiladi)
_clients: dict[str, deque] = defaultdict(deque)

# Birinchi 1 sonliqlikda (burst) qancha so'rovlik ruxsat berilganini belgilaydi.
BURST_WINDOW_SEC = 1.0

# Brauzer UI'si token ololishi va hujjatlar ochilishi uchun.
# Boshqa barcha endpointlar production'da API kalit yoki JWT talab qiladi.
PUBLIC_PATHS = frozenset({
    "/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/login",
    "/api/register",
})


def is_rate_limited(client_id: str) -> bool:
    now = time.time()
    window = 60.0
    dq = _clients[client_id]
    while dq and now - dq[0] > window:
        dq.popleft()
    if len(dq) >= settings.RATE_LIMIT_PER_MINUTE:
        return True
    if len(dq) >= settings.RATE_LIMIT_BURST and now - dq[0] <= BURST_WINDOW_SEC:
        return True
    dq.append(now)
    return False


def _request_token(request: Request) -> str | None:
    """Authorization header yoki (media GET uchun) ?token= qiymatini oladi."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token
    if request.method == "GET":
        token = (request.query_params.get("token") or "").strip()
        if token:
            return token
    return None


def require_api_key(request: Request):
    if settings.ENVIRONMENT != "production" or not settings.API_KEY:
        return
    if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
        return

    key = request.headers.get("x-api-key")
    if key and hmac.compare_digest(key, settings.API_KEY):
        return

    token = _request_token(request)
    if token and decode_access_token(token):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Noto'g'ri yoki yo'q x-api-key",
    )


def make_request_id(request: Request) -> str:
    return request.headers.get("x-request-id") or str(uuid.uuid4())
