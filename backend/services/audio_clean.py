"""Vocal separation (Demucs) and noise removal (DeepFilterNet).

Both load, run, and free their models per call: on an 8 GB card they must
never sit in VRAM next to a TTS model. Measured on an RTX 4060 for 33 s of
audio: Demucs 10.5 s and 0.7 GB peak, DeepFilterNet 6 s and 0.3 GB peak.
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import numpy as np

from ..utils import abort

logger = logging.getLogger(__name__)

_SEGMENT_SECONDS = 7.0


def _free_gpu() -> None:
    import gc

    import torch

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def separate(input_path: Path, output_dir: Path) -> tuple[Path, Path]:
    """Split a file into ``vocals.wav`` and ``background.wav``."""
    import librosa
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = get_model("htdemucs").to(device).eval()
    abort.guard(model)
    try:
        audio, _ = librosa.load(str(input_path), sr=model.samplerate, mono=False)
        if audio.ndim == 1:
            audio = np.stack([audio, audio])
        audio = audio[:2]
        wav = torch.tensor(audio, dtype=torch.float32)
        reference = wav.mean(0)
        mean, std = reference.mean(), reference.std() + 1e-8
        batch = ((wav - mean) / std)[None]
        # The batch stays on the CPU so a long video only ever puts one
        # segment at a time on the GPU.
        with torch.no_grad(), torch.autocast(device, dtype=torch.float16, enabled=device == "cuda"):
            sources = apply_model(
                model, batch, split=True, overlap=0.25, shifts=0, segment=_SEGMENT_SECONDS, device=device, progress=False
            )
        sources = sources[0].float().cpu() * std + mean
        index = model.sources.index("vocals")
        vocals = sources[index].numpy().T
        background = (sources.sum(0) - sources[index]).numpy().T
        rate = model.samplerate
    finally:
        del model
        _free_gpu()
    output_dir.mkdir(parents=True, exist_ok=True)
    vocals_path, background_path = output_dir / "vocals.wav", output_dir / "background.wav"
    sf.write(str(vocals_path), vocals, rate, subtype="FLOAT")
    sf.write(str(background_path), background, rate, subtype="FLOAT")
    return vocals_path, background_path


def _ensure_df_importable() -> None:
    # DeepFilterNet imports this type at module scope; torchaudio 2.x removed
    # the module, and the audio I/O that used it is not called from here.
    if "torchaudio.backend.common" in sys.modules:
        return
    backend = types.ModuleType("torchaudio.backend")
    common = types.ModuleType("torchaudio.backend.common")
    common.AudioMetaData = type("AudioMetaData", (), {})
    backend.common = common
    sys.modules["torchaudio.backend"] = backend
    sys.modules["torchaudio.backend.common"] = common


def denoise(audio: np.ndarray, sample_rate: int, strength: float = 1.0) -> np.ndarray:
    """Remove background noise. ``strength`` 0-1 limits how much is taken out."""
    import librosa
    import torch

    x = np.asarray(audio, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if len(x) == 0 or strength <= 0:
        return x
    abort.check()
    _ensure_df_importable()
    try:
        from df.enhance import enhance, init_df
    except ImportError:
        # No DeepFilterNet wheels for Python 3.12 (e.g. Colab): keep the audio as is.
        logger.warning("DeepFilterNet is not installed; skipping noise removal")
        return x

    model, state, _ = init_df(log_level="ERROR")
    try:
        rate = state.sr()
        wave = librosa.resample(x, orig_sr=sample_rate, target_sr=rate) if sample_rate != rate else x
        # 100 dB is effectively unlimited; 12 dB keeps a natural room tone.
        limit = 12 + 88 * float(min(1.0, strength)) ** 2
        with torch.no_grad():
            cleaned = enhance(model, state, torch.tensor(wave, dtype=torch.float32)[None], atten_lim_db=limit)
        cleaned = cleaned.squeeze().cpu().numpy().astype(np.float32)
    finally:
        del model, state
        _free_gpu()
    if sample_rate != rate:
        cleaned = librosa.resample(cleaned, orig_sr=rate, target_sr=sample_rate)
    if len(cleaned) < len(x):
        cleaned = np.pad(cleaned, (0, len(x) - len(cleaned)))
    return np.asarray(cleaned[: len(x)], dtype=np.float32)
