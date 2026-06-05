"""Batch generation and VRAM limit API endpoints (YunJi custom).

Endpoints:
  POST /api/generate-batch - Generate multiple video segments and merge
  POST /api/vram-limit     - Save VRAM limit preference
  GET  /api/vram-limit     - Get VRAM limit preference
  POST /api/system/upscale-video - Upscale video (currently disabled)

Upstream dependency: handler.video_generation, handler.image_generation
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from api_types import GenerateVideoRequest, GenerateVideoResponse
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from server_utils.media_validation import normalize_optional_path

from extensions._context import ExtensionContext
from extensions._utils import ffmpeg_concat_copy, ffmpeg_mux_background_audio, find_ffmpeg_binary


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    @app.post("/api/system/upscale-video")
    async def route_upscale_video(request: Request):
        return JSONResponse(status_code=410, content={"error": "视频增强功能已移除：LTX 当前实现不是真正的保真超分。"})

    @app.post("/api/vram-limit")
    async def route_save_vram_limit(request: Request):
        try:
            body = await request.json()
            limit = body.get("vramLimit", "")
            limit_text = str(limit).strip()
            settings_file = ctx.config_dir / "settings.json"
            if settings_file.exists():
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {}
            data["vram_limit"] = limit_text
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            try:
                numeric_limit = float(limit_text) if limit_text else 0.0
            except (TypeError, ValueError):
                numeric_limit = 0.0
            if numeric_limit <= 0:
                from low_vram_runtime import restore_full_vram_config_tweaks, write_low_vram_pref
                ctx.handler.pipelines.low_vram_mode = False
                write_low_vram_pref(False)
                restore_full_vram_config_tweaks(ctx.handler)
            return {
                "status": "ok", "vramLimit": limit_text,
                "lowVramMode": bool(getattr(ctx.handler.pipelines, "low_vram_mode", False)),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @app.get("/api/vram-limit")
    async def route_get_vram_limit():
        try:
            settings_file = ctx.config_dir / "settings.json"
            if settings_file.exists():
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {"vramLimit": data.get("vram_limit", "0") or "0"}
            return {"vramLimit": "0"}
        except Exception as e:
            return {"vramLimit": "0", "error": str(e)}

    @app.post("/api/generate-batch")
    async def route_generate_batch(request: Request):
        from starlette.concurrency import run_in_threadpool
        try:
            data = await request.json()
            result = await run_in_threadpool(execute_batch, data, ctx)
            return result
        except Exception as e:
            import traceback
            traceback.print_exc()
            return JSONResponse(status_code=500, content={"error": str(e)})


def execute_batch(data: dict, ctx: ExtensionContext) -> GenerateVideoResponse | JSONResponse:
    from starlette.concurrency import run_in_threadpool
    import time

    segments_in = data.get("segments") or []
    if not segments_in:
        return JSONResponse(status_code=400, content={"error": "segments 不能为空"})

    resolution = data.get("resolution") or "720p"
    aspect_ratio = data.get("aspectRatio") or "16:9"
    neg = data.get("negativePrompt", "low quality, blurry, noisy, static noise, distorted")
    try:
        replay_seed = int(data.get("seed")) if data.get("seed") is not None else None
    except (TypeError, ValueError):
        replay_seed = None
    model = data.get("model") or "ltx-2"
    fps = str(data.get("fps") or "24")
    audio = str(data.get("audio") or "false").lower()
    camera_motion = data.get("cameraMotion") or "static"
    modelPath = data.get("modelPath")
    loraPath = data.get("loraPath")
    loraStrength = float(data.get("loraStrength") or 1.0)
    loraPaths = data.get("loraPaths")
    loraStrengths = data.get("loraStrengths")

    vg = getattr(ctx.handler, "video_generation", None)
    if vg is None or not callable(getattr(vg, "generate", None)):
        return JSONResponse(status_code=500, content={"error": "内部错误：找不到 video_generation 处理器"})

    clip_paths: list[str] = []
    for idx, seg in enumerate(segments_in):
        start_raw = seg.get("startImage") or seg.get("startFramePath")
        end_raw = seg.get("endImage") or seg.get("endFramePath")
        start_p = normalize_optional_path(start_raw)
        end_p = normalize_optional_path(end_raw)
        if not start_p or not os.path.isfile(start_p):
            return JSONResponse(status_code=400, content={"error": f"片段 {idx + 1} 起始图路径无效"})
        if not end_p or not os.path.isfile(end_p):
            return JSONResponse(status_code=400, content={"error": f"片段 {idx + 1} 结束图路径无效"})
        dur = seg.get("duration", 5)
        try:
            dur_i = int(float(dur))
        except (TypeError, ValueError):
            dur_i = 5
        dur_i = max(1, min(60, dur_i))
        prompt_text = (seg.get("prompt") or "").strip()
        if not prompt_text:
            prompt_text = "cinematic transition"

        req = GenerateVideoRequest(
            prompt=prompt_text, resolution=resolution, model=model,
            cameraMotion=camera_motion, negativePrompt=neg,
            duration=str(dur_i), fps=fps, audio=audio,
            imagePath=None, audioPath=None,
            startFramePath=start_p, endFramePath=end_p,
            aspectRatio=aspect_ratio,
            customWidth=data.get("customWidth"), customHeight=data.get("customHeight"),
            modelPath=modelPath, loraPath=loraPath, loraStrength=loraStrength,
            loraPaths=loraPaths, loraStrengths=loraStrengths,
            seed=(replay_seed + idx if replay_seed else None),
        )

        def _one_gen(r: GenerateVideoRequest = req):
            return vg.generate(r)

        resp = _one_gen()
        if resp.status != "complete" or not resp.video_path:
            return JSONResponse(status_code=500, content={"error": f"片段 {idx + 1} 生成失败: status={getattr(resp, 'status', None)}"})
        clip_paths.append(str(resp.video_path))

    if len(clip_paths) == 1:
        final_path = clip_paths[0]
    else:
        ff = find_ffmpeg_binary(ctx)
        if not ff:
            return JSONResponse(status_code=500, content={
                "error": "已生成多段视频，但未找到 ffmpeg，无法拼接。可选：① 安装 ffmpeg 并加入系统 PATH；② 设置环境变量 LTX_FFMPEG_PATH；③ 在 %LOCALAPPDATA%\\LTXDesktop\\ffmpeg_path.txt 写入路径。",
                "segment_paths": clip_paths,
            })
        out_dir = ctx.get_output_path()
        final_path = str(out_dir / f"batch_merged_{uuid.uuid4().hex[:10]}.mp4")
        try:
            ffmpeg_concat_copy(clip_paths, final_path, ff, ctx)
        except Exception as ex:
            return JSONResponse(status_code=500, content={"error": str(ex), "segment_paths": clip_paths})

    bg_audio = normalize_optional_path(data.get("backgroundAudioPath") or data.get("batchBackgroundAudioPath"))
    if bg_audio and os.path.isfile(bg_audio):
        ff_mux = find_ffmpeg_binary(ctx)
        if not ff_mux:
            return JSONResponse(status_code=500, content={
                "error": "已生成视频，但混入配乐需要 ffmpeg，请配置 LTX_FFMPEG_PATH 或 ffmpeg_path.txt",
                "video_path": final_path,
            })
        out_mux = str(ctx.get_output_path() / f"batch_with_audio_{uuid.uuid4().hex[:10]}.mp4")
        try:
            ffmpeg_mux_background_audio(ff_mux, final_path, bg_audio, out_mux)
            final_path = out_mux
        except Exception as ex:
            return JSONResponse(status_code=500, content={"error": str(ex), "video_path": final_path})

    return GenerateVideoResponse(status="complete", video_path=final_path)
