"""Qadamlar (steps) API: ko'rish, tahrirlash, input talablari, qayta tahlil."""
import re
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import VideoJob, WorkflowStep, JobStatus
from app.schemas import StepOut, StepUpdate, InputRequirementsOut, InputRequirement
from app.services.queue import enqueue_analyze

router = APIRouter(tags=["steps"], dependencies=[])


def _input_keys(steps) -> list[InputRequirement]:
    seen: dict[str, InputRequirement] = {}
    for s in steps:
        if not s.value_template:
            continue
        for key in re.findall(r"\{\{\s*(\w+)\s*\}\}", s.value_template):
            if key in seen:
                continue
            seen[key] = InputRequirement(
                key=key,
                hint=s.value_hint or key,
                required=s.input_required,
                example=_example(key, s.value_hint),
            )
    return list(seen.values())


def _example(key: str, hint: str | None) -> str | None:
    t = (hint or key).lower()
    if "email" in t or key in ("email", "mail"):
        return "user@example.com"
    if any(k in t for k in ("parol", "password", "pwd")):
        return "••••••••"
    if "ism" in t or "name" in t:
        return "Ali Valiyev"
    if "tel" in t or "phone" in t or "raqam" in t:
        return "+998 90 123 45 67"
    if "url" in t or "link" in t:
        return "https://example.com"
    return "qiymat"


@router.get("/jobs/{job_id}/steps", response_model=list[StepOut])
async def list_steps(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job topilmadi")
    q = select(WorkflowStep).where(WorkflowStep.job_id == job_id).order_by(WorkflowStep.order_index)
    return (await db.execute(q)).scalars().all()


@router.get("/jobs/{job_id}/input-requirements", response_model=InputRequirementsOut)
async def input_requirements(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job topilmadi")
    q = select(WorkflowStep).where(WorkflowStep.job_id == job_id).order_by(WorkflowStep.order_index)
    steps = (await db.execute(q)).scalars().all()
    return InputRequirementsOut(job_id=job_id, fields=_input_keys(steps))


@router.patch("/steps/{step_id}", response_model=StepOut)
async def update_step(step_id: str, payload: StepUpdate, db: AsyncSession = Depends(get_db)):
    step = await db.get(WorkflowStep, step_id)
    if not step:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Qadam topilmadi")
    for field, value in payload.model_dump(exclude_unset=True).items():
        # action/description/order_index NOT NULL — ularni null bo'lib
        # almashtirib bo'lishdan himoya qilamiz.
        if value is None and field in ("action", "description", "order_index"):
            continue
        setattr(step, field, value)
    if payload.value_template is not None:
        step.input_required = bool(re.search(r"\{\{\s*\w+\s*\}\}", payload.value_template or ""))
    await db.commit()
    await db.refresh(step)
    return step


@router.post("/jobs/{job_id}/reanalyze", status_code=status.HTTP_202_ACCEPTED)
async def reanalyze(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job topilmadi")
    if job.status not in (JobStatus.ANALYZED, JobStatus.FAILED):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Qayta tahlil bo'lmaydi: {job.status}")
    job.status = JobStatus.ANALYZING
    job.error = None
    await db.commit()
    enqueue_analyze(job.id)
    return {"ok": True, "job_id": job.id}
