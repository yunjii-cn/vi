"""Windows-specific error handling and fixes.

Upstream dependency: None (purely YunJi additions)
"""

from __future__ import annotations

import asyncio
import sys

from extensions._context import ExtensionContext


def install(app, ctx: ExtensionContext) -> None:
    if sys.platform != "win32":
        return

    try:
        loop = asyncio.get_event_loop()

        def silence_winerror_10054(loop, context):
            exc = context.get("exception")
            if (
                isinstance(exc, ConnectionResetError)
                and getattr(exc, "winerror", None) == 10054
            ):
                return
            loop.default_exception_handler(context)

        loop.set_exception_handler(silence_winerror_10054)
    except Exception:
        pass
