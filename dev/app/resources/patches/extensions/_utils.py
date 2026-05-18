"""Shared utilities used by multiple extensions."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from extensions._context import ExtensionContext


def resolve_models_root(ctx: ExtensionContext) -> Path | None:
    try:
        md = getattr(ctx.handler.pipelines, "models_dir", None)
        if md and str(md).strip():
            return Path(str(md)).expanduser().resolve()
    except Exception:
        pass
    return None


def default_lora_dir(ctx: ExtensionContext) -> Path | None:
    root = resolve_models_root(ctx)
    return root / "loras" if root else None


def find_ffmpeg_binary(ctx: ExtensionContext) -> str | None:
    def _ok(p: str | None) -> str | None:
        if not p:
            return None
        p = os.path.normpath(os.path.expandvars(str(p).strip().strip('"')))
        return p if os.path.isfile(p) else None

    for env_key in ("LTX_FFMPEG_PATH", "FFMPEG_PATH"):
        hit = _ok(os.environ.get(env_key))
        if hit:
            return hit

    try:
        pref = ctx.config_dir / "ffmpeg_path.txt"
        if pref.is_file():
            line = pref.read_text(encoding="utf-8").splitlines()[0].strip()
            hit = _ok(line)
            if hit:
                return hit
    except Exception:
        pass

    try:
        import imageio_ffmpeg
        hit = _ok(imageio_ffmpeg.get_ffmpeg_exe())
        if hit:
            return hit
    except Exception:
        pass

    for name in ("ffmpeg", "ffmpeg.exe"):
        hit = _ok(shutil.which(name))
        if hit:
            return hit

    path_env = os.environ.get("PATH", "") or os.environ.get("Path", "")
    for folder in path_env.split(os.pathsep):
        folder = folder.strip().strip('"')
        if not folder:
            continue
        for exe in ("ffmpeg.exe", "ffmpeg"):
            hit = _ok(os.path.join(folder, exe))
            if hit:
                return hit

    localappdata = os.environ.get("LOCALAPPDATA", "") or ""
    programfiles = os.environ.get("ProgramFiles", r"C:\Program Files")
    programfiles_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    userprofile = os.environ.get("USERPROFILE", "") or ""

    static_candidates: list[str] = [
        os.path.join(os.path.dirname(sys.executable), "ffmpeg.exe"),
        os.path.join(os.path.dirname(sys.executable), "ffmpeg"),
        os.path.join(localappdata, "LTXDesktop", "ffmpeg.exe"),
        os.path.join(programfiles, "LTX Desktop", "ffmpeg.exe"),
        os.path.join(programfiles, "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(programfiles_x86, "ffmpeg", "bin", "ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        os.path.join(userprofile, "scoop", "shims", "ffmpeg.exe"),
        os.path.join(userprofile, "scoop", "apps", "ffmpeg", "current", "bin", "ffmpeg.exe"),
        r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
    ]
    for c in static_candidates:
        hit = _ok(c)
        if hit:
            return hit

    try:
        wg = os.path.join(localappdata, "Microsoft", "WinGet", "Packages")
        if os.path.isdir(wg):
            for root, _dirs, files in os.walk(wg):
                if "ffmpeg.exe" in files:
                    hit = _ok(os.path.join(root, "ffmpeg.exe"))
                    if hit:
                        return hit
                depth = root[len(wg):].count(os.sep)
                if depth > 6:
                    _dirs[:] = []
    except Exception:
        pass

    return None


def ensure_a2v_stereo_audio(audio_path: str, temp_paths: list[str]) -> str:
    try:
        import numpy as np
        import soundfile as sf

        data, sample_rate = sf.read(audio_path, always_2d=True, dtype="float32")
        if data.shape[1] >= 2:
            return audio_path

        stereo = np.repeat(data[:, :1], 2, axis=1)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        sf.write(tmp, stereo, sample_rate, subtype="PCM_16")
        temp_paths.append(tmp)
        return tmp
    except Exception as exc:
        print(f"[PATCH] A2V stereo audio check failed; using original audio: {exc}")
        return audio_path


def ffmpeg_concat_copy(
    segment_paths: list[str], output_mp4: str, ffmpeg_bin: str, ctx: ExtensionContext
) -> None:
    out_abs = os.path.abspath(output_mp4)
    dyn_abs = os.path.abspath(str(ctx.get_output_path()))
    lines: list[str] = []
    for p in segment_paths:
        ap = os.path.abspath(p)
        rel = os.path.relpath(ap, start=dyn_abs)
        rel = rel.replace("\\", "/")
        if "'" in rel:
            rel = rel.replace("'", "'\\''")
        lines.append(f"file '{rel}'")
    list_body = "\n".join(lines) + "\n"
    list_path = os.path.join(
        dyn_abs, f"_batch_concat_{os.getpid()}_{__import__('time').time_ns()}.txt"
    )
    try:
        Path(list_path).write_text(list_body, encoding="utf-8")
        cmd = [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out_abs]
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | 0x00000008
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              startupinfo=si, creationflags=flags)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"ffmpeg 拼接失败 (code {proc.returncode}): {err[:800]}")
    finally:
        try:
            if os.path.isfile(list_path):
                os.unlink(list_path)
        except OSError:
            pass


def ffmpeg_mux_background_audio(
    ffmpeg_bin: str, video_in: str, audio_in: str, video_out: str
) -> None:
    out_abs = os.path.abspath(video_out)
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | 0x00000008
    proc = subprocess.run(
        [ffmpeg_bin, "-y", "-i", os.path.abspath(video_in), "-i", os.path.abspath(audio_in),
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", out_abs],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        startupinfo=si, creationflags=flags,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"配乐混流失败 (code {proc.returncode}): {err[:800]}")
