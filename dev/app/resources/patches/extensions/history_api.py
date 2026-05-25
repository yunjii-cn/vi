"""History and replay API endpoints (YunJi custom).

Endpoints:
  GET  /api/system/history     - List generated assets with pagination
  GET  /api/system/replay      - Get replay metadata for a specific asset
  POST /api/system/delete-file - Delete a generated asset

Upstream dependency: None (purely YunJi)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from extensions._context import ExtensionContext


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    def _read_replay_sidecar(media_path: Path) -> dict | None:
        replay_path = media_path.with_suffix(media_path.suffix + ".replay.json")
        if not replay_path.exists() or not replay_path.is_file():
            return None
        try:
            data = json.loads(replay_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        payload = data.get("payload")
        if not isinstance(payload, dict):
            return None
        return {
            "version": data.get("version", 1), "mode": data.get("mode"),
            "endpoint": data.get("endpoint"), "label": data.get("label"),
            "payload": payload, "task_id": data.get("task_id"),
            "created_at": data.get("created_at"),
            "finished_at": data.get("finished_at"),
        }

    def _extract_gen_info(replay: dict | None, item_type: str) -> dict:
        info: dict = {}
        if not replay:
            return info
        payload = replay.get("payload") or {}
        mode = replay.get("mode") or replay.get("label", "")
        endpoint = replay.get("endpoint") or ""

        if payload.get("prompt"):
            info["prompt"] = payload["prompt"]
        if payload.get("seed"):
            info["seed"] = payload["seed"]

        w = payload.get("customWidth") or payload.get("width") or payload.get("image_width")
        h = payload.get("customHeight") or payload.get("height") or payload.get("image_height")
        if w and h:
            info["width"] = w
            info["height"] = h
        if payload.get("fps"):
            info["fps"] = payload["fps"]
        dur = payload.get("duration")
        if dur:
            info["duration"] = str(dur)
        if payload.get("aspectRatio"):
            info["aspect_ratio"] = payload["aspectRatio"]
        if payload.get("cameraMotion") and payload["cameraMotion"] != "none":
            info["camera_motion"] = payload["cameraMotion"]
        if payload.get("num_inference_steps") or payload.get("numSteps"):
            info["steps"] = payload.get("num_inference_steps") or payload.get("numSteps")
        elif endpoint == "/api/generate":
            if payload.get("audioPath"):
                info["steps"] = 11
            else:
                info["steps"] = 8
        if payload.get("guidance_scale") or payload.get("cfg_guidance_scale"):
            info["cfg"] = payload.get("guidance_scale") or payload.get("cfg_guidance_scale")
        elif endpoint == "/api/generate":
            info["cfg"] = 1.0

        lora_paths = payload.get("loraPaths") or []
        lora_strengths = payload.get("loraStrengths") or []
        if not lora_paths and payload.get("loraPath"):
            lora_paths = [payload["loraPath"]]
            lora_strengths = [payload.get("loraStrength", 1.0)]
        if lora_paths:
            lora_names = []
            lora_details = []
            for i, p in enumerate(lora_paths):
                name = Path(p).stem if isinstance(p, str) else str(p)
                s = lora_strengths[i] if i < len(lora_strengths) else 1.0
                lora_names.append(f"{name}({s})" if s != 1.0 else name)
                detail: dict = {"name": name, "strength": s}
                if isinstance(p, str) and p.strip():
                    detail["path"] = p.strip()
                lora_details.append(detail)
            info["loras"] = lora_names
            info["lora_details"] = lora_details

        has_start = bool(payload.get("imagePath") or payload.get("startFramePath"))
        has_end = bool(payload.get("endFramePath"))
        has_keyframes = bool(payload.get("keyframePaths"))
        has_video_ref = bool(payload.get("video_path"))
        has_audio_ref = bool(payload.get("audioPath"))

        if endpoint == "/api/generate-image":
            info["gen_method"] = "图像生成"
        elif has_keyframes:
            info["gen_method"] = "智能多帧"
        elif has_start and has_end:
            info["gen_method"] = "首尾帧视频"
        elif has_start:
            info["gen_method"] = "图生视频"
        elif has_audio_ref:
            info["gen_method"] = "音频驱动视频"
        elif has_video_ref:
            info["gen_method"] = "视频迁移"
        elif endpoint == "/api/generate-batch":
            info["gen_method"] = "分段拼接"
        elif endpoint == "/api/generate":
            info["gen_method"] = "文生视频"
        elif endpoint == "/api/ic-lora/generate":
            info["gen_method"] = "视频迁移"

        created = replay.get("created_at")
        finished = replay.get("finished_at")
        if created and finished:
            try:
                ct = float(created) if isinstance(created, (int, float)) else None
                ft = float(finished) if isinstance(finished, (int, float)) else None
                if ct is None and isinstance(created, str):
                    from datetime import datetime as _dt
                    ct = _dt.fromisoformat(created).timestamp()
                if ft is None and isinstance(finished, str):
                    from datetime import datetime as _dt
                    ft = _dt.fromisoformat(finished).timestamp()
                if ct and ft and ft > ct:
                    elapsed = ft - ct
                    if elapsed >= 60:
                        info["elapsed"] = f"{int(elapsed // 60)}分{int(elapsed % 60)}秒"
                    else:
                        info["elapsed"] = f"{elapsed:.1f}秒"
            except Exception:
                pass

        return info

    @app.get("/api/system/history")
    async def route_get_history(request: Request):
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            limit = max(1, min(int(request.query_params.get("limit", 20)), 500))
            history = []
            dyn_path = ctx.get_output_path()
            if dyn_path.exists():
                for entry in os.scandir(dyn_path):
                    filename = entry.name
                    if filename == "uploads":
                        continue
                    full_path = Path(entry.path)
                    lower_name = filename.lower()
                    if lower_name.startswith("_") or lower_name.startswith("tmp"):
                        continue
                    if lower_name.endswith(".replay.json"):
                        continue
                    if entry.is_file() and lower_name.endswith(
                        (".mp4", ".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac")
                    ):
                        try:
                            st = entry.stat()
                            size = st.st_size
                            if size <= 0:
                                continue
                            if lower_name.endswith(".mp4") and size < 4096:
                                continue
                        except OSError:
                            continue
                        mtime = st.st_mtime
                        if lower_name.endswith(".mp4"):
                            item_type = "video"
                        elif lower_name.endswith((".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac")):
                            item_type = "audio"
                        else:
                            item_type = "image"
                        replay = _read_replay_sidecar(full_path)
                        gen_info = _extract_gen_info(replay, item_type)
                        history.append({
                            "filename": filename, "type": item_type, "mtime": mtime,
                            "size": size, "fullpath": str(full_path),
                            "replay": replay, "replay_available": replay is not None,
                            "gen_info": gen_info,
                        })
            history.sort(key=lambda x: x["mtime"], reverse=True)
            total_items = len(history)
            total_pages = (total_items + limit - 1) // limit
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            return {
                "status": "success", "history": history[start_idx:end_idx],
                "total_pages": total_pages, "current_page": page, "total_items": total_items,
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.get("/api/system/replay")
    async def route_get_replay(request: Request):
        try:
            raw_path = (request.query_params.get("path") or "").strip()
            filename = (request.query_params.get("filename") or "").strip()
            media_path: Path | None = None
            if raw_path:
                media_path = Path(raw_path)
            elif filename:
                media_path = ctx.get_output_path() / filename
            if media_path is None:
                return JSONResponse(status_code=400, content={"error": "Missing path"})
            if not media_path.is_absolute():
                media_path = ctx.get_output_path() / media_path.name
            replay = _read_replay_sidecar(media_path)
            if not replay:
                return {"status": "missing", "replay": None}
            return {"status": "success", "replay": replay}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.post("/api/system/delete-file")
    async def route_delete_file(request: Request):
        try:
            data = await request.json()
            filename = data.get("filename", "")
            if not filename:
                return JSONResponse(status_code=400, content={"error": "Filename is required"})
            dyn_path = ctx.get_output_path()
            file_path = dyn_path / filename
            if file_path.exists() and file_path.is_file():
                file_path.unlink()
                replay_path = file_path.with_suffix(file_path.suffix + ".replay.json")
                if replay_path.exists() and replay_path.is_file():
                    replay_path.unlink()
                return {"status": "success", "message": "File deleted"}
            else:
                return JSONResponse(status_code=404, content={"error": "File not found"})
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
