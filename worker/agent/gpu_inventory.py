"""GPU inventory collection – NVIDIA NVML when available, stubs otherwise."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def _try_nvml() -> Optional[List[Dict[str, Any]]]:
    """Attempt to read GPU info via pynvml."""
    try:
        import pynvml  # type: ignore

        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        gpus = []
        for i in range(count):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode()
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(h)
                util_pct = float(util.gpu)
            except Exception:
                util_pct = None
            try:
                power_w = pynvml.nvmlDeviceGetPowerUsage(h) / 1000.0
            except Exception:
                power_w = None
            try:
                temp = float(pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU))
            except Exception:
                temp = None
            try:
                uuid = pynvml.nvmlDeviceGetUUID(h)
                if isinstance(uuid, bytes):
                    uuid = uuid.decode()
            except Exception:
                uuid = None

            gpus.append(
                {
                    "index": i,
                    "name": name,
                    "uuid": uuid,
                    "virt_mode": "unknown",
                    "vram_total_mb": mem.total // (1024 * 1024),
                    "vram_used_mb": mem.used // (1024 * 1024),
                    "utilization_pct": util_pct,
                    "power_w": power_w,
                    "temperature_c": temp,
                    "mig_profile": None,
                    "vgpu_profile": None,
                }
            )
        pynvml.nvmlShutdown()
        return gpus
    except Exception as exc:
        log.debug("NVML not available: %s", exc)
        return None


def collect() -> List[Dict[str, Any]]:
    """Return list of GPU inventory dicts."""
    gpus = _try_nvml()
    if gpus is not None:
        return gpus
    # Stub: return an empty list if no GPU hardware is present
    log.info("No GPU hardware detected; returning empty inventory")
    return []
