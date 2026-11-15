"""Video yuklash, ro'yxat, ko'rish, o'chirish, preview."""
import glob
import os
import re
import shutil
import uuid
import aiofiles
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.paths import resolve_storage_path
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import VideoJob, WorkflowStep, Run, JobStatus
from app.schemas import JobOut, JobsList
from app.services.queue import enqueue_analyze

router = APIRouter(tags=["jobs"], dependencies=[Depends(require_api_key)])

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def _safe_name(name: str) -> str:
    name = os.path.basename(name or "video")
    return _UNSAFE_NAME.sub("_", name)[:120] or "video"


async def _get_job(db: AsyncSession, job_id: str) -> VideoJob | None:
    res = await db.execute(
        select(VideoJob)
        .where(VideoJob.id == job_id)
        .options(selectinload(VideoJob.steps))
    )
    return res.scalars().first()


@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def upload_video(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    if not file.content_type or file.content_type not in settings.ALLOWED_MIME_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Faqat video fayl (mp4/mov/webm/mkv).")

    tmp_path = os.path.join(settings.STORAGE_DIR, "tmp", f"{uuid.uuid4()}.part")
    os.makedirs(os.path.dirname(tmp_path), exist_ok=True)

    size = 0
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    try:
        async with aiofiles.open(tmp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        f"Maksimal {settings.MAX_UPLOAD_MB} MB.",
                    )
                await f.write(chunk)

        ext = os.path.splitext(_safe_name(file.filename or "video.mp4"))[1] or ".mp4"
        job_id = str(uuid.uuid4())
        final_path = os.path.join(settings.STORAGE_DIR, "videos", f"{job_id}{ext}")
        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        os.replace(tmp_path, final_path)
    except HTTPException:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise
    except Exception as exc:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Yuklash xatosi: {exc}")

    job = VideoJob(
        id=job_id, filename=_safe_name(file.filename or "video.mp4"),
        storage_path=final_path, content_type=file.content_type, size_bytes=size,
        status=JobStatus.PENDING,
    )
    db.add(job)
    await db.commit()
    job = await _get_job(db, job_id)
    if not job:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Job yaratilmadi")

    enqueue_analyze(job.id)
    return job


@router.get("/jobs", response_model=JobsList)
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
):
    q = select(VideoJob).options(selectinload(VideoJob.steps))
    if status_filter:
        q = q.where(VideoJob.status == status_filter)
    q = q.order_by(VideoJob.created_at.desc())
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    q = q.offset((page - 1) * page_size).limit(page_size)
    return {"items": (await db.execute(q)).scalars().all(), "total": total}


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await _get_job(db, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job topilmadi")
    return job


@router.get("/jobs/{job_id}/video")
async def get_video(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await _get_job(db, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job topilmadi")
    path = resolve_storage_path(job.storage_path)
    if not path or not os.path.exists(path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video topilmadi")
    return FileResponse(path, media_type=job.content_type or "video/mp4")


@router.get("/jobs/{job_id}/screenshot/{order}")
async def get_screenshot(job_id: str, order: int, db: AsyncSession = Depends(get_db)):
    job = await _get_job(db, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job topilmadi")
    step = await db.execute(
        select(WorkflowStep).where(WorkflowStep.job_id == job_id, WorkflowStep.order_index == order)
    )
    step = step.scalars().first()
    path = resolve_storage_path(step.screenshot_path) if step else None
    if not step or not path or not os.path.exists(path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Screenshot topilmadi")
    return FileResponse(path, media_type="image/jpeg")


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await _get_job(db, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job topilmadi")

    run_ids = (
        await db.execute(select(Run.id).where(Run.job_id == job_id))
    ).scalars().all()

    try:
        path = resolve_storage_path(job.storage_path)
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

    # Video bilan birga tahlil screenshotlari, run natijalari va batch
    # holat fayllari ham o'chiriladi — aks holda ular abadiy qoladi.
    shutil.rmtree(
        os.path.join(settings.STORAGE_DIR, "screenshots", job_id),
        ignore_errors=True,
    )
    for run_id in run_ids:
        shutil.rmtree(
            os.path.join(settings.STORAGE_DIR, "runs", run_id),
            ignore_errors=True,
        )
    for pattern in (
        f"batch_state_{job_id}.json*",
        f"batch_session_{job_id}.json*",
    ):
        for stale in glob.glob(os.path.join(settings.STORAGE_DIR, pattern)):
            try:
                os.remove(stale)
            except OSError:
                pass

    await db.delete(job)
    await db.commit()
    return None
