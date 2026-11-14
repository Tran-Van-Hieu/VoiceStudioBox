"""One fixed reference clip per designed voice, so it stays the same person.

Qwen VoiceDesign invents the voice anew on every call: across the chunks of a
long text the same description drifted (speaker consistency 0.87-0.91 vs 0.96+
for a cloned voice), and every Studio line or batch item was a new take too.
Instead the description is spoken once into a short clip (per language,
emotion and instruction), kept on disk, and every chunk is cloned from that
clip with Qwen TTS: consistency 0.96-0.97 on long en/es/ja texts.

Takes vary a lot by seed (Japanese naturalness 2.9 to 4.3 for the same line),
so several are made and scored: words by Whisper, naturalness by UTMOS. The
best is used; all are kept so the user can listen and pick another.

Files per clip group, in the data folder's cache/design_anchors:
  <profile>_<key>.json        what was asked, the scores, the chosen take
  <profile>_<key>.take<N>.wav the takes
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

from .. import config

logger = logging.getLogger(__name__)

DESIGN_ENGINE = "qwen_voice_design"
CLONE_ENGINE = "qwen"
_TAKES = 4
_WORDS_PASS = 0.9
_VD_LANGUAGES = {"zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"}
_STEM_RE = re.compile(r"^[0-9a-f-]{36}_[0-9a-f]{12}$")

# Narration lines, the way these voices are mostly used. A Spanish audition-style
# line was misheard on 4 of 5 takes; this one passed 5 of 5 with steadier quality.
_LINES = {
    "en": "Good evening. Tonight I want to tell you a story about a small town by the sea, where every house had a lantern in the window.",
    "es": "Buenas tardes. Hoy quiero contarles una historia sobre un pequeño pueblo junto al mar, donde cada casa tenía una lámpara en la ventana.",
    "ja": "こんばんは。今夜は、海のそばの小さな町のお話をしましょう。その町では、どの家の窓にも小さな明かりが灯っていました。",
    "zh": "晚上好。今天我想给大家讲一个海边小镇的故事，那里每一户人家的窗前都点着一盏小灯。",
    "ko": "안녕하세요. 오늘 밤에는 바닷가 작은 마을의 이야기를 들려드리려고 합니다. 그 마을의 집집마다 창가에 작은 등불이 켜져 있었어요.",
    "de": "Guten Abend. Heute möchte ich Ihnen eine Geschichte über eine kleine Stadt am Meer erzählen, in der in jedem Fenster eine Laterne brannte.",
    "fr": "Bonsoir. Ce soir, je veux vous raconter l'histoire d'une petite ville au bord de la mer, où chaque maison avait une lanterne à la fenêtre.",
    "ru": "Добрый вечер. Сегодня я хочу рассказать вам историю о маленьком городке у моря, где в каждом окне горел фонарь.",
    "pt": "Boa noite. Hoje quero contar uma história sobre uma pequena cidade à beira-mar, onde cada casa tinha uma lanterna na janela.",
    "it": "Buonasera. Stasera voglio raccontarvi la storia di una piccola città sul mare, dove ogni casa aveva una lanterna alla finestra.",
}


def _dir() -> Path:
    d = config.get_data_dir() / "cache" / "design_anchors"
    d.mkdir(parents=True, exist_ok=True)
    return d


def clip_language(language: str) -> str:
    # VoiceDesign cannot speak e.g. Vietnamese; the clip is then English and
    # the clone speaks the target language with that voice.
    return language if language in _VD_LANGUAGES else "en"


def _stem(profile, lang: str, emotion: str, intensity: float, instruct: Optional[str]) -> str:
    level = 0.0 if emotion == "neutral" else round(float(intensity), 2)
    key = json.dumps([profile.design_prompt, _LINES[lang], emotion, level, instruct or ""], ensure_ascii=False)
    return f"{profile.id}_{hashlib.sha1(key.encode()).hexdigest()[:12]}"


def _take_file(stem: str, index: int) -> Path:
    return _dir() / f"{stem}.take{index}.wav"


def _meta(stem: str) -> Optional[dict]:
    path = _dir() / f"{stem}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_meta(stem: str, meta: dict) -> None:
    (_dir() / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def _pick(takes: list[dict]) -> int:
    """Most natural take among those read correctly, else the best-read one."""
    passed = [i for i, t in enumerate(takes) if t["words"] >= _WORDS_PASS]
    if passed:
        return max(passed, key=lambda i: takes[i]["mos"] or 0.0)
    return max(range(len(takes)), key=lambda i: takes[i]["words"])


async def ensure(profile, language: str, emotion: str, intensity: float, instruct: Optional[str]) -> tuple[str, str]:
    """Path and transcript of the profile's chosen clip, designing it on first use."""
    lang = clip_language(language)
    stem = _stem(profile, lang, emotion, intensity, instruct)
    meta = _meta(stem)
    if meta is None or not _take_file(stem, meta["chosen"]).is_file():
        meta = await design(profile, lang, emotion, intensity, instruct)
    return str(_take_file(stem, meta["chosen"])), meta["text"]


async def design(profile, language: str, emotion: str, intensity: float, instruct: Optional[str], round_: int = 0) -> dict:
    """Make and score a set of takes; *round_* > 0 gives a fresh set of seeds."""
    from ..backends import get_tts_backend_for_engine
    from ..utils.expressive import compose_instruct
    from . import naturalness, speech_qc, vram

    lang = clip_language(language)
    stem = _stem(profile, lang, emotion, intensity, instruct)
    text = _LINES[lang]
    vram.unload_tts(except_engine=DESIGN_ENGINE)
    designer = get_tts_backend_for_engine(DESIGN_ENGINE)
    voice = {"voice_type": "designed", "design_prompt": profile.design_prompt}
    style = compose_instruct(DESIGN_ENGINE, emotion, intensity, instruct)
    qc = speech_qc.build_qc(DESIGN_ENGINE, lang)
    seed = 7 + 1000 * round_
    try:
        # One pass for all takes: rows of a batch sample independently.
        made = await designer.generate_batch([text] * _TAKES, voice, lang, seed, style)
    finally:
        # Free the designer before Whisper loads: together they peaked at 7.1 GB.
        designer.unload_model()
    takes: list[dict] = []
    try:
        for i, (audio, rate) in enumerate(made):
            audio = np.asarray(audio, dtype=np.float32)
            peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
            if peak > 0.95:
                audio = audio * (0.95 / peak)
            try:
                words = (await qc.evaluate(audio, rate, text)).score if qc is not None else 1.0
            except Exception as e:  # checks skipped or Whisper unavailable: count it as read right
                logger.info("Design take not checked: %s", e)
                words = 1.0
            mos = await asyncio.to_thread(naturalness.score, audio, rate)
            await asyncio.to_thread(sf.write, str(_take_file(stem, i)), audio, rate, "PCM_16")
            takes.append({"seed": seed, "words": round(float(words), 2), "mos": mos, "seconds": round(len(audio) / rate, 1)})
    finally:
        naturalness.release()
    meta = {
        "profile_id": profile.id,
        "language": lang,
        "emotion": emotion,
        "intensity": intensity,
        "instruct": instruct or "",
        "text": text,
        "takes": takes,
        "chosen": _pick(takes),
        "picked_by": "auto",
        "round": round_,
        "created_at": time.time(),
    }
    _write_meta(stem, meta)
    logger.info("Designed %d takes for %s (%s, %s); using take %d %s", len(takes), profile.id, lang, emotion, meta["chosen"], takes[meta["chosen"]])
    return meta


def groups(profile_id: str) -> list[dict]:
    """Every clip group of a profile, newest first, with its stem for the API."""
    out = []
    for path in _dir().glob(f"{profile_id}_*.json"):
        meta = _meta(path.stem)
        if meta is not None:
            out.append({**meta, "id": path.stem})
    return sorted(out, key=lambda m: -m.get("created_at", 0))


def _checked(profile_id: str, stem: str) -> dict:
    if not _STEM_RE.match(stem) or not stem.startswith(f"{profile_id}_"):
        raise ValueError("Không tìm thấy giọng mẫu.")
    meta = _meta(stem)
    if meta is None:
        raise ValueError("Không tìm thấy giọng mẫu.")
    return meta


def choose(profile_id: str, stem: str, index: int) -> dict:
    meta = _checked(profile_id, stem)
    if not 0 <= index < len(meta["takes"]) or not _take_file(stem, index).is_file():
        raise ValueError("Không có bản này.")
    meta.update(chosen=index, picked_by="user")
    _write_meta(stem, meta)
    return {**meta, "id": stem}


def take_path(profile_id: str, stem: str, index: int) -> Path:
    meta = _checked(profile_id, stem)
    path = _take_file(stem, index)
    if not 0 <= index < len(meta["takes"]) or not path.is_file():
        raise ValueError("Không có bản này.")
    return path


async def redesign(profile, stem: str) -> dict:
    """A fresh set of takes for an existing group (when none of them sounds right)."""
    meta = _checked(profile.id, stem)
    new = await design(profile, meta["language"], meta["emotion"], meta["intensity"], meta["instruct"] or None, meta.get("round", 0) + 1)
    return {**new, "id": stem}
