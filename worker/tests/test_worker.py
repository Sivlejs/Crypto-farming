from unittest.mock import MagicMock, patch


def test_gpu_inventory_no_nvml():
    """Inventory should return empty list when NVML is unavailable."""
    from agent.gpu_inventory import collect

    with patch("agent.gpu_inventory._try_nvml", return_value=None):
        result = collect()
    assert result == []


def test_gpu_inventory_with_nvml():
    """Inventory should return GPU data when NVML succeeds."""
    fake_gpu = {
        "index": 0,
        "name": "NVIDIA A10",
        "uuid": "GPU-abc123",
        "virt_mode": "unknown",
        "vram_total_mb": 23040,
        "vram_used_mb": 1024,
        "utilization_pct": 42.0,
        "power_w": 150.0,
        "temperature_c": 65.0,
        "mig_profile": None,
        "vgpu_profile": None,
    }
    from agent.gpu_inventory import collect

    with patch("agent.gpu_inventory._try_nvml", return_value=[fake_gpu]):
        result = collect()
    assert len(result) == 1
    assert result[0]["name"] == "NVIDIA A10"


def test_client_register():
    """Client should POST to /workers/register and store token."""
    from agent.client import ControlPlaneClient

    mock_response = MagicMock()
    mock_response.json.return_value = {"worker_id": "wid-123", "token": "tok-abc"}
    mock_response.raise_for_status = MagicMock()

    client = ControlPlaneClient("http://localhost:8000")
    with patch.object(client._client, "post", return_value=mock_response):
        wid = client.register("test-worker", "host1", [])
    assert wid == "wid-123"
    assert client._token == "tok-abc"


def test_client_heartbeat():
    """Client should POST to /workers/heartbeat and return True on success."""
    from agent.client import ControlPlaneClient

    mock_response = MagicMock()
    mock_response.status_code = 200

    client = ControlPlaneClient("http://localhost:8000")
    client._token = "tok-abc"
    with patch.object(client._client, "post", return_value=mock_response):
        ok = client.heartbeat([])
    assert ok is True


def test_metrics_update():
    """metrics.update_gpu_metrics should set prometheus gauges without error."""
    from agent import metrics as m

    gpus = [
        {
            "index": 0,
            "name": "NVIDIA A100",
            "utilization_pct": 75.0,
            "vram_used_mb": 10000,
            "vram_total_mb": 40000,
            "power_w": 300.0,
            "temperature_c": 70.0,
        }
    ]
    # Should not raise
    m.update_gpu_metrics(gpus)
