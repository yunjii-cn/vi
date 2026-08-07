#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最简单的构建脚本 - 确保完美工作
"""
import os
import sys
import subprocess
import shutil
from pathlib import Path
from datetime import datetime

VERSION = datetime.now().strftime("%Y.%m.%d.%H%M")
ROOT_DIR = Path(__file__).resolve().parent
BUILD_DIR = ROOT_DIR.parent.parent / "build"
DEV_DIR = ROOT_DIR.parent
APP_NAME = "云集智能视频创意站"

def main():
    print("=" * 60)
    print(f"  {APP_NAME} - 简化版构建工具")
    print("=" * 60)
    print()
    
    release_name = f"{APP_NAME}-v{VERSION}"
    release_dir = BUILD_DIR / release_name
    
    if release_dir.exists():
        shutil.rmtree(release_dir, ignore_errors=True)
    
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    
    icon_path = ROOT_DIR / "icon.ico"
    
    pyinstaller_args = [
        sys.executable, "-m", "PyInstaller",
        "--name", release_name,
        "--onedir",
        "--windowed",
        f"--icon={icon_path}",
        f"--distpath={BUILD_DIR}",
        f"--workpath={BUILD_DIR}/_pyinst_work",
        f"--specpath={BUILD_DIR}/_pyinst_work",
        "--clean",
        "--noconfirm",
        "--collect-all", "PyQt6",
        "--hidden-import", "psutil",
        "--hidden-import", "debug_hub",
        "--exclude-module", "matplotlib",
        "--exclude-module", "scipy",
        "--exclude-module", "tkinter",
        "--exclude-module", "tensorflow",
        "--exclude-module", "torch",
    ]
    
    if icon_path.exists():
        pyinstaller_args.extend([f"--add-data={icon_path};."])
    
    icon_png = ROOT_DIR / "icon.png"
    if icon_png.exists():
        pyinstaller_args.extend([f"--add-data={icon_png};."])
    
    ico_png = ROOT_DIR / "ico.png"
    if ico_png.exists():
        pyinstaller_args.extend([f"--add-data={ico_png};."])
    
    pyinstaller_args.append("main.py")
    
    print("  运行 PyInstaller...")
    os.chdir(str(ROOT_DIR))
    subprocess.run(pyinstaller_args, check=True)
    
    print()
    print("  复制资源...")
    _IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")
    
    # 复制 resources
    patches_src = ROOT_DIR / "resources" / "patches"
    patches_dst = release_dir / "app" / "resources" / "patches"
    if patches_src.exists():
        shutil.copytree(str(patches_src), str(patches_dst), ignore=_IGNORE)
        print("    ✓ patches")
    
    backend_src = ROOT_DIR / "resources" / "backend"
    backend_dst = release_dir / "app" / "resources" / "backend"
    if backend_src.exists():
        shutil.copytree(str(backend_src), str(backend_dst), ignore=_IGNORE)
        print("    ✓ backend")
    
    ui_src = ROOT_DIR / "resources" / "ui"
    ui_dst = release_dir / "app" / "resources" / "ui"
    if ui_src.exists():
        shutil.copytree(str(ui_src), str(ui_dst), ignore=_IGNORE)
        print("    ✓ ui")
    
    # 创建 data 和 temp
    data_dir = release_dir / "data"
    for sub in ("outputs", "uploads", "models", "config"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    
    temp_dir = release_dir / "temp"
    for sub in ("logs", "cache/thumbnails", "cache/temp", "debug"):
        (temp_dir / sub).mkdir(parents=True, exist_ok=True)
    
    print()
    print("  复制到 dev 目录...")
    exe_src = release_dir / f"{release_name}.exe"
    exe_dst = DEV_DIR / f"{release_name}.exe"
    
    if exe_dst.exists():
        try:
            exe_dst.unlink()
        except:
            pass
    
    if exe_src.exists():
        shutil.copy2(str(exe_src), str(exe_dst))
        print(f"    ✓ EXE: {exe_dst.name}")
    
    internal_src = release_dir / "_internal"
    internal_dst = DEV_DIR / "_internal"
    
    if internal_dst.exists():
        shutil.rmtree(str(internal_dst), ignore_errors=True)
    
    if internal_src.exists():
        shutil.copytree(str(internal_src), str(internal_dst))
        print("    ✓ _internal")
    
    # ⚠️ 复制 app 资源到 dev/app/resources
    # 注意：这会覆盖 dev/app/resources 目录的源代码！
    # PyInstaller 构建产物中的 resources 应与源代码一致，
    # 但建议使用 build-version.py 进行正式发布，本脚本仅用于快速调试。
    app_resources_src = release_dir / "app" / "resources"
    app_resources_dst = DEV_DIR / "app" / "resources"

    if app_resources_dst.exists():
        print("    ⚠️ 警告: 即将覆盖 dev/app/resources 源代码！")
        print("      建议使用 build-version.py 进行正式构建")
        # 只复制不存在的或更新的文件，避免覆盖源码的修改
        _copied, _skipped = 0, 0
        for src_file in app_resources_src.rglob("*"):
            if src_file.is_file():
                rel = src_file.relative_to(app_resources_src)
                dst_file = app_resources_dst / rel
                if not dst_file.exists() or src_file.stat().st_mtime > dst_file.stat().st_mtime:
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src_file), str(dst_file))
                    _copied += 1
                else:
                    _skipped += 1
        print(f"    ✓ app/resources: 复制 {_copied}, 跳过 {_skipped}")
    
    # 确保 data 和 temp 在 dev
    for dir_name in ("data", "temp"):
        dir_path = DEV_DIR / dir_name
        dir_path.mkdir(parents=True, exist_ok=True)
        if dir_name == "data":
            for sub in ("outputs", "uploads", "models", "config"):
                (dir_path / sub).mkdir(parents=True, exist_ok=True)
        else:
            for sub in ("logs", "cache/thumbnails", "cache/temp", "debug"):
                (dir_path / sub).mkdir(parents=True, exist_ok=True)
    
    print()
    print("=" * 60)
    print(f"  ✓ 构建完成！")
    print(f"  版本: v{VERSION}")
    print(f"  发布目录: {release_dir}")
    print(f"  DEV EXE: {exe_dst}")
    print("=" * 60)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print()
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
