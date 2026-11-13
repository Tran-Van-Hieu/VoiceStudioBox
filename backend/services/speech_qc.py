"""Listen to generated speech and catch dropped words or garbage audio.

Each chunk is transcribed with Whisper and aligned against the text it should
contain. Diacritics are ignored because Whisper's tone marks are unreliable
while whole missing words are not: the check is looking for missing or
invented words, not grading pronunciation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import numpy as np

from ..utils.audio import has_tts_runaway, save_audio
from ..utils.audio_quality import measure
from ..utils import abort
from ..utils.vi_text import normalize_vietnamese
from . import extras, speaker

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"\[[^\]]*\]")
_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_HCMC_RE = re.compile(r"(?<!\w)TP\.?\s*HCM(?!\w)", re.IGNORECASE)

# Below this many words a one-word Whisper slip would fail the whole clip.
_SHORT_TEXT_WORDS = 4
_SHORT_TEXT_THRESHOLD = 0.5
# Heard words beyond the expected text, as a share of it, before they count.
_EXTRA_WORDS_ALLOWED = 0.3
# Same voice scores about 0.9 and a four-semitone shift about 0.7.
_MIN_SPEAKER_SIMILARITY = 0.62
_SPEAKER_PENALTY = 0.3
_MAX_NOISE_FLOOR_DB = -40.0
_ARTIFACT_PENALTY = 0.2
_MAX_CLIP_RATIO = 0.01
# The click detector fires on plosives, and on the human reference clips too
# (17 per minute on a clean real voice). With a limit of 0 every Qwen and
# Chatterbox chunk failed and was generated four times over.
MAX_CLICKS_PER_MIN = 30.0


def _words(text: str, language: str) -> list[str]:
    text = _TAG_RE.sub(" ", text)
    if language == "vi":
        # Whisper writes "TP.HCM" and "2026" for what was spoken in full.
        text = _HCMC_RE.sub("thành phố Hồ Chí Minh", text)
        text = normalize_vietnamese(text)
    text = unicodedata.normalize("NFD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c)).replace("đ", "d")
    return _NON_WORD_RE.sub(" ", text).split()


def score_transcript(expected: str, heard: str, language: str) -> tuple[float, float]:
    """Return ``(score, coverage)``, both 0-1, for a transcript against its text."""
    want, got = _words(expected, language), _words(heard, language)
    if not want:
        return 1.0, 1.0
    blocks = SequenceMatcher(a=want, b=got, autojunk=False).get_matching_blocks()
    matched = sum(b.size for b in blocks)
    coverage = matched / len(want)
    extra = max(0, len(got) - matched) / len(want)
    return max(0.0, coverage - max(0.0, extra - _EXTRA_WORDS_ALLOWED) * 0.5), coverage


@dataclass
class QCResult:
    passed: bool
    score: float
    coverage: float
    heard: str = ""
    similarity: float | None = None
    problem: str | None = None


@dataclass
class SpeechQC:
    """Stateful checker for one generation; collects a report as it goes."""

    language: str
    model_size: str
    threshold: float
    max_retries: int
    checked: int = 0
    retried: int = 0
    failed: list[dict] = field(default_factory=list)
    reference: np.ndarray | None = None
    lowest_similarity: float | None = None
    clicks_limit: float = MAX_CLICKS_PER_MIN

    async def evaluate(self, audio: np.ndarray, sample_rate: int, text: str) -> QCResult:
        try:
            with abort.checking():
                return await self._evaluate(audio, sample_rate, text)
        except abort.CheckSkipped:
            return QCResult(True, 1.0, 1.0, "(skipped)")

    async def _evaluate(self, audio: np.ndarray, sample_rate: int, text: str) -> QCResult:
        if has_tts_runaway(audio, sample_rate):
            return QCResult(False, 0.0, 0.0, "(unstable audio)")

        from . import transcribe

        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            save_audio(audio, path, sample_rate)
            heard = await transcribe.get_whisper_model().transcribe(
                path, language=self.language, model_size=self.model_size
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

        score, coverage = score_transcript(text, heard, self.language)
        limit = (
            _SHORT_TEXT_THRESHOLD
            if len(_words(text, self.language)) < _SHORT_TEXT_WORDS
            else self.threshold
        )
        problem = None if score >= limit else "words"
        similarity = await asyncio.to_thread(self._similarity, audio, sample_rate)
        if similarity is not None and similarity < _MIN_SPEAKER_SIMILARITY:
            problem = problem or "voice"
            score -= _SPEAKER_PENALTY
        if self._has_artifacts(audio, sample_rate):
            problem = problem or "noise"
            score -= _ARTIFACT_PENALTY
        return QCResult(problem is None, score, coverage, heard, similarity, problem)

    def _similarity(self, audio: np.ndarray, sample_rate: int) -> float | None:
        if self.reference is None:
            return None
        return speaker.similarity(self.reference, speaker.embed(audio, sample_rate))

    def _has_artifacts(self, audio: np.ndarray, sample_rate: int) -> bool:
        m = measure(audio, sample_rate)
        return (
            m["clicks_per_min"] > self.clicks_limit
            or m["noise_floor_db"] > _MAX_NOISE_FLOOR_DB
            or m["clip_ratio"] > _MAX_CLIP_RATIO
        )

    def record(self, text: str, attempts: int, best: QCResult) -> None:
        self.checked += 1
        if attempts > 1:
            self.retried += 1
        if best.similarity is not None:
            low = self.lowest_similarity
            self.lowest_similarity = best.similarity if low is None else min(low, best.similarity)
        if not best.passed:
            logger.warning("QC could not fix %r (score %.2f): %r", text[:80], best.score, best.heard[:120])
            self.failed.append(
                {"text": text, "score": round(best.score, 2), "heard": best.heard, "problem": best.problem}
            )

    def report(self) -> dict:
        low = self.lowest_similarity
        return {
            "checked": self.checked,
            "retried": self.retried,
            "failed": self.failed,
            "lowest_similarity": None if low is None else round(low, 3),
        }


def build_qc(engine: str, language: str) -> SpeechQC | None:
    """A checker for this generation, or None when QC is off or unavailable."""
    settings = extras.get_all()
    mode = settings["qc_mode"]
    if mode == "off":
        return None

    from ..backends import LANGUAGE_CODE_TO_NAME, get_stt_backend

    if language not in LANGUAGE_CODE_TO_NAME:
        return None
    model_size = settings["qc_whisper_model"]
    # Never start a surprise multi-GB download in the middle of a generation.
    if not get_stt_backend()._is_model_cached(model_size):
        logger.warning("Speech QC skipped: Whisper %s is not downloaded", model_size)
        return None
    return SpeechQC(language, model_size, settings["qc_threshold"], settings["qc_max_retries"])
