"""VideoGeneration handler monkey-patch.

Enhanced generate/generate_video with:
  - Multi-keyframe support (startFrame, endFrame, keyframePaths)
  - Custom resolution (customWidth, customHeight, aspectRatio)
  - Multi-LoRA support
  - Custom model checkpoint selection (dev, fp8, distilled)
  - A2V audio pipeline integration
  - Keyframe time mapping
  - Auto low-VRAM detection & CUDA OOM protection

Upstream dependency: handlers.video_generation_handler.VideoGenerationHandler
                    api_types.GenerateVideoRequest
                    services.fast_video_pipeline.ltx_fast_video_pipeline
                    ltx_pipelines.utils.args.ImageConditioningInput
When upstream changes VideoGenerationHandler, review this ENTIRE patch carefully.
This is the most upstream-sensitive extension.
"""

from __future__ import annotations

import math
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from api_types import GenerateVideoRequest
from extensions._context import ExtensionContext
from extensions._utils import ensure_a2v_stereo_audio


# ── VRAM 检测 ──────────────────────────────────────────────────────
def _get_available_vram_gb() -> float | None:
    """返回当前 GPU 可用显存 (GB)，查询失败返回 None。"""
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        free_b = torch.cuda.mem_get_info()[0]  # (free, total)
        return free_b / (1024 ** 3)
    except Exception:
        return None


def _get_total_vram_gb() -> float | None:
    """返回 GPU 总显存 (GB)，查询失败返回 None。"""
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        return torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
    except Exception:
        return None


def install(app, ctx: ExtensionContext) -> None:
    from handlers.video_generation_handler import VideoGenerationHandler
    from server_utils.media_validation import normalize_optional_path
    from PIL import Image

    _orig_generate = VideoGenerationHandler.generate
    _orig_generate_video = VideoGenerationHandler.generate_video

    def patched_generate(self, req: GenerateVideoRequest):
        gen = self._generation
        is_running = (
            gen.is_generation_running()
            if hasattr(gen, "is_generation_running")
            else "?方法不存在"
        )
        gen_id = getattr(gen, "_generation_id", "?属性不存在")
        is_gen = getattr(gen, "_is_generating", "?属性不存在")
        cancelled = getattr(
            gen, "_cancelled", getattr(gen, "_is_cancelled", "?属性不存在")
        )
        print(f"\n[PATCH][patched_generate] ==> 收到新请求")
        print(f"  is_generation_running() = {is_running}")
        print(f"  _generation_id          = {gen_id}")
        print(f"  _is_generating          = {is_gen}")
        print(f"  _cancelled              = {cancelled}")
        start_frame_path = normalize_optional_path(getattr(req, "startFramePath", None))
        end_frame_path = normalize_optional_path(getattr(req, "endFramePath", None))
        _raw_kf = getattr(req, "keyframePaths", None)
        keyframe_paths_list: list[str] = []
        if isinstance(_raw_kf, list):
            for p in _raw_kf:
                np = normalize_optional_path(p)
                if np:
                    keyframe_paths_list.append(np)
        use_multi_keyframes = len(keyframe_paths_list) >= 2
        _raw_kf_st = getattr(req, "keyframeStrengths", None)
        keyframe_strengths_list: list[float] | None = None
        if isinstance(_raw_kf_st, list) and _raw_kf_st:
            try:
                keyframe_strengths_list = [float(x) for x in _raw_kf_st]
            except (TypeError, ValueError):
                keyframe_strengths_list = None
        _raw_kf_t = getattr(req, "keyframeTimes", None)
        keyframe_times_list: list[float] | None = None
        if isinstance(_raw_kf_t, list) and _raw_kf_t:
            try:
                keyframe_times_list = [float(x) for x in _raw_kf_t]
            except (TypeError, ValueError):
                keyframe_times_list = None
        aspect_ratio = getattr(req, "aspectRatio", None)
        print(f"  startFramePath          = {start_frame_path}")
        print(f"  endFramePath            = {end_frame_path}")
        print(f"  keyframePaths (n={len(keyframe_paths_list)}) = {use_multi_keyframes}")
        print(f"  aspectRatio             = {aspect_ratio}")

        audio_path = normalize_optional_path(getattr(req, "audioPath", None))
        print(f"[PATCH] audio_path = {audio_path}")

        image_path = normalize_optional_path(getattr(req, "imagePath", None))
        print(f"[PATCH] image_path = {image_path}")

        print(f"[PATCH] 使用自定义逻辑处理")

        resolution = req.resolution
        duration = int(float(req.duration))
        fps = int(float(req.fps))
        motion_speed = float(getattr(req, "motionSpeed", 1.0) or 1.0)
        if motion_speed < 0.25:
            motion_speed = 0.25
        if motion_speed > 3.0:
            motion_speed = 3.0

        RESOLUTION_MAP = {
            "360p": (640, 352),
            "480p": (768, 416),
            "540p": (1024, 576),
            "720p": (1280, 704),
            "1080p": (1920, 1088),
        }

        def get_16_9_size(res):
            return RESOLUTION_MAP.get(res, (1280, 704))

        def get_9_16_size(res):
            w, h = get_16_9_size(res)
            return h, w

        def _round64(v: float) -> int:
            return max(64, int(round(float(v) / 64.0) * 64))

        def get_ratio_size(res, ratio_text):
            base_w, base_h = get_16_9_size(res)
            try:
                a, b = str(ratio_text or "16:9").split(":", 1)
                ratio = float(a) / float(b)
            except Exception:
                return base_w, base_h
            if ratio <= 0:
                return base_w, base_h
            short_side = base_h
            if ratio >= 1:
                return _round64(short_side * ratio), _round64(short_side)
            return _round64(short_side), _round64(short_side / ratio)

        custom_w = getattr(req, "customWidth", None)
        custom_h = getattr(req, "customHeight", None)
        try:
            custom_w_i = int(custom_w) if custom_w is not None else 0
            custom_h_i = int(custom_h) if custom_h is not None else 0
        except (TypeError, ValueError):
            custom_w_i = 0
            custom_h_i = 0

        if custom_w_i > 0 and custom_h_i > 0:
            width, height = _round64(custom_w_i), _round64(custom_h_i)
        elif req.aspectRatio == "9:16":
            width, height = get_9_16_size(resolution)
        elif req.aspectRatio == "16:9":
            width, height = get_16_9_size(resolution)
        else:
            width, height = get_ratio_size(resolution, req.aspectRatio)

        num_frames = ((duration * fps) // 8) * 8 + 1
        num_frames = max(num_frames, 9)

        # ── VRAM 信息日志 ──
        vram_gb = _get_available_vram_gb()
        total_vram_gb = _get_total_vram_gb()
        print(f"[PATCH] VRAM: 可用={vram_gb}GB, 总量={total_vram_gb}GB, 分辨率={width}x{height}, 帧数={num_frames}")

        print(f"[PATCH] 计算得到的分辨率: {width}x{height}, 帧数: {num_frames}")

        if use_multi_keyframes:
            self._start_frame_path = None
            self._end_frame_path = None
            image_path_for_video = None
        else:
            self._start_frame_path = start_frame_path
            self._end_frame_path = end_frame_path
            image_path_for_video = image_path

        generation_id = self._make_generation_id()
        self._generation.start_generation(generation_id)

        try:
            req_seed = getattr(req, "seed", None)
            try:
                req_seed = int(req_seed) if req_seed is not None else None
            except (TypeError, ValueError):
                req_seed = None
            result = patched_generate_video(
                self,
                prompt=req.prompt,
                image=None,
                image_path=image_path_for_video,
                height=height,
                width=width,
                num_frames=num_frames,
                fps=fps,
                seed=req_seed if req_seed and req_seed > 0 else self._resolve_seed(),
                camera_motion=req.cameraMotion,
                negative_prompt=req.negativePrompt,
                audio_path=audio_path,
                lora_path=getattr(req, "loraPath", None),
                lora_strength=float(getattr(req, "loraStrength", 1.0) or 1.0),
                lora_paths=getattr(req, "loraPaths", None),
                lora_strengths=getattr(req, "loraStrengths", None),
                model_path=getattr(req, "modelPath", None),
                keyframe_paths=keyframe_paths_list if use_multi_keyframes else None,
                keyframe_strengths=(
                    keyframe_strengths_list if use_multi_keyframes else None
                ),
                keyframe_times=(keyframe_times_list if use_multi_keyframes else None),
                distilled=getattr(req, "distilled", True),
                num_inference_steps=getattr(req, "numInferenceSteps", None),
                motion_speed=motion_speed,
            )
            if result is None:
                print(f"[PATCH][patched_generate] <== 推理已取消")
                return type("Response", (), {"status": "cancelled"})()
            print(f"[PATCH][patched_generate] <== 完成, 返回状态: complete")
            return type("Response", (), {"status": "complete", "video_path": result})()
        except RuntimeError as e:
            import traceback
            err_msg = str(e)
            if "cancel" in err_msg.lower():
                print(f"[PATCH][patched_generate] 推理已被用户取消")
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass
                return type("Response", (), {"status": "cancelled"})()
            self._generation.fail_generation(err_msg)
            if "out of memory" in err_msg.lower() or "CUDA" in err_msg:
                print(f"[PATCH][patched_generate] CUDA OOM: {e}")
                traceback.print_exc()
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass
                raise RuntimeError(
                    f"显存不足 (CUDA Out of Memory)。建议：\n"
                    f"1. 降低分辨率（如 1080p → 720p）\n"
                    f"2. 减少帧数（如缩短时长）\n"
                    f"3. 在设置中开启「低显存模式」\n"
                    f"原始错误: {e}"
                ) from e
            raise
        except Exception as e:
            import traceback
            print(f"[PATCH][patched_generate] 错误: {e}")
            traceback.print_exc()
            self._generation.fail_generation(str(e))
            raise

    def patched_generate_video(
        self,
        prompt,
        image,
        image_path=None,
        height=None,
        width=None,
        num_frames=None,
        fps=None,
        seed=None,
        camera_motion=None,
        negative_prompt=None,
        audio_path=None,
        lora_path=None,
        lora_strength=1.0,
        lora_paths: list[str] | None = None,
        lora_strengths: list[float] | None = None,
        keyframe_paths: list[str] | None = None,
        keyframe_strengths: list[float] | None = None,
        keyframe_times: list[float] | None = None,
        model_path: str | None = None,
        distilled: bool = True,
        num_inference_steps: int | None = None,
        motion_speed: float = 1.0,
    ):
        gen = self._generation
        is_running = (
            gen.is_generation_running()
            if hasattr(gen, "is_generation_running")
            else "?方法不存在"
        )
        gen_id = getattr(gen, "_generation_id", "?属性不存在")
        is_gen = getattr(gen, "_is_generating", "?属性不存在")
        print(f"[PATCH][patched_generate_video] ==> 开始推理")
        print(f"  is_generation_running() = {is_running}")
        print(f"  _generation_id          = {gen_id}")
        print(f"  _is_generating          = {is_gen}")
        print(f"  resolution              = {width}x{height}, frames={num_frames}, fps={fps}")
        print(f"  motion_speed            = {motion_speed}")
        inference_frame_rate = max(1.0, float(fps) / motion_speed)
        print(f"  inference_frame_rate    = {inference_frame_rate} (fps={fps} / motion_speed={motion_speed})")
        print(f"  image param             = {type(image)}, {image is not None}")
        print(f"  image_path              = {image_path}")
        from ltx_pipelines.utils.args import (
            ImageConditioningInput as LtxImageConditioningInput,
        )

        images_inputs = []
        temp_paths = []
        kf_list = [p for p in (keyframe_paths or []) if p]
        use_multi_kf = len(kf_list) >= 2

        start_path = getattr(self, "_start_frame_path", None)
        end_path = getattr(self, "_end_frame_path", None)
        print(f"[PATCH] start_path={start_path}, end_path={end_path}, multi_kf={use_multi_kf} n={len(kf_list)}")

        latent_num_frames = (num_frames - 1) // 8 + 1
        last_latent_idx = latent_num_frames - 1
        uses_latent_frame_idx = bool(audio_path)
        last_conditioning_idx = last_latent_idx if uses_latent_frame_idx else num_frames - 1
        print(
            f"[PATCH] latent_num_frames={latent_num_frames}, last_latent_idx={last_latent_idx}, "
            f"conditioning_idx_mode={'latent' if uses_latent_frame_idx else 'frame'}, "
            f"last_conditioning_idx={last_conditioning_idx}"
        )

        if use_multi_kf:
            n_kf = len(kf_list)
            st_override = keyframe_strengths or []
            if len(st_override) not in (0, n_kf):
                print(f"[PATCH] keyframeStrengths 长度({len(st_override)})与关键帧数({n_kf})不一致，改用默认强度曲线")
                st_override = []

            def _default_multi_guide_strength(i: int, n: int) -> float:
                if n <= 2:
                    return 1.0
                if i == 0:
                    return 0.62
                if i == n - 1:
                    return 1.0
                return 0.42

            kt = keyframe_times or []
            times_match = len(kt) == n_kf
            if times_match:
                fps_f = max(float(fps), 0.001)
                max_t = (num_frames - 1) / fps_f
                fi_list: list[int] = []
                for ki in range(n_kf):
                    t_sec = max(0.0, min(max_t, float(kt[ki])))
                    pf = int(round(t_sec * fps_f))
                    pf = min(num_frames - 1, max(0, pf))
                    fi = pf // 8 if uses_latent_frame_idx else pf
                    fi = min(last_conditioning_idx, max(0, fi))
                    fi_list.append(int(fi))
                for j in range(1, n_kf):
                    if fi_list[j] <= fi_list[j - 1]:
                        fi_list[j] = min(last_conditioning_idx, fi_list[j - 1] + 1)
                print(f"[PATCH] Multi-keyframe: 使用 keyframeTimes 映射 -> {fi_list}")
            else:
                fi_list = []
                prev_fi = -1
                for ki in range(n_kf):
                    if last_conditioning_idx <= 0:
                        fi = 0
                    elif ki == 0:
                        fi = 0
                    elif ki == n_kf - 1:
                        fi = last_conditioning_idx
                    else:
                        pf = int(round(ki * (num_frames - 1) / max(1, (n_kf - 1))))
                        fi = pf // 8 if uses_latent_frame_idx else pf
                        fi = min(last_conditioning_idx - 1, max(1, fi))
                        if fi <= prev_fi:
                            fi = min(last_conditioning_idx - 1, prev_fi + 1)
                    prev_fi = fi
                    fi_list.append(int(fi))

            for ki, kp in enumerate(kf_list):
                if not os.path.isfile(kp):
                    raise RuntimeError(f"多关键帧路径无效或不存在: {kp}")
                fi = fi_list[ki]
                if len(st_override) == n_kf:
                    st = float(st_override[ki])
                    st = max(0.1, min(1.0, st))
                else:
                    st = _default_multi_guide_strength(ki, n_kf)
                img = self._prepare_image(kp, width, height)
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
                img.save(tmp)
                temp_paths.append(tmp)
                tmp_normalized = tmp.replace("\\", "/")
                images_inputs.append(
                    LtxImageConditioningInput(path=tmp_normalized, frame_idx=int(fi), strength=float(st))
                )
                print(f"[PATCH] Multi-keyframe [{ki}]: {tmp_normalized}, frame_idx={fi}, strength={st:.3f}")
        else:
            if not start_path and not end_path and image_path:
                print(f"[PATCH] 使用 image_path 作为起始帧: {image_path}")
                start_path = image_path

            has_image_param = image is not None
            if has_image_param:
                print(f"[PATCH] image param is available, will be used as start frame")

            target_start_path = start_path if start_path else None
            if not target_start_path and image is not None:
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
                image.save(tmp)
                temp_paths.append(tmp)
                target_start_path = tmp
                print(f"[PATCH] Using image param as start frame: {target_start_path}")

            if target_start_path:
                start_img = self._prepare_image(target_start_path, width, height)
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
                start_img.save(tmp)
                temp_paths.append(tmp)
                tmp_normalized = tmp.replace("\\", "/")
                images_inputs.append(
                    LtxImageConditioningInput(path=tmp_normalized, frame_idx=0, strength=1.0)
                )
                print(f"[PATCH] Added start frame: {tmp_normalized}, frame_idx=0")

            if end_path:
                end_img = self._prepare_image(end_path, width, height)
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
                end_img.save(tmp)
                temp_paths.append(tmp)
                tmp_normalized = tmp.replace("\\", "/")
                images_inputs.append(
                    LtxImageConditioningInput(
                        path=tmp_normalized, frame_idx=last_conditioning_idx, strength=1.0,
                    )
                )
                print(f"[PATCH] Added end frame: {tmp_normalized}, frame_idx={last_conditioning_idx}")

        print(f"[PATCH] images_inputs count: {len(images_inputs)}")
        if images_inputs:
            for idx, img in enumerate(images_inputs):
                print(f"[PATCH] images_inputs[{idx}]: path={getattr(img, 'path', 'N/A')}, frame_idx={getattr(img, 'frame_idx', 'N/A')}, strength={getattr(img, 'strength', 'N/A')}")

        print(f"[PATCH] audio_path = {audio_path}")
        if audio_path:
            audio_path = ensure_a2v_stereo_audio(audio_path, temp_paths)
            print(f"[PATCH] a2v_audio_path = {audio_path}")

        if self._generation.is_generation_cancelled():
            raise RuntimeError("Generation was cancelled")

        generation_id = uuid.uuid4().hex[:8]

        extra_loras_for_hook: tuple | None = None
        gpu_slot = getattr(self._pipelines.state, "gpu_slot", None)
        active = getattr(gpu_slot, "active_pipeline", None) if gpu_slot else None
        cached_sig = getattr(self._pipelines, "_pipeline_signature", None)

        new_kind = "a2v" if audio_path else "fast"
        if (
            cached_sig
            and isinstance(cached_sig, tuple)
            and len(cached_sig) > 0
            and cached_sig[0] != new_kind
            and active is not None
        ):
            from keep_models_runtime import force_unload_gpu_pipeline
            print(f"[PATCH] 管线类型切换 {cached_sig[0]} -> {new_kind}，强制卸载旧模型")
            force_unload_gpu_pipeline(self._pipelines)
            gpu_slot = getattr(self._pipelines.state, "gpu_slot", None)
            active = getattr(gpu_slot, "active_pipeline", None) if gpu_slot else None

        if audio_path:
            desired_sig = ("a2v",)
            if model_path and str(model_path).strip():
                print("[PATCH] A2V 音频管线暂不支持自定义 checkpoint，已忽略 modelPath")
            print(f"[PATCH] 加载 A2V pipeline（支持音频）")
            pipeline_state = self._pipelines.load_a2v_pipeline()
            self._pipelines._pipeline_signature = desired_sig
            num_inference_steps = 11
        else:
            loras = []
            try:
                from ltx_core.loader import LoraPathStrengthAndSDOps
                from ltx_core.loader.sd_ops import LTXV_LORA_COMFY_RENAMING_MAP

                if lora_path and lora_path.strip() and os.path.exists(lora_path.strip()):
                    loras.append(LoraPathStrengthAndSDOps(
                        path=lora_path.strip(), strength=float(lora_strength), sd_ops=LTXV_LORA_COMFY_RENAMING_MAP
                    ))
                if lora_paths and lora_strengths:
                    for lp, ls in zip(lora_paths, lora_strengths):
                        if lp and lp.strip() and os.path.exists(lp.strip()):
                            p = lp.strip()
                            if not any(x.path == p for x in loras):
                                loras.append(LoraPathStrengthAndSDOps(
                                    path=p, strength=float(ls), sd_ops=LTXV_LORA_COMFY_RENAMING_MAP
                                ))
                                print(f"[PATCH] Multi-LoRA 已就绪: {p}, strength={ls}")
            except Exception as _lora_err:
                print(f"[PATCH] LoRA 准备失败，回退无 LoRA: {_lora_err}")
                loras = []

            if not loras:
                loras = None

            from runtime_config.model_download_specs import resolve_model_path
            from services.fast_video_pipeline.ltx_fast_video_pipeline import LTXFastVideoPipeline

            default_checkpoint_path = str(
                resolve_model_path(
                    self._pipelines.models_dir, self._pipelines.config.model_download_specs, "checkpoint",
                )
            )
            selected_checkpoint_path = default_checkpoint_path
            model_path_source = "default(specs)"
            if model_path and str(model_path).strip():
                selected_path = Path(str(model_path).strip()).expanduser()
                try:
                    selected_path = selected_path.resolve()
                except OSError:
                    pass
                if not selected_path.is_file():
                    raise RuntimeError(f"选择的模型文件不存在: {selected_path}")
                selected_checkpoint_path = str(selected_path)
                model_path_source = "user"
            else:
                bf16_default = Path(default_checkpoint_path)
                if bf16_default.is_file():
                    selected_checkpoint_path = str(bf16_default)
                    model_path_source = "default(bf16)"
                else:
                    for d in self._pipelines.models_dirs:
                        for fn in ("ltx-2.3-22b-distilled-1.1.safetensors", "ltx-2.3-22b-distilled.safetensors"):
                            candidate = d / fn
                            if candidate.is_file():
                                selected_checkpoint_path = str(candidate)
                                model_path_source = f"fallback({fn})"
                                break
                        if model_path_source != "default(specs)":
                            break
                    if model_path_source == "default(specs)":
                        fp8_path = str(
                            resolve_model_path(
                                self._pipelines.models_dir, self._pipelines.config.model_download_specs, "checkpoint_fp8",
                            )
                        )
                        if Path(fp8_path).is_file():
                            selected_checkpoint_path = fp8_path
                            model_path_source = "fallback(fp8)"

            using_custom_checkpoint = selected_checkpoint_path != default_checkpoint_path
            selected_checkpoint_name = Path(selected_checkpoint_path).name.lower()
            is_dev_checkpoint = (
                using_custom_checkpoint and "dev" in selected_checkpoint_name and "distilled" not in selected_checkpoint_name
            )
            is_prequant_fp8_checkpoint = (
                using_custom_checkpoint and not is_dev_checkpoint and "fp8" in selected_checkpoint_name
            )
            print(f"[PATCH] Fast checkpoint = {selected_checkpoint_path} (source={model_path_source})")
            if is_dev_checkpoint:
                print("[PATCH] 检测到 dev checkpoint，将使用 TI2V two-stage dev pipeline")
            elif is_prequant_fp8_checkpoint:
                print("[PATCH] 检测到预量化 FP8 distilled checkpoint，将使用 scaled-FP8 fallback pipeline")

            if loras is not None:
                sig_list = []
                for item in sorted(loras, key=lambda x: x.path):
                    sig_list.extend([item.path, round(float(item.strength), 4)])
                desired_sig = ("dev" if is_dev_checkpoint else "fast", selected_checkpoint_path, tuple(sig_list))
            else:
                desired_sig = ("dev" if is_dev_checkpoint else "fast", selected_checkpoint_path, "", 0.0)

            if cached_sig == desired_sig and active is not None:
                print(f"[PATCH] 复用 Fast pipeline: {desired_sig}")
                pipeline_state = active
            elif loras is not None or using_custom_checkpoint:
                print("[PATCH] 构建自定义 Fast pipeline（unload 后重建）")
                if (
                    loras is not None
                    and not is_dev_checkpoint
                    and not getattr(self, "_ltx_lora_warmup_done", False)
                ):
                    try:
                        print("[PATCH] LoRA warmup: 先加载无 LoRA fast pipeline 触发缓存")
                        self._pipelines.load_gpu_pipeline("fast", should_warm=True)
                        from keep_models_runtime import force_unload_gpu_pipeline
                        force_unload_gpu_pipeline(self._pipelines)
                        import gc
                        gc.collect()
                        try:
                            import torch
                            if torch.cuda.is_available():
                                torch.cuda.empty_cache()
                                torch.cuda.ipc_collect()
                        except Exception:
                            pass
                        self._ltx_lora_warmup_done = True
                    except Exception as _warm_err:
                        print(f"[PATCH] LoRA warmup failed (ignore): {_warm_err}")
                from keep_models_runtime import force_unload_gpu_pipeline
                force_unload_gpu_pipeline(self._pipelines)
                import gc
                gc.collect()
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        torch.cuda.ipc_collect()
                except Exception:
                    pass
                gemma_root = self._pipelines._text_handler.resolve_gemma_root()
                from state.app_state_types import GpuSlot, VideoPipelineState, VideoPipelineWarmth

                upsampler_path = str(
                    resolve_model_path(self._pipelines.models_dir, self._pipelines.config.model_download_specs, "upsampler")
                )
                from lora_injection import _lora_init_kwargs, inject_loras_into_fast_pipeline

                if is_dev_checkpoint:
                    distilled_lora_path = str(
                        resolve_model_path(self._pipelines.models_dir, self._pipelines.config.model_download_specs, "distilled_lora")
                    )
                    from ltx_dev_video_pipeline import LTXDevVideoPipeline
                    lora_kw = {}
                    ltx_pipe = LTXDevVideoPipeline(
                        selected_checkpoint_path, gemma_root, upsampler_path, distilled_lora_path,
                        self._pipelines.config.device, loras=loras,
                    )
                    n_inj = 0
                elif is_prequant_fp8_checkpoint:
                    from ltx_fp8_video_pipeline import LTXFp8VideoPipeline
                    lora_kw = _lora_init_kwargs(LTXFp8VideoPipeline, loras)
                    ltx_pipe = LTXFp8VideoPipeline(
                        selected_checkpoint_path, gemma_root, upsampler_path,
                        self._pipelines.config.device, **lora_kw,
                    )
                    n_inj = inject_loras_into_fast_pipeline(ltx_pipe, loras)
                else:
                    lora_kw = _lora_init_kwargs(LTXFastVideoPipeline, loras)
                    ltx_pipe = LTXFastVideoPipeline(
                        selected_checkpoint_path, gemma_root, upsampler_path,
                        self._pipelines.config.device, **lora_kw,
                    )
                    n_inj = inject_loras_into_fast_pipeline(ltx_pipe, loras)
                if hasattr(ltx_pipe, "pipeline") and hasattr(ltx_pipe.pipeline, "model_ledger"):
                    try:
                        ltx_pipe.pipeline.model_ledger.loras = tuple(loras)
                    except Exception:
                        pass
                pipeline_state = VideoPipelineState(
                    pipeline=ltx_pipe, warmth=VideoPipelineWarmth.COLD, is_compiled=False,
                )
                self._pipelines.state.gpu_slot = GpuSlot(active_pipeline=pipeline_state)
                _ml = getattr(getattr(ltx_pipe, "pipeline", None), "model_ledger", None)
                _ml_loras = getattr(_ml, "loras", None) if _ml else None
                print(
                    f"[PATCH] LoRA: __init__ 额外参数={list(lora_kw.keys())}, "
                    f"深度注入点数={n_inj}, model_ledger.loras={_ml_loras}"
                )
                if getattr(self._pipelines, "low_vram_mode", False):
                    from low_vram_runtime import try_sequential_offload_on_pipeline_state
                    try_sequential_offload_on_pipeline_state(pipeline_state)
            else:
                print(f"[PATCH] 加载 Fast pipeline（无 LoRA）")
                pipeline_state = self._pipelines.load_gpu_pipeline("fast", should_warm=False)

            # ── 显存紧张时自动启用 sequential offload ──
            # LTX 支持 layer streaming / sequential CPU offload（分段加载），
            # 即使 22B 模型在 24GB 显卡上也能跑 1080p。
            # 这里确保 offload 已激活——若 low_vram_mode 已由自动检测开启，
            # install_low_vram_pipeline_hooks 已在加载时应用 offload；
            # 此处是兜底：万一 hooks 未生效，强制再 offload 一次。
            if not getattr(self._pipelines, "low_vram_mode", False):
                # 用户未开启低显存模式，检查是否需要自动启用
                from low_vram_runtime import auto_detect_low_vram
                if auto_detect_low_vram():
                    print(f"[PATCH] 自动检测到低显存 GPU，强制启用 CPU offload")
                    from low_vram_runtime import try_sequential_offload_on_pipeline_state
                    try_sequential_offload_on_pipeline_state(pipeline_state, force=True)

            self._pipelines._pipeline_signature = desired_sig
            if not distilled and num_inference_steps is None:
                num_inference_steps = 30
            elif distilled:
                num_inference_steps = None
            print(f"[PATCH] 推理模式: {'蒸馏(8+3步)' if distilled else f'标准({num_inference_steps}步)'}")
            extra_loras_for_hook = tuple(loras) if loras else None

        from lora_build_hook import install_lora_build_hook, pending_loras_token, reset_pending_loras
        install_lora_build_hook()
        _lora_hook_tok = pending_loras_token(extra_loras_for_hook)
        try:
            if not self._generation.is_generation_running():
                self._generation.start_generation(generation_id)
            _total_steps = 8 if distilled else (num_inference_steps or 30)
            self._generation.update_progress("preparing", 1, 0, _total_steps, log_message="初始化...")
            neg_prompt = negative_prompt if negative_prompt else self.config.default_negative_prompt
            enhanced_prompt = prompt + self.config.camera_motion_prompts.get(camera_motion, "")

            dyn_dir = ctx.get_output_path()
            _dur = (num_frames - 1) // fps if fps else 0
            output_path = dyn_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{width}x{height}_{fps}fps_{_dur}s_LTX2.3.mp4"

            try:
                self._generation.update_progress("encoding_text", 3, 0, _total_steps, log_message="编码文本...")
                self._text.prepare_text_encoding(enhanced_prompt, enhance_prompt=False)
                height = max(64, round(height / 64) * 64)
                width = max(64, round(width / 64) * 64)

                self._generation.update_progress("loading_model", 5, 0, _total_steps, log_message="加载模型...")

                _tqdm_pattern = re.compile(r'(\d+)%\|.*?\|\s*(\d+)/(\d+)')
                _tqdm_time_pattern = re.compile(r'\[([0-9:.]+)<')
                _ansi_pattern = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
                _orig_stderr = sys.stderr
                _gen_ref = self._generation
                _phase_weights = [0.65, 0.20, 0.10]
                _phase_labels = ["推理", "解码", "保存"]
                _phase_idx = [0]
                _last_tot = [0]
                _inference_base = 10
                _inference_span = 85
                _tqdm_started = threading.Event()
                _gen_t0 = time.perf_counter()

                _estimator_stop = threading.Event()
                def _estimator_loop():
                    while not _estimator_stop.is_set():
                        _estimator_stop.wait(0.8)
                        if _estimator_stop.is_set():
                            break
                        if _tqdm_started.is_set():
                            break
                        elapsed = time.perf_counter() - _gen_t0
                        ratio = 1 - math.exp(-elapsed / 30.0)
                        pct = int(5 + 5 * ratio)
                        pct = min(pct, 9)
                        phase_label = "loading_model"
                        log_msg = "加载模型"
                        if elapsed > 20:
                            phase_label = "warming_up"
                            log_msg = "预热引擎"
                        try:
                            _gen_ref.update_progress(phase_label, pct, 0, _total_steps, log_message=log_msg)
                        except Exception:
                            pass
                _estimator_thread = threading.Thread(target=_estimator_loop, name="progress-estimator", daemon=True)
                _estimator_thread.start()

                class _TqdmCapture:
                    def __init__(self, original):
                        self._original = original
                    def write(self, s):
                        if s:
                            self._original.write(s)
                            self._original.flush()
                        if not s or not s.strip():
                            return
                        clean = _ansi_pattern.sub('', s).replace('\r', '\n')
                        for line in clean.split('\n'):
                            line = line.strip()
                            if not line:
                                continue
                            m = _tqdm_pattern.search(line)
                            if not m:
                                continue
                            if not _tqdm_started.is_set():
                                _tqdm_started.set()
                                _estimator_stop.set()
                            pct = int(m.group(1))
                            cur = int(m.group(2))
                            tot = int(m.group(3))
                            if tot != _last_tot[0]:
                                if _last_tot[0] > 0:
                                    _phase_idx[0] = min(_phase_idx[0] + 1, len(_phase_weights) - 1)
                                _last_tot[0] = tot
                            pi = _phase_idx[0]
                            base = sum(_phase_weights[:pi])
                            weight = _phase_weights[pi] if pi < len(_phase_weights) else 0.1
                            overall_pct = int(_inference_base + _inference_span * (base + weight * pct / 100.0))
                            overall_pct = min(max(overall_pct, _inference_base), 98)
                            tm = _tqdm_time_pattern.search(line)
                            time_str = tm.group(1) if tm else ''
                            label = _phase_labels[pi] if pi < len(_phase_labels) else "处理"
                            log_msg = f"{label} {cur}/{tot}"
                            if time_str:
                                log_msg += f" {time_str}"
                            try:
                                _gen_ref.update_progress("inference", overall_pct, cur, tot, log_message=log_msg)
                            except Exception:
                                pass
                    def flush(self):
                        self._original.flush()
                    def __getattr__(self, name):
                        return getattr(self._original, name)

                sys.stderr = _TqdmCapture(_orig_stderr)

                if audio_path:
                    gen_kwargs = {
                        "prompt": enhanced_prompt, "negative_prompt": neg_prompt,
                        "seed": seed, "height": height, "width": width,
                        "num_frames": num_frames, "frame_rate": inference_frame_rate,
                        "num_inference_steps": num_inference_steps,
                        "images": images_inputs, "audio_path": audio_path,
                        "audio_start_time": 0.0, "audio_max_duration": None,
                        "output_path": str(output_path),
                    }
                else:
                    gen_kwargs = {
                        "prompt": enhanced_prompt, "seed": seed,
                        "height": height, "width": width,
                        "num_frames": num_frames, "frame_rate": inference_frame_rate,
                        "images": images_inputs, "output_path": str(output_path),
                        "distilled": distilled,
                        "num_inference_steps": num_inference_steps,
                        "negative_prompt": neg_prompt,
                    }
                if motion_speed != 1.0:
                    gen_kwargs["output_fps"] = int(fps)

                try:
                    _cancel_fn = lambda: self._generation.is_generation_cancelled()
                    try:
                        from ltx_core.layer_streaming import LayerStreamingWrapper
                        LayerStreamingWrapper._cancel_check_fn = _cancel_fn
                        print("[PATCH] LayerStreaming cancel check installed")
                    except Exception as _e:
                        print(f"[PATCH] Failed to install LayerStreaming cancel check: {_e}")
                    try:
                        pipeline_state.pipeline.generate(**gen_kwargs)
                    finally:
                        try:
                            from ltx_core.layer_streaming import LayerStreamingWrapper
                            if hasattr(LayerStreamingWrapper, "_cancel_check_fn"):
                                delattr(LayerStreamingWrapper, "_cancel_check_fn")
                        except Exception:
                            pass
                except RuntimeError as e:
                    err_msg = str(e)
                    if "cancel" in err_msg.lower():
                        print(f"[PATCH] 推理已被用户取消")
                        try:
                            import torch
                            if torch.cuda.is_available():
                                torch.cuda.empty_cache()
                        except Exception:
                            pass
                        return None
                    if "out of memory" in err_msg.lower() or "CUDA" in err_msg:
                        print(f"[PATCH] ⚠ CUDA OOM during generation: {e}")
                        try:
                            import torch
                            if torch.cuda.is_available():
                                torch.cuda.empty_cache()
                        except Exception:
                            pass
                        raise RuntimeError(
                            f"显存不足 (CUDA Out of Memory)。建议：\n"
                            f"1. 降低分辨率（如 1080p → 720p）\n"
                            f"2. 减少帧数（如缩短时长）\n"
                            f"3. 在设置中开启「低显存模式」\n"
                            f"原始错误: {e}"
                        ) from e
                    raise
                finally:
                    _estimator_stop.set()
                    sys.stderr = _orig_stderr
                self._generation.update_progress("complete", 100, _total_steps, _total_steps)
                self._generation.complete_generation(str(output_path))
                return str(output_path)
            finally:
                self._text.clear_api_embeddings()
                for p in temp_paths:
                    if os.path.exists(p):
                        os.unlink(p)
                self._start_frame_path = None
                self._end_frame_path = None
                from low_vram_runtime import maybe_release_pipeline_after_task
                try:
                    maybe_release_pipeline_after_task(self)
                except Exception:
                    pass
        finally:
            reset_pending_loras(_lora_hook_tok)

    VideoGenerationHandler.generate = patched_generate
    VideoGenerationHandler.generate_video = patched_generate_video
