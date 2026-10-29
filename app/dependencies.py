"""Dependency injection providers for FastAPI routers."""

from fastapi import Depends, Request
from app.cache import TTLCache
from app.config import Settings, get_settings
from app.portal.client import PortalClient
from app.services.consumption import ConsumptionService
from app.services.hierarchy import HierarchyService
from app.services.meters import MetersService
from app.services.transformers import TransformersService


def get_app_settings() -> Settings:
    return get_settings()


def get_cache(request: Request) -> TTLCache:
    return request.app.state.cache


def get_portal_client(request: Request) -> PortalClient:
    return request.app.state.portal_client


def get_meters_service(request: Request) -> MetersService:
    return request.app.state.meters_service


def get_consumption_service(request: Request) -> ConsumptionService:
    return request.app.state.consumption_service


def get_transformers_service(request: Request) -> TransformersService:
    return request.app.state.transformers_service


def get_hierarchy_service(request: Request) -> HierarchyService:
    return request.app.state.hierarchy_service

