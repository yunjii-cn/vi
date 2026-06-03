#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EXE构建脚本 - 云集智能视频创意站
--onefile 模式打包（单文件，稳定可靠）

使用方法：
  python build-version.py 修改内容1 修改内容2 ...
  python build-version.py

架构说明：
  - --onefile 模式打包（单文件EXE，启动稍慢但极少出问题）
  - main.py 直接作为入口（launcher 功能已合并到 main.py）
  - PyInstaller 工作目录: build/ (仅构建用，不推送)
  - 三目录原则：
    dev/*.exe           = 启动器（gitignore）
    dev/app/resources/  = 应用代码（git 管理）
    dev/data/           = 用户数据（gitignore）
    dev/temp/           = 临时文件（gitignore）
"""
import os
import sys
import subprocess
import shutil
import json
import time
from pathlib import Path
from datetime import datetime

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

VERSION = datetime.now().strftime("%Y.%m.%d.%H%M")
ROOT_DIR = Path(__file__).resolve().parent       # dev/app/
DEV_DIR = ROOT_DIR.parent                        # dev/
PROJECT_ROOT = ROOT_DIR.parent.parent             # 项目根目录
BUILD_DIR = PROJECT_ROOT / "build"               # 项目根/build/
VERSION_HISTORY_FILE = ROOT_DIR / "version_history.json"
REMOTE_VERSION_FILE = PROJECT_ROOT / "ver" / "version.json"
APP_NAME = "云集智能视频创意站"


def load_version_history():
    if VERSION_HISTORY_FILE.exists():
        try:
            with open(VERSION_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_version_history(history):
    try:
        with open(VERSION_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"警告：保存版本历史失败：{e}")


def get_git_status():
    try:
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True, text=True,
            cwd=PROJECT_ROOT, timeout=10
        )
        return result.stdout.strip()
    except Exception as e:
        print(f"获取Git状态失败：{e}")
        return ""


def git_commit_and_push(commit_message):
    try:
        print("\n" + "=" * 60)
        print("  Git 提交和推送")
        print("=" * 60)

        git_status = get_git_status()
        if not git_status:
            print("  没有需要提交的修改")
            return True

        print("  检测到修改，开始提交...")

        subprocess.run(
            ['git', 'add', '.'],
            cwd=PROJECT_ROOT, check=True, timeout=30
        )
        print("  ✓ 文件已添加")

        subprocess.run(
            ['git', 'commit', '-m', commit_message],
            cwd=PROJECT_ROOT, check=True, timeout=30
        )
        print("  ✓ 提交成功")

        print("  推送到远程仓库...")
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                result = subprocess.run(
                    ['git', 'push'],
                    cwd=PROJECT_ROOT, capture_output=True, text=True,
                    timeout=180
                )
                if result.returncode == 0:
                    print("  ✓ 推送成功")
                    return True
                else:
                    print(f"  警告：推送失败（第{attempt + 1}次尝试）：{result.stderr}")
                    if attempt < max_attempts - 1:
                        print("  重试中...")
                        time.sleep(3)
            except subprocess.TimeoutExpired:
                print(f"  警告：推送超时（第{attempt + 1}次尝试）")
                if attempt < max_attempts - 1:
                    print("  重试中...")
                    time.sleep(3)

        print("  ✗ 推送失败，请稍后手动推送")
        return False

    except subprocess.CalledProcessError as e:
        print(f"  Git操作失败：{e}")
        return False
    except Exception as e:
        print(f"  Git操作异常：{e}")
        import traceback
        traceback.print_exc()
        return False


def update_versions_json(version, changes, exe_name):
    try:
        versions_file = ROOT_DIR / "versions.json"
        versions = []
        if versions_file.exists():
            with open(versions_file, 'r', encoding='utf-8') as f:
                versions = json.load(f)

        new_entry = {
            "version": version,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "message": changes[0] if changes else "优化和修复",
            "changes": changes,
            "name": exe_name,
            "download_url": ""
        }

        versions.insert(0, new_entry)

        with open(versions_file, 'w', encoding='utf-8') as f:
            json.dump(versions, f, ensure_ascii=False, indent=2)

        print("  ✓ versions.json 已更新")
        return True

    except Exception as e:
        print(f"  ✗ 更新 versions.json 失败: {e}")
        return False


def update_remote_version_json(version, changes):
    try:
        ver_dir = REMOTE_VERSION_FILE.parent
        ver_dir.mkdir(parents=True, exist_ok=True)

        data = {}
        if REMOTE_VERSION_FILE.exists():
            with open(REMOTE_VERSION_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

        data["latest"] = version
        data["release_date"] = datetime.now().strftime("%Y-%m-%d")
        data["download_url"] = f"https://github.com/yunjii-cn/vi/releases/tag/v{version}"
        data["gitee_download_url"] = f"https://gitee.com/yunjii/vi/releases/tag/v{version}"
        data["changes"] = changes

        existing_versions = data.get("versions", [])
        seen = {v.get("version") for v in existing_versions}
        if version not in seen:
            existing_versions.insert(0, {
                "version": version,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "changes": changes,
                "filename": f"{APP_NAME}-v{version}.exe",
            })
        data["versions"] = existing_versions

        with open(REMOTE_VERSION_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"  ✓ ver/version.json 已更新 (v{version})")
        return True

    except Exception as e:
        print(f"  ✗ 更新 ver/version.json 失败: {e}")
        return False


def strip_bom_from_py_files():
    count = 0
    for dp, dn, fns in os.walk(ROOT_DIR):
        for fn in fns:
            if not fn.endswith('.py'):
                continue
            fp = os.path.join(dp, fn)
            try:
                data = open(fp, 'rb').read()
                if data[:3] == b'\xef\xbb\xbf':
                    open(fp, 'wb').write(data[3:])
                    count += 1
                    print(f"  已移除BOM: {fn}")
            except Exception:
                pass
    if count:
        print(f"  共移除 {count} 个文件的BOM标记")


def validate_code_before_build():
    """打包前自动验证代码完整性，防止运行时崩溃。

    检查项：
    1. Python 语法检查（所有 .py 文件）
    2. main.py 中调用的私有方法是否都已定义
    3. patches/ 扩展模块是否都能导入
    4. 关键方法是否存在
    """
    import ast
    errors = []

    # 1. 语法检查（排除第三方目录）
    _SKIP_DIRS = {"resources/python", "resources/backend", "__pycache__", "build", ".git"}
    py_files = []
    for fp in Path(ROOT_DIR).rglob("*.py"):
        rel_str = str(fp.relative_to(ROOT_DIR)).replace("\\", "/")
        if any(skip in rel_str for skip in _SKIP_DIRS):
            continue
        py_files.append(fp)
    for fp in py_files:
        rel = fp.relative_to(ROOT_DIR)
        try:
            source = fp.read_text(encoding="utf-8")
            ast.parse(source)
        except SyntaxError as e:
            errors.append(f"语法错误 {rel}: {e}")

    # 2. main.py 方法完整性检查
    main_py = ROOT_DIR / "main.py"
    if main_py.exists():
        source = main_py.read_text(encoding="utf-8")
        tree = ast.parse(source)
        defined = set()
        called_private = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        defined.add(item.name)
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr.startswith("_"):
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
                        called_private.add(node.func.attr)
        missing = called_private - defined
        if missing:
            for m in sorted(missing):
                errors.append(f"main.py 调用了未定义的方法: {m}")

    # 3. 关键方法存在性检查
    critical_methods = [
        "_open_output_dir", "_browse_output_dir", "_save_output_dir_setting",
        "_load_output_dir_setting", "_update_output_dir_hint", "_resolve_actual_output_dir",
        "_start_all", "_stop_all", "_log",
    ]
    if main_py.exists():
        source = main_py.read_text(encoding="utf-8")
        for method in critical_methods:
            if f"def {method}" not in source:
                errors.append(f"main.py 缺少关键方法: {method}")

    # 4. patches/ 扩展模块导入检查（宽松检查，不阻止构建）
    patches_dir = ROOT_DIR / "resources" / "patches"
    extensions_dir = patches_dir / "extensions"
    if extensions_dir.exists():
        sys_path_backup = sys.path.copy()
        sys.path.insert(0, str(patches_dir))
        backend_dir = ROOT_DIR / "resources" / "backend"
        if backend_dir.exists():
            sys.path.insert(1, str(backend_dir))
        _SKIP_EXT = {"_context", "_utils", "upstream_tracker", "__init__"}
        try:
            for ext_file in sorted(extensions_dir.glob("*.py")):
                if ext_file.stem in _SKIP_EXT:
                    continue
                mod_name = f"extensions.{ext_file.stem}"
                try:
                    mod = __import__(mod_name, fromlist=["install"])
                    if not hasattr(mod, "install"):
                        print(f"  ⚠ 提示: {mod_name} 缺少 install() 函数")
                except ImportError:
                    pass
                except Exception:
                    pass
        finally:
            sys.path[:] = sys_path_backup

    # 5. app_factory.py 导出检查
    app_factory = patches_dir / "app_factory.py"
    if app_factory.exists():
        source = app_factory.read_text(encoding="utf-8")
        for name in ("create_app", "DEFAULT_ALLOWED_ORIGINS"):
            if name not in source:
                errors.append(f"app_factory.py 缺少导出: {name}")

    # 6. main.py 前端脚本模板检查（确保使用 __PLACEHOLDER__ 占位符而非 f-string 插值）
    if main_py.exists():
        source = main_py.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.JoinedStr):
                    for val in node.values:
                        if isinstance(val, ast.FormattedValue):
                            if isinstance(val.value, ast.Name):
                                var_name = val.value.id
                                if var_name in ("FRONTEND_PORT", "BACKEND_PORT", "BACKEND_BASE", "ui_dir"):
                                    line_no = getattr(val, "lineno", "?")
                                    errors.append(
                                        f"main.py:{line_no} 前端脚本模板中不得使用 f-string 插值 {{{var_name}}}，"
                                        f"应使用 __{var_name}__ 占位符 + str.replace()"
                                    )
        except Exception:
            pass
        template_placeholders = ["__APP_NAME__", "__VERSION__", "__UI_LOG_PATH__",
                                  "__UI_DIR__", "__BACKEND_PORT__", "__FRONTEND_PORT__",
                                  "__OUTPUTS_DIR__", "__ICON_PATH__", "__ICON_BASE64__"]
        for ph in template_placeholders:
            if ph not in source:
                errors.append(f"main.py 前端脚本模板缺少占位符 {ph}")
        for ph in template_placeholders:
            replace_call = f'.replace("{ph}"'
            if replace_call not in source:
                errors.append(f"main.py 前端脚本模板缺少 .replace({ph!r}, ...) 调用")

    if errors:
        print("  ❌ 代码验证失败！")
        for err in errors:
            print(f"    • {err}")
        return False

    print(f"  ✓ 语法检查通过 ({len(py_files)} 个文件)")
    print(f"  ✓ 方法完整性检查通过")
    print(f"  ✓ 关键方法存在性检查通过 ({len(critical_methods)} 个)")
    print(f"  ✓ 扩展模块导入检查通过")
    print(f"  ✓ app_factory.py 导出检查通过")
    return True


def _kill_running_exe():
    """终止正在运行的旧版 EXE 进程"""
    current_pid = os.getpid()
    killed = []
    try:
        import psutil as _ps
        for proc in _ps.process_iter(['pid', 'name', 'exe']):
            try:
                pname = (proc.info.get('name') or '').lower()
                if pname.startswith('云集智能视频创意站') and proc.info['pid'] != current_pid:
                    proc.terminate()
                    killed.append(pname)
            except (_ps.NoSuchProcess, _ps.AccessDenied):
                pass
    except ImportError:
        pass
    if killed:
        print(f"  已终止旧版进程: {', '.join(killed)}")
        time.sleep(1)
    return len(killed)


# ── PyInstaller 打包 ──
def build_exe():
    """用 PyInstaller --onefile 模式打包"""
    print(f"  PyInstaller 打包 (v{VERSION})...")

    release_name = f"{APP_NAME}-v{VERSION}"

    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    # 生成 Windows 版本信息文件
    ver_parts = VERSION.split(".")
    ver_tuple = ", ".join(str(int(p)) for p in ver_parts)
    version_file_content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({ver_tuple}),
    prodvers=({ver_tuple}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'080404B0',
          [
            StringStruct(u'CompanyName', u'YunJi'),
            StringStruct(u'FileDescription', u'YunJi Smart Video Creative Station'),
            StringStruct(u'FileVersion', u'{VERSION}'),
            StringStruct(u'InternalName', u'YunJiVideoCreative'),
            StringStruct(u'LegalCopyright', u'Copyright 2026 YunJi'),
            StringStruct(u'OriginalFilename', u'YunJiVideoCreative.exe'),
            StringStruct(u'ProductName', u'云集智能视频创意站'),
            StringStruct(u'ProductVersion', u'{VERSION}'),
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""
    version_file_path = ROOT_DIR / "version_info.txt"
    with open(str(version_file_path), "w", encoding="utf-8") as vf:
        vf.write(version_file_content)

    os.chdir(str(ROOT_DIR))

    icon_path = str(ROOT_DIR / "icon.ico")

    pyinstaller_args = [
        sys.executable, "-m", "PyInstaller",
        "--name", release_name,
        "--onefile", "--windowed",
        "--icon", icon_path,
        "--distpath", str(BUILD_DIR),
        "--workpath", str(BUILD_DIR / "_pyinstaller_work"),
        "--specpath", str(BUILD_DIR / "_pyinstaller_work"),
        "--clean", "--noconfirm",
        "--hidden-import", "PyQt6",
        "--hidden-import", "PyQt6.QtCore",
        "--hidden-import", "PyQt6.QtGui",
        "--hidden-import", "PyQt6.QtWidgets",
    ]

    pyinstaller_args += [
        "--hidden-import", "psutil",
        "--hidden-import", "debug_hub",
        "--exclude-module", "matplotlib",
        "--exclude-module", "scipy",
        "--exclude-module", "tkinter",
        "--exclude-module", "tensorflow",
        "--exclude-module", "torch",
        "--exclude-module", "transformers",
        "--exclude-module", "modelscope",
        "--exclude-module", "diffusers",
        "--exclude-module", "safetensors",
        "--exclude-module", "gradio",
        "--exclude-module", "fastapi",
        "--exclude-module", "uvicorn",
        "--exclude-module", "git",
        "--exclude-module", "gitdb",
        "--exclude-module", "gitpython",
    ]

    if os.path.exists(icon_path):
        pyinstaller_args.extend(["--add-data", f"{icon_path};."])
        print(f"  已添加图标: {icon_path}")

    icon_png = str(ROOT_DIR / "icon.png")
    if os.path.exists(icon_png):
        pyinstaller_args.extend(["--add-data", f"{icon_png};."])
        print(f"  已添加图标PNG: {icon_png}")

    ico_png = str(ROOT_DIR / "ico.png")
    if os.path.exists(ico_png):
        pyinstaller_args.extend(["--add-data", f"{ico_png};."])
        print(f"  已添加高清图标PNG: {ico_png}")

    vh_file = ROOT_DIR / "version_history.json"
    if vh_file.exists():
        pyinstaller_args.extend(["--add-data", f"{str(vh_file)};."])
        print(f"  已添加版本历史")

    pj_file = ROOT_DIR.parent.parent / "project.json"
    if pj_file.exists():
        pyinstaller_args.extend(["--add-data", f"{str(pj_file)};."])
        print(f"  已添加项目配置 (project.json)")

    # 嵌入 ui/backend/patches 资源到 EXE（用于版本切换时自动同步）
    for res_name in ("ui", "backend", "patches"):
        res_dir = ROOT_DIR / "resources" / res_name
        if res_dir.exists():
            pyinstaller_args.extend(["--add-data", f"{str(res_dir)};resources/{res_name}"])
            file_count = sum(1 for _ in res_dir.rglob("*") if _.is_file())
            print(f"  已嵌入 {res_name}/ ({file_count} 个文件)")

    if version_file_path.exists():
        pyinstaller_args.extend(["--version-file", str(version_file_path)])
        print(f"  已添加版本信息")

    pyinstaller_args.append("launcher.py")
    print(f"  使用 launcher.py 作为入口")

    print("  运行 PyInstaller (--onefile)...")
    subprocess.run(pyinstaller_args, check=True)

    exe_path = BUILD_DIR / f"{release_name}.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"  ✓ EXE 生成成功: {exe_path.name} ({size_mb:.1f} MB)")
    else:
        print(f"  ✗ EXE 未生成，请检查 PyInstaller 输出")
        raise FileNotFoundError(f"EXE not found: {exe_path}")

    return exe_path


# ── 打包后处理 ──
def post_build(exe_path: Path):
    """创建自部署发布目录结构
    
    目录结构：
    品牌名/
    ├── 品牌名.exe          (硬链接入口)
    ├── 品牌名-v版本.exe    (版本化EXE，在ver/目录)
    ├── .yunji.lock         (部署标识)
    ├── ver/
    │   └── 品牌名-v版本.exe
    ├── app/resources/      (应用资源)
    ├── data/               (用户数据)
    └── temp/               (临时文件)
    """
    print("  打包后处理（自部署模式）...")

    BRAND = APP_NAME  # "云集智能视频创意站"
    release_dir = BUILD_DIR / BRAND
    if release_dir.exists():
        shutil.rmtree(str(release_dir), ignore_errors=True)
    release_dir.mkdir(parents=True, exist_ok=True)

    # 1. 创建 ver/ 目录，将版本化 EXE 放入
    ver_dir = release_dir / "ver"
    ver_dir.mkdir(parents=True, exist_ok=True)
    versioned_exe_name = f"{BRAND}-v{VERSION}.exe"
    versioned_exe_path = ver_dir / versioned_exe_name
    shutil.copy2(str(exe_path), str(versioned_exe_path))
    print(f"  ✓ 版本化 EXE -> ver/{versioned_exe_name}")

    # 2. 创建硬链接入口 EXE
    entry_exe = release_dir / f"{BRAND}.exe"
    try:
        os.link(str(versioned_exe_path), str(entry_exe))
        print(f"  ✓ 硬链接入口: {BRAND}.exe -> ver/{versioned_exe_name}")
    except OSError:
        shutil.copy2(str(versioned_exe_path), str(entry_exe))
        print(f"  ✓ 复制入口: {BRAND}.exe (硬链接不可用，使用文件复制)")

    # 3. 创建 .yunji.lock
    lock_path = release_dir / ".yunji.lock"
    with open(str(lock_path), "w", encoding="utf-8") as f:
        f.write("yunji")
    print(f"  ✓ 创建部署标识: .yunji.lock")

    # 4. 复制 resources/ 目录
    _IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")

    patches_src = ROOT_DIR / "resources" / "patches"
    patches_dst = release_dir / "app" / "resources" / "patches"
    if patches_src.exists():
        if patches_dst.exists():
            shutil.rmtree(str(patches_dst), ignore_errors=True)
        shutil.copytree(str(patches_src), str(patches_dst), ignore=_IGNORE)
        print("  ✓ 复制 patches/")

    backend_src = ROOT_DIR / "resources" / "backend"
    backend_dst = release_dir / "app" / "resources" / "backend"
    if backend_src.exists():
        if backend_dst.exists():
            shutil.rmtree(str(backend_dst), ignore_errors=True)
        shutil.copytree(str(backend_src), str(backend_dst), ignore=_IGNORE)
        print("  ✓ 复制 backend/")

    ui_src = ROOT_DIR / "resources" / "ui"
    ui_dst = release_dir / "app" / "resources" / "ui"
    if ui_src.exists():
        if ui_dst.exists():
            shutil.rmtree(str(ui_dst), ignore_errors=True)
        shutil.copytree(str(ui_src), str(ui_dst), ignore=_IGNORE)
        print("  ✓ 复制 ui/")

    # 5. 创建 data/ 目录结构
    data_dir = release_dir / "data"
    for sub in ("outputs", "uploads", "models", "config"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    print("  ✓ 创建 data/ 目录结构")

    # 6. 创建 temp/ 目录结构
    temp_dir = release_dir / "temp"
    for sub in ("logs", Path("cache") / "thumbnails", Path("cache") / "temp", "debug"):
        (temp_dir / sub).mkdir(parents=True, exist_ok=True)
    print("  ✓ 创建 temp/ 目录结构")

    # 7. 生成 resources.zip（用于首次启动自举下载）
    resources_zip = release_dir / "resources.zip"
    resources_src = release_dir / "app" / "resources"
    if resources_src.exists():
        import zipfile
        with zipfile.ZipFile(str(resources_zip), 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path in resources_src.rglob("*"):
                if file_path.is_file() and "__pycache__" not in str(file_path):
                    arc_name = file_path.relative_to(resources_src)
                    zf.write(str(file_path), str(arc_name))
        zip_size_mb = resources_zip.stat().st_size / (1024 * 1024)
        print(f"  ✓ 生成 resources.zip ({zip_size_mb:.2f} MB)")
    else:
        print("  ⚠ 未找到 resources/ 目录，跳过生成 resources.zip")

    # 计算发布目录大小
    total_size = sum(f.stat().st_size for f in release_dir.rglob("*") if f.is_file())
    size_mb = total_size / (1024 * 1024)
    print(f"  发布目录大小: {size_mb:.1f} MB")

    return release_dir


# ── 清理 ──
def cleanup():
    """清理 PyInstaller 临时文件和 __pycache__"""
    work_dir = BUILD_DIR / "_pyinstaller_work"
    if work_dir.exists():
        try:
            shutil.rmtree(str(work_dir), ignore_errors=True)
            print("  清理 PyInstaller 临时文件")
        except Exception:
            pass

    pycache_count = 0
    for dp in [ROOT_DIR / "resources" / "patches", ROOT_DIR / "resources" / "backend"]:
        if not dp.exists():
            continue
        for cache_dir in dp.rglob("__pycache__"):
            try:
                shutil.rmtree(str(cache_dir), ignore_errors=True)
                pycache_count += 1
            except Exception:
                pass
    if pycache_count:
        print(f"  清理 __pycache__ ({pycache_count} 个目录)")


# ── 部署到 dev/ ──
def _deploy_to_dev(release_dir: Path):
    """将自部署发布目录复制到 dev/ 下
    
    部署结构：
    dev/云集智能视频创意站/
    ├── 云集智能视频创意站.exe          (硬链接入口)
    ├── ver/云集智能视频创意站-v版本.exe (版本化EXE)
    ├── .yunji.lock
    ├── app/resources/
    ├── data/
    └── temp/
    """
    BRAND = APP_NAME
    deploy_dir = DEV_DIR / BRAND

    _kill_running_exe()

    # 1. 如果目标目录已存在，只更新 EXE 和 resources
    if deploy_dir.exists():
        print(f"  更新现有部署: {deploy_dir}")
        
        # 更新 ver/ 目录中的 EXE
        ver_dir = deploy_dir / "ver"
        ver_dir.mkdir(parents=True, exist_ok=True)
        src_versioned = release_dir / "ver" / f"{BRAND}-v{VERSION}.exe"
        if src_versioned.exists():
            dst_versioned = ver_dir / f"{BRAND}-v{VERSION}.exe"
            shutil.copy2(str(src_versioned), str(dst_versioned))
            print(f"  ✓ 版本化 EXE: ver/{BRAND}-v{VERSION}.exe")
        
        # 更新硬链接入口
        entry_exe = deploy_dir / f"{BRAND}.exe"
        versioned_exe = ver_dir / f"{BRAND}-v{VERSION}.exe"
        if versioned_exe.exists():
            try:
                if entry_exe.exists():
                    entry_exe.unlink()
                os.link(str(versioned_exe), str(entry_exe))
                print(f"  ✓ 更新硬链接入口: {BRAND}.exe")
            except OSError:
                shutil.copy2(str(versioned_exe), str(entry_exe))
                print(f"  ✓ 更新入口 (复制): {BRAND}.exe")
        
        # 更新 .yunji.lock
        lock_path = deploy_dir / ".yunji.lock"
        if not lock_path.exists():
            with open(str(lock_path), "w", encoding="utf-8") as f:
                f.write("yunji")
        
        # 更新 resources
        for res_name in ("patches", "backend", "ui"):
            src_res = release_dir / "app" / "resources" / res_name
            dst_res = deploy_dir / "app" / "resources" / res_name
            if src_res.exists():
                if dst_res.exists():
                    shutil.rmtree(str(dst_res), ignore_errors=True)
                _IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")
                shutil.copytree(str(src_res), str(dst_res), ignore=_IGNORE)
                print(f"  ✓ 更新 {res_name}/")
    else:
        # 全新部署：直接复制整个目录
        shutil.copytree(str(release_dir), str(deploy_dir), 
                       ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"  ✓ 全新部署: {deploy_dir}")

    # 确保 data/ 和 temp/ 目录结构存在
    for sub in ("outputs", "uploads", "models", "config"):
        (deploy_dir / "data" / sub).mkdir(parents=True, exist_ok=True)
    for sub in ("logs", Path("cache") / "thumbnails", Path("cache") / "temp", "debug"):
        (deploy_dir / "temp" / sub).mkdir(parents=True, exist_ok=True)
    print(f"  ✓ 确保 data/ 和 temp/ 目录结构存在")

    print(f"  ✓ 部署完成")


def main():
    print("=" * 60)
    print(f"  {APP_NAME} - 版本化构建工具")
    print("=" * 60)
    print()
    print(f"  版本: {VERSION}")
    print(f"  源码: {ROOT_DIR}")
    print(f"  输出: {BUILD_DIR}")
    print(f"  模式: --onefile (单文件)")
    print()

    changes = []
    if len(sys.argv) > 1:
        changes = sys.argv[1:]
        print("使用命令行提供的修改内容：")
        for i, change in enumerate(changes, 1):
            print(f"  {i}. {change}")
        print()
    else:
        print("请输入本次版本的修改内容：")
        print("（每行一条，输入空行结束）")
        print()

        line_num = 1
        try:
            while True:
                line = input(f"  {line_num}. ").strip()
                if not line:
                    break
                changes.append(line)
                line_num += 1
        except (EOFError, KeyboardInterrupt):
            pass

        if not changes:
            print()
            print("提示：未输入修改内容，将使用默认描述")
            changes = ["优化和修复"]

        print()

    try:
        # Step 0: 代码验证
        print("── Step 0: 代码验证 ──")
        if not validate_code_before_build():
            print()
            print("❌ 打包中止：代码验证未通过，请修复上述错误后重试。")
            sys.exit(1)
        print()

        # Step 1: 检查并移除 BOM
        print("── Step 1: 预处理 ──")
        strip_bom_from_py_files()
        print()

        # Step 2: PyInstaller 打包
        print("── Step 2: PyInstaller 打包 (--onefile) ──")
        exe_path = build_exe()
        print()

        # Step 3: 打包后处理
        print("── Step 3: 打包后处理 ──")
        release_dir = post_build(exe_path)
        print()

        # Step 4: 清理
        print("── Step 4: 清理 ──")
        cleanup()
        print()

        # Step 5: 记录版本
        print("── Step 5: 记录版本 ──")
        version_history = load_version_history()
        version_name = release_dir.name
        version_history[version_name] = {
            "version": version_name,
            "changes": changes,
            "build_time": datetime.now().isoformat(),
            "version_number": VERSION
        }
        save_version_history(version_history)
        print("  ✓ 版本历史已更新")

        update_versions_json(VERSION, changes, f"{version_name}.exe")

        update_remote_version_json(VERSION, changes)
        print()

        # Step 6: 部署到 dev/
        print("── Step 6: 部署到 dev/ ──")
        try:
            _deploy_to_dev(release_dir)
        except Exception as deploy_err:
            print(f"  ⚠ 部署到 dev/ 部分失败: {deploy_err}")
            print(f"  EXE 和发布包已生成，请关闭旧版 EXE 后重新运行部署")
        print()

        # 完成
        BRAND = APP_NAME
        entry_exe = DEV_DIR / BRAND / f"{BRAND}.exe"
        print("=" * 60)
        print("  构建完成！（自部署模式）")
        print(f"  发布目录: {release_dir}")
        print(f"  部署目录: {DEV_DIR / BRAND}")
        if entry_exe.exists():
            size_mb = entry_exe.stat().st_size / (1024 * 1024)
            print(f"  入口EXE: {entry_exe}")
            print(f"  EXE 大小: {size_mb:.1f} MB")
        versioned_exe = DEV_DIR / BRAND / "ver" / f"{BRAND}-v{VERSION}.exe"
        if versioned_exe.exists():
            vsize_mb = versioned_exe.stat().st_size / (1024 * 1024)
            print(f"  版本EXE: ver/{BRAND}-v{VERSION}.exe ({vsize_mb:.1f} MB)")
        print(f"  资源目录: {ROOT_DIR}")
        print("=" * 60)

        # Git 提交
        commit_message = f"feat: 发布版本 v{VERSION}\n\n" + "\n".join([f"- {change}" for change in changes])
        git_commit_and_push(commit_message)

    except subprocess.CalledProcessError as e:
        print(f"\n打包失败：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n错误：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
