#!/usr/bin/env python3
"""
云集智能视频创意站 - 映射启动器
永久无需更新，只负责：
1. 显示启动进度条界面（与主程序一致）
2. 初始化三目录结构（app/、data/、temp/）
3. 从远程下载核心文件（resources.zip）到 app/resources/
4. 从远程下载最新稳定版EXE到 ver/
5. 无缝衔接启动主程序
"""
import os
import sys
import json
import re
import ssl
import shutil
import subprocess
import threading
import zipfile
import tempfile
import urllib.request
import base64

from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtCore import Qt, QRectF, pyqtProperty, QPropertyAnimation
from PyQt6.QtGui import QPixmap, QColor, QPainter, QFont, QLinearGradient, QIcon

APP_NAME = "云集智能视频创意站"
APP_SUBTITLE = "LTX-2.3 Cinematic Workstation"
LAUNCHER_VERSION = "1.0.0"

GITEE_TOKEN = ""
_gitee_token_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gitee_token")
if os.path.exists(_gitee_token_path):
    try:
        with open(_gitee_token_path, "r") as _f:
            GITEE_TOKEN = _f.read().strip()
    except Exception:
        pass

SOURCES = {
    "gitee": {
        "name": "Gitee",
        "version_url": f"https://gitee.com/api/v5/repos/yunjii/vi/contents/ver/version.json?ref=main&access_token={GITEE_TOKEN}",
        "exe_url_tpl": "https://gitee.com/yunjii/vi/raw/main/ver/{filename}?access_token={GITEE_TOKEN}",
        "resources_url": f"https://gitee.com/yunjii/vi/repository/archive/main.zip?access_token={GITEE_TOKEN}",
        "is_api": True,
    },
    "github": {
        "name": "GitHub",
        "version_url": "https://raw.githubusercontent.com/yunjii-cn/vi/main/ver/version.json",
        "exe_url_tpl": "https://github.com/yunjii-cn/vi/releases/download/v{version}/{filename}",
        "resources_url": "https://github.com/yunjii-cn/vi/releases/latest/download/resources.zip",
        "is_api": False,
    },
}


def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _fetch_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
        return resp.read().decode()


def _fetch_bytes(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
        return resp.read()


def _race_fetch(fetch_fn, timeout=15):
    result = [None]
    lock = threading.Lock()

    def try_source(key):
        try:
            data = fetch_fn(key)
            with lock:
                if result[0] is None:
                    result[0] = (key, data)
        except Exception:
            pass

    threads = []
    for key in SOURCES:
        t = threading.Thread(target=try_source, args=(key,), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=timeout)

    return result[0]


def fetch_version_info():
    def do_fetch(key):
        source = SOURCES[key]
        raw = _fetch_json(source["version_url"])
        if source.get("is_api"):
            api_data = json.loads(raw)
            if isinstance(api_data, list):
                file_data = api_data[0] if api_data else {}
            else:
                file_data = api_data
            content_b64 = file_data.get("content", "")
            content_json = base64.b64decode(content_b64).decode("utf-8")
            return json.loads(content_json)
        else:
            return json.loads(raw)

    return _race_fetch(do_fetch, timeout=12)


def fetch_resources_zip():
    def do_fetch(key):
        source = SOURCES[key]
        url = source.get("resources_url", "")
        if not url:
            raise Exception("no resources_url")
        return _fetch_bytes(url, timeout=60)

    return _race_fetch(do_fetch, timeout=45)


def fetch_exe(version_info):
    latest = version_info.get("latest", "")
    filename = version_info.get("filename", "")
    if not filename:
        filename = f"云集智能视频创意站-v{latest}.exe"

    def do_fetch(key):
        source = SOURCES[key]
        url = source["exe_url_tpl"].format(filename=filename, version=latest, GITEE_TOKEN=GITEE_TOKEN)
        return _fetch_bytes(url, timeout=120)

    return _race_fetch(do_fetch, timeout=90), filename


def extract_resources(zip_data, source_key, resources_dir):
    tmp_zip = os.path.join(tempfile.gettempdir(), "_vi_launcher_resources.zip")
    try:
        with open(tmp_zip, "wb") as f:
            f.write(zip_data)

        with zipfile.ZipFile(tmp_zip, "r") as zf:
            names = zf.namelist()
            prefix = ""
            for n in names:
                if n.endswith("app/resources/"):
                    prefix = n
                    break
            if not prefix:
                for n in names:
                    m = re.search(r'^(.+?/)?app/resources/', n)
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
    finally:
        try:
            os.unlink(tmp_zip)
        except Exception:
            pass


class LauncherSplash(QSplashScreen):
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
            for name in ('icon.png', 'icon.ico'):
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
        sub = APP_SUBTITLE
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
            grad.setColorAt(0, QColor("#E53935"))
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


def find_install_root():
    if hasattr(sys, 'frozen'):
        exe_dir = os.path.abspath(os.path.dirname(sys.executable))
    else:
        exe_dir = os.path.dirname(os.path.abspath(__file__))

    for _ in range(5):
        if os.path.isdir(os.path.join(exe_dir, "app")):
            return exe_dir
        parent = os.path.dirname(exe_dir)
        if parent == exe_dir:
            break
        exe_dir = parent
    return None


def find_latest_exe(ver_dir):
    if not os.path.isdir(ver_dir):
        return None
    best = None
    best_ver = ""
    for f in os.listdir(ver_dir):
        if f.endswith(".exe") and "云集智能视频创意站" in f:
            m = re.search(r'v(\d+\.\d+\.\d+\.\d+)', f)
            ver = m.group(1) if m else "0"
            if ver > best_ver:
                best_ver = ver
                best = os.path.join(ver_dir, f)
    return best


def main():
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("YunJi.VideoCreativeStation.Launcher")
    except Exception:
        pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    try:
        if hasattr(sys, '_MEIPASS'):
            icon_path = os.path.join(sys._MEIPASS, 'icon.ico')
        else:
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')
        if os.path.exists(icon_path):
            app.setWindowIcon(QIcon(icon_path))
    except Exception:
        pass

    splash = LauncherSplash()
    screen = app.primaryScreen().geometry()
    x = (screen.width() - splash.width()) // 2
    y = (screen.height() - splash.height()) // 2
    splash.move(x, y)
    splash.show()
    splash.repaint()
    app.processEvents()

    if hasattr(sys, 'frozen'):
        install_root = os.path.abspath(os.path.dirname(sys.executable))
    else:
        install_root = os.path.dirname(os.path.abspath(__file__))

    existing_root = find_install_root()
    if existing_root:
        install_root = existing_root

    ver_dir = os.path.join(install_root, "ver")
    resources_dir = os.path.join(install_root, "app", "resources")
    data_dir = os.path.join(install_root, "data")
    temp_dir = os.path.join(install_root, "temp")

    splash.set_progress(0.05, "正在初始化目录结构...")
    app.processEvents()

    os.makedirs(ver_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)
    for sub in ("outputs", "uploads", "models", "config"):
        os.makedirs(os.path.join(data_dir, sub), exist_ok=True)
    for sub in ("logs", "cache", "debug"):
        os.makedirs(os.path.join(temp_dir, sub), exist_ok=True)

    need_resources = not os.path.isdir(resources_dir) or not os.path.isdir(os.path.join(resources_dir, "backend"))

    splash.set_progress(0.1, "正在检查远程版本信息...")
    app.processEvents()

    version_info = None
    try:
        race_result = fetch_version_info()
        if race_result:
            source_key, version_info = race_result
            source_name = SOURCES[source_key]["name"]
    except Exception:
        pass

    if need_resources:
        splash.set_progress(0.15, "正在下载核心文件...")
        app.processEvents()

        race_result = fetch_resources_zip()
        if race_result:
            source_key, zip_data = race_result
            source_name = SOURCES[source_key]["name"]
            splash.set_progress(0.4, f"正在解压核心文件（via {source_name}）...")
            app.processEvents()
            try:
                os.makedirs(resources_dir, exist_ok=True)
                extract_resources(zip_data, source_key, resources_dir)
                splash.set_progress(0.5, "核心文件安装完成")
            except Exception as e:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"核心文件安装失败：\n{e}\n\n请检查网络连接后重试。",
                    "云集智能视频创意站 - 启动失败",
                    0x10
                )
                sys.exit(1)
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "无法从任何源下载核心文件，请检查网络连接。",
                "云集智能视频创意站 - 启动失败",
                0x10
            )
            sys.exit(1)
    else:
        splash.set_progress(0.5, "核心文件已就绪")
    app.processEvents()

    latest_exe = find_latest_exe(ver_dir)
    remote_latest = ""
    remote_filename = ""

    if version_info:
        remote_latest = version_info.get("latest", "")
        remote_filename = version_info.get("filename", "")
        if not remote_filename and remote_latest:
            remote_filename = f"云集智能视频创意站-v{remote_latest}.exe"

    need_download = False
    if remote_latest and remote_filename:
        local_ver = ""
        if latest_exe:
            m = re.search(r'v(\d+\.\d+\.\d+\.\d+)', os.path.basename(latest_exe))
            local_ver = m.group(1) if m else ""
        if remote_latest > local_ver:
            need_download = True

    if need_download:
        splash.set_progress(0.55, f"正在下载 v{remote_latest}...")
        app.processEvents()

        race_result, filename = fetch_exe(version_info)
        if race_result:
            source_key, exe_data = race_result
            source_name = SOURCES[source_key]["name"]
            exe_path = os.path.join(ver_dir, filename)
            splash.set_progress(0.85, f"正在保存 v{remote_latest}（via {source_name}）...")
            app.processEvents()
            try:
                with open(exe_path, "wb") as f:
                    f.write(exe_data)
                latest_exe = exe_path
                splash.set_progress(0.9, f"v{remote_latest} 下载完成")
            except Exception as e:
                splash.set_progress(0.9, f"下载保存失败: {e}")
        else:
            splash.set_progress(0.9, "EXE下载失败，尝试使用本地版本")
    else:
        if latest_exe:
            m = re.search(r'v(\d+\.\d+\.\d+\.\d+)', os.path.basename(latest_exe))
            local_ver = m.group(1) if m else "未知"
            splash.set_progress(0.9, f"本地已是最新版本 v{local_ver}")
        else:
            splash.set_progress(0.9, "未找到本地EXE，尝试下载...")
            if version_info:
                race_result, filename = fetch_exe(version_info)
                if race_result:
                    source_key, exe_data = race_result
                    source_name = SOURCES[source_key]["name"]
                    exe_path = os.path.join(ver_dir, filename)
                    try:
                        with open(exe_path, "wb") as f:
                            f.write(exe_data)
                        latest_exe = exe_path
                        splash.set_progress(0.95, f"v{remote_latest} 下载完成")
                    except Exception:
                        pass
    app.processEvents()

    if not latest_exe or not os.path.exists(latest_exe):
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            "未找到可执行的主程序。\n\n"
            "请确保：\n"
            "1. 网络连接正常\n"
            "2. ver/ 目录中有主程序EXE\n\n"
            "也可以手动从以下地址下载：\n"
            "https://github.com/yunjii-cn/vi/releases",
            "云集智能视频创意站 - 启动失败",
            0x10
        )
        sys.exit(1)

    splash.set_progress(0.95, "正在启动主程序...")
    app.processEvents()

    import time
    time.sleep(0.3)

    splash.set_progress(1.0, "启动中...")
    app.processEvents()
    time.sleep(0.2)

    subprocess.Popen(
        f'ping -n 2 127.0.0.1 >nul & start "" "{latest_exe}"',
        shell=True,
        creationflags=subprocess.CREATE_NO_WINDOW
    )

    splash.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
