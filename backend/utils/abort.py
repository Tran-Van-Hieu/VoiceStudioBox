"""Stop the GPU job within one model step, from any thread.

A thread running torch cannot be killed, and cancelling the asyncio task that
awaits it leaves the thread holding the GPU while the next job starts beside
it. So the running model checks a flag on every forward pass instead and
raises out of its own call, which also means the thread is really gone before
the queue moves on.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from typing import Any

_HOOK_DEPTH = 2

_stop = threading.Event()
_skip = threading.Event()
_checking = 0


class Aborted(asyncio.CancelledError):
    """The user pressed Stop. A CancelledError, so no ``except Exception`` eats it."""


class CheckSkipped(Exception):
    """The user asked to skip the check that is running; the audio itself stands."""


def reset() -> None:
    """Called by the queue before each job."""
    global _checking
    _stop.clear()
    _skip.clear()
    _checking = 0


def request_stop() -> None:
    _stop.set()


def stopping() -> bool:
    return _stop.is_set()


def check() -> None:
    if _stop.is_set():
        raise Aborted()
    if _skip.is_set() and _checking:
        raise CheckSkipped()


def skip_checks() -> None:
    _skip.set()


def checks_skipped() -> bool:
    return _skip.is_set()


def is_checking() -> bool:
    return _checking > 0


@contextmanager
def checking():
    """Mark a stretch as verification work, which Skip check may interrupt."""
    global _checking
    _checking += 1
    try:
        yield
    finally:
        _checking -= 1


def _on_forward(_module: Any, _inputs: Any) -> None:
    check()


def _hook(module: Any, depth: int) -> None:
    if getattr(module, "_abort_guarded", False):
        return
    module.register_forward_pre_hook(_on_forward)
    object.__setattr__(module, "_abort_guarded", True)
    if depth > 0:
        for child in module.children():
            _hook(child, depth - 1)


def guard(owner: Any) -> None:
    """Make every torch module reachable from *owner* honour Stop.

    Backends hold their networks at different depths (``backend.model``,
    ``backend.model.t3``, ``backend.model.model``), so look two attributes down.
    """
    try:
        import torch
    except ImportError:
        return

    def visit(value: Any, level: int, seen: set[int]) -> None:
        if value is None or id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, torch.nn.Module):
            _hook(value, _HOOK_DEPTH)
            return
        if level == 0 or isinstance(value, (str, bytes, int, float, bool, list, tuple, dict, set)):
            return
        for child in getattr(value, "__dict__", {}).values():
            visit(child, level - 1, seen)

    visit(owner, 3, set())
