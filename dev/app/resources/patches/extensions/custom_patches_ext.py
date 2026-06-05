"""Custom runtime patches for Windows and library compatibility.

Upstream dependency: None (purely YunJi fixes)
Patches: av_open non-ASCII paths, SigLIP vision model, Gemma3 rotary emb, safetensors mmap
"""

from __future__ import annotations

from extensions._context import ExtensionContext


def install(app, ctx: ExtensionContext) -> None:
    from custom_patches import install_all_patches
    install_all_patches()
