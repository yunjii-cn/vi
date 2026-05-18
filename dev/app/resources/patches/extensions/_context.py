"""Shared context for all YunJi extensions.

ExtensionContext holds references that extensions need:
  - handler: AppHandler (access to pipelines, generation, config, etc.)
  - app: FastAPI instance
  - get_output_path(): dynamic output directory resolver
  - config_dir: LTX Desktop config directory
  - upstream_version: tracked upstream version string
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from fastapi import FastAPI
    from app_handler import AppHandler

UPSTREAM_VERSION = "v1.0.2"
UPSTREAM_REPO = "github.com/Lightricks/LTX-Desktop"


class ExtensionContext:
    __slots__ = (
        "handler",
        "app",
        "_get_output_path",
        "_config_dir",
        "upstream_version",
        "upstream_repo",
        "_queue_state",
    )

    def __init__(
        self,
        handler: "AppHandler",
        app: "FastAPI",
        get_output_path: Callable[[], Path],
        config_dir: Path,
    ):
        self.handler = handler
        self.app = app
        self._get_output_path = get_output_path
        self._config_dir = config_dir
        self.upstream_version = UPSTREAM_VERSION
        self.upstream_repo = UPSTREAM_REPO
        self._queue_state: dict | None = None

    def get_output_path(self) -> Path:
        return self._get_output_path()

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    @property
    def queue_state(self) -> dict:
        if self._queue_state is None:
            self._queue_state = {
                "lock": __import__("threading").RLock(),
                "pending": __import__("collections").deque(),
                "items": {},
                "history": __import__("collections").deque(maxlen=80),
                "wake": __import__("threading").Event(),
                "shutdown": __import__("threading").Event(),
                "worker_started": False,
            }
        return self._queue_state


def resolve_config_dir() -> Path:
    app_data_dir = os.environ.get("LTX_APP_DATA_DIR")
    if app_data_dir:
        p = Path(app_data_dir)
    else:
        p = (
            Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~/AppData/Local")))
            / "LTXDesktop"
        )
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


def resolve_output_path(config_dir: Path) -> Path:
    config_file = config_dir / "custom_dir.txt"
    if config_file.exists():
        try:
            custom_dir = config_file.read_text(encoding="utf-8").strip()
            if custom_dir:
                p = Path(custom_dir)
                p.mkdir(parents=True, exist_ok=True)
                return p
        except Exception:
            pass
    default_dir = config_dir / "outputs"
    default_dir.mkdir(parents=True, exist_ok=True)
    return default_dir
