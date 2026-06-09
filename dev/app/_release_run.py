#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发布脚本:云集智能视频创意站 v2026.06.09.2220
直接调用 build-version.py --release 并传入精心编写的版本描述
"""
import os
import sys
import subprocess

VENV_PYTHON = r"E:\软件开发\云集智能视频创意站\dev\data\.venv\Scripts\python.exe"
PROJECT_ROOT = r"E:\软件开发\云集智能视频创意站"
BUILD_SCRIPT = os.path.join(PROJECT_ROOT, "dev", "app", "build-version.py")

# 版本描述(面向用户,合并git历史,只看修改结果区别总结)
changes = [
    # 第 1 行:作为 commit 标题(brief,前 60 字符)
    "资产管理器与启动器体验全面升级",
    # 后续行:作为 commit body 列表项
    "1. 资产管理器(我的作品)性能与稳定性大幅提升:",
    "  - 修复点击作品卡片无法读取对应作品的Bug",
    "  - 视频预下载队列化限2并发,改为缓存命中机制,避免重复下载与网络拥塞",
    "  - 缩略图并发加载数从5提升到8,列表渲染更快",
    "  - 后端缩略图API加入24小时浏览器缓存,二次访问秒开",
    "  - 文件被覆盖重新生成时,卡片原地刷新缩略图,解决图不对版问题",
    "  - 资产管理器长列表滚动性能优化,滑动更流畅",
    "2. 启动器托盘菜单视觉修复:",
    "  - 修复右键菜单鼠标经过时无红色高亮的Bug(现以 #CC0000 正确高亮当前项)",
    "  - 改为Qt原生渲染菜单,自定义样式完整生效",
    "3. 启动器单实例锁定:",
    "  - 新增自动清理旧的同名进程功能,测试或多次启动时不再累积僵尸进程",
    "  - 同时支持 EXE 模式与开发模式,精确匹配不误杀",
    "4. 参数设置优化:",
    "  - 帧率与时长改用可视化预设芯片,操作更直观",
    "  - 时长预设改为 3/5/8/10/12/15/20/30 秒,选项与 vid-quality 完全一致",
    "5. 维护与清理:",
    "  - 移除前端代理中冗余的静态文件兜底路由",
    "  - 清理调试日志,启动器更轻量",
]

# 调用 build-version.py --release
cmd = [VENV_PYTHON, BUILD_SCRIPT, "--release"] + changes
print("=" * 60)
print("  启动正式发布流程")
print("=" * 60)
print(f"  命令: {os.path.basename(BUILD_SCRIPT)} --release <共 {len(changes)} 条描述>")
print()
print("  版本描述预览:")
for i, c in enumerate(changes, 1):
    print(f"    {i}. {c}")
print()
print("=" * 60)
print()

result = subprocess.run(cmd, cwd=PROJECT_ROOT)
sys.exit(result.returncode)
