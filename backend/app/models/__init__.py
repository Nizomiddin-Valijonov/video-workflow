import uuid
import enum
from datetime import datetime
from typing import Any
from sqlalchemy import String, DateTime, ForeignKey, Integer, JSON, Text, Enum, Float, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase


class Base(DeclarativeBase): pass


def uuid_str() -> str:
    return str(uuid.uuid4())


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    AWAITING_INPUT = "awaiting_input"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ActionType(str, enum.Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    UPLOAD = "upload"
    WAIT = "wait"
    SUBMIT = "submit"
    NONE = "none"


class VideoJob(Base):
    __tablename__ = "video_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid_str)
    filename: Mapped[str] = mapped_column(String)
    storage_path: Mapped[str] = mapped_column(String)
    content_type: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default=JobStatus.PENDING)
    # "video" — video tahlilidan, "recorded" — brauzerdan yozib olingan.
    source: Mapped[str] = mapped_column(
        String(32), default="video", server_default="video"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    steps: Mapped[list["WorkflowStep"]] = relationship(
        back_populates="job", cascade="all, delete-orphan",
        order_by="WorkflowStep.order_index",
    )
    runs: Mapped[list["Run"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid_str)
    job_id: Mapped[str] = mapped_column(ForeignKey("video_jobs.id"))
    order_index: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String)
    selector: Mapped[str | None] = mapped_column(String, nullable=True)
    element_type: Mapped[str | None] = mapped_column(String, nullable=True)
    value_template: Mapped[str | None] = mapped_column(String, nullable=True)
    value_hint: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(Text)
    screenshot_path: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    input_required: Mapped[bool] = mapped_column(Boolean, default=False)
    input_key: Mapped[str | None] = mapped_column(String, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    job: Mapped[VideoJob] = relationship(back_populates="steps")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=uuid_str)
    job_id: Mapped[str] = mapped_column(ForeignKey("video_jobs.id"))
    status: Mapped[str] = mapped_column(String, default=JobStatus.PENDING)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    browser: Mapped[str] = mapped_column(String(32), default="chromium")
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    total_steps: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_screenshot: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    job: Mapped[VideoJob] = relationship(back_populates="runs")
