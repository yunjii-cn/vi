#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EXE 入口点 - 云集智能视频创意站
参考云集智能网联代理专家的 launcher.py
简洁设计：不 monkey-patch subprocess，避免破坏标准库行为
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


def _kill_old_instances():
    """
    ★ 2026-06-08(加强):杀掉所有同前缀的旧 EXE 进程,确保单实例
    - 匹配规则:EXE 文件名以"云集智能视频创意站"开头
    - 不依赖 `-v` 分隔符(避免部分旧 EXE 没版本号时漏杀)
    - 不区分大小写
    - 排除自身 PID
    """
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return

    my_exe = os.path.normcase(os.path.abspath(sys.executable))
    my_name = os.path.basename(my_exe)
    my_pid = ctypes.windll.kernel32.GetCurrentProcessId()

    # ★ 2026-06-08:匹配所有"云集智能视频创意站"开头的 EXE(任何版本/任何变体)
    base_prefix = "云集智能视频创意站".lower()

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

    pids_to_kill = []

    if kernel32.Process32FirstW(snap, ctypes.byref(entry)):
        while True:
            pid = entry.th32ProcessID
            exe_name = (entry.szExeFile or "").lower()
            # ★ 关键匹配:同前缀 + 排除自身 + 必须是 exe
            if (pid != my_pid
                    and exe_name.startswith(base_prefix)
                    and exe_name.endswith('.exe')):
                pids_to_kill.append(pid)
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break

    kernel32.CloseHandle(snap)

    # ★ 强杀进程(给 0x0001 权限:PROCESS_TERMINATE)
    PROCESS_TERMINATE = 0x0001
    killed = []
    for pid in pids_to_kill:
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            if kernel32.TerminateProcess(handle, 0):
                killed.append(pid)
            kernel32.CloseHandle(handle)

    if killed:
        # ★ 等待旧进程真正退出(最多 2s),避免新进程启动后端口冲突
        import time as _time
        for _ in range(20):
            _time.sleep(0.1)
            still_alive = []
            for pid in killed:
                h = kernel32.OpenProcess(0x00100000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                if h:
                    STILL_ACTIVE = 259
                    exit_code = ctypes.c_ulong()
                    if kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code)) and exit_code.value == STILL_ACTIVE:
                        still_alive.append(pid)
                    kernel32.CloseHandle(h)
            if not still_alive:
                break


_kill_old_instances()

import main
main.main()
