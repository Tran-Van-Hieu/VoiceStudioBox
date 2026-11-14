"""Emotion model shared by every engine.

Engines express emotion in different ways, so one setting is translated into
whichever controls each engine really has:

* ``instruct`` prose — Qwen CustomVoice / VoiceDesign.
* ``exaggeration`` / ``cfg_weight`` — Chatterbox Multilingual.
* the reference clip — any cloning engine follows the emotion of its sample,
  so a profile can hold one sample per emotion.
* prosody (tempo, pitch, level) after synthesis — the fallback for engines with
  no emotion control, and scaled down wherever a stronger control is active.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Optional

import numpy as np


@dataclass(frozen=True)
class EmotionSpec:
    instruct: str  # English prose for instruct-driven engines
    exaggeration: float  # Chatterbox value at full intensity
    cfg_weight: float  # Chatterbox value at full intensity
    speed: float  # tempo multiplier at full intensity
    pitch: float  # semitones at full intensity
    gain_db: float  # level change at full intensity


NEUTRAL = "neutral"

EMOTIONS: dict[str, EmotionSpec] = {
    NEUTRAL: EmotionSpec("", 0.5, 0.5, 1.0, 0.0, 0.0),
    "happy": EmotionSpec("in a happy, warm and smiling tone", 0.8, 0.45, 1.06, 0.7, 0.5),
    "sad": EmotionSpec("in a sad, heavy tone with a slow pace", 0.65, 0.35, 0.9, -0.7, -1.5),
    "angry": EmotionSpec("in an angry, tense and forceful tone", 1.0, 0.4, 1.08, 0.4, 1.5),
    "excited": EmotionSpec("in an excited, energetic and lively tone", 1.1, 0.4, 1.12, 1.0, 1.0),
    "calm": EmotionSpec("in a calm, gentle and soothing tone", 0.35, 0.55, 0.94, -0.3, -1.0),
    "serious": EmotionSpec("in a serious, formal and steady tone", 0.4, 0.6, 0.97, -0.3, 0.0),
    "fearful": EmotionSpec("in a fearful, trembling and anxious tone", 0.85, 0.4, 1.05, 0.5, -1.0),
    "whisper": EmotionSpec("in a soft, breathy whisper", 0.3, 0.5, 0.95, 0.0, -6.0),
    "storytelling": EmotionSpec(
        "like a storyteller, expressive with natural dramatic pauses", 0.7, 0.45, 0.96, 0.0, 0.0
    ),
}

# Names accepted in inline tags such as "[vui] Hôm nay trời đẹp."
_ALIASES: dict[str, str] = {
    **{name: name for name in EMOTIONS},
    "bình thường": NEUTRAL,
    "trung tính": NEUTRAL,
    "vui": "happy",
    "vui vẻ": "happy",
    "buồn": "sad",
    "giận": "angry",
    "tức giận": "angry",
    "hào hứng": "excited",
    "phấn khích": "excited",
    "nhẹ nhàng": "calm",
    "bình tĩnh": "calm",
    "nghiêm túc": "serious",
    "sợ": "fearful",
    "sợ hãi": "fearful",
    "thì thầm": "whisper",
    "kể chuyện": "storytelling",
}

_TAG_RE = re.compile(
    r"\[\s*(" + "|".join(re.escape(a) for a in sorted(_ALIASES, key=len, reverse=True)) + r")\s*\]",
    re.IGNORECASE,
)

DEFAULT_INTENSITY = 0.6

# Engines whose model takes the emotion directly, so post-processing would
# only double it up.
INSTRUCT_PROSE_ENGINES = {"qwen_voice_design"}


# Engines that set the speaking rate while generating. Time-stretching their
# output afterwards is what made every clip sound phasey and robotic.
NATIVE_SPEED_ENGINES = {"omnivoice"}


def normalize_emotion(emotion: Optional[str]) -> str:
    return emotion if emotion in EMOTIONS else NEUTRAL


def parse_emotion_spans(text: str, default: Optional[str] = None) -> list[tuple[str, str]]:
    """Split *text* at inline emotion tags into ``(emotion, text)`` spans.

    A tag applies until the next one. Unknown brackets such as ``[laugh]`` are
    left in the text for the engines that understand them.
    """
    current = normalize_emotion(default)
    spans: list[tuple[str, str]] = []
    pos = 0
    for m in _TAG_RE.finditer(text):
        chunk = text[pos:m.start()].strip()
        if chunk:
            spans.append((current, chunk))
        current = _ALIASES[m[1].lower()]
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        spans.append((current, tail))
    return spans


def compose_instruct(
    engine: str, emotion: str, intensity: float, user_instruct: Optional[str]
) -> Optional[str]:
    """The ``instruct`` string for *engine*, merging the user's own text."""
    user = (user_instruct or "").strip()
    spec = EMOTIONS[normalize_emotion(emotion)]
    if engine in INSTRUCT_PROSE_ENGINES and spec.instruct:
        adverb = "slightly " if intensity < 0.4 else "very clearly " if intensity > 0.8 else ""
        sentence = f"Speak {adverb}{spec.instruct}."
        return f"{user} {sentence}" if user else sentence
    if engine == "omnivoice" and emotion == "whisper":
        # OmniVoice's vocabulary is closed; whisper is its only delivery style.
        if "whisper" in user.lower():
            return user
        return f"{user}, whisper" if user else "whisper"
    return user or None


def prosody_factor(engine: str, has_emotion_sample: bool) -> float:
    """How much post-synthesis prosody to add on top of the engine's own control."""
    if engine in INSTRUCT_PROSE_ENGINES:
        return 0.0
    if has_emotion_sample:
        return 0.25
    return 1.0


def apply_prosody(
    audio: np.ndarray, sample_rate: int, emotion: str, intensity: float, factor: float
) -> np.ndarray:
    """Apply the emotion's tempo and level, scaled by intensity × factor.

    ``spec.pitch`` is deliberately not applied: shifting a finished take by half
    a semitone dropped its naturalness score from 4.2 to 3.0 (robotic).
    """
    emotion = normalize_emotion(emotion)
    k = max(0.0, min(1.0, intensity)) * factor
    if emotion == NEUTRAL or k <= 0 or len(audio) == 0:
        return audio
    from .audio_post import change_speed

    spec = EMOTIONS[emotion]
    x = np.asarray(audio, dtype=np.float32)
    x = change_speed(x, sample_rate, 1.0 + (spec.speed - 1.0) * k)
    x = x * (10 ** (spec.gain_db * k / 20))
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 0.99:  # scale, never clip: clipped peaks crackle
        x = x * (0.99 / peak)
    return x.astype(np.float32)


@dataclass
class SynthStyle:
    """Per-call hints read by the engine backends."""

    emotion: str = NEUTRAL
    intensity: float = DEFAULT_INTENSITY
    length_scale: float = 1.0  # >1 gives fixed-length engines more time
    speed: float = 1.0  # read only by NATIVE_SPEED_ENGINES


_style: ContextVar[Optional[SynthStyle]] = ContextVar("voicebox_synth_style", default=None)


def current_style() -> SynthStyle:
    return _style.get() or SynthStyle()


@contextmanager
def use_style(style: SynthStyle) -> Iterator[SynthStyle]:
    token = _style.set(style)
    try:
        yield style
    finally:
        _style.reset(token)
