import hashlib
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .database import get_db

bearer = HTTPBearer()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_worker_token(worker_id: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=settings.worker_token_expire_hours)
    payload = {"sub": worker_id, "exp": expire, "type": "worker"}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def verify_worker_token(
    creds: HTTPAuthorizationCredentials = Security(bearer),
    db: Session = Depends(get_db),
) -> models.Worker:
    token = creds.credentials
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        worker_id: str = payload.get("sub")
        if not worker_id or payload.get("type") != "worker":
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    worker = db.query(models.Worker).filter(models.Worker.id == worker_id).first()
    if not worker:
        raise HTTPException(status_code=401, detail="Worker not found")
    return worker
