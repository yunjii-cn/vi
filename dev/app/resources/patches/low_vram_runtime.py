"""低显存模式：尽量降峰值显存（以速度换显存）；效果取决于官方管线是否支持 offload。"""

from __future__ import annotations

import gc
import logging
import os
import types
from pathlib import Path
from typing import Any

logger = logging.getLogger("ltx_low_vram")


def _ltx_desktop_config_dir() -> Path:
    app_data_dir = os.environ.get("LTX_APP_DATA_DIR")
    if app_data_dir:
        p = Path(app_data_dir)
    else:
        p = (
            Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~/AppData/Local")))
            / "LTXDesktop"
        )
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


def low_vram_pref_path() -> Path:
    return _ltx_desktop_config_dir() / "low_vram_mode.pref"


def read_low_vram_pref() -> bool:
    f = low_vram_pref_path()
    if not f.is_file():
        return False
    return f.read_text(encoding="utf-8").strip().lower() in ("1", "true", "yes", "on")


def write_low_vram_pref(enabled: bool) -> None:
    low_vram_pref_path().write_text(
        "true\n" if enabled else "false\n", encoding="utf-8"
    )


def apply_low_vram_config_tweaks(handler: Any) -> None:
    """在官方 RuntimeConfig 上尽量关闭 fast 放大等（若字段存在）。"""
    cfg = getattr(handler, "config", None)
    if cfg is None:
        return
    fm = getattr(cfg, "fast_model", None)
    if fm is None:
        return
    try:
        if hasattr(fm, "model_copy"):
            updated = fm.model_copy(update={"use_upscaler": False})
            setattr(cfg, "fast_model", updated)
        elif hasattr(fm, "use_upscaler"):
            setattr(fm, "use_upscaler", False)
    except Exception as e:
        logger.debug("low_vram: 无法关闭 fast_model.use_upscaler: %s", e)


def restore_full_vram_config_tweaks(handler: Any) -> None:
    """显存上限为 0 时恢复速度优先配置。"""
    cfg = getattr(handler, "config", None)
    if cfg is None:
        return
    fm = getattr(cfg, "fast_model", None)
    if fm is None:
        return
    try:
        if hasattr(fm, "model_copy"):
            updated = fm.model_copy(update={"use_upscaler": True})
            setattr(cfg, "fast_model", updated)
        elif hasattr(fm, "use_upscaler"):
            setattr(fm, "use_upscaler", True)
    except Exception as e:
        logger.debug("low_vram: 无法恢复 fast_model.use_upscaler: %s", e)


def install_low_vram_on_pipelines(handler: Any) -> None:
    """启动时读取偏好，挂到 pipelines 上供各补丁读取。

    自动检测逻辑：当 GPU VRAM <= 24GB 且用户未手动关闭时，
    自动启用低显存模式以防止 CUDA OOM 导致的 ACCESS_VIOLATION 崩溃。
    """
    pl = handler.pipelines
    user_pref = read_low_vram_pref()
    auto_low = auto_detect_low_vram()
    low = (user_pref and should_use_cpu_offload()) or auto_low
    setattr(pl, "low_vram_mode", bool(low))
    if low:
        apply_low_vram_config_tweaks(handler)
        if auto_low and not (user_pref and should_use_cpu_offload()):
            logger.info(
                "low_vram_mode: 已自动开启（GPU VRAM ≤ 24GB，防止 CUDA OOM 崩溃）"
            )
        else:
            logger.info(
                "low_vram_mode: 已开启（尝试关闭 fast 放大；若显存仍高，多为权重常驻 GPU，需降分辨率/时长或 FP8 权重）"
            )
    else:
        restore_full_vram_config_tweaks(handler)


def install_low_vram_pipeline_hooks(pl: Any) -> None:
    """在 load_gpu_pipeline / load_a2v 返回后尝试 Diffusers 式 CPU offload（无则静默）。"""
    if getattr(pl, "_ltx_low_vram_hooks_installed", False):
        return
    pl._ltx_low_vram_hooks_installed = True

    if hasattr(pl, "load_gpu_pipeline"):
        _orig_gpu = pl.load_gpu_pipeline
        pl._ltx_orig_load_gpu_for_low_vram = _orig_gpu

        def _load_gpu_wrapped(self: Any, *a: Any, **kw: Any) -> Any:
            r = _orig_gpu(*a, **kw)
            if getattr(self, "low_vram_mode", False):
                try_sequential_offload_on_pipeline_state(r)
            return r

        pl.load_gpu_pipeline = types.MethodType(_load_gpu_wrapped, pl)

    if hasattr(pl, "load_a2v_pipeline"):
        _orig_a2v = pl.load_a2v_pipeline
        pl._ltx_orig_load_a2v_for_low_vram = _orig_a2v

        def _load_a2v_wrapped(self: Any, *a: Any, **kw: Any) -> Any:
            r = _orig_a2v(*a, **kw)
            if getattr(self, "low_vram_mode", False):
                try_sequential_offload_on_pipeline_state(r)
            return r

        pl.load_a2v_pipeline = types.MethodType(_load_a2v_wrapped, pl)

    if hasattr(pl, "load_ic_lora"):
        _orig_ic_lora = pl.load_ic_lora
        pl._ltx_orig_load_ic_lora_for_low_vram = _orig_ic_lora

        def _load_ic_lora_wrapped(self: Any, *a: Any, **kw: Any) -> Any:
            r = _orig_ic_lora(*a, **kw)
            if getattr(self, "low_vram_mode", False):
                try_sequential_offload_on_pipeline_state(r)
            return r

        pl.load_ic_lora = types.MethodType(_load_ic_lora_wrapped, pl)

    # Monkey patch: 接管 1.0.3 新增的底层 layer streaming 来实现完美的线性显存控制
    if not getattr(pl, "_ltx_layer_streaming_patched", False):
        pl._ltx_layer_streaming_patched = True
        try:
            def _patch_pipeline_class(cls_name, mod_name):
                import importlib
                try:
                    mod = importlib.import_module(mod_name)
                    pipeline_cls = getattr(mod, cls_name)
                    _orig_call = pipeline_cls.__call__
                    
                    def _patched_call(self, *args, **kwargs):
                        lim = get_vram_limit()
                        auto = auto_detect_low_vram()
                        if lim is not None or auto:
                            effective_lim = lim
                            if effective_lim is None and auto:
                                try:
                                    import torch
                                    if torch.cuda.is_available():
                                        effective_lim = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
                                except Exception:
                                    effective_lim = 24.0
                            is_bf16 = _is_bf16_model_pipeline(self)
                            count = get_streaming_prefetch_count(effective_lim, is_bf16_model=is_bf16)
                            kwargs["streaming_prefetch_count"] = count
                            if count is None:
                                logger.info(
                                    "low_vram_mode: VRAM limit is unlimited/high. Disabled layer streaming."
                                )
                            else:
                                logger.info(
                                    "low_vram_mode: Dynamically tuned layer streaming prefetch count to %s for %sGB limit (model=%s).",
                                    count,
                                    effective_lim,
                                    "BF16" if is_bf16 else "FP8",
                                )
                                
                        return _orig_call(self, *args, **kwargs)
                        
                    pipeline_cls.__call__ = _patched_call
                    logger.info(f"low_vram_mode: Successfully patched {cls_name} to override streaming_prefetch_count")
                except Exception as e:
                    pass

            _patch_pipeline_class("DistilledPipeline", "ltx_pipelines.distilled")
            _patch_pipeline_class("TI2VidTwoStagesPipeline", "ltx_pipelines.ti2vid_two_stages")
            _patch_pipeline_class("LTXRetakePipeline", "services.retake_pipeline.ltx_retake_pipeline")
            _patch_pipeline_class("ICLoRAPipeline", "services.ic_lora_pipeline.ltx_ic_lora_pipeline")
            _patch_pipeline_class("A2VPipeline", "services.a2v_pipeline.distilled_a2v_pipeline")
        except Exception:
            pass


def get_vram_limit() -> float | None:
    try:
        import json
        from pathlib import Path
        settings_file = _ltx_desktop_config_dir() / "settings.json"
        if settings_file.exists():
            with open(settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "vram_limit" in data:
                lim = data["vram_limit"]
                if lim != "":
                    return float(lim)
    except Exception:
        pass
    return None


def _is_bf16_model_pipeline(pipeline: Any) -> bool:
    for attr in ("stage", "stage_1", "stage_2"):
        stage = getattr(pipeline, attr, None)
        if stage is not None:
            quant = getattr(stage, "_quantization", None)
            if quant is not None:
                return False
    return True


def get_streaming_prefetch_count(vram_limit: float | None = None, is_bf16_model: bool = False) -> int | None:
    """把设置里的显存上限映射为 layer streaming 强度。

    ``0`` 或留空表示纯 GPU / 速度优先，不启用层流式加载。
    
    Args:
        vram_limit: 显存上限 (GB)。若为 None 则从 settings.json 读取。
        is_bf16_model: 是否为 BF16 模型（无量化）。BF16 模型每层约 1.2GB，
            FP8 模型每层约 0.67GB，需要更保守的 prefetch count 以避免 OOM。
    """
    if vram_limit is None:
        lim = get_vram_limit()
    else:
        lim = vram_limit
    if lim is None or lim == 0:
        return None
    if lim <= 10.0:
        return 1
    if lim >= 27.0 and not is_bf16_model:
        return None
    if lim >= 48.0 and is_bf16_model:
        return None

    if is_bf16_model:
        per_layer_gb = 1.2
        reserved_gb = 18.0
    else:
        per_layer_gb = 0.67
        reserved_gb = 10.0

    available_gb = float(lim) - reserved_gb
    if available_gb <= 0:
        return 1

    max_layers_on_gpu = int(available_gb / per_layer_gb)
    return max(1, min(32, max_layers_on_gpu - 1))


def should_use_cpu_offload() -> bool:
    """是否启用 CPU/offload 路径。

    启用条件（满足任一）：
      1. 用户在设置中手动配置了 vram_limit > 0
      2. 自动检测到 GPU VRAM ≤ 24GB（需配合 low_vram_mode 标志）
    """
    lim = get_vram_limit()
    if lim is not None and lim > 0:
        return True
    # 未手动设置 vram_limit 时，由 auto_detect_low_vram 决定
    return auto_detect_low_vram()


def auto_detect_low_vram() -> bool:
    """自动检测是否需要低显存模式（不依赖用户手动设置）。

    当 GPU 总 VRAM <= 24GB 时，22B 模型推理很容易 OOM 导致 CUDA 驱动崩溃，
    因此自动返回 True 以启用 sequential offload 保护。
    结果会被缓存到 _auto_low_vram_cached 避免重复查询。
    """
    # 缓存：避免每次调用都查询 GPU 属性
    cached = globals().get("_auto_low_vram_cached")
    if cached is not None:
        return cached

    # 用户手动设置了 vram_limit，直接用
    lim = get_vram_limit()
    if lim is not None and lim > 0:
        globals()["_auto_low_vram_cached"] = True
        return True

    # 未手动设置时，根据实际 VRAM 自动判断
    # 阈值 26GB：覆盖 3090 (24GB) / 4090 (24GB) / 4080 (16GB) 等
    # 22B fp8 模型权重 ≈ 22GB，≤26GB 的卡都需要 offload 才能安全跑
    result = False
    try:
        import torch
        if torch.cuda.is_available():
            total_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
            result = total_gb <= 26.0
            if result:
                logger.info(
                    "auto_detect_low_vram: GPU VRAM = %.1fGB (≤ 26GB)，自动启用低显存模式",
                    total_gb,
                )
    except Exception:
        pass
    globals()["_auto_low_vram_cached"] = result
    return result


def try_sequential_offload_on_pipeline_state(state: Any, force: bool = False) -> None:
    """按设定最高显存分配，爆显存后写入系统内存。

    Args:
        state: pipeline state 对象
        force: 强制应用 offload，无视 should_use_cpu_offload() 检查
               （用于 video_gen_patch 中自动检测到显存紧张时）
    """
    if state is None:
        return
    if not force and not should_use_cpu_offload():
        logger.info(
            "low_vram_mode: VRAM limit is 0/blank. Skip CPU offload for pure GPU speed."
        )
        return
    root = getattr(state, "pipeline", state)
    candidates: list[Any] = [root]
    inner = getattr(root, "pipeline", None)
    if inner is not None and inner is not root:
        candidates.append(inner)
        
    # Capped-VRAM mode applies macro offload so T5/VAE can leave GPU while DiT runs.
    # Pure GPU mode returns above and keeps the old fast path.
    for obj in candidates:
        for method_name in (
            "enable_model_cpu_offload",
            "enable_sequential_cpu_offload",
        ):
            fn = getattr(obj, method_name, None)
            if callable(fn):
                try:
                    fn()
                    logger.info(
                        "low_vram_mode: 已对管线调用 %s()",
                        method_name,
                    )
                    return
                except Exception as e:
                    logger.debug(
                        "low_vram_mode: %s() 失败（可忽略）: %s",
                        method_name,
                        e,
                    )


def maybe_release_pipeline_after_task(handler: Any) -> None:
    """单次生成结束后：低显存模式下强制卸载管线并回收缓存。"""
    pl = getattr(handler, "pipelines", None) or getattr(handler, "_pipelines", None)
    if pl is None or not getattr(pl, "low_vram_mode", False):
        return
    try:
        from keep_models_runtime import force_unload_gpu_pipeline

        force_unload_gpu_pipeline(pl)
    except Exception as e:
        logger.debug("low_vram_mode: 任务后卸载失败: %s", e)
    try:
        pl._pipeline_signature = None
    except Exception:
        pass
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
