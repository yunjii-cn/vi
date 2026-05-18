"""Custom patches for Windows compatibility and bug fixes."""

import json
import struct
import sys

try:
    import torch
except ImportError:
    torch = None


def _patch_av_open():
    if sys.platform != 'win32':
        return
    try:
        import av

        _orig_av_open = av.open

        def _patched_av_open(file, mode='r', *args, **kwargs):
            if isinstance(file, str):
                try:
                    from ctypes import windll, create_unicode_buffer
                    buf = create_unicode_buffer(512)
                    if windll.kernel32.GetShortPathNameW(file, buf, 512):
                        file = buf.value
                except:
                    pass
            return _orig_av_open(file, mode, *args, **kwargs)

        av.open = _patched_av_open
        print("[PATCH] av.open path patch installed (Windows non-ASCII paths)")
    except ImportError:
        pass


def _patch_siglip_vision_model():
    try:
        from ltx_core.text_encoders.gemma.encoders import encoder_configurator

        def _patched_create(module):
            import torch
            from transformers.modeling_rope_utils import ROPE_INIT_FUNCTIONS
            model = module.model
            vision_tower = model.model.vision_tower
            if hasattr(vision_tower, 'vision_model'):
                v_model = vision_tower.vision_model
            else:
                v_model = vision_tower
            l_model = model.model.language_model

            config = model.config.text_config
            dim = getattr(config, 'head_dim', config.hidden_size // config.num_attention_heads)
            base = getattr(config, 'rope_local_base_freq', None)
            if base is None:
                base = getattr(config, 'rope_base', 10000.0)
            local_rope_freqs = 1.0 / (base ** (torch.arange(0, dim, 2, dtype=torch.int64).to(dtype=torch.float) / dim))
            
            rope_scaling = getattr(config, 'rope_scaling', None)
            if rope_scaling and isinstance(rope_scaling, dict) and 'rope_type' in rope_scaling:
                inv_freqs, _ = ROPE_INIT_FUNCTIONS[rope_scaling["rope_type"]](config)
            else:
                inv_freqs = 1.0 / (10000.0 ** (torch.arange(0, dim, 2, dtype=torch.int64).to(dtype=torch.float) / dim))

            positions_length = len(v_model.embeddings.position_ids[0])
            position_ids = torch.arange(positions_length, dtype=torch.long, device="cpu").unsqueeze(0)
            v_model.embeddings.register_buffer("position_ids", position_ids)
            embed_scale = torch.tensor(model.config.text_config.hidden_size**0.5, device="cpu")
            l_model.embed_tokens.register_buffer("embed_scale", embed_scale)
            
            if hasattr(l_model, 'rotary_emb_local'):
                l_model.rotary_emb_local.register_buffer("inv_freq", local_rope_freqs)
            if hasattr(l_model, 'rotary_emb'):
                l_model.rotary_emb.register_buffer("inv_freq", inv_freqs)
                for attr_name in ['sliding_attention_inv_freq', 'sliding_attention_original_inv_freq',
                                  'full_attention_inv_freq', 'full_attention_original_inv_freq']:
                    if hasattr(l_model.rotary_emb, attr_name):
                        buf = getattr(l_model.rotary_emb, attr_name, None)
                        if buf is not None and getattr(buf, 'device', None) and buf.device.type == 'meta':
                            l_model.rotary_emb.register_buffer(attr_name, inv_freqs)

            if hasattr(l_model, 'rotary_emb') and hasattr(l_model.rotary_emb, 'inv_freq'):
                if l_model.rotary_emb.inv_freq.device.type == 'meta':
                    l_model.rotary_emb.register_buffer("inv_freq", inv_freqs)

            if hasattr(l_model, 'layers'):
                for layer in l_model.layers:
                    if hasattr(layer, 'self_attn') and hasattr(layer.self_attn, 'rotary_emb'):
                        rotary_emb = layer.self_attn.rotary_emb
                        if hasattr(rotary_emb, 'inv_freq') and rotary_emb.inv_freq.device.type == 'meta':
                            rotary_emb.register_buffer("inv_freq", inv_freqs)

            return module

        encoder_configurator.create_and_populate = _patched_create
        old_ops = encoder_configurator.GEMMA_MODEL_OPS
        new_ops = old_ops._replace(mutator=_patched_create)
        encoder_configurator.GEMMA_MODEL_OPS = new_ops
        try:
            from ltx_pipelines.utils import blocks
            if hasattr(blocks, 'GEMMA_MODEL_OPS') and blocks.GEMMA_MODEL_OPS is old_ops:
                blocks.GEMMA_MODEL_OPS = new_ops
        except Exception:
            pass
        try:
            from ltx_core.text_encoders.gemma import __init__ as gemma_init
            if hasattr(gemma_init, 'GEMMA_MODEL_OPS') and gemma_init.GEMMA_MODEL_OPS is old_ops:
                gemma_init.GEMMA_MODEL_OPS = new_ops
        except Exception:
            pass
        print("[PATCH] SiglipVisionModel compatibility patch installed (transformers>=5.6)")
    except Exception as e:
        print(f"[PATCH] SiglipVisionModel patch FAILED: {e}")


def _patch_gemma3_rotary_emb():
    try:
        from transformers.models.gemma3.modeling_gemma3 import Gemma3RotaryEmbedding
        
        orig_forward = Gemma3RotaryEmbedding.forward
        
        def patched_forward(self, x, position_ids, layer_type="default"):
            inv_freq = self.inv_freq
            
            if getattr(inv_freq, 'device', None) and inv_freq.device.type == 'meta':
                head_dim = x.shape[-1] // 2
                inv_freq = 1.0 / (10000.0 ** (torch.arange(0, head_dim, 2, dtype=torch.int64).float() / head_dim))
                self.register_buffer("inv_freq", inv_freq)
            
            inv_freq_expanded = inv_freq[None, None, :].float().expand(position_ids.shape[0], 1, -1).to(x.device)
            freqs = position_ids[:, :, None].float() @ inv_freq_expanded
            
            emb = torch.cat([freqs, freqs], dim=-1)
            cos = emb.cos()
            sin = emb.sin()
            
            return cos.to(dtype=x.dtype), sin.to(dtype=x.dtype)
        
        Gemma3RotaryEmbedding.forward = patched_forward
        print("[PATCH] Gemma3RotaryEmbedding meta tensor fix installed")
    except Exception as e:
        print(f"[PATCH] Gemma3RotaryEmbedding patch FAILED: {e}")


def _patch_safetensors_mmap():
    if sys.platform != 'win32':
        return
    try:
        import safetensors
        import gc
        _orig_safe_open = safetensors.safe_open

        _DTYPE_MAP = {
            "BOOL": (torch.bool, 1),
            "U8": (torch.uint8, 1),
            "I8": (torch.int8, 1),
            "I16": (torch.int16, 2),
            "I32": (torch.int32, 4),
            "I64": (torch.int64, 8),
            "F16": (torch.float16, 2),
            "BF16": (torch.bfloat16, 2),
            "F32": (torch.float32, 4),
            "F64": (torch.float64, 8),
            "F8_E4M3": (torch.float8_e4m3fn, 1),
            "F8_E5M2": (torch.float8_e5m2, 1),
        }

        class _NoMmapHandle:
            def __init__(self, path, device):
                self._path = path
                self._device = device
                self._header = None
                self._data_offset = 0
                self._f = open(path, "rb")
                header_size = struct.unpack("<Q", self._f.read(8))[0]
                header_json = self._f.read(header_size)
                self._header = json.loads(header_json)
                self._data_offset = 8 + header_size
                self._meta = self._header.get("__metadata__", None)

            def keys(self):
                return [k for k in self._header if k != "__metadata__"]

            def metadata(self):
                return self._meta

            def get_tensor(self, name):
                info = self._header[name]
                dtype_str = info["dtype"]
                shape = info["shape"]
                start, end = info["data_offsets"]
                n_bytes = end - start
                dtype, elem_size = _DTYPE_MAP.get(dtype_str, (torch.float32, 4))
                buf = bytearray(n_bytes)
                self._f.seek(self._data_offset + start)
                view = memoryview(buf)
                off = 0
                while off < n_bytes:
                    chunk = self._f.readinto(view[off:])
                    if chunk == 0:
                        break
                    off += chunk
                t = torch.frombuffer(buf, dtype=dtype).reshape(shape).clone()
                dev = torch.device(self._device) if isinstance(self._device, str) else self._device
                if dev.type != "cpu":
                    t = t.to(device=dev)
                return t

            def __enter__(self):
                return self

            def __exit__(self, *args):
                if self._f:
                    self._f.close()

        def _patched_safe_open(path, framework="pt", device="cpu"):
            try:
                return _orig_safe_open(path, framework=framework, device=device)
            except OSError as e:
                if "1455" in str(e) or "页面文件" in str(e) or "paging file" in str(e).lower():
                    gc.collect()
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    try:
                        return _orig_safe_open(path, framework=framework, device=device)
                    except OSError:
                        return _NoMmapHandle(path, device)
                raise

        safetensors.safe_open = _patched_safe_open
        print("[PATCH] safetensors safe_open mmap fallback patch installed (Windows os error 1455)")
    except Exception as e:
        print(f"[PATCH] safetensors safe_open patch FAILED: {e}")


def install_all_patches():
    _patch_av_open()
    _patch_siglip_vision_model()
    _patch_gemma3_rotary_emb()
    _patch_safetensors_mmap()