from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from gpu_monitor.config import load_config
from gpu_monitor.monitor import DeviceMonitor
from gpu_monitor.models import ServerSnapshot
from gpu_monitor.ssh_client import SSHMonitorClient

STATIC_DIR = files("gpu_monitor") / "static"


def create_app(config_path: Path) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        servers, poll_interval_seconds = load_config(config_path)
        app.state.config = {
            "poll_interval_seconds": poll_interval_seconds,
            "servers": [server.host for server in servers],
        }
        ssh_client = SSHMonitorClient(servers)
        device_monitor = DeviceMonitor(
            servers,
            ssh_client,
            poll_interval_seconds=poll_interval_seconds,
        )
        app.state.device_monitor = device_monitor
        try:
            yield
        finally:
            ssh_client.close()

    app = FastAPI(title="GPU Monitor", version="0.4.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.post("/api/servers/{server_name}/refresh", response_model=ServerSnapshot)
    async def refresh_server(
        request: Request, server_name: str, force: bool = False
    ) -> ServerSnapshot:
        try:
            return await request.app.state.device_monitor.refresh_snapshot(
                server_name, force=force
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Server not found") from exc

    @app.get("/api/config")
    async def get_config(request: Request) -> dict[str, int | list[str]]:
        return request.app.state.config

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    return app

