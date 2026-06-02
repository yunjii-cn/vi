"""Image generation orchestration handler."""

from __future__ import annotations

import logging
import math
import os
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from _routes._errors import HTTPError
from api_types import (
    GenerateImageCancelledResponse,
    GenerateImageCompleteResponse,
    GenerateImageRequest,
    GenerateImageResponse,
)
from handlers.base import StateHandlerBase
from handlers.generation_handler import GenerationHandler
from handlers.pipelines_handler import PipelinesHandler
from services.interfaces import ZitAPIClient
from state.app_state_types import AppState

if TYPE_CHECKING:
    from runtime_config.runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)


class ImageGenerationHandler(StateHandlerBase):
    def __init__(
        self,
        state: AppState,
        lock: RLock,
        generation_handler: GenerationHandler,
        pipelines_handler: PipelinesHandler,
        config: RuntimeConfig,
        zit_api_client: ZitAPIClient,
    ) -> None:
        super().__init__(state, lock, config)
        self._generation = generation_handler
        self._pipelines = pipelines_handler
        self._zit_api_client = zit_api_client
        self._progress_estimator_stop: threading.Event | None = None

    def _start_progress_estimator(self, start_pct: int, end_pct: int, total_steps: int, time_constant: float = 30.0) -> None:
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

        t = threading.Thread(target=_loop, name="img-progress-estimator", daemon=True)
        t.start()

    def _stop_progress_estimator(self) -> None:
        if self._progress_estimator_stop is not None:
            self._progress_estimator_stop.set()
            self._progress_estimator_stop = None

    def generate(self, req: GenerateImageRequest) -> GenerateImageResponse:
        if self._generation.is_generation_running():
            raise HTTPError(409, "Generation already in progress")

        width = (req.width // 16) * 16
        height = (req.height // 16) * 16
        num_images = max(1, min(12, req.numImages))

        model_path: str | None = None
        req_mp = getattr(req, "modelPath", None)
        if req_mp and str(req_mp).strip():
            from pathlib import Path as _P
            mp = _P(str(req_mp).strip()).expanduser()
            try:
                mp = mp.resolve()
            except OSError:
                pass
            if mp.is_file() or mp.is_dir():
                model_path = str(mp)

        lora_paths: list[str] = getattr(req, "loraPaths", None) or []
        lora_strengths: list[float] = getattr(req, "loraStrengths", None) or []

        generation_id = uuid.uuid4().hex[:8]
        settings = self.state.app_settings.model_copy(deep=True)
        if settings.seed_locked:
            seed = settings.locked_seed
            logger.info("Using locked seed for image: %s", seed)
        elif self.config.dev_mode:
            seed = 1000
        else:
            seed = int(time.time()) % 2147483647

        if self.config.force_api_generations:
            return self._generate_via_api(
                prompt=req.prompt,
                width=width,
                height=height,
                num_inference_steps=req.numSteps,
                seed=seed,
                num_images=num_images,
            )

        try:
            self._pipelines.load_image_generation_pipeline_to_gpu(checkpoint_path=model_path)
            self._generation.start_generation(generation_id)
            output_paths = self.generate_image(
                prompt=req.prompt,
                width=width,
                height=height,
                num_inference_steps=req.numSteps,
                seed=seed,
                num_images=num_images,
                lora_paths=lora_paths,
                lora_strengths=lora_strengths,
            )
            self._generation.complete_generation(output_paths)
            return GenerateImageCompleteResponse(status="complete", image_paths=output_paths)
        except Exception as e:
            self._generation.fail_generation(str(e))
            if "cancelled" in str(e).lower():
                logger.info("Image generation cancelled by user")
                return GenerateImageCancelledResponse(status="cancelled")
            raise HTTPError(500, str(e)) from e

    def generate_image(
        self,
        prompt: str,
        width: int,
        height: int,
        num_inference_steps: int,
        seed: int | None,
        num_images: int,
        lora_paths: list[str] | None = None,
        lora_strengths: list[float] | None = None,
    ) -> list[str]:
        if self._generation.is_generation_cancelled():
            raise RuntimeError("Generation was cancelled")

        self._generation.update_progress("loading_model", 5, 0, num_inference_steps)
        image_generation_pipeline = self._pipelines.load_image_generation_pipeline_to_gpu()
        self._generation.update_progress("encoding_text", 10, 0, num_inference_steps)
        self._generation.update_progress("inference", 15, 0, num_inference_steps)

        if seed is None:
            seed = int(time.time()) % 2147483647

        loaded_loras: list[str] = []
        if lora_paths:
            inner_pipe = getattr(image_generation_pipeline, "pipeline", image_generation_pipeline)
            for idx, lp in enumerate(lora_paths):
                try:
                    strength = lora_strengths[idx] if lora_strengths and idx < len(lora_strengths) else 1.0
                    inner_pipe.load_lora_weights(lp, adapter_name=f"yj_lora_{idx}")
                    loaded_loras.append(f"yj_lora_{idx}")
                    logger.info("[img] Loaded LoRA %s (strength=%.2f)", lp, strength)
                except Exception as e:
                    logger.warning("[img] Failed to load LoRA %s: %s", lp, e)
            if loaded_loras:
                try:
                    strengths = [lora_strengths[i] if lora_strengths and i < len(lora_strengths) else 1.0 for i in range(len(loaded_loras))]
                    inner_pipe.set_adapters(loaded_loras, adapter_weights=strengths)
                except Exception as e:
                    logger.warning("[img] Failed to set LoRA adapters: %s", e)

        outputs: list[str] = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.config.outputs_dir.mkdir(parents=True, exist_ok=True)

        self._start_progress_estimator(15, 90, num_inference_steps)
        try:
            for i in range(num_images):
                if self._generation.is_generation_cancelled():
                    raise RuntimeError("Generation was cancelled")

                result = image_generation_pipeline.generate(
                    prompt=prompt,
                    height=height,
                    width=width,
                    guidance_scale=0.0,
                    num_inference_steps=num_inference_steps,
                    seed=seed + i,
                )

                output_path = self.config.outputs_dir / f"{timestamp}_{width}x{height}_{uuid.uuid4().hex[:8]}.png"
                save_path = str(output_path)
                if sys.platform == "win32":
                    try:
                        from ctypes import windll, create_unicode_buffer
                        buf = create_unicode_buffer(512)
                        if windll.kernel32.GetShortPathNameW(save_path, buf, 512):
                            save_path = buf.value
                    except Exception:
                        pass
                try:
                    result.images[0].save(save_path)
                except OSError:
                    logger.error("Failed to save image to %s", save_path)
                    import tempfile
                    fallback_dir = tempfile.gettempdir()
                    fallback_name = f"img_{uuid.uuid4().hex[:8]}.png"
                    fallback_path = os.path.join(fallback_dir, fallback_name)
                    result.images[0].save(fallback_path)
                    import shutil
                    final_path = str(output_path)
                    try:
                        shutil.copy2(fallback_path, final_path)
                    except Exception:
                        output_path = Path(fallback_path)
                    else:
                        os.unlink(fallback_path)
                outputs.append(str(output_path))
        finally:
            self._stop_progress_estimator()
            if loaded_loras:
                try:
                    inner_pipe = getattr(image_generation_pipeline, "pipeline", image_generation_pipeline)
                    for adapter_name in loaded_loras:
                        try:
                            inner_pipe.delete_adapters(adapter_name)
                        except Exception:
                            pass
                except Exception:
                    pass

        if self._generation.is_generation_cancelled():
            raise RuntimeError("Generation was cancelled")

        self._generation.update_progress("complete", 100, num_inference_steps, num_inference_steps)
        return outputs

    def _generate_via_api(
        self,
        *,
        prompt: str,
        width: int,
        height: int,
        num_inference_steps: int,
        seed: int,
        num_images: int,
    ) -> GenerateImageResponse:
        generation_id = uuid.uuid4().hex[:8]
        output_paths: list[Path] = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        settings = self.state.app_settings.model_copy(deep=True)
        self.config.outputs_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._generation.start_api_generation(generation_id)
            self._generation.update_progress("validating_request", 5, None, None)

            if not settings.fal_api_key.strip():
                raise HTTPError(500, "FAL_API_KEY_NOT_CONFIGURED")

            for idx in range(num_images):
                if self._generation.is_generation_cancelled():
                    raise RuntimeError("Generation was cancelled")

                inference_progress = 15 + int((idx / num_images) * 60)
                self._generation.update_progress("inference", inference_progress, None, None)
                image_bytes = self._zit_api_client.generate_text_to_image(
                    api_key=settings.fal_api_key,
                    prompt=prompt,
                    width=width,
                    height=height,
                    seed=seed + idx,
                    num_inference_steps=num_inference_steps,
                )

                if self._generation.is_generation_cancelled():
                    raise RuntimeError("Generation was cancelled")

                download_progress = 75 + int(((idx + 1) / num_images) * 20)
                self._generation.update_progress("downloading_output", download_progress, None, None)

                output_path = self.config.outputs_dir / f"{timestamp}_{width}x{height}_{uuid.uuid4().hex[:8]}.png"
                write_path = str(output_path)
                if sys.platform == "win32":
                    try:
                        from ctypes import windll, create_unicode_buffer
                        buf = create_unicode_buffer(512)
                        if windll.kernel32.GetShortPathNameW(write_path, buf, 512):
                            write_path = buf.value
                    except Exception:
                        pass
                Path(write_path).write_bytes(image_bytes)
                output_paths.append(output_path)

            self._generation.update_progress("complete", 100, None, None)
            self._generation.complete_generation([str(path) for path in output_paths])
            return GenerateImageCompleteResponse(status="complete", image_paths=[str(path) for path in output_paths])
        except HTTPError as e:
            self._generation.fail_generation(e.detail)
            raise
        except Exception as e:
            self._generation.fail_generation(str(e))
            if "cancelled" in str(e).lower():
                for path in output_paths:
                    path.unlink(missing_ok=True)
                logger.info("Image generation cancelled by user")
                return GenerateImageCancelledResponse(status="cancelled")
            raise HTTPError(500, str(e)) from e
