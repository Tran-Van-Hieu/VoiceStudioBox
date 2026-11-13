"""
Extra user settings added by the vi-upgrades fork.

Stored as a small JSON document in the data directory instead of new SQLite
columns, so the upstream migration chain stays untouched and the file can be
backed up or edited by hand. Every read merges the stored values over
``DEFAULTS`` so new keys appear automatically.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from typing import Any

from .. import config

logger = logging.getLogger(__name__)

_FILENAME = "extras_settings.json"
_lock = threading.Lock()
_cache: dict[str, Any] | None = None

DEFAULTS: dict[str, Any] = {
    # Playback speed applied to every engine after synthesis (1.0 = unchanged).
    "speed": 1.0,
    # Pronunciation dictionary: list of {from, to, case_sensitive, whole_word, enabled}.
    "pronunciation_enabled": True,
    "pronunciation": [],
    # Loudness normalization target in LUFS. None = off (plain peak normalize).
    "loudness_target_lufs": None,
    # Default download format for single generations and batches.
    "export_format": "wav",  # wav | mp3 | flac
    "mp3_bitrate": 192,  # kbps
    # Custom system prompt for the transcript refinement LLM.
    "refinement_custom_prompt": "",
    "refinement_prompt_mode": "append",  # append | replace
    # VRAM management.
    "vram_idle_unload_minutes": 0,  # 0 = never
    "vram_exclusive_engine": True,  # unload other TTS engines before loading one
    # Speech QC: Whisper listens to each chunk and bad ones are regenerated.
    "qc_mode": "all",  # off | all (omnivoice is read as all)
    "qc_max_retries": 3,
    "qc_threshold": 0.75,
    "qc_whisper_model": "turbo",
    # OmniVoice decoding steps: 32 is the model's own default, 16 is twice as fast.
    "omnivoice_steps": 32,
    # Rest between two different batches/scripts: models are unloaded, then the
    # queue waits this long before the next one starts. 0 = no rest.
    "queue_cooldown_seconds": 120,
    # sample id -> emotion, for profiles with one take per emotion.
    "sample_emotions": {},
}

_ALLOWED_FORMATS = {"wav", "mp3", "flac"}
_ALLOWED_BITRATES = {96, 128, 160, 192, 256, 320}


def _path():
    return config.get_data_dir() / _FILENAME


def _load_from_disk() -> dict[str, Any]:
    data: dict[str, Any] = {}
    path = _path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception as e:  # corrupt file -> fall back to defaults
            logger.warning("Could not read %s: %s", path, e)
            data = {}
    merged = copy.deepcopy(DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in DEFAULTS})
    return merged


def get_all() -> dict[str, Any]:
    """Return a copy of every extras setting (defaults filled in)."""
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load_from_disk()
        return copy.deepcopy(_cache)


def get(key: str) -> Any:
    return get_all().get(key, DEFAULTS.get(key))


def _sanitize_rules(rules: Any) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    if not isinstance(rules, list):
        return clean
    for r in rules[:2000]:
        if not isinstance(r, dict):
            continue
        src = str(r.get("from", "")).strip()
        if not src:
            continue
        clean.append(
            {
                "from": src[:200],
                "to": str(r.get("to", ""))[:500],
                "case_sensitive": bool(r.get("case_sensitive", False)),
                "whole_word": bool(r.get("whole_word", True)),
                "enabled": bool(r.get("enabled", True)),
            }
        )
    return clean


def _sanitize(key: str, value: Any) -> Any:
    if key == "speed":
        v = float(value)
        return round(min(2.0, max(0.5, v)), 2)
    if key == "pronunciation":
        return _sanitize_rules(value)
    if key == "loudness_target_lufs":
        if value is None or value == "" or value == "off":
            return None
        return round(min(-5.0, max(-40.0, float(value))), 1)
    if key == "export_format":
        v = str(value).lower()
        if v not in _ALLOWED_FORMATS:
            raise ValueError(f"Unsupported export format: {value}")
        return v
    if key == "mp3_bitrate":
        v = int(value)
        if v not in _ALLOWED_BITRATES:
            raise ValueError(f"Unsupported MP3 bitrate: {value}")
        return v
    if key == "refinement_custom_prompt":
        return str(value or "")[:8000]
    if key == "refinement_prompt_mode":
        v = str(value)
        if v not in ("append", "replace"):
            raise ValueError("refinement_prompt_mode must be 'append' or 'replace'")
        return v
    if key == "vram_idle_unload_minutes":
        return int(min(1440, max(0, int(value))))
    if key == "qc_mode":
        v = str(value)
        if v not in ("off", "omnivoice", "all"):
            raise ValueError("qc_mode must be 'off', 'omnivoice' or 'all'")
        return v
    if key == "omnivoice_steps":
        return max(8, min(64, int(value)))
    if key == "queue_cooldown_seconds":
        return max(0, min(3600, int(value)))
    if key == "qc_max_retries":
        return max(0, min(5, int(value)))
    if key == "qc_threshold":
        return round(max(0.4, min(0.95, float(value))), 2)
    if key == "qc_whisper_model":
        v = str(value)
        if v not in ("base", "small", "medium", "large", "turbo"):
            raise ValueError(f"Unsupported Whisper model: {value}")
        return v
    if key == "sample_emotions":
        return {str(k): str(v) for k, v in dict(value).items() if v and v != "neutral"}
    if key in ("pronunciation_enabled", "vram_exclusive_engine"):
        return bool(value)
    return value


def update(patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update, persist it, and return the full settings."""
    global _cache
    current = get_all()
    for key, value in patch.items():
        if key not in DEFAULTS:
            continue
        current[key] = _sanitize(key, value)
    with _lock:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        _cache = current
    return copy.deepcopy(current)
