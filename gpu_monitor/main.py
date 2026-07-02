from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from gpu_monitor.config import load_config
from gpu_monitor.gpu_monitor import GPUMonitor, ServerSnapshot
from gpu_monitor.ssh_client import SSHMonitorClient

STATIC_DIR = files("gpu_monitor") / "static"


def create_app(config_path: Path | str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        servers, poll_interval_seconds = load_config(config_path)
        app.state.poll_interval_seconds = poll_interval_seconds
        app.state.server_names = [server.host for server in servers]
        ssh_client = SSHMonitorClient(servers)
        gpu_monitor = GPUMonitor(
            servers,
            ssh_client,
            poll_interval_seconds=poll_interval_seconds,
        )
        app.state.gpu_monitor = gpu_monitor
        try:
            yield
        finally:
            ssh_client.close()

    app = FastAPI(title="GPU Monitor", version="0.2.3", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.post("/api/servers/{server_name}/refresh", response_model=ServerSnapshot)
    async def refresh_server(
        request: Request, server_name: str, force: bool = False
    ) -> ServerSnapshot:
        try:
            return await request.app.state.gpu_monitor.refresh_snapshot(
                server_name, force=force
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Server not found") from exc

    @app.get("/api/config")
    async def get_config(request: Request) -> dict[str, int | list[str]]:
        return {
            "poll_interval_seconds": request.app.state.poll_interval_seconds,
            "servers": request.app.state.server_names,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    return app

