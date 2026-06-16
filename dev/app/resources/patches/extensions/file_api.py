"""File serving, upload, and thumbnail API endpoints (YunJi custom).

Endpoints:
  GET  /api/system/file            - Serve a file with Range request support
  GET  /api/system/video-thumbnail - Generate (and cache) video thumbnail
  GET  /api/system/image-thumbnail - Generate (and cache) image thumbnail
  POST /api/system/upload-image    - Upload an image
  POST /api/system/upload-file     - Upload a file (multipart)

Upstream dependency: None (purely YunJi)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import Response as StarletteResponse, StreamingResponse

from extensions._context import ExtensionContext


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    # ★ 2026-06-17 提速 v3: 缩略图磁盘缓存
    #   原问题: cv2 每次重新解码整个视频,5 个视频 = 5-15 秒
    #   新方案: 生成一次存到 .thumbs/ 目录,后续直接 304/0 字节
    #   - 缓存键: 源文件 mtime+size + 宽度
    #   - 缓存目录: output_path/.thumbs/(避免污染 outputs 目录)
    #   - 源文件改动 → mtime 变 → 缓存键变 → 自动失效
    def _thumb_cache_dir() -> Path:
        d = ctx.get_output_path() / ".thumbs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _thumb_cache_path(source_path: Path, width: int):
        try:
            st = source_path.stat()
        except FileNotFoundError:
            return None
        path_hash = hashlib.md5(str(source_path).encode("utf-8")).hexdigest()[:16]
        fname = f"{path_hash}_{st.st_mtime_ns:x}_{st.st_size:x}_w{width}.jpg"
        return _thumb_cache_dir() / fname

    def _read_cached_thumb(cache_path):
        """尝试读磁盘缓存,失败返回 None。"""
        if not cache_path or not cache_path.is_file():
            return None
        try:
            with cache_path.open("rb") as f:
                return f.read()
        except OSError:
            return None

    def _write_cached_thumb(cache_path, data: bytes):
        """写磁盘缓存(失败不影响主流程)。"""
        if not cache_path:
            return
        try:
            tmp = cache_path.with_suffix(".tmp")
            with tmp.open("wb") as f:
                f.write(data)
            tmp.replace(cache_path)
        except OSError:
            pass

    def _make_thumb_etag(source_path: Path, width: int):
        try:
            st = source_path.stat()
            return f'W/"{st.st_mtime_ns:x}-{st.st_size:x}-w{width}"'
        except FileNotFoundError:
            return None

    # ──────────────────────────────────────────────
    # 文件直传(支持 Range)
    # ──────────────────────────────────────────────
    @app.get("/api/system/file")
    async def route_serve_file(path: str = Query(...), request: Request = None):
        if not os.path.exists(path):
            return JSONResponse(status_code=404, content={"error": "File not found"})
        file_size = os.path.getsize(path)
        range_header = request.headers.get("range", "") if request else ""
        mime_type, _ = mimetypes.guess_type(path)
        if mime_type is None:
            mime_type = "application/octet-stream"
        if range_header.startswith("bytes="):
            match = re.match(r'^bytes=(\d+)-(\d*)$', range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
                end = min(end, file_size - 1)
                content_length = end - start + 1

                async def file_iterator():
                    with open(path, "rb") as f:
                        f.seek(start)
                        remaining = content_length
                        while remaining > 0:
                            chunk_size = min(65536, remaining)
                            data = await asyncio.get_running_loop().run_in_executor(None, f.read, chunk_size)
                            if not data:
                                break
                            remaining -= len(data)
                            yield data

                return StreamingResponse(
                    file_iterator(), status_code=206, media_type=mime_type,
                    headers={
                        "content-range": f"bytes {start}-{end}/{file_size}",
                        "content-length": str(content_length), "accept-ranges": "bytes",
                        "cache-control": "no-cache",
                    },
                )
        resp = FileResponse(path, media_type=mime_type)
        resp.headers["accept-ranges"] = "bytes"
        resp.headers["cache-control"] = "no-cache"
        return resp

    # ──────────────────────────────────────────────
    # 视频缩略图 — 磁盘缓存 + ETag/304 + 异步
    # ──────────────────────────────────────────────
    @app.get("/api/system/video-thumbnail")
    async def route_video_thumbnail(path: str, w: int = 360, request: Request = None):
        """生成视频缩略图(带磁盘缓存 + ETag/304)。

        提速 v3:
          1) 先查磁盘缓存 .thumbs/{hash}_{mtime}_{size}_w{width}.jpg
          2) 命中 + ETag 304 → 0 字节响应
          3) 命中 + 无 304 → 直接返回缓存的 JPEG(无 cv2 调用)
          4) 未命中 → cv2 解码(线程池) + 写缓存,后续走 1-3 路径
        """
        try:
            video_path = Path(path)
            if not video_path.is_absolute():
                video_path = ctx.get_output_path() / video_path.name
            if not video_path.exists() or not video_path.is_file():
                return JSONResponse(status_code=404, content={"error": "Video not found"})
            if w <= 0 or w > 1920:
                w = 360

            etag = _make_thumb_etag(video_path, w)
            cache_path = _thumb_cache_path(video_path, w)

            # ① ETag 304 短路
            if etag and request:
                if_none_match = request.headers.get("if-none-match", "")
                if etag in if_none_match and cache_path and cache_path.is_file():
                    return StarletteResponse(
                        status_code=304,
                        headers={
                            "ETag": etag,
                            "Cache-Control": "public, max-age=86400",
                        },
                    )

            # ② 磁盘缓存命中(无 cv2,毫秒级)
            cached = _read_cached_thumb(cache_path) if cache_path else None
            if cached is not None:
                return StarletteResponse(
                    content=cached, media_type="image/jpeg",
                    headers={
                        "ETag": etag or "",
                        "Cache-Control": "public, max-age=86400",
                        "X-Thumb-Cache": "HIT",
                    },
                )

            # ③ 磁盘未命中,跑 cv2(放线程池,不阻塞 event loop)
            import cv2

            def _generate():
                def frame_is_black(frame) -> bool:
                    if frame is None:
                        return True
                    h, w_frame = frame.shape[:2]
                    if h <= 0 or w_frame <= 0:
                        return True
                    y0, y1 = max(0, h // 4), min(h, (h * 3) // 4)
                    x0, x1 = max(0, w_frame // 4), min(w_frame, (w_frame * 3) // 4)
                    sample = frame[y0:y1, x0:x1] if y1 > y0 and x1 > x0 else frame
                    return float(sample.mean()) < 4.0

                selected = None
                for attempt in range(2):  # 由 3 次降为 2 次
                    cap = cv2.VideoCapture(str(video_path))
                    if not cap.isOpened():
                        cap.release()
                        if attempt == 0:
                            time.sleep(0.15)
                        continue
                    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                    candidates = []
                    if frame_count > 1:
                        # 直接取中段,跳过 0.2/0.8 两端(降低找帧次数)
                        candidates.append(max(1, int(frame_count * 0.5)))
                    candidates.extend([1, 3, 8])
                    for frame_idx in dict.fromkeys(candidates):
                        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(frame_idx)))
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            continue
                        if not frame_is_black(frame):
                            selected = frame
                            break
                    cap.release()
                    if selected is not None:
                        break
                if selected is None:
                    return None
                h, fw = selected.shape[:2]
                if fw > w:
                    scale = float(w) / float(fw)
                    selected = cv2.resize(selected, (w, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
                ok, encoded = cv2.imencode(".jpg", selected, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
                if not ok:
                    return None
                return encoded.tobytes()

            jpg_bytes = await asyncio.get_running_loop().run_in_executor(None, _generate)
            if jpg_bytes is None:
                return JSONResponse(status_code=425, content={"error": "No non-black frame yet"})

            # ④ 写磁盘缓存
            _write_cached_thumb(cache_path, jpg_bytes)

            return StarletteResponse(
                content=jpg_bytes, media_type="image/jpeg",
                headers={
                    "ETag": etag or "",
                    "Cache-Control": "public, max-age=86400",
                    "X-Thumb-Cache": "MISS",
                },
            )
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    # ──────────────────────────────────────────────
    # 图片缩略图 — 磁盘缓存 + ETag/304 + 异步(新增)
    # ──────────────────────────────────────────────
    @app.get("/api/system/image-thumbnail")
    async def route_image_thumbnail(path: str, w: int = 360, request: Request = None):
        """生成图片缩略图(避免浏览器拉 5-20MB 原图)。"""
        try:
            img_path = Path(path)
            if not img_path.is_absolute():
                img_path = ctx.get_output_path() / img_path.name
            if not img_path.exists() or not img_path.is_file():
                return JSONResponse(status_code=404, content={"error": "Image not found"})
            if w <= 0 or w > 1920:
                w = 360

            etag = _make_thumb_etag(img_path, w)
            cache_path = _thumb_cache_path(img_path, w)

            # ETag 304 短路
            if etag and request:
                if_none_match = request.headers.get("if-none-match", "")
                if etag in if_none_match and cache_path and cache_path.is_file():
                    return StarletteResponse(
                        status_code=304,
                        headers={
                            "ETag": etag,
                            "Cache-Control": "public, max-age=86400",
                        },
                    )

            # 磁盘缓存命中
            cached = _read_cached_thumb(cache_path) if cache_path else None
            if cached is not None:
                return StarletteResponse(
                    content=cached, media_type="image/jpeg",
                    headers={
                        "ETag": etag or "",
                        "Cache-Control": "public, max-age=86400",
                        "X-Thumb-Cache": "HIT",
                    },
                )

            # 未命中,跑 cv2
            import cv2

            def _generate():
                img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
                if img is None:
                    return None
                h, fw = img.shape[:2]
                if fw > w:
                    scale = float(w) / float(fw)
                    img = cv2.resize(img, (w, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
                ok, encoded = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
                if not ok:
                    return None
                return encoded.tobytes()

            jpg_bytes = await asyncio.get_running_loop().run_in_executor(None, _generate)
            if jpg_bytes is None:
                return JSONResponse(status_code=500, content={"error": "Image decode failed"})

            _write_cached_thumb(cache_path, jpg_bytes)

            return StarletteResponse(
                content=jpg_bytes, media_type="image/jpeg",
                headers={
                    "ETag": etag or "",
                    "Cache-Control": "public, max-age=86400",
                    "X-Thumb-Cache": "MISS",
                },
            )
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    # ──────────────────────────────────────────────
    # 上传
    # ──────────────────────────────────────────────
    @app.post("/api/system/upload-image")
    async def route_upload_image(request: Request):
        try:
            data = await request.json()
            b64_data = data.get("image")
            filename = data.get("filename", "image.png")
            if not b64_data:
                return JSONResponse(status_code=400, content={"error": "No image data provided"})
            if "," in b64_data:
                b64_data = b64_data.split(",")[1]
            image_bytes = base64.b64decode(b64_data)
            upload_dir = ctx.get_output_path() / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)
            safe_filename = "".join([c for c in filename if c.isalnum() or c in "._-"])
            file_path = upload_dir / f"up_{uuid.uuid4().hex[:6]}_{safe_filename}"
            with file_path.open("wb") as buffer:
                buffer.write(image_bytes)
            return {"status": "success", "path": str(file_path)}
        except Exception as e:
            import traceback
            error_msg = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
            print(f"Upload error: {error_msg}")
            return JSONResponse(status_code=500, content={"error": str(e), "detail": error_msg})

    @app.post("/api/system/upload-file")
    async def route_upload_file(request: Request):
        try:
            form = await request.form()
            upload_file = form.get("file")
            if not upload_file:
                return JSONResponse(status_code=400, content={"error": "No file provided"})
            upload_dir = ctx.get_output_path() / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)
            filename = upload_file.filename or "upload.bin"
            safe_filename = "".join([c for c in filename if c.isalnum() or c in "._-"])
            if not safe_filename or safe_filename.startswith("."):
                safe_filename = f"upload_{uuid.uuid4().hex[:6]}{safe_filename}"
            file_path = upload_dir / f"up_{uuid.uuid4().hex[:6]}_{safe_filename}"
            content = await upload_file.read()
            with file_path.open("wb") as buffer:
                buffer.write(content)
            return {"status": "success", "path": str(file_path)}
        except Exception as e:
            import traceback
            error_msg = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
            print(f"Upload file error: {error_msg}")
            return JSONResponse(status_code=500, content={"error": str(e), "detail": error_msg})
