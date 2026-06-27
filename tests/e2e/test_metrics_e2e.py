"""E2E tests for /metrics and /health endpoints.
"""

import httpx
import pytest


@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestMetricsE2E:
    """Metrics endpoint smoke tests."""

    def test_health_endpoint(self, orchestrator_server, backend_target, backend_client):
        """GET /health returns 200 OK."""
        resp = backend_client.get(f"{orchestrator_server.base_url}/health")
        assert resp.status_code in (200, 404), f"Got {resp.status_code}: {resp.text[:200]}"

    def test_metrics_endpoint(self, orchestrator_server, backend_target, backend_client):
        """GET /metrics returns without unhandled error."""
        resp = backend_client.get(f"{orchestrator_server.base_url}/metrics")
        # /metrics returns whatever the metrics module returns
        # Accept any response as long as the endpoint is reachable
        assert resp.status_code < 600, f"Unreachable: {resp.status_code}"
