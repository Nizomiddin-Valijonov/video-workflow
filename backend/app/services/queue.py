"""Fon vazifalari navbati.

Docker rejimida RQ + Redis ishlatiladi. Docker'siz desktop rejimida
(LOCAL_MODE=true yoki Redis paketi bo'lmasa) vazifalar shu jarayonning
thread pool'ida bajariladi. Shu tufayli sayt bitta kompyuterda
Postgres/Redis o'rnatmasdan ham ishlaydi.
"""
from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.core.config import settings

_local_ids: dict[str, str] = {}
_local_lock = threading.Lock()
_local_executor: ThreadPoolExecutor | None = None

_USE_RQ = False
if not settings.LOCAL_MODE and settings.REDIS_URL:
    try:
        from redis import Redis
        from rq import Queue
        from rq.job import Job as RQJob

        redis_conn = Redis.from_url(
            settings.REDIS_URL,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            decode_responses=True,
        )
        analysis_queue = Queue("analysis", connection=redis_conn)
        run_queue = Queue("runs", connection=redis_conn)
        _USE_RQ = True
    except Exception as exc:  # Redis o'rnatilmagan bo'lsa — desktop rejim
        print(f"[QUEUE] Redis/RQ mavjud emas, local rejimga o'tildi: {exc}", flush=True)


def _executor() -> ThreadPoolExecutor:
    global _local_executor
    if _local_executor is None:
        # Bitta tahlil va bitta workflow bir vaqtda ishlashi mumkin.
        # SQLite WAL rejimi va busy_timeout bir vaqtli yozuvlarni ta'minlaydi.
        _local_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="vw-local"
        )
    return _local_executor


def _local_status(job_id: str, status: str) -> None:
    with _local_lock:
        _local_ids[job_id] = status


def _run_analysis(job_id: str) -> None:
    _local_status(job_id, "started")
    try:
        from workers.analyzer import analyze_job

        analyze_job(job_id)
        _local_status(job_id, "finished")
    except Exception:
        traceback.print_exc()
        _local_status(job_id, "failed")


def _run_workflow(run_id: str) -> None:
    _local_status(run_id, "started")
    try:
        from workers.runner import execute_run

        execute_run(run_id)
        _local_status(run_id, "finished")
    except Exception:
        traceback.print_exc()
        _local_status(run_id, "failed")


def enqueue_analyze(job_id: str) -> str:
    if _USE_RQ:
        job = analysis_queue.enqueue(
            "workers.analyzer.analyze_job",
            job_id,
            job_timeout=settings.WORKER_JOB_TIMEOUT,
            result_ttl=3600,
            failure_ttl=86400,
        )
        return job.id

    local_id = f"local-analysis-{uuid.uuid4()}"
    _local_status(local_id, "queued")
    _executor().submit(_run_analysis, job_id)
    return local_id


def enqueue_run(run_id: str) -> str:
    if _USE_RQ:
        job = run_queue.enqueue(
            "workers.runner.execute_run",
            run_id,
            job_timeout=settings.WORKER_JOB_TIMEOUT,
            result_ttl=3600,
            failure_ttl=86400,
        )
        return job.id

    local_id = f"local-run-{uuid.uuid4()}"
    _local_status(local_id, "queued")
    _executor().submit(_run_workflow, run_id)
    return local_id


def get_rq_status(rq_job_id: str) -> str | None:
    if not _USE_RQ:
        with _local_lock:
            return _local_ids.get(rq_job_id)
    try:
        rq_job = RQJob.fetch(rq_job_id, connection=redis_conn)
        return rq_job.get_status()
    except Exception:
        return None
