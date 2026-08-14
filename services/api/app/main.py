from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException

from app.api.errors import (
    http_exception_handler,
    llm_analysis_exception_handler,
    llm_configuration_exception_handler,
    ocr_processing_exception_handler,
    request_validation_exception_handler,
)
from app.api.router import api_router
from app.core.config import settings
from app.mcp.server import mcp_http_app, mcp_runtime
from app.services.llm import LLMAnalysisError, LLMConfigurationError
from app.services.media_ocr import OCRProcessingError
import app.models  # noqa: F401


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.mcp_enabled:
        yield
        return
    mcp_runtime.prepare_for_lifespan()
    async with mcp_runtime.server.session_manager.run():
        yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.api_docs_enabled else None,
    redoc_url="/redoc" if settings.api_docs_enabled else None,
    openapi_url="/openapi.json" if settings.api_docs_enabled else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
app.add_exception_handler(LLMConfigurationError, llm_configuration_exception_handler)
app.add_exception_handler(LLMAnalysisError, llm_analysis_exception_handler)
app.add_exception_handler(OCRProcessingError, ocr_processing_exception_handler)
app.include_router(api_router, prefix=settings.api_v1_prefix)
if settings.mcp_enabled:
    # Keep the existing FastAPI routes first; the SDK app owns only /mcp.
    app.mount("/", mcp_http_app, name="mcp")
