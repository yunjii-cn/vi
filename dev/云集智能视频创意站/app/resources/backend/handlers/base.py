"""Shared base types for state handlers."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Concatenate, ParamSpec, TypeVar

from state.app_state_types import AppState

if TYPE_CHECKING:
    from runtime_config.runtime_config import RuntimeConfig
    from runtime_config.model_download_specs import ModelFileDownloadSpec
    from state.app_state_types import ModelFileType

_P = ParamSpec("_P")
_R = TypeVar("_R")
_S = TypeVar("_S", bound="StateHandlerBase")


class StateHandlerBase:
    """Base handler with shared state and lock references."""

    def __init__(self, state: AppState, lock: RLock, config: RuntimeConfig) -> None:
        self._state = state
        self._lock = lock
        self._config = config

    @property
    def state(self) -> AppState:
        return self._state

    @property
    def lock(self) -> RLock:
        return self._lock

    @property
    def config(self) -> RuntimeConfig:
        return self._config

    @property
    def models_dir(self) -> Path:
        """Effective models dir: custom from settings, or startup default."""
        custom = self._state.app_settings.models_dir
        return Path(custom) if custom else self._config.default_models_dir

    @property
    def models_dirs(self) -> list[Path]:
        """All model directories: primary + custom, deduplicated."""
        primary = self.models_dir
        dirs = [primary]
        for custom_str in self._state.app_settings.custom_models_dirs:
            p = Path(custom_str).expanduser()
            if not p.is_dir():
                continue
            try:
                if p.resolve() != primary.resolve():
                    dirs.append(p)
            except OSError:
                dirs.append(p)
        return dirs

    def resolve_model(self, model_type: "ModelFileType") -> Path:
        """Resolve model path by searching all model directories."""
        from runtime_config.model_download_specs import resolve_model_path_multi

        return resolve_model_path_multi(
            self.models_dirs, self._config.model_download_specs, model_type
        )


def with_state_lock(
    method: Callable[Concatenate[_S, _P], _R],
) -> Callable[Concatenate[_S, _P], _R]:
    @wraps(method)
    def wrapped(self: _S, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        with self.lock:
            return method(self, *args, **kwargs)

    return wrapped
