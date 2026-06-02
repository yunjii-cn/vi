"""IC-LoRA endpoints orchestration handler."""

from __future__ import annotations

import base64
import logging
import math
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from api_types import (
    ConditioningType,
    IcLoraExtractRequest,
    IcLoraExtractResponse,
    IcLoraGenerateCancelledResponse,
    IcLoraGenerateCompleteResponse,
    IcLoraGenerateRequest,
    IcLoraGenerateResponse,
    ImageConditioningInput,
)
from _routes._errors import HTTPError
from handlers.base import StateHandlerBase
from handlers.generation_handler import GenerationHandler
from handlers.pipelines_handler import PipelinesHandler
from handlers.text_handler import TextHandler
from runtime_config.runtime_config import RuntimeConfig
from state.conditioning_cache import ConditioningCacheEntry, ConditioningCacheKey
from services.interfaces import VideoProcessor
from services.services_utils import FrameArray
from state.app_state_types import AppState, ICLoraState

if TYPE_CHECKING:
    from runtime_config.runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)


class IcLoraHandler(StateHandlerBase):
    def __init__(
        self,
        state: AppState,
        lock: RLock,
        generation_handler: GenerationHandler,
        pipelines_handler: PipelinesHandler,
        text_handler: TextHandler,
        video_processor: VideoProcessor,
        config: RuntimeConfig,
    ) -> None:
        super().__init__(state, lock, config)
        self._generation = generation_handler
        self._pipelines = pipelines_handler
        self._text = text_handler
        self._video_processor = video_processor
        self._progress_estimator_stop: threading.Event | None = None

    def _start_progress_estimator(self, start_pct: int, end_pct: int, total_steps: int, time_constant: float = 50.0) -> None:
        self._stop_progress_estimator()
        stop_event = threading.Event()
        self._progress_estimator_stop = stop_event
        gen = self._generation

        def _loop() -> None:
            t0 = time.perf_counter()
            while not stop_event.is_set():
                stop_event.wait(2.0)
                if stop_event.is_set():
                    break
                elapsed = time.perf_counter() - t0
                ratio = 1 - math.exp(-elapsed / time_constant)
                pct = int(start_pct + (end_pct - start_pct) * ratio)
                step = max(1, int(total_steps * ratio))
                gen.update_progress("inference", pct, step, total_steps)

        t = threading.Thread(target=_loop, name="iclora-progress-estimator", daemon=True)
        t.start()

    def _stop_progress_estimator(self) -> None:
        if self._progress_estimator_stop is not None:
            self._progress_estimator_stop.set()
            self._progress_estimator_stop = None

    def _build_conditioning_frame(
        self,
        frame: FrameArray,
        conditioning_type: ConditioningType,
        ic_state: ICLoraState | None = None,
    ) -> FrameArray:
        match conditioning_type:
            case "canny":
                return self._video_processor.apply_canny(frame)
            case "depth":
                if ic_state is None or ic_state.depth_pipeline is None:
                    raise HTTPError(500, "Depth conditioning requires loaded IC-LoRA resources")
                return self._video_processor.apply_depth(frame, ic_state.depth_pipeline)
            case "pose":
                if ic_state is None or ic_state.pose_resources is None:
                    raise HTTPError(500, "Pose conditioning requires loaded IC-LoRA resources with pose pipeline")
                return self._video_processor.apply_pose(frame, ic_state.pose_resources.pipeline)
            case _:
                raise HTTPError(400, f"Unsupported conditioning_type: {conditioning_type}")

    def _require_ic_lora_model_paths(
        self, conditioning_type: ConditioningType
    ) -> tuple[Path, Path | None, Path | None, Path | None]:
        lora_path = self.resolve_model("ic_lora")
        if not lora_path.exists():
            raise HTTPError(400, f"IC-LoRA model not found: {lora_path}")

        depth_model_path: Path | None = None
        person_detector_path: Path | None = None
        pose_model_path: Path | None = None

        if conditioning_type == "depth":
            depth_model_path = self.resolve_model("depth_processor")
            if not depth_model_path.exists():
                raise HTTPError(400, f"Depth processor model not found: {depth_model_path}")

        if conditioning_type == "pose":
            person_detector_path = self.resolve_model("person_detector")
            pose_model_path = self.resolve_model("pose_processor")
            if not person_detector_path.exists():
                raise HTTPError(400, f"Person detector model not found: {person_detector_path}")
            if not pose_model_path.exists():
                raise HTTPError(400, f"Pose processor model not found: {pose_model_path}")

        return lora_path, depth_model_path, person_detector_path, pose_model_path

    def extract_conditioning(self, req: IcLoraExtractRequest) -> IcLoraExtractResponse:
        video_file = Path(req.video_path)
        if not video_file.exists():
            raise HTTPError(400, f"Video not found: {req.video_path}")

        cap = self._video_processor.open_video(str(video_file))
        info = self._video_processor.get_video_info(cap)
        target_frame = int(req.frame_time * float(info["fps"]))
        frame = self._video_processor.read_frame(cap, frame_idx=target_frame)
        self._video_processor.release(cap)

        if frame is None:
            raise HTTPError(400, "Could not read frame from video")

        ic_state: ICLoraState | None = None
        if req.conditioning_type in ("depth", "pose"):
            lora_path, depth_model_path, person_detector_path, pose_model_path = (
                self._require_ic_lora_model_paths(req.conditioning_type)
            )
            ic_state = self._pipelines.load_ic_lora(
                str(lora_path),
                str(depth_model_path) if depth_model_path else None,
                str(person_detector_path) if person_detector_path else None,
                str(pose_model_path) if pose_model_path else None,
            )

        result = self._build_conditioning_frame(frame, req.conditioning_type, ic_state)

        conditioning = self._video_processor.encode_frame_jpeg(result, quality=85)
        original = self._video_processor.encode_frame_jpeg(frame, quality=85)

        return IcLoraExtractResponse(
            conditioning="data:image/jpeg;base64," + base64.b64encode(conditioning).decode("utf-8"),
            original="data:image/jpeg;base64," + base64.b64encode(original).decode("utf-8"),
            conditioning_type=req.conditioning_type,
            frame_time=req.frame_time,
        )

    def _resolve_seed(self) -> int:
        settings = self.state.app_settings
        if settings.seed_locked:
            return settings.locked_seed
        if self.config.dev_mode:
            return 1000
        return int(time.time()) % 2147483647

    def generate(self, req: IcLoraGenerateRequest) -> IcLoraGenerateResponse:
        if self._generation.is_generation_running():
            raise HTTPError(409, "Generation already in progress")

        video_path = Path(req.video_path)
        if not video_path.exists():
            raise HTTPError(400, f"Video not found: {req.video_path}")
        lora_path, depth_model_path, person_detector_path, pose_model_path = (
            self._require_ic_lora_model_paths(req.conditioning_type)
        )

        checkpoint_path: str | None = None
        req_model_path = getattr(req, "modelPath", None)
        if req_model_path and str(req_model_path).strip():
            from pathlib import Path as _P
            mp = _P(str(req_model_path).strip()).expanduser()
            try:
                mp = mp.resolve()
            except OSError:
                pass
            if mp.is_file():
                checkpoint_path = str(mp)

        generation_id = uuid.uuid4().hex[:8]
        t_total_start = time.perf_counter()
        print(f"[ic-lora] Generation started (conditioning={req.conditioning_type}, video={video_path})", flush=True)
        logger.info("[ic-lora] Generation started (conditioning=%s)", req.conditioning_type)

        try:
            t_load_start = time.perf_counter()
            ic_state = self._pipelines.load_ic_lora(
                str(lora_path),
                str(depth_model_path) if depth_model_path else None,
                str(person_detector_path) if person_detector_path else None,
                str(pose_model_path) if pose_model_path else None,
                checkpoint_path=checkpoint_path,
            )
            t_load_end = time.perf_counter()
            print(f"[ic-lora] Pipeline load: {t_load_end - t_load_start:.2f}s", flush=True)
            logger.info("[ic-lora] Pipeline load: %.2fs", t_load_end - t_load_start)

            self._generation.start_generation(generation_id)
            self._generation.update_progress("loading_model", 5, 0, 1)

            if self._generation.is_generation_cancelled():
                raise RuntimeError("Generation was cancelled")

            s = self.state.app_settings
            use_api = not self._text.should_use_local_encoding()
            encoding_method = "api" if use_api else "local"
            t_text_start = time.perf_counter()
            self._text.prepare_text_encoding(req.prompt, enhance_prompt=use_api and s.prompt_enhancer_enabled_t2v)
            t_text_end = time.perf_counter()
            logger.info("[ic-lora] Text encoding (%s): %.2fs", encoding_method, t_text_end - t_text_start)

            cap = self._video_processor.open_video(str(video_path))
            if not cap.isOpened():
                raise HTTPError(400, f"Cannot open video: {video_path}")
            info = self._video_processor.get_video_info(cap)
            input_width = int(info["width"])
            input_height = int(info["height"])
            print(f"[ic-lora] Video info: {input_width}x{input_height}, frames={info.get('frame_count')}, fps={info.get('fps')}", flush=True)

            input_frame_count = int(info["frame_count"])
            input_fps = float(info["fps"])

            user_fps = getattr(req, "fps", None)
            user_duration = getattr(req, "duration", None)
            target_fps = float(user_fps) if user_fps else min(input_fps, 24.0)
            target_duration = float(user_duration) if user_duration else min(input_frame_count / input_fps, 6.0)
            target_frame_count = int(round(target_fps * target_duration))
            target_frame_count = max(target_frame_count, 9)
            target_frame_count = min(target_frame_count, 161)
            target_frame_count = (target_frame_count // 8 + 1) * 8 - 1

            logger.info(
                "[ic-lora] Frame plan: input=%dframes@%.1ffps, target=%dframes@%.1ffps (%.1fs) user_fps=%s user_duration=%s",
                input_frame_count, input_fps, target_frame_count, target_fps, target_duration, user_fps, user_duration,
            )

            frame_count = target_frame_count
            fps = target_fps

            motion_speed = float(getattr(req, "motionSpeed", 1.0) or 1.0)
            if motion_speed < 0.25:
                motion_speed = 0.25
            if motion_speed > 3.0:
                motion_speed = 3.0
            inference_fps = max(1.0, fps / motion_speed)
            logger.info(
                "[ic-lora] Motion speed: %.2fx, inference_fps=%.1f, output_fps=%.1f",
                motion_speed, inference_fps, fps,
            )

            quality = getattr(req, "quality", None) or "720"
            short_side_map = {"1080": 1024, "720": 768, "540": 512, "480": 384, "360": 256}
            short_side = short_side_map.get(quality, 768)

            if input_width >= input_height:
                width = short_side
                height = round(width * input_height / input_width / 128) * 128
            else:
                height = short_side
                width = round(height * input_width / input_height / 128) * 128
            height = max(height, 128)
            width = max(width, 128)

            if height % 128 != 0:
                height = (height // 128) * 128
            if width % 128 != 0:
                width = (width // 128) * 128
            height = max(height, 128)
            width = max(width, 128)

            logger.info(
                "[ic-lora] Output resolution: %dx%d (quality=%s, input=%dx%d, stage1=%dx%d, latent=%dx%d)",
                width, height, quality, input_width, input_height,
                width // 2, height // 2,
                width // 64, height // 64,
            )

            try:
                import torch as _torch_check
                if _torch_check.cuda.is_available():
                    _vram_gb = _torch_check.cuda.get_device_properties(0).total_mem / (1024**3)
                    _cond_mem_gb = frame_count * width * height * 3 * 4 / (1024**3)
                    _estimated_gb = _cond_mem_gb * 4 + 8
                    if _estimated_gb > _vram_gb * 0.85:
                        logger.warning(
                            "[ic-lora] VRAM guard: estimated %.1fGB > %.1fGB*0.85 (%.0fGB), reducing frame_count from %d",
                            _estimated_gb, _vram_gb, _vram_gb * 0.85, frame_count,
                        )
                        _max_frames = max(9, int((_vram_gb * 0.85 - 8) / (width * height * 3 * 4 / (1024**3)) / 4))
                        _max_frames = min(_max_frames, 161)
                        _max_frames = (_max_frames // 8 + 1) * 8 - 1
                        if _max_frames < frame_count:
                            frame_count = _max_frames
                            logger.info("[ic-lora] VRAM guard: reduced frame_count to %d", frame_count)
            except Exception as _vg_err:
                logger.warning("[ic-lora] VRAM guard check failed: %s", _vg_err)

            cache_key = ConditioningCacheKey(str(video_path), req.conditioning_type)
            cached = ic_state.conditioning_cache.get(cache_key)

            t_preprocess_start = 0.0
            t_preprocess_end = 0.0
            use_cache = False

            if cached is not None:
                cached_file = Path(cached.control_video_path)
                cache_valid = cached_file.exists() and cached_file.stat().st_size > 1024
                if cache_valid and req.conditioning_type == "pose":
                    try:
                        import cv2 as _cv2_cache
                        import numpy as _np_cache
                        _cap_check = _cv2.VideoCapture(str(cached_file))
                        _ret, _frame_check = _cap_check.read()
                        _cap_check.release()
                        if _ret and _frame_check is not None and _np_cache.mean(_frame_check) < 30:
                            print(f"[ic-lora] Cached pose video has very dark frames (mean={_np_cache.mean(_frame_check):.1f}), invalidating", flush=True)
                            cache_valid = False
                            try:
                                cached_file.unlink()
                            except Exception:
                                pass
                    except Exception as _e:
                        print(f"[ic-lora] Cache validation check failed: {_e}", flush=True)
                if cache_valid and cached.frame_count == frame_count and abs(cached.fps - fps) < 0.5:
                    control_video_path = cached.control_video_path
                    use_cache = True
                    self._video_processor.release(cap)
                    print(f"[ic-lora] Conditioning cache hit for {video_path.name}/{req.conditioning_type} (cached_frames={cached.frame_count}, target_frames={frame_count})", flush=True)
                    logger.info("[ic-lora] Conditioning cache hit for %s/%s", video_path.name, req.conditioning_type)
                elif cache_valid:
                    print(f"[ic-lora] Cache frame count mismatch (cached={cached.frame_count}, target={frame_count}) or fps mismatch (cached={cached.fps:.1f}, target={fps:.1f}), reprocessing", flush=True)
                else:
                    print(f"[ic-lora] Conditioning cache invalid (file missing or too small), reprocessing", flush=True)

            if not use_cache:
                if self._generation.is_generation_cancelled():
                    raise RuntimeError("Generation was cancelled")

                t_preprocess_start = time.perf_counter()

                control_video_path = str(
                    self.config.outputs_dir / f"_control_{req.conditioning_type}_{uuid.uuid4().hex[:8]}.mp4"
                )

                import av as _av
                import numpy as _np_av
                from fractions import Fraction as _Fraction
                _container = _av.open(control_video_path, mode="w")
                _stream = _container.add_stream("libx264", rate=_Fraction(int(round(fps)), 1))
                _stream.width = width
                _stream.height = height
                _stream.pix_fmt = "yuv420p"
                _stream.options = {"crf": "18", "preset": "ultrafast"}

                frame_idx = 0
                black_frame_count = 0
                _diag_original_path: str | None = None
                _diag_conditioning_path: str | None = None
                read_idx = 0
                if input_frame_count > target_frame_count:
                    sample_step = input_frame_count / target_frame_count
                else:
                    sample_step = 1.0
                next_sample = 0.0
                while frame_idx < target_frame_count:
                    if input_frame_count > target_frame_count:
                        target_read = int(next_sample)
                        while read_idx < target_read:
                            skip_frame = self._video_processor.read_frame(cap)
                            if skip_frame is None:
                                break
                            read_idx += 1
                        next_sample += sample_step
                    frame = self._video_processor.read_frame(cap)
                    if frame is None:
                        print(f"[ic-lora] WARNING: read_frame returned None at read_idx={read_idx}, output frame {frame_idx}/{target_frame_count}", flush=True)
                        break
                    read_idx += 1
                    if frame_idx == 0:
                        import numpy as _np0
                        print(f"[ic-lora] First frame: shape={frame.shape}, dtype={frame.dtype}, min={_np0.min(frame)}, max={_np0.max(frame)}, mean={_np0.mean(frame):.1f}", flush=True)
                    control_frame = self._build_conditioning_frame(frame, req.conditioning_type, ic_state)
                    import numpy as _np
                    if _np.max(control_frame) == 0:
                        black_frame_count += 1
                    if frame_idx == 0:
                        import numpy as _np1
                        print(f"[ic-lora] First conditioning frame: shape={control_frame.shape}, dtype={control_frame.dtype}, min={_np1.min(control_frame)}, max={_np1.max(control_frame)}, mean={_np1.mean(control_frame):.1f}", flush=True)
                        try:
                            import cv2 as _cv2
                            _diag_original_path = str(self.config.outputs_dir / f"_diag_original_frame0_{uuid.uuid4().hex[:6]}.png")
                            _diag_conditioning_path = str(self.config.outputs_dir / f"_diag_{req.conditioning_type}_frame0_{uuid.uuid4().hex[:6]}.png")
                            _cv2.imwrite(_diag_original_path, frame)
                            _cv2.imwrite(_diag_conditioning_path, control_frame)
                            print(f"[ic-lora] Diagnostic frames saved: original={_diag_original_path}, conditioning={_diag_conditioning_path}", flush=True)
                        except Exception as _e:
                            print(f"[ic-lora] Failed to save diagnostic frames: {_e}", flush=True)
                    rgb_frame = _np_av.ascontiguousarray(control_frame[:, :, ::-1])
                    if control_frame.shape[1] != width or control_frame.shape[0] != height:
                        import cv2 as _cv2_resize
                        rgb_frame = _cv2_resize.resize(rgb_frame, (width, height), interpolation=_cv2_resize.INTER_AREA)
                    _av_frame = _av.VideoFrame.from_ndarray(rgb_frame, format="rgb24")
                    for _packet in _stream.encode(_av_frame):
                        _container.mux(_packet)
                    frame_idx += 1

                for _packet in _stream.encode():
                    _container.mux(_packet)
                _container.close()

                self._video_processor.release(cap)
                t_preprocess_end = time.perf_counter()
                print(f"[ic-lora] Preprocessing ({req.conditioning_type}): {frame_idx} frames, {black_frame_count} all-black, {t_preprocess_end - t_preprocess_start:.2f}s", flush=True)
                if black_frame_count > 0:
                    logger.warning(
                        "[ic-lora] Preprocessing (%s): %d/%d frames are all-black (pose detection may have failed)",
                        req.conditioning_type, black_frame_count, frame_idx,
                    )
                if black_frame_count > 0 and frame_idx > 0 and black_frame_count / frame_idx > 0.8:
                    msg = (
                        f"动作检测失败：{black_frame_count}/{frame_idx} 帧无法检测到人物姿态。"
                        "可能原因：1)视频中人物太小或太远 2)视频太暗 3)姿态模型未正确加载。"
                        "请尝试使用更清晰的人物视频，或切换为其他迁移模式。"
                    )
                    print(f"[ic-lora] ERROR: {msg}", flush=True)
                    raise HTTPError(400, msg)
                logger.info(
                    "[ic-lora] Preprocessing (%s, %d frames): %.2fs",
                    req.conditioning_type, frame_idx, t_preprocess_end - t_preprocess_start,
                )

                if black_frame_count == 0 or (frame_idx > 0 and black_frame_count / frame_idx <= 0.5):
                    ic_state.conditioning_cache.put(
                        cache_key, ConditioningCacheEntry(control_video_path, frame_count, fps)
                    )
                else:
                    print(f"[ic-lora] Not caching conditioning video ({black_frame_count}/{frame_idx} black frames)", flush=True)

            images: list[ImageConditioningInput] = [
                ImageConditioningInput(path=img.path, frame_idx=int(img.frame), strength=float(img.strength))
                for img in req.images
            ]

            if self._generation.is_generation_cancelled():
                raise RuntimeError("Generation was cancelled")

            self._generation.update_progress("encoding_text", 10, 0, 1)
            self._generation.update_progress("inference", 15, 0, 1)

            output_path = (
                self.config.outputs_dir / f"ic_lora_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.mp4"
            )

            lora_paths = getattr(req, "loraPaths", None)
            lora_strengths = getattr(req, "loraStrengths", None)
            original_stage1_builder = None

            if lora_paths:
                try:
                    from ltx_core.loader.primitives import LoraPathStrengthAndSDOps as _LoraPathStrengthAndSDOps
                    stage1 = ic_state.pipeline.stage_1
                    original_stage1_builder = stage1._transformer_builder
                    existing_loras = list(original_stage1_builder.loras)
                    user_loras = []
                    for _li, _lp in enumerate(lora_paths):
                        if not _lp or not Path(_lp).exists():
                            print(f"[ic-lora] WARNING: LoRA path not found, skipping: {_lp}", flush=True)
                            continue
                        _ls = lora_strengths[_li] if lora_strengths and _li < len(lora_strengths) else 1.0
                        user_loras.append(_LoraPathStrengthAndSDOps(_lp, _ls, None))
                    if user_loras:
                        combined_loras = tuple(existing_loras) + tuple(user_loras)
                        stage1._transformer_builder = original_stage1_builder.with_loras(combined_loras)
                        print(f"[ic-lora] Added {len(user_loras)} user LoRA(s) to stage_1 (total: {len(combined_loras)})", flush=True)
                        logger.info("[ic-lora] Added %d user LoRA(s) to stage_1 (total: %d)", len(user_loras), len(combined_loras))
                except Exception as _lora_err:
                    print(f"[ic-lora] WARNING: Failed to apply user LoRAs: {_lora_err}", flush=True)
                    logger.warning("[ic-lora] Failed to apply user LoRAs: %s", _lora_err)
                    original_stage1_builder = None

            user_seed = getattr(req, "seed", None)
            resolved_seed = int(user_seed) if user_seed is not None else self._resolve_seed()

            try:
                import faulthandler
                faulthandler.enable()
            except Exception:
                pass

            import os as _os
            _prev_cuda_launch_blocking = _os.environ.get("CUDA_LAUNCH_BLOCKING", "")
            _os.environ["CUDA_LAUNCH_BLOCKING"] = "1"

            t_inference_start = time.perf_counter()
            self._start_progress_estimator(15, 95, 1, time_constant=80.0)
            self._generation.set_denoising_cancel_active()
            try:
                import torch as _torch
                if _torch.cuda.is_available():
                    _torch.cuda.empty_cache()
                ic_state.pipeline.generate(
                    prompt=req.prompt,
                    seed=resolved_seed,
                    height=height,
                    width=width,
                    num_frames=frame_count,
                    frame_rate=inference_fps,
                    images=images,
                    video_conditioning=[(control_video_path, req.conditioning_strength)],
                    output_path=str(output_path),
                    conditioning_attention_strength=getattr(req, "attention_strength", 1.0),
                    output_fps=int(fps) if motion_speed != 1.0 else None,
                )
            finally:
                self._stop_progress_estimator()
                self._generation.clear_denoising_cancel_active()
                if _prev_cuda_launch_blocking:
                    _os.environ["CUDA_LAUNCH_BLOCKING"] = _prev_cuda_launch_blocking
                elif "CUDA_LAUNCH_BLOCKING" in _os.environ:
                    del _os.environ["CUDA_LAUNCH_BLOCKING"]

            if original_stage1_builder is not None:
                try:
                    ic_state.pipeline.stage_1._transformer_builder = original_stage1_builder
                except Exception:
                    pass
            t_inference_end = time.perf_counter()
            logger.info("[ic-lora] Inference: %.2fs", t_inference_end - t_inference_start)

            t_total_end = time.perf_counter()
            preprocess_time = (t_preprocess_end - t_preprocess_start) if not use_cache else 0.0
            logger.info(
                "[ic-lora] Total generation: %.2fs (load=%.2fs, text=%.2fs, preprocess=%.2fs, inference=%.2fs)",
                t_total_end - t_total_start,
                t_load_end - t_load_start,
                t_text_end - t_text_start,
                preprocess_time,
                t_inference_end - t_inference_start,
            )

            self._generation.update_progress("complete", 100, 1, 1)
            self._generation.complete_generation(str(output_path))
            return IcLoraGenerateCompleteResponse(status="complete", video_path=str(output_path))

        except HTTPError:
            self._generation.fail_generation("IC-LoRA generation failed")
            raise
        except Exception as exc:
            self._generation.fail_generation(str(exc))
            if "cancelled" in str(exc).lower():
                return IcLoraGenerateCancelledResponse(status="cancelled")
            raise HTTPError(500, f"Generation error: {exc}") from exc
        finally:
            self._text.clear_api_embeddings()
            try:
                if control_video_path and Path(control_video_path).exists():
                    Path(control_video_path).unlink()
            except OSError:
                pass
            try:
                if _diag_original_path and Path(_diag_original_path).exists():
                    Path(_diag_original_path).unlink()
            except OSError:
                pass
            try:
                if _diag_conditioning_path and Path(_diag_conditioning_path).exists():
                    Path(_diag_conditioning_path).unlink()
            except OSError:
                pass
