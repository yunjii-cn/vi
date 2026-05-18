"""Upstream version tracking for LTX Desktop.

This file records which upstream version our patches are compatible with.
When updating the upstream backend code, update this file accordingly.

Update procedure:
  1. Download new upstream release from github.com/Lightricks/LTX-Desktop/releases
  2. Extract and compare resources/backend/ with our copy
  3. Update resources/backend/ with new upstream code
  4. Review each extension in patches/extensions/ for compatibility
  5. Update CURRENT_VERSION below
  6. Add entry to VERSION_HISTORY
  7. Test all functionality

Key upstream changes to watch for:
  - app_factory.py: Route registration, middleware order, auth changes
  - handlers/video_generation_handler.py: Our monkey-patch target
  - handlers/ic_lora_handler.py: Our monkey-patch target
  - handlers/health_handler.py: Our monkey-patch target
  - services/gpu_info/gpu_info_impl.py: Our monkey-patch target
  - services/retake_pipeline/ltx_retake_pipeline.py: Our monkey-patch target
  - api_types.py: Request/response model changes
  - _routes/: New/removed/changed API routes
"""

CURRENT_VERSION = "v1.0.2"
LATEST_KNOWN_VERSION = "v1.0.4"
UPSTREAM_REPO = "https://github.com/Lightricks/LTX-Desktop"

VERSION_HISTORY = [
    {
        "version": "v1.0.2",
        "date": "2026-03-12",
        "base_commit": "f7d205b",
        "notes": "Initial base version. Our patches were developed against this release.",
    },
    {
        "version": "v1.0.3",
        "date": "2026-04-02",
        "base_commit": "1d2acd2",
        "notes": (
            "Major changes: Model layer streaming (VRAM ~26GB -> ~11GB), "
            "Video Editor improvements, OpenAPI contract, removed Playground screen, "
            "coding agent Skill integration. NOT YET MERGED into our backend."
        ),
        "merged": False,
    },
    {
        "version": "v1.0.4",
        "date": "2026-04-03",
        "base_commit": "81f7f45",
        "notes": "Bug fixes on top of v1.0.3. NOT YET MERGED.",
        "merged": False,
    },
]

EXTENSION_UPSTREAM_DEPENDENCIES = {
    "request_model": ["api_types.GenerateVideoRequest"],
    "video_gen_patch": [
        "handlers.video_generation_handler.VideoGenerationHandler",
        "api_types.GenerateVideoRequest",
        "services.fast_video_pipeline.ltx_fast_video_pipeline.LTXFastVideoPipeline",
        "ltx_pipelines.utils.args.ImageConditioningInput",
    ],
    "retake_patch": [
        "services.retake_pipeline.ltx_retake_pipeline.LTXRetakePipeline",
        "ltx_pipelines.utils.media_io.get_videostream_metadata",
        "ltx_pipelines.utils.samplers.euler_denoising_loop",
        "ltx_pipelines.utils.helpers.noise_video_state",
    ],
    "ic_lora_patch": [
        "handlers.ic_lora_handler.IcLoraHandler",
        "runtime_config.model_download_specs.resolve_model_path",
        "state.app_state_types.PoseResources",
    ],
    "health_patch": [
        "handlers.health_handler.HealthHandler",
        "services.gpu_info.gpu_info_impl.GpuInfoImpl",
    ],
    "output_config": ["app_factory.py /outputs mount"],
    "low_vram_hooks": ["handlers.pipelines_handler.PipelinesHandler"],
    "lora_hooks": ["lora_build_hook (YunJi custom, not upstream)"],
    "custom_patches_ext": ["custom_patches (YunJi custom, not upstream)"],
    "system_api": ["handler.generation", "handler.pipelines", "handler.config"],
    "queue_api": ["handler.generation", "handler.video_generation", "handler.image_generation", "handler.ic_lora"],
    "history_api": ["None (purely YunJi)"],
    "tts_api": ["None (uses VoxCPM2, independent)"],
    "lora_api": ["handler.pipelines.models_dir"],
    "model_api": ["handler.pipelines.models_dir"],
    "file_api": ["None (purely YunJi)"],
    "batch_api": ["handler.video_generation"],
    "env_check": ["None (purely YunJi)"],
    "community_models": ["handler.pipelines.models_dir", "huggingface_hub"],
    "windows_fixes": ["None (platform-specific)"],
}
