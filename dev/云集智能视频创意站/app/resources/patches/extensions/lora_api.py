"""LoRA scanning and management API endpoints (YunJi custom).

Endpoints:
  GET  /api/loras       - List available LoRA models (with metadata)
  GET  /api/lora-info   - Get detailed metadata for a single LoRA file
  POST /api/lora-dir    - Save LoRA directory preference
  GET  /api/lora-dir    - Get LoRA directory preference

Upstream dependency: handler.pipelines.models_dir
"""

from __future__ import annotations

import json
import os
import re
import struct
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from extensions._context import ExtensionContext
from extensions._utils import default_lora_dir, resolve_models_root

_LORA_KNOWN_INFO: dict[str, dict] = {
    "ltx-2-19b-distilled-lora-384.safetensors": {
        "description": "Pro模式LoRA（视频生成Pro高质量模式必需，384步推理）",
        "trigger_words": [],
        "base_model": "Lightricks/LTX-2",
    },
    "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors": {
        "description": "视频迁移控制模型（视频迁移功能必需，支持深度/姿态/参考图控制，ref0.5版本平衡控制力与自由度）",
        "trigger_words": [],
        "base_model": "Lightricks/LTX-2.3",
    },
    "90sAnimationStyle.safetensors": {
        "description": "90年代经典动画风格，模拟复古赛璐璐动画质感，色彩饱和、线条粗犷，适合怀旧动画、复古卡通视频创作",
        "trigger_words": ["90s animation style", "retro cartoon"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "Cinematic_sci-fi-cyberpunk.safetensors": {
        "description": "科幻赛博朋克电影风格，霓虹灯光、未来都市、暗色调高对比度，适合科幻短片、赛博朋克场景视频",
        "trigger_words": ["sci-fi", "cyberpunk", "cinematic"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "Claymation.safetensors": {
        "description": "黏土动画风格，模拟定格动画中黏土角色的圆润质感和手工痕迹，适合趣味短片、儿童内容、创意广告",
        "trigger_words": ["claymation", "clay animation"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "CozyFelt.safetensors": {
        "description": "温暖毛毡风格，模拟手工毛毡布艺的柔软纹理和温馨色调，适合治愈系视频、温馨场景、儿童内容",
        "trigger_words": ["cozy felt", "felt craft"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "FantasyPuppetStyle.safetensors": {
        "description": "奇幻木偶风格，模拟提线木偶和布偶的质感与动态，适合奇幻故事、童话改编、创意艺术视频",
        "trigger_words": ["fantasy puppet", "puppet style"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "Fantasy_Anime.safetensors": {
        "description": "奇幻动漫风格，融合日式动画的精致画面与奇幻世界观，适合奇幻冒险、魔法战斗、异世界题材视频",
        "trigger_words": ["fantasy anime", "magical anime"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "Fantasy_Painterly.safetensors": {
        "description": "奇幻绘画风格，模拟油画/水彩的手绘笔触质感，画面具有浓厚的艺术感和绘画肌理，适合艺术风格视频、插画动画",
        "trigger_words": ["painterly", "fantasy painting"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "Fantasy_Realism.safetensors": {
        "description": "奇幻写实风格，在写实基础上融入奇幻元素，光影真实但场景超现实，适合奇幻电影、概念艺术视频",
        "trigger_words": ["fantasy realism", "magical realism"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "LTX2.3_Crisp_Enhance.safetensors": {
        "description": "清晰增强LoRA，提升画面锐度和细节清晰度，使视频画面更加精致通透，适合需要高清晰度的产品展示、风景视频",
        "trigger_words": ["crisp", "sharp", "detailed"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "LTX2.3_Soft_Enhance.safetensors": {
        "description": "柔和增强LoRA，为画面添加柔光滤镜效果，色彩温润、过渡平滑，适合人像美化、梦幻氛围、柔焦效果视频",
        "trigger_words": ["soft", "gentle", "dreamy"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "Luxe_Sensual.safetensors": {
        "description": "奢华感官风格，高端质感的柔光与金属反光效果，画面华丽精致，适合奢侈品广告、高端产品展示、时尚大片",
        "trigger_words": ["luxe", "sensual", "luxury"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "PaperCutOutStyle.safetensors": {
        "description": "纸雕剪纸风格，模拟层叠剪纸的立体效果和纸张纹理，适合创意动画、文化宣传、节日主题视频",
        "trigger_words": ["paper cut", "paper craft", "papercut"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "Pixar_Toon.safetensors": {
        "description": "皮克斯卡通风格，3D卡通渲染质感，角色圆润可爱、色彩明快，适合动画短片、儿童内容、趣味视频",
        "trigger_words": ["pixar style", "3d cartoon", "pixar toon"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "Post_Apocalyptic.safetensors": {
        "description": "末世废土风格，荒芜废墟、破败建筑、灰暗色调，适合末日题材、科幻废土、生存类视频",
        "trigger_words": ["post-apocalyptic", "wasteland", "ruins"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "Wild_West.safetensors": {
        "description": "西部荒野风格，美国西部牛仔、荒漠小镇、夕阳旷野，适合西部题材、冒险故事、复古风格视频",
        "trigger_words": ["wild west", "cowboy", "western"],
        "base_model": "Lightricks/LTX-2.3",
    },
    "Z-Iamge-人像美学.safetensors": {
        "description": "Z-Image人像美学增强，优化人像肤色、光影和整体美感，适合人像写真、美妆展示、人物特写视频",
        "trigger_words": [],
        "base_model": "Z-Image",
    },
    "Z-Image-Fun-Lora-Distill-8-Steps-2603-ComfyUI.safetensors": {
        "description": "Z-Image蒸馏加速LoRA，仅需8步即可生成高质量图像，大幅提升生成速度，适合快速预览、批量生成场景",
        "trigger_words": [],
        "base_model": "Z-Image",
    },
    "Z-Image｜轻柔东方审美人像摄影写真风格_v1.0.safetensors": {
        "description": "轻柔东方审美人像摄影风格，呈现东方美学的柔和光影与含蓄韵味，适合中式写真、古风人像、东方美学视频",
        "trigger_words": [],
        "base_model": "Z-Image",
    },
    "Z-image-眼睛细节增强-DetailedEyes-LoRA_V2.safetensors": {
        "description": "眼睛细节增强V2，显著提升眼部细节和眼神表现力，瞳孔虹膜纹理更丰富，适合人像特写、眼部细节优化",
        "trigger_words": ["detailed eyes"],
        "base_model": "Z-Image",
    },
    "Z-image-高清人像.safetensors": {
        "description": "高清人像增强，提升人像整体清晰度和细节表现，肤质细腻、五官精致，适合高清人像视频、写真输出",
        "trigger_words": [],
        "base_model": "Z-Image",
    },
    "ZIB-电影光Chiaroscuro and Cinematic Lighting Style.safetensors": {
        "description": "电影光效明暗对比风格（Chiaroscuro），强烈的明暗对比营造戏剧性氛围，适合电影感视频、戏剧性场景、艺术短片",
        "trigger_words": ["chiaroscuro", "cinematic lighting"],
        "base_model": "Z-Image",
    },
    "ZIT-伦勃朗光线rembrandt_ZIT_tyler_x_harris.safetensors": {
        "description": "伦勃朗光线风格，经典三角光人像布光，面部一侧受光、一侧阴影，适合经典人像、艺术摄影、戏剧性肖像",
        "trigger_words": ["rembrandt lighting"],
        "base_model": "Z-Image",
    },
    "ZIT-影棚摄影photolab_v2.safetensors": {
        "description": "影棚摄影风格V2，专业影棚布光效果，干净背景、精准控光，适合产品摄影、人像棚拍、商业展示视频",
        "trigger_words": ["photolab", "studio photography"],
        "base_model": "Z-Image",
    },
    "ZIT-电影光Cinematic Chiaroscuro Lighting.safetensors": {
        "description": "电影级明暗对比光效，好莱坞式电影布光质感，适合电影感视频、叙事短片、氛围感场景",
        "trigger_words": ["cinematic chiaroscuro"],
        "base_model": "Z-Image",
    },
    "ZIT-电影黑暗MschCine26_V1.safetensors": {
        "description": "电影暗调风格，低调照明、暗色系画面、悬疑氛围，适合悬疑片、恐怖片、暗黑风格视频",
        "trigger_words": ["dark cinematic"],
        "base_model": "Z-Image",
    },
    "ZiB-female解剖学_anatomy.safetensors": {
        "description": "女性人体解剖学增强，优化女性人体结构和比例的准确性，适合人物创作、艺术参考、人体结构优化",
        "trigger_words": ["anatomy"],
        "base_model": "Z-Image",
    },
    "hina_zImageTurbo_asianMix_v4.59C-bf16.safetensors": {
        "description": "亚洲面孔混合模型V4.59C，优化亚洲人面孔特征表现，适合亚洲人像、东亚面孔、多元人像视频",
        "trigger_words": [],
        "base_model": "Z-Image",
    },
    "redcraftRedzimageUpdatedDEC03_redzimage15AIO-lora.safetensors": {
        "description": "RedCraft Z-Image更新版AIO LoRA，综合增强画质与细节的多功能LoRA，适合通用画质提升、多场景增强",
        "trigger_words": [],
        "base_model": "Z-Image",
    },
    "woman877-zimage.safetensors": {
        "description": "女性人像增强，优化女性面部和整体人像表现，适合女性写真、时尚人像、美妆展示",
        "trigger_words": [],
        "base_model": "Z-Image",
    },
    "z-Image-3D卡通_V1.safetensors": {
        "description": "3D卡通风格V1，将图像转化为3D卡通渲染效果，角色立体可爱，适合卡通动画、趣味视频、儿童内容",
        "trigger_words": ["3d cartoon"],
        "base_model": "Z-Image",
    },
    "z-image 极致氛围光影LORA_V1.0.safetensors": {
        "description": "极致氛围光影LoRA V1.0，强化场景氛围感和光影表现力，光效层次丰富，适合氛围感视频、光影艺术、情绪短片",
        "trigger_words": [],
        "base_model": "Z-Image",
    },
    "z-image-女帝-ben_nd.safetensors": {
        "description": "女帝风格，呈现高贵冷艳的女性形象，气场强大、气质出众，适合女王范人像、时尚大片、角色塑造",
        "trigger_words": [],
        "base_model": "Z-Image",
    },
    "z-image-极致写实.safetensors": {
        "description": "极致写实增强，追求照片级真实感，细节丰富、质感逼真，适合超写实人像、产品展示、高保真视频",
        "trigger_words": [],
        "base_model": "Z-Image",
    },
    "z-image-细节增强v2.safetensors": {
        "description": "细节增强V2，提升画面整体细节表现力，纹理更清晰、层次更丰富，适合细节优化、画质提升、微距效果",
        "trigger_words": [],
        "base_model": "Z-Image",
    },
    "z-image_小情绪_v1.1.safetensors": {
        "description": "小情绪风格V1.1，捕捉细腻微妙的情绪表达，适合情绪短片、文艺人像、情感故事视频",
        "trigger_words": [],
        "base_model": "Z-Image",
    },
}


def _read_safetensors_metadata(file_path: str | Path) -> dict:
    p = Path(file_path)
    if not p.is_file() or p.suffix.lower() != ".safetensors":
        return {}
    try:
        with open(p, "rb") as f:
            header_size_bytes = f.read(8)
            if len(header_size_bytes) < 8:
                return {}
            header_size = struct.unpack("<Q", header_size_bytes)[0]
            if header_size <= 0 or header_size > 100 * 1024 * 1024:
                return {}
            header_json_bytes = f.read(header_size)
            if len(header_json_bytes) < header_size:
                return {}
            header = json.loads(header_json_bytes)
        metadata = header.get("__metadata__", {})
        if not isinstance(metadata, dict):
            return {}
        result: dict = {}
        desc = (
            metadata.get("description")
            or metadata.get("ss_training_comment")
            or metadata.get("modelspec.description")
            or ""
        )
        if isinstance(desc, str) and desc.strip():
            result["description"] = desc.strip()
        triggers = metadata.get("trigger_words") or metadata.get("tags") or ""
        if isinstance(triggers, str) and triggers.strip():
            result["trigger_words"] = [t.strip() for t in triggers.split(",") if t.strip()]
        elif isinstance(triggers, list) and triggers:
            result["trigger_words"] = [str(t).strip() for t in triggers if str(t).strip()]
        base = (
            metadata.get("base_model")
            or metadata.get("ss_base_model_version")
            or metadata.get("modelspec.architecture")
            or ""
        )
        if isinstance(base, str) and base.strip():
            result["base_model"] = base.strip()
        return result
    except Exception:
        return {}


def _load_custom_models_dirs(ctx: ExtensionContext) -> list[Path]:
    dirs: list[Path] = []
    try:
        for candidate in [ctx.config_dir, ctx.config_dir / "config"]:
            launcher_config = candidate / "launcher_config.json"
            if launcher_config.is_file():
                with open(launcher_config, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                for d in cfg.get("model_dirs", []):
                    p = d.get("path", "").strip().strip('"').strip("'")
                    if p:
                        pp = Path(p).expanduser()
                        if pp.is_dir() and pp not in dirs:
                            dirs.append(pp)
                break
    except Exception:
        pass
    try:
        f = ctx.config_dir / "custom_models_dirs.txt"
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip().strip('"').strip("'")
                if not line:
                    continue
                p = Path(line).expanduser()
                if p.is_dir() and p not in dirs:
                    dirs.append(p)
    except Exception:
        pass
    return dirs


_HF_SHARD_RE = re.compile(r"^(model|diffusion_pytorch_model|pytorch_model)-\d+-of-\d+$", re.IGNORECASE)

_NON_LORA_PATTERNS = re.compile(
    r"(?:^|[-_])"
    r"(?:upscaler|vae|text_encoder|tokenizer|scheduler|unet|transformer|controlnet)"
    r"(?:[-_]|$)",
    re.IGNORECASE,
)


def _is_likely_lora(fn: str, dirpath: str) -> bool:
    stem = Path(fn).stem
    if _HF_SHARD_RE.match(stem):
        return False
    if stem.startswith(".") or stem.startswith("__"):
        return False
    name_lower = fn.lower()
    dir_lower = dirpath.lower()
    if "lora" in name_lower or "lora" in dir_lower:
        return True
    if _NON_LORA_PATTERNS.search(stem):
        return False
    size_indicators = ("22b", "19b", "8b", "7b", "3b", "1b", "2.3", "2-3", "distilled", "checkpoint")
    if any(ind in name_lower for ind in size_indicators):
        return False
    return True


def _beautify_lora_name(fn: str) -> str:
    n = Path(fn).stem
    n = n.replace("-", " ").replace("_", " ").strip()
    return n or fn


def _scan_loras_in_dir(root: Path, suffixes: set[str], read_meta: bool = False) -> list[dict]:
    found: list[dict] = []
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                suf = Path(fn).suffix.lower()
                if suf in suffixes:
                    if not _is_likely_lora(fn, dirpath):
                        continue
                    full = Path(dirpath) / fn
                    if full.is_file():
                        try:
                            resolved = str(full.resolve())
                        except OSError:
                            resolved = str(full)
                        display_name = _beautify_lora_name(fn)
                        entry: dict = {"name": display_name, "filename": fn, "path": resolved}
                        if read_meta and suf == ".safetensors":
                            meta = _read_safetensors_metadata(full)
                            if meta:
                                entry.update(meta)
                            known = _LORA_KNOWN_INFO.get(fn)
                            if known:
                                if not entry.get("description"):
                                    entry["description"] = known["description"]
                                if not entry.get("trigger_words") and known.get("trigger_words"):
                                    entry["trigger_words"] = known["trigger_words"]
                                if not entry.get("base_model") and known.get("base_model"):
                                    entry["base_model"] = known["base_model"]
                        found.append(entry)
    except OSError:
        pass
    found.sort(key=lambda x: x["name"].lower())
    return found


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    _LORA_SCAN_SUFFIXES = {".safetensors", ".ckpt", ".pt", ".bin"}

    @app.post("/api/lora-dir")
    async def route_save_lora_dir(request: Request):
        try:
            body = await request.json()
            lora_dir = body.get("loraDir", "").strip()
            settings_file = ctx.config_dir / "settings.json"
            if settings_file.exists():
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {}
            data["lora_dir"] = lora_dir
            data["loraDir"] = lora_dir
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return {"status": "ok", "loraDir": lora_dir}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @app.get("/api/lora-dir")
    async def route_get_lora_dir():
        try:
            settings_file = ctx.config_dir / "settings.json"
            models_root = resolve_models_root(ctx)
            _default_lora_dir = default_lora_dir(ctx)
            payload = {
                "loraDir": "", "modelsDir": str(models_root) if models_root else "",
                "defaultLoraDir": str(_default_lora_dir) if _default_lora_dir else "",
            }
            if settings_file.exists():
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                payload["loraDir"] = data.get("lora_dir", "") or data.get("loraDir", "")
            return payload
        except Exception as e:
            return {"loraDir": "", "error": str(e)}

    @app.get("/api/loras")
    async def route_list_loras(request: Request):
        raw = (request.query_params.get("dir") or "").strip()
        with_meta = request.query_params.get("meta", "").lower() in ("1", "true", "yes")
        if raw.startswith("True"):
            raw = raw[4:].lstrip()
        raw = raw.strip().strip('"').strip("'")

        custom_lora_dir = ""
        if not raw:
            try:
                settings_file = ctx.config_dir / "settings.json"
                if settings_file.exists():
                    with open(settings_file, "r", encoding="utf-8") as f:
                        settings_data = json.load(f)
                    custom_lora_dir = settings_data.get("lora_dir", "") or settings_data.get("loraDir", "")
                    if custom_lora_dir and str(custom_lora_dir).strip():
                        raw = str(custom_lora_dir).strip()
            except Exception as e:
                print(f"[PATCH] Failed to read lora_dir from settings: {e}")

        if raw:
            root = Path(raw).expanduser()
            try:
                root = root.resolve()
            except OSError:
                pass
            if not root.is_dir():
                pass
            else:
                found = _scan_loras_in_dir(root, _LORA_SCAN_SUFFIXES, read_meta=with_meta)
                _default_lora_dir = default_lora_dir(ctx)
                return {
                    "loras": found, "loras_dir": str(root),
                    "models_dir": str(root.parent),
                    "default_loras_dir": str(_default_lora_dir or ""),
                }

        seen_paths: set[str] = set()
        all_loras: list[dict] = []

        scan_dirs: list[Path] = []

        _default_lora_dir = default_lora_dir(ctx)
        if _default_lora_dir and _default_lora_dir.is_dir():
            scan_dirs.append(_default_lora_dir)

        models_root = resolve_models_root(ctx)
        if models_root and models_root.is_dir() and models_root not in scan_dirs:
            loras_sub = models_root / "loras"
            if loras_sub.is_dir() and loras_sub not in scan_dirs:
                scan_dirs.append(loras_sub)

        for cd in _load_custom_models_dirs(ctx):
            if cd.is_dir() and cd not in scan_dirs:
                scan_dirs.append(cd)
                loras_sub = cd / "loras"
                if loras_sub.is_dir() and loras_sub not in scan_dirs:
                    scan_dirs.append(loras_sub)

        for d in scan_dirs:
            for m in _scan_loras_in_dir(d, _LORA_SCAN_SUFFIXES, read_meta=with_meta):
                if m["path"] not in seen_paths:
                    seen_paths.add(m["path"])
                    all_loras.append(m)

        all_loras.sort(key=lambda x: x["name"].lower())
        primary_dir = str(scan_dirs[0]) if scan_dirs else ""
        return {
            "loras": all_loras,
            "loras_dir": primary_dir,
            "models_dir": str(models_root.parent) if models_root else "",
            "default_loras_dir": str(_default_lora_dir or ""),
        }

    @app.get("/api/lora-info")
    async def route_lora_info(request: Request):
        lora_path = (request.query_params.get("path") or "").strip()
        if not lora_path:
            return JSONResponse({"error": "path parameter required"}, status_code=400)
        p = Path(lora_path).expanduser()
        try:
            p = p.resolve()
        except OSError:
            pass
        if not p.is_file():
            return JSONResponse({"error": "file not found"}, status_code=404)
        entry: dict = {"name": p.name, "path": str(p)}
        if p.suffix.lower() == ".safetensors":
            meta = _read_safetensors_metadata(p)
            if meta:
                entry.update(meta)
        return entry
