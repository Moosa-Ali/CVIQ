import logging
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import BASE_DIR, settings
from .logging_setup import setup_logging
from .routes.config import router as config_router
from .routes.cv import library_router, router as cv_router
from .routes.export import router as export_router
from .routes.templates import router as templates_router

# Initialise logging at import time so logs capture from startup.
setup_logging()
logger = logging.getLogger("cviq")

app = FastAPI(title="CVIQ API", version="1.0.0")

origins = settings.cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials="*" not in origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request: method, path, status code, and duration in ms."""

    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "%s %s -> %s (%.1f ms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response


app.add_middleware(RequestLoggingMiddleware)

app.include_router(config_router)
app.include_router(cv_router)
app.include_router(library_router)
app.include_router(export_router)
app.include_router(templates_router)

_FRONTEND_DIR = BASE_DIR / "frontend"

if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
else:

    @app.get("/", response_class=HTMLResponse)
    def root():
        return HTMLResponse("<h1>CVIQ API</h1><p>Frontend not built. See /docs for the API.</p>")
