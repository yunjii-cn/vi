"""Dynamic output path configuration.

Overrides upstream's static /outputs mount with a configurable directory.
Reads custom_dir.txt from LTX Desktop config directory.

Upstream dependency: app_factory.py mounts /outputs statically
This extension remounts it with our dynamic path.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from extensions._context import ExtensionContext


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    output_path = ctx.get_output_path()
    upload_tmp_path = output_path / "uploads"

    if not output_path.exists():
        output_path.mkdir(parents=True, exist_ok=True)
    if not upload_tmp_path.exists():
        upload_tmp_path.mkdir(parents=True, exist_ok=True)

    ctx.handler.config.outputs_dir = output_path

    app.mount("/outputs", StaticFiles(directory=str(output_path)), name="outputs")
