"""HTTP client for the control plane API."""

import logging
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)


class ControlPlaneClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._token: Optional[str] = None
        self._client = httpx.Client(timeout=10.0)

    def register(self, name: str, hostname: str, gpus: List[Dict[str, Any]]) -> str:
        resp = self._client.post(
            f"{self.base_url}/workers/register",
            json={
                "name": name,
                "hostname": hostname,
                "gpus": gpus,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["token"]
        return data["worker_id"]

    def heartbeat(self, gpus: List[Dict[str, Any]], extra: Dict[str, Any] = None) -> bool:
        if not self._token:
            raise RuntimeError("Not registered")
        resp = self._client.post(
            f"{self.base_url}/workers/heartbeat",
            json={"gpus": gpus, "extra": extra or {}},
            headers={"Authorization": f"Bearer {self._token}"},
        )
        if resp.status_code == 200:
            return True
        log.warning("Heartbeat failed: %s %s", resp.status_code, resp.text)
        return False

    def close(self):
        self._client.close()
