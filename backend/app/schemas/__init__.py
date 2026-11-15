from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator


class StepBase(BaseModel):
    order_index: int
    action: str
    selector: str | None = None
    element_type: str | None = None
    value_template: str | None = None
    value_hint: str | None = None
    description: str
    confidence: float = 0.0
    input_required: bool = False
    input_key: str | None = None
    meta: dict[str, Any] = {}


class StepOut(StepBase):
    id: str
    job_id: str
    screenshot_path: str | None = None
    model_config = ConfigDict(from_attributes=True)


class JobBase(BaseModel):
    filename: str
    status: str
    error: str | None = None


class JobOut(JobBase):
    id: str
    source: str = "video"
    storage_path: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    duration_sec: float | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    steps: list[StepOut] = []
    model_config = ConfigDict(from_attributes=True)


class JobsList(BaseModel):
    items: list[JobOut]
    total: int


class StepUpdate(BaseModel):
    action: str | None = None
    selector: str | None = None
    element_type: str | None = None
    value_template: str | None = None
    value_hint: str | None = None
    description: str | None = None
    confidence: float | None = None
    input_required: bool | None = None
    input_key: str | None = None
    meta: dict[str, Any] | None = None


class InputRequirement(BaseModel):
    key: str
    hint: str | None = None
    required: bool = True
    example: str | None = None


class InputRequirementsOut(BaseModel):
    job_id: str
    fields: list[InputRequirement]


BrowserType = Literal["chromium", "chrome", "msedge", "firefox", "webkit"]


class RunIn(BaseModel):
    inputs: dict[str, str] = {}
    browser: BrowserType | None = None


class RunOut(BaseModel):
    id: str
    job_id: str
    status: str
    inputs: dict[str, str] = {}
    browser: str = "chromium"
    current_step: int = 0
    total_steps: int = 0
    error: str | None = None
    error_step: int | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None
    model_config = ConfigDict(from_attributes=True)

    @field_validator("inputs", mode="after")
    @classmethod
    def _hide_password(cls, value: dict[str, str]) -> dict[str, str]:
        # OneID paroli API javoblarida ko'rinmasligi kerak.
        if not isinstance(value, dict):
            return value
        if value.get("PASSWORD"):
            masked = dict(value)
            masked["PASSWORD"] = "***"
            return masked
        return value


class RunsList(BaseModel):
    items: list[RunOut]
    total: int


class HealthOut(BaseModel):
    ok: bool
    environment: str
    version: str
