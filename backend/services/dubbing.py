"""Dub audio or video: separate, transcribe, translate, revoice, mix back.

Heavy models run one at a time through the generation queue and are freed
between stages, so the whole pipeline stays inside 8 GB of VRAM. Every stage
saves its result, so a stopped job continues where it left off.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import numpy as np
import soundfile as sf

from .. import config
from ..utils import abort
from ..utils.audio_post import change_speed, normalize_loudness
from . import audio_clean, extras, speaker, task_queue, transcribe, voice_lab, vram
from .task_queue import create_background_task, run_exclusive

logger = logging.getLogger(__name__)

_MIX_RATE = 44100
_MAX_UTTERANCE = 25.0
_MERGE_GAP = 0.45
_MIN_UTTERANCE = 0.4
# Measured cosine similarity: the same speaker 0.83-0.88, two different speakers 0.65-0.68.
_DIARIZE_DISTANCE = 0.25
_BATCH_LINES = 8
_CONTEXT_BEFORE = 6
_CONTEXT_AFTER = 4
# The brief reads samples from the whole video within this many tokens. Attention
# memory grows with the square of the input: 12k tokens asked an 8 GB card for 10 GB.
_BRIEF_TOKENS = 3000
_BRIEF_WINDOWS = 4
# Chinese, Japanese and Korean cost about a token per character; other scripts
# about one per 3.5 characters.
_DENSE_SCRIPT = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯ｦ-ﾟ]")
_NO_STRETCH = 1.12
_MAX_STRETCH = 1.3
_SHORTEN_ABOVE = 1.2
_BORROW_GAP = 0.6
_DUB_LUFS = -16.0
_MIN_MATCHED_LUFS, _MAX_MATCHED_LUFS = -26.0, -12.0
_REPLACE_RAMP = 0.04
# One continuous utterance clones better than several joined: scored 1.0 on three
# seeds where two joined utterances scored 0.82-0.93.
_CLONE_SECONDS = 8.0
_CLONE_MIN_SECONDS = 3.0
_CLONE_MAX_SECONDS = 15.0
# Per-line voice match: a line shorter than this, longer than the last, or whose
# voice is not clearly louder than the music behind it, uses the speaker's reference.
_LINE_REF_MIN = 2.0
_LINE_REF_MAX = 30.0
_LINE_REF_SNR = 2.0  # vocals RMS over background RMS, about 6 dB

SUBTITLE_DEFAULTS: dict[str, Any] = {
    "burn": False,
    "which": "target",  # target | source
    "font": "Arial",
    "size": 18,  # libass units: the frame is 288 tall, so 18 is about 6% of the height
    "color": "#FFFFFF",
    "outline_color": "#000000",
    "outline": 2.0,
    "bold": False,
    "position": "bottom",  # bottom | middle | top
    "margin": 20,
}
# Legacy SSA codes: ffmpeg's force_style reads these, not the numpad ones.
_ALIGN = {"bottom": 2, "middle": 10, "top": 6}
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
_SRT_SPEAKER = re.compile(r"^\s*(?:\[([^\]\d]{1,30})\]|([^\W\d_](?:[^\W\d_]|[ .'-]){0,24}?)\s*[:：])\s*(.+)$")

LANGUAGE_NAMES = {
    "vi": "Vietnamese", "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "fr": "French", "de": "German", "es": "Spanish", "pt": "Portuguese", "ru": "Russian",
    "it": "Italian", "ar": "Arabic", "hi": "Hindi", "tr": "Turkish", "nl": "Dutch", "pl": "Polish",
    "sv": "Swedish", "da": "Danish", "no": "Norwegian", "fi": "Finnish", "el": "Greek", "he": "Hebrew",
    "ms": "Malay", "sw": "Swahili",
}

_ACTIVE_STAGES = {"queued", "extracting", "separating", "transcribing", "speakers", "translating", "synthesizing", "mixing"}
# Whisper invents these on music and silence; dubbing one puts words on the track that nobody said.
_HALLUCINATIONS = re.compile(
    r"subscribe|đăng k[ýí] (cho )?kênh|ghiền mì gõ|la la school|thanks? for watching|cảm ơn (các bạn )?đã (theo dõi|xem)"
    r"|hẹn gặp lại (các bạn )?(trong|ở)|amara\.org|subtitles? by|字幕|ご視聴",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_cancelled: set[str] = set()
_running: set[str] = set()
# Videos waiting for the GPU, oldest first; the head of the line is the one running.
_line: list[str] = []


class _Stopped(Exception):
    pass


def _root() -> Path:
    path = config.get_data_dir() / "dubbing"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _folder(project_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", project_id):
        raise ValueError("Invalid project id")
    return _root() / project_id


def save(project: dict) -> None:
    project["updated_at"] = time.time()
    path = _folder(project["id"]) / "project.json"
    tmp = path.with_suffix(".tmp")
    # A stray numpy scalar must never stop a project from saving: the failure
    # handler saves too, so the job would look like it is still running forever.
    tmp.write_text(json.dumps(project, ensure_ascii=False, indent=1, default=lambda v: v.item()), encoding="utf-8")
    tmp.replace(path)


def load(project_id: str) -> Optional[dict]:
    try:
        return json.loads((_folder(project_id) / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_all() -> list[dict]:
    rows = []
    for path in _root().glob("*/project.json"):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows.append({k: p.get(k) for k in ("id", "name", "stage", "progress", "error", "updated_at", "target_language", "mode")})
    return sorted(rows, key=lambda r: r.get("updated_at") or 0, reverse=True)


def delete(project_id: str) -> None:
    if project_id in _running:
        raise ValueError("Hãy dừng dự án trước khi xóa.")
    shutil.rmtree(_folder(project_id), ignore_errors=True)


def recover_interrupted() -> None:
    """A job cut off by closing the app must not look like it is still running."""
    for path in _root().glob("*/project.json"):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
            if p.get("stage") in _ACTIVE_STAGES:
                p.update(stage="stopped", error="Ứng dụng đã đóng khi đang xử lý. Bấm Tiếp tục.")
                save(p)
        except (OSError, ValueError):
            continue


def parse_srt(text: str) -> list[dict]:
    """Cues of an SRT file as {start, end, text, speaker}.

    A speaker is read from "[Name] …" or "Name: …", but only when at least half
    the cues carry one, so a stray "Note: …" is not taken for a person.
    """
    cues: list[dict] = []
    clean = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    for block in re.split(r"\n\s*\n", clean):
        lines = block.strip().split("\n")
        for j, line in enumerate(lines):
            m = _SRT_TIME.search(line)
            if not m:
                continue
            g = m.groups()
            start = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3].ljust(3, "0")) / 1000
            end = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7].ljust(3, "0")) / 1000
            body = " ".join(x.strip() for x in lines[j + 1 :] if x.strip())
            body = re.sub(r"<[^>]+>|\{\\[^}]*\}", "", body).strip()
            if body and end > start:
                cues.append({"start": round(start, 3), "end": round(end, 3), "text": body, "speaker": None})
            break
    if not cues:
        raise ValueError("File SRT không có dòng phụ đề nào đọc được.")
    matches = [_SRT_SPEAKER.match(c["text"]) for c in cues]
    if sum(1 for m in matches if m) * 2 >= len(cues):
        for cue, m in zip(cues, matches):
            if m:
                cue["speaker"] = (m.group(1) or m.group(2)).strip()
                cue["text"] = m.group(3).strip()
    return sorted(cues, key=lambda c: c["start"])


def clean_subtitle(value: Any) -> dict[str, Any]:
    """Burned-in subtitle style with every field checked: it ends up inside an ffmpeg filter."""
    v = {**SUBTITLE_DEFAULTS, **(value if isinstance(value, dict) else {})}
    return {
        "burn": bool(v["burn"]),
        "which": v["which"] if v["which"] in ("target", "source") else "target",
        "font": re.sub(r"[^\w \-]", "", str(v["font"]))[:60].strip() or "Arial",
        "size": max(6, min(96, int(float(v["size"])))),
        "color": str(v["color"]) if _HEX.match(str(v["color"])) else "#FFFFFF",
        "outline_color": str(v["outline_color"]) if _HEX.match(str(v["outline_color"])) else "#000000",
        "outline": round(max(0.0, min(8.0, float(v["outline"]))), 1),
        "bold": bool(v["bold"]),
        "position": v["position"] if v["position"] in _ALIGN else "bottom",
        "margin": max(0, min(300, int(float(v["margin"])))),
    }


def create(upload: Path, filename: str, options: dict[str, Any]) -> dict:
    project_id = uuid.uuid4().hex
    folder = _folder(project_id)
    folder.mkdir(parents=True)
    source = folder / f"source{Path(filename).suffix.lower()}"
    shutil.move(str(upload), source)
    project = {
        "id": project_id,
        "name": Path(filename).stem,
        "created_at": time.time(),
        "source": source.name,
        "stage": "queued",
        "progress": 0.0,
        "error": None,
        "segments": [],
        "speakers": {},
        "qc": None,
        **options,
    }
    save(project)
    return project


def _update(project: dict, stage: str, progress: float) -> None:
    project.update(stage=stage, progress=round(progress, 3), error=None)
    save(project)
    if project["id"] in _cancelled:
        raise _Stopped()


async def _gpu(project: dict, name: str, work: Callable[[], Awaitable[Any]]) -> Any:
    """Run one stage alone on the GPU, with every other model freed first."""

    async def wrapped():
        await asyncio.to_thread(vram.unload_tts, None, True)
        try:
            return await work()
        finally:
            # Qwen3 4B alone fills the card; TTS loaded beside it crawls.
            await asyncio.to_thread(vram.unload_tts, None, True)

    return await run_exclusive(f"dub-{project['id']}-{name}", wrapped)


def _has_video(path: Path) -> bool:
    from ..utils import ffmpeg

    try:
        out = ffmpeg.run(["-i", str(path)])
    except RuntimeError:  # no ffmpeg: treat as audio only
        return False
    text = out.stderr.decode(errors="replace")
    return bool(re.search(r"Stream #.*Video: (?!mjpeg|png)", text))


def _utterances(vocals: np.ndarray, rate: int) -> list[tuple[float, float]]:
    import librosa

    mono = vocals.mean(axis=1) if vocals.ndim > 1 else vocals
    spans = librosa.effects.split(mono, top_db=34, frame_length=2048, hop_length=512)
    merged: list[list[float]] = []
    for a, b in spans:
        start, end = a / rate, b / rate
        if merged and start - merged[-1][1] < _MERGE_GAP and end - merged[-1][0] <= _MAX_UTTERANCE:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    out: list[tuple[float, float]] = []
    for start, end in merged:
        if end - start < _MIN_UTTERANCE:
            continue
        while end - start > _MAX_UTTERANCE:
            out.append((start, start + _MAX_UTTERANCE))
            start += _MAX_UTTERANCE
        out.append((start, end))
    return out


async def _transcribe_all(project: dict, folder: Path) -> None:
    """Split the speech into lines: by an imported SRT when there is one, else by pauses."""
    vocals, rate = sf.read(str(folder / "stems" / "vocals.wav"), dtype="float32")
    cues: list[dict] = project.get("srt_cues") or []
    role = project.get("srt_role") or "source"
    spans = [(c["start"], c["end"]) for c in cues] or _utterances(vocals, rate)
    if not spans:
        raise RuntimeError("Không nghe thấy lời nói trong file này.")
    model_size = extras.get("qc_whisper_model")
    mono = vocals.mean(axis=1) if vocals.ndim > 1 else vocals
    language = project.get("source_language")

    async def work() -> None:
        nonlocal language
        if not language:
            language = await voice_lab.detect_language(folder / "stems" / "vocals.wav", model_size)
            project["source_language"] = language
        # An SRT in the source language already holds the words; one in the
        # target language holds the translation, and Whisper still writes the source.
        whisper = transcribe.get_whisper_model() if not cues or role == "target" else None
        segments = []
        for i, (start, end) in enumerate(spans):
            if project["id"] in _cancelled:
                raise _Stopped()
            cue = cues[i] if cues else None
            if whisper is None:
                text = cue["text"]
            else:
                fd, name = tempfile.mkstemp(suffix=".wav")
                import os

                os.close(fd)
                try:
                    sf.write(name, mono[int(start * rate) : int(end * rate)], rate, subtype="FLOAT")
                    text = (await whisper.transcribe(name, language=language, model_size=model_size)).strip()
                finally:
                    Path(name).unlink(missing_ok=True)
                if _HALLUCINATIONS.search(text):
                    text = ""
            if text or cue is not None:
                segments.append(
                    {
                        "id": f"s{i:05d}",
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "speaker": (cue or {}).get("speaker") or "S1",
                        "text": text,
                        "translation": cue["text"] if cue is not None and role == "target" else "",
                        "status": "pending",
                        "fit": None,
                        "overflow": False,
                    }
                )
            project["progress"] = round(0.2 + 0.15 * (i + 1) / len(spans), 3)
        if any(c.get("speaker") for c in cues):
            # Names from the SRT replace speaker detection.
            names = list(dict.fromkeys(s["speaker"] for s in segments))
            keys = {name: f"S{n + 1}" for n, name in enumerate(names)}
            for segment in segments:
                segment["speaker"] = keys[segment["speaker"]]
            project["speakers"] = {
                keys[name]: {"label": name, "mode": "clone", "profile_id": None} for name in names
            }
            project["speakers_from_srt"] = True
        project["segments"] = segments
        save(project)

    await _gpu(project, "whisper", work)


def _diarize(project: dict, folder: Path) -> None:
    from sklearn.cluster import AgglomerativeClustering

    vocals, rate = sf.read(str(folder / "stems" / "vocals.wav"), dtype="float32")
    mono = vocals.mean(axis=1) if vocals.ndim > 1 else vocals
    vectors = [speaker.embed(mono[int(s["start"] * rate) : int(s["end"] * rate)], rate) for s in project["segments"]]
    known = [i for i, v in enumerate(vectors) if v is not None]
    if len(known) < 2:
        project["speakers"] = {"S1": {"label": "Người nói 1", "mode": "clone", "profile_id": None}}
        return
    matrix = np.stack([vectors[i] for i in known])
    wanted = int(project.get("speaker_count") or 0)
    if wanted > 1:
        clustering = AgglomerativeClustering(n_clusters=min(wanted, len(known)), metric="cosine", linkage="average")
    elif wanted == 1:
        clustering = None
    else:
        clustering = AgglomerativeClustering(
            n_clusters=None, metric="cosine", linkage="average", distance_threshold=_DIARIZE_DISTANCE
        )
    labels = clustering.fit_predict(matrix) if clustering is not None else np.zeros(len(known), dtype=int)
    order = {label: f"S{i + 1}" for i, label in enumerate(dict.fromkeys(int(l) for l in labels))}
    assigned = {i: order[int(l)] for i, l in zip(known, labels)}
    for i, segment in enumerate(project["segments"]):
        if i in assigned:
            segment["speaker"] = assigned[i]
        else:
            nearest = min(known, key=lambda k: abs(k - i))
            segment["speaker"] = assigned[nearest]
    project["speakers"] = {
        name: {"label": f"Người nói {name[1:]}", "mode": "clone", "profile_id": None} for name in sorted(set(assigned.values()))
    }


def _parse_lines(reply: str, expected: int) -> Optional[list[str]]:
    """Read the model's lines. It answers as a JSON array or as a numbered list."""
    try:
        values = json.loads(reply[reply.index("[") : reply.rindex("]") + 1])
        if isinstance(values, list) and len(values) == expected:
            # The prompt numbers its lines and the model sometimes copies that in.
            return [re.sub(rf"^{i + 1}[.)]\s+", "", str(v).strip()) for i, v in enumerate(values)]
    except ValueError:
        pass
    numbered = [m[1].strip() for m in re.finditer(r"^\s*\d+[.)]\s*(.+)$", reply, re.MULTILINE)]
    if len(numbered) == expected:
        return numbered
    plain = reply.strip()
    if expected == 1 and plain and "\n" not in plain:
        return [plain]
    return None


def _system_prompt(project: dict) -> str:
    target = LANGUAGE_NAMES.get(project["target_language"], project["target_language"])
    source = LANGUAGE_NAMES.get(project.get("source_language") or "", "the source language")
    glossary = project.get("glossary") or ""
    brief = project.get("brief") or ""
    extra = f"\nWhat this video is about, and fixed terms:\n{brief}" if brief else ""
    if glossary.strip():
        extra += f"\nAlways use this glossary (source = target):\n{glossary}"
    return (
        f"You are a professional dubbing translator from {source} to natural spoken {target}. "
        "Translate only what each numbered line says, using the lines around it to understand who is speaking and what is meant. "
        "Never add, drop, soften or explain anything. Keep names, numbers, dates, units and facts exactly. "
        "Lines marked as context are for reading only. Output only a JSON array of strings, one per numbered line, in the same order."
        + extra
    )


def _approx_tokens(text: str) -> int:
    """Token count without loading a tokenizer; works for a custom LLM endpoint too."""
    dense = len(_DENSE_SCRIPT.findall(text))
    return dense + (len(text) - dense) // 3 + 1


def _brief_sample(lines: list[str], budget: int) -> str:
    """Evenly spaced runs of lines from start to end that fit in *budget* tokens.

    Names and topics that only appear late in a long video still reach the brief.
    """
    costs = [_approx_tokens(x) for x in lines]
    if sum(costs) <= budget:
        return "\n".join(lines)
    share = budget // _BRIEF_WINDOWS
    runs: list[str] = []
    for w in range(_BRIEF_WINDOWS):
        i, used, run = len(lines) * w // _BRIEF_WINDOWS, 0, []
        while i < len(lines) and used + costs[i] <= share:
            run.append(lines[i])
            used += costs[i]
            i += 1
        if run:
            runs.append("\n".join(run))
    return "\n…\n".join(runs)


def _out_of_memory(e: BaseException) -> bool:
    return "out of memory" in str(e).lower()


async def _make_brief(project: dict, llm, size: str) -> None:
    """Read samples of the whole transcript once, so every line is translated knowing the topic."""
    if project.get("brief"):
        return
    target = LANGUAGE_NAMES.get(project["target_language"], project["target_language"])
    lines = [s["text"] for s in project["segments"] if s["text"].strip()]
    budget = _BRIEF_TOKENS
    while budget >= _BRIEF_TOKENS // 4:
        try:
            reply = await llm.generate(
                _brief_sample(lines, budget),
                system=(
                    "You prepare a dubbing translator. In at most 8 short lines state: the topic, who is speaking and how they relate, "
                    f"the tone, then each recurring name or technical term with its correct {target} rendering as `term = rendering`. "
                    "State only what the transcript supports."
                ),
                max_tokens=500,
                temperature=0,
                model_size=size,
            )
        except RuntimeError as e:
            if not _out_of_memory(e):
                raise
            # A card with less free memory than expected: read less rather than stop the dub.
            logger.warning("Brief ran out of VRAM at %d tokens; trying with less", budget)
            await asyncio.to_thread(vram.unload_tts, None, True)
            budget //= 2
            continue
        project["brief"] = reply.strip()
        save(project)
        return
    # The brief only helps; translation goes on without it.
    logger.warning("Skipping the dubbing brief: not enough VRAM")


def _line_warning(source: str, translation: str) -> Optional[str]:
    """Why a translated line looks wrong, without needing another model."""
    if not translation.strip():
        return "empty"
    if source.strip().casefold() == translation.strip().casefold() and len(source) > 12:
        return "untranslated"
    want = sorted(re.sub(r"[.,]", "", n) for n in _NUMBER.findall(source))
    got = sorted(re.sub(r"[.,]", "", n) for n in _NUMBER.findall(translation))
    if want and got and want != got:
        return "numbers"
    return None


async def _translate_all(project: dict) -> None:
    size = project.get("llm_size") or "4B"
    llm = _llm_for(size)
    todo = [s for s in project["segments"] if not s["translation"]]

    async def ask(lines: list[str], before: list[str], after: list[str]) -> Optional[list[str]]:
        nonlocal size
        prompt = ""
        if before:
            prompt += "Context before (do not translate):\n" + "\n".join(before) + "\n\n"
        prompt += "Translate:\n" + "\n".join(f"{i + 1}. {x}" for i, x in enumerate(lines))
        if after:
            prompt += "\n\nContext after (do not translate):\n" + "\n".join(after)
        system = _system_prompt(project)
        try:
            reply = await llm.generate(prompt, system=system, max_tokens=1600, temperature=0, model_size=size)
        except RuntimeError as e:
            # 4B needs nearly all of an 8 GB card; free the TTS model and try once more.
            if not _out_of_memory(e):
                raise
            logger.warning("Qwen3 4B ran out of VRAM; retrying with the TTS model unloaded")
            await asyncio.to_thread(vram.unload_tts, None, True)
            try:
                reply = await llm.generate(prompt, system=system, max_tokens=1600, temperature=0, model_size=size)
            except RuntimeError as again:
                if not _out_of_memory(again) or not (before or after):
                    raise
                # Still too big: drop the surrounding lines, keep the lines to translate.
                return await ask(lines, [], [])
        return _parse_lines(reply, len(lines))

    async def work() -> None:
        await _make_brief(project, llm, size)
        everything = project["segments"]
        for offset in range(0, len(todo), _BATCH_LINES):
            batch = todo[offset : offset + _BATCH_LINES]
            first = everything.index(batch[0])
            last = everything.index(batch[-1])
            before = [s["text"] for s in everything[max(0, first - _CONTEXT_BEFORE) : first]]
            after = [s["text"] for s in everything[last + 1 : last + 1 + _CONTEXT_AFTER]]
            lines = [s["text"] for s in batch]
            result = await ask(lines, before, after)
            if result is None:
                result = []
                for line in lines:
                    single = await ask([line], before, after)
                    result.append(single[0] if single else "")
            for segment, translation in zip(batch, result):
                warning = _line_warning(segment["text"], translation)
                if warning:
                    index = everything.index(segment)
                    retry = await ask(
                        [segment["text"]],
                        [s["text"] for s in everything[max(0, index - _CONTEXT_BEFORE) : index]],
                        [s["text"] for s in everything[index + 1 : index + 1 + _CONTEXT_AFTER]],
                    )
                    if retry and not _line_warning(segment["text"], retry[0]):
                        translation, warning = retry[0], None
                segment["translation"] = translation
                segment["warning"] = warning
            project["progress"] = round(0.4 + 0.15 * min(1.0, (offset + _BATCH_LINES) / max(1, len(todo))), 3)
            save(project)
            if project["id"] in _cancelled:
                raise _Stopped()

    await _gpu(project, "translate", work)
    if not any(s["translation"].strip() for s in project["segments"]):
        raise RuntimeError("Bộ dịch không trả về bản dịch nào. Hãy thử bộ dịch khác.")


def _llm_for(size: str):
    """The local Qwen3, or the user's own OpenAI-compatible endpoint."""
    from ..backends import get_llm_backend, get_llm_backend_for_engine

    return get_llm_backend() if size == "custom" else get_llm_backend_for_engine("qwen_llm")


async def _shorten(project: dict, segments: list[dict]) -> None:
    llm = _llm_for(project.get("llm_size") or "4B")
    target = LANGUAGE_NAMES.get(project["target_language"], project["target_language"])
    system = (
        f"You shorten {target} dubbing lines. Rewrite each numbered line to be about 25% shorter when spoken, "
        "by removing filler words only. Keep every fact, name and number. Output only a JSON array of strings, in the same order."
    )

    async def work() -> None:
        for offset in range(0, len(segments), _BATCH_LINES):
            batch = segments[offset : offset + _BATCH_LINES]
            numbered = "\n".join(f"{i + 1}. {s['translation']}" for i, s in enumerate(batch))
            reply = await llm.generate(
                numbered, system=system, max_tokens=1200, temperature=0, model_size=project.get("llm_size") or "4B"
            )
            shorter = _parse_lines(reply, len(batch))
            for segment, line in zip(batch, shorter or [s["translation"] for s in batch]):
                keeps_facts = _line_warning(segment["text"], line) != "numbers"
                if line and len(line) < len(segment["translation"]) and keeps_facts:
                    segment["translation"] = line
                    (Path(_folder(project["id"])) / "clips" / f"{segment['id']}.wav").unlink(missing_ok=True)
                segment["shortened"] = True
            save(project)

    await _gpu(project, "shorten", work)


async def _ensure_voices(project: dict, folder: Path, db) -> None:
    """Clone each speaker that was left on "keep the original voice"."""
    from .. import models
    from . import profiles

    vocals, rate = sf.read(str(folder / "stems" / "vocals.wav"), dtype="float32")
    mono = vocals.mean(axis=1) if vocals.ndim > 1 else vocals
    for name, voice in project["speakers"].items():
        if voice["mode"] != "clone" or voice.get("profile_id"):
            continue
        spoken = [s for s in project["segments"] if s["speaker"] == name]
        if not spoken:  # every line was moved to another speaker during review
            continue
        spoken.sort(key=lambda s: abs((s["end"] - s["start"]) - _CLONE_SECONDS))
        picked, total = [], 0.0
        best = spoken[0]
        if _CLONE_MIN_SECONDS <= best["end"] - best["start"] <= _CLONE_MAX_SECONDS:
            spoken = [best]
        for segment in spoken:
            length = segment["end"] - segment["start"]
            if total + length + 0.25 > _CLONE_MAX_SECONDS:
                continue
            picked.append(segment)
            total += length + 0.25
            if total >= _CLONE_SECONDS * 0.7:
                break
        if not picked:
            picked = spoken[:1]
        picked.sort(key=lambda s: s["start"])
        gap = np.zeros(int(rate * 0.25), dtype=np.float32)
        parts: list[np.ndarray] = []
        for segment in picked:
            parts += [mono[int(segment["start"] * rate) : int(segment["end"] * rate)], gap]
        clip = np.concatenate(parts[:-1])[: int(rate * 29)]
        clip = await asyncio.to_thread(audio_clean.denoise, clip, rate, 0.7)
        path = folder / f"voice_{name}.wav"
        peak = float(np.max(np.abs(clip)))
        sf.write(str(path), clip * (0.89 / peak) if peak > 0.89 else clip, rate, subtype="PCM_16")
        created = await profiles.create_profile(
            models.VoiceProfileCreate(
                name=f"Lồng tiếng · {project['name'][:60]} · {name} · {project['id'][:4]}",
                language=project["target_language"],
                default_engine=project.get("engine"),
            ),
            db,
        )
        await profiles.add_profile_sample(created.id, str(path), " ".join(s["text"] for s in picked), db)
        voice["profile_id"] = created.id
    save(project)


def _line_reference(folder: Path, segment: dict) -> Optional[Path]:
    """The line's own source audio as its voice reference, or None when it is
    too short, too long or too buried under the music to clone from."""
    length = segment["end"] - segment["start"]
    if not (_LINE_REF_MIN <= length <= _LINE_REF_MAX) or not segment.get("text", "").strip():
        return None
    path = folder / "refs" / f"{segment['id']}.wav"
    if path.is_file():
        return path
    stems = folder / "stems"
    rate = sf.info(str(stems / "vocals.wav")).samplerate
    a, b = int(segment["start"] * rate), int(segment["end"] * rate)
    voice = sf.read(str(stems / "vocals.wav"), start=a, stop=b, dtype="float32", always_2d=True)[0].mean(axis=1)
    music = sf.read(str(stems / "background.wav"), start=a, stop=b, dtype="float32", always_2d=True)[0].mean(axis=1)

    def rms(x: np.ndarray) -> float:
        return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0

    if rms(voice) < 1e-4 or rms(voice) < _LINE_REF_SNR * rms(music):
        return None
    clip = audio_clean.denoise(voice, rate, 0.5)
    peak = float(np.max(np.abs(clip)))
    path.parent.mkdir(exist_ok=True)
    sf.write(str(path), clip * (0.89 / peak) if peak > 0.89 else clip, rate, subtype="PCM_16")
    return path


async def _synthesize(project: dict, folder: Path) -> None:
    from .generation import generate_audio_sync

    clips = folder / "clips"
    clips.mkdir(exist_ok=True)
    todo = [s for s in project["segments"] if s["translation"].strip() and not (clips / f"{s['id']}.wav").is_file()]

    engine = project.get("engine") or "omnivoice"

    async def one(segment: dict) -> None:
        voice = project["speakers"][segment["speaker"]]
        reference = None
        if project.get("voice_match") == "line" and voice["mode"] == "clone" and engine in ("qwen", "omnivoice"):
            path = await asyncio.to_thread(_line_reference, folder, segment)
            if path is not None:
                reference = (str(path), segment["text"])
        wav = await generate_audio_sync(
            profile_id=voice["profile_id"],
            text=segment["translation"],
            language=project["target_language"],
            engine=engine,
            model_size=project.get("model_size") or "1.7B",
            seed=project.get("seed"),
            reference=reference,
        )
        (clips / f"{segment['id']}.wav").write_bytes(wav)
        segment.update(status="done", reference="line" if reference else "speaker")
        segment.pop("score", None)

    for i, segment in enumerate(todo):
        if project["id"] in _cancelled:
            raise _Stopped()
        try:
            await run_exclusive(f"dub-{project['id']}-{segment['id']}", lambda s=segment: one(s))
        except Exception as e:
            logger.warning("Dub segment %s failed: %s", segment["id"], e)
            segment.update(status="failed", error=str(e))
        project["progress"] = round(0.6 + 0.25 * (i + 1) / len(todo), 3)
        save(project)


def _allowed_seconds(segments: list[dict], index: int, total: float) -> float:
    segment = segments[index]
    following = segments[index + 1]["start"] if index + 1 < len(segments) else total
    return segment["end"] - segment["start"] + max(0.0, min(_BORROW_GAP, following - segment["end"]))


def _measure_fit(project: dict, folder: Path, total: float) -> list[dict]:
    """Record how each clip compares to its slot; return the ones worth rewording."""
    over = []
    for i, segment in enumerate(project["segments"]):
        path = folder / "clips" / f"{segment['id']}.wav"
        if not path.is_file():
            continue
        info = sf.info(str(path))
        ratio = info.duration / max(0.1, _allowed_seconds(project["segments"], i, total))
        segment["fit"] = round(ratio, 3)
        if ratio > _SHORTEN_ABOVE and not segment.get("shortened"):
            over.append(segment)
    save(project)
    return over


def _stereo(path: Path, length: int) -> np.ndarray:
    import librosa

    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    if rate != _MIX_RATE:
        audio = librosa.resample(audio.T, orig_sr=rate, target_sr=_MIX_RATE).T
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    audio = audio[:, :2]
    if len(audio) < length:
        audio = np.pad(audio, ((0, length - len(audio)), (0, 0)))
    return audio[:length]


def _speech_loudness(vocals: np.ndarray, spans: np.ndarray) -> float:
    """How loud the original speakers are, so the new voices sit at the same level."""
    from ..utils.audio_quality import measure

    spoken = vocals.mean(axis=1)[spans > 0]
    value = measure(spoken, _MIX_RATE)["lufs"] if len(spoken) > _MIX_RATE else None
    if value is None or not np.isfinite(value):
        return _DUB_LUFS
    return float(np.clip(value, _MIN_MATCHED_LUFS, _MAX_MATCHED_LUFS))


def _mix(project: dict, folder: Path, total: float) -> tuple[np.ndarray, np.ndarray]:
    """Replace speech only. Wherever nobody was dubbed, the original audio is untouched."""
    import librosa

    length = int(total * _MIX_RATE)
    voices = np.zeros(length, dtype=np.float32)
    replaced = np.zeros(length, dtype=np.float32)
    for i, segment in enumerate(project["segments"]):
        path = folder / "clips" / f"{segment['id']}.wav"
        if not path.is_file():
            continue
        clip, rate = sf.read(str(path), dtype="float32")
        allowed = _allowed_seconds(project["segments"], i, total)
        ratio = (len(clip) / rate) / max(0.1, allowed)
        if ratio > 1.0:
            clip = change_speed(clip, rate, min(_MAX_STRETCH, max(1.0, ratio)))
        segment["overflow"] = bool(ratio > _MAX_STRETCH)
        if rate != _MIX_RATE:
            clip = librosa.resample(clip, orig_sr=rate, target_sr=_MIX_RATE)
        start = int(segment["start"] * _MIX_RATE)
        end = min(length, start + len(clip))
        if end <= start:
            continue
        voices[start:end] += clip[: end - start]
        original_end = min(length, int(segment["end"] * _MIX_RATE))
        replaced[start : max(end, original_end)] = 1.0

    original = _stereo(folder / "decoded.wav", length)
    background = _stereo(folder / "stems" / "background.wav", length)
    vocals = _stereo(folder / "stems" / "vocals.wav", length)
    voices = normalize_loudness(voices, _MIX_RATE, _speech_loudness(vocals, replaced))

    width = max(1, int(_MIX_RATE * _REPLACE_RAMP))
    mask = np.convolve(replaced, np.ones(width, dtype=np.float32) / width, mode="same")[:, None]
    # Under a dubbed line the original voice and the music each drop to their own level.
    keep = float(project.get("original_volume") or 0.0)
    music = float(project.get("music_volume", 1.0))
    mix = original * (1 - mask) + (background * music + vocals * keep) * mask + voices[:, None]

    peak = float(np.max(np.abs(mix)))
    if peak > 0.97:
        mix = mix * (0.97 / peak)
    return np.stack([voices, voices], axis=1), mix.astype(np.float32)


def _ass_colour(value: str) -> str:
    return f"&H00{value[5:7]}{value[3:5]}{value[1:3]}".upper()


def _subtitle_filter(project: dict, folder: Path) -> Optional[str]:
    """The ffmpeg filter that burns the subtitles in, or None when they stay off."""
    style = clean_subtitle(project.get("subtitle"))
    if not style["burn"]:
        return None
    (folder / "burn.srt").write_text(subtitles(project, style["which"], "srt"), encoding="utf-8")
    force = ",".join(
        [
            f"FontName={style['font']}",
            f"FontSize={style['size']}",
            f"PrimaryColour={_ass_colour(style['color'])}",
            f"OutlineColour={_ass_colour(style['outline_color'])}",
            "BorderStyle=1",
            f"Outline={style['outline']}",
            "Shadow=0",
            f"Bold={-1 if style['bold'] else 0}",
            f"Alignment={_ALIGN[style['position']]}",
            f"MarginV={style['margin']}",
        ]
    )
    # Run from the project folder: a Windows path inside a filter needs escaping.
    return f"subtitles=burn.srt:force_style='{force}'"


def _mux(source: Path, audio: Path, output: Path, subtitle_filter: Optional[str] = None) -> None:
    from ..utils import ffmpeg

    missing = "Cần cài ffmpeg để xuất video."
    base = ["-loglevel", "error", "-y", "-i", str(source), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"]
    tail = ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)]
    if subtitle_filter:
        encode = ["-vf", subtitle_filter, "-c:v", "libx264", "-crf", "18", "-preset", "medium"]
        result = ffmpeg.run(base + encode + tail, missing, cwd=str(output.parent))
        if result.returncode != 0:
            raise RuntimeError("Không gắn được phụ đề: " + result.stderr.decode(errors="replace")[-300:])
        return
    result = ffmpeg.run(base + ["-c:v", "copy"] + tail, missing)
    if result.returncode != 0:
        # The source codec does not fit in MP4; re-encode the picture.
        result = ffmpeg.run(base + ["-c:v", "libx264", "-crf", "18", "-preset", "medium"] + tail, missing)
    if result.returncode != 0:
        raise RuntimeError("Không ghép được video: " + result.stderr.decode(errors="replace")[-300:])


async def _check_dub(project: dict, folder: Path) -> None:
    """Listen to every finished line once more and flag what needs a look."""
    from ..utils.audio_quality import measure
    from . import speech_qc

    whisper_model = extras.get("qc_whisper_model")
    problems: list[dict] = []

    async def work() -> None:
        whisper = transcribe.get_whisper_model()
        for segment in project["segments"]:
            abort.check()
            path = folder / "clips" / f"{segment['id']}.wav"
            if not segment["translation"].strip() or not path.is_file():
                problems.append({"id": segment["id"], "kind": "missing", "severity": "red"})
                continue
            if "score" in segment:  # this clip was already heard on an earlier run
                score, heard = segment["score"], segment.get("heard", "")
            else:
                heard = await whisper.transcribe(str(path), language=project["target_language"], model_size=whisper_model)
                score, _ = speech_qc.score_transcript(segment["translation"], heard, project["target_language"])
                segment.update(score=round(score, 2), heard=heard)
            if score < 0.85:
                problems.append(
                    {"id": segment["id"], "kind": "words", "severity": "red" if score < 0.6 else "yellow", "value": round(score, 2), "heard": heard}
                )
            if segment.get("overflow"):
                problems.append({"id": segment["id"], "kind": "too_long", "severity": "yellow", "value": segment.get("fit")})

    async def guarded() -> None:
        try:
            with abort.checking():
                await work()
        except abort.CheckSkipped:
            problems.append({"kind": "check_skipped", "severity": "yellow"})

    await _gpu(project, "check", guarded)
    audio, rate = sf.read(str(folder / "dub.wav"), dtype="float32")
    metrics = measure(audio, rate)
    if metrics["clip_ratio"] > 0.002:
        problems.append({"kind": "clipping", "severity": "yellow", "value": round(metrics["clip_ratio"] * 100, 2)})
    clicks_limit = speech_qc.MAX_CLICKS_PER_MIN
    if metrics["clicks_per_min"] > clicks_limit:
        problems.append({"kind": "clicks", "severity": "yellow", "value": round(metrics["clicks_per_min"])})
    severities = {p["severity"] for p in problems}
    project["qc"] = {
        "level": "red" if "red" in severities else "yellow" if severities else "green",
        "problems": problems,
        "metrics": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in metrics.items()},
    }


async def _run(project_id: str) -> None:
    project = load(project_id)
    if project is None:
        return
    folder = _folder(project_id)
    _cancelled.discard(project_id)
    _running.add(project_id)
    _line.append(project_id)
    project.update(stage="queued", error=None)
    save(project)
    try:
        while _line[0] != project_id:
            if project_id in _cancelled:
                raise _Stopped()
            await asyncio.sleep(0.25)
        await _pipeline(project, project_id, folder)
    except (_Stopped, asyncio.CancelledError):
        project.update(stage="stopped", error=None)
        save(project)
    except Exception as e:
        logger.exception("Dubbing %s failed", project_id)
        project.update(stage="failed", error=str(e))
        save(project)
    finally:
        _line.remove(project_id)
        _running.discard(project_id)
        _cancelled.discard(project_id)


async def _pipeline(project: dict, project_id: str, folder: Path) -> None:
    from ..database import get_db

    if project_id in _cancelled:
        raise _Stopped()
    wav = folder / "decoded.wav"
    if not wav.is_file():
        _update(project, "extracting", 0.02)
        await asyncio.to_thread(voice_lab.decode_media, folder / project["source"], wav, _MIX_RATE, False)
        project["has_video"] = await asyncio.to_thread(_has_video, folder / project["source"])
    if not (folder / "stems" / "background.wav").is_file():
        _update(project, "separating", 0.08)

        async def separate() -> None:
            await asyncio.to_thread(audio_clean.separate, wav, folder / "stems")

        await _gpu(project, "separate", separate)
    total = sf.info(str(wav)).duration
    project["duration"] = total
    if not project["segments"]:
        _update(project, "transcribing", 0.2)
        await _transcribe_all(project, folder)
        _update(project, "speakers", 0.36)
        if not project.get("speakers_from_srt"):
            await asyncio.to_thread(_diarize, project, folder)
        save(project)
    if project.get("mode") == "transcribe":
        _update(project, "done", 1.0)
        return
    if any(not s["translation"] for s in project["segments"]):
        _update(project, "translating", 0.4)
        await _translate_all(project)
    if not project.get("approved"):
        if not project.get("auto_continue"):
            _update(project, "review", 0.56)
            return
        project["approved"] = True

    _update(project, "synthesizing", 0.6)
    db = next(get_db())
    try:
        await _ensure_voices(project, folder, db)
    finally:
        db.close()
    await _synthesize(project, folder)
    if not any((folder / "clips" / f"{s['id']}.wav").is_file() for s in project["segments"]):
        errors = {s.get("error") for s in project["segments"] if s.get("error")}
        raise RuntimeError("Không đọc được đoạn nào. " + "; ".join(sorted(errors))[:400])
    over = _measure_fit(project, folder, total)
    if over:
        await _shorten(project, over)
        await _synthesize(project, folder)
        _measure_fit(project, folder, total)

    _update(project, "mixing", 0.88)
    voices, mix = await asyncio.to_thread(_mix, project, folder, total)
    sf.write(str(folder / "voices.wav"), voices, _MIX_RATE, subtype="PCM_16")
    sf.write(str(folder / "dub.wav"), mix, _MIX_RATE, subtype="PCM_16")
    if project.get("has_video"):
        burn = _subtitle_filter(project, folder)
        await asyncio.to_thread(_mux, folder / project["source"], folder / "dub.wav", folder / "dub.mp4", burn)
    _update(project, "mixing", 0.94)
    await _check_dub(project, folder)
    _update(project, "done", 1.0)


def start(project_id: str) -> None:
    if project_id in _running:
        return
    create_background_task(_run(project_id))


def stop(project_id: str) -> None:
    """Stop one video now, whichever stage or model it is in."""
    if project_id not in _running:
        return
    _cancelled.add(project_id)
    prefix = f"dub-{project_id}-"
    for key in task_queue.queued_keys():
        if key.startswith(prefix):
            task_queue.cancel_generation(key)
    if any(key.startswith(prefix) for key in task_queue.running_keys()):
        abort.request_stop()


def stop_waiting() -> None:
    """Drop the videos that have not started, and leave the running one alone."""
    _cancelled.update(_line[1:])


def stop_all() -> None:
    for project_id in list(_running):
        stop(project_id)


def update(project_id: str, patch: dict[str, Any]) -> dict:
    """Apply review edits. Changing a line discards only that line's audio."""
    project = load(project_id)
    if project is None:
        raise ValueError("Không tìm thấy dự án.")
    if project_id in _running:
        raise ValueError("Dự án đang chạy.")
    clips = _folder(project_id) / "clips"
    by_id = {s["id"]: s for s in project["segments"]}
    for change in patch.get("segments", []):
        segment = by_id.get(change.get("id"))
        if segment is None:
            continue
        for key in ("text", "translation", "speaker"):
            if key in change and change[key] != segment.get(key):
                segment[key] = change[key]
                if key != "text":
                    (clips / f"{segment['id']}.wav").unlink(missing_ok=True)
                    segment["shortened"] = False
                    segment["status"] = "pending"
                    segment.pop("score", None)
    for name, voice in (patch.get("speakers") or {}).items():
        if name in project["speakers"]:
            before = project["speakers"][name].get("profile_id")
            project["speakers"][name].update(voice)
            if project["speakers"][name].get("profile_id") != before:
                for s in project["segments"]:
                    if s["speaker"] == name:
                        (clips / f"{s['id']}.wav").unlink(missing_ok=True)
                        s["status"] = "pending"
                        s.pop("score", None)
    for key in ("glossary", "engine", "model_size", "target_language"):
        if key in patch:
            project[key] = patch[key]
    for key in ("original_volume", "music_volume"):
        if patch.get(key) is not None:
            project[key] = round(max(0.0, min(1.0, float(patch[key]))), 2)
    if "subtitle" in patch:
        project["subtitle"] = clean_subtitle(patch["subtitle"])
    match = patch.get("voice_match")
    if match in ("speaker", "line") and match != project.get("voice_match", "speaker"):
        project["voice_match"] = match
        # Lines of cloned speakers are read again with the other kind of reference.
        for s in project["segments"]:
            if project["speakers"].get(s["speaker"], {}).get("mode") == "clone":
                (clips / f"{s['id']}.wav").unlink(missing_ok=True)
                s["status"] = "pending"
                s.pop("score", None)
    if patch.get("approved") is not None:
        project["approved"] = bool(patch["approved"])
    save(project)
    return project


def retranslate(project_id: str) -> dict:
    project = load(project_id)
    if project is None:
        raise ValueError("Không tìm thấy dự án.")
    project.pop("brief", None)
    for s in project["segments"]:
        s["translation"] = ""
        s["shortened"] = False
    shutil.rmtree(_folder(project_id) / "clips", ignore_errors=True)
    project["approved"] = False
    save(project)
    start(project_id)
    return project


def output_path(project_id: str, which: str) -> Path:
    names = {"video": "dub.mp4", "audio": "dub.wav", "voices": "voices.wav", "background": "stems/background.wav"}
    return _folder(project_id) / names[which]


def subtitles(project: dict, which: str, fmt: str) -> str:
    from ..utils.audio_post import _fmt_ts

    key = "text" if which == "source" else "translation"
    rows = [s for s in project["segments"] if s.get(key)]
    if fmt == "txt":
        return "\n".join(s[key] for s in rows)
    lines = ["WEBVTT", ""] if fmt == "vtt" else []
    for i, s in enumerate(rows, 1):
        start, end = _fmt_ts(s["start"]), _fmt_ts(s["end"])
        if fmt == "vtt":
            start, end = start.replace(",", "."), end.replace(",", ".")
        else:
            lines.append(str(i))
        lines += [f"{start} --> {end}", s[key], ""]
    return "\n".join(lines)
