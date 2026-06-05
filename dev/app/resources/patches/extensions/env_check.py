"""Environment check & recommended version detection.

Endpoints:
  GET  /api/system/env-check    - Detect installed components and compare with recommended versions
  POST /api/system/env-fix      - Fix a specific component to recommended version
  GET  /api/system/env-preset   - Get full environment preset for current hardware

Detects:
  - CUDA Toolkit version (nvcc / system PATH)
  - PyTorch version + GPU/CPU variant
  - cuDNN version (via torch.backends.cudnn)
  - Python version
  - ffmpeg availability
  - NVIDIA driver version (via pynvml)

Recommended versions are determined by hardware profile (GPU VRAM tier).
Official requirements sourced from backend/pyproject.toml:
  - Python >= 3.12
  - torch >= 2.3.0 (cu128 index)
  - CUDA 12.8 (via cu128 PyTorch wheel)

Upstream dependency: None (purely YunJi)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from fastapi import FastAPI, Request as FastAPIRequest
from fastapi.responses import JSONResponse

if sys.platform == 'win32':
    _SILENT_FLAGS = subprocess.CREATE_NO_WINDOW | 0x00000008
else:
    _SILENT_FLAGS = 0


def _silent_run(*args, **kwargs):
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    kwargs['startupinfo'] = si
    if sys.platform == 'win32':
        if 'creationflags' in kwargs:
            kwargs['creationflags'] = kwargs['creationflags'] | _SILENT_FLAGS
        else:
            kwargs['creationflags'] = _SILENT_FLAGS
    return subprocess.run(*args, **kwargs)

from extensions._context import ExtensionContext

logger = logging.getLogger("env_check")


FFMPEG_PORTABLE_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "LTXDesktop" / "ffmpeg"


RECOMMENDED_VERSIONS = {
    "ultra": {
        "cuda": "12.8",
        "pytorch": "2.9.0",
        "pytorch_variant": "cu128",
        "cudnn": "any",
        "python": "3.12",
        "python_match": "exact_major_minor",
        "ffmpeg": "any",
        "nvidia_driver_min": "560.70",
    },
    "high": {
        "cuda": "12.8",
        "pytorch": "2.9.0",
        "pytorch_variant": "cu128",
        "cudnn": "any",
        "python": "3.12",
        "python_match": "exact_major_minor",
        "ffmpeg": "any",
        "nvidia_driver_min": "560.70",
    },
    "medium": {
        "cuda": "12.8",
        "pytorch": "2.9.0",
        "pytorch_variant": "cu128",
        "cudnn": "any",
        "python": "3.12",
        "python_match": "exact_major_minor",
        "ffmpeg": "any",
        "nvidia_driver_min": "560.70",
    },
    "low": {
        "cuda": "12.8",
        "pytorch": "2.9.0",
        "pytorch_variant": "cu128",
        "cudnn": "any",
        "python": "3.12",
        "python_match": "exact_major_minor",
        "ffmpeg": "any",
        "nvidia_driver_min": "560.70",
    },
    "minimal": {
        "cuda": "12.8",
        "pytorch": "2.9.0",
        "pytorch_variant": "cu128",
        "cudnn": "any",
        "python": "3.12",
        "python_match": "exact_major_minor",
        "ffmpeg": "any",
        "nvidia_driver_min": "560.70",
    },
}


ENV_PRESETS = {
    "ultra": {
        "preset_name": "极致性能预设",
        "target_hardware": "48GB+ VRAM (A6000 48GB / A100 40-80GB / H100 80GB / RTX A6000)",
        "stack": {
            "python": "3.12",
            "pytorch": "2.9.0+cu128",
            "cuda": "12.8 (via cu128 wheel)",
            "cudnn": "bundled with PyTorch",
            "nvidia_driver": ">=560.70",
            "ffmpeg": "latest portable",
        },
        "model_recommendation": {
            "primary": "ltx-2.3-22b-distilled.safetensors",
            "fp8": "ltx-2.3-22b-distilled-fp8.safetensors (optional, for max speed)",
            "upscaler": "ltx-2.3-spatial-upscaler-x2-1.0.safetensors (recommended)",
            "reason": "VRAM充裕，可使用完整蒸馏模型+upscaler获得最佳画质",
        },
        "inference_config": {
            "vram_limit": 0,
            "offload": False,
            "layer_streaming": False,
            "upscaler": True,
            "sage_attention": True,
            "fp8_inference": "optional (原生FP8如Ada/Hopper架构)",
            "max_resolution": "1080p",
            "recommended_resolution": "1080p",
        },
        "system_requirements": {
            "min_ram_gb": 32,
            "recommended_ram_gb": 64,
            "min_vram_gb": 48,
            "min_disk_gb": 40,
        },
        "performance_estimate": {
            "1080p_25f": "10-20秒",
            "720p_25f": "5-10秒",
            "1080p_upscaled": "15-30秒",
        },
        "tips": [
            "VRAM充裕，无需任何限制",
            "推荐使用完整蒸馏模型+upscaler获得最佳画质",
            "如GPU为Ada Lovelace/Hopper架构，可开启原生FP8进一步加速",
            "SageAttention可显著降低注意力计算开销",
        ],
    },
    "high": {
        "preset_name": "高性能预设",
        "target_hardware": "20-24GB VRAM (RTX 3090 24GB / RTX 4090 24GB / RTX A5000 24GB)",
        "stack": {
            "python": "3.12",
            "pytorch": "2.9.0+cu128",
            "cuda": "12.8 (via cu128 wheel)",
            "cudnn": "bundled with PyTorch",
            "nvidia_driver": ">=560.70",
            "ffmpeg": "latest portable",
        },
        "model_recommendation": {
            "primary": "ltx-2.3-22b-distilled-fp8.safetensors",
            "fp8": "ltx-2.3-22b-distilled-fp8.safetensors (recommended, saves ~4GB VRAM)",
            "upscaler": "not recommended (frees 2-4GB VRAM for inference)",
            "reason": "FP8蒸馏模型节省显存，offload保障稳定性，关闭upscaler释放推理空间",
        },
        "inference_config": {
            "vram_limit": 22,
            "offload": True,
            "layer_streaming": True,
            "prefetch_count": 19,
            "upscaler": False,
            "sage_attention": "Ada Lovelace (RTX 4090): yes; Ampere (RTX 3090): no",
            "fp8_inference": "RTX 4090: native FP8; RTX 3090: FP8 via software emulation",
            "max_resolution": "1080p",
            "recommended_resolution": "720p-1080p",
        },
        "system_requirements": {
            "min_ram_gb": 32,
            "recommended_ram_gb": 64,
            "min_vram_gb": 20,
            "min_disk_gb": 30,
        },
        "performance_estimate": {
            "1080p_25f": "30-60秒",
            "720p_25f": "15-30秒",
            "540p_25f": "8-15秒",
        },
        "tips": [
            "已自动启用CPU offload + layer streaming，速度换稳定性",
            "推荐使用FP8蒸馏模型，节省约4GB显存",
            "关闭upscaler释放2-4GB VRAM用于推理",
            "RTX 4090可开启SageAttention和原生FP8加速",
            "RTX 3090的SageAttention和FP8为软件模拟，效果有限",
            "系统内存>=64GB时offload性能开销较小",
        ],
    },
    "medium": {
        "preset_name": "均衡模式预设",
        "target_hardware": "14-20GB VRAM (RTX 4080 16GB / RTX 5000 Ada 16GB / RTX 3080 20GB)",
        "stack": {
            "python": "3.12",
            "pytorch": "2.9.0+cu128",
            "cuda": "12.8 (via cu128 wheel)",
            "cudnn": "bundled with PyTorch",
            "nvidia_driver": ">=560.70",
            "ffmpeg": "latest portable",
        },
        "model_recommendation": {
            "primary": "ltx-2.3-22b-distilled-fp8.safetensors",
            "fp8": "ltx-2.3-22b-distilled-fp8.safetensors (required for stability)",
            "upscaler": "not recommended (VRAM too tight)",
            "reason": "FP8模型是稳定运行的必要选择，720p是最佳平衡点",
        },
        "inference_config": {
            "vram_limit": 16,
            "offload": True,
            "layer_streaming": True,
            "prefetch_count": 10,
            "upscaler": False,
            "sage_attention": "Ada Lovelace (RTX 4080/5000): yes; Ampere (RTX 3080): no",
            "fp8_inference": "Ada: native FP8; Ampere: software emulation",
            "max_resolution": "1080p (short clips only)",
            "recommended_resolution": "720p",
        },
        "system_requirements": {
            "min_ram_gb": 32,
            "recommended_ram_gb": 64,
            "min_vram_gb": 14,
            "min_disk_gb": 30,
        },
        "performance_estimate": {
            "1080p_25f": "60-120秒 (短片段)",
            "720p_25f": "30-60秒",
            "540p_25f": "15-25秒",
        },
        "tips": [
            "720p是最佳平衡点，画质与速度兼顾",
            "FP8蒸馏模型是必须的，完整模型可能OOM",
            "关闭upscaler + 启用offload是必须的",
            "Ada架构GPU可开启SageAttention加速",
            "1080p仅适合短片段(2-3秒)，长时间片段请用720p",
        ],
    },
    "low": {
        "preset_name": "节能模式预设",
        "target_hardware": "10-14GB VRAM (RTX 4070 Ti 12GB / RTX 3080 12GB / RTX 2080 Ti 11GB)",
        "stack": {
            "python": "3.12",
            "pytorch": "2.9.0+cu128",
            "cuda": "12.8 (via cu128 wheel)",
            "cudnn": "bundled with PyTorch",
            "nvidia_driver": ">=560.70",
            "ffmpeg": "latest portable",
        },
        "model_recommendation": {
            "primary": "ltx-2.3-22b-distilled-fp8.safetensors",
            "fp8": "ltx-2.3-22b-distilled-fp8.safetensors (required)",
            "upscaler": "not supported (VRAM insufficient)",
            "reason": "FP8是唯一可行选择，540p是稳定首选分辨率",
        },
        "inference_config": {
            "vram_limit": 12,
            "offload": True,
            "layer_streaming": True,
            "prefetch_count": 4,
            "upscaler": False,
            "sage_attention": "Ada Lovelace (RTX 4070 Ti): yes; Turing/Ampere: no",
            "fp8_inference": "Ada: native; others: software emulation (slower)",
            "max_resolution": "720p (short clips)",
            "recommended_resolution": "540p",
        },
        "system_requirements": {
            "min_ram_gb": 32,
            "recommended_ram_gb": 64,
            "min_vram_gb": 10,
            "min_disk_gb": 25,
        },
        "performance_estimate": {
            "720p_25f": "90-180秒 (短片段)",
            "540p_25f": "40-80秒",
            "480p_25f": "25-50秒",
        },
        "tips": [
            "540p是稳定首选，720p仅短片段",
            "推理较慢(offload频繁搬运权重)，请耐心等待",
            "确保系统内存>=32GB，否则offload可能内存不足",
            "Ada架构GPU可开启SageAttention获得显著加速",
        ],
    },
    "minimal": {
        "preset_name": "极限模式预设",
        "target_hardware": "<10GB VRAM (RTX 4060 8GB / RTX 3060 8GB / GTX 1080 Ti 11GB)",
        "stack": {
            "python": "3.12",
            "pytorch": "2.9.0+cu128",
            "cuda": "12.8 (via cu128 wheel)",
            "cudnn": "bundled with PyTorch",
            "nvidia_driver": ">=560.70",
            "ffmpeg": "latest portable",
        },
        "model_recommendation": {
            "primary": "ltx-2.3-22b-distilled-fp8.safetensors",
            "fp8": "ltx-2.3-22b-distilled-fp8.safetensors (absolutely required)",
            "upscaler": "not supported",
            "reason": "FP8是唯一可行选择，480p是稳定首选分辨率，540p有OOM风险",
        },
        "inference_config": {
            "vram_limit": 8,
            "offload": True,
            "layer_streaming": True,
            "prefetch_count": 1,
            "upscaler": False,
            "sage_attention": "Ada Lovelace (RTX 4060): yes; others: no",
            "fp8_inference": "Ada: native; others: software emulation",
            "max_resolution": "540p (risky)",
            "recommended_resolution": "480p",
        },
        "system_requirements": {
            "min_ram_gb": 32,
            "recommended_ram_gb": 64,
            "min_vram_gb": 8,
            "min_disk_gb": 25,
        },
        "performance_estimate": {
            "540p_25f": "120-240秒 (有OOM风险)",
            "480p_25f": "60-120秒",
            "360p_25f": "40-80秒",
        },
        "tips": [
            "480p是稳定首选，540p有OOM风险",
            "推理非常慢，请做好等待准备",
            "必须确保系统内存>=32GB",
            "GTX 1080 Ti (Pascal) 不支持SageAttention和FP8硬件加速",
            "建议升级到RTX 40系列以获得原生FP8加速",
        ],
    },
}


def _detect_cuda_version() -> dict[str, Any]:
    version = None
    source = "not_found"
    is_gpu = False

    try:
        import torch
        if torch.cuda.is_available():
            cuda_ver = torch.version.cuda
            if cuda_ver:
                version = cuda_ver
                source = "pytorch"
                is_gpu = True
    except Exception:
        pass

    if version is None:
        try:
            result = _silent_run(
                ["nvcc", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                import re
                m = re.search(r"release (\d+\.\d+)", result.stdout)
                if m:
                    version = m.group(1)
                    source = "nvcc"
                    is_gpu = True
        except Exception:
            pass

    if version is None:
        cuda_path = os.environ.get("CUDA_PATH", "")
        if cuda_path:
            version_file = Path(cuda_path) / "version.txt"
            if version_file.exists():
                try:
                    content = version_file.read_text(encoding="utf-8")
                    import re
                    m = re.search(r"(\d+\.\d+)", content)
                    if m:
                        version = m.group(1)
                        source = "CUDA_PATH"
                        is_gpu = True
                except Exception:
                    pass

    if version is None:
        try:
            base = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
            if base.exists():
                dirs = sorted([d.name for d in base.iterdir() if d.is_dir() and d.name.startswith("v")], reverse=True)
                if dirs:
                    version = dirs[0].lstrip("v")
                    source = "system_dir"
                    is_gpu = True
        except Exception:
            pass

    return {
        "name": "CUDA",
        "version": version,
        "source": source,
        "is_gpu": is_gpu,
        "variant": "GPU" if is_gpu else ("CPU" if version else None),
    }


def _detect_pytorch_version() -> dict[str, Any]:
    version = None
    variant = None
    is_gpu = False
    cuda_version = None

    try:
        import torch
        version = torch.__version__
        if hasattr(torch.version, "cuda") and torch.version.cuda:
            variant = f"cu{torch.version.cuda.replace('.', '')}"
            is_gpu = True
            cuda_version = torch.version.cuda
        elif hasattr(torch.version, "hip") and torch.version.hip:
            variant = f"rocm{torch.version.hip.replace('.', '')}"
            is_gpu = True
        else:
            variant = "cpu"
            is_gpu = False
    except Exception:
        pass

    return {
        "name": "PyTorch",
        "version": version,
        "source": "torch" if version else "not_found",
        "is_gpu": is_gpu,
        "variant": variant,
        "cuda_bundled": cuda_version,
    }


def _detect_cudnn_version() -> dict[str, Any]:
    version = None
    version_str = None

    try:
        import torch
        if torch.backends.cudnn.is_available():
            v = torch.backends.cudnn.version()
            version = str(v)
            major = v // 1000
            minor = (v % 1000) // 100
            patch = v % 100
            version_str = f"{major}.{minor}.{patch}"
    except Exception:
        pass

    return {
        "name": "cuDNN",
        "version": version_str or version,
        "raw_version": version,
        "source": "torch" if version else "not_found",
        "is_gpu": version is not None,
        "variant": "GPU" if version else None,
    }


def _detect_python_version() -> dict[str, Any]:
    return {
        "name": "Python",
        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "source": "sys",
        "is_gpu": False,
        "variant": None,
    }


def _detect_ffmpeg() -> dict[str, Any]:
    version = None
    path = None

    ffmpeg_path = os.environ.get("LTX_FFMPEG_PATH")
    if ffmpeg_path and Path(ffmpeg_path).exists():
        try:
            result = _silent_run(
                [ffmpeg_path, "-version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                import re
                m = re.search(r"ffmpeg version (\S+)", result.stdout)
                if m:
                    version = m.group(1)
                    path = ffmpeg_path
        except Exception:
            pass

    if version is None:
        ffmpeg_exe = shutil.which("ffmpeg")
        if ffmpeg_exe:
            try:
                result = _silent_run(
                    [ffmpeg_exe, "-version"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    import re
                    m = re.search(r"ffmpeg version (\S+)", result.stdout)
                    if m:
                        version = m.group(1)
                        path = ffmpeg_exe
            except Exception:
                pass

    ffmpeg_file = Path(os.environ.get("LOCALAPPDATA", "")) / "LTXDesktop" / "ffmpeg_path.txt"
    if version is None and ffmpeg_file.exists():
        try:
            custom_path = ffmpeg_file.read_text(encoding="utf-8").strip()
            if custom_path and Path(custom_path).exists():
                try:
                    result = _silent_run(
                        [custom_path, "-version"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if result.returncode == 0:
                        import re
                        m = re.search(r"ffmpeg version (\S+)", result.stdout)
                        if m:
                            version = m.group(1)
                            path = custom_path
                except Exception:
                    pass
        except Exception:
            pass

    return {
        "name": "ffmpeg",
        "version": version,
        "source": "found" if version else "not_found",
        "is_gpu": False,
        "variant": None,
        "path": path,
    }


def _detect_nvidia_driver() -> dict[str, Any]:
    version = None

    try:
        import pynvml
        pynvml.nvmlInit()
        version = pynvml.nvmlSystemGetDriverVersion()
        if isinstance(version, bytes):
            version = version.decode("utf-8", errors="replace")
        pynvml.nvmlShutdown()
    except Exception:
        pass

    if version is None:
        try:
            result = _silent_run(
                ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                version = result.stdout.strip().split("\n")[0].strip()
        except Exception:
            pass

    return {
        "name": "NVIDIA Driver",
        "version": version,
        "source": "found" if version else "not_found",
        "is_gpu": version is not None,
        "variant": "GPU" if version else None,
    }


def _normalize(v: str) -> list[int]:
    v = v.split("+")[0]
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return parts


def _compare_versions(installed: str | None, recommended: str, match_mode: str = "minimum") -> str:
    if installed is None:
        return "missing"
    if recommended == "any":
        return "ok"

    inst_parts = _normalize(installed)
    rec_parts = _normalize(recommended)

    if match_mode == "exact_major_minor":
        inst_mm = inst_parts[:2] if len(inst_parts) >= 2 else inst_parts
        rec_mm = rec_parts[:2] if len(rec_parts) >= 2 else rec_parts
        if inst_mm == rec_mm:
            return "ok"
        if len(inst_mm) >= 1 and len(rec_mm) >= 1 and inst_mm[0] < rec_mm[0]:
            return "outdated"
        return "mismatch"

    min_len = min(len(inst_parts), len(rec_parts))
    if min_len == 0:
        return "ok"

    for i in range(min_len):
        if inst_parts[i] < rec_parts[i]:
            return "outdated"
        if inst_parts[i] > rec_parts[i]:
            return "ok"

    if len(inst_parts) < len(rec_parts):
        return "outdated"

    return "ok"


def _check_variant_mismatch(component: dict[str, Any], recommended_variant: str | None) -> str | None:
    if recommended_variant is None:
        return None
    if component.get("variant") is None:
        return None
    if component["variant"] == recommended_variant:
        return None

    comp_lower = (component.get("variant") or "").lower()
    rec_lower = recommended_variant.lower()

    if "cpu" in comp_lower and "cu" in rec_lower:
        return "should_be_gpu"
    if "cu" in comp_lower and "cpu" in rec_lower:
        return "should_be_cpu"

    return None


def perform_env_check() -> dict[str, Any]:
    from extensions.hardware_profiles import detect_gpu_vram_gb, classify_gpu

    vram_gb, gpu_name = detect_gpu_vram_gb()
    tier = classify_gpu(vram_gb)
    recommended = RECOMMENDED_VERSIONS.get(tier, RECOMMENDED_VERSIONS["high"])

    components = [
        _detect_cuda_version(),
        _detect_pytorch_version(),
        _detect_cudnn_version(),
        _detect_python_version(),
        _detect_ffmpeg(),
        _detect_nvidia_driver(),
    ]

    results = []
    for comp in components:
        comp_key = comp["name"].lower().replace(" ", "_").replace("-", "_")
        rec_key = {
            "cuda": "cuda",
            "pytorch": "pytorch",
            "cudnn": "cudnn",
            "python": "python",
            "ffmpeg": "ffmpeg",
            "nvidia_driver": "nvidia_driver_min",
        }.get(comp_key, None)

        rec_version = recommended.get(rec_key, None) if rec_key else None
        rec_variant = recommended.get(f"{rec_key.replace('_min', '')}_variant", None) if rec_key else None

        if comp_key == "nvidia_driver":
            rec_variant = None

        match_mode = "minimum"
        if rec_key and rec_key in recommended:
            match_mode_key = f"{rec_key}_match" if not rec_key.endswith("_min") else f"{rec_key[:-4]}_match"
            match_mode = recommended.get(match_mode_key, "minimum")

        version_status = "ok"
        if rec_version and rec_version != "any":
            version_status = _compare_versions(comp.get("version"), rec_version, match_mode)

        variant_mismatch = _check_variant_mismatch(comp, rec_variant)

        status = version_status
        if variant_mismatch == "should_be_gpu":
            status = "wrong_variant_cpu"
        elif variant_mismatch == "should_be_cpu":
            status = "wrong_variant_gpu"

        results.append({
            **comp,
            "recommended_version": rec_version if rec_version != "any" else None,
            "recommended_variant": rec_variant,
            "status": status,
        })

    has_issues = any(r["status"] != "ok" for r in results)

    return {
        "gpu_name": gpu_name,
        "gpu_vram_gb": round(vram_gb, 1) if vram_gb is not None else None,
        "tier": tier,
        "components": results,
        "has_issues": has_issues,
    }


def get_env_preset() -> dict[str, Any]:
    """获取当前硬件的完整环境预设配置。"""
    from extensions.hardware_profiles import (
        detect_gpu_vram_gb,
        classify_gpu,
        detect_gpu_features,
        detect_system_ram_gb,
    )

    vram_gb, gpu_name = detect_gpu_vram_gb()
    tier = classify_gpu(vram_gb)
    ram_gb = detect_system_ram_gb()
    gpu_features = detect_gpu_features()

    preset = ENV_PRESETS.get(tier, ENV_PRESETS["high"])

    preset_copy = {}
    for k, v in preset.items():
        if isinstance(v, dict):
            preset_copy[k] = dict(v)
        elif isinstance(v, list):
            preset_copy[k] = list(v)
        else:
            preset_copy[k] = v

    if gpu_features.get("fp8_native"):
        preset_copy.setdefault("inference_config", {})["fp8_inference"] = "native (hardware accelerated)"
    if gpu_features.get("sage_attention"):
        preset_copy.setdefault("inference_config", {})["sage_attention"] = "supported (recommended)"

    return {
        "gpu_name": gpu_name,
        "gpu_vram_gb": round(vram_gb, 1) if vram_gb is not None else None,
        "gpu_features": gpu_features,
        "system_ram_gb": round(ram_gb, 1) if ram_gb is not None else None,
        "tier": tier,
        "preset": preset_copy,
    }


def _install_ffmpeg_portable() -> dict[str, Any]:
    try:
        FFMPEG_INSTALL_DIR.mkdir(parents=True, exist_ok=True)

        zip_path = FFMPEG_INSTALL_DIR / "ffmpeg-release-essentials.zip"

        logger.info("Downloading ffmpeg portable from %s", FFMPEG_PORTABLE_URL)
        req = Request(FFMPEG_PORTABLE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=300) as resp:
            total = resp.headers.get("Content-Length")
            total_bytes = int(total) if total else None
            downloaded = 0
            last_log_pct = -1
            with open(zip_path, "wb") as f:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_bytes and total_bytes > 0:
                        pct = int(downloaded / total_bytes * 100)
                        if pct >= last_log_pct + 20:
                            logger.info("ffmpeg download progress: %d%% (%d/%d bytes)", pct, downloaded, total_bytes)
                            last_log_pct = pct

        logger.info("Extracting ffmpeg to %s", FFMPEG_INSTALL_DIR)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(FFMPEG_INSTALL_DIR)

        zip_path.unlink(missing_ok=True)

        ffmpeg_exe = None
        for p in FFMPEG_INSTALL_DIR.rglob("ffmpeg.exe"):
            ffmpeg_exe = p
            break

        if ffmpeg_exe is None:
            return {"success": False, "error": "ffmpeg.exe not found in extracted archive"}

        bin_dir = ffmpeg_exe.parent
        for name in ("ffprobe.exe", "ffplay.exe"):
            src = bin_dir / name
            if not src.exists():
                for p in FFMPEG_INSTALL_DIR.rglob(name):
                    try:
                        shutil.copy2(p, bin_dir / name)
                    except Exception:
                        pass
                    break

        ffmpeg_path_file = Path(os.environ.get("LOCALAPPDATA", "")) / "LTXDesktop" / "ffmpeg_path.txt"
        ffmpeg_path_file.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_path_file.write_text(str(ffmpeg_exe), encoding="utf-8")

        os.environ["LTX_FFMPEG_PATH"] = str(ffmpeg_exe)

        logger.info("ffmpeg portable installed: %s", ffmpeg_exe)
        return {"success": True, "path": str(ffmpeg_exe)}

    except Exception as e:
        logger.exception("ffmpeg portable install failed")
        return {"success": False, "error": str(e)}


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    @app.get("/api/system/env-check")
    async def route_env_check():
        try:
            return perform_env_check()
        except Exception as e:
            logger.exception("env-check failed")
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.get("/api/system/env-preset")
    async def route_env_preset():
        try:
            return get_env_preset()
        except Exception as e:
            logger.exception("env-preset failed")
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.post("/api/system/env-fix")
    async def route_env_fix(request: FastAPIRequest):
        try:
            data = await request.json()
        except Exception:
            data = {}

        component = data.get("component", "").lower().strip()
        if not component:
            return JSONResponse(status_code=400, content={"error": "Missing 'component' field"})

        env_info = perform_env_check()
        comp_info = None
        for c in env_info["components"]:
            if c["name"].lower().replace(" ", "_").replace("-", "_") == component:
                comp_info = c
                break

        if comp_info is None:
            return JSONResponse(status_code=400, content={"error": f"Unknown component: {component}"})

        rec_version = comp_info.get("recommended_version")
        rec_variant = comp_info.get("recommended_variant")

        if component == "pytorch" and rec_variant:
            try:
                pip_args = [
                    sys.executable, "-m", "pip", "install",
                    f"torch=={rec_version}" if rec_version else "torch",
                    f"--index-url", f"https://download.pytorch.org/whl/{rec_variant}",
                    "--force-reinstall", "--no-deps",
                ]

                result = _silent_run(
                    pip_args,
                    capture_output=True, text=True, timeout=600,
                )

                if result.returncode == 0:
                    return {
                        "status": "success",
                        "component": component,
                        "message": f"PyTorch reinstalled: {rec_version}+{rec_variant}",
                        "stdout": result.stdout[-500:] if result.stdout else "",
                    }
                else:
                    return JSONResponse(
                        status_code=500,
                        content={
                            "error": f"pip install failed: {result.stderr[-500:] if result.stderr else 'unknown'}",
                            "component": component,
                        },
                    )
            except Exception as e:
                return JSONResponse(status_code=500, content={"error": str(e), "component": component})

        if component == "ffmpeg":
            install_result = await asyncio.to_thread(_install_ffmpeg_portable)
            if install_result.get("success"):
                return {
                    "status": "success",
                    "component": component,
                    "message": f"ffmpeg installed: {install_result['path']}",
                }
            else:
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": f"ffmpeg auto-install failed: {install_result.get('error', 'unknown')}. "
                                 f"Please download manually from https://www.gyan.dev/ffmpeg/builds/ "
                                 f"and extract ffmpeg.exe, then set LTX_FFMPEG_PATH or add to PATH.",
                        "component": component,
                    },
                )

        if component == "cuda":
            return JSONResponse(
                status_code=501,
                content={
                    "error": f"CUDA Toolkit {rec_version} needs manual installation from NVIDIA: "
                             f"https://developer.nvidia.com/cuda-downloads",
                    "component": component,
                    "recommended_version": rec_version,
                },
            )

        if component == "python":
            return JSONResponse(
                status_code=501,
                content={
                    "error": f"Python {rec_version} is recommended for best compatibility. "
                             f"Please install Python {rec_version} and recreate the virtual environment.",
                    "component": component,
                    "recommended_version": rec_version,
                },
            )

        return JSONResponse(
            status_code=501,
            content={
                "error": f"Component {component} does not support auto-fix. "
                         f"Recommended version: {rec_version or 'N/A'}",
                "component": component,
                "recommended_version": rec_version,
            },
        )

    logger.info("env_check: module loaded")
