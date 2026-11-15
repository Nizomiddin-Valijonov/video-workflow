"""Docker'siz o'rnatish uchun boshlang'ich batch job.

Installer bilan birga keladigan 30 qadamli online-mahalla job'ini SQLite
bazasiga bir marta qo'shadi. Shu tufayli foydalanuvchi yangi video
yuklamasdan ham mavjud batch ishini davom ettira oladi.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from sqlalchemy import func, select

from app.core.config import settings
from app.core.paths import resolve_storage_path
from app.db.session import SessionLocal, init_db
from app.models import JobStatus, VideoJob, WorkflowStep

BATCH_JOB_ID = "65db58f6-078d-4154-9181-102bf765eac7"
BATCH_JOB_BACKUP = "job_65db58f6_steps_save_backup.json"

# Installer bilan keladigan zaxira nusxa. Foydalanuvchi storage papkasida
# bo'lmasa ham, shu yerdan olinadi.
SEED_DIR = os.environ.get("SEED_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seed"
)


def _screenshot_path(order_index: int) -> str | None:
    """Eski job skrinshotlari mavjud bo'lsa, qadamga bog'laydi."""
    path = os.path.join(
        settings.STORAGE_DIR, "screenshots", BATCH_JOB_ID, f"{order_index:03d}.jpg"
    )
    return path if os.path.exists(path) else None


async def seed_local() -> None:
    if not settings.LOCAL_MODE:
        return

    backup_path = os.path.join(settings.STORAGE_DIR, BATCH_JOB_BACKUP)
    if not os.path.exists(backup_path):
        backup_path = os.path.join(SEED_DIR, BATCH_JOB_BACKUP)
    if not os.path.exists(backup_path):
        return

    await init_db()
    async with SessionLocal() as db:
        job = await db.get(VideoJob, BATCH_JOB_ID)
        step_count = 0
        if job is not None:
            step_count = int(
                (
                    await db.execute(
                        select(func.count())
                        .select_from(WorkflowStep)
                        .where(WorkflowStep.job_id == BATCH_JOB_ID)
                    )
                ).scalar()
                or 0
            )
        if job is not None and step_count:
            return

        if job is None:
            job = VideoJob(
                id=BATCH_JOB_ID,
                filename="online-mahalla batch",
                storage_path=os.path.join(
                    settings.STORAGE_DIR, "videos", f"{BATCH_JOB_ID}.mp4"
                ),
                content_type="video/mp4",
                size_bytes=0,
                status=JobStatus.ANALYZED,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(job)
            await db.flush()

        if not step_count:
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    steps = json.load(f)
            except Exception:
                steps = []
            for item in steps if isinstance(steps, list) else []:
                if not isinstance(item, dict):
                    continue
                order_index = int(item.get("order_index") or 0)
                db.add(
                    WorkflowStep(
                        job_id=BATCH_JOB_ID,
                        order_index=order_index,
                        action=str(item.get("action") or "click"),
                        selector=item.get("selector"),
                        element_type=item.get("element_type"),
                        value_template=item.get("value_template"),
                        value_hint=item.get("value_hint"),
                        description=str(item.get("description") or ""),
                        screenshot_path=resolve_storage_path(item.get("screenshot_path"))
                        or _screenshot_path(order_index),
                        confidence=float(item.get("confidence") or 0.0),
                        input_required=bool(item.get("input_required")),
                        input_key=item.get("input_key"),
                        meta=item.get("meta") or {},
                    )
                )
        await db.commit()
