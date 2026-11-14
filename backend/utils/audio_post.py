"""Post-processing shared by every engine: speed, loudness, encoding, subtitles.

* ``change_speed``  – tempo change without pitch shift (Rubber Band via
  pedalboard, librosa phase vocoder as fallback).
* ``normalize_loudness`` – EBU R128 / BS.1770 integrated loudness to a LUFS
  target with a -1 dBFS peak ceiling.
* ``encode_audio_bytes`` – WAV / FLAC / MP3 with selectable bitrate. MP3 uses
  ffmpeg when installed, otherwise libsndfile's built-in LAME encoder.
* ``build_cues`` / ``to_srt`` – subtitle cues from per-chunk timing.
"""

from __future__ import annotations

import io
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable

import numpy as np

logger = logging.getLogger(__name__)

PEAK_CEILING = 10 ** (-1.0 / 20)  # -1 dBFS


# ── Speed ──────────────────────────────────────────────────────────────────


# Pauses may grow to this many times their length (never past _PAUSE_MAX_SECONDS,
# the final check flags 1.5 s as too long) or shrink to this share, before the
# rest of a speed change falls to time-stretching.
_PAUSE_MAX_GROWTH = 3.0
_PAUSE_MAX_SECONDS = 1.2
_PAUSE_MIN_SHARE = 0.35
_PAUSE_MIN_MS = 80


def _pause_runs(x: np.ndarray, sample_rate: int) -> list[tuple[int, int]]:
    """(start, end) samples of the silences between words and sentences."""
    frame = max(1, int(sample_rate * 0.01))
    n = len(x) // frame
    if n < 10:
        return []
    db = 20 * np.log10(np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1)) + 1e-9)
    quiet = db < np.percentile(db, 95) - 35
    runs, start = [], None
    for i, q in enumerate(np.append(quiet, False)):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * 10 >= _PAUSE_MIN_MS:
                runs.append((start * frame, i * frame))
            start = None
    return runs


def _resize_pauses(x: np.ndarray, sample_rate: int, speed: float) -> np.ndarray:
    """Reach as much of *speed* as possible by resizing silences alone."""
    runs = _pause_runs(x, sample_rate)
    if not runs:
        return x
    lengths = np.array([b - a for a, b in runs], dtype=np.float64)
    want = len(x) / speed - len(x)  # samples to add (negative: remove)
    grow = want > 0
    if grow:
        bound = np.maximum(lengths, np.minimum(lengths * _PAUSE_MAX_GROWTH, _PAUSE_MAX_SECONDS * sample_rate))
    else:
        bound = lengths * _PAUSE_MIN_SHARE
    new, left = lengths.copy(), want
    for _ in range(8):  # spread the change; a pause at its bound passes its share on
        room = np.abs(bound - new)
        open_ = room > 0.5
        if abs(left) < 1 or not open_.any():
            break
        share = left * lengths * open_ / (lengths * open_).sum()
        step = np.clip(share, 0, room) if grow else -np.clip(-share, 0, room)
        new += step
        left -= step.sum()
    out, pos = [], 0
    for (a, b), size in zip(runs, new.astype(int)):
        out.append(x[pos:a])
        if size >= b - a:
            out += [x[a:b], np.zeros(size - (b - a), dtype=np.float32)]
        else:
            half = size // 2  # keep both edges of the silence, drop its middle
            out += [x[a : a + half], x[b - (size - half) : b]]
        pos = b
    out.append(x[pos:])
    return np.concatenate(out)


def change_speed(audio: np.ndarray, sample_rate: int, speed: float) -> np.ndarray:
    """Return *audio* played *speed* times faster (pitch preserved).

    Pauses are resized first: time-stretching the speech itself dropped Qwen's
    naturalness score from 4.2 to 3.5 at 0.9x, resizing pauses only to 4.2.
    Only what pauses cannot absorb is time-stretched, at a much milder ratio.
    """
    if audio is None or len(audio) == 0 or abs(float(speed) - 1.0) < 0.01:
        return audio
    speed = float(min(2.0, max(0.5, speed)))
    x = np.asarray(audio, dtype=np.float32)
    target = len(x) / speed
    x = _resize_pauses(x, sample_rate, speed)
    speed = len(x) / target
    if abs(speed - 1.0) < 0.01:
        return x
    try:
        from pedalboard import time_stretch

        out = time_stretch(x[np.newaxis, :], float(sample_rate), stretch_factor=speed)
        return np.asarray(out[0], dtype=np.float32)
    except Exception as e:
        logger.info("pedalboard time_stretch unavailable (%s); using librosa", e)
    import librosa

    return np.asarray(librosa.effects.time_stretch(x, rate=speed), dtype=np.float32)


# ── Loudness ───────────────────────────────────────────────────────────────


def normalize_loudness(audio: np.ndarray, sample_rate: int, target_lufs: float) -> np.ndarray:
    """Scale *audio* to *target_lufs* integrated loudness, peak-limited to -1 dBFS."""
    x = np.asarray(audio, dtype=np.float32)
    if len(x) < int(sample_rate * 0.4):  # BS.1770 needs at least one 400 ms block
        return x
    try:
        import pyloudnorm as pyln

        meter = pyln.Meter(sample_rate)
        loudness = meter.integrated_loudness(x.astype(np.float64))
    except Exception as e:
        logger.warning("Loudness measurement failed: %s", e)
        return x
    if not np.isfinite(loudness):
        return x
    gain = 10 ** ((float(target_lufs) - loudness) / 20)
    y = x * gain
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > PEAK_CEILING:
        y = y * (PEAK_CEILING / peak)
    return y.astype(np.float32)


def effective_speed(speed: float | None) -> float:
    """The request's speed, or the user's default when the request has none."""
    from ..services import extras

    return float(speed or extras.get("speed") or 1.0)


def apply_output_processing(
    audio: np.ndarray, sample_rate: int, speed: float | None, engine: str | None = None
) -> np.ndarray:
    """Speed change + loudness target from the user's extras settings."""
    from ..services import extras
    from .expressive import NATIVE_SPEED_ENGINES

    settings = extras.get_all()
    if engine not in NATIVE_SPEED_ENGINES:  # those already spoke at this speed
        audio = change_speed(audio, sample_rate, effective_speed(speed))
    target = settings.get("loudness_target_lufs")
    if target is not None:
        audio = normalize_loudness(audio, sample_rate, target)
    return audio


# ── Encoding ───────────────────────────────────────────────────────────────

MIME = {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac"}


def _ffmpeg_mp3(wav_path: Path, bitrate: int) -> bytes | None:
    from . import ffmpeg

    if ffmpeg.find_ffmpeg() is None:
        return None
    fd, out_name = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    out = Path(out_name)
    args = ["-loglevel", "error", "-y", "-i", str(wav_path)]
    # MPEG-2 LSF (24 kHz) tops out at 160 kbps; resample for higher bitrates.
    if bitrate > 160:
        args += ["-ar", "44100"]
    args += ["-c:a", "libmp3lame", "-b:a", f"{bitrate}k", "-f", "mp3", str(out)]
    try:
        r = ffmpeg.run(args, timeout=900)
        if r.returncode != 0:
            logger.warning("ffmpeg mp3 encode failed: %s", r.stderr.decode(errors="replace"))
            return None
        return out.read_bytes()
    finally:
        out.unlink(missing_ok=True)


def _soundfile_mp3(audio: np.ndarray, sample_rate: int, bitrate: int) -> bytes:
    import soundfile as sf

    x = np.asarray(audio, dtype=np.float32)
    sr = sample_rate
    if bitrate > 160 and sr < 32000:
        import librosa

        x = librosa.resample(x, orig_sr=sr, target_sr=44100).astype(np.float32)
        sr = 44100
    buf = io.BytesIO()
    # libsndfile maps compression_level 0..1 onto LAME's CBR bitrate range.
    max_rate = 320 if sr >= 32000 else 160
    level = 1.0 - (min(bitrate, max_rate) - 32) / (max_rate - 32)
    try:
        sf.write(
            buf,
            x,
            sr,
            format="MP3",
            subtype="MPEG_LAYER_III",
            compression_level=max(0.0, min(1.0, level)),
            bitrate_mode="CONSTANT",
        )
    except TypeError:  # older soundfile without compression kwargs
        buf = io.BytesIO()
        sf.write(buf, x, sr, format="MP3", subtype="MPEG_LAYER_III")
    return buf.getvalue()


def encode_audio_bytes(
    audio: np.ndarray,
    sample_rate: int,
    fmt: str = "wav",
    bitrate: int = 192,
) -> bytes:
    """Encode float audio into *fmt* bytes."""
    import soundfile as sf

    fmt = fmt.lower()
    x = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    if fmt == "wav":
        buf = io.BytesIO()
        sf.write(buf, x, sample_rate, format="WAV", subtype="PCM_16")
        return buf.getvalue()
    if fmt == "flac":
        buf = io.BytesIO()
        sf.write(buf, x, sample_rate, format="FLAC", subtype="PCM_16")
        return buf.getvalue()
    if fmt == "mp3":
        fd, wav_name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        wav_path = Path(wav_name)
        try:
            sf.write(str(wav_path), x, sample_rate, subtype="PCM_16")
            data = _ffmpeg_mp3(wav_path, bitrate)
        finally:
            wav_path.unlink(missing_ok=True)
        if data is not None:
            return data
        return _soundfile_mp3(x, sample_rate, bitrate)
    raise ValueError(f"Unsupported format: {fmt}")


def encode_file_bytes(path: Path, fmt: str, bitrate: int) -> bytes:
    """Encode an audio file on disk (any libsndfile-readable format)."""
    if fmt == "wav" and path.suffix.lower() == ".wav":
        return path.read_bytes()
    import soundfile as sf

    audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return encode_audio_bytes(audio, sr, fmt, bitrate)


# ── Subtitles ──────────────────────────────────────────────────────────────

_CUE_MAX_CHARS = 84
_SENTENCE_RE = re.compile(r"(?<=[.!?…。！？])\s+|\n+")
_CLAUSE_RE = re.compile(r"(?<=[,;:，；])\s+")


def _split_cue_texts(text: str, max_chars: int = _CUE_MAX_CHARS) -> list[str]:
    """Split a chunk's text into subtitle-sized pieces."""
    text = re.sub(r"\[[^\]]*\]", "", text)  # drop paralinguistic tags like [laugh]
    out: list[str] = []
    for sent in _SENTENCE_RE.split(text):
        sent = " ".join(sent.split())
        if not sent:
            continue
        if len(sent) <= max_chars:
            out.append(sent)
            continue
        buf = ""
        for piece in _CLAUSE_RE.split(sent):
            for word in piece.split(" "):
                cand = f"{buf} {word}".strip()
                if len(cand) > max_chars and buf:
                    out.append(buf)
                    buf = word
                else:
                    buf = cand
            # prefer breaking after a clause if the line is reasonably long
            if len(buf) > max_chars * 0.6:
                out.append(buf)
                buf = ""
        if buf:
            out.append(buf)
    return out


def build_cues(segments: Iterable[dict]) -> list[dict]:
    """Turn ``[{start, end, text}]`` chunk segments into subtitle cues.

    Time inside a chunk is distributed proportionally to character count.
    """
    cues: list[dict] = []
    for seg in segments:
        start, end = float(seg["start"]), float(seg["end"])
        pieces = _split_cue_texts(str(seg.get("text", "")))
        if not pieces or end <= start:
            continue
        total = sum(len(p) for p in pieces) or 1
        t = start
        for p in pieces:
            dur = (end - start) * len(p) / total
            cues.append({"start": t, "end": t + dur, "text": p})
            t += dur
    return cues


def _fmt_ts(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: list[dict]) -> str:
    lines = []
    for i, c in enumerate(cues, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_ts(c['start'])} --> {_fmt_ts(c['end'])}")
        lines.append(_wrap_line(c["text"]))
        lines.append("")
    return "\n".join(lines)


def _wrap_line(text: str, width: int = 42) -> str:
    """Wrap a cue onto at most two balanced lines."""
    if len(text) <= width:
        return text
    mid = len(text) // 2
    left = text.rfind(" ", 0, mid + 1)
    right = text.find(" ", mid)
    candidates = [p for p in (left, right) if p > 0]
    if not candidates:
        return text
    cut = min(candidates, key=lambda p: abs(p - mid))
    return text[:cut] + "\n" + text[cut + 1 :]


# ── Segment sidecar files ─────────────────────────────────────────────────


def segments_path(generation_id: str) -> Path:
    from .. import config

    return config.get_generations_dir() / f"{generation_id}.segments.json"


def load_quality(generation_id: str) -> dict | None:
    """The speech-QC report saved with a generation, if it was checked."""
    import json

    try:
        data = json.loads(segments_path(generation_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("qc")


def load_final(generation_id: str) -> dict | None:
    """The final whole-file check saved with a generation."""
    import json

    try:
        data = json.loads(segments_path(generation_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("final")


def save_segments(
    generation_id: str,
    segments: list[dict],
    duration: float,
    qc: dict | None = None,
    final: dict | None = None,
) -> None:
    import json

    try:
        segments_path(generation_id).write_text(
            json.dumps({"duration": duration, "segments": segments, "qc": qc, "final": final}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Could not save subtitle timing for %s: %s", generation_id, e)


def load_segments(generation_id: str, text: str, duration: float) -> list[dict]:
    """Saved chunk timing, or one segment spanning the clip as a fallback."""
    import json

    p = segments_path(generation_id)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            segs = data.get("segments") or []
            # Effects such as delay/reverb only add a tail, so timing stays
            # valid; just make sure no cue runs past the end of the file.
            if duration > 0:
                segs = [
                    {**s, "start": min(s["start"], duration), "end": min(s["end"], duration)}
                    for s in segs
                ]
            if segs:
                return segs
        except Exception as e:
            logger.warning("Bad segments file %s: %s", p, e)
    return [{"start": 0.0, "end": float(duration or 0), "text": text}]
