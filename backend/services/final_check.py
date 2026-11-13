"""The last gate before a user gets a file: listen to the finished audio.

Chunk QC checks pieces as they are made. This checks what was actually
assembled, after crossfades, speed change, and loudness, and points at the
places to look at.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Optional

import numpy as np

from ..utils.audio import save_audio
from ..utils.audio_quality import measure
from ..utils.text_rules import build_text_transform
from . import extras, speaker, speech_qc

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 25.0
_RED_SCORE = 0.60
_YELLOW_SCORE = 0.80
_LONG_PAUSE_SECONDS = 1.5
_SILENCE_DB = 40
_RED_SPEAKER = 0.55
_YELLOW_SPEAKER = 0.68
_CLIP_LIMIT = 0.002
_CLICKS_PER_MIN = 0.5
_NOISE_FLOOR_DB = -45.0


def _windows(segments: list[dict]) -> list[list[int]]:
    """Group consecutive segments into stretches Whisper hears reliably."""
    groups: list[list[int]] = []
    current: list[int] = []
    start = 0.0
    for i, seg in enumerate(segments):
        if current and seg["end"] - start > _WINDOW_SECONDS:
            groups.append(current)
            current = []
        if not current:
            start = seg["start"]
        current.append(i)
    if current:
        groups.append(current)
    return groups


def _long_pauses(audio: np.ndarray, sample_rate: int) -> list[tuple[float, float]]:
    import librosa

    spans = librosa.effects.split(audio, top_db=_SILENCE_DB, frame_length=1024, hop_length=256)
    return [
        (float(a[1]) / sample_rate, float(b[0]) / sample_rate)
        for a, b in zip(spans[:-1], spans[1:])
        if (b[0] - a[1]) / sample_rate > _LONG_PAUSE_SECONDS
    ]


async def _listen_piecewise(x, sample_rate, segments, group, language, model_size) -> str:
    from . import transcribe

    heard = []
    for i in group:
        piece = x[int(segments[i]["start"] * sample_rate) : int(segments[i]["end"] * sample_rate)]
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            save_audio(piece, path, sample_rate)
            heard.append(await transcribe.get_whisper_model().transcribe(path, language=language, model_size=model_size))
        finally:
            os.unlink(path)
    return " ".join(heard)


async def run(
    audio: np.ndarray,
    sample_rate: int,
    segments: list[dict],
    language: str,
    engine: str,
    reference: Optional[np.ndarray],
    chunk_report: Optional[dict] = None,
) -> Optional[dict]:
    """Return ``{level, score, metrics, issues}``, or None when Whisper is unavailable."""
    from . import transcribe

    qc = speech_qc.build_qc(engine, language)
    if qc is None:
        return None
    x = np.asarray(audio, dtype=np.float32)
    transform = build_text_transform(language)
    issues: list[dict] = []
    scores: list[float] = []
    speakers: list[float] = []

    for group in _windows(segments):
        start, end = segments[group[0]]["start"], segments[group[-1]]["end"]
        piece = x[int(start * sample_rate) : int(end * sample_rate)]
        if len(piece) < sample_rate // 4:
            continue
        text = " ".join(str(segments[i].get("text", "")) for i in group)
        expected = transform(text) if transform else text
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            save_audio(piece, path, sample_rate)
            heard = await transcribe.get_whisper_model().transcribe(path, language=language, model_size=qc.model_size)
        finally:
            os.unlink(path)
        score, _ = speech_qc.score_transcript(expected, heard, language)
        diff = speech_qc.word_diff(expected, heard, language, qc.tones)
        if (diff if qc.exact else score < _YELLOW_SCORE) and len(group) > 1:
            # Whisper sometimes invents a stock sentence for a whole stretch of
            # fine speech ("Hãy subscribe cho kênh..."); shorter pieces expose that.
            second = await _listen_piecewise(x, sample_rate, segments, group, language, qc.model_size)
            better, _ = speech_qc.score_transcript(expected, second, language)
            second_diff = speech_qc.word_diff(expected, second, language, qc.tones)
            if (len(second_diff) < len(diff)) if qc.exact else better > score:
                score, heard, diff = better, second, second_diff
        scores.append(score)
        if diff if qc.exact else score < _YELLOW_SCORE:
            issues.append(
                {
                    "kind": "words",
                    "severity": "red" if qc.exact or score < _RED_SCORE else "yellow",
                    "diff": diff,
                    "start": start,
                    "end": end,
                    "value": round(score, 2),
                    "text": text,
                    "heard": heard,
                    "segments": group,
                }
            )
        similarity = speaker.similarity(reference, speaker.embed(piece, sample_rate))
        if similarity is not None:
            speakers.append(similarity)
            if similarity < _YELLOW_SPEAKER:
                issues.append(
                    {
                        "kind": "voice",
                        "severity": "red" if similarity < _RED_SPEAKER else "yellow",
                        "start": start,
                        "end": end,
                        "value": round(similarity, 2),
                        "text": text,
                        "segments": group,
                    }
                )

    for a, b in _long_pauses(x, sample_rate):
        issues.append({"kind": "pause", "severity": "yellow", "start": a, "end": b, "value": round(b - a, 1)})

    metrics = measure(x, sample_rate)
    if metrics["clip_ratio"] > _CLIP_LIMIT:
        issues.append({"kind": "clipping", "severity": "yellow", "value": round(metrics["clip_ratio"] * 100, 2)})
    if metrics["clicks_per_min"] > max(_CLICKS_PER_MIN, qc.clicks_limit):
        issues.append({"kind": "clicks", "severity": "yellow", "value": round(metrics["clicks_per_min"])})
    if metrics["noise_floor_db"] > _NOISE_FLOOR_DB:
        issues.append({"kind": "noise", "severity": "yellow", "value": round(metrics["noise_floor_db"], 1)})
    if chunk_report and chunk_report.get("failed"):
        issues.append({"kind": "chunks", "severity": "yellow", "value": len(chunk_report["failed"])})

    severities = {i["severity"] for i in issues}
    level = "red" if "red" in severities else "yellow" if severities else "green"
    return {
        "level": level,
        "score": round(min(scores), 3) if scores else None,
        "speaker": round(min(speakers), 3) if speakers else None,
        "metrics": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in metrics.items()},
        "issues": issues,
        "threshold": extras.get("qc_threshold"),
        "match": "exact" if qc.exact else "tolerant",
    }
