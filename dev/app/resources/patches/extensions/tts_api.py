"""TTS (Text-to-Speech) API endpoints (YunJi custom).

Endpoints:
  POST /api/tts/generate         - Generate speech from text
  POST /api/tts/upload-reference - Upload reference audio for voice cloning
  GET  /api/tts/status           - Check TTS availability

Upstream dependency: None (uses VoxCPM2 model, independent of LTX backend)
"""

from __future__ import annotations

import base64
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from extensions._context import ExtensionContext
from extensions._utils import resolve_models_root


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    _TTS_WORKER = Path(__file__).resolve().parent.parent / "tts_worker.py"

    _tts_progress_lock = threading.Lock()
    _tts_progress_state: dict[str, object] = {
        "active": False,
        "phase": "",
        "progress": 0,
        "generation_id": None,
        "started_at": None,
        "error": None,
    }

    ctx._tts_progress_lock = _tts_progress_lock
    ctx._tts_progress_state = _tts_progress_state

    def _execute_tts_from_queue(payload: dict) -> dict:
        tmp_files: list[str] = []
        try:
            text = (payload.get("text") or "").strip()
            if not text:
                raise RuntimeError("text 不能为空")
            mode = payload.get("mode", "text_only")
            cfg_value = float(payload.get("cfg_value", 2.0))
            inference_timesteps = int(payload.get("inference_timesteps", 10))
            reference_wav_b64 = payload.get("reference_wav")
            prompt_wav_b64 = payload.get("prompt_wav")
            prompt_text = payload.get("prompt_text", "")
            if mode in {"clone", "ultimate_clone"} and not reference_wav_b64:
                raise RuntimeError("克隆模式必须上传参考音频")
            ref_wav_path = None
            prompt_wav_path = None
            if isinstance(reference_wav_b64, str) and reference_wav_b64.strip():
                ref_wav_path = _decode_audio_b64_to_temp(reference_wav_b64, tmp_files)
            if isinstance(prompt_wav_b64, str) and prompt_wav_b64.strip():
                prompt_wav_path = _decode_audio_b64_to_temp(prompt_wav_b64, tmp_files)
            out_dir = ctx.get_output_path()
            worker_payload = {
                "text": text, "mode": mode, "cfg_value": cfg_value,
                "inference_timesteps": inference_timesteps,
                "reference_wav_path": ref_wav_path, "prompt_wav_path": prompt_wav_path,
                "prompt_text": prompt_text, "model_dir": _resolve_tts_model_dir(),
                "output_dir": str(out_dir),
            }
            _tts_progress_state["generation_id"] = uuid.uuid4().hex[:8]
            _tts_progress_state["error"] = None
            _set_tts_progress(True, "loading_model", 5)
            result = _run_tts_worker(worker_payload)
            _set_tts_progress(False, "complete", 100)
            if result.get("status") != "complete":
                raise RuntimeError(str(result.get("error") or "TTS worker 生成失败"))
            fname = str(result.get("audio_path"))
            sample_rate = int(result.get("sample_rate") or 0)
            return {"status": "complete", "audio_path": fname, "audio_url": f"/outputs/{fname}", "sample_rate": sample_rate}
        except Exception as e:
            _set_tts_progress(False, "error", 0, error=str(e))
            raise
        finally:
            for p in tmp_files:
                try:
                    if os.path.exists(p):
                        os.unlink(p)
                except Exception:
                    pass

    ctx._execute_tts_from_queue = _execute_tts_from_queue

    def _set_tts_progress(active: bool, phase: str = "", progress: int = 0, error: str | None = None) -> None:
        with _tts_progress_lock:
            _tts_progress_state["active"] = active
            _tts_progress_state["phase"] = phase
            _tts_progress_state["progress"] = progress
            if error is not None:
                _tts_progress_state["error"] = error
            if phase:
                _tts_progress_state["phase"] = phase
            if progress > 0:
                _tts_progress_state["progress"] = progress

    def _resolve_tts_model_dir() -> str:
        env_dir = os.environ.get("LTX_TTS_MODEL_DIR", "").strip()
        if env_dir:
            return str(Path(env_dir).expanduser())
        models_root = resolve_models_root(ctx)
        all_dirs = _get_all_models_dirs()
        for d in all_dirs:
            candidate = d / "VoxCPM2"
            if candidate.is_dir() and any(candidate.iterdir()):
                return str(candidate)
        if models_root:
            return str(models_root / "VoxCPM2")
        return "VoxCPM2"

    def _get_all_models_dirs() -> list[Path]:
        dirs: list[Path] = []
        models_root = resolve_models_root(ctx)
        if models_root:
            dirs.append(models_root)
        try:
            custom_dirs = ctx.handler.state.app_settings.custom_models_dirs
            for custom_str in custom_dirs:
                p = Path(custom_str).expanduser()
                if p.is_dir() and p not in dirs:
                    dirs.append(p)
        except Exception:
            pass
        return dirs

    def _guess_audio_suffix(raw: bytes) -> str:
        if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
            return ".wav"
        if raw[:3] == b"ID3" or (len(raw) >= 2 and raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0):
            return ".mp3"
        if raw[:4] == b"fLaC":
            return ".flac"
        if raw[:4] == b"OggS":
            return ".ogg"
        # 2026-06-10 修复: 补全浏览器录制常见格式(否则 ffmpeg 拿到 .bin 后只能靠内容嗅探,失败率高)
        if len(raw) >= 4 and raw[:4] == b"\x1a\x45\xdf\xa3":
            return ".webm"
        if len(raw) >= 12 and raw[4:8] == b"ftyp":
            return ".m4a"
        return ".bin"

    def _decode_audio_b64_to_temp(b64_data: str, tmp_files: list[str]) -> str:
        clean = b64_data.split(",", 1)[1] if "," in b64_data else b64_data
        raw = base64.b64decode(clean, validate=True)
        suffix = _guess_audio_suffix(raw)
        fd = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        fd.write(raw)
        fd.close()
        tmp_files.append(fd.name)
        return fd.name

    def _run_tts_worker(payload: dict[str, object]) -> dict[str, object]:
        if not _TTS_WORKER.exists():
            raise RuntimeError(f"TTS worker 不存在: {_TTS_WORKER}")
        req_fd = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
        req_path = req_fd.name
        req_fd.write(json.dumps(payload, ensure_ascii=False))
        req_fd.close()

        progress_timer_cancel = threading.Event()

        def _progress_updater() -> None:
            t0 = time.perf_counter()
            time_constant = 40.0
            while not progress_timer_cancel.is_set():
                progress_timer_cancel.wait(2.0)
                if progress_timer_cancel.is_set():
                    break
                elapsed = time.perf_counter() - t0
                ratio = 1 - math.exp(-elapsed / time_constant)
                est_progress = int(10 + 80 * ratio)
                _set_tts_progress(True, "inference", est_progress)

        try:
            _set_tts_progress(True, "loading_model", 5)
            _tts_progress_state["started_at"] = time.time()

            progress_thread = threading.Thread(target=_progress_updater, daemon=True)
            progress_thread.start()

            python_exe = sys.executable
            if sys.platform == "win32" and python_exe.lower().endswith("python.exe"):
                pythonw = python_exe[:-10] + "pythonw.exe"
                if os.path.isfile(pythonw):
                    python_exe = pythonw
            cmd = [python_exe, str(_TTS_WORKER), "--request-json", req_path]
            creationflags = 0
            startupinfo = None
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0

            _set_tts_progress(True, "inference", 10)

            proc = subprocess.run(
                cmd, capture_output=True, text=True, check=False, timeout=600,
                startupinfo=startupinfo, creationflags=creationflags,
                stdin=subprocess.DEVNULL,
            )

            progress_timer_cancel.set()
            progress_thread.join(timeout=3)

            if proc.returncode != 0:
                stderr_tail = (proc.stderr or "").strip()[-1200:]
                stdout_tail = (proc.stdout or "").strip()[-800:]
                msg = stderr_tail or stdout_tail or f"退出码 {proc.returncode}"
                raise RuntimeError(f"TTS worker 执行失败: {msg}")
            out = (proc.stdout or "").strip().splitlines()
            if not out:
                raise RuntimeError("TTS worker 无输出")
            try:
                return json.loads(out[-1])
            except Exception as exc:
                stdout_tail = (proc.stdout or "").strip()[-1200:]
                raise RuntimeError(f"TTS worker 输出解析失败: {stdout_tail}") from exc
        finally:
            progress_timer_cancel.set()
            try:
                os.unlink(req_path)
            except Exception:
                pass

    @app.post("/api/tts/generate")
    async def route_tts_generate(request: Request):
        from starlette.concurrency import run_in_threadpool
        tmp_files: list[str] = []
        generation_id = uuid.uuid4().hex[:8]
        try:
            data = await request.json()
            text = (data.get("text") or "").strip()
            if not text:
                return JSONResponse(status_code=400, content={"error": "text 不能为空"})
            mode = data.get("mode", "text_only")
            cfg_value = float(data.get("cfg_value", 2.0))
            inference_timesteps = int(data.get("inference_timesteps", 10))
            reference_wav_b64 = data.get("reference_wav")
            prompt_wav_b64 = data.get("prompt_wav")
            prompt_text = data.get("prompt_text", "")
            if mode in {"clone", "ultimate_clone"} and not reference_wav_b64:
                return JSONResponse(status_code=400, content={"error": "克隆模式必须上传参考音频"})
            ref_wav_path = None
            prompt_wav_path = None
            if isinstance(reference_wav_b64, str) and reference_wav_b64.strip():
                ref_wav_path = _decode_audio_b64_to_temp(reference_wav_b64, tmp_files)
            if isinstance(prompt_wav_b64, str) and prompt_wav_b64.strip():
                prompt_wav_path = _decode_audio_b64_to_temp(prompt_wav_b64, tmp_files)
            out_dir = ctx.get_output_path()
            payload = {
                "text": text, "mode": mode, "cfg_value": cfg_value,
                "inference_timesteps": inference_timesteps,
                "reference_wav_path": ref_wav_path, "prompt_wav_path": prompt_wav_path,
                "prompt_text": prompt_text, "model_dir": _resolve_tts_model_dir(),
                "output_dir": str(out_dir),
            }

            _tts_progress_state["generation_id"] = generation_id
            _tts_progress_state["error"] = None
            _set_tts_progress(True, "loading_model", 5)

            result = await run_in_threadpool(_run_tts_worker, payload)

            _set_tts_progress(False, "complete", 100)

            if result.get("status") != "complete":
                raise RuntimeError(str(result.get("error") or "TTS worker 生成失败"))
            fname = str(result.get("audio_path"))
            sample_rate = int(result.get("sample_rate") or 0)
            return {"status": "complete", "audio_path": fname, "audio_url": f"/outputs/{fname}", "sample_rate": sample_rate}
        except Exception as e:
            _set_tts_progress(False, "error", 0, error=str(e))
            import traceback
            traceback.print_exc()
            return JSONResponse(status_code=500, content={"error": str(e)})
        finally:
            for p in tmp_files:
                try:
                    if os.path.exists(p):
                        os.unlink(p)
                except Exception:
                    pass

    @app.get("/api/tts/progress")
    async def route_tts_progress():
        with _tts_progress_lock:
            return {
                "active": _tts_progress_state.get("active", False),
                "phase": _tts_progress_state.get("phase", ""),
                "progress": _tts_progress_state.get("progress", 0),
                "generation_id": _tts_progress_state.get("generation_id"),
                "error": _tts_progress_state.get("error"),
            }

    @app.post("/api/tts/upload-reference")
    async def route_tts_upload_reference(request: Request):
        import base64 as b64
        try:
            data = await request.json()
            b64_data = data.get("audio")
            if not b64_data:
                return JSONResponse(status_code=400, content={"error": "No audio data"})
            if "," in b64_data:
                b64_data = b64_data.split(",", 1)[1]
            b64.b64decode(b64_data)
            return {"status": "ok", "data": b64_data}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.get("/api/tts/status")
    async def route_tts_status():
        try:
            import importlib
            spec = importlib.util.find_spec("voxcpm")
            has_pkg = spec is not None
            model_dir = _resolve_tts_model_dir()
            models_root = resolve_models_root(ctx)
            model_dir_exists = os.path.isdir(model_dir)
            install_hint = ""
            if not has_pkg:
                install_hint = "pip install voxcpm torchaudio soundfile librosa  (或: uv sync --extra tts)"
            return {
                "available": has_pkg and model_dir_exists,
                "voxcpm_installed": has_pkg, "model_dir_exists": model_dir_exists,
                "model_dir": model_dir,
                "models_dir": str(models_root) if models_root else "",
                "expected_model_dir": model_dir,
                "worker_script": str(_TTS_WORKER), "worker_exists": _TTS_WORKER.exists(),
                "install_hint": install_hint,
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    _ASR_MODEL = None
    _ASR_MODEL_NAME = "base"

    def _get_asr_model():
        nonlocal _ASR_MODEL
        if _ASR_MODEL is not None:
            return _ASR_MODEL
        from faster_whisper import WhisperModel
        models_root = resolve_models_root(ctx)
        asr_dir = None
        if models_root:
            candidate = models_root / "faster-whisper" / _ASR_MODEL_NAME
            if candidate.is_dir():
                asr_dir = str(candidate)
        if asr_dir:
            _ASR_MODEL = WhisperModel(asr_dir, device="cpu", compute_type="int8")
        else:
            _ASR_MODEL = WhisperModel(_ASR_MODEL_NAME, device="cpu", compute_type="int8", download_root=str(models_root / "faster-whisper") if models_root else None)
        return _ASR_MODEL

    @app.post("/api/tts/transcribe")
    async def route_tts_transcribe(request: Request):
        tmp_path = None
        try:
            data = await request.json()
            audio_b64 = data.get("audio")
            if not audio_b64:
                return JSONResponse(status_code=400, content={"error": "audio 不能为空"})
            # 2026-06-10 修复: 兼容 data URL 头(data:audio/...;base64,XXXX),先剥离
            if "," in audio_b64 and audio_b64.lstrip().lower().startswith("data:"):
                audio_b64 = audio_b64.split(",", 1)[1]
            try:
                raw = base64.b64decode(audio_b64, validate=True)
            except Exception as de:
                return JSONResponse(status_code=400, content={"error": f"audio base64 解码失败: {de}"})
            # 2026-06-10 修复: 加上大小校验,避免空文件 / 过小文件导致 faster-whisper 误报
            if len(raw) < 256:
                return JSONResponse(status_code=400, content={"error": f"audio 数据过小({len(raw)} 字节),请确认已正确上传音频文件"})
            suffix = _guess_audio_suffix(raw)
            fd = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            fd.write(raw)
            fd.close()
            tmp_path = fd.name

            model = _get_asr_model()
            # 2026-06-10 修复: 显式传 file 参数 + 接受 numpy array 而不是依赖 ffmpeg 子进程
            # 优先尝试用本地文件,失败则回退到 numpy 数组
            try:
                segments, info = model.transcribe(tmp_path, beam_size=3, vad_filter=True)
            except Exception as e1:
                # 回退: 直接解码到 numpy
                try:
                    import numpy as np
                    import soundfile as sf
                    audio_data, sample_rate = sf.read(tmp_path)
                    if audio_data.ndim > 1:
                        audio_data = audio_data.mean(axis=1)
                    audio_data = audio_data.astype(np.float32)
                    segments, info = model.transcribe(audio_data, beam_size=3, vad_filter=True, language=None)
                except ImportError:
                    raise e1
            text_parts = []
            for seg in segments:
                text_parts.append(seg.text.strip())
            full_text = " ".join(text_parts).strip()
            return {"status": "ok", "text": full_text, "language": info.language, "language_probability": round(info.language_probability, 3)}
        except ImportError:
            return JSONResponse(status_code=501, content={"error": "faster-whisper 未安装，请运行: uv pip install faster-whisper", "install_required": True})
        except Exception as e:
            import traceback
            traceback.print_exc()
            # 2026-06-10 修复: 错误信息带上类型,方便排查(faster-whisper 模型加载失败 vs 转写失败 vs ffmpeg 不可用)
            return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {e}"})
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    @app.get("/api/tts/asr-status")
    async def route_tts_asr_status():
        try:
            import importlib
            spec = importlib.util.find_spec("faster_whisper")
            has_pkg = spec is not None
            return {"available": has_pkg, "package": "faster_whisper" if has_pkg else None}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
