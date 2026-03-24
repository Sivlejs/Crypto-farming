# Gunicorn configuration for Nexus AI on Render / VPS
# Uses eventlet async workers required by Flask-SocketIO

import multiprocessing
import os

# ── Binding ───────────────────────────────────────────────────
bind    = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 1                  # 1 worker for SocketIO (eventlet handles concurrency)
worker_class = "eventlet"    # REQUIRED for Flask-SocketIO WebSocket support
threads  = 1

# ── Performance ───────────────────────────────────────────────
worker_connections = 1000    # eventlet greenlet connections
timeout     = 120            # seconds before killing a hung worker
keepalive   = 5              # seconds to keep idle connections open
graceful_timeout = 30        # seconds to finish requests before forced kill

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
