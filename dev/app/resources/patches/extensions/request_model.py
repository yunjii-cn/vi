"""Extend GenerateVideoRequest, IcLoraGenerateRequest, and GenerateImageRequest with YunJi custom fields.

Upstream dependency: api_types.GenerateVideoRequest, api_types.IcLoraGenerateRequest, api_types.GenerateImageRequest
When upstream adds new fields, review this extension for conflicts.
"""

from __future__ import annotations

from pydantic import ConfigDict


def install() -> None:
    _patch_video_request()
    _patch_ic_lora_request()
    _patch_image_request()


def _patch_video_request() -> None:
    from api_types import GenerateVideoRequest

    annotations = dict(getattr(GenerateVideoRequest, "__annotations__", {}))
    changed = False

    for field_name, ann in (
        ("startFramePath", str | None),
        ("endFramePath", str | None),
        ("keyframePaths", list[str] | None),
        ("keyframeStrengths", list[float] | None),
        ("keyframeTimes", list[float] | None),
        ("loraPaths", list[str] | None),
        ("loraStrengths", list[float] | None),
        ("modelPath", str | None),
        ("seed", int | None),
        ("customWidth", int | None),
        ("customHeight", int | None),
        ("distilled", bool),
        ("numInferenceSteps", int | None),
    ):
        if field_name not in annotations:
            annotations[field_name] = ann
            setattr(GenerateVideoRequest, field_name, None)
            changed = True

    if changed:
        GenerateVideoRequest.__annotations__ = annotations

    existing_config = dict(getattr(GenerateVideoRequest, "model_config", {}) or {})
    if existing_config.get("extra") != "allow":
        existing_config["extra"] = "allow"
        GenerateVideoRequest.model_config = ConfigDict(**existing_config)
        changed = True

    if changed:
        GenerateVideoRequest.model_rebuild(force=True)


def _patch_ic_lora_request() -> None:
    try:
        from api_types import IcLoraGenerateRequest
    except ImportError:
        return

    annotations = dict(getattr(IcLoraGenerateRequest, "__annotations__", {}))
    changed = False

    for field_name, ann in (
        ("modelPath", str | None),
        ("quality", str | None),
        ("fps", int | float | None),
        ("duration", int | float | None),
        ("attention_strength", float),
        ("seed", int | None),
        ("loraPaths", list[str] | None),
        ("loraStrengths", list[float] | None),
    ):
        if field_name not in annotations:
            annotations[field_name] = ann
            setattr(IcLoraGenerateRequest, field_name, None)
            changed = True

    if changed:
        IcLoraGenerateRequest.__annotations__ = annotations

    existing_config = dict(getattr(IcLoraGenerateRequest, "model_config", {}) or {})
    if existing_config.get("extra") != "allow":
        existing_config["extra"] = "allow"
        IcLoraGenerateRequest.model_config = ConfigDict(**existing_config)
        changed = True

    if changed:
        IcLoraGenerateRequest.model_rebuild(force=True)


def _patch_image_request() -> None:
    try:
        from api_types import GenerateImageRequest
    except ImportError:
        return

    annotations = dict(getattr(GenerateImageRequest, "__annotations__", {}))
    changed = False

    for field_name, ann in (
        ("modelPath", str | None),
        ("loraPaths", list[str] | None),
        ("loraStrengths", list[float] | None),
    ):
        if field_name not in annotations:
            annotations[field_name] = ann
            setattr(GenerateImageRequest, field_name, None)
            changed = True

    if changed:
        GenerateImageRequest.__annotations__ = annotations

    existing_config = dict(getattr(GenerateImageRequest, "model_config", {}) or {})
    if existing_config.get("extra") != "allow":
        existing_config["extra"] = "allow"
        GenerateImageRequest.model_config = ConfigDict(**existing_config)
        changed = True

    if changed:
        GenerateImageRequest.model_rebuild(force=True)
