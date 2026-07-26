from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.services.llm import LLMAnalysisError, LLMConfigurationError
from app.services.media_ocr import OCRProcessingError


def _error_body(
    *,
    code: str,
    message: str,
    retryable: bool,
    details: Any = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "detail": message,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }
    if details is not None:
        body["error"]["details"] = details
    return body


def _http_error_code(status_code: int) -> str:
    return {
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        502: "upstream_processing_error",
        503: "service_unavailable",
    }.get(status_code, "http_error")


async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "请求处理失败"
    details = None if isinstance(exc.detail, str) else exc.detail
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content=_error_body(
            code=_http_error_code(exc.status_code),
            message=message,
            retryable=exc.status_code in {429, 502, 503, 504},
            details=details,
        ),
    )


async def request_validation_exception_handler(
    _: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_error_body(
            code="validation_error",
            message="请求参数未通过校验",
            retryable=False,
            details=exc.errors(),
        ),
    )


async def llm_configuration_exception_handler(
    _: Request,
    exc: LLMConfigurationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=_error_body(
            code="llm_not_configured",
            message=str(exc),
            retryable=False,
        ),
    )


async def llm_analysis_exception_handler(
    _: Request,
    exc: LLMAnalysisError,
) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content=_error_body(
            code="llm_invalid_response",
            message=str(exc),
            retryable=True,
        ),
    )


async def ocr_processing_exception_handler(
    _: Request,
    exc: OCRProcessingError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_error_body(
            code="ocr_processing_error",
            message=str(exc),
            retryable=True,
        ),
    )
