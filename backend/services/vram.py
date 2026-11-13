"""
VRAM management: live usage, idle auto-unload, one-TTS-engine-at-a-time.

* ``status()`` reports device memory plus which models are resident.
* ``before_engine_load(engine)`` unloads the other TTS engines when the
  "exclusive engine" setting is on, so switching engines on an 8 GB card
  never stacks two models in VRAM.
* ``idle_watcher()`` (started on server startup) unloads TTS models once
  nothing has been generated for the configured number of minutes.

The TTS engine stays loaded for the configured idle time, so the next
generation starts at once. Whisper and the LLM are only helpers for checks and
translation: they leave a minute after the last job, and RAM is handed back.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import threading
import time
from contextlib import contextmanager

from . import extras

logger = logging.getLogger(__name__)

_NON_TTS_ENGINES = {"whisper", "qwen_llm"}
_TRIM_HELPERS_AFTER_SECONDS = 60

_lock = threading.Lock()
_release_pending = False
_last_used = time.time()
_in_use: dict[str, int] = {}
_trimmed = True


def touch() -> None:
    global _last_used, _trimmed
    _last_used = time.time()
    _trimmed = False


@contextmanager
def engine_in_use(engine: str):
    """Mark *engine* busy so neither the idle watcher nor an exclusive switch unloads it."""
    with _lock:
        _in_use[engine] = _in_use.get(engine, 0) + 1
    touch()
    try:
        yield
    finally:
        with _lock:
            _in_use[engine] = max(0, _in_use.get(engine, 1) - 1)
        touch()


def _busy_engines() -> set[str]:
    with _lock:
        return {e for e, n in _in_use.items() if n > 0}


def _loaded_configs():
    from ..backends import check_model_loaded, get_all_model_configs

    loaded = []
    for cfg in get_all_model_configs():
        try:
            if check_model_loaded(cfg):
                loaded.append(cfg)
        except Exception:
            pass
    return loaded


def _empty_cuda_cache() -> None:
    try:
        from ..backends.base import empty_device_cache

        empty_device_cache("cuda")
    except Exception:
        pass


def unload_tts(except_engine: str | None = None, include_all: bool = False) -> list[str]:
    """Unload resident models. Returns the display names of what was unloaded.

    Skips engines that are generating right now and, unless *include_all*,
    Whisper / LLM models.
    """
    from ..backends import unload_model_by_config

    busy = _busy_engines()
    unloaded: list[str] = []
    for cfg in _loaded_configs():
        if cfg.engine == except_engine or cfg.engine in busy:
            continue
        if not include_all and cfg.engine in _NON_TTS_ENGINES:
            continue
        try:
            if unload_model_by_config(cfg):
                unloaded.append(cfg.display_name)
        except Exception as e:
            logger.warning("Failed to unload %s: %s", cfg.model_name, e)
    if unloaded:
        _empty_cuda_cache()
        logger.info("VRAM manager unloaded: %s", ", ".join(unloaded))
    return unloaded


def before_engine_load(engine: str) -> None:
    """Called right before a TTS engine loads; frees other engines if configured."""
    touch()
    if not extras.get("vram_exclusive_engine"):
        return
    try:
        unload_tts(except_engine=engine)
    except Exception as e:
        logger.warning("Exclusive-engine unload failed: %s", e)


def release_all() -> dict:
    """Free VRAM and RAM now, or as soon as the running job ends."""
    global _release_pending
    from ..utils.tasks import get_task_manager

    if _busy_engines() or get_task_manager().get_active_generations():
        _release_pending = True
        return {"unloaded": [], "deferred": True}
    _release_pending = False
    unloaded = unload_tts(include_all=True)
    _release_process_memory()
    return {"unloaded": unloaded, "deferred": False}


def release_now() -> list[str]:
    """Unload everything that is not generating, even with jobs still queued."""
    unloaded = unload_tts(include_all=True)
    _release_process_memory()
    return unloaded


def _trim_helpers() -> None:
    """Drop Whisper and the LLM and return unused RAM; the TTS engine stays ready."""
    from ..backends import unload_model_by_config

    for cfg in _loaded_configs():
        if cfg.engine in _NON_TTS_ENGINES:
            try:
                unload_model_by_config(cfg)
            except Exception as e:
                logger.warning("Failed to unload %s: %s", cfg.model_name, e)
    _release_process_memory()


def _release_process_memory() -> None:
    gc.collect()
    _empty_cuda_cache()
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            # Without these types the pseudo-handle is truncated to 32 bits and
            # the call fails with "invalid handle".
            current = ctypes.WinDLL("kernel32").GetCurrentProcess
            current.restype = wintypes.HANDLE
            empty = ctypes.WinDLL("psapi").EmptyWorkingSet
            empty.argtypes = [wintypes.HANDLE]
            empty.restype = wintypes.BOOL
            empty(current())
        except Exception as e:
            logger.debug("EmptyWorkingSet failed: %s", e)


def status() -> dict:
    info: dict = {
        "cuda_available": False,
        "device_name": None,
        "total_mb": None,
        "free_mb": None,
        "used_mb": None,
        "torch_allocated_mb": None,
        "torch_reserved_mb": None,
        "loaded_models": [],
        "idle_seconds": int(time.time() - _last_used),
        "idle_unload_minutes": extras.get("vram_idle_unload_minutes"),
        "exclusive_engine": extras.get("vram_exclusive_engine"),
    }
    try:
        import torch

        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            mb = 1024 * 1024
            info.update(
                cuda_available=True,
                device_name=torch.cuda.get_device_name(0),
                total_mb=round(total / mb),
                free_mb=round(free / mb),
                used_mb=round((total - free) / mb),
                torch_allocated_mb=round(torch.cuda.memory_allocated() / mb),
                torch_reserved_mb=round(torch.cuda.memory_reserved() / mb),
            )
    except Exception as e:
        logger.debug("CUDA status unavailable: %s", e)
    try:
        import psutil

        mem = psutil.virtual_memory()
        mb = 1024 * 1024
        info.update(
            ram_total_mb=round(mem.total / mb),
            ram_used_mb=round((mem.total - mem.available) / mb),
            app_ram_mb=round(psutil.Process().memory_info().rss / mb),
        )
    except Exception as e:
        logger.debug("RAM status unavailable: %s", e)
    try:
        from ..utils.tasks import get_task_manager

        info["queue_length"] = len(get_task_manager().get_active_generations())
    except Exception:
        info["queue_length"] = 0
    info["release_pending"] = _release_pending
    busy = _busy_engines()
    info["loaded_models"] = [
        {
            "model_name": c.model_name,
            "display_name": c.display_name,
            "engine": c.engine,
            "in_use": c.engine in busy,
        }
        for c in _loaded_configs()
    ]
    return info


async def idle_watcher(interval: float = 3.0) -> None:
    """Background loop: deferred release and idle unload of TTS models."""
    while True:
        try:
            await asyncio.sleep(interval)
            from . import task_queue
            from ..utils.tasks import get_task_manager

            if _release_pending and not _busy_engines() and not get_task_manager().get_active_generations():
                await asyncio.to_thread(release_all)
                continue
            global _trimmed
            if (
                not _trimmed
                and time.time() - _last_used >= _TRIM_HELPERS_AFTER_SECONDS
                and not _busy_engines()
                and not get_task_manager().get_active_generations()
                # A long translation is one job with many LLM calls: never idle.
                and not task_queue.running_keys()
                and not task_queue.queued_keys()
            ):
                _trimmed = True
                await asyncio.to_thread(_trim_helpers)
            minutes = int(extras.get("vram_idle_unload_minutes") or 0)
            if minutes <= 0 or _busy_engines():
                continue

            if get_task_manager().get_active_generations():
                touch()
                continue
            if time.time() - _last_used < minutes * 60:
                continue
            unloaded = await asyncio.to_thread(unload_tts)
            if unloaded:
                touch()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("VRAM idle watcher error: %s", e)
