"""Noise removal must be skipped, not crash, where DeepFilterNet is missing (Colab, Python 3.12)."""

import sys

import numpy as np

from backend.services import audio_clean


def test_denoise_without_deepfilternet_returns_input(monkeypatch):
    monkeypatch.setitem(sys.modules, "df.enhance", None)  # makes the import raise ImportError
    audio = np.linspace(-0.5, 0.5, 4800, dtype=np.float32)
    out = audio_clean.denoise(audio, 24000, 1.0)
    np.testing.assert_array_equal(out, audio)
