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

    import inspect
    orig_require_sig = inspect.signature(orig_require_ic_lora_model_paths)
    _orig_accepts_conditioning_type = "conditioning_type" in orig_require_sig.parameters or len(orig_require_sig.parameters) >= 2

    def _call_orig_require(self, conditioning_type=None):
        try:
            if _orig_accepts_conditioning_type:
                ct = conditioning_type if conditioning_type is not None else "canny"
                result = orig_require_ic_lora_model_paths(self, ct)
            else:
                result = orig_require_ic_lora_model_paths(self)
        except TypeError:
            try:
                result = orig_require_ic_lora_model_paths(self)
            except TypeError:
                result = orig_require_ic_lora_model_paths(self, conditioning_type)

        if isinstance(result, tuple) and len(result) >= 4:
            return result[0], result[1], result[2], result[3]
        if isinstance(result, tuple) and len(result) == 2:
            return result[0], result[1], None, None
        return result, None, None, None

    def _ensure_pose_resources(self, ic_state):
        if ic_state.pose_resources is not None:
            return ic_state.pose_resources

        person_detector_path = self.resolve_model("person_detector")
        pose_model_path = self.resolve_model("pose_processor")
        if not person_detector_path.exists():
            raise HTTPError(400, f"Pose person detector model not found: {person_detector_path}")
        if not pose_model_path.exists():
            raise HTTPError(400, f"Pose processor model not found: {pose_model_path}")

        print(f"[PATCH] Loading pose resources: detector={person_detector_path}, pose={pose_model_path}", flush=True)

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
        print("[PATCH] IC-LoRA Pose resources loaded successfully", flush=True)
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

    def patched_require_ic_lora_model_paths(self, conditioning_type=None):
        lora_path, depth_model_path, person_detector_path, pose_model_path = (
            _call_orig_require(self, conditioning_type)
        )

        override = getattr(self, "_motion_reference_lora_path", None)
        if override:
            override_path = Path(override)
            if not override_path.exists():
                raise HTTPError(400, f"Video reference IC-LoRA model not found: {override_path}")
            lora_path = override_path

        return lora_path, depth_model_path, person_detector_path, pose_model_path

    def patched_generate(self, req):
        conditioning_type = getattr(req, "conditioning_type", None)
        if conditioning_type == "pose":
            person_detector_path = self.resolve_model("person_detector")
            pose_model_path = self.resolve_model("pose_processor")
            missing = []
            if not person_detector_path.exists():
                missing.append(f"person_detector ({person_detector_path.name})")
            if not pose_model_path.exists():
                missing.append(f"pose_processor ({pose_model_path.name})")
            if missing:
                raise HTTPError(
                    400,
                    f"Pose conditioning requires models not found: {', '.join(missing)}. "
                    f"Please download them from the Model Management page (person_detector + pose_processor). "
                    f"Check models dir: {self.models_dir}",
                )

        override = getattr(req, "ic_lora_path", None)
        if not override and getattr(req, "conditioning_type", None) == "video":
            candidate = self.models_dir / "LTX2.3-22B_IC-LoRA-Cameraman_v1_10500.safetensors"
            if not candidate.exists():
                for d in self.models_dirs[1:]:
                    alt = d / "LTX2.3-22B_IC-LoRA-Cameraman_v1_10500.safetensors"
                    if alt.exists():
                        candidate = alt
                        break
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
