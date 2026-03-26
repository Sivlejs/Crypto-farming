"""Worker agent entry point."""

import logging
import signal
import socket
import time

from . import gpu_inventory, metrics
from .client import ControlPlaneClient
from .config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

_running = True


def _shutdown(sig, frame):
    global _running
    log.info("Received signal %s, shutting down…", sig)
    _running = False


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)


def run():
    # Start Prometheus metrics server
    metrics.start(settings.metrics_port)

    client = ControlPlaneClient(settings.control_plane_url)
    hostname = socket.gethostname()

    # Initial GPU inventory
    gpus = gpu_inventory.collect()
    log.info("Detected %d GPU(s)", len(gpus))

    # Register with control plane
    worker_id = None
    for attempt in range(10):
        try:
            worker_id = client.register(settings.worker_name, hostname, gpus)
            log.info("Registered as worker %s", worker_id)
            break
        except Exception as exc:
            log.warning("Registration attempt %d failed: %s", attempt + 1, exc)
            time.sleep(min(30, 5 * (attempt + 1)))

    if not worker_id:
        log.error("Could not register with control plane after 10 attempts; exiting")
        return

    last_hb = 0.0
    while _running:
        now = time.time()
        if now - last_hb >= settings.heartbeat_interval_seconds:
            gpus = gpu_inventory.collect()
            metrics.update_gpu_metrics(gpus)
            ok = client.heartbeat(gpus)
            metrics.HEARTBEAT_SUCCESS.set(1 if ok else 0)
            if ok:
                log.debug("Heartbeat OK")
            last_hb = now
        time.sleep(1)

    client.close()
    log.info("Worker agent stopped")


if __name__ == "__main__":
    run()
