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
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from extensions._context import ExtensionContext


# ★ 2026-06-16 v3: 服务端文件列表缓存(纯 stat,不含 replay 内容)
#   理由: 翻页时同一个目录被扫 N 次,纯 stat 也得 50-100ms(目录里有几百个文件)
#   做法: 缓存 (dir_mtime, dir_size) → meta_list,目录变更自动失效
#   缓存时间: 5 分钟(用户可能删除/新增文件,太久不刷新会错过)
_HISTORY_META_CACHE: dict = {
    "dir_mtime": 0.0,
    "dir_size": 0,
    "meta": None,
    "cached_at": 0.0,
}
_HISTORY_META_TTL = 300.0  # 5 分钟
_HISTORY_PERSIST_FILE = "_history_meta_cache.json"  # 进程重启后命中


def _load_persistent_cache(output_path: Path):
    """从 _history_meta_cache.json 加载历史列表,冷启动也能秒开。"""
    try:
        cache_file = output_path.parent / _HISTORY_PERSIST_FILE
        if not cache_file.is_file():
            return None
        import json as _json
        data = _json.loads(cache_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def _save_persistent_cache(output_path: Path):
    """持久化缓存到磁盘,下次冷启动直接读。"""
    try:
        if _HISTORY_META_CACHE["meta"] is None:
            return
        import json as _json
        cache_file = output_path.parent / _HISTORY_PERSIST_FILE
        payload = {
            "dir_mtime": _HISTORY_META_CACHE["dir_mtime"],
            "dir_size": _HISTORY_META_CACHE["dir_size"],
            "meta": _HISTORY_META_CACHE["meta"],
        }
        cache_file.write_text(_json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


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
            "result": data.get("result"),
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

        is_upscale = mode == "upscale" or endpoint in ("/api/upscale/image", "/api/upscale/video")
        if is_upscale:
            info["gen_method"] = "高清放大"
            result_data = replay.get("result") or {}
            orig_size = result_data.get("original_size") or ""
            out_size = result_data.get("output_size") or ""
            if payload.get("keepOriginalRatio"):
                info["upscale_mode"] = "原始比例"
            elif payload.get("targetWidth") or payload.get("targetHeight"):
                info["upscale_mode"] = "目标分辨率"
            else:
                info["upscale_mode"] = "按倍数"
            if payload.get("scale"):
                info["upscale_scale"] = payload["scale"]
            if orig_size and out_size:
                try:
                    ow, oh = orig_size.split("x")
                    ow, oh = int(ow), int(oh)
                    rw, rh = out_size.split("x")
                    rw, rh = int(rw), int(rh)
                    actual_scale_w = round(rw / ow, 1) if ow > 0 else 0
                    actual_scale_h = round(rh / oh, 1) if oh > 0 else 0
                    if actual_scale_w == actual_scale_h and actual_scale_w == int(actual_scale_w):
                        info["upscale_actual_scale"] = int(actual_scale_w)
                    else:
                        info["upscale_actual_scale"] = actual_scale_w
                except Exception:
                    pass
            if payload.get("engine") == "ltx_fast":
                info["upscale_model"] = "LTX快速放大"
            elif payload.get("model"):
                info["upscale_model"] = payload["model"]
            if payload.get("engine"):
                info["upscale_engine"] = payload["engine"]
            if payload.get("resizeMode"):
                info["upscale_resize_mode"] = payload["resizeMode"]
            if payload.get("keepOriginalRatio"):
                info["upscale_keep_ratio"] = True
            if orig_size:
                info["upscale_original_size"] = orig_size
            if out_size:
                info["upscale_output_size"] = out_size
            if result_data.get("resize_mode"):
                info["upscale_resize_mode"] = result_data["resize_mode"]
            if result_data.get("width") and result_data.get("height"):
                info["width"] = result_data["width"]
                info["height"] = result_data["height"]
            if result_data.get("frames"):
                info["upscale_frames"] = result_data["frames"]
            if result_data.get("elapsed"):
                info["upscale_elapsed"] = result_data["elapsed"]
            if result_data.get("original_fps"):
                info["upscale_original_fps"] = result_data["original_fps"]
            if result_data.get("output_fps"):
                info["upscale_output_fps"] = result_data["output_fps"]
            if result_data.get("duration"):
                info["upscale_duration"] = result_data["duration"]

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
            dyn_path = ctx.get_output_path()
            if not dyn_path.exists():
                return {
                    "status": "success", "history": [],
                    "total_pages": 0, "current_page": page, "total_items": 0,
                }
            # ★ 2026-06-16 v3 优化 1/3: 三级缓存(内存 + 磁盘 + 目录 stat)
            #   L1 内存:进程内命中,5分钟内翻页零延迟
            #   L2 磁盘:进程重启后,从 _history_meta_cache.json 命中(用户开新会话秒开)
            #   L3 目录:不命中才扫,扫完自动写回 L2
            #   失效条件:目录 mtime/size 变化 OR 缓存超过 5min OR delete-file
            try:
                dir_st = dyn_path.stat()
                dir_mtime = dir_st.st_mtime
                dir_size = dir_st.st_size
            except OSError:
                dir_mtime = 0.0
                dir_size = 0
            # L1: 内存缓存
            l1_hit = (
                _HISTORY_META_CACHE["meta"] is not None
                and _HISTORY_META_CACHE["dir_mtime"] == dir_mtime
                and _HISTORY_META_CACHE["dir_size"] == dir_size
                and (time.time() - _HISTORY_META_CACHE["cached_at"]) < _HISTORY_META_TTL
            )
            if l1_hit:
                meta = _HISTORY_META_CACHE["meta"]
            else:
                # L2: 磁盘缓存(冷启动加速)
                l2_data = _load_persistent_cache(dyn_path)
                if (
                    l2_data is not None
                    and l2_data.get("dir_mtime") == dir_mtime
                    and l2_data.get("dir_size") == dir_size
                    and isinstance(l2_data.get("meta"), list)
                ):
                    meta = l2_data["meta"]
                    # 同步到 L1
                    _HISTORY_META_CACHE["dir_mtime"] = dir_mtime
                    _HISTORY_META_CACHE["dir_size"] = dir_size
                    _HISTORY_META_CACHE["meta"] = meta
                    _HISTORY_META_CACHE["cached_at"] = time.time()
                else:
                    # L3: 真实扫描(只有这条路径会触盘 stat)
                    meta = []
                    for entry in os.scandir(dyn_path):
                        filename = entry.name
                        if filename == "uploads" or filename == _HISTORY_PERSIST_FILE:
                            continue
                        lower_name = filename.lower()
                        if lower_name.startswith("_") or lower_name.startswith("tmp"):
                            continue
                        if lower_name.endswith(".replay.json"):
                            continue
                        if not entry.is_file():
                            continue
                        if not lower_name.endswith(
                            (".mp4", ".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac")
                        ):
                            continue
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
                        full_path = Path(entry.path)
                        meta.append({
                            "filename": filename, "type": item_type, "mtime": mtime,
                            "size": size, "fullpath": str(full_path),
                        })
                    # 按 mtime 倒序
                    meta.sort(key=lambda x: x["mtime"], reverse=True)
                    # 写 L1 + L2
                    _HISTORY_META_CACHE["dir_mtime"] = dir_mtime
                    _HISTORY_META_CACHE["dir_size"] = dir_size
                    _HISTORY_META_CACHE["meta"] = meta
                    _HISTORY_META_CACHE["cached_at"] = time.time()
                    _save_persistent_cache(dyn_path)
            total_items = len(meta)
            total_pages = (total_items + limit - 1) // limit if total_items > 0 else 0
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            page_meta = meta[start_idx:end_idx]
            # ★ 只为切片里的 N 个项目读 replay + 抽 gen_info
            history = []
            for item in page_meta:
                full_path = Path(item["fullpath"])
                replay = _read_replay_sidecar(full_path)
                gen_info = _extract_gen_info(replay, item["type"])
                history.append({
                    "filename": item["filename"], "type": item["type"], "mtime": item["mtime"],
                    "size": item["size"], "fullpath": item["fullpath"],
                    "replay": replay, "replay_available": replay is not None,
                    "gen_info": gen_info,
                })

            # ★ 2026-06-16 v4 提速: ETag + Last-Modified 协商
            #   ETag 基于 (dir_mtime, dir_size, total_items, page, limit) 算
            #   目录没变(没有新增/删除) + 翻页参数一致 → 浏览器拿到 304,0 字节响应
            #   用户感受: F5 刷新历史列表从 1-2 秒变成 < 50ms(网络)
            #   关键: 不影响生成后的"新增文件" — 一旦 dir_mtime 变化,ETag 立刻变 → 200 响应
            import hashlib as _hashlib
            etag_src = f"{dir_mtime:.3f}|{dir_size}|{total_items}|{page}|{limit}"
            etag = 'W/"' + _hashlib.md5(etag_src.encode("utf-8")).hexdigest()[:16] + '"'
            try:
                from datetime import datetime as _dt, timezone as _tz
                last_modified = _dt.fromtimestamp(dir_mtime, tz=_tz.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
            except Exception:
                last_modified = ""
            if_none_match = request.headers.get("if-none-match", "")
            if_modified_since = request.headers.get("if-modified-since", "")
            if (if_none_match and etag in if_none_match) or \
               (if_modified_since and last_modified and if_modified_since == last_modified):
                # 客户端缓存仍然新鲜 → 304 + ETag/Last-Modified,响应体为空
                from fastapi import Response as _Response
                return _Response(
                    status_code=304,
                    headers={"ETag": etag, "Last-Modified": last_modified,
                             "Cache-Control": "no-cache, must-revalidate"},
                )

            return {
                "status": "success", "history": history,
                "total_pages": total_pages, "current_page": page, "total_items": total_items,
            }
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
                # ★ 2026-06-16 v3: 删除文件后清掉元信息缓存,下次请求立即重新扫描
                _HISTORY_META_CACHE["cached_at"] = 0.0
                return {"status": "success", "message": "File deleted"}
            else:
                return JSONResponse(status_code=404, content={"error": "File not found"})
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
