"""
Qwen3 LLM backend implementations.

PyTorch (transformers AutoModelForCausalLM) path that shares the same
`LLMBackend` protocol and model-load progress plumbing as the TTS
and STT engines.
"""

import asyncio
import logging
from typing import Optional

from . import DEFAULT_LLM_MAX_TOKENS, DEFAULT_LLM_TEMPERATURE
from .base import (
    is_model_cached,
    get_torch_device,
    empty_device_cache,
    model_load_progress,
)

logger = logging.getLogger(__name__)


PYTORCH_HF_REPOS = {
    "0.6B": "Qwen/Qwen3-0.6B",
    "1.7B": "Qwen/Qwen3-1.7B",
    "4B": "Qwen/Qwen3-4B",
}

def _progress_name(model_size: str) -> str:
    return f"qwen3-{model_size.lower()}"


def _build_messages(
    prompt: str,
    system: Optional[str],
    examples: Optional[list[tuple[str, str]]] = None,
) -> list[dict]:
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    if examples:
        for user_text, assistant_text in examples:
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": assistant_text})
    messages.append({"role": "user", "content": prompt})
    return messages


class PyTorchQwenLLMBackend:
    """Qwen3 LLM backend using HuggingFace transformers."""

    def __init__(self, model_size: str = "0.6B"):
        self.model = None
        self.tokenizer = None
        self.model_size = model_size
        self._current_model_size: Optional[str] = None
        self.device = self._get_device()

    def _get_device(self) -> str:
        return get_torch_device(allow_xpu=True, allow_directml=True, allow_mps=True)

    def is_loaded(self) -> bool:
        return self.model is not None

    def _get_model_path(self, model_size: str) -> str:
        if model_size not in PYTORCH_HF_REPOS:
            raise ValueError(f"Unknown Qwen3 size: {model_size}")
        return PYTORCH_HF_REPOS[model_size]

    def _is_model_cached(self, model_size: str) -> bool:
        return is_model_cached(self._get_model_path(model_size))

    async def load_model(self, model_size: Optional[str] = None) -> None:
        if model_size is None:
            model_size = self.model_size

        if self.model is not None and self._current_model_size == model_size:
            return

        if self.model is not None and self._current_model_size != model_size:
            self.unload_model()

        await asyncio.to_thread(self._load_model_sync, model_size)

    def _load_model_sync(self, model_size: str) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        progress_model_name = _progress_name(model_size)
        is_cached = self._is_model_cached(model_size)
        repo = self._get_model_path(model_size)

        with model_load_progress(progress_model_name, is_cached):
            logger.info("Loading Qwen3 %s on %s...", model_size, self.device)
            # Loads run with the process's default HF_HUB_OFFLINE state.
            # Forcing offline for cached models flips process-global state
            # and silently switches every concurrent download/load on other
            # threads to offline mode (issue #841) — the same regression
            # removed app-wide in #524/#530.
            self.tokenizer = AutoTokenizer.from_pretrained(repo)
            dtype = torch.float16 if self.device in ("cuda", "mps") else torch.float32
            self.model = AutoModelForCausalLM.from_pretrained(
                repo,
                dtype=dtype,
            )
            self.model.to(self.device)
            self.model.eval()

        from ..utils import abort

        abort.guard(self.model)
        self._current_model_size = model_size
        self.model_size = model_size
        logger.info("Qwen3 %s loaded successfully", model_size)

    def unload_model(self) -> None:
        if self.model is None:
            return
        del self.model
        del self.tokenizer
        self.model = None
        self.tokenizer = None
        self._current_model_size = None
        empty_device_cache(self.device)
        logger.info("Qwen3 unloaded")

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = DEFAULT_LLM_MAX_TOKENS,
        temperature: float = DEFAULT_LLM_TEMPERATURE,
        model_size: Optional[str] = None,
        examples: Optional[list[tuple[str, str]]] = None,
    ) -> str:
        from ..services import vram

        with vram.engine_in_use("qwen_llm"):
            await self.load_model(model_size)
            return await asyncio.to_thread(
                self._generate_sync, prompt, system, max_tokens, temperature, examples
            )

    def _generate_sync(
        self,
        prompt: str,
        system: Optional[str],
        max_tokens: int,
        temperature: float,
        examples: Optional[list[tuple[str, str]]] = None,
    ) -> str:
        import torch

        messages = _build_messages(prompt, system, examples)
        text = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        inputs = self.tokenizer(text, return_tensors="pt").to(self.device)

        do_sample = temperature > 0
        generate_kwargs = {
            "max_new_tokens": max_tokens,
            "do_sample": do_sample,
            "pad_token_id": self.tokenizer.eos_token_id,
        }
        if do_sample:
            generate_kwargs["temperature"] = temperature
            generate_kwargs["top_p"] = 0.9

        with torch.no_grad():
            output_ids = self.model.generate(**inputs, **generate_kwargs)

        input_len = inputs["input_ids"].shape[1]
        new_tokens = output_ids[0, input_len:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
