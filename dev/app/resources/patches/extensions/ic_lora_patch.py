"""IC-LoRA reference conditioning patch.

Adds video/pose conditioning support for IC-LoRA generation.
Patches IcLoraHandler._build_conditioning_frame, _require_ic_lora_model_paths, generate.

Upstream dependency: handlers.ic_lora_handler.IcLoraHandler
                    runtime_config.model_download_specs.resolve_model_path
                    state.app_state_types.PoseResources
When upstream changes IcLoraHandler, review this patch for compatibility.
"""

from __future__ import annotations

from pathlib import Path

from _routes._errors import HTTPError
from extensions._context import ExtensionContext


def install(app, ctx: ExtensionContext) -> None:
    try:
        from handlers.ic_lora_handler import IcLoraHandler
        from runtime_config.model_download_specs import resolve_model_path
        from state.app_state_types import PoseResources
    except Exception as exc:
        print(f"[PATCH] IC-LoRA reference patch skipped: {exc}")
        return

    if getattr(IcLoraHandler, "_motion_reference_patch_installed", False):
        return

    orig_build_conditioning_frame = IcLoraHandler._build_conditioning_frame
    orig_generate = IcLoraHandler.generate
    orig_require_ic_lora_model_paths = IcLoraHandler._require_ic_lora_model_paths

    def _ensure_pose_resources(self, ic_state):
        if ic_state.pose_resources is not None:
            return ic_state.pose_resources

        person_detector_path = resolve_model_path(
            self.models_dir, self.config.model_download_specs, "person_detector"
        )
        pose_model_path = resolve_model_path(
            self.models_dir, self.config.model_download_specs, "pose_processor"
        )
        if not person_detector_path.exists():
            raise HTTPError(400, f"Pose person detector model not found: {person_detector_path}")
        if not pose_model_path.exists():
            raise HTTPError(400, f"Pose processor model not found: {pose_model_path}")

        pose_pipeline_class = getattr(self._pipelines, "_pose_processor_pipeline_class", None)
        if pose_pipeline_class is None:
            raise HTTPError(500, "Pose processor pipeline class is unavailable")

        pose_pipeline = pose_pipeline_class.create(
            str(pose_model_path),
            str(person_detector_path),
            self.config.device,
        )
        ic_state.pose_resources = PoseResources(
            pipeline=pose_pipeline,
            person_detector_model_path=str(person_detector_path),
            pose_model_path=str(pose_model_path),
        )
        print("[PATCH] IC-LoRA Pose resources loaded")
        return ic_state.pose_resources

    def patched_build_conditioning_frame(self, frame, conditioning_type, ic_state=None):
        if conditioning_type == "video":
            return frame
        if conditioning_type != "pose":
            return orig_build_conditioning_frame(self, frame, conditioning_type, ic_state)
        if ic_state is None:
            raise HTTPError(500, "Pose conditioning requires loaded IC-LoRA resources")
        pose_resources = _ensure_pose_resources(self, ic_state)
        return self._video_processor.apply_pose(frame, pose_resources.pipeline)

    def patched_require_ic_lora_model_paths(self):
        lora_path, depth_model_path = orig_require_ic_lora_model_paths(self)
        override = getattr(self, "_motion_reference_lora_path", None)
        if override:
            override_path = Path(override)
            if not override_path.exists():
                raise HTTPError(400, f"Video reference IC-LoRA model not found: {override_path}")
            return override_path, depth_model_path
        return lora_path, depth_model_path

    def patched_generate(self, req):
        override = getattr(req, "ic_lora_path", None)
        if not override and getattr(req, "conditioning_type", None) == "video":
            candidate = self.models_dir / "LTX2.3-22B_IC-LoRA-Cameraman_v1_10500.safetensors"
            if candidate.exists():
                override = str(candidate)
        previous = getattr(self, "_motion_reference_lora_path", None)
        self._motion_reference_lora_path = override
        try:
            return orig_generate(self, req)
        finally:
            self._motion_reference_lora_path = previous

    IcLoraHandler._build_conditioning_frame = patched_build_conditioning_frame
    IcLoraHandler._require_ic_lora_model_paths = patched_require_ic_lora_model_paths
    IcLoraHandler.generate = patched_generate
    IcLoraHandler._motion_reference_patch_installed = True
    print("[PATCH] IC-LoRA Pose/Video reference conditioning patch installed")
