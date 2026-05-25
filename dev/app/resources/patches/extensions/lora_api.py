"""LoRA scanning and management API endpoints (YunJi custom).

Endpoints:
  GET  /api/loras       - List available LoRA models (with metadata)
  GET  /api/lora-info   - Get detailed metadata for a single LoRA file
  POST /api/lora-dir    - Save LoRA directory preference
  GET  /api/lora-dir    - Get LoRA directory preference

Upstream dependency: handler.pipelines.models_dir
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from extensions._context import ExtensionContext
from extensions._utils import default_lora_dir, resolve_models_root

_LORA_KNOWN_INFO: dict[str, dict] = {
    "ltx-2-19b-distilled-lora-384.safetensors": {
        "description": "Pro模式LoRA（视频生成Pro高质量模式必需，384步推理）",
        "trigger_words": [],
        "base_model": "Lightricks/LTX-2",
    },
    "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors": {
        "description": "视频迁移控制模型（视频迁移功能必需，支持深度/姿态/参考图控制）",
        "trigger_words": [],
        "base_model": "Lightricks/LTX-2.3",
    },
}


def _read_safetensors_metadata(file_path: str | Path) -> dict:
    p = Path(file_path)
    if not p.is_file() or p.suffix.lower() != ".safetensors":
        return {}
    try:
        with open(p, "rb") as f:
            header_size_bytes = f.read(8)
            if len(header_size_bytes) < 8:
                return {}
            header_size = struct.unpack("<Q", header_size_bytes)[0]
            if header_size <= 0 or header_size > 100 * 1024 * 1024:
                return {}
            header_json_bytes = f.read(header_size)
            if len(header_json_bytes) < header_size:
                return {}
            header = json.loads(header_json_bytes)
        metadata = header.get("__metadata__", {})
        if not isinstance(metadata, dict):
            return {}
        result: dict = {}
        desc = (
            metadata.get("description")
            or metadata.get("ss_training_comment")
            or metadata.get("modelspec.description")
            or ""
        )
        if isinstance(desc, str) and desc.strip():
            result["description"] = desc.strip()
        triggers = metadata.get("trigger_words") or metadata.get("tags") or ""
        if isinstance(triggers, str) and triggers.strip():
            result["trigger_words"] = [t.strip() for t in triggers.split(",") if t.strip()]
        elif isinstance(triggers, list) and triggers:
            result["trigger_words"] = [str(t).strip() for t in triggers if str(t).strip()]
        base = (
            metadata.get("base_model")
            or metadata.get("ss_base_model_version")
            or metadata.get("modelspec.architecture")
            or ""
        )
        if isinstance(base, str) and base.strip():
            result["base_model"] = base.strip()
        return result
    except Exception:
        return {}


def _load_custom_models_dirs(ctx: ExtensionContext) -> list[Path]:
    dirs: list[Path] = []
    try:
        for candidate in [ctx.config_dir, ctx.config_dir / "config"]:
            launcher_config = candidate / "launcher_config.json"
            if launcher_config.is_file():
                with open(launcher_config, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                for d in cfg.get("model_dirs", []):
                    p = d.get("path", "").strip().strip('"').strip("'")
                    if p:
                        pp = Path(p).expanduser()
                        if pp.is_dir() and pp not in dirs:
                            dirs.append(pp)
                break
    except Exception:
        pass
    try:
        f = ctx.config_dir / "custom_models_dirs.txt"
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip().strip('"').strip("'")
                if not line:
                    continue
                p = Path(line).expanduser()
                if p.is_dir() and p not in dirs:
                    dirs.append(p)
    except Exception:
        pass
    return dirs


def _scan_loras_in_dir(root: Path, suffixes: set[str], read_meta: bool = False) -> list[dict]:
    found: list[dict] = []
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                suf = Path(fn).suffix.lower()
                if suf in suffixes:
                    full = Path(dirpath) / fn
                    if full.is_file():
                        try:
                            resolved = str(full.resolve())
                        except OSError:
                            resolved = str(full)
                        entry: dict = {"name": fn, "path": resolved}
                        if read_meta and suf == ".safetensors":
                            meta = _read_safetensors_metadata(full)
                            if meta:
                                entry.update(meta)
                            known = _LORA_KNOWN_INFO.get(fn)
                            if known:
                                if not entry.get("description"):
                                    entry["description"] = known["description"]
                                if not entry.get("trigger_words") and known.get("trigger_words"):
                                    entry["trigger_words"] = known["trigger_words"]
                                if not entry.get("base_model") and known.get("base_model"):
                                    entry["base_model"] = known["base_model"]
                        found.append(entry)
    except OSError:
        pass
    found.sort(key=lambda x: x["name"].lower())
    return found


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    _LORA_SCAN_SUFFIXES = {".safetensors", ".ckpt", ".pt", ".bin"}

    @app.post("/api/lora-dir")
    async def route_save_lora_dir(request: Request):
        try:
            body = await request.json()
            lora_dir = body.get("loraDir", "").strip()
            settings_file = ctx.config_dir / "settings.json"
            if settings_file.exists():
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {}
            data["lora_dir"] = lora_dir
            data["loraDir"] = lora_dir
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return {"status": "ok", "loraDir": lora_dir}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @app.get("/api/lora-dir")
    async def route_get_lora_dir():
        try:
            settings_file = ctx.config_dir / "settings.json"
            models_root = resolve_models_root(ctx)
            _default_lora_dir = default_lora_dir(ctx)
            payload = {
                "loraDir": "", "modelsDir": str(models_root) if models_root else "",
                "defaultLoraDir": str(_default_lora_dir) if _default_lora_dir else "",
            }
            if settings_file.exists():
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                payload["loraDir"] = data.get("lora_dir", "") or data.get("loraDir", "")
            return payload
        except Exception as e:
            return {"loraDir": "", "error": str(e)}

    @app.get("/api/loras")
    async def route_list_loras(request: Request):
        raw = (request.query_params.get("dir") or "").strip()
        with_meta = request.query_params.get("meta", "").lower() in ("1", "true", "yes")
        if raw.startswith("True"):
            raw = raw[4:].lstrip()
        raw = raw.strip().strip('"').strip("'")

        custom_lora_dir = ""
        if not raw:
            try:
                settings_file = ctx.config_dir / "settings.json"
                if settings_file.exists():
                    with open(settings_file, "r", encoding="utf-8") as f:
                        settings_data = json.load(f)
                    custom_lora_dir = settings_data.get("lora_dir", "") or settings_data.get("loraDir", "")
                    if custom_lora_dir and str(custom_lora_dir).strip():
                        raw = str(custom_lora_dir).strip()
            except Exception as e:
                print(f"[PATCH] Failed to read lora_dir from settings: {e}")

        if raw:
            root = Path(raw).expanduser()
            try:
                root = root.resolve()
            except OSError:
                pass
            if not root.is_dir():
                pass
            else:
                found = _scan_loras_in_dir(root, _LORA_SCAN_SUFFIXES, read_meta=with_meta)
                _default_lora_dir = default_lora_dir(ctx)
                return {
                    "loras": found, "loras_dir": str(root),
                    "models_dir": str(root.parent),
                    "default_loras_dir": str(_default_lora_dir or ""),
                }

        seen_paths: set[str] = set()
        all_loras: list[dict] = []

        scan_dirs: list[Path] = []

        _default_lora_dir = default_lora_dir(ctx)
        if _default_lora_dir and _default_lora_dir.is_dir():
            scan_dirs.append(_default_lora_dir)

        models_root = resolve_models_root(ctx)
        if models_root and models_root.is_dir() and models_root not in scan_dirs:
            loras_sub = models_root / "loras"
            if loras_sub.is_dir() and loras_sub not in scan_dirs:
                scan_dirs.append(loras_sub)

        for cd in _load_custom_models_dirs(ctx):
            if cd.is_dir() and cd not in scan_dirs:
                scan_dirs.append(cd)
                loras_sub = cd / "loras"
                if loras_sub.is_dir() and loras_sub not in scan_dirs:
                    scan_dirs.append(loras_sub)

        for d in scan_dirs:
            for m in _scan_loras_in_dir(d, _LORA_SCAN_SUFFIXES, read_meta=with_meta):
                if m["path"] not in seen_paths:
                    seen_paths.add(m["path"])
                    all_loras.append(m)

        all_loras.sort(key=lambda x: x["name"].lower())
        primary_dir = str(scan_dirs[0]) if scan_dirs else ""
        return {
            "loras": all_loras,
            "loras_dir": primary_dir,
            "models_dir": str(models_root.parent) if models_root else "",
            "default_loras_dir": str(_default_lora_dir or ""),
        }

    @app.get("/api/lora-info")
    async def route_lora_info(request: Request):
        lora_path = (request.query_params.get("path") or "").strip()
        if not lora_path:
            return JSONResponse({"error": "path parameter required"}, status_code=400)
        p = Path(lora_path).expanduser()
        try:
            p = p.resolve()
        except OSError:
            pass
        if not p.is_file():
            return JSONResponse({"error": "file not found"}, status_code=404)
        entry: dict = {"name": p.name, "path": str(p)}
        if p.suffix.lower() == ".safetensors":
            meta = _read_safetensors_metadata(p)
            if meta:
                entry.update(meta)
        return entry
