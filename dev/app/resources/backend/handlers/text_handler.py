"""Text encoding cache and API embedding handler."""

from __future__ import annotations

import json
import time
from threading import RLock
from typing import TYPE_CHECKING

import torch
import requests

from handlers.base import StateHandlerBase, with_state_lock
from state.app_state_types import AppState, TextEncodingResult

if TYPE_CHECKING:
    from runtime_config.runtime_config import RuntimeConfig


class TextHandler(StateHandlerBase):
    def __init__(self, state: AppState, lock: RLock, config: RuntimeConfig) -> None:
        super().__init__(state, lock, config)
        self._video_dim = 4096

    @with_state_lock
    def _get_cached_prompt(self, prompt: str, enhance_prompt: bool) -> TextEncodingResult | None:
        te = self.state.text_encoder
        if te is None:
            return None
        return te.prompt_cache.get((prompt.strip(), enhance_prompt))

    @with_state_lock
    def _cache_prompt(self, prompt: str, enhance_prompt: bool, result: TextEncodingResult) -> None:
        te = self.state.text_encoder
        if te is None:
            return

        max_size = self.state.app_settings.prompt_cache_size
        if max_size <= 0:
            return

        key = (prompt.strip(), enhance_prompt)
        if key in te.prompt_cache:
            del te.prompt_cache[key]
        elif len(te.prompt_cache) >= max_size:
            oldest = next(iter(te.prompt_cache))
            del te.prompt_cache[oldest]
        te.prompt_cache[key] = result

    @with_state_lock
    def _set_api_embeddings(self, result: TextEncodingResult | None) -> None:
        if self.state.text_encoder is not None:
            self.state.text_encoder.api_embeddings = result

    def clear_api_embeddings(self) -> None:
        self._set_api_embeddings(None)

    def should_use_local_encoding(self) -> bool:
        """Decide whether to use local text encoding based on availability.

        The user's ``use_local_text_encoder`` setting acts as a tiebreaker only
        when **both** the API key and the local encoder are available.  When only
        one option exists, that option is used regardless of the setting.
        """
        settings = self.state.app_settings.model_copy(deep=True)
        provider = settings.text_encoder_provider
        
        if provider == "ollama" or provider == "comfyui":
            return False
            
        api_available = bool(settings.ltx_api_key)
        text_encoder_dir = self.resolve_model("text_encoder")
        local_available = text_encoder_dir.exists() and any(text_encoder_dir.iterdir())

        if api_available and local_available:
            return settings.use_local_text_encoder  # setting is tiebreaker
        return local_available  # use whichever is available

    def _encode_via_ollama(self, prompt: str) -> TextEncodingResult | None:
        """Encode prompt using Ollama embedding API."""
        settings = self.state.app_settings.model_copy(deep=True)
        ollama_url = settings.ollama_url.rstrip("/")
        model = settings.ollama_model

        try:
            start = time.time()
            response = requests.post(
                f"{ollama_url}/api/embeddings",
                json={
                    "model": model,
                    "prompt": prompt,
                },
                timeout=60,
            )

            if response.status_code != 200:
                print(f"Ollama API error {response.status_code}: {response.text}")
                return None

            data = response.json()
            embedding = data.get("embedding")
            if not embedding:
                print("Ollama API returned no embedding")
                return None

            embedding_tensor = torch.tensor(embedding, dtype=torch.bfloat16, device=self.config.device)
            embedding_tensor = embedding_tensor.unsqueeze(0).unsqueeze(0)
            
            video_dim = min(self._video_dim, embedding_tensor.shape[-1])
            video_context = embedding_tensor[..., :video_dim].contiguous()
            
            audio_dim = embedding_tensor.shape[-1] - video_dim
            audio_context = embedding_tensor[..., video_dim:].contiguous() if audio_dim > 0 else None

            print(f"Text encoded via Ollama in {time.time() - start:.1f}s")
            return TextEncodingResult(video_context=video_context, audio_context=audio_context)

        except Exception as exc:
            print(f"Ollama encoding failed: {exc}")
            return None

    def _encode_via_comfyui(self, prompt: str) -> TextEncodingResult | None:
        """Encode prompt using ComfyUI embedding API."""
        settings = self.state.app_settings.model_copy(deep=True)
        comfyui_url = settings.comfyui_url.rstrip("/")

        try:
            start = time.time()
            
            prompt_data = {
                "prompt": prompt,
                "max_tokens": 512,
            }
            
            response = requests.post(
                f"{comfyui_url}/prompt",
                json={"prompt": json.dumps(prompt_data)},
                timeout=60,
            )

            if response.status_code != 200:
                print(f"ComfyUI API error {response.status_code}: {response.text}")
                return None

            data = response.json()
            prompt_id = data.get("prompt_id")
            
            if not prompt_id:
                print("ComfyUI API returned no prompt_id")
                return None

            for _ in range(30):
                status_response = requests.get(
                    f"{comfyui_url}/history/{prompt_id}",
                    timeout=30,
                )
                
                if status_response.status_code == 200:
                    history = status_response.json()
                    if prompt_id in history:
                        outputs = history[prompt_id].get("outputs", {})
                        for node_id, node_output in outputs.items():
                            embeddings = node_output.get("embeddings")
                            if embeddings:
                                embedding_tensor = torch.tensor(embeddings, dtype=torch.bfloat16, device=self.config.device)
                                embedding_tensor = embedding_tensor.unsqueeze(0).unsqueeze(0)
                                
                                video_dim = min(self._video_dim, embedding_tensor.shape[-1])
                                video_context = embedding_tensor[..., :video_dim].contiguous()
                                
                                audio_dim = embedding_tensor.shape[-1] - video_dim
                                audio_context = embedding_tensor[..., video_dim:].contiguous() if audio_dim > 0 else None

                                print(f"Text encoded via ComfyUI in {time.time() - start:.1f}s")
                                return TextEncodingResult(video_context=video_context, audio_context=audio_context)
                time.sleep(1)

            print("ComfyUI encoding timeout")
            return None

        except Exception as exc:
            print(f"ComfyUI encoding failed: {exc}")
            return None

    def prepare_text_encoding(self, prompt: str, enhance_prompt: bool) -> None:
        """Validate settings and prepare text embeddings for a generation run.

        Raises RuntimeError with a prefixed message if text encoding is
        misconfigured, the local encoder is missing, or API encoding fails
        with no local fallback.
        """
        settings = self.state.app_settings.model_copy(deep=True)
        provider = settings.text_encoder_provider
        
        if provider == "ollama" or provider == "comfyui":
            embeddings = self._prepare_api_embeddings(prompt, enhance_prompt)
            if embeddings is None:
                raise RuntimeError(
                    f"TEXT_ENCODING_NOT_CONFIGURED: {provider} text encoding failed. "
                    f"Please check your {provider} service is running."
                )
            return

        api_available = bool(settings.ltx_api_key)
        text_encoder_dir = self.resolve_model("text_encoder")
        local_available = text_encoder_dir.exists() and any(text_encoder_dir.iterdir())

        if not api_available and not local_available:
            raise RuntimeError(
                "TEXT_ENCODING_NOT_CONFIGURED: To generate videos, you need to configure text encoding. "
                "Either enter an LTX API Key in Settings, or enable the Local Text Encoder, "
                "or configure Ollama/ComfyUI in settings."
            )

        use_local = self.should_use_local_encoding()
        gemma_root = self.resolve_gemma_root()
        embeddings = self._prepare_api_embeddings(prompt, enhance_prompt)

        if not use_local and embeddings is None and gemma_root is None:
            raise RuntimeError(
                "LTX API text encoding failed and local text encoder is not available. "
                "Please download the text encoder from Settings or check your API key."
            )

    def resolve_gemma_root(self) -> str | None:
        if not self.should_use_local_encoding():
            return None
        text_encoder_dir = self.resolve_model("text_encoder")
        return str(text_encoder_dir)

    def _prepare_api_embeddings(self, prompt: str, enhance_prompt: bool) -> TextEncodingResult | None:
        if self.should_use_local_encoding():
            self.clear_api_embeddings()
            return None

        settings = self.state.app_settings.model_copy(deep=True)
        provider = settings.text_encoder_provider
        
        if provider == "ollama":
            cached = self._get_cached_prompt(prompt, enhance_prompt)
            if cached is not None:
                self._set_api_embeddings(cached)
                return cached
            
            encoded = self._encode_via_ollama(prompt)
            if encoded is not None:
                self._cache_prompt(prompt, enhance_prompt, encoded)
                self._set_api_embeddings(encoded)
            return encoded
            
        elif provider == "comfyui":
            cached = self._get_cached_prompt(prompt, enhance_prompt)
            if cached is not None:
                self._set_api_embeddings(cached)
                return cached
            
            encoded = self._encode_via_comfyui(prompt)
            if encoded is not None:
                self._cache_prompt(prompt, enhance_prompt, encoded)
                self._set_api_embeddings(encoded)
            return encoded

        if not settings.ltx_api_key:
            self.clear_api_embeddings()
            return None

        cached = self._get_cached_prompt(prompt, enhance_prompt)
        if cached is not None:
            self._set_api_embeddings(cached)
            return cached

        te = self.state.text_encoder
        if te is None:
            return None

        encoded = te.service.encode_via_api(
            prompt=prompt,
            api_key=settings.ltx_api_key,
            checkpoint_path=str(self.resolve_model("checkpoint")),
            enhance_prompt=enhance_prompt,
        )
        if encoded is not None:
            self._cache_prompt(prompt, enhance_prompt, encoded)
            self._set_api_embeddings(encoded)
        return encoded
