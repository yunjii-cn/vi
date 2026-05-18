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
            "finished_at": data.get("finished_at"),
        }

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
                        history.append({
                            "filename": filename, "type": item_type, "mtime": mtime,
                            "size": size, "fullpath": str(full_path),
                            "replay": replay, "replay_available": replay is not None,
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
