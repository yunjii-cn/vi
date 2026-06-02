"""Upscale API endpoints for image and video super-resolution.

Endpoints:
  POST /api/upscale/image  - Upscale a single image
  POST /api/upscale/video  - Upscale a video (frame-by-frame)
  GET  /api/upscale/status - Check upscale engine availability

Models:
  - realesrgan: Real-ESRGAN high-fidelity upscale (external, needs pip install)
  - ltx_fast:   LTX built-in spatial upscaler (fast but not faithful)
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from threading import RLock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from extensions._context import ExtensionContext

_REALESRGAN_MODEL = None
_REALESRGAN_LOCK = RLock()


def _patch_torchvision_for_basicsr():
    import sys
    if 'torchvision.transforms.functional_tensor' in sys.modules:
        return
    try:
        import torchvision.transforms._functional_tensor as _ft
        sys.modules['torchvision.transforms.functional_tensor'] = _ft
    except Exception:
        pass

def _get_realesrgan_model(ctx: ExtensionContext, model_name: str = "realesrgan-x4plus", tile_size: int = 0):
    global _REALESRGAN_MODEL
    with _REALESRGAN_LOCK:
        if _REALESRGAN_MODEL is not None and tile_size <= 0:
            return _REALESRGAN_MODEL
        if _REALESRGAN_MODEL is not None and tile_size > 0:
            try:
                _REALESRGAN_MODEL.tile_size = tile_size
                return _REALESRGAN_MODEL
            except Exception:
                pass
        _patch_torchvision_for_basicsr()
        from realesrgan import RealESRGANer
        from basicsr.archs.rrdbnet_arch import RRDBNet
        models_root = Path(ctx.config_dir) / "models" / "realesrgan"
        models_root.mkdir(parents=True, exist_ok=True)
        model_map = {
            "realesrgan-x4plus": (RRDBNet, 4, "realesrgan_x4plus.pth", "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"),
            "realesrgan-x4plus-anime": (RRDBNet, 4, "realesrgan_x4plus_anime_6B.pth", "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth"),
            "realesrgan-x2plus": (RRDBNet, 2, "realesrgan_x2plus.pth", "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth"),
        }
        if model_name not in model_map:
            model_name = "realesrgan-x4plus"
        arch_class, scale, filename, url = model_map[model_name]
        model_path = models_root / filename
        if not model_path.exists():
            import urllib.request
            print(f"[upscale] Downloading {model_name} -> {model_path}")
            urllib.request.urlretrieve(url, str(model_path))
        num_block = 6 if "anime" in model_name else 23
        import inspect
        init_params = set(inspect.signature(arch_class.__init__).parameters.keys())
        if "num_feat" in init_params:
            net = arch_class(num_in_ch=3, num_out_ch=3, scale=scale, num_feat=64, num_block=num_block)
        elif "nf" in init_params:
            net = arch_class(num_in_ch=3, num_out_ch=3, scale=scale, nf=64, nb=num_block)
        else:
            net = arch_class(num_in_ch=3, num_out_ch=3, scale=scale)
        import torch
        use_cuda = torch.cuda.is_available()
        device = torch.device("cuda" if use_cuda else "cpu")
        if tile_size <= 0:
            tile_size = 0
            if use_cuda:
                try:
                    vram_mb = torch.cuda.get_device_properties(0).total_mem / (1024 * 1024)
                    if vram_mb < 6000:
                        tile_size = 256
                    elif vram_mb < 10000:
                        tile_size = 400
                    else:
                        tile_size = 512
                except Exception:
                    tile_size = 400
        print(f"[upscale] Device: {'CUDA: ' + torch.cuda.get_device_name(0) if use_cuda else 'CPU'}, tile={tile_size}, half={use_cuda}")
        _REALESRGAN_MODEL = RealESRGANer(
            scale=scale,
            model_path=str(model_path),
            dni_weight=None,
            model=net,
            tile=tile_size,
            tile_pad=10,
            pre_pad=0,
            half=use_cuda,
            device=device,
        )
        return _REALESRGAN_MODEL


def _subprocess_run(cmd, **kwargs):
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | 0x00000008
    return subprocess.run(cmd, startupinfo=si, creationflags=flags, **kwargs)


def _compute_upscale_params(src_w: int, src_h: int, scale: int, target_w: int | None, target_h: int | None, resize_mode: str) -> tuple[int, int, int, str]:
    if target_w is None and target_h is None:
        return src_w * scale, src_h * scale, scale, "fit"
    tw = target_w if target_w is not None and target_w > 0 else src_w
    th = target_h if target_h is not None and target_h > 0 else src_h
    if tw <= src_w and th <= src_h:
        return src_w * scale, src_h * scale, scale, "fit"
    if resize_mode == "stretch":
        if (target_w is not None and target_w > 0) and (target_h is not None and target_h > 0):
            needed_scale = max(2, -(-max(tw, th) // max(src_w, src_h)))
            out_w, out_h = src_w * needed_scale, src_h * needed_scale
            return out_w, out_h, needed_scale, "stretch"
        needed_scale = max(2, -(-max(tw, th) // max(src_w, src_h)))
        out_w, out_h = src_w * needed_scale, src_h * needed_scale
        return out_w, out_h, needed_scale, "fit"
    if resize_mode == "crop":
        ratio = max(tw / src_w, th / src_h)
        needed_scale = max(2, -(-int(ratio * max(src_w, src_h)) // max(src_w, src_h)))
        out_w, out_h = src_w * needed_scale, src_h * needed_scale
        return out_w, out_h, needed_scale, "crop"
    ratio = min(tw / src_w, th / src_h)
    needed_scale = max(2, -(-int(ratio * max(src_w, src_h)) // max(src_w, src_h)))
    out_w, out_h = src_w * needed_scale, src_h * needed_scale
    return out_w, out_h, needed_scale, "fit"


def _upscale_video_lanczos(ctx, input_path: str, output_path: str, scale: int, target_w: int | None, target_h: int | None, resize_mode: str, target_fps: int | None = None) -> dict:
    import re
    t0 = time.perf_counter()

    ffmpeg = _find_ffmpeg(ctx)
    probe = _subprocess_run([ffmpeg, "-i", input_path], capture_output=True, text=True, timeout=30)
    m_size = re.search(r",\s*(\d{2,5})x(\d{2,5})\s*,", probe.stderr or "")
    if m_size and int(m_size.group(1)) > 0 and int(m_size.group(2)) > 0:
        src_w, src_h = int(m_size.group(1)), int(m_size.group(2))
    else:
        m_size2 = re.search(r"(\d{3,5})x(\d{3,5})", probe.stderr or "")
        if m_size2 and int(m_size2.group(1)) > 0 and int(m_size2.group(2)) > 0:
            src_w, src_h = int(m_size2.group(1)), int(m_size2.group(2))
        else:
            return {"error": "无法探测源视频尺寸"}

    m_fps = re.search(r"(\d+(?:\.\d+)?)\s*tbr", probe.stderr or "")
    if not m_fps:
        m_fps = re.search(r"(\d+(?:\.\d+)?)\s*fps", probe.stderr or "")
    src_fps = float(m_fps.group(1)) if m_fps else 24.0

    has_audio = bool(re.search(r"Stream\s+#0:\d+\(?\w*\)?:\s*Audio", probe.stderr or ""))

    total_frames = 0
    duration = 0.0
    m_dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr or "")
    if m_dur:
        _h, _m, _s = int(m_dur.group(1)), int(m_dur.group(2)), float(m_dur.group(3))
        duration = _h * 3600 + _m * 60 + _s
        total_frames = int(duration * src_fps)

    if target_w is not None and target_h is not None and target_w > 0 and target_h > 0:
        final_w, final_h = target_w, target_h
    else:
        final_w, final_h = src_w * scale, src_h * scale

    final_w = final_w if final_w % 2 == 0 else final_w - 1
    final_h = final_h if final_h % 2 == 0 else final_h - 1

    out_fps = src_fps
    fps_filter = ""
    if target_fps is not None and target_fps > 0 and abs(target_fps - src_fps) > 0.5:
        out_fps = float(target_fps)
        if target_fps > src_fps:
            fps_filter = f",minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1"
        else:
            fps_filter = f",fps={target_fps}"

    print(f"[upscale] lanczos video: src={src_w}x{src_h}, target={final_w}x{final_h}, src_fps={src_fps}, out_fps={out_fps}, audio={has_audio}")

    ctx._upscale_progress_state.update({"phase": "upscaling", "progress": 30, "current_step": 1, "total_steps": 2})

    vf_parts = f"scale={final_w}:{final_h}:flags=lanczos{fps_filter}"
    cmd = [ffmpeg, "-y", "-i", input_path,
           "-vf", vf_parts,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
           "-r", str(out_fps)]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd.append(output_path)

    r = _subprocess_run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        return {"error": f"ffmpeg lanczos failed: {r.stderr[:500]}"}

    t1 = time.perf_counter()
    ctx._upscale_progress_state.update({"phase": "complete", "progress": 100, "current_step": 2, "total_steps": 2})

    print(f"[upscale] lanczos video completed: {output_path}, elapsed={round(t1-t0,2)}s")

    return {
        "width": final_w,
        "height": final_h,
        "frames": total_frames,
        "duration": round(duration, 2),
        "elapsed": round(t1 - t0, 2),
        "resize_mode": resize_mode or "fit",
        "original_size": f"{src_w}x{src_h}",
        "output_size": f"{final_w}x{final_h}",
        "engine": "ltx_fast",
        "original_fps": src_fps,
        "output_fps": out_fps,
    }


def _resize_output(img_array: "np.ndarray", target_w: int, target_h: int, resize_mode: str, src_w: int, src_h: int, out_w: int, out_h: int) -> "np.ndarray":
    from PIL import Image
    result = Image.fromarray(img_array)
    has_target = target_w is not None and target_h is not None and target_w > 0 and target_h > 0
    if not has_target:
        return result
    if resize_mode == "stretch":
        result = result.resize((target_w, target_h), Image.LANCZOS)
    elif resize_mode == "crop":
        left = (out_w - target_w) // 2
        top = (out_h - target_h) // 2
        if left < 0:
            left = 0
        if top < 0:
            top = 0
        right = left + target_w
        bottom = top + target_h
        if right > out_w:
            right = out_w
        if bottom > out_h:
            bottom = out_h
        result = result.crop((left, top, right, bottom))
        if result.size != (target_w, target_h):
            result = result.resize((target_w, target_h), Image.LANCZOS)
    else:
        ratio = min(target_w / out_w, target_h / out_h)
        new_w = int(out_w * ratio)
        new_h = int(out_h * ratio)
        result = result.resize((new_w, new_h), Image.LANCZOS)
        if new_w != target_w or new_h != target_h:
            canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
            paste_x = (target_w - new_w) // 2
            paste_y = (target_h - new_h) // 2
            canvas.paste(result, (paste_x, paste_y))
            result = canvas
    return result


def _upscale_image_realesrgan(ctx: ExtensionContext, input_path: str, output_path: str, model_name: str, scale: int, denoise: float, target_w: int | None = None, target_h: int | None = None, resize_mode: str = "fit", tile_size: int = 0) -> dict:
    import numpy as np
    from PIL import Image
    ctx._upscale_progress_state.update({"phase": "upscaling", "progress": 20, "current_step": 0, "total_steps": 4})
    img = Image.open(input_path).convert("RGB")
    src_w, src_h = img.size
    ctx._upscale_progress_state.update({"progress": 30, "current_step": 1})
    out_w, out_h, actual_scale, mode_used = _compute_upscale_params(src_w, src_h, scale, target_w, target_h, resize_mode)
    ctx._upscale_progress_state.update({"progress": 40, "current_step": 2})
    model = _get_realesrgan_model(ctx, model_name, tile_size=tile_size)
    ctx._upscale_progress_state.update({"progress": 55, "current_step": 3})
    t0 = time.perf_counter()
    output, _ = model.enhance(np.array(img), outscale=actual_scale, alpha_upsampler="realesrgan")
    t1 = time.perf_counter()
    ctx._upscale_progress_state.update({"progress": 75, "current_step": 4, "total_steps": 5})
    if target_w is not None and target_h is not None and target_w > 0 and target_h > 0:
        result_img = _resize_output(output, target_w, target_h, resize_mode, src_w, src_h, out_w, out_h)
    else:
        result_img = Image.fromarray(output)
    ctx._upscale_progress_state.update({"progress": 90, "current_step": 5, "total_steps": 6})
    final_w, final_h = result_img.size
    result_img.save(output_path, quality=95)
    ctx._upscale_progress_state.update({"progress": 95, "current_step": 6, "total_steps": 6})
    return {
        "width": final_w,
        "height": final_h,
        "elapsed": round(t1 - t0, 2),
        "resize_mode": mode_used,
        "original_size": f"{src_w}x{src_h}",
        "output_size": f"{final_w}x{final_h}",
    }


def _upscale_video_realesrgan(ctx: ExtensionContext, input_path: str, output_path: str, model_name: str, scale: int, denoise: float, target_w: int | None = None, target_h: int | None = None, resize_mode: str = "fit", tile_size: int = 0, target_fps: int | None = None) -> dict:
    import numpy as np
    from PIL import Image

    cap_dir = ctx.get_output_path() / f"_upscale_frames_{uuid.uuid4().hex[:8]}"
    cap_dir.mkdir(parents=True, exist_ok=True)
    out_dir = ctx.get_output_path() / f"_upscale_out_{uuid.uuid4().hex[:8]}"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        ffmpeg = _find_ffmpeg(ctx)
        import re
        probe = _subprocess_run([ffmpeg, "-i", input_path], capture_output=True, text=True, timeout=30)
        src_fps = 24.0
        duration = 0.0
        for line in (probe.stderr or "").split("\n"):
            if "Video:" in line and "fps" in line:
                m = re.search(r"(\d+(?:\.\d+)?)\s*fps", line)
                if m:
                    src_fps = float(m.group(1))
        m_dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr or "")
        if m_dur:
            _h, _m, _s = int(m_dur.group(1)), int(m_dur.group(2)), float(m_dur.group(3))
            duration = _h * 3600 + _m * 60 + _s

        cap_pattern = str(cap_dir / "frame_%06d.png")
        cap_cmd = [ffmpeg, "-y", "-i", input_path]
        if target_fps is not None and target_fps > 0 and target_fps < src_fps:
            cap_cmd += ["-r", str(target_fps)]
        cap_cmd += ["-q:v", "2", cap_pattern]
        r1 = _subprocess_run(cap_cmd, capture_output=True, text=True, timeout=300)
        if r1.returncode != 0:
            return {"error": f"ffmpeg extract failed: {r1.stderr[:300]}"}

        frames = sorted(cap_dir.glob("frame_*.png"))
        if not frames:
            return {"error": "No frames extracted from video"}

        total_frames = len(frames)
        first_img = Image.open(frames[0]).convert("RGB")
        src_w, src_h = first_img.size
        out_w, out_h, actual_scale, mode_used = _compute_upscale_params(src_w, src_h, scale, target_w, target_h, resize_mode)
        print(f"[upscale] video params: src={src_w}x{src_h}, target={target_w}x{target_h}, out={out_w}x{out_h}, scale={actual_scale}, mode={mode_used}, frames={total_frames}")

        model = _get_realesrgan_model(ctx, model_name, tile_size=tile_size)
        t0 = time.perf_counter()
        for i, frame_path in enumerate(frames):
            img = Image.open(frame_path).convert("RGB")
            output, _ = model.enhance(np.array(img), outscale=actual_scale, alpha_upsampler="realesrgan")
            if target_w is not None and target_h is not None and target_w > 0 and target_h > 0:
                result_img = _resize_output(output, target_w, target_h, resize_mode, src_w, src_h, out_w, out_h)
            else:
                result_img = Image.fromarray(output)
            result_img.save(str(out_dir / f"frame_{i+1:06d}.png"), quality=95)
            if i % 10 == 0:
                print(f"[upscale] Processed {i+1}/{total_frames} frames")
        t1 = time.perf_counter()

        final_w, final_h = result_img.size

        out_fps = src_fps
        fps_filter = ""
        if target_fps is not None and target_fps > 0 and target_fps < src_fps:
            out_fps = float(target_fps)
        elif target_fps is not None and target_fps > 0 and target_fps > src_fps:
            out_fps = float(target_fps)
            fps_filter = f",minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1"

        out_pattern = str(out_dir / "frame_%06d.png")
        final_w = final_w if final_w % 2 == 0 else final_w - 1
        final_h = final_h if final_h % 2 == 0 else final_h - 1
        input_fps_for_encode = out_fps if target_fps is not None and target_fps > 0 and target_fps < src_fps else src_fps
        print(f"[upscale] encoding video: out_fps={out_fps}, pattern={out_pattern}, output={output_path}, final_size={final_w}x{final_h}")
        r2 = _subprocess_run(
            [ffmpeg, "-y", "-framerate", str(input_fps_for_encode), "-i", out_pattern,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
             "-vf", f"scale={final_w}:{final_h}{fps_filter}",
             "-r", str(out_fps),
             output_path],
            capture_output=True, text=True, timeout=300,
        )
        if r2.returncode != 0:
            print(f"[upscale] ffmpeg encode FAILED: {r2.stderr[:500]}")
            return {"error": f"ffmpeg encode failed: {r2.stderr[:300]}"}
        print(f"[upscale] video encoded successfully: {output_path}")

        return {
            "width": final_w,
            "height": final_h,
            "frames": total_frames,
            "duration": round(duration, 2),
            "elapsed": round(t1 - t0, 2),
            "resize_mode": mode_used,
            "original_size": f"{src_w}x{src_h}",
            "output_size": f"{final_w}x{final_h}",
            "original_fps": src_fps,
            "output_fps": out_fps,
        }
    finally:
        import shutil
        for d in [cap_dir, out_dir]:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass


def _find_ffmpeg(ctx: ExtensionContext) -> str:
    from extensions._utils import find_ffmpeg_binary
    return find_ffmpeg_binary(ctx) or "ffmpeg"


def _upscale_video_realesrgan_with_progress(ctx: ExtensionContext, input_path: str, output_path: str, model_name: str, scale: int, denoise: float, target_w: int | None = None, target_h: int | None = None, resize_mode: str = "fit", tile_size: int = 0, target_fps: int | None = None) -> dict:
    import numpy as np
    from PIL import Image

    cap_dir = ctx.get_output_path() / f"_upscale_frames_{uuid.uuid4().hex[:8]}"
    cap_dir.mkdir(parents=True, exist_ok=True)
    out_dir = ctx.get_output_path() / f"_upscale_out_{uuid.uuid4().hex[:8]}"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        ffmpeg = _find_ffmpeg(ctx)
        import re
        probe = _subprocess_run([ffmpeg, "-i", input_path], capture_output=True, text=True, timeout=30)
        src_fps = 24.0
        duration = 0.0
        for line in (probe.stderr or "").split("\n"):
            if "Video:" in line and "fps" in line:
                m = re.search(r"(\d+(?:\.\d+)?)\s*fps", line)
                if m:
                    src_fps = float(m.group(1))
        m_dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr or "")
        if m_dur:
            _h, _m, _s = int(m_dur.group(1)), int(m_dur.group(2)), float(m_dur.group(3))
            duration = _h * 3600 + _m * 60 + _s

        cap_pattern = str(cap_dir / "frame_%06d.png")
        cap_cmd = [ffmpeg, "-y", "-i", input_path]
        if target_fps is not None and target_fps > 0 and target_fps < src_fps:
            cap_cmd += ["-r", str(target_fps)]
        cap_cmd += ["-q:v", "2", cap_pattern]
        r1 = _subprocess_run(cap_cmd, capture_output=True, text=True, timeout=300)
        if r1.returncode != 0:
            return {"error": f"ffmpeg extract failed: {r1.stderr[:300]}"}

        frames = sorted(cap_dir.glob("frame_*.png"))
        if not frames:
            return {"error": "No frames extracted from video"}

        total_frames = len(frames)
        ctx._upscale_progress_state.update({"phase": "upscaling", "progress": 10, "current_step": 0, "total_steps": total_frames})

        first_img = Image.open(frames[0]).convert("RGB")
        src_w, src_h = first_img.size
        out_w, out_h, actual_scale, mode_used = _compute_upscale_params(src_w, src_h, scale, target_w, target_h, resize_mode)
        print(f"[upscale] video_with_progress params: src={src_w}x{src_h}, target={target_w}x{target_h}, out={out_w}x{out_h}, scale={actual_scale}, mode={mode_used}, frames={total_frames}, src_fps={src_fps}, target_fps={target_fps}")

        model = _get_realesrgan_model(ctx, model_name, tile_size=tile_size)
        t0 = time.perf_counter()
        for i, frame_path in enumerate(frames):
            img = Image.open(frame_path).convert("RGB")
            output, _ = model.enhance(np.array(img), outscale=actual_scale, alpha_upsampler="realesrgan")
            if target_w is not None and target_h is not None and target_w > 0 and target_h > 0:
                result_img = _resize_output(output, target_w, target_h, resize_mode, src_w, src_h, out_w, out_h)
            else:
                result_img = Image.fromarray(output)
            result_img.save(str(out_dir / f"frame_{i+1:06d}.png"), quality=95)
            pct = int(10 + 80 * (i + 1) / total_frames)
            ctx._upscale_progress_state.update({"progress": pct, "current_step": i + 1, "total_steps": total_frames})
            if i % 10 == 0:
                print(f"[upscale] Processed {i+1}/{total_frames} frames ({pct}%)")
        t1 = time.perf_counter()

        final_w, final_h = result_img.size

        ctx._upscale_progress_state.update({"phase": "upscaling", "progress": 92, "current_step": total_frames, "total_steps": total_frames})

        out_fps = src_fps
        fps_filter = ""
        if target_fps is not None and target_fps > 0 and target_fps < src_fps:
            out_fps = float(target_fps)
        elif target_fps is not None and target_fps > 0 and target_fps > src_fps:
            out_fps = float(target_fps)
            fps_filter = f",minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1"

        out_pattern = str(out_dir / "frame_%06d.png")
        final_w = final_w if final_w % 2 == 0 else final_w - 1
        final_h = final_h if final_h % 2 == 0 else final_h - 1
        print(f"[upscale] encoding video (progress): out_fps={out_fps}, pattern={out_pattern}, output={output_path}, final_size={final_w}x{final_h}")
        input_fps_for_encode = out_fps if target_fps is not None and target_fps > 0 and target_fps < src_fps else src_fps
        r2 = _subprocess_run(
            [ffmpeg, "-y", "-framerate", str(input_fps_for_encode), "-i", out_pattern,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
             "-vf", f"scale={final_w}:{final_h}{fps_filter}",
             "-r", str(out_fps),
             output_path],
            capture_output=True, text=True, timeout=300,
        )
        if r2.returncode != 0:
            print(f"[upscale] ffmpeg encode FAILED (progress): {r2.stderr[:500]}")
            return {"error": f"ffmpeg encode failed: {r2.stderr[:300]}"}
        print(f"[upscale] video encoded successfully (progress): {output_path}")

        return {
            "width": final_w,
            "height": final_h,
            "frames": total_frames,
            "duration": round(duration, 2),
            "elapsed": round(t1 - t0, 2),
            "resize_mode": mode_used,
            "original_size": f"{src_w}x{src_h}",
            "output_size": f"{final_w}x{final_h}",
            "original_fps": src_fps,
            "output_fps": out_fps,
        }
    finally:
        import shutil
        for d in [cap_dir, out_dir]:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass


def install(app: FastAPI, ctx: ExtensionContext) -> None:

    ctx._upscale_progress_state = {"phase": "", "progress": 0, "current_step": None, "total_steps": None}

    def _execute_upscale_from_queue(endpoint: str, payload: dict) -> dict:
        input_path = payload.get("inputPath", "").strip()
        engine = payload.get("engine", "realesrgan")
        scale = int(payload.get("scale", 2))
        if scale < 2:
            scale = 2
        if scale > 4:
            scale = 4
        model_name = payload.get("model", "realesrgan-x4plus")
        denoise = float(payload.get("denoise", 0.5))
        target_w = payload.get("targetWidth")
        target_h = payload.get("targetHeight")
        if target_w is not None:
            target_w = int(target_w)
        if target_h is not None:
            target_h = int(target_h)
        resize_mode = payload.get("resizeMode", "fit")
        tile_size = int(payload.get("tileSize", 0))
        keep_original_ratio = bool(payload.get("keepOriginalRatio", False))
        target_fps_raw = payload.get("targetFps")
        target_fps = int(target_fps_raw) if target_fps_raw is not None and int(target_fps_raw) > 0 else None

        print(f"[upscale] payload: endpoint={endpoint}, engine={engine}, scale={scale}, model={model_name}, target_w={target_w}, target_h={target_h}, keep_ratio={keep_original_ratio}, resize_mode={resize_mode}, tile={tile_size}")

        if keep_original_ratio and target_h is not None and target_h > 0:
            from PIL import Image as _PILImage
            try:
                if endpoint == "/api/upscale/image":
                    _src = _PILImage.open(input_path)
                    src_w, src_h = _src.size
                    _src.close()
                else:
                    import re
                    _ff = _find_ffmpeg(ctx)
                    _pr = _subprocess_run([_ff, "-i", input_path], capture_output=True, text=True, timeout=30)
                    _m = re.search(r",\s*(\d{2,5})x(\d{2,5})\s*,", _pr.stderr or "")
                    if _m and int(_m.group(1)) > 0 and int(_m.group(2)) > 0:
                        src_w, src_h = int(_m.group(1)), int(_m.group(2))
                    else:
                        _m2 = re.search(r"(\d{3,5})x(\d{3,5})", _pr.stderr or "")
                        if _m2 and int(_m2.group(1)) > 0 and int(_m2.group(2)) > 0:
                            src_w, src_h = int(_m2.group(1)), int(_m2.group(2))
                        else:
                            src_w, src_h = 1920, 1080
                if src_w <= 0 or src_h <= 0:
                    src_w, src_h = 1920, 1080
                short_side = target_h
                if src_w >= src_h:
                    target_w = int(short_side * src_w / src_h)
                    target_h = short_side
                else:
                    target_w = short_side
                    target_h = int(short_side * src_h / src_w)
                resize_mode = "fit"
                print(f"[upscale] keepOriginalRatio: src={src_w}x{src_h}, computed target={target_w}x{target_h}")
            except Exception as _ex:
                print(f"[upscale] keepOriginalRatio failed: {_ex}")
                target_w = None
                target_h = None

        ctx._upscale_progress_state.update({"phase": "loading_model", "progress": 5, "current_step": None, "total_steps": None})

        if endpoint == "/api/upscale/image":
            output_dir = ctx.get_output_path()
            output_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(input_path).suffix or ".png"
            _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            if target_w is not None and target_h is not None and target_w > 0 and target_h > 0:
                output_path = str(output_dir / f"{_ts}_upscaled_{target_w}x{target_h}{ext}")
            else:
                output_path = str(output_dir / f"{_ts}_upscaled_{scale}x{ext}")
            if engine == "ltx_fast":
                raise RuntimeError("LTX快速放大仅支持视频，图片放大请使用Real-ESRGAN")
            result = _upscale_image_realesrgan(ctx, input_path, output_path, model_name, scale, denoise, target_w, target_h, resize_mode, tile_size)
            ctx._upscale_progress_state.update({"phase": "complete", "progress": 100, "current_step": None, "total_steps": None})
            if "error" in result:
                raise RuntimeError(result["error"])
            result["outputPath"] = output_path
            result["engine"] = engine
            result["image_paths"] = [output_path]
            return result
        else:
            output_dir = ctx.get_output_path()
            output_dir.mkdir(parents=True, exist_ok=True)
            _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            if target_w is not None and target_h is not None and target_w > 0 and target_h > 0:
                output_path = str(output_dir / f"{_ts}_upscaled_{target_w}x{target_h}.mp4")
            else:
                output_path = str(output_dir / f"{_ts}_upscaled_{scale}x.mp4")
            print(f"[upscale] starting video upscale: input={input_path}, output={output_path}, engine={engine}, target={target_w}x{target_h}, scale={scale}, model={model_name}")
            ctx._upscale_progress_state.update({"phase": "upscaling", "progress": 10, "current_step": 0, "total_steps": None})

            if engine == "ltx_fast":
                result = _upscale_video_lanczos(ctx, input_path, output_path, scale, target_w, target_h, resize_mode, target_fps)
            else:
                result = _upscale_video_realesrgan_with_progress(ctx, input_path, output_path, model_name, scale, denoise, target_w, target_h, resize_mode, tile_size, target_fps)

            ctx._upscale_progress_state.update({"phase": "complete", "progress": 100, "current_step": None, "total_steps": None})
            if "error" in result:
                print(f"[upscale] video upscale FAILED: {result['error']}")
                raise RuntimeError(result["error"])
            if not Path(output_path).is_file():
                print(f"[upscale] video upscale WARNING: output file not found: {output_path}")
                raise RuntimeError(f"Video file was not created: {output_path}")
            print(f"[upscale] video upscale completed: output={output_path}, size={Path(output_path).stat().st_size}, result_keys={list(result.keys())}")
            result["outputPath"] = output_path
            result["engine"] = engine
            result["video_path"] = output_path
            return result

    ctx._execute_upscale_from_queue = _execute_upscale_from_queue

    @app.get("/api/upscale/status")
    async def route_upscale_status():
        realesrgan_available = False
        try:
            import importlib.metadata
            importlib.metadata.version("realesrgan")
            realesrgan_available = True
        except Exception:
            try:
                import importlib.util
                realesrgan_available = importlib.util.find_spec("realesrgan") is not None
            except Exception:
                pass
        ltx_upscaler_available = False
        try:
            upsampler_path = ctx.handler.pipelines.resolve_model("upsampler")
            ltx_upscaler_available = upsampler_path is not None and Path(str(upsampler_path)).exists()
        except Exception:
            pass
        gpu_info = None
        try:
            import torch
            if torch.cuda.is_available():
                gpu_info = {
                    "device": "cuda",
                    "name": torch.cuda.get_device_name(0),
                    "vram_mb": round(torch.cuda.get_device_properties(0).total_mem / (1024 * 1024)),
                }
            else:
                gpu_info = {"device": "cpu", "name": "CPU", "vram_mb": 0}
        except Exception:
            gpu_info = {"device": "unknown", "name": "unknown", "vram_mb": 0}
        return {
            "realesrgan": realesrgan_available,
            "ltx_upscaler": ltx_upscaler_available,
            "gpu": gpu_info,
        }

    @app.post("/api/upscale/image")
    async def route_upscale_image(request: Request):
        try:
            data = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

        input_path = data.get("inputPath", "").strip()
        if not input_path or not Path(input_path).is_file():
            return JSONResponse(status_code=400, content={"error": "输入图片路径无效"})

        engine = data.get("engine", "realesrgan")
        scale = int(data.get("scale", 2))
        if scale < 2:
            scale = 2
        if scale > 4:
            scale = 4
        model_name = data.get("model", "realesrgan-x4plus")
        denoise = float(data.get("denoise", 0.5))
        target_w = data.get("targetWidth")
        target_h = data.get("targetHeight")
        if target_w is not None:
            target_w = int(target_w)
        if target_h is not None:
            target_h = int(target_h)
        resize_mode = data.get("resizeMode", "fit")
        tile_size = int(data.get("tileSize", 0))
        target_fps_raw = data.get("targetFps")
        target_fps = int(target_fps_raw) if target_fps_raw is not None and int(target_fps_raw) > 0 else None

        output_dir = ctx.get_output_path()
        output_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(input_path).suffix or ".png"
        _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if target_w is not None and target_h is not None and target_w > 0 and target_h > 0:
            output_path = str(output_dir / f"{_ts}_upscaled_{target_w}x{target_h}{ext}")
        else:
            output_path = str(output_dir / f"{_ts}_upscaled_{scale}x{ext}")

        if engine == "ltx_fast":
            return JSONResponse(status_code=410, content={"error": "LTX快速放大仅支持视频，图片放大请使用Real-ESRGAN"})

        try:
            from starlette.concurrency import run_in_threadpool
            result = await run_in_threadpool(
                _upscale_image_realesrgan, ctx, input_path, output_path, model_name, scale, denoise, target_w, target_h, resize_mode, tile_size
            )
            if "error" in result:
                return JSONResponse(status_code=500, content={"error": result["error"]})
            result["outputPath"] = output_path
            result["engine"] = engine
            return result
        except ImportError:
            return JSONResponse(status_code=501, content={"error": "Real-ESRGAN未安装，请运行: uv pip install realesrgan basicsr"})
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.post("/api/upscale/video")
    async def route_upscale_video(request: Request):
        try:
            data = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

        input_path = data.get("inputPath", "").strip()
        if not input_path or not Path(input_path).is_file():
            return JSONResponse(status_code=400, content={"error": "输入视频路径无效"})

        engine = data.get("engine", "realesrgan")
        scale = int(data.get("scale", 2))
        if scale < 2:
            scale = 2
        if scale > 4:
            scale = 4
        model_name = data.get("model", "realesrgan-x4plus")
        denoise = float(data.get("denoise", 0.5))
        target_w = data.get("targetWidth")
        target_h = data.get("targetHeight")
        if target_w is not None:
            target_w = int(target_w)
        if target_h is not None:
            target_h = int(target_h)
        resize_mode = data.get("resizeMode", "fit")
        tile_size = int(data.get("tileSize", 0))
        target_fps_raw = data.get("targetFps")
        target_fps = int(target_fps_raw) if target_fps_raw is not None and int(target_fps_raw) > 0 else None

        output_dir = ctx.get_output_path()
        output_dir.mkdir(parents=True, exist_ok=True)
        _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if target_w is not None and target_h is not None and target_w > 0 and target_h > 0:
            output_path = str(output_dir / f"{_ts}_upscaled_{target_w}x{target_h}.mp4")
        else:
            output_path = str(output_dir / f"{_ts}_upscaled_{scale}x.mp4")

        if engine == "ltx_fast":
            try:
                from starlette.concurrency import run_in_threadpool
                result = await run_in_threadpool(
                    _upscale_video_lanczos, ctx, input_path, output_path, scale, target_w, target_h, resize_mode, target_fps
                )
                if "error" in result:
                    return JSONResponse(status_code=500, content={"error": result["error"]})
                result["outputPath"] = output_path
                result["engine"] = engine
                result["video_path"] = output_path
                return result
            except Exception as e:
                return JSONResponse(status_code=500, content={"error": str(e)})

        try:
            from starlette.concurrency import run_in_threadpool
            result = await run_in_threadpool(
                _upscale_video_realesrgan, ctx, input_path, output_path, model_name, scale, denoise, target_w, target_h, resize_mode, tile_size, target_fps
            )
            if "error" in result:
                return JSONResponse(status_code=500, content={"error": result["error"]})
            result["outputPath"] = output_path
            result["engine"] = engine
            result["video_path"] = output_path
            return result
        except ImportError:
            return JSONResponse(status_code=501, content={"error": "Real-ESRGAN未安装，请运行: uv pip install realesrgan basicsr"})
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
