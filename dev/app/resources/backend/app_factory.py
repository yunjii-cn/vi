"""FastAPI app factory decoupled from runtime bootstrap side effects."""

from __future__ import annotations

import base64
import hmac
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import Response as StarletteResponse

from _routes._errors import HTTPError
from _routes.generation import router as generation_router
from _routes.health import router as health_router
from _routes.ic_lora import router as ic_lora_router
from _routes.image_gen import router as image_gen_router
from _routes.models import router as models_router
from _routes.suggest_gap_prompt import router as suggest_gap_prompt_router
from _routes.retake import router as retake_router
from _routes.runtime_policy import router as runtime_policy_router
from _routes.settings import router as settings_router
from _routes.system import router as system_router
from logging_policy import log_http_error, log_unhandled_exception
from state import init_state_service

if TYPE_CHECKING:
    from app_handler import AppHandler

DEFAULT_ALLOWED_ORIGINS: list[str] = ["*"]


def create_app(
    *,
    handler: "AppHandler",
    allowed_origins: list[str] | None = None,
    title: str = "LTX-2 Video Generation Server",
    auth_token: str = "",
    admin_token: str = "",
    output_dir_override: str | Path | None = None,
    allowed_file_roots: list[str | Path] | None = None,
) -> FastAPI:
    """Create a configured FastAPI app bound to the provided handler."""
    init_state_service(handler)

    app = FastAPI(title=title)
    app.state.admin_token = admin_token  # type: ignore[attr-defined]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins or DEFAULT_ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _auth_middleware(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        call_next: Callable[[Request], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        if not auth_token:
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        def _token_matches(candidate: str) -> bool:
            return hmac.compare_digest(candidate, auth_token)

        # WebSocket: check query param
        if request.headers.get("upgrade", "").lower() == "websocket":
            if _token_matches(request.query_params.get("token", "")):
                return await call_next(request)
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        # HTTP: Bearer or Basic auth
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer ") and _token_matches(auth_header[7:]):
            return await call_next(request)
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode()
                _, _, password = decoded.partition(":")
                if _token_matches(password):
                    return await call_next(request)
            except Exception:
                pass
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    _FALLBACK = "An unexpected error occurred"

    async def _route_http_error_handler(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, HTTPError):
            log_http_error(request, exc)
            return JSONResponse(status_code=exc.status_code, content={"error": exc.detail or _FALLBACK})
        return JSONResponse(status_code=500, content={"error": str(exc) or _FALLBACK})

    async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, RequestValidationError):
            return JSONResponse(status_code=422, content={"error": str(exc) or _FALLBACK})
        return JSONResponse(status_code=422, content={"error": str(exc) or _FALLBACK})

    async def _route_generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        log_unhandled_exception(request, exc)
        return JSONResponse(status_code=500, content={"error": str(exc) or _FALLBACK})

    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(HTTPError, _route_http_error_handler)
    app.add_exception_handler(Exception, _route_generic_error_handler)

    # ★ 2026-07-26 fix: 使用调用方传入的输出目录(与运行时保持一致)
    #    回退到相对于代码目录的路径(兼容旧行为)
    output_dir = Path(output_dir_override) if output_dir_override else (Path(__file__).parent.parent / "outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/outputs", StaticFiles(directory=str(output_dir)), name="outputs")

    app.include_router(health_router)
    app.include_router(generation_router)
    app.include_router(models_router)
    app.include_router(settings_router)
    app.include_router(image_gen_router)
    app.include_router(suggest_gap_prompt_router)
    app.include_router(retake_router)
    app.include_router(ic_lora_router)
    app.include_router(runtime_policy_router)
    app.include_router(system_router)

    # ★ 配置路径遍历防护: 设置允许提供文件的根目录列表
    if allowed_file_roots:
        from _routes.system import configure_file_roots
        configure_file_roots(allowed_file_roots)
    elif output_dir_override:
        from _routes.system import configure_file_roots
        configure_file_roots([str(output_dir)])

    return app
