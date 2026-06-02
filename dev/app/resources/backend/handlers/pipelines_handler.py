"""Pipeline lifecycle and warmup handler."""

from __future__ import annotations

import logging
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from handlers.base import StateHandlerBase
from handlers.text_handler import TextHandler
from services.interfaces import (
    A2VPipeline,
    DepthProcessorPipeline,
    FastVideoPipeline,
    ImageGenerationPipeline,
    GpuCleaner,
    IcLoraPipeline,
    PoseProcessorPipeline,
    RetakePipeline,
    VideoPipelineModelType,
)
from services.services_utils import device_supports_fp8, get_device_type
from state.app_state_types import (
    A2VPipelineState,
    AppState,
    CpuSlot,
    GpuGeneration,
    GenerationRunning,
    GpuSlot,
    ICLoraState,
    PoseResources,
    RetakePipelineState,
    VideoPipelineState,
    VideoPipelineWarmth,
)

if TYPE_CHECKING:
    from runtime_config.runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)


class PipelinesHandler(StateHandlerBase):
    def __init__(
        self,
        state: AppState,
        lock: RLock,
        text_handler: TextHandler,
        gpu_cleaner: GpuCleaner,
        fast_video_pipeline_class: type[FastVideoPipeline],
        image_generation_pipeline_class: type[ImageGenerationPipeline],
        ic_lora_pipeline_class: type[IcLoraPipeline],
        depth_processor_pipeline_class: type[DepthProcessorPipeline],
        pose_processor_pipeline_class: type[PoseProcessorPipeline],
        a2v_pipeline_class: type[A2VPipeline],
        retake_pipeline_class: type[RetakePipeline],
        config: RuntimeConfig,
    ) -> None:
        super().__init__(state, lock, config)
        self._text_handler = text_handler
        self._gpu_cleaner = gpu_cleaner
        self._fast_video_pipeline_class = fast_video_pipeline_class
        self._image_generation_pipeline_class = image_generation_pipeline_class
        self._ic_lora_pipeline_class = ic_lora_pipeline_class
        self._depth_processor_pipeline_class = depth_processor_pipeline_class
        self._pose_processor_pipeline_class = pose_processor_pipeline_class
        self._a2v_pipeline_class = a2v_pipeline_class
        self._retake_pipeline_class = retake_pipeline_class
        self._runtime_device = get_device_type(self.config.device)

    def _ensure_no_running_generation(self) -> None:
        match self.state.active_generation:
            case GpuGeneration(state=GenerationRunning()) if self.state.gpu_slot is not None:
                raise RuntimeError("Generation already running; cannot swap pipelines")
            case _:
                return

    def _resolve_available_checkpoint(self) -> str:
        bf16 = self.resolve_model("checkpoint")
        if bf16.is_file():
            return str(bf16)
        for d in self.models_dirs:
            for fn in ("ltx-2.3-22b-distilled-1.1.safetensors", "ltx-2.3-22b-distilled.safetensors"):
                candidate = d / fn
                if candidate.is_file():
                    return str(candidate)
        fp8 = self.resolve_model("checkpoint_fp8")
        if fp8.is_file():
            return str(fp8)
        for d in self.models_dirs:
            for fn in ("ltx-2.3-22b-distilled-fp8.safetensors",):
                candidate = d / fn
                if candidate.is_file():
                    return str(candidate)
        return str(self.resolve_model("checkpoint"))

    def _resolve_ic_lora_checkpoint(self) -> str:
        bf16 = self.resolve_model("checkpoint")
        if bf16.is_file():
            return str(bf16)
        for d in self.models_dirs:
            for fn in ("ltx-2.3-22b-distilled-1.1.safetensors", "ltx-2.3-22b-distilled.safetensors"):
                candidate = d / fn
                if candidate.is_file():
                    return str(candidate)
        fp8 = self.resolve_model("checkpoint_fp8")
        if fp8.is_file():
            return str(fp8)
        for d in self.models_dirs:
            for fn in ("ltx-2.3-22b-distilled-fp8.safetensors",):
                candidate = d / fn
                if candidate.is_file():
                    return str(candidate)
        return str(self.resolve_model("checkpoint"))

    def _pipeline_matches_model_type(self, model_type: VideoPipelineModelType) -> bool:
        match self.state.gpu_slot:
            case GpuSlot(active_pipeline=VideoPipelineState(pipeline=pipeline)):
                return pipeline.pipeline_kind == model_type
            case _:
                return False

    def _assert_invariants(self) -> None:
        match self.state.gpu_slot:
            case GpuSlot(active_pipeline=active_pipeline):
                gpu_has_image_generation_pipeline = isinstance(active_pipeline, ImageGenerationPipeline)
            case _:
                gpu_has_image_generation_pipeline = False

        if gpu_has_image_generation_pipeline and self.state.cpu_slot is not None:
            raise RuntimeError("Invariant violation: image generation pipeline cannot be in both GPU and CPU slots")

    def _install_text_patches_if_needed(self) -> None:
        te = self.state.text_encoder
        if te is None:
            return
        te.service.install_patches(lambda: self.state)

    def _compile_if_enabled(self, state: VideoPipelineState) -> VideoPipelineState:
        if not self.state.app_settings.use_torch_compile:
            return state
        if state.is_compiled:
            return state
        if self._runtime_device == "mps":
            logger.info("Skipping torch.compile() for %s - not supported on MPS", state.pipeline.pipeline_kind)
            return state

        try:
            state.pipeline.compile_transformer()
            state.is_compiled = True
        except Exception as exc:
            logger.warning("Failed to compile transformer: %s", exc, exc_info=True)
        return state

    def _create_video_pipeline(self, model_type: VideoPipelineModelType) -> VideoPipelineState:
        gemma_root = self._text_handler.resolve_gemma_root()

        checkpoint_path = self._resolve_available_checkpoint()
        upsampler_path = str(self.resolve_model("upsampler"))

        pipeline = self._fast_video_pipeline_class.create(
            checkpoint_path,
            gemma_root,
            upsampler_path,
            self.config.device,
        )

        state = VideoPipelineState(
            pipeline=pipeline,
            warmth=VideoPipelineWarmth.COLD,
            is_compiled=False,
        )
        return self._compile_if_enabled(state)

    def unload_gpu_pipeline(self) -> None:
        with self._lock:
            self._ensure_no_running_generation()
            self.state.gpu_slot = None
            self._assert_invariants()
        self._gpu_cleaner.cleanup()

    def force_unload_all(self) -> None:
        with self._lock:
            self.state.gpu_slot = None
            self.state.cpu_slot = None
        self._gpu_cleaner.cleanup()

    def park_image_generation_pipeline_on_cpu(self) -> None:
        image_generation_pipeline: ImageGenerationPipeline | None = None

        with self._lock:
            if self.state.gpu_slot is None:
                return

            active = self.state.gpu_slot.active_pipeline
            if not isinstance(active, ImageGenerationPipeline):
                return

            if isinstance(self.state.active_generation, GpuGeneration) and isinstance(
                self.state.active_generation.state, GenerationRunning
            ):
                raise RuntimeError("Cannot park image generation pipeline while generation is running")

            image_generation_pipeline = active
            self.state.gpu_slot = None

        assert image_generation_pipeline is not None
        image_generation_pipeline.to("cpu")
        self._gpu_cleaner.cleanup()

        with self._lock:
            self.state.cpu_slot = CpuSlot(active_pipeline=image_generation_pipeline)
            self._assert_invariants()

    def load_image_generation_pipeline_to_gpu(self, checkpoint_path: str | None = None) -> ImageGenerationPipeline:
        with self._lock:
            if self.state.gpu_slot is not None:
                active = self.state.gpu_slot.active_pipeline
                if isinstance(active, ImageGenerationPipeline):
                    return active
                self._ensure_no_running_generation()

        image_generation_pipeline: ImageGenerationPipeline | None = None

        with self._lock:
            match self.state.cpu_slot:
                case CpuSlot(active_pipeline=stored):
                    image_generation_pipeline = stored
                    self.state.cpu_slot = None
                case _:
                    image_generation_pipeline = None

        if image_generation_pipeline is None:
            effective_path = checkpoint_path or str(self.resolve_model("zit"))
            zit_path = Path(effective_path)
            if zit_path.is_dir():
                if not any(zit_path.iterdir()):
                    raise RuntimeError("Z-Image-Turbo model directory is empty. Please download the AI models first using the Model Status menu.")
            elif not zit_path.is_file():
                raise RuntimeError("Z-Image-Turbo model not found. Please download the AI models first using the Model Status menu.")
            image_generation_pipeline = self._image_generation_pipeline_class.create(effective_path, self._runtime_device)
        else:
            image_generation_pipeline.to(self._runtime_device)

        self._gpu_cleaner.cleanup()

        with self._lock:
            self.state.gpu_slot = GpuSlot(active_pipeline=image_generation_pipeline)
            self._assert_invariants()

        return image_generation_pipeline

    def preload_image_generation_pipeline_to_cpu(self) -> ImageGenerationPipeline:
        with self._lock:
            match self.state.cpu_slot:
                case CpuSlot(active_pipeline=existing):
                    return existing
                case _:
                    pass

        zit_path = self.resolve_model("zit")
        if not (zit_path.exists() and any(zit_path.iterdir())):
            raise RuntimeError("Z-Image-Turbo model not downloaded. Please download the AI models first using the Model Status menu.")

        image_generation_pipeline = self._image_generation_pipeline_class.create(str(zit_path), None)
        with self._lock:
            if self.state.cpu_slot is None:
                self.state.cpu_slot = CpuSlot(active_pipeline=image_generation_pipeline)
                self._assert_invariants()
                return image_generation_pipeline
            return self.state.cpu_slot.active_pipeline

    def _evict_gpu_pipeline_for_swap(self) -> None:
        should_park_image_generation_pipeline = False
        should_cleanup = False

        with self._lock:
            self._ensure_no_running_generation()
            if self.state.gpu_slot is None:
                return

            active = self.state.gpu_slot.active_pipeline
            if isinstance(active, ImageGenerationPipeline):
                should_park_image_generation_pipeline = True
            else:
                self.state.gpu_slot = None
                self._assert_invariants()
                should_cleanup = True

        if should_park_image_generation_pipeline:
            self.park_image_generation_pipeline_on_cpu()
        elif should_cleanup:
            self._gpu_cleaner.cleanup()

    def load_gpu_pipeline(self, model_type: VideoPipelineModelType, should_warm: bool = False) -> VideoPipelineState:
        self._install_text_patches_if_needed()

        state: VideoPipelineState | None = None
        with self._lock:
            if self._pipeline_matches_model_type(model_type):
                match self.state.gpu_slot:
                    case GpuSlot(active_pipeline=VideoPipelineState() as existing_state):
                        state = existing_state
                    case _:
                        pass

        if state is None:
            self._evict_gpu_pipeline_for_swap()
            state = self._create_video_pipeline(model_type)
            with self._lock:
                self.state.gpu_slot = GpuSlot(active_pipeline=state)
                self._assert_invariants()

        if should_warm and state.warmth == VideoPipelineWarmth.COLD:
            with self._lock:
                state.warmth = VideoPipelineWarmth.WARMING

            self.warmup_pipeline(model_type)
            with self._lock:
                if state.warmth == VideoPipelineWarmth.WARMING:
                    state.warmth = VideoPipelineWarmth.WARM

        return state

    def load_ic_lora(
        self,
        lora_path: str,
        depth_model_path: str | None = None,
        person_detector_model_path: str | None = None,
        pose_model_path: str | None = None,
        checkpoint_path: str | None = None,
    ) -> ICLoraState:
        self._install_text_patches_if_needed()

        effective_checkpoint = checkpoint_path or self._resolve_ic_lora_checkpoint()

        with self._lock:
            match self.state.gpu_slot:
                case GpuSlot(
                    active_pipeline=ICLoraState(
                        lora_path=current_lora_path,
                        depth_model_path=current_depth_model_path,
                    ) as state
                ) if (
                    current_lora_path == lora_path
                    and current_depth_model_path == (depth_model_path or "")
                    and (
                        (person_detector_model_path is None and pose_model_path is None)
                        or (
                            state.pose_resources is not None
                            and state.pose_resources.person_detector_model_path == (person_detector_model_path or "")
                            and state.pose_resources.pose_model_path == (pose_model_path or "")
                        )
                    )
                ):
                    return state
                case _:
                    pass

        self._evict_gpu_pipeline_for_swap()

        pipeline = self._ic_lora_pipeline_class.create(
            effective_checkpoint,
            self._text_handler.resolve_gemma_root(),
            str(self.resolve_model("upsampler")),
            lora_path,
            self.config.device,
        )

        depth_pipeline = None
        if depth_model_path:
            depth_pipeline = self._depth_processor_pipeline_class.create(depth_model_path, self.config.device)

        pose_resources = None
        if person_detector_model_path and pose_model_path:
            pose_pipeline = self._pose_processor_pipeline_class.create(
                pose_model_path,
                person_detector_model_path,
                self.config.device,
            )
            pose_resources = PoseResources(
                pipeline=pose_pipeline,
                person_detector_model_path=person_detector_model_path,
                pose_model_path=pose_model_path,
            )

        state = ICLoraState(
            pipeline=pipeline,
            lora_path=lora_path,
            depth_pipeline=depth_pipeline,
            depth_model_path=depth_model_path or "",
            pose_resources=pose_resources,
        )

        with self._lock:
            self.state.gpu_slot = GpuSlot(active_pipeline=state)
            self._assert_invariants()
        return state

    def load_a2v_pipeline(self) -> A2VPipelineState:
        self._install_text_patches_if_needed()

        with self._lock:
            match self.state.gpu_slot:
                case GpuSlot(active_pipeline=A2VPipelineState() as state):
                    return state
                case _:
                    pass

        self._evict_gpu_pipeline_for_swap()

        pipeline = self._a2v_pipeline_class.create(
            str(self.resolve_model("checkpoint")),
            self._text_handler.resolve_gemma_root(),
            str(self.resolve_model("upsampler")),
            self.config.device,
        )
        state = A2VPipelineState(pipeline=pipeline)

        with self._lock:
            self.state.gpu_slot = GpuSlot(active_pipeline=state)
            self._assert_invariants()
        return state

    def load_retake_pipeline(self, *, distilled: bool = True) -> RetakePipelineState:
        self._install_text_patches_if_needed()

        quantized = device_supports_fp8(self.config.device)

        with self._lock:
            match self.state.gpu_slot:
                case GpuSlot(
                    active_pipeline=RetakePipelineState(distilled=current_distilled, quantized=current_quantized) as state
                ) if current_distilled == distilled and current_quantized == quantized:
                    return state
                case _:
                    pass

        self._evict_gpu_pipeline_for_swap()

        from ltx_core.quantization import QuantizationPolicy

        quantization = QuantizationPolicy.fp8_cast() if quantized else None
        pipeline = self._retake_pipeline_class.create(
            checkpoint_path=str(self.resolve_model("checkpoint")),
            gemma_root=self._text_handler.resolve_gemma_root(),
            device=self.config.device,
            loras=[],
            quantization=quantization,
        )
        state = RetakePipelineState(pipeline=pipeline, distilled=distilled, quantized=quantized)

        with self._lock:
            self.state.gpu_slot = GpuSlot(active_pipeline=state)
            self._assert_invariants()
        return state

    def warmup_pipeline(self, model_type: VideoPipelineModelType) -> None:
        state = self.load_gpu_pipeline(model_type, should_warm=False)
        warmup_path = self.config.outputs_dir / f"_warmup_{model_type}.mp4"
        state.pipeline.warmup(output_path=str(warmup_path))
