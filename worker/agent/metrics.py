"""Prometheus metrics server for the worker agent."""

import logging

from prometheus_client import Gauge, start_http_server

log = logging.getLogger(__name__)

GPU_UTIL = Gauge("worker_gpu_utilization_pct", "GPU utilization %", ["gpu_index", "gpu_name"])
GPU_VRAM_USED = Gauge("worker_gpu_vram_used_mb", "GPU VRAM used MB", ["gpu_index", "gpu_name"])
GPU_VRAM_TOTAL = Gauge("worker_gpu_vram_total_mb", "GPU VRAM total MB", ["gpu_index", "gpu_name"])
GPU_POWER = Gauge("worker_gpu_power_w", "GPU power W", ["gpu_index", "gpu_name"])
GPU_TEMP = Gauge("worker_gpu_temperature_c", "GPU temperature °C", ["gpu_index", "gpu_name"])
HEARTBEAT_SUCCESS = Gauge("worker_heartbeat_success", "Last heartbeat succeeded (1/0)")


def update_gpu_metrics(gpus):
    for g in gpus:
        idx = str(g.get("index", 0))
        name = g.get("name", "unknown")
        if g.get("utilization_pct") is not None:
            GPU_UTIL.labels(idx, name).set(g["utilization_pct"])
        if g.get("vram_used_mb") is not None:
            GPU_VRAM_USED.labels(idx, name).set(g["vram_used_mb"])
        if g.get("vram_total_mb") is not None:
            GPU_VRAM_TOTAL.labels(idx, name).set(g["vram_total_mb"])
        if g.get("power_w") is not None:
            GPU_POWER.labels(idx, name).set(g["power_w"])
        if g.get("temperature_c") is not None:
            GPU_TEMP.labels(idx, name).set(g["temperature_c"])


def start(port: int):
    start_http_server(port)
    log.info("Prometheus metrics listening on :%d/metrics", port)
