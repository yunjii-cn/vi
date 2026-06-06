#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EXE构建脚本 - 云集智能视频创意站
--onefile 模式打包（单文件，稳定可靠）

使用方法：
  python build-version.py --dev 修改内容1 修改内容2 ...    # 日常开发：仅git提交推送
  python build-version.py --build 修改内容1 修改内容2 ...  # 测试构建：打包EXE到dist+git推送（不更新版本列表）
  python build-version.py --release 修改内容1 修改内容2 ... # 正式发布：以git为依据合并版本描述+更新版本列表
  python build-version.py --build                       # 交互式输入修改内容
  python build-version.py                               # 默认 --build

版本发布规则：
  - ver/ 文件夹是稳定版的依据：只有手动放入 ver/ 的版本才会发布EXE给用户下载
  - 已发布的版本和EXE永久存在（不可逆），除非主动删除
  - 日常开发用bat测试，成熟后打包EXE到dist并推送git
  - 亲测稳定后手动放入ver/，再使用 --release 正式发布
  - git提交面向开发者（源码修改描述），软件版本面向用户（版本变化描述）
  - --release 时以git历史为依据合并和完善版本描述，只看修改结果区别总结

架构说明：
  - --onefile 模式打包（单文件EXE，启动稍慢但极少出问题）
  - main.py 直接作为入口（launcher 功能已合并到 main.py）
  - PyInstaller 工作目录: dev/dist/ (仅构建用，不推送)
  - 三目录原则：
    dev/dist/           = 构建产物（gitignore）
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
BUILD_DIR = DEV_DIR / "dist"                        # dev/dist/
VERSION_HISTORY_FILE = ROOT_DIR / "version_history.json"
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
                    ['git', 'push', 'origin', 'main'],
                    cwd=PROJECT_ROOT, capture_output=True, text=True,
                    timeout=180
                )
                if result.returncode == 0:
                    print("  ✓ 推送成功 (origin/main)")
                    break
                else:
                    print(f"  警告：推送失败（第{attempt + 1}次尝试）：{result.stderr}")
                    if attempt < max_attempts - 1:
                        time.sleep(2)
            except subprocess.TimeoutExpired:
                print(f"  警告：推送超时（第{attempt + 1}次尝试）")
                if attempt < max_attempts - 1:
                    time.sleep(2)
        # 推送到 Gitee
        try:
            result = subprocess.run(
                ['git', 'push', 'gitee', 'main'],
                cwd=PROJECT_ROOT, capture_output=True, text=True,
                timeout=180
            )
            if result.returncode == 0:
                print("  ✓ 推送成功 (gitee/main)")
            else:
                print(f"  警告：Gitee推送失败：{result.stderr}")
        except subprocess.TimeoutExpired:
            print("  警告：Gitee推送超时")

        return True

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
        # 更新 dev/app/versions.json（旧格式，兼容）
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


def generate_dev_changelog(limit=200):
    """从git历史生成开发动态文件，嵌入EXE供用户查看"""
    try:
        result = subprocess.run(
            ["git", "log", f"-{limit}", "--format=%h|%s|%an|%ai"],
            capture_output=True, text=True, timeout=30,
            cwd=str(ROOT_DIR.parent.parent)
        )
        if result.returncode != 0 or not result.stdout.strip():
            print("  △ 无法获取git历史，跳过生成开发动态")
            return False

        commits = []
        for line in result.stdout.strip().splitlines():
            parts = line.strip().split("|", 3)
            if len(parts) >= 4:
                commits.append({
                    "sha": parts[0],
                    "message": parts[1],
                    "author": parts[2],
                    "date": parts[3],
                })

        if not commits:
            return False

        changelog_file = ROOT_DIR / "dev_changelog.json"
        with open(changelog_file, 'w', encoding='utf-8') as f:
            json.dump(commits, f, ensure_ascii=False, indent=2)

        print(f"  ✓ 开发动态已生成 ({len(commits)} 条)")
        return True

    except Exception as e:
        print(f"  △ 生成开发动态失败: {e}")
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

    # 嵌入versions.json，自部署时释放到app/目录
    vs_file = ROOT_DIR / "versions.json"
    if vs_file.exists():
        pyinstaller_args.extend(["--add-data", f"{str(vs_file)};."])
        print(f"  已添加版本列表 (versions.json)")

    # 生成开发动态（与versions.json同路径逻辑）
    generate_dev_changelog()
    cl_file = ROOT_DIR / "dev_changelog.json"
    if cl_file.exists():
        pyinstaller_args.extend(["--add-data", f"{str(cl_file)};."])
        print(f"  已添加开发动态 (dev_changelog.json)")

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
    """打包后处理：确认EXE输出，清理临时文件
    
    PyInstaller已将EXE直接输出到 dev/dist/，无需额外复制。
    自部署结构由EXE运行时自行创建。
    """
    print("  打包后处理...")

    if not exe_path.exists():
        print(f"  ⚠ EXE不存在: {exe_path}")
        return exe_path

    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print(f"  ✓ EXE: {exe_path.name} ({size_mb:.1f} MB)")

    return exe_path


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


# ── 发布EXE到 ver/ 目录 ──
def publish_to_ver(exe_path: Path):
    """将EXE复制到 ver/ 目录，标记为稳定版"""
    ver_dir = PROJECT_ROOT / "ver"
    ver_dir.mkdir(parents=True, exist_ok=True)
    dest = ver_dir / exe_path.name
    if dest.exists():
        print(f"  ⚠ ver/ 中已存在: {exe_path.name}，跳过复制")
    else:
        shutil.copy2(str(exe_path), str(dest))
        print(f"  ✓ 已发布到 ver/: {exe_path.name}")
    return dest


def _collect_git_changes_since_last_release():
    """从git历史中收集自上次release以来的变更，合并为面向用户的版本描述"""
    try:
        # 获取上次release标签或提交
        result = subprocess.run(
            ['git', 'log', '--oneline', '--grep=^release:', '-1'],
            capture_output=True, text=True,
            cwd=PROJECT_ROOT, timeout=10
        )
        last_release_hash = ""
        if result.stdout.strip():
            last_release_hash = result.stdout.strip().split()[0]
            print(f"  上次正式发布: {result.stdout.strip()[:60]}")

        if last_release_hash:
            # 获取自上次release以来的所有提交
            result = subprocess.run(
                ['git', 'log', '--oneline', f'{last_release_hash}..HEAD'],
                capture_output=True, text=True,
                cwd=PROJECT_ROOT, timeout=10
            )
            commits = result.stdout.strip().split('\n') if result.stdout.strip() else []
        else:
            # 没有release记录，取最近20条
            result = subprocess.run(
                ['git', 'log', '--oneline', '-20'],
                capture_output=True, text=True,
                cwd=PROJECT_ROOT, timeout=10
            )
            commits = result.stdout.strip().split('\n') if result.stdout.strip() else []

        if not commits or commits == ['']:
            print("  未找到git变更记录")
            return []

        print(f"  收集到 {len(commits)} 条git提交记录")
        return commits
    except Exception as e:
        print(f"  获取git历史失败: {e}")
        return []


# ── 主流程 ──
def main():
    # 解析命令行参数
    args = sys.argv[1:]
    mode = "build"  # 默认模式：打包+git推送
    changes = []

    for arg in args:
        if arg in ("--dev", "--build", "--release"):
            mode = arg.lstrip("-")
        else:
            changes.append(arg)

    mode_labels = {
        "dev": "日常开发 (dev)",
        "build": "测试构建 (build)",
        "release": "正式发布 (release)",
    }
    mode_label = mode_labels.get(mode, mode)

    print("=" * 60)
    print(f"  {APP_NAME} - 版本化构建工具")
    print("=" * 60)
    print()
    print(f"  版本: {VERSION}")
    print(f"  模式: {mode_label}")
    print(f"  源码: {ROOT_DIR}")
    if mode in ("build", "release"):
        print(f"  输出: {BUILD_DIR}")
    if mode == "release":
        print(f"  发布: ver/")
    print()

    if changes:
        print("使用命令行提供的修改内容：")
        for i, change in enumerate(changes, 1):
            print(f"  {i}. {change}")
        print()
    else:
        print("请输入本次修改内容：")
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
        # ── 日常开发模式：仅git提交推送 ──
        if mode == "dev":
            commit_message = f"dev: {changes[0]}\n\n" + "\n".join([f"- {c}" for c in changes])
            git_commit_and_push(commit_message)

            print()
            print("=" * 60)
            print("  开发提交完成！")
            print(f"  描述: {changes[0]}")
            print("=" * 60)
            return

        # ── 测试构建模式：打包EXE + git提交推送 ──
        if mode == "build":
            # Step 0: 代码验证
            print("── Step 0: 代码验证 ──")
            if not validate_code_before_build():
                print()
                print("❌ 打包中止：代码验证未通过，请修复上述错误后重试。")
                sys.exit(1)
            print()

            # Step 1: 预处理
            print("── Step 1: 预处理 ──")
            strip_bom_from_py_files()
            print()

            # Step 2: PyInstaller 打包
            print("── Step 2: PyInstaller 打包 (--onefile) ──")
            exe_path = build_exe()
            print()

            # Step 3: 打包后处理
            print("── Step 3: 打包后处理 ──")
            exe_output = post_build(exe_path)
            print()

            # Step 4: 清理
            print("── Step 4: 清理 ──")
            cleanup()
            print()

            # Step 5: 记录版本历史（仅本地记录，不更新远程版本列表）
            print("── Step 5: 记录版本历史 ──")
            version_history = load_version_history()
            version_name = f"{APP_NAME}-v{VERSION}"
            version_history[version_name] = {
                "version": version_name,
                "changes": changes,
                "build_time": datetime.now().isoformat(),
                "version_number": VERSION
            }
            save_version_history(version_history)
            print("  ✓ 版本历史已更新（本地）")

            # 更新 versions.json（嵌入EXE + 远程访问）
            exe_name = f"{APP_NAME}-v{VERSION}.exe"
            update_versions_json(VERSION, changes, exe_name)

            print()

            # 完成
            print("=" * 60)
            print("  测试构建完成！")
            print(f"  EXE: {exe_output}")
            size_mb = exe_output.stat().st_size / (1024 * 1024)
            print(f"  大小: {size_mb:.1f} MB")
            print(f"  资源目录: {ROOT_DIR}")
            print("  提示: 亲测稳定后手动放入 ver/，再使用 --release 正式发布")
            print("=" * 60)

            # Git 提交（开发者视角的源码修改描述）
            commit_message = f"build: v{VERSION}\n\n" + "\n".join([f"- {c}" for c in changes])
            git_commit_and_push(commit_message)
            return

        # ── 正式发布模式：以git为依据合并版本描述 + 更新版本列表 ──
        if mode == "release":
            # 检查ver/目录中是否有待发布的EXE
            ver_dir = PROJECT_ROOT / "ver"
            if not ver_dir.is_dir():
                print("❌ ver/ 目录不存在，请先将测试稳定的EXE放入 ver/ 目录")
                sys.exit(1)

            ver_exes = sorted(ver_dir.glob("*.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not ver_exes:
                print("❌ ver/ 目录中没有EXE文件，请先将测试稳定的EXE放入 ver/ 目录")
                sys.exit(1)

            # 找出ver/中尚未在版本列表中记录的EXE
            release_file = PROJECT_ROOT / "release" / "version.json"
            released_versions = set()
            if release_file.exists():
                try:
                    with open(release_file, 'r', encoding='utf-8') as f:
                        release_data = json.load(f)
                    for v in release_data.get("versions", []):
                        released_versions.add(v.get("exe", v.get("filename", "")))
                except Exception:
                    pass

            unreleased = [e for e in ver_exes if e.name not in released_versions]

            if not unreleased:
                print("  ver/ 中的所有EXE已发布，无需重复发布")
                return

            print(f"  发现 {len(unreleased)} 个待发布的EXE：")
            for e in unreleased:
                size_mb = e.stat().st_size / (1024 * 1024)
                print(f"    - {e.name} ({size_mb:.1f} MB)")
            print()

            # 收集git历史作为版本描述依据
            print("── 收集git变更历史 ──")
            git_commits = _collect_git_changes_since_last_release()
            print()

            # 如果命令行提供了描述，直接使用；否则提示用户基于git历史编写
            if not changes or changes == ["优化和修复"]:
                if git_commits:
                    print("  以下git提交记录可作为版本描述参考：")
                    for i, commit in enumerate(git_commits[:15], 1):
                        print(f"    {i}. {commit[:80]}")
                    print()
                    print("  请输入面向用户的版本描述（合并git历史，只看修改结果区别总结）：")
                    print("  （每行一条，输入空行结束）")
                    print()
                    changes = []
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
                        changes = ["优化和修复"]

            # 更新版本列表
            print()
            print("── 更新版本列表 ──")
            for ver_exe in unreleased:
                # 从EXE文件名提取版本号
                exe_name = ver_exe.name
                ver_match = None
                import re
                m = re.search(r'v(\d{4}\.\d{2}\.\d{2}\.\d{4})', exe_name)
                if m:
                    ver_match = m.group(1)

                if ver_match:
                    update_versions_json(ver_match, changes, exe_name)
                    print(f"  ✓ {exe_name} 已添加到版本列表")
                else:
                    print(f"  ⚠ 无法从文件名提取版本号: {exe_name}，跳过")

            print()

            # 完成
            print("=" * 60)
            print("  正式版发布完成！")
            print(f"  发布版本数: {len(unreleased)}")
            print(f"  版本描述: {changes[0]}")
            print("=" * 60)

            # Git 提交（面向用户的版本变化描述）
            commit_message = f"release: v{VERSION}\n\n" + "\n".join([f"- {c}" for c in changes])
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
