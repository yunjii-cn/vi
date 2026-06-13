#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发布脚本:将 ver/ 中的 EXE 真正发布到 Gitee + GitHub
- 仅 git push / 更新 versions.json / 创建 git tag 都不够,必须创建 Release + 上传 EXE
- download.php 通过 Gitee API 获取 release;启动器「软件更新」走 GitHub
- 本脚本是 build-version.py --release 的真正"最后一步"
"""
import os
import sys
import json
import time
import requests

PROJECT_ROOT = r"E:\软件开发\云集智能视频创意站"
TOKEN_PATH_GITEE = os.path.join(PROJECT_ROOT, "dev", "app", ".gitee_token")
TOKEN_PATH_GITHUB = os.path.join(PROJECT_ROOT, "dev", "app", ".github_token")
VER_DIR = os.path.join(PROJECT_ROOT, "dev", "ver")

# 配置:仓库坐标
GITEE_OWNER = "yunjii"
GITEE_REPO = "vi"
GITEE_API = "https://gitee.com/api/v5"

GITHUB_OWNER = "yunjii-cn"
GITHUB_REPO = "vi"
GITHUB_API = "https://api.github.com"
GITHUB_UPLOAD = "https://uploads.github.com"


# ═════════════ Token ═════════════
def _read_token(path, name):
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def get_gitee_token():
    return _read_token(TOKEN_PATH_GITEE, "Gitee")


def get_github_token():
    return _read_token(TOKEN_PATH_GITHUB, "GitHub")


# ═════════════ Gitee ═════════════
def gitee_list_releases(token):
    r = requests.get(
        f"{GITEE_API}/repos/{GITEE_OWNER}/{GITEE_REPO}/releases",
        params={"access_token": token, "per_page": 100},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def gitee_create_release(token, tag_name, name, body, target_commitish="main"):
    return requests.post(
        f"{GITEE_API}/repos/{GITEE_OWNER}/{GITEE_REPO}/releases",
        params={"access_token": token},
        json={
            "tag_name": tag_name,
            "name": name,
            "body": body,
            "target_commitish": target_commitish,
            "prerelease": False,
        },
        timeout=30,
    )


def gitee_upload_asset(token, release_id, file_path):
    upload_url = f"{GITEE_API}/repos/{GITEE_OWNER}/{GITEE_REPO}/releases/{release_id}/attach_files"
    fname = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        files = {"file": (fname, f, "application/octet-stream")}
        return requests.post(
            upload_url,
            params={"access_token": token},
            files=files,
            timeout=1200,  # 60MB+ EXE,长超时
        )


def publish_to_gitee(exe_path, version, body, dry_run=False, existing_tags=None):
    """发布到 Gitee;返回 True 成功,False 失败,'skip' 已存在"""
    token = get_gitee_token()
    if not token:
        print("    [Gitee] ✗ 无 token,跳过")
        return False
    tag_name = f"v{version}"

    if existing_tags is None:
        existing_tags = [r.get("tag_name") for r in gitee_list_releases(token)]
    if tag_name in existing_tags:
        print(f"    [Gitee] 跳过(已存在): {tag_name}")
        return "skip"

    if dry_run:
        print(f"    [Gitee] [DRY-RUN] 将创建 {tag_name} 并上传 {os.path.basename(exe_path)}")
        return True

    # 1. 创建 release
    r = gitee_create_release(token, tag_name, tag_name, body)
    if r.status_code >= 300:
        print(f"    [Gitee] ✗ 创建 release 失败: {r.status_code} {r.text[:200]}")
        return False
    release_id = r.json().get("id")
    if not release_id:
        print(f"    [Gitee] ✗ 创建 release 失败: 无 id,响应 {r.text[:200]}")
        return False
    print(f"    [Gitee] ✓ release 创建 (id={release_id})")

    # 2. 上传 EXE
    r = gitee_upload_asset(token, release_id, exe_path)
    if r.status_code >= 300:
        print(f"    [Gitee] ✗ 上传 EXE 失败: {r.status_code} {r.text[:200]}")
        return False
    print(f"    [Gitee] ✓ EXE 上传成功 ({os.path.getsize(exe_path)/1024/1024:.1f} MB)")
    return True


# ═════════════ GitHub ═════════════
def _gh_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "YunJii-Publisher/1.0",
    }


def github_list_release_tags(token):
    r = requests.get(
        f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases",
        headers=_gh_headers(token),
        params={"per_page": 100},
        timeout=15,
    )
    r.raise_for_status()
    return [r0.get("tag_name") for r0 in r.json()]


def github_create_release(token, tag_name, name, body, target_commitish="main"):
    return requests.post(
        f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases",
        headers=_gh_headers(token),
        json={
            "tag_name": tag_name,
            "target_commitish": target_commitish,
            "name": name,
            "body": body,
            "draft": False,
            "prerelease": False,
            "generate_release_notes": False,
        },
        timeout=30,
    )


def github_upload_asset(token, upload_url_template, file_path, fname=None):
    """upload_url_template 形如 https://uploads.github.com/repos/.../releases/{id}/assets{?name,label}
    fname: 上传后的文件名;不指定则用 file_path 的 basename
    注意:GitHub 在 name 查询参数中会剥离非 ASCII 字符,中文文件名上传后会被截断,
         这里默认对 GitHub 用英文名 yunji-video-creative-v{version}.exe
    """
    from requests import Request
    if fname is None:
        fname = os.path.basename(file_path)
    # 模板末尾的 {?name,label} 需要替换成 ?name=...(&name=...)之类的查询字符串
    upload_url = upload_url_template.replace("{?name,label}", f"?name={requests.utils.quote(fname)}")
    with open(file_path, "rb") as f:
        raw = f.read()
    # 用 PreparedRequest 确保 Content-Type 是 application/octet-stream 且 body 是原始字节
    # requests.post(data=bytes) 在某些情况下会被识别成 form-data
    req = Request(
        method="POST",
        url=upload_url,
        headers={
            **_gh_headers(token),
            "Content-Type": "application/octet-stream",
        },
        data=raw,
    )
    prepared = req.prepare()
    return requests.Session().send(prepared, timeout=1200)


def github_release_fname_for_version(version):
    """GitHub Release 上的英文文件名(沿用历史命名 yunji-video-creative-v{ver}.exe)"""
    return f"yunji-video-creative-v{version}.exe"


def github_list_release_assets(token, release_id):
    """列出 GitHub release 下的所有 asset(name, id)"""
    r = requests.get(
        f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/{release_id}/assets",
        headers=_gh_headers(token),
        timeout=15,
    )
    r.raise_for_status()
    return [{"name": a.get("name"), "id": a.get("id")} for a in r.json()]


def github_delete_asset(token, asset_id):
    return requests.delete(
        f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/assets/{asset_id}",
        headers=_gh_headers(token),
        timeout=15,
    )


def publish_to_github(exe_path, version, body, dry_run=False, existing_tags=None):
    """发布到 GitHub"""
    token = get_github_token()
    if not token:
        print("    [GitHub] ✗ 无 token,跳过")
        return False
    tag_name = f"v{version}"
    gh_fname = github_release_fname_for_version(version)  # 英文名

    if existing_tags is None:
        existing_tags = github_list_release_tags(token)

    if dry_run:
        if tag_name in existing_tags:
            print(f"    [GitHub] [DRY-RUN] {tag_name} 已存在,跳过")
        else:
            print(f"    [GitHub] [DRY-RUN] 将创建 {tag_name} 并上传 {gh_fname}")
        return "skip" if tag_name in existing_tags else True

    # 1. 创建 release(如果 tag 已存在于 git 但没有 release,GitHub 会自动使用现有 tag)
    if tag_name not in existing_tags:
        r = github_create_release(token, tag_name, tag_name, body)
        if r.status_code >= 300:
            print(f"    [GitHub] ✗ 创建 release 失败: {r.status_code} {r.text[:200]}")
            return False
        rel = r.json()
        release_id = rel.get("id")
        upload_url_tpl = rel.get("upload_url", "")
        if not release_id or not upload_url_tpl:
            print(f"    [GitHub] ✗ 创建 release 失败: id/upload_url 缺失,响应 {r.text[:200]}")
            return False
        print(f"    [GitHub] ✓ release 创建 (id={release_id})")
    else:
        print(f"    [GitHub] ↻ release 已存在,检查现有 asset")
        # 找 release id
        rr = requests.get(
            f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tags/{tag_name}",
            headers=_gh_headers(token),
            timeout=15,
        )
        if rr.status_code >= 300:
            print(f"    [GitHub] ✗ 查询已存在 release 失败: {rr.status_code} {rr.text[:200]}")
            return False
        rel = rr.json()
        release_id = rel.get("id")
        upload_url_tpl = rel.get("upload_url", "")
        if not release_id or not upload_url_tpl:
            print(f"    [GitHub] ✗ 已存在 release 缺少 id/upload_url")
            return False
        print(f"    [GitHub] ✓ release id={release_id}")

    # 2. 清理已有 asset(如果文件名不是预期的英文名,或者已有同名 asset)
    existing_assets = github_list_release_assets(token, release_id)
    for a in existing_assets:
        if a["name"] == gh_fname:
            print(f"    [GitHub] ↻ asset {gh_fname} 已存在,跳过上传")
            return "skip"
        # 删除命名不符合约定的旧 asset
        rd = github_delete_asset(token, a["id"])
        print(f"    [GitHub] ↻ 删除旧 asset {a['name']} (id={a['id']}): {rd.status_code}")

    # 3. 上传 EXE(用英文文件名)
    r = github_upload_asset(token, upload_url_tpl, exe_path, fname=gh_fname)
    if r.status_code >= 300:
        print(f"    [GitHub] ✗ 上传 EXE 失败: {r.status_code} {r.text[:200]}")
        return False
    print(f"    [GitHub] ✓ EXE 上传成功 ({os.path.getsize(exe_path)/1024/1024:.1f} MB) → {gh_fname}")
    return True


# ═════════════ 主体 ═════════════
# 已知发布正文(Gitee / GitHub 共用 markdown body)
# - 这里集中维护,后续要发新版本时只需追加一个 entry
KNOWN_BODIES = {
    "2026.06.09.2220": """## v2026.06.09.2220 更新

### 资产管理器(我的作品)
- 修复点击作品卡片无法读取对应作品的Bug
- 视频预下载队列化限2并发+缓存命中机制,避免重复下载与网络拥塞
- 缩略图并发加载数从5提升到8,列表渲染更快
- 后端缩略图API加入24小时浏览器缓存,二次访问秒开
- 文件被覆盖重新生成时,卡片原地刷新缩略图,解决图不对版问题
- 资产管理器长列表滚动性能优化,滑动更流畅

### 启动器
- 修复托盘右键菜单鼠标经过时无红色高亮(现以 #CC0000 正确高亮当前项)
- 改为Qt原生渲染菜单,自定义样式完整生效
- 新增单实例自动清理旧同名进程功能,测试或多次启动时不再累积僵尸进程
- 同时支持EXE模式与开发模式,精确匹配不误杀
- 帧率与时长改用可视化预设芯片,操作更直观
- 时长预设改为3/5/8/10/12/15/20/30秒,与vid-quality完全一致
- 移除前端代理中冗余的静态文件兜底路由
- 清理调试日志,启动器更轻量
""",
}


def parse_version_from_exe_name(exe_name):
    """云集智能视频创意站-v2026.06.09.2220.exe -> 2026.06.09.2220"""
    if not exe_name.startswith("云集智能视频创意站-v") or not exe_name.endswith(".exe"):
        return None
    return exe_name[len("云集智能视频创意站-v"):-len(".exe")]


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("[DRY-RUN 模式] 不会实际创建 Release 或上传 EXE\n")

    print("=" * 60)
    print("  Gitee + GitHub Release 发布工具")
    print("=" * 60)
    print(f"  Gitee:  https://gitee.com/{GITEE_OWNER}/{GITEE_REPO}/releases")
    print(f"  GitHub: https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases")
    print()

    exes = sorted([f for f in os.listdir(VER_DIR) if f.endswith(".exe")])
    print(f"  ver/ 共 {len(exes)} 个 EXE\n")

    # 解析命令行: --exe <文件名>  或  --all-known
    args = sys.argv[1:]
    targets = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--exe" and i + 1 < len(args):
            targets.append(args[i + 1])
            i += 2
        elif a == "--all-known":
            # 全部 KNOWN_BODIES 中有描述的 EXE
            for ver in KNOWN_BODIES:
                exe_name = f"云集智能视频创意站-v{ver}.exe"
                if exe_name in exes:
                    targets.append(exe_name)
            i += 1
        else:
            i += 1

    # 默认: 全部 KNOWN_BODIES 中有描述的 EXE
    if not targets:
        for ver in KNOWN_BODIES:
            exe_name = f"云集智能视频创意站-v{ver}.exe"
            if exe_name in exes:
                targets.append(exe_name)

    if not targets:
        print("  ✗ 没有需要发布的 EXE(KNOWN_BODIES 中无对应版本)")
        return 1

    print("  待发布目标:")
    for exe_name in targets:
        ver = parse_version_from_exe_name(exe_name)
        body = KNOWN_BODIES.get(ver)
        marker = "✓" if body else "✗(无描述,跳过)"
        print(f"    {marker} {exe_name}")
    print()

    # 预先取一次 Gitee/GitHub 已有 tag,避免每次重复请求
    gitee_token = get_gitee_token()
    github_token = get_github_token()
    gitee_existing = [r.get("tag_name") for r in gitee_list_releases(gitee_token)] if gitee_token else []
    github_existing = github_list_release_tags(github_token) if github_token else []

    ok = skip = fail = 0
    for exe_name in targets:
        ver = parse_version_from_exe_name(exe_name)
        if not ver:
            print(f"  ⚠ 跳过(命名不匹配): {exe_name}")
            skip += 1
            continue
        body = KNOWN_BODIES.get(ver)
        if not body:
            print(f"  ⚠ 跳过(无 KNOWN_BODIES 描述): {exe_name}")
            skip += 1
            continue
        exe_path = os.path.join(VER_DIR, exe_name)
        if not os.path.isfile(exe_path):
            print(f"  ✗ EXE 不存在: {exe_path}")
            fail += 1
            continue

        print(f"  ▶ {exe_name}  (Gitee + GitHub)")
        r1 = publish_to_gitee(exe_path, ver, body, dry_run=dry_run, existing_tags=gitee_existing)
        r2 = publish_to_github(exe_path, ver, body, dry_run=dry_run, existing_tags=github_existing)
        # 统计:两个平台要么都成功/跳过,要么任一失败
        # skip = "已存在"等同成功
        if (r1 in (True, "skip")) and (r2 in (True, "skip")):
            if r1 == "skip" and r2 == "skip":
                skip += 1
            else:
                ok += 1
        else:
            fail += 1
        print()

    print("=" * 60)
    print(f"  发布结果:成功 {ok} / 跳过 {skip} / 失败 {fail}")
    print("=" * 60)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
