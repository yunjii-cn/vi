"""Model scanning API endpoint (YunJi custom).

Endpoints:
  GET /api/models - List available model checkpoints

Upstream dependency: handler.pipelines.models_dir
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from extensions._context import ExtensionContext
from extensions._utils import resolve_models_root


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    _MODEL_SCAN_SUFFIXES = {".safetensors", ".ckpt", ".pt", ".bin", ".pth"}

    @app.get("/api/models")
    async def route_list_models(request: Request):
        raw = (request.query_params.get("dir") or "").strip()
        if raw.startswith("True"):
            raw = raw[4:].lstrip()
        raw = raw.strip().strip('"').strip("'")
        if not raw:
            try:
                md = getattr(ctx.handler.pipelines, "models_dir", None)
                if md is None or not str(md).strip():
                    return {"models": []}
                root = Path(str(md)).expanduser().resolve()
            except OSError:
                return {"models": []}
            if not root.is_dir():
                return {"models": []}
        else:
            root = Path(raw).expanduser()
            try:
                root = root.resolve()
            except OSError:
                pass
        if not root.is_dir():
            return {
                "models": [], "error": "not_a_directory",
                "message": "路径不是文件夹或不存在，请检查拼写、盘符与权限", "path": str(root),
            }
        found: list[dict[str, str]] = []
        try:
            for dirpath, _dirnames, filenames in os.walk(root):
                for fn in filenames:
                    suf = Path(fn).suffix.lower()
                    if suf in _MODEL_SCAN_SUFFIXES:
                        full = Path(dirpath) / fn
                        if full.is_file():
                            try:
                                resolved = str(full.resolve())
                            except OSError:
                                resolved = str(full)
                            found.append({"name": fn, "path": resolved})
        except OSError as e:
            return JSONResponse(status_code=400, content={"models": [], "error": "scan_failed", "message": str(e), "path": str(root)})
        found.sort(key=lambda x: x["name"].lower())
        return {"models": found}
