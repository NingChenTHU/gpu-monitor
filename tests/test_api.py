import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from gpu_monitor.gpu_monitor import ServerSnapshot
from gpu_monitor.main import create_app


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

        self.assertIn("/api/servers", paths)
        self.assertIn("/api/config", paths)

    def test_server_api_returns_snapshot_list(self) -> None:
        app = create_test_app()
        app.state.gpu_monitor = FakeMonitor()
        client = TestClient(app)

        response = client.get("/api/servers")

        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)
        self.assertEqual(response.json()[0]["name"], "gpu-a")

    def test_config_api_returns_poll_interval_seconds(self) -> None:
        app = create_test_app()
        app.state.poll_interval_seconds = 25
        client = TestClient(app)

        response = client.get("/api/config")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"poll_interval_seconds": 25})


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

    async def get_all_snapshots(self) -> list[ServerSnapshot]:
        return [self.snapshot]
