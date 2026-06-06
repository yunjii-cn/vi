#!/usr/bin/env python3
"""
文件用途: PyQt6 GUI启动器主程序
项目名称: 云集智能视频创意站 (LTX-2.3)
版本: v1.0.0+

核心功能:
- LTX Desktop 安装检测与路径定位
- Python 环境自动发现（本地环境包 / 系统环境）
- 后端核心引擎启动（端口3000）
- 前端 UI 服务启动（端口4000）
- 服务状态实时监控
- GPU/VRAM 信息显示
- 模型目录配置
- 运行日志实时显示
- 系统托盘

关键类:
- ServiceMonitor: 服务端口监控线程
- ServiceProcess: 服务启动线程
- ConfigManager: 配置管理器
- ServiceCard: 服务状态卡片
- SplashScreen: 自定义启动画面
- MainWindow: 主窗口

依赖:
- PyQt6
- psutil
"""

import sys
import os

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
import subprocess
import socket
import json
import time
import re
import webbrowser
import threading
import shutil
import urllib.request
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

try:
    import debug_hub as _DBG
except ImportError:
    _DBG = None

_IS_FROZEN = sys.platform == 'win32' and getattr(sys, 'frozen', False)
_EXE_DIR = os.path.dirname(sys.executable) if _IS_FROZEN else os.path.dirname(os.path.abspath(__file__))

def _find_install_root(start_dir=None):
    d = start_dir or _EXE_DIR
    for _ in range(5):
        if os.path.isdir(os.path.join(d, "app")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def _load_project_config():
    _DEFAULTS = {
        "brand_name": "云集智能视频创意站",
        "repos": {
            "github": "yunjii-cn/vi",
            "gitee": "yunjii/vi"
        },
        "paths": {
            "version_json": "release/version.json",
            "dev": "dev",
            "app": "app",
            "ver": "ver",
            "dist": "dist",
            "build": "build",
            "release": "release",
            "lock_file": ".yunji.lock"
        }
    }

    def _deep_merge(base, override):
        result = dict(base)
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = _deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    for search_path in [
        os.path.join(getattr(sys, '_MEIPASS', ''), 'project.json'),
        os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), 'project.json'),
    ]:
        try:
            with open(search_path, 'r', encoding='utf-8') as f:
                return _deep_merge(_DEFAULTS, json.load(f))
        except Exception:
            continue
    return _DEFAULTS


_CFG = _load_project_config()

BRAND_NAME = _CFG["brand_name"]
LOCK_FILE = _CFG["paths"]["lock_file"]
VER_DIR = _CFG["paths"]["ver"]
APP_DIR = _CFG["paths"]["app"]


def _create_hardlink(src, dst):
    try:
        if os.path.exists(dst):
            os.remove(dst)
        os.link(src, dst)
        return True
    except OSError:
        pass
    try:
        if os.path.exists(dst):
            os.remove(dst)
        shutil.copy2(src, dst)
        return True
    except Exception:
        return False


def _create_desktop_shortcut(entry_exe):
    """在桌面创建快捷方式，确保唯一性（避免版本切换积累）"""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        CSIDL_DESKTOP = 0x0000
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOP, None, 0, buf)
        desktop = buf.value
        if not desktop:
            return False

        shortcut_path = os.path.join(desktop, f"{BRAND_NAME}.lnk")

        # 先删除已有快捷方式，确保唯一
        if os.path.exists(shortcut_path):
            try:
                os.remove(shortcut_path)
            except Exception:
                pass

        work_dir = os.path.dirname(entry_exe)

        # 写临时PS1脚本创建快捷方式（避免路径中的引号转义问题）
        ps1_content = (
            f'$ws = New-Object -ComObject WScript.Shell\n'
            f'$sc = $ws.CreateShortcut("{shortcut_path}")\n'
            f'$sc.TargetPath = "{entry_exe}"\n'
            f'$sc.WorkingDirectory = "{work_dir}"\n'
            f'$sc.IconLocation = "{entry_exe},0"\n'
            f'$sc.Save()\n'
        )

        ps1_path = os.path.join(os.environ.get('TEMP', '.'), '_yunji_shortcut.ps1')
        with open(ps1_path, 'w', encoding='utf-8-sig') as f:
            f.write(ps1_content)

        subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'Bypass', '-File', ps1_path],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=10
        )

        try:
            os.remove(ps1_path)
        except Exception:
            pass

        return os.path.exists(shortcut_path)
    except Exception:
        return False


def _find_dev_dir():
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        d = exe_dir
        for _ in range(5):
            if os.path.isfile(os.path.join(d, LOCK_FILE)):
                if os.path.isdir(os.path.join(d, VER_DIR)) and os.path.isfile(os.path.join(d, f"{BRAND_NAME}.exe")):
                    return d
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        exe_basename = os.path.basename(sys.executable)
        if exe_basename == f"{BRAND_NAME}.exe" and os.path.isfile(os.path.join(exe_dir, LOCK_FILE)):
            return exe_dir
        return _self_deploy(exe_dir)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _self_deploy(exe_dir):
    src_exe = os.path.abspath(sys.executable)
    exe_basename = os.path.basename(src_exe)
    if BRAND_NAME not in exe_basename:
        sys.exit(1)

    deploy_dir = os.path.join(exe_dir, BRAND_NAME)
    lock_path = os.path.join(deploy_dir, LOCK_FILE)
    already_deployed = os.path.isdir(deploy_dir) and os.path.isfile(lock_path)

    if already_deployed:
        entry_exe = os.path.join(deploy_dir, f"{BRAND_NAME}.exe")
        if os.path.isfile(entry_exe) and os.path.normpath(src_exe) != os.path.normpath(entry_exe):
            subprocess.Popen(
                f'ping -n 2 127.0.0.1 >nul & start "" "{entry_exe}" --cleanup="{src_exe}"',
                shell=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            os._exit(0)
        return deploy_dir

    os.makedirs(deploy_dir, exist_ok=True)

    ver_dir = os.path.join(deploy_dir, VER_DIR)
    os.makedirs(ver_dir, exist_ok=True)
    app_dir = os.path.join(deploy_dir, APP_DIR)
    os.makedirs(app_dir, exist_ok=True)

    # 从EXE内部释放嵌入的resources（ui/backend/patches）
    meipass = getattr(sys, '_MEIPASS', '')
    if meipass:
        resources_dst = os.path.join(app_dir, "resources")
        os.makedirs(resources_dst, exist_ok=True)
        _IGNORE_PATTERNS = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
        for res_name in ("ui", "backend", "patches"):
            src = os.path.join(meipass, "resources", res_name)
            dst = os.path.join(resources_dst, res_name)
            if os.path.isdir(src):
                try:
                    if os.path.exists(dst):
                        shutil.rmtree(dst, ignore_errors=True)
                    shutil.copytree(src, dst, ignore=_IGNORE_PATTERNS)
                except Exception:
                    pass
        # 释放versions.json到app目录
        vs_src = os.path.join(meipass, "versions.json")
        vs_dst = os.path.join(app_dir, "versions.json")
        if os.path.isfile(vs_src) and not os.path.isfile(vs_dst):
            try:
                shutil.copy2(vs_src, vs_dst)
            except Exception:
                pass
        # 释放gitlog.json到app目录
        cl_src = os.path.join(meipass, "gitlog.json")
        cl_dst = os.path.join(app_dir, "gitlog.json")
        if os.path.isfile(cl_src) and not os.path.isfile(cl_dst):
            try:
                shutil.copy2(cl_src, cl_dst)
            except Exception:
                pass

    with open(lock_path, "w", encoding="utf-8") as f:
        f.write("yunji")

    if not exe_basename.startswith(BRAND_NAME + "-v"):
        m = re.search(r'v(\d+\.\d+\.\d+\.\d+)', exe_basename)
        ver_str = m.group(1) if m else datetime.now().strftime("%Y.%m.%d.%H%M")
        new_name = f"{BRAND_NAME}-v{ver_str}.exe"
    else:
        new_name = exe_basename

    target_exe = os.path.join(ver_dir, new_name)
    if os.path.normpath(src_exe) != os.path.normpath(target_exe):
        shutil.copy2(src_exe, target_exe)

    entry_exe = os.path.join(deploy_dir, f"{BRAND_NAME}.exe")
    if not os.path.isfile(entry_exe):
        _create_hardlink(target_exe, entry_exe)

    if os.path.normpath(src_exe) != os.path.normpath(entry_exe):
        if os.path.isfile(entry_exe):
            # 使用 shell=True 确保中文路径正确处理
            subprocess.Popen(
                f'ping -n 2 127.0.0.1 >nul & start "" "{entry_exe}" --cleanup="{src_exe}"',
                shell=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        os._exit(0)

    return deploy_dir


def _bootstrap_download_resources(install_root, splash=None):
    import zipfile
    import ssl
    import tempfile

    app_dir = os.path.join(install_root, "app")
    resources_dir = os.path.join(app_dir, "resources")
    os.makedirs(resources_dir, exist_ok=True)
    for sub in ("data", "models", "outputs", "uploads", "config"):
        os.makedirs(os.path.join(install_root, "data", sub), exist_ok=True)
    for sub in ("logs", "cache", "debug"):
        os.makedirs(os.path.join(install_root, "temp", sub), exist_ok=True)

    result_holder = [None]
    lock = threading.Lock()

    def try_download(key):
        source = UPDATE_SOURCES[key]
        url = source.get("resources_url", "")
        if not url:
            return
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                data = resp.read()
            with lock:
                if result_holder[0] is None:
                    result_holder[0] = (key, data)
        except Exception:
            pass

    threads = []
    for key in UPDATE_SOURCES:
        t = threading.Thread(target=try_download, args=(key,), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=45)

    if result_holder[0] is None:
        return False, "无法从任何源下载核心文件，请检查网络连接"

    winning_key, zip_data = result_holder[0]
    source_name = UPDATE_SOURCES[winning_key]["name"]

    tmp_zip = os.path.join(tempfile.gettempdir(), "_vi_bootstrap.zip")
    try:
        with open(tmp_zip, "wb") as f:
            f.write(zip_data)

        with zipfile.ZipFile(tmp_zip, "r") as zf:
            names = zf.namelist()

            if winning_key == "gitee":
                prefix = ""
                for n in names:
                    if n.endswith("dev/app/resources/"):
                        prefix = n
                        break
                if not prefix:
                    for n in names:
                        m = re.search(r'^(.+?/)?dev/app/resources/', n)
                        if m:
                            prefix = m.group(0)
                            break

                if prefix:
                    for name in names:
                        if name.startswith(prefix) and not name.endswith("/"):
                            rel = name[len(prefix):]
                            if not rel:
                                continue
                            target = os.path.join(resources_dir, rel.replace("/", os.sep))
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            with zf.open(name) as src, open(target, "wb") as dst:
                                dst.write(src.read())
                else:
                    for name in names:
                        if "resources/" in name and not name.endswith("/"):
                            idx = name.index("resources/")
                            rel = name[idx + len("resources/"):]
                            if not rel:
                                continue
                            target = os.path.join(resources_dir, rel.replace("/", os.sep))
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            with zf.open(name) as src, open(target, "wb") as dst:
                                dst.write(src.read())
            else:
                resources_prefix = ""
                for n in names:
                    if n.endswith("app/resources/"):
                        resources_prefix = n
                        break
                    m = re.search(r'^(.+?/)?app/resources/', n)
                    if m:
                        resources_prefix = m.group(0)
                        break

                if resources_prefix:
                    for name in names:
                        if name.startswith(resources_prefix) and not name.endswith("/"):
                            rel = name[len(resources_prefix):]
                            if not rel:
                                continue
                            target = os.path.join(resources_dir, rel.replace("/", os.sep))
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            with zf.open(name) as src, open(target, "wb") as dst:
                                dst.write(src.read())
                else:
                    zf.extractall(app_dir)

    except Exception as e:
        return False, f"解压核心文件失败: {e}"
    finally:
        try:
            os.unlink(tmp_zip)
        except Exception:
            pass

    backend_dir = os.path.join(resources_dir, "backend")
    patches_dir = os.path.join(resources_dir, "patches")
    ui_dir = os.path.join(resources_dir, "ui")
    has_backend = os.path.isdir(backend_dir) and len(os.listdir(backend_dir)) > 0
    has_patches = os.path.isdir(patches_dir) and len(os.listdir(patches_dir)) > 0
    has_ui = os.path.isdir(ui_dir) and len(os.listdir(ui_dir)) > 0

    if not (has_backend and has_patches and has_ui):
        missing = []
        if not has_backend:
            missing.append("backend")
        if not has_patches:
            missing.append("patches")
        if not has_ui:
            missing.append("ui")
        return False, f"核心文件不完整，缺少: {', '.join(missing)}"

    return True, f"核心文件下载完成（via {source_name}）"

def _find_debug_flag():
    if _IS_FROZEN:
        temp_debug = os.path.join(_EXE_DIR, "temp", "debug", ".debug")
        if os.path.exists(temp_debug):
            return temp_debug
        root_debug = os.path.join(_EXE_DIR, ".debug")
        if os.path.exists(root_debug):
            return root_debug
        return temp_debug
    else:
        dev_dir = os.path.dirname(_EXE_DIR)
        dev_temp_debug = os.path.join(dev_dir, "temp", "debug", ".debug")
        if os.path.exists(dev_temp_debug):
            return dev_temp_debug
        dev_root_debug = os.path.join(dev_dir, ".debug")
        if os.path.exists(dev_root_debug):
            return dev_root_debug
        root_debug = os.path.join(_EXE_DIR, ".debug")
        if os.path.exists(root_debug):
            return root_debug
        return dev_temp_debug

_DEBUG_FLAG = _find_debug_flag()

_DEBUG_TAGS = set()
_DEBUG_MODE = False

def _read_debug_tags():
    global _DEBUG_TAGS, _DEBUG_MODE
    tags = set()
    try:
        if os.path.exists(_DEBUG_FLAG):
            with open(_DEBUG_FLAG, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            if content:
                for line in content.splitlines():
                    tag = line.strip().upper()
                    if tag and not tag.startswith('#'):
                        tags.add(tag)
            else:
                tags.add('*')
        else:
            return
    except Exception:
        tags.add('*')
    _DEBUG_TAGS = tags
    _DEBUG_MODE = len(tags) > 0

_read_debug_tags()

# 冻结模式且非调试时隐藏控制台输出
if _IS_FROZEN and not _DEBUG_MODE:
    class _NullWriter:
        def write(self, *args, **kwargs):
            return 0
        def flush(self):
            pass
        def isatty(self):
            return False
    sys.stdout = _NullWriter()
    sys.stderr = _NullWriter()

# ── 单实例管理（Windows 命名内核对象） ──────────────────────────
# 版本化 Mutex (YunJiVideo_SingleInstance_v{VERSION})：同版本检测
# 命名共享内存 (YunJiVideo_Path_v{VERSION})：存储运行中实例的 EXE 路径
# 跨版本 Shutdown 事件 (YunJiVideo_ShutdownEvent)：通知旧版本优雅退出
_KERNEL32 = None
_USER32 = None
_INSTANCE_MUTEX = None
_MAIN_WINDOW_REF = None

def _init_win32():
    """延迟初始化 Win32 API"""
    global _KERNEL32, _USER32
    if _KERNEL32 is None and sys.platform == 'win32':
        import ctypes
        _KERNEL32 = ctypes.windll.kernel32
        _USER32 = ctypes.windll.user32


def _ensure_single_instance():
    """
    单实例逻辑（简化版）：
    检测到任何已运行的云集进程 → 一律杀掉，然后继续启动。
    不尝试激活旧窗口，避免窗口置顶失败导致"打不开"的问题。
    """
    global _INSTANCE_MUTEX

    if sys.platform != 'win32':
        return

    try:
        global _KERNEL32
        if _KERNEL32 is None:
            import ctypes
            _KERNEL32 = ctypes.windll.kernel32

        import ctypes

        mutex_name = "YunJiVideo_SingleInstance_Global"

        mutex = _KERNEL32.CreateMutexW(None, True, mutex_name)
        already_exists = (ctypes.GetLastError() == 183)

        if not already_exists:
            _INSTANCE_MUTEX = mutex
            return

        _KERNEL32.CloseHandle(mutex)

        _kill_old_processes()

        import time
        for _ in range(30):
            time.sleep(0.1)
            mutex = _KERNEL32.CreateMutexW(None, True, mutex_name)
            if ctypes.GetLastError() != 183:
                _INSTANCE_MUTEX = mutex
                return
            _KERNEL32.CloseHandle(mutex)

        mutex = _KERNEL32.CreateMutexW(None, True, mutex_name)
        _INSTANCE_MUTEX = mutex

    except Exception:
        pass


def _get_exe_path():
    if getattr(sys, 'frozen', False):
        return sys.executable
    return os.path.abspath(__file__)


def _kill_old_processes():
    """杀掉所有已运行的云集进程（排除自身）"""
    try:
        import subprocess
        current_pid = os.getpid()
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5, startupinfo=si,
            creationflags=subprocess.CREATE_NO_WINDOW | 0x00000008,
        )
        if result.returncode == 0:
            prefix = "云集智能视频创意站"
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = line.strip().strip('"').split('","')
                if len(parts) >= 2:
                    exe_name = parts[0]
                    if exe_name.startswith(prefix) and exe_name.endswith(".exe"):
                        try:
                            pid = int(parts[1])
                            if pid != current_pid:
                                os.kill(pid, 9)
                        except (ValueError, OSError):
                            pass
    except Exception:
        pass


def _is_shutdown_signaled():
    return False


def _cleanup_single_instance():
    global _INSTANCE_MUTEX
    try:
        if _INSTANCE_MUTEX:
            _KERNEL32.ReleaseMutex(_INSTANCE_MUTEX)
            _KERNEL32.CloseHandle(_INSTANCE_MUTEX)
            _INSTANCE_MUTEX = None
    except Exception:
        pass


def _validate_exe_filename():
    if not _IS_FROZEN:
        return
    try:
        exe_path = sys.executable
        exe_name = os.path.basename(exe_path)
        if APP_NAME not in exe_name:
            import ctypes
            correct_name = APP_NAME + ".exe"
            result = ctypes.windll.user32.MessageBoxW(
                0,
                f"检测到程序文件名已被修改！\n\n"
                f"当前文件名: {exe_name}\n"
                f"正确文件名: {correct_name}\n\n"
                f"文件名被修改可能影响程序正常运行。\n"
                f"是否自动修正文件名并重新启动？\n\n"
                f"点击「是」自动修正并重启\n"
                f"点击「否」退出程序",
                f"{APP_NAME} - 文件名异常",
                0x24
            )
            if result == 6:
                exe_dir = os.path.dirname(exe_path)
                target_path = os.path.join(exe_dir, correct_name)
                try:
                    shutil.copy2(exe_path, target_path)
                    subprocess.Popen(
                        f'ping -n 2 127.0.0.1 >nul & start "" "{target_path}"',
                        shell=True,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    sys.exit(0)
                except Exception:
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        f"自动修正失败，请手动将文件重命名为:\n{correct_name}",
                        f"{APP_NAME} - 修正失败",
                        0x10
                    )
                    sys.exit(1)
            else:
                sys.exit(1)
    except SystemExit:
        raise
    except Exception:
        pass

# 调试模式初始化
if _DEBUG_MODE:
    try:
        if _DBG:
            _DBG.init()
            class _TeeWriter:
                def __init__(self, original):
                    self.original = original
                def write(self, data):
                    if not data:
                        return 0
                    try:
                        self.original.write(data)
                    except Exception:
                        pass
                    try:
                        _DBG.dbg("MAIN", data.rstrip(), "info")
                    except Exception:
                        pass
                    return len(data) if isinstance(data, str) else 0
                def flush(self):
                    try:
                        self.original.flush()
                    except Exception:
                        pass
                def isatty(self):
                    return False
            sys.stdout = _TeeWriter(sys.stdout)
            sys.stderr = _TeeWriter(sys.stderr)
            _DBG.dbg("MAIN", f"调试模式已开启", "ok")
    except Exception:
        pass

if sys.platform == 'win32':
    _HIDDEN_FLAGS = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        import ctypes
        _SEM = 0x0001 | 0x0002 | 0x0004 | 0x8000
        ctypes.windll.kernel32.SetErrorMode(_SEM)
    except Exception:
        pass
else:
    _HIDDEN_FLAGS = 0


def _hidden_startupinfo():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si


def _clean_python_env(kwargs):
    env = kwargs.get('env')
    if env is None:
        env = os.environ.copy()
        kwargs['env'] = env
    if 'PYTHONHOME' in env:
        del env['PYTHONHOME']
    return kwargs


def hidden_run(*args, **kwargs):
    si = kwargs.get('startupinfo', subprocess.STARTUPINFO())
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    kwargs['startupinfo'] = si
    if sys.platform == 'win32':
        if 'creationflags' in kwargs:
            kwargs['creationflags'] = kwargs['creationflags'] | _HIDDEN_FLAGS
        else:
            kwargs['creationflags'] = _HIDDEN_FLAGS
        kwargs.setdefault('stdin', subprocess.DEVNULL)
    if args and isinstance(args[0], list) and args[0] and 'python' in os.path.basename(args[0][0]).lower():
        kwargs = _clean_python_env(kwargs)
    return subprocess.run(*args, **kwargs)


def hidden_popen(*args, **kwargs):
    si = kwargs.get('startupinfo', subprocess.STARTUPINFO())
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    kwargs['startupinfo'] = si
    if sys.platform == 'win32':
        if 'creationflags' in kwargs:
            kwargs['creationflags'] = kwargs['creationflags'] | _HIDDEN_FLAGS
        else:
            kwargs['creationflags'] = _HIDDEN_FLAGS
        kwargs.setdefault('stdin', subprocess.DEVNULL)
    if args and isinstance(args[0], list) and args[0] and 'python' in os.path.basename(args[0][0]).lower():
        kwargs = _clean_python_env(kwargs)
    return subprocess.Popen(*args, **kwargs)


from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QFrame, QGridLayout, QGroupBox,
    QMessageBox, QSystemTrayIcon, QMenu, QFileDialog,
    QStackedWidget, QSizePolicy, QScrollArea, QSplashScreen,
    QComboBox, QLineEdit, QSpinBox, QCheckBox, QProgressBar,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextBrowser, QTreeWidget, QTreeWidgetItem,
    QSlider, QDial, QCalendarWidget, QDateEdit, QTimeEdit,
    QColorDialog, QFontDialog, QInputDialog, QWizard, QDialog,
    QButtonGroup, QAbstractButton, QHeaderView,
)
from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal, QTimer, QRectF, pyqtProperty, QProcess, QPropertyAnimation
from PyQt6.QtGui import QFont, QColor, QIcon, QPixmap, QPainter, QLinearGradient, QPen, QPalette
from PyQt6.QtSvg import QSvgRenderer


def get_version_from_filename():
    try:
        if hasattr(sys, 'frozen'):
            exe_path = sys.executable
            exe_name = os.path.basename(exe_path)
            match = re.search(r'v(\d+\.\d+\.\d+\.\d+)', exe_name)
            if match:
                return match.group(1)
        return datetime.now().strftime("%Y.%m.%d.%H%M")
    except:
        return datetime.now().strftime("%Y.%m.%d.%H%M")


VERSION = get_version_from_filename()
APP_NAME = "云集智能视频创意站"
COMPANY_NAME = "武汉市云集智能科技有限公司"

# 执行单实例检测（需要 VERSION 和 APP_NAME）
_ensure_single_instance()

_CHECK_ICON_PATH = None

def _get_check_icon_path():
    global _CHECK_ICON_PATH
    if _CHECK_ICON_PATH and os.path.exists(_CHECK_ICON_PATH):
        return _CHECK_ICON_PATH
    pm = QPixmap(16, 16)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("#FF0000"), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawLine(3, 8, 6, 12)
    p.drawLine(6, 12, 13, 4)
    p.end()
    tmp_dir = tempfile.gettempdir()
    icon_path = os.path.join(tmp_dir, "yunji_red_check.png")
    pm.save(icon_path, "PNG")
    _CHECK_ICON_PATH = icon_path
    return icon_path


def _red_check_checkbox_style():
    icon_path = _get_check_icon_path().replace("\\", "/")
    return f"""
        QCheckBox {{
            color: #CCCCCC; font-size: 12px; background: transparent;
            spacing: 6px;
        }}
        QCheckBox::indicator {{
            width: 16px; height: 16px; border-radius: 3px;
            border: 1px solid #555555; background-color: #1A1A1A;
        }}
        QCheckBox::indicator:checked {{
            background-color: #1A1A1A; border-color: #FF0000;
            image: url({icon_path});
        }}
        QCheckBox::indicator:hover {{
            border-color: #FF0000;
        }}
    """

DEFAULT_BACKEND_PORT = 3000
DEFAULT_FRONTEND_PORT = 4000

SERVICES = {
    "backend": {
        "name": "核心引擎",
        "port": DEFAULT_BACKEND_PORT,
        "url": f"http://127.0.0.1:{DEFAULT_BACKEND_PORT}",
        "icon": "⚙️",
        "color": "#FF0000",
        "desc": "LTX-2.3 视频生成引擎",
    },
    "frontend": {
        "name": "AI视频工作站",
        "port": DEFAULT_FRONTEND_PORT,
        "url": f"http://127.0.0.1:{DEFAULT_FRONTEND_PORT}",
        "icon": "🖥️",
        "color": "#43A047",
        "desc": "AI视频创意工作站界面",
    },
}

GLOBAL_STYLE = """
QMainWindow {
    background-color: #0D0D0D;
}
QWidget {
    background-color: #0D0D0D;
    color: #F0F0F0;
    font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
}
QPushButton {
    background-color: #252525;
    color: #FFFFFF;
    border: 1px solid #333333;
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 12px;
}
QPushButton:hover {
    background-color: #333333;
    border-color: #444444;
}
QPushButton:disabled {
    background-color: #1A1A1A;
    color: #555555;
    border-color: #222222;
}
QLabel {
    color: #F0F0F0;
}
QScrollArea {
    border: none;
    background-color: #0D0D0D;
}
QFrame#cardFrame {
    background-color: #1A1A1A;
    border: 1px solid #333333;
    border-radius: 10px;
}
QTextEdit {
    background-color: #0A0A0A;
    border: 1px solid #333333;
    border-radius: 6px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 11px;
    color: #B0B0C0;
    padding: 4px;
}
QGroupBox {
    border: none;
    margin-top: 14px;
    padding-top: 18px;
    font-weight: bold;
    font-size: 13px;
    color: #B0B0D0;
    background-color: transparent;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
"""


class ServiceMonitor(QObject):
    status_changed = pyqtSignal(str, bool)

    def __init__(self, check_interval=3, parent=None):
        super().__init__(parent)
        self.check_interval = check_interval
        self._status_cache = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_all_async)

    def start(self):
        self._timer.start(self.check_interval * 1000)

    def _check_all_async(self):
        import threading
        results = {}
        done = threading.Event()

        def _worker():
            for sid, svc in SERVICES.items():
                results[sid] = self._check_port(svc["port"])
            done.set()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        def _emit_results():
            if not done.is_set():
                QTimer.singleShot(50, _emit_results)
                return
            for sid, alive in results.items():
                if self._status_cache.get(sid) != alive:
                    self._status_cache[sid] = alive
                    self.status_changed.emit(sid, alive)

        _emit_results()

    def _check_port(self, port):
        try:
            conn = socket.create_connection(('127.0.0.1', port), timeout=1)
            conn.close()
            return True
        except Exception:
            return False

    def stop(self):
        self._timer.stop()


class ServiceProcess(QThread):
    output_received = pyqtSignal(str, str)
    process_finished = pyqtSignal(int, int)

    def __init__(self, service_id, cmd, cwd, env=None, parent=None):
        super().__init__(parent)
        self.service_id = service_id
        self.cmd = cmd
        self.cwd = cwd
        self.env = env or os.environ.copy()
        self.process = None

    def run(self):
        try:
            if sys.platform == 'win32':
                try:
                    import ctypes
                    ctypes.windll.ole32.CoInitializeEx(None, 0x0)
                except Exception:
                    pass
            self.process = hidden_popen(
                self.cmd,
                cwd=self.cwd,
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
            )
            for line in iter(self.process.stdout.readline, ''):
                if line:
                    self.output_received.emit(self.service_id, line.strip())
            exit_code = self.process.wait()
            self.process_finished.emit(exit_code, 0)
        except Exception as e:
            self.output_received.emit(self.service_id, f"[ERROR] {e}")
            self.process_finished.emit(1, 0)
        finally:
            if sys.platform == 'win32':
                try:
                    import ctypes
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass

    def terminate(self):
        if self.process:
            try:
                pid = self.process.pid
                hidden_run(
                    ['taskkill', '/F', '/PID', str(pid)],
                    capture_output=True, timeout=5
                )
                self.process.wait(timeout=5)
            except:
                try:
                    self.process.kill()
                except:
                    pass


_gitee_token_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gitee_token")
GITEE_TOKEN = ""
if os.path.exists(_gitee_token_path):
    try:
        with open(_gitee_token_path, "r") as _f:
            GITEE_TOKEN = _f.read().strip()
    except Exception:
        pass

_GITEE_TOKEN_PARAM = f"&access_token={GITEE_TOKEN}" if GITEE_TOKEN else ""

UPDATE_SOURCES = {
    "github_mirror": {
        "name": "GitHub镜像",
        "version_url": "https://ghgo.xyz/https://raw.githubusercontent.com/yunjii-cn/vi/main/dev/app/versions.json",
        "commits_url": "https://ghgo.xyz/https://api.github.com/repos/yunjii-cn/vi/commits?per_page=100",
        "download_url_tpl": "https://github.com/yunjii-cn/vi/releases/download/v{version}/{filename}",
        "releases_url": "https://ghgo.xyz/https://api.github.com/repos/yunjii-cn/vi/releases",
        "resources_url": "https://github.com/yunjii-cn/vi/releases/latest/download/resources.zip",
        "is_api": False,
    },
    "github": {
        "name": "GitHub",
        "version_url": "https://raw.githubusercontent.com/yunjii-cn/vi/main/dev/app/versions.json",
        "commits_url": "https://api.github.com/repos/yunjii-cn/vi/commits?per_page=100",
        "download_url_tpl": "https://github.com/yunjii-cn/vi/releases/download/v{version}/{filename}",
        "releases_url": "https://api.github.com/repos/yunjii-cn/vi/releases",
        "resources_url": "https://github.com/yunjii-cn/vi/releases/latest/download/resources.zip",
        "is_api": False,
    },
    "gitee": {
        "name": "Gitee",
        "version_url": f"https://gitee.com/api/v5/repos/yunjii/vi/contents/dev/app/versions.json?ref=main{_GITEE_TOKEN_PARAM}",
        "commits_url": f"https://gitee.com/api/v5/repos/yunjii/vi/commits?per_page=100{_GITEE_TOKEN_PARAM}",
        "download_url_tpl": f"https://gitee.com/yunjii/vi/releases/download/v{{version}}/{{filename}}{_GITEE_TOKEN_PARAM}",
        "releases_url": f"https://gitee.com/api/v5/repos/yunjii/vi/releases?per_page=10{_GITEE_TOKEN_PARAM}",
        "resources_url": f"https://gitee.com/yunjii/vi/repository/archive/main.zip{_GITEE_TOKEN_PARAM}",
        "is_api": True,
    },
}

MIRRORS = {
    "pip": "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "pip_extra": "https://download.pytorch.org/whl/cu128",
    "pip_fallback": "https://pypi.org/simple/",
    "hf_mirror": "https://hf-mirror.com",
    "uv_github": "https://github.com/astral-sh/uv/releases/latest/download/",
    "uv_mirror": "https://ghgo.xyz/https://github.com/astral-sh/uv/releases/latest/download/",
}

MIRROR_SOURCES = {
    "tsinghua": {
        "pip": "https://pypi.tuna.tsinghua.edu.cn/simple/",
        "pip_extra": "https://download.pytorch.org/whl/cu128",
        "pip_fallback": "https://pypi.org/simple/",
        "uv_urls": [
            ("https://ghproxy.net/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GHProxy.net"),
            ("https://ghfast.top/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GHFast"),
            ("https://gh-proxy.com/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GH-Proxy"),
            ("https://ghgo.xyz/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GHGo"),
            ("https://mirror.ghproxy.com/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "Mirror.GHProxy"),
            ("https://ghps.cc/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GHPS"),
            ("https://gh.api.99988866.xyz/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GH-99988866"),
            ("https://kkgithub.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "KKGitHub"),
            ("https://bgithub.xyz/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "BGitHub"),
            ("https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GitHub直连"),
        ],
        "ltx_urls": [
            ("https://ghproxy.net/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GHProxy.net"),
            ("https://ghfast.top/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GHFast"),
            ("https://gh-proxy.com/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GH-Proxy"),
            ("https://ghgo.xyz/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GHGo"),
            ("https://mirror.ghproxy.com/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "Mirror.GHProxy"),
            ("https://ghps.cc/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GHPS"),
            ("https://gh.api.99988866.xyz/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GH-99988866"),
            ("https://kkgithub.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "KKGitHub"),
            ("https://bgithub.xyz/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "BGitHub"),
            ("https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GitHub直连"),
        ],
        "ltx2_urls": [
            ("https://ghproxy.net/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GHProxy.net"),
            ("https://ghfast.top/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GHFast"),
            ("https://gh-proxy.com/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GH-Proxy"),
            ("https://ghgo.xyz/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GHGo"),
            ("https://mirror.ghproxy.com/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "Mirror.GHProxy"),
            ("https://ghps.cc/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GHPS"),
            ("https://gh.api.99988866.xyz/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GH-99988866"),
            ("https://kkgithub.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "KKGitHub"),
            ("https://bgithub.xyz/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "BGitHub"),
            ("https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GitHub直连"),
        ],
        "hf_endpoint": "https://hf-mirror.com",
        "test_host": "pypi.tuna.tsinghua.edu.cn",
        "test_port": 443,
        "label": "清华镜像",
    },
    "aliyun": {
        "pip": "https://mirrors.aliyun.com/pypi/simple/",
        "pip_extra": "https://download.pytorch.org/whl/cu128",
        "pip_fallback": "https://pypi.org/simple/",
        "uv_urls": [
            ("https://ghproxy.net/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GHProxy.net"),
            ("https://ghfast.top/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GHFast"),
            ("https://gh-proxy.com/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GH-Proxy"),
            ("https://ghgo.xyz/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GHGo"),
            ("https://mirror.ghproxy.com/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "Mirror.GHProxy"),
            ("https://ghps.cc/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GHPS"),
            ("https://gh.api.99988866.xyz/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GH-99988866"),
            ("https://kkgithub.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "KKGitHub"),
            ("https://bgithub.xyz/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "BGitHub"),
            ("https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GitHub直连"),
        ],
        "ltx_urls": [
            ("https://ghproxy.net/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GHProxy.net"),
            ("https://ghfast.top/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GHFast"),
            ("https://gh-proxy.com/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GH-Proxy"),
            ("https://ghgo.xyz/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GHGo"),
            ("https://mirror.ghproxy.com/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "Mirror.GHProxy"),
            ("https://ghps.cc/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GHPS"),
            ("https://gh.api.99988866.xyz/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GH-99988866"),
            ("https://kkgithub.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "KKGitHub"),
            ("https://bgithub.xyz/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "BGitHub"),
            ("https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GitHub直连"),
        ],
        "ltx2_urls": [
            ("https://ghproxy.net/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GHProxy.net"),
            ("https://ghfast.top/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GHFast"),
            ("https://gh-proxy.com/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GH-Proxy"),
            ("https://ghgo.xyz/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GHGo"),
            ("https://mirror.ghproxy.com/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "Mirror.GHProxy"),
            ("https://ghps.cc/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GHPS"),
            ("https://gh.api.99988866.xyz/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GH-99988866"),
            ("https://kkgithub.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "KKGitHub"),
            ("https://bgithub.xyz/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "BGitHub"),
            ("https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GitHub直连"),
        ],
        "hf_endpoint": "https://hf-mirror.com",
        "test_host": "mirrors.aliyun.com",
        "test_port": 443,
        "label": "阿里云镜像",
    },
    "official": {
        "pip": "https://pypi.org/simple/",
        "pip_extra": "https://download.pytorch.org/whl/cu128",
        "pip_fallback": "https://pypi.org/simple/",
        "uv_urls": [
            ("https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GitHub直连"),
            ("https://ghproxy.net/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GHProxy.net"),
            ("https://ghfast.top/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GHFast"),
            ("https://gh-proxy.com/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GH-Proxy"),
            ("https://mirror.ghproxy.com/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "Mirror.GHProxy"),
            ("https://ghps.cc/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GHPS"),
            ("https://kkgithub.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "KKGitHub"),
            ("https://bgithub.xyz/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "BGitHub"),
        ],
        "ltx_urls": [
            ("https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GitHub直连"),
            ("https://ghproxy.net/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GHProxy.net"),
            ("https://ghfast.top/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GHFast"),
            ("https://gh-proxy.com/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GH-Proxy"),
            ("https://mirror.ghproxy.com/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "Mirror.GHProxy"),
            ("https://ghps.cc/https://github.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "GHPS"),
            ("https://kkgithub.com/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "KKGitHub"),
            ("https://bgithub.xyz/Lightricks/ltx-desktop/archive/refs/tags/v{ver}.zip", "BGitHub"),
        ],
        "ltx2_urls": [
            ("https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GitHub直连"),
            ("https://ghproxy.net/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GHProxy.net"),
            ("https://ghfast.top/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GHFast"),
            ("https://gh-proxy.com/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GH-Proxy"),
            ("https://mirror.ghproxy.com/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "Mirror.GHProxy"),
            ("https://ghps.cc/https://github.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "GHPS"),
            ("https://kkgithub.com/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "KKGitHub"),
            ("https://bgithub.xyz/Lightricks/LTX-2/archive/59ca828d5ae24358832ffd7003c2306fbceeba3a.zip", "BGitHub"),
        ],
        "hf_endpoint": "https://huggingface.co",
        "test_host": "pypi.org",
        "test_port": 443,
        "label": "官方源",
    },
}

UV_VERSION = "0.7.2"
PYTHON_VERSION = "3.12"

# HuggingFace/ModelScope 模型下载辅助脚本（通过 venv Python 子进程执行，输出 JSON 进度协议）
HF_HELPER_SCRIPT = r'''
import sys, json, os, threading

def make_progress_tqdm(write_line):
    from tqdm.auto import tqdm as tqdm_auto
    lock = threading.Lock()
    shared = {"downloaded": 0}
    class _PT(tqdm_auto):
        def __init__(self, *a, **kw):
            kw["disable"] = True
            super().__init__(*a, **kw)
        def update(self, n=1):
            r = super().update(n)
            if n is not None:
                with lock:
                    shared["downloaded"] += int(n)
                write_line(json.dumps({"type": "progress", "downloaded": shared["downloaded"]}))
            return r
    return _PT

def make_patch_context(tqdm_cls):
    from huggingface_hub import file_download
    from unittest.mock import patch
    original_http_get = file_download.http_get
    def _wrapped_http_get(*args, **kwargs):
        if kwargs.get("_tqdm_bar") is None:
            kwargs["_tqdm_bar"] = tqdm_cls(disable=True)
        return original_http_get(*args, **kwargs)
    xet_get_fn = getattr(file_download, "xet_get", None)
    def _wrapped_xet_get(*args, **kwargs):
        if kwargs.get("_tqdm_bar") is None:
            kwargs["_tqdm_bar"] = tqdm_cls(disable=True)
        return xet_get_fn(*args, **kwargs)
    p1 = patch.object(file_download, "http_get", _wrapped_http_get)
    if xet_get_fn is not None:
        p2 = patch.object(file_download, "xet_get", _wrapped_xet_get)
    else:
        p2 = None
    return p1, p2

def main():
    mode = sys.argv[1]
    repo = sys.argv[2]
    local_dir = sys.argv[3]
    filename = sys.argv[4] if len(sys.argv) > 4 else ""
    def write_line(obj):
        sys.stdout.write(obj + "\n")
        sys.stdout.flush()
    tqdm_cls = make_progress_tqdm(write_line)
    try:
        if mode in ("file", "snapshot"):
            p1, p2 = make_patch_context(tqdm_cls)
            p1.start()
            if p2 is not None:
                p2.start()
            try:
                if mode == "file":
                    from huggingface_hub import hf_hub_download
                    result = hf_hub_download(repo_id=repo, filename=filename, local_dir=local_dir)
                else:
                    from huggingface_hub import snapshot_download
                    result = snapshot_download(repo_id=repo, local_dir=local_dir, local_dir_use_symlinks=False)
                write_line(json.dumps({"type": "done", "path": str(result)}))
            finally:
                p1.stop()
                if p2 is not None:
                    p2.stop()
        elif mode == "ms_snapshot":
            from modelscope.hub.snapshot_download import snapshot_download
            result = snapshot_download(model_id=repo, local_dir=local_dir)
            write_line(json.dumps({"type": "done", "path": str(result)}))
        elif mode == "ms_file":
            from modelscope.hub.file_download import model_file_download
            import shutil
            result = model_file_download(model_id=repo, file_path=filename, cache_dir=local_dir)
            td = os.path.join(local_dir, filename)
            if os.path.exists(result) and result != td:
                shutil.copy2(result, td)
            write_line(json.dumps({"type": "done", "path": td}))
        else:
            write_line(json.dumps({"type": "error", "message": f"unknown mode: {mode}"}))
            sys.exit(1)
    except Exception as e:
        write_line(json.dumps({"type": "error", "message": str(e)}))
        sys.exit(1)

if __name__ == "__main__":
    main()
'''

CUDA_VARIANT_MAP = {
    "cu128": {"min_driver": 560.70, "cuda_ver": "12.8", "index_url": "https://download.pytorch.org/whl/cu128"},
    "cu126": {"min_driver": 560.28, "cuda_ver": "12.6", "index_url": "https://download.pytorch.org/whl/cu126"},
    "cu124": {"min_driver": 551.61, "cuda_ver": "12.4", "index_url": "https://download.pytorch.org/whl/cu124"},
    "cu121": {"min_driver": 530.30, "cuda_ver": "12.1", "index_url": "https://download.pytorch.org/whl/cu121"},
    "cu118": {"min_driver": 450.80, "cuda_ver": "11.8", "index_url": "https://download.pytorch.org/whl/cu118"},
}


def _detect_cuda_variant():
    try:
        result = hidden_run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            ver_str = result.stdout.strip().split("\n")[0].strip()
            parts = ver_str.split(".")
            driver_ver = float(parts[0])
            if len(parts) >= 2:
                driver_ver += float(parts[1]) / 100.0
            for variant, info in CUDA_VARIANT_MAP.items():
                if driver_ver >= info["min_driver"]:
                    return variant, info
    except Exception:
        pass
    try:
        import ctypes
        nvcuda = ctypes.windll.LoadLibrary("nvcuda.dll")
        driver = ctypes.c_int()
        nvcuda.cuDriverGetVersion(ctypes.byref(driver))
        driver_ver = driver.value / 1000.0
        for variant, info in CUDA_VARIANT_MAP.items():
            if driver_ver >= info["min_driver"]:
                return variant, info
    except Exception:
        pass
    return None, None

LTX_MODELS = {
    "ltx-2.3-distilled-fp8": {
        "repo": "Lightricks/LTX-2.3-fp8",
        "file": "ltx-2.3-22b-distilled-fp8.safetensors",
        "size_bytes": 29531884062,
        "required": True,
        "desc": "LTX-2.3 蒸馏版 FP8 (推荐，显存友好)",
        "category": "视频模型",
        "modelscope_id": "Lightricks/LTX-2.3-fp8",
    },
    "ltx-2.3-distilled": {
        "repo": "Lightricks/LTX-2.3",
        "file": "ltx-2.3-22b-distilled.safetensors",
        "size_bytes": 46149345038,
        "required": False,
        "desc": "LTX-2.3 蒸馏版完整精度",
        "category": "视频模型",
        "modelscope_id": "Lightricks/LTX-2.3",
    },
    "ltx-2.3-distilled-1.1": {
        "repo": "Lightricks/LTX-2.3",
        "file": "ltx-2.3-22b-distilled-1.1.safetensors",
        "size_bytes": 46149345038,
        "required": False,
        "recommended": True,
        "desc": "LTX-2.3 蒸馏版 v1.1 BF16 (更新版本，质量提升)",
        "category": "视频模型",
        "modelscope_id": "Lightricks/LTX-2.3",
    },
    "ltx-2.3-dev-fp8": {
        "repo": "Lightricks/LTX-2.3-fp8",
        "file": "ltx-2.3-22b-dev-fp8.safetensors",
        "size_bytes": 29145431166,
        "required": False,
        "desc": "LTX-2.3 开发版 FP8 (高质量，需更多显存)",
        "category": "视频模型",
        "modelscope_id": "Lightricks/LTX-2.3-fp8",
    },
    "ltx-2.3-spatial-upscaler": {
        "repo": "Lightricks/LTX-2.3",
        "file": "ltx-2.3-spatial-upscaler-x2-1.0.safetensors",
        "size_bytes": 995743504,
        "required": True,
        "desc": "LTX-2.3 空间超分辨率 x2",
        "category": "高清放大",
        "modelscope_id": "Lightricks/LTX-2.3",
    },
    "ltx-2.3-temporal-upscaler": {
        "repo": "Lightricks/LTX-2.3",
        "file": "ltx-2.3-temporal-upscaler-x2-1.0.safetensors",
        "size_bytes": 261944000,
        "required": False,
        "desc": "LTX-2.3 时间超分辨率 x2",
        "category": "高清放大",
        "modelscope_id": "Lightricks/LTX-2.3",
    },
    "ltx-2.3-ic-lora-union": {
        "repo": "Lightricks/LTX-2.3-22b-IC-LoRA-Union-Control",
        "file": "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors",
        "size_bytes": 654465352,
        "required": False,
        "desc": "IC-LoRA 联合控制 (动作迁移/深度/边缘)",
        "category": "控制模型",
        "modelscope_id": "Lightricks/LTX-2.3-22b-IC-LoRA-Union-Control",
    },
    "ltx-2-19b-distilled-lora-384": {
        "repo": "Lightricks/LTX-2",
        "file": "ltx-2-19b-distilled-lora-384.safetensors",
        "size_bytes": 400000000,
        "required": False,
        "desc": "Pro模式LoRA (视频生成Pro高质量模式必需，384步推理)",
        "category": "视频LoRA",
        "modelscope_id": "Lightricks/LTX-2",
    },
    "ltx2.3-22b-ic-lora-cameraman": {
        "repo": "Lightricks/LTX-2.3-22B_IC-LoRA-Cameraman",
        "file": "LTX2.3-22B_IC-LoRA-Cameraman_v1_10500.safetensors",
        "size_bytes": 300000000,
        "required": False,
        "desc": "摄影师运镜LoRA (视频迁移-摄像机运镜控制)",
        "category": "视频LoRA",
        "modelscope_id": "Lightricks/LTX-2.3-22B_IC-LoRA-Cameraman",
    },
    "z-image-turbo": {
        "repo": "ByteDance/Z-Image-Turbo",
        "file": "Z-Image-Turbo-BF16.safetensors",
        "size_bytes": 13589545564,
        "required": False,
        "recommended": True,
        "desc": "Z-Image-Turbo BF16 (图像生成基础模型，8步高质量生成)",
        "category": "图像模型",
        "modelscope_id": "ByteDance/Z-Image-Turbo",
    },
    "90sAnimationStyle": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "90sAnimationStyle.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "90年代经典动画风格 (复古赛璐璐质感)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "Cinematic_sci-fi-cyberpunk": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "Cinematic_sci-fi-cyberpunk.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "科幻赛博朋克电影风格 (霓虹灯光，未来都市)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "Claymation": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "Claymation.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "黏土动画风格 (定格动画黏土质感)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "CozyFelt": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "CozyFelt.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "温暖毛毡风格 (手工毛毡布艺纹理)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "FantasyPuppetStyle": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "FantasyPuppetStyle.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "奇幻木偶风格 (提线木偶质感与动态)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "Fantasy_Anime": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "Fantasy_Anime.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "奇幻动漫风格 (日式动画精致画面)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "Fantasy_Painterly": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "Fantasy_Painterly.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "奇幻绘画风格 (油画/水彩手绘笔触)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "Fantasy_Realism": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "Fantasy_Realism.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "奇幻写实风格 (写实基础+奇幻元素)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "LTX2.3_Crisp_Enhance": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "LTX2.3_Crisp_Enhance.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "清晰增强 (提升画面锐度和细节)",
        "category": "视频LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "LTX2.3_Soft_Enhance": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "LTX2.3_Soft_Enhance.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "柔和增强 (柔光滤镜，梦幻氛围)",
        "category": "视频LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "Luxe_Sensual": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "Luxe_Sensual.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "奢华感官风格 (高端质感柔光金属反光)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "PaperCutOutStyle": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "PaperCutOutStyle.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "纸雕剪纸风格 (层叠剪纸立体效果)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "Pixar_Toon": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "Pixar_Toon.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "皮克斯卡通风格 (3D卡通渲染质感)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "Post_Apocalyptic": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "Post_Apocalyptic.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "末世废土风格 (荒芜废墟，灰暗色调)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "Wild_West": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "Wild_West.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "西部荒野风格 (牛仔荒漠小镇，夕阳旷野)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "Z-Iamge-人像美学": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "Z-Iamge-人像美学.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "Z-Image人像美学增强 (优化人像肤色光影)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "Z-Image-Fun-Lora-Distill-8-Steps-2603-ComfyUI": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "Z-Image-Fun-Lora-Distill-8-Steps-2603-ComfyUI.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "Z-Image蒸馏加速LoRA (仅需8步生成)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "Z-Image-轻柔东方审美": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "Z-Image｜轻柔东方审美人像摄影写真风格_v1.0.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "轻柔东方审美人像摄影 (东方美学柔和光影)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "Z-image-眼睛细节增强": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "Z-image-眼睛细节增强-DetailedEyes-LoRA_V2.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "眼睛细节增强V2 (提升眼部细节和眼神)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "Z-image-高清人像": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "Z-image-高清人像.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "Z-Image高清人像增强 (提升清晰度和细节)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "ZIB-电影光Chiaroscuro": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "ZIB-电影光Chiaroscuro and Cinematic Lighting Style.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "电影光效明暗对比风格 (Chiaroscuro戏剧性)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "ZIT-伦勃朗光线": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "ZIT-伦勃朗光线rembrandt_ZIT_tyler_x_harris.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "伦勃朗光线风格 (经典三角光人像布光)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "ZIT-影棚摄影": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "ZIT-影棚摄影photolab_v2.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "影棚摄影风格V2 (专业影棚布光效果)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "ZIT-电影光Cinematic": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "ZIT-电影光Cinematic Chiaroscuro Lighting.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "电影级明暗对比光效 (好莱坞式电影布光)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "ZIT-电影黑暗": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "ZIT-电影黑暗MschCine26_V1.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "电影暗调风格 (低调照明，悬疑氛围)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "ZiB-female解剖学": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "ZiB-female解剖学_anatomy.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "女性人体解剖学增强 (优化人体结构比例)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "hina_zImageTurbo_asianMix": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "hina_zImageTurbo_asianMix_v4.59C-bf16.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "亚洲面孔混合模型V4.59C (优化亚洲人面孔)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "redcraftRedzimageUpdated": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "redcraftRedzimageUpdatedDEC03_redzimage15AIO-lora.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "RedCraft Z-Image更新版AIO LoRA (综合增强画质细节)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "woman877-zimage": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "woman877-zimage.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "女性人像增强 (优化女性面部和人像表现)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "z-Image-3D卡通": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "z-Image-3D卡通_V1.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "3D卡通风格V1 (3D卡通渲染效果)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "z-image-极致氛围光影": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "z-image 极致氛围光影LORA_V1.0.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "极致氛围光影V1.0 (强化场景氛围感和光影表现力)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "z-image-女帝": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "z-image-女帝-ben_nd.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "女帝风格 (高贵冷艳女性形象)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "z-image-极致写实": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "z-image-极致写实.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "极致写实增强 (照片级真实感)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "z-image-细节增强v2": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "z-image-细节增强v2.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "细节增强V2 (提升画面细节表现力)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "z-image_小情绪_v1.1": {
        "repo": "ByteDance/Z-Image-Loras",
        "file": "z-image_小情绪_v1.1.safetensors",
        "size_bytes": 124800000,
        "required": False,
        "desc": "小情绪风格V1.1 (捕捉细腻微妙情绪表达)",
        "category": "图像LoRA",
        "modelscope_id": "ByteDance/Z-Image-Loras",
    },
    "text-encoder": {
        "repo": "Lightricks/gemma-3-12b-it-qat-q4_0-unquantized",
        "file": "gemma-3-12b-it-qat-q4_0-unquantized",
        "size_bytes": 25000000000,
        "required": True,
        "desc": "Gemma 文本编码器 (本地文本编码必需，约23GB)",
        "is_folder": True,
        "category": "辅助模型",
        "modelscope_id": "Lightricks/gemma-3-12b-it-qat-q4_0-unquantized",
    },
}

_LORA_DESCRIPTIONS: dict[str, str] = {
    "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors": "视频迁移控制模型（视频迁移功能必需，支持深度/姿态/参考图控制）",
    "ltx-2-19b-distilled-lora-384.safetensors": "Pro模式LoRA（视频生成Pro高质量模式必需，384步推理）",
    "LTX2.3-22B_IC-LoRA-Cameraman_v1_10500.safetensors": "摄影师运镜LoRA（视频迁移-摄像机运镜控制，模拟专业摄影机运动）",
    "90sAnimationStyle.safetensors": "90年代经典动画风格（复古赛璐璐动画质感，触发词: 90s animation style, retro cartoon）",
    "Cinematic_sci-fi-cyberpunk.safetensors": "科幻赛博朋克电影风格（霓虹灯光、未来都市，触发词: sci-fi, cyberpunk, cinematic）",
    "Claymation.safetensors": "黏土动画风格（定格动画黏土质感，触发词: claymation, clay animation）",
    "CozyFelt.safetensors": "温暖毛毡风格（手工毛毡布艺柔软纹理，触发词: cozy felt, felt craft）",
    "FantasyPuppetStyle.safetensors": "奇幻木偶风格（提线木偶质感与动态，触发词: fantasy puppet, puppet style）",
    "Fantasy_Anime.safetensors": "奇幻动漫风格（日式动画精致画面与奇幻世界观，触发词: fantasy anime, magical anime）",
    "Fantasy_Painterly.safetensors": "奇幻绘画风格（油画/水彩手绘笔触质感，触发词: painterly, fantasy painting）",
    "Fantasy_Realism.safetensors": "奇幻写实风格（写实基础融入奇幻元素，触发词: fantasy realism, magical realism）",
    "LTX2.3_Crisp_Enhance.safetensors": "清晰增强（提升画面锐度和细节清晰度，触发词: crisp, sharp, detailed）",
    "LTX2.3_Soft_Enhance.safetensors": "柔和增强（柔光滤镜效果，触发词: soft, gentle, dreamy）",
    "Luxe_Sensual.safetensors": "奢华感官风格（高端质感柔光与金属反光，触发词: luxe, sensual, luxury）",
    "PaperCutOutStyle.safetensors": "纸雕剪纸风格（层叠剪纸立体效果，触发词: paper cut, paper craft, papercut）",
    "Pixar_Toon.safetensors": "皮克斯卡通风格（3D卡通渲染质感，触发词: pixar style, 3d cartoon, pixar toon）",
    "Post_Apocalyptic.safetensors": "末世废土风格（荒芜废墟、破败建筑，触发词: post-apocalyptic, wasteland, ruins）",
    "Wild_West.safetensors": "西部荒野风格（牛仔、荒漠小镇，触发词: wild west, cowboy, western）",
    "Z-Iamge-人像美学.safetensors": "Z-Image人像美学增强（优化人像肤色光影和美感）",
    "Z-Image-Fun-Lora-Distill-8-Steps-2603-ComfyUI.safetensors": "Z-Image蒸馏加速LoRA（8步生成高质量图像，适合快速预览、批量生成）",
    "Z-Image｜轻柔东方审美人像摄影写真风格_v1.0.safetensors": "轻柔东方审美人像摄影（东方美学柔和光影，适合中式写真、古风人像）",
    "Z-image-眼睛细节增强-DetailedEyes-LoRA_V2.safetensors": "眼睛细节增强V2（提升眼部细节和眼神表现力，触发词: detailed eyes）",
    "Z-image-高清人像.safetensors": "Z-Image高清人像增强（提升人像清晰度和细节）",
    "ZIB-电影光Chiaroscuro and Cinematic Lighting Style.safetensors": "电影光效明暗对比风格（触发词: chiaroscuro, cinematic lighting）",
    "ZIT-伦勃朗光线rembrandt_ZIT_tyler_x_harris.safetensors": "伦勃朗光线风格（经典三角光人像布光，触发词: rembrandt lighting）",
    "ZIT-影棚摄影photolab_v2.safetensors": "影棚摄影风格V2（专业影棚布光效果，触发词: photolab, studio photography）",
    "ZIT-电影光Cinematic Chiaroscuro Lighting.safetensors": "电影级明暗对比光效（触发词: cinematic chiaroscuro）",
    "ZIT-电影黑暗MschCine26_V1.safetensors": "电影暗调风格（低调照明、暗色系，触发词: dark cinematic）",
    "ZiB-female解剖学_anatomy.safetensors": "女性人体解剖学增强（优化女性人体结构和比例，触发词: anatomy）",
    "hina_zImageTurbo_asianMix_v4.59C-bf16.safetensors": "亚洲面孔混合模型V4.59C（优化亚洲人面孔特征）",
    "redcraftRedzimageUpdatedDEC03_redzimage15AIO-lora.safetensors": "RedCraft Z-Image更新版AIO LoRA（综合增强画质与细节）",
    "woman877-zimage.safetensors": "女性人像增强（优化女性面部和人像表现）",
    "z-Image-3D卡通_V1.safetensors": "3D卡通风格V1（3D卡通渲染效果，触发词: 3d cartoon）",
    "z-image 极致氛围光影LORA_V1.0.safetensors": "极致氛围光影V1.0（强化场景氛围感和光影表现力）",
    "z-image-女帝-ben_nd.safetensors": "女帝风格（高贵冷艳女性形象）",
    "z-image-极致写实.safetensors": "极致写实增强（照片级真实感）",
    "z-image-细节增强v2.safetensors": "细节增强V2（提升画面细节表现力）",
    "z-image_小情绪_v1.1.safetensors": "小情绪风格V1.1（捕捉细腻微妙情绪表达）",
}

def _get_lora_description(filename: str, is_lora: bool) -> str:
    desc = _LORA_DESCRIPTIONS.get(filename)
    if desc:
        return desc
    return "LoRA风格模型" if is_lora else "本地模型文件"

_LORA_TRIGGER_WORDS: dict[str, list[str]] = {
    "ltx-2-19b-distilled-lora-384.safetensors": [],
    "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors": [],
    "LTX2.3-22B_IC-LoRA-Cameraman_v1_10500.safetensors": [],
    "90sAnimationStyle.safetensors": ["90s animation style", "retro cartoon"],
    "Cinematic_sci-fi-cyberpunk.safetensors": ["sci-fi", "cyberpunk", "cinematic"],
    "Claymation.safetensors": ["claymation", "clay animation"],
    "CozyFelt.safetensors": ["cozy felt", "felt craft"],
    "FantasyPuppetStyle.safetensors": ["fantasy puppet", "puppet style"],
    "Fantasy_Anime.safetensors": ["fantasy anime", "magical anime"],
    "Fantasy_Painterly.safetensors": ["painterly", "fantasy painting"],
    "Fantasy_Realism.safetensors": ["fantasy realism", "magical realism"],
    "LTX2.3_Crisp_Enhance.safetensors": ["crisp", "sharp", "detailed"],
    "LTX2.3_Soft_Enhance.safetensors": ["soft", "gentle", "dreamy"],
    "Luxe_Sensual.safetensors": ["luxe", "sensual", "luxury"],
    "PaperCutOutStyle.safetensors": ["paper cut", "paper craft", "papercut"],
    "Pixar_Toon.safetensors": ["pixar style", "3d cartoon", "pixar toon"],
    "Post_Apocalyptic.safetensors": ["post-apocalyptic", "wasteland", "ruins"],
    "Wild_West.safetensors": ["wild west", "cowboy", "western"],
    "Z-Iamge-人像美学.safetensors": [],
    "Z-Image-Fun-Lora-Distill-8-Steps-2603-ComfyUI.safetensors": [],
    "Z-Image｜轻柔东方审美人像摄影写真风格_v1.0.safetensors": [],
    "Z-image-眼睛细节增强-DetailedEyes-LoRA_V2.safetensors": ["detailed eyes"],
    "Z-image-高清人像.safetensors": [],
    "ZIB-电影光Chiaroscuro and Cinematic Lighting Style.safetensors": ["chiaroscuro", "cinematic lighting"],
    "ZIT-伦勃朗光线rembrandt_ZIT_tyler_x_harris.safetensors": ["rembrandt lighting"],
    "ZIT-影棚摄影photolab_v2.safetensors": ["photolab", "studio photography"],
    "ZIT-电影光Cinematic Chiaroscuro Lighting.safetensors": ["cinematic chiaroscuro"],
    "ZIT-电影黑暗MschCine26_V1.safetensors": ["dark cinematic"],
    "ZiB-female解剖学_anatomy.safetensors": ["anatomy"],
    "hina_zImageTurbo_asianMix_v4.59C-bf16.safetensors": [],
    "redcraftRedzimageUpdatedDEC03_redzimage15AIO-lora.safetensors": [],
    "woman877-zimage.safetensors": [],
    "z-Image-3D卡通_V1.safetensors": ["3d cartoon"],
    "z-image 极致氛围光影LORA_V1.0.safetensors": [],
    "z-image-女帝-ben_nd.safetensors": [],
    "z-image-极致写实.safetensors": [],
    "z-image-细节增强v2.safetensors": [],
    "z-image_小情绪_v1.1.safetensors": [],
}

def _get_lora_trigger_words(filename: str) -> str:
    tw = _LORA_TRIGGER_WORDS.get(filename, [])
    if tw:
        return ", ".join(tw)

_MODEL_EXAMPLES: dict[str, str] = {
    "ltx-2.3-22b-distilled.safetensors": "A beautiful sunset over the ocean, cinematic lighting, 4K",
    "ltx-2.3-22b-distilled-fp8.safetensors": "A cat walking on a rooftop, photorealistic, detailed",
    "ltx-2.3-spatial-upscaler-x2-1.0.safetensors": "（高清放大专用，配合视频生成使用）",
    "ltx-2-19b-distilled-lora-384.safetensors": "（Pro模式专用，提升视频生成质量）",
    "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors": "（视频迁移控制专用，支持深度/姿态/参考图）",
    "LTX2.3-22B_IC-LoRA-Cameraman_v1_10500.safetensors": "A camera slowly zooming in, cameraman, tracking shot",
    "90sAnimationStyle.safetensors": "A girl walking in a park, 90s animation style, retro cartoon",
    "Cinematic_sci-fi-cyberpunk.safetensors": "A futuristic city at night, sci-fi, cyberpunk, cinematic, neon lights",
    "Claymation.safetensors": "A dog playing with a ball, claymation, clay animation, stop motion",
    "CozyFelt.safetensors": "A warm living room with a cat, cozy felt, felt craft, soft texture",
    "FantasyPuppetStyle.safetensors": "A knight fighting a dragon, fantasy puppet, puppet style, theatrical",
    "Fantasy_Anime.safetensors": "A magical forest with glowing butterflies, fantasy anime, magical anime",
    "Fantasy_Painterly.safetensors": "A castle on a hilltop, painterly, fantasy painting, oil painting style",
    "Fantasy_Realism.safetensors": "A dragon flying over mountains, fantasy realism, magical realism, detailed",
    "LTX2.3_Crisp_Enhance.safetensors": "A portrait of a woman, crisp, sharp, detailed, high definition",
    "LTX2.3_Soft_Enhance.safetensors": "A dreamy landscape, soft, gentle, dreamy, ethereal light",
    "Luxe_Sensual.safetensors": "A luxury car interior, luxe, sensual, luxury, golden light",
    "PaperCutOutStyle.safetensors": "A garden with flowers, paper cut, paper craft, papercut, layered",
    "Pixar_Toon.safetensors": "A funny character dancing, pixar style, 3d cartoon, pixar toon",
    "Post_Apocalyptic.safetensors": "A survivor walking through ruins, post-apocalyptic, wasteland, desolate",
    "Wild_West.safetensors": "A cowboy riding a horse, wild west, cowboy, western, desert",
    "Z-Iamge-人像美学.safetensors": "一位女性在自然光下的肖像，柔和光影，肤色细腻",
    "Z-Image-Fun-Lora-Distill-8-Steps-2603-ComfyUI.safetensors": "（8步快速生成，适合预览和批量生成）",
    "Z-image-眼睛细节增强-DetailedEyes-LoRA_V2.safetensors": "A close-up portrait, detailed eyes, vivid eye color",
    "Z-image-高清人像.safetensors": "一位男性在影棚中的高清人像，细节丰富，肤色自然",
    "ZIB-电影光Chiaroscuro and Cinematic Lighting Style.safetensors": "A dramatic portrait, chiaroscuro, cinematic lighting, strong shadows",
    "ZIT-伦勃朗光线rembrandt_ZIT_tyler_x_harris.safetensors": "A classical portrait, rembrandt lighting, triangle light on cheek",
    "ZIT-影棚摄影photolab_v2.safetensors": "A professional headshot, photolab, studio photography, clean background",
    "ZIT-电影光Cinematic Chiaroscuro Lighting.safetensors": "A mysterious figure in shadows, cinematic chiaroscuro, dramatic",
    "ZIT-电影黑暗MschCine26_V1.safetensors": "A dark alley at night, dark cinematic, low key lighting, moody",
    "z-Image-3D卡通_V1.safetensors": "A cute character waving, 3d cartoon, rendered, colorful",
    "z-image 极致氛围光影LORA_V1.0.safetensors": "黄昏时分的城市天际线，极致氛围光影，金色阳光",
    "z-image-女帝-ben_nd.safetensors": "一位高贵冷艳的女性，女帝风格，威严气场",
    "z-image-极致写实.safetensors": "一张照片级的城市街景，极致写实，真实感",
    "z-image-细节增强v2.safetensors": "一朵花的微距特写，细节增强，纹理清晰",
    "z-image_小情绪_v1.1.safetensors": "一位少女若有所思的表情，小情绪，细腻情感",
}

TORCH_VERSION_CONSTRAINT = ">=2.5,<3.0"
TORCHVISION_VERSION_CONSTRAINT = ">=0.20,<1.0"
TORCHAUDIO_VERSION_CONSTRAINT = ">=2.5,<3.0"

LTX_DESKTOP_VERSION = "1.0.4"

LTX_PIP_DEPS = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "safetensors>=0.4.0",
    "accelerate>=0.24.0",
    "transformers>=4.57,<4.58",
    "tokenizers>=0.22,<0.23",
    "diffusers>=0.25.0,<1.0",
    "Pillow>=10.3.0",
    "sentencepiece>=0.1.99",
    "huggingface_hub>=0.30.0,<1.0",
    "pydantic>=2.7.0",
    "python-multipart>=0.0.9",
    "ftfy>=6.0.0",
    "imageio>=2.37.2",
    "imageio-ffmpeg>=0.6.0",
    "peft>=0.13.2,<1.0",
    "protobuf>=3.20.0",
    "opencv-python-headless>=4.8.0",
    "tqdm>=4.66.0",
    "pynvml>=11.5.0",
    "einops",
    "scipy>=1.14",
    "av",
    "triton-windows",
]

LTX_EXT_DEPS = [
    ("faster-whisper", "faster_whisper"),
    ("realesrgan", "realesrgan"),
    ("basicsr", "basicsr"),
]

LTX_PIP_VERSION_LOCKS = {
    "transformers": ">=4.57,<4.58",
    "tokenizers": ">=0.22,<0.23",
    "diffusers": ">=0.25,<1.0",
    "accelerate": ">=0.24,<2.0",
    "safetensors": ">=0.4,<1.0",
    "peft": ">=0.13,<1.0",
    "pydantic": ">=2.7,<3.0",
    "huggingface_hub": ">=0.30,<1.0",
    "sentencepiece": ">=0.1.99,<1.0",
    "ftfy": ">=6.0,<7.0",
    "imageio": ">=2.37,<3.0",
    "imageio-ffmpeg": ">=0.6,<1.0",
    "protobuf": ">=3.20,<7.0",
    "opencv-python-headless": ">=4.8,<5.0",
    "tqdm": ">=4.66,<5.0",
    "pynvml": ">=11.5,<14.0",
    "einops": ">=0.8,<1.0",
    "scipy": ">=1.14,<2.0",
    "av": ">=16.0,<17.0",
}


class EnvDetectWorker(QThread):
    finished = pyqtSignal(bool)
    env_update = pyqtSignal(str, str, str, bool)

    def __init__(self, python_exe, env_check_widgets_keys, parent=None):
        super().__init__(parent)
        self._python_exe = python_exe
        self._widget_keys = env_check_widgets_keys

    def run(self):
        if not self._python_exe or not os.path.exists(self._python_exe):
            self.finished.emit(False)
            return
        try:
            check = hidden_run(
                [self._python_exe, "-c", """
import torch, sys, importlib.metadata
from packaging.version import Version

tv = torch.__version__
tc = getattr(torch.version, "cuda", "") or ""
variant = f"cu{tc.replace('.','')}" if tc else "cpu"
is_gpu = torch.cuda.is_available()
print(f"TORCH|{tv}|{variant}|{'GPU' if is_gpu else 'CPU'}")
if tc:
    print(f"CUDA|{tc}|pytorch")
else:
    print("CUDA||not_found")
try:
    cv = str(torch.backends.cudnn.version()) if torch.cuda.is_available() else ""
    print(f"CUDNN|{cv}|pytorch" if cv else "CUDNN||not_found")
except:
    print("CUDNN||not_found")
print(f"PYVER|{sys.version}")
try:
    import pynvml
    pynvml.nvmlInit()
    dv = pynvml.nvmlSystemGetDriverVersion()
    print(f"NVDROP|{dv}")
    pynvml.nvmlShutdown()
except:
    print("NVDROP|")

deps = ["fastapi","uvicorn","safetensors","accelerate","transformers","tokenizers","diffusers",
        "Pillow","sentencepiece","huggingface_hub","sageattention","pydantic",
        "python-multipart","ftfy","imageio","imageio-ffmpeg","peft","protobuf",
        "opencv-python-headless","tqdm","pynvml","einops","scipy","av","triton-windows",
        "voxcpm","soundfile","librosa","faster-whisper","realesrgan","basicsr"]
locks = {"transformers":(Version("4.57"),Version("4.58")),"tokenizers":(Version("0.22"),Version("0.23")),"diffusers":(Version("0.25"),Version("1.0")),
         "accelerate":(Version("0.24"),Version("2.0")),"safetensors":(Version("0.4"),Version("1.0")),
         "peft":(Version("0.13"),Version("1.0")),"pydantic":(Version("2.7"),Version("3.0")),
         "huggingface_hub":(Version("0.30"),Version("1.0")),"sentencepiece":(Version("0.1.99"),Version("1.0")),
         "ftfy":(Version("6.0"),Version("7.0")),"imageio":(Version("2.37"),Version("3.0")),
         "imageio-ffmpeg":(Version("0.6"),Version("1.0")),"protobuf":(Version("3.20"),Version("7.0")),
         "opencv-python-headless":(Version("4.8"),Version("5.0")),"tqdm":(Version("4.66"),Version("5.0")),
         "pynvml":(Version("11.5"),Version("14.0")),"einops":(Version("0.8"),Version("1.0")),
         "scipy":(Version("1.14"),Version("2.0")),"av":(Version("16.0"),Version("17.0"))}
for d in deps:
    try:

        v = importlib.metadata.version(d)
        if d in locks:
            lo,hi = locks[d]
            vv = Version(v.split('+')[0].split('dev')[0].rstrip('.'))
            status = "OK" if lo <= vv < hi else "BAD"
        else:
            status = "OK"
        print(f"DEP|{status}|{d}|{v}")
    except:
        print(f"DEP|MISS|{d}|0")
"""],
                capture_output=True, text=True, timeout=60
            )
            if check.returncode == 0:
                for line in check.stdout.strip().split('\n'):
                    parts = line.split('|')
                    if parts[0] == "TORCH" and len(parts) >= 4:
                        ver, variant, mode = parts[1], parts[2], parts[3]
                        if mode == "CPU":
                            self.env_update.emit("pytorch", f"× {ver} CPU版", "err", True)
                        else:
                            display_ver = ver if '+' in ver else f"{ver}+{variant}"
                            self.env_update.emit("pytorch", f"√ {display_ver}", "ok", False)
                    elif parts[0] == "CUDA" and len(parts) >= 2:
                        ver = parts[1]
                        if ver:
                            self.env_update.emit("cuda", f"√ {ver}", "ok", False)
                        else:
                            self.env_update.emit("cuda", "× 未检测到", "err", True)
                    elif parts[0] == "CUDNN" and len(parts) >= 2:
                        ver = parts[1]
                        if ver:
                            self.env_update.emit("cudnn", f"√ {ver}", "ok", False)
                        else:
                            self.env_update.emit("cudnn", "△ 未检测到", "warn", False)
                    elif parts[0] == "PYVER" and len(parts) >= 1:
                        py_ver = parts[1].strip() if len(parts) > 1 else ""
                        if "3.12" in py_ver:
                            self.env_update.emit("python", f"√ Python {py_ver.split()[0]}", "ok", False)
                        elif "3.13" in py_ver or "3.14" in py_ver:
                            self.env_update.emit("python", f"△ {py_ver.split()[0]} (不兼容)", "warn", True)
                    elif parts[0] == "NVDROP" and len(parts) >= 1:
                        drv = parts[1].strip() if len(parts) > 1 else ""
                        if drv:
                            try:
                                dv = float(drv.split('.')[0]) + float(drv.split('.')[1]) / 100.0
                                if dv >= 560.70:
                                    self.env_update.emit("nvidia_driver", f"√ {drv}", "ok", False)
                                else:
                                    self.env_update.emit("nvidia_driver", f"△ {drv} (需>=560.70)", "warn", True)
                            except:
                                self.env_update.emit("nvidia_driver", f"√ {drv}", "ok", False)
                        else:
                            self.env_update.emit("nvidia_driver", "× 未检测到", "err", True)
                    elif parts[0] == "DEP" and len(parts) >= 4:
                        status, name, ver = parts[1], parts[2], parts[3]
                        dep_key_map = {
                            "faster-whisper": "faster_whisper",
                            "realesrgan": "real_esrgan",
                        }
                        widget_key = dep_key_map.get(name, name)
                        if widget_key in self._widget_keys:
                            if status == "OK":
                                self.env_update.emit(widget_key, f"√ {ver}", "ok", False)
                            elif status == "BAD":
                                lock = LTX_PIP_VERSION_LOCKS.get(widget_key, "")
                                self.env_update.emit(widget_key, f"△ {ver} (需{lock})", "warn", True)
                            else:
                                self.env_update.emit(widget_key, "× 未安装", "err", True)
        except:
            pass
        self.finished.emit(True)


class _SpeedProbeWorker(QThread):
    """真实下载探测 Worker，对候选 URL 做 256KB Range 下载测速"""
    finished = pyqtSignal(dict)  # {name: {"speed_bps": float, "first_byte_ms": float}}

    PROBE_BYTES = 262143  # 256KB
    PROBE_TIMEOUT = 10

    def __init__(self, probe_urls, parent=None):
        """probe_urls: list of (url, name) tuples"""
        super().__init__(parent)
        self.probe_urls = probe_urls
        self._should_stop = False

    def run(self):
        results = {}
        for url, name in self.probe_urls:
            if self._should_stop:
                break
            result = self._probe_one(url, name)
            if result:
                results[name] = result
        self.finished.emit(results)

    def _probe_one(self, url, name):
        """对单个 URL 做 256KB Range 下载探测，返回 {speed_bps, first_byte_ms} 或 None"""
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Range": f"bytes=0-{self.PROBE_BYTES}",
            })
            t_start = time.monotonic()
            with urllib.request.urlopen(req, timeout=self.PROBE_TIMEOUT) as resp:
                t_first_byte = time.monotonic()
                first_byte_ms = (t_first_byte - t_start) * 1000
                total_read = 0
                while total_read < self.PROBE_BYTES + 1:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    total_read += len(chunk)
                t_end = time.monotonic()
                elapsed = t_end - t_start
                if elapsed > 0 and total_read > 0:
                    speed_bps = total_read / elapsed
                    return {"speed_bps": speed_bps, "first_byte_ms": first_byte_ms}
        except Exception:
            pass
        return None

    def stop(self):
        self._should_stop = True


class DeployWorker(QThread):
    progress = pyqtSignal(int, str)
    log = pyqtSignal(str, str)
    log_replace = pyqtSignal(str, str)  # 替换日志最后一行（用于进度条更新）
    finished = pyqtSignal(bool, str)
    env_update = pyqtSignal(str, str, str, bool)

    STEP_STATUS_SKIPPED = "skipped"
    STEP_STATUS_INSTALLED = "installed"
    STEP_STATUS_REPAIRED = "repaired"
    STEP_STATUS_FAILED = "failed"

    MAX_RETRIES = 3

    def __init__(self, app_res, parent=None, mirror_source="auto", uv_urls=None, ltx_urls=None, data_dir=None, speed_cache=None, temp_dir=None, skip_models=False):
        super().__init__(parent)
        self.app_res = app_res
        if data_dir:
            self._data_dir = data_dir
        else:
            self._data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(app_res))), "data")
        if temp_dir:
            self._temp_dir = temp_dir
        else:
            self._temp_dir = os.path.join(os.path.dirname(self._data_dir), "temp")
        self._models_dir = os.path.join(self._data_dir, "models")
        self._should_stop = False
        self._should_pause = False
        self._step_results = {}
        self._mirror_source = mirror_source
        self._speed_cache = speed_cache or {}
        self._skip_models = skip_models
        self._resolved_mirrors = self._resolve_mirrors(mirror_source)
        if uv_urls:
            self._resolved_mirrors["uv_urls"] = uv_urls
        if ltx_urls:
            self._resolved_mirrors["ltx_urls"] = ltx_urls

    def _resolve_mirrors(self, source):
        if source == "auto" or source not in MIRROR_SOURCES:
            source = "tsinghua"
        src = MIRROR_SOURCES[source]
        result = {
            "pip": src["pip"],
            "pip_extra": src["pip_extra"],
            "pip_fallback": src["pip_fallback"],
            "uv_urls": list(src["uv_urls"]),
            "ltx_urls": list(src["ltx_urls"]),
            "ltx2_urls": list(src.get("ltx2_urls", [])),
            "hf_endpoint": src["hf_endpoint"],
        }
        # 根据测速缓存排序 URL 列表（最快的排前面）
        probe = self._speed_cache.get("probe_results", {})
        if probe:
            for key in ("uv_urls", "ltx_urls", "ltx2_urls"):
                urls = result[key]
                # 按探测速度降序排序
                def speed_key(item):
                    url, name = item
                    return probe.get(name, {}).get("speed_bps", 0)
                result[key] = sorted(urls, key=speed_key, reverse=True)
        return result

    def stop(self):
        self._should_stop = True

    def pause(self):
        self._should_pause = True

    def resume(self):
        self._should_pause = False

    def cancel(self):
        self._should_stop = True
        self._should_pause = False

    def _wait_if_paused(self):
        while self._should_pause and not self._should_stop:
            self.msleep(200)

    def run(self):
        try:
            self._deploy_all()
        except Exception as e:
            self.finished.emit(False, f"部署异常: {e}")

    @property
    def _uv_exe(self):
        return os.path.join(self.app_res, "uv", "uv.exe")

    def _uv_index_args(self):
        return [
            "--default-index", self._resolved_mirrors["pip"],
            "--index", self._resolved_mirrors["pip_fallback"],
            "--index-strategy", "first-index",
        ]

    @property
    def _venv_python(self):
        for venv_name in (".venv", "venv"):
            p = os.path.join(self._data_dir, venv_name, "Scripts", "python.exe")
            if os.path.exists(p):
                return p
        return os.path.join(self.app_res, "venv", "Scripts", "python.exe")

    @property
    def _venv_pip(self):
        for venv_name in (".venv", "venv"):
            p = os.path.join(self._data_dir, venv_name, "Scripts", "pip.exe")
            if os.path.exists(p):
                return p
        return os.path.join(self.app_res, "venv", "Scripts", "pip.exe")

    def _retry_run(self, cmd, label, max_retries=None, **kwargs):
        retries = max_retries or self.MAX_RETRIES
        last_err = None
        for attempt in range(1, retries + 1):
            if self._should_stop:
                return None
            self._wait_if_paused()
            if self._should_stop:
                return None
            try:
                proc = hidden_run(cmd, **kwargs)
                if proc.returncode == 0:
                    return proc
                stderr_text = (proc.stderr or "") if hasattr(proc, 'stderr') else ""
                stdout_text = (proc.stdout or "") if hasattr(proc, 'stdout') else ""
                err_parts = []
                if stderr_text.strip():
                    err_parts.append(stderr_text.strip()[:500])
                if stdout_text.strip():
                    err_parts.append(stdout_text.strip()[:300])
                last_err = " | ".join(err_parts) if err_parts else f"返回码 {proc.returncode}"
                if attempt < retries:
                    self.log.emit(f"    第{attempt}次 {label} 失败，{2**attempt}秒后重试...", "warn")
                    time.sleep(min(2 ** attempt, 30))
            except subprocess.TimeoutExpired:
                last_err = "超时"
                if attempt < retries:
                    self.log.emit(f"    第{attempt}次 {label} 超时，重试中...", "warn")
            except Exception as e:
                last_err = str(e)
                if attempt < retries:
                    self.log.emit(f"    第{attempt}次 {label} 异常: {e}，重试中...", "warn")
        return last_err

    def _safe_remove(self, path):
        for _ in range(5):
            try:
                if os.path.exists(path):
                    os.remove(path)
                return True
            except PermissionError:
                time.sleep(0.5)
            except Exception:
                return False
        return False

    @staticmethod
    def _format_speed_text(speed_bps):
        """格式化速度文本，返回如 '3.8MB/s' 或 '512KB/s'"""
        if speed_bps >= 1024 * 1024:
            return f"{speed_bps / (1024 * 1024):.1f}MB/s"
        elif speed_bps >= 1024:
            return f"{speed_bps / 1024:.0f}KB/s"
        elif speed_bps > 0:
            return f"{speed_bps:.0f}B/s"
        return ""

    @staticmethod
    def _format_eta_text(done, total, speed_bps):
        """格式化 ETA 文本，返回如 'ETA 4s' / 'ETA 2m 30s' / 'ETA 1h 20m'"""
        if speed_bps <= 0 or total <= 0 or done >= total:
            return ""
        remaining = (total - done) / speed_bps
        if remaining < 60:
            return f"ETA {remaining:.0f}s"
        elif remaining < 3600:
            m, s = divmod(int(remaining), 60)
            return f"ETA {m}m {s}s"
        else:
            h, m = divmod(int(remaining), 3600)
            m = m // 60
            return f"ETA {h}h {m}m"

    @staticmethod
    def _update_speed_sample(smoothed_speed, last_time, last_bytes, now, current_bytes):
        """EWMA(alpha=0.3) 速度采样，每 0.5 秒更新一次。
        返回 (new_smoothed_speed, new_last_time, new_last_bytes)"""
        elapsed = now - last_time
        if elapsed >= 0.5:
            instant_speed = (current_bytes - last_bytes) / elapsed if elapsed > 0 else 0
            if smoothed_speed == 0.0:
                smoothed_speed = instant_speed
            else:
                smoothed_speed = 0.3 * instant_speed + 0.7 * smoothed_speed
            return smoothed_speed, now, current_bytes
        return smoothed_speed, last_time, last_bytes

    def _format_progress(self, done, total, label, speed_bps=0):
        """格式化下载进度条文本，支持速度和 ETA 显示"""
        if total > 0:
            pct = done * 100 // total
            bar_len = 20
            filled = bar_len * pct // 100
            bar = "█" * filled + "░" * (bar_len - filled)
            done_mb = done / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            parts = [f"  ↓ {label} [{bar}] {pct:3d}% {done_mb:.1f}/{total_mb:.1f}MB"]
            speed_text = self._format_speed_text(speed_bps)
            if speed_text:
                parts.append(speed_text)
            eta_text = self._format_eta_text(done, total, speed_bps)
            if eta_text:
                parts.append(eta_text)
            return " ".join(parts)
        else:
            done_mb = done / (1024 * 1024)
            parts = [f"  ↓ {label} {done_mb:.1f}MB"]
            speed_text = self._format_speed_text(speed_bps)
            if speed_text:
                parts.append(speed_text)
            return " ".join(parts)

    def _monitor_download_progress(self, proc, info, models_dir, target_path, is_folder, expected_bytes):
        """监控下载进程，从stderr解析进度信息，实时更新进度条。
        huggingface_hub / modelscope 下载时会在stderr输出进度条信息。"""
        import threading as _threading
        import re as _re
        label = info.get("file", "")
        last_pct = -1

        # 后台线程实时读取stderr，解析进度信息
        stderr_lines = []
        current_pct = [0]  # 用列表包装以便在线程中修改

        def _drain_stderr():
            try:
                buf = b""
                while True:
                    chunk = proc.stderr.read(256)
                    if not chunk:
                        break
                    buf += chunk
                    # 按行处理
                    while b"\r" in buf or b"\n" in buf:
                        # \r 是进度条覆盖，\n 是换行
                        if b"\r" in buf:
                            idx = buf.index(b"\r")
                            line = buf[:idx].decode("utf-8", errors="replace")
                            buf = buf[idx + 1:]
                        elif b"\n" in buf:
                            idx = buf.index(b"\n")
                            line = buf[:idx].decode("utf-8", errors="replace")
                            buf = buf[idx + 1:]
                        else:
                            break
                        line = line.strip()
                        if line:
                            stderr_lines.append(line)
                            # 解析 huggingface_hub 进度格式: "Downloading:  45%|████▌     | 500M/1.10G"
                            m = _re.search(r'(\d+)%', line)
                            if m:
                                try:
                                    current_pct[0] = int(m.group(1))
                                except ValueError:
                                    pass
                            # 解析 modelscope 进度格式: "Downloading: 45.0%"
                            m2 = _re.search(r'[Dd]ownload.*?(\d+(?:\.\d+)?)\s*%', line)
                            if m2:
                                try:
                                    current_pct[0] = int(float(m2.group(1)))
                                except ValueError:
                                    pass
                # 处理剩余数据
                if buf:
                    line = buf.decode("utf-8", errors="replace").strip()
                    if line:
                        stderr_lines.append(line)
                        m = _re.search(r'(\d+)%', line)
                        if m:
                            try:
                                current_pct[0] = int(m.group(1))
                            except ValueError:
                                pass
            except Exception:
                pass

        drain = _threading.Thread(target=_drain_stderr, daemon=True)
        drain.start()

        # 同时监控目标文件/目录大小变化（作为备用进度来源）
        hf_home = os.environ.get("HF_HOME",
                    os.environ.get("HUGGINGFACE_HUB_CACHE",
                        os.path.join(os.path.expanduser("~"), ".cache", "huggingface")))
        hf_hub_cache = os.path.join(hf_home, "hub") if not hf_home.endswith("hub") else hf_home
        repo_cache_name = "models--" + info["repo"].replace("/", "--")
        repo_blobs_dir = os.path.join(hf_hub_cache, repo_cache_name, "blobs")

        while proc.poll() is None:
            if self._should_stop:
                try:
                    proc.terminate()
                except Exception:
                    pass
                self._last_download_stderr = ""
                return

            # 优先使用stderr解析的百分比
            pct_from_stderr = current_pct[0]

            # 如果stderr没有进度信息，尝试从文件大小推断
            pct_from_size = 0
            if pct_from_stderr == 0 and expected_bytes > 0:
                downloaded = 0
                try:
                    if is_folder:
                        if os.path.isdir(target_path):
                            for dp, dn, fns in os.walk(target_path):
                                for fn in fns:
                                    try:
                                        downloaded += os.path.getsize(os.path.join(dp, fn))
                                    except OSError:
                                        pass
                    else:
                        if os.path.isdir(repo_blobs_dir):
                            for fn in os.listdir(repo_blobs_dir):
                                if fn.endswith(".incomplete"):
                                    try:
                                        downloaded += os.path.getsize(os.path.join(repo_blobs_dir, fn))
                                    except OSError:
                                        pass
                        if os.path.exists(target_path):
                            try:
                                downloaded += os.path.getsize(target_path)
                            except OSError:
                                pass
                except Exception:
                    pass
                if downloaded > 0:
                    pct_from_size = min(downloaded * 100 // expected_bytes, 99)

            pct = max(pct_from_stderr, pct_from_size)
            if pct > 0 and pct != last_pct:
                last_pct = pct
                if expected_bytes > 0 and pct_from_size > pct_from_stderr:
                    # 从文件大小推断的，显示具体大小
                    self.log_replace.emit(self._format_progress(
                        expected_bytes * pct // 100, expected_bytes, label), "info")
                else:
                    # 从stderr解析的百分比
                    self.log_replace.emit(self._format_progress(
                        expected_bytes * pct // 100, expected_bytes, label), "info")

            time.sleep(1)

        # 进程结束，最终进度更新
        drain.join(timeout=3)
        self._last_download_stderr = "\n".join(stderr_lines[-10:]) if stderr_lines else ""

        if proc.returncode == 0:
            self.log_replace.emit(self._format_progress(expected_bytes, expected_bytes, label), "info")

    def _write_helper_script(self):
        """将 HF 下载辅助脚本写入临时文件，返回路径"""
        temp_dir = self._temp_dir
        os.makedirs(temp_dir, exist_ok=True)
        helper_path = os.path.join(temp_dir, "_hf_download_helper.py")
        with open(helper_path, 'w', encoding='utf-8') as f:
            f.write(HF_HELPER_SCRIPT)
        return helper_path

    def _monitor_download_progress_v2(self, proc, label, expected_bytes, source_name="",
                                      target_path="", is_folder=False):
        """监控下载进程，从 stdout JSON 协议读取真实字节进度，并以文件大小变化作为备用进度源。
        子进程输出 JSON 行: {"type":"progress","downloaded":N} / {"type":"done","path":"..."} / {"type":"error","message":"..."}
        返回 (success: bool, result_path_or_error: str)"""
        import json as _json
        import queue as _queue
        import threading as _threading

        smoothed_speed = 0.0
        speed_last_time = time.monotonic()
        speed_last_bytes = 0
        last_pct = -1
        downloaded = 0
        result_path = None
        error_msg = ""
        stderr_buf = []
        stdout_q = _queue.Queue()
        got_json_progress = [False]

        # 后台线程读取 stdout（避免 readline/for 迭代的主线程阻塞问题）
        def _reader():
            try:
                while True:
                    raw = proc.stdout.readline()
                    if not raw:
                        break
                    stdout_q.put(raw)
                stdout_q.put(None)  # 哨兵：表示 EOF
            except Exception:
                stdout_q.put(None)

        reader = _threading.Thread(target=_reader, daemon=True)
        reader.start()

        # 后台线程读取 stderr（防止 pipe 满阻塞子进程）
        def _drain_stderr():
            try:
                while True:
                    chunk = proc.stderr.read(1024)
                    if not chunk:
                        break
                    stderr_buf.append(chunk.decode('utf-8', errors='replace'))
            except Exception:
                pass

        drain = _threading.Thread(target=_drain_stderr, daemon=True)
        drain.start()

        def _get_disk_bytes():
            """从磁盘文件大小推断已下载字节数（ModelScope 等无 JSON 进度时的备用来源）"""
            if not target_path:
                return 0
            try:
                if is_folder:
                    total = 0
                    if os.path.isdir(target_path):
                        for dp, dn, fns in os.walk(target_path):
                            for fn in fns:
                                try:
                                    total += os.path.getsize(os.path.join(dp, fn))
                                except OSError:
                                    pass
                    return total
                else:
                    # 单文件：直接读取目标文件，或查找 .incomplete 临时文件
                    if os.path.exists(target_path):
                        return os.path.getsize(target_path)
                    # HF hub 使用 blobs/*.incomplete 临时文件
                    blobs_dir = os.path.join(os.path.dirname(target_path), ".cache", "huggingface", "hub")
                    if not os.path.isdir(blobs_dir):
                        # 尝试常见 HF_HOME 路径
                        hf_home = os.environ.get("HF_HOME", os.path.join(os.path.expanduser("~"), ".cache", "huggingface"))
                        blobs_dir = os.path.join(hf_home, "hub")
                    if os.path.isdir(blobs_dir):
                        # 遍历 blobs 目录中的 .incomplete 文件
                        for root, dirs, files in os.walk(blobs_dir):
                            for fn in files:
                                if fn.endswith(".incomplete"):
                                    return os.path.getsize(os.path.join(root, fn))
                    return 0
            except Exception:
                return 0

        # 主循环：从队列读取 stdout 行，每 2s 做一次文件大小备用检测
        try:
            while True:
                try:
                    raw_line = stdout_q.get(timeout=2.0)
                except _queue.Empty:
                    # 2s 无 JSON 输出，检查停止/暂停状态，并做文件大小备用检测
                    if self._should_stop:
                        try: proc.terminate()
                        except Exception: pass
                        break
                    if self._should_pause:
                        try: proc.terminate()
                        except Exception: pass
                        break
                    if expected_bytes > 0 and not got_json_progress[0]:
                        db = _get_disk_bytes()
                        if db > 0:
                            pct = db * 100 // expected_bytes
                            if pct != last_pct and pct <= 99:
                                last_pct = pct
                                smoothed_speed, speed_last_time, speed_last_bytes = self._update_speed_sample(
                                    smoothed_speed, speed_last_time, speed_last_bytes, time.monotonic(), db)
                                self.log_replace.emit(self._format_progress(
                                    db, expected_bytes, label, speed_bps=smoothed_speed), "info")
                    continue
                if raw_line is None:  # EOF
                    break
                if self._should_stop:
                    try: proc.terminate()
                    except Exception: pass
                    break
                if self._should_pause:
                    try: proc.terminate()
                    except Exception: pass
                    break
                line = raw_line.decode('utf-8', errors='replace').strip() if isinstance(raw_line, bytes) else raw_line.strip()
                if not line:
                    continue
                try:
                    obj = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                msg_type = obj.get("type", "")
                if msg_type == "progress":
                    got_json_progress[0] = True
                    downloaded = int(obj.get("downloaded", 0))
                    smoothed_speed, speed_last_time, speed_last_bytes = self._update_speed_sample(
                        smoothed_speed, speed_last_time, speed_last_bytes, time.monotonic(), downloaded)
                    if expected_bytes > 0:
                        pct = downloaded * 100 // expected_bytes
                        if pct != last_pct:
                            last_pct = pct
                            self.log_replace.emit(self._format_progress(
                                downloaded, expected_bytes, label, speed_bps=smoothed_speed), "info")
                    elif downloaded > 0 and downloaded % (5 * 1024 * 1024) < 100000:
                        self.log_replace.emit(self._format_progress(
                            downloaded, 0, label, speed_bps=smoothed_speed), "info")
                elif msg_type == "done":
                    result_path = obj.get("path", "")
                elif msg_type == "error":
                    error_msg = obj.get("message", "未知错误")
        except Exception as e:
            error_msg = str(e) if not error_msg else error_msg

        # 等待进程结束
        proc.wait(timeout=30)
        drain.join(timeout=3)

        if proc.returncode == 0 and not error_msg:
            final_bytes = max(downloaded, expected_bytes)
            self.log_replace.emit(self._format_progress(final_bytes, final_bytes, label, speed_bps=smoothed_speed), "info")
            return True, result_path or ""
        else:
            err = error_msg or ("\n".join(stderr_buf[-5:])[:300] if stderr_buf else "未知错误")
            self._last_download_stderr = err
            return False, err

    def _download_with_retry(self, url, save_path, label, max_retries=None):
        retries = max_retries or self.MAX_RETRIES
        last_err = "未知错误"
        temp_path = save_path + ".tmp"
        for attempt in range(1, retries + 1):
            if self._should_stop:
                return False
            try:
                self._safe_remove(temp_path)
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    total = int(resp.headers.get("Content-Length", 0))
                    done = 0
                    last_pct = -1
                    # 速度跟踪变量
                    smoothed_speed = 0.0
                    speed_last_time = time.monotonic()
                    speed_last_bytes = 0
                    with open(temp_path, 'wb') as f:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)
                            done += len(chunk)
                            # EWMA 速度采样
                            smoothed_speed, speed_last_time, speed_last_bytes = self._update_speed_sample(
                                smoothed_speed, speed_last_time, speed_last_bytes, time.monotonic(), done)
                            if total > 0:
                                pct = done * 100 // total
                                if pct != last_pct:
                                    last_pct = pct
                                    self.log_replace.emit(self._format_progress(done, total, label, speed_bps=smoothed_speed), "info")
                            elif done % (5 * 1024 * 1024) < 65536:
                                self.log_replace.emit(self._format_progress(done, 0, label, speed_bps=smoothed_speed), "info")
                if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                    self._safe_remove(save_path)
                    try:
                        os.rename(temp_path, save_path)
                    except OSError:
                        import shutil as _shutil
                        _shutil.copy2(temp_path, save_path)
                        self._safe_remove(temp_path)
                    return True
                last_err = "下载文件为空"
                self._safe_remove(temp_path)
            except urllib.error.URLError as e:
                last_err = f"网络错误: {e.reason}" if hasattr(e, 'reason') else str(e)
                self._safe_remove(temp_path)
            except TimeoutError:
                last_err = "连接超时(30秒)"
                self._safe_remove(temp_path)
            except Exception as e:
                last_err = str(e) if str(e) else type(e).__name__
                self._safe_remove(temp_path)
            if attempt < retries:
                self.log.emit(f"    第{attempt}次下载 {label} 失败({last_err})，{2**attempt}秒后重试...", "warn")
                time.sleep(min(2 ** attempt, 15))
        return False

    def _download_racing(self, url_name_pairs, save_path, label_prefix=""):
        """竞速下载：同时发起多个连接，第一个成功响应的胜出，其余取消"""
        import threading
        import tempfile

        if not url_name_pairs:
            return None, "无可用下载源"

        if len(url_name_pairs) == 1:
            url, name = url_name_pairs[0]
            lbl = f"{label_prefix} ({name})" if label_prefix else name
            if self._download_with_retry(url, save_path, lbl):
                return name, None
            return None, f"{name} 下载失败"

        winner = [None]  # [winner_name]
        winner_event = threading.Event()
        results = {}  # name -> (success: bool, error: str)
        lock = threading.Lock()

        def _try_one(url, name, idx):
            lbl = f"{label_prefix} ({name})" if label_prefix else name
            temp_path = save_path + f".r{idx}.tmp"
            try:
                self._safe_remove(temp_path)
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                # 竞速阶段：短超时快速判断连通性
                with urllib.request.urlopen(req, timeout=15) as resp:
                    # 验证响应有效性：检查Content-Length或读取首段数据
                    content_length = resp.headers.get("Content-Length")
                    if content_length and int(content_length) < 1024:
                        # 小于1KB的响应可能是错误页面，跳过
                        with lock:
                            results[name] = (False, f"响应过小({content_length}B)，可能是错误页面")
                        return
                    # 读取首段数据验证不是HTML错误页
                    first_chunk = resp.read(8192)
                    if first_chunk and b"<html" in first_chunk[:512].lower():
                        with lock:
                            results[name] = (False, "返回HTML错误页面")
                        return
                    # 成功建立有效连接，检查是否已有胜出者
                    if winner_event.is_set():
                        self._safe_remove(temp_path)
                        return
                    # 标记自己为胜出者
                    with lock:
                        if winner[0] is not None:
                            self._safe_remove(temp_path)
                            return
                        winner[0] = name
                    winner_event.set()
                    self.log.emit(f"  √ {lbl} 竞速胜出，开始下载...", "info")
                    # 继续下载完整文件（首段数据已读取）
                    total = int(resp.headers.get("Content-Length", 0))
                    done = len(first_chunk)
                    last_pct = -1
                    # 速度跟踪变量
                    smoothed_speed = 0.0
                    speed_last_time = time.monotonic()
                    speed_last_bytes = done
                    with open(temp_path, 'wb') as f:
                        f.write(first_chunk)
                        while True:
                            if self._should_stop:
                                break
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)
                            done += len(chunk)
                            # EWMA 速度采样
                            smoothed_speed, speed_last_time, speed_last_bytes = self._update_speed_sample(
                                smoothed_speed, speed_last_time, speed_last_bytes, time.monotonic(), done)
                            if total > 0:
                                pct = done * 100 // total
                                if pct != last_pct:
                                    last_pct = pct
                                    self.log_replace.emit(self._format_progress(done, total, lbl, speed_bps=smoothed_speed), "info")
                            elif done % (5 * 1024 * 1024) < 65536:
                                self.log_replace.emit(self._format_progress(done, 0, lbl, speed_bps=smoothed_speed), "info")
                if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                    self._safe_remove(save_path)
                    try:
                        os.rename(temp_path, save_path)
                    except OSError:
                        import shutil as _shutil
                        _shutil.copy2(temp_path, save_path)
                        self._safe_remove(temp_path)
                    with lock:
                        results[name] = (True, None)
                else:
                    self._safe_remove(temp_path)
                    with lock:
                        results[name] = (False, "下载文件为空")
            except Exception as e:
                self._safe_remove(temp_path)
                err = f"网络错误: {e.reason}" if hasattr(e, 'reason') else str(e) if str(e) else type(e).__name__
                with lock:
                    results[name] = (False, err)

        # 启动竞速线程
        threads = []
        for idx, (url, name) in enumerate(url_name_pairs):
            t = threading.Thread(target=_try_one, args=(url, name, idx), daemon=True)
            threads.append(t)
            t.start()

        # 等待竞速结果（最多15秒判定胜出者）
        winner_event.wait(timeout=15)

        if winner[0] is None:
            # 竞速阶段全部超时，等待所有线程完成
            for t in threads:
                t.join(timeout=30)
            # 检查是否有成功的
            for name, (ok, err) in results.items():
                if ok:
                    return name, None
            # 全部失败，对剩余源重新竞速
            remaining = [(url, name) for url, name in url_name_pairs
                         if name not in results or not results[name][0]]
            if remaining:
                self.log.emit(f"  △ 竞速下载全部失败，重新竞速重试...", "warn")
                return self._download_racing(remaining, save_path, label_prefix)
            return None, "所有下载源均失败"

        # 有胜出者，等待其下载完成
        for t in threads:
            t.join(timeout=600)

        w = winner[0]
        if w in results and results[w][0]:
            return w, None

        # 胜出者下载失败，尝试其他已完成的
        for name, (ok, err) in results.items():
            if ok:
                return name, None

        # 对剩余源重新竞速
        remaining = [(url, name) for url, name in url_name_pairs
                     if name != w and (name not in results or not results[name][0])]
        if remaining:
            self.log.emit(f"  △ 胜出源 {w} 下载失败，重新竞速其他源...", "warn")
            return self._download_racing(remaining, save_path, label_prefix)
        return None, "所有下载源均失败"

    def _deploy_all(self):
        steps = [
            (5, "UV 包管理器", self._step_uv, True),
            (15, "Python 环境", self._step_python, True),
            (25, "核心依赖", self._step_deps, True),
            (35, "扩展组件", self._step_extensions, False),
            (40, "补丁文件", self._step_patches, True),
            (50, "前端界面", self._step_ui, True),
            (60, "后端代码", self._step_backend, True),
            (70, "数据配置", self._step_data, True),
            (75, "ffmpeg 便携版", self._step_ffmpeg, False),
            (85, "必需模型", self._step_models, False),
        ]

        _STEP_ENV_MAP = {
            "UV 包管理器": [("python", "↻ UV 安装中...", "pending")],
            "Python 环境": [("python", "↻ Python 安装中...", "pending")],
            "核心依赖": [
                ("pytorch", "↻ 依赖安装中...", "pending"), ("cuda", "↻ 检测中...", "pending"),
                ("cudnn", "↻ 检测中...", "pending"), ("nvidia_driver", "↻ 检测中...", "pending"),
                ("transformers", "↻ 安装中...", "pending"), ("diffusers", "↻ 安装中...", "pending"),
                ("accelerate", "↻ 安装中...", "pending"), ("safetensors", "↻ 安装中...", "pending"),
                ("peft", "↻ 安装中...", "pending"), ("huggingface_hub", "↻ 安装中...", "pending"),
                ("ffmpeg", "↻ 检测中...", "pending"), ("opencv-python-headless", "↻ 安装中...", "pending"),
                ("Pillow", "↻ 安装中...", "pending"), ("imageio", "↻ 安装中...", "pending"),
                ("imageio-ffmpeg", "↻ 安装中...", "pending"), ("scipy", "↻ 安装中...", "pending"),
                ("einops", "↻ 安装中...", "pending"), ("av", "↻ 安装中...", "pending"),
                ("tqdm", "↻ 安装中...", "pending"), ("protobuf", "↻ 安装中...", "pending"),
                ("sentencepiece", "↻ 安装中...", "pending"), ("ftfy", "↻ 安装中...", "pending"),
                ("pynvml", "↻ 安装中...", "pending"), ("pydantic", "↻ 安装中...", "pending"),
                ("python-multipart", "↻ 安装中...", "pending"),
                ("triton-windows", "↻ 安装中...", "pending"),
            ],
            "扩展组件": [
                ("voxcpm", "↻ VoxCPM2 安装中...", "pending"),
                ("faster_whisper", "↻ faster-whisper 安装中...", "pending"),
                ("real_esrgan", "↻ Real-ESRGAN 安装中...", "pending"),
            ],
            "补丁文件": [("patches", "↻ 补丁部署中...", "pending")],
            "前端界面": [("ui", "↻ 前端部署中...", "pending")],
            "后端代码": [("backend", "↻ 后端部署中...", "pending"), ("ltx", "↻ 后端部署中...", "pending")],
            "数据配置": [("models", "↻ 数据配置中...", "pending"), ("project", "↻ 配置中...", "pending")],
            "ffmpeg 便携版": [("ffmpeg", "↻ ffmpeg 安装中...", "pending")],
            "必需模型": [("models", "↻ 模型下载中...", "pending")],
        }

        _STEP_DONE_MAP = {
            "UV 包管理器": [("python", "√ UV 已安装", "ok")],
            "Python 环境": [("python", "√ Python 已安装", "ok")],
            "核心依赖": [],
            "扩展组件": [
                ("voxcpm", "√ VoxCPM2 已安装", "ok"),
                ("faster_whisper", "√ faster-whisper 已安装", "ok"),
                ("real_esrgan", "√ Real-ESRGAN 已安装", "ok"),
            ],
            "补丁文件": [("patches", "√ 整合包内置", "ok")],
            "前端界面": [("ui", "√ 整合包内置", "ok")],
            "后端代码": [("backend", "√ 整合包内置", "ok"), ("ltx", "√ 整合包内置", "ok")],
            "数据配置": [("models", "√ data/models", "ok"), ("project", "√ 已配置", "ok")],
            "ffmpeg 便携版": [("ffmpeg", "√ ffmpeg 已安装", "ok")],
            "必需模型": [("models", "√ 模型已下载", "ok")],
        }

        self.log.emit("=" * 50, "info")
        self.log.emit("▶ 开始部署维护 (UV)", "info")
        self.log.emit("=" * 50, "info")

        pre_check_results = self._pre_check_all()
        self.log.emit("", "info")
        self.log.emit("📊 环境预检结果:", "info")
        for name, status in pre_check_results.items():
            icon = {"ok": "√", "partial": "△", "missing": "×"}.get(status, "❓")
            text = {"ok": "完整", "partial": "部分损坏", "missing": "未安装"}.get(status, "未知")
            log_type = {"ok": "ok", "partial": "warn", "missing": "error"}.get(status, "info")
            self.log.emit(f"  {icon} {name}: {text}", log_type)
        self.log.emit("", "info")

        failed_steps = []
        for pct, name, func, required in steps:
            if self._should_stop:
                self.finished.emit(False, "用户取消部署")
                return
            self._wait_if_paused()
            if self._should_stop:
                self.finished.emit(False, "用户取消部署")
                return
            # 如果 skip_models=True，跳过必需模型步骤
            if name == "必需模型" and self._skip_models:
                self._step_results[name] = self.STEP_STATUS_SKIPPED
                self.log.emit(f"⏭️ [{name}] 引导模式跳过模型下载", "ok")
                for env_key, env_text, env_status in _STEP_DONE_MAP.get(name, []):
                    self.env_update.emit(env_key, env_text, env_status, False)
                continue
            self.progress.emit(pct, f"↻ {name} ({pct}%)")
            self.log.emit(f"{'─' * 40}", "info")
            self.log.emit(f"↻ [{name}] 开始处理...", "info")
            for env_key, env_text, env_status in _STEP_ENV_MAP.get(name, []):
                self.env_update.emit(env_key, env_text, env_status, False)
            try:
                status = func()
                self._step_results[name] = status
                if status == self.STEP_STATUS_SKIPPED:
                    self.log.emit(f"⏭️ [{name}] 已完整，跳过安装", "ok")
                elif status == self.STEP_STATUS_REPAIRED:
                    self.log.emit(f"⚙ [{name}] 修复完成", "ok")
                elif status == self.STEP_STATUS_INSTALLED:
                    self.log.emit(f"√ [{name}] 安装完成", "ok")
                if status in (self.STEP_STATUS_SKIPPED, self.STEP_STATUS_INSTALLED, self.STEP_STATUS_REPAIRED):
                    for env_key, env_text, env_status in _STEP_DONE_MAP.get(name, []):
                        self.env_update.emit(env_key, env_text, env_status, False)
            except Exception as e:
                self._step_results[name] = self.STEP_STATUS_FAILED
                error_msg = str(e)
                solution = self._get_solution(name, error_msg)
                if required:
                    self.log.emit(f"× [{name}] 失败: {error_msg}", "err")
                    if solution:
                        self.log.emit(f"◆ 解决方案: {solution}", "warn")
                    self.log.emit("", "info")
                    self._print_summary(failed_steps)
                    self.finished.emit(False, f"{name} 失败: {error_msg}\n◆ {solution}" if solution else f"{name} 失败: {error_msg}")
                    return
                else:
                    self.log.emit(f"△ [{name}] 失败(非必需): {error_msg}", "warn")
                    if solution:
                        self.log.emit(f"◆ 解决方案: {solution}", "warn")
                    failed_steps.append(name)

        self.log.emit("", "info")

        if self._venv_python and os.path.exists(self._venv_python):
            self._run_env_detection(self._venv_python)

        self._print_summary(failed_steps)

        if failed_steps:
            self.progress.emit(100, f"部署完成（{len(failed_steps)}项非必需步骤跳过）")
            self.finished.emit(True, f"部署完成，但以下非必需步骤跳过: {', '.join(failed_steps)}")
        else:
            self.progress.emit(100, "部署完成！")
            self.finished.emit(True, "部署完成！所有环境已就绪")

    def _pre_check_all(self):
        results = {}
        checks = [
            ("UV 包管理器", self._check_uv_ok),
            ("Python 环境", self._check_python_ok),
            ("PyTorch", self._check_torch_ok),
            ("核心依赖", self._check_deps_ok),
            ("扩展组件", self._check_extensions_ok),
            ("补丁文件", self._check_patches_ok),
            ("前端界面", self._check_ui_ok),
            ("后端代码", self._check_backend_ok),
            ("数据配置", self._check_data_ok),
            ("ffmpeg", self._check_ffmpeg_ok),
            ("必需模型", self._check_models_ok),
        ]
        for name, check_fn in checks:
            try:
                ok = check_fn()
            except Exception as e:
                self.log.emit(f"  △ {name} 检查异常: {e}", "warn")
                ok = False
            if name == "PyTorch":
                results[name] = "ok" if ok else ("partial" if self._check_python_ok() else "missing")
            elif name == "核心依赖":
                results[name] = "ok" if ok else ("partial" if self._check_python_ok() else "missing")
            else:
                results[name] = "ok" if ok else "missing"
        return results

    def _print_summary(self, failed_steps):
        self.log.emit("=" * 50, "info")
        self.log.emit("■ 部署结果汇总:", "info")
        self.log.emit("=" * 50, "info")
        for name, status in self._step_results.items():
            icon = {
                self.STEP_STATUS_SKIPPED: "⏭️",
                self.STEP_STATUS_INSTALLED: "√",
                self.STEP_STATUS_REPAIRED: "⚙",
                self.STEP_STATUS_FAILED: "×",
            }.get(status, "❓")
            text = {
                self.STEP_STATUS_SKIPPED: "已存在，跳过",
                self.STEP_STATUS_INSTALLED: "新安装",
                self.STEP_STATUS_REPAIRED: "修复安装",
                self.STEP_STATUS_FAILED: "失败",
            }.get(status, "未知")
            self.log.emit(f"  {icon} {name}: {text}", "info")
        self.log.emit("=" * 50, "info")

    def _get_solution(self, step_name, error_msg):
        solutions = {
            "UV 包管理器": [
                ("下载失败", "请检查网络连接，或手动下载 uv.exe 放到 resources/uv/ 目录"),
                ("验证失败", "UV 安装后无法运行，请删除 resources/uv/ 目录后重试"),
                ("所有镜像源", "网络连接异常，请检查防火墙设置或尝试使用代理"),
            ],
            "Python 环境": [
                ("UV 未安装", "请先完成 UV 包管理器安装步骤"),
                ("安装失败", "UV 安装 Python 失败，请检查网络连接和磁盘空间"),
                ("venv.*失败", "虚拟环境创建失败，请删除 data/.venv/ 目录后重试"),
                ("验证失败", "Python 环境验证失败，请删除 data/.venv/ 和 resources/python/ 目录后重新部署"),
            ],
            "核心依赖": [
                ("Python 未安装", "请先完成 Python 环境安装步骤"),
                ("PyTorch.*失败", "请检查网络连接，PyTorch 包较大(约2GB)，需稳定网络"),
                ("安装失败", "请尝试手动安装: uv pip install torch torchvision torchaudio"),
                ("验证失败", "依赖安装后无法导入，请删除 data/.venv/ 目录后重新部署"),
                ("voxcpm.*失败", "TTS语音依赖安装失败，不影响视频生成，可稍后手动: uv pip install voxcpm soundfile librosa"),
            ],
            "扩展组件": [
                ("VoxCPM2.*失败", "TTS语音依赖安装失败，可稍后手动: uv pip install voxcpm soundfile librosa"),
                ("faster-whisper.*失败", "语音识别依赖安装失败，可稍后手动: uv pip install faster-whisper"),
                ("Real-ESRGAN.*失败", "高清放大依赖安装失败，可稍后手动: uv pip install realesrgan basicsr"),
                ("安装失败", "扩展组件非核心功能所需，可稍后手动安装，不影响视频生成"),
            ],
            "补丁文件": [
                ("未找到.*源", "请确保项目参考目录完整，或重新下载整合包"),
            ],
            "前端界面": [
                ("未找到.*源", "请确保项目参考目录完整，或重新下载整合包"),
            ],
            "后端代码": [
                ("未找到.*LTX", "自动下载失败，请手动安装 LTX Desktop 后重试部署"),
                ("自动下载.*失败", "请手动下载 LTX Desktop 安装后重试，或将 LTX Desktop 放到项目根目录"),
                ("解压.*失败", "安装包格式不支持自动解压，请手动安装 LTX Desktop"),
            ],
            "数据配置": [
                ("权限", "请以管理员身份运行启动器"),
            ],
            "ffmpeg 便携版": [
                ("下载失败", "请检查网络连接，或手动从 https://www.gyan.dev/ffmpeg/builds/ 下载便携版解压到 %LOCALAPPDATA%\\LTXDesktop\\ffmpeg\\"),
                ("解压失败", "磁盘空间不足或文件损坏，请清理磁盘后重试"),
                ("安装失败", "可在环境检测中点击 ffmpeg 修复按钮单独安装"),
            ],
            "必需模型": [
                ("Python 未安装", "请先完成 Python 环境安装步骤"),
                ("下载失败", "模型文件较大，请确保网络稳定。可在模型管理页面单独下载"),
                ("HF.*失败", "HuggingFace 镜像不可用，请稍后重试或在模型管理页面手动下载"),
                ("ModelScope.*失败", "ModelScope 镜像也不可用，请检查网络连接"),
                ("必需模型.*失败", "请手动从 HuggingFace 或 ModelScope 下载模型文件到 data/models/ 目录"),
            ],
        }
        step_solutions = solutions.get(step_name, [])
        import re as _re
        for pattern, solution in step_solutions:
            if _re.search(pattern, error_msg):
                return solution
        if step_solutions:
            return step_solutions[-1][1]
        return "请检查网络连接和磁盘空间，然后重试部署"

    def _check_uv_ok(self):
        uv_exe = self._uv_exe
        if not os.path.exists(uv_exe):
            return False
        try:
            result = hidden_run(
                [uv_exe, "--version"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except:
            return False

    def _check_python_ok(self):
        python_exe = self._venv_python
        if not os.path.exists(python_exe):
            return False
        try:
            result = hidden_run(
                [python_exe, "-c", "import sys, encodings; print(sys.version)"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0 and "3." in result.stdout
        except:
            return False

    def _check_torch_ok(self):
        python_exe = self._venv_python
        if not os.path.exists(python_exe):
            return False
        try:
            result = hidden_run(
                [python_exe, "-c", "import torch; print(torch.__version__); print(torch.cuda.is_available())"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                return False
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2 and 'False' in lines[1]:
                self.log.emit(f"  △ PyTorch {lines[0].strip()} 已安装但无 CUDA 支持", "warn")
                return False
            return True
        except Exception:
            return False

    def _check_deps_ok(self):
        python_exe = self._venv_python
        if not os.path.exists(python_exe):
            return False
        key_deps = ["torch", "fastapi", "uvicorn", "safetensors", "diffusers", "transformers",
                     "accelerate", "PIL", "sentencepiece", "huggingface_hub",
                     "ltx_core", "ltx_pipelines"]
        for dep in key_deps:
            try:
                result = hidden_run(
                    [python_exe, "-c", f"import importlib.util; spec = importlib.util.find_spec('{dep}'); raise SystemExit(0 if spec else 1)"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode != 0:
                    return False
            except:
                return False
        return True

    def _check_extensions_ok(self):
        python_exe = self._venv_python
        if not os.path.exists(python_exe):
            return False
        ext_deps = ["voxcpm", "soundfile", "librosa", "faster_whisper", "realesrgan", "basicsr"]
        for dep in ext_deps:
            try:
                result = hidden_run(
                    [python_exe, "-c", f"import importlib.util; spec = importlib.util.find_spec('{dep}'); raise SystemExit(0 if spec else 1)"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode != 0:
                    return False
            except:
                return False
        return True

    def _check_missing_deps(self, python_exe):
        if not os.path.exists(python_exe):
            return list(LTX_PIP_DEPS)
        try:
            result = hidden_run(
                [python_exe, "-c", """
import importlib.util
deps = ["fastapi","uvicorn","safetensors","accelerate","transformers","tokenizers","diffusers",
        "PIL","sentencepiece","huggingface_hub","sageattention","pydantic",
        "multipart","ftfy","imageio","imageio_ffmpeg","peft","protobuf",
        "cv2","tqdm","pynvml","einops","scipy","av","triton"]
for d in deps:
    spec = importlib.util.find_spec(d)
    print(f"{'OK' if spec else 'MISS'}|{d}")
"""],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                return list(LTX_PIP_DEPS)
            import_map = {
                "PIL": "Pillow", "multipart": "python-multipart",
                "imageio_ffmpeg": "imageio-ffmpeg", "cv2": "opencv-python-headless",
                "triton": "triton-windows",
            }
            missing = []
            installed = set()
            for line in result.stdout.strip().split('\n'):
                if '|' not in line:
                    continue
                status, name = line.split('|', 1)
                if status == "MISS":
                    pip_name = import_map.get(name, name)
                    for dep in LTX_PIP_DEPS:
                        import re
                        dep_base = re.split(r'[><=!~\[]', dep)[0].strip()
                        if dep_base == pip_name and dep not in installed:
                            missing.append(dep)
                            installed.add(dep)
                            break
            return missing
        except Exception:
            return list(LTX_PIP_DEPS)

    def _check_outdated_deps(self, python_exe):
        if not os.path.exists(python_exe):
            return list(LTX_PIP_DEPS)
        try:
            result = hidden_run(
                [python_exe, "-c", """
import importlib.util, importlib.metadata, re, sys

VERSION_LOCKS = {
    "transformers": (">=4.57,<4.58"),
    "tokenizers": (">=0.22,<0.23"),
    "diffusers": (">=0.25,<1.0"),
    "accelerate": (">=0.24,<2.0"),
    "safetensors": (">=0.4,<1.0"),
    "peft": (">=0.13,<1.0"),
    "pydantic": (">=2.7,<3.0"),
    "huggingface_hub": (">=0.30,<1.0"),
    "sentencepiece": (">=0.1.99,<1.0"),
    "ftfy": (">=6.0,<7.0"),
    "imageio": (">=2.37,<3.0"),
    "imageio-ffmpeg": (">=0.6,<1.0"),
    "protobuf": (">=3.20,<7.0"),
    "opencv-python-headless": (">=4.8,<5.0"),
    "tqdm": (">=4.66,<5.0"),
    "pynvml": (">=11.5,<14.0"),
    "einops": (">=0.8,<1.0"),
    "scipy": (">=1.14,<2.0"),
    "av": (">=16.0,<17.0"),
}

IMPORT_MAP = {
    "opencv-python-headless": "cv2",
    "imageio-ffmpeg": "imageio_ffmpeg",
    "Pillow": "PIL",
}

from packaging.version import Version

for dep_name, spec in VERSION_LOCKS.items():
    imp_name = IMPORT_MAP.get(dep_name, dep_name)
    spec_obj = importlib.util.find_spec(imp_name)
    if spec_obj is None:
        continue
    try:
        installed_ver = importlib.metadata.version(dep_name)
    except Exception:
        continue
    parts = spec.split(",")
    ok = True
    for part in parts:
        part = part.strip()
        m = re.match(r'(>=|<=|>|<|==|~=)\\s*(.+)', part)
        if not m:
            continue
        op, ver_str = m.group(1), m.group(2).strip()
        try:
            iv = Version(installed_ver)
            rv = Version(ver_str)
        except Exception:
            continue
        if op == ">=" and not (iv >= rv):
            ok = False
        elif op == "<" and not (iv < rv):
            ok = False
        elif op == ">" and not (iv > rv):
            ok = False
        elif op == "<=" and not (iv <= rv):
            ok = False
        elif op == "==" and not (iv == rv):
            ok = False
    if not ok:
        print(f"OUTDATED|{dep_name}")
"""],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                return []
            outdated = []
            for line in result.stdout.strip().split('\n'):
                if '|' not in line:
                    continue
                status, name = line.split('|', 1)
                if status == "OUTDATED":
                    outdated.append(name)
            return outdated
        except Exception:
            return []

    def _check_patches_ok(self):
        dst = os.path.join(self.app_res, "patches")
        key_files = ["runtime_policy.py", "app_factory.py", "settings.json"]
        return os.path.exists(dst) and all(os.path.exists(os.path.join(dst, f)) for f in key_files)

    def _check_ui_ok(self):
        dst = os.path.join(self.app_res, "ui")
        key_files = ["index.html", "index.js", "index.css"]
        return os.path.exists(dst) and all(os.path.exists(os.path.join(dst, f)) for f in key_files)

    def _check_backend_ok(self):
        dst = os.path.join(self.app_res, "backend")
        return os.path.exists(dst) and os.path.exists(os.path.join(dst, "ltx2_server.py"))

    def _check_data_ok(self):
        dst = self._data_dir
        settings_path = os.path.join(dst, "settings.json")
        if not os.path.exists(dst):
            return False
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                if "models_dir" in settings:
                    return True
            except:
                pass
        return os.path.exists(os.path.join(dst, "custom_dir.txt"))

    def _check_ffmpeg_ok(self):
        ffmpeg_path = os.environ.get("LTX_FFMPEG_PATH")
        if ffmpeg_path and os.path.isfile(ffmpeg_path):
            return True
        ffmpeg_file = Path(os.environ.get("LOCALAPPDATA", "")) / "LTXDesktop" / "ffmpeg_path.txt"
        if ffmpeg_file.exists():
            try:
                custom_path = ffmpeg_file.read_text(encoding="utf-8").strip()
                if custom_path and os.path.isfile(custom_path):
                    return True
            except Exception:
                pass
        if shutil.which("ffmpeg") or shutil.which("ffmpeg.exe"):
            return True
        return False

    def _check_models_ok(self):
        models_dir = self._models_dir or os.path.join(self._data_dir or "", "models")
        if not os.path.exists(models_dir):
            return False
        for model_id, info in LTX_MODELS.items():
            if info["required"]:
                target = os.path.join(models_dir, info["file"])
                expected_bytes = info["size_bytes"]
                if info.get("is_folder", False):
                    if not os.path.exists(target) or not os.path.isdir(target):
                        return False
                    try:
                        folder_size = 0
                        for f in Path(target).rglob("*"):
                            if f.is_file():
                                try:
                                    folder_size += f.stat().st_size
                                except OSError:
                                    pass
                                if folder_size >= expected_bytes * 0.5:
                                    break
                        if folder_size < expected_bytes * 0.5:
                            return False
                    except Exception:
                        return False
                else:
                    if not os.path.exists(target):
                        return False
                    actual_bytes = os.path.getsize(target)
                    if actual_bytes < expected_bytes * 0.9:
                        return False
        return True

    def _step_uv(self):
        if self._check_uv_ok():
            return self.STEP_STATUS_SKIPPED

        uv_dir = os.path.join(self.app_res, "uv")
        os.makedirs(uv_dir, exist_ok=True)
        uv_exe = os.path.join(uv_dir, "uv.exe")

        mirrors = self._resolved_mirrors["uv_urls"]

        zip_path = os.path.join(uv_dir, "uv.zip")
        self.log.emit("  下载 UV (竞速选择最快源)...", "info")
        winner_name, err = self._download_racing(mirrors, zip_path, "UV")

        if winner_name is None:
            raise Exception(f"UV 下载失败: {err}，请检查网络连接")

        self.log.emit(f"  √ UV 下载完成 ({winner_name})", "ok")

        self.log.emit("  解压 UV...", "info")
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for member in zf.namelist():
                    if member.endswith('uv.exe'):
                        with zf.open(member) as src, open(uv_exe, 'wb') as dst:
                            dst.write(src.read())
                        break
            os.remove(zip_path)
        except zipfile.BadZipFile:
            if os.path.exists(zip_path):
                os.remove(zip_path)
            raise Exception("解压失败: 下载的文件损坏(非有效ZIP)，请重试")
        except Exception as e:
            if os.path.exists(zip_path):
                os.remove(zip_path)
            raise Exception(f"解压失败: {e}")

        if not self._check_uv_ok():
            raise Exception("UV 安装后验证失败")

        return self.STEP_STATUS_INSTALLED

    def _step_python(self):
        if self._check_python_ok():
            return self.STEP_STATUS_SKIPPED

        uv_exe = self._uv_exe
        if not os.path.exists(uv_exe):
            raise Exception("UV 未安装，无法安装 Python")

        is_repair = os.path.exists(self._venv_python) and not self._check_python_ok()

        venv_dir = os.path.join(self._data_dir, ".venv")
        os.makedirs(os.path.dirname(venv_dir), exist_ok=True)

        if is_repair:
            self.log.emit("  △ Python 虚拟环境损坏，重建...", "warn")
            for old_name in (".venv", "venv"):
                old_dir = os.path.join(self._data_dir, old_name)
                if os.path.exists(old_dir):
                    shutil.rmtree(old_dir, ignore_errors=True)

        python_install_dir = os.path.join(self.app_res, "python")
        env = os.environ.copy()
        env["UV_PYTHON_INSTALL_DIR"] = python_install_dir
        env.pop("PYTHONHOME", None)

        self.log.emit(f"  安装 Python {PYTHON_VERSION}...", "info")
        result = self._retry_run(
            [uv_exe, "python", "install", PYTHON_VERSION],
            label="Python 安装",
            capture_output=True, text=True, timeout=600, env=env
        )
        if isinstance(result, str):
            raise Exception(f"Python 安装失败: {result}")

        self.log.emit(f"  创建虚拟环境 ({venv_dir})...", "info")
        result = self._retry_run(
            [uv_exe, "venv", venv_dir, "--python", PYTHON_VERSION],
            label="venv 创建",
            capture_output=True, text=True, timeout=120, env=env
        )
        if isinstance(result, str):
            raise Exception(f"venv 创建失败: {result}")

        if not self._check_python_ok():
            raise Exception("Python 环境验证失败")

        return self.STEP_STATUS_REPAIRED if is_repair else self.STEP_STATUS_INSTALLED

    def _step_deps(self):
        torch_ok = self._check_torch_ok()
        deps_ok = self._check_deps_ok()

        python_exe = self._venv_python
        if not os.path.exists(python_exe):
            raise Exception("Python 未安装，无法安装依赖")

        uv_exe = self._uv_exe
        is_repair = torch_ok or deps_ok

        env = os.environ.copy()
        for _ek in ("UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX", "UV_INDEX", "PYTHONHOME"):
            env.pop(_ek, None)
        env["UV_LINK_MODE"] = "copy"

        any_installed = False

        if not torch_ok:
            cuda_variant, cuda_info = _detect_cuda_variant()
            if cuda_variant and cuda_info:
                torch_index = cuda_info["index_url"]
                self.log.emit(f"  检测到 NVIDIA 驱动，选择 PyTorch {cuda_variant} (CUDA {cuda_info['cuda_ver']})", "info")
            else:
                torch_index = self._resolved_mirrors["pip_extra"]
                self.log.emit("  △ 未检测到 NVIDIA 驱动版本，使用默认 CUDA 索引", "warn")

            try:
                check_result = hidden_run(
                    [python_exe, "-c", "import torch; print(torch.__version__); print(torch.cuda.is_available())"],
                    capture_output=True, text=True, timeout=10
                )
                if check_result.returncode == 0:
                    ver = check_result.stdout.strip().split('\n')[0].strip() if check_result.stdout.strip() else ""
                    no_cuda = 'False' in check_result.stdout.strip().split('\n')[-1] if check_result.stdout.strip() else True
                    if "+cpu" in ver or no_cuda:
                        self.log.emit(f"  检测到 CPU 版本 PyTorch {ver}，将卸载后重装 CUDA 版本", "warn")
                        uninst_env = os.environ.copy()
                        uninst_env.pop("PYTHONHOME", None)
                        uninst = hidden_run(
                            [uv_exe, "pip", "uninstall", "--python", python_exe,
                             "torch", "torchvision", "torchaudio"],
                            capture_output=True, text=True, timeout=120, env=uninst_env
                        )
                        if uninst.returncode != 0:
                            err_msg = uninst.stderr.strip()[:200] if uninst.stderr else f"返回码 {uninst.returncode}"
                            self.log.emit(f"  △ 卸载旧版本失败: {err_msg}", "warn")
                        else:
                            self.log.emit("  √ 旧版本已卸载", "info")
                        hidden_run(
                            [uv_exe, "cache", "clean"],
                            capture_output=True, text=True, timeout=30
                        )
            except Exception:
                pass
            self.env_update.emit("pytorch", "↻ PyTorch 安装中...", "pending", False)
            self.log.emit("  安装 PyTorch (CUDA)，包较大请耐心等待...", "info")
            torch_env = os.environ.copy()
            torch_env.pop("UV_INDEX_URL", None)
            torch_env.pop("UV_EXTRA_INDEX_URL", None)
            torch_env.pop("UV_DEFAULT_INDEX", None)
            torch_env.pop("UV_INDEX", None)
            torch_env.pop("PYTHONHOME", None)
            # 仅使用CUDA索引安装torch系列，避免PyPI镜像提供CPU版本
            result = self._retry_run(
                [uv_exe, "pip", "install", "--python", python_exe,
                 f"torch{TORCH_VERSION_CONSTRAINT}",
                 f"torchvision{TORCHVISION_VERSION_CONSTRAINT}",
                 f"torchaudio{TORCHAUDIO_VERSION_CONSTRAINT}",
                 "--default-index", torch_index],
                label=f"PyTorch ({cuda_variant or 'CUDA'})",
                max_retries=2,
                capture_output=True, text=True, timeout=1800, env=torch_env
            )
            if isinstance(result, str):
                self.log.emit("  △ CUDA索引安装失败，尝试添加PyPI镜像重试...", "warn")
                fallback_env = torch_env.copy()
                result = self._retry_run(
                    [uv_exe, "pip", "install", "--python", python_exe,
                     f"torch{TORCH_VERSION_CONSTRAINT}",
                     f"torchvision{TORCHVISION_VERSION_CONSTRAINT}",
                     f"torchaudio{TORCHAUDIO_VERSION_CONSTRAINT}",
                     "--default-index", torch_index,
                     "--index", self._resolved_mirrors["pip"],
                     "--index-strategy", "first-index"],
                    label=f"PyTorch ({cuda_variant or 'CUDA'} only-index)",
                    max_retries=2,
                    capture_output=True, text=True, timeout=1800, env=fallback_env
                )
            if isinstance(result, str):
                raise Exception(f"PyTorch 安装失败: {result}，PyTorch 包较大(约2GB)，请确保网络稳定")

            # 等待文件系统同步，PyTorch首次导入需加载大量DLL
            import time; time.sleep(3)
            if not self._check_torch_ok():
                diag = hidden_run(
                    [python_exe, "-c", "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.__file__)"],
                    capture_output=True, text=True, timeout=30
                )
                diag_info = diag.stdout.strip()[:200] if diag.returncode == 0 else "无法导入 torch"
                raise Exception(f"PyTorch 安装后验证失败，当前状态: {diag_info}")
            any_installed = True
            deps_ok = False

        if not deps_ok:
            self.log.emit("  安装项目依赖...", "info")
            failed_deps = []
            for dep in LTX_PIP_DEPS:
                if self._should_stop:
                    return self.STEP_STATUS_FAILED
                import re
                dep_name = re.split(r'[><=!~\[]', dep)[0].strip()
                if dep_name in LTX_PIP_VERSION_LOCKS:
                    install_spec = f"{dep_name}{LTX_PIP_VERSION_LOCKS[dep_name]}"
                else:
                    install_spec = dep
                self.env_update.emit(dep_name, f"↻ {dep_name} 安装中...", "pending", False)
                result = self._retry_run(
                    [uv_exe, "pip", "install", "--python", python_exe, install_spec] + self._uv_index_args(),
                    label=install_spec,
                    max_retries=1,
                    capture_output=True, text=True, timeout=300, env=env
                )
                if result is not None and not isinstance(result, str):
                    ver = self._get_dep_version(python_exe, dep_name)
                    self.env_update.emit(dep_name, f"√ {ver}" if ver else f"√ {dep_name}", "ok", False)
                    self.log.emit(f"  ✓ {dep_name} {ver}" if ver else f"  ✓ {dep_name}", "ok")
                else:
                    err_hint = result[:150] if isinstance(result, str) else ""
                    self.log.emit(f"  △ {dep} 镜像安装失败{': ' + err_hint if err_hint else ''}，尝试直连PyPI...", "warn")
                    result = self._retry_run(
                        [uv_exe, "pip", "install", "--python", python_exe, install_spec,
                         "--default-index", "https://pypi.org/simple/"],
                        label=f"{dep_name}(PyPI直连)",
                        max_retries=1,
                        capture_output=True, text=True, timeout=300, env=env
                    )
                    if result is not None and not isinstance(result, str):
                        ver = self._get_dep_version(python_exe, dep_name)
                        self.env_update.emit(dep_name, f"√ {ver}" if ver else f"√ {dep_name}", "ok", False)
                        self.log.emit(f"  ✓ {dep_name} {ver}(PyPI)" if ver else f"  ✓ {dep_name}(PyPI)", "ok")
                    else:
                        failed_deps.append(dep)
                        self.env_update.emit(dep_name, f"× {dep_name}", "err", True)
                        self.log.emit(f"  △ {dep} 安装失败", "warn")

            if failed_deps:
                self.log.emit(f"  △ 以下依赖安装失败: {', '.join(failed_deps)}", "warn")
                critical_deps = [d for d in failed_deps if any(k in d for k in ["fastapi", "uvicorn", "diffusers", "transformers"])]
                if critical_deps:
                    raise Exception(f"关键依赖安装失败: {', '.join(critical_deps)}")
            any_installed = True
        else:
            missing_deps = self._check_missing_deps(python_exe)
            outdated_deps = self._check_outdated_deps(python_exe)
            need_fix = missing_deps + outdated_deps
            if need_fix:
                self.log.emit(f"  检测到 {len(missing_deps)} 个缺失 + {len(outdated_deps)} 个版本不达标依赖，正在修复...", "info")
                for dep_spec in need_fix:
                    if self._should_stop:
                        return self.STEP_STATUS_FAILED
                    import re
                    dep_name = re.split(r'[><=!~\[]', dep_spec)[0].strip()
                    if dep_name in LTX_PIP_VERSION_LOCKS:
                        install_spec = f"{dep_name}{LTX_PIP_VERSION_LOCKS[dep_name]}"
                    else:
                        install_spec = dep_spec
                    self.env_update.emit(dep_name, f"↻ {dep_name} 修复中...", "pending", False)
                    result = self._retry_run(
                        [uv_exe, "pip", "install", "--python", python_exe, install_spec] + self._uv_index_args(),
                        label=install_spec,
                        max_retries=1,
                        capture_output=True, text=True, timeout=300, env=env
                    )
                    if result is not None and not isinstance(result, str):
                        ver = self._get_dep_version(python_exe, dep_name)
                        self.env_update.emit(dep_name, f"√ {ver}" if ver else f"√ {dep_name}", "ok", False)
                        self.log.emit(f"  ✓ {dep_name} {ver}" if ver else f"  ✓ {dep_name}", "ok")
                    else:
                        err_hint = result[:150] if isinstance(result, str) else ""
                        self.log.emit(f"  △ {dep_name} 镜像安装失败{': ' + err_hint if err_hint else ''}，尝试直连PyPI...", "warn")
                        result = self._retry_run(
                            [uv_exe, "pip", "install", "--python", python_exe, install_spec,
                             "--default-index", "https://pypi.org/simple/"],
                            label=f"{dep_name}(PyPI直连)",
                            max_retries=1,
                            capture_output=True, text=True, timeout=300, env=env
                        )
                        if result is not None and not isinstance(result, str):
                            ver = self._get_dep_version(python_exe, dep_name)
                            self.env_update.emit(dep_name, f"√ {ver}" if ver else f"√ {dep_name}", "ok", False)
                            self.log.emit(f"  ✓ {dep_name} {ver}(PyPI)" if ver else f"  ✓ {dep_name}(PyPI)", "ok")
                        else:
                            self.env_update.emit(dep_name, f"× {dep_name}", "err", True)
                            self.log.emit(f"  △ {dep_name} 安装失败", "warn")
                any_installed = True
            else:
                self.log.emit("  ✓ 所有依赖已就绪", "ok")

        self._install_ltx_packages(python_exe, uv_exe, env)

        self._run_env_detection(python_exe)

        if any_installed:
            return self.STEP_STATUS_REPAIRED if is_repair else self.STEP_STATUS_INSTALLED
        return self.STEP_STATUS_SKIPPED

    def _step_extensions(self):
        python_exe = self._venv_python
        if not os.path.exists(python_exe):
            raise Exception("Python 未安装，无法安装扩展组件")

        uv_exe = self._uv_exe
        env = os.environ.copy()
        for _ek in ("UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX", "UV_INDEX", "PYTHONHOME"):
            env.pop(_ek, None)
        env["UV_LINK_MODE"] = "copy"

        all_ext_deps = [
            ("voxcpm", "voxcpm", "voxcpm>=2.0.0"),
            ("soundfile", "soundfile", "soundfile"),
            ("librosa", "librosa", "librosa"),
            ("faster_whisper", "faster_whisper", "faster-whisper"),
            ("realesrgan", "real_esrgan", "realesrgan"),
            ("basicsr", "basicsr", "basicsr"),
        ]

        missing = []
        for imp_name, env_key, pip_spec in all_ext_deps:
            try:
                result = hidden_run(
                    [python_exe, "-c",
                     f"import importlib.util; spec = importlib.util.find_spec('{imp_name}'); raise SystemExit(0 if spec else 1)"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode != 0:
                    missing.append((imp_name, env_key, pip_spec))
            except Exception:
                missing.append((imp_name, env_key, pip_spec))

        if not missing:
            self.log.emit("  ✓ 所有扩展组件已就绪", "ok")
            return self.STEP_STATUS_SKIPPED

        self.log.emit(f"  检测到 {len(missing)} 个扩展组件缺失，开始安装...", "info")

        any_installed = False
        failed = []
        for imp_name, env_key, pip_spec in missing:
            if self._should_stop:
                return self.STEP_STATUS_FAILED

            import re
            dep_base = re.split(r'[><=!~\[]', pip_spec)[0].strip()
            display_name = {
                "voxcpm": "VoxCPM2",
                "soundfile": "soundfile",
                "librosa": "librosa",
                "faster_whisper": "faster-whisper",
                "real_esrgan": "Real-ESRGAN",
                "basicsr": "basicsr",
            }.get(env_key, dep_base)

            self.env_update.emit(env_key, f"↻ {display_name} 安装中...", "pending", False)
            self.log.emit(f"  安装 {display_name} ({pip_spec})...", "info")

            result = self._retry_run(
                [uv_exe, "pip", "install", "--python", python_exe, pip_spec] + self._uv_index_args(),
                label=display_name,
                max_retries=1,
                capture_output=True, text=True, timeout=600, env=env
            )

            if result is not None and not isinstance(result, str):
                pass
            else:
                err_hint = result[:200] if isinstance(result, str) else ""
                self.log.emit(f"  △ {display_name} 镜像安装失败{': ' + err_hint if err_hint else ''}，尝试直连PyPI...", "warn")
                fallback_env = env.copy()
                result = self._retry_run(
                    [uv_exe, "pip", "install", "--python", python_exe, pip_spec,
                     "--default-index", "https://pypi.org/simple/"],
                    label=f"{display_name}(PyPI直连)",
                    max_retries=1,
                    capture_output=True, text=True, timeout=600, env=fallback_env
                )

            if result is not None and not isinstance(result, str):
                try:
                    check = hidden_run(
                        [python_exe, "-c",
                         f"import importlib.util; spec = importlib.util.find_spec('{imp_name}'); raise SystemExit(0 if spec else 1)"],
                        capture_output=True, text=True, timeout=5
                    )
                    if check.returncode == 0:
                        ver = self._get_dep_version(python_exe, dep_base)
                        self.env_update.emit(env_key, f"√ {display_name} {ver}" if ver else f"√ {display_name}", "ok", False)
                        self.log.emit(f"  ✓ {display_name} {ver}" if ver else f"  ✓ {display_name}", "ok")
                        any_installed = True
                    else:
                        failed.append(display_name)
                        self.env_update.emit(env_key, f"× {display_name}", "err", True)
                        self.log.emit(f"  △ {display_name} 安装后验证失败", "warn")
                except Exception:
                    failed.append(display_name)
                    self.env_update.emit(env_key, f"× {display_name}", "err", True)
                    self.log.emit(f"  △ {display_name} 验证异常", "warn")
            else:
                failed.append(display_name)
                self.env_update.emit(env_key, f"× {display_name}", "err", True)
                err_detail = result if isinstance(result, str) else ""
                self.log.emit(f"  △ {display_name} 安装失败{': ' + err_detail[:200] if err_detail else ''}", "warn")

        if failed:
            self.log.emit(f"  △ 以下扩展组件安装失败: {', '.join(failed)}", "warn")
            self.log.emit("  这些组件非核心功能所需，可稍后手动安装", "warn")

        if any_installed:
            return self.STEP_STATUS_REPAIRED
        if failed:
            raise Exception(f"扩展组件安装失败: {', '.join(failed)}，可稍后手动安装")
        return self.STEP_STATUS_SKIPPED

    def _get_dep_version(self, python_exe, dep_name):
        try:
            r = hidden_run(
                [python_exe, "-c", f"import importlib.metadata; print(importlib.metadata.version('{dep_name}'))"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except:
            pass
        return ""

    def _run_env_detection(self, python_exe):
        try:
            check = hidden_run(
                [python_exe, "-c", """
import torch, sys, importlib.metadata
from packaging.version import Version

tv = torch.__version__
tc = getattr(torch.version, "cuda", "") or ""
variant = f"cu{tc.replace('.','')}" if tc else "cpu"
is_gpu = torch.cuda.is_available()
print(f"TORCH|{tv}|{variant}|{'GPU' if is_gpu else 'CPU'}")
if tc:
    print(f"CUDA|{tc}|pytorch")
else:
    print("CUDA||not_found")
try:
    cv = str(torch.backends.cudnn.version()) if torch.cuda.is_available() else ""
    print(f"CUDNN|{cv}|pytorch" if cv else "CUDNN||not_found")
except:
    print("CUDNN||not_found")
print(f"PYVER|{sys.version}")
try:
    import pynvml
    pynvml.nvmlInit()
    dv = pynvml.nvmlSystemGetDriverVersion()
    print(f"NVDROP|{dv}")
    pynvml.nvmlShutdown()
except:
    print("NVDROP|")

deps = ["fastapi","uvicorn","safetensors","accelerate","transformers","tokenizers","diffusers",
        "Pillow","sentencepiece","huggingface_hub","sageattention","pydantic",
        "python-multipart","ftfy","imageio","imageio-ffmpeg","peft","protobuf",
        "opencv-python-headless","tqdm","pynvml","einops","scipy","av","triton-windows"]
locks = {"transformers":(Version("4.57"),Version("4.58")),"tokenizers":(Version("0.22"),Version("0.23")),"diffusers":(Version("0.25"),Version("1.0")),
         "accelerate":(Version("0.24"),Version("2.0")),"safetensors":(Version("0.4"),Version("1.0")),
         "peft":(Version("0.13"),Version("1.0")),"pydantic":(Version("2.7"),Version("3.0")),
         "huggingface_hub":(Version("0.30"),Version("1.0")),"sentencepiece":(Version("0.1.99"),Version("1.0")),
         "ftfy":(Version("6.0"),Version("7.0")),"imageio":(Version("2.37"),Version("3.0")),
         "imageio-ffmpeg":(Version("0.6"),Version("1.0")),"protobuf":(Version("3.20"),Version("7.0")),
         "opencv-python-headless":(Version("4.8"),Version("5.0")),"tqdm":(Version("4.66"),Version("5.0")),
         "pynvml":(Version("11.5"),Version("14.0")),"einops":(Version("0.8"),Version("1.0")),
         "scipy":(Version("1.14"),Version("2.0")),"av":(Version("16.0"),Version("17.0"))}
for d in deps:
    try:
        v = importlib.metadata.version(d)
        if d in locks:
            lo,hi = locks[d]
            vv = Version(v.split('+')[0].split('dev')[0].rstrip('.'))
            status = "OK" if lo <= vv < hi else "BAD"
        else:
            status = "OK"
        print(f"DEP|{status}|{d}|{v}")
    except:
        print(f"DEP|MISS|{d}|0")
"""],
                capture_output=True, text=True, timeout=60
            )
            if check.returncode == 0:
                for line in check.stdout.strip().split('\n'):
                    parts = line.split('|')
                    if parts[0] == "TORCH" and len(parts) >= 4:
                        ver, variant, mode = parts[1], parts[2], parts[3]
                        if mode == "CPU":
                            self.env_update.emit("pytorch", f"× {ver} CPU版", "err", True)
                        else:
                            self.env_update.emit("pytorch", f"√ {ver}+{variant}", "ok", False)
                    elif parts[0] == "CUDA" and len(parts) >= 2:
                        ver = parts[1]
                        if ver:
                            self.env_update.emit("cuda", f"√ {ver}", "ok", False)
                        else:
                            self.env_update.emit("cuda", "× 未检测到", "err", True)
                    elif parts[0] == "CUDNN" and len(parts) >= 2:
                        ver = parts[1]
                        if ver:
                            self.env_update.emit("cudnn", f"√ {ver}", "ok", False)
                        else:
                            self.env_update.emit("cudnn", "△ 未检测到", "warn", False)
                    elif parts[0] == "PYVER" and len(parts) >= 1:
                        py_ver = parts[1].strip() if len(parts) > 1 else ""
                        if "3.12" in py_ver:
                            self.env_update.emit("python", f"√ Python {py_ver.split()[0]}", "ok", False)
                        elif "3.13" in py_ver or "3.14" in py_ver:
                            self.env_update.emit("python", f"△ {py_ver.split()[0]} (不兼容)", "warn", True)
                    elif parts[0] == "NVDROP" and len(parts) >= 1:
                        drv = parts[1].strip() if len(parts) > 1 else ""
                        if drv:
                            try:
                                dv = float(drv.split('.')[0]) + float(drv.split('.')[1]) / 100.0
                                if dv >= 560.70:
                                    self.env_update.emit("nvidia_driver", f"√ {drv}", "ok", False)
                                else:
                                    self.env_update.emit("nvidia_driver", f"△ {drv} (需>=560.70)", "warn", True)
                            except:
                                self.env_update.emit("nvidia_driver", f"√ {drv}", "ok", False)
                        else:
                            self.env_update.emit("nvidia_driver", "× 未检测到", "err", True)
                    elif parts[0] == "DEP" and len(parts) >= 4:
                        status, name, ver = parts[1], parts[2], parts[3]
                        if name in self._env_check_widgets:
                            if status == "OK":
                                self.env_update.emit(name, f"√ {ver}", "ok", False)
                            elif status == "BAD":
                                lock = LTX_PIP_VERSION_LOCKS.get(name, "")
                                self.env_update.emit(name, f"△ {ver} (需{lock})", "warn", True)
                            else:
                                self.env_update.emit(name, "× 未安装", "err", True)
        except:
            pass

    def _install_ltx_packages(self, python_exe, uv_exe, env):
        try:
            result = hidden_run(
                [python_exe, "-c", "import importlib.util; spec = importlib.util.find_spec('ltx_core'); raise SystemExit(0 if spec else 1)"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self.log.emit("  ✓ ltx_core 已安装", "ok")
                return
        except:
            pass

        self.log.emit("  安装 LTX 核心包 (ltx-core, ltx-pipelines)...", "info")
        ltx2_urls = self._resolved_mirrors.get("ltx2_urls", [])
        if ltx2_urls:
            for url, label in ltx2_urls:
                if self._should_stop:
                    return
                self.log.emit(f"  下载 LTX-2 源码 ({label})...", "info")
                try:
                    zip_path = os.path.join(tempfile.gettempdir(), "ltx2_source.zip")
                    extract_dir = os.path.join(tempfile.gettempdir(), "ltx2_source")
                    urllib.request.urlretrieve(url, zip_path)
                    if os.path.exists(extract_dir):
                        shutil.rmtree(extract_dir)
                    with zipfile.ZipFile(zip_path, 'r') as zf:
                        zf.extractall(extract_dir)
                    subdirs = [d for d in os.listdir(extract_dir) if os.path.isdir(os.path.join(extract_dir, d))]
                    if not subdirs:
                        raise Exception("压缩包内容为空")
                    src_root = os.path.join(extract_dir, subdirs[0])
                    ltx_core_dir = os.path.join(src_root, "packages", "ltx-core")
                    ltx_pipelines_dir = os.path.join(src_root, "packages", "ltx-pipelines")
                    if not os.path.isdir(ltx_core_dir):
                        raise Exception(f"未找到 ltx-core 子目录")
                    if not os.path.isdir(ltx_pipelines_dir):
                        raise Exception(f"未找到 ltx-pipelines 子目录")
                    self.log.emit("  安装 ltx-core...", "info")
                    result = self._retry_run(
                        [uv_exe, "pip", "install", "--python", python_exe, ltx_core_dir] + self._uv_index_args(),
                        label="ltx-core",
                        max_retries=2,
                        capture_output=True, text=True, timeout=600, env=env
                    )
                    if isinstance(result, str):
                        raise Exception(f"ltx-core 安装失败: {result}")
                    self.log.emit("  ✓ ltx-core 安装成功", "ok")
                    self.log.emit("  安装 ltx-pipelines...", "info")
                    result = self._retry_run(
                        [uv_exe, "pip", "install", "--python", python_exe, ltx_pipelines_dir] + self._uv_index_args(),
                        label="ltx-pipelines",
                        max_retries=2,
                        capture_output=True, text=True, timeout=600, env=env
                    )
                    if isinstance(result, str):
                        raise Exception(f"ltx-pipelines 安装失败: {result}")
                    self.log.emit("  ✓ ltx-pipelines 安装成功", "ok")
                    try:
                        os.remove(zip_path)
                        shutil.rmtree(extract_dir)
                    except:
                        pass
                    return
                except Exception as e:
                    self.log.emit(f"  △ {label} 失败: {e}", "warn")
                    continue

        self.log.emit("  尝试从 GitHub 直接安装 ltx-core...", "info")
        result = self._retry_run(
            [uv_exe, "pip", "install", "--python", python_exe,
             "ltx-core @ git+https://github.com/Lightricks/LTX-2.git@59ca828d5ae24358832ffd7003c2306fbceeba3a#subdirectory=packages/ltx-core"],
            label="ltx-core (git)",
            max_retries=2,
            capture_output=True, text=True, timeout=600, env=env
        )
        if isinstance(result, str):
            raise Exception(f"ltx-core 安装失败: {result}")
        self.log.emit("  ✓ ltx-core 安装成功", "ok")
        result = self._retry_run(
            [uv_exe, "pip", "install", "--python", python_exe,
             "ltx-pipelines @ git+https://github.com/Lightricks/LTX-2.git@59ca828d5ae24358832ffd7003c2306fbceeba3a#subdirectory=packages/ltx-pipelines"],
            label="ltx-pipelines (git)",
            max_retries=2,
            capture_output=True, text=True, timeout=600, env=env
        )
        if isinstance(result, str):
            raise Exception(f"ltx-pipelines 安装失败: {result}")
        self.log.emit("  ✓ ltx-pipelines 安装成功", "ok")

        self._install_sageattention(python_exe, uv_exe, env)

    def _install_sageattention(self, python_exe, uv_exe, env):
        try:
            result = hidden_run(
                [python_exe, "-c", "import importlib.util; spec = importlib.util.find_spec('sageattention'); raise SystemExit(0 if spec else 1)"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self.log.emit("  ✓ SageAttention 已安装", "ok")
                return
        except:
            pass

        self.log.emit("  安装 SageAttention (性能加速)...", "info")

        torch_ver = ""
        cuda_ver = ""
        try:
            r = hidden_run([python_exe, "-c", "import torch; print(torch.__version__)"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                torch_ver = r.stdout.strip().split("+")[0]
            r3 = hidden_run([python_exe, "-c", "import torch; print(torch.version.cuda or '')"], capture_output=True, text=True, timeout=5)
            if r3.returncode == 0:
                cuda_ver = r3.stdout.strip().replace(".", "")
        except:
            pass

        sa_pypi_spec = "sageattention>=1.0,<3.0"

        wheel_urls = []
        if torch_ver and cuda_ver:
            torch_major_minor = ".".join(torch_ver.split(".")[:2])
            for tag in ["v2.2.0-windows.post2", "v2.2.0-windows.post1", "v2.2.0-windows"]:
                ver_base = tag.lstrip("v").split("-")[0]
                wheel_urls.append(
                    (f"预编译 wheel ({tag})",
                     f"https://github.com/woct0rdho/SageAttention/releases/download/{tag}/sageattention-{ver_base}+cu{cuda_ver}torch{torch_major_minor}-cp39-abi3-win_amd64.whl")
                )

        attempts = [("PyPI (Triton版)", sa_pypi_spec)]
        for label, url in wheel_urls:
            attempts.append((label, url))

        for label, url in attempts:
            if self._should_stop:
                return
            self.log.emit(f"  尝试 {label}...", "info")
            try:
                is_url = url.startswith("http://") or url.startswith("https://")
                cmd = [uv_exe, "pip", "install", "--python", python_exe, url]
                if not is_url:
                    cmd += self._uv_index_args()
                result = self._retry_run(
                    cmd,
                    label=f"SageAttention ({label})",
                    max_retries=1,
                    capture_output=True, text=True, timeout=600, env=env
                )
                if result is not None and not isinstance(result, str):
                    self.log.emit("  ✓ SageAttention 安装成功", "ok")
                    return
                self.log.emit(f"  △ {label} 安装失败，尝试下一方式...", "warn")
            except Exception as e:
                self.log.emit(f"  △ {label} 异常: {e}", "warn")

        self.log.emit("  △ SageAttention 安装失败（非关键依赖，不影响运行）", "warn")
        self.log.emit("  △ 提示: 可从 https://github.com/woct0rdho/SageAttention/releases 下载对应版本的 .whl 文件后手动安装", "warn")

    def _step_patches(self):
        if self._check_patches_ok():
            return self.STEP_STATUS_SKIPPED

        dst = os.path.join(self.app_res, "patches")
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(self.app_res)))
        src = os.path.join(project_root, "项目参考", "LTX2.3启动器", "patches")
        if not os.path.exists(src):
            raise Exception("未找到补丁文件源，请确保项目参考目录完整")
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

        if not self._check_patches_ok():
            raise Exception("补丁文件部署后验证失败")

        return self.STEP_STATUS_INSTALLED

    def _step_ui(self):
        if self._check_ui_ok():
            return self.STEP_STATUS_SKIPPED

        dst = os.path.join(self.app_res, "ui")
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(self.app_res)))
        src = os.path.join(project_root, "项目参考", "LTX2.3启动器", "UI")
        if not os.path.exists(src):
            raise Exception("未找到前端界面源，请确保项目参考目录完整")
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

        if not self._check_ui_ok():
            raise Exception("前端界面部署后验证失败")

        return self.STEP_STATUS_INSTALLED

    def _step_backend(self):
        if self._check_backend_ok():
            return self.STEP_STATUS_SKIPPED

        dst = os.path.join(self.app_res, "backend")

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(self.app_res)))
        ltx_search = [
            os.path.join(project_root, "LTX Desktop", "resources", "backend"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "LTX Desktop", "resources", "backend"),
            r"C:\Program Files\LTX Desktop\resources\backend",
            r"D:\Program Files\LTX Desktop\resources\backend",
            r"E:\Program Files\LTX Desktop\resources\backend",
        ]
        src = None
        for p in ltx_search:
            if os.path.exists(p) and os.path.exists(os.path.join(p, "ltx2_server.py")):
                src = p
                break

        if not src:
            self.log.emit("  本地未找到 LTX Desktop，尝试自动下载...", "info")
            src = self._download_and_extract_ltx_backend()
            if not src:
                raise Exception("未找到 LTX Desktop 后端代码，自动下载也失败。请手动安装 LTX Desktop 后重试")

        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

        if not self._check_backend_ok():
            raise Exception("后端代码部署后验证失败")

        return self.STEP_STATUS_INSTALLED

    def _download_and_extract_ltx_backend(self):
        temp_dir = os.path.join(self.app_res, "_temp_ltx")
        os.makedirs(temp_dir, exist_ok=True)
        zip_path = os.path.join(temp_dir, f"ltx-desktop-v{LTX_DESKTOP_VERSION}.zip")

        ltx_urls = [(url_tpl.format(ver=LTX_DESKTOP_VERSION), name)
                     for url_tpl, name in self._resolved_mirrors["ltx_urls"]]
        self.log.emit(f"  下载 LTX Desktop v{LTX_DESKTOP_VERSION} 源码 (竞速选择最快源)...", "info")
        winner_name, err = self._download_racing(ltx_urls, zip_path, "LTX Desktop 源码")

        if winner_name is None:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None

        self.log.emit(f"  √ 源码下载完成 ({winner_name})", "ok")

        self.log.emit("  从源码包提取后端代码...", "info")
        backend_src = None
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                root_dir = None
                for name in zf.namelist():
                    parts = name.split("/")
                    if len(parts) >= 2 and parts[1] == "backend" and parts[-1] == "ltx2_server.py":
                        root_dir = parts[0]
                        break

                if not root_dir:
                    for name in zf.namelist():
                        parts = name.split("/")
                        if len(parts) >= 3 and parts[1] == "resources" and parts[2] == "backend" and parts[-1] == "ltx2_server.py":
                            root_dir = parts[0]
                            break

                if root_dir:
                    backend_prefix = f"{root_dir}/backend/"
                    resources_prefix = f"{root_dir}/resources/backend/"

                    actual_prefix = None
                    for name in zf.namelist():
                        if name.startswith(resources_prefix) and name.endswith("ltx2_server.py"):
                            actual_prefix = resources_prefix
                            break
                        elif name.startswith(backend_prefix) and name.endswith("ltx2_server.py"):
                            actual_prefix = backend_prefix
                            break

                    if actual_prefix:
                        extract_dir = os.path.join(temp_dir, "extracted")
                        os.makedirs(extract_dir, exist_ok=True)
                        for member in zf.namelist():
                            if member.startswith(actual_prefix) and not member.endswith("/"):
                                relative = member[len(actual_prefix):]
                                if relative:
                                    target = os.path.join(extract_dir, relative)
                                    os.makedirs(os.path.dirname(target), exist_ok=True)
                                    with zf.open(member) as src, open(target, 'wb') as dst:
                                        dst.write(src.read())
                        backend_src = extract_dir
                        self.log.emit("  √ 成功从源码包提取后端代码", "ok")
                    else:
                        self.log.emit("  △ 源码包中未找到后端代码目录结构", "warn")
                else:
                    self.log.emit("  △ 源码包中未找到 ltx2_server.py", "warn")

        except zipfile.BadZipFile:
            self.log.emit("  △ 下载的源码包损坏", "warn")
        except Exception as e:
            self.log.emit(f"  △ 解压源码包失败: {e}", "warn")

        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except Exception:
            pass

        if not backend_src:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

        return backend_src

    def _step_data(self):
        dst = self._data_dir
        os.makedirs(dst, exist_ok=True)

        settings_path = os.path.join(dst, "settings.json")
        if not os.path.exists(settings_path):
            src_settings = os.path.join(os.path.dirname(self._data_dir), "项目参考", "环境包", "LTXDesktop", "settings.json")
            if os.path.exists(src_settings):
                shutil.copy2(src_settings, settings_path)

        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                settings["models_dir"] = os.path.join(dst, "models")
                with open(settings_path, 'w', encoding='utf-8') as f:
                    json.dump(settings, f, ensure_ascii=False, indent=2)
            except:
                pass

        outputs_dir = os.path.join(dst, "outputs")
        os.makedirs(outputs_dir, exist_ok=True)
        custom_dir_file = os.path.join(dst, "custom_dir.txt")
        if not os.path.exists(custom_dir_file):
            with open(custom_dir_file, 'w', encoding='utf-8') as f:
                f.write(outputs_dir)

        models_dir = os.path.join(dst, "models")
        os.makedirs(models_dir, exist_ok=True)

        if self._check_data_ok():
            return self.STEP_STATUS_SKIPPED
        return self.STEP_STATUS_INSTALLED

    def _step_ffmpeg(self):
        ffmpeg_path = os.environ.get("LTX_FFMPEG_PATH")
        if ffmpeg_path and os.path.isfile(ffmpeg_path):
            self.log.emit("  ✓ ffmpeg 已存在，跳过安装", "ok")
            return self.STEP_STATUS_SKIPPED

        ffmpeg_file = Path(os.environ.get("LOCALAPPDATA", "")) / "LTXDesktop" / "ffmpeg_path.txt"
        if ffmpeg_file.exists():
            try:
                custom_path = ffmpeg_file.read_text(encoding="utf-8").strip()
                if custom_path and os.path.isfile(custom_path):
                    self.log.emit("  ✓ ffmpeg 已存在，跳过安装", "ok")
                    return self.STEP_STATUS_SKIPPED
            except Exception:
                pass

        ffmpeg_exe = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
        if ffmpeg_exe:
            self.log.emit("  ✓ ffmpeg 已在系统 PATH 中，跳过安装", "ok")
            return self.STEP_STATUS_SKIPPED

        self.log.emit("  正在下载 ffmpeg 便携版...", "info")
        try:
            from urllib.request import Request, urlopen
            ffmpeg_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
            install_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "LTXDesktop" / "ffmpeg"
            install_dir.mkdir(parents=True, exist_ok=True)
            zip_path = install_dir / "ffmpeg-release-essentials.zip"

            req = Request(ffmpeg_url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=300) as resp:
                with open(zip_path, "wb") as f:
                    shutil.copyfileobj(resp, f)

            self.log.emit("  正在解压 ffmpeg...", "info")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(install_dir)
            zip_path.unlink(missing_ok=True)

            ffmpeg_exe = None
            for p in install_dir.rglob("ffmpeg.exe"):
                ffmpeg_exe = p
                break

            if ffmpeg_exe is None:
                raise Exception("ffmpeg.exe 未在解压文件中找到")

            ffmpeg_path_file = Path(os.environ.get("LOCALAPPDATA", "")) / "LTXDesktop" / "ffmpeg_path.txt"
            ffmpeg_path_file.parent.mkdir(parents=True, exist_ok=True)
            ffmpeg_path_file.write_text(str(ffmpeg_exe), encoding="utf-8")
            os.environ["LTX_FFMPEG_PATH"] = str(ffmpeg_exe)

            self.log.emit(f"  ✓ ffmpeg 便携版安装成功: {ffmpeg_exe}", "ok")
            return self.STEP_STATUS_INSTALLED
        except Exception as e:
            raise Exception(f"ffmpeg 便携版安装失败: {e}，可稍后在环境检测中点击修复按钮手动安装")

    def _step_models(self):
        models_dir = os.path.join(self._data_dir, "models")
        os.makedirs(models_dir, exist_ok=True)

        python_exe = self._venv_python
        if not os.path.exists(python_exe):
            raise Exception("Python 未安装，无法下载模型")

        uv_exe = self._uv_exe
        has_hf_hub = False
        try:
            result = hidden_run(
                [python_exe, "-c", "from huggingface_hub import hf_hub_download; print('ok')"],
                capture_output=True, text=True, timeout=10
            )
            has_hf_hub = result.returncode == 0
        except:
            pass

        if not has_hf_hub:
            self.log.emit("  安装 huggingface_hub...", "info")
            env = os.environ.copy()
            for _ek in ("UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX", "UV_INDEX", "PYTHONHOME"):
                env.pop(_ek, None)
            env["UV_LINK_MODE"] = "copy"
            self._retry_run(
                [uv_exe, "pip", "install", "--python", python_exe, "huggingface_hub>=0.30,<1.0"] + self._uv_index_args(),
                label="huggingface_hub",
                capture_output=True, text=True, timeout=120, env=env
            )

        any_downloaded = False
        any_skipped = False

        # 提前写入辅助脚本（复用，避免重复写入）
        helper_path = self._write_helper_script()

        for model_id, info in LTX_MODELS.items():
            if not info.get("required", False):
                continue
            if self._should_stop:
                return self.STEP_STATUS_FAILED

            is_folder = info.get("is_folder", False)
            target_path = os.path.join(models_dir, info["file"])
            expected_bytes = info["size_bytes"]

            if is_folder:
                if os.path.exists(target_path) and os.path.isdir(target_path):
                    folder_size = sum(f.stat().st_size for f in __import__('pathlib').Path(target_path).rglob("*") if f.is_file())
                    if folder_size > expected_bytes * 0.5:
                        self.log.emit(f"  √ {info['file']} 已存在且完整，跳过", "info")
                        any_skipped = True
                        continue
                    else:
                        self.log.emit(f"  △ {info['file']} 不完整，重新下载...", "warn")
                self.log.emit(f"  下载 {info['file']} ({info['size_bytes']/1024/1024/1024:.1f}GB，文件夹)...", "info")
                env = os.environ.copy()
                env["HF_ENDPOINT"] = self._resolved_mirrors["hf_endpoint"]
                env["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
                env.pop("PYTHONHOME", None)
                success = False
                for attempt in range(1, self.MAX_RETRIES + 1):
                    if self._should_stop:
                        return self.STEP_STATUS_FAILED
                    try:
                        cmd = [python_exe, "-u", helper_path, "snapshot", info['repo'], target_path, ""]
                        proc = hidden_popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                        ok, result = self._monitor_download_progress_v2(proc, info['file'], expected_bytes, "HF镜像", target_path=target_path, is_folder=True)
                        if ok and os.path.exists(target_path):
                            folder_size = sum(f.stat().st_size for f in __import__('pathlib').Path(target_path).rglob("*") if f.is_file())
                            if folder_size > expected_bytes * 0.5:
                                success = True
                                self.log.emit(f"  √ {info['file']} 下载完成", "ok")
                                break
                        err = result if not ok else "未知错误"
                        self.log.emit(f"  △ 第{attempt}次下载失败: {err}", "warn")
                        if attempt < self.MAX_RETRIES:
                            time.sleep(3)
                    except Exception as e:
                        self.log.emit(f"  △ 下载异常: {e}", "warn")
                        if attempt < self.MAX_RETRIES:
                            time.sleep(3)
                if success:
                    any_downloaded = True
                continue

            target_file = target_path

            if os.path.exists(target_file) and os.path.getsize(target_file) > expected_bytes * 0.9:
                self.log.emit(f"  √ {info['file']} 已存在且完整，跳过", "info")
                any_skipped = True
                continue

            if os.path.exists(target_file) and os.path.getsize(target_file) <= expected_bytes * 0.9:
                actual_mb = os.path.getsize(target_file) / 1024 / 1024
                expected_mb = expected_bytes / 1024 / 1024
                self.log.emit(f"  △ {info['file']} 不完整({actual_mb:.0f}MB/{expected_mb:.0f}MB)，重新下载...", "warn")
                os.remove(target_file)

            self.log.emit(f"  下载 {info['file']} ({info['size_bytes']/1024/1024/1024:.1f}GB)...", "info")

            env = os.environ.copy()
            env["HF_ENDPOINT"] = self._resolved_mirrors["hf_endpoint"]
            env["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
            env.pop("PYTHONHOME", None)

            success = False

            for attempt in range(1, self.MAX_RETRIES + 1):
                if self._should_stop:
                    return self.STEP_STATUS_FAILED
                try:
                    cmd = [python_exe, "-u", helper_path, "file", info['repo'], models_dir, info['file']]
                    proc = hidden_popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                    ok, result = self._monitor_download_progress_v2(proc, info['file'], expected_bytes, "HF镜像", target_path=target_file, is_folder=False)
                    if ok and os.path.exists(target_file) and os.path.getsize(target_file) > expected_bytes * 0.9:
                        success = True
                        self.log.emit(f"  √ {info['file']} 下载完成 (HF镜像)", "ok")
                        break
                    else:
                        err = result if not ok else "未知错误"
                        self.log.emit(f"  △ HF镜像第{attempt}次下载失败: {err}", "warn")
                        if attempt < self.MAX_RETRIES:
                            time.sleep(5 * attempt)
                except Exception as e:
                    self.log.emit(f"  △ HF镜像下载异常: {e}", "warn")
                    if attempt < self.MAX_RETRIES:
                        time.sleep(5 * attempt)

            if not success:
                self.log.emit("  尝试 ModelScope 镜像...", "info")
                try:
                    ms_env = os.environ.copy()
                    for _ek in ("UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX", "UV_INDEX", "PYTHONHOME"):
                        ms_env.pop(_ek, None)
                    ms_env["UV_LINK_MODE"] = "copy"
                    self._retry_run(
                        [uv_exe, "pip", "install", "--python", python_exe, "modelscope"] + self._uv_index_args(),
                        label="modelscope",
                        capture_output=True, text=True, timeout=120, env=ms_env
                    )
                    ms_model_id = info.get("modelscope_id", info["repo"])
                    ms_mode = "ms_snapshot" if is_folder else "ms_file"
                    cmd = [python_exe, "-u", helper_path, ms_mode, ms_model_id,
                           target_path if is_folder else models_dir,
                           "" if is_folder else info['file']]
                    proc = hidden_popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ms_env)
                    ok, result = self._monitor_download_progress_v2(proc, info['file'], expected_bytes, "ModelScope", target_path=target_path if is_folder else target_file, is_folder=is_folder)
                    if ok and os.path.exists(target_file) and os.path.getsize(target_file) > expected_bytes * 0.9:
                        success = True
                        self.log.emit(f"  √ {info['file']} 下载完成 (ModelScope)", "ok")
                    else:
                        err = result if not ok else "未知错误"
                        self.log.emit(f"  △ ModelScope 也失败: {err}", "warn")
                except Exception as e:
                    self.log.emit(f"  △ ModelScope 异常: {e}", "warn")

            if not success:
                self.log.emit("  尝试 HF 直连下载...", "info")
                try:
                    direct_env = os.environ.copy()
                    direct_env.pop("HF_ENDPOINT", None)
                    direct_env["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
                    direct_env.pop("PYTHONHOME", None)
                    cmd = [python_exe, "-u", helper_path, "file", info['repo'], models_dir, info['file']]
                    proc = hidden_popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=direct_env)
                    ok, result = self._monitor_download_progress_v2(proc, info['file'], expected_bytes, "HF直连", target_path=target_file, is_folder=False)
                    if ok and os.path.exists(target_file) and os.path.getsize(target_file) > expected_bytes * 0.9:
                        success = True
                        self.log.emit(f"  √ {info['file']} 下载完成 (HF直连)", "ok")
                    else:
                        err = result if not ok else "未知错误"
                        self.log.emit(f"  △ HF直连也失败: {err}", "warn")
                except Exception as e:
                    self.log.emit(f"  △ HF直连异常: {e}", "warn")

            if not success:
                if info["required"]:
                    raise Exception(f"必需模型 {info['file']} 下载失败（HF+ModelScope 均不可用），请手动下载到 data/models/ 目录")
                else:
                    self.log.emit(f"  △ 可选模型 {info['file']} 下载失败，可稍后在模型管理页面手动下载", "warn")
            else:
                any_downloaded = True

        if any_downloaded:
            return self.STEP_STATUS_INSTALLED
        elif any_skipped:
            return self.STEP_STATUS_SKIPPED
        return self.STEP_STATUS_INSTALLED


class ConfigManager:
    CONFIG_FILE = "launcher_config.json"

    def __init__(self, base_dir, data_dir=None):
        self.base_dir = base_dir
        if data_dir and os.path.isdir(data_dir):
            self.config_dir = os.path.join(data_dir, "config")
        else:
            self.config_dir = os.path.join(base_dir, "config")
        os.makedirs(self.config_dir, exist_ok=True)
        self.config_path = os.path.join(self.config_dir, self.CONFIG_FILE)
        self._migrate_legacy_config(base_dir)
        self.config = self._load()

    def _migrate_legacy_config(self, base_dir):
        legacy_path = os.path.join(base_dir, "config", self.CONFIG_FILE)
        if os.path.exists(legacy_path) and not os.path.exists(self.config_path):
            try:
                import shutil
                os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
                shutil.copy2(legacy_path, self.config_path)
            except Exception:
                pass

    def _load(self):
        default = {
            "version": VERSION,
            "services": {"auto_start": False, "auto_open": True},
            "ui": {"window_size": {"width": 1100, "height": 800}},
            "models_dir": "",
            "ports": {"backend": 3000, "frontend": 4000},
            "browser": {"default": "system", "custom_path": ""},
        }
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                for k, v in loaded.items():
                    if k in default:
                        if isinstance(v, dict) and isinstance(default[k], dict):
                            default[k].update(v)
                        else:
                            default[k] = v
                    else:
                        default[k] = v
            except:
                pass
        return default

    def save(self):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except:
            pass

    def get(self, key, default=None):
        keys = key.split('.')
        val = self.config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    def set(self, key, value):
        keys = key.split('.')
        cfg = self.config
        for k in keys[:-1]:
            if k not in cfg:
                cfg[k] = {}
            cfg = cfg[k]
        cfg[keys[-1]] = value
        self.save()


class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, label="", parent=None, checked=True, checked_color="#1B5E20"):
        super().__init__(parent)
        self._checked = checked
        self._label = label
        self._checked_color = checked_color
        self.setFixedHeight(28)
        self.setMinimumWidth(100)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def isChecked(self):
        return self._checked

    def setChecked(self, checked):
        if self._checked != checked:
            self._checked = checked
            self.toggled.emit(self._checked)
        self.update()

    def mousePressEvent(self, event):
        self._checked = not self._checked
        self.toggled.emit(self._checked)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track_w, track_h = 36, 18
        track_x = 0
        track_y = (self.height() - track_h) // 2
        if self._checked:
            p.setBrush(QColor(self._checked_color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(track_x, track_y, track_w, track_h, track_h // 2, track_h // 2)
            knob_x = track_x + track_w - track_h + 2
        else:
            p.setBrush(QColor("#3A3A3A"))
            p.setPen(QPen(QColor("#4A4A4A"), 1))
            p.drawRoundedRect(track_x, track_y, track_w, track_h, track_h // 2, track_h // 2)
            knob_x = track_x + 2
        p.setBrush(QColor("#FFFFFF"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(knob_x, track_y + 2, track_h - 4, track_h - 4)
        if self._label:
            p.setPen(QColor("#CCCCCC"))
            p.setFont(QFont("Microsoft YaHei", 9))
            p.drawText(track_x + track_w + 6, track_y + track_h - 3, self._label)
        p.end()


class ServiceCard(QFrame):
    restart_clicked = pyqtSignal(str)
    open_clicked = pyqtSignal(str)
    port_change_clicked = pyqtSignal(str)

    def __init__(self, service_id, parent=None):
        super().__init__(parent)
        self.service_id = service_id
        self.service_info = SERVICES[service_id]
        self.is_running = False
        self.setObjectName("cardFrame")
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        icon_lbl = QLabel(self.service_info["icon"])
        icon_lbl.setStyleSheet("font-size: 24px; background: transparent;")
        top_row.addWidget(icon_lbl)

        name_lbl = QLabel(self.service_info["name"])
        name_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #FFFFFF; background: transparent;")
        top_row.addWidget(name_lbl)

        desc_lbl = QLabel(self.service_info["desc"])
        desc_lbl.setStyleSheet("font-size: 11px; color: #888888; background: transparent;")
        top_row.addWidget(desc_lbl)

        top_row.addStretch()

        self.status_dot = QLabel()
        self.status_dot.setFixedSize(14, 14)
        self.status_dot.setStyleSheet("background-color: #424242; border: 2px solid #616161; border-radius: 7px;")
        top_row.addWidget(self.status_dot)

        self.status_text = QLabel("未启动")
        self.status_text.setStyleSheet("font-size: 12px; color: #AAAAAA; background: transparent;")
        top_row.addWidget(self.status_text)

        layout.addLayout(top_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.restart_btn = QPushButton("🔄 重启")
        self.restart_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #1565C0; color: #FFFFFF;
                border: 1px solid #1976D2; border-radius: 6px;
                padding: 6px 16px; font-size: 12px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: #1976D2; }}
        """)
        self.restart_btn.clicked.connect(lambda: self.restart_clicked.emit(self.service_id))
        btn_row.addWidget(self.restart_btn)

        if self.service_id == "backend":
            self.open_btn = QPushButton("ℹ️ 信息")
        else:
            self.open_btn = QPushButton("🌐 打开")
        btn_color = self.service_info["color"]
        hover_color = "#FF3333" if btn_color == "#FF0000" else "#4CAF50"
        self.open_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_color}; color: #FFFFFF;
                border: 1px solid {btn_color}; border-radius: 6px;
                padding: 6px 16px; font-size: 12px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {hover_color}; }}
            QPushButton:pressed {{ background-color: {btn_color}; }}
        """)
        self.open_btn.clicked.connect(lambda: self.open_clicked.emit(self.service_id))
        btn_row.addWidget(self.open_btn)

        btn_row.addStretch()

        self.port_lbl = QLabel(f"端口 {self.service_info['port']}")
        self.port_lbl.setStyleSheet("font-size: 12px; color: #AAAAAA; background: transparent; padding: 4px 8px;")
        btn_row.addWidget(self.port_lbl)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(self.service_info['port'])
        self.port_spin.setFixedWidth(100)
        self.port_spin.setStyleSheet("""
            QSpinBox {
                background-color: #252525; color: #FFFFFF;
                border: 1px solid #444444; border-radius: 4px;
                padding: 4px 6px; font-size: 12px;
            }
            QSpinBox:focus { border-color: #1976D2; }
        """)
        self.port_spin.setVisible(False)
        btn_row.addWidget(self.port_spin)

        self.port_edit_btn = QPushButton("✏️ 修改")
        self.port_edit_btn.setFixedSize(68, 30)
        self.port_edit_btn.setStyleSheet("""
            QPushButton {
                background-color: #333333; color: #CCCCCC;
                border: 1px solid #444444; border-radius: 4px;
                font-size: 12px; padding: 4px 8px;
            }
            QPushButton:hover { background-color: #3D3D3D; color: #FFFFFF; border-color: #1976D2; }
        """)
        btn_row.addWidget(self.port_edit_btn)

        self.port_confirm_btn = QPushButton("✓ 确定")
        self.port_confirm_btn.setFixedSize(68, 30)
        self.port_confirm_btn.setStyleSheet("""
            QPushButton {
                background-color: #2E7D32; color: #FFFFFF;
                border: 1px solid #388E3C; border-radius: 4px;
                font-size: 12px; font-weight: bold; padding: 4px 8px;
            }
            QPushButton:hover { background-color: #388E3C; }
        """)
        self.port_confirm_btn.setVisible(False)
        btn_row.addWidget(self.port_confirm_btn)

        self.port_cancel_btn = QPushButton("✖ 取消")
        self.port_cancel_btn.setFixedSize(68, 30)
        self.port_cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #CC0000; color: #FFFFFF;
                border: 1px solid #FF0000; border-radius: 4px;
                font-size: 12px; font-weight: bold; padding: 4px 8px;
            }
            QPushButton:hover { background-color: #FF0000; }
            QPushButton:pressed { background-color: #DD0000; }
        """)
        self.port_cancel_btn.setVisible(False)
        btn_row.addWidget(self.port_cancel_btn)

        self.port_edit_btn.clicked.connect(self._enter_port_edit)
        self.port_confirm_btn.clicked.connect(self._confirm_port_edit)
        self.port_cancel_btn.clicked.connect(self._cancel_port_edit)

        layout.addLayout(btn_row)

    def update_status(self, is_running):
        self.is_running = is_running
        if is_running:
            self.status_dot.setStyleSheet("background-color: #4CAF50; border: 2px solid #388E3C; border-radius: 7px;")
            self.status_text.setText("运行中")
            self.status_text.setStyleSheet("font-size: 12px; color: #4CAF50; font-weight: bold; background: transparent;")
            self.setStyleSheet(f"""
                QFrame#cardFrame {{
                    background-color: #1A1A1A;
                    border: 2px solid {self.service_info["color"]};
                    border-radius: 10px;
                }}
            """)
        else:
            self.status_dot.setStyleSheet("background-color: #424242; border: 2px solid #616161; border-radius: 7px;")
            self.status_text.setText("未启动")
            self.status_text.setStyleSheet("font-size: 12px; color: #AAAAAA; background: transparent;")
            self.setStyleSheet("""
                QFrame#cardFrame {
                    background-color: #1A1A1A;
                    border: 1px solid #333333;
                    border-radius: 10px;
                }
            """)

    def _enter_port_edit(self):
        self.port_lbl.setVisible(False)
        self.port_edit_btn.setVisible(False)
        self.port_spin.setVisible(True)
        self.port_confirm_btn.setVisible(True)
        self.port_cancel_btn.setVisible(True)
        self.port_spin.setFocus()

    def _confirm_port_edit(self):
        new_port = self.port_spin.value()
        self.service_info['port'] = new_port
        self.port_lbl.setText(f"端口 {new_port}")
        self._exit_port_edit()
        self.port_change_clicked.emit(self.service_id)

    def _cancel_port_edit(self):
        self.port_spin.setValue(self.service_info['port'])
        self._exit_port_edit()

    def _exit_port_edit(self):
        self.port_lbl.setVisible(True)
        self.port_edit_btn.setVisible(True)
        self.port_spin.setVisible(False)
        self.port_confirm_btn.setVisible(False)
        self.port_cancel_btn.setVisible(False)


class SplashScreen(QSplashScreen):
    def __init__(self):
        pixmap = QPixmap(520, 360)
        pixmap.fill(QColor("#0D0D0D"))
        super().__init__(pixmap)
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint)
        self._progress = 0.0
        self._message = "正在初始化..."
        self._icon_pixmap = None
        try:
            if hasattr(sys, '_MEIPASS'):
                base = sys._MEIPASS
            else:
                base = os.path.dirname(os.path.abspath(__file__))
            for name in ('ico.png', 'icon.png', 'icon.ico'):
                p = os.path.join(base, name)
                if os.path.exists(p):
                    self._icon_pixmap = QPixmap(p)
                    if not self._icon_pixmap.isNull():
                        break
                    self._icon_pixmap = None
        except Exception:
            pass

    def _get_progress(self):
        return self._progress

    def _set_progress(self, val):
        self._progress = val
        self.repaint()

    progress = pyqtProperty(float, _get_progress, _set_progress)

    def set_progress(self, value, message=""):
        if message:
            self._message = message
        old_progress = self._progress
        self._progress = value
        anim = QPropertyAnimation(self, b"progress")
        anim.setDuration(300)
        anim.setStartValue(old_progress)
        anim.setEndValue(value)
        anim.valueChanged.connect(self.repaint)
        anim.start()
        self._anim = anim

    def drawContents(self, painter):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor("#0D0D0D"))

        if self._icon_pixmap:
            icon_size = 80
            scaled = self._icon_pixmap.scaled(icon_size, icon_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            ix = (w - scaled.width()) // 2
            painter.drawPixmap(ix, 50, scaled)

        painter.setPen(QColor("#F0F0F0"))
        title_font = QFont("Microsoft YaHei", 22, QFont.Weight.Bold)
        painter.setFont(title_font)
        title = APP_NAME
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(title)
        painter.drawText((w - tw) // 2, 175, title)

        painter.setPen(QColor("#888888"))
        sub_font = QFont("Microsoft YaHei", 11)
        painter.setFont(sub_font)
        sub = "LTX-2.3 Cinematic Workstation"
        fm_sub = painter.fontMetrics()
        sw = fm_sub.horizontalAdvance(sub)
        painter.drawText((w - sw) // 2, 200, sub)

        bar_x, bar_y, bar_w, bar_h = 60, 240, w - 120, 10
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#222222"))
        painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 5, 5)

        fill_w = bar_w * min(self._progress, 1.0)
        if fill_w > 0:
            grad = QLinearGradient(bar_x, bar_y, bar_x + fill_w, bar_y)
            grad.setColorAt(0, QColor("#FF0000"))
            grad.setColorAt(1, QColor("#FF7043"))
            painter.setBrush(grad)
            painter.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 5, 5)

        painter.setPen(QColor("#888888"))
        msg_font = QFont("Microsoft YaHei", 10)
        painter.setFont(msg_font)
        fm2 = painter.fontMetrics()
        mw = fm2.horizontalAdvance(self._message)
        painter.drawText((w - mw) // 2, 275, self._message)

        pct = f"{int(min(self._progress, 1.0) * 100)}%"
        painter.setPen(QColor("#FF7043"))
        pct_font = QFont("Microsoft YaHei", 9)
        painter.setFont(pct_font)
        fm3 = painter.fontMetrics()
        pw = fm3.horizontalAdvance(pct)
        painter.drawText((w - pw) // 2, 300, pct)


class UsageGuideDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("使用说明 - 云集智能视频创意站")
        self.setMinimumSize(800, 600)
        self.resize(960, 720)
        self.setStyleSheet("""
            QDialog { background-color: #0D0D0D; }
            QTextBrowser {
                background-color: #111118; color: #E0E0F0; border: none;
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 13px; padding: 24px 32px;
            }
            QTextBrowser:hover { border: none; }
            QPushButton {
                background-color: #252525; color: #CCCCCC; border: 1px solid #444444;
                border-radius: 6px; padding: 8px 28px; font-size: 13px;
            }
            QPushButton:hover { background-color: #333333; color: #FFFFFF; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(self._build_html())
        layout.addWidget(browser)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16, 10, 16, 12)
        btn_row.addStretch()
        close_btn = QPushButton("✖ 关闭")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    @staticmethod
    def _build_html() -> str:
        BG = "#111118"
        SURFACE = "#1a1d27"
        SURFACE2 = "#242836"
        BORDER = "#2e3347"
        TEXT = "#e4e6ef"
        TEXT2 = "#9498ab"
        ACCENT = "#6c7bf0"
        ACCENT2 = "#8b5cf6"
        GREEN = "#34d399"
        YELLOW = "#fbbf24"
        RED = "#f87171"
        BLUE = "#60a5fa"
        def h2(t):
            return f'<h2 style="font-size:18px;font-weight:600;color:{TEXT};border-bottom:1px solid {BORDER};padding-bottom:6px;margin-top:28px;margin-bottom:12px;">{t}</h2>'
        def h3(t):
            return f'<h3 style="font-size:15px;font-weight:600;color:{TEXT};margin-top:20px;margin-bottom:8px;">{t}</h3>'
        def h4(t):
            return f'<h4 style="font-size:13px;font-weight:600;color:{TEXT2};margin-top:14px;margin-bottom:6px;">{t}</h4>'
        def p(t):
            return f'<p style="margin:6px 0;font-size:13px;color:{TEXT};">{t}</p>'
        def note(t):
            return f'<div style="background:rgba(108,123,240,0.1);border-left:3px solid {ACCENT};padding:10px 14px;margin:10px 0;font-size:12px;color:{TEXT2};">{t}</div>'
        def warn(t):
            return f'<div style="background:rgba(251,191,36,0.08);border-left:3px solid {YELLOW};padding:10px 14px;margin:10px 0;font-size:12px;color:{TEXT2};">{t}</div>'
        def badge(text, color):
            return f'<span style="background:rgba({_badge_rgb(color)},0.15);color:{color};padding:1px 6px;border-radius:3px;font-size:11px;font-weight:600;">{text}</span>'
        def _badge_rgb(c):
            m = {"#34d399":"52,211,153","#fbbf24":"251,191,36","#f87171":"248,113,113","#60a5fa":"96,165,250","#8b5cf6":"139,92,246","#6c7bf0":"108,123,240"}
            return m.get(c,"128,128,128")
        def tier(text, color):
            return f'<span style="background:rgba({_badge_rgb(color)},0.18);color:{color};padding:2px 8px;border-radius:3px;font-size:11px;font-weight:700;">{text}</span>'
        def table(headers, rows):
            hd = "".join(f'<th style="background:{SURFACE2};color:{TEXT2};font-size:11px;font-weight:600;padding:8px 10px;text-align:left;border-bottom:1px solid {BORDER};">{h}</th>' for h in headers)
            body = ""
            for row in rows:
                cells = "".join(f'<td style="padding:8px 10px;border-bottom:1px solid {BORDER};font-size:12px;color:{TEXT};">{c}</td>' for c in row)
                body += f'<tr>{cells}</tr>'
            return f'<table style="width:100%;border-collapse:collapse;margin:10px 0;"><thead><tr>{hd}</tr></thead><tbody>{body}</tbody></table>'
        def ul(items):
            lis = "".join(f'<li style="margin:3px 0;font-size:12px;color:{TEXT};">{i}</li>' for i in items)
            return f'<ul style="margin:6px 0;padding-left:18px;">{lis}</ul>'
        def ol(items):
            lis = "".join(f'<li style="margin:3px 0;font-size:12px;color:{TEXT};">{i}</li>' for i in items)
            return f'<ol style="margin:6px 0;padding-left:18px;">{lis}</ol>'
        def card(title, desc):
            return f'<div style="background:{SURFACE};border:1px solid {BORDER};border-radius:8px;padding:14px;margin:6px 0;"><div style="font-size:13px;font-weight:600;color:{TEXT};margin-bottom:4px;">{title}</div><div style="font-size:12px;color:{TEXT2};">{desc}</div></div>'
        def faq(q, a_html):
            return f'<div style="background:{SURFACE};border:1px solid {BORDER};border-radius:6px;margin:6px 0;padding:12px 14px;"><div style="font-weight:600;font-size:13px;color:{TEXT};margin-bottom:6px;">Q: {q}</div><div style="font-size:12px;color:{TEXT2};">{a_html}</div></div>'
        html = f"""
        <div style="max-width:880px;margin:0 auto;">
        <h1 style="font-size:24px;font-weight:700;color:{ACCENT};margin-bottom:4px;">使用说明</h1>
        <p style="color:{TEXT2};font-size:13px;margin-bottom:24px;">云集智能视频创意站 — AI 视频创作一站式工具</p>

        {h2("一、软件简介")}
        {p("<strong>云集智能视频创意站</strong>是一款基于 LTX-2.3 视频生成引擎的 AI 创意工具，由武汉市云集智能科技有限公司开发。集成了文生视频、图生视频、智能多帧拼接、视频迁移、图像生成、TTS 语音合成等核心功能。")}
        {ul([
            "<strong>核心引擎</strong>：LTX-2.3 22B Distilled（支持 FP8 量化与 BF16 精度，8GB 显存即可启动）",
            "<strong>技术架构</strong>：PyQt6 桌面客户端 + FastAPI 后端 + Web 前端界面",
            "<strong>运行环境</strong>：Windows 10/11，NVIDIA GPU（最低 8GB 显存，推荐 24GB）",
        ])}

        {h2("二、系统要求")}
        {h3("基础环境")}
        {table(
            ["项目", "最低要求", "推荐配置"],
            [
                ["操作系统", "Windows 10 64位", "Windows 11"],
                ["GPU", "NVIDIA 8GB 显存", "NVIDIA 24GB 显存"],
                ["系统内存", "32 GB", "64 GB"],
                ["磁盘", "50 GB 可用空间", "100 GB SSD"],
                ["GPU 驱动", "≥ 560.70", "最新版本"],
                ["CUDA", "12.8（随 PyTorch 安装）", "12.8"],
                ["Python", "3.12", "3.12"],
                ["网络", "需要联网下载模型", "宽带"],
            ]
        )}
        {note("<strong>注意</strong>：系统内存 32GB 是使用 CPU offload（低显存模式）的必要条件，因为模型权重需要暂存到内存中。低于 32GB 可能导致内存不足崩溃。")}

        {h3("GPU 硬件分级")}
        {p("软件根据 GPU 显存大小自动匹配最优推理参数，分为 5 个等级：")}
        {table(
            ["等级", "显存范围", "代表显卡", "推荐分辨率", "速度参考"],
            [
                [tier("极致性能", ACCENT2), "32GB+", "RTX 5090 / A6000 / A100", "1080p", "≈10-20秒/25帧"],
                [tier("高性能", GREEN), "20-31GB", "RTX 3090 / 4090 / A5000", "720p-1080p", "≈30-60秒/25帧"],
                [tier("均衡模式", BLUE), "14-20GB", "RTX 4080 / 3080 20GB", "720p", "≈30-60秒/25帧"],
                [tier("节能模式", YELLOW), "10-14GB", "RTX 4070 Ti / 3080 12GB", "540p", "≈40-80秒/25帧"],
                [tier("极限模式", RED), "&lt;10GB", "RTX 4060 / 3060 / GTX 1080 Ti", "480p", "≈60-120秒/25帧"],
            ]
        )}

        {h3("GPU 架构与加速特性")}
        {table(
            ["GPU 架构", "代表型号", "BF16 加速", "原生 FP8 加速", "SageAttention"],
            [
                ["<strong>Blackwell</strong>", "RTX 5090 / 5080 / 5070", badge("✅ 硬件加速", GREEN), badge("✅ 硬件加速", GREEN), badge("✅ 支持", GREEN)],
                ["<strong>Ada Lovelace</strong>", "RTX 4090 / 4080 / 4070", badge("✅ 硬件加速", GREEN), badge("✅ 硬件加速", GREEN), badge("✅ 支持", GREEN)],
                ["<strong>Ampere</strong>", "RTX 3090 / 3080 / A5000", badge("✅ 硬件加速", GREEN), badge("❌ 软件模拟", YELLOW), badge("❌ 不支持", RED)],
                ["<strong>Turing</strong>", "RTX 2080 Ti / 2080", badge("❌ 软件模拟", YELLOW), badge("❌ 软件模拟", YELLOW), badge("❌ 不支持", RED)],
                ["<strong>Pascal</strong>", "GTX 1080 Ti / 1080", badge("❌ 不支持", RED), badge("❌ 不支持", RED), badge("❌ 不支持", RED)],
            ]
        )}
        {note('BF16 和 FP8 的\u201c软件模拟\u201d指 PyTorch 会自动将低精度运算转换为 FP32/FP16 执行，功能正常但无硬件加速收益。Pascal 架构不支持 BF16，会回退到 FP32 计算。')}

        {h3("模型选择建议")}
        {card(f'BF16 蒸馏模型 {badge("~44GB", BLUE)}', "原始精度，无损画质。适用于 24GB+ 显存，需配合 Layer Streaming 分段加载。")}
        {card(f'FP8 蒸馏模型 {badge("~22GB", GREEN)}', "权重量化，轻微损失。显存占用减半，8GB+ 显存即可运行，推荐大多数用户。")}
        {note("<strong>Layer Streaming（分段加载）</strong>：当模型权重无法全部放入显存时，软件自动启用分段加载策略——将 Transformer 层保存在 CPU 内存中，按需异步传输到 GPU 计算，计算完毕立即释放。这使得 44GB 的 BF16 模型也能在 24GB 显卡上运行，代价是推理速度会因 CPU↔GPU 数据搬运而降低。")}

        {h2("三、首次启动与部署")}
        {p("首次启动时，软件将自动完成以下部署步骤：")}
        {ol([
            "<strong>下载 UV 包管理器</strong>（国内镜像加速）",
            "<strong>安装 Python 3.12</strong> 并创建虚拟环境（<code>data/.venv/</code>）",
            "<strong>安装 PyTorch + CUDA 12.8</strong> 及项目依赖",
            "<strong>部署补丁文件和前端界面</strong>",
            "<strong>下载 LTX Desktop 后端代码</strong>",
            "<strong>下载 AI 模型</strong>（HF-Mirror 国内镜像加速）",
        ])}
        {note("整个部署过程约需 15-30 分钟（取决于网速），请耐心等待。")}

        {h3("目录结构")}
        <div style="background:{SURFACE};border:1px solid {BORDER};border-radius:8px;padding:14px;margin:8px 0;font-family:Consolas,monospace;font-size:12px;line-height:1.8;color:{TEXT2};">
        app/ &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;← 应用程序（只读）<br>
        app/resources/ &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;← 后端、补丁、前端等资源<br>
        data/ &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;← 用户数据（可写，需备份）<br>
        data/.venv/ &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;← Python 虚拟环境<br>
        data/outputs/ &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;← 生成的视频/图像/音频<br>
        data/uploads/ &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;← 上传的参考图片<br>
        data/models/ &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;← AI 模型文件<br>
        data/settings.json &nbsp;← 用户设置<br>
        temp/ &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;← 临时文件（可删除）<br>
        temp/logs/ &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;← 日志文件
        </div>

        {h2("四、界面概览")}
        {p("软件采用<strong>左侧控制面板 + 右侧预览区</strong>的经典布局。")}
        {h3("左侧面板")}
        {table(
            ["区域", "功能"],
            [
                ["顶栏", "软件名称、版本号、环境检测、系统设置按钮"],
                ["GPU 状态栏", "显卡状态、显存占用进度条"],
                ["系统设置", "GPU 选择、显存上限设置、硬件配置"],
                ["视频模型", "选择模型 Checkpoint"],
                ["模型仓库", "查看已安装/可下载的模型"],
                ["LoRA 路径", "设置 LoRA 文件夹路径"],
                ["开始渲染", "启动生成任务"],
                ["任务队列", "查看当前排队中的任务"],
                ["模式标签页", "视频生成 / 智能多帧 / 视频迁移 / 图像生成 / TTS 语音 / 高清放大"],
                ["参数面板", "根据当前模式显示对应参数"],
                ["底部导航", "软件更新、模型管理"],
            ]
        )}
        {h3("右侧预览区")}
        {ul([
            "<strong>视频/图像预览</strong>：实时显示生成结果",
            "<strong>载入种子/参数</strong>：一键复用历史生成参数",
            "<strong>下载按钮</strong>：保存当前预览内容",
        ])}

        {h2("五、功能模块")}
        {h3("5.1 标准生成")}
        {p("核心功能，支持文生视频和图生视频。不上传图片即为文生视频，上传首帧图片即为图生视频。")}
        {h4("基础画面设置")}
        {table(
            ["参数", "说明", "可选值"],
            [
                ["清晰度级别", "输出分辨率档位", "1080P / 720P / 540P"],
                ["画幅比例", "视频宽高比", "16:9、9:16、1:1、4:3、3:4、21:9、9:21、自定义"],
                ["帧率 (FPS)", "每秒帧数", "24 / 25 / 30 / 48 / 60"],
                ["时长", "视频秒数", "1-30 秒"],
                ["运动速度", "动态强度/运动快慢", "0.25x-3.0x，默认1.0x"],
                ["镜头运动", "摄像机运动方式", "静止、推近、拉远、向左、向右、升臂、降臂、焦点偏移"],
            ]
        )}
        {h4("生成媒介")}
        {table(
            ["输入", "效果"],
            [
                ["仅文字描述", "文生视频（Text-to-Video）"],
                ["上传首帧图片", "图生视频（Image-to-Video）"],
                ["上传首帧 + 尾帧", "首尾插帧（Frame Interpolation）"],
                ["上传首帧 + 参考音频", "音频驱动视频（Audio-to-Video）"],
            ]
        )}
        {h4("LoRA 增强")}
        {ul([
            "点击 <strong>+</strong> 按钮添加多个 LoRA",
            "每个 LoRA 可独立设置权重（0-2）",
            "LoRA 文件需放置在模型目录下的 <code>loras</code> 文件夹中",
        ])}
        {h4("AI 环境音")}
        {p("勾选「生成 AI 环境音」后，视频生成完毕将自动添加匹配场景的 AI 音效。")}

        {h3("5.2 智能多帧")}
        {p("将多张图片智能编排为连续视频，支持两种工作流：")}
        {card("单次多关键帧", "所有图片作为一条视频的关键帧锚点，一次生成完整视频。")}
        {card("分段拼接", "每两张图片生成一段视频，再通过 ffmpeg 拼接为完整成片。")}
        {p("操作步骤：上传图片 → 拖拽排序 → 选择模式 → 可选上传 BGM → 开始渲染")}
        {note("分段拼接模式需要系统安装 ffmpeg。可通过环境变量 <code>LTX_FFMPEG_PATH</code> 指定路径。")}

        {h3("5.3 视频迁移")}
        {p("将参考视频中的动作/运镜/风格迁移到新主体上。")}
        {table(
            ["迁移类型", "说明"],
            [
                ["动作迁移", "提取参考视频中的姿态/轮廓/深度，驱动目标图片"],
                ["运镜迁移", "复制参考视频的镜头运动轨迹"],
                ["视频重绘", "保持原视频结构，重新生成画面风格"],
            ]
        )}
        {h4("控制类型（动作迁移）")}
        {table(
            ["类型", "说明"],
            [
                ["Pose 姿态", "提取人体骨骼姿态"],
                ["Canny 轮廓", "提取边缘轮廓"],
                ["Depth 深度", "提取深度图"],
            ]
        )}
        {p("控制强度范围 0-2，默认 1。值越大参考视频控制力越强，值越小 AI 创作自由度越高。")}

        {h3("5.4 图像生成")}
        {p("基于 LTX-2.3 引擎生成静态图像。")}
        {table(
            ["参数", "说明"],
            [
                ["预设分辨率", "1:1 (1024×1024)、16:9 (1280×720)、9:16 (720×1280)、自定义"],
                ["采样步数", "1-50，默认 28。步数越多细节越丰富，但耗时更长"],
            ]
        )}

        {h3("5.5 TTS 语音")}
        {p("TTS 语音功能包含四个子模式：文字转语音、语音转文字、声音克隆、终极克隆，通过子标签页切换，参数共享。")}
        {card("文字转语音", "输入文本，AI 自动设计声音风格。")}
        {card("语音转文字", "上传音频文件，自动识别为文字，支持中英文。识别结果可一键复制。")}
        {card("声音克隆", "上传参考音频，模仿其音色合成新文本。点击「识别为文字」可自动将参考音频转为文本。")}
        {card("终极克隆", "上传参考音频 + 对应文本转录，最高还原度。点击「识别为文字」可自动填充文本转录。")}
        {p('在文本开头加英文括号描述声音特征，例如：<code>(年轻女声，温柔甜美)</code> 你好，欢迎来到...')}

        {h3("5.6 高清放大")}
        {p("高清放大功能支持对图片和视频进行高分辨率增强，通过子标签页切换视频放大和图片放大。")}
        {table(
            ["参数", "说明", "可选值"],
            [
                ["放大引擎", "选择放大算法", "Real-ESRGAN（高保真）/ LTX 快速放大"],
                ["放大倍数", "输出分辨率相对输入的倍数", "2x / 4x"],
                ["放大模型", "Real-ESRGAN 模型变体", "通用 x4plus / 动漫 x4plus-anime / 通用 x2plus"],
                ["降噪强度", "控制降噪程度", "0-1，0=保留原始细节，1=强力降噪"],
            ]
        )}
        {card("Real-ESRGAN（高保真）", "基于深度学习的超分辨率算法，支持通用和动漫模型，适合对画质要求高的场景。需要安装 realesrgan 和 basicsr 依赖。")}
        {card("LTX 快速放大", "基于 LTX 内置空间上采样器的快速放大，速度极快但非保真增强，适合快速预览。")}

        {h2("六、系统设置")}
        {h3("6.1 环境检测")}
        {p("点击顶栏「环境检测」按钮，自动检测：Python 环境、CUDA/cuDNN 版本、GPU 型号与驱动、模型文件完整性、ffmpeg 可用性、推荐预设等级。")}
        {h3("6.2 系统高级设置")}
        {table(
            ["设置项", "说明"],
            [
                ["工作设备选择", "多 GPU 时选择使用哪块显卡"],
                ["显存上限", "限制可用显存（GB），0 表示不限制"],
            ]
        )}
        {h3("6.3 显存管理")}
        {ul([
            "顶栏实时显示 GPU 显存占用进度条",
            "点击「释放显存」可手动清理 GPU 缓存",
        ])}

        {h2("七、模型管理")}
        {p("点击底部「模型管理」按钮打开模型管理弹窗。")}
        {ul([
            "<strong>系统模型目录</strong> — 软件默认的模型存放路径，文件不可删除",
            "<strong>自定义目录</strong> — 用户添加的额外模型目录，文件可删除",
            "支持的格式：<code>.safetensors</code>、<code>.ckpt</code>、<code>.pt</code>、<code>.bin</code>、<code>.pth</code>",
            "LoRA 文件放到模型目录下的 <code>loras</code> 子文件夹中",
        ])}

        {h2("八、任务队列与种子")}
        {ul([
            "点击「开始渲染」后，任务自动进入队列",
            "<strong>随机模式</strong>：每次生成使用随机种子，结果不可复现",
            "<strong>固定模式</strong>：使用指定种子，相同参数可复现相同结果",
            "预览区可点击「载入种子」一键复用历史参数",
        ])}

        {h2("九、常见问题")}
        {faq("首次启动部署失败？", ul([
            "检查网络连接，确保能访问国内镜像源",
            "关闭杀毒软件/防火墙后重试",
            "查看 <code>temp/logs/</code> 目录下的日志文件",
        ]))}
        {faq("生成时显存不足？", ul([
            "在系统设置中设置显存上限（建议设为实际显存的 90%）",
            "使用 FP8 量化模型（仅需约 22GB 显存，8GB 显卡即可运行）",
            "降低清晰度级别（540P 代替 1080P）",
            "缩短视频时长（减少总帧数）",
            "点击「释放显存」清理缓存",
            "确保系统内存 ≥ 32GB",
        ]))}
        {faq("BF16 模型和 FP8 模型怎么选？", ul([
            "<strong>24GB+ 显存</strong>：推荐 BF16 模型，画质最佳，软件自动启用 Layer Streaming",
            "<strong>8-24GB 显存</strong>：推荐 FP8 模型，显存占用减半，稳定性更好",
            "两种模型的<strong>计算精度相同</strong>（均为 BF16），区别仅在于权重存储精度",
        ]))}
        {faq("推理速度很慢怎么办？", ul([
            "<strong>使用 FP8 模型</strong>：显存占用减半，Layer Streaming 搬运量减半，速度显著提升",
            "<strong>降低分辨率</strong>：540P 比 1080P 快 3-5 倍",
            "<strong>减少帧数</strong>：帧数是影响推理时间的最直接因素",
            "<strong>确保系统内存充足</strong>：64GB 内存时 offload 性能开销较小",
            "<strong>升级显卡</strong>：RTX 40/50 系列支持原生 FP8 硬件加速",
        ]))}
        {faq("视频无法播放？", ul([
            "确保后端服务正在运行（核心引擎状态为绿色）",
            "刷新浏览器页面",
            "检查 <code>temp/logs/</code> 目录下的错误日志",
        ]))}
        {faq("模型列表为空？", ul([
            "点击模型仓库旁的「刷新」按钮",
            "确认模型文件已正确放置在 <code>data/models/</code> 目录下",
        ]))}
        {faq("分段拼接失败？", ul([
            "确认系统已安装 ffmpeg",
            "可通过环境变量 <code>LTX_FFMPEG_PATH</code> 指定 ffmpeg 路径",
        ]))}
        {faq("软件自动退出？", ul([
            "查看 <code>temp/logs/crash.log</code> 和 <code>temp/logs/qt_exception.log</code>",
            "如果是 0xC0000005 错误，软件会自动重试（最多 2 次）",
            "确保显卡驱动为最新版本",
            "尝试降低显存使用量",
        ]))}

        {h2("十、快捷操作")}
        {table(
            ["操作", "说明"],
            [
                ["Esc", "最小化窗口到系统托盘"],
                ["系统托盘图标", "双击恢复窗口，右键显示菜单"],
                ["拖拽上传", "支持拖拽图片/视频/音频到上传区域"],
                ["缩略图排序", "在智能多帧模式中拖拽缩略图调整顺序"],
            ]
        )}

        <div style="margin-top:40px;padding-top:16px;border-top:1px solid {BORDER};font-size:11px;color:{TEXT2};text-align:center;">
        <p><strong>云集智能视频创意站</strong></p>
        <p>著作权人：一释寻（熊艺杰）&nbsp;|&nbsp;著作权归属：武汉市云集智能科技有限公司</p>
        <p>本软件基于 LTX-2 视频生成引擎构建，部分功能依赖开源组件。未经著作权人书面授权，任何单位或个人不得以任何形式复制、修改、传播、出租本软件。</p>
        </div>
        </div>
        """
        return html


class MainWindow(QMainWindow):
    log_signal = pyqtSignal(str, str)
    enable_buttons_signal = pyqtSignal()
    _version_data_ready = pyqtSignal()

    def __init__(self, splash=None):
        super().__init__()
        self._splash = splash
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        self.setStyleSheet(GLOBAL_STYLE)

        try:
            if hasattr(sys, '_MEIPASS'):
                icon_path = os.path.join(sys._MEIPASS, 'ico.png')
            else:
                icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ico.png')
            if not os.path.exists(icon_path):
                if hasattr(sys, '_MEIPASS'):
                    icon_path = os.path.join(sys._MEIPASS, 'icon.ico')
                else:
                    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except:
            pass

        self._ltx_install_dir = None
        self._python_exe = None
        self._pythonw_exe = None
        self._data_dir = None
        self._exe_data_dir = None
        self._exe_temp_dir = None
        self._backend_dir = None
        self._patches_dir = None
        self._models_dir = None
        self._ui_dir = None
        self._project_root = None
        self._git_root = None

        self.service_processes: Dict[str, ServiceProcess] = {}
        self.service_cards: Dict[str, ServiceCard] = {}
        self.is_starting = False
        self.auto_scroll = True
        self._debug_mode = False
        self._debug_log_file = None
        self._fe_debug_log_path = ""
        self._fe_debug_read_pos = 0
        self._fe_debug_timer = QTimer(self)
        self._fe_debug_timer.timeout.connect(self._poll_fe_debug_log)
        self.browsers = {"系统默认": "system"}
        self.selected_browser = "system"
        self.custom_browser_path = ""

        # 新手引导状态
        self._guide_step = 0  # 0=未开始, 1=部署, 2=模型, 3=服务, 4=完成
        self._guide_auto = True  # 全自动模式
        self._guide_active = False  # 引导是否激活
        self._guide_banner = None  # 引导横幅widget
        self._guide_step_labels = []  # 步骤指示器标签
        self._guide_desc_label = None  # 步骤描述标签
        self._guide_auto_switch = None  # 全自动开关
        self._guide_next_btn = None  # 下一步按钮
        self._guide_retry_btn = None  # 重试按钮
        self._guide_skip_btn = None  # 跳过按钮（仅步骤2）
        self._guide_browser_check_timer = None  # 浏览器检测定时器
        self._guide_browser_check_count = 0  # 浏览器检测计数
        self._guide_skip_models = False  # 部署时是否跳过模型
        self._guide_bg_models_started = False  # 步骤1期间是否已后台启动模型下载
        self._guide_bg_poll_timer = None  # 步骤1轮询huggingface_hub是否可用
        self._guide_deploy_sub_hint = ""  # 部署子步骤提示
        self._guide_model_sub_hint = ""  # 模型下载子步骤提示

        self._resolve_base_dir()
        self.config = ConfigManager(self._get_app_dir(), data_dir=self._exe_data_dir)

        self._backend_port = self.config.get("ports.backend", DEFAULT_BACKEND_PORT)
        self._frontend_port = self.config.get("ports.frontend", DEFAULT_FRONTEND_PORT)
        SERVICES["backend"]["port"] = self._backend_port
        SERVICES["backend"]["url"] = f"http://127.0.0.1:{self._backend_port}"
        SERVICES["frontend"]["port"] = self._frontend_port
        SERVICES["frontend"]["url"] = f"http://127.0.0.1:{self._frontend_port}"

        self.monitor = ServiceMonitor()
        self.monitor.status_changed.connect(self._on_status_changed)

        self.log_signal.connect(self._append_log)
        self.enable_buttons_signal.connect(self._enable_buttons)
        self._version_data_ready.connect(self._on_version_data_ready)

        self._setup_ui()

        QTimer.singleShot(500, self._setup_tray)
        QTimer.singleShot(0, self._deferred_init)

    def _resolve_base_dir(self):
        if hasattr(sys, 'frozen'):
            dev_dir = _find_dev_dir()
            self._app_dir = os.path.join(dev_dir, APP_DIR) if os.path.isdir(os.path.join(dev_dir, APP_DIR)) else os.path.join(dev_dir, "app")
            self._project_root = dev_dir
            self._repo_root = os.path.dirname(dev_dir)
            self._exe_data_dir = os.path.join(dev_dir, "data")
            self._exe_temp_dir = os.path.join(dev_dir, "temp")

            # 确保桌面有唯一的快捷方式（延迟执行，不阻塞启动）
            entry_exe = os.path.join(dev_dir, f"{BRAND_NAME}.exe")
            if os.path.isfile(entry_exe):
                QTimer.singleShot(5000, lambda: _create_desktop_shortcut(entry_exe))
        else:
            self._app_dir = os.path.dirname(os.path.abspath(__file__))
            self._project_root = os.path.dirname(self._app_dir)
            self._repo_root = os.path.dirname(self._project_root)
            self._app_resources = os.path.join(self._app_dir, "resources")
            self._exe_data_dir = os.path.join(self._project_root, "data")
            dev_temp_dir = os.path.join(self._project_root, "temp")
            if os.path.isdir(dev_temp_dir):
                self._exe_temp_dir = dev_temp_dir
            else:
                self._exe_temp_dir = None

        for candidate in [self._project_root, os.path.dirname(self._project_root)]:
            if os.path.exists(os.path.join(candidate, "项目参考")):
                self._project_root = candidate
                break

        current = self._project_root
        while current:
            if os.path.isdir(os.path.join(current, ".git")):
                self._git_root = current
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

        if not hasattr(self, '_app_resources') or not self._app_resources:
            self._app_resources = os.path.join(self._app_dir, "resources")

        if not self._data_dir:
            self._data_dir = self._exe_data_dir or os.path.join(self._project_root, "data")

        # EXE模式：检测并同步版本资源
        self._sync_resources_version()

    def _sync_resources_version(self):
        """EXE启动时检测 app/resources/ 版本，不匹配则从 _MEIPASS 提取覆盖。
        
        机制：
        - app/resources/.version 记录当前资源版本
        - app/resources/.updating 存在说明上次更新中断，强制重新提取
        - 幂等操作：重复提取无副作用
        - 只覆盖 ui/backend/patches，保留 venv/python/uv 等共享目录
        """
        if not getattr(sys, 'frozen', False):
            return
        if not hasattr(sys, '_MEIPASS') or not sys._MEIPASS:
            return

        resources_dir = self._app_resources
        if not resources_dir:
            return

        version_file = os.path.join(resources_dir, ".version")
        updating_file = os.path.join(resources_dir, ".updating")

        # 读取当前资源版本
        current_res_version = ""
        try:
            with open(version_file, "r", encoding="utf-8") as f:
                current_res_version = f.read().strip()
        except Exception:
            pass

        # 版本匹配且无中断标记，无需同步
        if current_res_version == VERSION and not os.path.exists(updating_file):
            return

        # 需要同步：从 _MEIPASS 提取 ui/backend/patches
        meipass = sys._MEIPASS
        os.makedirs(resources_dir, exist_ok=True)

        # 写入更新中标记
        try:
            with open(updating_file, "w", encoding="utf-8") as f:
                f.write(VERSION)
        except Exception:
            pass

        _IGNORE_PATTERNS = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")

        for res_name in ("ui", "backend", "patches"):
            src = os.path.join(meipass, "resources", res_name)
            dst = os.path.join(resources_dir, res_name)
            if not os.path.isdir(src):
                continue
            try:
                if os.path.exists(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst, ignore=_IGNORE_PATTERNS)
            except Exception:
                # 提取失败，.updating 保留，下次启动重试
                return

        # 提取成功，写入版本标记
        try:
            with open(version_file, "w", encoding="utf-8") as f:
                f.write(VERSION)
        except Exception:
            pass

        # 删除更新中标记
        try:
            os.remove(updating_file)
        except Exception:
            pass

    def _get_app_dir(self):
        return self._app_dir

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        nav_bar = QFrame()
        nav_bar.setStyleSheet("QFrame { background-color: #1A1A1A; border: none; border-bottom: 2px solid #333333; border-radius: 0; }")
        nav_layout = QHBoxLayout(nav_bar)
        nav_layout.setSpacing(10)
        nav_layout.setContentsMargins(15, 10, 15, 10)

        menu_style = """
            QPushButton {
                background-color: #252525; color: #FFFFFF;
                border: 1px solid #333333; border-radius: 4px;
                padding: 10px 18px; font-size: 13px;
            }
            QPushButton:hover { background-color: #333333; border-color: #444444; }
            QPushButton:checked { background-color: #CC0000; border-color: #CC0000; color: #FFFFFF; }
            QPushButton:checked:hover { background-color: #FF0000; border-color: #FF0000; }
            QPushButton:checked:pressed { background-color: #DD0000; border-color: #DD0000; }
        """

        self.btn_home = QPushButton("🚀 运行服务")
        self.btn_home.setCheckable(True)
        self.btn_home.setChecked(True)
        self.btn_home.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_home.setStyleSheet(menu_style)
        self.btn_home.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_home.clicked.connect(lambda: self._switch_page(0))
        nav_layout.addWidget(self.btn_home)

        self.btn_deploy = QPushButton("⚙ 部署维护")
        self.btn_deploy.setCheckable(True)
        self.btn_deploy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_deploy.setStyleSheet(menu_style)
        self.btn_deploy.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_deploy.clicked.connect(lambda: self._switch_page(1))
        nav_layout.addWidget(self.btn_deploy)

        self.btn_models = QPushButton("📦 模型管理")
        self.btn_models.setCheckable(True)
        self.btn_models.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_models.setStyleSheet(menu_style)
        self.btn_models.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_models.clicked.connect(lambda: self._switch_page(2))
        nav_layout.addWidget(self.btn_models)

        self.btn_update = QPushButton("🔄 软件更新")
        self.btn_update.setCheckable(True)
        self.btn_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_update.setStyleSheet(menu_style)
        self.btn_update.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_update.clicked.connect(lambda: self._switch_page(3))
        nav_layout.addWidget(self.btn_update)

        main_layout.addWidget(nav_bar)

        self.page_stack = QStackedWidget()
        main_layout.addWidget(self.page_stack, 1)

        self._build_service_page()
        print("[DEBUG] Service page built")
        self._build_env_page()
        print("[DEBUG] Env page built")
        self._build_models_page()
        print("[DEBUG] Models page built")
        self.page_stack.addWidget(self._build_update_page())
        print("[DEBUG] Update page built")

        self.resize(1100, 800)

    def _build_service_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        browser_panel = QFrame()
        browser_panel.setStyleSheet("""
            QFrame {
                background-color: #1A1A1A;
                border: 1px solid #333333;
                border-radius: 8px;
                padding: 6px;
            }
        """)
        browser_layout = QHBoxLayout(browser_panel)
        browser_layout.setSpacing(12)
        browser_layout.setContentsMargins(12, 8, 12, 8)

        browser_label = QLabel("🌐 浏览器:")
        browser_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #FFFFFF; background: transparent;")
        browser_layout.addWidget(browser_label)

        self.browser_combo = QComboBox()
        self.browser_combo.setStyleSheet("""
            QComboBox {
                background-color: #252525; color: #FFFFFF;
                border: 1px solid #333333; border-radius: 4px;
                padding: 6px 30px 6px 10px; font-size: 12px; min-width: 160px;
            }
            QComboBox:hover { border-color: #444444; }
            QComboBox:focus { border-color: #1976D2; }
            QComboBox::drop-down { border: none; width: 25px; }
            QComboBox::down-arrow {
                image: none; border-left: 5px solid transparent;
                border-right: 5px solid transparent; border-top: 5px solid #888888;
            }
            QComboBox QAbstractItemView {
                background-color: #252525; border: 1px solid #333333;
                selection-background-color: #1976D2; selection-color: #FFFFFF;
            }
        """)
        self.browser_combo.addItem("系统默认", "system")
        self.browser_combo.addItem("◇ 自定义浏览器...", "custom")
        self.browser_combo.currentIndexChanged.connect(self._on_browser_changed)
        browser_layout.addWidget(self.browser_combo)

        self.browser_path_edit = QLineEdit()
        self.browser_path_edit.setPlaceholderText("粘贴或输入浏览器路径...")
        self.browser_path_edit.setStyleSheet("""
            QLineEdit {
                background-color: #121212; color: #F0F0F0;
                border: 1px solid #333333; border-radius: 6px;
                padding: 6px 10px; font-size: 12px;
            }
            QLineEdit:hover, QLineEdit:focus { border-color: #1976D2; }
        """)
        self.browser_path_edit.textChanged.connect(self._on_custom_browser_path_changed)
        self.browser_path_edit.setVisible(False)
        browser_layout.addWidget(self.browser_path_edit, 1)

        self.btn_select_browser = QPushButton("📂 选择")
        self.btn_select_browser.setStyleSheet("""
            QPushButton {
                background-color: #1565C0; color: #E0E0E0;
                border: 1px solid #1976D2; border-radius: 6px;
                padding: 6px 14px; font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #1976D2; }
        """)
        self.btn_select_browser.clicked.connect(self._select_custom_browser)
        self.btn_select_browser.setVisible(False)
        browser_layout.addWidget(self.btn_select_browser)

        browser_layout.addSpacing(20)

        self.auto_open_checkbox = ToggleSwitch("启动后打开", checked=self.config.get("services.auto_open", True))
        self.auto_open_checkbox.toggled.connect(
            lambda checked: self.config.set("services.auto_open", checked)
        )
        browser_layout.addWidget(self.auto_open_checkbox)

        browser_layout.addStretch()

        self.btn_usage_guide = QPushButton("📖 使用说明")
        self.btn_usage_guide.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_usage_guide.setStyleSheet("""
            QPushButton {
                background-color: transparent; color: #90CAF9;
                border: 1px solid #3949AB; border-radius: 6px;
                padding: 5px 14px; font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #1A237E; color: #E8EAF6; border-color: #5C6BC0; }
        """)
        self.btn_usage_guide.clicked.connect(self._open_usage_guide)
        browser_layout.addWidget(self.btn_usage_guide)

        layout.addWidget(browser_panel)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        for sid, svc in SERVICES.items():
            card = ServiceCard(sid)
            card.restart_clicked.connect(self._restart_service)
            card.open_clicked.connect(self._open_service)
            card.port_change_clicked.connect(self._on_port_change_clicked)
            self.service_cards[sid] = card
            cards_row.addWidget(card)

        layout.addLayout(cards_row)

        log_frame = QFrame()
        log_frame.setObjectName("cardFrame")
        log_layout = QVBoxLayout(log_frame)
        log_layout.setSpacing(6)
        log_layout.setContentsMargins(10, 10, 10, 10)

        log_header = QHBoxLayout()
        log_title = QLabel("\u25A0 运行日志")
        log_title.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        log_title.setStyleSheet("color: #FFFFFF; background: transparent;")
        log_header.addWidget(log_title)

        log_header.addStretch()

        self.auto_scroll_btn = ToggleSwitch("自动滚动", checked=True)
        self.auto_scroll_btn.toggled.connect(self._toggle_auto_scroll)
        log_header.addWidget(self.auto_scroll_btn)

        self.debug_mode_btn = ToggleSwitch("调试模式", checked=False)
        self.debug_mode_btn.toggled.connect(self._toggle_debug_mode)
        log_header.addWidget(self.debug_mode_btn)

        clear_btn = QPushButton("🗑 清空")
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #2A2A2A; color: #888888;
                border: 1px solid #3A3A3A; border-radius: 4px;
                padding: 4px 10px; font-size: 11px;
            }
            QPushButton:hover { background-color: #3A3A3A; color: #CCCCCC; }
        """)
        clear_btn.clicked.connect(lambda: self.log_text.clear())
        log_header.addWidget(clear_btn)

        copy_log_btn = QPushButton("📋 复制")
        copy_log_btn.setStyleSheet("""
            QPushButton {
                background-color: #2A2A2A; color: #888888;
                border: 1px solid #3A3A3A; border-radius: 4px;
                padding: 4px 10px; font-size: 11px;
            }
            QPushButton:hover { background-color: #3A3A3A; color: #CCCCCC; }
        """)
        copy_log_btn.clicked.connect(lambda: self._copy_log(self.log_text))
        log_header.addWidget(copy_log_btn)

        save_log_btn = QPushButton("💾 保存")
        save_log_btn.setStyleSheet("""
            QPushButton {
                background-color: #1A3A5C; color: #8BB8E8;
                border: 1px solid #1E4D7A; border-radius: 4px;
                padding: 4px 10px; font-size: 11px;
            }
            QPushButton:hover { background-color: #1E4D7A; color: #FFFFFF; }
        """)
        save_log_btn.clicked.connect(lambda: self._save_log(self.log_text, "运行日志"))
        log_header.addWidget(save_log_btn)

        log_layout.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.document().setMaximumBlockCount(2000)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #0A0A0A; color: #BBBBBB;
                border: 1px solid #1A1A1A; border-radius: 4px;
                font-family: 'Consolas', 'Microsoft YaHei'; font-size: 10px;
                padding: 4px;
            }
        """)
        log_layout.addWidget(self.log_text, 1)

        layout.addWidget(log_frame, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self.btn_start_all = QPushButton("▶ 一键启动")
        self.btn_start_all.setStyleSheet("""
            QPushButton {
                background-color: #CC0000; color: #FFFFFF;
                border: 1px solid #FF0000; border-radius: 8px;
                padding: 14px 0; font-size: 15px; font-weight: bold;
            }
            QPushButton:hover { background-color: #FF0000; }
            QPushButton:pressed { background-color: #DD0000; }
            QPushButton:disabled { background-color: #1A1A1A; color: #555555; border-color: #222222; }
        """)
        self.btn_start_all.clicked.connect(self._start_all)
        btn_row.addWidget(self.btn_start_all)

        self.btn_stop_all = QPushButton("⏹ 停止服务")
        self.btn_stop_all.setEnabled(False)
        self.btn_stop_all.setStyleSheet("""
            QPushButton {
                background-color: #1565C0; color: #FFFFFF;
                border: 1px solid #1976D2; border-radius: 8px;
                padding: 14px 0; font-size: 15px; font-weight: bold;
            }
            QPushButton:hover { background-color: #1976D2; }
            QPushButton:disabled { background-color: #1A1A1A; color: #555555; border-color: #222222; }
        """)
        self.btn_stop_all.clicked.connect(self._stop_all)
        btn_row.addWidget(self.btn_stop_all)

        layout.addLayout(btn_row)
        self.page_stack.addWidget(page)

    def _build_env_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        deploy_frame = QFrame()
        deploy_frame.setObjectName("cardFrame")
        deploy_layout = QHBoxLayout(deploy_frame)
        deploy_layout.setSpacing(8)
        deploy_layout.setContentsMargins(14, 8, 14, 8)

        info_btn = QPushButton("📋 部署说明")
        info_btn.setFixedSize(82, 26)
        info_btn.setStyleSheet("""
            QPushButton {
                background-color: #1565C0; color: #FFFFFF;
                border: 1px solid #1976D2; border-radius: 6px;
                padding: 3px 6px; font-size: 10px;
            }
            QPushButton:hover { background-color: #1976D2; }
        """)
        info_btn.clicked.connect(self._show_deploy_info)
        deploy_layout.addWidget(info_btn)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet("color: #333333; background: transparent;")
        deploy_layout.addWidget(sep1)

        source_label = QLabel("下载源")
        source_label.setStyleSheet("font-size: 11px; color: #888888; background: transparent;")
        deploy_layout.addWidget(source_label)

        self.deploy_source_combo = QComboBox()
        self.deploy_source_combo.setStyleSheet("""
            QComboBox {
                background-color: #252525; color: #FFFFFF;
                border: 1px solid #333333; border-radius: 6px;
                padding: 4px 22px 4px 8px; font-size: 11px; min-width: 120px;
            }
            QComboBox::drop-down { border: none; width: 18px; }
            QComboBox::down-arrow {
                image: none; border-left: 4px solid transparent;
                border-right: 4px solid transparent; border-top: 4px solid #888888;
            }
            QComboBox QAbstractItemView {
                background-color: #252525; border: 1px solid #333333;
                selection-background-color: #1976D2;
            }
        """)
        self.deploy_source_combo.addItem("自动选择", "auto")
        self.deploy_source_combo.addItem("清华镜像", "tsinghua")
        self.deploy_source_combo.addItem("阿里云镜像", "aliyun")
        self.deploy_source_combo.addItem("官方源", "official")
        deploy_layout.addWidget(self.deploy_source_combo)

        speed_btn = QPushButton("⚡ 测速")
        speed_btn.setFixedSize(60, 26)
        speed_btn.setStyleSheet("""
            QPushButton {
                background-color: #2E7D32; color: #FFFFFF;
                border: 1px solid #388E3C; border-radius: 6px;
                padding: 3px 6px; font-size: 10px;
            }
            QPushButton:hover { background-color: #388E3C; }
        """)
        speed_btn.clicked.connect(self._speed_test_mirrors)
        deploy_layout.addWidget(speed_btn)

        self.speed_result_label = QLabel("")
        self.speed_result_label.setStyleSheet("font-size: 10px; color: #666666; background: transparent;")
        self.speed_result_label.setFixedWidth(60)
        self.speed_result_label.setVisible(False)
        deploy_layout.addWidget(self.speed_result_label)

        deploy_layout.addStretch()

        deploy_layout.addSpacing(50)

        self.progress_container = QFrame()
        self.progress_container.setFixedHeight(16)
        self.progress_container.setStyleSheet("background: transparent;")
        self.progress_container.setVisible(False)
        progress_layout = QHBoxLayout(self.progress_container)
        progress_layout.setContentsMargins(0, 3, 0, 3)
        progress_layout.setSpacing(8)

        self.deploy_progress_bar = QProgressBar()
        self.deploy_progress_bar.setRange(0, 100)
        self.deploy_progress_bar.setValue(0)
        self.deploy_progress_bar.setTextVisible(False)
        self.deploy_progress_bar.setFixedHeight(10)
        self.deploy_progress_bar.setStyleSheet("""
            QProgressBar { background-color: rgba(26,26,26,180); border: 1px solid rgba(33,150,243,80); border-radius: 5px; }
            QProgressBar::chunk { background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1565C0,stop:1 #42A5F5); border-radius: 4px; }
        """)
        progress_layout.addWidget(self.deploy_progress_bar, 1)

        self.deploy_progress_label = QLabel("")
        self.deploy_progress_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.deploy_progress_label.setStyleSheet("font-size: 9px; color: #CCCCCC; background: transparent;")
        progress_layout.addWidget(self.deploy_progress_label, 1)

        deploy_layout.addWidget(self.progress_container, 1)

        self.btn_one_click_deploy = QPushButton("⚙ 一键部署维护")
        self.btn_one_click_deploy.setFixedSize(120, 34)
        self.btn_one_click_deploy.setStyleSheet("""
            QPushButton {
                background-color: #CC0000; color: #FFFFFF;
                border: 1px solid #FF0000; border-radius: 8px;
                padding: 6px 12px; font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #FF0000; }
            QPushButton:pressed { background-color: #DD0000; }
            QPushButton:disabled { background-color: #1A1A1A; color: #555555; border-color: #222222; }
        """)
        self.btn_one_click_deploy.clicked.connect(self._one_click_deploy)
        deploy_layout.addWidget(self.btn_one_click_deploy)

        self.btn_deploy_pause = QPushButton("⏸ 暂停")
        self.btn_deploy_pause.setFixedSize(60, 30)
        self.btn_deploy_pause.setVisible(False)
        self.btn_deploy_pause.setStyleSheet("""
            QPushButton {
                background-color: #E65100; color: #FFFFFF;
                border: 1px solid #F57C00; border-radius: 6px;
                padding: 4px 8px; font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background-color: #F57C00; }
        """)
        self.btn_deploy_pause.clicked.connect(self._toggle_deploy_pause)
        deploy_layout.addWidget(self.btn_deploy_pause)

        self.btn_deploy_cancel = QPushButton("✖ 取消")
        self.btn_deploy_cancel.setFixedSize(60, 30)
        self.btn_deploy_cancel.setVisible(False)
        self.btn_deploy_cancel.setStyleSheet("""
            QPushButton {
                background-color: #424242; color: #E0E0E0;
                border: 1px solid #616161; border-radius: 6px;
                padding: 4px 8px; font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background-color: #616161; }
        """)
        self.btn_deploy_cancel.clicked.connect(self._cancel_deploy)
        deploy_layout.addWidget(self.btn_deploy_cancel)

        layout.addWidget(deploy_frame)

        content_row = QHBoxLayout()
        content_row.setSpacing(8)

        left_col = QVBoxLayout()
        left_col.setSpacing(8)

        env_frame = QFrame()
        env_frame.setObjectName("cardFrame")
        env_layout = QVBoxLayout(env_frame)
        env_layout.setSpacing(4)
        env_layout.setContentsMargins(12, 10, 12, 10)

        env_header = QHBoxLayout()
        env_title = QLabel("\U0001F50D 环境检测")
        _emoji_font2 = QFont()
        _emoji_font2.setFamilies(["Segoe UI Emoji", "Segoe UI Symbol", "Apple Color Emoji", "Noto Color Emoji", "Microsoft YaHei UI"])
        _emoji_font2.setPointSize(11)
        _emoji_font2.setBold(True)
        env_title.setFont(_emoji_font2)
        env_title.setStyleSheet("color: #FFFFFF; background: transparent;")
        env_header.addWidget(env_title)
        env_header.addStretch()

        self._quick_detect_btn = QPushButton("⚡ 快速检测")
        self._quick_detect_btn.setFixedSize(82, 26)
        self._quick_detect_btn.setToolTip("快速检测应用组件路径")
        self._quick_detect_btn.setStyleSheet("""
            QPushButton { background-color: #2E7D32; border: 1px solid #388E3C; border-radius: 6px;
                          color: #FFFFFF; font-size: 10px; padding: 3px 6px; }
            QPushButton:hover { background-color: #388E3C; border-color: #43A047; }
            QPushButton:disabled { background-color: #1A1A1A; color: #555555; border-color: #222222; }
        """)
        self._quick_detect_btn.clicked.connect(self._quick_detect)
        env_header.addWidget(self._quick_detect_btn)

        self._full_detect_btn = QPushButton("🔍 完整性检测")
        self._full_detect_btn.setFixedSize(92, 26)
        self._full_detect_btn.setToolTip("完整检测所有组件和依赖版本")
        self._full_detect_btn.setStyleSheet("""
            QPushButton { background-color: #1565C0; border: 1px solid #1976D2; border-radius: 6px;
                          color: #FFFFFF; font-size: 10px; padding: 3px 6px; }
            QPushButton:hover { background-color: #1976D2; border-color: #1E88E5; }
            QPushButton:disabled { background-color: #1A1A1A; color: #555555; border-color: #222222; }
        """)
        self._full_detect_btn.clicked.connect(self._full_detect)
        env_header.addWidget(self._full_detect_btn)

        self._copy_env_btn = QPushButton("📋 复制清单")
        self._copy_env_btn.setFixedSize(82, 26)
        self._copy_env_btn.setToolTip("复制环境检测清单到剪贴板")
        self._copy_env_btn.setStyleSheet("""
            QPushButton { background-color: #333333; border: 1px solid #444444; border-radius: 6px;
                          color: #AAAAAA; font-size: 10px; padding: 3px 6px; }
            QPushButton:hover { background-color: #444444; border-color: #666666; color: #FFFFFF; }
        """)
        self._copy_env_btn.clicked.connect(self._copy_env_check_list)
        env_header.addWidget(self._copy_env_btn)

        env_layout.addLayout(env_header)

        self._env_check_scroll_content = QVBoxLayout()
        self._env_check_scroll_content.setSpacing(6)
        self._env_check_scroll_content.setContentsMargins(0, 4, 0, 0)

        self._env_check_widgets = {}
        self._env_check_categories = [
            ("runtime", "🔧 核心运行时", [
                ("python", "Python", "官方要求 >=3.12，推荐 3.12.x（3.13+不兼容）"),
                ("pytorch", "PyTorch", "官方要求 >=2.3.0，推荐 2.9.0+cu128（禁止CPU版）"),
                ("cuda", "CUDA", "推荐 12.8（通过PyTorch cu128 wheel内置）"),
                ("cudnn", "cuDNN", "随PyTorch捆绑，无需单独安装"),
                ("nvidia_driver", "NVIDIA驱动", "CUDA 12.8 最低驱动版本 560.70"),
            ]),
            ("inference", "📦 推理依赖", [
                ("transformers", "transformers", "官方要求 >=4.57,<4.58"),
                ("diffusers", "diffusers", "官方要求 >=0.25,<1.0（git特定commit）"),
                ("accelerate", "accelerate", "官方要求 >=0.24,<2.0"),
                ("safetensors", "safetensors", "官方要求 >=0.4,<1.0"),
                ("peft", "peft", "官方要求 >=0.13,<1.0"),
                ("huggingface_hub", "huggingface_hub", "官方要求 >=0.30,<1.0"),
            ]),
            ("tools", "🛠 工具库", [
                ("ffmpeg", "ffmpeg", "无版本要求，推荐最新便携版"),
                ("opencv-python-headless", "opencv-headless", "官方要求 >=4.8,<5.0"),
                ("Pillow", "Pillow", "图像处理库"),
                ("imageio", "imageio", "官方要求 >=2.37,<3.0"),
                ("imageio-ffmpeg", "imageio-ffmpeg", "官方要求 >=0.6,<1.0"),
                ("scipy", "scipy", "官方要求 >=1.14,<2.0"),
                ("einops", "einops", "官方要求 >=0.8,<1.0"),
                ("av", "av", "官方要求 >=16.0,<17.0"),
                ("tqdm", "tqdm", "官方要求 >=4.66,<5.0"),
                ("protobuf", "protobuf", "官方要求 >=3.20,<7.0"),
                ("sentencepiece", "sentencepiece", "官方要求 >=0.1.99,<1.0"),
                ("ftfy", "ftfy", "官方要求 >=6.0,<7.0"),
                ("pynvml", "pynvml", "官方要求 >=11.5,<14.0"),
                ("pydantic", "pydantic", "官方要求 >=2.7,<3.0"),
                ("python-multipart", "python-multipart", "FastAPI文件上传依赖"),
                ("sageattention", "sageattention", "Ampere+架构推荐安装"),
                ("triton-windows", "triton-windows", "Windows Triton后端"),
            ]),
            ("app", "📁 应用组件", [
                ("ltx", "LTX Desktop", "核心引擎，整合包内置"),
                ("backend", "后端代码", "LTX Server后端，整合包内置"),
                ("patches", "补丁文件", "云集定制补丁，整合包内置"),
                ("ui", "前端界面", "AI视频工作站界面，整合包内置"),
                ("models", "模型目录", "AI模型文件存储目录"),
                ("project", "项目根目录", "项目根路径"),
            ]),
            ("extensions", "🧩 扩展组件", [
                ("voxcpm", "VoxCPM2", "TTS语音合成，要求 >=2.0.0"),
                ("faster_whisper", "faster-whisper", "语音识别/字幕生成"),
                ("real_esrgan", "Real-ESRGAN", "视频/图片高清放大"),
            ]),
        ]

        for cat_key, cat_title, cat_items in self._env_check_categories:
            cat_header = QLabel(cat_title)
            cat_header.setStyleSheet("font-size: 10px; color: #AAAAAA; font-weight: bold; background: transparent; border-bottom: 1px solid #333333; padding-bottom: 2px;")
            self._env_check_scroll_content.addWidget(cat_header)

            cat_grid = QGridLayout()
            cat_grid.setSpacing(2)
            cat_grid.setContentsMargins(0, 2, 0, 0)

            for i, (key, display_name, tooltip_text) in enumerate(cat_items):
                row_idx = i // 2
                col_idx = i % 2
                cell = QHBoxLayout()
                cell.setSpacing(6)

                name_lbl = QLabel(display_name)
                name_lbl.setFixedWidth(85)
                name_lbl.setToolTip(tooltip_text)
                name_lbl.setStyleSheet("font-size: 9px; color: #888888; background: transparent;")
                cell.addWidget(name_lbl)

                val_lbl = QLabel("未检测")
                val_lbl.setStyleSheet("font-size: 9px; color: #42A5F5; background: transparent;")
                val_lbl.setMaximumWidth(200)
                val_lbl.setWordWrap(False)
                val_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                val_lbl._full_text = ""
                cell.addWidget(val_lbl, 1)

                fix_btn = QPushButton("🔧")
                fix_btn.setFixedSize(30, 16)
                fix_btn.setStyleSheet("""
                    QPushButton { background-color: #333333; border: 1px solid #444444; border-radius: 2px;
                                  color: #AAAAAA; font-size: 8px;
                                  padding: 0px; margin-left: 4px; }
                    QPushButton:hover { background-color: #1565C0; border-color: #42A5F5; color: #FFFFFF; }
                    QPushButton:pressed { background-color: #0D47A1; }
                """)
                fix_btn.setFixedWidth(0)
                fix_btn.setText("")
                fix_btn.clicked.connect(lambda checked, k=key: self._fix_single_component(k))
                cell.addWidget(fix_btn)

                cat_grid.addLayout(cell, row_idx, col_idx)
                self._env_check_widgets[key] = (val_lbl, fix_btn)

            self._env_check_scroll_content.addLayout(cat_grid)

        env_layout.addLayout(self._env_check_scroll_content)

        self._env_check_summary = QLabel("")
        self._env_check_summary.setStyleSheet("font-size: 9px; color: #888888; background: transparent; padding-top: 4px;")
        self._env_check_summary.setWordWrap(True)
        env_layout.addWidget(self._env_check_summary)

        left_col.addWidget(env_frame)

        dir_frame = QFrame()
        dir_frame.setObjectName("cardFrame")
        dir_layout = QVBoxLayout(dir_frame)
        dir_layout.setSpacing(6)
        dir_layout.setContentsMargins(12, 10, 12, 10)

        dir_header = QHBoxLayout()
        dir_title = QLabel("\u25C7 目录配置")
        dir_title.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        dir_title.setStyleSheet("color: #FFFFFF; background: transparent;")
        dir_header.addWidget(dir_title)
        dir_header.addStretch()
        dir_layout.addLayout(dir_header)

        models_row = QHBoxLayout()
        models_row.setSpacing(6)
        models_label = QLabel("模型")
        models_label.setFixedWidth(32)
        models_label.setStyleSheet("font-size: 10px; color: #888888; background: transparent;")
        models_row.addWidget(models_label)
        browse_btn = QPushButton("📂 更改模型目录")
        browse_btn.setStyleSheet("""
            QPushButton {
                background-color: #1A3A5C; color: #8BB8E8;
                border: 1px solid #1E4D7A; border-radius: 4px;
                padding: 4px 10px; font-size: 10px;
            }
            QPushButton:hover { background-color: #1E4D7A; color: #FFFFFF; }
        """)
        browse_btn.clicked.connect(self._browse_models_dir)
        models_row.addWidget(browse_btn, 1)
        dir_layout.addLayout(models_row)

        output_label_row = QHBoxLayout()
        output_label_row.setSpacing(6)
        output_dir_label = QLabel("输出")
        output_dir_label.setFixedWidth(32)
        output_dir_label.setStyleSheet("font-size: 10px; color: #888888; background: transparent;")
        output_label_row.addWidget(output_dir_label)
        self._output_dir_edit = QLineEdit()
        self._output_dir_edit.setPlaceholderText("留空使用默认路径")
        self._output_dir_edit.setStyleSheet("""
            QLineEdit {
                background-color: #1E1E1E; color: #CCCCCC;
                border: 1px solid #2A2A2A; border-radius: 4px;
                padding: 4px 8px; font-size: 10px;
            }
            QLineEdit:focus { border-color: #1976D2; }
        """)
        self._load_output_dir_setting()
        output_label_row.addWidget(self._output_dir_edit, 1)
        dir_layout.addLayout(output_label_row)

        output_btn_row = QHBoxLayout()
        output_btn_row.setSpacing(6)
        output_btn_row.setContentsMargins(38, 0, 0, 0)

        _btn_style_browse = """
            QPushButton {
                background-color: #1A3A5C; color: #8BB8E8;
                border: 1px solid #1E4D7A; border-radius: 4px;
                padding: 3px 10px; font-size: 10px;
            }
            QPushButton:hover { background-color: #1E4D7A; color: #FFFFFF; }
        """
        _btn_style_save = """
            QPushButton {
                background-color: #1B4332; color: #7BC47F;
                border: 1px solid #2D6A4F; border-radius: 4px;
                padding: 3px 10px; font-size: 10px;
            }
            QPushButton:hover { background-color: #2D6A4F; color: #FFFFFF; }
        """
        _btn_style_open = """
            QPushButton {
                background-color: #4A3728; color: #D4A574;
                border: 1px solid #6B4F3A; border-radius: 4px;
                padding: 3px 10px; font-size: 10px;
            }
            QPushButton:hover { background-color: #6B4F3A; color: #FFFFFF; }
        """

        output_browse_btn = QPushButton("📂 选择目录")
        output_browse_btn.setStyleSheet(_btn_style_browse)
        output_browse_btn.clicked.connect(self._browse_output_dir)
        output_btn_row.addWidget(output_browse_btn)

        output_save_btn = QPushButton("💾 保存设置")
        output_save_btn.setStyleSheet(_btn_style_save)
        output_save_btn.clicked.connect(self._save_output_dir_setting)
        output_btn_row.addWidget(output_save_btn)

        output_open_btn = QPushButton("📁 打开目录")
        output_open_btn.setStyleSheet(_btn_style_open)
        output_open_btn.clicked.connect(self._open_output_dir)
        output_btn_row.addWidget(output_open_btn)

        output_btn_row.addStretch()
        dir_layout.addLayout(output_btn_row)

        self._output_dir_hint = QLabel()
        self._output_dir_hint.setStyleSheet("font-size: 9px; color: #555555; background: transparent; padding-left: 38px;")
        dir_layout.addWidget(self._output_dir_hint)

        left_col.addWidget(dir_frame)
        left_col.addStretch()

        content_row.addLayout(left_col, 4)

        deploy_log_frame = QFrame()
        deploy_log_frame.setObjectName("cardFrame")
        deploy_log_layout = QVBoxLayout(deploy_log_frame)
        deploy_log_layout.setSpacing(4)
        deploy_log_layout.setContentsMargins(12, 10, 12, 10)

        deploy_log_header = QHBoxLayout()
        deploy_log_title = QLabel("\U0001F4CB 运行日志")
        _emoji_font4 = QFont()
        _emoji_font4.setFamilies(["Segoe UI Emoji", "Segoe UI Symbol", "Apple Color Emoji", "Noto Color Emoji", "Microsoft YaHei UI"])
        _emoji_font4.setPointSize(11)
        _emoji_font4.setBold(True)
        deploy_log_title.setFont(_emoji_font4)
        deploy_log_title.setStyleSheet("color: #FFFFFF; background: transparent;")
        deploy_log_header.addWidget(deploy_log_title)
        deploy_log_header.addStretch()

        self.deploy_auto_scroll_btn = ToggleSwitch("自动滚动", checked=True)
        self.deploy_auto_scroll_btn.toggled.connect(self._toggle_deploy_auto_scroll)
        deploy_log_header.addWidget(self.deploy_auto_scroll_btn)

        self.deploy_debug_btn = ToggleSwitch("调试模式", checked=False)
        self.deploy_debug_btn.toggled.connect(lambda checked: self.debug_mode_btn.setChecked(checked))
        deploy_log_header.addWidget(self.deploy_debug_btn)

        clear_deploy_log_btn = QPushButton("🗑 清空")
        clear_deploy_log_btn.setStyleSheet("""
            QPushButton {
                background-color: #2A2A2A; color: #888888;
                border: 1px solid #3A3A3A; border-radius: 4px;
                padding: 4px 10px; font-size: 11px;
            }
            QPushButton:hover { background-color: #3A3A3A; color: #CCCCCC; }
        """)
        clear_deploy_log_btn.clicked.connect(lambda: self.deploy_log_text.clear())
        deploy_log_header.addWidget(clear_deploy_log_btn)

        save_deploy_log_btn = QPushButton("💾 保存")
        save_deploy_log_btn.setStyleSheet("""
            QPushButton {
                background-color: #1A3A5C; color: #8BB8E8;
                border: 1px solid #1E4D7A; border-radius: 4px;
                padding: 4px 10px; font-size: 11px;
            }
            QPushButton:hover { background-color: #1E4D7A; color: #FFFFFF; }
        """)
        save_deploy_log_btn.clicked.connect(lambda: self._save_log(self.deploy_log_text, "部署日志"))
        deploy_log_header.addWidget(save_deploy_log_btn)

        deploy_log_layout.addLayout(deploy_log_header)

        self.deploy_log_text = QTextEdit()
        self.deploy_log_text.setReadOnly(True)
        self.deploy_log_text.document().setMaximumBlockCount(2000)
        self.deploy_log_text.setStyleSheet("""
            QTextEdit {
                background-color: #0A0A0A; color: #BBBBBB;
                border: 1px solid #1A1A1A; border-radius: 4px;
                font-family: 'Consolas', 'Microsoft YaHei'; font-size: 10px;
                padding: 4px;
            }
        """)
        deploy_log_layout.addWidget(self.deploy_log_text, 1)

        content_row.addWidget(deploy_log_frame, 6)

        layout.addLayout(content_row, 1)
        self._env_page_widget = page
        self.page_stack.addWidget(page)

    def _build_models_page(self):
        if not self._models_dir:
            self._models_dir = os.path.join(self._data_dir or "", "models")
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.setContentsMargins(15, 15, 15, 15)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)

        dir_label = QLabel("模型目录:")
        dir_label.setStyleSheet("font-size: 12px; color: #AAAAAA; font-weight: bold; background: transparent;")
        dir_row.addWidget(dir_label)

        self._model_dir_combo = QComboBox()
        self._model_dir_combo.setStyleSheet("""
            QComboBox { background-color: #252525; color: #FFFFFF; border: 1px solid #333333; border-radius: 4px; padding: 6px 28px 6px 10px; font-size: 11px; min-width: 300px; }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox::down-arrow { image: none; border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 5px solid #888888; }
            QComboBox QAbstractItemView { background-color: #252525; border: 1px solid #333333; selection-background-color: #CC0000; }
        """)
        dir_row.addWidget(self._model_dir_combo, 1)

        self._remove_dir_btn = QPushButton(" 删除")
        self._remove_dir_btn.setFixedWidth(55)
        rm_icon_svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#888888" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>'
        pm_rm = QPixmap(12, 12)
        pm_rm.fill(QColor(0, 0, 0, 0))
        painter_rm = QPainter(pm_rm)
        QSvgRenderer(rm_icon_svg).render(painter_rm)
        painter_rm.end()
        self._remove_dir_btn.setIcon(QIcon(pm_rm))
        self._remove_dir_btn.setStyleSheet("""
            QPushButton { background-color: transparent; border: 1px solid #555555; border-radius: 4px; color: #888888; font-size: 10px; padding: 4px 6px; }
            QPushButton:hover { background-color: #FF0000; border-color: #FF0000; color: #FFFFFF; }
            QPushButton:hover:pressed { background-color: #DD0000; border-color: #DD0000; }
            QPushButton:disabled { color: #444444; border-color: #333333; }
        """)
        self._remove_dir_btn.setToolTip("删除选中的自定义目录")
        self._remove_dir_btn.clicked.connect(self._remove_selected_model_dir)
        dir_row.addWidget(self._remove_dir_btn)

        add_dir_btn = QPushButton(" 添加目录")
        add_icon_svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/></svg>'
        pm_add = QPixmap(12, 12)
        pm_add.fill(QColor(0, 0, 0, 0))
        painter_add = QPainter(pm_add)
        QSvgRenderer(add_icon_svg).render(painter_add)
        painter_add.end()
        add_dir_btn.setIcon(QIcon(pm_add))
        add_dir_btn.setStyleSheet("QPushButton { background-color: #CC0000; color: #FFFFFF; border: 1px solid #FF0000; border-radius: 4px; padding: 4px 12px; font-size: 10px; font-weight: bold; } QPushButton:hover { background-color: #FF0000; } QPushButton:pressed { background-color: #DD0000; }")
        add_dir_btn.clicked.connect(self._add_model_dir)
        dir_row.addWidget(add_dir_btn)

        source_label = QLabel("下载源:")
        source_label.setStyleSheet("font-size: 11px; color: #AAAAAA; background: transparent; font-weight: bold;")
        dir_row.addWidget(source_label)

        self.model_source_combo = QComboBox()
        self.model_source_combo.setStyleSheet("""
            QComboBox { background-color: #252525; color: #FFFFFF; border: 1px solid #333333; border-radius: 4px; padding: 4px 24px 4px 8px; font-size: 11px; min-width: 130px; }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox::down-arrow { image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 4px solid #888888; }
            QComboBox QAbstractItemView { background-color: #252525; border: 1px solid #333333; selection-background-color: #CC0000; }
        """)
        self.model_source_combo.addItem("HF-Mirror (国内)", "hf_mirror")
        self.model_source_combo.addItem("HuggingFace (官方)", "hf_official")
        self.model_source_combo.addItem("ModelScope (国内)", "modelscope")
        dir_row.addWidget(self.model_source_combo)

        layout.addLayout(dir_row)

        self._model_dir_combo.currentIndexChanged.connect(lambda: self._update_remove_dir_btn_state())
        self._populate_model_dir_combo()
        # 启动时同步junction映射
        self._sync_model_junctions()

        # ── 主内容区：上方分类筛选 + 下方表格 ──
        content_layout = QVBoxLayout()
        content_layout.setSpacing(6)

        # 上方分类筛选栏（横向）
        self._model_category_list = QListWidget()
        self._model_category_list.setFlow(QListWidget.Flow.LeftToRight)
        self._model_category_list.setWrapping(False)
        self._model_category_list.setFixedHeight(38)
        self._model_category_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._model_category_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._model_category_list.setStyleSheet("""
            QListWidget {
                background-color: #1A1A1A; border: 1px solid #333333; border-radius: 6px;
                padding: 2px 4px; outline: none;
            }
            QListWidget::item {
                padding: 4px 12px; border-radius: 4px; color: #AAAAAA; font-size: 11px;
                margin: 2px 3px;
            }
            QListWidget::item:selected {
                background-color: #CC0000; color: #FFFFFF; font-weight: bold;
            }
            QListWidget::item:hover {
                background-color: #252525; color: #FFFFFF;
            }
        """)
        self._model_category_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)

        categories = [
            ("全部模型", "all"),
            ("视频模型", "视频模型"),
            ("视频LoRA", "视频LoRA"),
            ("图像模型", "图像模型"),
            ("图像LoRA", "图像LoRA"),
            ("控制模型", "控制模型"),
            ("高清放大", "高清放大"),
            ("辅助模型", "辅助模型"),
        ]
        for name, key in categories:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._model_category_list.addItem(item)
        self._model_category_list.setCurrentRow(0)
        self._model_category_filter = "all"
        self._model_category_list.currentItemChanged.connect(self._on_model_category_changed)
        content_layout.addWidget(self._model_category_list)

        # 表格
        self._model_table = QTableWidget()
        self._model_table.setColumnCount(8)
        self._model_table.setHorizontalHeaderLabels(["", "模型名称", "描述", "分类", "标签", "大小", "状态", "操作"])
        self._model_table.setStyleSheet("""
            QTableWidget { background-color: #111113; border: 1px solid #333333; border-radius: 6px; gridline-color: #222222; font-size: 12px; color: #DDDDDD; }
            QTableWidget::item { padding: 4px 6px; border-bottom: 1px solid #222222; border: none; outline: none; background: transparent; }
            QTableWidget::item:hover { background-color: #2A2A2E; border: none; outline: none; }
            QTableWidget::item:selected { background-color: #CC0000; color: #FFFFFF; border: none; outline: none; }
            QTableWidget::item:focus { background-color: #CC0000; color: #FFFFFF; outline: none; border: none; }
            QHeaderView::section { background-color: #1A1A1A; color: #AAAAAA; border: none; border-bottom: 2px solid #333333; border-right: 1px solid #222222; padding: 6px 8px; font-size: 11px; font-weight: bold; }
            QHeaderView::section:hover { background-color: #252525; color: #FFFFFF; }
            QCheckBox { spacing: 4px; }
            QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #555555; border-radius: 3px; background-color: #252525; }
            QCheckBox::indicator:checked { background-color: #CC0000; border-color: #FF0000; }
            QCheckBox::indicator:hover { border-color: #888888; }
            QLabel { background: transparent; }
        """)
        self._model_table.horizontalHeader().setSectionsMovable(False)
        self._model_table.horizontalHeader().setStretchLastSection(False)
        self._model_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._model_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self._model_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._model_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self._model_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self._model_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self._model_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)
        self._model_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        self._model_table.setColumnWidth(0, 40)
        self._model_table.setColumnWidth(1, 300)
        self._model_table.setColumnWidth(3, 80)
        self._model_table.setColumnWidth(4, 50)
        self._model_table.setColumnWidth(5, 65)
        self._model_table.setColumnWidth(6, 90)
        self._model_table.setColumnWidth(7, 100)
        self._model_table.verticalHeader().setVisible(False)
        self._model_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._model_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._model_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._model_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._model_sort_col = -1
        self._model_sort_asc = True
        self._model_table.horizontalHeader().sectionClicked.connect(self._on_model_header_clicked)
        self._model_table.setMouseTracking(True)
        self._model_table.cellEntered.connect(self._on_model_row_hover)
        self._model_table.installEventFilter(self)
        self._hover_row = -1
        self._model_detail_row = -1  # 当前展开详情的行

        # 点击行展开详情
        self._model_table.cellClicked.connect(self._on_model_row_clicked)

        self._select_all_cb = QCheckBox("全选")
        self._select_all_cb.setStyleSheet("QCheckBox { color: #AAAAAA; font-size: 11px; spacing: 4px; background: transparent; } QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #555555; border-radius: 3px; background-color: #252525; } QCheckBox::indicator:checked { background-color: #CC0000; border-color: #FF0000; }")
        self._select_all_cb.stateChanged.connect(self._toggle_select_all_models)

        self._model_table.setHorizontalHeaderItem(0, QTableWidgetItem(""))
        self._model_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)

        content_layout.addWidget(self._model_table, 1)

        layout.addLayout(content_layout, 1)

        self._populate_model_table()

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._select_all_cb)
        btn_row.addSpacing(10)
        self._batch_download_btn = QPushButton(" 批量下载")
        dl_icon_svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>'
        pm_dl = QPixmap(14, 14)
        pm_dl.fill(QColor(0, 0, 0, 0))
        painter_dl = QPainter(pm_dl)
        QSvgRenderer(dl_icon_svg).render(painter_dl)
        painter_dl.end()
        self._batch_download_btn.setIcon(QIcon(pm_dl))
        self._batch_download_btn.setStyleSheet("""
            QPushButton { background-color: #1B5E20; color: #FFFFFF; border: 1px solid #2E7D32; border-radius: 8px; padding: 10px 20px; font-size: 13px; font-weight: bold; }
            QPushButton:hover { background-color: #2E7D32; }
        """)
        self._batch_download_btn.clicked.connect(self._batch_download_models)
        btn_row.addWidget(self._batch_download_btn)

        self._batch_edit_btn = QPushButton(" 批量编辑")
        edit_icon_svg2 = b'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>'
        pm_ed2 = QPixmap(14, 14)
        pm_ed2.fill(QColor(0, 0, 0, 0))
        painter_ed2 = QPainter(pm_ed2)
        QSvgRenderer(edit_icon_svg2).render(painter_ed2)
        painter_ed2.end()
        self._batch_edit_btn.setIcon(QIcon(pm_ed2))
        self._batch_edit_btn.setStyleSheet("""
            QPushButton { background-color: #1B5E20; color: #FFFFFF; border: 1px solid #2E7D32; border-radius: 8px; padding: 10px 20px; font-size: 13px; font-weight: bold; }
            QPushButton:hover { background-color: #2E7D32; }
        """)
        self._batch_edit_btn.clicked.connect(self._batch_edit_models)
        btn_row.addWidget(self._batch_edit_btn)

        check_btn = QPushButton(" 检测完整性")
        check_icon_svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>'
        pm_chk = QPixmap(14, 14)
        pm_chk.fill(QColor(0, 0, 0, 0))
        painter_chk = QPainter(pm_chk)
        QSvgRenderer(check_icon_svg).render(painter_chk)
        painter_chk.end()
        check_btn.setIcon(QIcon(pm_chk))
        check_btn.setStyleSheet("""
            QPushButton { background-color: #CC0000; color: #FFFFFF; border: 1px solid #FF0000; border-radius: 8px; padding: 10px 20px; font-size: 13px; font-weight: bold; }
            QPushButton:hover { background-color: #FF0000; }
            QPushButton:pressed { background-color: #DD0000; }
        """)
        check_btn.clicked.connect(self._check_model_integrity)
        btn_row.addWidget(check_btn)

        refresh_btn = QPushButton(" 同步更新")
        refresh_icon_svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>'
        pm_ref = QPixmap(14, 14)
        pm_ref.fill(QColor(0, 0, 0, 0))
        painter_ref = QPainter(pm_ref)
        QSvgRenderer(refresh_icon_svg).render(painter_ref)
        painter_ref.end()
        refresh_btn.setIcon(QIcon(pm_ref))
        refresh_btn.setStyleSheet("QPushButton { background-color: #1565C0; color: #FFFFFF; border: 1px solid #1976D2; border-radius: 8px; padding: 10px 20px; font-size: 13px; font-weight: bold; } QPushButton:hover { background-color: #1976D2; }")
        refresh_btn.clicked.connect(self._sync_model_updates)
        btn_row.addWidget(refresh_btn)

        layout.addLayout(btn_row)
        self.page_stack.addWidget(page)

    def _on_model_category_changed(self, current, previous):
        if current:
            self._model_category_filter = current.data(Qt.ItemDataRole.UserRole)
            self._apply_model_sort_and_render()

    def _on_model_row_clicked(self, row, col):
        """点击行展开/收起详情行"""
        if row < 0:
            return

        # 如果点击的是详情行本身，忽略
        if self._model_table.item(row, 0) and self._model_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == "detail":
            return

        # 收起旧的详情行
        old_detail_row = getattr(self, '_model_detail_row', -1)
        if old_detail_row >= 0:
            self._collapse_detail_row(old_detail_row)
            if old_detail_row == row:
                self._model_detail_row = -1
                return
            # 如果旧行在当前行上方，行号已因删除而偏移
            if old_detail_row < row:
                row -= 1

        self._model_detail_row = row + 1  # 详情行插在数据行下方
        r = self._model_rows[row] if row < len(self._model_rows) else None
        if r:
            self._expand_detail_row(row + 1, r)

    def _collapse_detail_row(self, detail_row):
        """收起指定位置的详情行"""
        if detail_row < 0 or detail_row >= self._model_table.rowCount():
            return
        item0 = self._model_table.item(detail_row, 0)
        if item0 and item0.data(Qt.ItemDataRole.UserRole) == "detail":
            self._model_table.removeRow(detail_row)

    def _expand_detail_row(self, insert_row, r):
        """在指定位置插入详情行"""
        self._model_table.insertRow(insert_row)

        # 标记为详情行
        marker = QTableWidgetItem()
        marker.setData(Qt.ItemDataRole.UserRole, "detail")
        self._model_table.setItem(insert_row, 0, marker)

        # 创建详情widget
        detail_widget = QWidget()
        detail_widget.setStyleSheet("background-color: #1A1A1A; border: none;")

        detail_layout = QHBoxLayout(detail_widget)
        detail_layout.setContentsMargins(20, 8, 16, 8)
        detail_layout.setSpacing(12)

        # 左侧图标
        icon_svg_map = {
            "视频模型": b'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#CC0000" stroke-width="1.5"><rect x="2" y="2" width="20" height="20" rx="3"/><path d="M7 12h10M12 7v10"/></svg>',
            "图像模型": b'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#E91E63" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>',
            "视频LoRA": b'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#FF9800" stroke-width="1.5"><circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/></svg>',
            "图像LoRA": b'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#FF5722" stroke-width="1.5"><circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/></svg>',
            "控制模型": b'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#2196F3" stroke-width="1.5"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>',
            "高清放大": b'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#4CAF50" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>',
            "辅助模型": b'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#9C27B0" stroke-width="1.5"><path d="M4 7V4h16v3"/><path d="M9 20h6"/><path d="M12 4v16"/></svg>',
        }
        category = r.get("category", "")
        svg_data = icon_svg_map.get(category, icon_svg_map.get("视频模型"))
        icon_label = QLabel()
        pm_icon = QPixmap(48, 48)
        pm_icon.fill(QColor(0, 0, 0, 0))
        painter_icon = QPainter(pm_icon)
        painter_icon.setRenderHint(QPainter.RenderHint.Antialiasing)
        QSvgRenderer(svg_data).render(painter_icon)
        painter_icon.end()
        icon_label.setPixmap(pm_icon)
        icon_label.setFixedSize(48, 48)
        detail_layout.addWidget(icon_label)

        # 右侧信息
        info_layout = QVBoxLayout()
        info_layout.setSpacing(3)

        # 名称
        name_lbl = QLabel(r.get("name", ""))
        name_lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #FFFFFF; background: transparent; border: none;")
        info_layout.addWidget(name_lbl)

        # 描述
        desc = r.get("description", "")
        if desc:
            desc_lbl = QLabel(f"模型介绍：{desc}")
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet("font-size: 11px; color: #BBBBBB; background: transparent; border: none;")
            info_layout.addWidget(desc_lbl)

        # 信息行：分类 | 标签 | 大小 | 状态
        meta_row = QHBoxLayout()
        meta_row.setSpacing(20)

        cat_lbl = QLabel(f"分类: {category}")
        cat_lbl.setStyleSheet("font-size: 10px; color: #888888; background: transparent; border: none;")
        meta_row.addWidget(cat_lbl)

        tag = r.get("tag", "")
        if tag:
            tag_lbl = QLabel(f"标签: {tag}")
            tag_color = "#FF0000" if tag == "必需" else "#FF9800" if tag == "推荐" else "#888888"
            tag_lbl.setStyleSheet(f"font-size: 10px; color: {tag_color}; background: transparent; border: none; font-weight: bold;")
            meta_row.addWidget(tag_lbl)

        size_gb = r.get("size_gb", 0)
        if size_gb > 0:
            size_lbl = QLabel(f"大小: {size_gb:.1f} GB")
            size_lbl.setStyleSheet("font-size: 10px; color: #888888; background: transparent; border: none;")
            meta_row.addWidget(size_lbl)

        status = r.get("status", "")
        status_color = r.get("status_color", "#DDDDDD")
        status_lbl = QLabel(f"状态: {status}")
        status_lbl.setStyleSheet(f"font-size: 10px; color: {status_color}; background: transparent; border: none; font-weight: bold;")
        meta_row.addWidget(status_lbl)

        meta_row.addStretch()
        info_layout.addLayout(meta_row)

        # 触发词
        tw = r.get("trigger_words", "")
        if tw:
            tw_lbl = QLabel(f"触发词: {tw}")
            tw_lbl.setWordWrap(True)
            tw_lbl.setStyleSheet("font-size: 10px; color: #42A5F5; background: transparent; border: none;")
            info_layout.addWidget(tw_lbl)

        # 示例提示词
        example = _MODEL_EXAMPLES.get(r.get("filename", "") or r.get("name", ""), "")
        if example:
            ex_lbl = QLabel(f"示例: {example}")
            ex_lbl.setWordWrap(True)
            ex_lbl.setStyleSheet("font-size: 10px; color: #66BB6A; background: transparent; border: none;")
            info_layout.addWidget(ex_lbl)

        # 来源
        repo = r.get("repo_id", "")
        if repo:
            repo_lbl = QLabel(f"来源: {repo}")
            repo_lbl.setStyleSheet("font-size: 10px; color: #666666; background: transparent; border: none;")
            info_layout.addWidget(repo_lbl)

        detail_layout.addLayout(info_layout, 1)

        # 收起按钮
        close_up_svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#AAAAAA" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>'
        pm_close = QPixmap(12, 12)
        pm_close.fill(QColor(0, 0, 0, 0))
        painter_close = QPainter(pm_close)
        QSvgRenderer(close_up_svg).render(painter_close)
        painter_close.end()
        close_btn = QPushButton(" 收起")
        close_btn.setIcon(QIcon(pm_close))
        close_btn.setFixedSize(72, 28)
        close_btn.setStyleSheet("""
            QPushButton { background-color: #333333; color: #AAAAAA; border: 1px solid #444444; border-radius: 4px; font-size: 9px; }
            QPushButton:hover { background-color: #444444; color: #FFFFFF; }
        """)
        close_btn.clicked.connect(lambda: self._collapse_detail_row(getattr(self, '_model_detail_row', -1)) or setattr(self, '_model_detail_row', -1))
        detail_layout.addWidget(close_btn)

        # 设置详情widget到表格
        self._model_table.setCellWidget(insert_row, 0, detail_widget)
        # 合并所有列
        self._model_table.setSpan(insert_row, 0, 1, self._model_table.columnCount())
        self._model_table.setRowHeight(insert_row, detail_widget.sizeHint().height() + 16 if detail_widget.sizeHint().height() > 50 else 120)

    def _classify_model(self, model_id, info):
        fname = info.get("file", "").lower()
        category = info.get("category", "")
        # 优先使用 LTX_MODELS 中已定义的 category
        if category and category != "其他":
            return category
        # 回退：根据文件名推断
        if "upscaler" in fname:
            return "高清放大"
        if "ic-lora-union" in fname:
            return "控制模型"
        if "lora" in fname:
            # 区分视频LoRA和图像LoRA
            if "z-image" in fname or "zimage" in fname or "zib-" in fname or "zit-" in fname:
                return "图像LoRA"
            return "视频LoRA"
        if "text-encoder" in model_id or "gemma" in fname:
            return "辅助模型"
        if "z-image" in fname or "zimage" in fname:
            return "图像模型"
        if "distilled" in fname or "dev" in fname:
            return "视频模型"
        return "其他"

    def _infer_tag(self, dir_path):
        name = os.path.basename(dir_path).lower()
        has_safetensors = any(f.endswith('.safetensors') for f in os.listdir(dir_path) if os.path.isfile(os.path.join(dir_path, f)))
        if has_safetensors:
            safetensors_files = [f for f in os.listdir(dir_path) if f.endswith('.safetensors')]
            if safetensors_files:
                first_file = safetensors_files[0].lower()
                if 'lora' in first_file:
                    if 'z-image' in first_file or 'zimage' in first_file:
                        return "图像LoRA"
                    return "视频LoRA"
                if 'upscaler' in first_file:
                    return "高清放大"
                if 'control' in first_file or 'ic-lora-union' in first_file:
                    return "控制模型"
        tag_map = {
            "lora": "LoRA", "control": "控制模型", "upscaler": "高清放大",
            "text-encoder": "辅助模型", "text_encoder": "辅助模型",
            "vae": "VAE", "unet": "UNet", "style": "风格LoRA",
            "character": "人物LoRA", "人物": "人物LoRA", "风格": "风格LoRA",
        }
        for key, tag in tag_map.items():
            if key in name:
                return tag
        return name

    def _populate_model_table(self):
        self._model_checkboxes = {}
        self._model_rows = []
        try:
            import httpx
            with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
                resp = client.get(f"http://127.0.0.1:{self._backend_port}/api/models/registry")
                if resp.status_code != 200:
                    self._populate_model_table_fallback()
                    return
                data = resp.json()
        except Exception:
            self._populate_model_table_fallback()
            return

        registry_models = data.get("models", [])
        local_dirs = data.get("local_dirs", [])

        local_files_map = {}
        for ld in local_dirs:
            for m in ld.get("models", []):
                p = m.get("path", "")
                if p not in local_files_map:
                    local_files_map[p] = m

        seen_filenames = set()

        for rm in registry_models:
            fname = rm.get("filename", "")
            if fname in seen_filenames:
                continue
            seen_filenames.add(fname)

            # 使用后端的 model_category 字段映射到前端分类
            mc = rm.get("model_category", "")
            if mc == "checkpoint":
                fname_lower = fname.lower()
                if "z-image" in fname_lower or "zimage" in fname_lower:
                    category = "图像模型"
                else:
                    category = "视频模型"
            elif mc == "lora":
                fname_lower = fname.lower()
                if "ic-lora-union" in fname_lower:
                    category = "控制模型"
                elif "z-image" in fname_lower or "zimage" in fname_lower or "zib-" in fname_lower or "zit-" in fname_lower:
                    category = "图像LoRA"
                else:
                    category = "视频LoRA"
            elif mc == "upscaler":
                category = "高清放大"
            elif mc == "supporting":
                if "text-encoder" in rm.get("model_id", "") or "gemma" in fname.lower():
                    category = "辅助模型"
                elif "z-image" in fname.lower() or "zimage" in fname.lower():
                    category = "图像模型"
                elif "tts" in fname.lower() or "voxcpm" in fname.lower():
                    category = "辅助模型"
                else:
                    category = "辅助模型"
            else:
                # 回退：根据文件名推断
                category = self._classify_model("", {"file": fname, "model_id": rm.get("model_id", "")})

            rm_tags = rm.get("tags", [])
            if rm_tags and "required" in rm_tags:
                tag_text = "必需"
            elif rm_tags and "recommended" in rm_tags:
                tag_text = "推荐"
            else:
                tag_text = "可选"
            size_gb = rm.get("size_gb", 0)
            downloaded = rm.get("downloaded", False)

            self._model_rows.append({
                "name": rm.get("name", fname),
                "description": rm.get("description", ""),
                "trigger_words": ", ".join(rm.get("trigger_words", [])) if rm.get("trigger_words") else "",
                "category": category,
                "tag": tag_text,
                "size_gb": size_gb,
                "status": "已下载" if downloaded else "未下载",
                "status_icon": "√" if downloaded else "×",
                "status_color": "#66BB6A" if downloaded else "#FF0000",
                "model_id": rm.get("model_id", ""),
                "local_path": rm.get("local_path", ""),
                "downloaded": downloaded,
                "source": "registry",
                "repo_id": rm.get("repo_id", ""),
                "filename": rm.get("filename", ""),
                "quantization": rm.get("quantization", ""),
                "variant": rm.get("variant", ""),
                "is_folder": rm.get("is_folder", False),
                "modelscope_id": rm.get("modelscope_id", ""),
            })

        for path_str, lm in local_files_map.items():
            lfn = lm.get("filename", "") or lm.get("name", "")
            if lfn in seen_filenames:
                continue
            seen_filenames.add(lfn)

            model_type = lm.get("model_type", "checkpoint")
            fname_lower = lfn.lower()
            if model_type == "lora":
                if "z-image" in fname_lower or "zimage" in fname_lower or "zib-" in fname_lower or "zit-" in fname_lower:
                    cat_text = "图像LoRA"
                else:
                    cat_text = "视频LoRA"
            elif "z-image" in fname_lower or "zimage" in fname_lower:
                cat_text = "图像模型"
            else:
                cat_text = "视频模型"
            size_bytes = lm.get("size_bytes", 0)
            size_gb = size_bytes / 1024 / 1024 / 1024 if size_bytes else 0
            display_name = lm.get("name", lfn)
            desc = _get_lora_description(lfn, model_type == "lora")
            tw = _get_lora_trigger_words(lfn)

            self._model_rows.append({
                "name": display_name,
                "description": desc,
                "trigger_words": tw,
                "category": cat_text,
                "tag": "本地",
                "size_gb": size_gb,
                "status": "本地",
                "status_icon": "√",
                "status_color": "#66BB6A",
                "model_id": "",
                "local_path": path_str,
                "downloaded": True,
                "source": "local",
            })

        meta = self._load_model_meta()
        for r in self._model_rows:
            key = r.get("model_id") or r.get("name", "")
            if key in meta:
                m = meta[key]
                if m.get("description"):
                    r["description"] = m["description"]
                if m.get("tag"):
                    r["tag"] = m["tag"]

        self._apply_model_sort_and_render()

    def _populate_model_table_fallback(self):
        self._model_checkboxes = {}
        self._model_rows = []
        seen_filenames = set()

        # junction映射使自定义目录的文件在主模型目录下可见
        # 递归扫描主模型目录构建文件索引
        file_index = {}
        if self._models_dir and os.path.isdir(self._models_dir):
            for dirpath, _dirnames, filenames in os.walk(self._models_dir):
                for fn in filenames:
                    if fn not in file_index:
                        file_index[fn] = os.path.join(dirpath, fn)

        for model_id, info in LTX_MODELS.items():
            fname = info.get("file", "")
            if fname in seen_filenames:
                continue
            seen_filenames.add(fname)

            category = self._classify_model(model_id, info)
            tag = "必需" if info.get("required") else "推荐" if info.get("recommended") else "可选"
            size_gb = info["size_bytes"] / 1024 / 1024 / 1024

            # 先检查顶层，再查递归索引
            model_path = os.path.join(self._models_dir, info["file"]) if self._models_dir else ""
            exists = os.path.exists(model_path)
            if not exists and fname in file_index:
                model_path = file_index[fname]
                exists = True

            expected_bytes = info["size_bytes"]
            if info.get("is_folder", False):
                if exists and os.path.isdir(model_path):
                    folder_size = sum(f.stat().st_size for f in __import__('pathlib').Path(model_path).rglob("*") if f.is_file())
                    is_complete = folder_size > expected_bytes * 0.5
                else:
                    is_complete = False
            else:
                is_complete = exists and os.path.getsize(model_path) > expected_bytes * 0.9 if exists else False

            if is_complete:
                status = "完整"
                status_icon = "√"
                status_color = "#66BB6A"
            elif exists:
                status = "不完整"
                status_icon = "△"
                status_color = "#FFA726"
            else:
                status = "未下载"
                status_icon = "×"
                status_color = "#FF0000"

            self._model_rows.append({
                "name": info.get("file", model_id),
                "description": info.get("desc", ""),
                "trigger_words": _get_lora_trigger_words(info.get("file", "")),
                "category": category,
                "tag": tag,
                "size_gb": size_gb,
                "status": status,
                "status_icon": status_icon,
                "status_color": status_color,
                "model_id": model_id,
                "local_path": model_path if exists else "",
                "downloaded": is_complete,
                "source": "fallback",
                "is_complete": is_complete,
                "exists": exists,
            })

        scan_suffixes = {".safetensors", ".ckpt", ".pt", ".bin", ".pth"}
        hf_shard_pattern = __import__('re').compile(r'^(model|diffusion_pytorch_model|pytorch_model)-\d+-of-\d+$')
        scan_dirs = [self._models_dir]
        dirs_config = self.config.get("model_dirs", [])
        for d in dirs_config:
            p = d.get("path", "")
            if p and os.path.isdir(p) and p not in scan_dirs:
                scan_dirs.append(p)

        for scan_dir in scan_dirs:
            if not os.path.isdir(scan_dir):
                continue
            for dirpath, _dirnames, filenames in os.walk(scan_dir):
                for fn in filenames:
                    if Path(fn).suffix.lower() not in scan_suffixes:
                        continue
                    if hf_shard_pattern.match(Path(fn).stem):
                        continue
                    if fn in seen_filenames:
                        continue
                    seen_filenames.add(fn)
                    full_path = os.path.join(dirpath, fn)
                    if not os.path.isfile(full_path):
                        continue
                    try:
                        fsize = os.path.getsize(full_path)
                    except OSError:
                        fsize = 0
                    is_lora = "lora" in fn.lower() or "lora" in dirpath.lower()
                    fn_lower = fn.lower()
                    if is_lora:
                        if "z-image" in fn_lower or "zimage" in fn_lower or "zib-" in fn_lower or "zit-" in fn_lower:
                            cat_text = "图像LoRA"
                        else:
                            cat_text = "视频LoRA"
                    elif "z-image" in fn_lower or "zimage" in fn_lower:
                        cat_text = "图像模型"
                    else:
                        cat_text = "视频模型"
                    size_gb = fsize / 1024 / 1024 / 1024 if fsize else 0
                    desc = _get_lora_description(fn, is_lora)
                    tw = _get_lora_trigger_words(fn)

                    self._model_rows.append({
                        "name": fn,
                        "description": desc,
                        "trigger_words": tw,
                        "category": cat_text,
                        "tag": "本地",
                        "size_gb": size_gb,
                        "status": "本地",
                        "status_icon": "√",
                        "status_color": "#66BB6A",
                        "model_id": "",
                        "local_path": full_path,
                        "downloaded": True,
                        "source": "local_scan",
                    })

        self._apply_model_sort_and_render()

    def _update_category_counts(self):
        """更新左侧分类栏的模型数量显示"""
        if not hasattr(self, '_model_category_list') or not hasattr(self, '_model_rows'):
            return
        cat_counts = {}
        for r in self._model_rows:
            cat = r.get("category", "其他")
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        total = len(self._model_rows)
        for i in range(self._model_category_list.count()):
            item = self._model_category_list.item(i)
            key = item.data(Qt.ItemDataRole.UserRole)
            if key == "all":
                item.setText(f"全部模型 ({total})")
            else:
                count = cat_counts.get(key, 0)
                item.setText(f"{key} ({count})")

    def _toggle_select_all_models(self, state):
        checked = state == Qt.CheckState.Checked.value if isinstance(state, int) else bool(state)
        if hasattr(self, '_model_checkboxes'):
            for _, cb in self._model_checkboxes.items():
                cb.setChecked(checked)

    def _on_model_header_clicked(self, col):
        if col == 0 or col == 6:
            return
        if self._model_sort_col == col:
            self._model_sort_asc = not self._model_sort_asc
        else:
            self._model_sort_col = col
            self._model_sort_asc = True
        self._apply_model_sort_and_render()

    def _apply_model_sort_and_render(self):
        if self._model_sort_col >= 0 and hasattr(self, '_model_rows') and self._model_rows:
            col = self._model_sort_col
            asc = self._model_sort_asc

            def sort_key(row):
                # 必需模型始终置顶，推荐次之（无论排序方向）
                tag_priority = {"必需": 0, "推荐": 1}.get(row.get("tag", ""), 2)
                if col == 1:
                    return (tag_priority, row.get("name", "").lower())
                elif col == 2:
                    return (tag_priority, row.get("description", "").lower())
                elif col == 3:
                    return (tag_priority, row.get("category", "").lower())
                elif col == 4:
                    tag_order = {"必需": 0, "推荐": 1, "可选": 2, "本地": 3}
                    return (tag_priority, tag_order.get(row.get("tag", ""), 9))
                elif col == 5:
                    return (tag_priority, row.get("size_gb", 0))
                elif col == 6:
                    status_order = {"已下载": 0, "完整": 0, "本地": 0, "不完整": 1, "未下载": 2}
                    return (tag_priority, status_order.get(row.get("status", ""), 9))
                return (tag_priority, "")

            self._model_rows.sort(key=sort_key, reverse=not asc)
        elif hasattr(self, '_model_rows') and self._model_rows:
            # 无排序时，必需模型也置顶
            def default_key(row):
                tag_priority = {"必需": 0, "推荐": 1}.get(row.get("tag", ""), 2)
                return (tag_priority, row.get("name", "").lower())
            self._model_rows.sort(key=default_key)

        self._render_model_table()

    def _render_model_table(self):
        self._stop_download_progress_timer()
        self._model_detail_row = -1  # 重置详情行
        self._model_table.setRowCount(0)
        self._model_checkboxes = {}
        if not hasattr(self, '_model_rows'):
            return

        # 分类筛选
        filtered_rows = self._model_rows
        if hasattr(self, '_model_category_filter') and self._model_category_filter != "all":
            cat = self._model_category_filter
            filtered_rows = [r for r in self._model_rows if r.get("category", "") == cat]

        # 更新分类栏计数
        self._update_category_counts()

        base_labels = ["", "模型名称", "描述", "分类", "标签", "大小", "状态", "操作"]
        if 1 <= self._model_sort_col <= 6:
            arrow = " ▲" if self._model_sort_asc else " ▼"
            base_labels[self._model_sort_col] = base_labels[self._model_sort_col] + arrow
        self._model_table.setHorizontalHeaderLabels(base_labels)

        for row, r in enumerate(filtered_rows):
            self._model_table.insertRow(row)

            cb = QCheckBox()
            cb_widget = QWidget()
            cb_widget.setStyleSheet("background: transparent;")
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.addWidget(cb)
            cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self._model_table.setCellWidget(row, 0, cb_widget)
            mid = r.get("model_id", "")
            self._model_checkboxes[row] = cb

            name_item = QTableWidgetItem(r.get("name", ""))
            self._model_table.setItem(row, 1, name_item)

            # 描述中融入触发词信息
            desc_text = r.get("description", "")
            tw = r.get("trigger_words", "")
            if tw:
                desc_text = f"[触发词: {tw}] {desc_text}" if desc_text else f"[触发词: {tw}]"
            desc_item = QTableWidgetItem(desc_text)
            desc_item.setForeground(QColor("#999999"))
            if desc_text:
                desc_item.setToolTip(desc_text)
            self._model_table.setItem(row, 2, desc_item)

            self._model_table.setItem(row, 3, QTableWidgetItem(r.get("category", "")))

            tag_item = QTableWidgetItem(r.get("tag", ""))
            if r.get("tag") == "必需":
                tag_item.setForeground(QColor("#FF0000"))
            elif r.get("tag") == "推荐":
                tag_item.setForeground(QColor("#FF9800"))
            self._model_table.setItem(row, 4, tag_item)

            size_gb = r.get("size_gb", 0)
            self._model_table.setItem(row, 5, QTableWidgetItem(f"{size_gb:.1f} GB" if size_gb > 0 else ""))

            status_text = f"{r.get('status_icon', '')} {r.get('status', '')}"
            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(QColor(r.get("status_color", "#DDDDDD")))
            self._model_table.setItem(row, 6, status_item)

            ops_widget = QWidget()
            ops_widget.setStyleSheet("background: transparent;")
            ops_layout = QHBoxLayout(ops_widget)
            ops_layout.setContentsMargins(4, 2, 4, 2)
            ops_layout.setSpacing(4)

            downloaded = r.get("downloaded", False)
            source = r.get("source", "")

            if source in ("registry", "fallback"):
                if not downloaded:
                    dl_btn = QPushButton("📥 下载")
                    dl_btn.setStyleSheet("QPushButton { background-color: #2E7D32; color: white; border: none; border-radius: 3px; padding: 3px 8px; font-size: 10px; } QPushButton:hover { background-color: #388E3C; } QPushButton:pressed { background-color: #1B5E20; }")
                    dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    dl_btn.clicked.connect(lambda checked, m=mid: self._download_model(m))
                    ops_layout.addWidget(dl_btn)

                if downloaded and r.get("local_path"):
                    rm_btn = QPushButton("🗑 删除")
                    rm_btn.setStyleSheet("QPushButton { background-color: #1565C0; color: white; border: none; border-radius: 3px; padding: 3px 8px; font-size: 10px; } QPushButton:hover { background-color: #1976D2; } QPushButton:pressed { background-color: #0D47A1; }")
                    rm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    lp = r.get("local_path", "")
                    rm_btn.clicked.connect(lambda checked, p=lp: self._delete_local_model_file(p))
                    ops_layout.addWidget(rm_btn)
            else:
                rm_btn = QPushButton("🗑 删除")
                rm_btn.setStyleSheet("QPushButton { background-color: #1565C0; color: white; border: none; border-radius: 3px; padding: 3px 8px; font-size: 10px; } QPushButton:hover { background-color: #1976D2; } QPushButton:pressed { background-color: #0D47A1; }")
                rm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                lp = r.get("local_path", "")
                rm_btn.clicked.connect(lambda checked, p=lp: self._delete_local_model_file(p))
                ops_layout.addWidget(rm_btn)

            ops_layout.addStretch()
            self._model_table.setCellWidget(row, 7, ops_widget)

        for r in range(self._model_table.rowCount()):
            self._model_table.setRowHeight(r, 32)

        # 必需/推荐模型行高亮背景
        required_bg = QColor(204, 0, 0, 25)
        recommended_bg = QColor(255, 152, 0, 20)
        self._required_model_rows = set()
        self._recommended_model_rows = set()
        for row_idx, r in enumerate(filtered_rows):
            if r.get("tag") == "必需":
                self._required_model_rows.add(row_idx)
                for c in range(self._model_table.columnCount()):
                    item = self._model_table.item(row_idx, c)
                    if item:
                        item.setBackground(required_bg)
            elif r.get("tag") == "推荐":
                self._recommended_model_rows.add(row_idx)
                for c in range(self._model_table.columnCount()):
                    item = self._model_table.item(row_idx, c)
                    if item:
                        item.setBackground(recommended_bg)

        if hasattr(self, '_download_procs') and self._download_procs:
            self._start_download_progress_timer()

    def _on_model_row_hover(self, row, col):
        # 跳过详情行
        if row >= 0:
            item0 = self._model_table.item(row, 0)
            if item0 and item0.data(Qt.ItemDataRole.UserRole) == "detail":
                return
        if row == self._hover_row:
            return
        old = self._hover_row
        self._hover_row = row
        hover_bg = QColor("#2A2A2E")
        required_bg = QColor(204, 0, 0, 25)
        clear_bg = QColor("transparent")
        for c in range(self._model_table.columnCount()):
            if old >= 0:
                item = self._model_table.item(old, c)
                if item:
                    # 必需模型行恢复高亮背景
                    item.setBackground(required_bg if old in getattr(self, '_required_model_rows', set()) else clear_bg)
            if row >= 0:
                item = self._model_table.item(row, c)
                if item:
                    item.setBackground(hover_bg)

    def eventFilter(self, obj, event):
        if obj is getattr(self, '_model_table', None):
            if event.type() == event.Type.Leave:
                if self._hover_row >= 0:
                    old = self._hover_row
                    self._hover_row = -1
                    required_bg = QColor(204, 0, 0, 25)
                    clear_bg = QColor("transparent")
                    for c in range(self._model_table.columnCount()):
                        item = self._model_table.item(old, c)
                        if item:
                            item.setBackground(required_bg if old in getattr(self, '_required_model_rows', set()) else clear_bg)
        return super().eventFilter(obj, event)

    def _delete_local_model_file(self, file_path):
        if not file_path or not os.path.exists(file_path):
            self._log(f"⚠ 文件不存在: {file_path}", "warn")
            return

        is_dir = os.path.isdir(file_path)
        size_str = ""
        try:
            if is_dir:
                total = 0
                for dp, dn, fns in os.walk(file_path):
                    for fn in fns:
                        try:
                            total += os.path.getsize(os.path.join(dp, fn))
                        except OSError:
                            pass
                size_str = f" ({total/1024/1024/1024:.1f}GB)"
            else:
                size_str = f" ({os.path.getsize(file_path)/1024/1024/1024:.1f}GB)"
        except OSError:
            pass

        name = os.path.basename(file_path)
        msg = QMessageBox(self)
        msg.setWindowTitle("删除模型文件")
        msg.setText(f"确定要删除此模型文件吗？\n\n{name}{size_str}\n\n此操作不可恢复！")
        msg.setIcon(QMessageBox.Icon.Question)
        btn_yes = msg.addButton("是，删除", QMessageBox.ButtonRole.YesRole)
        btn_no = msg.addButton("否，取消", QMessageBox.ButtonRole.NoRole)
        msg.setDefaultButton(btn_no)
        msg.exec()
        if msg.clickedButton() != btn_yes:
            return

        try:
            if is_dir:
                shutil.rmtree(file_path, ignore_errors=True)
            else:
                os.remove(file_path)
            self._log(f"√ 已删除: {name}", "ok")
            self._refresh_model_status()
        except Exception as e:
            self._log(f"⚠ 删除失败: {e}", "warn")

    def _get_meta_file(self):
        return os.path.join(self._models_dir, ".models_metadata.json")

    def _load_model_meta(self):
        try:
            path = self._get_meta_file()
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_model_meta(self, data):
        try:
            path = self._get_meta_file()
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _batch_edit_models(self):
        selected = []
        for row_idx, cb in self._model_checkboxes.items():
            if cb.isChecked() and 0 <= row_idx < len(self._model_rows):
                selected.append(self._model_rows[row_idx])

        if not selected:
            QMessageBox.information(self, "批量编辑", "请先勾选要编辑的模型")
            return

        meta = self._load_model_meta()

        dlg = QDialog(self)
        dlg.setWindowTitle("批量编辑模型信息")
        dlg.resize(700, 500)
        dlg.setStyleSheet("QDialog { background-color: #1A1A1A; } QLabel { color: #DDDDDD; font-size: 12px; }")

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 16, 16, 16)

        hint = QLabel("修改描述和标签，点击保存后生效。留空保留原值。")
        hint.setStyleSheet("color: #888888; font-size: 11px; margin-bottom: 8px;")
        layout.addWidget(hint)

        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["模型名称", "描述", "标签"])
        table.setRowCount(len(selected))
        table.setStyleSheet("""
            QTableWidget { background-color: #111113; border: 1px solid #333333; color: #DDDDDD; font-size: 12px; }
            QTableWidget::item { padding: 4px 6px; }
            QHeaderView::section { background-color: #1A1A1A; color: #AAAAAA; border-bottom: 2px solid #333333; padding: 6px 8px; font-size: 11px; }
        """)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(0, 200)
        table.setColumnWidth(2, 100)

        for i, r in enumerate(selected):
            key = r.get("model_id") or r.get("name", "")
            name_item = QTableWidgetItem(r.get("name", key))
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(i, 0, name_item)

            cur_meta = meta.get(key, {})
            desc_item = QTableWidgetItem(cur_meta.get("description", r.get("description", "")))
            table.setItem(i, 1, desc_item)

            tag_item = QTableWidgetItem(cur_meta.get("tag", r.get("tag", "")))
            table.setItem(i, 2, tag_item)

            table.setRowHeight(i, 30)

        layout.addWidget(table)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("✖ 取消")
        cancel_btn.setStyleSheet("QPushButton { background-color: #333333; color: #DDDDDD; border: 1px solid #555555; border-radius: 6px; padding: 8px 20px; font-size: 13px; } QPushButton:hover { background-color: #444444; }")
        cancel_btn.clicked.connect(dlg.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("💾 保存")
        save_btn.setStyleSheet("QPushButton { background-color: #CC0000; color: white; border: none; border-radius: 6px; padding: 8px 20px; font-size: 13px; font-weight: bold; } QPushButton:hover { background-color: #FF0000; } QPushButton:pressed { background-color: #DD0000; }")
        save_btn.clicked.connect(dlg.accept)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        changed = 0
        for i, r in enumerate(selected):
            key = r.get("model_id") or r.get("name", "")
            if not key:
                continue
            desc_item = table.item(i, 1)
            tag_item = table.item(i, 2)
            new_desc = desc_item.text().strip() if desc_item else ""
            new_tag = tag_item.text().strip() if tag_item else ""

            if new_desc or new_tag:
                meta[key] = {"description": new_desc, "tag": new_tag}
                changed += 1
            elif key in meta:
                del meta[key]
                changed += 1

        if changed:
            self._save_model_meta(meta)
            self._log(f"✎ 已更新 {changed} 个模型的描述/标签", "ok")
            self._refresh_model_status()

    def _populate_model_dir_combo(self):
        if not hasattr(self, '_model_dir_combo'):
            return
        self._model_dir_combo.blockSignals(True)
        self._model_dir_combo.clear()
        dirs_config = self.config.get("model_dirs", [])
        if not dirs_config:
            dirs_config = [{"path": self._models_dir, "label": "默认"}]
        for i, d in enumerate(dirs_config):
            path = d.get("path", "")
            label = d.get("label", "")
            is_default = (i == 0 and label == "默认")
            if is_default:
                display = f"[系统默认] {path}"
            else:
                display = f"[{label}] {path}"
            self._model_dir_combo.addItem(display)
        self._model_dir_combo.blockSignals(False)
        if self._model_dir_combo.count() > 0:
            self._model_dir_combo.setCurrentIndex(0)
        self._update_remove_dir_btn_state()

    def _update_remove_dir_btn_state(self):
        if not hasattr(self, '_remove_dir_btn') or not hasattr(self, '_model_dir_combo'):
            return
        idx = self._model_dir_combo.currentIndex()
        dirs_config = self.config.get("model_dirs", [])
        if not dirs_config:
            dirs_config = [{"path": self._models_dir, "label": "默认"}]
        is_default = (idx == 0 and dirs_config and dirs_config[0].get("label") == "默认")
        self._remove_dir_btn.setEnabled(not is_default)
        self._remove_dir_btn.setToolTip("系统默认目录不可删除" if is_default else "删除选中的自定义目录")

    def _save_model_dirs(self, dirs_config):
        self.config.set("model_dirs", dirs_config)
        self.config.save()

    # ── Junction映射：将自定义模型目录映射到主模型目录下 ──

    def _get_junction_name(self, source_dir):
        """根据源目录路径生成junction名称，避免冲突"""
        base = os.path.basename(source_dir.rstrip("\\/"))
        if not base:
            base = "custom"
        name = base
        idx = 2
        while os.path.exists(os.path.join(self._models_dir, name)):
            # 如果已存在同名junction且指向同一目录，直接用
            existing = os.path.join(self._models_dir, name)
            try:
                target = os.readlink(existing) if os.path.islink(existing) else ""
                if not target:
                    # 可能是junction，用Windows API检查
                    import ctypes
                    buf = ctypes.create_unicode_buffer(260)
                    ctypes.windll.kernel32.GetFinalPathNameByHandleW
                    # 简单方式：检查是否是reparse point
                    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(existing))
                    if attrs != 0xFFFFFFFF and (attrs & 0x400):
                        # 是junction/reparse point，检查目标
                        try:
                            real = os.path.realpath(existing)
                            if os.path.normpath(real) == os.path.normpath(source_dir):
                                return name
                        except Exception:
                            pass
                elif os.path.normpath(target) == os.path.normpath(source_dir):
                    return name
            except Exception:
                pass
            name = f"{base}_{idx}"
            idx += 1
        return name

    def _create_model_junction(self, source_dir):
        """在主模型目录下创建junction映射到source_dir"""
        if not self._models_dir or not os.path.isdir(self._models_dir):
            return False
        if not os.path.isdir(source_dir):
            return False
        # 如果源目录已经在主模型目录下，不需要映射
        try:
            if os.path.normpath(source_dir).startswith(os.path.normpath(self._models_dir)):
                return True
        except Exception:
            pass
        junction_name = self._get_junction_name(source_dir)
        junction_path = os.path.join(self._models_dir, junction_name)
        # 如果junction已存在且指向正确目标，跳过
        if os.path.exists(junction_path):
            try:
                real = os.path.realpath(junction_path)
                if os.path.normpath(real) == os.path.normpath(source_dir):
                    return True
            except Exception:
                pass
            # 已存在但不指向正确目标，先删除
            try:
                os.rmdir(junction_path)
            except Exception:
                return False
        # 创建junction：用引号包裹路径避免中文/空格编码问题
        try:
            import subprocess
            cmd_str = f'mklink /J "{junction_path}" "{source_dir}"'
            result = subprocess.run(
                cmd_str,
                capture_output=True, timeout=10,
                shell=True
            )
            if result.returncode == 0:
                self._log(f"  已创建映射: {junction_name} → {source_dir}", "ok")
                return True
            else:
                err_msg = result.stderr.decode('gbk', errors='replace').strip() if result.stderr else f"返回码{result.returncode}"
                self._log(f"  映射创建失败: {err_msg}", "warn")
                return False
        except Exception as e:
            self._log(f"  映射创建异常: {e}", "warn")
            return False

    def _remove_model_junction(self, source_dir):
        """移除主模型目录下指向source_dir的junction"""
        if not self._models_dir or not os.path.isdir(self._models_dir):
            return
        try:
            for entry in os.listdir(self._models_dir):
                entry_path = os.path.join(self._models_dir, entry)
                # 检查是否是junction/reparse point
                import ctypes
                attrs = ctypes.windll.kernel32.GetFileAttributesW(str(entry_path))
                if attrs != 0xFFFFFFFF and (attrs & 0x400):
                    # 是junction，检查目标
                    try:
                        real = os.path.realpath(entry_path)
                        if os.path.normpath(real) == os.path.normpath(source_dir):
                            os.rmdir(entry_path)
                            self._log(f"  已移除映射: {entry}", "ok")
                            return
                    except Exception:
                        pass
        except Exception:
            pass

    def _sync_model_junctions(self):
        """启动时同步junction映射：确保配置中的自定义目录都有映射"""
        if not self._models_dir or not os.path.isdir(self._models_dir):
            return
        dirs_config = self.config.get("model_dirs", [])
        for d in dirs_config:
            path = d.get("path", "")
            if path and os.path.isdir(path) and path != self._models_dir:
                self._create_model_junction(path)
        # 清理指向不存在目录的junction
        try:
            import ctypes
            for entry in os.listdir(self._models_dir):
                entry_path = os.path.join(self._models_dir, entry)
                attrs = ctypes.windll.kernel32.GetFileAttributesW(str(entry_path))
                if attrs != 0xFFFFFFFF and (attrs & 0x400):
                    # 是junction，检查目标是否还存在
                    try:
                        real = os.path.realpath(entry_path)
                        if not os.path.isdir(real):
                            os.rmdir(entry_path)
                            self._log(f"  已清理失效映射: {entry}", "ok")
                    except Exception:
                        try:
                            os.rmdir(entry_path)
                        except Exception:
                            pass
        except Exception:
            pass

    def _remove_selected_model_dir(self):
        if not hasattr(self, '_model_dir_combo'):
            return
        idx = self._model_dir_combo.currentIndex()
        if idx < 0:
            return
        dirs_config = self.config.get("model_dirs", [])
        if not dirs_config:
            return
        if idx == 0 and dirs_config[0].get("label") == "默认":
            self._log("⚠ 系统默认目录不可移除", "warn")
            return
        removed = dirs_config.pop(idx)
        removed_path = removed.get("path", "")
        self._save_model_dirs(dirs_config)
        # 移除对应的junction映射
        if removed_path:
            self._remove_model_junction(removed_path)
        self._populate_model_dir_combo()
        self._notify_backend_dirs_changed()
        self._refresh_model_status()
        self._log(f"√ 已移除目录: {removed_path}", "ok")

    def _add_model_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择模型目录")
        if not dir_path:
            return
        dir_name = os.path.basename(dir_path)
        label = self._infer_tag(dir_path) or dir_name
        dirs_config = self.config.get("model_dirs", [])
        if not dirs_config:
            dirs_config = [{"path": self._models_dir, "label": "默认"}]
        dirs_config.append({"path": dir_path, "label": label})
        self._save_model_dirs(dirs_config)
        # 创建junction映射到主模型目录
        self._create_model_junction(dir_path)
        self._populate_model_dir_combo()
        self._notify_backend_dirs_changed()
        self._refresh_model_status()
        self._log(f"√ 已添加目录 [{label}] {dir_path}", "ok")

    def _remove_model_dir_by_path(self, path):
        dirs_config = self.config.get("model_dirs", [])
        if not dirs_config:
            return
        new_config = [d for d in dirs_config if d.get("path") != path]
        if len(new_config) == len(dirs_config):
            return
        self._save_model_dirs(new_config)
        self._populate_model_dir_combo()
        self._notify_backend_dirs_changed()

    def _notify_backend_dirs_changed(self):
        try:
            import httpx
            with httpx.Client(timeout=httpx.Timeout(5.0)) as client:
                client.post(f"http://127.0.0.1:{self._backend_port}/api/models/registry/refresh-dirs")
        except Exception:
            pass

    def _remove_model_dir(self):
        pass

    def _batch_download_models(self):
        selected_mids = []
        for row_idx, cb in self._model_checkboxes.items():
            if cb.isChecked() and 0 <= row_idx < len(self._model_rows):
                mid = self._model_rows[row_idx].get("model_id", "")
                if mid:
                    selected_mids.append(mid)
        if not selected_mids:
            self._log("⚠ 请先勾选要下载的模型", "warn")
            return
        for mid in selected_mids:
            self._download_model(mid)

    def _sync_model_updates(self):
        self._log("正在同步模型注册表...", "info")
        try:
            import httpx
            with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
                try:
                    client.post(f"http://127.0.0.1:{self._backend_port}/api/models/registry/refresh-dirs")
                except Exception:
                    pass
                resp = client.post(f"http://127.0.0.1:{self._backend_port}/api/models/registry/sync")
                data = resp.json()
            if data.get("success"):
                added = data.get("added", 0)
                updated = data.get("updated", 0)
                if added > 0 or updated > 0:
                    self._log(f"√ 同步完成：新增 {added} 个，更新 {updated} 个模型", "ok")
                else:
                    self._log("√ 已同步，暂无新模型", "ok")
            elif data.get("error"):
                if data.get("local_refreshed"):
                    self._log(f"△ 远程同步失败: {data['error']}，已刷新本地列表", "warn")
                else:
                    self._log(f"△ 同步失败: {data['error']}", "warn")
            else:
                self._log("√ 已刷新模型列表", "ok")
        except Exception as e:
            self._log(f"△ 同步异常: {e}，已刷新本地列表", "warn")
        self._refresh_model_status()

    def _build_update_page(self):
        self._active_update_source = "auto"
        self._ver_race_errors = {}

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        current_card = QFrame()
        current_card.setStyleSheet(
            "QFrame { background-color: #1A1A1A; border: 1px solid #333333; border-radius: 8px; }"
        )
        current_card.setContentsMargins(0, 0, 0, 0)
        cc_layout = QVBoxLayout(current_card)
        cc_layout.setContentsMargins(16, 10, 16, 10)
        cc_layout.setSpacing(4)
        cc_top = QHBoxLayout()
        cc_top.setSpacing(10)
        cc_ver = QLabel(f"当前版本  v{VERSION}")
        cc_ver.setStyleSheet("font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif; font-size: 11pt; font-weight: bold; color: #DDDDDD; border: none;")
        cc_top.addWidget(cc_ver)
        cc_top.addStretch()

        self._ver_source_combo = QComboBox()
        self._ver_source_combo.setFixedWidth(110)
        self._ver_source_combo.setStyleSheet(
            "QComboBox { background-color: #252525; color: #AAAAAA; border: 1px solid #333333; border-radius: 4px; font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 8pt; padding: 2px 6px; }"
            "QComboBox::drop-down { border: none; }"
            "QComboBox QAbstractItemView { background-color: #252525; color: #AAAAAA; selection-background-color: #CC0000; }"
        )
        self._ver_source_combo.addItem("自动竞速", "auto")
        for key, src in UPDATE_SOURCES.items():
            self._ver_source_combo.addItem(src["name"], key)
        self._ver_source_combo.setCurrentIndex(0)
        self._ver_source_combo.currentIndexChanged.connect(self._on_update_source_changed)
        cc_top.addWidget(self._ver_source_combo)

        btn_check_remote = QPushButton("检查更新")
        btn_check_remote.setFixedSize(100, 30)
        btn_check_remote.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_check_remote.setStyleSheet(
            "QPushButton { background-color: #2E7D32; color: #fff; border: none; border-radius: 6px; font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 9pt; font-weight: bold; }"
            "QPushButton:hover { background-color: #388E3C; }"
        )
        btn_check_remote.clicked.connect(self._check_remote_versions)
        cc_top.addWidget(btn_check_remote)
        cc_layout.addLayout(cc_top)
        self._ver_current_desc_label = QLabel("")
        self._ver_current_desc_label.setWordWrap(True)
        self._ver_current_desc_label.setStyleSheet("font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 9pt; color: #AAAAAA; border: none;")
        cc_layout.addWidget(self._ver_current_desc_label)
        cc_detail_row = QHBoxLayout()
        cc_detail_row.setSpacing(16)
        self._ver_current_build_label = QLabel("")
        self._ver_current_build_label.setStyleSheet("font-size: 8pt; color: #666; border: none;")
        cc_detail_row.addWidget(self._ver_current_build_label)
        self._ver_current_commit_label = QLabel("")
        self._ver_current_commit_label.setStyleSheet("font-family: Consolas; font-size: 8pt; color: #666; border: none;")
        cc_detail_row.addWidget(self._ver_current_commit_label)
        cc_detail_row.addStretch()
        cc_layout.addLayout(cc_detail_row)
        layout.addWidget(current_card)

        tab_bar = QFrame()
        tab_bar.setStyleSheet("background-color: #1A1A1A; border: none;")
        tab_bar.setFixedHeight(44)
        tab_layout = QHBoxLayout(tab_bar)
        tab_layout.setContentsMargins(10, 5, 10, 5)
        tab_layout.setSpacing(4)

        self._ver_tab_stable_btn = QPushButton("软件版本")
        self._ver_tab_stable_btn.setFixedSize(100, 32)
        self._ver_tab_stable_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ver_tab_stable_btn.setStyleSheet(
            "QPushButton { background-color: #CC0000; color: #FFFFFF; border: none; border-radius: 6px; font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 9pt; font-weight: bold; }"
            "QPushButton:hover { background-color: #FF0000; }"
            "QPushButton:pressed { background-color: #DD0000; }"
        )
        self._ver_tab_stable_btn.clicked.connect(lambda: self._switch_ver_tab("stable"))
        tab_layout.addWidget(self._ver_tab_stable_btn)

        self._ver_tab_git_btn = QPushButton("开发动态")
        self._ver_tab_git_btn.setFixedSize(100, 32)
        self._ver_tab_git_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ver_tab_git_btn.setStyleSheet(
            "QPushButton { background-color: #333; color: #AAAAAA; border: 1px solid #444; border-radius: 6px; font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 9pt; font-weight: bold; }"
            "QPushButton:hover { background-color: #444; color: #ddd; }"
        )
        self._ver_tab_git_btn.clicked.connect(lambda: self._switch_ver_tab("git"))
        tab_layout.addWidget(self._ver_tab_git_btn)

        tab_layout.addStretch()

        self._ver_status_label = QLabel("")
        self._ver_status_label.setStyleSheet("font-size: 8pt; color: #888; border: none;")
        tab_layout.addWidget(self._ver_status_label)

        self._ver_expand_switch = ToggleSwitch(label="全部展开", checked=True, checked_color="#CC0000")
        self._ver_expand_switch.toggled.connect(self._toggle_expand_all)
        tab_layout.addWidget(self._ver_expand_switch)

        layout.addWidget(tab_bar)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet(
            "QScrollArea { background-color: #111113; border: none; }"
            "QScrollBar:vertical { background-color: #111113; width: 8px; border: none; }"
            "QScrollBar::handle:vertical { background-color: #333; border-radius: 4px; min-height: 30px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
        )
        self._ver_scroll_content = QWidget()
        self._ver_scroll_layout = QVBoxLayout(self._ver_scroll_content)
        self._ver_scroll_layout.setContentsMargins(10, 6, 10, 6)
        self._ver_scroll_layout.setSpacing(4)
        self._ver_scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll_area.setWidget(self._ver_scroll_content)
        layout.addWidget(scroll_area, stretch=1)

        self._ver_stable_data = []
        self._ver_git_data = []
        self._ver_current_version = VERSION
        self._ver_active_tab = "stable"
        self._ver_info_text = "点击「检查更新」查看最新版本"
        self._ver_expanded = True
        self._ver_card_expanded = {}
        self._ver_detail_page_size = 10
        self._ver_list_page_size = 20
        self._ver_rendered_count = 0
        self._ver_cache_file = os.path.join(self._app_dir, "data", "update_cache.json") if self._app_dir else ""
        self._ver_race_winner = ""
        self._latest_version = ""
        self._latest_info = None

        self._update_current_version_card()
        self._ver_status_label.setText("加载中...")
        self._ver_cache_check_scheduled = False

        return page

    def _switch_ver_tab(self, tab):
        if tab == self._ver_active_tab:
            return
        self._ver_active_tab = tab
        active_style = (
            "QPushButton { background-color: #CC0000; color: #FFFFFF; border: none; border-radius: 6px; font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 9pt; font-weight: bold; }"
            "QPushButton:hover { background-color: #FF0000; }"
            "QPushButton:pressed { background-color: #DD0000; }"
        )
        inactive_style = (
            "QPushButton { background-color: #333; color: #AAAAAA; border: 1px solid #444; border-radius: 6px; font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 9pt; font-weight: bold; }"
            "QPushButton:hover { background-color: #444; color: #ddd; }"
        )
        if tab == "stable":
            self._ver_tab_stable_btn.setStyleSheet(active_style)
            self._ver_tab_git_btn.setStyleSheet(inactive_style)
        else:
            self._ver_tab_git_btn.setStyleSheet(active_style)
            self._ver_tab_stable_btn.setStyleSheet(inactive_style)
        # 切换Tab时重置卡片展开状态，同步展开开关
        self._ver_card_expanded.clear()
        if self._ver_expanded:
            for v in self._ver_stable_data:
                self._ver_card_expanded[v["version"]] = True
        if self._ver_expand_switch:
            self._ver_expand_switch._checked = self._ver_expanded
            self._ver_expand_switch.update()
        self._render_active_tab()

    def _render_active_tab(self):
        if self._ver_scroll_content is None:
            return
        if getattr(self, '_ver_rendering', False):
            return
        self._ver_rendering = True
        try:
            old_widgets = []
            while self._ver_scroll_layout.count():
                item = self._ver_scroll_layout.takeAt(0)
                w = item.widget()
                if w:
                    old_widgets.append(w)
                else:
                    sub_layout = item.layout()
                    if sub_layout:
                        while sub_layout.count():
                            sub = sub_layout.takeAt(0)
                            if sub.widget():
                                old_widgets.append(sub.widget())
            for w in old_widgets:
                w.setParent(None)
                w.deleteLater()
            self._ver_rendered_count = 0
            if self._ver_active_tab == "stable":
                self._render_stable_tab()
            else:
                self._render_git_tab()
        finally:
            self._ver_rendering = False

    def _toggle_expand_all(self, checked):
        self._ver_expanded = checked
        # 同步所有卡片的展开状态，但列表模式下当前版本始终展开
        for v in self._ver_stable_data:
            self._ver_card_expanded[v["version"]] = self._ver_expanded
        if not self._ver_expanded:
            self._ver_card_expanded[self._ver_current_version] = True
        self._render_active_tab()

    def _render_stable_tab(self):
        current_v = next((v for v in self._ver_stable_data if v["version"] == self._ver_current_version), None)
        if current_v:
            # 当前版本始终默认展开
            is_current_expanded = self._ver_card_expanded.get(self._ver_current_version, True)
            card_bg = "#1a2e1a"
            border_color = "#2a4a2a"
            current_card = QFrame()
            current_card.setProperty("card_bg", card_bg)
            current_card.setCursor(Qt.CursorShape.PointingHandCursor)
            current_card.setStyleSheet(
                f"background-color: {card_bg}; border: 1px solid {border_color}; border-radius: 8px; border-left: 3px solid #4CAF50;"
            )
            cc_layout = QVBoxLayout(current_card)
            cc_layout.setContentsMargins(12, 8, 12, 6)
            cc_layout.setSpacing(4)

            cc_row = QHBoxLayout()
            cc_row.setSpacing(8)
            ver_label = QLabel(f"v{self._ver_current_version}")
            ver_label.setStyleSheet("font-family: Consolas; font-size: 12pt; font-weight: bold; color: #4CAF50; border: none;")
            cc_row.addWidget(ver_label)
            cc_row.addStretch()
            status_label = QLabel("● 当前版本")
            status_label.setStyleSheet("font-size: 9pt; color: #4CAF50; border: none; font-weight: bold;")
            cc_row.addWidget(status_label)
            has_update = self._latest_version and self._latest_version != VERSION
            if has_update:
                dl_btn = QPushButton("📥 下载更新")
                dl_btn.setFixedSize(95, 24)
                dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                dl_btn.setStyleSheet(
                    "QPushButton { background-color: #1565C0; color: #fff; border: none; border-radius: 4px; font-size: 8pt; font-weight: bold; }"
                    "QPushButton:hover { background-color: #1976D2; }"
                )
                dl_btn.clicked.connect(self._on_download_update)
                cc_row.addWidget(dl_btn)

            release_btn = QPushButton("🔗 Release")
            release_btn.setFixedSize(80, 24)
            release_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            release_btn.setStyleSheet(
                "QPushButton { background-color: #333; color: #aaa; border: 1px solid #444; border-radius: 4px; font-size: 8pt; font-weight: bold; }"
                "QPushButton:hover { background-color: #444; color: #fff; }"
            )
            release_btn.clicked.connect(self._open_release_page)
            cc_row.addWidget(release_btn)
            cc_layout.addLayout(cc_row)

            status_text = QLabel(self._ver_info_text)
            status_text.setWordWrap(True)
            status_text.setStyleSheet("font-size: 9pt; color: #8aaa8a; border: none;")
            cc_layout.addWidget(status_text)

            if is_current_expanded:
                changes = current_v.get("changes", [])
                if changes:
                    detail_frame = QFrame()
                    detail_frame.setStyleSheet("background-color: #0d2d1a; border-top: 1px solid #1a4a2a; border-radius: 0;")
                    detail_layout = QVBoxLayout(detail_frame)
                    detail_layout.setContentsMargins(12, 4, 12, 4)
                    detail_layout.setSpacing(2)
                    for ch in changes:
                        lbl = QLabel(f"· {ch}")
                        lbl.setWordWrap(True)
                        lbl.setStyleSheet("font-size: 8pt; color: #8a8; border: none;")
                        detail_layout.addWidget(lbl)
                    cc_layout.addWidget(detail_frame)
                git_commit = current_v.get("git_commit", "")
                build_time = current_v.get("build_time", "")
                if git_commit or build_time:
                    meta_parts = []
                    if git_commit:
                        meta_parts.append(f"commit: {git_commit}")
                    if build_time:
                        meta_parts.append(f"构建: {build_time}")
                    meta_label = QLabel("  ".join(meta_parts))
                    meta_label.setStyleSheet("font-size: 8pt; color: #5a7a5a; border: none;")
                    cc_layout.addWidget(meta_label)

            current_card.clicked_data = current_v
            current_card.mousePressEvent = lambda e, d=current_v: self._on_current_card_click(None, d)
            self._ver_scroll_layout.addWidget(current_card)

        self._ver_rendered_count = 0
        self._render_stable_versions(self._ver_stable_data, self._ver_current_version)

    def _on_current_card_click(self, card, data):
        ver = data.get("version", self._ver_current_version)
        self._ver_card_expanded[ver] = not self._ver_card_expanded.get(ver, self._ver_expanded)
        self._render_active_tab()

    def _on_version_card_click(self, card, data):
        self._toggle_card_detail(card, data, "stable")

    def _render_git_tab(self):
        git_header = QFrame()
        git_header.setStyleSheet("background-color: #111113; border: none;")
        git_header_layout = QHBoxLayout(git_header)
        git_header_layout.setContentsMargins(4, 6, 4, 2)
        git_title = QLabel("🔀 Git版本历史")
        git_title.setStyleSheet("font-size: 9pt; font-weight: bold; color: #42A5F5; border: none;")
        git_header_layout.addWidget(git_title)
        git_header_layout.addStretch()
        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.setFixedSize(68, 28)
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setStyleSheet(
            "QPushButton { background-color: #2a2a2a; color: #aaa; border: none; border-radius: 4px; font-size: 8pt; padding: 2px 4px; }"
            "QPushButton:hover { background-color: #3a3a3a; }"
        )
        refresh_btn.clicked.connect(self._refresh_git_history)
        git_header_layout.addWidget(refresh_btn)
        self._ver_scroll_layout.addWidget(git_header)
        if not self._ver_git_data:
            # Try local git history first
            local_commits = self._get_git_history(500)
            if local_commits:
                self._ver_git_data = local_commits
                self._render_git_history(self._ver_git_data)
            else:
                loading_lbl = QLabel("正在加载开发动态...")
                loading_lbl.setStyleSheet("font-size: 9pt; color: #888; border: none;")
                loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._ver_scroll_layout.addWidget(loading_lbl)
                if not getattr(self, '_commits_race_procs', None):
                    QTimer.singleShot(500, self._fetch_remote_commits)
        else:
            self._render_git_history(self._ver_git_data)

    def _toggle_card_detail(self, card, data, card_type):
        ver = data.get("version", "")
        is_expanded = self._ver_card_expanded.get(ver, False)
        self._ver_card_expanded[ver] = not is_expanded

        if card is None:
            for i in range(self._ver_scroll_layout.count()):
                w = self._ver_scroll_layout.itemAt(i).widget()
                if w and hasattr(w, 'clicked_data') and w.clicked_data is data:
                    card = w
                    break
            if card is None:
                return
        detail_widget = card.findChild(QWidget, "_detail")
        if detail_widget is not None:
            detail_widget.setParent(None)
            detail_widget.deleteLater()
            return
        card_bg = card.property("card_bg") or "#161616"
        detail = QFrame()
        detail.setObjectName("_detail")
        detail.setStyleSheet(f"background-color: {card_bg}; border: none;")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(14, 4, 14, 6)
        detail_layout.setSpacing(2)
        if card_type == "stable":
            git_commit = data.get("git_commit", "")
            if git_commit:
                lbl = QLabel(f"🔗 commit: {git_commit}")
                lbl.setStyleSheet("font-family: Consolas; font-size: 9pt; color: #555; border: none;")
                detail_layout.addWidget(lbl)
            changes = data.get("changes", [])
            if changes:
                for ch in changes:
                    lbl = QLabel(f"· {ch}")
                    lbl.setWordWrap(True)
                    lbl.setStyleSheet("font-size: 8pt; color: #777; border: none;")
                    detail_layout.addWidget(lbl)
            else:
                lbl = QLabel("暂无修改记录")
                lbl.setStyleSheet("font-size: 8pt; color: #3a3a3a; border: none;")
                detail_layout.addWidget(lbl)
        else:
            message = data.get("message", "")
            msg_lines = message.split("\n") if message else []
            for line in msg_lines:
                line = line.strip()
                if line:
                    lbl = QLabel(f"· {line}")
                    lbl.setWordWrap(True)
                    lbl.setStyleSheet("font-size: 8pt; color: #ccc; border: none;")
                    detail_layout.addWidget(lbl)
            author = data.get("author", "")
            if author:
                lbl2 = QLabel(f"👤 {author}")
                lbl2.setStyleSheet("font-size: 9pt; color: #666; border: none;")
                detail_layout.addWidget(lbl2)
        card_layout = card.layout()
        card_layout.addWidget(detail)

    def _render_stable_versions(self, all_versions, current_version):
        if not all_versions:
            lbl = QLabel("暂无稳定版本")
            lbl.setStyleSheet("font-size: 9pt; color: #888; border: none;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._ver_scroll_layout.addWidget(lbl)
            return
        expanded = self._ver_expanded
        page_size = self._ver_detail_page_size if expanded else self._ver_list_page_size

        current_v = None
        other_versions = []
        for v in all_versions:
            if v["version"] == current_version:
                current_v = v
            else:
                other_versions.append(v)
        ordered = []
        if current_v:
            ordered.append(current_v)
        ordered.extend(other_versions)

        end = min(self._ver_rendered_count + page_size, len(ordered))
        for idx in range(self._ver_rendered_count, end):
            v = ordered[idx]
            ver = v["version"]
            is_current = (ver == current_version)
            is_available = v.get("available", False)
            is_remote_new = v.get("is_remote_new", False)
            changes = v.get("changes", [])
            exe_info = v.get("exe_info")

            if is_current:
                row_bg = "#1A1A1A"
                border_color = "#333333"
            elif is_remote_new:
                row_bg = "#1A1A1A"
                border_color = "#333333"
            elif is_available:
                row_bg = "#141414"
                border_color = "#222222"
            else:
                row_bg = "#111113"
                border_color = "#1A1A1A"

            card = QFrame()
            card.setProperty("card_bg", row_bg)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setStyleSheet(f"background-color: {row_bg}; border: 1px solid {border_color}; border-radius: 8px;")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(0, 0, 0, 0)
            card_layout.setSpacing(0)

            # === 第一行：版本号 + 摘要 + 状态 + 按钮 ===
            row = QFrame()
            row.setObjectName("_ver_row")
            row.setStyleSheet(f"background-color: {row_bg}; border: none;")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(14, 8, 14, 8)
            row_layout.setSpacing(10)

            ver_color = "#FFFFFF" if is_current else ("#DDDDDD" if is_remote_new else ("#CCCCCC" if is_available else "#666"))
            ver_label = QLabel(f"v{ver}")
            ver_label.setStyleSheet(f"font-family: 'Segoe UI', Consolas, monospace; font-size: 10pt; font-weight: bold; color: {ver_color}; border: none;")
            row_layout.addWidget(ver_label)

            # 摘要：仅列表模式显示message，展开模式在详情区显示全部changes
            if not expanded:
                msg_text = v.get("message", "")
                if msg_text:
                    msg_label = QLabel(msg_text)
                    msg_label.setWordWrap(False)
                    msg_label.setFixedHeight(20)
                    msg_label.setStyleSheet("font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 9pt; color: #999; border: none;")
                    row_layout.addWidget(msg_label, stretch=1)
                else:
                    spacer = QLabel("")
                    spacer.setStyleSheet("border: none;")
                    row_layout.addWidget(spacer, stretch=1)
            else:
                spacer = QLabel("")
                spacer.setStyleSheet("border: none;")
                row_layout.addWidget(spacer, stretch=1)

            # 状态标签
            status_text = ""
            if is_current:
                status_text = "● 当前版本"
            elif is_remote_new:
                status_text = "🆕 新版本"
            elif is_available and exe_info:
                size_text = f" {exe_info.get('size_mb', '')}MB" if exe_info.get("size_mb") else ""
                status_text = f"📦 已下载{size_text}"
            elif v.get("remote_info", {}).get("download_url") or v.get("remote_info", {}).get("filename"):
                status_text = "可下载"
            status_label = QLabel(status_text if status_text else "—")
            status_label.setMinimumWidth(60)
            if is_current:
                status_label.setStyleSheet("font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 8pt; color: #4CAF50; border: none; font-weight: bold;")
            elif is_remote_new:
                status_label.setStyleSheet("font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 8pt; color: #42A5F5; border: none;")
            elif is_available:
                status_label.setStyleSheet("font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 8pt; color: #FF9800; border: none;")
            else:
                status_label.setStyleSheet("font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 8pt; color: #555; border: none;")
            row_layout.addWidget(status_label)

            # 按钮区域
            btn_container = QHBoxLayout()
            btn_container.setSpacing(6)

            if is_available and exe_info and not is_current:
                switch_btn = QPushButton("切换")
                switch_btn.setFixedSize(56, 28)
                switch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                switch_btn.setStyleSheet(
                    "QPushButton { background-color: #CC0000; color: #fff; border: none; border-radius: 5px; font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 8pt; font-weight: bold; }"
                    "QPushButton:hover { background-color: #FF0000; }"
                )
                exe_path = exe_info["path"]
                git_commit = v.get("git_commit", "")
                switch_btn.clicked.connect(lambda checked, p=exe_path, gc=git_commit: self._switch_to_exe(p, gc))
                btn_container.addWidget(switch_btn)

            if is_remote_new or (v.get("remote_info", {}).get("download_url") and not is_available):
                dl_btn = QPushButton("下载")
                dl_btn.setFixedSize(56, 28)
                dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                dl_btn.setStyleSheet(
                    "QPushButton { background-color: #2E7D32; color: #fff; border: none; border-radius: 5px; font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 8pt; font-weight: bold; }"
                    "QPushButton:hover { background-color: #388E3C; }"
                )
                rinfo = v.get("remote_info")
                dl_btn.clicked.connect(lambda checked, ri=rinfo, vr=ver: self._start_inline_download(vr, ri))
                btn_container.addWidget(dl_btn)

            if is_current and v.get("remote_info", {}).get("download_url"):
                rdl_btn = QPushButton("重下载")
                rdl_btn.setFixedSize(56, 28)
                rdl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                rdl_btn.setStyleSheet(
                    "QPushButton { background-color: #333; color: #aaa; border: 1px solid #444; border-radius: 5px; font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 8pt; font-weight: bold; }"
                    "QPushButton:hover { background-color: #444; color: #ddd; }"
                )
                rinfo = v.get("remote_info")
                rdl_btn.clicked.connect(lambda checked, ri=rinfo, vr=ver: self._start_inline_download(vr, ri))
                btn_container.addWidget(rdl_btn)

            row_layout.addLayout(btn_container)
            card_layout.addWidget(row)

            # === 第二行：内联下载进度条（初始隐藏）===
            progress_row = QFrame()
            progress_row.setObjectName(f"_dl_progress_{ver}")
            progress_row.setStyleSheet(f"background-color: {row_bg}; border: none;")
            progress_row.setVisible(False)
            pr_layout = QHBoxLayout(progress_row)
            pr_layout.setContentsMargins(140, 0, 14, 8)
            pr_layout.setSpacing(8)

            dl_progress = QProgressBar()
            dl_progress.setRange(0, 100)
            dl_progress.setValue(0)
            dl_progress.setFixedHeight(18)
            dl_progress.setStyleSheet("""
                QProgressBar { background-color: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 4px; text-align: center; color: #ccc; font-size: 8pt; }
                QProgressBar::chunk { background-color: #2E7D32; border-radius: 3px; }
            """)
            pr_layout.addWidget(dl_progress, stretch=1)

            dl_status_label = QLabel("0.0/0.0MB")
            dl_status_label.setFixedWidth(100)
            dl_status_label.setStyleSheet("font-family: 'Segoe UI', sans-serif; font-size: 8pt; color: #888; border: none;")
            pr_layout.addWidget(dl_status_label)

            pause_btn = QPushButton("暂停")
            pause_btn.setFixedSize(48, 22)
            pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            pause_btn.setStyleSheet(
                "QPushButton { background-color: #333; color: #aaa; border: 1px solid #444; border-radius: 4px; font-family: 'Microsoft YaHei UI', sans-serif; font-size: 7pt; font-weight: bold; }"
                "QPushButton:hover { background-color: #444; color: #ddd; }"
            )
            pr_layout.addWidget(pause_btn)

            cancel_btn = QPushButton("取消")
            cancel_btn.setFixedSize(48, 22)
            cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            cancel_btn.setStyleSheet(
                "QPushButton { background-color: #CC0000; color: #fff; border: none; border-radius: 4px; font-family: 'Microsoft YaHei UI', sans-serif; font-size: 7pt; font-weight: bold; }"
                "QPushButton:hover { background-color: #FF0000; }"
            )
            pr_layout.addWidget(cancel_btn)

            card_layout.addWidget(progress_row)

            v_data = v
            card.clicked_data = v_data
            card.mousePressEvent = lambda e, d=v_data: self._on_version_card_click(None, d)

            # 根据逐卡展开状态决定是否显示详情
            card_expanded = self._ver_card_expanded.get(ver, expanded)
            if card_expanded and (changes or v.get("git_commit", "")):
                self._toggle_card_detail(card, v_data, "stable")
            self._ver_scroll_layout.addWidget(card)

        self._ver_rendered_count = end

        if end < len(ordered):
            load_more_btn = QPushButton(f"加载更多（{len(ordered) - end} 条剩余）")
            load_more_btn.setFixedHeight(34)
            load_more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            load_more_btn.setStyleSheet(
                "QPushButton { background-color: #1a1a1a; color: #888; border: 1px solid #2a2a2a; border-radius: 6px; font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; font-size: 9pt; }"
                "QPushButton:hover { background-color: #222; color: #bbb; }"
            )
            load_more_btn.clicked.connect(lambda: self._load_more_stable(ordered, current_version))
            self._ver_scroll_layout.addWidget(load_more_btn)

    def _render_git_history(self, commits):
        if not commits:
            lbl = QLabel("暂无开发动态")
            lbl.setStyleSheet("font-size: 9pt; color: #555; border: none;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._ver_scroll_layout.addWidget(lbl)
            return
        expanded = self._ver_expanded
        page_size = self._ver_detail_page_size if expanded else self._ver_list_page_size
        end = min(self._ver_rendered_count + page_size, len(commits))
        for idx in range(self._ver_rendered_count, end):
            commit = commits[idx]
            sha = commit.get("sha", commit.get("hash", ""))
            if len(sha) > 8:
                sha = sha[:8]
            message = commit.get("message", "")
            author = commit.get("author", "")
            date_str = commit.get("date", commit.get("time", ""))
            version_tag = commit.get("version", "")
            if date_str and "T" in date_str:
                date_str = date_str.split("T")[0]
            current_commit = self._get_current_commit()
            is_current = (sha == current_commit)
            if is_current:
                card_bg = "#152015"
                border_color = "#1f3a1f"
            else:
                card_bg = "#161616"
                border_color = "#2a2a2a"
            card = QFrame()
            card.setProperty("card_bg", card_bg)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setStyleSheet(f"background-color: {card_bg}; border: 1px solid {border_color}; border-radius: 6px;")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 6, 10, 2)
            card_layout.setSpacing(0)
            header = QFrame()
            header.setStyleSheet(f"background-color: {card_bg}; border: none;")
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(0, 0, 0, 0)
            header_layout.setSpacing(4)
            sha_color = "#4CAF50" if is_current else "#42A5F5"
            sha_label = QLabel(sha)
            sha_label.setStyleSheet(f"font-family: Consolas; font-size: 9pt; font-weight: bold; color: {sha_color}; border: none;")
            header_layout.addWidget(sha_label)
            if version_tag:
                ver_label = QLabel(f"v{version_tag}")
                ver_label.setStyleSheet("font-size: 8pt; color: #FF9800; border: none; font-weight: bold; padding: 1px 4px; background-color: #2a1f00; border-radius: 3px;")
                header_layout.addWidget(ver_label)
            if date_str:
                dt_label = QLabel(date_str)
                dt_label.setStyleSheet("font-family: Consolas; font-size: 9pt; color: #666; border: none;")
                header_layout.addWidget(dt_label)
            header_layout.addStretch()
            msg_first_line = message.split("\n")[0] if message else ""
            if not expanded and len(msg_first_line) > 40:
                msg_first_line = msg_first_line[:37] + "..."
            msg_label = QLabel(msg_first_line)
            msg_label.setWordWrap(True)
            msg_label.setStyleSheet("font-size: 8pt; color: #999; border: none; padding-left: 2px;")
            header_layout.addWidget(msg_label, stretch=1)
            if author:
                author_label = QLabel(author)
                author_label.setStyleSheet("font-size: 8pt; color: #555; border: none;")
                header_layout.addWidget(author_label)
            if is_current:
                cur_label = QLabel("● 当前")
                cur_label.setStyleSheet("font-size: 8pt; color: #4CAF50; border: none; font-weight: bold;")
                header_layout.addWidget(cur_label)
            card_layout.addWidget(header)
            if expanded:
                detail = QFrame()
                detail.setObjectName("_detail")
                detail.setStyleSheet(f"background-color: {card_bg}; border: none;")
                detail_layout = QVBoxLayout(detail)
                detail_layout.setContentsMargins(14, 4, 14, 6)
                detail_layout.setSpacing(2)
                # 显示完整commit信息
                if message:
                    msg_lines = message.split("\n")
                    full_msg = QLabel(msg_lines[0])
                    full_msg.setWordWrap(True)
                    full_msg.setStyleSheet("font-size: 8pt; color: #bbb; border: none;")
                    detail_layout.addWidget(full_msg)
                    for line in msg_lines[1:]:
                        line = line.strip()
                        if line:
                            lbl = QLabel(f"· {line}")
                            lbl.setWordWrap(True)
                            lbl.setStyleSheet("font-size: 8pt; color: #777; border: none;")
                            detail_layout.addWidget(lbl)
                # 显示完整sha
                full_sha = commit.get("sha", commit.get("hash", ""))
                if full_sha and len(full_sha) > 8:
                    sha_detail = QLabel(f"完整哈希: {full_sha}")
                    sha_detail.setStyleSheet("font-size: 8pt; color: #555; border: none; font-family: Consolas;")
                    detail_layout.addWidget(sha_detail)
                # 显示完整日期
                full_date = commit.get("date", commit.get("time", ""))
                if full_date:
                    date_detail = QLabel(f"时间: {full_date}")
                    date_detail.setStyleSheet("font-size: 8pt; color: #555; border: none;")
                    detail_layout.addWidget(date_detail)
                card_layout.addWidget(detail)
            commit_data = commit
            card.clicked_data = commit_data
            card.mousePressEvent = lambda e, d=commit_data: self._toggle_card_detail(None, d, "git")
            self._ver_scroll_layout.addWidget(card)

        self._ver_rendered_count = end

        if end < len(commits):
            load_more_btn = QPushButton(f"加载更多（{len(commits) - end} 条剩余）")
            load_more_btn.setFixedHeight(32)
            load_more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            load_more_btn.setStyleSheet(
                "QPushButton { background-color: #1a1a1a; color: #888; border: 1px solid #2a2a2a; border-radius: 6px; font-size: 9pt; }"
                "QPushButton:hover { background-color: #222; color: #aaa; }"
            )
            load_more_btn.clicked.connect(lambda: self._load_more_git(commits))
            self._ver_scroll_layout.addWidget(load_more_btn)

    def _check_remote_versions(self):
        if self._ver_status_label is not None:
            self._ver_status_label.setText("正在检查远程更新...")
        source_key = self._active_update_source
        if source_key == "auto":
            self._check_remote_versions_race()
        else:
            self._check_remote_versions_single(source_key)

    def _on_update_source_changed(self, index):
        combo = getattr(self, '_ver_source_combo', None)
        if not combo:
            return
        key = combo.itemData(index)
        if key:
            self._active_update_source = key
            self._check_remote_versions()

    def _check_remote_versions_single(self, source_key):
        """从指定单个源获取版本列表"""
        if source_key not in UPDATE_SOURCES:
            self._check_remote_versions_race()
            return
        self._cancel_race_procs()
        self._ver_race_done = False
        self._ver_race_results = {}
        self._ver_race_errors = {}
        self._ver_race_procs = {}
        source = UPDATE_SOURCES[source_key]
        url = source["version_url"]
        proc = QProcess(self)
        proc.setProperty("race_key", source_key)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        proc.finished.connect(lambda ec, es, k=source_key: self._ver_race_finished(ec, es, k))
        proc.start("curl.exe", [
            "-s", "-k", "-L", "--connect-timeout", "8", "-m", "15",
            "-H", "User-Agent: Mozilla/5.0", url
        ])
        self._ver_race_procs[source_key] = proc

    def _check_remote_versions_race(self):
        self._cancel_race_procs()
        self._ver_race_done = False
        self._ver_race_results = {}
        self._ver_race_errors = {}
        self._ver_race_procs = {}
        for key in UPDATE_SOURCES:
            source = UPDATE_SOURCES[key]
            url = source["version_url"]
            proc = QProcess(self)
            proc.setProperty("race_key", key)
            proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
            proc.finished.connect(lambda ec, es, k=key: self._ver_race_finished(ec, es, k))
            proc.start("curl.exe", [
                "-s", "-k", "-L", "--connect-timeout", "8", "-m", "15",
                "-H", "User-Agent: Mozilla/5.0", url
            ])
            self._ver_race_procs[key] = proc

    def _ver_race_finished(self, exit_code, exit_status, key):
        if self._ver_race_done:
            return
        proc = self._ver_race_procs.get(key)
        if proc is None:
            return
        if exit_code == 0:
            try:
                raw = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
                source = UPDATE_SOURCES[key]
                import base64
                if source.get("is_api"):
                    api_data = json.loads(raw)
                    if isinstance(api_data, list):
                        file_data = api_data[0] if api_data else {}
                    else:
                        file_data = api_data
                    content_b64 = file_data.get("content", "")
                    content_json = base64.b64decode(content_b64).decode("utf-8")
                    data = json.loads(content_json)
                else:
                    data = json.loads(raw)
                self._ver_race_done = True
                self._ver_race_winner = key
                self._cancel_race_procs()
                self._ver_curl_done(key, data)
                return
            except Exception as e:
                self._ver_race_errors[key] = str(e)[:80]
        else:
            stderr = ""
            try:
                stderr = bytes(proc.readAllStandardError()).decode("utf-8", errors="replace")[:80]
            except Exception:
                pass
            self._ver_race_errors[key] = f"exit={exit_code}" + (f" {stderr}" if stderr else "")
        self._ver_race_procs.pop(key, None)
        try:
            proc.finished.disconnect()
        except Exception:
            pass
        proc.deleteLater()
        all_done = all(k not in self._ver_race_procs for k in UPDATE_SOURCES)
        if all_done and not self._ver_race_done:
            self._ver_race_done = True
            self._ver_curl_done(None, None)

    def _cancel_race_procs(self, exclude=None):
        procs = getattr(self, '_ver_race_procs', None)
        if not procs:
            return
        keys_to_remove = [k for k in procs if k != exclude]
        for k in keys_to_remove:
            proc = procs.pop(k, None)
            if proc:
                try:
                    proc.finished.disconnect()
                except Exception:
                    pass
                try:
                    proc.terminate()
                except Exception:
                    pass
                proc.deleteLater()

    def _ver_curl_done(self, winning_source, data):
        if winning_source is None or data is None:
            self._load_all_versions_fallback()
            self._version_data_ready.emit()
            return

        # 自动竞速模式下保持用户选择，仅记录胜出源；单源模式下更新为实际使用的源
        if self._active_update_source != "auto":
            self._active_update_source = winning_source
        self._ver_race_winner = winning_source
        remote_latest = data.get("latest", "")
        remote_versions_list = data.get("versions", [])
        if not remote_versions_list and remote_latest:
            remote_versions_list = [{
                "version": remote_latest,
                "date": data.get("release_date", ""),
                "changes": data.get("changes", []),
                "filename": data.get("filename", f"{APP_NAME}-v{remote_latest}.exe"),
            }]
        # 版本列表只从整理过的version.json读取，EXE扫描仅用于标记"是否已下载"
        stable_exes = self._list_stable_exes()
        exe_versions = {e["version"]: e for e in stable_exes}
        current_version = VERSION
        # Add current running EXE if not already found
        if current_version and current_version not in exe_versions:
            if getattr(sys, 'frozen', False):
                cur_exe = os.path.abspath(sys.executable)
                try:
                    size_mb = round(os.path.getsize(cur_exe) / (1024 * 1024), 1)
                except Exception:
                    size_mb = 0
                exe_versions[current_version] = {"filename": os.path.basename(cur_exe), "path": cur_exe, "version": current_version, "size_mb": size_mb}

        # 为远程版本构建下载URL（基于当前源的模板）
        source = UPDATE_SOURCES.get(winning_source, {})
        download_tpl = source.get("download_url_tpl", "")

        all_versions = []
        seen = set()
        # 只从远程version.json的versions列表构建版本列表
        for rinfo in remote_versions_list:
            rv = rinfo.get("version", "")
            ver_num = self._normalize_version(rv)
            if not ver_num or ver_num in seen:
                continue
            seen.add(ver_num)
            is_new = (ver_num != current_version and ver_num not in exe_versions)
            # 构建下载URL：优先使用remote_info中的download_url，否则用模板
            dl_url = rinfo.get("download_url", "")
            if not dl_url and download_tpl:
                fn = rinfo.get("filename", f"{APP_NAME}-v{ver_num}.exe")
                dl_url = download_tpl.format(filename=fn, version=ver_num)
            rinfo_copy = dict(rinfo)
            if dl_url:
                rinfo_copy["download_url"] = dl_url
            all_versions.append({
                "version": ver_num,
                "name": rinfo.get("name", f"v{ver_num}"),
                "changes": rinfo.get("changes", []),
                "build_time": rinfo.get("build_time", rinfo.get("date", "")),
                "git_commit": rinfo.get("git_commit", ""),
                "available": ver_num in exe_versions,
                "exe_info": exe_versions.get(ver_num),
                "is_remote_new": is_new,
                "remote_info": rinfo_copy,
            })
        all_versions.sort(key=lambda x: x["version"], reverse=True)
        self._latest_version = remote_latest
        self._latest_info = next((v for v in remote_versions_list if v.get("version") == remote_latest), None)
        self._ver_stable_data = all_versions
        if not self._ver_git_data:
            self._ver_git_data = []
        self._ver_current_version = current_version
        # 初始化逐卡展开状态
        for v in all_versions:
            if v["version"] not in self._ver_card_expanded:
                self._ver_card_expanded[v["version"]] = self._ver_expanded
        has_update = remote_latest and remote_latest != VERSION and remote_latest not in exe_versions
        src_name = UPDATE_SOURCES[winning_source]["name"]
        # 自动竞速模式下不切换下拉框，仅更新内部记录
        if self._active_update_source != "auto":
            combo = getattr(self, '_ver_source_combo', None)
            if combo:
                idx = combo.findData(winning_source)
                if idx >= 0:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(idx)
                    combo.blockSignals(False)
        if has_update:
            changes_preview = ""
            if data.get("changes"):
                changes_preview = "（" + "、".join(data["changes"][:2]) + "）"
            self._ver_info_text = f"🆕 发现新版本 v{remote_latest}{changes_preview}（via {src_name}）"
        else:
            self._ver_info_text = f"✅ 已是最新版本（via {src_name}）"
        self._save_update_cache()
        self._version_data_ready.emit()

        # 异步获取Release列表，合并真实下载链接
        self._fetch_release_assets(winning_source, all_versions)

        if not self._ver_git_data:
            self._fetch_remote_commits()

    def _fetch_release_assets(self, source_key, current_versions):
        """异步从Release API获取真实下载链接，合并到版本列表"""
        source = UPDATE_SOURCES.get(source_key, {})
        releases_url = source.get("releases_url", "")
        if not releases_url:
            return

        proc = QProcess(self)
        proc.setProperty("source_key", source_key)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)

        def on_done(ec, es):
            if ec != 0:
                proc.deleteLater()
                return
            try:
                raw = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
                releases = json.loads(raw)
                if not isinstance(releases, list):
                    proc.deleteLater()
                    return
                # 构建版本号→下载URL映射
                release_downloads = {}
                for rel in releases:
                    tag = rel.get("tag_name", "")
                    tag_ver = self._normalize_version(tag)
                    if not tag_ver:
                        continue
                    for asset in rel.get("assets", []):
                        asset_name = asset.get("name", "")
                        if asset_name.endswith(".exe"):
                            url = asset.get("browser_download_url", "")
                            if url:
                                # 从文件名提取版本号
                                asset_ver = self._normalize_version(asset_name)
                                if asset_ver:
                                    release_downloads[asset_ver] = {
                                        "download_url": url,
                                        "filename": asset_name,
                                        "size": asset.get("size", 0),
                                    }
                                # 也用tag版本号映射
                                if tag_ver not in release_downloads:
                                    release_downloads[tag_ver] = {
                                        "download_url": url,
                                        "filename": asset_name,
                                        "size": asset.get("size", 0),
                                    }
                # 合并到版本列表
                updated = False
                for v in self._ver_stable_data:
                    ver = v.get("version", "")
                    if ver in release_downloads:
                        rd = release_downloads[ver]
                        ri = v.get("remote_info", {})
                        if not isinstance(ri, dict):
                            ri = {}
                        ri["download_url"] = rd["download_url"]
                        ri["filename"] = rd.get("filename", ri.get("filename", ""))
                        v["remote_info"] = ri
                        # 如果之前没有标记为可下载，现在有了
                        if not v.get("available") and not v.get("is_remote_new"):
                            v["is_remote_new"] = (ver != VERSION)
                        updated = True
                # 只更新已有版本的下载链接，不添加Release中未在version.json中登记的版本
                # （版本列表以version.json为唯一数据源，Release仅补充下载链接）
                if updated:
                    self._ver_stable_data.sort(key=lambda x: x["version"], reverse=True)
                    self._save_update_cache()
                    self._version_data_ready.emit()
            except Exception:
                pass
            proc.deleteLater()

        proc.finished.connect(on_done)
        proc.start("curl.exe", [
            "-s", "-k", "-L", "--connect-timeout", "8", "-m", "20",
            "-H", "User-Agent: Mozilla/5.0", releases_url
        ])

    def _fetch_remote_commits(self):
        source_key = self._active_update_source
        if source_key == "auto":
            self._fetch_remote_commits_race()
        else:
            self._fetch_remote_commits_single(source_key)

    def _fetch_remote_commits_race(self):
        self._cancel_commits_race_procs()
        self._commits_race_done = False
        self._commits_race_procs = {}
        for key in UPDATE_SOURCES:
            source = UPDATE_SOURCES[key]
            url = source["commits_url"]
            proc = QProcess(self)
            proc.setProperty("race_key", key)
            proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
            proc.finished.connect(lambda ec, es, k=key: self._commits_race_finished(ec, es, k))
            proc.start("curl.exe", [
                "-s", "-k", "-L", "--connect-timeout", "8", "-m", "15",
                "-H", "User-Agent: Mozilla/5.0", url
            ])
            self._commits_race_procs[key] = proc

    def _fetch_remote_commits_single(self, source_key):
        """从指定单个源获取Git提交历史"""
        if source_key not in UPDATE_SOURCES:
            self._fetch_remote_commits_race()
            return
        self._cancel_commits_race_procs()
        self._commits_race_done = False
        self._commits_race_procs = {}
        source = UPDATE_SOURCES[source_key]
        url = source["commits_url"]
        proc = QProcess(self)
        proc.setProperty("race_key", source_key)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        proc.finished.connect(lambda ec, es, k=source_key: self._commits_race_finished(ec, es, k))
        proc.start("curl.exe", [
            "-s", "-k", "-L", "--connect-timeout", "8", "-m", "15",
            "-H", "User-Agent: Mozilla/5.0", url
        ])
        self._commits_race_procs[source_key] = proc

    def _cancel_commits_race_procs(self, exclude=None):
        keys_to_remove = [k for k in getattr(self, '_commits_race_procs', {}) if k != exclude]
        for k in keys_to_remove:
            proc = self._commits_race_procs.pop(k, None)
            if proc:
                try:
                    proc.finished.disconnect()
                except Exception:
                    pass
                try:
                    proc.terminate()
                except Exception:
                    pass
                proc.deleteLater()

    def _commits_race_finished(self, exit_code, exit_status, key):
        if self._commits_race_done:
            return
        proc = self._commits_race_procs.get(key)
        if proc is None:
            return
        if exit_code == 0:
            try:
                raw = proc.readAllStandardOutput().data().decode('utf-8', errors='replace')
                data = json.loads(raw)
                commits = []
                for c in data:
                    commit_info = c.get("commit", c)
                    commits.append({
                        "sha": c.get("sha", ""),
                        "message": commit_info.get("message", ""),
                        "author": commit_info.get("author", {}).get("name", "") if isinstance(commit_info.get("author"), dict) else commit_info.get("author", ""),
                        "date": commit_info.get("author", {}).get("date", "") if isinstance(commit_info.get("author"), dict) else commit_info.get("date", ""),
                    })
                self._commits_race_done = True
                self._cancel_commits_race_procs()
                # Merge: prefer remote commits, but add local-only commits
                local_commits = self._get_git_history(500)
                if local_commits:
                    remote_shas = {c.get("sha", "")[:8] for c in commits}
                    for lc in local_commits:
                        if lc.get("hash", "") not in remote_shas:
                            commits.append({"sha": lc["hash"], "message": lc["message"], "author": lc["author"], "date": lc.get("time", "")})
                self._ver_git_data = commits
                self._save_update_cache()
                self._version_data_ready.emit()
                return
            except Exception:
                pass
        self._commits_race_procs.pop(key, None)
        try:
            proc.finished.disconnect()
        except Exception:
            pass
        proc.deleteLater()
        all_done = all(k not in self._commits_race_procs for k in UPDATE_SOURCES)
        if all_done and not self._commits_race_done:
            self._commits_race_done = True
            self._ver_git_data = []
            self._version_data_ready.emit()

    def _refresh_git_history(self):
        self._ver_git_data = []
        # Try local git first
        local_commits = self._get_git_history(500)
        if local_commits:
            self._ver_git_data = local_commits
        if self._ver_active_tab == "git":
            self._render_active_tab()
        # Also try remote in background
        if not getattr(self, '_commits_race_procs', None):
            self._fetch_remote_commits()

    def _on_version_data_ready(self):
        QTimer.singleShot(0, self._deferred_render_version_tab)

    def _deferred_render_version_tab(self):
        self._render_active_tab()
        self._update_current_version_card()
        if self._ver_status_label is not None:
            count = len(self._ver_stable_data)
            hist = len(self._ver_git_data)
            cache_tag = "（缓存）" if getattr(self, '_ver_cache_check_scheduled', False) and not getattr(self, '_ver_race_done', True) else ""
            self._ver_status_label.setText(f"稳定版 {count} 个 | 版本历史 {hist} 条{cache_tag}")

    def _update_current_version_card(self):
        current_v = next((v for v in self._ver_stable_data if v["version"] == self._ver_current_version), None)
        if hasattr(self, '_ver_current_desc_label') and self._ver_current_desc_label:
            if current_v:
                changes = current_v.get("changes", [])
                desc = "、".join(changes) if changes else "暂无描述"
                self._ver_current_desc_label.setText(desc)
            else:
                self._ver_current_desc_label.setText(f"v{self._ver_current_version}")
        if hasattr(self, '_ver_current_build_label') and self._ver_current_build_label:
            if current_v:
                bt = current_v.get("build_time", "")
                self._ver_current_build_label.setText(f"🕐 构建时间: {bt}" if bt else "")
            else:
                self._ver_current_build_label.setText("")
        if hasattr(self, '_ver_current_commit_label') and self._ver_current_commit_label:
            if current_v:
                gc = current_v.get("git_commit", "")
                self._ver_current_commit_label.setText(f"🔗 {gc}" if gc else "")
            else:
                self._ver_current_commit_label.setText("")

    def _save_update_cache(self):
        if not self._ver_cache_file:
            return
        try:
            cache_dir = os.path.dirname(self._ver_cache_file)
            if cache_dir and not os.path.exists(cache_dir):
                os.makedirs(cache_dir, exist_ok=True)
            cache_data = {
                "timestamp": time.time(),
                "stable_data": self._ver_stable_data,
                "git_data": self._ver_git_data,
                "info_text": self._ver_info_text,
                "race_winner": getattr(self, '_ver_race_winner', ''),
                "active_source": self._active_update_source,
                "latest_version": self._latest_version,
            }
            with open(self._ver_cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_update_cache(self):
        if not self._ver_cache_file or not os.path.exists(self._ver_cache_file):
            return None
        try:
            with open(self._ver_cache_file, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
            return cache_data
        except Exception:
            return None

    def _load_update_cache_and_check(self):
        cache = self._load_update_cache()
        if cache:
            self._ver_stable_data = cache.get("stable_data", [])
            self._ver_git_data = cache.get("git_data", [])
            self._ver_info_text = cache.get("info_text", "")
            self._ver_race_winner = cache.get("race_winner", "")
            self._active_update_source = cache.get("active_source", "auto")
            self._latest_version = cache.get("latest_version", "")
            # 同步源下拉框选中项
            self._sync_source_combo()
            # 初始化逐卡展开状态
            for v in self._ver_stable_data:
                if v["version"] not in self._ver_card_expanded:
                    self._ver_card_expanded[v["version"]] = self._ver_expanded
            # Re-check local EXEs to update available/exe_info (cache may be stale)
            self._refresh_local_exe_status()
            QTimer.singleShot(0, self._deferred_render_version_tab)
        else:
            # 无缓存时只从本地 versions.json 加载版本列表，不自动发起远程请求
            self._load_all_versions_fallback()
            local_commits = self._get_git_history(500)
            if local_commits:
                self._ver_git_data = local_commits
            self._ver_info_text = "点击「检查更新」获取最新版本"
            self._ver_status_label.setText("")
            QTimer.singleShot(0, self._deferred_render_version_tab)

    def _sync_source_combo(self):
        """同步源下拉框选中项与 _active_update_source"""
        combo = getattr(self, '_ver_source_combo', None)
        if not combo:
            return
        for i in range(combo.count()):
            if combo.itemData(i) == self._active_update_source:
                combo.blockSignals(True)
                combo.setCurrentIndex(i)
                combo.blockSignals(False)
                return

    def _effective_source_key(self):
        """获取实际使用的源key：自动竞速模式下使用竞速胜出的源，否则使用用户选择的源"""
        key = getattr(self, '_active_update_source', 'auto')
        if key == "auto":
            winner = getattr(self, '_ver_race_winner', '')
            key = winner if winner and winner in UPDATE_SOURCES else (list(UPDATE_SOURCES.keys())[0] if UPDATE_SOURCES else "github_mirror")
        if key not in UPDATE_SOURCES:
            key = list(UPDATE_SOURCES.keys())[0] if UPDATE_SOURCES else "github_mirror"
        return key

    def _refresh_local_exe_status(self):
        """Re-scan local EXE files and update available/exe_info for all cached versions."""
        stable_exes = self._list_stable_exes()
        exe_versions = {e["version"]: e for e in stable_exes}
        # Add current running EXE if not already found
        cur_ver = VERSION
        if cur_ver and cur_ver not in exe_versions:
            if getattr(sys, 'frozen', False):
                cur_exe = os.path.abspath(sys.executable)
                try:
                    size_mb = round(os.path.getsize(cur_exe) / (1024 * 1024), 1)
                except Exception:
                    size_mb = 0
                exe_versions[cur_ver] = {"filename": os.path.basename(cur_exe), "path": cur_exe, "version": cur_ver, "size_mb": size_mb}
        # Update existing version entries
        for v in self._ver_stable_data:
            ver = v.get("version", "")
            if ver in exe_versions:
                v["available"] = True
                v["exe_info"] = exe_versions[ver]
            elif v.get("available") and not v.get("exe_info"):
                v["available"] = False
        # 不添加本地EXE中未在version.json登记的版本（版本列表以version.json为唯一数据源）
        self._ver_stable_data.sort(key=lambda x: x["version"], reverse=True)

    def _load_more_stable(self, ordered, current_version):
        btn = None
        for i in range(self._ver_scroll_layout.count()):
            item = self._ver_scroll_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), QPushButton):
                btn = item.widget()
                break
        if btn:
            self._ver_scroll_layout.removeWidget(btn)
            btn.setParent(None)
            btn.deleteLater()
        self._render_stable_versions(ordered, current_version)

    def _load_more_git(self, commits):
        btn = None
        for i in range(self._ver_scroll_layout.count()):
            item = self._ver_scroll_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), QPushButton):
                btn = item.widget()
                break
        if btn:
            self._ver_scroll_layout.removeWidget(btn)
            btn.setParent(None)
            btn.deleteLater()
        self._render_git_history(commits)

    def _run_git(self, *args, cwd=None, timeout=60):
        import subprocess
        cmd = ["git"] + list(args)
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            r = subprocess.run(cmd, cwd=cwd or self._project_root,
                              capture_output=True, text=True, timeout=timeout,
                              startupinfo=si,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            return {"ok": r.returncode == 0, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()}
        except subprocess.TimeoutExpired:
            return {"ok": False, "stdout": "", "stderr": "命令超时"}
        except Exception as e:
            return {"ok": False, "stdout": "", "stderr": str(e)}

    def _is_git_repo(self):
        r = self._run_git("rev-parse", "--is-inside-work-tree")
        return r["ok"] and r["stdout"] == "true"

    def _get_current_commit(self):
        r = self._run_git("rev-parse", "--short", "HEAD")
        return r["stdout"] if r["ok"] else "unknown"

    def _get_git_history(self, limit=30):
        # 优先从git获取历史
        if self._is_git_repo():
            r = self._run_git("log", f"-{limit}", "--format=%h|%s|%an|%ai", timeout=30)
            if r["ok"] and r["stdout"].strip():
                # 加载versions.json用于丰富提交描述
                ver_map = {}
                try:
                    vpath = self._resolve_versions_json_path()
                    if os.path.exists(vpath):
                        with open(vpath, "r", encoding="utf-8") as f:
                            raw = json.load(f)
                        vdata = raw.get("versions", raw) if isinstance(raw, dict) else raw
                        for v in vdata:
                            ver_map[v.get("version", "")] = v
                except Exception:
                    pass
                commits = []
                for line in r["stdout"].splitlines():
                    parts = line.strip().split("|", 3)
                    if len(parts) >= 4:
                        commit = {"sha": parts[0], "hash": parts[0], "message": parts[1], "author": parts[2], "date": parts[3], "time": parts[3]}
                        msg = parts[1]
                        msg_ver = self._normalize_version(msg)
                        if msg_ver and msg_ver in ver_map:
                            commit["version"] = msg_ver
                        commits.append(commit)
                if commits:
                    return commits

        # git不可用时，从versions.json生成开发动态
        return self._get_history_from_versions(limit)

    def _get_history_from_versions(self, limit=30):
        """从gitlog.json生成开发动态（EXE模式下git不可用时使用）
        与versions.json同路径逻辑：开发模式dev/app/，EXE模式app/（自部署释放）
        """
        commits = []
        changelog_path = self._resolve_gitlog_path()
        if changelog_path:
            try:
                with open(changelog_path, "r", encoding="utf-8") as f:
                    commits = json.load(f)
                # 补充version字段
                for c in commits:
                    if "version" not in c:
                        msg = c.get("message", "")
                        ver = self._normalize_version(msg)
                        if ver:
                            c["version"] = ver
                return commits[:limit]
            except Exception:
                pass

        # 回退到versions.json
        vpath = self._resolve_versions_json_path()
        if not vpath or not os.path.exists(vpath):
            return commits
        try:
            with open(vpath, "r", encoding="utf-8") as f:
                raw = json.load(f)
            vdata = raw.get("versions", raw) if isinstance(raw, dict) else raw
            for v in vdata[:limit]:
                ver = v.get("version", "")
                changes = v.get("changes", [])
                if changes:
                    desc = "；".join(changes)
                else:
                    desc = v.get("message", ver)
                commit = {
                    "sha": ver.replace(".", "")[:7],
                    "hash": ver.replace(".", "")[:7],
                    "message": desc,
                    "author": "yunjii",
                    "date": v.get("date", ""),
                    "time": v.get("date", ""),
                    "version": ver,
                }
                commits.append(commit)
        except Exception:
            pass
        return commits

    def _list_stable_exes(self):
        exes = []
        seen_paths = set()
        # Search in ver/ directory
        ver_dir = os.path.join(self._project_root, "ver") if self._project_root else ""
        if ver_dir and os.path.isdir(ver_dir):
            for f in os.listdir(ver_dir):
                if f.endswith(".exe") and "云集智能视频创意站" in f:
                    path = os.path.join(ver_dir, f)
                    if path in seen_paths:
                        continue
                    seen_paths.add(path)
                    m = re.search(r'v(\d+\.\d+\.\d+\.\d+)', f)
                    ver = m.group(1) if m else "unknown"
                    try:
                        size_mb = round(os.path.getsize(path) / (1024 * 1024), 1)
                    except:
                        size_mb = 0
                    exes.append({"filename": f, "path": path, "version": ver, "size_mb": size_mb})
        # Search in dev/ directory for versioned EXE folders
        dev_dir = self._project_root
        if dev_dir and os.path.isdir(dev_dir):
            for f in os.listdir(dev_dir):
                full_path = os.path.join(dev_dir, f)
                if os.path.isdir(full_path) and "云集智能视频创意站-v" in f:
                    # Look for exe inside the versioned folder
                    for inner in os.listdir(full_path):
                        if inner.endswith(".exe") and "云集智能视频创意站" in inner:
                            exe_path = os.path.join(full_path, inner)
                            if exe_path in seen_paths:
                                continue
                            seen_paths.add(exe_path)
                            m = re.search(r'v(\d+\.\d+\.\d+\.\d+)', f)
                            ver = m.group(1) if m else "unknown"
                            try:
                                size_mb = round(os.path.getsize(exe_path) / (1024 * 1024), 1)
                            except:
                                size_mb = 0
                            exes.append({"filename": inner, "path": exe_path, "version": ver, "size_mb": size_mb})
        exes.sort(key=lambda x: x["version"], reverse=True)
        return exes

    def _resolve_versions_json_path(self):
        """统一解析版本列表文件路径：_app_dir/versions.json

        开发模式：dev/app/versions.json
        自部署/用户EXE：app/versions.json（与EXE同目录）
        """
        if self._app_dir:
            p = os.path.join(self._app_dir, "versions.json")
            if os.path.exists(p):
                return p
        return ""

    def _resolve_gitlog_path(self):
        """统一解析开发动态文件路径：_app_dir/gitlog.json

        开发模式：dev/app/gitlog.json
        自部署/用户EXE：app/gitlog.json（与EXE同目录）
        """
        if self._app_dir:
            p = os.path.join(self._app_dir, "gitlog.json")
            if os.path.exists(p):
                return p
        return ""

    def _get_local_version_history(self):
        path = self._resolve_versions_json_path()
        if not path:
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "versions" in data:
                return data["versions"]
            if isinstance(data, list):
                return data
            return []
        except:
            return []

    def _normalize_version(self, ver_str):
        m = re.search(r'v?(\d+\.\d+\.\d+(?:\.\d+)?)', ver_str or "")
        return m.group(1) if m else ""

    def _load_all_versions_fallback(self):
        # 版本列表只从整理过的version.json读取，EXE扫描仅用于标记"是否已下载"
        stable_exes = self._list_stable_exes()
        local_versions = self._get_local_version_history()
        exe_versions = {e["version"]: e for e in stable_exes}
        # Add current running EXE if not already found
        cur_ver = VERSION
        if cur_ver and cur_ver not in exe_versions:
            if getattr(sys, 'frozen', False):
                cur_exe = os.path.abspath(sys.executable)
                try:
                    size_mb = round(os.path.getsize(cur_exe) / (1024 * 1024), 1)
                except Exception:
                    size_mb = 0
                exe_versions[cur_ver] = {"filename": os.path.basename(cur_exe), "path": cur_exe, "version": cur_ver, "size_mb": size_mb}
        all_versions = []
        seen = set()
        # 只从本地versions.json构建版本列表
        for v in local_versions:
            ver = v.get("version", "")
            ver_num = self._normalize_version(ver)
            if not ver_num or ver_num in seen:
                continue
            seen.add(ver_num)
            all_versions.append({
                "version": ver_num,
                "name": v.get("name", f"v{ver_num}"),
                "changes": v.get("changes", []),
                "build_time": v.get("build_time", v.get("date", "")),
                "git_commit": v.get("git_commit", ""),
                "available": ver_num in exe_versions,
                "exe_info": exe_versions.get(ver_num),
                "is_remote_new": False,
            })
        all_versions.sort(key=lambda x: x["version"], reverse=True)
        self._ver_stable_data = all_versions
        self._ver_current_version = VERSION
        # 初始化逐卡展开状态
        for v in all_versions:
            if v["version"] not in self._ver_card_expanded:
                self._ver_card_expanded[v["version"]] = self._ver_expanded
        error_parts = []
        for k, err in getattr(self, '_ver_race_errors', {}).items():
            src_name = UPDATE_SOURCES.get(k, {}).get("name", k)
            error_parts.append(f"{src_name}: {err}")
        if error_parts:
            self._ver_info_text = f"⚠ 无法连接远程仓库，显示本地版本\n失败详情: {'; '.join(error_parts)}"
        else:
            self._ver_info_text = "⚠ 无法连接远程仓库，显示本地版本"

    def _on_download_version(self, remote_info):
        if not remote_info:
            return
        filename = remote_info.get("filename", "")
        version = remote_info.get("version", "")
        if not filename and not version:
            return
        # 优先使用remote_info中的download_url（来自Release API）
        download_url = remote_info.get("download_url", "")
        release_page = ""
        if not download_url:
            source_key = self._effective_source_key()
            source = UPDATE_SOURCES.get(source_key, UPDATE_SOURCES.get('github_mirror', list(UPDATE_SOURCES.values())[0] if UPDATE_SOURCES else {}))
            if filename and version:
                download_url = source.get("download_url_tpl", "").format(filename=filename, version=version)
            if not download_url:
                download_url = source.get("download_url_tpl", "").format(filename=filename or "", version=version or "")
        # 构建Release页面URL
        if version:
            source_key = self._effective_source_key()
            if source_key == "gitee":
                release_page = f"https://gitee.com/yunjii/vi/releases/tag/v{version}"
            else:
                release_page = f"https://github.com/yunjii-cn/vi/releases/tag/v{version}"
        else:
            release_page = "https://github.com/yunjii-cn/vi/releases"

        # 尝试应用内下载到 ver/ 目录
        if download_url and version:
            ver_dir = os.path.join(self._project_root, "ver") if self._project_root else ""
            if ver_dir:
                os.makedirs(ver_dir, exist_ok=True)
                target_filename = filename if filename else f"{APP_NAME}-v{version}.exe"
                target_path = os.path.join(ver_dir, target_filename)
                # 如果已存在则直接切换
                if os.path.isfile(target_path):
                    self._log(f"✓ 版本 v{version} 已下载，正在切换...", "ok")
                    self._switch_to_exe(target_path)
                    return
                # 应用内下载
                self._download_exe_to_ver(download_url, target_path, version, release_page)
                return

        # 回退：浏览器下载
        try:
            import webbrowser
            if download_url:
                webbrowser.open(download_url)
            else:
                webbrowser.open(release_page)
        except Exception:
            try:
                import webbrowser
                webbrowser.open(release_page)
            except Exception as e2:
                self._log(f"× 无法打开下载链接: {e2}", "err")

    def _start_inline_download(self, version, remote_info):
        """内联下载EXE：在版本行中显示进度条，而非弹窗"""
        if not remote_info:
            return
        filename = remote_info.get("filename", "")
        download_url = remote_info.get("download_url", "")
        if not download_url:
            source_key = self._effective_source_key()
            source = UPDATE_SOURCES.get(source_key, UPDATE_SOURCES.get('github_mirror', list(UPDATE_SOURCES.values())[0] if UPDATE_SOURCES else {}))
            if filename and version:
                download_url = source.get("download_url_tpl", "").format(filename=filename, version=version)
        if not download_url:
            self._log(f"× v{version} 无可用下载链接", "err")
            return

        ver_dir = os.path.join(self._project_root, "ver") if self._project_root else ""
        if not ver_dir:
            self._log("× 无法确定版本目录", "err")
            return
        os.makedirs(ver_dir, exist_ok=True)
        target_filename = filename if filename else f"{APP_NAME}-v{version}.exe"
        target_path = os.path.join(ver_dir, target_filename)

        # 如果已存在则直接切换
        if os.path.isfile(target_path):
            self._log(f"✓ 版本 v{version} 已下载，正在切换...", "ok")
            self._switch_to_exe(target_path)
            return

        # 查找进度条组件
        scroll_content = getattr(self, '_ver_scroll_content', None)
        if not scroll_content:
            self._on_download_version(remote_info)
            return

        progress_row = scroll_content.findChild(QFrame, f"_dl_progress_{version}")
        if not progress_row:
            self._on_download_version(remote_info)
            return

        # 显示进度行
        progress_row.setVisible(True)
        dl_progress = progress_row.findChild(QProgressBar)
        dl_status_label = None
        pause_btn = None
        cancel_btn = None
        for child in progress_row.children():
            if isinstance(child, QLabel):
                dl_status_label = child
            elif isinstance(child, QPushButton):
                if child.text() == "暂停":
                    pause_btn = child
                elif child.text() == "取消":
                    cancel_btn = child

        if not dl_progress or not dl_status_label:
            self._on_download_version(remote_info)
            return

        dl_progress.setValue(0)
        dl_status_label.setText("0.0/0.0MB")

        # 下载状态
        dl_state = {"paused": False, "cancelled": False, "done": False, "pause_event": threading.Event()}
        dl_state["pause_event"].set()  # 初始非暂停

        def on_pause():
            if dl_state["paused"]:
                dl_state["paused"] = False
                dl_state["pause_event"].set()
                if pause_btn:
                    pause_btn.setText("暂停")
            else:
                dl_state["paused"] = True
                dl_state["pause_event"].clear()
                if pause_btn:
                    pause_btn.setText("继续")

        def on_cancel():
            dl_state["cancelled"] = True
            dl_state["pause_event"].set()  # 解除暂停以让线程退出

        if pause_btn:
            pause_btn.clicked.disconnect()
            pause_btn.clicked.connect(on_pause)
        if cancel_btn:
            cancel_btn.clicked.disconnect()
            cancel_btn.clicked.connect(on_cancel)

        tmp_path = target_path + ".downloading"

        def do_download():
            try:
                req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req, timeout=60)
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                block_size = 65536
                with open(tmp_path, "wb") as f:
                    while True:
                        if dl_state["cancelled"]:
                            break
                        dl_state["pause_event"].wait()  # 暂停时阻塞
                        if dl_state["cancelled"]:
                            break
                        chunk = resp.read(block_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = int(downloaded * 100 / total)
                            mb = downloaded / (1024 * 1024)
                            total_mb = total / (1024 * 1024)
                            QTimer.singleShot(0, lambda p=pct, m=mb, t=total_mb: (
                                dl_progress.setValue(p),
                                dl_status_label.setText(f"{m:.1f}/{t:.1f}MB")
                            ))
                        elif downloaded % (1024 * 1024) < block_size:
                            mb = downloaded / (1024 * 1024)
                            QTimer.singleShot(0, lambda m=mb: dl_status_label.setText(f"{m:.1f}MB"))

                if dl_state["cancelled"]:
                    if os.path.isfile(tmp_path):
                        os.remove(tmp_path)
                    QTimer.singleShot(0, lambda: (
                        progress_row.setVisible(False),
                        self._log(f"× v{version} 下载已取消", "warn")
                    ))
                    return

                # 下载完成
                if os.path.isfile(tmp_path) and os.path.getsize(tmp_path) > 1024 * 1024:
                    os.replace(tmp_path, target_path)
                    dl_state["done"] = True
                    QTimer.singleShot(0, lambda: (
                        dl_progress.setValue(100),
                        dl_status_label.setText("下载完成"),
                        self._log(f"✓ v{version} 下载完成，已保存到 ver/ 目录", "ok")
                    ))
                    import time; time.sleep(0.5)
                    # 刷新版本列表
                    QTimer.singleShot(500, self._render_active_tab)
                    # 自动切换
                    QTimer.singleShot(800, lambda: self._switch_to_exe(target_path))
                else:
                    if os.path.isfile(tmp_path):
                        os.remove(tmp_path)
                    QTimer.singleShot(0, lambda: (
                        progress_row.setVisible(False),
                        self._log(f"× v{version} 下载文件异常", "err")
                    ))
            except Exception as e:
                if os.path.isfile(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                QTimer.singleShot(0, lambda: (
                    progress_row.setVisible(False),
                    self._log(f"× v{version} 下载失败: {e}", "err")
                ))

        t = threading.Thread(target=do_download, daemon=True)
        t.start()

    def _download_exe_to_ver(self, url, target_path, version, fallback_page):
        """应用内下载EXE到ver/目录，下载完成后自动切换"""
        self._log(f"⬇ 正在下载 v{version}...", "info")

        # 创建下载进度对话框
        dlg = QDialog(self)
        dlg.setWindowTitle(f"下载 v{version}")
        dlg.setFixedSize(420, 140)
        dlg.setStyleSheet("QDialog { background-color: #1A1A1A; }")
        dlg_layout = QVBoxLayout(dlg)
        dlg_layout.setContentsMargins(16, 12, 16, 12)
        dlg_layout.setSpacing(8)

        status_label = QLabel(f"正在下载 v{version}...")
        status_label.setStyleSheet("font-size: 11px; color: #DDDDDD; background: transparent; border: none;")
        dlg_layout.addWidget(status_label)

        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(0)
        progress.setStyleSheet("""
            QProgressBar { background-color: #222222; border: 1px solid #333333; border-radius: 4px; height: 20px; text-align: center; color: #FFFFFF; font-size: 10px; }
            QProgressBar::chunk { background-color: #CC0000; border-radius: 3px; }
        """)
        dlg_layout.addWidget(progress)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet("QPushButton { background-color: #333; color: #aaa; border: 1px solid #444; border-radius: 4px; padding: 4px 16px; font-size: 10px; } QPushButton:hover { background-color: #444; color: #fff; }")
        btn_row.addWidget(cancel_btn)
        dlg_layout.addLayout(btn_row)

        # 下载状态
        download_state = {"cancelled": False, "done": False}

        def on_cancel():
            download_state["cancelled"] = True
            dlg.reject()

        cancel_btn.clicked.connect(on_cancel)

        dlg.show()
        QApplication.processEvents()

        # 执行下载
        import urllib.request
        tmp_path = target_path + ".downloading"

        def do_download():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req, timeout=60)
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                block_size = 65536
                with open(tmp_path, "wb") as f:
                    while True:
                        if download_state["cancelled"]:
                            break
                        chunk = resp.read(block_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = int(downloaded * 100 / total)
                            QTimer.singleShot(0, lambda p=pct: progress.setValue(p))
                            if downloaded % (1024 * 1024) < block_size:
                                mb = downloaded / (1024 * 1024)
                                total_mb = total / (1024 * 1024)
                                QTimer.singleShot(0, lambda m=mb, t=total_mb: status_label.setText(f"正在下载 v{version}... {m:.1f}/{t:.1f} MB"))
                                QApplication.processEvents()

                if download_state["cancelled"]:
                    if os.path.isfile(tmp_path):
                        os.remove(tmp_path)
                    return

                # 下载完成
                if os.path.isfile(tmp_path) and os.path.getsize(tmp_path) > 1024 * 1024:
                    os.replace(tmp_path, target_path)
                    download_state["done"] = True
                    QTimer.singleShot(0, lambda: status_label.setText(f"✓ v{version} 下载完成！"))
                    QTimer.singleShot(0, lambda: progress.setValue(100))
                    QApplication.processEvents()
                    import time
                    time.sleep(0.5)
                    dlg.accept()
                    self._log(f"✓ v{version} 下载完成，已保存到 ver/ 目录", "ok")
                    # 自动切换
                    QTimer.singleShot(500, lambda: self._switch_to_exe(target_path))
                else:
                    if os.path.isfile(tmp_path):
                        os.remove(tmp_path)
                    QTimer.singleShot(0, lambda: status_label.setText("× 下载文件异常，请在浏览器下载"))
                    self._log(f"× v{version} 下载文件异常", "err")
                    import time
                    time.sleep(1)
                    dlg.reject()
                    # 回退到浏览器
                    import webbrowser
                    webbrowser.open(fallback_page)
            except Exception as e:
                if os.path.isfile(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                QTimer.singleShot(0, lambda: status_label.setText(f"× 下载失败: {e}"))
                self._log(f"× v{version} 下载失败: {e}", "err")
                import time
                time.sleep(1)
                dlg.reject()
                # 回退到浏览器
                try:
                    import webbrowser
                    webbrowser.open(fallback_page)
                except Exception:
                    pass

        import threading
        t = threading.Thread(target=do_download, daemon=True)
        t.start()

    def _on_download_update(self):
        if not hasattr(self, '_latest_info') or not self._latest_info:
            if self._latest_version:
                self._on_download_version({"version": self._latest_version, "filename": f"{APP_NAME}-v{self._latest_version}.exe"})
                return
            return
        self._on_download_version(self._latest_info)

    def _open_release_page(self):
        source_key = self._effective_source_key()
        source = UPDATE_SOURCES.get(source_key, UPDATE_SOURCES.get('github_mirror', {}))
        release_page = source.get("releases_url", "")
        if release_page and "/api." in release_page:
            release_page = "https://github.com/yunjii-cn/vi/releases"
        try:
            import webbrowser
            webbrowser.open(release_page or "https://github.com/yunjii-cn/vi/releases")
        except Exception as e:
            self._log(f"× 无法打开Release页面: {e}", "err")

    def _switch_to_exe(self, exe_path, git_commit=""):
        if not os.path.exists(exe_path):
            self._log(f"× 版本文件不存在: {exe_path}", "err")
            return

        # 验证EXE文件有效性（大小至少1MB）
        try:
            exe_size = os.path.getsize(exe_path)
            if exe_size < 1024 * 1024:
                self._log(f"× 版本文件异常（过小: {exe_size} 字节），可能已损坏", "err")
                return
        except Exception as e:
            self._log(f"× 无法读取版本文件: {e}", "err")
            return

        dev_dir = _find_dev_dir()
        entry_exe = os.path.join(dev_dir, f"{BRAND_NAME}.exe")

        # 备份当前入口EXE（用于回滚）
        backup_exe = entry_exe + ".bak"
        if os.path.isfile(entry_exe):
            try:
                shutil.copy2(entry_exe, backup_exe)
            except Exception:
                backup_exe = ""

        # 更新硬链接指向新版本
        success = _create_hardlink(exe_path, entry_exe)
        if not success:
            self._log("× 切换版本失败：无法更新启动入口", "err")
            return

        # 先停止所有服务
        self._log("⏳ 正在停止服务并切换版本...", "info")
        try:
            self._stop_all()
        except Exception:
            pass

        # 等待端口释放
        import time
        time.sleep(1)

        # 启动新版本
        cmd = f'ping -n 3 127.0.0.1 >nul & start "" "{entry_exe}"'
        subprocess.Popen(cmd, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)

        # 清理备份
        if backup_exe and os.path.isfile(backup_exe):
            try:
                os.remove(backup_exe)
            except Exception:
                pass

        self.close()

    def _show_update_log(self):
        versions_json_path = self._resolve_versions_json_path()
        log_text = ""
        if versions_json_path and os.path.exists(versions_json_path):
            try:
                with open(versions_json_path, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                vlist = raw.get("versions", raw) if isinstance(raw, dict) else raw
                for v in vlist:
                    ver = v.get("version", "")
                    date = v.get("date", "")
                    msg = v.get("message", "")
                    changes = v.get("changes", [])
                    log_text += f"v{ver}  ({date})\n"
                    if msg:
                        log_text += f"  {msg}\n"
                    for ch in changes:
                        log_text += f"  • {ch}\n"
                    log_text += "\n"
            except:
                log_text = "无法读取版本日志。"
        else:
            log_text = "暂无版本日志。"
        dlg = QDialog(self)
        dlg.setWindowTitle("更新日志")
        dlg.setMinimumSize(600, 500)
        dlg.setStyleSheet("QDialog { background-color: #1A1A1A; }")
        dlg_layout = QVBoxLayout(dlg)
        dlg_layout.setContentsMargins(15, 15, 15, 15)
        title = QLabel("📋 更新日志")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold))
        title.setStyleSheet("color: #FFFFFF; background: transparent; border: none;")
        dlg_layout.addWidget(title)
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(log_text)
        text_edit.setStyleSheet("QTextEdit { background-color: #111113; border: 1px solid #333333; border-radius: 6px; color: #DDDDDD; font-size: 12px; padding: 8px; }")
        dlg_layout.addWidget(text_edit, 1)
        close_btn = QPushButton("✖ 关闭")
        close_btn.setStyleSheet("QPushButton { background-color: #333333; color: #AAAAAA; border: 1px solid #444444; border-radius: 6px; padding: 6px 20px; font-size: 12px; } QPushButton:hover { background-color: #444444; color: #FFFFFF; }")
        close_btn.clicked.connect(dlg.accept)
        dlg_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
        dlg.exec()

    def _download_model(self, model_id):
        if not model_id:
            self._log("× 模型ID为空，无法下载", "err")
            return

        info = LTX_MODELS.get(model_id)
        if not info:
            if hasattr(self, '_model_rows'):
                for r in self._model_rows:
                    if r.get("model_id") == model_id and r.get("source") in ("registry", "fallback"):
                        info = {
                            "repo": r.get("repo_id", "Lightricks/LTX-2.3"),
                            "file": r.get("filename", r.get("name", "")),
                            "size_bytes": r.get("size_gb", 0) * 1024 * 1024 * 1024,
                            "required": r.get("tag") == "必需",
                            "desc": r.get("description", ""),
                            "is_folder": r.get("is_folder", False),
                            "modelscope_id": r.get("modelscope_id", r.get("repo_id", "Lightricks/LTX-2.3")),
                        }
                        break
            if not info:
                self._log(f"× 未找到模型信息: {model_id}", "err")
                return

        models_dir = self._models_dir or os.path.join(self._data_dir or "", "models")
        os.makedirs(models_dir, exist_ok=True)

        python_exe = self._python_exe
        if not python_exe or not os.path.exists(python_exe):
            for candidate in [
                os.path.join(self._data_dir, ".venv", "Scripts", "python.exe"),
                os.path.join(self._data_dir, "venv", "Scripts", "python.exe"),
                os.path.join(self._app_resources, "venv", "Scripts", "python.exe"),
                os.path.join(self._app_resources, "python", "python.exe"),
            ]:
                if os.path.exists(candidate):
                    python_exe = candidate
                    break
        if not python_exe or not os.path.exists(python_exe):
            self._log("× 找不到Python环境，无法下载", "err")
            return

        source = self.model_source_combo.currentData() if hasattr(self, 'model_source_combo') else "hf_mirror"
        source_name = {"hf_mirror": "HF-Mirror", "hf_official": "HuggingFace", "modelscope": "ModelScope"}.get(source, source)

        self._log(f"▼ 开始下载 {info['file']} ({info['size_bytes']/1024/1024/1024:.1f}GB) [{source_name}]", "info")
        self._log(f"  [DBG] model_id={model_id}, repo={info['repo']}, is_folder={info.get('is_folder', False)}, size_bytes={info['size_bytes']}", "info")
        self._log(f"  [DBG] python_exe={python_exe}", "info")
        self._log(f"  [DBG] models_dir={models_dir}", "info")

        if not hasattr(self, '_download_procs'):
            self._download_procs = {}

        is_folder = info.get("is_folder", False)
        target_path = os.path.join(models_dir, info["file"])

        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        env["PYTHONUNBUFFERED"] = "1"
        if source == "hf_mirror" or source == "modelscope":
            deploy_source = self.deploy_source_combo.currentData() if hasattr(self, 'deploy_source_combo') else "tsinghua"
            if deploy_source in MIRROR_SOURCES:
                env["HF_ENDPOINT"] = MIRROR_SOURCES[deploy_source]["hf_endpoint"]
            else:
                env["HF_ENDPOINT"] = MIRRORS["hf_mirror"]
        elif source == "hf_official":
            env.pop("HF_ENDPOINT", None)

        progress_path = os.path.join(models_dir, f".dl_progress_{model_id}")
        try:
            if os.path.exists(progress_path):
                os.remove(progress_path)
        except OSError:
            pass

        self._log(f"  [DBG] HF_ENDPOINT={env.get('HF_ENDPOINT', '(not set)')}", "info")

        # 步骤1后台下载时限制并发，部署维护优先
        max_workers_arg = ""
        if getattr(self, '_guide_active', False) and getattr(self, '_guide_step', 0) == 1:
            max_workers_arg = ", max_workers=1"

        if is_folder:
            dl_script = (
                "import sys\n"
                f"try:\n"
                f"    from huggingface_hub import snapshot_download\n"
                f"    snapshot_download(repo_id='{info['repo']}', local_dir=r'{target_path}', local_dir_use_symlinks=False{max_workers_arg})\n"
                f"except Exception as e:\n"
                f"    sys.stderr.write(f'SCRIPT_ERROR:{{e}}\\n')\n"
            )
        else:
            dl_script = (
                "import sys\n"
                f"try:\n"
                f"    from huggingface_hub import hf_hub_download\n"
                f"    hf_hub_download(repo_id='{info['repo']}', filename='{info['file']}', local_dir=r'{models_dir}', resume_download=True)\n"
                f"except Exception as e:\n"
                f"    sys.stderr.write(f'SCRIPT_ERROR:{{e}}\\n')\n"
            )

        script_path = os.path.join(models_dir, f".dl_script_{model_id}.py")
        with open(script_path, 'w', encoding='utf-8') as sf:
            sf.write(dl_script)

        cmd = [python_exe, "-u", script_path]

        self._download_procs[model_id] = {
            "cmd": cmd, "env": env, "paused": False, "cancelled": False,
            "target_path": target_path, "is_folder": is_folder,
            "expected_bytes": info["size_bytes"],
            "progress_path": progress_path, "script_path": script_path,
            "current_pct": 0,
        }

        self._set_model_downloading(model_id, True)
        self._start_download_progress_timer()

        def run_download():
            all_stderr = ""
            try:
                proc = hidden_popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
            except Exception as e:
                QTimer.singleShot(0, lambda: self._on_model_download_done(model_id, False, source_name, f"启动失败: {e}"))
                self._download_procs.pop(model_id, None)
                return

            self._download_procs[model_id]["proc"] = proc
            self._log(f"  下载进程已启动 PID={proc.pid}, 进度文件={progress_path}", "info")

            debug_log_path = os.path.join(models_dir, f".dl_debug_{model_id}.log")
            try:
                with open(debug_log_path, 'w', encoding='utf-8') as df:
                    df.write(f"=== Download Debug Log ===\n")
                    df.write(f"model_id={model_id}\n")
                    df.write(f"repo={info['repo']}\n")
                    df.write(f"file={info['file']}\n")
                    df.write(f"is_folder={is_folder}\n")
                    df.write(f"expected_bytes={info['size_bytes']}\n")
                    df.write(f"target_path={target_path}\n")
                    df.write(f"progress_path={progress_path}\n")
                    df.write(f"script_path={script_path}\n")
                    df.write(f"models_dir={models_dir}\n")
                    df.write(f"HF_ENDPOINT={env.get('HF_ENDPOINT', 'not set')}\n")
                    df.write(f"PYTHONUNBUFFERED={env.get('PYTHONUNBUFFERED', 'not set')}\n")
                    df.write(f"python_exe={python_exe}\n")
                    df.write(f"cmd={' '.join(cmd)}\n")
                    df.flush()
            except Exception:
                pass

            def dbg_log(msg):
                try:
                    with open(debug_log_path, 'a', encoding='utf-8') as df:
                        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        df.write(f"[{ts}] {msg}\n")
                        df.flush()
                except Exception:
                    pass

            def drain_stderr():
                nonlocal all_stderr
                first_lines = []
                try:
                    while True:
                        chunk = proc.stderr.read(4096)
                        if not chunk:
                            break
                        text = chunk.decode("utf-8", errors="replace")
                        all_stderr += text
                        if len(first_lines) < 5:
                            for line in text.split("\n"):
                                line = line.strip()
                                if line and len(first_lines) < 5:
                                    first_lines.append(line[:200])
                            if len(first_lines) >= 1:
                                dbg_log(f"STDERR_FIRST: {first_lines}")
                except Exception:
                    pass
                if all_stderr:
                    dbg_log(f"STDERR_TOTAL_LEN={len(all_stderr)}")

            drain_thread = threading.Thread(target=drain_stderr, daemon=True)
            drain_thread.start()

            def progress_monitor():
                try:
                    expected = info["size_bytes"]
                    last_pct = 0
                    tick_count = 0
                    dbg_log(f"START expected={expected} target_path={target_path} is_folder={is_folder}")
                    while proc.poll() is None:
                        tick_count += 1
                        if self._download_procs.get(model_id, {}).get("cancelled"):
                            dbg_log(f"tick={tick_count} CANCELLED")
                            break
                        pct = 0
                        downloaded_bytes = 0
                        try:
                            if is_folder:
                                if os.path.isdir(target_path):
                                    for dp, dn, fns in os.walk(target_path):
                                        for fn in fns:
                                            try:
                                                downloaded_bytes += os.path.getsize(os.path.join(dp, fn))
                                            except OSError:
                                                pass
                            else:
                                cache_dir = os.path.join(models_dir, ".cache", "huggingface", "download")
                                if os.path.isdir(cache_dir):
                                    for dp, dn, fns in os.walk(cache_dir):
                                        for fn in fns:
                                            if fn.endswith(".incomplete"):
                                                try:
                                                    downloaded_bytes += os.path.getsize(os.path.join(dp, fn))
                                                except OSError:
                                                    pass
                                if os.path.exists(target_path):
                                    try:
                                        downloaded_bytes += os.path.getsize(target_path)
                                    except OSError:
                                        pass
                            if downloaded_bytes > 0 and expected > 0:
                                pct = min(100, int(downloaded_bytes * 100 / expected))
                        except Exception:
                            pass
                        if tick_count <= 5 or pct > last_pct:
                            dbg_log(f"tick={tick_count} pct={pct} last_pct={last_pct} downloaded_bytes={downloaded_bytes if downloaded_bytes else 0}")
                        if pct > last_pct:
                            last_pct = pct
                            try:
                                self._download_procs[model_id]["current_pct"] = pct
                            except (KeyError, TypeError):
                                break
                        time.sleep(1)
                    try:
                        self._download_procs[model_id]["current_pct"] = 100
                    except (KeyError, TypeError):
                        pass
                    dbg_log(f"LOOP_END proc_returncode={proc.poll()} tick_count={tick_count} last_pct={last_pct}")
                except Exception as e:
                    dbg_log(f"CRASH {e}")
                    import traceback
                    dbg_log(f"TRACEBACK {traceback.format_exc()}")

            pmon_thread = threading.Thread(target=progress_monitor, daemon=True)
            pmon_thread.start()

            try:
                proc.wait()
            except Exception:
                pass

            drain_thread.join(timeout=5)

            for p in (progress_path, script_path):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass

            if self._download_procs.get(model_id, {}).get("cancelled"):
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                QTimer.singleShot(0, lambda: self._on_model_download_done(model_id, False, source_name, "已取消"))
            elif proc.returncode == 0:
                if is_folder or os.path.exists(target_path):
                    QTimer.singleShot(0, lambda: self._on_model_download_done(model_id, True, source_name))
                else:
                    QTimer.singleShot(0, lambda: self._on_model_download_done(model_id, False, source_name, "文件未找到"))
            else:
                err_text = all_stderr[:500] if all_stderr else f"exit code {proc.returncode}"
                if all_stderr:
                    self._log(f"  [DBG] subprocess stderr (first 300): {all_stderr[:300]}", "info")
                QTimer.singleShot(0, lambda: self._on_model_download_done(model_id, False, source_name, err_text))

        threading.Thread(target=run_download, daemon=True).start()

    def _set_model_downloading(self, model_id, downloading):
        if not hasattr(self, '_download_procs'):
            self._download_procs = {}
        if not hasattr(self, '_model_table'):
            return
        for row in range(self._model_table.rowCount()):
            ops_widget = self._model_table.cellWidget(row, 8)
            if not ops_widget:
                continue
            if hasattr(self, '_model_rows') and row < len(self._model_rows):
                r = self._model_rows[row]
                if r.get("model_id") != model_id:
                    continue
            else:
                continue

            layout = ops_widget.layout()
            if not layout:
                continue
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget()
                if w:
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()

            if downloading:
                status_item = self._model_table.item(row, 6)
                if status_item:
                    status_item.setText("进度 0%")
                    status_item.setForeground(QColor("#FFA726"))

                pause_btn = QPushButton("⏸ 暂停")
                pause_btn.setObjectName(f"dl_pause_{model_id}")
                pause_btn.setFixedHeight(24)
                pause_btn.setStyleSheet("""
                    QPushButton { background-color: #E65100; color: white; border: none; border-radius: 3px;
                        padding: 3px 8px; font-size: 10px; }
                    QPushButton:hover { background-color: #F57C00; }
                    QPushButton:pressed { background-color: #BF360C; }
                """)
                pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                pause_btn.clicked.connect(lambda checked, mid=model_id: self._toggle_pause_download(mid))
                layout.addWidget(pause_btn)

                cancel_btn = QPushButton("✖ 取消")
                cancel_btn.setFixedHeight(24)
                cancel_btn.setStyleSheet("""
                    QPushButton { background-color: #CC0000; color: white; border: none; border-radius: 3px;
                        padding: 3px 8px; font-size: 10px; }
                    QPushButton:hover { background-color: #FF0000; }
                    QPushButton:pressed { background-color: #DD0000; }
                """)
                cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                cancel_btn.clicked.connect(lambda checked, mid=model_id: self._cancel_download(mid))
                layout.addWidget(cancel_btn)
            else:
                pass

            layout.addStretch()

    def _start_download_progress_timer(self):
        if not hasattr(self, '_dl_progress_timer'):
            self._dl_progress_timer = QTimer(self)
            self._dl_progress_timer.timeout.connect(self._poll_download_progress)
        if not self._dl_progress_timer.isActive():
            self._dl_progress_timer.start(1000)

    def _stop_download_progress_timer(self):
        if hasattr(self, '_dl_progress_timer') and self._dl_progress_timer.isActive():
            self._dl_progress_timer.stop()

    def _poll_download_progress(self):
        if not hasattr(self, '_download_procs') or not self._download_procs:
            self._stop_download_progress_timer()
            return
        if not hasattr(self, '_model_table') or not hasattr(self, '_model_rows'):
            return
        try:
            row_count = self._model_table.rowCount()
        except RuntimeError:
            self._stop_download_progress_timer()
            return
        done_ids = []
        for model_id, dl in list(self._download_procs.items()):
            pct = dl.get("current_pct", 0)
            proc = dl.get("proc")
            proc_done = proc is not None and proc.poll() is not None
            if proc_done:
                done_ids.append(model_id)
                continue
            for row_idx, r in enumerate(self._model_rows):
                if r.get("model_id") == model_id:
                    if row_idx < row_count:
                        try:
                            status_item = self._model_table.item(row_idx, 6)
                            if status_item:
                                if pct >= 100:
                                    status_item.setText("处理中…")
                                    status_item.setForeground(QColor("#42A5F5"))
                                else:
                                    status_item.setText(f"进度 {pct}%")
                                    status_item.setForeground(QColor("#FFA726"))
                        except RuntimeError:
                            pass
                    break
        for mid in done_ids:
            for row_idx, r in enumerate(self._model_rows):
                if r.get("model_id") == mid:
                    if row_idx < row_count:
                        try:
                            status_item = self._model_table.item(row_idx, 6)
                            if status_item:
                                status_item.setText("已下载")
                                status_item.setForeground(QColor("#66BB6A"))
                            ops_widget = self._model_table.cellWidget(row_idx, 8)
                            if ops_widget:
                                layout = ops_widget.layout()
                                if layout:
                                    while layout.count():
                                        item = layout.takeAt(0)
                                        w = item.widget()
                                        if w:
                                            w.hide()
                                            w.setParent(None)
                                            w.deleteLater()
                                    lp = r.get("local_path", "")
                                    if lp:
                                        rm_btn = QPushButton("🗑 删除")
                                        rm_btn.setFixedHeight(24)
                                        rm_btn.setStyleSheet("QPushButton { background-color: #1565C0; color: white; border: none; border-radius: 3px; padding: 3px 8px; font-size: 10px; } QPushButton:hover { background-color: #1976D2; } QPushButton:pressed { background-color: #0D47A1; }")
                                        rm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                                        rm_btn.clicked.connect(lambda checked, p=lp: self._delete_local_model_file(p))
                                        layout.addWidget(rm_btn)
                                    layout.addStretch()
                        except RuntimeError:
                            pass
                    break
            if hasattr(self, '_download_procs'):
                self._download_procs.pop(mid, None)
        if not self._download_procs:
            self._stop_download_progress_timer()
        if done_ids:
            QTimer.singleShot(1500, self._refresh_model_status)
        # 更新引导横幅的模型下载子步骤提示
        if self._guide_active and self._guide_step in (1, 2) and self._download_procs:
            active_names = []
            for model_id, dl in self._download_procs.items():
                info = LTX_MODELS.get(model_id)
                name = info.get("desc", info.get("file", model_id)) if info else model_id
                pct = dl.get("current_pct", 0)
                active_names.append(f"{name} {pct}%")
            if active_names:
                prefix = "后台下载" if self._guide_step == 1 else ""
                self._guide_model_sub_hint = f"{prefix}{'、'.join(active_names)}"
                self._update_guide_banner()

    def _finish_download_ui(self, model_id):
        pass

    def _toggle_pause_download(self, model_id):
        if not hasattr(self, '_download_procs') or model_id not in self._download_procs:
            return
        dl = self._download_procs[model_id]
        proc = dl.get("proc")
        if not proc or proc.poll() is not None:
            return

        pause_btn_name = f"dl_pause_{model_id}"

        if dl.get("paused"):
            self._resume_process_threads(proc.pid)
            dl["paused"] = False
            if hasattr(self, '_model_table'):
                for row in range(self._model_table.rowCount()):
                    ops_widget = self._model_table.cellWidget(row, 8)
                    if not ops_widget:
                        continue
                    btn = ops_widget.findChild(QPushButton, pause_btn_name)
                    if btn:
                        btn.setText("⏸ 暂停")
                        btn.setStyleSheet("""
                            QPushButton { background-color: #E65100; color: white; border: none; border-radius: 3px;
                                padding: 3px 8px; font-size: 10px; }
                            QPushButton:hover { background-color: #F57C00; }
                            QPushButton:pressed { background-color: #BF360C; }
                        """)
                        btn.setCursor(Qt.CursorShape.PointingHandCursor)
                        break
            if hasattr(self, '_model_rows'):
                for row_idx, r in enumerate(self._model_rows):
                    if r.get("model_id") == model_id:
                        status_item = self._model_table.item(row_idx, 6)
                        if status_item:
                            status_item.setText(f"进度 {dl.get('current_pct', 0)}%")
                            status_item.setForeground(QColor("#FFA726"))
                        break
        else:
            self._suspend_process_threads(proc.pid)
            dl["paused"] = True
            if hasattr(self, '_model_table'):
                for row in range(self._model_table.rowCount()):
                    ops_widget = self._model_table.cellWidget(row, 8)
                    if not ops_widget:
                        continue
                    btn = ops_widget.findChild(QPushButton, pause_btn_name)
                    if btn:
                        btn.setText("▶ 继续")
                        btn.setStyleSheet("""
                            QPushButton { background-color: #1565C0; color: white; border: none; border-radius: 3px;
                                padding: 3px 8px; font-size: 10px; }
                            QPushButton:hover { background-color: #1976D2; }
                            QPushButton:pressed { background-color: #0D47A1; }
                        """)
                        btn.setCursor(Qt.CursorShape.PointingHandCursor)
                        break
            if hasattr(self, '_model_rows'):
                for row_idx, r in enumerate(self._model_rows):
                    if r.get("model_id") == model_id:
                        status_item = self._model_table.item(row_idx, 6)
                        if status_item:
                            status_item.setText(f"⏸ 进度 {dl.get('current_pct', 0)}%")
                            status_item.setForeground(QColor("#FFB74D"))
                        break

    def _cancel_download(self, model_id):
        if not hasattr(self, '_download_procs') or model_id not in self._download_procs:
            return
        dl = self._download_procs[model_id]
        dl["cancelled"] = True
        proc = dl.get("proc")
        if proc and proc.poll() is None:
            if dl.get("paused"):
                self._resume_process_threads(proc.pid)
            proc.kill()

    @staticmethod
    def _suspend_process_threads(pid):
        try:
            import ctypes
            TH32CS_SNAPTHREAD = 0x00000004
            kernel32 = ctypes.windll.kernel32
            snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
            if snap == -1:
                return
            class THREADENTRY32(ctypes.Structure):
                _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
                            ("th32ThreadID", ctypes.c_ulong), ("th32OwnerProcessID", ctypes.c_ulong),
                            ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                            ("dwFlags", ctypes.c_ulong)]
            entry = THREADENTRY32()
            entry.dwSize = ctypes.sizeof(THREADENTRY32)
            if kernel32.Thread32First(snap, ctypes.byref(entry)):
                while True:
                    if entry.th32OwnerProcessID == pid:
                        handle = kernel32.OpenThread(0x0002, False, entry.th32ThreadID)
                        if handle:
                            kernel32.SuspendThread(handle)
                            kernel32.CloseHandle(handle)
                    if not kernel32.Thread32Next(snap, ctypes.byref(entry)):
                        break
            kernel32.CloseHandle(snap)
        except Exception:
            pass

    @staticmethod
    def _resume_process_threads(pid):
        try:
            import ctypes
            TH32CS_SNAPTHREAD = 0x00000004
            kernel32 = ctypes.windll.kernel32
            snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
            if snap == -1:
                return
            class THREADENTRY32(ctypes.Structure):
                _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
                            ("th32ThreadID", ctypes.c_ulong), ("th32OwnerProcessID", ctypes.c_ulong),
                            ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                            ("dwFlags", ctypes.c_ulong)]
            entry = THREADENTRY32()
            entry.dwSize = ctypes.sizeof(THREADENTRY32)
            if kernel32.Thread32First(snap, ctypes.byref(entry)):
                while True:
                    if entry.th32OwnerProcessID == pid:
                        handle = kernel32.OpenThread(0x0002, False, entry.th32ThreadID)
                        if handle:
                            kernel32.ResumeThread(handle)
                            kernel32.CloseHandle(handle)
                    if not kernel32.Thread32Next(snap, ctypes.byref(entry)):
                        break
            kernel32.CloseHandle(snap)
        except Exception:
            pass

    def _on_model_download_done(self, model_id, success, source_name, error=""):
        info = LTX_MODELS.get(model_id)
        fname = info["file"] if info else model_id
        if not info and hasattr(self, '_model_rows'):
            for r in self._model_rows:
                if r.get("model_id") == model_id:
                    fname = r.get("filename", r.get("name", model_id))
                    break
        if success:
            self._log(f"√ 模型 {fname} 下载完成 ({source_name})", "ok")
        else:
            self._log(f"× 模型 {fname} 下载失败 ({source_name}): {error}", "err")
        if hasattr(self, '_download_procs'):
            self._download_procs.pop(model_id, None)
            if not self._download_procs:
                self._stop_download_progress_timer()
        self._refresh_model_status()
        # 引导步骤1或2：检查必需模型下载状态
        info = LTX_MODELS.get(model_id)
        is_required = info.get("required", False) if info else False
        if is_required and self._guide_active and self._guide_step in (1, 2):
            required_ok = self._check_required_models_ok()
            self._write_debug_log(f"[引导] 必需模型 {fname} 下载{'成功' if success else '失败'}，必需模型全部就绪: {required_ok}")
            if required_ok:
                if self._guide_step == 1:
                    # 部署还在进行中，模型已就绪，标记等待部署完成
                    self._write_debug_log("[引导] 必需模型已全部就绪，等待部署维护完成")
                    self._guide_model_sub_hint = "模型已就绪，等待部署维护完成…"
                    self._update_guide_banner()
                else:
                    # 步骤2，模型就绪，推进到步骤3
                    self._guide_advance(3)
            elif not success:
                if self._guide_step == 2:
                    # 步骤2下载失败，关闭全自动开关
                    self._guide_on_error()
                # 步骤1时模型下载失败，不立即报错，等部署完成后再处理

    def _uninstall_model(self, model_id):
        info = LTX_MODELS.get(model_id)
        if not info:
            return
        if info.get("required", False):
            self._log(f"⚠ {info['file']} 是必需模型，无法卸载", "warn")
            return
        model_path = os.path.join(self._models_dir, info["file"])
        if not os.path.exists(model_path):
            return
        self._log(f"正在卸载模型 {info['file']} ({info['size_bytes']/1024/1024/1024:.1f}GB)...", "info")
        try:
            os.remove(model_path)
            self._log(f"√ 已卸载模型 {info['file']}", "ok")
        except Exception as e:
            self._log(f"× 卸载模型失败: {e}", "err")
        self._refresh_model_status()

    def _download_all_models(self):
        for model_id, info in LTX_MODELS.items():
            model_path = os.path.join(self._models_dir, info["file"])
            expected_bytes = info["size_bytes"]
            if info.get("is_folder", False):
                if os.path.exists(model_path) and os.path.isdir(model_path):
                    folder_size = sum(f.stat().st_size for f in __import__('pathlib').Path(model_path).rglob("*") if f.is_file())
                    is_complete = folder_size > expected_bytes * 0.5
                else:
                    is_complete = False
            else:
                is_complete = os.path.exists(model_path) and os.path.getsize(model_path) > expected_bytes * 0.9
            if not is_complete:
                self._download_model(model_id)

    def _check_model_integrity(self):
        results = []
        for model_id, info in LTX_MODELS.items():
            model_path = os.path.join(self._models_dir, info["file"])
            expected_bytes = info["size_bytes"]
            expected_gb = expected_bytes / 1024 / 1024 / 1024
            if not os.path.exists(model_path):
                results.append(f"× {info['file']}: 未下载 (官方 {expected_gb:.1f}GB)")
            else:
                if info.get("is_folder", False):
                    folder_size = sum(f.stat().st_size for f in __import__('pathlib').Path(model_path).rglob("*") if f.is_file())
                    ratio = folder_size / expected_bytes if expected_bytes > 0 else 0
                    if ratio > 0.5:
                        actual_gb = folder_size / 1024 / 1024 / 1024
                        results.append(f"√ {info['file']}: 完整 ({actual_gb:.2f}GB / {expected_gb:.1f}GB)")
                    else:
                        actual_mb = folder_size / 1024 / 1024
                        expected_mb = expected_bytes / 1024 / 1024
                        results.append(f"△ {info['file']}: 不完整 ({actual_mb:.0f}MB / {expected_mb:.0f}MB)")
                else:
                    actual_bytes = os.path.getsize(model_path)
                    ratio = actual_bytes / expected_bytes if expected_bytes > 0 else 0
                    if ratio > 0.9:
                        actual_gb = actual_bytes / 1024 / 1024 / 1024
                        results.append(f"√ {info['file']}: 完整 ({actual_gb:.2f}GB / {expected_gb:.1f}GB)")
                    else:
                        actual_mb = actual_bytes / 1024 / 1024
                        expected_mb = expected_bytes / 1024 / 1024
                        results.append(f"△ {info['file']}: 不完整 ({actual_mb:.0f}MB / {expected_mb:.0f}MB)")

        self._refresh_model_status()
        self._log("📋 模型完整性检测：", "info")
        for r in results:
            if r.startswith("√"):
                self._log(f"  {r}", "ok")
            elif r.startswith("△"):
                self._log(f"  {r}", "warn")
            else:
                self._log(f"  {r}", "err")

    def _refresh_model_status(self):
        if hasattr(self, '_model_table'):
            self._populate_model_table()

    def _switch_page(self, idx):
        self.page_stack.setCurrentIndex(idx)
        self.btn_home.setChecked(idx == 0)
        self.btn_deploy.setChecked(idx == 1)
        self.btn_models.setChecked(idx == 2)
        self.btn_update.setChecked(idx == 3)

    def _setup_tray(self):
        try:
            if hasattr(sys, '_MEIPASS'):
                base = sys._MEIPASS
            else:
                base = os.path.dirname(os.path.abspath(__file__))
            icon_path = None
            for name in ('ico.png', 'icon.png', 'icon.ico'):
                p = os.path.join(base, name)
                if os.path.exists(p):
                    icon_path = p
                    break
            if icon_path:
                tray_icon = QIcon(icon_path)
            else:
                tray_icon = QIcon()
            self.tray = QSystemTrayIcon(tray_icon, self)
            tray_menu = QMenu()
            show_action = tray_menu.addAction("显示主窗口")
            show_action.triggered.connect(self.showNormal)
            quit_action = tray_menu.addAction("退出")
            quit_action.triggered.connect(self._quit_app)
            self.tray.setContextMenu(tray_menu)
            self.tray.activated.connect(lambda reason: self.showNormal() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
            self.tray.show()
        except:
            pass

    def _deferred_init(self):
        try:
            if self._splash:
                self._splash.set_progress(0.3, "正在检测环境...")

            self._detect_paths_only()
            self._load_env_check_cache_to_ui()
            self._detect_running_services()

            if self._splash:
                self._splash.set_progress(0.6, "正在启动监控...")

            self.monitor.start()

            if self._splash:
                self._splash.set_progress(0.7, "正在检测浏览器...")

            self.browsers = self._detect_browsers()
            self.selected_browser = self.config.get("browser.default", "system")
            self.custom_browser_path = self.config.get("browser.custom_path", "")

            self.browser_combo.blockSignals(True)
            self.browser_combo.clear()
            for name, path in self.browsers.items():
                if path == "system":
                    self.browser_combo.addItem(name, path)
                else:
                    self.browser_combo.addItem(f"{name} ({path})", path)
            self.browser_combo.addItem("◇ 自定义浏览器...", "custom")

            if self.selected_browser == "custom" and self.custom_browser_path:
                for i in range(self.browser_combo.count()):
                    if self.browser_combo.itemData(i) == "custom":
                        self.browser_combo.setCurrentIndex(i)
                        break
                self.browser_path_edit.setText(self.custom_browser_path)
                self.browser_path_edit.setVisible(True)
                self.btn_select_browser.setVisible(True)
            else:
                for i in range(self.browser_combo.count()):
                    if self.browser_combo.itemData(i) == self.selected_browser:
                        self.browser_combo.setCurrentIndex(i)
                        break
            self.browser_combo.blockSignals(False)

            if self._splash:
                self._splash.set_progress(0.8, "正在加载配置...")

            size = self.config.get("ui.window_size", {"width": 1100, "height": 800})
            self.resize(size["width"], size["height"])
            screen = QApplication.primaryScreen().availableGeometry()
            self.move((screen.width() - self.width()) // 2, (screen.height() - self.height()) // 2)

            if self._splash:
                self._splash.set_progress(0.9, "即将就绪...")

            QTimer.singleShot(300, self._finish_splash)
            QTimer.singleShot(2000, self._delayed_gpu_detect)
            QTimer.singleShot(15000, self._splash_fallback)
            QTimer.singleShot(1000, self._auto_deploy_check)
            QTimer.singleShot(1500, self._schedule_update_cache_check)

            self._probe_timer = QTimer(self)
            self._probe_timer.timeout.connect(lambda: _DBG.run_probes() if _DBG else None)
            self._probe_timer.start(15000)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._log(f"△ 初始化异常: {e}", "err")

    def _schedule_update_cache_check(self):
        if not getattr(self, '_ver_cache_check_scheduled', False):
            self._ver_cache_check_scheduled = True
            self._load_update_cache_and_check()

    def _finish_splash(self):
        if self._splash and self._splash.isVisible():
            self._splash.set_progress(1.0, "加载完成！")
            self.show()
            self.raise_()
            self.activateWindow()
            self._splash.finish(self)
            self._splash.deleteLater()
            self._splash = None

    def _splash_fallback(self):
        if self._splash and self._splash.isVisible():
            self.show()
            self.raise_()
            self.activateWindow()
            self._splash.finish(self)
            self._splash.deleteLater()
            self._splash = None

    def _delayed_gpu_detect(self):
        pass

    def _detect_paths_only(self):
        project_root = self._project_root
        app_res = self._app_resources

        data_venv_python = None
        if self._exe_data_dir:
            for venv_name in (".venv", "venv"):
                p = os.path.join(self._exe_data_dir, venv_name, "Scripts", "python.exe")
                if os.path.exists(p):
                    data_venv_python = p
                    break
        venv_python = os.path.join(app_res, "venv", "Scripts", "python.exe")
        res_python = os.path.join(app_res, "python", "python.exe")
        ref_python = os.path.join(project_root, "项目参考", "环境包", "LTXDesktop", "python", "python.exe")
        sys_python = os.path.join(os.path.expanduser("~"), r"AppData\Local\LTXDesktop\python\python.exe")

        if data_venv_python and os.path.exists(data_venv_python):
            self._python_exe = data_venv_python
            self._pythonw_exe = os.path.join(os.path.dirname(data_venv_python), "pythonw.exe")
            if self._exe_data_dir:
                self._data_dir = self._exe_data_dir
        elif os.path.exists(venv_python):
            self._python_exe = venv_python
            self._pythonw_exe = os.path.join(os.path.dirname(venv_python), "pythonw.exe")
        elif os.path.exists(res_python):
            self._python_exe = res_python
            self._pythonw_exe = os.path.join(os.path.dirname(res_python), "pythonw.exe")
        elif os.path.exists(ref_python):
            self._python_exe = ref_python
            self._pythonw_exe = os.path.join(os.path.dirname(ref_python), "pythonw.exe")
            self._data_dir = self._exe_data_dir or os.path.join(project_root, "项目参考", "环境包", "LTXDesktop")
        elif os.path.exists(sys_python):
            self._python_exe = sys_python
            self._pythonw_exe = os.path.join(os.path.dirname(sys_python), "pythonw.exe")
            self._data_dir = self._exe_data_dir or os.path.join(os.path.expanduser("~"), r"AppData\Local\LTXDesktop")

        if not self._python_exe:
            dev_python = sys.executable
            if dev_python and os.path.isfile(dev_python) and "python" in os.path.basename(dev_python).lower():
                self._python_exe = dev_python
                self._pythonw_exe = os.path.join(os.path.dirname(dev_python), "pythonw.exe")
                self._data_dir = self._exe_data_dir or os.path.join(project_root, "data")

        res_backend = os.path.join(app_res, "backend")
        ltx_search = [
            os.path.join(project_root, "LTX Desktop", "LTX Desktop.exe"),
            os.path.join(project_root, "项目参考", "LTX Desktop", "LTX Desktop.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\LTX Desktop\LTX Desktop.exe"),
            r"C:\Program Files\LTX Desktop\LTX Desktop.exe",
            r"D:\Program Files\LTX Desktop\LTX Desktop.exe",
            r"E:\Program Files\LTX Desktop\LTX Desktop.exe",
        ]

        if os.path.exists(res_backend) and os.path.exists(os.path.join(res_backend, "ltx2_server.py")):
            self._backend_dir = res_backend
        else:
            for p in ltx_search:
                if os.path.exists(p):
                    self._ltx_install_dir = os.path.dirname(p)
                    self._backend_dir = os.path.join(self._ltx_install_dir, "resources", "backend")
                    break
            if self._backend_dir and not os.path.exists(self._backend_dir):
                self._backend_dir = None

        res_patches = os.path.join(app_res, "patches")
        ref_patches = os.path.join(project_root, "项目参考", "LTX2.3启动器", "patches")
        if os.path.exists(res_patches) and os.path.exists(os.path.join(res_patches, "runtime_policy.py")):
            self._patches_dir = res_patches
        elif os.path.exists(ref_patches):
            self._patches_dir = ref_patches

        res_ui = os.path.join(app_res, "ui")
        ref_ui = os.path.join(project_root, "项目参考", "LTX2.3启动器", "UI")
        if os.path.exists(res_ui) and os.path.exists(os.path.join(res_ui, "index.html")):
            self._ui_dir = res_ui
        elif os.path.exists(ref_ui):
            self._ui_dir = ref_ui

        data_models = os.path.join(self._data_dir, "models")
        os.makedirs(data_models, exist_ok=True)
        self._models_dir = data_models

        self._set_env_widget("project", f"√ {project_root}", "ok")

        if os.path.exists(venv_python):
            self._set_env_widget("python", "√ UV venv (res/venv)", "ok")
        elif os.path.exists(res_python):
            self._set_env_widget("python", "√ 整合包 (res/python)", "ok")
        elif os.path.exists(ref_python):
            self._set_env_widget("python", "√ 项目参考", "warn")
        elif os.path.exists(sys_python):
            self._set_env_widget("python", "√ 系统环境", "warn")
        elif self._python_exe:
            self._set_env_widget("python", f"√ 开发模式 ({os.path.basename(self._python_exe)})", "warn")
        else:
            self._set_env_widget("python", "× 未找到", "err", True)

        if os.path.exists(res_backend) and os.path.exists(os.path.join(res_backend, "ltx2_server.py")):
            self._set_env_widget("backend", "√ 整合包内置", "ok")
            self._set_env_widget("ltx", "√ 整合包内置", "ok")
        else:
            ltx_found = False
            for p in ltx_search:
                if os.path.exists(p):
                    self._set_env_widget("ltx", f"√ {os.path.dirname(p)}", "ok")
                    ltx_found = True
                    break
            if not ltx_found:
                self._set_env_widget("ltx", "× 未找到", "err", True)
            if self._backend_dir and os.path.exists(self._backend_dir):
                self._set_env_widget("backend", "√ 已找到", "ok")
            else:
                self._set_env_widget("backend", "× 未找到", "err", True)

        if os.path.exists(res_patches) and os.path.exists(os.path.join(res_patches, "runtime_policy.py")):
            self._set_env_widget("patches", "√ 整合包内置", "ok")
        elif os.path.exists(ref_patches):
            self._set_env_widget("patches", "√ 项目参考", "warn")
        else:
            self._set_env_widget("patches", "× 未找到", "err", True)

        if os.path.exists(res_ui) and os.path.exists(os.path.join(res_ui, "index.html")):
            self._set_env_widget("ui", "√ 整合包内置", "ok")
        elif os.path.exists(ref_ui):
            self._set_env_widget("ui", "√ 项目参考", "warn")
        else:
            self._set_env_widget("ui", "× 未找到", "err", True)

        data_models = os.path.join(self._data_dir, "models")
        if os.path.exists(data_models) and os.listdir(data_models):
            self._set_env_widget("models", "√ data/models", "ok")
        else:
            if self._models_dir and os.path.exists(self._models_dir):
                self._set_env_widget("models", "√ 自定义", "ok")
            else:
                self._set_env_widget("models", "△ 未配置", "warn", True)

    def _load_env_check_cache_to_ui(self):
        cached = self._load_env_check_cache()
        if cached:
            for key, info in cached.items():
                self._set_env_widget(key, info.get("text", "未检测"), info.get("status", "unknown"), info.get("fix_visible", False))

    def _auto_deploy_check(self):
        # 检查是否已完成引导
        if self.config.get("guide_completed", False):
            return
        # 如果环境就绪+模型齐全，标记引导完成
        uv_ok = self._check_uv_ok() if hasattr(self, '_check_uv_ok') else False
        python_ok = self._check_python_ok() if hasattr(self, '_check_python_ok') else False
        models_ok = self._check_required_models_ok() if hasattr(self, '_check_required_models_ok') else False
        if uv_ok and python_ok and models_ok:
            self.config.set("guide_completed", True)
            return
        # 如果环境已就绪但模型不全，从步骤2开始引导
        if uv_ok and python_ok and not models_ok:
            if hasattr(self, '_auto_deploy_prompted') and self._auto_deploy_prompted:
                return
            self._auto_deploy_prompted = True
            self._guide_step = 2
            self._show_newbie_guide()
            if self._guide_auto and self._guide_active:
                self._execute_guide_step()
            return
        # 如果没有python环境（首次），从步骤1开始引导
        if self._python_exe and os.path.exists(self._python_exe):
            return
        if hasattr(self, '_auto_deploy_prompted') and self._auto_deploy_prompted:
            return
        self._auto_deploy_prompted = True
        # 显示引导横幅，从步骤1开始
        self._show_newbie_guide()
        # 全自动模式下自动执行步骤1
        if self._guide_auto and self._guide_active:
            self._execute_guide_step()

    def _save_env_check_result(self):
        result = {}
        for key, (val_lbl, fix_btn) in self._env_check_widgets.items():
            ss = val_lbl.styleSheet()
            if "#66BB6A" in ss:
                st = "ok"
            elif "#FF0000" in ss:
                st = "err"
            elif "#FFA726" in ss:
                st = "warn"
            elif "#42A5F5" in ss:
                st = "pending"
            else:
                st = "unknown"
            result[key] = {
                "text": val_lbl.text(),
                "status": st,
                "fix_visible": fix_btn.width() > 0
            }
        result_path = os.path.join(self._data_dir, "env_check_result.json")
        try:
            with open(result_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_env_check_cache(self):
        result_path = os.path.join(self._data_dir, "env_check_result.json")
        if not os.path.exists(result_path):
            return None
        try:
            with open(result_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def _on_env_update(self, key, text, status, fix_visible):
        self._set_env_widget(key, text, status, fix_visible)
        self._save_env_check_result()

    def _set_env_widget(self, key, text, status="ok", fix_visible=False):
        if key not in self._env_check_widgets:
            return
        val_lbl, fix_btn = self._env_check_widgets[key]
        val_lbl._full_text = text
        fm = val_lbl.fontMetrics()
        max_w = val_lbl.maximumWidth()
        if max_w > 0 and fm.horizontalAdvance(text) > max_w:
            elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, max_w)
            val_lbl.setText(elided)
            val_lbl.setToolTip(text)
        else:
            val_lbl.setText(text)
            val_lbl.setToolTip("")
        color = {
            "ok": "#66BB6A",
            "warn": "#FFA726",
            "err": "#FF0000",
            "pending": "#42A5F5",
        }.get(status, "#42A5F5")
        val_lbl.setStyleSheet(f"font-size: 9px; color: {color}; background: transparent;")
        if fix_visible:
            fix_btn.setFixedWidth(30)
            fix_btn.setText("修复")
        else:
            fix_btn.setFixedWidth(0)
            fix_btn.setText("")

    def _copy_env_check_list(self):
        lines = []
        for cat_key, cat_title, cat_items in self._env_check_categories:
            lines.append(cat_title)
            for key, display_name, tooltip_text in cat_items:
                if key in self._env_check_widgets:
                    val_lbl_i, _ = self._env_check_widgets[key]
                    val_text = getattr(val_lbl_i, '_full_text', val_lbl_i.text())
                    lines.append(f"  {display_name}: {val_text}")
            lines.append("")
        text = "\n".join(lines).strip()
        if text:
            QApplication.clipboard().setText(text)
            self._log("√ 环境检测清单已复制到剪贴板", "ok")

    def _detect_environment(self, quick=False):
        cached = self._load_env_check_cache()
        if cached:
            for key, info in cached.items():
                self._set_env_widget(key, info.get("text", "未检测"), info.get("status", "unknown"), info.get("fix_visible", False))

        project_root = self._project_root
        app_res = self._app_resources
        w = self._env_check_widgets

        self._set_env_widget("project", f"√ {project_root}", "ok")

        data_venv_python = None
        if self._exe_data_dir:
            for venv_name in (".venv", "venv"):
                p = os.path.join(self._exe_data_dir, venv_name, "Scripts", "python.exe")
                if os.path.exists(p):
                    data_venv_python = p
                    break
        venv_python = os.path.join(app_res, "venv", "Scripts", "python.exe")
        res_python = os.path.join(app_res, "python", "python.exe")
        ref_python = os.path.join(project_root, "项目参考", "环境包", "LTXDesktop", "python", "python.exe")
        sys_python = os.path.join(os.path.expanduser("~"), r"AppData\Local\LTXDesktop\python\python.exe")

        if data_venv_python and os.path.exists(data_venv_python):
            self._python_exe = data_venv_python
            self._pythonw_exe = os.path.join(os.path.dirname(data_venv_python), "pythonw.exe")
            if self._exe_data_dir:
                self._data_dir = self._exe_data_dir
            self._set_env_widget("python", "√ data/.venv", "ok")
        elif os.path.exists(venv_python):
            self._python_exe = venv_python
            self._pythonw_exe = os.path.join(os.path.dirname(venv_python), "pythonw.exe")
            self._set_env_widget("python", "√ UV venv (res/venv)", "ok")
        elif os.path.exists(res_python):
            self._python_exe = res_python
            self._pythonw_exe = os.path.join(os.path.dirname(res_python), "pythonw.exe")
            self._set_env_widget("python", "√ 整合包 (res/python)", "ok")
        elif os.path.exists(ref_python):
            self._python_exe = ref_python
            self._pythonw_exe = os.path.join(os.path.dirname(ref_python), "pythonw.exe")
            self._data_dir = self._exe_data_dir or os.path.join(project_root, "项目参考", "环境包", "LTXDesktop")
            self._set_env_widget("python", "√ 项目参考", "warn")
        elif os.path.exists(sys_python):
            self._python_exe = sys_python
            self._pythonw_exe = os.path.join(os.path.dirname(sys_python), "pythonw.exe")
            self._data_dir = self._exe_data_dir or os.path.join(os.path.expanduser("~"), r"AppData\Local\LTXDesktop")
            self._set_env_widget("python", "√ 系统环境", "warn")
        elif not self._python_exe:
            dev_python = sys.executable
            if dev_python and os.path.isfile(dev_python) and "python" in os.path.basename(dev_python).lower():
                self._python_exe = dev_python
                self._pythonw_exe = os.path.join(os.path.dirname(dev_python), "pythonw.exe")
                self._data_dir = self._exe_data_dir or os.path.join(project_root, "data")
                self._set_env_widget("python", f"√ 开发模式 ({os.path.basename(dev_python)})", "warn")
            else:
                self._set_env_widget("python", "× 未找到", "err", True)

        res_backend = os.path.join(app_res, "backend")
        ltx_search = [
            os.path.join(project_root, "LTX Desktop", "LTX Desktop.exe"),
            os.path.join(project_root, "项目参考", "LTX Desktop", "LTX Desktop.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\LTX Desktop\LTX Desktop.exe"),
            r"C:\Program Files\LTX Desktop\LTX Desktop.exe",
            r"D:\Program Files\LTX Desktop\LTX Desktop.exe",
            r"E:\Program Files\LTX Desktop\LTX Desktop.exe",
        ]

        if os.path.exists(res_backend) and os.path.exists(os.path.join(res_backend, "ltx2_server.py")):
            self._backend_dir = res_backend
            self._set_env_widget("backend", "√ 整合包内置", "ok")
            self._set_env_widget("ltx", "√ 整合包内置", "ok")
        else:
            ltx_found = False
            for p in ltx_search:
                if os.path.exists(p):
                    self._ltx_install_dir = os.path.dirname(p)
                    self._backend_dir = os.path.join(self._ltx_install_dir, "resources", "backend")
                    self._set_env_widget("ltx", f"√ {self._ltx_install_dir}", "ok")
                    ltx_found = True
                    break
            if not ltx_found:
                self._set_env_widget("ltx", "× 未找到", "err", True)

            if self._backend_dir and os.path.exists(self._backend_dir):
                self._set_env_widget("backend", "√ 已找到", "ok")
            else:
                self._backend_dir = None
                self._set_env_widget("backend", "× 未找到", "err", True)

        res_patches = os.path.join(app_res, "patches")
        ref_patches = os.path.join(project_root, "项目参考", "LTX2.3启动器", "patches")
        if os.path.exists(res_patches) and os.path.exists(os.path.join(res_patches, "runtime_policy.py")):
            self._patches_dir = res_patches
            self._set_env_widget("patches", "√ 整合包内置", "ok")
        elif os.path.exists(ref_patches):
            self._patches_dir = ref_patches
            self._set_env_widget("patches", "√ 项目参考", "warn")
        else:
            self._set_env_widget("patches", "× 未找到", "err", True)

        res_ui = os.path.join(app_res, "ui")
        ref_ui = os.path.join(project_root, "项目参考", "LTX2.3启动器", "UI")
        if os.path.exists(res_ui) and os.path.exists(os.path.join(res_ui, "index.html")):
            self._ui_dir = res_ui
            self._set_env_widget("ui", "√ 整合包内置", "ok")
        elif os.path.exists(ref_ui):
            self._ui_dir = ref_ui
            self._set_env_widget("ui", "√ 项目参考", "warn")
        else:
            self._set_env_widget("ui", "× 未找到", "err", True)

        data_models = os.path.join(self._data_dir, "models")
        if os.path.exists(data_models) and os.listdir(data_models):
            self._models_dir = data_models
            self._set_env_widget("models", "√ data/models", "ok")
        else:
            settings_path = os.path.join(self._data_dir, "settings.json")
            if os.path.exists(settings_path):
                try:
                    with open(settings_path, 'r', encoding='utf-8') as f:
                        settings = json.load(f)
                    self._models_dir = settings.get("models_dir", "")
                except:
                    self._models_dir = ""

            if self._models_dir and os.path.exists(self._models_dir):
                self._set_env_widget("models", f"√ 自定义", "ok")
            else:
                ref_models = os.path.join(project_root, "项目参考", "LTX2.3模型")
                if os.path.exists(ref_models):
                    self._models_dir = ref_models
                    self._set_env_widget("models", "√ 项目参考", "warn")
                else:
                    self._models_dir = data_models
                    self._set_env_widget("models", "△ 未配置", "warn", True)

        if not quick:
            self._detect_runtime_and_deps()

        self._save_env_check_result()

        all_ok = all([
            self._python_exe,
            self._backend_dir and os.path.exists(self._backend_dir),
            self._patches_dir and os.path.exists(self._patches_dir),
            self._ui_dir and os.path.exists(self._ui_dir),
        ])
        if all_ok:
            pass
        else:
            pass

    def _detect_runtime_and_deps(self):
        if not self._python_exe:
            return
        try:
            result = hidden_run(
                [self._python_exe, "-c", """
import importlib.metadata
from packaging.version import Version
import torch

deps = ["fastapi","uvicorn","safetensors","accelerate","transformers","tokenizers","diffusers",
        "Pillow","sentencepiece","huggingface_hub","sageattention","pydantic",
        "python-multipart","ftfy","imageio","imageio-ffmpeg","peft","protobuf",
        "opencv-python-headless","tqdm","pynvml","einops","scipy","av","triton-windows"]
locks = {"transformers":(Version("4.57"),Version("4.58")),"tokenizers":(Version("0.22"),Version("0.23")),"diffusers":(Version("0.25"),Version("1.0")),
         "accelerate":(Version("0.24"),Version("2.0")),"safetensors":(Version("0.4"),Version("1.0")),
         "peft":(Version("0.13"),Version("1.0")),"pydantic":(Version("2.7"),Version("3.0")),
         "huggingface_hub":(Version("0.30"),Version("1.0")),"sentencepiece":(Version("0.1.99"),Version("1.0")),
         "ftfy":(Version("6.0"),Version("7.0")),"imageio":(Version("2.37"),Version("3.0")),
         "imageio-ffmpeg":(Version("0.6"),Version("1.0")),"protobuf":(Version("3.20"),Version("7.0")),
         "opencv-python-headless":(Version("4.8"),Version("5.0")),"tqdm":(Version("4.66"),Version("5.0")),
         "pynvml":(Version("11.5"),Version("14.0")),"einops":(Version("0.8"),Version("1.0")),
         "scipy":(Version("1.14"),Version("2.0")),"av":(Version("16.0"),Version("17.0"))}

# PyTorch
tv = torch.__version__
tc = getattr(torch.version, "cuda", None) or ""
variant = f"cu{tc.replace('.','')}" if tc else "cpu"
is_gpu = torch.cuda.is_available()
print(f"TORCH|{tv}|{variant}|{'GPU' if is_gpu else 'CPU'}")

# CUDA via torch
if tc:
    print(f"CUDA|{tc}|pytorch")
else:
    print("CUDA||not_found")

# cuDNN
try:
    cv = str(torch.backends.cudnn.version()) if torch.cuda.is_available() else ""
    print(f"CUDNN|{cv}|pytorch" if cv else "CUDNN||not_found")
except:
    print("CUDNN||not_found")

# Python version
import sys
print(f"PYVER|{sys.version}")

# NVIDIA driver
try:
    import pynvml
    pynvml.nvmlInit()
    dv = pynvml.nvmlSystemGetDriverVersion()
    print(f"NVDROP|{dv}")
    pynvml.nvmlShutdown()
except:
    print("NVDROP|")

# GPU info
if torch.cuda.is_available():
    name = torch.cuda.get_device_name(0)
    mem = torch.cuda.get_device_properties(0).total_mem // 1024 // 1024 // 1024
    print(f"GPU|{name}|{mem}GB")

# Deps
for d in deps:
    try:
        v = importlib.metadata.version(d)
        if d in locks:
            lo,hi = locks[d]
            vv = Version(v.split('+')[0].split('dev')[0].rstrip('.'))
            status = "OK" if lo <= vv < hi else "BAD"
        else:
            status = "OK"
        print(f"DEP|{status}|{d}|{v}")
    except:
        print(f"DEP|MISS|{d}|0")
"""],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                return

            issue_count = 0
            for line in result.stdout.strip().split('\n'):
                if '|' not in line:
                    continue
                parts = line.split('|')

                if parts[0] == "TORCH" and len(parts) >= 4:
                    ver, variant, mode = parts[1], parts[2], parts[3]
                    if mode == "CPU":
                        self._set_env_widget("pytorch", f"× {ver} CPU版", "err", True)
                        issue_count += 1
                    else:
                        self._set_env_widget("pytorch", f"√ {ver}+{variant}", "ok")

                elif parts[0] == "CUDA" and len(parts) >= 2:
                    ver = parts[1]
                    if ver:
                        self._set_env_widget("cuda", f"√ {ver}", "ok")
                    else:
                        self._set_env_widget("cuda", "× 未检测到", "err", True)
                        issue_count += 1

                elif parts[0] == "CUDNN" and len(parts) >= 2:
                    ver = parts[1]
                    if ver:
                        self._set_env_widget("cudnn", f"√ {ver}", "ok")
                    else:
                        self._set_env_widget("cudnn", "△ 未检测到", "warn")

                elif parts[0] == "PYVER" and len(parts) >= 1:
                    py_ver = parts[1].strip() if len(parts) > 1 else ""
                    if "3.12" in py_ver:
                        pass
                    elif "3.13" in py_ver or "3.14" in py_ver:
                        self._set_env_widget("python", f"△ {py_ver.split()[0]} (不兼容)", "warn", True)
                        issue_count += 1

                elif parts[0] == "NVDROP" and len(parts) >= 1:
                    drv = parts[1].strip() if len(parts) > 1 else ""
                    if drv:
                        try:
                            dv = float(drv.split('.')[0]) + float(drv.split('.')[1]) / 100.0
                            if dv >= 560.70:
                                self._set_env_widget("nvidia_driver", f"√ {drv}", "ok")
                            else:
                                self._set_env_widget("nvidia_driver", f"△ {drv} (需>=560.70)", "warn", True)
                                issue_count += 1
                        except:
                            self._set_env_widget("nvidia_driver", f"√ {drv}", "ok")
                    else:
                        self._set_env_widget("nvidia_driver", "× 未检测到", "err", True)
                        issue_count += 1

                elif parts[0] == "DEP" and len(parts) >= 4:
                    status, name, ver = parts[1], parts[2], parts[3]
                    if name in self._env_check_widgets:
                        if status == "OK":
                            self._set_env_widget(name, f"√ {ver}", "ok")
                        elif status == "BAD":
                            lock = LTX_PIP_VERSION_LOCKS.get(name, "")
                            self._set_env_widget(name, f"△ {ver} (需{lock})", "warn", True)
                            issue_count += 1
                        else:
                            self._set_env_widget(name, "× 未安装", "err", True)
                            issue_count += 1

            if issue_count > 0:
                if issue_count >= 5:
                    self._env_check_summary.setText(f"⚠ 检测到 {issue_count} 个问题，建议使用「一键部署维护」批量修复")
                    self._env_check_summary.setStyleSheet("font-size: 9px; color: #FFA726; background: transparent; padding-top: 4px;")
                else:
                    self._env_check_summary.setText(f"⚠ 检测到 {issue_count} 个问题，可点击对应「修复」按钮单独修复")
                    self._env_check_summary.setStyleSheet("font-size: 9px; color: #FFA726; background: transparent; padding-top: 4px;")
            else:
                self._env_check_summary.setText("√ 所有组件检测通过")
                self._env_check_summary.setStyleSheet("font-size: 9px; color: #66BB6A; background: transparent; padding-top: 4px;")

            try:
                from extensions._utils import find_ffmpeg_binary
                from extensions._context import ExtensionContext
                ffmpeg_path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
                if ffmpeg_path:
                    self._set_env_widget("ffmpeg", f"√ {os.path.basename(ffmpeg_path)}", "ok")
                else:
                    self._set_env_widget("ffmpeg", "× 未安装", "warn", True)
            except Exception:
                try:
                    result = hidden_run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        self._set_env_widget("ffmpeg", "√ 已安装", "ok")
                    else:
                        self._set_env_widget("ffmpeg", "× 未安装", "warn", True)
                except Exception:
                    self._set_env_widget("ffmpeg", "× 未安装", "warn", True)
        except Exception:
            pass

    def _quick_detect(self):
        self._quick_detect_btn.setEnabled(False)
        self._full_detect_btn.setEnabled(False)
        self._env_check_summary.setText("快速检测中...")
        self._env_check_summary.setStyleSheet("font-size: 9px; color: #888888; background: transparent; padding-top: 4px;")
        self._detect_paths_only()
        self._save_env_check_result()
        self._quick_detect_btn.setEnabled(True)
        self._full_detect_btn.setEnabled(True)
        all_ok = self._python_exe and self._backend_dir and self._patches_dir and self._ui_dir
        if all_ok:
            self._env_check_summary.setText("√ 路径检测通过（点击完整性检测查看详细版本）")
            self._env_check_summary.setStyleSheet("font-size: 9px; color: #66BB6A; background: transparent; padding-top: 4px;")
        else:
            self._env_check_summary.setText("⚠ 部分路径缺失，建议使用一键部署维护")
            self._env_check_summary.setStyleSheet("font-size: 9px; color: #FFA726; background: transparent; padding-top: 4px;")

    def _full_detect(self):
        self._quick_detect_btn.setEnabled(False)
        self._full_detect_btn.setEnabled(False)
        self._env_check_summary.setText("完整性检测中...")
        self._env_check_summary.setStyleSheet("font-size: 9px; color: #888888; background: transparent; padding-top: 4px;")
        for key in ("pytorch", "cuda", "cudnn", "nvidia_driver"):
            self._set_env_widget(key, "↻ 检测中...", "pending", False)
        for key in ("transformers", "diffusers", "accelerate", "safetensors", "peft",
                     "huggingface_hub", "ffmpeg", "opencv-python-headless", "Pillow",
                     "imageio", "imageio-ffmpeg", "scipy", "einops", "av", "tqdm",
                     "protobuf", "sentencepiece", "ftfy", "pynvml", "pydantic",
                     "python-multipart", "sageattention", "triton-windows"):
            self._set_env_widget(key, "↻ 检测中...", "pending", False)
        self._detect_paths_only()
        if self._python_exe and os.path.exists(self._python_exe):
            self._start_runtime_detect()
        else:
            self._quick_detect_btn.setEnabled(True)
            self._full_detect_btn.setEnabled(True)
            self._env_check_summary.setText("⚠ Python 环境未找到，无法检测运行时依赖")
            self._env_check_summary.setStyleSheet("font-size: 9px; color: #FF0000; background: transparent; padding-top: 4px;")

    def _start_runtime_detect(self):
        widget_keys = set(self._env_check_widgets.keys())
        self._env_detect_worker = EnvDetectWorker(self._python_exe, widget_keys, parent=self)
        self._env_detect_worker.env_update.connect(self._on_env_update)
        self._env_detect_worker.finished.connect(self._on_env_detect_finished)
        self._env_detect_worker.start()

    def _on_env_detect_finished(self, ok):
        self._quick_detect_btn.setEnabled(True)
        self._full_detect_btn.setEnabled(True)
        self._detect_ffmpeg()
        self._save_env_check_result()
        issue_count = 0
        for key, (val_lbl, fix_btn) in self._env_check_widgets.items():
            ss = val_lbl.styleSheet()
            if "#FF0000" in ss or "#FFA726" in ss:
                issue_count += 1
        if issue_count > 0:
            if issue_count >= 5:
                self._env_check_summary.setText(f"⚠ 检测到 {issue_count} 个问题，建议使用「一键部署维护」批量修复")
                self._env_check_summary.setStyleSheet("font-size: 9px; color: #FFA726; background: transparent; padding-top: 4px;")
            else:
                self._env_check_summary.setText(f"⚠ 检测到 {issue_count} 个问题，可点击对应「修复」按钮单独修复")
                self._env_check_summary.setStyleSheet("font-size: 9px; color: #FFA726; background: transparent; padding-top: 4px;")
        else:
            self._env_check_summary.setText("√ 所有组件检测通过")
            self._env_check_summary.setStyleSheet("font-size: 9px; color: #66BB6A; background: transparent; padding-top: 4px;")

    def _detect_ffmpeg(self):
        ffmpeg_exe = None
        ffmpeg_path = os.environ.get("LTX_FFMPEG_PATH")
        if ffmpeg_path and os.path.isfile(ffmpeg_path):
            ffmpeg_exe = ffmpeg_path
        if not ffmpeg_exe:
            ffmpeg_file = Path(os.environ.get("LOCALAPPDATA", "")) / "LTXDesktop" / "ffmpeg_path.txt"
            if ffmpeg_file.exists():
                try:
                    custom_path = ffmpeg_file.read_text(encoding="utf-8").strip()
                    if custom_path and os.path.isfile(custom_path):
                        ffmpeg_exe = custom_path
                except Exception:
                    pass
        if not ffmpeg_exe:
            ffmpeg_exe = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
        if ffmpeg_exe:
            try:
                result = hidden_run([ffmpeg_exe, "-version"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    import re
                    m = re.search(r"ffmpeg version (\S+)", result.stdout)
                    ver = m.group(1) if m else ""
                    self._set_env_widget("ffmpeg", f"√ {ver}" if ver else "√ 已安装", "ok")
                    return
            except Exception:
                pass
        self._set_env_widget("ffmpeg", "× 未安装", "warn", True)

    def _fix_single_component(self, key):
        if key in ("python", "pytorch", "cuda", "cudnn", "nvidia_driver"):
            self._one_click_deploy()
            return
        if key in ("ltx", "backend", "patches", "ui", "models"):
            self._one_click_deploy()
            return
        if key == "ffmpeg":
            self._install_ffmpeg_portable()
            return
        if key in ("voxcpm", "faster_whisper", "real_esrgan"):
            self._fix_extension_component(key)
            return
        if not self._python_exe or not os.path.exists(self._python_exe):
            self._log("× Python 环境未就绪，请先在部署维护中安装", "err")
            return
        uv_exe = os.path.join(self._app_resources, "uv", "uv.exe")
        if not os.path.exists(uv_exe):
            self._one_click_deploy()
            return
        lock_spec = LTX_PIP_VERSION_LOCKS.get(key, "")
        install_spec = f"{key}{lock_spec}" if lock_spec else key
        self._log(f"正在修复 {key}...", "info")
        try:
            env = os.environ.copy()
            mirror_src = self._mirror_source if hasattr(self, '_mirror_source') and self._mirror_source else "auto"
            if mirror_src == "auto" or mirror_src not in MIRROR_SOURCES:
                mirror_src = "tsinghua"
            pip_url = MIRROR_SOURCES[mirror_src]["pip"]
            fallback_url = MIRROR_SOURCES[mirror_src]["pip_fallback"]
            for _ek in ("UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX", "UV_INDEX", "PYTHONHOME"):
                env.pop(_ek, None)
            env["UV_LINK_MODE"] = "copy"
            result = hidden_run(
                [uv_exe, "pip", "install", "--python", self._python_exe, install_spec,
                 "--default-index", pip_url,
                 "--index", fallback_url,
                 "--index-strategy", "first-index"],
                capture_output=True, text=True, timeout=300, env=env
            )
            if result.returncode == 0:
                self._log(f"√ {key} 修复成功", "ok")
                self._detect_paths_only()
                if self._python_exe and os.path.exists(self._python_exe):
                    self._start_runtime_detect()
            else:
                err = (result.stderr or result.stdout or "")[:200]
                self._log(f"△ {key} 镜像修复失败: {err}，尝试直连PyPI...", "warn")
                result = hidden_run(
                    [uv_exe, "pip", "install", "--python", self._python_exe, install_spec,
                     "--default-index", "https://pypi.org/simple/"],
                    capture_output=True, text=True, timeout=300, env=env
                )
                if result.returncode == 0:
                    self._log(f"√ {key} 修复成功(PyPI直连)", "ok")
                    self._detect_paths_only()
                    if self._python_exe and os.path.exists(self._python_exe):
                        self._start_runtime_detect()
                else:
                    err = (result.stderr or result.stdout or "")[:200]
                    self._log(f"× {key} 修复失败: {err}", "err")
        except Exception as e:
            self._log(f"× {key} 修复异常: {e}", "err")

    def _install_ffmpeg_portable(self):
        self._log("正在下载 ffmpeg 便携版...", "info")
        self._set_env_widget("ffmpeg", "↻ 下载中...", "pending", False)
        try:
            from urllib.request import Request, urlopen
            ffmpeg_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
            install_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "LTXDesktop" / "ffmpeg"
            install_dir.mkdir(parents=True, exist_ok=True)
            zip_path = install_dir / "ffmpeg-release-essentials.zip"

            req = Request(ffmpeg_url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=300) as resp:
                with open(zip_path, "wb") as f:
                    shutil.copyfileobj(resp, f)

            self._log("正在解压 ffmpeg...", "info")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(install_dir)
            zip_path.unlink(missing_ok=True)

            ffmpeg_exe = None
            for p in install_dir.rglob("ffmpeg.exe"):
                ffmpeg_exe = p
                break

            if ffmpeg_exe is None:
                self._log("× ffmpeg.exe 未在解压文件中找到", "err")
                self._set_env_widget("ffmpeg", "× 安装失败", "err", True)
                return

            ffmpeg_path_file = Path(os.environ.get("LOCALAPPDATA", "")) / "LTXDesktop" / "ffmpeg_path.txt"
            ffmpeg_path_file.parent.mkdir(parents=True, exist_ok=True)
            ffmpeg_path_file.write_text(str(ffmpeg_exe), encoding="utf-8")
            os.environ["LTX_FFMPEG_PATH"] = str(ffmpeg_exe)

            self._log(f"√ ffmpeg 便携版安装成功: {ffmpeg_exe}", "ok")
            self._detect_ffmpeg()
        except Exception as e:
            self._log(f"× ffmpeg 安装失败: {e}", "err")
            self._set_env_widget("ffmpeg", "× 安装失败", "err", True)

    def _fix_extension_component(self, key):
        if not self._python_exe or not os.path.exists(self._python_exe):
            self._log("× Python 环境未就绪，请先在部署维护中安装", "err")
            return
        uv_exe = os.path.join(self._app_resources, "uv", "uv.exe")
        if not os.path.exists(uv_exe):
            self._one_click_deploy()
            return

        ext_install_map = {
            "voxcpm": [("voxcpm>=2.0.0", "voxcpm"), ("soundfile", "soundfile"), ("librosa", "librosa")],
            "faster_whisper": [("faster-whisper", "faster_whisper")],
            "real_esrgan": [("realesrgan", "realesrgan"), ("basicsr", "basicsr")],
        }
        packages = ext_install_map.get(key, [(key, key)])
        display_name = {"voxcpm": "VoxCPM2", "faster_whisper": "faster-whisper", "real_esrgan": "Real-ESRGAN"}.get(key, key)

        self._log(f"正在修复 {display_name}...", "info")
        self._set_env_widget(key, f"↻ {display_name} 安装中...", "pending", False)

        env = os.environ.copy()
        mirror_src = self._mirror_source if hasattr(self, '_mirror_source') and self._mirror_source else "auto"
        if mirror_src == "auto" or mirror_src not in MIRROR_SOURCES:
            mirror_src = "tsinghua"
        pip_url = MIRROR_SOURCES[mirror_src]["pip"]
        fallback_url = MIRROR_SOURCES[mirror_src]["pip_fallback"]
        for _ek in ("UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX", "UV_INDEX", "PYTHONHOME"):
            env.pop(_ek, None)
        env["UV_LINK_MODE"] = "copy"

        all_ok = True
        for pip_spec, imp_name in packages:
            try:
                result = hidden_run(
                    [uv_exe, "pip", "install", "--python", self._python_exe, pip_spec,
                     "--default-index", pip_url,
                     "--index", fallback_url,
                     "--index-strategy", "first-index"],
                    capture_output=True, text=True, timeout=600, env=env
                )
                if result.returncode != 0:
                    err = (result.stderr or result.stdout or "")[:200]
                    self._log(f"△ {pip_spec} 镜像安装失败: {err}，尝试直连PyPI...", "warn")
                    result = hidden_run(
                        [uv_exe, "pip", "install", "--python", self._python_exe, pip_spec,
                         "--default-index", "https://pypi.org/simple/"],
                        capture_output=True, text=True, timeout=600, env=env
                    )
                    if result.returncode != 0:
                        err = (result.stderr or result.stdout or "")[:200]
                        self._log(f"× {pip_spec} 安装失败: {err}", "err")
                        all_ok = False
            except Exception as e:
                self._log(f"× {pip_spec} 安装异常: {e}", "err")
                all_ok = False

        if all_ok:
            self._log(f"√ {display_name} 修复成功", "ok")
            self._detect_paths_only()
            if self._python_exe and os.path.exists(self._python_exe):
                self._start_runtime_detect()
        else:
            self._set_env_widget(key, f"× {display_name} 安装失败", "err", True)

    def _detect_running_services(self):
        any_alive = False
        try:
            conn = socket.create_connection(('127.0.0.1', self._backend_port), timeout=1)
            conn.close()
            self._log(f"√ 检测到后端服务已在运行 (端口{self._backend_port})", "ok")
            any_alive = True
            for sid, card in self.service_cards.items():
                if sid == "backend":
                    card.set_status("running")
        except Exception:
            pass
        try:
            conn = socket.create_connection(('127.0.0.1', self._frontend_port), timeout=1)
            conn.close()
            self._log(f"√ 检测到前端服务已在运行 (端口{self._frontend_port})", "ok")
            any_alive = True
            for sid, card in self.service_cards.items():
                if sid == "frontend":
                    card.set_status("running")
        except Exception:
            pass
        if any_alive:
            self.btn_start_all.setEnabled(True)
            self.btn_stop_all.setEnabled(True)

    def _log(self, msg, level="info", tag="APP"):
        ts = datetime.now().strftime("%H:%M:%S")
        colors = {"info": "#B0B0C0", "ok": "#66BB6A", "warn": "#FFA726", "error": "#FF0000", "err": "#FF0000"}
        c = colors.get(level, "#B0B0C0")
        self.log_text.append(f"<span style='color:#666688'>[{ts}]</span> <span style='color:{c}'>{msg}</span>")
        self._write_debug_log(msg, level, tag)

    def _append_log(self, service_id, msg):
        svc = SERVICES.get(service_id, {})
        color_map = {"backend": "#FF7043", "frontend": "#66BB6A"}
        c = color_map.get(service_id, "#B0B0C0")
        name = svc.get("name", service_id)
        ts = datetime.now().strftime("%H:%M:%S")
        if not hasattr(self, '_log_batch'):
            self._log_batch = []
            self._log_batch_timer = QTimer(self)
            self._log_batch_timer.setSingleShot(True)
            self._log_batch_timer.timeout.connect(self._flush_log_batch)
        self._log_batch.append(f"<span style='color:#666688'>[{ts}]</span> <span style='color:{c}'>[{name}]</span> {msg}")
        self._write_debug_log(f"[{name}] {msg}", "info", service_id.upper())
        if not self._log_batch_timer.isActive():
            self._log_batch_timer.start(100)

    def _flush_log_batch(self):
        if not hasattr(self, '_log_batch') or not self._log_batch:
            return
        combined = "<br>".join(self._log_batch)
        self._log_batch.clear()
        self.log_text.append(combined)
        if self.auto_scroll:
            sb = self.log_text.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _toggle_auto_scroll(self):
        self.auto_scroll = self.auto_scroll_btn.isChecked()
        if hasattr(self, 'deploy_auto_scroll_btn'):
            self.deploy_auto_scroll_btn.blockSignals(True)
            self.deploy_auto_scroll_btn.setChecked(self.auto_scroll)
            self.deploy_auto_scroll_btn.blockSignals(False)

    def _toggle_deploy_auto_scroll(self):
        self.auto_scroll = self.deploy_auto_scroll_btn.isChecked()
        self.auto_scroll_btn.blockSignals(True)
        self.auto_scroll_btn.setChecked(self.auto_scroll)
        self.auto_scroll_btn.blockSignals(False)

    def _save_log(self, text_edit, title="日志"):
        try:
            from datetime import datetime as dt
            default_name = f"{title}_{dt.now().strftime('%Y%m%d_%H%M%S')}.txt"
            file_path, _ = QFileDialog.getSaveFileName(
                self, f"保存{title}", default_name,
                "文本文件 (*.txt);;所有文件 (*)"
            )
            if not file_path:
                return
            html_content = text_edit.toHtml()
            import re
            html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL)
            text_content = re.sub(r'<[^>]+>', '\n', html_content)
            text_content = text_content.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"')
            lines = [line.strip() for line in text_content.split('\n') if line.strip()]
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            self._log(f"√ {title}已保存到: {file_path}", "ok")
        except Exception as e:
            self._log(f"× 保存{title}失败: {e}", "err")

    def _copy_log(self, text_edit):
        try:
            html_content = text_edit.toHtml()
            import re
            html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL)
            text_content = re.sub(r'<[^>]+>', '\n', html_content)
            text_content = text_content.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"')
            lines = [line.strip() for line in text_content.split('\n') if line.strip()]
            clipboard = QApplication.clipboard()
            clipboard.setText('\n'.join(lines))
            self._log("√ 日志已复制到剪贴板", "ok")
        except Exception as e:
            self._log(f"× 复制日志失败: {e}", "err")

    def _toggle_debug_mode(self):
        self._debug_mode = self.debug_mode_btn.isChecked()
        if hasattr(self, 'deploy_debug_btn'):
            self.deploy_debug_btn.blockSignals(True)
            self.deploy_debug_btn.setChecked(self._debug_mode)
            self.deploy_debug_btn.blockSignals(False)
        if self._debug_mode:
            if _DBG:
                try:
                    ok = _DBG.init()
                    if ok:
                        log_path = _DBG.get_log_path()
                        self._log(f"🐛 调试模式已开启，日志文件: {log_path}", "ok")
                        tags = _DBG.get_active_tags()
                        self._log(f"🐛 活跃标签: {', '.join(tags) if tags else 'ALL'}", "info")
                        self._register_debug_probes()
                    else:
                        self._debug_mode = False
                        self.debug_mode_btn.setChecked(False)
                        if hasattr(self, 'deploy_debug_btn'):
                            self.deploy_debug_btn.blockSignals(True)
                            self.deploy_debug_btn.setChecked(False)
                            self.deploy_debug_btn.blockSignals(False)
                        self._log("× 开启调试模式失败: 无活跃标签", "err")
                except Exception as e:
                    self._debug_mode = False
                    self.debug_mode_btn.setChecked(False)
                    if hasattr(self, 'deploy_debug_btn'):
                        self.deploy_debug_btn.blockSignals(True)
                        self.deploy_debug_btn.setChecked(False)
                        self.deploy_debug_btn.blockSignals(False)
                    self._log(f"× 开启调试模式失败: {e}", "err")
            else:
                try:
                    if self._exe_temp_dir:
                        log_dir = os.path.join(self._exe_temp_dir, "logs")
                    else:
                        log_dir = os.path.join(self._project_root, "temp", "logs")
                    os.makedirs(log_dir, exist_ok=True)
                    log_path = os.path.join(log_dir, f"debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
                    self._debug_log_file = open(log_path, 'a', encoding='utf-8')
                    self._log(f"🐛 调试模式已开启（简易模式），日志文件: {log_path}", "ok")
                except Exception as e:
                    self._debug_mode = False
                    self.debug_mode_btn.setChecked(False)
                    if hasattr(self, 'deploy_debug_btn'):
                        self.deploy_debug_btn.blockSignals(True)
                        self.deploy_debug_btn.setChecked(False)
                        self.deploy_debug_btn.blockSignals(False)
                    self._log(f"× 开启调试模式失败: {e}", "err")
        else:
            if _DBG:
                _DBG.shutdown()
            if self._debug_log_file:
                try:
                    self._debug_log_file.close()
                except Exception:
                    pass
                self._debug_log_file = None
            self._log("🐛 调试模式已关闭", "info")

    def _register_debug_probes(self):
        if not _DBG:
            return
        def _probe_gpu():
            try:
                import torch
                if torch.cuda.is_available():
                    alloc = torch.cuda.memory_allocated(0) / 1024**3
                    reserved = torch.cuda.memory_reserved(0) / 1024**3
                    _DBG.dbg("GPU", f"VRAM - 已分配: {alloc:.2f}GB, 已保留: {reserved:.2f}GB")
            except ImportError:
                _DBG.dbg("GPU", "PyTorch 未安装，无法获取 GPU 信息")
            except Exception as e:
                _DBG.dbg("GPU", f"GPU 信息获取失败: {e}", "error")

        def _probe_ports():
            for sid, svc in SERVICES.items():
                port = svc.get("port", 0)
                if port:
                    try:
                        conn = socket.create_connection(('127.0.0.1', port), timeout=0.5)
                        conn.close()
                        _DBG.dbg("NETWORK", f"端口 {port} ({sid}): LISTENING")
                    except Exception:
                        _DBG.dbg("NETWORK", f"端口 {port} ({sid}): CLOSED")

        def _probe_process():
            try:
                import psutil
                my_pid = os.getpid()
                _DBG.dbg("PROCESS", f"当前进程 PID: {my_pid}")
                for proc in psutil.process_iter(['pid', 'name', 'memory_info']):
                    try:
                        pname = proc.info.get('name', '')
                        if 'python' in pname.lower() or '云集' in pname:
                            mem = proc.info.get('memory_info')
                            mem_mb = mem.rss / 1024 / 1024 if mem else 0
                            _DBG.dbg("PROCESS", f"  PID:{proc.info['pid']} {pname} ({mem_mb:.1f}MB)")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except ImportError:
                pass

        _DBG.register_probe("GPU", "gpu_vram", _probe_gpu, interval=10)
        _DBG.register_probe("NETWORK", "port_status", _probe_ports, interval=30)
        _DBG.register_probe("PROCESS", "process_list", _probe_process, interval=30)

    def _write_debug_log(self, msg, level="info", tag="APP"):
        if not self._debug_mode:
            return
        if _DBG:
            _DBG.dbg(tag, msg, level)
        elif self._debug_log_file:
            try:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._debug_log_file.write(f"[{ts}] [{tag}] [{level}] {msg}\n")
                self._debug_log_file.flush()
            except Exception:
                pass

    def _start_fe_debug_polling(self):
        if not self._fe_debug_log_path:
            data_dir = self._data_dir
            self._fe_debug_log_path = os.path.join(data_dir, "_fe_debug.log")
        self._fe_debug_read_pos = 0
        if os.path.exists(self._fe_debug_log_path):
            try:
                self._fe_debug_read_pos = os.path.getsize(self._fe_debug_log_path)
            except Exception:
                pass
        self._fe_debug_timer.start(2000)

    def _stop_fe_debug_polling(self):
        self._fe_debug_timer.stop()

    def _poll_fe_debug_log(self):
        if not self._fe_debug_log_path or not os.path.exists(self._fe_debug_log_path):
            return
        try:
            file_size = os.path.getsize(self._fe_debug_log_path)
            if file_size < self._fe_debug_read_pos:
                self._fe_debug_read_pos = 0
            if file_size <= self._fe_debug_read_pos:
                return
            with open(self._fe_debug_log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._fe_debug_read_pos)
                new_lines = f.readlines()
                self._fe_debug_read_pos = f.tell()
            for line in new_lines:
                line = line.strip()
                if not line:
                    continue
                is_error = "[FE-ERROR]" in line
                color = "#FF0000" if is_error else "#FFA726"
                ts = datetime.now().strftime("%H:%M:%S")
                self.log_text.append(
                    f"<span style='color:#666688'>[{ts}]</span> "
                    f"<span style='color:{color}'>🌐 {line}</span>"
                )
                if self._debug_mode:
                    self._write_debug_log(line, "error" if is_error else "warn", "FE")
            if self.auto_scroll:
                sb = self.log_text.verticalScrollBar()
                sb.setValue(sb.maximum())
        except Exception:
            pass

    def _on_status_changed(self, sid, alive):
        if sid in self.service_cards:
            self.service_cards[sid].update_status(alive)
        any_alive = any(card.is_running for card in self.service_cards.values())
        if any_alive:
            if not self.is_starting:
                self.btn_start_all.setEnabled(True)
            self.btn_stop_all.setEnabled(True)
        else:
            self.btn_start_all.setEnabled(True)
            self.btn_stop_all.setEnabled(False)

    def _enable_buttons(self):
        self.btn_start_all.setEnabled(True)
        self.btn_stop_all.setEnabled(False)
        self.is_starting = False

    def _update_buttons_after_start(self):
        any_alive = any(card.is_running for card in self.service_cards.values())
        if any_alive:
            self.btn_start_all.setEnabled(True)
            self.btn_stop_all.setEnabled(True)
        else:
            self.btn_start_all.setEnabled(True)
            self.btn_stop_all.setEnabled(False)

    def _start_all(self):
        try:
            self._start_all_impl()
        except Exception as e:
            import traceback
            err_msg = traceback.format_exc()
            self._log(f"× 启动异常: {err_msg}", "error")
            try:
                _crash_dir = self._exe_temp_dir if self._exe_temp_dir else os.path.join(os.path.dirname(self._app_dir), "temp")
                os.makedirs(os.path.join(_crash_dir, "logs"), exist_ok=True)
                crash_log = os.path.join(_crash_dir, "logs", "crash_start.log")
                with open(crash_log, "w", encoding="utf-8") as f:
                    f.write(err_msg)
            except:
                pass
            self._enable_buttons()

    def _start_all_impl(self):
        if not self._python_exe:
            self._log("× 未找到 Python 环境！请先在部署维护中安装。", "err")
            # 引导步骤3：启动失败
            if self._guide_active and self._guide_step == 3:
                self._guide_on_error()
            return
        if not self._backend_dir or not os.path.exists(self._backend_dir):
            self._log("× 未找到 LTX Desktop 后端代码！请先在部署维护中安装。", "err")
            if self._guide_active and self._guide_step == 3:
                self._guide_on_error()
            return
        if not self._patches_dir or not os.path.exists(self._patches_dir):
            self._log("× 未找到补丁文件！请确保 patches 目录存在。", "err")
            if self._guide_active and self._guide_step == 3:
                self._guide_on_error()
            return

        try:
            check_result = subprocess.run(
                [self._python_exe, "-c", "import uvicorn; import fastapi; print('ok')"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
            )
            if check_result.returncode != 0:
                self._log("× 核心依赖缺失（uvicorn/fastapi），请先在部署维护中安装依赖。", "err")
                self._log(f"  缺失详情: {check_result.stderr.strip()[:200]}", "err")
                if self._guide_active and self._guide_step == 3:
                    self._guide_on_error()
                return
        except Exception as e:
            self._log(f"× 依赖检查失败: {e}", "err")
            if self._guide_active and self._guide_step == 3:
                self._guide_on_error()
            return

        self.is_starting = True
        self.btn_start_all.setEnabled(False)
        self.btn_stop_all.setEnabled(True)
        self._log("正在启动全部服务...", "info")

        self._start_backend()

        self._log(f"等待核心引擎就绪 (端口{self._backend_port})...", "info")
        self._wait_backend_count = 0
        self._wait_backend_timer = QTimer(self)
        self._wait_backend_timer.timeout.connect(self._poll_backend_port)
        self._wait_backend_timer.start(1000)

    def _poll_backend_port(self):
        self._wait_backend_count += 1
        import threading
        result = [False]

        def _check():
            try:
                conn = socket.create_connection(('127.0.0.1', self._backend_port), timeout=1)
                conn.close()
                result[0] = True
            except Exception:
                pass

        t = threading.Thread(target=_check, daemon=True)
        t.start()
        t.join(timeout=2)

        if result[0]:
            self._wait_backend_timer.stop()
            self._log(f"核心引擎端口 {self._backend_port} 已就绪 (第{self._wait_backend_count}秒)", "ok")
            self._start_frontend()
            self.is_starting = False
            self._update_buttons_after_start()
            if hasattr(self, '_model_table'):
                QTimer.singleShot(2000, self._refresh_model_status)
            self._wait_frontend_count = 0
            auto_open = self.auto_open_checkbox.isChecked()
            if auto_open:
                self._wait_frontend_timer = QTimer(self)
                self._wait_frontend_timer.timeout.connect(self._poll_frontend_port)
                self._wait_frontend_timer.start(1000)
            # 引导步骤3：后端已就绪，启动浏览器检测
            if self._guide_active and self._guide_step == 3:
                self._start_guide_browser_check()
        else:
            if self._wait_backend_count >= 90:
                self._wait_backend_timer.stop()
                self._log(f"核心引擎端口 {self._backend_port} 等待超时", "err")
                self.is_starting = False
                # 引导步骤3：启动超时，关闭全自动开关
                if self._guide_active and self._guide_step == 3:
                    self._guide_on_error()

    def _poll_frontend_port(self):
        self._wait_frontend_count += 1
        import threading
        result = [False]

        def _check():
            try:
                conn = socket.create_connection(('127.0.0.1', self._frontend_port), timeout=1)
                conn.close()
                result[0] = True
            except Exception:
                pass

        t = threading.Thread(target=_check, daemon=True)
        t.start()
        t.join(timeout=2)

        if result[0]:
            self._wait_frontend_timer.stop()
            self._open_ui()
        else:
            if self._wait_frontend_count >= 30:
                self._wait_frontend_timer.stop()

    def _start_backend(self):
        try:
            conn = socket.create_connection(('127.0.0.1', self._backend_port), timeout=1)
            conn.close()
            is_own = "backend" in self.service_processes and self.service_processes["backend"].isRunning()
            if is_own:
                self._log(f"√ 核心引擎已在运行 (端口{self._backend_port})，跳过启动", "ok")
                return
            else:
                self._log(f"△ 端口{self._backend_port}被未知进程占用，正在清理...", "warn")
                self._kill_specific_port(self._backend_port)
                time.sleep(0.5)
        except Exception:
            pass

        try:
            data_dir = self._data_dir
            patches_dir = self._patches_dir
            backend_dir = self._backend_dir

            if not patches_dir or not os.path.exists(patches_dir):
                self._log("× 补丁目录不存在，无法启动核心引擎", "error")
                return
            if not backend_dir or not os.path.exists(backend_dir):
                self._log("× 后端目录不存在，无法启动核心引擎", "error")
                return

            app_res = self._app_resources
            data_dir = self._data_dir
            outputs_dir = os.path.join(data_dir, "outputs")

            os.makedirs(outputs_dir, exist_ok=True)
            os.makedirs(data_dir, exist_ok=True)
            custom_dir_file = os.path.join(data_dir, "custom_dir.txt")
            if not os.path.exists(custom_dir_file):
                with open(custom_dir_file, 'w', encoding='utf-8') as f:
                    f.write(outputs_dir)
            self._update_output_dir_hint()

            models_dir = os.path.join(data_dir, "models")
            if os.path.islink(models_dir) or (os.path.isdir(models_dir) and not os.path.exists(models_dir)):
                try:
                    os.rmdir(models_dir)
                    self._log(f"◇ 已移除失效的模型目录链接: {models_dir}", "warn")
                except Exception:
                    pass
            if not os.path.isdir(models_dir):
                os.makedirs(models_dir, exist_ok=True)
                self._log(f"◇ 已创建模型目录: {models_dir}", "ok")

            env = os.environ.copy()
            env["LTX_APP_DATA_DIR"] = data_dir
            env["PYTHONPATH"] = f"{patches_dir};{backend_dir}"
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env.pop("PYTHONHOME", None)

            launcher_code = f"""import sys, os
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
patch_dir = r"{patches_dir}"
backend_dir = r"{backend_dir}"
sys.path = [p for p in sys.path if p and os.path.normpath(p) != os.path.normpath(backend_dir)]
sys.path = [p for p in sys.path if p and p != "." and p != ""]
sys.path.insert(0, patch_dir)
sys.path.insert(1, backend_dir)
import uvicorn
from ltx2_server import app
if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port={self._backend_port}, log_level="info", access_log=False)
"""
            launcher_path = os.path.join(patches_dir, "launcher.py")
            try:
                with open(launcher_path, "w", encoding="utf-8") as f:
                    f.write(launcher_code)
            except (PermissionError, OSError):
                launcher_path = os.path.join(data_dir, "_launcher_backend.py")
                with open(launcher_path, "w", encoding="utf-8") as f:
                    f.write(launcher_code)
                self._log(f"△ 补丁目录不可写，launcher 已写入 data/", "warn")

            cmd = [self._python_exe, launcher_path]
            self._log(f"⚙ 启动命令: {self._python_exe} {launcher_path}", "info")
            self._log(f"⚙ 工作目录: {backend_dir}", "info")
            self._log(f"⚙ 数据目录: {data_dir}", "info")
            proc = ServiceProcess("backend", cmd, backend_dir, env)
            proc.output_received.connect(self._append_log)
            proc.process_finished.connect(self._on_process_finished)
            self.service_processes["backend"] = proc
            proc.start()
            self._log("√ 核心引擎启动命令已发送", "ok")
        except Exception as e:
            import traceback
            self._log(f"× 启动核心引擎异常: {e}", "error")
            self._log(traceback.format_exc(), "error")

    def _start_frontend(self):
        self._log("正在启动 AI视频工作站...", "info")
        try:
            conn = socket.create_connection(('127.0.0.1', self._frontend_port), timeout=1)
            conn.close()
            is_own = "frontend" in self.service_processes and self.service_processes["frontend"].isRunning()
            if is_own:
                self._log(f"√ AI视频工作站已在运行 (端口{self._frontend_port})，跳过启动", "ok")
                return
            else:
                self._log(f"△ 端口{self._frontend_port}被未知进程占用，正在清理...", "warn")
                self._kill_specific_port(self._frontend_port)
                time.sleep(0.5)
        except Exception:
            pass

        try:
            ui_dir = self._ui_dir
            patches_dir = self._patches_dir
            backend_port = self._backend_port
            frontend_port = self._frontend_port

            if not ui_dir or not os.path.exists(ui_dir):
                self._log("× UI 目录不存在，无法启动 AI视频工作站", "error")
                return
            if not patches_dir or not os.path.exists(patches_dir):
                self._log("× 补丁目录不存在，无法启动 AI视频工作站", "error")
                return

            temp_logs_dir = self._exe_temp_dir if self._exe_temp_dir else os.path.join(os.path.dirname(self._app_dir), "temp")
            ui_log_path = os.path.join(temp_logs_dir, "logs", "ui_server.log")
            data_dir_escaped = repr(ui_log_path)
            ui_dir_escaped = repr(ui_dir)

            outputs_dir_for_proxy = os.path.join(self._data_dir, "outputs")
            outputs_dir_escaped = repr(outputs_dir_for_proxy)

            icon_candidates = []
            for name in ('ico.png', 'icon.png', 'icon.ico'):
                if hasattr(sys, '_MEIPASS'):
                    icon_candidates.append(os.path.join(sys._MEIPASS, name))
                icon_candidates.append(os.path.join(self._app_dir, name))
            icon_path = next((p for p in icon_candidates if os.path.exists(p)), icon_candidates[-1])
            icon_path_escaped = repr(icon_path)

            icon_base64 = ""
            if os.path.exists(icon_path):
                import base64
                with open(icon_path, "rb") as _f:
                    icon_base64 = base64.b64encode(_f.read()).decode("ascii")

            app_name_escaped = repr(APP_NAME)
            version_escaped = repr(VERSION)

            frontend_script = (
"""import os, sys, logging, httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse
import uvicorn

APP_NAME = __APP_NAME__
VERSION = __VERSION__

_ui_log_path = __UI_LOG_PATH__
os.makedirs(os.path.dirname(_ui_log_path), exist_ok=True)

def _ui_log(msg):
    with open(_ui_log_path, 'a', encoding='utf-8') as f:
        f.write(f"[UI] {msg}\\n")
    print(f"[UI_SERVER] {msg}", flush=True)

def _safe_file(path, media_type, headers=None):
    with open(path, "rb") as f:
        return Response(content=f.read(), media_type=media_type, headers=headers or {})

_ui_log(f"Starting UI server, backend port=__BACKEND_PORT__")

ui_dir = __UI_DIR__
BACKEND_PORT = __BACKEND_PORT__
FRONTEND_PORT = __FRONTEND_PORT__
BACKEND_BASE = f"http://127.0.0.1:{BACKEND_PORT}"
app = FastAPI()
NC = {"Cache-Control": "no-store, max-age=0"}
_ui_log(f"Routes configured, ui_dir={ui_dir}")

@app.get("/")
async def index():
    html_path = os.path.join(ui_dir, "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    icon_b64 = __ICON_BASE64__
    if icon_b64:
        html = html.replace('src="/app-icon.png"', f'src="data:image/png;base64,{icon_b64}"')
    return Response(content=html.encode("utf-8"), media_type="text/html", headers=NC)

@app.get("/api/app-info")
async def app_info():
    return {"app_name": APP_NAME, "version": VERSION}

@app.get("/index.css")
async def css():
    return _safe_file(os.path.join(ui_dir, "index.css"), "text/css", NC)

@app.get("/index.js")
async def js():
    with open(os.path.join(ui_dir, "index.js"), "r", encoding="utf-8") as f:
        content = f.read()
    content = content.replace("{{BACKEND_PORT}}", str(BACKEND_PORT))
    return Response(content=content, media_type="application/javascript", headers=NC)

@app.get("/i18n.js")
async def i18n():
    return _safe_file(os.path.join(ui_dir, "i18n.js"), "application/javascript", NC)

@app.get("/docs")
async def usage_guide():
    guide_path = os.path.join(ui_dir, "usage_guide.html")
    return _safe_file(guide_path, "text/html; charset=utf-8", NC)

@app.get("/app-icon.png")
async def app_icon():
    icon_candidates = [__ICON_PATH__]
    if hasattr(sys, '_MEIPASS'):
        for name in ('ico.png', 'icon.png', 'icon.ico'):
            icon_candidates.insert(0, os.path.join(sys._MEIPASS, name))
    for p in icon_candidates:
        if os.path.exists(p):
            return _safe_file(p, "image/png", NC)
    return Response(content=b"Not found", status_code=404)

@app.api_route("/outputs/{path:path}", methods=["GET", "HEAD"])
async def proxy_outputs(request: Request, path: str):
    outputs_dir = __OUTPUTS_DIR__
    file_path = os.path.join(outputs_dir, path)
    if not os.path.exists(file_path) or os.path.isdir(file_path):
        return Response(content=b"Not found", status_code=404)
    import mimetypes as _mt
    import re as _re
    try:
        file_size = os.path.getsize(file_path)
    except OSError:
        return Response(content=b"Internal error", status_code=500)
    mime_type, _ = _mt.guess_type(file_path)
    if mime_type is None:
        mime_type = "application/octet-stream"
    base_headers = {"Accept-Ranges": "bytes", "Content-Length": str(file_size), "Content-Type": mime_type}
    if request.method == "HEAD":
        return Response(content=b"", status_code=200, media_type=mime_type, headers=base_headers)
    headers = dict(request.headers)
    range_header = headers.get("range", "")
    if range_header.startswith("bytes="):
        match = _re.match(r"^bytes=(\\d+)-(\\d*)$", range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            end = min(end, file_size - 1)
            if start > end or start >= file_size:
                return Response(content=b"Invalid range", status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
            content_length = end - start + 1
            def _iter():
                with open(file_path, "rb") as f:
                    f.seek(start)
                    remaining = content_length
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            return StreamingResponse(
                _iter(),
                status_code=206,
                media_type=mime_type,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(content_length),
                },
            )
    def _full_iter():
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk
    return StreamingResponse(_full_iter(), status_code=200, media_type=mime_type, headers=base_headers)

@app.api_route("/api/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_api(request: Request, path: str):
    query = str(request.query_params)
    url = f"{BACKEND_BASE}/api/{path}"
    if query:
        url = f"{url}?{query}"
    
    body = await request.body()
    headers = dict(request.headers)
    headers.pop("host", None)
    
    is_upload_request = path.startswith("system/upload")
    is_media_request = path.startswith("system/file") or path.startswith("system/video-thumbnail") or is_upload_request
    is_direct_file = path.startswith("system/file")
    
    if is_direct_file:
        import mimetypes
        import re
        file_path = request.query_params.get("path", "")
        if not file_path or not os.path.exists(file_path):
            if request.method == "HEAD":
                return Response(content=b"", status_code=404, media_type="application/octet-stream")
            return Response(content=b"Not found", status_code=404)
        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            return Response(content=b"Internal error", status_code=500)
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type is None:
            mime_type = "application/octet-stream"
        base_headers = {"Accept-Ranges": "bytes", "Content-Length": str(file_size), "Content-Type": mime_type}
        if request.method == "HEAD":
            return Response(content=b"", status_code=200, media_type=mime_type, headers=base_headers)
        range_header = headers.get("range", "")
        if range_header.startswith("bytes="):
            match = re.match(r"^bytes=(\\d+)-(\\d*)$", range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
                end = min(end, file_size - 1)
                if start > end or start >= file_size:
                    return Response(content=b"Invalid range", status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
                content_length = end - start + 1
                
                def iterfile():
                    with open(file_path, "rb") as f:
                        f.seek(start)
                        remaining = content_length
                        while remaining > 0:
                            chunk = f.read(min(65536, remaining))
                            if not chunk:
                                break
                            remaining -= len(chunk)
                            yield chunk
                
                return StreamingResponse(
                    iterfile(),
                    status_code=206,
                    media_type=mime_type,
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(content_length),
                    },
                )
        def _full_iter():
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
        return StreamingResponse(_full_iter(), status_code=200, media_type=mime_type, headers=base_headers)
    
    timeout = httpx.Timeout(300.0) if is_media_request else httpx.Timeout(60.0)
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if is_upload_request:
                # Stream large uploads directly without buffering entire body
                async with client.stream(request.method, url, content=body, headers=headers) as resp:
                    resp_content = b""
                    async for chunk in resp.aiter_bytes():
                        resp_content += chunk
                    return Response(
                        content=resp_content,
                        status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/json"),
                    )
            else:
                resp = await client.request(request.method, url, content=body, headers=headers)
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"),
                )
    except httpx.ConnectError:
        return Response(content=b'{"detail":"Backend unavailable","status":"offline"}', status_code=503, media_type="application/json")
    except httpx.TimeoutException:
        return Response(content=b'{"detail":"Backend timeout","status":"timeout"}', status_code=504, media_type="application/json")
    except Exception as e:
        _ui_log(f"PROXY ERROR: {e}")
        return Response(content=str(e).encode(), status_code=502)

@app.api_route("/health", methods=["GET"])
async def proxy_health():
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(f"{BACKEND_BASE}/health")
            return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")
    except (httpx.ConnectError, httpx.TimeoutException):
        return Response(content=b'{"status":"offline","models_loaded":false}', status_code=503, media_type="application/json")

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if sys.platform == 'win32':
        class NF(logging.Filter):
            def filter(self, r):
                if r.name != "asyncio": return True
                m = r.getMessage()
                if "_call_connection_lost" in m or "_ProactorBasePipeTransport" in m: return False
                if hasattr(r, 'exc_info') and r.exc_info:
                    _, e, _ = r.exc_info
                    if isinstance(e, ConnectionResetError) and getattr(e, 'winerror', None) == 10054: return False
                if "10054" in m and "ConnectionResetError" in m: return False
                return True
        logging.getLogger("asyncio").addFilter(NF())
    _ui_log(f"Starting uvicorn on port {FRONTEND_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=FRONTEND_PORT, log_level="info", access_log=False)
""")
            frontend_script = frontend_script.replace("__APP_NAME__", app_name_escaped)
            frontend_script = frontend_script.replace("__VERSION__", version_escaped)
            frontend_script = frontend_script.replace("__UI_LOG_PATH__", data_dir_escaped)
            frontend_script = frontend_script.replace("__UI_DIR__", ui_dir_escaped)
            frontend_script = frontend_script.replace("__BACKEND_PORT__", str(backend_port))
            frontend_script = frontend_script.replace("__FRONTEND_PORT__", str(frontend_port))
            frontend_script = frontend_script.replace("__OUTPUTS_DIR__", outputs_dir_escaped)
            frontend_script = frontend_script.replace("__ICON_PATH__", icon_path_escaped)
            frontend_script = frontend_script.replace("__ICON_BASE64__", repr(icon_base64))
            script_path = os.path.join(patches_dir, "_ui_server.py")
            try:
                with open(script_path, "w", encoding="utf-8") as f:
                    f.write(frontend_script)
            except (PermissionError, OSError):
                frontend_data_dir_fallback = self._exe_data_dir if (self._exe_data_dir and os.path.isdir(self._exe_data_dir)) else self._data_dir
                script_path = os.path.join(frontend_data_dir_fallback, "_ui_server.py")
                with open(script_path, "w", encoding="utf-8") as f:
                    f.write(frontend_script)
                self._log(f"△ 补丁目录不可写，UI 脚本已写入 data/", "warn")

            env = os.environ.copy()
            frontend_data_dir = self._exe_data_dir if (self._exe_data_dir and os.path.isdir(self._exe_data_dir)) else self._data_dir
            env["LTX_APP_DATA_DIR"] = frontend_data_dir
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env.pop("PYTHONHOME", None)

            python_exe = self._python_exe
            cmd = [python_exe, script_path]
            self._log(f"⚙ UI 启动命令: {python_exe} {script_path}", "info")
            self._log(f"⚙ UI 数据目录: {frontend_data_dir}", "info")
            proc = ServiceProcess("frontend", cmd, os.path.dirname(script_path), env)
            proc.output_received.connect(self._append_log)
            proc.process_finished.connect(self._on_process_finished)
            self.service_processes["frontend"] = proc
            proc.start()
            self._log("√ AI视频工作站启动命令已发送", "ok")
            self._start_fe_debug_polling()
        except Exception as e:
            import traceback
            self._log(f"× 启动 AI视频工作站异常: {e}", "error")
            self._log(traceback.format_exc(), "error")

    def _stop_all(self):
        self._log("正在停止全部服务...", "warn")
        self._stop_fe_debug_polling()
        for sid, proc in list(self.service_processes.items()):
            if proc and proc.isRunning():
                proc.terminate()
                self._log(f"⏹ {SERVICES[sid]['name']} 已停止", "warn")
        self.service_processes.clear()
        self._kill_port_processes()
        self._enable_buttons()

    def _stop_non_service_procs(self):
        """关闭GUI时调用：停止部署worker、下载进程等，但保留前后端服务进程继续运行"""
        # 停止部署worker
        if hasattr(self, '_deploy_worker') and self._deploy_worker and self._deploy_worker.isRunning():
            self._deploy_worker.cancel()
            self._deploy_worker.wait(3000)

        # 停止模型下载进程
        if hasattr(self, '_download_procs') and self._download_procs:
            for model_id, dl in list(self._download_procs.items()):
                proc = dl.get("proc")
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass

        # 停止前端调试轮询
        self._stop_fe_debug_polling()

        # 断开服务进程的信号连接（不终止进程本身）
        for sid, proc in list(self.service_processes.items()):
            if proc and proc.isRunning():
                try:
                    proc.finished.disconnect()
                except Exception:
                    pass

    def _kill_port_processes(self):
        port_map = {"backend": self._backend_port, "frontend": self._frontend_port}
        for sid, port in port_map.items():
            try:
                result = hidden_run(
                    ['netstat', '-aon', '-p', 'TCP'],
                    capture_output=True, text=True, timeout=5
                )
                port_pat = re.compile(rf':{port}\s')
                for line in result.stdout.split('\n'):
                    if port_pat.search(line) and 'LISTENING' in line:
                        parts = line.strip().split()
                        if len(parts) >= 5:
                            pid = int(parts[-1])
                            if pid != os.getpid():
                                hidden_run(
                                    ['taskkill', '/F', '/PID', str(pid)],
                                    capture_output=True, timeout=5
                                )
                                self._log(f"⏹ 已强制终止端口 {port} 的进程 (PID:{pid})", "warn")
            except Exception:
                pass

    def _restart_service(self, sid):
        self._log(f"正在重启 {SERVICES[sid]['name']}...", "info")
        if sid in self.service_processes:
            self.service_processes[sid].terminate()
            del self.service_processes[sid]
            time.sleep(1)
        if sid == "backend":
            self._start_backend()
        elif sid == "frontend":
            self._start_frontend()

    def _open_service(self, sid):
        if sid == "backend":
            self._show_engine_info()
        else:
            url = SERVICES[sid]["url"]
            self._open_url_in_browser(url)

    def _show_engine_info(self):
        """Show engine details dialog for the backend service."""
        dlg = QDialog(self)
        dlg.setWindowTitle("核心引擎信息")
        dlg.setMinimumWidth(480)
        dlg.setStyleSheet("""
            QDialog { background-color: #1E1E1E; color: #E0E0E0; }
            QGroupBox {
                color: #FFFFFF; font-weight: bold; font-size: 13px;
                border: 1px solid #444444; border-radius: 6px;
                margin-top: 12px; padding-top: 16px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
            QLabel { color: #CCCCCC; font-size: 12px; background: transparent; }
        """)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        # --- Engine Info ---
        grp1 = QGroupBox("引擎信息")
        g1 = QFormLayout(grp1)
        g1.setSpacing(6)
        g1.addRow("名称:", QLabel("LTX Video 引擎"))
        g1.addRow("版本:", QLabel(VERSION))
        g1.addRow("端口:", QLabel(str(SERVICES.get("backend", {}).get("port", "—"))))
        api_url = f"http://127.0.0.1:{SERVICES.get('backend', {}).get('port', 3000)}"
        g1.addRow("API 地址:", QLabel(api_url))
        running = self.service_cards.get("backend").is_running if self.service_cards.get("backend") else False
        status_lbl = QLabel("✅ 运行中" if running else "⏹ 已停止")
        status_lbl.setStyleSheet("color: #4CAF50;" if running else "color: #FF0000;")
        g1.addRow("状态:", status_lbl)
        layout.addWidget(grp1)

        # --- Hardware ---
        grp2 = QGroupBox("硬件信息")
        g2 = QFormLayout(grp2)
        g2.setSpacing(6)
        gpu_name = "—"
        vram_str = "—"
        try:
            import torch
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                vram_bytes = torch.cuda.get_device_properties(0).total_mem
                vram_str = f"{vram_bytes / (1024**3):.1f} GB"
            else:
                gpu_name = "无 CUDA 设备"
        except ImportError:
            gpu_name = "PyTorch 未安装"
        except Exception as e:
            gpu_name = f"检测失败: {e}"
        g2.addRow("GPU:", QLabel(gpu_name))
        g2.addRow("显存:", QLabel(vram_str))
        layout.addWidget(grp2)

        # --- Model Status ---
        grp3 = QGroupBox("模型状态")
        g3 = QFormLayout(grp3)
        g3.setSpacing(6)
        model_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
        file_count = 0
        if os.path.isdir(model_dir):
            for root, dirs, files in os.walk(model_dir):
                file_count += len(files)
        g3.addRow("模型目录:", QLabel(model_dir))
        g3.addRow("文件数量:", QLabel(str(file_count)))
        layout.addWidget(grp3)

        # Close button
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #333333; color: #FFFFFF;
                border: 1px solid #555555; border-radius: 6px;
                padding: 8px 24px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background-color: #444444; }
        """)
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        dlg.exec()

    def _open_usage_guide(self):
        dlg = UsageGuideDialog(self)
        dlg.exec()

    def _open_ui(self):
        self._open_url_in_browser(f"http://127.0.0.1:{self._frontend_port}")

    def _detect_browsers(self):
        import winreg
        browsers = []
        seen_paths = set()
        _browser_keywords = {
            "chrome", "chromium", "firefox", "edge", "msedge", "opera",
            "vivaldi", "brave", "yandex", "safari", "maxthon", "thorium",
            "waterfox", "palemoon", "iron", "slimjet", "comodo", "dragon",
            "avast", "secure", "epic", "tor", "falkon", "midori", "qutebrowser",
            "360se", "360chrome", "360chromex", "qqbrowser", "sogou",
            "twinkstar", "quark", "doubao", "liebao", "ucbrowser", "uc",
            "world", "avant", "green", "coolnovo", "baidu", "sogouexplorer",
            "se", "theworld", "2345explorer", "hao123", "huohou",
            "browser", "navigator",
        }
        _exclude_keywords = {
            "devenv", "game", "update", "setup", "install", "uninstall",
            "helper", "service", "crash", "reporter", "notification",
            "360game", "360safe", "360sd", "360tray", "360leakfixer",
            "zhudongfangyu", "software", "manager", "guard", "protect",
            "plugin", "extension", "addon",
        }

        def _is_browser(name, path):
            name_lower = name.lower()
            path_lower = path.lower()
            for ek in _exclude_keywords:
                if ek in name_lower or ek in os.path.basename(path_lower):
                    return False
            for bk in _browser_keywords:
                if bk in name_lower or bk in os.path.basename(path_lower):
                    return True
            return False

        def _add_browser(name, path):
            if not path or not os.path.isfile(path):
                return
            norm = os.path.normcase(os.path.abspath(path))
            if norm in seen_paths:
                return
            if not _is_browser(name, path):
                return
            seen_paths.add(norm)
            browsers.append((name, path))

        paths = [
            ("Chrome", os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe")),
            ("Chrome", os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe")),
            ("Chrome", os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")),
            ("Edge", os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe")),
            ("Edge", os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe")),
            ("Firefox", os.path.expandvars(r"%ProgramFiles%\Mozilla Firefox\firefox.exe")),
            ("Firefox", os.path.expandvars(r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe")),
            ("360安全浏览器", os.path.expandvars(r"%ProgramFiles%\360\360se6\Application\360se.exe")),
            ("360安全浏览器", os.path.expandvars(r"%ProgramFiles(x86)%\360\360se6\Application\360se.exe")),
            ("360安全浏览器", os.path.expandvars(r"%LocalAppData%\360\360se6\Application\360se.exe")),
            ("360极速浏览器", os.path.expandvars(r"%ProgramFiles%\360\360chrome\Chrome\Application\360chrome.exe")),
            ("360极速浏览器", os.path.expandvars(r"%ProgramFiles(x86)%\360\360chrome\Chrome\Application\360chrome.exe")),
            ("360极速浏览器", os.path.expandvars(r"%LocalAppData%\360\360chrome\Chrome\Application\360chrome.exe")),
            ("QQ浏览器", os.path.expandvars(r"%ProgramFiles%\Tencent\QQBrowser\QQBrowser.exe")),
            ("QQ浏览器", os.path.expandvars(r"%ProgramFiles(x86)%\Tencent\QQBrowser\QQBrowser.exe")),
            ("搜狗浏览器", os.path.expandvars(r"%ProgramFiles%\SogouExplorer\SogouExplorer.exe")),
            ("搜狗浏览器", os.path.expandvars(r"%ProgramFiles(x86)%\SogouExplorer\SogouExplorer.exe")),
            ("遨游浏览器", os.path.expandvars(r"%ProgramFiles%\Maxthon5\Bin\Maxthon.exe")),
            ("遨游浏览器", os.path.expandvars(r"%ProgramFiles(x86)%\Maxthon5\Bin\Maxthon.exe")),
            ("星愿浏览器", os.path.expandvars(r"%LocalAppData%\Twinkstar Browser\Application\twinkstar.exe")),
            ("Vivaldi", os.path.expandvars(r"%LocalAppData%\Vivaldi\Application\vivaldi.exe")),
            ("Vivaldi", os.path.expandvars(r"%ProgramFiles%\Vivaldi\Application\vivaldi.exe")),
            ("Brave", os.path.expandvars(r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe")),
            ("Opera", os.path.expandvars(r"%LocalAppData%\Programs\Opera\launcher.exe")),
            ("Opera GX", os.path.expandvars(r"%LocalAppData%\Programs\Opera GX\launcher.exe")),
            ("Yandex", os.path.expandvars(r"%LocalAppData%\Yandex\YandexBrowser\Application\browser.exe")),
            ("Waterfox", os.path.expandvars(r"%ProgramFiles%\Waterfox\waterfox.exe")),
            ("Thorium", os.path.expandvars(r"%LocalAppData%\Thorium\Application\thorium.exe")),
            ("猎豹浏览器", os.path.expandvars(r"%ProgramFiles%\liebao\LiebaoBrowser\liebao.exe")),
            ("猎豹浏览器", os.path.expandvars(r"%ProgramFiles(x86)%\liebao\LiebaoBrowser\liebao.exe")),
            ("世界之窗浏览器", os.path.expandvars(r"%ProgramFiles%\TheWorld\theworld.exe")),
            ("世界之窗浏览器", os.path.expandvars(r"%ProgramFiles(x86)%\TheWorld\theworld.exe")),
            ("Pale Moon", os.path.expandvars(r"%ProgramFiles%\Pale Moon\palemoon.exe")),
        ]
        for name, path in paths:
            _add_browser(name, path)

        def _reg_enum_startmenu(hive):
            for root_key in [
                r"SOFTWARE\Clients\StartMenuInternet",
                r"SOFTWARE\WOW6432Node\Clients\StartMenuInternet",
            ]:
                try:
                    key = winreg.OpenKey(hive, root_key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
                    i = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            i += 1
                            try:
                                sk = winreg.OpenKey(key, fr"{subkey_name}\shell\open\command")
                                cmd, _ = winreg.QueryValueEx(sk, "")
                                winreg.CloseKey(sk)
                                if cmd:
                                    exe_path = cmd.strip('"').split('"')[0] if '"' in cmd else cmd.split()[0]
                                    display_name = subkey_name
                                    try:
                                        nk = winreg.OpenKey(key, subkey_name)
                                        dn, _ = winreg.QueryValueEx(nk, "")
                                        winreg.CloseKey(nk)
                                        if dn:
                                            display_name = dn
                                    except Exception:
                                        pass
                                    _add_browser(display_name, exe_path)
                            except Exception:
                                pass
                        except Exception:
                            break
                    winreg.CloseKey(key)
                except Exception:
                    pass

        _reg_enum_startmenu(winreg.HKEY_LOCAL_MACHINE)
        _reg_enum_startmenu(winreg.HKEY_CURRENT_USER)

        def _reg_enum_app_paths(hive):
            try:
                key = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
                i = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        i += 1
                        if not subkey_name.lower().endswith(".exe"):
                            continue
                        try:
                            sk = winreg.OpenKey(key, subkey_name)
                            path_val, _ = winreg.QueryValueEx(sk, "")
                            winreg.CloseKey(sk)
                            if path_val:
                                exe_path = path_val.strip('"').split('"')[0] if '"' in path_val else path_val.split()[0]
                                browser_name = subkey_name.replace(".exe", "")
                                _add_browser(browser_name, exe_path)
                        except Exception:
                            pass
                    except Exception:
                        break
                winreg.CloseKey(key)
            except Exception:
                pass

        _reg_enum_app_paths(winreg.HKEY_LOCAL_MACHINE)
        _reg_enum_app_paths(winreg.HKEY_CURRENT_USER)

        result = {"系统默认": "system"}
        for name, path in browsers:
            result[name] = path
        return result

    def _on_browser_changed(self, index):
        selected_data = self.browser_combo.itemData(index)
        if selected_data is None:
            return
        is_custom = selected_data == "custom"
        self.browser_path_edit.setVisible(is_custom)
        self.btn_select_browser.setVisible(is_custom)
        if is_custom:
            self.selected_browser = "custom"
            self.config.set("browser.default", "custom")
        else:
            self.selected_browser = selected_data
            self.config.set("browser.default", selected_data)
            browser_name = selected_data if selected_data == "system" else ""
            if not browser_name:
                for name, path in self.browsers.items():
                    if path == selected_data:
                        browser_name = name
                        break
                if not browser_name:
                    browser_name = os.path.basename(selected_data) if selected_data else "未知"
            self._log(f"已设置浏览器: {browser_name}", "info")

    def _on_custom_browser_path_changed(self, path):
        self.custom_browser_path = path
        self.config.set("browser.custom_path", path)
        if path and os.path.exists(path):
            self.selected_browser = "custom"
            self.config.set("browser.default", "custom")
            self._log(f"已设置自定义浏览器: {path}", "info")

    def _on_port_change_clicked(self, sid):
        new_port = SERVICES[sid]["port"]
        other_sid = "frontend" if sid == "backend" else "backend"
        if new_port == SERVICES[other_sid]["port"]:
            self._log(f"△ 不能与 {SERVICES[other_sid]['name']} 使用相同端口 ({new_port})", "warn")
            return
        try:
            conn = socket.create_connection(('127.0.0.1', new_port), timeout=0.5)
            conn.close()
            self._log(f"△ 经检测端口 {new_port} 已被占用，请更改", "warn")
            return
        except Exception:
            pass
        old_port = self._backend_port if sid == "backend" else self._frontend_port
        was_running = sid in self.service_processes and self.service_processes[sid].isRunning()
        if was_running:
            self._log(f"正在停止 {SERVICES[sid]['name']} (端口 {old_port})...", "warn")
            self.service_processes[sid].terminate()
            del self.service_processes[sid]
            self._kill_specific_port(old_port)
            if not self._wait_for_port_free(old_port, timeout=8):
                self._log(f"△ 端口 {old_port} 释放超时，强制继续...", "warn")
        if sid == "backend":
            self._backend_port = new_port
            self.config.set("ports.backend", new_port)
        else:
            self._frontend_port = new_port
            self.config.set("ports.frontend", new_port)
        SERVICES[sid]["url"] = f"http://127.0.0.1:{new_port}"
        if was_running:
            self._log(f"正在以新端口 {new_port} 重启 {SERVICES[sid]['name']}...", "info")
            if sid == "backend":
                self._start_backend()
                frontend_running = "frontend" in self.service_processes and self.service_processes["frontend"].isRunning()
                if frontend_running:
                    self._log("等待核心引擎就绪后重启 AI视频工作站...", "info")
                    if hasattr(self, '_port_change_timer') and self._port_change_timer is not None:
                        self._port_change_timer.stop()
                    self._port_change_wait_count = 0
                    self._port_change_timer = QTimer(self)
                    self._port_change_timer.timeout.connect(self._on_port_change_backend_poll)
                    self._port_change_timer.start(1000)
            else:
                self._start_frontend()
                self._log("等待 AI视频工作站就绪后打开浏览器...", "info")
                if hasattr(self, '_port_change_fe_timer') and self._port_change_fe_timer is not None:
                    self._port_change_fe_timer.stop()
                self._port_change_fe_wait_count = 0
                self._port_change_open_browser = True
                self._port_change_fe_timer = QTimer(self)
                self._port_change_fe_timer.timeout.connect(self._on_port_change_frontend_poll)
                self._port_change_fe_timer.start(1000)
        else:
            self._log(f"{SERVICES[sid]['name']} 端口已设为 {new_port}（启动服务后生效）", "info")

    def _kill_specific_port(self, port):
        try:
            result = hidden_run(
                ['netstat', '-aon', '-p', 'TCP'],
                capture_output=True, text=True, timeout=5
            )
            port_pat = re.compile(rf':{port}\s')
            for line in result.stdout.split('\n'):
                if port_pat.search(line) and 'LISTENING' in line:
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        pid = int(parts[-1])
                        if pid != os.getpid():
                            hidden_run(
                                ['taskkill', '/F', '/PID', str(pid)],
                                capture_output=True, timeout=5
                            )
                            self._log(f"⏹ 已强制终止端口 {port} 的进程 (PID:{pid})", "warn")
        except Exception:
            pass

    def _wait_for_port_free(self, port, timeout=8):
        start = time.time()
        while time.time() - start < timeout:
            try:
                conn = socket.create_connection(('127.0.0.1', port), timeout=0.5)
                conn.close()
                time.sleep(0.3)
            except Exception:
                return True
        return False

    def _on_port_change_backend_poll(self):
        self._port_change_wait_count += 1
        try:
            conn = socket.create_connection(('127.0.0.1', self._backend_port), timeout=1)
            conn.close()
            self._port_change_timer.stop()
            self._log(f"核心引擎已就绪 (端口{self._backend_port})，正在重启 AI视频工作站...", "ok")
            frontend_running = "frontend" in self.service_processes and self.service_processes["frontend"].isRunning()
            if frontend_running:
                self.service_processes["frontend"].terminate()
                del self.service_processes["frontend"]
                self._kill_specific_port(self._frontend_port)
                self._wait_for_port_free(self._frontend_port, timeout=5)
            self._start_frontend()
        except Exception:
            if self._port_change_wait_count >= 90:
                self._port_change_timer.stop()
                self._log("× 等待核心引擎就绪超时，AI视频工作站未重启", "err")

    def _on_port_change_frontend_poll(self):
        self._port_change_fe_wait_count += 1
        try:
            conn = socket.create_connection(('127.0.0.1', self._frontend_port), timeout=1)
            conn.close()
            self._port_change_fe_timer.stop()
            self._log(f"AI视频工作站已就绪 (端口{self._frontend_port})", "ok")
            if getattr(self, '_port_change_open_browser', False):
                self._port_change_open_browser = False
                self._open_ui()
        except Exception:
            if self._port_change_fe_wait_count >= 30:
                self._port_change_fe_timer.stop()
                self._log("× 等待 AI视频工作站就绪超时", "err")

    def _select_custom_browser(self):
        file_dialog = QFileDialog()
        file_dialog.setNameFilter("可执行文件 (*.exe)")
        file_dialog.setWindowTitle("选择浏览器可执行文件")
        if file_dialog.exec():
            selected = file_dialog.selectedFiles()
            if selected:
                self.browser_path_edit.setText(selected[0])

    def _open_url_in_browser(self, url):
        if self.selected_browser == "custom" and self.custom_browser_path and os.path.exists(self.custom_browser_path):
            try:
                hidden_popen([self.custom_browser_path, url])
                self._log(f"使用自定义浏览器打开: {url}", "info")
                return
            except Exception as e:
                self._log(f"打开自定义浏览器失败: {e}", "err")
        elif self.selected_browser != "system" and self.selected_browser != "custom":
            if self.selected_browser and os.path.exists(self.selected_browser):
                try:
                    hidden_popen([self.selected_browser, url])
                    browser_name = os.path.basename(self.selected_browser)
                    self._log(f"使用 {browser_name} 打开: {url}", "info")
                    return
                except Exception as e:
                    self._log(f"打开浏览器失败: {e}", "err")
        webbrowser.open(url)

    def _on_process_finished(self, exit_code, _):
        for sid, proc in list(self.service_processes.items()):
            if not proc.isRunning():
                self._log(f"△ {SERVICES[sid]['name']} 进程已退出 (code={exit_code})", "warn")
                del self.service_processes[sid]
        if not self.service_processes and not self.is_starting:
            any_alive = any(card.is_running for card in self.service_cards.values())
            if not any_alive:
                self._enable_buttons()

    def _show_deploy_info(self):
        info_lines = [
            "📋 整合包说明：",
            "",
            "  三目录纯净整合包结构：",
            "  app/                ← 应用程序（只读，纯净可发布）",
            "  app/resources/      ← 后端、补丁、前端等资源",
            "",
            "  data/               ← 用户数据（可写，需要备份）",
            "  data/.venv/         ← Python 虚拟环境（UV 自动创建）",
            "  data/outputs/       ← 生成的视频/图像/音频",
            "  data/uploads/       ← 上传的参考图片",
            "  data/models/        ← AI 模型文件",
            "  data/settings.json  ← 用户设置",
            "",
            "  temp/               ← 临时文件（可删除，无需备份）",
            "  temp/logs/          ← 日志文件",
            "  temp/cache/         ← 缓存文件",
            "",
            "  部署维护会自动完成：",
            "  1. 下载 UV 包管理器（国内镜像）",
            "  2. 安装 Python 3.12 + 创建虚拟环境到 data/.venv/（UV 自动管理）",
            "  3. 安装 PyTorch + CUDA 12.8 + 项目依赖 + TTS语音依赖（UV 极速安装）",
            "  4. 部署补丁文件和前端界面",
            "  5. 自动下载 LTX Desktop 并提取后端代码",
            "  6. 下载 AI 模型（HF-Mirror 国内镜像）",
            "  7. 自动配置所有路径",
        ]
        for line in info_lines:
            self._log(line, "info")

    def _speed_test_mirrors(self):
        # 检查缓存（24小时内有效）
        cache = self._load_speed_cache()
        if cache:
            self._speed_results = cache.get("ping_results", {})
            self._speed_gh_ok = cache.get("gh_ok", {})
            self._speed_probe_results = cache.get("probe_results", {})
            self._speed_after_deploy = False
            # 直接显示缓存结果
            self._on_speed_test_done()
            return

        self._speed_results = {}
        self._speed_gh_ok = {}
        self._speed_probe_results = {}
        self._speed_phase = "ping"
        self._speed_queue = list(MIRROR_SOURCES.keys())
        self._speed_after_deploy = False
        self.speed_result_label.setText("• 测速中...")
        self.speed_result_label.setStyleSheet("font-size: 10px; color: #FFA726; background: transparent;")
        self._ping_next()

    def _ping_next(self):
        if not self._speed_queue:
            if self._speed_phase == "ping":
                self._speed_phase = "gh"
                self._speed_queue = ["ghfast", "ghproxy", "ghgo"]
                # 更新引导横幅
                if self._guide_active and self._guide_step == 1:
                    self._guide_deploy_sub_hint = "检测GitHub镜像可用性…"
                    self._update_guide_banner()
                self._check_gh_next()
            else:
                self._on_speed_test_done()
            return
        key = self._speed_queue.pop(0)
        host = MIRROR_SOURCES[key]["test_host"]
        # 更新引导横幅
        if self._guide_active and self._guide_step == 1:
            label = MIRROR_SOURCES[key].get("label", key)
            self._guide_deploy_sub_hint = f"测速中: {label}…"
            self._update_guide_banner()
        self._ping_proc = QProcess(self)
        self._ping_proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._ping_key = key
        self._ping_proc.finished.connect(self._on_ping_finished)
        self._ping_proc.errorOccurred.connect(self._on_ping_error)
        self._ping_proc.start("ping", ["-n", "1", "-w", "2000", host])

    def _on_ping_error(self, error):
        proc = getattr(self, '_ping_proc', None)
        if proc is None:
            return
        key = getattr(self, '_ping_key', '')
        if key:
            self._speed_results[key] = 99999
        try:
            proc.finished.disconnect()
        except Exception:
            pass
        try:
            proc.errorOccurred.disconnect()
        except Exception:
            pass
        proc.deleteLater()
        self._ping_proc = None
        self._ping_next()

    def _on_ping_finished(self, exit_code, exit_status):
        proc = getattr(self, '_ping_proc', None)
        if proc is None:
            return
        output = bytes(proc.readAllStandardOutput()).decode("gbk", errors="ignore")
        match = re.search(r"(?:时间|time)[=<](\d+)ms", output, re.IGNORECASE)
        self._speed_results[self._ping_key] = float(match.group(1)) if match else 99999
        try:
            proc.finished.disconnect()
        except Exception:
            pass
        try:
            proc.errorOccurred.disconnect()
        except Exception:
            pass
        proc.deleteLater()
        self._ping_proc = None
        self._ping_next()

    def _check_gh_next(self):
        if not self._speed_queue:
            # GH 检测完成，进入真实下载探测阶段
            self._start_probe_phase()
            return
        gh_key = self._speed_queue.pop(0)
        gh_hosts = {
            "ghfast": "https://ghfast.top/https://github.com",
            "ghproxy": "https://gh-proxy.com/https://github.com",
            "ghgo": "https://ghgo.xyz/https://github.com",
        }
        url = gh_hosts.get(gh_key)
        if not url:
            self._check_gh_next()
            return
        # 更新引导横幅
        if self._guide_active and self._guide_step == 1:
            gh_labels = {"ghfast": "GHFast", "ghproxy": "GH-Proxy", "ghgo": "GHGo"}
            self._guide_deploy_sub_hint = f"检测 {gh_labels.get(gh_key, gh_key)} 可用性…"
            self._update_guide_banner()
        self._gh_key = gh_key
        self._gh_proc = QProcess(self)
        self._gh_proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._gh_proc.finished.connect(self._on_gh_check_finished)
        self._gh_proc.errorOccurred.connect(self._on_gh_error)
        self._gh_proc.start("curl.exe", ["-s", "-o", "NUL", "-w", "%{http_code}", "--head", "--connect-timeout", "5", "-m", "8", url])

    def _on_gh_error(self, error):
        proc = getattr(self, '_gh_proc', None)
        if proc is None:
            return
        gh_key = getattr(self, '_gh_key', '')
        if gh_key:
            self._speed_gh_ok[gh_key] = False
        try:
            proc.finished.disconnect()
        except Exception:
            pass
        try:
            proc.errorOccurred.disconnect()
        except Exception:
            pass
        proc.deleteLater()
        self._gh_proc = None
        self._check_gh_next()

    def _on_gh_check_finished(self, exit_code, exit_status):
        proc = getattr(self, '_gh_proc', None)
        if proc is None:
            return
        output = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="ignore").strip()
        self._speed_gh_ok[self._gh_key] = output.startswith("2") or output.startswith("3")
        try:
            proc.finished.disconnect()
        except Exception:
            pass
        try:
            proc.errorOccurred.disconnect()
        except Exception:
            pass
        proc.deleteLater()
        self._gh_proc = None
        self._check_gh_next()

    def _build_uv_urls(self, source_key):
        src = MIRROR_SOURCES.get(source_key, MIRROR_SOURCES["tsinghua"])
        # 优先使用用户选择的镜像源配置的URL列表
        urls = list(src.get("uv_urls", []))
        # 根据测速结果将可用的GH镜像提到前面
        priority_urls = []
        remaining_urls = []
        gh_mirror_names = {}
        if self._speed_gh_ok.get("ghfast"):
            gh_mirror_names["GHFast"] = True
        if self._speed_gh_ok.get("ghproxy"):
            gh_mirror_names["GH-Proxy"] = True
        if self._speed_gh_ok.get("ghgo"):
            gh_mirror_names["GHGo"] = True
        for url, name in urls:
            is_gh_mirror = any(k in name for k in gh_mirror_names)
            if is_gh_mirror and gh_mirror_names.get(next((k for k in gh_mirror_names if k in name), ""), False):
                priority_urls.append((url, name))
            elif name == "GitHub直连":
                remaining_urls.append((url, name))
            else:
                remaining_urls.append((url, name))
        return priority_urls + remaining_urls

    def _build_ltx_urls(self, source_key):
        src = MIRROR_SOURCES.get(source_key, MIRROR_SOURCES["tsinghua"])
        # 优先使用用户选择的镜像源配置的URL列表
        urls = list(src.get("ltx_urls", []))
        # 根据测速结果将可用的GH镜像提到前面
        priority_urls = []
        remaining_urls = []
        gh_mirror_names = {}
        if self._speed_gh_ok.get("ghfast"):
            gh_mirror_names["GHFast"] = True
        if self._speed_gh_ok.get("ghproxy"):
            gh_mirror_names["GH-Proxy"] = True
        if self._speed_gh_ok.get("ghgo"):
            gh_mirror_names["GHGo"] = True
        for url, name in urls:
            is_gh_mirror = any(k in name for k in gh_mirror_names)
            if is_gh_mirror and gh_mirror_names.get(next((k for k in gh_mirror_names if k in name), ""), False):
                priority_urls.append((url, name))
            elif name == "GitHub直连":
                remaining_urls.append((url, name))
            else:
                remaining_urls.append((url, name))
        return priority_urls + remaining_urls

    def _start_probe_phase(self):
        """启动真实下载探测阶段"""
        self._speed_probe_results = {}
        # 更新引导横幅
        if self._guide_active and self._guide_step == 1:
            self._guide_deploy_sub_hint = "真实下载测速中…"
            self._update_guide_banner()
        probe_urls = []
        # 对每个可用的 GH 镜像取第一个 URL 做探测
        gh_probe_map = {
            "ghfast": ("https://ghfast.top/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GHFast"),
            "ghproxy": ("https://gh-proxy.com/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GH-Proxy"),
            "ghgo": ("https://ghgo.xyz/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", "GHGo"),
        }
        for gh_key, (url, name) in gh_probe_map.items():
            if self._speed_gh_ok.get(gh_key):
                probe_urls.append((url, name))
        # HF 镜像探测
        probe_urls.append(("https://hf-mirror.com/", "HF-Mirror"))
        if not probe_urls:
            self._on_speed_test_done()
            return
        self.speed_result_label.setText("• 测速中(真实探测)...")
        self._probe_worker = _SpeedProbeWorker(probe_urls, self)
        self._probe_worker.finished.connect(self._on_probe_finished)
        self._probe_worker.start()

    def _on_probe_finished(self, results):
        """真实探测完成，保存结果并结束测速"""
        self._speed_probe_results = results
        # 保存到缓存
        self._save_speed_cache()
        self._on_speed_test_done()

    def _load_speed_cache(self):
        """加载测速缓存，返回缓存数据或 None"""
        settings_path = os.path.join(self._data_dir, "settings.json")
        if not os.path.exists(settings_path):
            return None
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
            cache = settings.get("speed_cache")
            if not cache:
                return None
            # 缓存有效期 24 小时
            ts = cache.get("timestamp", 0)
            if time.time() - ts > 86400:
                return None
            return cache
        except Exception:
            return None

    def _save_speed_cache(self):
        """保存测速结果到缓存"""
        settings_path = os.path.join(self._data_dir, "settings.json")
        settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
            except Exception:
                pass
        cache = {
            "timestamp": time.time(),
            "ping_results": self._speed_results,
            "gh_ok": self._speed_gh_ok,
            "probe_results": {name: {"speed_bps": v["speed_bps"], "first_byte_ms": v["first_byte_ms"]}
                              for name, v in self._speed_probe_results.items()},
        }
        settings["speed_cache"] = cache
        try:
            with open(settings_path, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_speed_test_done(self):
        fastest = "tsinghua"
        valid = {k: v for k, v in self._speed_results.items() if v < 99999}
        if valid:
            fastest = min(valid, key=valid.get)
        # 更新引导横幅
        if self._guide_active and self._guide_step == 1:
            fastest_label = MIRROR_SOURCES[fastest]['label']
            self._guide_deploy_sub_hint = f"已选择最快源: {fastest_label}，准备开始部署…"
            self._update_guide_banner()
        # 如果有探测结果，根据真实下载速度选择最快源
        probe_results = getattr(self, '_speed_probe_results', {})
        if probe_results:
            best_probe = max(probe_results.items(), key=lambda x: x[1].get("speed_bps", 0))
            if best_probe:
                speed_mbps = best_probe[1]["speed_bps"] / (1024 * 1024)
                self.speed_result_label.setText(f"⚡ {best_probe[0]} {speed_mbps:.1f}MB/s")
                self.speed_result_label.setStyleSheet("font-size: 10px; color: #66BB6A; background: transparent;")
        else:
            self.speed_result_label.setText(f"⚡ {MIRROR_SOURCES[fastest]['label']}")
            self.speed_result_label.setStyleSheet("font-size: 10px; color: #66BB6A; background: transparent;")
        # 显示 GH 镜像可用状态和探测速度
        gh_labels = []
        for gh_key, label in [("ghfast", "GHFast"), ("ghproxy", "GH-Proxy"), ("ghgo", "GHGo")]:
            if self._speed_gh_ok.get(gh_key):
                probe = probe_results.get(label)
                if probe:
                    gh_labels.append(f"{label}({probe['speed_bps']/(1024*1024):.1f}MB/s)")
                else:
                    gh_labels.append(label)
        if hasattr(self, 'deploy_source_combo'):
            for i in range(self.deploy_source_combo.count()):
                if self.deploy_source_combo.itemData(i) == fastest:
                    self.deploy_source_combo.setCurrentIndex(i)
                    break
        if self._speed_after_deploy:
            deploy_source = getattr(self, '_speed_deploy_source', None) or fastest
            self._start_deploy_worker_with_speed(deploy_source)
            self._speed_after_deploy = False
            self._speed_deploy_source = None

    def _show_newbie_guide(self):
        """显示新手引导横幅（全局共享，位于导航栏下方）"""
        # 如果引导已完成，不显示
        if self.config.get("guide_completed", False):
            return
        # 如果环境已就绪且模型齐全，标记完成不显示
        uv_ok = self._check_uv_ok() if hasattr(self, '_check_uv_ok') else False
        python_ok = self._check_python_ok() if hasattr(self, '_check_python_ok') else False
        models_ok = self._check_required_models_ok() if hasattr(self, '_check_required_models_ok') else False
        if uv_ok and python_ok and models_ok:
            self.config.set("guide_completed", True)
            return

        # 激活引导
        self._guide_active = True
        if self._guide_step == 0:
            self._guide_step = 1  # 默认从步骤1开始
        self._guide_auto = True

        # 如果横幅已存在，直接显示
        if self._guide_banner:
            self._guide_banner.setVisible(True)
            self._update_guide_banner()
            return

        # 创建引导横幅
        self._guide_banner = QFrame()
        self._guide_banner.setObjectName("guideBannerFrame")
        self._guide_banner.setStyleSheet("""
            #guideBannerFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0D47A1, stop:0.5 #1565C0, stop:1 #0D47A1);
                border: none;
                border-radius: 0px;
            }
        """)
        banner_layout = QHBoxLayout(self._guide_banner)
        banner_layout.setContentsMargins(20, 10, 16, 10)
        banner_layout.setSpacing(12)

        # 左侧：步骤指示器 ① → ② → ③
        step_container = QHBoxLayout()
        step_container.setSpacing(4)
        self._guide_step_labels = []
        step_texts = ["① 部署", "② 模型", "③ 运行"]
        for i, text in enumerate(step_texts):
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedHeight(28)
            lbl.setMinimumWidth(60)
            lbl.setStyleSheet("""
                font-size: 12px; font-weight: bold;
                color: #888888; background: transparent; border: none;
                padding: 2px 8px; border-radius: 4px;
            """)
            step_container.addWidget(lbl)
            self._guide_step_labels.append(lbl)
            # 箭头（最后一个不加）
            if i < len(step_texts) - 1:
                arrow = QLabel("→")
                arrow.setStyleSheet("font-size: 14px; color: #666666; background: transparent; border: none;")
                arrow.setFixedWidth(16)
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                step_container.addWidget(arrow)
        banner_layout.addLayout(step_container)

        # 分隔线
        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setStyleSheet("background: rgba(255,255,255,60); border: none;")
        banner_layout.addWidget(sep)

        # 中间：步骤描述（支持多行，更丰富的文字）
        self._guide_desc_label = QLabel()
        self._guide_desc_label.setStyleSheet(
            "font-size: 12px; color: #E3F2FD; background: transparent; border: none; line-height: 1.5;"
        )
        self._guide_desc_label.setWordWrap(True)
        banner_layout.addWidget(self._guide_desc_label, 1)

        # 右侧区域
        right_layout = QVBoxLayout()
        right_layout.setSpacing(2)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # 全自动开关 + 关闭按钮行
        switch_row = QHBoxLayout()
        switch_row.setSpacing(8)

        auto_label = QLabel("自动引导")
        auto_label.setStyleSheet("font-size: 11px; color: #BBDEFB; background: transparent; border: none;")
        switch_row.addWidget(auto_label)

        self._guide_auto_switch = ToggleSwitch(checked=True, checked_color="#CC0000")
        self._guide_auto_switch.setFixedHeight(20)
        self._guide_auto_switch.toggled.connect(self._on_guide_auto_toggled)
        switch_row.addWidget(self._guide_auto_switch)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent; color: rgba(255,255,255,120);
                border: none; font-size: 13px; font-weight: bold; border-radius: 3px;
            }
            QPushButton:hover {
                color: #FFFFFF; background-color: rgba(255,255,255,40);
            }
        """)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self._close_guide_banner)
        switch_row.addWidget(close_btn)
        right_layout.addLayout(switch_row)

        # 半自动按钮行（默认隐藏）
        self._guide_btn_row = QHBoxLayout()
        self._guide_btn_row.setSpacing(6)

        self._guide_next_btn = QPushButton("下一步")
        self._guide_next_btn.setFixedSize(60, 22)
        self._guide_next_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255,255,255,30); color: #FFFFFF;
                border: 1px solid rgba(255,255,255,80); border-radius: 3px;
                font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background-color: rgba(255,255,255,50); }
        """)
        self._guide_next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._guide_next_btn.clicked.connect(self._on_guide_next)
        self._guide_next_btn.setVisible(False)
        self._guide_btn_row.addWidget(self._guide_next_btn)

        self._guide_retry_btn = QPushButton("重试")
        self._guide_retry_btn.setFixedSize(50, 22)
        self._guide_retry_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255,255,255,30); color: #FFFFFF;
                border: 1px solid rgba(255,255,255,80); border-radius: 3px;
                font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background-color: rgba(255,255,255,50); }
        """)
        self._guide_retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._guide_retry_btn.clicked.connect(self._on_guide_retry)
        self._guide_retry_btn.setVisible(False)
        self._guide_btn_row.addWidget(self._guide_retry_btn)

        self._guide_skip_btn = QPushButton("跳过")
        self._guide_skip_btn.setFixedSize(50, 22)
        self._guide_skip_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255,200,0,30); color: #E3F2FD;
                border: 1px solid rgba(255,200,0,80); border-radius: 3px;
                font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background-color: rgba(255,200,0,50); }
        """)
        self._guide_skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._guide_skip_btn.clicked.connect(self._on_guide_skip)
        self._guide_skip_btn.setVisible(False)
        self._guide_btn_row.addWidget(self._guide_skip_btn)

        right_layout.addLayout(self._guide_btn_row)
        banner_layout.addLayout(right_layout)

        # 插入到 main_layout 中 nav_bar 之后、page_stack 之前
        central = self.centralWidget()
        if central:
            main_layout = central.layout()
            if main_layout:
                # nav_bar 是 index 0，page_stack 是 index 1
                # 在 page_stack 之前插入横幅
                main_layout.insertWidget(1, self._guide_banner)

        self._update_guide_banner()

    def _update_guide_banner(self):
        """更新引导横幅的显示状态"""
        if not self._guide_banner or not self._guide_banner.isVisible():
            return

        step = self._guide_step
        step_names = ["① 部署", "② 模型", "③ 运行"]
        is_auto = self._guide_auto

        # 更新步骤指示器样式
        for i, lbl in enumerate(self._guide_step_labels):
            current_step = i + 1  # 1-based
            if current_step < step:
                # 已完成步骤：绿色打勾
                check_names = ["✓ 部署", "✓ 模型", "✓ 运行"]
                lbl.setText(check_names[i])
                lbl.setStyleSheet("""
                    font-size: 12px; font-weight: bold;
                    color: #66BB6A; background: transparent; border: none;
                    padding: 2px 8px; border-radius: 4px;
                """)
            elif current_step == step:
                # 当前步骤：白色加粗 + 微妙呼吸动画背景
                lbl.setText(step_names[i])
                lbl.setStyleSheet("""
                    font-size: 12px; font-weight: bold;
                    color: #FFFFFF; background: rgba(255,255,255,40);
                    border: none; padding: 2px 8px; border-radius: 4px;
                """)
            else:
                # 未到达步骤：淡蓝灰色
                lbl.setText(step_names[i])
                lbl.setStyleSheet("""
                    font-size: 12px; font-weight: bold;
                    color: #90CAF9; background: transparent; border: none;
                    padding: 2px 8px; border-radius: 4px;
                """)

        # 更新步骤描述 — 丰富、动态、亲切的文字
        if step == 1:
            if is_auto:
                # 获取当前部署子步骤进度
                sub_hint = getattr(self, '_guide_deploy_sub_hint', '')
                model_hint = getattr(self, '_guide_model_sub_hint', '')
                if sub_hint and model_hint:
                    desc = f"正在为您准备运行环境… {sub_hint}　|　{model_hint}"
                elif sub_hint:
                    desc = f"正在为您准备运行环境… {sub_hint}"
                elif model_hint:
                    desc = f"欢迎使用云集智能视频创意站！\u2003{model_hint}"
                else:
                    desc = ("欢迎使用云集智能视频创意站！\u2003首次使用将自动安装运行环境"
                            "（UV包管理器 → Python → 核心依赖 → 扩展组件），请耐心等待。")
            else:
                desc = ("点击「下一步」开始安装运行环境"
                        "（UV包管理器 → Python → 核心依赖 → 扩展组件）。")
        elif step == 2:
            if is_auto:
                # 获取模型下载子步骤进度
                sub_hint = getattr(self, '_guide_model_sub_hint', '')
                if sub_hint:
                    desc = f"正在下载必需AI模型… {sub_hint}"
                else:
                    desc = ("环境已就绪！正在下载必需AI模型（约5GB），"
                            "模型是AI创作的核心引擎，下载完成后即可开始创作。")
            else:
                desc = ("点击「下一步」下载必需AI模型（约5GB），"
                        "模型是AI创作的核心引擎。如暂不需要可点击「跳过」。")
        elif step == 3:
            if is_auto:
                desc = "模型就绪！正在启动前后端服务，浏览器将自动打开…"
            else:
                desc = "点击「下一步」启动前后端服务，浏览器将自动打开前端界面。"
        elif step >= 4:
            desc = "🎉 全部就绪！欢迎使用云集智能视频创意站，开启您的AI创作之旅！"
        else:
            desc = ""

        self._guide_desc_label.setText(desc)

        # 更新按钮可见性
        self._guide_next_btn.setVisible(not is_auto and step < 4)
        self._guide_retry_btn.setVisible(not is_auto and step < 4)
        self._guide_skip_btn.setVisible(not is_auto and step == 2)

        # 引导完成
        if step >= 4:
            self._guide_complete()

    def _on_guide_auto_toggled(self, checked):
        """全自动开关切换"""
        self._guide_auto = checked
        self._write_debug_log(f"[引导] 全自动模式: {'开启' if checked else '关闭'}")
        if checked:
            # 切回全自动，自动执行当前步骤
            self._execute_guide_step()
        self._update_guide_banner()

    def _execute_guide_step(self):
        """执行当前引导步骤"""
        if not self._guide_active:
            return
        step = self._guide_step
        if step == 1:
            # 步骤1：部署维护（跳过模型下载）
            self._switch_page(1)
            # 立即显示检测网络信息
            self._guide_deploy_sub_hint = "正在检测网络环境…"
            self._update_guide_banner()
            self._one_click_deploy(skip_models=True)
            # 启动轮询：检测huggingface_hub是否可用，可用则后台启动模型下载
            if not self._guide_bg_models_started:
                self._start_bg_model_poll()
        elif step == 2:
            # 步骤2：模型管理
            self._switch_page(2)
            # 刷新模型表格（确保必需模型置顶高亮）
            if hasattr(self, '_populate_model_table'):
                QTimer.singleShot(300, self._populate_model_table)
            # 先检查是否所有必需模型已就绪
            if self._check_required_models_ok():
                self._write_debug_log("[引导] 必需模型已全部就绪，直接推进到步骤3")
                self._guide_advance(3)
            else:
                # 如果后台下载已在进行，检查是否有失败需要重试
                bg_active = (self._guide_bg_models_started
                             and hasattr(self, '_download_procs') and self._download_procs)
                if bg_active:
                    self._write_debug_log("[引导] 模型下载已在后台进行中，继续等待")
                else:
                    # 延迟启动下载，确保页面切换完成
                    QTimer.singleShot(500, self._download_required_models)
                self._guide_model_sub_hint = "正在下载必需模型…"
                self._update_guide_banner()
        elif step == 3:
            # 步骤3：运行服务
            self._switch_page(0)
            # 勾选"启动后打开"
            if hasattr(self, 'auto_open_checkbox'):
                self.auto_open_checkbox.setChecked(True)
            self._start_all()

    def _on_guide_next(self):
        """半自动模式下的"下一步"按钮"""
        step = self._guide_step
        if step == 1:
            # 执行部署
            self._switch_page(1)
            self._guide_deploy_sub_hint = "正在检测网络环境…"
            self._update_guide_banner()
            self._one_click_deploy(skip_models=True)
        elif step == 2:
            # 执行模型下载
            self._switch_page(2)
            if self._check_required_models_ok():
                self._guide_advance(3)
            else:
                bg_active = (self._guide_bg_models_started
                             and hasattr(self, '_download_procs') and self._download_procs)
                if not bg_active:
                    self._download_required_models()
        elif step == 3:
            # 执行服务启动
            self._switch_page(0)
            if hasattr(self, 'auto_open_checkbox'):
                self.auto_open_checkbox.setChecked(True)
            self._start_all()

    def _on_guide_retry(self):
        """重试当前步骤"""
        self._guide_auto = True
        if hasattr(self, '_guide_auto_switch') and self._guide_auto_switch:
            self._guide_auto_switch.setChecked(True)
        self._execute_guide_step()
        self._update_guide_banner()

    def _on_guide_skip(self):
        """跳过当前步骤（仅步骤2可用）"""
        if self._guide_step == 2:
            self._guide_step = 3
            self._write_debug_log("[引导] 跳过模型下载，进入步骤3")
            if self._guide_auto:
                self._execute_guide_step()
            self._update_guide_banner()

    def _guide_advance(self, next_step):
        """推进到下一步"""
        self._guide_step = next_step
        self._write_debug_log(f"[引导] 推进到步骤 {next_step}")
        # 重置子步骤提示
        self._guide_deploy_sub_hint = ""
        self._guide_model_sub_hint = ""
        if next_step >= 4:
            self._guide_complete()
            return
        self._update_guide_banner()
        # 全自动模式下自动执行下一步
        if self._guide_auto:
            self._execute_guide_step()

    def _guide_on_error(self):
        """引导步骤出错，关闭全自动开关"""
        self._guide_auto = False
        if hasattr(self, '_guide_auto_switch') and self._guide_auto_switch:
            self._guide_auto_switch.setChecked(False)
        self._update_guide_banner()
        self._write_debug_log("[引导] 步骤出错，已切换到半自动模式")

    def _guide_complete(self):
        """引导完成"""
        self._guide_step = 4
        self._guide_active = False
        self.config.set("guide_completed", True)
        self._write_debug_log("[引导] 引导完成！")
        # 停止浏览器检测定时器
        if self._guide_browser_check_timer:
            self._guide_browser_check_timer.stop()
            self._guide_browser_check_timer = None
        # 停止后台模型轮询定时器
        if self._guide_bg_poll_timer:
            self._guide_bg_poll_timer.stop()
            self._guide_bg_poll_timer = None
        # 延迟隐藏横幅
        QTimer.singleShot(2000, self._hide_guide_banner)

    def _hide_guide_banner(self):
        """隐藏引导横幅"""
        if self._guide_banner:
            self._guide_banner.setVisible(False)

    def _close_guide_banner(self):
        """关闭引导横幅（用户手动关闭）"""
        self._guide_active = False
        if self._guide_banner:
            self._guide_banner.setVisible(False)
        # 停止浏览器检测定时器
        if self._guide_browser_check_timer:
            self._guide_browser_check_timer.stop()
            self._guide_browser_check_timer = None
        # 停止后台模型轮询定时器
        if self._guide_bg_poll_timer:
            self._guide_bg_poll_timer.stop()
            self._guide_bg_poll_timer = None

    def _start_bg_model_poll(self):
        """启动轮询检测huggingface_hub是否可用，可用则后台启动模型下载"""
        if self._guide_bg_poll_timer:
            self._guide_bg_poll_timer.stop()
        self._guide_bg_poll_timer = QTimer(self)
        self._guide_bg_poll_timer.timeout.connect(self._check_bg_model_ready)
        self._guide_bg_poll_timer.start(3000)  # 每3秒检查一次

    def _check_bg_model_ready(self):
        """轮询检查huggingface_hub是否可导入，可用则启动模型下载"""
        if not self._guide_active or self._guide_step != 1:
            if self._guide_bg_poll_timer:
                self._guide_bg_poll_timer.stop()
            return
        if self._guide_bg_models_started:
            if self._guide_bg_poll_timer:
                self._guide_bg_poll_timer.stop()
            return
        # 检查huggingface_hub是否可导入
        python_exe = self._python_exe
        if not python_exe or not os.path.exists(python_exe):
            return
        try:
            result = subprocess.run(
                [python_exe, "-c", "from huggingface_hub import hf_hub_download; print('ok')"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and "ok" in (result.stdout or ""):
                self._guide_bg_models_started = True
                if self._guide_bg_poll_timer:
                    self._guide_bg_poll_timer.stop()
                self._write_debug_log("[引导] huggingface_hub已就绪，后台启动必需模型下载")
                self._guide_model_sub_hint = "后台下载模型中（部署维护优先）…"
                self._update_guide_banner()
                # 延迟2秒启动，给部署维护留出带宽
                QTimer.singleShot(2000, self._download_required_models)
        except Exception:
            pass  # 还没装好，继续轮询

    def _start_guide_browser_check(self):
        """启动浏览器检测定时器（步骤3使用）"""
        if self._guide_browser_check_timer:
            self._guide_browser_check_timer.stop()
        self._guide_browser_check_count = 0
        self._guide_browser_check_timer = QTimer(self)
        self._guide_browser_check_timer.timeout.connect(self._poll_guide_browser)
        self._guide_browser_check_timer.start(2000)

    def _poll_guide_browser(self):
        """轮询检测前端端口是否有浏览器连接"""
        if not self._guide_active or self._guide_step != 3:
            if self._guide_browser_check_timer:
                self._guide_browser_check_timer.stop()
            return
        self._guide_browser_check_count += 1
        # 超过60次（2分钟）停止检测
        if self._guide_browser_check_count > 60:
            if self._guide_browser_check_timer:
                self._guide_browser_check_timer.stop()
            return
        # 检测前端端口是否可连接
        try:
            import threading
            result = [False]
            def _check():
                try:
                    conn = socket.create_connection(('127.0.0.1', self._frontend_port), timeout=1)
                    conn.close()
                    result[0] = True
                except Exception:
                    pass
            t = threading.Thread(target=_check, daemon=True)
            t.start()
            t.join(timeout=2)
            if result[0]:
                self._write_debug_log("[引导] 检测到前端页面已可访问，引导完成")
                self._guide_advance(4)
        except Exception:
            pass

    def _check_required_models_ok(self):
        """检查所有必需模型是否已下载完成"""
        models_dir = self._models_dir or os.path.join(self._data_dir or "", "models")
        for model_id, info in LTX_MODELS.items():
            if not info.get("required", False):
                continue
            target_path = os.path.join(models_dir, info["file"])
            expected_bytes = info["size_bytes"]
            if info.get("is_folder", False):
                if os.path.exists(target_path) and os.path.isdir(target_path):
                    folder_size = sum(f.stat().st_size for f in Path(target_path).rglob("*") if f.is_file())
                    if folder_size > expected_bytes * 0.5:
                        self._write_debug_log(f"[引导] 必需模型 {info['file']}: 已下载 ({folder_size}/{expected_bytes} bytes)")
                        continue
                self._write_debug_log(f"[引导] 必需模型 {info['file']}: 未下载或 incomplete")
                return False
            else:
                if os.path.exists(target_path) and os.path.getsize(target_path) > expected_bytes * 0.9:
                    actual_size = os.path.getsize(target_path)
                    self._write_debug_log(f"[引导] 必需模型 {info['file']}: 已下载 ({actual_size}/{expected_bytes} bytes)")
                    continue
                actual_size = os.path.getsize(target_path) if os.path.exists(target_path) else 0
                self._write_debug_log(f"[引导] 必需模型 {info['file']}: 未下载或 incomplete ({actual_size}/{expected_bytes} bytes)")
                return False
        return True

    def _get_missing_required_models(self):
        """获取未下载的必需模型名称列表"""
        models_dir = self._models_dir or os.path.join(self._data_dir or "", "models")
        missing = []
        for model_id, info in LTX_MODELS.items():
            if not info.get("required", False):
                continue
            target_path = os.path.join(models_dir, info["file"])
            expected_bytes = info["size_bytes"]
            is_complete = False
            if info.get("is_folder", False):
                if os.path.exists(target_path) and os.path.isdir(target_path):
                    folder_size = sum(f.stat().st_size for f in Path(target_path).rglob("*") if f.is_file())
                    is_complete = folder_size > expected_bytes * 0.5
            else:
                is_complete = os.path.exists(target_path) and os.path.getsize(target_path) > expected_bytes * 0.9
            if not is_complete:
                missing.append(info.get("desc", info["file"]))
        return missing

    def _download_required_models(self):
        """下载所有未完成的必需模型"""
        models_dir = self._models_dir or os.path.join(self._data_dir or "", "models")
        for model_id, info in LTX_MODELS.items():
            if not info.get("required", False):
                continue
            target_path = os.path.join(models_dir, info["file"])
            expected_bytes = info["size_bytes"]
            is_complete = False
            if info.get("is_folder", False):
                if os.path.exists(target_path) and os.path.isdir(target_path):
                    folder_size = sum(f.stat().st_size for f in Path(target_path).rglob("*") if f.is_file())
                    is_complete = folder_size > expected_bytes * 0.5
            else:
                is_complete = os.path.exists(target_path) and os.path.getsize(target_path) > expected_bytes * 0.9
            if not is_complete:
                self._download_model(model_id)

    def _one_click_deploy(self, skip_models=False):
        self.btn_one_click_deploy.setEnabled(False)
        self._guide_skip_models = skip_models  # 保存skip_models供部署worker使用
        selected = "auto"
        if hasattr(self, 'deploy_source_combo'):
            selected = self.deploy_source_combo.currentData()

        if selected == "auto":
            self._speed_results = {}
            self._speed_gh_ok = {}
            self._speed_phase = "ping"
            self._speed_queue = list(MIRROR_SOURCES.keys())
            self._speed_after_deploy = True
            self._speed_deploy_source = None
            self.speed_result_label.setText("• 测速中...")
            self.speed_result_label.setStyleSheet("font-size: 10px; color: #FFA726; background: transparent;")
            self._ping_next()
        else:
            self._speed_results = {}
            self._speed_gh_ok = {}
            self._speed_phase = "ping"
            self._speed_queue = list(MIRROR_SOURCES.keys())
            self._speed_after_deploy = True
            self._speed_deploy_source = selected
            self.speed_result_label.setText("• 测速中...")
            self.speed_result_label.setStyleSheet("font-size: 10px; color: #FFA726; background: transparent;")
            self._ping_next()

    def _start_deploy_worker(self, source_key):
        self.progress_container.setVisible(True)
        self.deploy_progress_bar.setValue(0)
        self.deploy_progress_bar.setStyleSheet("""
            QProgressBar { background-color: rgba(26,26,26,180); border: 1px solid rgba(33,150,243,80); border-radius: 5px; }
            QProgressBar::chunk { background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1565C0,stop:1 #42A5F5); border-radius: 4px; }
        """)
        self.deploy_progress_label.setText("↻ 正在部署维护...")
        self.btn_deploy_pause.setVisible(True)
        self.btn_deploy_cancel.setVisible(True)
        self.btn_deploy_pause.setText("⏸ 暂停")
        self._deploy_worker = DeployWorker(self._app_resources, self, mirror_source=source_key, data_dir=self._exe_data_dir, speed_cache=self._load_speed_cache(), temp_dir=self._exe_temp_dir or os.path.join(self._project_root, "temp"), skip_models=getattr(self, '_guide_skip_models', False))
        self._deploy_worker.progress.connect(self._on_deploy_progress)
        self._deploy_worker.log.connect(self._log)
        self._deploy_worker.log.connect(self._append_deploy_log)
        self._deploy_worker.log_replace.connect(self._replace_deploy_log)
        self._deploy_worker.finished.connect(self._on_deploy_finished)
        self._deploy_worker.env_update.connect(self._on_env_update)
        self._deploy_worker.start()

    def _start_deploy_worker_with_speed(self, source_key):
        self.progress_container.setVisible(True)
        self.deploy_progress_bar.setValue(0)
        self.deploy_progress_bar.setStyleSheet("""
            QProgressBar { background-color: rgba(26,26,26,180); border: 1px solid rgba(33,150,243,80); border-radius: 5px; }
            QProgressBar::chunk { background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1565C0,stop:1 #42A5F5); border-radius: 4px; }
        """)
        self.deploy_progress_label.setText("↻ 正在测速选择最优源...")
        self.btn_deploy_pause.setVisible(True)
        self.btn_deploy_cancel.setVisible(True)
        self.btn_deploy_pause.setText("⏸ 暂停")
        uv_urls = self._build_uv_urls(source_key)
        ltx_urls = self._build_ltx_urls(source_key)
        self._deploy_worker = DeployWorker(
            self._app_resources, self, mirror_source=source_key,
            uv_urls=uv_urls, ltx_urls=ltx_urls, data_dir=self._exe_data_dir,
            speed_cache=self._load_speed_cache(),
            temp_dir=self._exe_temp_dir or os.path.join(self._project_root, "temp"),
            skip_models=getattr(self, '_guide_skip_models', False)
        )
        self._deploy_worker.progress.connect(self._on_deploy_progress)
        self._deploy_worker.log.connect(self._log)
        self._deploy_worker.log.connect(self._append_deploy_log)
        self._deploy_worker.log_replace.connect(self._replace_deploy_log)
        self._deploy_worker.finished.connect(self._on_deploy_finished)
        self._deploy_worker.env_update.connect(self._on_env_update)
        self._deploy_worker.start()

    def _on_deploy_progress(self, pct, msg):
        self.deploy_progress_bar.setValue(int(pct))
        self.deploy_progress_label.setText(msg)
        # 更新引导横幅的部署子步骤提示
        if self._guide_active and self._guide_step == 1 and msg:
            # 提取关键子步骤信息（如 "↻ Python 环境 (15%)" → "Python 环境 15%"）
            clean_msg = msg.replace("↻ ", "").replace("↻", "").strip()
            self._guide_deploy_sub_hint = clean_msg
            self._update_guide_banner()

    def _append_deploy_log(self, msg, level):
        color_map = {"ok": "#66BB6A", "err": "#FF0000", "warn": "#FFA726", "info": "#CCCCCC"}
        color = color_map.get(level, "#CCCCCC")
        self.deploy_log_text.append(f'<span style="color:{color}">{msg}</span>')
        self._write_debug_log(msg, level)

    def _replace_deploy_log(self, msg, level):
        """替换日志最后一行（用于下载进度条实时更新）"""
        color_map = {"ok": "#66BB6A", "err": "#FF0000", "warn": "#FFA726", "info": "#CCCCCC"}
        color = color_map.get(level, "#CCCCCC")
        doc = self.deploy_log_text.document()
        cursor = self.deploy_log_text.textCursor()
        cursor.movePosition(cursor.End)
        cursor.movePosition(cursor.StartOfBlock, cursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertHtml(f'<span style="color:{color}">{msg}</span>')
        self.deploy_log_text.setTextCursor(cursor)
        self._write_debug_log(msg, level)

    def _on_deploy_finished(self, success, msg):
        self.btn_one_click_deploy.setEnabled(True)
        self.btn_deploy_pause.setVisible(False)
        self.btn_deploy_cancel.setVisible(False)
        self._guide_skip_models = False  # 重置skip_models标记
        if success:
            self.deploy_progress_bar.setValue(100)
            self.deploy_progress_bar.setStyleSheet("""
                QProgressBar { background-color: rgba(26,26,26,180); border: 1px solid rgba(76,175,80,80); border-radius: 5px; }
                QProgressBar::chunk { background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #2E7D32,stop:1 #66BB6A); border-radius: 4px; }
            """)
            self.deploy_progress_label.setText("√ 部署完成")
            self._log("√ 部署维护完成！所有环境已就绪", "ok")
            # 引导步骤1部署成功，检查模型下载状态决定跳转步骤
            if self._guide_active and self._guide_step == 1:
                if self._check_required_models_ok():
                    # 必需模型已全部就绪，直接跳到步骤3
                    self._write_debug_log("[引导] 部署完成且必需模型已就绪，跳到步骤3")
                    self._guide_advance(3)
                else:
                    # 必需模型仍在下载或未完成，跳到步骤2显示进度
                    self._guide_advance(2)
        else:
            self.deploy_progress_bar.setStyleSheet("""
                QProgressBar { background-color: rgba(26,26,26,180); border: 1px solid rgba(239,83,80,80); border-radius: 5px; }
                QProgressBar::chunk { background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #CC0000,stop:1 #FF0000); border-radius: 4px; }
            """)
            self.deploy_progress_label.setText(f"× 部署失败")
            self._log(f"× 部署失败: {msg}", "err")
            # 引导步骤1部署失败，关闭全自动开关
            if self._guide_active and self._guide_step == 1:
                self._guide_on_error()
        self._detect_paths_only()
        if self._python_exe and os.path.exists(self._python_exe):
            self._start_runtime_detect()

    def _toggle_deploy_pause(self):
        if not hasattr(self, '_deploy_worker') or not self._deploy_worker:
            return
        if self._deploy_worker.isRunning():
            if self.btn_deploy_pause.text().startswith("⏸"):
                self._deploy_worker.pause()
                self.btn_deploy_pause.setText("▶ 继续")
                self.deploy_progress_label.setText("⏸ 已暂停")
            else:
                self._deploy_worker.resume()
                self.btn_deploy_pause.setText("⏸ 暂停")

    def _cancel_deploy(self):
        if not hasattr(self, '_deploy_worker') or not self._deploy_worker:
            return
        self._deploy_worker.cancel()
        self._deploy_worker.wait(3000)
        self.btn_deploy_pause.setVisible(False)
        self.btn_deploy_cancel.setVisible(False)
        self.btn_one_click_deploy.setEnabled(True)
        self.deploy_progress_bar.setValue(0)
        self.deploy_progress_label.setText("× 已取消")

    def _browse_models_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择模型目录")
        if dir_path:
            self._models_dir = dir_path
            self._set_env_widget("models", f"√ {dir_path}", "ok")
            settings_path = os.path.join(self._data_dir, "settings.json")
            settings = {}
            if os.path.exists(settings_path):
                try:
                    with open(settings_path, 'r', encoding='utf-8') as f:
                        settings = json.load(f)
                except:
                    pass
            settings["models_dir"] = dir_path
            try:
                with open(settings_path, 'w', encoding='utf-8') as f:
                    json.dump(settings, f, ensure_ascii=False, indent=2)
                self._log(f"√ 模型目录已更新: {dir_path}", "ok")
            except Exception as e:
                self._log(f"× 保存模型目录失败: {e}", "err")

    def _get_output_settings_path(self):
        return os.path.join(self._data_dir, "custom_dir.txt")

    def _load_output_dir_setting(self):
        path = self._get_output_settings_path()
        if path and os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    saved = f.read().strip()
                if saved and os.path.isdir(saved):
                    self._output_dir_edit.setText(saved)
            except Exception:
                pass
        self._update_output_dir_hint()

    def _update_output_dir_hint(self):
        actual = self._resolve_actual_output_dir()
        if hasattr(self, '_output_dir_hint') and self._output_dir_hint:
            self._output_dir_hint.setText(f"当前实际路径：{actual}" if actual else "")

    def _resolve_actual_output_dir(self):
        custom = self._output_dir_edit.text().strip() if hasattr(self, '_output_dir_edit') else ""
        if custom and os.path.isdir(custom):
            return custom
        user_outputs = getattr(self, '_user_outputs_dir', None)
        if user_outputs:
            return user_outputs
        data_dir = getattr(self, '_data_dir', None)
        if data_dir:
            custom_file = os.path.join(data_dir, "custom_dir.txt")
            if os.path.exists(custom_file):
                try:
                    with open(custom_file, 'r', encoding='utf-8') as f:
                        saved = f.read().strip()
                    if saved:
                        return saved
                except Exception:
                    pass
            return os.path.join(data_dir, "outputs")
        exe_dir = getattr(self, '_exe_dir', None) or os.path.dirname(os.path.abspath(sys.argv[0]))
        return os.path.join(exe_dir, "data", "outputs")

    def _browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if dir_path:
            self._output_dir_edit.setText(dir_path)
            self._update_output_dir_hint()

    def _save_output_dir_setting(self):
        dir_path = self._output_dir_edit.text().strip()
        path = self._get_output_settings_path()
        if not path:
            self._log("× 无法保存输出目录设置：数据目录未就绪", "err")
            return
        try:
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(dir_path)
            if dir_path:
                self._log(f"√ 输出目录已设置: {dir_path}", "ok")
            else:
                self._log("√ 输出目录已恢复默认", "ok")
            self._update_output_dir_hint()
            self._notify_backend_output_dir(dir_path)
        except Exception as e:
            self._log(f"× 保存输出目录失败: {e}", "err")

    def _open_output_dir(self):
        output_dir = self._resolve_actual_output_dir()
        os.makedirs(output_dir, exist_ok=True)
        try:
            os.startfile(output_dir)
        except Exception:
            self._log(f"× 无法打开目录: {output_dir}", "err")

    def _notify_backend_output_dir(self, dir_path):
        try:
            import urllib.request
            import json
            port = self._backend_port
            if not port:
                return
            url = f"http://127.0.0.1:{port}/api/system/set-dir"
            data = json.dumps({"directory": dir_path}).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            resp = urllib.request.urlopen(req, timeout=3)
            result = resp.read().decode("utf-8")
            self._log(f"√ 后端输出目录已同步更新", "ok")
        except Exception:
            pass

    def _quit_app(self):
        self._save_window_state()
        self.tray.hide()
        QApplication.quit()

    def _save_window_state(self):
        try:
            self.config.set("window.geometry", self.saveGeometry().toBase64().data().decode())
        except Exception:
            pass

    def closeEvent(self, event):
        self._cancel_race_procs()
        self._cancel_commits_race_procs()
        for proc_attr in ('_ping_proc', '_gh_proc'):
            proc = getattr(self, proc_attr, None)
            if proc:
                try:
                    proc.finished.disconnect()
                except Exception:
                    pass
                try:
                    proc.errorOccurred.disconnect()
                except Exception:
                    pass
                try:
                    proc.terminate()
                except Exception:
                    pass
                proc.deleteLater()
                setattr(self, proc_attr, None)
        # 关闭时保留前后端服务进程，只停止其他子进程
        self._stop_non_service_procs()
        try:
            self.monitor.stop()
        except Exception:
            pass
        if hasattr(self, '_probe_timer') and self._probe_timer.isActive():
            self._probe_timer.stop()
        if _DBG:
            _DBG.shutdown()
        if self._debug_log_file:
            try:
                self._debug_log_file.close()
            except Exception:
                pass
            self._debug_log_file = None
        _cleanup_single_instance()
        event.accept()
        self._quit_app()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.showMinimized()
        else:
            super().keyPressEvent(event)


def main():
    # 处理自部署清理：删除旧的源EXE
    cleanup_target = None
    for arg in sys.argv[1:]:
        if arg.startswith("--cleanup="):
            cleanup_target = arg[len("--cleanup="):]
            sys.argv.remove(arg)
            break

    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)
        except Exception:
            pass

    import faulthandler
    log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "temp", "crash.log")
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        crash_f = open(log_file, "w")
        faulthandler.enable(crash_f)
        faulthandler._crash_file = crash_f
    except Exception:
        faulthandler.enable()
        crash_f = None

    def _crash_hook(exc_type, exc_value, exc_tb):
        import traceback
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            if crash_f and not crash_f.closed:
                crash_f.write(f"Uncaught exception at {time.strftime('%Y-%m-%d %H:%M:%S')}:\n{tb_text}\n")
                crash_f.flush()
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _crash_hook
    if _IS_FROZEN:
        _validate_exe_filename()

        install_root = _find_install_root()
        exe_dir = os.path.abspath(os.path.dirname(sys.executable))

        # 优先检查自部署目录是否已存在，避免资源释放到外面
        deploy_dir = os.path.join(exe_dir, BRAND_NAME)
        if os.path.isdir(deploy_dir) and os.path.isfile(os.path.join(deploy_dir, LOCK_FILE)):
            install_root = deploy_dir
        elif install_root is None:
            # 首次运行，尚未自部署：不在这里释放资源
            # _self_deploy() 会在 deploy_dir/app/resources 下正确释放
            install_root = exe_dir

        if install_root != exe_dir:
            exe_name = os.path.basename(sys.executable)
            target_exe = os.path.join(install_root, exe_name)
            if not os.path.exists(target_exe):
                for f in os.listdir(install_root):
                    if f.endswith(".exe") and "云集智能视频创意站" in f:
                        target_exe = os.path.join(install_root, f)
                        break
            if os.path.exists(target_exe) and os.path.abspath(target_exe) != os.path.abspath(sys.executable):
                subprocess.Popen(
                    f'ping -n 2 127.0.0.1 >nul & start "" "{target_exe}"',
                    shell=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                sys.exit(0)
            elif not os.path.exists(target_exe):
                try:
                    shutil.copy2(sys.executable, target_exe)
                    subprocess.Popen(
                        f'ping -n 2 127.0.0.1 >nul & start "" "{target_exe}"',
                        shell=True,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    sys.exit(0)
                except Exception:
                    pass

        app_resources = os.path.join(install_root, "app", "resources")
        # 首次运行（尚未自部署）时跳过资源释放，由 _self_deploy() 处理
        _already_deployed = os.path.isfile(os.path.join(install_root, LOCK_FILE))
        if not _already_deployed and install_root == exe_dir:
            pass  # _self_deploy() 会在 deploy_dir 下正确释放资源
        elif not os.path.isdir(app_resources) or not os.path.isdir(os.path.join(app_resources, "backend")):
            # 优先从EXE内部释放嵌入资源，无需网络下载
            meipass = getattr(sys, '_MEIPASS', '')
            if meipass:
                try:
                    os.makedirs(app_resources, exist_ok=True)
                    _IGNORE_PATTERNS = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
                    for res_name in ("ui", "backend", "patches"):
                        src = os.path.join(meipass, "resources", res_name)
                        dst = os.path.join(app_resources, res_name)
                        if os.path.isdir(src):
                            if os.path.exists(dst):
                                shutil.rmtree(dst, ignore_errors=True)
                            shutil.copytree(src, dst, ignore=_IGNORE_PATTERNS)
                except Exception as e:
                    print(f"[main] 从EXE释放资源失败: {e}")

            # 如果EXE内部释放后仍缺少资源，再尝试网络下载
            if not os.path.isdir(os.path.join(app_resources, "backend")):
                QApplication.setHighDpiScaleFactorRoundingPolicy(
                    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
                )
                _app = QApplication(sys.argv)
                _app.setStyle('Fusion')
                _dlg = QDialog()
                _dlg.setWindowTitle("云集智能视频创意站 - 首次启动")
                _dlg.setFixedSize(480, 280)
                _dlg.setStyleSheet("QDialog { background-color: #1A1A1A; }")
                _dlg.setWindowFlags(_dlg.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
                _layout = QVBoxLayout(_dlg)
                _layout.setContentsMargins(30, 25, 30, 20)
                _layout.setSpacing(12)
                _title = QLabel("🚀 首次启动，正在准备核心文件...")
                _title.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
                _title.setStyleSheet("color: #FFFFFF; border: none;")
                _title.setAlignment(Qt.AlignmentFlag.AlignCenter)
                _layout.addWidget(_title)
                _progress = QProgressBar()
                _progress.setRange(0, 0)
                _progress.setFixedHeight(20)
                _progress.setStyleSheet(
                    "QProgressBar { background-color: #2A2A2A; border: 1px solid #444; border-radius: 10px; text-align: center; color: #AAA; }"
                    "QProgressBar::chunk { background-color: #4CAF50; border-radius: 9px; }"
                )
                _layout.addWidget(_progress)
                _status = QLabel("正在从远程仓库下载核心文件...")
                _status.setStyleSheet("color: #AAAAAA; font-size: 10pt; border: none;")
                _status.setAlignment(Qt.AlignmentFlag.AlignCenter)
                _layout.addWidget(_status)
                _detail = QLabel("将同时尝试 GitHub 和 Gitee，使用最快的源")
                _detail.setStyleSheet("color: #666666; font-size: 9pt; border: none;")
                _detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
                _layout.addWidget(_detail)
                _layout.addStretch()
                _dlg.show()
                _app.processEvents()

                bootstrap_result = [None]

                def do_bootstrap():
                    ok, msg = _bootstrap_download_resources(install_root)
                    bootstrap_result[0] = (ok, msg)

                bt = threading.Thread(target=do_bootstrap, daemon=True)
                bt.start()

                while bt.is_alive():
                    _app.processEvents()
                    bt.join(timeout=0.05)

                _dlg.close()

                ok, msg = bootstrap_result[0]
                if not ok:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        f"核心文件下载失败：\n{msg}\n\n"
                        "请检查网络连接后重试。\n"
                        "也可以手动从以下地址下载：\n"
                        "https://github.com/yunjii-cn/vi/releases",
                        "云集智能视频创意站 - 启动失败",
                        0x10
                    )
                    sys.exit(1)

                app_resources = os.path.join(install_root, "app", "resources")
                if not os.path.isdir(os.path.join(app_resources, "backend")):
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        "核心文件下载后验证失败，请重新启动程序重试。",
                        "云集智能视频创意站 - 启动失败",
                        0x10
                    )
                    sys.exit(1)

    try:
        import ctypes
        app_id = "YunJi.VideoCreativeStation"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    class SafeQApplication(QApplication):
        def notify(self, receiver, event):
            try:
                return super().notify(receiver, event)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(f"[SafeQApplication] Unhandled exception in Qt event loop:\n{tb}")
                try:
                    crash_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "temp", "logs")
                    os.makedirs(crash_dir, exist_ok=True)
                    with open(os.path.join(crash_dir, "qt_exception.log"), "a", encoding="utf-8") as f:
                        f.write(f"\n--- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n{tb}\n")
                except Exception:
                    pass
                return False

    app = SafeQApplication(sys.argv)
    app.setStyle('Fusion')

    try:
        if hasattr(sys, '_MEIPASS'):
            icon_path = os.path.join(sys._MEIPASS, 'ico.png')
        else:
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ico.png')
        if not os.path.exists(icon_path):
            if hasattr(sys, '_MEIPASS'):
                icon_path = os.path.join(sys._MEIPASS, 'icon.ico')
            else:
                icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')
        if os.path.exists(icon_path):
            app.setWindowIcon(QIcon(icon_path))
    except Exception:
        pass

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#0d0d0d"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f0f0f0"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#0d0d0d"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f0f0f0"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#1a1a1a"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f0f0f0"))
    app.setPalette(palette)

    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)
    app.setStyleSheet(GLOBAL_STYLE)

    splash = SplashScreen()
    screen = app.primaryScreen().geometry()
    x = (screen.width() - splash.width()) // 2
    y = (screen.height() - splash.height()) // 2
    splash.move(x, y)
    splash.show()
    splash.repaint()
    app.processEvents()

    try:
        if os.environ.get('_PYI_SPLASH_IPC'):
            import pyi_splash as _pyi_splash
            _pyi_splash.close()
            del _pyi_splash
    except Exception:
        pass

    splash.set_progress(0.1, "正在创建主窗口...")
    app.processEvents()

    print("[DEBUG] Creating MainWindow...")
    window = MainWindow(splash)
    print("[DEBUG] MainWindow created, resizing...")
    window.resize(1100, 800)
    print("[DEBUG] Window resized, starting event loop...")

    # 自部署清理：延迟删除旧的源EXE文件
    if cleanup_target and os.path.isfile(cleanup_target):
        def _do_cleanup():
            try:
                os.remove(cleanup_target)
                print(f"[cleanup] 已删除旧EXE: {cleanup_target}")
            except Exception as e:
                print(f"[cleanup] 删除旧EXE失败(可能仍被占用): {e}")
        QTimer.singleShot(3000, _do_cleanup)

    global _MAIN_WINDOW_REF
    _MAIN_WINDOW_REF = window

    try:
        app.processEvents()
    except Exception:
        pass
    time.sleep(0.2)

    try:
        exit_code = app.exec()
    except Exception:
        exit_code = 1
    sys.exit(exit_code)


if __name__ == "__main__":
    _seh_crash_code = -1073741819
    _max_retries = 2
    for _attempt in range(_max_retries + 1):
        try:
            main()
        except SystemExit as e:
            if e.code == _seh_crash_code and _attempt < _max_retries:
                print(f"[MAIN] 0xC0000005 崩溃，第{_attempt + 1}次重试...")
                time.sleep(1)
                continue
            raise
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            try:
                if _IS_FROZEN:
                    crash_dir = os.path.join(_EXE_DIR, "temp", "logs")
                else:
                    crash_dir = os.path.join(os.path.dirname(_EXE_DIR), "temp", "logs")
                os.makedirs(crash_dir, exist_ok=True)
                crash_path = os.path.join(crash_dir, "crash.log")
                with open(crash_path, "w", encoding="utf-8") as f:
                    f.write(f"Crash at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(err)
            except Exception:
                pass
            if _DEBUG_MODE:
                try:
                    print(f"[MAIN] 未捕获异常:\n{err}")
                except Exception:
                    pass
            raise



