"""System API endpoints (YunJi custom, not in upstream).

Endpoints:
  POST /api/system/clear-gpu      - Clear GPU memory and unload models
  GET  /api/system/low-vram-mode  - Get low VRAM mode status
  POST /api/system/low-vram-mode  - Set low VRAM mode
  POST /api/system/reset-state    - Reset generation state
  POST /api/system/set-dir        - Set output directory
  GET  /api/system/get-dir        - Get output directory
  GET  /api/system/browse-dir     - Browse for output directory
  GET  /api/system/list-gpus      - List available GPUs
  POST /api/system/switch-gpu     - Switch active GPU
  GET  /api/system/hardware-profile - Get hardware profile & recommendations
  POST /api/system/apply-profile  - Apply recommended profile settings

Upstream dependency: handler.generation, handler.pipelines, handler.config
"""

from __future__ import annotations

import asyncio
import gc
import os
import subprocess
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from extensions._context import ExtensionContext


def install(app: FastAPI, ctx: ExtensionContext) -> None:
    handler = ctx.handler

    @app.post("/api/system/clear-gpu")
    async def route_clear_gpu():
        try:
            import torch

            if getattr(handler.generation, "is_generation_running", lambda: False)():
                try:
                    handler.generation.cancel_generation()
                except Exception:
                    pass
                await asyncio.sleep(0.5)

            if hasattr(handler.generation, "_generation_id"):
                handler.generation._generation_id = None
            if hasattr(handler.generation, "_is_generating"):
                handler.generation._is_generating = False

            try:
                mock_swapped = False
                orig_running = None
                if hasattr(handler.pipelines, "_generation_service"):
                    orig_running = handler.pipelines._generation_service.is_generation_running
                    handler.pipelines._generation_service.is_generation_running = lambda: False
                    mock_swapped = True
                try:
                    from keep_models_runtime import force_unload_gpu_pipeline
                    force_unload_gpu_pipeline(handler.pipelines)
                finally:
                    if mock_swapped:
                        handler.pipelines._generation_service.is_generation_running = orig_running
            except Exception as e:
                print(f"Force unload warning: {e}")

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
            try:
                handler.pipelines._pipeline_signature = None
            except Exception:
                pass
            return {"status": "success", "message": "GPU memory cleared and models unloaded"}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.get("/api/system/low-vram-mode")
    async def route_get_low_vram_mode():
        enabled = bool(getattr(handler.pipelines, "low_vram_mode", False))
        return {"enabled": enabled}

    @app.post("/api/system/low-vram-mode")
    async def route_set_low_vram_mode(request: Request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        enabled = bool(data.get("enabled", False))
        from low_vram_runtime import (
            apply_low_vram_config_tweaks, restore_full_vram_config_tweaks,
            should_use_cpu_offload, write_low_vram_pref,
        )
        if enabled and not should_use_cpu_offload():
            return JSONResponse(status_code=400, content={"error": "显存上限为 0/空时不会启用低显存模式"})
        handler.pipelines.low_vram_mode = enabled
        write_low_vram_pref(enabled)
        if enabled:
            apply_low_vram_config_tweaks(handler)
        else:
            restore_full_vram_config_tweaks(handler)
        return {"status": "success", "enabled": enabled}

    @app.post("/api/system/reset-state")
    async def route_reset_state():
        try:
            gen = handler.generation
            for attr in ("_is_generating", "_generation_id", "_cancelled", "_is_cancelled"):
                if hasattr(gen, attr):
                    if attr in ("_is_generating", "_cancelled", "_is_cancelled"):
                        setattr(gen, attr, False)
                    else:
                        setattr(gen, attr, None)
            for attr in ("_cancel_event",):
                if hasattr(gen, attr):
                    try:
                        getattr(gen, attr).clear()
                    except Exception:
                        pass
            print("[reset-state] Generation state has been reset cleanly.")
            return {"status": "success", "message": "Generation state reset"}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.post("/api/system/set-dir")
    async def route_set_dir(request: Request):
        try:
            data = await request.json()
            new_dir = data.get("directory", "").strip()
            config_file = ctx.config_dir / "custom_dir.txt"
            if new_dir:
                p = Path(new_dir)
                p.mkdir(parents=True, exist_ok=True)
                config_file.write_text(new_dir, encoding="utf-8")
            else:
                if config_file.exists():
                    config_file.unlink()
            handler.config.outputs_dir = ctx.get_output_path()
            return {"status": "success", "directory": str(ctx.get_output_path())}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.get("/api/system/get-dir")
    async def route_get_dir():
        return {"status": "success", "directory": str(ctx.get_output_path())}

    @app.get("/api/system/browse-dir")
    async def route_browse_dir():
        try:
            ps_script = (
                "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null;"
                "[System.Reflection.Assembly]::LoadWithPartialName('System.Drawing') | Out-Null;"
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog;"
                "$f.Description = '选择 LTX 视频和图像生成的全局输出目录';"
                "$f.ShowNewFolderButton = $true;"
                "$owner = New-Object System.Windows.Forms.Form;"
                "$owner.TopMost = $true;"
                "$owner.StartPosition = 'CenterScreen';"
                "$owner.Size = New-Object System.Drawing.Size(1, 1);"
                "$owner.Show();"
                "$owner.BringToFront();"
                "$owner.Focus();"
                "if ($f.ShowDialog($owner) -eq 'OK') { echo $f.SelectedPath };"
                "$owner.Dispose();"
            )

            def run_ps():
                process = subprocess.Popen(
                    ["powershell", "-STA", "-NoProfile", "-Command", ps_script],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                stdout, _ = process.communicate()
                return stdout.strip()

            from starlette.concurrency import run_in_threadpool
            selected_dir = await run_in_threadpool(run_ps)
            return {"status": "success", "directory": selected_dir}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.get("/api/system/list-gpus")
    async def route_list_gpus():
        try:
            import torch
            gpus = []
            if torch.cuda.is_available():
                current_idx = 0
                dev = getattr(handler.config, "device", None)
                if dev is not None and getattr(dev, "index", None) is not None:
                    current_idx = dev.index
                for i in range(torch.cuda.device_count()):
                    try:
                        name = torch.cuda.get_device_name(i)
                    except Exception:
                        name = f"GPU {i}"
                    try:
                        vram_bytes = torch.cuda.get_device_properties(i).total_memory
                        vram_gb = vram_bytes / (1024**3)
                        vram_mb = vram_bytes / (1024**2)
                    except Exception:
                        vram_gb = 0.0
                        vram_mb = 0
                    gpus.append({
                        "id": i, "name": name,
                        "vram": f"{vram_gb:.1f} GB", "vram_mb": int(vram_mb),
                        "active": (i == current_idx),
                    })
            return {"status": "success", "gpus": gpus}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.post("/api/system/switch-gpu")
    async def route_switch_gpu(request: Request):
        try:
            import torch
            data = await request.json()
            gpu_id = data.get("gpu_id")
            if gpu_id is None or not torch.cuda.is_available() or gpu_id >= torch.cuda.device_count():
                return JSONResponse(status_code=400, content={"error": "Invalid GPU ID"})

            if getattr(handler.generation, "is_generation_running", lambda: False)():
                try:
                    handler.generation.cancel_generation()
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            if hasattr(handler.generation, "_generation_id"):
                handler.generation._generation_id = None
            if hasattr(handler.generation, "_is_generating"):
                handler.generation._is_generating = False

            try:
                mock_swapped = False
                orig_running = None
                if hasattr(handler.pipelines, "_generation_service"):
                    orig_running = handler.pipelines._generation_service.is_generation_running
                    handler.pipelines._generation_service.is_generation_running = lambda: False
                    mock_swapped = True
                try:
                    from keep_models_runtime import force_unload_gpu_pipeline
                    force_unload_gpu_pipeline(handler.pipelines)
                finally:
                    if mock_swapped:
                        handler.pipelines._generation_service.is_generation_running = orig_running
            except Exception:
                pass
            gc.collect()
            torch.cuda.empty_cache()
            try:
                handler.pipelines._pipeline_signature = None
            except Exception:
                pass

            new_device = torch.device(f"cuda:{gpu_id}")
            handler.config.device = new_device
            torch.cuda.set_device(gpu_id)
            os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

            try:
                te_state = None
                if hasattr(handler, "state") and hasattr(handler.state, "text_encoder"):
                    te_state = handler.state.text_encoder
                elif hasattr(handler, "_state") and hasattr(handler._state, "text_encoder"):
                    te_state = handler._state.text_encoder
                if te_state is not None:
                    if hasattr(te_state, "service") and hasattr(te_state.service, "device"):
                        te_state.service.device = new_device
                        print(f"[TextEncoder] device updated to {new_device}")
                    if hasattr(te_state, "cached_encoder") and te_state.cached_encoder is not None:
                        try:
                            te_state.cached_encoder.to(torch.device("cpu"))
                        except Exception:
                            pass
                        te_state.cached_encoder = None
                        print("[TextEncoder] cached encoder cleared (will reload on new GPU)")
                    if hasattr(te_state, "api_embeddings"):
                        te_state.api_embeddings = None
                    if hasattr(te_state, "prompt_cache") and te_state.prompt_cache:
                        te_state.prompt_cache.clear()
                        print("[TextEncoder] prompt cache cleared")
            except Exception as _te_err:
                print(f"[TextEncoder] device sync warning (non-fatal): {_te_err}")

            print(f"Switched active GPU to: {torch.cuda.get_device_name(gpu_id)} (ID: {gpu_id})")
            return {"status": "success", "message": f"Switched to GPU {gpu_id}"}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    # ── 硬件档案 & 推荐方案 ────────────────────────────────────

    @app.get("/api/system/hardware-profile")
    async def route_hardware_profile():
        """自动检测硬件并返回推荐视频生成参数。"""
        try:
            from extensions.hardware_profiles import auto_detect_profile
            return auto_detect_profile()
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.get("/api/system/calc-params")
    async def route_calc_params(quality: str = "720", fps: int = 24):
        """动态计算推荐参数 — 核心联动逻辑。

        VRAM 限制的是总帧数，不是秒数。调高 FPS → 推荐秒数自动缩短。

        Query params:
            quality: 清晰度 ("1080"/"720"/"540"/"480"/"360")
            fps: 帧率
        """
        try:
            from extensions.hardware_profiles import calc_dynamic_params, classify_gpu, detect_gpu_vram_gb
            vram_gb, _ = detect_gpu_vram_gb()
            tier = classify_gpu(vram_gb)
            return calc_dynamic_params(tier, quality, fps)
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.post("/api/system/apply-profile")
    async def route_apply_profile(request: Request):
        """根据硬件档案自动应用推荐设置 (vram_limit, offload, upscaler)。

        Body (optional): {"tier": "high"} — 指定等级，不传则自动检测
        """
        try:
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            tier = body.get("tier") or None

            from extensions.hardware_profiles import get_profile, apply_profile_to_settings
            profile = get_profile(tier)
            apply_profile_to_settings(profile)

            # 同步 low_vram_mode 到运行时
            from low_vram_runtime import install_low_vram_on_pipelines
            install_low_vram_on_pipelines(handler)

            return {
                "status": "success",
                "tier": profile.tier,
                "tier_name": profile.tier_name,
                "vram_limit_gb": profile.vram_limit_gb,
                "offload_enabled": profile.offload_enabled,
                "upscaler_enabled": profile.upscaler_enabled,
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
