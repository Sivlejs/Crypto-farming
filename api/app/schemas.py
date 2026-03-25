from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .models import JobStatus, VirtMode, WorkerStatus

# ── Worker schemas ──────────────────────────────────────────────────────────


class GPUInfo(BaseModel):
    index: int
    name: str
    uuid: Optional[str] = None
    virt_mode: VirtMode = VirtMode.unknown
    vram_total_mb: Optional[int] = None
    vram_used_mb: Optional[int] = None
    utilization_pct: Optional[float] = None
    power_w: Optional[float] = None
    temperature_c: Optional[float] = None
    mig_profile: Optional[str] = None
    vgpu_profile: Optional[str] = None


class WorkerRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    gpus: List[GPUInfo] = Field(default_factory=list)


class WorkerRegisterResponse(BaseModel):
    worker_id: str
    token: str


class HeartbeatRequest(BaseModel):
    gpus: List[GPUInfo] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)


class HeartbeatResponse(BaseModel):
    ok: bool
    timestamp: datetime


class WorkerOut(BaseModel):
    id: str
    name: str
    status: WorkerStatus
    last_heartbeat: Optional[datetime]
    hostname: Optional[str]
    ip_address: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class GPUOut(BaseModel):
    id: str
    worker_id: str
    index: int
    name: str
    uuid: Optional[str]
    virt_mode: VirtMode
    vram_total_mb: Optional[int]
    vram_used_mb: Optional[int]
    utilization_pct: Optional[float]
    power_w: Optional[float]
    temperature_c: Optional[float]
    mig_profile: Optional[str]
    vgpu_profile: Optional[str]
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Job schemas ─────────────────────────────────────────────────────────────


class JobCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    image: str = Field(..., min_length=1)
    command: Optional[str] = None
    gpu_count: int = Field(1, ge=1)
    gpu_vram_mb: Optional[int] = None
    gpu_profile: Optional[str] = None
    env_vars: Dict[str, str] = Field(default_factory=dict)


class JobOut(BaseModel):
    id: str
    name: str
    image: str
    command: Optional[str]
    status: JobStatus
    worker_id: Optional[str]
    gpu_count: int
    gpu_vram_mb: Optional[int]
    gpu_profile: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    exit_code: Optional[int]

    model_config = {"from_attributes": True}


# ── Health ──────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    version: str = "0.1.0"
