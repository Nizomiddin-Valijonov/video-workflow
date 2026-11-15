"""Workflow ishchirish (runs) API."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models import VideoJob, Run, JobStatus
from app.schemas import RunIn, RunOut, RunsList
from app.services.browser import normalize_browser
from app.services.events import publish
from app.services.queue import enqueue_run
from app.services.run_secrets import forget as forget_run_secrets
from app.services.run_secrets import remember as remember_run_secrets
from app.services.run_secrets import split as split_run_secrets

router = APIRouter(tags=["runs"], dependencies=[])


def _clear_secrets(run: Run) -> None:
    """Parolni yakuniy holatda bazada qoldirmaymiz."""
    inputs = getattr(run, "inputs", None)
    if isinstance(inputs, dict) and inputs.get("PASSWORD"):
        updated = dict(inputs)
        updated["PASSWORD"] = ""
        run.inputs = updated


@router.post("/jobs/{job_id}/run", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def start_run(job_id: str, payload: RunIn, db: AsyncSession = Depends(get_db)):
    # Job'ni steps bilan eager-load qilish
    q = await db.execute(
        select(VideoJob).where(VideoJob.id == job_id).options(selectinload(VideoJob.steps))
    )
    job = q.scalar_one_or_none()
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job topilmadi")
    if job.status != JobStatus.ANALYZED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Holat: {job.status}")
    if not job.steps:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Qadamlar yo'q")

    # Bir job uchun bir vaqtda faqat bitta faol run bo'lishi mumkin:
    # ikki nusxa bir xil formani parallel to'ldirib, natijani buzadi.
    active = await db.execute(
        select(Run).where(
            Run.job_id == job_id,
            Run.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
        ).limit(1)
    )
    if active.scalars().first():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Bu job uchun run allaqachon ishlamoqda. Avval uni to'xtating.",
        )

    try:
        browser = normalize_browser(payload.browser)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # LOGIN/PASSWORD bazaga yozilmaydi — faqat jarayon xotirasida
    # saqlanadi va run tugagach o'chiriladi.
    public_inputs, secret_inputs = split_run_secrets(payload.inputs)
    run = Run(
        job_id=job.id,
        inputs=public_inputs,
        browser=browser,
        status=JobStatus.PENDING,
        total_steps=len(job.steps)
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    remember_run_secrets(run.id, secret_inputs)

    # enqueue_run ni try/except bilan o'rab, xatoni logga yozamiz
    try:
        result = enqueue_run(run.id)
        print(f"✅ Run {run.id} navbatga qo'shildi: {result}", flush=True)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ enqueue_run xato: {e}", flush=True)
        # Navbatga qo'shilmagan run abadiy PENDING bo'lib qolmasin.
        run.status = JobStatus.FAILED
        run.error = f"Navbatga qo'shishda xato: {e}"
        run.finished_at = datetime.utcnow()
        _clear_secrets(run)
        forget_run_secrets(run.id)
        await db.commit()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Navbatga qo'shishda xato: {e}"
        )

    return run


@router.get("/jobs/{job_id}/runs", response_model=RunsList)
async def list_runs(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job topilmadi")
    q = select(Run).where(Run.job_id == job_id).order_by(Run.created_at.desc())
    runs = (await db.execute(q)).scalars().all()
    return {"items": runs, "total": len(runs)}


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    run = await db.get(Run, run_id)
    if not run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run topilmadi")
    return run


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, db: AsyncSession = Depends(get_db)):
    run = await db.get(Run, run_id)
    if not run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run topilmadi")
    if run.status in (JobStatus.COMPLETED, JobStatus.FAILED):
        return {"ok": False, "detail": "Allaqachon tugagan"}
    run.status = JobStatus.FAILED
    run.error = "Foydalanuvchi tomonidan bekor qilindi."
    run.finished_at = datetime.utcnow()
    _clear_secrets(run)
    await db.commit()
    # Ishchi o'zining keyingi tekshiruvida run.status=FAILED ko'radi va
    # to'xtaydi; frontend esa darhol xabar oladi.
    try:
        await publish(run.job_id, {
            "type": "run_status", "run_id": run.id, "status": "cancelled",
            "step": run.current_step, "error": run.error,
        })
    except Exception:
        pass
    return {"ok": True}