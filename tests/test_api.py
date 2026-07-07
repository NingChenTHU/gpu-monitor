import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from gpu_monitor.main import create_app
from gpu_monitor.models import ServerSnapshot


class ApiTests(unittest.TestCase):
    def test_static_index_loads(self) -> None:
        app = create_test_app()
        client = TestClient(app)

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("GPU Monitor", response.text)
        self.assertIn("<h1>GPU Monitor</h1>", response.text)

    def test_server_api_routes_are_registered(self) -> None:
        app = create_test_app()

        paths = {route.path for route in app.routes}

        self.assertNotIn("/api/servers/refresh", paths)
        self.assertIn("/api/servers/{server_name}/refresh", paths)
        self.assertIn("/api/config", paths)

    def test_single_server_refresh_returns_one_snapshot(self) -> None:
        app = create_test_app()
        monitor = FakeMonitor()
        app.state.gpu_monitor = monitor
        client = TestClient(app)

        response = client.post("/api/servers/gpu-a/refresh?force=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "gpu-a")
        self.assertEqual(monitor.single_refreshes, [("gpu-a", True)])

    def test_single_server_refresh_returns_404_for_unknown_server(self) -> None:
        app = create_test_app()
        app.state.gpu_monitor = FakeMonitor()
        client = TestClient(app)

        response = client.post("/api/servers/missing/refresh")

        self.assertEqual(response.status_code, 404)

    def test_config_api_returns_poll_interval_seconds(self) -> None:
        app = create_test_app()
        app.state.config = {
            "poll_interval_seconds": 25,
            "servers": ["gpu-a", "gpu-b"],
        }
        client = TestClient(app)

        response = client.get("/api/config")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"poll_interval_seconds": 25, "servers": ["gpu-a", "gpu-b"]},
        )


def create_test_app():
    with tempfile.TemporaryDirectory() as tmp_dir:
        config_path = Path(tmp_dir) / "servers.toml"
        config_path.write_text(
            'poll_interval_seconds = 20\n[[servers]]\nHost = "gpu-a"\n',
            encoding="utf-8",
        )
        return create_app(config_path)


class FakeMonitor:
    def __init__(self) -> None:
        self.snapshot = ServerSnapshot(name="gpu-a")
        self.single_refreshes: list[tuple[str, bool]] = []

    async def refresh_snapshot(
        self, server_name: str, *, force: bool = False
    ) -> ServerSnapshot:
        if server_name != self.snapshot.name:
            raise KeyError(server_name)
        self.single_refreshes.append((server_name, force))
        return self.snapshot
