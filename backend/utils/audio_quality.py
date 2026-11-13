"""Measurable audio defects: clipping, clicks, noise floor, loudness."""

from __future__ import annotations

import numpy as np

_FRAME_S = 0.02
_CLIP_LEVEL = 0.995
_CLICK_MIN = 0.03
_CLICK_RATIO = 12.0


def _mono(audio: np.ndarray) -> np.ndarray:
    x = np.asarray(audio, dtype=np.float32)
    return x.mean(axis=1) if x.ndim > 1 else x


def frame_rms(x: np.ndarray, sample_rate: int) -> np.ndarray:
    n = max(1, int(sample_rate * _FRAME_S))
    count = len(x) // n
    if count == 0:
        return np.zeros(1, dtype=np.float32)
    frames = x[: count * n].reshape(count, n)
    return np.sqrt(np.mean(frames**2, axis=1) + 1e-12)


def count_clicks(x: np.ndarray, sample_rate: int) -> int:
    """Isolated sample jumps far above the waveform's local slope."""
    if len(x) < sample_rate // 10:
        return 0
    d = np.abs(np.diff(x, n=2))
    w = max(3, int(sample_rate * 0.01))
    local = np.convolve(d, np.ones(w) / w, mode="same")
    hits = np.flatnonzero((d > _CLICK_MIN) & (d > _CLICK_RATIO * (local + 1e-5)))
    if len(hits) == 0:
        return 0
    return int(1 + np.sum(np.diff(hits) > sample_rate * 0.005))


def measure(audio: np.ndarray, sample_rate: int) -> dict:
    x = _mono(audio)
    if len(x) == 0:
        return {"peak": 0.0, "clip_ratio": 0.0, "noise_floor_db": -120.0, "clicks_per_min": 0.0, "lufs": None}
    rms = frame_rms(x, sample_rate)
    floor = float(np.percentile(rms, 10))
    seconds = len(x) / sample_rate
    lufs = None
    if seconds >= 0.5:
        try:
            import pyloudnorm as pyln

            value = pyln.Meter(sample_rate).integrated_loudness(x.astype(np.float64))
            lufs = float(value) if np.isfinite(value) else None
        except Exception:
            lufs = None
    return {
        "peak": float(np.max(np.abs(x))),
        "clip_ratio": float(np.mean(np.abs(x) >= _CLIP_LEVEL)),
        "noise_floor_db": 20 * float(np.log10(floor + 1e-9)),
        "clicks_per_min": count_clicks(x, sample_rate) / seconds * 60,
        "lufs": lufs,
    }


def estimate_snr_db(audio: np.ndarray, sample_rate: int) -> float:
    """Loud frames against quiet frames; good enough to rank recordings."""
    rms = frame_rms(_mono(audio), sample_rate)
    return 20 * float(np.log10((np.percentile(rms, 90) + 1e-9) / (np.percentile(rms, 10) + 1e-9)))
