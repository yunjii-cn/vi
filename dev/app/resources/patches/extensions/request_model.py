"""Extend GenerateVideoRequest with YunJi custom fields.

Upstream dependency: api_types.GenerateVideoRequest
When upstream adds new fields, review this extension for conflicts.
"""

from __future__ import annotations

from pydantic import ConfigDict


def install() -> None:
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
