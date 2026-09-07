"""Domain-level business errors.

设计说明:本模块只依赖标准库,不依赖 FastAPI / SQLAlchemy。
HTTP 映射由 API 层统一处理(main.py 注册全局 handler),保证
Service 层保持框架无关、可独立单测。

错误分类(对应 PRD §5 FR-API 与统一错误结构):
- NotFoundError        -> 404,资源不存在
- InvalidOperationError -> 422,业务规则不允许(资格不足、状态不允许)
- ConflictError        -> 409,重复/冲突操作(重复退款、重复取消)
- BusinessError        -> 兜底(默认 400)
"""


class BusinessError(Exception):
    """Base class for domain errors. FastAPI-agnostic."""

    http_status: int = 400
    code: str = "BUSINESS_ERROR"

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status


class NotFoundError(BusinessError):
    http_status = 404
    code = "NOT_FOUND"


class InvalidOperationError(BusinessError):
    http_status = 422
    code = "INVALID_OPERATION"


class ConflictError(BusinessError):
    http_status = 409
    code = "CONFLICT"


class VerificationFailedError(BusinessError):
    """Execute->Verify: authoritative DB state contradicts the tool result."""

    http_status = 422
    code = "VERIFICATION_FAILED"
