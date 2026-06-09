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


def _is_this_a_launcher_process(pid):
    """
    ★ 2026-06-09:判断指定 PID 是否为"启动器实例"（EXE 或开发模式的 main.py）
    - EXE 模式:exe 文件名以"云集智能视频创意站"开头
    - 开发模式:命令行包含 main.py 且工作目录在 dev/app 下
    - 双重保险,避免误杀其他 python 进程
    """
    if sys.platform != 'win32':
        return False
    try:
        # 用 WMI 不可靠,直接用 psutil(项目已依赖)
        import psutil
        proc = psutil.Process(pid)
        try:
            cmdline = proc.cmdline()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            return False
        if not cmdline:
            return False
        cmdline_str = ' '.join(cmdline).lower()
        # ★ 匹配条件1:EXE 模式(脚本名是"云集智能视频创意站"开头)
        if any(part.lower().startswith('云集智能视频创意站') and part.lower().endswith('.exe')
               for part in cmdline):
            return True
        # ★ 匹配条件2:开发模式(命令行包含 main.py 且当前在 dev/app 目录下)
        if any('main.py' in part.lower() for part in cmdline):
            # 进一步验证:进程的工作目录或脚本路径应该在 dev/app 下
            try:
                cwd = proc.cwd().lower()
                if 'dev\\app' in cwd or 'dev/app' in cwd:
                    return True
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
            # 或:命令行里包含绝对路径的 main.py,且路径含 dev/app
            for part in cmdline:
                if 'main.py' in part.lower() and ('dev\\app' in part.lower() or 'dev/app' in part.lower()):
                    return True
        return False
    except Exception:
        return False


def _kill_old_instances():
    """
    ★ 2026-06-08(加强):杀掉所有同前缀的旧 EXE 进程,确保单实例
    ★ 2026-06-09(扩展):开发模式下也支持杀掉旧的 main.py 实例
    - EXE 模式:匹配 exe 文件名以"云集智能视频创意站"开头
    - 开发模式:匹配 cmdline 含 main.py 且工作目录在 dev/app 下
    - 不区分大小写
    - 排除自身 PID
    - 不误杀其他 python 进程(双条件校验)
    """
    if sys.platform != 'win32':
        return

    my_pid = ctypes.windll.kernel32.GetCurrentProcessId()
    is_frozen = getattr(sys, 'frozen', False)

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
    # ★ EXE 模式:用 EXE 文件名前缀快速匹配
    if is_frozen:
        my_exe = os.path.normcase(os.path.abspath(sys.executable))
        base_prefix = "云集智能视频创意站".lower()

    if kernel32.Process32FirstW(snap, ctypes.byref(entry)):
        while True:
            pid = entry.th32ProcessID
            if pid == my_pid:
                # 跳过自身
                pass
            else:
                exe_name = (entry.szExeFile or "").lower()
                should_kill = False
                if is_frozen:
                    # EXE 模式:exe 文件名以"云集智能视频创意站"开头
                    should_kill = (
                        exe_name.startswith(base_prefix)
                        and exe_name.endswith('.exe')
                    )
                else:
                    # ★ 开发模式:python.exe 进程 + 详细 cmdline 校验
                    if exe_name in ('python.exe', 'pythonw.exe'):
                        should_kill = _is_this_a_launcher_process(pid)
                if should_kill:
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


def main():
    """
    ★ 2026-06-09:EXE/dev 共用入口
    - EXE 模式:launcher.py 自身即 __main__,此函数被直接调用
    - 开发模式:main.py 在其 __main__ 块里调用 _kill_old_instances() 后再 main(),
      不需要再走一遍这里的 main() 流程
    """
    _kill_old_instances()
    import main
    main.main()


if __name__ == "__main__":
    main()
