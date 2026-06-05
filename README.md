# 云集智能视频创意站

基于 LTX Studio 的 AI 视频生成桌面启动器，提供一键部署、模型管理、环境维护等完整工作流。

## 功能特性

- **一键部署** — 自动安装 Python 运行时、UV 包管理器、LTX Studio 后端及依赖
- **模型管理** — 内置模型下载页面，支持 HuggingFace 多镜像源下载
- **环境维护** — 自动检测 GPU 信息、FFmpeg 安装、依赖修复
- **低显存模式** — 支持 FP8 量化推理，6GB 显存即可运行
- **LoRA 支持** — IC-LoRA 风格迁移、社区 LoRA 注册表
- **TTS 语音** — 集成文字转语音功能
- **批量生成** — 支持批量任务队列管理
- **软件更新** — 双源更新机制（Gitee/GitHub），自动适应网络环境

## 目录结构

```
云集智能视频创意站/
├── release/                    # 版本发布信息
│   └── version.json            # 版本列表（唯一数据源）
├── app/                        # 只读（Git管理）
│   ├── main.py                 # PyQt6 桌面启动器
│   ├── launcher.py             # EXE入口点
│   ├── icon.ico / icon.png     # 应用图标
│   ├── splash.png              # 闪屏画面
│   ├── requirements.txt        # Python依赖
│   ├── build-version.py        # 构建脚本
│   └── resources/
│       ├── backend/            # LTX Studio 后端（PYTHONPATH劫持）
│       ├── patches/            # 运行时补丁（优先于backend加载）
│       │   ├── extensions/     # 扩展功能模块
│       │   ├── _ui_server.py   # Web UI 服务器
│       │   └── settings.json   # 默认配置
│       └── ui/                 # Web 前端
│           ├── index.html
│           ├── index.js
│           ├── index.css
│           └── i18n.js
├── data/                       # 可写（用户数据）
│   ├── .venv/                  # Python虚拟环境
│   ├── models/                 # AI模型文件
│   ├── outputs/                # 生成输出
│   ├── uploads/                # 用户上传
│   └── config/                 # 运行时配置
└── temp/                       # 可删除（临时文件）
    └── logs/                   # 运行日志
```

### 三目录设计原则

| 目录 | 权限 | 备份 | 说明 |
|------|------|------|------|
| `app/` | 只读 | 不需要 | 程序本体，Git管理，可随时从仓库恢复 |
| `data/` | 可写 | 建议 | 用户数据、模型、环境，是核心资产 |
| `temp/` | 可删除 | 不需要 | 日志和临时文件，删除后自动重建 |

## 快速开始

### 方式一：下载 EXE（推荐）

1. 前往 [Releases](https://github.com/yunjii-cn/vi/releases) 下载最新版 EXE
2. 将 EXE 放入目标目录（如 `D:\云集智能视频创意站\`）
3. 双击运行，按向导完成部署

### 方式二：从源码运行

```bash
# 克隆仓库
git clone https://github.com/yunjii-cn/vi.git
cd vi

# 安装依赖
pip install -r dev/app/requirements.txt

# 运行启动器
python dev/app/main.py
```

## 构建EXE

```bash
cd dev/app
python build-version.py
```

构建产物输出到 `dev/` 目录下，版本号格式为 `YYYY.MM.DD.HHMM`。

## 技术栈

| 技术 | 用途 |
|------|------|
| Python 3.12 | 开发语言 |
| PyQt6 | 桌面GUI框架 |
| PyInstaller | EXE打包（--onedir模式） |
| UV | Python包管理器 |
| LTX Studio | AI视频生成后端 |
| FastAPI | 后端API服务 |
| GitHub/Gitee Releases | 版本分发 |

## 开源协议

本项目采用 [GPL-3.0](LICENSE) 协议开源，符合自由软件基金会（FSF）定义的自由软件标准。

- ✅ 允许自由使用、修改和分发
- ✅ 版本历史公开透明（见 [release/version.json](release/version.json)）
- ✅ 源代码始终可获取（GitHub/Gitee 双源镜像）
- ❌ **禁止闭源商业使用** — 任何衍生作品必须同样以 GPL-3.0 开源
- ❌ **禁止移除版权声明**
- ❌ **禁止专利限制** — 不得对软件施加专利限制

## 链接

- **GitHub**: https://github.com/yunjii-cn/vi
- **Gitee 镜像**: https://gitee.com/yunjii/vi
- **问题反馈**: https://github.com/yunjii-cn/vi/issues

---

Copyright (C) 2026 yunjii-cn
