#!/usr/bin/env python3
"""
映射启动器构建脚本
生成一个永久无需更新的轻量级启动器EXE
"""
import os
import sys
import subprocess
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LAUNCHER_SRC = ROOT / "launcher.py"
ICON_PATH = ROOT / "icon.ico"
OUTPUT_DIR = ROOT / "dist"

APP_NAME = "云集智能视频创意站"


def build():
    if not LAUNCHER_SRC.exists():
        print(f"错误: 找不到 {LAUNCHER_SRC}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    icon_args = []
    if ICON_PATH.exists():
        icon_args = ["--icon", str(ICON_PATH)]
    else:
        parent_icon = ROOT.parent / "dev" / "app" / "icon.ico"
        if parent_icon.exists():
            icon_args = ["--icon", str(parent_icon)]

    add_data_args = []
    for icon_name in ("icon.ico", "icon.png"):
        src = ROOT / icon_name
        if not src.exists():
            src = ROOT.parent / "dev" / "app" / icon_name
        if src.exists():
            add_data_args += ["--add-data", f"{src};."]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onefile", "--windowed",
        "--distpath", str(OUTPUT_DIR),
        "--workpath", str(OUTPUT_DIR / "_work"),
        "--specpath", str(OUTPUT_DIR / "_work"),
        "--clean", "--noconfirm",
        "--hidden-import", "PyQt6",
        "--hidden-import", "PyQt6.QtCore",
        "--hidden-import", "PyQt6.QtGui",
        "--hidden-import", "PyQt6.QtWidgets",
        "--exclude-module", "matplotlib",
        "--exclude-module", "scipy",
        "--exclude-module", "tkinter",
        "--exclude-module", "tensorflow",
        "--exclude-module", "torch",
        "--exclude-module", "transformers",
        "--exclude-module", "diffusers",
        "--exclude-module", "safetensors",
        "--exclude-module", "numpy",
        "--exclude-module", "PIL",
    ] + icon_args + add_data_args + [str(LAUNCHER_SRC)]

    print("=" * 60)
    print(f"  {APP_NAME} - 映射启动器构建")
    print("=" * 60)
    print()
    print(f"  源码: {LAUNCHER_SRC}")
    print(f"  输出: {OUTPUT_DIR}")
    print(f"  模式: --onefile (单文件)")
    print()

    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print("构建失败！")
        sys.exit(1)

    exe_path = OUTPUT_DIR / f"{APP_NAME}.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print()
        print("=" * 60)
        print("  构建成功！")
        print(f"  EXE: {exe_path}")
        print(f"  大小: {size_mb:.1f} MB")
        print()
        print("  使用方法：")
        print("  1. 将 EXE 放到目标目录")
        print("  2. 双击运行，自动下载核心文件和主程序")
        print("  3. 后续启动自动检查更新并启动最新版")
        print("=" * 60)
    else:
        print("构建完成但未找到EXE文件")
        sys.exit(1)

    work_dir = OUTPUT_DIR / "_work"
    if work_dir.exists():
        shutil.rmtree(str(work_dir), ignore_errors=True)


if __name__ == "__main__":
    build()
