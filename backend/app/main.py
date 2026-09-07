"""FastAPI application for the Enterprise AI Customer Service Agent.

当前阶段提供 API 骨架、健康检查与 Phase 2B Mock Business API;
不包含 Agent / RAG / 业务逻辑之外的编排能力。
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import approvals, health, orders, refunds, tickets, users
from app.demo import routes as demo_routes
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services.errors import BusinessError

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(
            "%s (env=%s, version=%s) starting",
            settings.app_name,
            settings.environment,
            settings.app_version,
        )
        yield
        logger.info("Application shutting down")

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )
    application.include_router(health.router, prefix=settings.api_v1_prefix)
    application.include_router(orders.order_router, prefix=settings.api_v1_prefix)
    application.include_router(orders.logistics_router, prefix=settings.api_v1_prefix)
    application.include_router(users.router, prefix=settings.api_v1_prefix)
    application.include_router(refunds.router, prefix=settings.api_v1_prefix)
    application.include_router(tickets.router, prefix=settings.api_v1_prefix)
    application.include_router(approvals.router, prefix=settings.api_v1_prefix)
    application.include_router(demo_routes.router, prefix=settings.api_v1_prefix)
    _register_exception_handlers(application)
    return application


def _register_exception_handlers(application: FastAPI) -> None:
    """注册基础错误处理,统一错误响应结构: {"status": "error", "detail": ..., "code": ...}."""

    @application.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"status": "error", "code": "HTTP_ERROR", "detail": exc.detail},
        )

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "code": "VALIDATION_ERROR",
                "detail": "Request validation failed",
                "errors": exc.errors(),
            },
        )

    @application.exception_handler(BusinessError)
    async def business_exception_handler(request: Request, exc: BusinessError) -> JSONResponse:
        """领域错误 → HTTP:保留业务语义,不泄漏数据库实现细节。"""
        return JSONResponse(
            status_code=exc.http_status,
            content={"status": "error", "code": exc.code, "detail": exc.detail},
        )

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "code": "INTERNAL_ERROR", "detail": "Internal server error"},
        )


app = create_app()
