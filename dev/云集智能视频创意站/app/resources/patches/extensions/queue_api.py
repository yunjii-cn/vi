"""Queue management API endpoints (YunJi custom).

Endpoints:
  POST /api/queue/submit/{task_id}  - Submit a task to the queue
  GET  /api/queue/status            - Get queue status
  GET  /api/queue/task/{task_id}    - Get task details
  POST /api/queue/cancel/{task_id}  - Cancel a task

Upstream dependency: handler.generation, handler.video_generation, handler.image_generation, handler.ic_lora
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from _routes._errors import HTTPError
from api_types import GenerateImageRequest, GenerateVideoRequest, IcLoraGenerateRequest
from extensions._context import ExtensionContext

if TYPE_CHECKING:
    from fastapi import FastAPI


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    handler = ctx.handler
    qs = ctx.queue_state
    queue_lock = qs["lock"]
    queue_pending = qs["pending"]
    queue_items = qs["items"]
    queue_history = qs["history"]
    queue_wake = qs["wake"]
    queue_shutdown = qs["shutdown"]
    queue_paused = qs["paused"]

    def _queue_task_view(task: dict) -> dict:
        return {
            "id": task["id"], "mode": task.get("mode"), "endpoint": task.get("endpoint"),
            "payload": task.get("payload") or {}, "label": task.get("label"),
            "status": task.get("status"), "created_at": task.get("created_at"),
            "started_at": task.get("started_at"), "finished_at": task.get("finished_at"),
            "result": task.get("result"), "error": task.get("error"),
            "position": task.get("position", 0), "phase": task.get("phase"),
            "progress": task.get("progress", 0),
            "current_step": task.get("current_step"), "total_steps": task.get("total_steps"),
        }

    def _snapshot_queue() -> dict:
        gp = handler.generation.get_generation_progress()
        tts_state = getattr(ctx, "_tts_progress_state", None)
        upscale_state = getattr(ctx, "_upscale_progress_state", None)
        with queue_lock:
            pending_ids = [task["id"] for task in queue_pending if task.get("status") == "queued"]
            current_task = None
            items: list[dict] = []
            for task_id in pending_ids:
                task = queue_items.get(task_id)
                if task is None:
                    continue
                task["position"] = len(items) + 1
                items.append(_queue_task_view(task))
            history_ids = list(queue_history)
            running_ids = [task_id for task_id, task in queue_items.items() if task.get("status") == "running"]
            if running_ids:
                task = queue_items[running_ids[0]]
                task["position"] = 0
                is_tts_task = task.get("endpoint") == "/api/tts/generate"
                is_upscale_task = task.get("endpoint") in ("/api/upscale/image", "/api/upscale/video")
                if is_tts_task and tts_state:
                    phase_map = {"loading_model": "加载模型", "inference": "推理生成", "complete": "完成"}
                    raw_phase = str(tts_state.get("phase") or "")
                    task["phase"] = phase_map.get(raw_phase, raw_phase) or raw_phase
                    task["progress"] = int(tts_state.get("progress") or 0)
                    task["current_step"] = None
                    task["total_steps"] = None
                elif is_upscale_task and upscale_state:
                    phase_map = {"loading_model": "加载模型", "upscaling": "高清处理", "complete": "完成"}
                    raw_phase = str(upscale_state.get("phase") or "")
                    task["phase"] = phase_map.get(raw_phase, raw_phase) or raw_phase
                    task["progress"] = int(upscale_state.get("progress") or 0)
                    task["current_step"] = upscale_state.get("current_step")
                    task["total_steps"] = upscale_state.get("total_steps")
                else:
                    task["phase"] = gp.phase
                    task["progress"] = gp.progress
                    task["current_step"] = getattr(gp, "currentStep", None)
                    task["total_steps"] = getattr(gp, "totalSteps", None)
                current_task = _queue_task_view(task)
            history_items = [_queue_task_view(queue_items[task_id]) for task_id in history_ids if task_id in queue_items]
            return {
                "current": current_task, "pending": items, "history": history_items,
                "stats": {"queued": len(items), "running": 1 if current_task else 0, "history": len(history_items)},
                "paused": queue_paused,
            }

    def _normalize_queue_result(result) -> dict:
        if hasattr(result, "model_dump"):
            return result.model_dump()
        if isinstance(result, dict):
            return result
        if isinstance(result, JSONResponse):
            raise RuntimeError(result.body.decode("utf-8", errors="replace"))
        if isinstance(result, str):
            r: dict[str, object] = {"status": "complete"}
            if result.lower().endswith((".mp4", ".webm", ".avi")):
                r["video_path"] = result
            elif result.lower().endswith((".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac")):
                r["audio_path"] = result
            else:
                r["video_path"] = result
            return r
        view: dict[str, object] = {"status": getattr(result, "status", "complete") or "complete"}
        for attr in ("video_path", "image_paths", "audio_path", "video_url", "audio_url"):
            value = getattr(result, attr, None)
            if value:
                view[attr] = value
        return view

    def _build_replay_metadata(task: dict) -> dict:
        return {
            "version": 1, "created_at": task.get("created_at"),
            "finished_at": task.get("finished_at"), "task_id": task.get("id"),
            "mode": task.get("mode"), "endpoint": task.get("endpoint"),
            "label": task.get("label"), "payload": task.get("payload") or {},
            "result": task.get("result"),
        }

    def _result_media_paths(result: dict | None) -> list[str]:
        if not isinstance(result, dict):
            return []
        paths: list[str] = []
        for key in ("video_path", "video_url", "audio_path", "audio_url"):
            v = result.get(key)
            if isinstance(v, str) and v:
                paths.append(v)
        image_paths = result.get("image_paths")
        if isinstance(image_paths, list):
            paths.extend(p for p in image_paths if isinstance(p, str) and p)
        return paths

    def _write_replay_sidecars(task: dict) -> None:
        metadata = _build_replay_metadata(task)
        media_paths = _result_media_paths(task.get("result"))
        print(f"[replay] _write_replay_sidecars called, media_paths={media_paths}")
        for raw_path in media_paths:
            try:
                media_path = Path(raw_path)
                if str(raw_path).startswith("/outputs/"):
                    media_path = ctx.get_output_path() / Path(raw_path).name
                if not media_path.is_absolute():
                    media_path = ctx.get_output_path() / raw_path
                    if not media_path.exists():
                        media_path = ctx.get_output_path() / Path(raw_path).name
                if not media_path.exists() or not media_path.is_file():
                    print(f"[replay] media file not found: {media_path} (raw={raw_path})")
                    continue
                replay_path = media_path.with_suffix(media_path.suffix + ".replay.json")
                replay_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[replay] wrote sidecar: {replay_path}")
            except Exception as exc:
                print(f"[replay] failed to write sidecar for {raw_path}: {exc}")

    def _make_replay_seed() -> int:
        return int(time.time_ns() % 2147483647) or 1

    def _ensure_payload_replay_seed(endpoint: str, payload: dict) -> dict:
        if not isinstance(payload, dict):
            return {}
        if endpoint in {"/api/generate", "/api/generate-image", "/api/ic-lora/generate", "/api/generate-batch"}:
            try:
                seed = payload.get("seed")
                seed_i = int(seed)
                if seed_i <= 0:
                    raise ValueError
                payload["seed"] = seed_i
            except Exception:
                payload["seed"] = _make_replay_seed()
        return payload

    def _execute_queue_task(task: dict) -> dict:
        endpoint = str(task.get("endpoint") or "")
        payload = task.get("payload") or {}
        if endpoint == "/api/generate":
            req = GenerateVideoRequest.model_validate(payload)
            return _normalize_queue_result(handler.video_generation.generate(req))
        if endpoint == "/api/generate-image":
            req = GenerateImageRequest.model_validate(payload)
            return _normalize_queue_result(handler.image_generation.generate(req))
        if endpoint == "/api/ic-lora/generate":
            req = IcLoraGenerateRequest.model_validate(payload)
            return _normalize_queue_result(handler.ic_lora.generate(req))
        if endpoint == "/api/generate-batch":
            return _normalize_queue_result(_run_generate_batch_payload(payload))
        if endpoint == "/api/tts/generate":
            tts_fn = getattr(ctx, "_execute_tts_from_queue", None)
            if tts_fn is None:
                raise HTTPError(400, "TTS module not loaded")
            return _normalize_queue_result(tts_fn(payload))
        if endpoint in ("/api/upscale/image", "/api/upscale/video"):
            upscale_fn = getattr(ctx, "_execute_upscale_from_queue", None)
            if upscale_fn is None:
                raise HTTPError(400, "Upscale module not loaded")
            return _normalize_queue_result(upscale_fn(endpoint, payload))
        raise HTTPError(400, f"Unsupported queue endpoint: {endpoint}")

    def _run_generate_batch_payload(payload: dict) -> dict:
        from extensions.batch_api import execute_batch
        return execute_batch(payload, ctx)

    queue_worker_started = [False]

    def _queue_worker_loop() -> None:
        while not queue_shutdown.is_set():
            queue_wake.wait(timeout=0.5)
            queue_wake.clear()
            if queue_shutdown.is_set():
                return
            with queue_lock:
                if queue_paused:
                    continue
            task = None
            with queue_lock:
                while queue_pending:
                    candidate = queue_pending.popleft()
                    if candidate.get("status") == "queued":
                        task = candidate
                        task["status"] = "running"
                        task["started_at"] = time.time()
                        task["progress"] = 0
                        task["phase"] = "queued"
                        break
                pending_ids = [item["id"] for item in queue_pending if item.get("status") == "queued"]
                for idx, task_id in enumerate(pending_ids, start=1):
                    if task_id in queue_items:
                        queue_items[task_id]["position"] = idx
            if task is None:
                continue
            task_id = task.get("id", "?")
            task_label = task.get("label", "")[:60]
            task_endpoint = task.get("endpoint", "?")
            print(f"[queue] ▶ 开始执行任务 {task_id} [{task_endpoint}] {task_label}")
            try:
                result = _execute_queue_task(task)
                with queue_lock:
                    terminal_status = str(result.get("status") or "complete")
                    task["status"] = "cancelled" if terminal_status == "cancelled" else "complete"
                    task["finished_at"] = time.time()
                    task["result"] = result
                    task["progress"] = 100 if task["status"] == "complete" else task.get("progress", 0)
                    task["phase"] = task["status"]
                    queue_history.appendleft(task["id"])
                    _write_replay_sidecars(task)
                print(f"[queue] √ 任务 {task_id} 完成 ({task['status']})")
                if task_endpoint in ("/api/upscale/image", "/api/upscale/video"):
                    try:
                        ctx._upscale_progress_state.update({"phase": "", "progress": 0, "current_step": None, "total_steps": None})
                    except Exception:
                        pass
            except Exception as exc:
                import traceback
                print(f"[queue] × 任务 {task_id} 失败: {exc}")
                traceback.print_exc()
                with queue_lock:
                    task["status"] = "error"
                    task["finished_at"] = time.time()
                    task["error"] = str(exc)
                    task["phase"] = "error"
                    queue_history.appendleft(task["id"])
                if task_endpoint in ("/api/upscale/image", "/api/upscale/video"):
                    try:
                        ctx._upscale_progress_state.update({"phase": "", "progress": 0, "current_step": None, "total_steps": None})
                    except Exception:
                        pass

    def _ensure_queue_worker() -> None:
        with queue_lock:
            if queue_worker_started[0]:
                return
            worker = threading.Thread(target=_queue_worker_loop, name="ltx-generation-queue", daemon=True)
            worker.start()
            queue_worker_started[0] = True

    @app.post("/api/queue/submit")
    async def route_queue_submit(request: Request):
        try:
            data = await request.json()
            endpoint = str(data.get("endpoint") or "").strip()
            payload = data.get("payload") or {}
            mode = str(data.get("mode") or "").strip() or "task"
            label = str(data.get("label") or "").strip() or str(payload.get("prompt") or mode)
            if not endpoint:
                return JSONResponse(status_code=400, content={"error": "Missing queue endpoint"})
            payload = _ensure_payload_replay_seed(endpoint, payload)
            task_id = uuid.uuid4().hex[:10]
            task = {
                "id": task_id, "endpoint": endpoint, "payload": payload,
                "mode": mode, "label": label[:120], "status": "queued",
                "created_at": time.time(), "started_at": None, "finished_at": None,
                "result": None, "error": None, "progress": 0, "phase": "queued",
                "current_step": None, "total_steps": None, "position": 0,
            }
            _ensure_queue_worker()
            with queue_lock:
                queue_items[task_id] = task
                queue_pending.append(task)
                task["position"] = sum(1 for item in queue_pending if item.get("status") == "queued")
            queue_wake.set()
            return {"status": "queued", "task_id": task_id, "position": task["position"]}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.get("/api/queue/status")
    async def route_queue_status():
        try:
            return _snapshot_queue()
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.get("/api/queue/task/{task_id}")
    async def route_queue_task(task_id: str):
        with queue_lock:
            task = queue_items.get(task_id)
            if task is None:
                return JSONResponse(status_code=404, content={"error": "Task not found"})
            if task.get("status") == "running":
                is_tts_task = task.get("endpoint") == "/api/tts/generate"
                is_upscale_task = task.get("endpoint") in ("/api/upscale/image", "/api/upscale/video")
                tts_state = getattr(ctx, "_tts_progress_state", None)
                upscale_state = getattr(ctx, "_upscale_progress_state", None)
                if is_tts_task and tts_state:
                    phase_map = {"loading_model": "加载模型", "inference": "推理生成", "complete": "完成"}
                    raw_phase = str(tts_state.get("phase") or "")
                    task["phase"] = phase_map.get(raw_phase, raw_phase) or raw_phase
                    task["progress"] = int(tts_state.get("progress") or 0)
                    task["current_step"] = None
                    task["total_steps"] = None
                elif is_upscale_task and upscale_state:
                    phase_map = {"loading_model": "加载模型", "upscaling": "高清处理", "complete": "完成"}
                    raw_phase = str(upscale_state.get("phase") or "")
                    task["phase"] = phase_map.get(raw_phase, raw_phase) or raw_phase
                    task["progress"] = int(upscale_state.get("progress") or 0)
                    task["current_step"] = upscale_state.get("current_step")
                    task["total_steps"] = upscale_state.get("total_steps")
                else:
                    gp = handler.generation.get_generation_progress()
                    task["phase"] = gp.phase
                    task["progress"] = gp.progress
                    task["current_step"] = getattr(gp, "currentStep", None)
                    task["total_steps"] = getattr(gp, "totalSteps", None)
            return _queue_task_view(task)

    @app.post("/api/queue/cancel/{task_id}")
    async def route_queue_cancel(task_id: str):
        try:
            with queue_lock:
                task = queue_items.get(task_id)
                if task is None:
                    return JSONResponse(status_code=404, content={"error": "Task not found"})
                if task.get("status") == "queued":
                    task["status"] = "cancelled"
                    task["finished_at"] = time.time()
                    task["phase"] = "cancelled"
                    queue_history.appendleft(task_id)
                    return {"status": "cancelled", "task_id": task_id}
                is_running = task.get("status") == "running"
            if is_running:
                result = handler.generation.force_cancel_generation()
                return {"status": result.status, "task_id": task_id}
            return {"status": task.get("status"), "task_id": task_id}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.post("/api/queue/pause")
    async def route_queue_pause():
        try:
            with queue_lock:
                queue_paused = True
            return {"status": "paused"}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.post("/api/queue/resume")
    async def route_queue_resume():
        try:
            with queue_lock:
                queue_paused = False
            queue_wake.set()
            return {"status": "resumed"}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
