"""Community & official model registry with download support.

Endpoints:
  GET  /api/models/registry        - List all available models (official + community)
  POST /api/models/registry/download - Download a model from registry by ID
  GET  /api/models/registry/status   - Check download status
  GET  /api/models/registry/dirs     - List all model directories
  POST /api/models/registry/custom-dir  - Add a custom directory
  DELETE /api/models/registry/custom-dir - Remove a custom directory
  POST /api/models/registry/sync     - Sync registry from remote source

The registry supports remote sync: on startup it loads a cached registry,
and can fetch the latest model list from a remote JSON endpoint.
Built-in defaults serve as fallback when no cache or remote is available.

Upstream dependency: handler.pipelines.models_dir (for model storage path)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
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


_BUILTIN_REGISTRY: dict[str, ModelRegistryEntry] = {
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
    "ltx-2.3-22b-distilled-1.1": ModelRegistryEntry(
        model_id="ltx-2.3-22b-distilled-1.1",
        name="LTX 2.3 22B Distilled v1.1",
        description="Official distilled model v1.1, updated version with improved quality",
        source="official",
        repo_id="Lightricks/LTX-2.3",
        filename="ltx-2.3-22b-distilled-1.1.safetensors",
        size_gb=43.0,
        quantization="bf16",
        variant="distilled-v1.1",
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

REGISTRY_REMOTE_URL = "https://raw.githubusercontent.com/yunjiai/ltx-model-registry/main/models.json"
REGISTRY_MIRROR_URLS = [
    "https://raw.githubusercontent.com/yunjiai/ltx-model-registry/main/models.json",
    "https://ghp.ci/https://raw.githubusercontent.com/yunjiai/ltx-model-registry/main/models.json",
    "https://cdn.jsdelivr.net/gh/yunjiai/ltx-model-registry@main/models.json",
    "https://mirror.ghproxy.com/https://raw.githubusercontent.com/yunjiai/ltx-model-registry/main/models.json",
]
REGISTRY_CACHE_FILE = "model_registry_cache.json"
REGISTRY_SYNC_INTERVAL = 3600

_download_lock = threading.Lock()
_download_status: dict[str, Any] = {"active": False, "model_id": None, "progress": 0.0, "error": None}
_ctx: ExtensionContext | None = None
_custom_models_dirs: list[Path] = []
_merged_registry: dict[str, ModelRegistryEntry] = dict(_BUILTIN_REGISTRY)
_last_sync_time: float = 0.0
_last_sync_status: dict[str, Any] = {"time": None, "success": False, "added": 0, "error": None}
_working_mirror_url: str | None = None

_CUSTOM_DIRS_FILE = "custom_models_dirs.txt"


def _entry_to_dict(entry: ModelRegistryEntry) -> dict[str, Any]:
    return {
        "model_id": entry.model_id,
        "name": entry.name,
        "description": entry.description,
        "source": entry.source,
        "repo_id": entry.repo_id,
        "filename": entry.filename,
        "size_gb": entry.size_gb,
        "quantization": entry.quantization,
        "variant": entry.variant,
        "min_vram_gb": entry.min_vram_gb,
        "recommended_tiers": entry.recommended_tiers,
        "is_folder": entry.is_folder,
        "pipeline_mode": entry.pipeline_mode,
        "tags": entry.tags,
    }


def _dict_to_entry(d: dict[str, Any]) -> ModelRegistryEntry:
    return ModelRegistryEntry(
        model_id=d.get("model_id", ""),
        name=d.get("name", d.get("model_id", "")),
        description=d.get("description", ""),
        source=d.get("source", "community"),
        repo_id=d.get("repo_id", ""),
        filename=d.get("filename", ""),
        size_gb=float(d.get("size_gb", 0)),
        quantization=d.get("quantization", "bf16"),
        variant=d.get("variant", ""),
        min_vram_gb=int(d.get("min_vram_gb", 0)),
        recommended_tiers=d.get("recommended_tiers", []),
        is_folder=bool(d.get("is_folder", False)),
        pipeline_mode=d.get("pipeline_mode", "fast"),
        tags=d.get("tags", []),
    )


def _load_cached_registry() -> dict[str, ModelRegistryEntry]:
    if _ctx is None:
        return {}
    try:
        cache_path = _ctx.config_dir / REGISTRY_CACHE_FILE
        if cache_path.is_file():
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = {}
            for item in data.get("models", []):
                try:
                    e = _dict_to_entry(item)
                    if e.model_id:
                        entries[e.model_id] = e
                except Exception:
                    pass
            return entries
    except Exception as e:
        logger.warning("Failed to load cached registry: %s", e)
    return {}


def _save_cached_registry(registry: dict[str, ModelRegistryEntry]) -> None:
    if _ctx is None:
        return
    try:
        _ctx.config_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "models": [_entry_to_dict(e) for e in registry.values()],
        }
        cache_path = _ctx.config_dir / REGISTRY_CACHE_FILE
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning("Failed to save cached registry: %s", e)


def _merge_registries(builtin: dict, cached: dict) -> dict[str, ModelRegistryEntry]:
    merged = dict(builtin)
    for mid, entry in cached.items():
        if mid not in merged:
            merged[mid] = entry
        elif entry.source != "official" or builtin.get(mid, None) is None:
            merged[mid] = entry
    return merged


def _sync_registry_from_remote() -> dict[str, Any]:
    global _merged_registry, _last_sync_time, _last_sync_status, _working_mirror_url
    result = {"success": False, "added": 0, "updated": 0, "error": None}

    urls = list(REGISTRY_MIRROR_URLS)
    if _working_mirror_url and _working_mirror_url in urls:
        urls.remove(_working_mirror_url)
        urls.insert(0, _working_mirror_url)

    last_error = None
    import httpx
    for url in urls:
        try:
            with httpx.Client(timeout=httpx.Timeout(5.0)) as client:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()

            remote_entries: dict[str, ModelRegistryEntry] = {}
            for item in data.get("models", []):
                try:
                    e = _dict_to_entry(item)
                    if e.model_id:
                        remote_entries[e.model_id] = e
                except Exception:
                    pass

            if not remote_entries:
                last_error = f"Remote registry returned empty model list (from {url})"
                continue

            added = 0
            updated = 0
            for mid, entry in remote_entries.items():
                if mid not in _merged_registry:
                    _merged_registry[mid] = entry
                    added += 1
                else:
                    existing = _merged_registry[mid]
                    if existing.source == "official" and entry.source == "official":
                        _merged_registry[mid] = entry
                        updated += 1
                    elif existing.source != "official":
                        _merged_registry[mid] = entry
                        updated += 1

            _save_cached_registry(_merged_registry)
            _last_sync_time = time.time()
            _working_mirror_url = url
            _last_sync_status = {
                "time": _last_sync_time,
                "success": True,
                "added": added,
                "updated": updated,
                "error": None,
            }
            result = {"success": True, "added": added, "updated": updated, "error": None}
            logger.info("Registry sync: added=%d, updated=%d (from %s)", added, updated, url)
            return result
        except Exception as e:
            last_error = e
            logger.debug("Mirror %s failed: %s", url, e)
            continue

    error_msg = f"All mirrors failed. Last error: {last_error}. Please check your network connection."
    result["error"] = error_msg
    _last_sync_status = {"time": time.time(), "success": False, "added": 0, "error": error_msg}
    logger.warning("Registry sync failed: all mirrors unreachable")
    return result


def _load_custom_dirs() -> list[Path]:
    global _custom_models_dirs
    if _custom_models_dirs:
        return _custom_models_dirs
    dirs: list[Path] = []
    if _ctx is not None:
        try:
            for candidate in [_ctx.config_dir, _ctx.config_dir / "config"]:
                launcher_config = candidate / "launcher_config.json"
                if launcher_config.is_file():
                    with open(launcher_config, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    for d in cfg.get("model_dirs", []):
                        p = d.get("path", "").strip().strip('"').strip("'")
                        if p:
                            pp = Path(p).expanduser()
                            if pp.is_dir() and pp not in dirs:
                                dirs.append(pp)
                    break
        except Exception:
            pass
        try:
            f = _ctx.config_dir / _CUSTOM_DIRS_FILE
            if f.is_file():
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip().strip('"').strip("'")
                    if not line:
                        continue
                    p = Path(line).expanduser()
                    if p.is_dir() and p not in dirs:
                        dirs.append(p)
        except Exception:
            pass
    _custom_models_dirs = dirs
    return dirs


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
    for entry in _merged_registry.values():
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


_MODEL_SCAN_SUFFIXES = {".safetensors", ".ckpt", ".pt", ".bin", ".pth"}
_LORA_SCAN_SUFFIXES = {".safetensors", ".ckpt", ".pt", ".bin"}
_HF_SHARD_RE = __import__("re").compile(r"^(model|diffusion_pytorch_model)-\d{5}-of-\d{5}$")


def _scan_dir_for_models(root: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                suf = Path(fn).suffix.lower()
                if suf in _MODEL_SCAN_SUFFIXES:
                    stem = Path(fn).stem
                    if _HF_SHARD_RE.match(stem):
                        continue
                    full = Path(dirpath) / fn
                    if full.is_file():
                        try:
                            size = full.stat().st_size
                        except OSError:
                            size = 0
                        rel = str(full.relative_to(root))
                        is_lora = "lora" in fn.lower() or "lora" in dirpath.lower()
                        model_type = "lora" if is_lora else "checkpoint"
                        found.append({
                            "name": fn,
                            "path": str(full.resolve()),
                            "relative_path": rel,
                            "size_bytes": size,
                            "model_type": model_type,
                        })
    except OSError:
        pass
    found.sort(key=lambda x: x["name"].lower())
    return found


def _get_local_models_by_dir() -> list[dict[str, Any]]:
    default_dir = _get_default_models_dir()
    custom_dirs = _load_custom_dirs()
    result = []
    if default_dir is not None:
        result.append({
            "path": str(default_dir),
            "is_default": True,
            "models": _scan_dir_for_models(default_dir),
        })
    for cd in custom_dirs:
        if default_dir is not None and str(cd) == str(default_dir):
            continue
        result.append({
            "path": str(cd),
            "is_default": False,
            "models": _scan_dir_for_models(cd),
        })
    return result


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    global _ctx, _merged_registry
    _ctx = ctx

    cached = _load_cached_registry()
    if cached:
        _merged_registry = _merge_registries(_BUILTIN_REGISTRY, cached)

    @app.get("/api/models/registry")
    async def route_registry():
        try:
            default_dir = _get_default_models_dir()
            custom_dirs = _load_custom_dirs()
            return {
                "models": _get_registry_status(),
                "default_models_dir": str(default_dir) if default_dir else None,
                "custom_models_dirs": [str(d) for d in custom_dirs],
                "local_dirs": _get_local_models_by_dir(),
                "sync_status": _last_sync_status,
            }
        except Exception as e:
            logger.exception("registry list failed")
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.post("/api/models/registry/sync")
    async def route_sync_registry():
        global _custom_models_dirs
        _custom_models_dirs = []
        _load_custom_dirs()
        try:
            result = _sync_registry_from_remote()
            result["local_refreshed"] = True
            return result
        except Exception as e:
            return {"success": False, "added": 0, "updated": 0, "error": str(e), "local_refreshed": True}

    @app.post("/api/models/registry/refresh-dirs")
    async def route_refresh_dirs():
        global _custom_models_dirs
        _custom_models_dirs = []
        _load_custom_dirs()
        return {"status": "ok"}

    @app.get("/api/models/registry/sync-status")
    async def route_sync_status():
        return _last_sync_status

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

        entry = _merged_registry.get(model_id)
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

    @app.post("/api/models/local/delete")
    async def route_delete_local_model(request: FastAPIRequest):
        try:
            data = await request.json()
        except Exception:
            data = {}

        file_path = data.get("path", "").strip().strip('"').strip("'")
        if not file_path:
            return JSONResponse(status_code=400, content={"error": "Missing 'path'"})

        p = Path(file_path).expanduser()
        try:
            resolved = str(p.resolve())
        except OSError:
            resolved = str(p)

        default_dir = _get_default_models_dir()
        if default_dir and resolved.startswith(str(default_dir)):
            return JSONResponse(status_code=403, content={"error": "不允许删除系统默认目录中的模型文件"})

        if not p.is_file():
            return JSONResponse(status_code=404, content={"error": f"文件不存在: {file_path}"})

        suf = p.suffix.lower()
        if suf not in _MODEL_SCAN_SUFFIXES:
            return JSONResponse(status_code=400, content={"error": f"不支持的文件类型: {suf}"})

        try:
            p.unlink()
            logger.info("Deleted local model file: %s", resolved)
            return {"status": "deleted", "path": resolved}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": f"删除失败: {e}"})

    logger.info("community_models: module loaded")
