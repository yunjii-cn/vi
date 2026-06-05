"""Health and GPU info handler patches.

Patches:
  - HealthHandler.get_health: force models_loaded=True (workaround for API mode)
  - GpuInfoImpl.get_gpu_info: use pynvml for accurate VRAM reporting

Upstream dependency: handlers.health_handler.HealthHandler
                    services.gpu_info.gpu_info_impl.GpuInfoImpl
When upstream changes these handlers, review patches.
"""

from __future__ import annotations

from extensions._context import ExtensionContext


def install(app, ctx: ExtensionContext) -> None:
    _install_health_patch(ctx)
    _install_gpu_info_patch(ctx)


def _install_health_patch(ctx: ExtensionContext) -> None:
    from handlers.health_handler import HealthHandler

    if not hasattr(HealthHandler, "_fixed_v2"):
        _orig_get_health = HealthHandler.get_health

        def patched_health_v2(self):
            resp = _orig_get_health(self)
            if not resp.models_loaded:
                resp.models_loaded = True
            return resp

        HealthHandler.get_health = patched_health_v2
        HealthHandler._fixed_v2 = True


def _install_gpu_info_patch(ctx: ExtensionContext) -> None:
    from services.gpu_info.gpu_info_impl import GpuInfoImpl

    if not hasattr(GpuInfoImpl, "_fixed_vram_patch"):
        _orig_get_gpu_info = GpuInfoImpl.get_gpu_info

        def patched_get_gpu_info(self):
            import torch

            if self.get_cuda_available():
                idx = 0
                if (
                    hasattr(ctx.handler.config.device, "index")
                    and ctx.handler.config.device.index is not None
                ):
                    idx = ctx.handler.config.device.index
                try:
                    import pynvml

                    pynvml.nvmlInit()
                    handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
                    raw_name = pynvml.nvmlDeviceGetName(handle)
                    name = (
                        raw_name.decode("utf-8", errors="replace")
                        if isinstance(raw_name, bytes)
                        else str(raw_name)
                    )
                    memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    pynvml.nvmlShutdown()
                    return {
                        "name": f"{name} [ID: {idx}]",
                        "vram": memory.total // (1024 * 1024),
                        "vramUsed": memory.used // (1024 * 1024),
                    }
                except Exception:
                    pass
            return _orig_get_gpu_info(self)

        GpuInfoImpl.get_gpu_info = patched_get_gpu_info
        GpuInfoImpl._fixed_vram_patch = True
