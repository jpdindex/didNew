from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import socket

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response

from backend.api.api_health import router as health_router
from backend.api.api_match_kpis import router as match_kpis_router
from backend.system.system_control import (
    BackendError,
    PROJECT_ROOT,
    configure_logging,
    firebase_is_configured,
    FirebaseUnavailableError,
    get_settings,
    ensure_firebase_app,
)

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Starting %s (%s)", settings.app_name, settings.app_env)
    if firebase_is_configured(settings):
        try:
            ensure_firebase_app(settings)
        except FirebaseUnavailableError:
            logger.warning("Firebase Admin initialization failed; health will report firebaseInitialized=false")
    yield
    logger.info("Stopping %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version="0.4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Swagger-Key"],
)


@app.exception_handler(BackendError)
async def backend_error_handler(_: Request, exc: BackendError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled backend error")
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "Internal server error"}},
    )


app.include_router(health_router)
app.include_router(match_kpis_router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
def root_status() -> dict[str, str | bool]:
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=settings.app_name, version="0.4.0", routes=app.routes)
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["SwaggerKey"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-Swagger-Key",
    }
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi


def find_backend_port(host: str, first_port: int = 8000, last_port: int = 8099) -> int:
    """Return the first available local backend port, starting at 8000."""
    for port in range(first_port, last_port + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No available backend port in {first_port}-{last_port}")


if __name__ == "__main__":
    import uvicorn

    port = find_backend_port(settings.backend_host, settings.backend_port)
    run_options = {
        "host": settings.backend_host,
        "port": port,
        "reload": settings.backend_reload,
        "access_log": False,
    }
    if settings.backend_reload:
        run_options.update(
            reload_dirs=[str(PROJECT_ROOT / "backend")],
            reload_excludes=[
                str(PROJECT_ROOT / "backend" / "check"),
                str(PROJECT_ROOT / "backend" / "data"),
                "**/__pycache__",
                "**/.pytest_cache",
            ],
        )
    uvicorn.run(
        "backend.app:app",
        **run_options,
    )
