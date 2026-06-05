"""Retake pipeline monkey-patch for video upscaling.

Intercepts LTXRetakePipeline._run to support:
  - Target resolution upscaling (width x height)
  - Noise scale control (strength)
  - Partial denoising (skip early sigmas)

Upstream dependency: services.retake_pipeline.ltx_retake_pipeline.LTXRetakePipeline
                    ltx_pipelines.utils.media_io.get_videostream_metadata
                    ltx_pipelines.utils.samplers.euler_denoising_loop
                    ltx_pipelines.utils.helpers.noise_video_state
When upstream changes LTXRetakePipeline, review this patch.
"""

from __future__ import annotations

from extensions._context import ExtensionContext


def install(app, ctx: ExtensionContext) -> None:
    from services.retake_pipeline.ltx_retake_pipeline import LTXRetakePipeline

    _orig_ltx_retake_run = LTXRetakePipeline._run

    def patched_ltx_retake_run(self, video_path, prompt, start_time, end_time, seed, **kwargs):
        target_w = getattr(self, "_target_width", None)
        target_h = getattr(self, "_target_height", None)
        target_strength = getattr(self, "_target_strength", 0.7)
        is_upscale = target_w is not None and target_h is not None

        import ltx_pipelines.utils.media_io as media_io
        import services.retake_pipeline.ltx_retake_pipeline as lrp
        import ltx_pipelines.utils.samplers as samplers
        import ltx_pipelines.utils.helpers as helpers

        _orig_get_meta = media_io.get_videostream_metadata
        _orig_lrp_get_meta = getattr(lrp, "get_videostream_metadata", _orig_get_meta)
        _orig_euler_loop = samplers.euler_denoising_loop
        _orig_noise_video = helpers.noise_video_state

        fps, num_frames, src_w, src_h = _orig_get_meta(video_path)

        if is_upscale:
            print(f">>> 启动超分内核: {src_w}x{src_h} -> {target_w}x{target_h} (强度: {target_strength})")

            def get_meta_patched(path):
                return fps, num_frames, target_w, target_h

            media_io.get_videostream_metadata = get_meta_patched
            lrp.get_videostream_metadata = get_meta_patched

            def noise_video_patched(*args, **kwargs_inner):
                kwargs_inner["noise_scale"] = target_strength
                return _orig_noise_video(*args, **kwargs_inner)

            helpers.noise_video_state = noise_video_patched

            def patched_euler_loop(sigmas, video_state, audio_state, stepper, denoise_fn):
                full_len = len(sigmas)
                skip_idx = 0
                for i, s in enumerate(sigmas):
                    if s <= target_strength:
                        skip_idx = i
                        break
                skip_idx = min(skip_idx, full_len - 2)
                new_sigmas = sigmas[skip_idx:]
                print(f">>> 采样拦截成功: 原步数 {full_len}, 现步数 {len(new_sigmas)}, 起始强度 {new_sigmas[0].item():.2f}")
                return _orig_euler_loop(new_sigmas, video_state, audio_state, stepper, denoise_fn)

            samplers.euler_denoising_loop = patched_euler_loop
            kwargs["regenerate_video"] = False
            kwargs["regenerate_audio"] = False

            try:
                return _orig_ltx_retake_run(self, video_path, prompt, start_time, end_time, seed, **kwargs)
            finally:
                media_io.get_videostream_metadata = _orig_get_meta
                lrp.get_videostream_metadata = _orig_lrp_get_meta
                samplers.euler_denoising_loop = _orig_euler_loop
                helpers.noise_video_state = _orig_noise_video

        return _orig_ltx_retake_run(self, video_path, prompt, start_time, end_time, seed, **kwargs)

    LTXRetakePipeline._run = patched_ltx_retake_run
