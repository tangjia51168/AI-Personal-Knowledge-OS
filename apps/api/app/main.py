import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import get_settings
from app.core.http import HttpBoundary
from app.core.production import validate_production
from app.db.security import validate_runtime_database
from app.modules.ai.routes import router as ai_router
from app.modules.audit.routes import router as audit_router
from app.modules.files.routes import router as files_router
from app.modules.identity.recovery_routes import router as recovery_router
from app.modules.identity.routes import router as identity_router
from app.modules.identity.security_events import router as identity_security_router
from app.modules.jobs.routes import router as jobs_router
from app.modules.learning.routes import router as learning_router
from app.modules.notes.routes import router as notes_router
from app.modules.organization.routes import router as organization_router
from app.modules.sources.routes import router as sources_router
from app.modules.system.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# Uvicorn's default access log includes query strings (download tickets). The
# HttpBoundary emits only request IDs and route templates, for every launch mode.
logging.getLogger("uvicorn.access").disabled = True
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    validate_production(get_settings())
    validate_runtime_database()
    yield


app = FastAPI(
    title="AI Personal Knowledge OS",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if get_settings().app_env == "production" else "/docs",
    redoc_url=None if get_settings().app_env == "production" else "/redoc",
    openapi_url=None if get_settings().app_env == "production" else "/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)
app.add_middleware(HttpBoundary)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=get_settings().trusted_hosts)
app.include_router(router)
app.include_router(identity_router)
app.include_router(recovery_router)
app.include_router(identity_security_router)
app.include_router(jobs_router)
app.include_router(learning_router)
app.include_router(sources_router)
app.include_router(notes_router)
app.include_router(organization_router)
app.include_router(files_router)
app.include_router(ai_router)
app.include_router(audit_router)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR"}
    return JSONResponse({"error": detail}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors: list[dict[str, Any]] = [
        {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return JSONResponse({"error": {"code": "VALIDATION_ERROR", "fields": errors}}, status_code=422)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Exception text may contain credentials; only log its class at this boundary.
    logger.error("Unhandled API exception: %s", type(exc).__name__)
    return JSONResponse({"error": {"code": "INTERNAL_ERROR"}}, status_code=500)
