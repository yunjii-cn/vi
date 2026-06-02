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
    model_category: str = "checkpoint"
    usage_scenario: str = ""
    trigger_word: str = ""
    requires: list[str] = field(default_factory=list)
    preview_url: str = ""


HF_MIRROR_ENDPOINT = "https://hf-mirror.com"

_BUILTIN_REGISTRY: dict[str, ModelRegistryEntry] = {
    "ltx-2.3-22b-distilled": ModelRegistryEntry(
        model_id="ltx-2.3-22b-distilled",
        name="LTX 2.3 22B Distilled",
        description="视频生成核心模型（文生视频/图生视频/智能多帧，BF16精度，需24GB+显存）",
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
        model_category="checkpoint",
        usage_scenario="文生视频、图生视频、智能多帧拼接",
        trigger_word="",
        requires=[],
    ),
    "ltx-2.3-22b-distilled-1.1": ModelRegistryEntry(
        model_id="ltx-2.3-22b-distilled-1.1",
        name="LTX 2.3 22B Distilled v1.1",
        description="视频生成核心模型v1.1（改进版，BF16精度，需24GB+显存）",
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
        model_category="checkpoint",
        usage_scenario="文生视频、图生视频、智能多帧拼接（改进版）",
        trigger_word="",
        requires=[],
    ),
    "ltx-2.3-22b-distilled-fp8": ModelRegistryEntry(
        model_id="ltx-2.3-22b-distilled-fp8",
        name="LTX 2.3 22B Distilled FP8",
        description="FP8量化核心模型（视频生成，节省4GB显存，推荐10-24GB显卡）",
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
        model_category="checkpoint",
        usage_scenario="文生视频、图生视频、智能多帧拼接（FP8量化，低显存推荐）",
        trigger_word="",
        requires=[],
    ),
    "ltx-2.3-22b-dev-fp8": ModelRegistryEntry(
        model_id="ltx-2.3-22b-dev-fp8",
        name="LTX 2.3 22B Dev FP8",
        description="FP8开发模型（Pro高质量模式，需20GB+显存）",
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
        model_category="checkpoint",
        usage_scenario="Pro高质量视频生成（20GB+显存）",
        trigger_word="",
        requires=["ltx-2-19b-distilled-lora-384"],
    ),
    "ltx-2.3-spatial-upscaler": ModelRegistryEntry(
        model_id="ltx-2.3-spatial-upscaler",
        name="LTX 2.3 Spatial Upscaler x2",
        description="2x画质增强模型（视频生成高清输出）",
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
        model_category="upscaler",
        usage_scenario="视频2x画质增强（配合核心模型使用）",
        trigger_word="",
        requires=["ltx-2.3-22b-distilled-fp8"],
    ),
    "ltx-2-19b-distilled-lora-384": ModelRegistryEntry(
        model_id="ltx-2-19b-distilled-lora-384",
        name="LTX 2 19B Distilled LoRA 384",
        description="Pro模式LoRA（视频生成Pro高质量模式必需，384步推理）",
        source="official",
        repo_id="Lightricks/LTX-2",
        filename="ltx-2-19b-distilled-lora-384.safetensors",
        size_gb=0.4,
        quantization="bf16",
        variant="distilled-lora",
        min_vram_gb=4,
        recommended_tiers=["ultra", "high"],
        pipeline_mode="fast",
        tags=["official", "lora", "distilled", "pro"],
        model_category="lora",
        usage_scenario="Pro模式必需LoRA（配合Dev模型使用）",
        trigger_word="",
        requires=["ltx-2.3-22b-dev-fp8"],
    ),
    "ltx-2.3-22b-ic-lora-union-control": ModelRegistryEntry(
        model_id="ltx-2.3-22b-ic-lora-union-control",
        name="LTX 2.3 IC LoRA Union Control",
        description="视频迁移控制模型（视频迁移功能必需，支持深度/姿态/参考图控制）",
        source="official",
        repo_id="Lightricks/LTX-2.3-22b-IC-LoRA-Union-Control",
        filename="ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors",
        size_gb=0.65,
        quantization="bf16",
        variant="ic-lora",
        min_vram_gb=4,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="ic_lora",
        tags=["official", "lora", "ic-lora", "video-transfer"],
        model_category="lora",
        usage_scenario="视频迁移：深度控制、姿态控制、参考图控制",
        trigger_word="",
        requires=["ltx-2.3-22b-distilled-fp8", "dpt-hybrid-midas"],
    ),
    "dpt-hybrid-midas": ModelRegistryEntry(
        model_id="dpt-hybrid-midas",
        name="DPT Hybrid MiDaS",
        description="深度估计模型（视频迁移-深度控制必需）",
        source="official",
        repo_id="Intel/dpt-hybrid-midas",
        filename="dpt-hybrid-midas",
        size_gb=0.5,
        quantization="fp32",
        variant="depth",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium", "low"],
        pipeline_mode="supporting",
        tags=["official", "supporting", "depth"],
        model_category="supporting",
        is_folder=True,
        usage_scenario="视频迁移-深度控制：从参考图提取深度图",
        trigger_word="",
        requires=["ltx-2.3-22b-ic-lora-union-control"],
    ),
    "yolox-l-person-detector": ModelRegistryEntry(
        model_id="yolox-l-person-detector",
        name="YOLOX-L Person Detector",
        description="人物检测模型（视频迁移-姿态控制必需，检测画面中人物位置）",
        source="official",
        repo_id="hr16/yolox-onnx",
        filename="yolox_l.torchscript.pt",
        size_gb=0.2,
        quantization="fp32",
        variant="detection",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium", "low"],
        pipeline_mode="supporting",
        tags=["official", "supporting", "detection"],
        model_category="supporting",
        usage_scenario="视频迁移-姿态控制：检测画面中人物位置",
        trigger_word="",
        requires=["dw-ll-pose-processor", "ltx-2.3-22b-ic-lora-union-control"],
    ),
    "dw-ll-pose-processor": ModelRegistryEntry(
        model_id="dw-ll-pose-processor",
        name="DWPose UCOCO 384",
        description="姿态估计模型（视频迁移-姿态/动作控制必需，提取人体骨架）",
        source="official",
        repo_id="hr16/DWPose-TorchScript-BatchSize5",
        filename="dw-ll_ucoco_384_bs5.torchscript.pt",
        size_gb=0.13,
        quantization="fp32",
        variant="pose",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium", "low"],
        pipeline_mode="supporting",
        tags=["official", "supporting", "pose"],
        model_category="supporting",
        usage_scenario="视频迁移-姿态/动作控制：提取人体骨架关键点",
        trigger_word="",
        requires=["yolox-l-person-detector", "ltx-2.3-22b-ic-lora-union-control"],
    ),
    "gemma-3-12b-text-encoder": ModelRegistryEntry(
        model_id="gemma-3-12b-text-encoder",
        name="Gemma 3 12B QAT Q4 Text Encoder",
        description="文本编码器（所有生成功能的提示词理解必需，Q4量化）",
        source="official",
        repo_id="Lightricks/gemma-3-12b-it-qat-q4_0-unquantized",
        filename="gemma-3-12b-it-qat-q4_0-unquantized",
        size_gb=25.0,
        quantization="q4",
        variant="text-encoder",
        min_vram_gb=8,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="supporting",
        tags=["official", "supporting", "text-encoder"],
        model_category="supporting",
        is_folder=True,
        usage_scenario="所有生成功能的提示词理解（无API Key时必需）",
        trigger_word="",
        requires=[],
    ),
    "z-image-turbo": ModelRegistryEntry(
        model_id="z-image-turbo",
        name="Z-Image-Turbo",
        description="图像生成模型（AI图像生成功能必需）",
        source="official",
        repo_id="Tongyi-MAI/Z-Image-Turbo",
        filename="Z-Image-Turbo",
        size_gb=31.0,
        quantization="bf16",
        variant="image-gen",
        min_vram_gb=8,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="supporting",
        tags=["official", "supporting", "image-gen"],
        model_category="supporting",
        is_folder=True,
        usage_scenario="AI图像生成：文生图、图生图",
        trigger_word="",
        requires=[],
    ),
    "voxcpm2-tts": ModelRegistryEntry(
        model_id="voxcpm2-tts",
        name="VoxCPM2 TTS",
        description="语音合成模型（TTS语音/声音克隆功能必需）",
        source="official",
        repo_id="openbmb/VoxCPM2",
        filename="VoxCPM2",
        size_gb=8.0,
        quantization="bf16",
        variant="tts",
        min_vram_gb=4,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="supporting",
        tags=["official", "supporting", "tts"],
        model_category="supporting",
        is_folder=True,
        usage_scenario="TTS语音合成、声音克隆",
        trigger_word="",
        requires=[],
    ),
    "ltx2.3-22b-ic-lora-cameraman": ModelRegistryEntry(
        model_id="ltx2.3-22b-ic-lora-cameraman",
        name="IC-LoRA Cameraman v1",
        description="摄影师运镜LoRA（视频迁移-摄像机运镜控制，模拟专业摄影机运动）",
        source="community",
        repo_id="Lightricks/LTX-2.3-22B_IC-LoRA-Cameraman",
        filename="LTX2.3-22B_IC-LoRA-Cameraman_v1_10500.safetensors",
        size_gb=0.3,
        quantization="bf16",
        variant="ic-lora-cameraman",
        min_vram_gb=4,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="ic_lora",
        tags=["community", "lora", "ic-lora", "camera-motion"],
        model_category="lora",
        usage_scenario="视频迁移-摄像机运镜：推拉摇移、跟随拍摄等专业运镜效果",
        trigger_word="",
        requires=["ltx-2.3-22b-ic-lora-union-control"],
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
        "model_category": entry.model_category,
        "usage_scenario": entry.usage_scenario,
        "trigger_word": entry.trigger_word,
        "requires": entry.requires,
        "preview_url": entry.preview_url,
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
        model_category=d.get("model_category", "checkpoint"),
        usage_scenario=d.get("usage_scenario", ""),
        trigger_word=d.get("trigger_word", ""),
        requires=d.get("requires", []),
        preview_url=d.get("preview_url", ""),
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


def _sync_custom_dirs_to_settings() -> None:
    if _ctx is None:
        return
    try:
        settings = _ctx.handler.state.app_settings
        settings.custom_models_dirs = [str(p) for p in _custom_models_dirs]
    except Exception as e:
        logger.warning("Failed to sync custom dirs to settings: %s", e)


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
    default = _get_default_models_dir()
    if default is not None:
        return default
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
            "model_category": entry.model_category,
            "is_folder": entry.is_folder,
            "downloaded": exists,
            "local_path": local_path,
            "repo_id": entry.repo_id,
            "filename": entry.filename,
            "usage_scenario": entry.usage_scenario,
            "trigger_word": entry.trigger_word,
            "requires": entry.requires,
            "preview_url": entry.preview_url,
        })
    return results


def _download_model_worker(entry: ModelRegistryEntry, models_dir: Path, use_mirror: bool = False) -> None:
    global _download_status
    try:
        from huggingface_hub import hf_hub_download, snapshot_download

        _download_status = {"active": True, "model_id": entry.model_id, "progress": 0.0, "error": None}
        logger.info("Downloading model %s from %s (mirror=%s)", entry.model_id, entry.repo_id, use_mirror)

        target_path = models_dir / entry.filename
        mirror_kwargs = {"endpoint": HF_MIRROR_ENDPOINT} if use_mirror else {}

        if entry.is_folder:
            snapshot_download(
                repo_id=entry.repo_id,
                local_dir=str(target_path),
                **mirror_kwargs,
            )
        else:
            hf_hub_download(
                repo_id=entry.repo_id,
                filename=entry.filename,
                local_dir=str(models_dir),
                **mirror_kwargs,
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
_HF_SHARD_RE = __import__("re").compile(r"^(model|diffusion_pytorch_model|pytorch_model)-\d+-of-\d+$")
_NON_LORA_PATTERNS = __import__("re").compile(
    r"(?:^|[-_])"
    r"(?:upscaler|vae|text_encoder|tokenizer|scheduler|unet|transformer|controlnet)"
    r"(?:[-_]|$)",
    __import__("re").IGNORECASE,
)


def _is_likely_lora(fn: str, dirpath: str) -> bool:
    stem = Path(fn).stem
    if _HF_SHARD_RE.match(stem):
        return False
    if stem.startswith(".") or stem.startswith("__"):
        return False
    name_lower = fn.lower()
    dir_lower = dirpath.lower()
    if "lora" in name_lower or "lora" in dir_lower:
        return True
    if _NON_LORA_PATTERNS.search(stem):
        return False
    size_indicators = ("22b", "19b", "8b", "7b", "3b", "1b", "2.3", "2-3", "distilled", "checkpoint")
    if any(ind in name_lower for ind in size_indicators):
        return False
    return True


def _beautify_model_name(fn: str) -> str:
    n = Path(fn).stem
    n = n.replace("-", " ").replace("_", " ").strip()
    return n or fn


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
                        is_lora = _is_likely_lora(fn, dirpath)
                        model_type = "lora" if is_lora else "checkpoint"
                        entry: dict[str, Any] = {
                            "name": _beautify_model_name(fn) if is_lora else fn,
                            "filename": fn,
                            "path": str(full.resolve()),
                            "relative_path": rel,
                            "size_bytes": size,
                            "model_type": model_type,
                        }
                        if is_lora and suf == ".safetensors":
                            meta = _read_safetensors_metadata_lite(full)
                            if meta:
                                entry.update(meta)
                        found.append(entry)
    except OSError:
        pass
    found.sort(key=lambda x: x["name"].lower())
    return found


def _read_safetensors_metadata_lite(file_path: Path) -> dict:
    import json as _json
    import struct as _struct
    if not file_path.is_file() or file_path.suffix.lower() != ".safetensors":
        return {}
    try:
        with open(file_path, "rb") as f:
            header_size_bytes = f.read(8)
            if len(header_size_bytes) < 8:
                return {}
            header_size = _struct.unpack("<Q", header_size_bytes)[0]
            if header_size <= 0 or header_size > 100 * 1024 * 1024:
                return {}
            header_json_bytes = f.read(header_size)
            if len(header_json_bytes) < header_size:
                return {}
            header = _json.loads(header_json_bytes)
        metadata = header.get("__metadata__", {})
        if not isinstance(metadata, dict):
            return {}
        result: dict = {}
        desc = (
            metadata.get("description")
            or metadata.get("ss_training_comment")
            or metadata.get("modelspec.description")
            or ""
        )
        if isinstance(desc, str) and desc.strip():
            result["description"] = desc.strip()
        triggers = metadata.get("trigger_words") or metadata.get("tags") or ""
        if isinstance(triggers, str) and triggers.strip():
            result["trigger_words"] = [t.strip() for t in triggers.split(",") if t.strip()]
        elif isinstance(triggers, list) and triggers:
            result["trigger_words"] = [str(t).strip() for t in triggers if str(t).strip()]
        base = (
            metadata.get("base_model")
            or metadata.get("ss_base_model_version")
            or metadata.get("modelspec.architecture")
            or ""
        )
        if isinstance(base, str) and base.strip():
            result["base_model"] = base.strip()
        return result
    except Exception:
        return {}


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

    _load_custom_dirs()
    _sync_custom_dirs_to_settings()

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
        use_mirror = bool(data.get("use_mirror", False))

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
                kwargs={"use_mirror": use_mirror},
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
        _sync_custom_dirs_to_settings()
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
        _sync_custom_dirs_to_settings()
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
