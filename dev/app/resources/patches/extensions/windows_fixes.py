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

    # 屏蔽 Windows 上常见的 WinError 10054 (远程主机强迫关闭连接),
    # 避免 uvicorn 在客户端断开时刷一堆无害异常。
    # ★ 2026-08-03 修复: 原先在 app 构建期直接调用 asyncio.get_event_loop(),
    #   在 Python 3.12 某些环境下会阻塞(无运行中的事件循环时创建新 loop 可能
    #   长时间挂起), 导致整个后端启动卡死。改为在 uvicorn 真正起来的 startup
    #   阶段、已有运行循环时再设置异常处理器, 既不阻塞也能保留原意图。
    def silence_winerror_10054(loop, context):
        exc = context.get("exception")
        if (
            isinstance(exc, ConnectionResetError)
            and getattr(exc, "winerror", None) == 10054
        ):
            return
        loop.default_exception_handler(context)

    async def _patch_loop_on_startup() -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(silence_winerror_10054)
        except Exception:
            pass

    try:
        app.router.on_startup.append(_patch_loop_on_startup)
    except Exception:
        pass
