from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import create_worker_token, verify_worker_token
from ..database import get_db

router = APIRouter(prefix="/workers", tags=["workers"])


@router.post("/register", response_model=schemas.WorkerRegisterResponse)
def register_worker(req: schemas.WorkerRegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(models.Worker).filter(models.Worker.name == req.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Worker name already registered")

    worker = models.Worker(
        name=req.name,
        token_hash="",  # will be updated after token creation
        hostname=req.hostname,
        ip_address=req.ip_address,
        status=models.WorkerStatus.online,
        last_heartbeat=datetime.utcnow(),
    )
    db.add(worker)
    db.flush()  # get ID

    token = create_worker_token(worker.id)
    from ..auth import hash_token

    worker.token_hash = hash_token(token)

    # Persist GPUs
    for gpu_info in req.gpus:
        gpu = models.GPU(worker_id=worker.id, **gpu_info.model_dump())
        db.add(gpu)

    db.commit()
    db.refresh(worker)
    return schemas.WorkerRegisterResponse(worker_id=worker.id, token=token)


@router.post("/heartbeat", response_model=schemas.HeartbeatResponse)
def heartbeat(
    req: schemas.HeartbeatRequest,
    worker: models.Worker = Depends(verify_worker_token),
    db: Session = Depends(get_db),
):
    worker.last_heartbeat = datetime.utcnow()
    worker.status = models.WorkerStatus.online

    # Update GPU inventory
    db.query(models.GPU).filter(models.GPU.worker_id == worker.id).delete()
    for gpu_info in req.gpus:
        gpu = models.GPU(worker_id=worker.id, **gpu_info.model_dump())
        db.add(gpu)

    db.commit()
    return schemas.HeartbeatResponse(ok=True, timestamp=datetime.utcnow())


@router.get("", response_model=List[schemas.WorkerOut])
def list_workers(db: Session = Depends(get_db)):
    return db.query(models.Worker).all()


@router.get("/{worker_id}", response_model=schemas.WorkerOut)
def get_worker(worker_id: str, db: Session = Depends(get_db)):
    w = db.query(models.Worker).filter(models.Worker.id == worker_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Worker not found")
    return w


@router.get("/{worker_id}/gpus", response_model=List[schemas.GPUOut])
def get_worker_gpus(worker_id: str, db: Session = Depends(get_db)):
    w = db.query(models.Worker).filter(models.Worker.id == worker_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Worker not found")
    return db.query(models.GPU).filter(models.GPU.worker_id == worker_id).all()
