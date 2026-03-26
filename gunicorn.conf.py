# Gunicorn configuration for Nexus AI on Render / VPS
# Uses eventlet async workers required by Flask-SocketIO

import eventlet
eventlet.monkey_patch()

import multiprocessing
import os

# ── Binding ───────────────────────────────────────────────────
# Default port 5000 for Docker/local development.
# Render sets PORT=10000 via render.yaml environment variable.
bind    = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
workers = 1                  # 1 worker for SocketIO (eventlet handles concurrency)
worker_class = "eventlet"    # REQUIRED for Flask-SocketIO WebSocket support
threads  = 1

# ── Performance ───────────────────────────────────────────────
worker_connections = 1000    # eventlet greenlet connections
# Increased timeout to handle blockchain RPC connection latency during startup.
# The health check endpoint returns quickly regardless of agent state, so
# this timeout mainly affects long-running requests (not health checks).
timeout     = 300            # seconds before killing a hung worker
keepalive   = 5              # seconds to keep idle connections open
graceful_timeout = 60        # seconds to finish requests before forced kill

# ── Restart policy ────────────────────────────────────────────
max_requests            = 500   # restart worker after N requests (memory leak guard)
max_requests_jitter     = 50    # add randomness to avoid thundering herd
worker_tmp_dir          = "/dev/shm"   # use shared memory for temp files

# ── Logging ───────────────────────────────────────────────────
accesslog      = "-"         # stdout
errorlog       = "-"         # stderr
loglevel       = os.environ.get("LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(t)s "%(r)s" %(s)s %(b)s %(L)ss'

# ── Process naming ─────────────────────────────────────────────
proc_name = "nexus-ai"
