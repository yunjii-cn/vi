"""Canonical model download specs and required-model policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from state.app_state_types import ModelFileType


@dataclass(frozen=True, slots=True)
class ModelFileDownloadSpec:
    relative_path: Path
    expected_size_bytes: int
    is_folder: bool
    repo_id: str
    description: str

    @property
    def name(self) -> str:
        return self.relative_path.name


MODEL_FILE_ORDER: tuple[ModelFileType, ...] = (
    "checkpoint",
    "checkpoint_fp8",
    "upsampler",
    "distilled_lora",
    "ic_lora",
    "depth_processor",
    "person_detector",
    "pose_processor",
    "text_encoder",
    "zit",
    "tts",
)


DEFAULT_MODEL_DOWNLOAD_SPECS: dict[ModelFileType, ModelFileDownloadSpec] = {
    "checkpoint": ModelFileDownloadSpec(
        relative_path=Path("ltx-2.3-22b-distilled.safetensors"),
        expected_size_bytes=43_000_000_000,
        is_folder=False,
        repo_id="Lightricks/LTX-2.3",
        description="视频生成核心模型（文生视频/图生视频/智能多帧）",
    ),
    "checkpoint_fp8": ModelFileDownloadSpec(
        relative_path=Path("ltx-2.3-22b-distilled-fp8.safetensors"),
        expected_size_bytes=22_000_000_000,
        is_folder=False,
        repo_id="Lightricks/LTX-2.3",
        description="FP8量化模型（视频生成，节省4GB显存，推荐10-24GB显卡）",
    ),
    "upsampler": ModelFileDownloadSpec(
        relative_path=Path("ltx-2.3-spatial-upscaler-x2-1.0.safetensors"),
        expected_size_bytes=1_900_000_000,
        is_folder=False,
        repo_id="Lightricks/LTX-2.3",
        description="2x画质增强模型（视频生成高清输出）",
    ),
    "distilled_lora": ModelFileDownloadSpec(
        relative_path=Path("ltx-2-19b-distilled-lora-384.safetensors"),
        expected_size_bytes=400_000_000,
        is_folder=False,
        repo_id="Lightricks/LTX-2",
        description="Pro模式LoRA（视频生成Pro高质量模式）",
    ),
    "ic_lora": ModelFileDownloadSpec(
        relative_path=Path("ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors"),
        expected_size_bytes=654_465_352,
        is_folder=False,
        repo_id="Lightricks/LTX-2.3-22b-IC-LoRA-Union-Control",
        description="视频迁移控制模型（视频迁移功能必需）",
    ),
    "depth_processor": ModelFileDownloadSpec(
        relative_path=Path("dpt-hybrid-midas"),
        expected_size_bytes=500_000_000,
        is_folder=True,
        repo_id="Intel/dpt-hybrid-midas",
        description="深度估计模型（视频迁移-深度控制）",
    ),
    "person_detector": ModelFileDownloadSpec(
        relative_path=Path("yolox_l.torchscript.pt"),
        expected_size_bytes=217_697_649,
        is_folder=False,
        repo_id="hr16/yolox-onnx",
        description="人物检测模型（视频迁移-姿态控制）",
    ),
    "pose_processor": ModelFileDownloadSpec(
        relative_path=Path("dw-ll_ucoco_384_bs5.torchscript.pt"),
        expected_size_bytes=135_059_124,
        is_folder=False,
        repo_id="hr16/DWPose-TorchScript-BatchSize5",
        description="姿态估计模型（视频迁移-姿态/动作控制）",
    ),
    "text_encoder": ModelFileDownloadSpec(
        relative_path=Path("gemma-3-12b-it-qat-q4_0-unquantized"),
        expected_size_bytes=25_000_000_000,
        is_folder=True,
        repo_id="Lightricks/gemma-3-12b-it-qat-q4_0-unquantized",
        description="文本编码器（所有生成功能的提示词理解）",
    ),
    "zit": ModelFileDownloadSpec(
        relative_path=Path("Z-Image-Turbo"),
        expected_size_bytes=31_000_000_000,
        is_folder=True,
        repo_id="Tongyi-MAI/Z-Image-Turbo",
        description="图像生成模型（AI图像生成功能必需）",
    ),
    "tts": ModelFileDownloadSpec(
        relative_path=Path("VoxCPM2"),
        expected_size_bytes=8_000_000_000,
        is_folder=True,
        repo_id="openbmb/VoxCPM2",
        description="语音合成模型（TTS语音/声音克隆功能必需）",
    ),
}


DEFAULT_REQUIRED_MODEL_TYPES: frozenset[ModelFileType] = frozenset(
    {"checkpoint", "upsampler", "zit", "text_encoder"}
)


def _normalized_relative_path(
    specs: Mapping[ModelFileType, ModelFileDownloadSpec],
    model_type: ModelFileType,
) -> Path:
    """Validate and normalize relative_path from specs — pure function."""
    relative_path = specs[model_type].relative_path
    if relative_path.is_absolute():
        raise ValueError(f"Model path for {model_type} must be relative: {relative_path}")

    normalized_parts = [part for part in relative_path.parts if part not in ("", ".")]
    if not normalized_parts:
        raise ValueError(f"Model path for {model_type} cannot be empty: {relative_path}")
    if ".." in normalized_parts:
        raise ValueError(f"Model path for {model_type} cannot traverse parents: {relative_path}")

    return Path(*normalized_parts)


def resolve_model_path(
    models_dir: Path,
    specs: Mapping[ModelFileType, ModelFileDownloadSpec],
    model_type: ModelFileType,
) -> Path:
    return models_dir / _normalized_relative_path(specs, model_type)


def resolve_model_path_multi(
    models_dirs: list[Path],
    specs: Mapping[ModelFileType, ModelFileDownloadSpec],
    model_type: ModelFileType,
) -> Path:
    relative = _normalized_relative_path(specs, model_type)
    spec = specs[model_type]
    for d in models_dirs:
        candidate = d / relative
        if spec.is_folder:
            if candidate.is_dir() and any(candidate.iterdir()):
                return candidate
        else:
            if candidate.is_file():
                return candidate
    return models_dirs[0] / relative


def resolve_downloading_dir(models_dir: Path) -> Path:
    return models_dir / ".downloading"


def resolve_downloading_target_path(
    models_dir: Path,
    specs: Mapping[ModelFileType, ModelFileDownloadSpec],
    model_type: ModelFileType,
) -> Path:
    return resolve_downloading_dir(models_dir) / _normalized_relative_path(specs, model_type)


def resolve_downloading_path(
    models_dir: Path,
    specs: Mapping[ModelFileType, ModelFileDownloadSpec],
    model_type: ModelFileType,
) -> Path:
    """Return the staging path under downloading_dir for a model type."""
    spec = specs[model_type]
    relative_path = _normalized_relative_path(specs, model_type)
    downloading_dir = resolve_downloading_dir(models_dir)
    if not spec.is_folder:
        parent = relative_path.parent
        if parent == Path("."):
            return downloading_dir
        return downloading_dir / parent
    return downloading_dir / relative_path


def resolve_required_model_types(
    base_required: frozenset[ModelFileType],
    has_api_key: bool,
    use_local_text_encoder: bool = False,
) -> frozenset[ModelFileType]:
    if not base_required:
        return base_required
    return cast(frozenset[ModelFileType], base_required | {"text_encoder"})
