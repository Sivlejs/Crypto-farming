from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("/gpus", response_model=List[schemas.GPUOut])
def list_all_gpus(db: Session = Depends(get_db)):
    return db.query(models.GPU).all()
