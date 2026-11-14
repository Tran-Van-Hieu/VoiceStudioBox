"""How natural a clip sounds, 1-5 (UTMOS22). Used to pick the best designed take.

The 392 MB weights download once into the data folder on first use. Runs on the
CPU (about a second per clip) so it never competes with the speaking model for
VRAM. Returns None when the weights cannot be had; callers then skip the score.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

from .. import config

logger = logging.getLogger(__name__)

_URL = "https://github.com/tarepan/SpeechMOS/releases/download/v1.0.0/utmos22_strong_step7459_v1.pt"
_WINDOW_S = 8.0
_model = None
_lock = threading.Lock()


def _load():
    global _model
    with _lock:
        if _model is None:
            import torch

            from ..utils.utmos.model import UTMOS22Strong

            folder = config.get_data_dir() / "models" / "utmos"
            folder.mkdir(parents=True, exist_ok=True)
            weights = torch.hub.load_state_dict_from_url(_URL, model_dir=str(folder), map_location="cpu", progress=False)
            model = UTMOS22Strong()
            model.load_state_dict(weights)
            _model = model.eval()
    return _model


def score(audio: np.ndarray, sample_rate: int) -> Optional[float]:
    """Mean naturalness over 8 s windows, or None if the scorer is unavailable."""
    try:
        import torch

        model = _load()
        x = np.asarray(audio, dtype=np.float32)
        win = int(sample_rate * _WINDOW_S)
        starts = range(0, max(1, len(x) - win + 1), win) if len(x) > win else [0]
        with torch.no_grad():
            values = [float(model(torch.from_numpy(x[s : s + win])[None], sample_rate)) for s in starts]
        return round(float(np.mean(values)), 2)
    except Exception as e:
        logger.warning("Naturalness score unavailable: %s", e)
        return None


def release() -> None:
    """Free the scorer's memory (about 400 MB of RAM)."""
    global _model
    with _lock:
        _model = None
