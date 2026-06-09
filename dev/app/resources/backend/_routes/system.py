"""Route handler for /api/system/* - serves local media files and thumbnails."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response as StarletteResponse

def _dbg(msg):
    """Print debug message directly to stdout so the launcher can capture it."""
    print(f"[DEBUG][system] {msg}", flush=True)

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/file")
async def serve_file(request: Request, path: str):
    """Serve a local media file by its absolute path."""
    _dbg(f"[serve_file] Received path parameter: {path}")
    _dbg(f"[serve_file] Path type: {type(path)}")
    
    # Validate the path
    if not path:
        _dbg("[serve_file] ERROR: Path parameter is empty")
        raise HTTPException(status_code=400, detail="Path parameter is required")
    
    # Convert forward slashes to backslashes for Windows
    original_path = path
    if "/" in path:
        path = path.replace("/", "\\")
        _dbg(f"[serve_file] Converted path: {path}")
    
    # Check if file exists
    file_path = Path(path)
    _dbg(f"[serve_file] File exists: {file_path.exists()}")
    _dbg(f"[serve_file] Is file: {file_path.is_file()}")
    _dbg(f"[serve_file] Absolute path: {file_path.absolute()}")
    
    if not file_path.exists():
        _dbg(f"[serve_file] ERROR: File not found: {path}")
        # List parent directory contents for debugging
        parent = file_path.parent
        if parent.exists():
            _dbg(f"[serve_file] Parent directory exists: {parent}")
            try:
                files = list(parent.iterdir())
                _dbg(f"[serve_file] Files in parent dir: {[f.name for f in files[:10]]}")
            except Exception as e:
                _dbg(f"[serve_file] ERROR: Cannot list parent directory: {e}")
        else:
            _dbg(f"[serve_file] ERROR: Parent directory does not exist: {parent}")
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    
    if not file_path.is_file():
        _dbg(f"[serve_file] ERROR: Path is not a file: {path}")
        raise HTTPException(status_code=400, detail=f"Path is not a file: {path}")
    
    # Determine content type based on file extension
    ext = file_path.suffix.lower()
    content_type_map = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
    }
    media_type = content_type_map.get(ext, "application/octet-stream")
    file_size = file_path.stat().st_size
    
    _dbg(f"[serve_file] Serving file: {path}")
    _dbg(f"[serve_file] Content-Type: {media_type}")
    _dbg(f"[serve_file] File size: {file_size} bytes")
    
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=file_path.name,
    )


@router.get("/video-thumbnail")
async def video_thumbnail(path: str):
    """Generate and return a JPEG thumbnail from a video file."""
    _dbg(f"[video-thumbnail] Received path parameter: {path}")
    try:
        video_path = Path(path)
        _dbg(f"[video-thumbnail] Is absolute: {video_path.is_absolute()}")
        if not video_path.is_absolute():
            _dbg(f"[video-thumbnail] ERROR: Path must be absolute: {path}")
            raise HTTPException(status_code=400, detail="Path must be absolute")
        _dbg(f"[video-thumbnail] File exists: {video_path.exists()}")
        _dbg(f"[video-thumbnail] Is file: {video_path.is_file()}")
        if not video_path.exists() or not video_path.is_file():
            _dbg(f"[video-thumbnail] ERROR: Video not found: {path}")
            raise HTTPException(status_code=404, detail="Video not found")

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
                candidates.extend(
                    [
                        max(1, int(frame_count * 0.2)),
                        max(1, int(frame_count * 0.5)),
                        max(1, int(frame_count * 0.8)),
                    ]
                )
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
            _dbg(f"[video-thumbnail] WARN: No non-black frame yet for: {path}")
            return JSONResponse(status_code=425, content={"error": "No non-black frame yet"})

        _dbg(f"[video-thumbnail] Frame selected, size: {selected.shape}")
        h, w = selected.shape[:2]
        if w > 360:
            scale = 360.0 / float(w)
            selected = cv2.resize(
                selected,
                (360, max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        ok, encoded = cv2.imencode(".jpg", selected, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if not ok:
            return JSONResponse(status_code=500, content={"error": "Thumbnail encode failed"})
        return StarletteResponse(
            content=encoded.tobytes(),
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400", "ETag": f'"{hash(str(video_path) + str(selected.shape))}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
