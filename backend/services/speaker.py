"""Speaker embeddings: "does this audio sound like that voice?".

Uses the small voice encoder that ships with Chatterbox, on the CPU, so the
check costs no VRAM. Measured against a clone of the same voice: 0.93 for the
clone, 0.69 pitch-shifted four semitones, 0.52 for noise, 0.42 for music.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

logger = logging.getLogger(__name__)

_SR = 16000
_MIN_SECONDS = 0.8
_lock = threading.Lock()
_encoder = None


def _get_encoder():
    global _encoder
    with _lock:
        if _encoder is None:
            import torch
            from chatterbox.models.voice_encoder import VoiceEncoder
            from huggingface_hub import hf_hub_download

            model = VoiceEncoder()
            weights = hf_hub_download("ResembleAI/chatterbox", "ve.pt")
            model.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
            _encoder = model.eval()
        return _encoder


def embed(audio: np.ndarray, sample_rate: int) -> Optional[np.ndarray]:
    """Unit-length voice embedding, or None for clips too short to judge."""
    import librosa

    x = np.asarray(audio, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if len(x) < sample_rate * _MIN_SECONDS:
        return None
    if sample_rate != _SR:
        x = librosa.resample(x, orig_sr=sample_rate, target_sr=_SR)
    try:
        encoder = _get_encoder()
        with _lock:
            return encoder.embeds_from_wavs([x], sample_rate=_SR, as_spk=False)[0]
    except Exception as e:
        logger.warning("Speaker embedding failed: %s", e)
        return None


def embed_files(paths: Iterable[str | Path]) -> Optional[np.ndarray]:
    """Average embedding of several reference clips."""
    import soundfile as sf

    found = []
    for path in paths:
        try:
            audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
        except Exception as e:
            logger.warning("Could not read %s: %s", path, e)
            continue
        vector = embed(audio, sr)
        if vector is not None:
            found.append(vector)
    if not found:
        return None
    mean = np.mean(found, axis=0)
    return mean / (np.linalg.norm(mean) + 1e-9)


def similarity(a: Optional[np.ndarray], b: Optional[np.ndarray]) -> Optional[float]:
    if a is None or b is None:
        return None
    return float(np.dot(a, b))
