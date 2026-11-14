"""
Chunked TTS generation utilities.

Splits long text into sentence-boundary chunks, generates audio per-chunk
via any TTSBackend, and concatenates with crossfade.  All logic is
engine-agnostic — it wraps the standard ``TTSBackend.generate()`` interface.

Short text (≤ max_chunk_chars) uses the single-shot fast path with zero
overhead.
"""

import logging
import re
from typing import List, Tuple

import numpy as np

from . import abort

logger = logging.getLogger("voicebox.chunked-tts")

# Default chunk size in characters.  Can be overridden per-request via
# the ``max_chunk_chars`` field on GenerationRequest.
DEFAULT_MAX_CHUNK_CHARS = 800
MAX_RUNAWAY_RETRIES = 2
MIN_RUNAWAY_RETRY_CHARS = 100

# Common abbreviations that should NOT be treated as sentence endings.
# Lowercase for case-insensitive matching.
_ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "ave",
        "blvd",
        "inc",
        "ltd",
        "corp",
        "dept",
        "est",
        "approx",
        "vs",
        "etc",
        "e.g",
        "i.e",
        "a.m",
        "p.m",
        "u.s",
        "u.s.a",
        "u.k",
    }
)

# Paralinguistic tags used by Chatterbox Turbo.  The splitter must never
# cut inside one of these.
_PARA_TAG_RE = re.compile(r"\[[^\]]*\]")


def split_text_into_chunks(text: str, max_chars: int = DEFAULT_MAX_CHUNK_CHARS) -> List[str]:
    """Split *text* at natural boundaries into chunks of at most *max_chars*.

    Priority: sentence-end (``.!?`` not preceded by an abbreviation and not
    inside brackets) → clause boundary (``;:,—``) → whitespace → hard cut.

    Paralinguistic tags like ``[laugh]`` are treated as atomic and will not
    be split across chunks.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    remaining = text

    while remaining:
        remaining = remaining.lstrip()
        if not remaining:
            break
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break

        segment = remaining[:max_chars]

        # Try to split at the last real sentence ending
        split_pos = _find_last_sentence_end(segment)
        if split_pos == -1:
            split_pos = _find_last_clause_boundary(segment)
        if split_pos == -1:
            split_pos = _balanced_space_split(remaining, segment, max_chars)
        if split_pos == -1:
            # Absolute fallback: hard cut but avoid splitting inside a tag
            split_pos = _safe_hard_cut(segment, max_chars)

        chunk = remaining[: split_pos + 1].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_pos + 1 :]

    return chunks


_SENTENCE_END_RE = re.compile(r"""[.!?\u2026\u3002\uff01\uff1f]+["'\u201d\u2019)\]]*(?=\s|$)""")


def split_sentences(text: str) -> List[str]:
    """Whole sentences of *text*. A line without end punctuation is one sentence."""
    out: List[str] = []
    for line in re.split(r"\r?\n", text):
        line = line.strip()
        start = 0
        for match in _SENTENCE_END_RE.finditer(line):
            pos = match.start()
            if line[pos] == ".":
                word = re.search(r"([\w.]+)$", line[start:pos])
                if word and word[1].lower() in _ABBREVIATIONS:
                    continue
            if _inside_bracket_tag(line, pos):
                continue
            sentence = line[start : match.end()].strip()
            if sentence:
                out.append(sentence)
            start = match.end()
        rest = line[start:].strip()
        if rest:
            out.append(rest)
    return out


def split_script(text: str, max_chars: int) -> List[str]:
    """Group whole sentences into parts of about *max_chars*.

    A sentence is never broken: one longer than the limit becomes its own part.
    """
    parts: List[str] = []
    current = ""
    for sentence in split_sentences(text):
        if current and len(current) + 1 + len(sentence) > max_chars:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


def _balanced_space_split(remaining: str, segment: str, max_chars: int) -> int:
    """Pick the space to cut at when a clause is longer than one chunk.

    Cutting as late as possible strands the clause's last word on its own
    ("... không | nhỏ."), so a clause that fits in two chunks is halved.
    """
    end = re.search(r"[.!?,;:\u2026\u3002\uff01\uff1f](?:\s|$)", remaining[max_chars:])
    clause_len = max_chars + end.end() if end else len(remaining)
    if clause_len < 2 * max_chars:
        middle = segment.rfind(" ", 0, clause_len // 2 + 1)
        if middle > 0:
            return middle
    return segment.rfind(" ")


def _find_last_sentence_end(text: str) -> int:
    """Return the index of the last sentence-ending punctuation in *text*.

    Skips periods that follow common abbreviations (``Dr.``, ``Mr.``, etc.)
    and periods inside bracket tags (``[laugh]``).  Also handles CJK
    sentence-ending punctuation (``。！？``).
    """
    best = -1
    # ASCII sentence ends
    for m in re.finditer(r"[.!?](?:\s|$)", text):
        pos = m.start()
        char = text[pos]
        # Skip periods after abbreviations
        if char == ".":
            # Walk backwards to find the preceding word
            word_start = pos - 1
            while word_start >= 0 and text[word_start].isalpha():
                word_start -= 1
            word = text[word_start + 1 : pos].lower()
            if word in _ABBREVIATIONS:
                continue
            # Skip decimal numbers (digit immediately before the period)
            if word_start >= 0 and text[word_start].isdigit():
                continue
        # Skip if we're inside a bracket tag
        if _inside_bracket_tag(text, pos):
            continue
        best = pos
    # CJK sentence-ending punctuation
    for m in re.finditer(r"[\u3002\uff01\uff1f]", text):
        if m.start() > best:
            best = m.start()
    return best


def _find_last_clause_boundary(text: str) -> int:
    """Return the index of the last clause-boundary punctuation."""
    best = -1
    for m in re.finditer(r"[;:,\u2014](?:\s|$)", text):
        pos = m.start()
        # Skip if inside a bracket tag
        if _inside_bracket_tag(text, pos):
            continue
        best = pos
    return best


def _inside_bracket_tag(text: str, pos: int) -> bool:
    """Return True if *pos* falls inside a ``[...]`` tag."""
    for m in _PARA_TAG_RE.finditer(text):
        if m.start() < pos < m.end():
            return True
    return False


def _safe_hard_cut(segment: str, max_chars: int) -> int:
    """Find a hard-cut position that doesn't split a ``[tag]``."""
    cut = max_chars - 1
    # Check if the cut falls inside a bracket tag; if so, move before it
    for m in _PARA_TAG_RE.finditer(segment):
        if m.start() < cut < m.end():
            return m.start() - 1 if m.start() > 0 else cut
    return cut


def concatenate_audio_chunks(
    chunks: List[np.ndarray],
    sample_rate: int,
    crossfade_ms: int = 50,
) -> np.ndarray:
    """Concatenate audio arrays with a short crossfade to eliminate clicks.

    Each chunk is expected to be a 1-D float32 ndarray at *sample_rate* Hz.
    """
    if not chunks:
        return np.array([], dtype=np.float32)
    if len(chunks) == 1:
        return chunks[0]

    crossfade_samples = int(sample_rate * crossfade_ms / 1000)
    result = np.array(chunks[0], dtype=np.float32, copy=True)

    for chunk in chunks[1:]:
        if len(chunk) == 0:
            continue
        overlap = min(crossfade_samples, len(result), len(chunk))
        if overlap > 0:
            fade_out = np.linspace(1.0, 0.0, overlap, dtype=np.float32)
            fade_in = np.linspace(0.0, 1.0, overlap, dtype=np.float32)
            result[-overlap:] = result[-overlap:] * fade_out + chunk[:overlap] * fade_in
            result = np.concatenate([result, chunk[overlap:]])
        else:
            result = np.concatenate([result, chunk])

    return result


async def generate_chunked(
    backend,
    text: str,
    voice_prompt: dict,
    language: str = "en",
    seed: int | None = None,
    instruct: str | None = None,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    crossfade_ms: int = 50,
    trim_fn=None,
    runaway_detector=None,
    text_transform=None,
    segments_out: list | None = None,
    qc=None,
    sentence_pause_ms: int = 0,
) -> Tuple[np.ndarray, int]:
    """Generate audio with automatic chunking for long text.

    ``text_transform`` (optional ``str -> str``) rewrites each chunk right
    before synthesis (pronunciation dictionary) while splitting and subtitle
    text keep the original wording. When ``segments_out`` is a list, one
    ``{start, end, text}`` dict per chunk is appended to it. ``qc`` (a
    ``SpeechQC``) listens to each chunk and regenerates the ones with missing
    words or garbage audio, keeping the best attempt.

    For text shorter than *max_chunk_chars* this is a thin wrapper around
    ``backend.generate()`` with zero overhead.

    For longer text the input is split at natural sentence boundaries,
    each chunk is generated independently, optionally trimmed (useful for
    Chatterbox engines that hallucinate trailing noise), and the results
    are concatenated with a crossfade (or hard cut if *crossfade_ms* is 0).

    Parameters
    ----------
    backend : TTSBackend
        Any backend implementing the ``generate()`` protocol.
    text : str
        Input text (may be arbitrarily long).
    voice_prompt, language, seed, instruct
        Forwarded to ``backend.generate()`` verbatim.
    max_chunk_chars : int
        Maximum characters per chunk (default 800).
    crossfade_ms : int
        Crossfade duration in milliseconds between chunks.  0 for a hard
        cut with no overlap (default 50).
    trim_fn : callable | None
        Optional ``(audio, sample_rate) -> audio`` post-processing
        function applied to each chunk before concatenation (e.g.
        ``trim_tts_output`` for Chatterbox engines).
    runaway_detector : callable | None
        Optional ``(audio, sample_rate) -> bool`` detector. When it flags
        unstable output, the affected text is split in half and retried.

    Returns
    -------
    (audio, sample_rate) : Tuple[np.ndarray, int]
    """
    def spoken(chunk_text: str) -> str:
        if text_transform is None:
            return chunk_text
        out = text_transform(chunk_text) or chunk_text
        return out if out.strip() else chunk_text

    async def generate_one(
        chunk_text: str,
        chunk_seed: int | None,
        retry_depth: int = 0,
        premade: tuple[np.ndarray, int] | None = None,
    ) -> tuple[np.ndarray, int]:
        if premade is not None:
            chunk_audio, chunk_sr = premade
        else:
            chunk_audio, chunk_sr = await backend.generate(
                spoken(chunk_text),
                voice_prompt,
                language,
                chunk_seed,
                instruct,
            )

        if runaway_detector is not None and runaway_detector(chunk_audio, chunk_sr):
            if retry_depth >= MAX_RUNAWAY_RETRIES or len(chunk_text) <= MIN_RUNAWAY_RETRY_CHARS:
                if trim_fn is not None:
                    # Engines with a trim step (Chatterbox) already cut the
                    # silence-then-noise tail; prefer the trimmed clip over
                    # failing the whole generation when we cannot split further.
                    logger.warning(
                        "Unstable TTS output for %d chars could not be retried further; keeping trimmed output",
                        len(chunk_text),
                    )
                    return np.asarray(trim_fn(chunk_audio, chunk_sr), dtype=np.float32), chunk_sr
                raise RuntimeError(
                    "TTS output remained unstable after retrying smaller text chunks"
                )

            retry_max_chars = max(MIN_RUNAWAY_RETRY_CHARS, len(chunk_text) // 2)
            retry_chunks = split_text_into_chunks(chunk_text, retry_max_chars)
            if len(retry_chunks) <= 1:
                raise RuntimeError("Unable to split unstable TTS output for retry")

            logger.warning(
                "Detected unstable TTS output for %d chars; retrying as %d smaller chunks",
                len(chunk_text),
                len(retry_chunks),
            )
            retry_audio: list[np.ndarray] = []
            for i, retry_text in enumerate(retry_chunks):
                retry_seed = (
                    chunk_seed + ((retry_depth + 1) * 1000) + i
                    if chunk_seed is not None
                    else None
                )
                audio, sample_rate = await generate_one(
                    retry_text,
                    retry_seed,
                    retry_depth + 1,
                )
                retry_audio.append(np.asarray(audio, dtype=np.float32))

            return (
                concatenate_audio_chunks(
                    retry_audio,
                    sample_rate,
                    crossfade_ms=crossfade_ms,
                ),
                sample_rate,
            )

        if trim_fn is not None:
            chunk_audio = trim_fn(chunk_audio, chunk_sr)
        return np.asarray(chunk_audio, dtype=np.float32), chunk_sr

    async def generate_checked(
        chunk_text: str, chunk_seed: int | None, premade: tuple[np.ndarray, int] | None = None
    ) -> tuple[np.ndarray, int]:
        abort.check()
        # With no retries allowed, listening here only doubles the final check's work.
        if qc is None or qc.max_retries == 0 or abort.checks_skipped():
            return await generate_one(chunk_text, chunk_seed, premade=premade)

        from dataclasses import replace

        from .expressive import current_style, use_style

        # Compare against what was actually spoken, not the raw text: a
        # dictionary entry or a spelled-out number differs from what was typed.
        expected = text_transform(chunk_text) if text_transform is not None else chunk_text
        best = best_audio = best_sr = None
        length_scale = 1.0
        attempts = 0
        for attempt in range(qc.max_retries + 1):
            attempts = attempt + 1
            attempt_seed = (
                chunk_seed if attempt == 0 or chunk_seed is None else chunk_seed + 7919 * attempt
            )
            with use_style(replace(current_style(), length_scale=length_scale)):
                candidate, candidate_sr = await generate_one(
                    chunk_text, attempt_seed, premade=premade if attempt == 0 else None
                )
            result = await qc.evaluate(candidate, candidate_sr, expected)
            if best is None or result.score > best.score:
                best, best_audio, best_sr = result, candidate, candidate_sr
            if result.passed:
                break
            if result.coverage < 0.9:
                length_scale = min(1.45, length_scale + 0.15)
            logger.info(
                "QC attempt %d failed (score %.2f, coverage %.2f); retrying",
                attempts,
                result.score,
                result.coverage,
            )
        qc.record(chunk_text, attempts, best)
        return best_audio, best_sr

    chunks = split_text_into_chunks(text, max_chunk_chars)

    if len(chunks) <= 1:
        # Short text — single-shot fast path
        audio, sample_rate = await generate_checked(text, seed)
        if segments_out is not None:
            segments_out.append(
                {"start": 0.0, "end": len(audio) / sample_rate, "text": text.strip()}
            )
        return audio, sample_rate

    # Long text — chunked generation
    logger.info(
        "Splitting %d chars into %d chunks (max %d chars each)",
        len(text),
        len(chunks),
        max_chunk_chars,
    )
    audio_chunks: List[np.ndarray] = []
    sample_rate: int | None = None
    first_takes = await _batched_first_takes(backend, chunks, spoken, voice_prompt, language, seed, instruct)

    for i, chunk_text in enumerate(chunks):
        logger.info(
            "Generating chunk %d/%d (%d chars)",
            i + 1,
            len(chunks),
            len(chunk_text),
        )
        # Vary the seed per chunk to avoid correlated RNG artefacts,
        # but keep it deterministic so the same (text, seed) pair
        # always produces the same output.
        chunk_seed = (seed + i) if seed is not None else None

        chunk_audio, chunk_sr = await generate_checked(chunk_text, chunk_seed, first_takes.get(i))

        if sentence_pause_ms > 0 and i < len(chunks) - 1 and chunk_text.rstrip()[-1:] in ".!?…。！？":
            pause = np.zeros(int(chunk_sr * sentence_pause_ms / 1000), dtype=np.float32)
            chunk_audio = np.concatenate([chunk_audio, pause])

        audio_chunks.append(chunk_audio)
        if sample_rate is None:
            sample_rate = chunk_sr

    audio = concatenate_audio_chunks(audio_chunks, sample_rate, crossfade_ms=crossfade_ms)
    if segments_out is not None:
        segments_out.extend(chunk_segments(audio_chunks, chunks, sample_rate, crossfade_ms))
    return audio, sample_rate


def batch_size(device: str) -> int:
    """Chunks per pass: Qwen 1.7B with 4 chunks peaked at 5.9 GB on an 8 GB card."""
    try:
        import torch

        if str(device).startswith("cuda") and torch.cuda.is_available():
            total = torch.cuda.get_device_properties(0).total_memory / 2**30
            return 8 if total >= 14 else 4
    except Exception:
        pass
    return 1


async def _batched_first_takes(backend, chunks, spoken, voice_prompt, language, seed, instruct) -> dict:
    """First take of every chunk, made several at a time by engines that can.

    Engines without ``generate_batch`` (OmniVoice: already fills the GPU, a batch
    was slower) and CPU runs return nothing, and chunks are made one by one.
    """
    batch = getattr(backend, "generate_batch", None)
    size = batch_size(getattr(backend, "device", "cpu")) if batch is not None else 1
    if size < 2 or len(chunks) < 2:
        return {}
    takes: dict = {}
    for start in range(0, len(chunks), size):
        abort.check()
        group = chunks[start : start + size]
        logger.info("Generating chunks %d-%d of %d in one pass", start + 1, start + len(group), len(chunks))
        result = await batch(
            [spoken(c) for c in group], voice_prompt, language,
            None if seed is None else seed + start, instruct,
        )
        if result is None:  # this prompt cannot be batched
            return {}
        for j, take in enumerate(result):
            takes[start + j] = (np.asarray(take[0], dtype=np.float32), take[1])
    return takes


def chunk_segments(
    audio_chunks: List[np.ndarray],
    texts: List[str],
    sample_rate: int,
    crossfade_ms: int,
) -> List[dict]:
    """Start/end (seconds) of each chunk inside the crossfaded result.

    Mirrors the overlap arithmetic of :func:`concatenate_audio_chunks`.
    """
    crossfade_samples = int(sample_rate * crossfade_ms / 1000)
    segments: List[dict] = []
    cur_len = 0
    for i, (chunk, text) in enumerate(zip(audio_chunks, texts)):
        n = len(chunk)
        if i == 0:
            start = 0
        else:
            if n == 0:
                continue
            start = cur_len - min(crossfade_samples, cur_len, n)
        end = start + n
        cur_len = max(cur_len, end)
        segments.append({"start": start / sample_rate, "end": end / sample_rate, "text": text})
    return segments
