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
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from extensions._context import ExtensionContext
from extensions._utils import resolve_models_root


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    _TTS_WORKER = Path(__file__).resolve().parent.parent / "tts_worker.py"

    def _resolve_tts_model_dir() -> str:
        env_dir = os.environ.get("LTX_TTS_MODEL_DIR", "").strip()
        if env_dir:
            return str(Path(env_dir).expanduser())
        models_root = resolve_models_root(ctx)
        if models_root:
            return str(models_root / "VoxCPM2")
        return "VoxCPM2"

    def _guess_audio_suffix(raw: bytes) -> str:
        if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
            return ".wav"
        if raw[:3] == b"ID3" or (len(raw) >= 2 and raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0):
            return ".mp3"
        if raw[:4] == b"fLaC":
            return ".flac"
        if raw[:4] == b"OggS":
            return ".ogg"
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
        try:
            cmd = [sys.executable, str(_TTS_WORKER), "--request-json", req_path]
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            flags = 0
            if sys.platform == "win32":
                flags = subprocess.CREATE_NO_WINDOW | 0x00000008
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=600,
                                  startupinfo=si, creationflags=flags)
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
            try:
                os.unlink(req_path)
            except Exception:
                pass

    @app.post("/api/tts/generate")
    async def route_tts_generate(request: Request):
        from starlette.concurrency import run_in_threadpool
        tmp_files: list[str] = []
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
            result = await run_in_threadpool(_run_tts_worker, payload)
            if result.get("status") != "complete":
                raise RuntimeError(str(result.get("error") or "TTS worker 生成失败"))
            fname = str(result.get("audio_path"))
            sample_rate = int(result.get("sample_rate") or 0)
            return {"status": "complete", "audio_path": fname, "audio_url": f"/outputs/{fname}", "sample_rate": sample_rate}
        except Exception as e:
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
