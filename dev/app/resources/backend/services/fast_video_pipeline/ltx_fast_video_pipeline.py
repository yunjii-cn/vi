"""LTX fast video pipeline wrapper."""

from __future__ import annotations

from collections.abc import Iterator
import logging
import os
from typing import Any, Final, cast

import torch

from api_types import ImageConditioningInput
from services.ltx_pipeline_common import default_tiling_config, encode_video_output, video_chunks_number
from services.services_utils import AudioOrNone, TilingConfigType, device_supports_fp8

logger = logging.getLogger(__name__)

_PROMPT_ENCODER_INIT_PATCHED = False


def _ensure_prompt_encoder_init_patch() -> None:
    global _PROMPT_ENCODER_INIT_PATCHED
    if _PROMPT_ENCODER_INIT_PATCHED:
        return
    try:
        from ltx_pipelines.utils.blocks import PromptEncoder

        original_init = PromptEncoder.__init__

        def patched_init(
            self_encoder: PromptEncoder,
            checkpoint_path: str,
            gemma_root: str,
            dtype: Any,
            device: Any,
            registry: Any = None,
        ) -> None:
            if not gemma_root:
                self_encoder._dtype = dtype
                self_encoder._device = device
                self_encoder._text_encoder_builder = None
                self_encoder._embeddings_processor_builder = None
                return
            original_init(self_encoder, checkpoint_path, gemma_root, dtype, device, registry)

        PromptEncoder.__init__ = patched_init
        _PROMPT_ENCODER_INIT_PATCHED = True
        logger.info("Installed PromptEncoder.__init__ patch (from fast pipeline)")
    except Exception as exc:
        logger.warning("Failed to patch PromptEncoder.__init__: %s", exc, exc_info=True)


class LTXFastVideoPipeline:
    pipeline_kind: Final = "fast"

    @staticmethod
    def create(
        checkpoint_path: str,
        gemma_root: str | None,
        upsampler_path: str,
        device: torch.device,
    ) -> "LTXFastVideoPipeline":
        return LTXFastVideoPipeline(
            checkpoint_path=checkpoint_path,
            gemma_root=gemma_root,
            upsampler_path=upsampler_path,
            device=device,
        )

    def __init__(self, checkpoint_path: str, gemma_root: str | None, upsampler_path: str, device: torch.device) -> None:
        from ltx_core.quantization import QuantizationPolicy
        from ltx_pipelines.distilled import DistilledPipeline

        self._checkpoint_path = checkpoint_path
        self._gemma_root = gemma_root
        self._upsampler_path = upsampler_path
        self._device = device
        self._quantization = QuantizationPolicy.fp8_cast() if device_supports_fp8(device) else None

        _ensure_prompt_encoder_init_patch()

        self.pipeline = DistilledPipeline(
            distilled_checkpoint_path=checkpoint_path,
            gemma_root=gemma_root or "",
            spatial_upsampler_path=upsampler_path,
            loras=[],
            device=device,
            quantization=self._quantization,
        )

    def _run_inference(
        self,
        prompt: str,
        seed: int,
        height: int,
        width: int,
        num_frames: int,
        frame_rate: float,
        images: list[ImageConditioningInput],
        tiling_config: TilingConfigType,
    ) -> tuple[torch.Tensor | Iterator[torch.Tensor], AudioOrNone]:
        from ltx_pipelines.utils.args import ImageConditioningInput as _LtxImageInput

        return self.pipeline(
            prompt=prompt,
            seed=seed,
            height=height,
            width=width,
            num_frames=num_frames,
            frame_rate=frame_rate,
            images=[_LtxImageInput(img.path, img.frame_idx, img.strength) for img in images],
            tiling_config=tiling_config,
            streaming_prefetch_count=2,
        )

    @torch.inference_mode()
    def generate(
        self,
        prompt: str,
        seed: int,
        height: int,
        width: int,
        num_frames: int,
        frame_rate: float,
        images: list[ImageConditioningInput],
        output_path: str,
    ) -> None:
        tiling_config = default_tiling_config()
        video, audio = self._run_inference(
            prompt=prompt,
            seed=seed,
            height=height,
            width=width,
            num_frames=num_frames,
            frame_rate=frame_rate,
            images=images,
            tiling_config=tiling_config,
        )
        chunks = video_chunks_number(num_frames, tiling_config)
        encode_video_output(video=video, audio=audio, fps=int(frame_rate), output_path=output_path, video_chunks_number_value=chunks)

    @torch.inference_mode()
    def warmup(self, output_path: str) -> None:
        warmup_frames = 9
        tiling_config = default_tiling_config()

        try:
            video, audio = self._run_inference(
                prompt="test warmup",
                seed=42,
                height=256,
                width=384,
                num_frames=warmup_frames,
                frame_rate=8,
                images=[],
                tiling_config=tiling_config,
            )
            chunks = video_chunks_number(warmup_frames, tiling_config)
            encode_video_output(video=video, audio=audio, fps=8, output_path=output_path, video_chunks_number_value=chunks)
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def compile_transformer(self) -> None:
        from ltx_pipelines.distilled import DistilledPipeline

        _ensure_prompt_encoder_init_patch()

        self.pipeline = DistilledPipeline(
            distilled_checkpoint_path=self._checkpoint_path,
            gemma_root=self._gemma_root or "",
            spatial_upsampler_path=self._upsampler_path,
            loras=[],
            device=self._device,
            quantization=self._quantization,
            torch_compile=True,
        )
