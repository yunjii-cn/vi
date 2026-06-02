"""Generation lifecycle handler."""

from __future__ import annotations

import logging
import threading
from threading import RLock
from typing import TYPE_CHECKING, ClassVar, Literal

_DENOISING_CANCEL_HANDLER = [None]
_DENOISING_CANCEL_PATCHED = False
_DENOISING_CANCEL_LOCK = threading.Lock()

from api_types import (
    CancelCancellingResponse,
    CancelNoActiveGenerationResponse,
    CancelResponse,
    GenerationProgressResponse,
)
from handlers.base import StateHandlerBase, with_state_lock
from state.app_state_types import (
    ApiGeneration,
    AppState,
    GenerationCancelled,
    GenerationComplete,
    GenerationError,
    GenerationProgress,
    GenerationRunning,
    GenerationState,
    GpuGeneration,
)

if TYPE_CHECKING:
    from handlers.pipelines_handler import PipelinesHandler
    from runtime_config.runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)
GenerationSlot = Literal["gpu", "api"]


class GenerationHandler(StateHandlerBase):
    _pipelines_handler: ClassVar[PipelinesHandler | None] = None

    def __init__(self, state: AppState, lock: RLock, config: RuntimeConfig) -> None:
        super().__init__(state, lock, config)
        self._install_denoising_cancel_patch()

    @classmethod
    def bind_pipelines_handler(cls, pipelines_handler: PipelinesHandler) -> None:
        cls._pipelines_handler = pipelines_handler

    @staticmethod
    def _install_denoising_cancel_patch() -> None:
        global _DENOISING_CANCEL_PATCHED
        with _DENOISING_CANCEL_LOCK:
            if _DENOISING_CANCEL_PATCHED:
                return
            _DENOISING_CANCEL_PATCHED = True
        try:
            from ltx_pipelines.utils.samplers import euler_denoising_loop as _orig_loop
            from ltx_pipelines.utils.samplers import _step_state as _orig_step_state

            def _cancellable_loop(sigmas, video_state, audio_state, stepper, transformer, denoiser):
                handler = _DENOISING_CANCEL_HANDLER[0]
                for step_idx, _ in enumerate(sigmas[:-1]):
                    if handler is not None and handler.is_generation_cancelled():
                        logger.info("[cancel] Cancel detected at denoising step %d, aborting", step_idx)
                        raise RuntimeError("Generation was cancelled during denoising")
                    denoised_video, denoised_audio = denoiser(
                        transformer, video_state, audio_state, sigmas, step_idx
                    )
                    video_state = _orig_step_state(video_state, denoised_video, stepper, sigmas, step_idx)
                    audio_state = _orig_step_state(audio_state, denoised_audio, stepper, sigmas, step_idx)
                return video_state, audio_state

            import ltx_pipelines.utils.samplers as _samplers_mod
            _samplers_mod.euler_denoising_loop = _cancellable_loop

            import ltx_pipelines.utils.blocks as _blocks_mod
            if getattr(_blocks_mod, "euler_denoising_loop", None) is _orig_loop:
                _blocks_mod.euler_denoising_loop = _cancellable_loop

            logger.info("[cancel] Installed cancel-check patch on euler_denoising_loop")
        except Exception as exc:
            logger.warning("[cancel] Failed to install cancel-check patch: %s", exc)

    def set_denoising_cancel_active(self) -> None:
        _DENOISING_CANCEL_HANDLER[0] = self

    def clear_denoising_cancel_active(self) -> None:
        _DENOISING_CANCEL_HANDLER[0] = None

    @with_state_lock
    def start_generation(self, generation_id: str) -> None:
        if self.is_generation_running():
            raise RuntimeError("Generation already in progress")

        # Clear any previous cancelled state and cancel handler
        self.state.active_generation = None
        self.clear_denoising_cancel_active()

        self.state.active_generation = GpuGeneration(
            state=GenerationRunning(
                id=generation_id,
                progress=GenerationProgress(phase="preparing", progress=2, current_step=0, total_steps=0),
            )
        )

    @with_state_lock
    def start_api_generation(self, generation_id: str) -> None:
        if self.is_generation_running():
            raise RuntimeError("Generation already in progress")

        self.state.active_generation = None
        self.clear_denoising_cancel_active()

        self.state.active_generation = ApiGeneration(
            state=GenerationRunning(
                id=generation_id,
                progress=GenerationProgress(phase="", progress=0, current_step=None, total_steps=None),
            )
        )

    @with_state_lock
    def _gpu_generation(self) -> GenerationState | None:
        match self.state.active_generation:
            case GpuGeneration(state=generation):
                return generation
            case _:
                return None

    @with_state_lock
    def _api_generation(self) -> GenerationState | None:
        match self.state.active_generation:
            case ApiGeneration(state=generation):
                return generation
            case _:
                return None

    @with_state_lock
    def _active_generation_state(self) -> tuple[GenerationSlot, GenerationState] | None:
        match self.state.active_generation:
            case GpuGeneration(state=generation):
                return "gpu", generation
            case ApiGeneration(state=generation):
                return "api", generation
            case _:
                return None

    @with_state_lock
    def _running_slot(self) -> GenerationSlot | None:
        active = self._active_generation_state()
        if active is None:
            return None

        slot, generation = active
        match generation:
            case GenerationRunning():
                return slot
            case _:
                return None

    @with_state_lock
    def _running_generation(self) -> tuple[GenerationSlot, GenerationRunning] | None:
        active = self._active_generation_state()
        if active is None:
            return None

        slot, generation = active
        match generation:
            case GenerationRunning() as running:
                return slot, running
            case _:
                return None

    @with_state_lock
    def _cancelled_generation(self) -> tuple[GenerationSlot, GenerationCancelled] | None:
        active = self._active_generation_state()
        if active is None:
            return None

        slot, generation = active
        match generation:
            case GenerationCancelled() as cancelled:
                return slot, cancelled
            case _:
                return None

    @with_state_lock
    def _set_generation_state(self, slot: GenerationSlot, generation: GenerationState) -> None:
        if slot == "gpu":
            self.state.active_generation = GpuGeneration(state=generation)
            return
        self.state.active_generation = ApiGeneration(state=generation)

    @with_state_lock
    def _generation_for_polling(self) -> GenerationState | None:
        active = self._active_generation_state()
        return None if active is None else active[1]

    @with_state_lock
    def is_generation_cancelled(self) -> bool:
        match self._active_generation_state():
            case (_, GenerationCancelled()):
                return True
            case _:
                return False

    @with_state_lock
    def update_progress(
        self,
        phase: str,
        progress: int,
        current_step: int | None = None,
        total_steps: int | None = None,
        log_message: str | None = None,
    ) -> None:
        running_generation = self._running_generation()
        if running_generation is None:
            print(f"[progress] update_progress SKIP: no running generation (phase={phase}, progress={progress}, gpu_slot={self.state.gpu_slot is not None})")
            return

        _, running = running_generation
        running.progress.phase = phase
        running.progress.progress = progress
        running.progress.current_step = current_step
        running.progress.total_steps = total_steps
        if log_message is not None:
            running.progress.log_message = log_message

    @with_state_lock
    def cancel_generation(self) -> CancelResponse:
        running_generation = self._running_generation()
        if running_generation is not None:
            slot, running = running_generation
            self._set_generation_state(slot, GenerationCancelled(id=running.id))
            return CancelCancellingResponse(status="cancelling", id=running.id)

        cancelled_generation = self._cancelled_generation()
        match cancelled_generation:
            case (_, GenerationCancelled(id=generation_id)):
                return CancelCancellingResponse(status="cancelling", id=generation_id)
            case _:
                return CancelNoActiveGenerationResponse(status="no_active_generation")

    def force_cancel_generation(self) -> CancelResponse:
        logger.info("[force-cancel] Force cancel requested — setting cancel flag and unloading pipelines")
        soft_result = self.cancel_generation()

        if self._pipelines_handler is not None:
            try:
                self._pipelines_handler.force_unload_all()
                logger.info("[force-cancel] All GPU pipelines force-unloaded")
            except Exception as exc:
                logger.warning("[force-cancel] Failed to force-unload pipelines: %s", exc)

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

        import gc
        gc.collect()

        return soft_result

    @with_state_lock
    def complete_generation(self, result: str | list[str]) -> None:
        running_generation = self._running_generation()
        if running_generation is None:
            return

        slot, running = running_generation
        self._set_generation_state(slot, GenerationComplete(id=running.id, result=result))

    @with_state_lock
    def fail_generation(self, error: str) -> None:
        running_generation = self._running_generation()
        if running_generation is not None:
            slot, running = running_generation
            logger.error("Generation %s failed: %s", running.id, error)
            self._set_generation_state(slot, GenerationError(id=running.id, error=error))
            return

        if self._cancelled_generation() is not None:
            return

        logger.error("Generation failed without active running job: %s", error)

    @with_state_lock
    def get_generation_progress(self) -> GenerationProgressResponse:
        gen = self._generation_for_polling()
        gpu_ok = self.state.gpu_slot is not None
        ag_type = type(self.state.active_generation).__name__ if self.state.active_generation else "None"

        match gen:
            case GenerationRunning(progress=progress):
                return GenerationProgressResponse(
                    status="running",
                    phase=progress.phase,
                    progress=progress.progress,
                    currentStep=progress.current_step,
                    totalSteps=progress.total_steps,
                    logMessage=progress.log_message,
                )
            case GenerationComplete():
                return GenerationProgressResponse(
                    status="complete",
                    phase="complete",
                    progress=100,
                    currentStep=0,
                    totalSteps=0,
                )
            case GenerationCancelled():
                return GenerationProgressResponse(
                    status="cancelled",
                    phase="cancelled",
                    progress=0,
                    currentStep=0,
                    totalSteps=0,
                )
            case GenerationError():
                return GenerationProgressResponse(
                    status="error",
                    phase="error",
                    progress=0,
                    currentStep=0,
                    totalSteps=0,
                )
            case _:
                return GenerationProgressResponse(
                    status="idle",
                    phase="",
                    progress=0,
                    currentStep=0,
                    totalSteps=0,
                )

    @with_state_lock
    def is_generation_running(self) -> bool:
        return self._running_slot() is not None
