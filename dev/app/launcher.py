#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EXE 入口点 - 云集智能视频创意站

★ 2026-06-16 进程控制策略重构（参考 1.PC 进程控制策略-技术文档.md）:
  - launcher.py 的强杀逻辑**只对 frozen 模式生效**(EXE)
  - launcher.py 看到 python.exe **直接跳过**(不杀开发模式实例)
  - dev 模式的单实例控制由 main.py 的优雅通知机制负责:
    * 共享内存存路径(同版本不同路径判断)
    * 全局 Shutdown Event(跨版本/同版本异路径时通知旧实例退出)
    * window activation(同版本同路径时激活旧窗口)
  - 避免 launcher.py 误伤:
    * 自伤(launcher 自己是 python.exe,宽泛条件会自杀)
    * 误伤(dev 目录下用户其他脚本)
    * 不优雅(强杀丢工作区状态)
"""
import sys
import os
import ctypes
import ctypes.wintypes
import time

if sys.platform == 'win32' and getattr(sys, 'frozen', False):
    class _NullWriter:
        def write(self, *args, **kwargs):
            return 0
        def flush(self):
            pass
        def isatty(self):
            return False
    sys.stdout = _NullWriter()
    sys.stderr = _NullWriter()


def _is_exe_instance(pid, base_prefix):
    """
    ★ 2026-06-16:严格判断指定 PID 是否为"本项目 EXE 启动器实例"
    - 用 psutil 拿 exe 路径,与当前进程 sys.executable 同 basename 才算
    - 不再用 cmdline 模糊匹配(避免误伤同前缀但不同项目的 exe)
    """
    if sys.platform != 'win32':
        return False
    try:
        import psutil
        proc = psutil.Process(pid)
        try:
            exe_path = proc.exe()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            return False
        if not exe_path:
            return False
        # 必须和当前 EXE 同 basename(同名 = 同 EXE 实例)
        return os.path.basename(exe_path).lower() == os.path.basename(sys.executable).lower()
    except Exception:
        return False


def _kill_old_instances():
    """
    ★ 2026-06-16 进程控制策略重构:
    - **只对 frozen 模式(EXE)生效**
    - dev 模式(python.exe 进程)**直接跳过** → dev 模式走 main.py 优雅通知
    - 用 CreateToolhelp32Snapshot + TerminateProcess(标准 Win32 API)
    - 等旧进程真正退出(最多 2s)再返回,避免新进程端口冲突
    """
    # ★ 关键: dev 模式完全跳过 launcher 的硬杀
    if sys.platform != 'win32':
        return
    if not getattr(sys, 'frozen', False):
        # dev 模式: 不杀 python.exe,留给 main.py 的单实例检测做优雅通知
        return

    my_pid = ctypes.windll.kernel32.GetCurrentProcessId()

    kernel32 = ctypes.windll.kernel32

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.wintypes.DWORD),
            ("cntUsage", ctypes.wintypes.DWORD),
            ("th32ProcessID", ctypes.wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", ctypes.wintypes.DWORD),
            ("cntThreads", ctypes.wintypes.DWORD),
            ("th32ParentProcessID", ctypes.wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return

    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)

    base_prefix = "云集智能视频创意站".lower()
    pids_to_kill = []

    if kernel32.Process32FirstW(snap, ctypes.byref(entry)):
        while True:
            pid = entry.th32ProcessID
            if pid != my_pid and _is_exe_instance(pid, base_prefix):
                pids_to_kill.append(pid)
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break

    kernel32.CloseHandle(snap)

    if not pids_to_kill:
        return

    # ★ 强杀进程(给 0x0001 权限:PROCESS_TERMINATE)
    PROCESS_TERMINATE = 0x0001
    PROCESS_QUERY_LIMITED_INFORMATION = 0x00100000
    STILL_ACTIVE = 259
    killed = []
    for pid in pids_to_kill:
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            if kernel32.TerminateProcess(handle, 0):
                killed.append(pid)
            kernel32.CloseHandle(handle)

    if killed:
        # ★ 等待旧进程真正退出(最多 2s),避免新进程启动后端口冲突
        for _ in range(20):
            time.sleep(0.1)
            still_alive = []
            for pid in killed:
                h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if h:
                    exit_code = ctypes.c_ulong()
                    if kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code)) and exit_code.value == STILL_ACTIVE:
                        still_alive.append(pid)
                    kernel32.CloseHandle(h)
            if not still_alive:
                break


def main():
    """
    ★ 2026-06-16:EXE 入口点
    - 强杀旧 EXE 实例(launcher 自己)
    - 然后导入 main.main()(EXE 内嵌的 main 模块)
    """
    _kill_old_instances()
    import main
    main.main()


if __name__ == "__main__":
    main()
