"""Real vaqtli hodisalar: Redis pub/sub yoki jarayon ichidagi event bus.

Docker rejimida Redis orqali alohida worker'lardan backendga loglar keladi.
Docker'siz rejimda worker thread'lari backendning asosiy event loop'iga
`call_soon_threadsafe` orqali xabar yuboradi.
"""
import asyncio
import json
import time
from collections import defaultdict
from typing import Any

try:
    import redis.asyncio as aioredis
    _HAS_REDIS = True
except Exception:
    _HAS_REDIS = False

from app.core.config import settings


class EventBus:
    def __init__(self) -> None:
        self._local: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._redis: Any = None
        self._listener: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: list[tuple[str, dict]] = []

    def _client(self):
        if settings.LOCAL_MODE:
            return None
        if self._redis is None and _HAS_REDIS:
            self._redis = aioredis.from_url(
                settings.REDIS_URL,
                max_connections=settings.REDIS_MAX_CONNECTIONS,
                decode_responses=True,
            )
        return self._redis

    def subscribe_local(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._local[job_id].append(q)
        return q

    def unsubscribe_local(self, job_id: str, q: asyncio.Queue) -> None:
        if q in self._local.get(job_id, []):
            self._local[job_id].remove(q)

    def _deliver(self, job_id: str, message: dict) -> None:
        message.setdefault("ts", time.strftime("%H:%M:%S"))
        message["job_id"] = job_id
        for q in list(self._local.get(job_id, [])):
            try:
                q.put_nowait(message)
            except Exception:
                pass

    async def publish_local(self, job_id: str, message: dict) -> None:
        self._deliver(job_id, message)

    async def publish_redis(self, channel: str, message: dict) -> None:
        client = self._client()
        if client is None:
            return
        try:
            await client.publish(channel, json.dumps(message, default=str))
        except Exception:
            pass

    async def _redis_listener(self) -> None:
        client = self._client()
        if client is None:
            return
        pubsub = client.pubsub()
        await pubsub.subscribe("vw:events")
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    try:
                        data = json.loads(msg["data"])
                    except Exception:
                        continue
                    jid = data.get("job_id")
                    if jid:
                        await self.publish_local(jid, data)
        except asyncio.CancelledError:
            await pubsub.close()
            raise

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        # Worker thread'lari backend ishga tushishidan oldin yuborgan
        # xabarlarni yetkazamiz.
        pending, self._pending = self._pending, []
        for job_id, message in pending:
            self._deliver(job_id, message)
        if _HAS_REDIS and not settings.LOCAL_MODE:
            self._listener = asyncio.create_task(self._redis_listener())

    async def stop(self) -> None:
        if self._listener:
            self._listener.cancel()
            try:
                await self._listener
            except asyncio.CancelledError:
                pass
            self._listener = None
        self._loop = None

    async def publish(self, job_id: str, message: dict) -> None:
        loop = self._loop
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None

        if loop is not None and loop.is_running():
            if current is loop:
                self._deliver(job_id, message)
            else:
                loop.call_soon_threadsafe(self._deliver, job_id, dict(message))
        elif current is not None:
            # Backend hali start() qilmagan bo'lsa ham o'z loop'ida yetkazamiz.
            self._deliver(job_id, message)
        else:
            self._pending.append((job_id, dict(message)))

        if not settings.LOCAL_MODE:
            await self.publish_redis("vw:events", message)


bus = EventBus()


def subscribe(job_id: str) -> asyncio.Queue:
    return bus.subscribe_local(job_id)


def unsubscribe(job_id: str, q: asyncio.Queue) -> None:
    bus.unsubscribe_local(job_id, q)


async def publish(job_id: str, message: dict) -> None:
    await bus.publish(job_id, message)
