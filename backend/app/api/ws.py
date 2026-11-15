"""Real vaqtli WebSocket (job logs / status)."""
import asyncio
import hmac

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.auth import decode_access_token
from app.core.config import settings
from app.services.events import subscribe, unsubscribe

router = APIRouter()


@router.websocket("/ws/jobs/{job_id}")
async def ws_job(ws: WebSocket, job_id: str, token: str | None = Query(None)):
    await ws.accept()
    # Token berilgan bo'lsa, uni har doim tekshiramiz.
    authorized = False
    if token:
        user_id = decode_access_token(token)
        if not user_id:
            await ws.close(code=1008, reason="Noto'g'ri token")
            return
        authorized = True

    # API kalit (brauzer WebSocket'da header qo'ya olmaydi — query orqali).
    expected_key = settings.API_KEY
    supplied_key = ws.query_params.get("api_key") or ws.headers.get("x-api-key")
    if expected_key and supplied_key and hmac.compare_digest(supplied_key, expected_key):
        authorized = True

    # Production'da yoki API kalit yoqilganda token/kalit majburiy.
    # Desktop (LOCAL_MODE, development) rejimida avvalgidek ochiq qoladi.
    if (settings.ENVIRONMENT == "production" or expected_key) and not authorized:
        await ws.close(code=1008, reason="Avtorizatsiya talab qilinadi")
        return

    q = subscribe(job_id)
    try:
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=25.0)
                await ws.send_json(msg)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping", "job_id": job_id})
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe(job_id, q)
