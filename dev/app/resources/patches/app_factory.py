"""YunJi extensions for LTX Desktop backend — Modular Extension Architecture.

Architecture:
  This file is a thin orchestrator that creates the FastAPI app and applies
  all YunJi extensions in the correct order. Each extension lives in its own
  module under patches/extensions/.

  When upstream (github.com/Lightricks/LTX-Desktop) updates app_factory.py,
  we compare and incorporate changes manually. Our extensions are isolated
  in the extensions/ directory, making upstream diffs easy to review.

Extension loading order (matters!):
  1. request_model    — Extend Pydantic models before any handler uses them
  2. windows_fixes    — Platform-specific error handling
  3. output_config    — Dynamic output path + /outputs mount
  4. low_vram_hooks   — Low VRAM mode pipeline hooks
  5. lora_hooks       — LoRA build hook
  6. ic_lora_patch    — IC-LoRA reference conditioning
  7. custom_patches   — Runtime compatibility patches
  8. video_gen_patch  — VideoGeneration monkey-patch (most upstream-sensitive)
  9. retake_patch     — Retake pipeline monkey-patch
  10. health_patch    — Health/GPU info patches
  11. model_api       — Model list API (MUST register before upstream models_router)
  12-18. API endpoints — All other YunJi custom API routes
  19. env_check       — Environment check & recommended version detection
  20. community_models — Community & official model registry with download support

Upstream: github.com/Lightricks/LTX-Desktop
Current upstream version tracked: v1.0.2
Latest upstream release: v1.0.4 (2026-04-03)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING
from collections.abc import Awaitable, Callable

import torch
from fastapi import FastAPI, Request
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
from logging_policy import log_http_error, log_unhandled_exception
from state import init_state_service

if TYPE_CHECKING:
    from app_handler import AppHandler

os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"

DEFAULT_ALLOWED_ORIGINS: list[str] = ["*"]


def create_app(
    *,
    handler: "AppHandler",
    allowed_origins: list[str] | None = None,
    title: str = "LTX-2 Video Generation Server",
    auth_token: str = "",
    admin_token: str = "",
) -> FastAPI:
    init_state_service(handler)

    from extensions._context import ExtensionContext, resolve_config_dir, resolve_output_path
    from extensions.request_model import install as install_request_model

    install_request_model()

    app = FastAPI(title=title)
    app.state.admin_token = admin_token

    config_dir = resolve_config_dir()
    output_path = resolve_output_path(config_dir)

    ctx = ExtensionContext(
        handler=handler,
        app=app,
        get_output_path=lambda: resolve_output_path(config_dir),
        config_dir=config_dir,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins or DEFAULT_ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # ★ 2026-06-16: 启用 GZip 压缩
    #   /api/models/registry 75KB → ~15KB
    #   /api/loras /api/presets 等 JSON 响应普遍 -70%~80%
    #   浏览器看到 Content-Encoding: gzip 自动解压
    from starlette.middleware.gzip import GZipMiddleware as _GZip
    app.add_middleware(_GZip, minimum_size=512)

    @app.middleware("http")
    async def _sync_gpu_middleware(request: Request, call_next: Callable[[Request], Awaitable[StarletteResponse]]) -> StarletteResponse:
        if torch.cuda.is_available() and getattr(handler.config.device, "type", "") == "cuda":
            idx = handler.config.device.index
            if idx is not None:
                torch.cuda.set_device(idx)
        return await call_next(request)

    import base64
    import hmac

    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next: Callable[[Request], Awaitable[StarletteResponse]]) -> StarletteResponse:
        if request.url.path.startswith("/outputs") or request.url.path == "/api/system/upload-image":
            return await call_next(request)
        if not auth_token:
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)

        def _token_matches(candidate: str) -> bool:
            return hmac.compare_digest(candidate, auth_token)

        if request.headers.get("upgrade", "").lower() == "websocket":
            if _token_matches(request.query_params.get("token", "")):
                return await call_next(request)
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})

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

    from extensions.windows_fixes import install as install_windows_fixes
    from extensions.output_config import install as install_output_config
    from extensions.low_vram_hooks import install as install_low_vram_hooks
    from extensions.lora_hooks import install as install_lora_hooks
    from extensions.ic_lora_patch import install as install_ic_lora_patch
    from extensions.custom_patches_ext import install as install_custom_patches
    from extensions.video_gen_patch import install as install_video_gen_patch
    from extensions.retake_patch import install as install_retake_patch
    from extensions.health_patch import install as install_health_patch
    from extensions.system_api import install as install_system_api
    from extensions.queue_api import install as install_queue_api
    from extensions.history_api import install as install_history_api
    from extensions.tts_api import install as install_tts_api
    from extensions.lora_api import install as install_lora_api
    from extensions.model_api import install as install_model_api
    from extensions.file_api import install as install_file_api
    from extensions.batch_api import install as install_batch_api
    from extensions.env_check import install as install_env_check
    from extensions.community_models import install as install_community_models
    from extensions.upscale_api import install as install_upscale_api

    install_windows_fixes(app, ctx)
    install_output_config(app, ctx)
    install_low_vram_hooks(app, ctx)
    install_lora_hooks(app, ctx)
    install_ic_lora_patch(app, ctx)
    install_custom_patches(app, ctx)
    install_video_gen_patch(app, ctx)
    install_retake_patch(app, ctx)
    install_health_patch(app, ctx)
    install_model_api(app, ctx)

    app.include_router(health_router)
    app.include_router(generation_router)
    app.include_router(models_router)
    app.include_router(settings_router)
    app.include_router(image_gen_router)
    app.include_router(suggest_gap_prompt_router)
    app.include_router(retake_router)
    app.include_router(ic_lora_router)
    app.include_router(runtime_policy_router)

    install_system_api(app, ctx)
    install_queue_api(app, ctx)
    install_history_api(app, ctx)
    install_tts_api(app, ctx)
    install_lora_api(app, ctx)
    install_file_api(app, ctx)
    install_batch_api(app, ctx)
    install_env_check(app, ctx)
    try:
        install_community_models(app, ctx)
        print("[YunJi] community_models installed OK")
    except Exception as e:
        print(f"[YunJi] WARNING: community_models install failed: {e}")
        import traceback
        traceback.print_exc()
    install_upscale_api(app, ctx)

    print(f"[YunJi] All extensions installed. Upstream: {ctx.upstream_version}")

    return app
