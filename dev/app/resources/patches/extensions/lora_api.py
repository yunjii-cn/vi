"""LoRA scanning and management API endpoints (YunJi custom).

Endpoints:
  GET  /api/loras       - List available LoRA models
  POST /api/lora-dir    - Save LoRA directory preference
  GET  /api/lora-dir    - Get LoRA directory preference

Upstream dependency: handler.pipelines.models_dir
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from extensions._context import ExtensionContext
from extensions._utils import default_lora_dir, resolve_models_root


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    _LORA_SCAN_SUFFIXES = {".safetensors", ".ckpt", ".pt", ".bin"}

    @app.post("/api/lora-dir")
    async def route_save_lora_dir(request: Request):
        try:
            body = await request.json()
            lora_dir = body.get("loraDir", "").strip()
            settings_file = ctx.config_dir / "settings.json"
            if settings_file.exists():
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {}
            data["lora_dir"] = lora_dir
            data["loraDir"] = lora_dir
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return {"status": "ok", "loraDir": lora_dir}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @app.get("/api/lora-dir")
    async def route_get_lora_dir():
        try:
            settings_file = ctx.config_dir / "settings.json"
            models_root = resolve_models_root(ctx)
            _default_lora_dir = default_lora_dir(ctx)
            payload = {
                "loraDir": "", "modelsDir": str(models_root) if models_root else "",
                "defaultLoraDir": str(_default_lora_dir) if _default_lora_dir else "",
            }
            if settings_file.exists():
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                payload["loraDir"] = data.get("lora_dir", "") or data.get("loraDir", "")
            return payload
        except Exception as e:
            return {"loraDir": "", "error": str(e)}

    @app.get("/api/loras")
    async def route_list_loras(request: Request):
        from pathlib import Path as _Path
        raw = (request.query_params.get("dir") or "").strip()
        if raw.startswith("True"):
            raw = raw[4:].lstrip()
        raw = raw.strip().strip('"').strip("'")
        if not raw:
            try:
                settings_file = ctx.config_dir / "settings.json"
                if settings_file.exists():
                    with open(settings_file, "r", encoding="utf-8") as f:
                        settings_data = json.load(f)
                    custom_lora_dir = settings_data.get("lora_dir", "") or settings_data.get("loraDir", "")
                    if custom_lora_dir and str(custom_lora_dir).strip():
                        raw = str(custom_lora_dir).strip()
            except Exception as e:
                print(f"[PATCH] Failed to read lora_dir from settings: {e}")
            if not raw:
                _default_lora_dir = default_lora_dir(ctx)
                raw = str(_default_lora_dir) if _default_lora_dir else ""
        if not raw:
            return {"loras": [], "loras_dir": "", "models_dir": ""}
        root = _Path(raw).expanduser()
        try:
            root = root.resolve()
        except OSError:
            pass
        if not root.is_dir():
            return {
                "loras": [], "error": "not_a_directory",
                "message": "路径不是文件夹或不存在，请检查拼写、盘符与权限",
                "path": str(root), "loras_dir": str(root), "models_dir": str(root.parent),
            }
        found: list[dict[str, str]] = []
        try:
            for dirpath, _dirnames, filenames in os.walk(root):
                for fn in filenames:
                    suf = _Path(fn).suffix.lower()
                    if suf in _LORA_SCAN_SUFFIXES:
                        full = _Path(dirpath) / fn
                        if full.is_file():
                            try:
                                resolved = str(full.resolve())
                            except OSError:
                                resolved = str(full)
                            found.append({"name": fn, "path": resolved})
        except OSError as e:
            return JSONResponse(status_code=400, content={"loras": [], "error": "scan_failed", "message": str(e), "path": str(root)})
        found.sort(key=lambda x: x["name"].lower())
        _default_lora_dir = default_lora_dir(ctx)
        return {
            "loras": found, "loras_dir": str(root), "models_dir": str(root.parent),
            "default_loras_dir": str(_default_lora_dir or ""),
        }
