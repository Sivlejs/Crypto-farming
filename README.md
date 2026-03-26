# vGPU Platform

> A high-performance **virtual GPU compute management platform** for ML inference/training, rendering, video transcoding, and scientific simulation workloads.  
> Supports NVIDIA vGPU, MIG slices, and PCIe passthrough as first-class resource types.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         User / Client                           │
└────────────────────────────┬────────────────────────────────────┘
                             │  HTTPS
              ┌──────────────▼──────────────┐
              │       web/  (Next.js)        │  :3000
              │    Dashboard + UI            │
              └──────────────┬──────────────┘
                             │ REST
              ┌──────────────▼──────────────┐
              │    api/  (FastAPI)           │  :8000
              │  Control Plane              │
              │  • Worker auth & registry   │
              │  • Heartbeat & inventory    │
              │  • Job lifecycle            │
              │  • /metrics (Prometheus)    │
              └────┬──────────────┬─────────┘
                   │              │
          ┌────────▼───┐   ┌──────▼────────┐
          │ PostgreSQL  │   │   Redis       │
          │  :5432      │   │   :6380       │
          └────────────┘   └───────────────┘
                   │
     ┌─────────────▼──────────────────────┐
     │         worker/  (Python agent)    │
     │  GPU Node / VM                     │
     │  • Registers with control plane    │
     │  • Sends GPU inventory heartbeats  │
     │  • NVML metrics (util/mem/power)   │
     │  • /metrics (Prometheus) :9100     │
     └─────────────────────────────────────┘
                   │
     ┌─────────────▼──────────────────────┐
     │      infra/  (Observability)       │
     │  Prometheus :9090  Grafana :3001   │
     └─────────────────────────────────────┘
```

---

## Project Layout

```
Crypto-farming/
├── api/            FastAPI control plane
│   ├── app/        Application code
│   │   ├── main.py
│   │   ├── models.py       SQLAlchemy ORM
│   │   ├── schemas.py      Pydantic schemas
│   │   ├── auth.py         JWT worker tokens
│   │   ├── config.py       Settings (env vars)
│   │   └── routers/        workers, jobs, inventory, health
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
├── worker/         Worker agent (runs on GPU nodes)
│   ├── agent/
│   │   ├── main.py         Entry point
│   │   ├── gpu_inventory.py  NVML/stub GPU collection
│   │   ├── metrics.py      Prometheus gauges
│   │   ├── client.py       Control plane HTTP client
│   │   └── config.py
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
├── web/            Next.js + Tailwind dashboard
│   ├── src/app/    Pages (/, /workers, /jobs)
│   ├── src/components/
│   ├── Dockerfile
│   └── package.json
├── infra/          Local dev infra
│   ├── docker-compose.yml  Postgres + Redis + API + Web + Prometheus + Grafana
│   ├── prometheus/
│   └── grafana/
└── .github/
    └── workflows/ci.yml
```

---

## Local Development

### Prerequisites
- Docker & Docker Compose v2
- Python 3.11+ (for worker)
- Node.js 20+ (for web)

### 1. Start the full stack

```bash
# From repo root
docker compose -f infra/docker-compose.yml up -d

# Services:
#  API:        http://localhost:8000
#  Dashboard:  http://localhost:3000
#  Prometheus: http://localhost:9090
#  Grafana:    http://localhost:3001  (admin / admin)
```

### 2. Run the API locally (without Docker)

```bash
cd api
pip install -r requirements.txt
export DATABASE_URL=postgresql://vgpu:vgpu@localhost:5432/vgpu
uvicorn app.main:app --reload
```

### 3. Run a worker locally

```bash
cd worker
pip install -r requirements.txt

# Configure
export CONTROL_PLANE_URL=http://localhost:8000
export WORKER_NAME=my-gpu-node-01

# Start
python -m agent.main
```

The worker will:
1. Collect GPU inventory (via NVML if NVIDIA GPU is present, else empty)
2. Register with the control plane
3. Send heartbeats every 15s
4. Expose Prometheus metrics on `:9100/metrics`

### 4. Run the web dashboard locally

```bash
cd web
npm install --legacy-peer-deps
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
# Open http://localhost:3000
```

---

## How to Add a Worker

A worker is a Python agent that runs on any GPU node/VM:

1. **Install requirements**:
   ```bash
   pip install -r worker/requirements.txt
   # For NVIDIA GPU metrics, also install pynvml:
   pip install pynvml
   ```

2. **Configure** (environment variables or `.env` file):
   ```env
   CONTROL_PLANE_URL=https://your-api-host:8000
   WORKER_NAME=gpu-node-prod-01     # unique name
   HEARTBEAT_INTERVAL_SECONDS=15
   METRICS_PORT=9100
   ```

3. **Start**:
   ```bash
   python -m agent.main
   ```

4. **Verify** – the worker should appear in the dashboard at `/workers` and in the API at `GET /workers`.

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/metrics` | Prometheus metrics |
| POST | `/workers/register` | Register a new worker |
| POST | `/workers/heartbeat` | Worker heartbeat (auth required) |
| GET | `/workers` | List all workers |
| GET | `/workers/{id}/gpus` | GPU inventory for a worker |
| GET | `/inventory/gpus` | All GPUs across all workers |
| POST | `/jobs` | Submit a GPU compute job |
| GET | `/jobs` | List jobs |
| GET | `/jobs/{id}` | Get job details |
| DELETE | `/jobs/{id}` | Cancel a job |

---

## GPU Resource Types

| Mode | Description |
|------|-------------|
| `passthrough` | Physical GPU passed directly to VM |
| `mig` | NVIDIA Multi-Instance GPU slice |
| `vgpu` | NVIDIA vGPU profile |
| `unknown` | Auto-detected / not yet classified |

---

## Configuration

All services are configured via environment variables:

### API
| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://vgpu:vgpu@localhost:5432/vgpu` | Postgres connection |
| `SECRET_KEY` | `change-me-in-production` | JWT signing key |
| `API_HOST` | `0.0.0.0` | Bind address |
| `API_PORT` | `8000` | Listen port |
| `ENABLE_OTEL` | `false` | Enable OpenTelemetry traces |

### Worker
| Variable | Default | Description |
|----------|---------|-------------|
| `CONTROL_PLANE_URL` | `http://localhost:8000` | API URL |
| `WORKER_NAME` | `worker-default` | Unique worker name |
| `HEARTBEAT_INTERVAL_SECONDS` | `15` | Heartbeat frequency |
| `METRICS_PORT` | `9100` | Prometheus metrics port |
| `ENABLE_OTEL` | `false` | Enable OpenTelemetry traces |

---

## CI/CD

GitHub Actions workflows (`.github/workflows/ci.yml`) run on every push:
- **API**: ruff lint + pytest
- **Worker**: ruff lint + pytest
- **Web**: eslint + next build
- **Compose**: docker compose smoke test
