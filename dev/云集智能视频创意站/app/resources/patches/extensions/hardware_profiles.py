"""硬件配置档案系统 — 根据 GPU VRAM 自动匹配最优推理参数。

核心思路:
  - VRAM 限制的是 **总帧数** (num_frames)，不是秒数
  - total_frames = fps × duration_seconds
  - 当 FPS 变化时，推荐秒数自动联动调整
  - 每个分辨率有一个"帧预算"，所有推荐值都由帧预算动态计算

提供:
  - GPU 分级 (ultra / high / medium / low / minimal)
  - 各分辨率的帧预算 (max_total_frames / recommended_total_frames)
  - 动态参数计算: calc_dynamic_params(tier, quality, fps) → 推荐时长
  - vram_limit 和 streaming_prefetch_count 自动计算
  - 系统内存感知 (128GB+ RAM 可开启更强 offload)
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from extensions._context import ExtensionContext

logger = logging.getLogger("hardware_profiles")


# ── 数据模型 ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class VideoRecommendation:
    """单种分辨率下的帧预算与推荐参数。

    duration 是派生值: duration = total_frames / fps
    不再存储静态的 max_duration_sec / recommended_frames。
    """
    label: str                          # 显示名，如 "1080p (推荐)"
    width: int                          # 宽度 (64 对齐)
    height: int                         # 高度 (64 对齐)
    max_total_frames: int               # VRAM 限制的最大总帧数 (num_frames)
    recommended_total_frames: int       # 推荐的安全帧数 (~60-70% of max)
    recommended_fps: list[int]          # 推荐帧率列表
    speed_estimate: str                 # 速度估算描述


@dataclass(frozen=True)
class HardwareProfile:
    """一个 GPU 等级的完整配置档案。"""
    tier: str                     # ultra / high / medium / low / minimal
    tier_name: str                # 中文显示名
    vram_range: str               # 显存范围描述
    vram_limit_gb: float          # 推荐的 vram_limit 设置
    prefetch_count: int | None    # layer streaming prefetch 数
    offload_enabled: bool         # 是否启用 sequential CPU offload
    upscaler_enabled: bool        # 是否启用 fast upscaler
    recommendations: list[VideoRecommendation] = field(default_factory=list)
    tips: list[str] = field(default_factory=list)


# ── 档案定义 ─────────────────────────────────────────────────────
# max_total_frames / recommended_total_frames 采用 8n+1 对齐
# (LTX-Video 的 temporal VAE 压缩因子为 8)

PROFILES: dict[str, HardwareProfile] = {
    "ultra": HardwareProfile(
        tier="ultra",
        tier_name="极致性能",
        vram_range="32GB+ (RTX 5090 / A6000 / A100 / H100)",
        vram_limit_gb=0,
        prefetch_count=None,
        offload_enabled=False,
        upscaler_enabled=True,
        recommendations=[
            VideoRecommendation("1080p (推荐)", 1920, 1088, 401, 257, [24, 25, 30], "≈10-20秒/25帧"),
            VideoRecommendation("720p (快速)", 1280, 704, 569, 401, [24, 25, 30], "≈5-10秒/25帧"),
            VideoRecommendation("540p (极速)", 960, 544, 801, 569, [24, 25, 30], "≈3-6秒/25帧"),
            VideoRecommendation("480p", 768, 416, 1001, 681, [24, 25, 30], "≈2-4秒/25帧"),
            VideoRecommendation("360p", 640, 352, 1201, 801, [24, 25, 30], "≈1-3秒/25帧"),
        ],
        tips=[
            "您的 GPU 显存充裕，无需任何限制",
            "1080p + 16秒@24fps 长视频可稳定运行",
            "开启 upscaler 可获得更高质量输出",
            "RTX 5090 支持原生 FP8 + SageAttention，推理速度极快",
        ],
    ),

    "high": HardwareProfile(
        tier="high",
        tier_name="高性能",
        vram_range="20-31GB (RTX 3090 / 4090 / A5000)",
        vram_limit_gb=22,
        prefetch_count=19,
        offload_enabled=True,
        upscaler_enabled=False,
        recommendations=[
            VideoRecommendation("1080p (推荐)", 1920, 1088, 257, 161, [24, 25], "≈30-60秒/25帧"),
            VideoRecommendation("720p (快速)", 1280, 704, 401, 257, [24, 25, 30], "≈15-30秒/25帧"),
            VideoRecommendation("540p (极速)", 960, 544, 569, 401, [24, 25, 30], "≈8-15秒/25帧"),
            VideoRecommendation("480p", 768, 416, 681, 449, [24, 25, 30], "≈5-10秒/25帧"),
            VideoRecommendation("360p", 640, 352, 801, 569, [24, 25, 30], "≈3-6秒/25帧"),
        ],
        tips=[
            "已自动启用 CPU offload + layer streaming，速度换稳定性",
            "关闭 upscaler 释放约 2-4GB VRAM 用于推理",
            "128GB 内存充裕，offload 开销主要在 PCIe 带宽而非内存容量",
            "帧预算是硬限制：调高FPS→推荐秒数自动缩短",
        ],
    ),

    "medium": HardwareProfile(
        tier="medium",
        tier_name="均衡模式",
        vram_range="14-20GB (RTX 4080 / RTX 5000 Ada / RTX 3080 20GB)",
        vram_limit_gb=16,
        prefetch_count=10,
        offload_enabled=True,
        upscaler_enabled=False,
        recommendations=[
            VideoRecommendation("1080p (挑战)", 1920, 1088, 89, 57, [24], "≈60-120秒/25帧"),
            VideoRecommendation("720p (推荐)", 1280, 704, 161, 97, [24, 25], "≈30-60秒/25帧"),
            VideoRecommendation("540p (快速)", 960, 544, 257, 161, [24, 25, 30], "≈15-25秒/25帧"),
            VideoRecommendation("480p", 768, 416, 401, 257, [24, 25], "≈10-20秒/25帧"),
            VideoRecommendation("360p", 640, 352, 569, 401, [24, 25, 30], "≈5-10秒/25帧"),
        ],
        tips=[
            "720p 是最佳平衡点，画质与速度兼顾",
            "关闭 upscaler + 启用 offload 是必须的",
            "帧预算是硬限制：调高FPS→推荐秒数自动缩短",
        ],
    ),

    "low": HardwareProfile(
        tier="low",
        tier_name="节能模式",
        vram_range="10-14GB (RTX 4070 Ti / RTX 3080 12GB / RTX 2080 Ti)",
        vram_limit_gb=12,
        prefetch_count=4,
        offload_enabled=True,
        upscaler_enabled=False,
        recommendations=[
            VideoRecommendation("720p (挑战)", 1280, 704, 89, 57, [24], "≈90-180秒/25帧"),
            VideoRecommendation("540p (推荐)", 960, 544, 161, 97, [24, 25], "≈40-80秒/25帧"),
            VideoRecommendation("480p (快速)", 768, 416, 257, 161, [24, 25], "≈25-50秒/25帧"),
            VideoRecommendation("360p", 640, 352, 401, 257, [24, 25], "≈15-30秒/25帧"),
        ],
        tips=[
            "540p 是稳定首选，720p 仅短片段",
            "推理较慢（offload 频繁搬运权重），请耐心等待",
            "确保系统内存 ≥ 32GB，否则 offload 可能内存不足",
        ],
    ),

    "minimal": HardwareProfile(
        tier="minimal",
        tier_name="极限模式",
        vram_range="<10GB (RTX 4060 / RTX 3060 / GTX 1080 Ti)",
        vram_limit_gb=8,
        prefetch_count=1,
        offload_enabled=True,
        upscaler_enabled=False,
        recommendations=[
            VideoRecommendation("540p (挑战)", 960, 544, 89, 57, [24], "≈120-240秒/25帧"),
            VideoRecommendation("480p (推荐)", 768, 416, 161, 97, [24], "≈60-120秒/25帧"),
            VideoRecommendation("360p (快速)", 640, 352, 257, 161, [24], "≈40-80秒/25帧"),
        ],
        tips=[
            "480p 是稳定首选，540p 有 OOM 风险",
            "推理非常慢，请做好等待准备",
            "必须确保系统内存 ≥ 32GB",
        ],
    ),
}


# ── 硬件检测 ─────────────────────────────────────────────────────

def detect_gpu_vram_gb() -> tuple[float | None, str]:
    """检测主 GPU 的 VRAM 大小和名称。

    Returns:
        (vram_gb, gpu_name) — 检测失败返回 (None, "")
    """
    # 优先 PyTorch
    try:
        import torch
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            vram_gb = props.total_memory / (1024 ** 3)
            name = torch.cuda.get_device_name(0)
            return vram_gb, name
    except Exception:
        pass

    # 回退 nvidia-smi
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | 0x00000008
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,name", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            startupinfo=si, creationflags=flags,
        )
        if result.returncode == 0:
            first_line = result.stdout.strip().split("\n")[0]
            parts = first_line.split(",")
            vram_mb = float(parts[0].strip())
            gpu_name = parts[1].strip() if len(parts) > 1 else ""
            return vram_mb / 1024, gpu_name
    except Exception:
        pass

    return None, ""


def detect_gpu_compute_capability() -> tuple[float | None, str]:
    """检测主 GPU 的计算能力版本和架构代号。

    Returns:
        (compute_cap, arch_name) — 检测失败返回 (None, "")
        compute_cap: 如 8.6, 8.9, 9.0
        arch_name: 如 "Ampere", "Ada Lovelace", "Hopper"
    """
    ARCH_MAP = [
        (10.0, "Blackwell"),
        (9.0, "Hopper"),
        (8.9, "Ada Lovelace"),
        (8.6, "Ampere"),
        (8.0, "Ampere"),
        (7.5, "Turing"),
        (7.0, "Volta"),
        (6.1, "Pascal"),
        (6.0, "Pascal"),
        (5.2, "Maxwell"),
        (5.0, "Maxwell"),
    ]

    try:
        import torch
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            major = props.major
            minor = props.minor
            cap = major + minor / 10
            arch = ""
            for threshold, name in ARCH_MAP:
                if cap >= threshold:
                    arch = name
                    break
            if not arch:
                arch = f"SM_{major}{minor}"
            return cap, arch
    except Exception:
        pass

    return None, ""


def detect_gpu_features() -> dict[str, Any]:
    """检测 GPU 特性支持情况。

    Returns:
        {
            "compute_capability": float | None,
            "arch_name": str,
            "fp8_native": bool,       # 原生FP8支持 (Ada Lovelace+, compute >= 8.9)
            "sage_attention": bool,   # SageAttention可用 (Ampere+, compute >= 8.0)
            "bf16": bool,             # BF16支持 (Ampere+, compute >= 8.0)
            "tensor_cores": bool,     # Tensor Core可用 (Volta+, compute >= 7.0)
        }
    """
    cap, arch = detect_gpu_compute_capability()

    fp8_native = cap is not None and cap >= 8.9
    sage_attention = cap is not None and cap >= 8.0
    bf16 = cap is not None and cap >= 8.0
    tensor_cores = cap is not None and cap >= 7.0

    return {
        "compute_capability": cap,
        "arch_name": arch,
        "fp8_native": fp8_native,
        "sage_attention": sage_attention,
        "bf16": bf16,
        "tensor_cores": tensor_cores,
    }


def detect_system_ram_gb() -> float | None:
    """检测系统可用内存 (GB)。"""
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        pass
    return None


def classify_gpu(vram_gb: float | None) -> str:
    """根据 VRAM 大小返回 GPU 等级 (tier)。

    Returns:
        "ultra" / "high" / "medium" / "low" / "minimal"
    """
    if vram_gb is None:
        return "high"  # 检测失败默认给 high（让用户自行调整）

    if vram_gb >= 32:
        return "ultra"
    if vram_gb >= 20:
        return "high"
    if vram_gb >= 14:
        return "medium"
    if vram_gb >= 10:
        return "low"
    return "minimal"


# ── 动态参数计算 ─────────────────────────────────────────────────

def _find_rec_for_quality(profile: HardwareProfile, quality: str) -> VideoRecommendation | None:
    """根据清晰度字符串 ("1080"/"720"/"540"/...) 找到对应的 VideoRecommendation。"""
    for r in profile.recommendations:
        if quality == "1080" and r.width >= 1500:
            return r
        if quality == "720" and 1000 <= r.width < 1500:
            return r
        if quality == "540" and 800 <= r.width < 1000:
            return r
        if quality == "480" and 600 <= r.width < 800:
            return r
        if quality == "360" and r.width < 600:
            return r
    # 回退：找最接近的
    if profile.recommendations:
        return profile.recommendations[0]
    return None


def calc_dynamic_params(tier: str, quality: str, fps: int) -> dict[str, Any]:
    """动态计算推荐参数 — 核心联动逻辑。

    核心公式: duration = total_frames / fps
    VRAM 限制的是总帧数，不是秒数。调高 FPS → 推荐秒数自动缩短。

    Args:
        tier: GPU 等级 ("ultra"/"high"/"medium"/"low"/"minimal")
        quality: 清晰度 ("1080"/"720"/"540"/"480"/"360")
        fps: 帧率

    Returns:
        {
            "tier": str,
            "quality": str,
            "width": int,
            "height": int,
            "fps": int,
            "max_total_frames": int,        # VRAM帧预算上限
            "recommended_total_frames": int, # 推荐安全帧数
            "max_duration_sec": int,         # 最大秒数 = max_total_frames / fps
            "recommended_duration_sec": int, # 推荐秒数 = recommended_total_frames / fps
        }
    """
    profile = PROFILES.get(tier, PROFILES["high"])
    rec = _find_rec_for_quality(profile, quality)

    if rec is None:
        return {
            "tier": tier, "quality": quality, "fps": fps,
            "error": f"no recommendation for quality={quality} tier={tier}",
        }

    max_duration = rec.max_total_frames // fps
    rec_duration = rec.recommended_total_frames // fps

    # 至少 1 秒
    max_duration = max(max_duration, 1)
    rec_duration = max(rec_duration, 1)

    return {
        "tier": tier,
        "quality": quality,
        "width": rec.width,
        "height": rec.height,
        "fps": fps,
        "max_total_frames": rec.max_total_frames,
        "recommended_total_frames": rec.recommended_total_frames,
        "max_duration_sec": max_duration,
        "recommended_duration_sec": rec_duration,
    }


# ── 核心接口 ─────────────────────────────────────────────────────

def get_profile(tier: str | None = None) -> HardwareProfile:
    """获取指定等级的档案，默认根据硬件自动检测。"""
    if tier is None:
        vram_gb, _ = detect_gpu_vram_gb()
        tier = classify_gpu(vram_gb)
    return PROFILES.get(tier, PROFILES["high"])


def auto_detect_profile() -> dict[str, Any]:
    """自动检测硬件并返回完整推荐方案（供 API 返回）。

    Returns:
        {
            "gpu_name": str,
            "gpu_vram_gb": float | None,
            "system_ram_gb": float | None,
            "tier": str,
            "tier_name": str,
            "vram_range": str,
            "vram_limit_gb": float,
            "prefetch_count": int | None,
            "offload_enabled": bool,
            "upscaler_enabled": bool,
            "recommendations": [...],  # 含帧预算字段
            "tips": [...],
        }
    """
    vram_gb, gpu_name = detect_gpu_vram_gb()
    ram_gb = detect_system_ram_gb()
    tier = classify_gpu(vram_gb)
    profile = PROFILES[tier]

    # 内存充裕时的微调
    extra_tips: list[str] = []
    if ram_gb is not None and ram_gb >= 64 and profile.offload_enabled:
        extra_tips.append(f"系统内存 {ram_gb:.0f}GB 充裕，offload 性能开销较小")

    return {
        "gpu_name": gpu_name,
        "gpu_vram_gb": round(vram_gb, 1) if vram_gb is not None else None,
        "system_ram_gb": round(ram_gb, 1) if ram_gb is not None else None,
        "tier": profile.tier,
        "tier_name": profile.tier_name,
        "vram_range": profile.vram_range,
        "vram_limit_gb": profile.vram_limit_gb,
        "prefetch_count": profile.prefetch_count,
        "offload_enabled": profile.offload_enabled,
        "upscaler_enabled": profile.upscaler_enabled,
        "recommendations": [
            {
                "label": r.label,
                "width": r.width,
                "height": r.height,
                "max_total_frames": r.max_total_frames,
                "recommended_total_frames": r.recommended_total_frames,
                "recommended_fps": r.recommended_fps,
                "speed_estimate": r.speed_estimate,
            }
            for r in profile.recommendations
        ],
        "tips": list(profile.tips) + extra_tips,
    }


def install(app, ctx: ExtensionContext) -> None:
    """扩展入口 — 注册硬件档案 API 端点。

    实际 API 注册由 system_api.py 的 install() 完成，
    此处仅确保模块被加载。

    注意: 启动时不调用 detect_gpu_vram_gb()，
    因为此时 CUDA 可能尚未初始化，直接调用 torch.cuda 会导致
    原生崩溃 (ACCESS_VIOLATION 0xC0000005)。
    GPU 检测延迟到 API 被调用时执行 (lazy)。
    """
    logger.info("hardware_profiles: module loaded, GPU detection deferred to API call")


def apply_profile_to_settings(profile: HardwareProfile) -> None:
    """将档案推荐值写入 settings.json 和 low_vram 偏好。"""
    import json
    from pathlib import Path
    from low_vram_runtime import _ltx_desktop_config_dir

    config_dir = _ltx_desktop_config_dir()
    settings_file = config_dir / "settings.json"

    # 读取现有设置
    settings: dict[str, Any] = {}
    if settings_file.exists():
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception:
            pass

    # 写入 vram_limit
    if profile.vram_limit_gb > 0:
        settings["vram_limit"] = profile.vram_limit_gb
    elif "vram_limit" in settings:
        # ultra tier: 不限制
        del settings["vram_limit"]

    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

    # 写入 low_vram 偏好
    from low_vram_runtime import write_low_vram_pref
    write_low_vram_pref(profile.offload_enabled)

    logger.info(
        "apply_profile_to_settings: tier=%s, vram_limit=%s, offload=%s",
        profile.tier, profile.vram_limit_gb, profile.offload_enabled,
    )
