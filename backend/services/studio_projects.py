"""Advanced text-to-speech: a script of segments, each with its own voice and delivery."""

from __future__ import annotations

import csv
import io
import json
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import numpy as np
import soundfile as sf

from .. import config
from ..database import Generation
from ..utils.audio_post import load_final, load_segments

_ROLE_RE = re.compile(r"^\s*([^:：\n]{1,40})\s*[:：]\s*(.+)$")
_MIX_RATE = 44100
# Pauses shorter than this keep the music down, or it would pump between sentences.
_MUSIC_RISE_GAP = 1.0
_MUSIC_RAMP = 0.35
_DEFAULT_SPEECH_PCT = 25
_DEFAULT_PAUSE_PCT = 100
# ``[dừng 3s]``, ``(nghỉ 2-5s)``, ``[pause 1.5s]`` anywhere in a script.
_PAUSE_MARK = re.compile(
    r"[\[(]\s*(?:dừng|dung|nghỉ|nghi|ngắt|ngat|pause|silence)\s*(\d+(?:[.,]\d+)?)(?:\s*[-–]\s*(\d+(?:[.,]\d+)?))?\s*(ms|s|giây|giay)?\s*[\])]",
    re.IGNORECASE,
)
_NATURAL_PAUSE = (("…", 900), ("?", 600), ("!", 600), (".", 500), (",", 250), (":", 350), (";", 350))
_PARAGRAPH_PAUSE_MS = 1200
_DEFAULT_PAUSE_MS = 400
_SEVERITY = {"green": 0, "yellow": 1, "red": 2}


def _root() -> Path:
    path = config.get_data_dir() / "studio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(project_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", project_id):
        raise ValueError("Invalid project id")
    return _root() / project_id / "project.json"


def save(project: dict) -> None:
    project["updated_at"] = time.time()
    path = _path(project["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(project, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load(project_id: str) -> Optional[dict]:
    try:
        return json.loads(_path(project_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def new_segment(**fields: Any) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "text": "",
        "speaker": None,
        "profile_id": None,
        "engine": None,
        "model_size": None,
        "language": None,
        "emotion": None,
        "emotion_intensity": 0.6,
        "speed": None,
        "seed": None,
        "pause_after_ms": 400,
        "filename": "",
        "takes": [],
        "active": None,
        **fields,
    }


def create(name: str, language: str, defaults: dict) -> dict:
    project = {
        "id": uuid.uuid4().hex,
        "name": name.strip() or "Dự án mới",
        "created_at": time.time(),
        "language": language,
        "defaults": defaults,
        "segments": [],
        "music": None,
    }
    save(project)
    return project


def list_all() -> list[dict]:
    rows = []
    for path in _root().glob("*/project.json"):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows.append(
            {
                "id": p["id"],
                "name": p["name"],
                "updated_at": p.get("updated_at"),
                "segments": len(p.get("segments", [])),
            }
        )
    return sorted(rows, key=lambda r: r.get("updated_at") or 0, reverse=True)


def delete(project_id: str) -> None:
    import shutil

    shutil.rmtree(_path(project_id).parent, ignore_errors=True)


def view(project: dict, db) -> dict:
    """The project with each take's live status and quality."""
    ids = [g for s in project["segments"] for g in s["takes"]]
    gens = {g.id: g for g in db.query(Generation).filter(Generation.id.in_(ids)).all()} if ids else {}
    segments = []
    worst = "green"
    pending = False
    for s in project["segments"]:
        gen = gens.get(s["active"]) if s["active"] else None
        status = (gen.status or "completed") if gen else "empty"
        final = load_final(gen.id) if gen and status == "completed" else None
        level = final["level"] if final else None
        if level and _SEVERITY[level] > _SEVERITY[worst]:
            worst = level
        pending = pending or status in ("generating", "loading_model")
        segments.append(
            {
                **s,
                "status": status,
                "duration": float(gen.duration or 0) if gen else None,
                "error": gen.error if gen else None,
                "quality": level,
                "final": final,
            }
        )
    return {**project, "segments": segments, "quality": worst, "pending": pending}


def _mark_ms(match: re.Match) -> int:
    low = float(match[1].replace(",", "."))
    high = float(match[2].replace(",", ".")) if match[2] else low
    value = random.uniform(min(low, high), max(low, high))
    return int(round(value if (match[3] or "s").lower() == "ms" else value * 1000))


def natural_pause_ms(text: str, paragraph_end: bool = False) -> int:
    """How long a reader would pause after *text*."""
    if paragraph_end:
        return _PARAGRAPH_PAUSE_MS
    tail = text.rstrip().rstrip("\"'”’)]")
    return next((ms for mark, ms in _NATURAL_PAUSE if tail.endswith(mark)), _DEFAULT_PAUSE_MS)


def expand_pause_marks(segments: list[dict]) -> list[dict]:
    """Turn pause marks inside a segment's text into that many seconds of silence."""
    out: list[dict] = []
    for segment in segments:
        text = segment.get("text") or ""
        if not _PAUSE_MARK.search(text):
            out.append(segment)
            continue
        position = 0
        pieces: list[tuple[str, int]] = []
        for match in _PAUSE_MARK.finditer(text):
            piece = text[position:match.start()].strip()
            if piece:
                pieces.append((piece, _mark_ms(match)))
            elif out and not pieces:
                out[-1]["pause_after_ms"] = _mark_ms(match)
            elif pieces:
                pieces[-1] = (pieces[-1][0], _mark_ms(match))
            position = match.end()
        tail = text[position:].strip()
        if tail:
            pieces.append((tail, int(segment.get("pause_after_ms") or 0)))
        for i, (piece, pause) in enumerate(pieces):
            base = segment if i == 0 else {**segment, "id": uuid.uuid4().hex, "takes": [], "active": None}
            out.append({**base, "text": piece, "pause_after_ms": pause})
    return out


def _match_profile(name: Optional[str], profiles: list) -> Optional[str]:
    if not name:
        return None
    wanted = name.strip().casefold()
    return next((p.id for p in profiles if p.name.casefold() == wanted), None)


def parse_script(text: str, profiles: list) -> list[dict]:
    """Turn ``Tên: lời thoại`` lines or plain paragraphs into segments."""
    out = []
    lines = re.split(r"\r?\n", text)
    for i, line in enumerate(lines):
        block = line.strip()
        if not block:
            continue
        paragraph_end = i + 1 < len(lines) and not lines[i + 1].strip()
        match = _ROLE_RE.match(block)
        if match and not match[1].strip().startswith(("[", "(")):
            body, speaker = match[2].strip(), match[1].strip()
            profile_id = _match_profile(speaker, profiles)
        else:
            body, speaker, profile_id = block, None, None
        out.append(
            new_segment(text=body, speaker=speaker, profile_id=profile_id, pause_after_ms=natural_pause_ms(body, paragraph_end))
        )
    return expand_pause_marks(out)


def split_into_segments(text: str, max_chars: int) -> list[dict]:
    """One segment per whole-sentence part of about *max_chars* characters."""
    from ..utils.chunked_tts import split_script

    parts = split_script(_PAUSE_MARK.sub(lambda m: f" {m[0]} ", text), max_chars)
    return expand_pause_marks([new_segment(text=p, pause_after_ms=natural_pause_ms(p)) for p in parts])


async def stop(project: dict, db) -> int:
    from . import jobs

    return await jobs.cancel_studio(project, db)


def parse_csv(raw: bytes, profiles: list) -> list[dict]:
    """Columns: text (required), voice, emotion, speed, pause_ms, language, filename."""
    text = raw.decode("utf-8-sig", errors="replace")
    dialect = max(",;\t", key=text.split("\n", 1)[0].count)
    rows = csv.DictReader(io.StringIO(text), delimiter=dialect)
    segments = []
    for row in rows:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not row.get("text"):
            continue
        segments.append(
            new_segment(
                text=row["text"],
                speaker=row.get("voice") or None,
                profile_id=_match_profile(row.get("voice"), profiles),
                emotion=row.get("emotion") or None,
                speed=float(row["speed"]) if row.get("speed") else None,
                pause_after_ms=int(row["pause_ms"]) if row.get("pause_ms") else 400,
                language=row.get("language") or None,
                filename=row.get("filename", ""),
            )
        )
    return segments


async def render(project: dict, segment_ids: Optional[list[str]], db) -> dict:
    """Queue a new take for each chosen segment that has text and a voice."""
    from .. import models
    from ..routes.generations import generate_speech
    from . import settings as settings_service

    settings = settings_service.get_generation_settings(db)
    defaults = project.get("defaults", {})
    started, skipped = 0, []
    for s in project["segments"]:
        if segment_ids is not None and s["id"] not in segment_ids:
            continue
        profile_id = s["profile_id"] or defaults.get("profile_id")
        if not s["text"].strip() or not profile_id:
            skipped.append(s["id"])
            continue
        request = models.GenerationRequest(
            profile_id=profile_id,
            text=s["text"],
            language=s["language"] or project["language"],
            engine=s["engine"] or defaults.get("engine"),
            model_size=s["model_size"] or defaults.get("model_size") or "1.7B",
            seed=s["seed"],
            speed=s["speed"] if s["speed"] is not None else defaults.get("speed"),
            emotion=s["emotion"] or None,
            emotion_intensity=s["emotion_intensity"],
            denoise=defaults.get("denoise"),
            sentence_pause_ms=defaults.get("sentence_pause_ms", 0),
            max_chunk_chars=settings.max_chunk_chars,
            crossfade_ms=settings.crossfade_ms,
            normalize=settings.normalize_audio,
        )
        generation = await generate_speech(request, db, f"studio-{project['id']}")
        s["takes"].append(generation.id)
        s["active"] = generation.id
        started += 1
    save(project)
    return {"started": started, "skipped": skipped}


def _active_clips(project: dict, db) -> list[tuple[dict, np.ndarray, Generation]]:
    import librosa

    clips = []
    for s in project["segments"]:
        gen = db.query(Generation).filter_by(id=s["active"]).first() if s["active"] else None
        if gen is None or (gen.status or "completed") != "completed" or not gen.audio_path:
            continue
        audio, rate = sf.read(str(config.resolve_storage_path(gen.audio_path)), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if rate != _MIX_RATE:
            audio = librosa.resample(audio, orig_sr=rate, target_sr=_MIX_RATE)
        clips.append((s, audio.astype(np.float32), gen))
    return clips


def _music_gain(spoken: np.ndarray, speech_pct: float, pause_pct: float, rate: int) -> np.ndarray:
    """Music level over time: *speech_pct* under the voice, *pause_pct* in real pauses."""
    active = np.array(spoken, dtype=np.float32)
    # Close the short gaps between sentences first, so only real pauses rise.
    edges = np.flatnonzero(np.diff(np.concatenate([[1], active, [1]])))
    for gap_start, gap_end in zip(edges[::2], edges[1::2]):
        if gap_start > 0 and gap_end < len(active) and gap_end - gap_start < rate * _MUSIC_RISE_GAP:
            active[gap_start:gap_end] = 1.0
    width = max(1, int(rate * _MUSIC_RAMP))
    kernel = np.ones(width, dtype=np.float32) / width
    smooth = np.convolve(active, kernel, mode="same")
    return (pause_pct + (speech_pct - pause_pct) * smooth) / 100.0


def merge(project: dict, db) -> tuple[np.ndarray, int, list[dict]]:
    """One track of every segment's active take, plus the music bed if any."""
    clips = _active_clips(project, db)
    if not clips:
        raise ValueError("Chưa có đoạn nào hoàn tất.")
    parts: list[np.ndarray] = []
    spoken_parts: list[np.ndarray] = []
    cues: list[dict] = []
    position = 0
    for s, audio, gen in clips:
        scale = len(audio) / _MIX_RATE / max(float(gen.duration or 0), 1e-6)
        for cue in load_segments(gen.id, gen.text or "", float(gen.duration or 0)):
            cues.append(
                {
                    **cue,
                    "start": position / _MIX_RATE + cue["start"] * scale,
                    "end": position / _MIX_RATE + cue["end"] * scale,
                }
            )
        parts.append(audio)
        spoken_parts.append(np.ones(len(audio), dtype=np.float32))
        position += len(audio)
        pause = np.zeros(int(_MIX_RATE * int(s.get("pause_after_ms") or 0) / 1000), dtype=np.float32)
        parts.append(pause)
        spoken_parts.append(pause)
        position += len(pause)
    mix = np.concatenate(parts[:-1])
    spoken = np.concatenate(spoken_parts[:-1])

    music = project.get("music")
    if music and Path(music["path"]).is_file():
        import librosa

        bed, rate = sf.read(music["path"], dtype="float32", always_2d=True)
        if rate != _MIX_RATE:
            bed = librosa.resample(bed.T, orig_sr=rate, target_sr=_MIX_RATE).T
        repeats = int(np.ceil(len(mix) / max(1, len(bed))))
        bed = np.tile(bed, (repeats, 1))[: len(mix)]
        gain = _music_gain(
            spoken,
            float(music.get("speech_pct", _DEFAULT_SPEECH_PCT)),
            float(music.get("pause_pct", _DEFAULT_PAUSE_PCT)),
            _MIX_RATE,
        )
        mix = mix[:, None] + bed * gain[:, None]
    peak = float(np.max(np.abs(mix)))
    if peak > 0.97:
        mix = mix * (0.97 / peak)
    return mix.astype(np.float32), _MIX_RATE, cues


def clips_for_zip(project: dict, db) -> list[tuple[str, np.ndarray, int, Generation]]:
    from . import batch as batch_service

    order = {s["id"]: i for i, s in enumerate(project["segments"], 1)}
    out = []
    for s, audio, gen in _active_clips(project, db):
        name = batch_service.safe_name(s.get("filename") or "", f"{order[s['id']]:03d}")
        out.append((name, audio, _MIX_RATE, gen))
    return out
