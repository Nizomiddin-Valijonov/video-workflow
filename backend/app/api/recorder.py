"""Brauzer harakatlarini yozib olish API'si.

Oqim: start → foydalanuvchi ochilgan brauzerda ishlaydi → (ixtiyoriy)
stop orqali shablon sifatida saqlash yoki cancel orqali bekor qilish.
Saqlanganda oddiy `VideoJob(source="recorded")` va `WorkflowStep`
yozuvlari yaratiladi — ular mavjud runner bilan o'ynaladi.
"""
from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import JobStatus, VideoJob, WorkflowStep
from app.schemas import BrowserType, JobOut
from app.services.recorder import RecorderBusy, RecorderError, recorder

router = APIRouter(
    tags=["recorder"], dependencies=[Depends(require_api_key)]
)


class RecordStartIn(BaseModel):
    url: str
    browser: BrowserType | None = None


class RecordStopIn(BaseModel):
    name: str | None = None


def _session_or_404(session_id: str):
    session = recorder.get(session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Yozib olish sessiyasi topilmadi.")
    return session


@router.post("/recorder/start", status_code=status.HTTP_201_CREATED)
async def start_recording(payload: RecordStartIn):
    try:
        session = await recorder.start(payload.url, payload.browser)
    except RecorderBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except RecorderError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Yozib olishni boshlab bo'lmadi: {exc}",
        ) from exc
    data = session.public()
    data["active"] = True
    return data


@router.get("/recorder/active")
async def active_recording():
    session = recorder.active()
    if session is None:
        return {"active": False, "session": None}
    data = session.public()
    data["active"] = True
    return {"active": True, "session": data}


@router.get("/recorder/{session_id}")
async def get_recording(session_id: str):
    session = _session_or_404(session_id)
    data = session.public()
    data["active"] = True
    return data


async def _load_job(db: AsyncSession, job_id: str) -> VideoJob | None:
    result = await db.execute(
        select(VideoJob)
        .where(VideoJob.id == job_id)
        .options(selectinload(VideoJob.steps))
    )
    return result.scalars().first()


@router.post("/recorder/{session_id}/stop", response_model=JobOut)
async def stop_and_save(
    session_id: str,
    payload: RecordStopIn | None = None,
    db: AsyncSession = Depends(get_db),
):
    session = _session_or_404(session_id)
    steps = session.build_steps()
    try:
        await recorder.stop(session_id)
    except RecorderError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    # Sessiya ID'si job ID'si bo'lib qoladi — screenshot papkasi ham shu
    # nom ostida yaratilgan. Juda kam uchraydigan to'qnashuvda yangi ID
    # olib, papkani ko'chiramiz.
    job_id = session.id
    if await db.get(VideoJob, job_id) is not None:
        job_id = str(uuid.uuid4())
        old_dir = os.path.join(settings.STORAGE_DIR, "screenshots", session.id)
        new_dir = os.path.join(settings.STORAGE_DIR, "screenshots", job_id)
        try:
            if os.path.isdir(old_dir):
                shutil.move(old_dir, new_dir)
                for step in steps:
                    path = step.get("screenshot_path")
                    if path and path.startswith(old_dir):
                        step["screenshot_path"] = new_dir + path[len(old_dir):]
        except OSError:
            pass

    host = urlparse(session.start_url).netloc or "brauzer"
    title = (payload.name if payload and payload.name else "").strip()
    if not title:
        title = f"Brauzer yozuvi — {host} — {datetime.now():%d.%m.%Y %H:%M}"
    title = title[:180]

    job = VideoJob(
        id=job_id,
        filename=title,
        storage_path="",
        content_type=None,
        size_bytes=0,
        duration_sec=None,
        status=JobStatus.ANALYZED,
        source="recorded",
        error=None,
    )
    db.add(job)
    for index, step in enumerate(steps):
        db.add(WorkflowStep(
            job_id=job_id,
            order_index=index,
            action=step["action"],
            selector=step.get("selector"),
            element_type=step.get("element_type"),
            value_template=step.get("value_template"),
            value_hint=step.get("value_hint"),
            description=step.get("description") or step["action"],
            screenshot_path=step.get("screenshot_path"),
            confidence=0.9,
            input_required=bool(step.get("input_required")),
            input_key=step.get("input_key"),
            meta=dict(step.get("meta") or {}),
        ))
    await db.commit()

    saved = await _load_job(db, job_id)
    if saved is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Shablon saqlanmadi."
        )
    return saved


@router.delete("/recorder/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_recording(session_id: str):
    try:
        await recorder.cancel(session_id)
    except RecorderError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return None
