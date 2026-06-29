from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import load_config
from app.gpu_monitor import GPUMonitor, ServerSnapshot
from app.ssh_client import SSHMonitorClient

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    servers, poll_interval_seconds = load_config()
    app.state.poll_interval_seconds = poll_interval_seconds
    ssh_client = SSHMonitorClient(servers)
    gpu_monitor = GPUMonitor(
        servers,
        ssh_client,
        poll_interval_seconds=poll_interval_seconds,
    )
    await gpu_monitor.start()
    app.state.gpu_monitor = gpu_monitor
    try:
        yield
    finally:
        await gpu_monitor.stop()
        ssh_client.close()


app = FastAPI(title="GPU Monitor", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/servers", response_model=list[ServerSnapshot])
async def list_servers(request: Request) -> list[ServerSnapshot]:
    return await request.app.state.gpu_monitor.get_all_snapshots()


@app.get("/api/config")
async def get_config(request: Request) -> dict[str, int]:
    return {"poll_interval_seconds": request.app.state.poll_interval_seconds}


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
