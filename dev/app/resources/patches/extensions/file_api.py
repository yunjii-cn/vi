"""File serving, upload, and thumbnail API endpoints (YunJi custom).

Endpoints:
  GET  /api/system/file           - Serve a file with Range request support
  GET  /api/system/video-thumbnail - Generate video thumbnail
  POST /api/system/upload-image    - Upload an image

Upstream dependency: None (purely YunJi)
"""

from __future__ import annotations

import asyncio
import base64
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

    @app.get("/api/system/video-thumbnail")
    async def route_video_thumbnail(path: str):
        try:
            video_path = Path(path)
            if not video_path.is_absolute():
                video_path = ctx.get_output_path() / video_path.name
            if not video_path.exists() or not video_path.is_file():
                return JSONResponse(status_code=404, content={"error": "Video not found"})
            import cv2

            def frame_is_black(frame) -> bool:
                if frame is None:
                    return True
                h, w = frame.shape[:2]
                if h <= 0 or w <= 0:
                    return True
                y0, y1 = max(0, h // 4), min(h, (h * 3) // 4)
                x0, x1 = max(0, w // 4), min(w, (w * 3) // 4)
                sample = frame[y0:y1, x0:x1] if y1 > y0 and x1 > x0 else frame
                return float(sample.mean()) < 4.0

            selected = None
            for attempt in range(3):
                cap = cv2.VideoCapture(str(video_path))
                if not cap.isOpened():
                    cap.release()
                    time.sleep(0.2 + attempt * 0.3)
                    continue
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                candidates = []
                if frame_count > 1:
                    candidates.extend([max(1, int(frame_count * 0.2)), max(1, int(frame_count * 0.5)), max(1, int(frame_count * 0.8))])
                candidates.extend([0, 1, 3, 8, 15])
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
                time.sleep(0.2 + attempt * 0.3)

            if selected is None:
                return JSONResponse(status_code=425, content={"error": "No non-black frame yet"})
            h, w = selected.shape[:2]
            if w > 360:
                scale = 360.0 / float(w)
                selected = cv2.resize(selected, (360, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(".jpg", selected, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
            if not ok:
                return JSONResponse(status_code=500, content={"error": "Thumbnail encode failed"})
            return StarletteResponse(content=encoded.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-cache"})
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

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
