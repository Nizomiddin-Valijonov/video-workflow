"""FastAPI ilova punkti (production darajada)."""
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.security import require_api_key, is_rate_limited, make_request_id
from app.db.session import init_db
from app.local_seed import seed_local
from app.api import jobs, steps, runs, ws, auth, excel, recorder as recorder_api
from app.services.events import bus
from app.services.recorder import recorder


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_local()
    await bus.start()
    try:
        yield
    finally:
        # Yopilmagan yozib olish oynalari qolib ketmasligi kerak.
        await recorder.shutdown()
        await bus.stop()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Video yuklash, uzdagi amallarni aniqlash va browser workflow avtomatlashtirish.",
    debug=settings.DEBUG,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Process-Time-ms"],
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.TRUSTED_HOSTS or ["*"],
)

# Desktop rejimda frontend statik yig'ilmasini backend o'zi beradi.
_frontend_dist = Path(settings.FRONTEND_DIST).expanduser() if settings.FRONTEND_DIST else None
_frontend_ready = bool(_frontend_dist and (_frontend_dist / "index.html").is_file())


def _set_security_headers(response, request_id: str, process_ms: float | None = None,
                          allow_docs_cdn: bool = False):
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Desktop oynasi (WebView2) Next.js statik yig'ilmasini xizmat qiladi.
    # Next hydration uchun inline skript uslublari zarur; tashqi tarmoq
    # resurslari baribir bloklangan (`'self'`).
    if allow_docs_cdn:
        # /docs va /redoc Swagger UI/ReDoc'ni jsDelivr'dan yuklaydi.
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data: https://fastapi.tiangolo.com; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
    else:
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "font-src 'self' data:; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none';"
        )
    response.headers["Content-Security-Policy"] = csp
    if process_ms is not None:
        response.headers["X-Process-Time-ms"] = f"{process_ms:.2f}"
    return response


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    request.state.request_id = make_request_id(request)
    try:
        require_api_key(request)
    except HTTPException as exc:
        # Middleware ichida ko'tarilgan HTTPException'ni Starlette 500 ga
        # aylantirib qo'yadi, shuning uchun javobni shu yerda qaytaramiz.
        response = JSONResponse(
            {"detail": exc.detail, "request_id": request.state.request_id},
            status_code=exc.status_code,
        )
        return _set_security_headers(response, request.state.request_id)

    # Chegara kaliti faqat ulanish manzilidan olinadi — `x-api-key`
    # sarlavhasini har so'rovda o'zgartirib limitni chetlab o'tib
    # bo'lmasligi kerak.
    client_id = request.client.host if request.client else "anon"
    if settings.ENVIRONMENT == "production" and not settings.API_KEY and is_rate_limited(client_id):
        response = JSONResponse(
            {"detail": "Chegara oshdi. Keyinroq qaytaring.", "request_id": request.state.request_id},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": "60"},
        )
        return _set_security_headers(response, request.state.request_id)

    start = time.time()

    # Katta yuklamalarni multipart parse qilishdan OLDIN rad etamiz:
    # Starlette butun tanani spool qilgandan keyingina hajmni tekshiradi.
    if request.method == "POST" and request.url.path.rstrip("/") == f"{settings.API_V1_PREFIX}/jobs/upload":
        raw_len = request.headers.get("content-length", "")
        if raw_len.isdigit():
            max_total = settings.MAX_UPLOAD_MB * 1024 * 1024 + 1024 * 1024
            if int(raw_len) > max_total:
                response = JSONResponse(
                    {"detail": f"Maksimal {settings.MAX_UPLOAD_MB} MB.",
                     "request_id": request.state.request_id},
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
                return _set_security_headers(response, request.state.request_id)

    response = await call_next(request)
    docs_path = request.url.path.rstrip("/") in ("/docs", "/redoc")
    return _set_security_headers(
        response,
        request.state.request_id,
        (time.time() - start) * 1000,
        allow_docs_cdn=docs_path,
    )


@app.middleware("http")
async def static_cache_middleware(request: Request, call_next):
    """Statik frontend fayllari uchun keshlash siyosati.

    Next.js `_next/static` fayllari nomida hash bor — ularni abadiy
    keshlash xavfsiz. HTML esa yangilanib turishi kerak.
    """
    response = await call_next(request)
    path = request.url.path
    if (
        _frontend_ready
        and request.method == "GET"
        and not path.startswith(settings.API_V1_PREFIX)
        and path not in ("/health", "/docs", "/redoc", "/openapi.json")
    ):
        content_type = response.headers.get("content-type", "")
        if path.startswith("/_next/static/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif content_type.startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        elif response.status_code == 200:
            response.headers.setdefault("Cache-Control", "public, max-age=3600")
    return response


app.include_router(jobs.router, prefix=settings.API_V1_PREFIX)
app.include_router(steps.router, prefix=settings.API_V1_PREFIX)
app.include_router(runs.router, prefix=settings.API_V1_PREFIX)
app.include_router(auth.router, prefix=settings.API_V1_PREFIX)
app.include_router(excel.router, prefix=settings.API_V1_PREFIX)
app.include_router(recorder_api.router, prefix=settings.API_V1_PREFIX)
app.include_router(ws.router, prefix=settings.API_V1_PREFIX)


if not _frontend_ready:
    @app.get("/", tags=["meta"])
    def root():
        return {"app": settings.APP_NAME, "version": settings.APP_VERSION, "status": "ok"}


@app.get("/health", tags=["meta"])
def health():
    return {"ok": True, "environment": settings.ENVIRONMENT, "version": settings.APP_VERSION}


if _frontend_ready:
    # Barcha API router'lari va /health'dan keyin mount qilinadi, shuning
    # uchun ular soya qilinmaydi. `html=True` — papkalar uchun index.html.
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        {
            "detail": "Ishchi xatolik yuz berdi.",
            "request_id": rid,
            "error": str(exc) if settings.DEBUG else None,
        },
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
