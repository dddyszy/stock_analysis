import logging
from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routers import auth, backtest, market, portfolio, recommend, settings as settings_router, sim, stocks
from app.core.config import BACKEND_DIR, get_settings
from app.db.session import ensure_database
from app.mcp.errors import McpAuthError, McpBusinessError, McpTransportError
from app.services.jobs import JobAlreadyRunning, mark_interrupted
from app.services.sim_trade import SimTradeError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)
settings = get_settings()


def migrate() -> None:
    ensure_database()
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(_: FastAPI):
    migrate()
    mark_interrupted()
    if settings.scheduler_enabled:
        from app.jobs.scheduler import scheduler, start_scheduler

        start_scheduler()
        yield
        scheduler.shutdown(wait=False)
    else:
        yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(McpAuthError)
async def _auth_error(_: Request, exc: McpAuthError) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": str(exc), "code": "mcp_auth"})


@app.exception_handler(McpBusinessError)
async def _biz_error(_: Request, exc: McpBusinessError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": f"腾讯自选股返回：{exc}", "code": "mcp_business"})


@app.exception_handler(McpTransportError)
async def _transport_error(_: Request, exc: McpTransportError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc), "code": "mcp_transport"})


@app.exception_handler(SimTradeError)
async def _sim_error(_: Request, exc: SimTradeError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc), "code": "sim"})


@app.exception_handler(JobAlreadyRunning)
async def _job_running(_: Request, exc: JobAlreadyRunning) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": f"任务 {exc} 正在运行", "code": "job_running"})


for r in (auth, market, stocks, recommend, portfolio, sim, settings_router, backtest):
    app.include_router(r.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "provider": settings.data_provider, "database": settings.effective_db_name}
