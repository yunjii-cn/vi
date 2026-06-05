"""LoRA build hook installation.

Upstream dependency: lora_build_hook module (YunJi custom, not upstream)
"""

from __future__ import annotations

from extensions._context import ExtensionContext


def install(app, ctx: ExtensionContext) -> None:
    from lora_build_hook import install_lora_build_hook
    install_lora_build_hook()
