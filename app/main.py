"""Main FastAPI application entry point, lifespan, routes, and error handlers."""

from contextlib import asynccontextmanager
import logging
from typing import AsyncGenerator
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.cache import TTLCache
from app.config import get_settings
from app.portal.client import (
    PortalAuthError,
    PortalClient,
    PortalError,
    PortalNotFoundError,
    PortalUnavailableError,
)
from app.routers import admin, health, hierarchy, meters, transformers
from app.services.consumption import ConsumptionService
from app.services.hierarchy import HierarchyService
from app.services.meters import MetersService
from app.services.transformers import TransformersService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("urja_api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup, dependencies, and teardown."""
    settings = get_settings()
    cache = TTLCache(default_ttl=float(settings.metadata_cache_ttl_seconds))
    portal_client = PortalClient(settings=settings)

    meters_service = MetersService(portal_client, cache, settings)
    consumption_service = ConsumptionService(portal_client, cache, settings)
    transformers_service = TransformersService(portal_client, meters_service, cache, settings)
    hierarchy_service = HierarchyService(portal_client, meters_service, cache, settings)

    app.state.settings = settings
    app.state.cache = cache
    app.state.portal_client = portal_client
    app.state.meters_service = meters_service
    app.state.consumption_service = consumption_service
    app.state.transformers_service = transformers_service
    app.state.hierarchy_service = hierarchy_service

    logger.info("Urja API application started")
    yield

    await portal_client.aclose()
    logger.info("Urja API application terminated")


app = FastAPI(
    title="Urja Meter Ops REST API",
    description=(
        "Clean, documented REST API service wrapper in front of legacy Urja Meter Ops web portal. "
        "Provides normalized meter listings, details, energy registers with deltas and anomaly flags, "
        "distribution transformers, and complete distribution hierarchy trees."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# Exception Handlers implementing standard error model: {"error": {"code": str, "message": str}}


@app.exception_handler(PortalNotFoundError)
async def not_found_handler(request: Request, exc: PortalNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": {"code": "NOT_FOUND", "message": exc.message}},
    )


@app.exception_handler(PortalUnavailableError)
async def unavailable_handler(request: Request, exc: PortalUnavailableError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"error": {"code": "BAD_GATEWAY", "message": exc.message}},
    )


@app.exception_handler(PortalAuthError)
async def auth_error_handler(request: Request, exc: PortalAuthError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"error": {"code": "PORTAL_AUTH_ERROR", "message": exc.message}},
    )


@app.exception_handler(PortalError)
async def portal_error_handler(request: Request, exc: PortalError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "PORTAL_ERROR", "message": exc.message}},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": {"code": "BAD_REQUEST", "message": str(exc)}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for err in exc.errors():
        loc = " -> ".join(str(l) for l in err.get("loc", []))
        msg = err.get("msg", "Validation error")
        errors.append(f"{loc}: {msg}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": {"code": "VALIDATION_ERROR", "message": "; ".join(errors)}},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code = "NOT_FOUND" if exc.status_code == 404 else ("BAD_REQUEST" if exc.status_code == 400 else "HTTP_ERROR")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": exc.detail}},
    )


# Include Routers
app.include_router(health.router)
app.include_router(meters.router)
app.include_router(transformers.router)
app.include_router(hierarchy.router)
app.include_router(admin.router)
