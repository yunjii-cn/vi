"""Low VRAM mode hooks.

Upstream dependency: handlers.pipelines_handler.PipelinesHandler
"""

from __future__ import annotations

from extensions._context import ExtensionContext


def install(app, ctx: ExtensionContext) -> None:
    pl = ctx.handler.pipelines
    pl._pipeline_signature = None
    from low_vram_runtime import (
        install_low_vram_on_pipelines,
        install_low_vram_pipeline_hooks,
    )
    install_low_vram_on_pipelines(ctx.handler)
    install_low_vram_pipeline_hooks(pl)
