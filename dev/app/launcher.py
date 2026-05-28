import sys
import os
import ctypes
import ctypes.wintypes
import time

BRAND_NAME = "云集智能视频创意站"

if sys.platform == 'win32' and getattr(sys, 'frozen', False):

    class _NullWriter:
        def write(self, s):
            pass
        def flush(self):
            pass
        def isatty(self):
            return False

    sys.stdout = _NullWriter()
    sys.stderr = _NullWriter()


def _verify_brand():
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return
    exe_name = os.path.basename(sys.executable)
    if BRAND_NAME not in exe_name:
        correct_name = f"{BRAND_NAME}.exe"
        ctypes.windll.user32.MessageBoxW(
            0,
            f"可执行文件名已被修改，无法运行。\n\n当前文件名: {exe_name}\n正确文件名: {correct_name}\n\n请将文件名改回「{correct_name}」后重试。",
            "品牌校验失败",
            0x10
        )
        sys.exit(1)


_verify_brand()


def _kill_old_instances():
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return

    my_exe = os.path.normcase(os.path.abspath(sys.executable))
    my_pid = ctypes.windll.kernel32.GetCurrentProcessId()

    base_prefix = BRAND_NAME

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
            exe_name = entry.szExeFile.lower()
            if pid != my_pid and exe_name.startswith(base_prefix.lower()) and exe_name.endswith('.exe'):
                pids_to_kill.append(pid)
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break

    kernel32.CloseHandle(snap)

    PROCESS_TERMINATE = 0x0001
    for pid in pids_to_kill:
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            kernel32.TerminateProcess(handle, 0)
            kernel32.CloseHandle(handle)

    if pids_to_kill:
        time.sleep(0.5)


_kill_old_instances()

_cleanup_path = ""
for arg in sys.argv[1:]:
    if arg.startswith("--cleanup="):
        _cleanup_path = arg[len("--cleanup="):]

if _cleanup_path and os.path.isfile(_cleanup_path):
    for _ in range(10):
        try:
            os.remove(_cleanup_path)
            break
        except PermissionError:
            time.sleep(0.5)

try:
    import main
    main.main()
except Exception as _e:
    import traceback
    _tb = traceback.format_exc()
    try:
        _exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(_exe_dir, "crash.log"), "w", encoding="utf-8") as _lf:
            _lf.write(_tb)
    except Exception:
        pass
    if sys.platform == 'win32':
        ctypes.windll.user32.MessageBoxW(
            0,
            f"程序启动失败:\n\n{_tb[:2000]}\n\n错误日志已保存到: {_exe_dir}\\crash.log",
            "启动错误",
            0x10
        )
    sys.exit(1)
