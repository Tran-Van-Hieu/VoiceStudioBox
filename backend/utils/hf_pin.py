"""Pin Hugging Face repos to an exact commit.

``from_pretrained(repo)`` follows the remote ``main`` branch, so a model could
change under the user at any load. A pinned repo always resolves to its
recorded commit, which also skips the network when the files are cached.

Must be installed before transformers or the TTS packages are imported.
"""

from __future__ import annotations

import functools
import threading

_pins: dict[str, str] = {}
_lock = threading.Lock()
_installed = False


def set_pin(repo_id: str, commit: str) -> None:
    with _lock:
        _pins[repo_id] = commit


def get_pin(repo_id: str) -> str | None:
    with _lock:
        return _pins.get(repo_id)


def _resolve(repo_id: str, revision: str | None) -> str | None:
    if revision not in (None, "main"):
        return revision
    return get_pin(repo_id) or revision


def install() -> None:
    """Route every download or cache lookup through the pin table."""
    global _installed
    if _installed:
        return
    import huggingface_hub
    from huggingface_hub import _snapshot_download, file_download

    # hf_hub_download is imported by name all over transformers; this inner
    # function is only ever reached through file_download's own globals.
    original_file = file_download._hf_hub_download_to_cache_dir

    @functools.wraps(original_file)
    def pinned_file(*args, **kwargs):
        if "repo_id" in kwargs and kwargs.get("repo_type") in (None, "model"):
            kwargs["revision"] = _resolve(kwargs["repo_id"], kwargs.get("revision")) or "main"
        return original_file(*args, **kwargs)

    file_download._hf_hub_download_to_cache_dir = pinned_file

    original_snapshot = _snapshot_download.snapshot_download

    @functools.wraps(original_snapshot)
    def pinned_snapshot(repo_id, *args, **kwargs):
        if kwargs.get("repo_type") in (None, "model"):
            kwargs["revision"] = _resolve(repo_id, kwargs.get("revision"))
        return original_snapshot(repo_id, *args, **kwargs)

    _snapshot_download.snapshot_download = pinned_snapshot
    huggingface_hub.snapshot_download = pinned_snapshot
    _installed = True
