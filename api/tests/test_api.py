import os

os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base, engine, get_db  # noqa: E402
from app.main import app  # noqa: E402

# Use the same engine (now SQLite) for testing
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_register_worker():
    resp = client.post(
        "/workers/register",
        json={
            "name": "test-worker-01",
            "hostname": "gpu-node-1",
            "gpus": [
                {"index": 0, "name": "NVIDIA A10", "virt_mode": "mig", "vram_total_mb": 23040}
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "worker_id" in data
    assert "token" in data
    return data


def test_list_workers():
    # register first
    client.post("/workers/register", json={"name": "worker-list-test"})
    resp = client.get("/workers")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_heartbeat():
    reg = client.post("/workers/register", json={"name": "hb-worker"})
    token = reg.json()["token"]
    resp = client.post(
        "/workers/heartbeat",
        json={"gpus": [], "extra": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_create_job():
    resp = client.post(
        "/jobs",
        json={
            "name": "test-inference",
            "image": "nvcr.io/nvidia/pytorch:24.01-py3",
            "command": "python infer.py",
            "gpu_count": 1,
            "gpu_vram_mb": 8192,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"


def test_list_gpus():
    resp = client.get("/inventory/gpus")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_metrics_endpoint():
    resp = client.get("/metrics")
    assert resp.status_code == 200
