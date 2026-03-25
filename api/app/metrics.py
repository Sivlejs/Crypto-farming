from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from sqlalchemy.orm import Session

from . import models
from .database import SessionLocal

router = APIRouter(tags=["metrics"])

WORKER_ONLINE = Gauge("vgpu_workers_online", "Number of online workers")
GPU_TOTAL = Gauge("vgpu_gpus_total", "Total registered GPUs")
JOBS_TOTAL = Counter("vgpu_jobs_total", "Total jobs submitted", ["status"])
GPU_UTIL = Gauge("vgpu_gpu_utilization_pct", "GPU utilization", ["worker", "gpu_index", "gpu_name"])
GPU_VRAM_USED = Gauge(
    "vgpu_gpu_vram_used_mb", "GPU VRAM used MB", ["worker", "gpu_index", "gpu_name"]
)


@router.get("/metrics")
def metrics():
    db: Session = SessionLocal()
    try:
        workers = db.query(models.Worker).all()
        online = sum(1 for w in workers if w.status == models.WorkerStatus.online)
        WORKER_ONLINE.set(online)

        gpus = db.query(models.GPU).all()
        GPU_TOTAL.set(len(gpus))

        for gpu in gpus:
            w = db.query(models.Worker).filter(models.Worker.id == gpu.worker_id).first()
            wname = w.name if w else "unknown"
            labels = (wname, str(gpu.index), gpu.name)
            if gpu.utilization_pct is not None:
                GPU_UTIL.labels(*labels).set(gpu.utilization_pct)
            if gpu.vram_used_mb is not None:
                GPU_VRAM_USED.labels(*labels).set(gpu.vram_used_mb)
    finally:
        db.close()

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
