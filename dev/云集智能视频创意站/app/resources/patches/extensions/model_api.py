"""Model scanning API endpoint (YunJi custom).

Endpoints:
  GET /api/models - List available model checkpoints

Upstream dependency: handler.pipelines.models_dir
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from extensions._context import ExtensionContext
from extensions._utils import resolve_models_root


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


def _scan_models_in_dir(root: Path, suffixes: set[str]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
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
                        found.append({"name": fn, "path": resolved})
    except OSError:
        pass
    found.sort(key=lambda x: x["name"].lower())
    return found


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    _MODEL_SCAN_SUFFIXES = {".safetensors", ".ckpt", ".pt", ".bin", ".pth"}

    @app.get("/api/models")
    async def route_list_models(request: Request):
        raw = (request.query_params.get("dir") or "").strip()
        if raw.startswith("True"):
            raw = raw[4:].lstrip()
        raw = raw.strip().strip('"').strip("'")

        if raw:
            root = Path(raw).expanduser()
            try:
                root = root.resolve()
            except OSError:
                pass
            if not root.is_dir():
                return {
                    "models": [], "error": "not_a_directory",
                    "message": "路径不是文件夹或不存在，请检查拼写、盘符与权限", "path": str(root),
                }
            return {"models": _scan_models_in_dir(root, _MODEL_SCAN_SUFFIXES)}

        seen_paths: set[str] = set()
        all_models: list[dict[str, str]] = []

        default_root = resolve_models_root(ctx)
        scan_dirs: list[Path] = []
        if default_root and default_root.is_dir():
            scan_dirs.append(default_root)

        for cd in _load_custom_models_dirs(ctx):
            if cd not in scan_dirs:
                scan_dirs.append(cd)

        for d in scan_dirs:
            for m in _scan_models_in_dir(d, _MODEL_SCAN_SUFFIXES):
                if m["path"] not in seen_paths:
                    seen_paths.add(m["path"])
                    all_models.append(m)

        all_models.sort(key=lambda x: x["name"].lower())
        return {"models": all_models}
