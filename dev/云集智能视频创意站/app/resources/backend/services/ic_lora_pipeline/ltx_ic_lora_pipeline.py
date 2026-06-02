"""LTX IC-LoRA pipeline wrapper."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import torch
from pathlib import Path

from api_types import ImageConditioningInput
from services.ltx_pipeline_common import default_tiling_config, encode_video_output, video_chunks_number
from services.services_utils import AudioOrNone, TilingConfigType, device_supports_fp8


class LTXIcLoraPipeline:
    @staticmethod
    def create(
        checkpoint_path: str,
        gemma_root: str | None,
        upsampler_path: str,
        lora_path: str,
        device: torch.device,
    ) -> "LTXIcLoraPipeline":
        return LTXIcLoraPipeline(
            checkpoint_path=checkpoint_path,
            gemma_root=gemma_root,
            upsampler_path=upsampler_path,
            lora_path=lora_path,
            device=device,
        )

    def __init__(
        self,
        checkpoint_path: str,
        gemma_root: str | None,
        upsampler_path: str,
        lora_path: str,
        device: torch.device,
    ) -> None:
        from ltx_core.loader.primitives import LoraPathStrengthAndSDOps
        from ltx_core.loader.sd_ops import LTXV_LORA_COMFY_RENAMING_MAP
        from ltx_core.quantization import QuantizationPolicy
        from ltx_pipelines.ic_lora import ICLoraPipeline
        from services.ltx_pipeline_common import _ensure_prompt_encoder_init_patch

        _ensure_prompt_encoder_init_patch()

        is_fp8_checkpoint = "fp8" in Path(checkpoint_path).name.lower()
        quantization = QuantizationPolicy.fp8_cast() if is_fp8_checkpoint else None

        lora_entry = LoraPathStrengthAndSDOps(path=lora_path, strength=1.0, sd_ops=LTXV_LORA_COMFY_RENAMING_MAP)
        self.pipeline = ICLoraPipeline(
            distilled_checkpoint_path=checkpoint_path,
            spatial_upsampler_path=upsampler_path,
            gemma_root=gemma_root or "",
            loras=[lora_entry],
            device=device,
            quantization=quantization,
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
        video_conditioning: list[tuple[str, float]],
        tiling_config: TilingConfigType,
        conditioning_attention_strength: float = 1.0,
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
            video_conditioning=video_conditioning,
            tiling_config=tiling_config,
            conditioning_attention_strength=conditioning_attention_strength,
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
        video_conditioning: list[tuple[str, float]],
        output_path: str,
        conditioning_attention_strength: float = 1.0,
        output_fps: int | None = None,
    ) -> None:
        import logging

        _ic_logger = logging.getLogger(__name__)

        tiling_config = default_tiling_config()
        video, audio = self._run_inference(
            prompt=prompt,
            seed=seed,
            height=height,
            width=width,
            num_frames=num_frames,
            frame_rate=frame_rate,
            images=images,
            video_conditioning=video_conditioning,
            tiling_config=tiling_config,
            conditioning_attention_strength=conditioning_attention_strength,
        )

        if isinstance(video, Iterator):
            _ic_logger.info("[IC-LoRA diag] VAE decoder returned iterator — will inspect first chunk")
            chunk_values: list[float] = []
            def _diag_wrap(video_iter: Iterator[torch.Tensor]) -> Iterator[torch.Tensor]:
                chunk_idx = 0
                for chunk in video_iter:
                    chunk_idx += 1
                    cmin = float(chunk.min())
                    cmax = float(chunk.max())
                    cmean = float(chunk.float().mean())
                    chunk_values.append(cmean)
                    _ic_logger.info(
                        "[IC-LoRA diag] VAE chunk %d: shape=%s dtype=%s min=%.4f max=%.4f mean=%.4f",
                        chunk_idx, tuple(chunk.shape), chunk.dtype, cmin, cmax, cmean,
                    )
                    yield chunk
                _ic_logger.info("[IC-LoRA diag] All %d VAE chunks mean: %.4f", len(chunk_values), sum(chunk_values) / max(len(chunk_values), 1))
            video = _diag_wrap(video)
        else:
            _ic_logger.info(
                "[IC-LoRA diag] VAE decoder returned tensor: shape=%s dtype=%s min=%.4f max=%.4f mean=%.4f",
                tuple(video.shape), video.dtype,
                float(video.min()), float(video.max()), float(video.float().mean()),
            )

        chunks = video_chunks_number(num_frames, tiling_config)
        encode_video_output(video=video, audio=audio, fps=output_fps if output_fps is not None else int(frame_rate), output_path=output_path, video_chunks_number_value=chunks)
        _ic_logger.info("[IC-LoRA diag] Video encoded to %s", output_path)
