import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import relationship

from .database import Base


def _uuid():
    return str(uuid.uuid4())


class WorkerStatus(str, enum.Enum):
    online = "online"
    offline = "offline"
    degraded = "degraded"


class JobStatus(str, enum.Enum):
    pending = "pending"
    assigned = "assigned"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class VirtMode(str, enum.Enum):
    passthrough = "passthrough"
    mig = "mig"
    vgpu = "vgpu"
    unknown = "unknown"


class Worker(Base):
    __tablename__ = "workers"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False, unique=True)
    token_hash = Column(String, nullable=False)
    status = Column(SAEnum(WorkerStatus), default=WorkerStatus.offline)
    last_heartbeat = Column(DateTime, nullable=True)
    hostname = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    extra = Column(JSON, default=dict)

    gpus = relationship("GPU", back_populates="worker", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="worker")


class GPU(Base):
    __tablename__ = "gpus"

    id = Column(String, primary_key=True, default=_uuid)
    worker_id = Column(String, ForeignKey("workers.id"), nullable=False)
    index = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
    uuid = Column(String, nullable=True)
    virt_mode = Column(SAEnum(VirtMode), default=VirtMode.unknown)
    vram_total_mb = Column(Integer, nullable=True)
    vram_used_mb = Column(Integer, nullable=True)
    utilization_pct = Column(Float, nullable=True)
    power_w = Column(Float, nullable=True)
    temperature_c = Column(Float, nullable=True)
    mig_profile = Column(String, nullable=True)
    vgpu_profile = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)

    worker = relationship("Worker", back_populates="gpus")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=_uuid)
    worker_id = Column(String, ForeignKey("workers.id"), nullable=True)
    name = Column(String, nullable=False)
    image = Column(String, nullable=False)
    command = Column(String, nullable=True)
    status = Column(SAEnum(JobStatus), default=JobStatus.pending)
    gpu_count = Column(Integer, default=1)
    gpu_vram_mb = Column(Integer, nullable=True)
    gpu_profile = Column(String, nullable=True)
    env_vars = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    exit_code = Column(Integer, nullable=True)
    logs_url = Column(String, nullable=True)
    extra = Column(JSON, default=dict)

    worker = relationship("Worker", back_populates="jobs")
