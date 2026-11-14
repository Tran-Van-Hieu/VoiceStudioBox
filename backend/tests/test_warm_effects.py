"""Warm presets: valid chains that add bass without clipping."""
import numpy as np

from backend.utils.effects import BUILTIN_PRESETS, apply_effects, validate_effects_chain


def test_warm_presets_add_bass_and_never_clip():
    sr = 24000
    t = np.arange(sr) / sr
    audio = (0.5 * np.sin(2 * np.pi * 120 * t) + 0.45 * np.sin(2 * np.pi * 3000 * t)).astype(np.float32)
    for key in ("warm", "warm_deep"):
        chain = BUILTIN_PRESETS[key]["effects_chain"]
        assert validate_effects_chain(chain) is None
        out = apply_effects(audio, sr, chain)
        assert np.max(np.abs(out)) <= 0.99 + 1e-6
        low = lambda x: np.abs(np.fft.rfft(x))[120]  # 1 Hz bins: the 120 Hz tone
        high = lambda x: np.abs(np.fft.rfft(x))[3000]
        assert low(out) / high(out) > low(audio) / high(audio) * 1.2
