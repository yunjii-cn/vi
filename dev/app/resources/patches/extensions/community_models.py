"""Community & official model registry with download support.

Endpoints:
  GET  /api/models/registry                  - List all available models (official + community)
  POST /api/models/registry/download         - Download a model from registry by ID
  GET  /api/models/registry/status           - Check download status
  GET  /api/models/registry/dirs             - List all model directories
  POST /api/models/registry/custom-dir       - Add a custom directory
  DELETE /api/models/registry/custom-dir     - Remove a custom directory
  POST /api/models/registry/sync             - Sync registry from remote source
  GET  /api/models/registry/preview?repo_id= - Proxy HF preview image (byte-cached)
  GET  /api/models/registry/hf-status        - Get HF background updater status
  POST /api/models/registry/refresh-hf       - Trigger manual HF metadata refresh
  POST /api/models/registry/hf-info          - Get/refresh single repo's HF metadata

Registry data flow (2026-06-10 overhaul):
  1. Built-in _BUILTIN_REGISTRY (manually maintained seed list, see below)
  2. model_registry_cache.json (last successful sync snapshot, loaded on startup)
  3. Remote yunjiai/ltx-model-registry/models.json (legacy mirror, optional)
  4. HuggingFace API: GET https://huggingface.co/api/models/{repo_id}
     - Runs in background thread on startup (30s delay), then every 12h
     - Auto-fills: description, tags, downloads, lastModified, size_gb (from siblings),
       pipeline_tag, preview image
     - TTL cache: meta 24h, preview 7d, image bytes 7d
     - User never needs to manually fill these for new entries

Minimal entry pattern (for NEW models — replaces the legacy verbose form):
    "my-cool-model": ModelRegistryEntry(
        model_id="my-cool-model",
        repo_id="AuthorName/RepoName",       # ← required
        filename="model.safetensors",        # ← required
        is_folder=False,                     # ← required if multi-file
        pipeline_mode="video",               # ← required for routing
        # Everything below is OPTIONAL — HF API auto-fills:
        #   description, name (derived from repo_id), size_gb, tags,
        #   downloads, lastModified, usage_scenario, preview_url
    )

Legacy verbose entries are still supported (builtin non-empty fields override HF API).

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

# ─────────────────────────────────────────────────────────────────────────
# 2026-06-10: HuggingFace API 自动元数据获取
# ─────────────────────────────────────────────────────────────────────────
# 设计: 注册表条目只需填写 repo_id (以及必要的 override),
#       description/size_gb/tags/downloads/lastModified/缩略图
#       由后台线程从 HF API 拉取,带本地 TTL 缓存。
# ─────────────────────────────────────────────────────────────────────────
_HF_API_BASE = "https://huggingface.co/api/models"
_HF_API_MIRRORS = [
    "https://hf-mirror.com/api/models",
    "https://huggingface.co/api/models",
]
# 文件 resolve URL 的镜像列表（预览图/文件下载探测用，国内镜像优先）
_HF_RESOLVE_MIRRORS = [
    "https://hf-mirror.com",
    "https://huggingface.co",
]
_HF_API_TIMEOUT = 8.0
_HF_USER_AGENT = "YunJi-Desktop/1.0 (+https://github.com/yunjiai)"
_HF_META_TTL_SECONDS = 86400          # 元数据 24h 缓存
_HF_PREVIEW_TTL_SECONDS = 7 * 86400   # 缩略图探测 7 天
_HF_BG_INTERVAL_SECONDS = 12 * 3600   # 后台 12h 周期
_HF_BG_INITIAL_DELAY = 10             # 启动 10s 后第一次 (用户感知友好)
_HF_BG_CONCURRENCY = 4

# ── 自适应源选择 ──
# 竞速一次后记住最快源，后续直接用，不再每次都竞速
# 如果快源连续失败则降级回竞速
_hf_preferred_api_base: str | None = None   # 最快的 API base
_hf_preferred_resolve: str | None = None    # 最快的 resolve base
_hf_preferred_consecutive_fails = 0         # 快源连续失败次数
_HF_PREFERRED_MAX_FAILS = 2                 # 连续失败超过此数则降级

# 缩略图候选 (按顺序探测,命中即用)
_HF_PREVIEW_CANDIDATES = (
    "preview.png", "preview.jpg", "preview.jpeg",
    "cover.png", "cover.jpg", "thumbnail.png",
)

# 自动发现 watch 列表: 匹配的 repo 会被后台任务自动发现并加入
# _WATCH_LIST 既可放完整 repo_id,也可放通配符 (如 "Lightricks/LTX*")
_WATCH_LIST: list[str] = [
    "Lightricks/LTX-2.3",
    "Lightricks/LTX-Video",
    "Lightricks/LTX-2",
    "Lightricks/LTX-2.3-22B_IC-LoRA-Cameraman",
    "Lightricks/LTX-2.3-22b-ic-lora-union-control",
    "Lightricks/LTX-2.3-22b-distilled",
    "Lightricks/LTX-2.3-spatial-upscaler-x2",
    "Lightricks/LTX-2-19b-distilled-lora-384",
    "Lightricks/gemma-3-12b-it-qat-q4_0-unquantized",
    "ByteDance/Z-Image-Turbo",
    "ByteDance/Z-Image-Loras",
    "openbmb/VoxCPM2",
    "Intel/dpt-hybrid-midas",
    "hr16/yolox-onnx",
    "hr16/DWPose-TorchScript-BatchSize5",
]

_hf_meta_cache_dir: Path | None = None
_hf_meta_inflight: set[str] = set()        # 防止并发重复请求
_hf_meta_lock = threading.Lock()
_hf_status: dict[str, Any] = {
    "running": False,
    "triggered_by": None,        # "background" | "manual" | None
    "last_run": None,
    "last_success": None,
    "last_error": None,
    "fetched": 0,
    "failed": 0,
    "total": 0,
}
_hf_stop_event = threading.Event()
_hf_cancel_event = threading.Event()  # 手动触发可取消
_hf_bg_thread: threading.Thread | None = None

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
    # ════════════════════════════════════════════════════════════
    # 2026-06-10: 启动器 LTX_MODELS 全部模型补全为注册表条目
    # ════════════════════════════════════════════════════════════
    # ── LTX-Video 2.3 蒸馏版（BF16 / FP8） ──
    "ltx-2.3-22b-distilled-fp8": ModelRegistryEntry(
        model_id="ltx-2.3-22b-distilled-fp8",
        name="LTX-Video 2.3 蒸馏版 FP8",
        description="LTX-Video 2.3 蒸馏版 FP8（220亿参数DiT架构，FP8量化显存约29GB，8步极速推理CFG=1，支持视频+音频同步生成）",
        source="official",
        repo_id="Lightricks/LTX-2.3-22b-distilled",
        filename="ltx-2.3-22b-distilled-fp8.safetensors",
        size_gb=29.0,
        quantization="fp8",
        variant="distilled",
        min_vram_gb=32,
        recommended_tiers=["ultra", "high"],
        pipeline_mode="video",
        tags=["official", "video", "checkpoint", "distilled", "fast"],
        model_category="video",
        usage_scenario="短视频快速生成，8步极速出片；FP8量化降低显存约40%，适合 RTX 4090/5090 等消费级显卡",
        trigger_word="",
        requires=[],
        is_folder=False,
    ),
    "ltx-2.3-22b-distilled": ModelRegistryEntry(
        model_id="ltx-2.3-22b-distilled",
        name="LTX-Video 2.3 蒸馏版 BF16",
        description="LTX-Video 2.3 蒸馏版 BF16全精度（220亿参数，保留完整BF16精度权重，画质与细节优于FP8，显存约46GB，8步推理）",
        source="official",
        repo_id="Lightricks/LTX-2.3-22b-distilled",
        filename="ltx-2.3-22b-distilled.safetensors",
        size_gb=46.0,
        quantization="bf16",
        variant="distilled",
        min_vram_gb=48,
        recommended_tiers=["ultra"],
        pipeline_mode="video",
        tags=["official", "video", "checkpoint", "distilled", "high-quality"],
        model_category="video",
        usage_scenario="对画质有极致要求的专业视频生成；保留完整BF16精度权重，8步快速推理",
        trigger_word="",
        requires=[],
        is_folder=False,
    ),
    "ltx-2.3-22b-distilled-1.1": ModelRegistryEntry(
        model_id="ltx-2.3-22b-distilled-1.1",
        name="LTX-Video 2.3 蒸馏版 v1.1",
        description="LTX-Video 2.3 蒸馏版 v1.1 BF16（220亿参数，v1.1迭代版，生成稳定性与画面一致性改进，8步快速推理）",
        source="official",
        repo_id="Lightricks/LTX-2.3-22b-distilled",
        filename="ltx-2.3-22b-distilled-1.1.safetensors",
        size_gb=46.0,
        quantization="bf16",
        variant="distilled-v1.1",
        min_vram_gb=48,
        recommended_tiers=["ultra"],
        pipeline_mode="video",
        tags=["official", "video", "checkpoint", "distilled", "stable"],
        model_category="video",
        usage_scenario="v1.1 稳定迭代版，改进生成稳定性与画面一致性，专业视频生成推荐",
        trigger_word="",
        requires=[],
        is_folder=False,
    ),
    "ltx-2.3-22b-dev-fp8": ModelRegistryEntry(
        model_id="ltx-2.3-22b-dev-fp8",
        name="LTX-Video 2.3 开发版 FP8",
        description="LTX-Video 2.3 开发版 FP8（220亿参数，支持完整CFG引导3.0-3.5，20-40+步推理，提示词遵循度与画面可控性更强，适合精细控制创作）",
        source="official",
        repo_id="Lightricks/LTX-2.3-22b-dev",
        filename="ltx-2.3-22b-dev-fp8.safetensors",
        size_gb=29.0,
        quantization="fp8",
        variant="dev",
        min_vram_gb=32,
        recommended_tiers=["ultra", "high"],
        pipeline_mode="video",
        tags=["official", "video", "checkpoint", "dev", "high-control"],
        model_category="video",
        usage_scenario="精细控制创作，提示词遵循度高；支持完整CFG引导，20-40步推理生成更可控的画面",
        trigger_word="",
        requires=[],
        is_folder=False,
    ),
    # ── LTX-Video Pro LoRA / IC-LoRA / 增强 ──
    "ltx-2-19b-distilled-lora-384": ModelRegistryEntry(
        model_id="ltx-2-19b-distilled-lora-384",
        name="LTX-2 19B Pro LoRA",
        description="LTX-2 19B蒸馏版专用LoRA（Rank=384，Pro模式高质量视频生成必需，支持深度引导与姿态驱动等高级控制）",
        source="official",
        repo_id="Lightricks/LTX-2-19b-distilled-lora-384",
        filename="ltx-2-19b-distilled-lora-384.safetensors",
        size_gb=0.3,
        quantization="bf16",
        variant="pro-lora",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["official", "video", "lora", "pro-mode"],
        model_category="lora",
        usage_scenario="Pro模式视频生成：384步推理 + Pro LoRA 获得更高质量输出；支持深度/姿态驱动等高级控制",
        trigger_word="",
        requires=["ltx-2.3-22b-distilled"],
    ),
    "ltx-2.3-22b-ic-lora-union-control": ModelRegistryEntry(
        model_id="ltx-2.3-22b-ic-lora-union-control",
        name="IC-LoRA Union Control",
        description="LTX-Video 2.3 IC-LoRA联合控制模型（融合深度图/Canny边缘/姿态多条件控制，ref=0.5，实现构图与场景布局细粒度引导）",
        source="official",
        repo_id="Lightricks/LTX-2.3-22b-ic-lora-union-control",
        filename="ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors",
        size_gb=0.3,
        quantization="bf16",
        variant="ic-lora-union",
        min_vram_gb=4,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="ic_lora",
        tags=["official", "video", "lora", "ic-lora", "depth", "canny", "pose"],
        model_category="lora",
        usage_scenario="视频迁移控制：融合深度图/Canny边缘/姿态多条件控制，ref=0.5 实现构图与场景布局的细粒度引导",
        trigger_word="",
        requires=[],
    ),
    "ltx-2.3-spatial-upscaler": ModelRegistryEntry(
        model_id="ltx-2.3-spatial-upscaler",
        name="LTX-Video 2.3 空间升频器 x2",
        description="LTX-Video 2.3 空间升频器（2倍空间分辨率上采样，4096px输入 -> 8192px输出，与潜在扩散模型配套的高质量超分模型）",
        source="official",
        repo_id="Lightricks/LTX-2.3-spatial-upscaler-x2",
        filename="ltx-2.3-spatial-upscaler-x2-1.0.safetensors",
        size_gb=2.5,
        quantization="bf16",
        variant="upscaler-x2",
        min_vram_gb=8,
        recommended_tiers=["ultra", "high"],
        pipeline_mode="upscaler",
        tags=["official", "video", "upscaler", "spatial"],
        model_category="upscaler",
        usage_scenario="视频高清放大专用，配合视频生成使用；2倍空间分辨率上采样",
        trigger_word="",
        requires=[],
    ),
    "ltx2.3-crisp-enhance": ModelRegistryEntry(
        model_id="ltx2.3-crisp-enhance",
        name="LTX 清晰增强 LoRA",
        description="LTX-Video 2.3 锐利增强LoRA（提升视频细节锐度与边缘清晰度，增强纹理/发丝/衣物等高频信息，适合写实风格）",
        source="official",
        repo_id="Lightricks/LTX-Video-Enhance",
        filename="LTX2.3_Crisp_Enhance.safetensors",
        size_gb=0.2,
        quantization="bf16",
        variant="enhance-crisp",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["official", "video", "lora", "enhance", "crisp"],
        model_category="lora",
        usage_scenario="视频清晰度增强：提升画面锐度与边缘清晰度，强化纹理/发丝/衣物等高频细节",
        trigger_word="crisp, sharp, detailed",
        requires=["ltx-2.3-22b-distilled"],
    ),
    "ltx2.3-soft-enhance": ModelRegistryEntry(
        model_id="ltx2.3-soft-enhance",
        name="LTX 柔和增强 LoRA",
        description="LTX-Video 2.3 柔和增强LoRA（柔化画面边缘与光影过渡，营造梦幻柔焦视觉效果，适合人像与浪漫场景）",
        source="official",
        repo_id="Lightricks/LTX-Video-Enhance",
        filename="LTX2.3_Soft_Enhance.safetensors",
        size_gb=0.2,
        quantization="bf16",
        variant="enhance-soft",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["official", "video", "lora", "enhance", "soft"],
        model_category="lora",
        usage_scenario="视频柔焦增强：柔化画面边缘与光影过渡，营造梦幻柔焦效果，适合人像与浪漫场景",
        trigger_word="soft, gentle, dreamy",
        requires=["ltx-2.3-22b-distilled"],
    ),
    # ── LTX Z-Image 图像核心 ──
    # 2026-06-10: 原 key "z-image-turbo-bf16" 改名为 "z-image-turbo",与启动器 LTX_MODELS 对齐
    "z-image-turbo": ModelRegistryEntry(
        model_id="z-image-turbo",
        name="Z-Image Turbo BF16",
        description="Z-Image Turbo BF16（6B参数国产AI绘图大模型，16GB显存即可8步快速出图，质量优秀）",
        source="official",
        repo_id="ByteDance/Z-Image-Turbo",
        filename="Z-Image-Turbo-BF16.safetensors",
        size_gb=12.0,
        quantization="bf16",
        variant="turbo",
        min_vram_gb=16,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="image",
        tags=["official", "image", "checkpoint", "z-image", "turbo", "chinese"],
        model_category="image",
        usage_scenario="国产6B参数 AI 绘图大模型，16GB 显存即可 8 步出图；速度快、质量优、中文提示词友好",
        trigger_word="",
        requires=[],
    ),
    "zit-2602nsw": ModelRegistryEntry(
        model_id="zit-2602nsw",
        name="ZIT-2602NSW 写实",
        description="ZIT-2602NSW（基于Z-Image Turbo微调的写实风格大模型，擅长人像摄影和写实风格图像）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="ZIT-2602NSW byStableYogi.safetensors",
        size_gb=12.0,
        quantization="bf16",
        variant="realistic",
        min_vram_gb=16,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="image",
        tags=["community", "image", "checkpoint", "z-image", "realistic", "portrait"],
        model_category="image",
        usage_scenario="Z-Image Turbo 微调写实版本，擅长人像摄影和写实风格图像；StableYogi 调优",
        trigger_word="",
        requires=[],
    ),
    # ── Z-Image 风格 LoRA（社区精选） ──
    "z-image-90s-animation": ModelRegistryEntry(
        model_id="z-image-90s-animation",
        name="90年代经典动画风格",
        description="90年代经典动画风格LoRA（复古赛璐璐动画质感，怀旧动画迷必选）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="90sAnimationStyle.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="style-90s",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium", "low"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "animation", "90s"],
        model_category="lora",
        usage_scenario="90年代赛璐璐动画风格、复古日式动画怀旧场景",
        trigger_word="90s animation style, retro cartoon",
        requires=["z-image-turbo"],
    ),
    "z-image-cinematic-scifi-cyberpunk": ModelRegistryEntry(
        model_id="z-image-cinematic-scifi-cyberpunk",
        name="赛博朋克电影风格",
        description="科幻赛博朋克电影风格LoRA（霓虹灯光、未来都市夜景，触发词: sci-fi, cyberpunk, cinematic）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="Cinematic_sci-fi-cyberpunk.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="style-scifi-cyberpunk",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "scifi", "cyberpunk"],
        model_category="lora",
        usage_scenario="科幻赛博朋克主题：霓虹灯、未来都市、雨夜街道、机甲人像",
        trigger_word="sci-fi, cyberpunk, cinematic",
        requires=["z-image-turbo"],
    ),
    "z-image-claymation": ModelRegistryEntry(
        model_id="z-image-claymation",
        name="黏土动画风格",
        description="黏土动画风格LoRA（定格动画黏土质感，Aardman/阿德曼工作室风格）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="Claymation.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="style-claymation",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "claymation"],
        model_category="lora",
        usage_scenario="黏土定格动画风格，适合儿童内容、可爱角色",
        trigger_word="claymation, clay animation",
        requires=["z-image-turbo"],
    ),
    "z-image-cozy-felt": ModelRegistryEntry(
        model_id="z-image-cozy-felt",
        name="温暖毛毡风格",
        description="温暖毛毡风格LoRA（手工毛毡布艺柔软纹理）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="CozyFelt.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="style-cozy-felt",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "felt", "handcraft"],
        model_category="lora",
        usage_scenario="毛毡手工艺风格，适合温馨家居、儿童绘本风格",
        trigger_word="cozy felt, felt craft",
        requires=["z-image-turbo"],
    ),
    "z-image-fantasy-puppet": ModelRegistryEntry(
        model_id="z-image-fantasy-puppet",
        name="奇幻木偶风格",
        description="奇幻木偶风格LoRA（提线木偶质感与动态）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="FantasyPuppetStyle.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="style-fantasy-puppet",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "puppet"],
        model_category="lora",
        usage_scenario="提线木偶风格，适合奇幻主题、戏剧感场景",
        trigger_word="fantasy puppet, puppet style",
        requires=["z-image-turbo"],
    ),
    "z-image-fantasy-anime": ModelRegistryEntry(
        model_id="z-image-fantasy-anime",
        name="奇幻动漫风格",
        description="奇幻动漫风格LoRA（日式动画精致画面与奇幻世界观）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="Fantasy_Anime.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="style-fantasy-anime",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "anime", "fantasy"],
        model_category="lora",
        usage_scenario="日式奇幻动漫风格，适合魔法、异世界题材",
        trigger_word="fantasy anime, magical anime",
        requires=["z-image-turbo"],
    ),
    "z-image-fantasy-painterly": ModelRegistryEntry(
        model_id="z-image-fantasy-painterly",
        name="奇幻绘画风格",
        description="奇幻绘画风格LoRA（油画/水彩手绘笔触质感）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="Fantasy_Painterly.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="style-painterly",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "painterly"],
        model_category="lora",
        usage_scenario="手绘油画/水彩质感，适合艺术插画、概念设计",
        trigger_word="painterly, fantasy painting",
        requires=["z-image-turbo"],
    ),
    "z-image-fantasy-realism": ModelRegistryEntry(
        model_id="z-image-fantasy-realism",
        name="奇幻写实风格",
        description="奇幻写实风格LoRA（写实基础融入奇幻元素）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="Fantasy_Realism.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="style-fantasy-realism",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "realism", "fantasy"],
        model_category="lora",
        usage_scenario="奇幻写实风格，适合写实基础上的奇幻创作",
        trigger_word="fantasy realism, magical realism",
        requires=["z-image-turbo"],
    ),
    "z-image-luxe-sensual": ModelRegistryEntry(
        model_id="z-image-luxe-sensual",
        name="奢华感官风格",
        description="奢华感官风格LoRA（高端质感柔光与金属反光）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="Luxe_Sensual.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="style-luxe",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "luxury"],
        model_category="lora",
        usage_scenario="高端奢华质感，适合商业广告、产品摄影",
        trigger_word="luxe, sensual, luxury",
        requires=["z-image-turbo"],
    ),
    "z-image-papercutout": ModelRegistryEntry(
        model_id="z-image-papercutout",
        name="纸雕剪纸风格",
        description="纸雕剪纸风格LoRA（层叠剪纸立体效果）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="PaperCutOutStyle.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="style-papercut",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "papercut"],
        model_category="lora",
        usage_scenario="纸艺剪纸风格，适合手工艺术、节日主题",
        trigger_word="paper cut, paper craft, papercut",
        requires=["z-image-turbo"],
    ),
    "z-image-pixar-toon": ModelRegistryEntry(
        model_id="z-image-pixar-toon",
        name="皮克斯卡通风格",
        description="皮克斯卡通风格LoRA（3D卡通渲染质感，皮克斯/迪士尼风格）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="Pixar_Toon.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="style-pixar",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "pixar", "3d-cartoon"],
        model_category="lora",
        usage_scenario="3D卡通渲染，适合皮克斯/迪士尼风格动画、可爱角色",
        trigger_word="pixar style, 3d cartoon, pixar toon",
        requires=["z-image-turbo"],
    ),
    "z-image-post-apocalyptic": ModelRegistryEntry(
        model_id="z-image-post-apocalyptic",
        name="末世废土风格",
        description="末世废土风格LoRA（荒芜废墟、破败建筑）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="Post_Apocalyptic.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="style-post-apocalyptic",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "post-apocalyptic"],
        model_category="lora",
        usage_scenario="末世废土风格，适合《疯狂的麦克斯》《地铁》系列题材",
        trigger_word="post-apocalyptic, wasteland, ruins",
        requires=["z-image-turbo"],
    ),
    "z-image-wild-west": ModelRegistryEntry(
        model_id="z-image-wild-west",
        name="西部荒野风格",
        description="西部荒野风格LoRA（牛仔、荒漠小镇、美式西部）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="Wild_West.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="style-wild-west",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "western"],
        model_category="lora",
        usage_scenario="美式西部牛仔风格，适合西部片、荒野题材",
        trigger_word="wild west, cowboy, western",
        requires=["z-image-turbo"],
    ),
    "z-image-portrait-aesthetic": ModelRegistryEntry(
        model_id="z-image-portrait-aesthetic",
        name="Z-Image 人像美学",
        description="Z-Image人像美学增强LoRA（优化人像肤色光影和美感）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="Z-Iamge-人像美学.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="portrait-aesthetic",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "portrait"],
        model_category="lora",
        usage_scenario="Z-Image 人像美学增强，优化人像肤色、光影、肤感",
        trigger_word="",
        requires=["z-image-turbo"],
    ),
    "z-image-fun-distill-8steps": ModelRegistryEntry(
        model_id="z-image-fun-distill-8steps",
        name="Z-Image 8步蒸馏加速",
        description="Z-Image 蒸馏加速LoRA（8步生成高质量图像，适合快速预览、批量生成）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="Z-Image-Fun-Lora-Distill-8-Steps-2603-ComfyUI.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="distill-fast",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium", "low"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "distill", "fast"],
        model_category="lora",
        usage_scenario="8步快速出图，适合预览、批量生成；保留 Z-Image 基础质量",
        trigger_word="",
        requires=["z-image-turbo"],
    ),
    "z-image-east-aesthetic": ModelRegistryEntry(
        model_id="z-image-east-aesthetic",
        name="轻柔东方审美人像",
        description="轻柔东方审美人像摄影LoRA（东方美学柔和光影，适合中式写真、古风人像）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="Z-Image｜轻柔东方审美人像摄影写真风格_v1.0.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="east-aesthetic",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "chinese-style", "portrait"],
        model_category="lora",
        usage_scenario="东方审美人像摄影，中式写真、古风人像、汉服、新中式美学",
        trigger_word="",
        requires=["z-image-turbo"],
    ),
    "z-image-detailed-eyes-v2": ModelRegistryEntry(
        model_id="z-image-detailed-eyes-v2",
        name="眼睛细节增强 V2",
        description="眼睛细节增强V2 LoRA（提升眼部细节和眼神表现力）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="Z-image-眼睛细节增强-DetailedEyes-LoRA_V2.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="detailed-eyes",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "eyes", "detail"],
        model_category="lora",
        usage_scenario="人像眼部细节增强，提升眼神、虹膜纹理、瞳孔质感",
        trigger_word="detailed eyes",
        requires=["z-image-turbo"],
    ),
    "z-image-hd-portrait": ModelRegistryEntry(
        model_id="z-image-hd-portrait",
        name="Z-Image 高清人像",
        description="Z-Image 高清人像增强LoRA（提升人像清晰度和细节）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="Z-image-高清人像.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="hd-portrait",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "portrait", "hd"],
        model_category="lora",
        usage_scenario="人像清晰度增强，提升人像细节表现",
        trigger_word="",
        requires=["z-image-turbo"],
    ),
    "z-image-cine-chiaroscuro": ModelRegistryEntry(
        model_id="z-image-cine-chiaroscuro",
        name="电影光 Chiaroscuro",
        description="电影光效明暗对比风格LoRA（触发词: chiaroscuro, cinematic lighting）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="ZIB-电影光Chiaroscuro and Cinematic Lighting Style.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="cine-chiaroscuro",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "cinematic", "lighting"],
        model_category="lora",
        usage_scenario="电影级明暗对比布光，适合戏剧化人像、黑白摄影",
        trigger_word="chiaroscuro, cinematic lighting",
        requires=["z-image-turbo"],
    ),
    "z-image-rembrandt-lighting": ModelRegistryEntry(
        model_id="z-image-rembrandt-lighting",
        name="伦勃朗光线",
        description="伦勃朗光线风格LoRA（经典三角光人像布光，伦勃朗油画质感）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="ZIT-伦勃朗光线rembrandt_ZIT_tyler_x_harris.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="rembrandt-lighting",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "lighting", "rembrandt"],
        model_category="lora",
        usage_scenario="经典三角光人像，伦勃朗油画人像布光质感",
        trigger_word="rembrandt lighting",
        requires=["z-image-turbo"],
    ),
    "z-image-studio-photo": ModelRegistryEntry(
        model_id="z-image-studio-photo",
        name="影棚摄影 V2",
        description="影棚摄影风格V2 LoRA（专业影棚布光效果，Photolab v2 调优）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="ZIT-影棚摄影photolab_v2.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="studio-photo",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "studio", "photography"],
        model_category="lora",
        usage_scenario="专业影棚人像、产品摄影",
        trigger_word="photolab, studio photography",
        requires=["z-image-turbo"],
    ),
    "z-image-cine-lighting": ModelRegistryEntry(
        model_id="z-image-cine-lighting",
        name="电影级明暗对比",
        description="电影级明暗对比光效LoRA（好莱坞式电影布光）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="ZIT-电影光Cinematic Chiaroscuro Lighting.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="cine-lighting",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "cinematic", "lighting"],
        model_category="lora",
        usage_scenario="好莱坞电影布光质感，适合 MV、海报级人像",
        trigger_word="cinematic chiaroscuro",
        requires=["z-image-turbo"],
    ),
    "z-image-dark-cine": ModelRegistryEntry(
        model_id="z-image-dark-cine",
        name="电影暗调",
        description="电影暗调风格LoRA（低调照明，悬疑氛围）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="ZIT-电影黑暗MschCine26_V1.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="dark-cine",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "dark", "cinematic"],
        model_category="lora",
        usage_scenario="暗调电影质感，悬疑、惊悚题材首选",
        trigger_word="dark cinematic",
        requires=["z-image-turbo"],
    ),
    "z-image-female-anatomy": ModelRegistryEntry(
        model_id="z-image-female-anatomy",
        name="女性人体解剖学",
        description="女性人体解剖学增强LoRA（优化人体结构比例）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="ZiB-female解剖学_anatomy.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="female-anatomy",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "anatomy"],
        model_category="lora",
        usage_scenario="女性人体结构比例优化，肢体语言自然",
        trigger_word="anatomy",
        requires=["z-image-turbo"],
    ),
    "z-image-asian-mix-v4": ModelRegistryEntry(
        model_id="z-image-asian-mix-v4",
        name="亚洲面孔混合 V4.59C",
        description="亚洲面孔混合模型V4.59C LoRA（优化亚洲人面孔特征）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="hina_zImageTurbo_asianMix_v4.59C-bf16.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="asian-mix",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "asian", "face"],
        model_category="lora",
        usage_scenario="亚洲人面孔特征优化，黄种人面孔细节提升",
        trigger_word="",
        requires=["z-image-turbo"],
    ),
    "z-image-redcraft-aio": ModelRegistryEntry(
        model_id="z-image-redcraft-aio",
        name="RedCraft AIO 综合增强",
        description="RedCraft Z-Image 更新版AIO LoRA（综合增强画质与细节）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="redcraftRedzimageUpdatedDEC03_redzimage15AIO-lora.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="aio",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "aio", "quality"],
        model_category="lora",
        usage_scenario="综合增强 Z-Image 画质与细节，AIO 一体化增强",
        trigger_word="",
        requires=["z-image-turbo"],
    ),
    "z-image-woman877": ModelRegistryEntry(
        model_id="z-image-woman877",
        name="女性人像增强",
        description="女性人像增强LoRA（优化女性面部和人像表现）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="woman877-zimage.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="woman-portrait",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "portrait", "woman"],
        model_category="lora",
        usage_scenario="女性人像增强，优化女性面部、肤质、表现力",
        trigger_word="",
        requires=["z-image-turbo"],
    ),
    "z-image-3d-cartoon-v1": ModelRegistryEntry(
        model_id="z-image-3d-cartoon-v1",
        name="3D 卡通风格 V1",
        description="3D卡通风格V1 LoRA（3D卡通渲染效果）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="z-Image-3D卡通_V1.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="style-3d-cartoon",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "3d-cartoon"],
        model_category="lora",
        usage_scenario="3D卡通渲染，适合 Q 版角色、可爱形象",
        trigger_word="3d cartoon",
        requires=["z-image-turbo"],
    ),
    "z-image-extreme-atmosphere": ModelRegistryEntry(
        model_id="z-image-extreme-atmosphere",
        name="极致氛围光影 V1.0",
        description="极致氛围光影V1.0 LoRA（强化场景氛围感和光影表现力）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="z-image 极致氛围光影LORA_V1.0.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="atmosphere",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "atmosphere", "lighting"],
        model_category="lora",
        usage_scenario="极致场景氛围感强化，适合光影电影感场景",
        trigger_word="",
        requires=["z-image-turbo"],
    ),
    "z-image-empress": ModelRegistryEntry(
        model_id="z-image-empress",
        name="女帝风格",
        description="女帝风格LoRA（高贵冷艳女性形象）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="z-image-女帝-ben_nd.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="empress",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "character", "empress"],
        model_category="lora",
        usage_scenario="高贵冷艳女帝角色形象，适合古风、玄幻女王形象",
        trigger_word="",
        requires=["z-image-turbo"],
    ),
    "z-image-extreme-realism": ModelRegistryEntry(
        model_id="z-image-extreme-realism",
        name="极致写实",
        description="极致写实增强LoRA（照片级真实感）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="z-image-极致写实.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="extreme-realism",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "realism"],
        model_category="lora",
        usage_scenario="极致写实质感，照片级真实感增强",
        trigger_word="",
        requires=["z-image-turbo"],
    ),
    "z-image-detail-v2": ModelRegistryEntry(
        model_id="z-image-detail-v2",
        name="细节增强 V2",
        description="细节增强V2 LoRA（提升画面细节表现力）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="z-image-细节增强v2.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="detail-enhance",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "detail"],
        model_category="lora",
        usage_scenario="通用画面细节增强，提升纹理、质感、清晰度",
        trigger_word="",
        requires=["z-image-turbo"],
    ),
    "z-image-small-emotion": ModelRegistryEntry(
        model_id="z-image-small-emotion",
        name="小情绪风格 V1.1",
        description="小情绪风格V1.1 LoRA（捕捉细腻微妙情绪表达）",
        source="community",
        repo_id="ByteDance/Z-Image-Loras",
        filename="z-image_小情绪_v1.1.safetensors",
        size_gb=0.12,
        quantization="bf16",
        variant="emotion",
        min_vram_gb=2,
        recommended_tiers=["ultra", "high", "medium"],
        pipeline_mode="lora",
        tags=["community", "image", "lora", "z-image", "emotion"],
        model_category="lora",
        usage_scenario="细腻微妙情绪表达，文艺、情绪化人像",
        trigger_word="",
        requires=["z-image-turbo"],
    ),
    # ── 辅助模型：Gemma 文本编码器 ──
    "gemma-3-12b-text-encoder": ModelRegistryEntry(
        model_id="gemma-3-12b-text-encoder",
        name="Gemma 3 12B 文本编码器",
        description="Gemma 3 12B 文本编码器（本地文本编码必需，约23GB，提供高质量 prompt 理解）",
        source="official",
        repo_id="Lightricks/gemma-3-12b-it-qat-q4_0-unquantized",
        filename="gemma-3-12b-it-qat-q4_0-unquantized",
        size_gb=23.0,
        quantization="q4_0",
        variant="text-encoder",
        min_vram_gb=16,
        recommended_tiers=["ultra", "high"],
        pipeline_mode="text_encoder",
        tags=["official", "supporting", "text-encoder", "gemma"],
        model_category="supporting",
        usage_scenario="LTX-Video 本地文本编码必需，提示词理解与编码",
        trigger_word="",
        requires=[],
        is_folder=True,
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
_DIR_CATEGORY_FILE = "model_dir_categories.json"

# 2026-06-10: 目录→分类手动映射缓存,优先于启发式规则
_dir_category_map: dict[str, str] | None = None

# 官方默认的目录→分类映射(内置,可被用户覆盖)
# 设计原则:
#   1. 目录约定是行业标准(ComfyUI/A1111/Forge 均如此),优于文件名推断
#   2. 优先声明明确类型的目录(image/video/controlnet/upscaler 等)
#   3. 通用目录(checkpoints/loras)留给启发式规则和用户自定义
#   4. 混合型目录(同时含lora和checkpoint)映射为基础分类,让_is_likely_lora自动区分lora
_DEFAULT_DIR_CATEGORY_MAP: dict[str, str] = {
    # ── 图像模型目录 ──
    "z_image": "image",
    "z-image": "image",
    "unet": "image",
    "diffusion_models": "image",
    # ── 图像LoRA目录 ──
    "Zimage": "image-lora",
    # ── 控制模型 ──
    "controlnet": "controlnet",
    "t2i_adapter": "controlnet",
    # ── 高清放大 ──
    "upscale_models": "upscaler",
    "latent_upscale_models": "upscaler",
    # ── 辅助模型 ──
    "vae": "supporting",
    "text_encoders": "supporting",
    "clip": "supporting",
    "clip_vision": "supporting",
    "tokenizer": "supporting",
    "scheduler": "supporting",
    "embeddings": "supporting",
    "hypernetworks": "supporting",
    "style_models": "supporting",
    "vae_approx": "supporting",
    "audio_encoders": "supporting",
    "photomaker": "supporting",
    "gligen": "supporting",
    "model_patches": "supporting",
    "frame_interpolation": "supporting",
    "geometry_estimation": "supporting",
    "optical_flow": "supporting",
    "detection": "supporting",
    "background_removal": "supporting",
    "classifiers": "supporting",
}


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


# ─────────────────────────────────────────────────────────────────────────
# 2026-06-10: HuggingFace API 自动元数据获取实现
# ─────────────────────────────────────────────────────────────────────────

def _get_hf_cache_dir() -> Path | None:
    """获取 HF 元数据缓存目录 (<config_dir>/cache/hf_meta/)。"""
    global _hf_meta_cache_dir
    if _hf_meta_cache_dir is not None:
        return _hf_meta_cache_dir
    if _ctx is None:
        return None
    try:
        d = _ctx.config_dir / "cache" / "hf_meta"
        d.mkdir(parents=True, exist_ok=True)
        _hf_meta_cache_dir = d
        return d
    except Exception as e:
        logger.warning("Failed to create HF cache dir: %s", e)
        return None


def _hf_cache_path(repo_id: str, kind: str = "meta") -> Path | None:
    """repo_id → 缓存文件路径 (kind: 'meta' | 'preview')。"""
    d = _get_hf_cache_dir()
    if d is None:
        return None
    safe = repo_id.replace("/", "_").replace("\\", "_")
    return d / f"{kind}_{safe}.json"


def _is_cache_valid(path: Path, ttl: int) -> bool:
    try:
        if not path.is_file():
            return False
        mtime = path.stat().st_mtime
        return (time.time() - mtime) < ttl
    except OSError:
        return False


def _read_cached_dict(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cached_dict(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.debug("Failed to write HF cache %s: %s", path, e)


def _http_get_json(url: str) -> dict | None:
    """同步 HTTP GET,带 UA 和超时。返回 None 表示失败。"""
    try:
        import httpx
        with httpx.Client(timeout=httpx.Timeout(_HF_API_TIMEOUT)) as client:
            r = client.get(url, headers={"User-Agent": _HF_USER_AGENT, "Accept": "application/json"})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.debug("HF HTTP GET failed %s: %s", url, e)
        return None


def _http_get_json_race(urls: list[str]) -> tuple[dict | None, str]:
    """多源竞速 GET JSON：同时向多个 URL 发起请求，返回最先成功的 (data, url)。

    所有源都失败则返回 (None, "")。
    使用 wait(FIRST_COMPLETED) 避免被慢源阻塞。
    """
    if len(urls) <= 1:
        data = _http_get_json(urls[0]) if urls else None
        return data, urls[0] if data else ""

    import concurrent.futures

    def _fetch_one(url: str) -> tuple[dict | None, str]:
        d = _http_get_json(url)
        return d, url

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(urls)) as executor:
            future_map = {executor.submit(_fetch_one, u): u for u in urls}
            # 逐轮检查：谁先完成就先看结果
            done = set()
            remaining = set(future_map.keys())
            while remaining:
                done_new, remaining = concurrent.futures.wait(
                    remaining, timeout=_HF_API_TIMEOUT,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done_new:
                    try:
                        data, url = future.result()
                        if data is not None:
                            # 取消剩余未完成的
                            for f in remaining:
                                f.cancel()
                            return data, url
                    except Exception:
                        continue
                done |= done_new
                if not done_new:
                    break  # 全部超时
    except Exception:
        pass
    return None, ""


def _http_head_ok(url: str) -> bool:
    """检查 URL 是否存在 (HEAD 请求)。"""
    try:
        import httpx
        with httpx.Client(timeout=httpx.Timeout(5.0)) as client:
            r = client.head(url, headers={"User-Agent": _HF_USER_AGENT}, follow_redirects=True)
            return 200 <= r.status_code < 400
    except Exception:
        return False


def _http_head_race(urls: list[str]) -> tuple[bool, str]:
    """多源竞速 HEAD：同时检查多个 URL，返回最先成功的 (True, url)。

    全部失败返回 (False, "")。
    """
    if len(urls) <= 1:
        ok = _http_head_ok(urls[0]) if urls else False
        return ok, urls[0] if ok else ""

    import concurrent.futures

    def _check_one(url: str) -> tuple[bool, str]:
        return _http_head_ok(url), url

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(urls)) as executor:
            future_map = {executor.submit(_check_one, u): u for u in urls}
            remaining = set(future_map.keys())
            while remaining:
                done_new, remaining = concurrent.futures.wait(
                    remaining, timeout=5.0,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done_new:
                    try:
                        ok, url = future.result()
                        if ok:
                            for f in remaining:
                                f.cancel()
                            return True, url
                    except Exception:
                        continue
                if not done_new:
                    break
    except Exception:
        pass
    return False, ""


def _fetch_hf_metadata_sync(repo_id: str, *, force: bool = False) -> dict | None:
    """从 HF API 拉取单个 repo 的元数据,带本地 TTL 缓存。

    返回字段: {description, tags, downloads, last_modified, size_gb, pipeline_tag, fetched_at}
    任何异常或失败都返回 None (不抛错),不影响主流程。

    自适应源选择策略:
      - 首次请求: 竞速所有镜像，记住最快源
      - 后续请求: 直接用最快源（省去竞速开销）
      - 快源连续失败超过阈值: 降级回竞速，重新选源
    """
    global _hf_preferred_api_base, _hf_preferred_consecutive_fails
    if not repo_id or "/" not in repo_id:
        return None

    cache_path = _hf_cache_path(repo_id, "meta")
    if cache_path is None:
        return None

    # 1) 缓存命中
    if not force and _is_cache_valid(cache_path, _HF_META_TTL_SECONDS):
        cached = _read_cached_dict(cache_path)
        if cached and cached.get("repo_id") == repo_id:
            return cached

    # 2) 去重: 同 repo 并发只跑一次
    with _hf_meta_lock:
        if repo_id in _hf_meta_inflight:
            return _read_cached_dict(cache_path)  # 等待中,返回已有缓存(可能空)
        _hf_meta_inflight.add(repo_id)
    try:
        result: dict | None = None
        winning_url = ""

        # 3) 自适应源选择：有快源且未降级 → 直接用快源；否则竞速
        if _hf_preferred_api_base and _hf_preferred_consecutive_fails < _HF_PREFERRED_MAX_FAILS:
            # 直接用快源
            url = f"{_hf_preferred_api_base.rstrip('/')}/{repo_id}"
            data = _http_get_json(url)
            if data is not None:
                winning_url = url
            else:
                # 快源失败，计数+1，降级到竞速
                _hf_preferred_consecutive_fails += 1
                logger.info("[HF] 快源 %s 失败 (%d/%d)，降级竞速",
                            _hf_preferred_api_base, _hf_preferred_consecutive_fails, _HF_PREFERRED_MAX_FAILS)
                urls = [f"{base.rstrip('/')}/{repo_id}" for base in _HF_API_MIRRORS]
                data, winning_url = _http_get_json_race(urls)
        else:
            # 无快源或已降级 → 竞速
            urls = [f"{base.rstrip('/')}/{repo_id}" for base in _HF_API_MIRRORS]
            data, winning_url = _http_get_json_race(urls)

        if data:
            # 记录/更新快源
            if winning_url:
                for base in _HF_API_MIRRORS:
                    if winning_url.startswith(base):
                        if _hf_preferred_api_base != base:
                            logger.info("[HF] 更新快源: %s", base)
                            _hf_preferred_api_base = base
                            # 同步更新 resolve 快源
                            idx = _HF_API_MIRRORS.index(base)
                            if idx < len(_HF_RESOLVE_MIRRORS):
                                _hf_preferred_resolve = _HF_RESOLVE_MIRRORS[idx]
                        _hf_preferred_consecutive_fails = 0
                        break
            # 提取 size_gb 从 siblings 累加
            size_bytes = 0
            siblings_list: list[str] = []
            for s in data.get("siblings", []) or []:
                rfilename = s.get("rfilename") or s.get("filename") or ""
                if rfilename:
                    siblings_list.append(rfilename)
                sz = s.get("size")
                if isinstance(sz, (int, float)) and sz:
                    size_bytes += int(sz)
            description = (
                data.get("description")
                or (data.get("cardData") or {}).get("description")
                or ""
            )
            if isinstance(description, dict):
                # 有时 description 字段是 {"text": "..."}
                description = description.get("text", "") or ""
            result = {
                "repo_id": repo_id,
                "description": (description or "").strip(),
                "tags": list(data.get("tags") or []),
                "downloads": int(data.get("downloads") or 0),
                "likes": int(data.get("likes") or 0),
                "last_modified": data.get("lastModified"),
                "created_at": data.get("createdAt"),
                "pipeline_tag": data.get("pipeline_tag") or "",
                "library_name": data.get("library_name") or "",
                "size_gb": round(size_bytes / 1024**3, 2) if size_bytes > 0 else 0.0,
                # 保存 siblings 列表(文件名),供 _probe_hf_file_preview_sync 复用
                "siblings": siblings_list,
                "fetched_at": time.time(),
            }

        if result is not None:
            _write_cached_dict(cache_path, result)
        return result
    finally:
        with _hf_meta_lock:
            _hf_meta_inflight.discard(repo_id)


def _probe_hf_preview_sync(repo_id: str) -> str | None:
    """探测 HF repo 的预览图,返回可直接 URL,带缓存。

    策略: 依次尝试 preview.png / preview.jpg / cover.png 等,HEAD 命中即返回。
    缓存 '不存在' 也缓存,TTL 更长。
    """
    if not repo_id or "/" not in repo_id:
        return None

    cache_path = _hf_cache_path(repo_id, "preview")
    if cache_path is None:
        return None

    if _is_cache_valid(cache_path, _HF_PREVIEW_TTL_SECONDS):
        cached = _read_cached_dict(cache_path)
        if cached and cached.get("repo_id") == repo_id:
            return cached.get("preview_url")  # 可能是 None (代表"已知无预览")

    for fname in _HF_PREVIEW_CANDIDATES:
        # 竞速：同时向所有镜像发起 HEAD，谁先返回 200 就用谁
        urls = [f"{mirror}/{repo_id}/resolve/main/{fname}" for mirror in _HF_RESOLVE_MIRRORS]
        ok, winning_url = _http_head_race(urls)
        if ok and winning_url:
            _write_cached_dict(cache_path, {"repo_id": repo_id, "preview_url": winning_url, "fetched_at": time.time()})
            return winning_url

    # 缓存"未找到"避免重复探测
    _write_cached_dict(cache_path, {"repo_id": repo_id, "preview_url": None, "fetched_at": time.time()})
    return None


# 2026-06-10: 单文件预览候选后缀(按 HF 社区惯例)
_HF_FILE_PREVIEW_EXTS = [".png", ".jpg", ".jpeg", ".webp", ".preview.png", ".preview.jpg", ".sample.png"]


def _probe_hf_file_preview_sync(repo_id: str, filename: str) -> str | None:
    """探测 HF repo 中特定文件的预览图 URL(不发起 HTTP,直接读缓存的 siblings 列表)。

    HF 社区惯例: safetensors 文件通常配同名 .png / .jpg 预览。
    例如 `90sAnimationStyle.safetensors` → `90sAnimationStyle.png`

    匹配优先级(从高到低):
      1) {basename}.png
      2) {basename}.jpg
      3) {basename}.webp
      4) {basename}.preview.png
      5) samples/{basename}.png (社区常见子目录)
      6) examples/{basename}.png

    返回可直接访问的 HF resolve URL,失败返回 None。
    """
    if not repo_id or not filename or "/" not in repo_id:
        return None
    # 取不带后缀的 basename
    basename = filename
    for ext in (".safetensors", ".bin", ".pt", ".ckpt", ".gguf"):
        if basename.lower().endswith(ext):
            basename = basename[: -len(ext)]
            break
    if not basename:
        return None

    # 读 meta 缓存,拿 siblings 列表
    cache_path = _hf_cache_path(repo_id, "meta")
    if cache_path is None:
        return None
    meta = _read_cached_dict(cache_path)
    siblings = meta.get("siblings") if meta else None
    if not siblings:
        return None

    # 把 siblings 转成 set 加速查找(忽略大小写)
    siblings_lower = {s.lower(): s for s in siblings if s}

    # 按优先级尝试后缀
    primary_mirror = _HF_RESOLVE_MIRRORS[0]  # 国内镜像优先
    for ext in _HF_FILE_PREVIEW_EXTS:
        candidate = (basename + ext).lower()
        if candidate in siblings_lower:
            actual = siblings_lower[candidate]
            return f"{primary_mirror}/{repo_id}/resolve/main/{actual}"
    # 尝试子目录
    for sub in ("samples/", "examples/", "preview/", "previews/"):
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            candidate = (sub + basename + ext).lower()
            if candidate in siblings_lower:
                actual = siblings_lower[candidate]
                return f"{primary_mirror}/{repo_id}/resolve/main/{actual}"
    return None


def _enrich_entry_with_hf(entry: ModelRegistryEntry) -> dict:
    """合并 builtin 字段和 HF API 字段,builtin override 优先。

    优先级: builtin (非空值) > HF API
    返回一个 dict,字段名对齐 _get_registry_status 现有输出。
    """
    base = _entry_to_dict(entry)
    # 已经有手填的优质 description? 直接用,不再请求 HF
    if base.get("description"):
        # 仍然尝试获取 size_gb 和 preview_url (手填的 size 可能是错的)
        hf_meta = _fetch_hf_metadata_sync(entry.repo_id)
        if hf_meta:
            if not base.get("size_gb"):
                base["size_gb"] = hf_meta.get("size_gb", 0.0)
            # 合并 tags (内置 + HF,去重)
            merged_tags = list(base.get("tags") or [])
            for t in hf_meta.get("tags", []):
                if t not in merged_tags:
                    merged_tags.append(t)
            base["tags"] = merged_tags
            base["hf_downloads"] = hf_meta.get("downloads", 0)
            base["hf_likes"] = hf_meta.get("likes", 0)
            base["hf_last_modified"] = hf_meta.get("last_modified")
            base["hf_fetched_at"] = hf_meta.get("fetched_at")
        base["preview_url"] = _resolve_preview_url(entry) or ""
        return base

    # 没有手填 description → 走 HF API 拉取
    hf_meta = _fetch_hf_metadata_sync(entry.repo_id)
    if hf_meta:
        # 用 HF 名称 (如 "Lightricks/LTX-Video") 派生人类可读名
        if not base.get("name") or base["name"] == base["model_id"]:
            base["name"] = repo_id_to_display_name(entry.repo_id, hf_meta.get("tags", []))
        base["description"] = hf_meta.get("description", "")
        if not base.get("size_gb"):
            base["size_gb"] = hf_meta.get("size_gb", 0.0)
        merged_tags = list(base.get("tags") or [])
        for t in hf_meta.get("tags", []):
            if t not in merged_tags:
                merged_tags.append(t)
        base["tags"] = merged_tags
        base["hf_downloads"] = hf_meta.get("downloads", 0)
        base["hf_likes"] = hf_meta.get("likes", 0)
        base["hf_last_modified"] = hf_meta.get("last_modified")
        base["hf_fetched_at"] = hf_meta.get("fetched_at")
        # 推导 usage_scenario (从 pipeline_tag)
        if not base.get("usage_scenario") and hf_meta.get("pipeline_tag"):
            base["usage_scenario"] = _pipeline_to_scenario(hf_meta["pipeline_tag"])
    else:
        # HF 也失败 → 至少有 repo_id 派生名
        if not base.get("name") or base["name"] == base["model_id"]:
            base["name"] = repo_id_to_display_name(entry.repo_id, [])

    base["preview_url"] = _resolve_preview_url(entry) or ""
    return base


def _resolve_preview_url(entry: "ModelRegistryEntry") -> str:
    """解析 entry 的预览图 URL。
    优先级: 单文件预览(走 siblings 缓存) > 仓库级预览(probe)。

    2026-06-10: 修复 ByteDance/Z-Image-Loras 等多文件仓库的预览图错乱——
    40+ 个 LoRA 共用一个仓库级 preview.png,现在按文件名匹配同名 .png/.jpg。
    """
    if not entry.repo_id or not entry.filename:
        return ""
    # 1) 单文件预览(基于已缓存的 siblings 列表,0 HTTP 请求)
    file_url = _probe_hf_file_preview_sync(entry.repo_id, entry.filename)
    if file_url:
        return file_url
    # 2) 回退到仓库级预览(probe HEAD 探测,有缓存)
    return _probe_hf_preview_sync(entry.repo_id) or ""


def repo_id_to_display_name(repo_id: str, tags: list[str]) -> str:
    """从 repo_id 和 tags 派生人类可读名。

    规则:
      - 末尾段作为基础名
      - 含 'lora' tag → "LoRA: xxx"
      - 含 'image' tag → "Image: xxx"
      - 含 'video' tag → "Video: xxx"
    """
    if not repo_id:
        return ""
    last = repo_id.split("/")[-1]
    pretty = last.replace("-", " ").replace("_", " ").strip()
    tags_lower = {t.lower() for t in tags}
    if "lora" in tags_lower or "loras" in tags_lower:
        return f"LoRA · {pretty}"
    if "video" in tags_lower or "text-to-video" in tags_lower:
        return f"Video · {pretty}"
    if "image" in tags_lower or "text-to-image" in tags_lower:
        return f"Image · {pretty}"
    return pretty


def _pipeline_to_scenario(pipeline_tag: str) -> str:
    """HF pipeline_tag → 中文 usage_scenario 短句。"""
    mapping = {
        "text-to-video": "文生视频",
        "image-to-video": "图生视频",
        "text-to-image": "文生图",
        "image-to-image": "图生图",
        "text-to-speech": "语音合成",
        "automatic-speech-recognition": "语音识别",
        "depth-estimation": "深度估计",
        "pose-estimation": "姿态估计",
        "object-detection": "目标检测",
        "image-classification": "图像分类",
    }
    return mapping.get(pipeline_tag, pipeline_tag)


def _refresh_all_hf_metadata(force: bool = False, triggered_by: str = "background") -> dict:
    """遍历 _merged_registry 中所有条目,多线程并发拉取 HF 元数据。

    使用 _HF_BG_CONCURRENCY 个工作线程并发获取，自适应源选择避免重复竞速。
    """
    global _hf_status
    if _hf_status.get("running"):
        return {"skipped": True, "reason": "already_running",
                "triggered_by": _hf_status.get("triggered_by")}

    repos = sorted({e.repo_id for e in _merged_registry.values() if e.repo_id})
    _hf_cancel_event.clear()  # 重置取消事件
    _hf_status = {
        "running": True,
        "triggered_by": triggered_by,
        "started_at": time.time(),
        "last_run": time.time(),
        "last_success": None,
        "last_error": None,
        "fetched": 0,
        "failed": 0,
        "total": len(repos),
        "current": "",
    }
    logger.info("[HF] metadata refresh started: %d repos, %d workers (by %s)",
                len(repos), _HF_BG_CONCURRENCY, triggered_by)

    import concurrent.futures

    def _fetch_one_repo(repo_id: str) -> tuple[str, dict | None, float]:
        """获取单个 repo 元数据，返回 (repo_id, meta, elapsed)。"""
        if _hf_cancel_event.is_set():
            return repo_id, None, 0.0
        t0 = time.time()
        try:
            meta = _fetch_hf_metadata_sync(repo_id, force=force)
            elapsed = time.time() - t0
            return repo_id, meta, elapsed
        except Exception as e:
            elapsed = time.time() - t0
            logger.warning("[HF] ✗ %s exception: %s (%.1fs)", repo_id, e, elapsed)
            return repo_id, None, elapsed

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=_HF_BG_CONCURRENCY) as executor:
            # 提交所有任务
            future_to_repo = {executor.submit(_fetch_one_repo, rid): rid for rid in repos}

            for future in concurrent.futures.as_completed(future_to_repo):
                if _hf_cancel_event.is_set():
                    logger.info("[HF] refresh cancelled (%d/%d)",
                                _hf_status["fetched"] + _hf_status["failed"], len(repos))
                    _hf_status["last_error"] = "cancelled"
                    # 取消剩余任务
                    for f in future_to_repo:
                        f.cancel()
                    break

                try:
                    repo_id, meta, elapsed = future.result()
                    _hf_status["current"] = repo_id
                    if meta:
                        _hf_status["fetched"] += 1
                        logger.info("[HF] ✓ %s (%.1fs) desc=%s", repo_id, elapsed,
                                    "yes" if meta.get("description") else "no")
                        # 顺便探测预览图
                        _probe_hf_preview_sync(repo_id)
                    else:
                        _hf_status["failed"] += 1
                        logger.info("[HF] ✗ %s (%.1fs) failed", repo_id, elapsed)
                except Exception as e:
                    _hf_status["failed"] += 1
                    logger.warning("[HF] task exception: %s", e)

        if not _hf_status.get("last_error"):
            _hf_status["last_success"] = time.time()
        logger.info("[HF] refresh done: fetched=%d failed=%d total=%d",
                    _hf_status["fetched"], _hf_status["failed"], _hf_status["total"])
    except Exception as e:
        _hf_status["last_error"] = str(e)
        logger.exception("HF metadata refresh crashed")
    finally:
        _hf_status["running"] = False
        _hf_status["current"] = ""
    return {
        "fetched": _hf_status["fetched"],
        "failed": _hf_status["failed"],
        "total": _hf_status["total"],
        "last_run": _hf_status["last_run"],
        "triggered_by": _hf_status.get("triggered_by"),
    }


def _hf_updater_loop() -> None:
    """后台守护线程: 启动后延迟首跑,之后每 _HF_BG_INTERVAL_SECONDS 跑一次。"""
    if _hf_stop_event.wait(timeout=_HF_BG_INITIAL_DELAY):
        return  # 已请求停止
    while not _hf_stop_event.is_set():
        try:
            _refresh_all_hf_metadata(force=False)
        except Exception as e:
            logger.warning("HF background loop error: %s", e)
        # 周期等待 (可被 stop event 提前唤醒)
        if _hf_stop_event.wait(timeout=_HF_BG_INTERVAL_SECONDS):
            return


def _start_hf_background_updater() -> None:
    global _hf_bg_thread
    if _hf_bg_thread is not None and _hf_bg_thread.is_alive():
        return
    _hf_stop_event.clear()
    _hf_bg_thread = threading.Thread(target=_hf_updater_loop, name="hf-meta-updater", daemon=True)
    _hf_bg_thread.start()
    logger.info("HF background updater started (initial delay %ds, interval %ds)",
                _HF_BG_INITIAL_DELAY, _HF_BG_INTERVAL_SECONDS)


def _stop_hf_background_updater() -> None:
    _hf_stop_event.set()


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


# ── 2026-06-10: 目录→分类手动映射 ──

def _load_dir_category_map() -> dict[str, str]:
    """加载目录→分类手动映射,优先用户自定义,回退到官方默认"""
    global _dir_category_map
    if _dir_category_map is not None:
        return _dir_category_map
    result = dict(_DEFAULT_DIR_CATEGORY_MAP)
    if _ctx is not None:
        try:
            f = _ctx.config_dir / _DIR_CATEGORY_FILE
            if f.is_file():
                user_map = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(user_map, dict):
                    # 用户映射覆盖默认(大小写不敏感存储,统一小写key)
                    for k, v in user_map.items():
                        result[k.lower()] = v
        except Exception:
            pass
    _dir_category_map = result
    return result


def _save_dir_category_map(user_map: dict[str, str]) -> bool:
    """保存用户自定义目录分类映射"""
    global _dir_category_map
    if _ctx is None:
        return False
    try:
        _ctx.config_dir.mkdir(parents=True, exist_ok=True)
        (_ctx.config_dir / _DIR_CATEGORY_FILE).write_text(
            json.dumps(user_map, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        # 重建缓存
        _dir_category_map = None
        _load_dir_category_map()
        return True
    except Exception as e:
        logger.warning("Failed to persist dir category map: %s", e)
        return False


def _classify_dirpath_by_map(dirpath: str) -> str | None:
    """根据手动映射判断目录分类,未匹配返回 None"""
    p = (dirpath or "").lower().replace("\\", "/")
    cat_map = _load_dir_category_map()
    # 提取路径各段(包括末段),优先匹配更长的路径段
    segments = [s for s in p.split("/") if s]
    segments.reverse()  # 从末段开始匹配(更深层目录优先)
    for seg in segments:
        seg_lower = seg.lower()
        if seg_lower in cat_map:
            return cat_map[seg_lower]
    # 也检查完整路径中的关键词
    for key in cat_map:
        if key in p:
            return cat_map[key]
    return None


def _refresh_dir_category_cache() -> None:
    """强制刷新目录分类映射缓存"""
    global _dir_category_map
    _dir_category_map = None
    _load_dir_category_map()


# ── 同步自定义目录到设置 ──

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


def _build_filename_index(models_dirs: list[Path]) -> dict[str, str]:
    """递归扫描所有模型目录，构建 filename → local_path 索引"""
    index: dict[str, str] = {}
    for md in models_dirs:
        try:
            for dirpath, _dirnames, filenames in os.walk(md):
                for fn in filenames:
                    if fn not in index:
                        index[fn] = str(Path(dirpath) / fn)
        except OSError:
            pass
    return index


def _get_registry_status() -> list[dict[str, Any]]:
    models_dirs = _get_models_dirs()
    # 构建递归文件索引，支持junction子目录中的模型
    file_index = _build_filename_index(models_dirs)
    results = []
    for entry in _merged_registry.values():
        exists = False
        local_path = None
        # 先检查顶层目录
        for md in models_dirs:
            if _check_model_exists(entry, md):
                exists = True
                local_path = str((md / entry.filename).resolve())
                break
        # 顶层未找到，在递归索引中查找（包括junction子目录）
        if not exists and not entry.is_folder and entry.filename in file_index:
            exists = True
            local_path = file_index[entry.filename]

        # 2026-06-10: 走 HF API 自动补全 description/size_gb/tags/preview_url 等
        # builtin 字段优先 (非空 override),缺失则用 HF 数据
        result = _enrich_entry_with_hf(entry)
        # 覆盖本地存在性 (这两个字段不应该被 HF API 干扰)
        result["downloaded"] = exists
        result["local_path"] = local_path
        # 兼容字段 (供前端既有逻辑使用)
        result["model_type"] = result.get("model_category", "checkpoint")
        results.append(result)
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
    dir_lower = (dirpath or "").lower().replace("\\", "/")
    # 2026-06-10: 目录名直接决定大方向(优先级最高,避免 unet/z_image/ 下的文件被默认判为 lora)
    if "/lora" in dir_lower or dir_lower.startswith("lora/") or dir_lower.endswith("/lora") or dir_lower.endswith("/loras"):
        return True
    if any(seg in dir_lower for seg in ("/unet", "/transformer", "/text_encoder", "/vae", "/tokenizer", "/scheduler", "/upscaler", "/controlnet")):
        return False
    # 2026-06-10: z_image/z-image 目录下的文件是图像模型,不是 lora
    # 注意: 只排除 z_image(下划线) 和 z-image(连字符),不排除 zimage(无分隔符)
    # 因为 Zimage/ 目录存放的是图像 LoRA,其文件应走正常 lora 判断
    if "z_image" in dir_lower or "z-image" in dir_lower:
        return False
    # 2026-06-10: 手动映射优先 — 如果目录被映射为非lora分类(image/video/upscaler/supporting等),不判为lora
    mapped = _classify_dirpath_by_map(dirpath)
    if mapped and mapped not in ("lora", "image-lora", "video-lora"):
        return False
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


def _classify_dirpath(dirpath: str, default_category: str = "checkpoint") -> str:
    """2026-06-10: 根据目录路径推导 model_category,用于前端图像/视频分类。

    优先级:
        1. 手动映射(model_dir_categories.json) — 最高优先级
        2. 启发式规则(lora/unet/z_image/transformer 等目录约定)
        3. default_category(默认 "checkpoint")
    """
    # 1) 手动映射优先
    mapped = _classify_dirpath_by_map(dirpath)
    if mapped:
        return mapped
    # 2) 启发式规则
    p = (dirpath or "").lower().replace("\\", "/")
    # 1) lora 必须最先判断(z-image/ltx-2 等子目录需要细分 image-lora/video-lora)
    if "/lora/" in p or p.startswith("lora/") or p.endswith("/lora") or p.endswith("/loras"):
        # 找 /lora/ 之后的第一段作为 sub_name
        lora_idx = p.rfind("/lora")
        if lora_idx < 0:
            lora_idx = p.rfind("/loras")
        if lora_idx >= 0:
            tail = p[lora_idx:].lstrip("/")
            # tail 形如 "lora/<sub>/..." 或 "loras/<sub>/..."
            parts = tail.split("/")
            if len(parts) >= 2 and parts[1]:
                sub_name = parts[1]
                if any(k in sub_name for k in ("z-image", "z_image", "zimage", "zit", "zib", "sdxl", "flux", "stable", "image")):
                    return "image-lora"
                if any(k in sub_name for k in ("ltx", "lightricks", "wan", "video", "ltx2", "ltx-2")):
                    return "video-lora"
        return "lora"
    # 2) upscaler / vae / text_encoder / tokenizer / scheduler / controlnet
    if "/upscaler" in p:
        return "upscaler"
    if "/text_encoder" in p:
        return "supporting"
    if "/vae" in p:
        return "supporting"
    if "/tokenizer" in p:
        return "supporting"
    if "/scheduler" in p:
        return "supporting"
    if "/controlnet" in p:
        return "controlnet"
    # 3) unet/ 通常是图像模型
    if "/unet" in p:
        return "image"
    if "z_image" in p or "z-image" in p or "zimage" in p:
        return "image"
    # 4) transformer/ 是视频模型
    if "/transformer" in p:
        return "video"
    if "wan" in p:
        return "video"
    if "ltx" in p:
        return "video"
    return default_category


def _classify_lora_dirpath(dirpath: str) -> str:
    """2026-06-10: LoRA 专用的目录分类:返回 image-lora / video-lora / lora"""
    # 1) 手动映射优先
    mapped = _classify_dirpath_by_map(dirpath)
    if mapped and mapped in ("lora", "image-lora", "video-lora"):
        return mapped
    # 2) 启发式
    p = (dirpath or "").lower().replace("\\", "/")
    if any(k in p for k in ("z-image", "z_image", "zimage", "zit", "zib")):
        return "image-lora"
    if any(k in p for k in ("ltx", "lightricks", "wan", "video", "ltx2", "ltx-2")):
        return "video-lora"
    return "lora"


def _scan_dir_for_models(root: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            # 2026-06-10: 在循环外就计算目录分类(一次扫描,所有文件共享)
            category = _classify_dirpath(dirpath, "checkpoint")
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
                            # 2026-06-10: 同步输出 model_category 与 model_type 镜像字段,便于前端与 registry 输出统一
                            "model_category": _classify_lora_dirpath(dirpath) if is_lora else category,
                            "dir_path": str(Path(dirpath).resolve()),
                        }
                        if is_lora and suf == ".safetensors":
                            meta = _read_safetensors_metadata_lite(full)
                            if meta:
                                entry.update(meta)
                            # 2026-06-10: 如果 metadata 没读到 base_model,根据目录路径补一个推断值
                            if not entry.get("base_model"):
                                p_low = (dirpath or "").lower()
                                if any(k in p_low for k in ("z-image", "z_image", "zimage", "zit", "zib")):
                                    entry["base_model"] = "z-image"
                                elif "ltx" in p_low or "lightricks" in p_low:
                                    entry["base_model"] = "ltx-2"
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
                # 2026-06-10: 暴露 HF 后台更新状态给前端
                "hf_status": dict(_hf_status),
            }
        except Exception as e:
            logger.exception("registry list failed")
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.get("/api/models/registry/sync-status")
    async def route_sync_status():
        return _last_sync_status

    @app.get("/api/models/registry/hf-status")
    async def route_hf_status():
        """返回 HF 后台元数据更新状态 (running / last_run / fetched / failed / total / current)。"""
        return dict(_hf_status)

    @app.post("/api/models/registry/refresh-hf")
    async def route_refresh_hf(request: FastAPIRequest):
        """手动触发一次 HF 全量元数据刷新 (异步,立即返回)。

        行为:
          - 若已在运行: 返回 "already_running",由前端决定是否展示进度
          - 若空闲: 启动新线程,返回 "started"
        不会因为后台正在跑就拒绝前端手动请求 —— 前端会拿到 triggered_by 自行处理。
        """
        try:
            data = await request.json()
        except Exception:
            data = {}
        force = bool(data.get("force", False))
        if _hf_status.get("running"):
            return {"status": "already_running", "hf_status": dict(_hf_status)}
        def _run():
            _refresh_all_hf_metadata(force=force, triggered_by="manual")
        threading.Thread(target=_run, name="hf-refresh-manual", daemon=True).start()
        return {"status": "started", "hf_status": dict(_hf_status)}

    @app.post("/api/models/registry/cancel-hf")
    async def route_cancel_hf():
        """取消当前正在运行的 HF 同步 (仅对 triggered_by=='manual' 生效)。

        取消后台同步是反模式 (下次还会跑),所以这个端点只取消手动触发的。
        """
        if not _hf_status.get("running"):
            return {"status": "not_running", "hf_status": dict(_hf_status)}
        if _hf_status.get("triggered_by") != "manual":
            return {"status": "refused", "reason": "background sync not cancellable",
                    "hf_status": dict(_hf_status)}
        _hf_cancel_event.set()
        return {"status": "cancelling", "hf_status": dict(_hf_status)}

    @app.post("/api/models/registry/hf-info")
    async def route_hf_info(request: FastAPIRequest):
        """获取单个 repo 的 HF 元数据(强制刷新,不走缓存)。"""
        try:
            data = await request.json()
        except Exception:
            data = {}
        repo_id = (data.get("repo_id") or "").strip()
        if not repo_id or "/" not in repo_id:
            return JSONResponse(status_code=400, content={"error": "Invalid repo_id"})
        meta = _fetch_hf_metadata_sync(repo_id, force=True)
        preview = _probe_hf_preview_sync(repo_id)
        return {"repo_id": repo_id, "meta": meta, "preview_url": preview or ""}

    @app.get("/api/models/registry/preview")
    async def route_preview(repo_id: str, filename: str = ""):
        """代理 HF 预览图,带本地字节级缓存。
        2026-06-10: 增加 filename 参数,用于单文件预览(避免多文件仓库共用一个预览图)。
        """
        if not repo_id or "/" not in repo_id:
            return JSONResponse(status_code=400, content={"error": "Invalid repo_id"})
        from fastapi.responses import Response
        # 2026-06-10: 缓存 key 按 filename 区分,不同文件不同 cache
        cache_dir = _get_hf_cache_dir()
        cache_suffix = f"_{filename}" if filename else ""
        if cache_dir is not None:
            safe = repo_id.replace("/", "_").replace("\\", "_") + cache_suffix
            bytes_cache = cache_dir / f"preview_img_{safe}.bin"
            meta_cache = cache_dir / f"preview_img_{safe}.meta.json"
            if bytes_cache.is_file() and meta_cache.is_file() and _is_cache_valid(bytes_cache, _HF_PREVIEW_TTL_SECONDS):
                try:
                    meta = json.loads(meta_cache.read_text(encoding="utf-8"))
                    return Response(
                        content=bytes_cache.read_bytes(),
                        media_type=meta.get("content_type", "image/png"),
                        headers={"Cache-Control": "public, max-age=86400", "X-Cache": "HIT"},
                    )
                except Exception:
                    pass
        # 2026-06-10: 优先用单文件预览,回退到仓库级预览
        preview_url = ""
        if filename:
            preview_url = _probe_hf_file_preview_sync(repo_id, filename) or ""
        if not preview_url:
            preview_url = _probe_hf_preview_sync(repo_id) or ""
        if not preview_url:
            return JSONResponse(status_code=404, content={"error": "No preview available"})
        try:
            import httpx
            with httpx.Client(timeout=httpx.Timeout(15.0), follow_redirects=True) as client:
                r = client.get(preview_url, headers={"User-Agent": _HF_USER_AGENT})
                r.raise_for_status()
                content = r.content
                content_type = r.headers.get("content-type", "image/png")
            # 写本地缓存(按 filename 区分)
            if cache_dir is not None:
                try:
                    safe = repo_id.replace("/", "_").replace("\\", "_") + cache_suffix
                    (cache_dir / f"preview_img_{safe}.bin").write_bytes(content)
                    (cache_dir / f"preview_img_{safe}.meta.json").write_text(
                        json.dumps({"content_type": content_type, "url": preview_url}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            return Response(
                content=content,
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=86400", "X-Cache": "MISS"},
            )
        except Exception as e:
            return JSONResponse(status_code=502, content={"error": f"Fetch preview failed: {e}"})

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

    # ── 2026-06-10: 目录→分类手动映射 API ──

    @app.get("/api/models/dir-categories")
    async def route_get_dir_categories():
        """返回目录→分类映射(包含默认+用户自定义)"""
        _refresh_dir_category_cache()
        cat_map = _load_dir_category_map()
        return {
            "categories": cat_map,
            "defaults": _DEFAULT_DIR_CATEGORY_MAP,
        }

    @app.post("/api/models/dir-categories")
    async def route_save_dir_categories(request: FastAPIRequest):
        """保存用户自定义目录分类映射(覆盖默认)"""
        try:
            data = await request.json()
        except Exception:
            data = {}
        user_map = data.get("categories", {})
        if not isinstance(user_map, dict):
            return JSONResponse(status_code=400, content={"error": "categories must be a dict"})
        # 验证分类值合法
        valid_cats = {"image", "image-checkpoint", "video", "lora", "image-lora", "video-lora", "upscaler", "supporting", "controlnet", "checkpoint"}
        for k, v in user_map.items():
            if v not in valid_cats:
                return JSONResponse(status_code=400, content={"error": f"Invalid category '{v}' for dir '{k}'. Valid: {sorted(valid_cats)}"})
        ok = _save_dir_category_map(user_map)
        _refresh_dir_category_cache()
        return {"status": "saved" if ok else "failed", "categories": _load_dir_category_map()}

    @app.post("/api/models/dir-categories/reset")
    async def route_reset_dir_categories():
        """重置为官方默认分类"""
        _save_dir_category_map({})
        _refresh_dir_category_cache()
        return {"status": "reset", "categories": _load_dir_category_map()}

    # ── 2026-06-10: 启动 HF 后台元数据更新器 ──
    # 启动 30s 后第一次跑,之后每 12h 增量刷新 (有 TTL 缓存兜底,只更新过期项)
    try:
        _start_hf_background_updater()
    except Exception as e:
        logger.warning("Failed to start HF background updater: %s", e)

    logger.info("community_models: module loaded")
