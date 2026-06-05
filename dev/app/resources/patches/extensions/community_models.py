"""Community & official model registry with download support.

Endpoints:
  GET  /api/models/registry        - List all available models (official + community)
  POST /api/models/registry/download - Download a model from registry by ID
  GET  /api/models/registry/status   - Check download status
  GET  /api/models/registry/dirs     - List all model directories
  POST /api/models/registry/custom-dir  - Add a custom directory
  DELETE /api/models/registry/custom-dir - Remove a custom directory

The registry defines official and community quantized models with metadata
including VRAM requirements, recommended hardware tiers, and download sources.

Upstream dependency: handler.pipelines.models_dir (for model storage path)
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request as FastAPIRequest
from fastapi.responses import JSONResponse

from extensions._context import ExtensionContext
from extensions._utils import resolve_models_root

logger = logging.getLogger("community_models")


@dataclass(frozen=True, slots=True)
class ModelRegistryEntry:
    model_id: str
    name: str
    description: str
    source: str
    repo_id: str
    filename: str
    size_gb: float
    quantization: str
    variant: str
    min_vram_gb: int
    recommended_tiers: list[str]
    is_folder: bool = False
    pipeline_mode: str = "fast"
    tags: list[str] = field(default_factory=list)


MODEL_REGISTRY: dict[str, ModelRegistryEntry] = {
    "ltx-2.3-22b-distilled": ModelRegistryEntry(
        model_id="ltx-2.3-22b-distilled",
        name="LTX 2.3 22B Distilled",
        description="Official distilled model, best quality, requires high VRAM",
        source="official",
        repo_id="Lightricks/LTX-2.3",
        filename="ltx-2.3-22b-distilled.safetensors",
        size_gb=43.0,
        quantization="bf16",
        variant="distilled",
        min_vram_gb=24,
        recommended_tiers=["ultra", "high"],
        pipeline_mode="fast",
        tags=["official", "distilled", "bf16"],
    ),
    "ltx-2.3-22b-distilled-fp8": ModelRegistryEntry(
        model_id="ltx-2.3-22b-distilled-fp8",
        name="LTX 2.3 22B Distilled FP8",
        description="Official FP8 quantized distilled model, saves ~4GB VRAM, recommended for most users",
        source="official",
        repo_id="Lightricks/LTX-2.3",
        filename="ltx-2.3-22b-distilled-fp8.safetensors",
        size_gb=22.0,
        quantization="fp8",
        variant="distilled-fp8",
        min_vram_gb=10,
        recommended_tiers=["high", "medium", "low", "minimal"],
        pipeline_mode="fast",
        tags=["official", "distilled", "fp8", "recommended"],
    ),
    "ltx-2.3-22b-dev-fp8": ModelRegistryEntry(
        model_id="ltx-2.3-22b-dev-fp8",
        name="LTX 2.3 22B Dev FP8",
        description="Official FP8 dev model for Pro mode, higher quality but slower",
        source="official",
        repo_id="Lightricks/LTX-Video",
        filename="ltx-2.3-22b-dev-fp8.safetensors",
        size_gb=22.0,
        quantization="fp8",
        variant="dev-fp8",
        min_vram_gb=20,
        recommended_tiers=["ultra", "high"],
        pipeline_mode="dev",
        tags=["official", "dev", "fp8"],
    ),
    "ltx-2.3-spatial-upscaler": ModelRegistryEntry(
        model_id="ltx-2.3-spatial-upscaler",
        name="LTX 2.3 Spatial Upscaler x2",
        description="2x spatial upscaler for higher resolution output",
        source="official",
        repo_id="Lightricks/LTX-2.3",
        filename="ltx-2.3-spatial-upscaler-x2-1.0.safetensors",
        size_gb=1.9,
        quantization="bf16",
        variant="upscaler",
        min_vram_gb=4,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="upscaler",
        tags=["official", "upscaler"],
    ),
}

_download_lock = threading.Lock()
_download_status: dict[str, Any] = {"active": False, "model_id": None, "progress": 0.0, "error": None}
_ctx: ExtensionContext | None = None
_custom_models_dirs: list[Path] = []

_CUSTOM_DIRS_FILE = "custom_models_dirs.txt"


def _load_custom_dirs() -> list[Path]:
    global _custom_models_dirs
    if _custom_models_dirs:
        return _custom_models_dirs
    if _ctx is None:
        return []
    try:
        f = _ctx.config_dir / _CUSTOM_DIRS_FILE
        if f.is_file():
            dirs = []
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip().strip('"').strip("'")
                if not line:
                    continue
                p = Path(line).expanduser()
                if p.is_dir() and p not in dirs:
                    dirs.append(p)
            _custom_models_dirs = dirs
            return dirs
    except Exception:
        pass
    return []


def _save_custom_dirs() -> None:
    if _ctx is None:
        return
    try:
        _ctx.config_dir.mkdir(parents=True, exist_ok=True)
        lines = [str(p) for p in _custom_models_dirs]
        (_ctx.config_dir / _CUSTOM_DIRS_FILE).write_text("\n".join(lines), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to persist custom models dirs: %s", e)


def _get_models_dirs() -> list[Path]:
    dirs: list[Path] = []
    for cd in _load_custom_dirs():
        if cd not in dirs:
            dirs.append(cd)
    default = _get_default_models_dir()
    if default is not None and default not in dirs:
        dirs.append(default)
    return dirs


def _fix_broken_junction(p: Path) -> bool:
    try:
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(p))
        if attrs != 0xFFFFFFFF and (attrs & 0x400):
            if not p.exists():
                os.rmdir(str(p))
                p.mkdir(parents=True, exist_ok=True)
                logger.info("Fixed broken junction at %s, created real directory", p)
                return True
    except Exception:
        pass
    return False


def _get_default_models_dir() -> Path | None:
    if _ctx is not None:
        try:
            root = resolve_models_root(_ctx)
            if root:
                _fix_broken_junction(root)
                root.mkdir(parents=True, exist_ok=True)
                return root
        except Exception:
            pass
    try:
        from ltx2_server import DEFAULT_MODELS_DIR
        if DEFAULT_MODELS_DIR:
            _fix_broken_junction(DEFAULT_MODELS_DIR)
            DEFAULT_MODELS_DIR.mkdir(parents=True, exist_ok=True)
            return DEFAULT_MODELS_DIR
    except Exception:
        pass
    return None


def _get_models_dir() -> Path | None:
    dirs = _get_models_dirs()
    return dirs[0] if dirs else None


def _check_model_exists(entry: ModelRegistryEntry, models_dir: Path) -> bool:
    target = models_dir / entry.filename
    if entry.is_folder:
        return target.exists() and any(target.iterdir()) if target.exists() else False
    return target.exists()


def _get_registry_status() -> list[dict[str, Any]]:
    models_dirs = _get_models_dirs()
    results = []
    for entry in MODEL_REGISTRY.values():
        exists = False
        local_path = None
        for md in models_dirs:
            if _check_model_exists(entry, md):
                exists = True
                local_path = str((md / entry.filename).resolve())
                break

        results.append({
            "model_id": entry.model_id,
            "name": entry.name,
            "description": entry.description,
            "source": entry.source,
            "quantization": entry.quantization,
            "variant": entry.variant,
            "size_gb": entry.size_gb,
            "min_vram_gb": entry.min_vram_gb,
            "recommended_tiers": entry.recommended_tiers,
            "pipeline_mode": entry.pipeline_mode,
            "tags": entry.tags,
            "downloaded": exists,
            "local_path": local_path,
            "repo_id": entry.repo_id,
            "filename": entry.filename,
        })
    return results


def _download_model_worker(entry: ModelRegistryEntry, models_dir: Path) -> None:
    global _download_status
    try:
        from huggingface_hub import hf_hub_download, snapshot_download

        _download_status = {"active": True, "model_id": entry.model_id, "progress": 0.0, "error": None}
        logger.info("Downloading model %s from %s", entry.model_id, entry.repo_id)

        target_path = models_dir / entry.filename

        if entry.is_folder:
            snapshot_download(
                repo_id=entry.repo_id,
                local_dir=str(target_path),
            )
        else:
            hf_hub_download(
                repo_id=entry.repo_id,
                filename=entry.filename,
                local_dir=str(models_dir),
            )

        _download_status = {
            "active": False,
            "model_id": entry.model_id,
            "progress": 100.0,
            "error": None,
        }
        logger.info("Model %s downloaded successfully", entry.model_id)

    except Exception as e:
        logger.exception("Model download failed: %s", entry.model_id)
        _download_status = {
            "active": False,
            "model_id": entry.model_id,
            "progress": 0.0,
            "error": str(e),
        }


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    global _ctx
    _ctx = ctx

    @app.get("/api/models/registry")
    async def route_registry():
        try:
            default_dir = _get_default_models_dir()
            custom_dirs = _load_custom_dirs()
            return {
                "models": _get_registry_status(),
                "default_models_dir": str(default_dir) if default_dir else None,
                "custom_models_dirs": [str(d) for d in custom_dirs],
            }
        except Exception as e:
            logger.exception("registry list failed")
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.post("/api/models/registry/download")
    async def route_download(request: FastAPIRequest):
        global _download_status
        try:
            data = await request.json()
        except Exception:
            data = {}

        model_id = data.get("model_id", "").strip()
        custom_dir_param = data.get("custom_dir", "").strip()

        if not model_id:
            return JSONResponse(status_code=400, content={"error": "Missing 'model_id'"})

        entry = MODEL_REGISTRY.get(model_id)
        if entry is None:
            return JSONResponse(status_code=404, content={"error": f"Unknown model: {model_id}"})

        with _download_lock:
            if _download_status.get("active"):
                return JSONResponse(
                    status_code=409,
                    content={"error": f"Download already in progress: {_download_status.get('model_id')}"},
                )

            download_dir = _get_models_dir()
            if custom_dir_param:
                p = Path(custom_dir_param).expanduser()
                if p.is_dir():
                    download_dir = p

            if download_dir is None:
                return JSONResponse(status_code=500, content={"error": "Models directory not found"})

            models_dirs = _get_models_dirs()
            for md in models_dirs:
                if _check_model_exists(entry, md):
                    return {"status": "already_exists", "model_id": model_id}

            thread = threading.Thread(
                target=_download_model_worker,
                args=(entry, download_dir),
                daemon=True,
            )
            thread.start()

        return {"status": "started", "model_id": model_id}

    @app.get("/api/models/registry/status")
    async def route_download_status():
        return _download_status

    @app.get("/api/models/registry/dirs")
    async def route_models_dirs():
        default_dir = _get_default_models_dir()
        custom_dirs = _load_custom_dirs()
        return {
            "default_models_dir": str(default_dir) if default_dir else None,
            "custom_models_dirs": [str(d) for d in custom_dirs],
            "all_dirs": [str(d) for d in _get_models_dirs()],
        }

    @app.post("/api/models/registry/custom-dir")
    async def route_add_custom_dir(request: FastAPIRequest):
        global _custom_models_dirs
        try:
            data = await request.json()
        except Exception:
            data = {}

        path_str = data.get("path", "").strip().strip('"').strip("'")
        if not path_str:
            return JSONResponse(status_code=400, content={"error": "Missing 'path'"})

        p = Path(path_str).expanduser()
        if not p.is_dir():
            return JSONResponse(status_code=400, content={"error": f"Directory does not exist: {path_str}"})

        if p in _custom_models_dirs or str(p) in [str(d) for d in _custom_models_dirs]:
            return JSONResponse(status_code=409, content={"error": "Directory already added"})

        _custom_models_dirs.append(p)
        _save_custom_dirs()
        return {"status": "added", "custom_models_dirs": [str(d) for d in _custom_models_dirs]}

    @app.delete("/api/models/registry/custom-dir")
    async def route_remove_custom_dir(request: FastAPIRequest):
        global _custom_models_dirs
        try:
            data = await request.json()
        except Exception:
            data = {}

        path_str = data.get("path", "").strip().strip('"').strip("'")
        if not path_str:
            return JSONResponse(status_code=400, content={"error": "Missing 'path'"})

        p = Path(path_str).expanduser()
        before = len(_custom_models_dirs)
        _custom_models_dirs = [d for d in _custom_models_dirs if str(d) != str(p)]
        if len(_custom_models_dirs) == before:
            return JSONResponse(status_code=404, content={"error": "Directory not found in custom list"})

        _save_custom_dirs()
        return {"status": "removed", "custom_models_dirs": [str(d) for d in _custom_models_dirs]}

    logger.info("community_models: module loaded")
