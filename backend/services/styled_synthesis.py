"""Synthesis with emotion spans and speech QC; used by every generation path."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .. import config
from ..utils.chunked_tts import chunk_segments, concatenate_audio_chunks, generate_chunked
from ..utils.expressive import (
    DEFAULT_INTENSITY,
    NATIVE_SPEED_ENGINES,
    NEUTRAL,
    SynthStyle,
    apply_prosody,
    compose_instruct,
    parse_emotion_spans,
    prosody_factor,
    use_style,
)
from ..utils.text_rules import build_text_transform
from ..database import ProfileSample
from . import profiles, speaker, speech_qc

logger = logging.getLogger(__name__)

# About 15 s of speech, the length OmniVoice is built to generate at once.
# Measured with independent transcription: Vietnamese 0.99, Spanish 0.995.
OMNIVOICE_MAX_CHUNK_CHARS = 240

# Qwen is autoregressive and drifts away from the reference over a long take:
# in 40 s chunks similarity fell from 0.96 to 0.92 by the end of every chunk.
# ~20 s chunks restart from the reference before the drift is audible.
QWEN_MAX_CHUNK_CHARS = 300

# Chunk limits count characters of Latin-script text (~15 per second spoken).
# Japanese and Chinese speak ~6 per second, so Qwen got 45 s chunks; at 0.4x
# Japanese took 91 s instead of 114 s at the same quality. OmniVoice gained
# nothing, and VoiceDesign changed voice at every extra chunk, so only Qwen.
DENSE_SCRIPT_CHUNK_SCALE = {"ja": 0.4, "zh": 0.4}

ENGINE_MAX_CHUNK_CHARS = {
    "omnivoice": OMNIVOICE_MAX_CHUNK_CHARS,
    "qwen": QWEN_MAX_CHUNK_CHARS,
    "qwen_voice_design": QWEN_MAX_CHUNK_CHARS,
}


@dataclass
class ResolvedPrompts:
    prompts: dict[str, dict]
    has_sample: dict[str, bool]
    reference_paths: dict[str, list[str]] = field(default_factory=dict)


async def resolve_prompts(
    profile_id: str,
    db,
    engine: str,
    text: str,
    emotion: Optional[str],
    use_cache: bool = True,
) -> ResolvedPrompts:
    """Build one voice prompt per emotion used in *text*."""
    needed = {e for e, _ in parse_emotion_spans(text, emotion)} or {NEUTRAL}
    prompts: dict[str, dict] = {}
    has_sample: dict[str, bool] = {}
    samples = db.query(ProfileSample).filter_by(profile_id=profile_id).all()
    reference_paths = {
        e: [str(config.resolve_storage_path(s.audio_path)) for s in profiles.select_samples_for_emotion(samples, e)[0]]
        for e in needed
    }
    for e in needed:
        prompts[e], has_sample[e] = await profiles.create_voice_prompt_for_emotion(
            profile_id, db, engine=engine, emotion=e, use_cache=use_cache
        )
    return ResolvedPrompts(prompts, has_sample, reference_paths)


async def prepare_voice(
    *,
    profile_id: str,
    db,
    engine: str,
    model_size: str,
    text: str,
    language: str,
    emotion: Optional[str],
    intensity: Optional[float] = None,
    instruct: Optional[str] = None,
    use_cache: bool = True,
    on_load=None,
):
    """Load the model and build the voice prompts: ``(tts_model, engine, resolved)``.

    A designed voice on VoiceDesign is read by Qwen TTS, cloned from the voice's
    fixed clip (see design_anchor), so every chunk and every take is the same
    person. The returned engine is the one that actually speaks.
    """
    from ..backends import get_tts_backend_for_engine, load_engine_model
    from ..database import VoiceProfile
    from . import design_anchor, vram

    if engine == design_anchor.DESIGN_ENGINE:
        profile = db.query(VoiceProfile).filter_by(id=profile_id).first()
        clone = get_tts_backend_for_engine(design_anchor.CLONE_ENGINE)
        if profile is not None and profile.voice_type == "designed" and clone._is_model_cached(model_size):
            level = DEFAULT_INTENSITY if intensity is None else float(intensity)
            needed = {e for e, _ in parse_emotion_spans(text, emotion)} or {NEUTRAL}
            clips = {e: await design_anchor.ensure(profile, language, e, level, instruct) for e in needed}
            engine = design_anchor.CLONE_ENGINE
            if not clone.is_loaded():
                if on_load:
                    await on_load()
                vram.before_engine_load(engine)
            await load_engine_model(engine, model_size)
            prompts = {}
            for e, (path, line) in clips.items():
                prompts[e], _ = await clone.create_voice_prompt(path, line, use_cache=use_cache)
            return clone, engine, ResolvedPrompts(prompts, {e: True for e in clips}, {e: [p] for e, (p, _) in clips.items()})

    model = get_tts_backend_for_engine(engine)
    if not model.is_loaded():
        if on_load:
            await on_load()
        vram.before_engine_load(engine)
    await load_engine_model(engine, model_size)
    return model, engine, await resolve_prompts(profile_id, db, engine, text, emotion, use_cache)


async def reference_embedding(resolved: ResolvedPrompts, emotion: str = NEUTRAL):
    """Voice embedding of the clips behind *emotion*, or None for preset voices."""
    paths = resolved.reference_paths.get(emotion) or next(iter(resolved.reference_paths.values()), [])
    return await asyncio.to_thread(speaker.embed_files, paths) if paths else None


async def synthesize_styled(
    *,
    tts_model,
    engine: str,
    resolved: ResolvedPrompts,
    text: str,
    language: str,
    seed: Optional[int],
    instruct: Optional[str],
    emotion: Optional[str],
    intensity: Optional[float],
    trim_fn=None,
    runaway_detector=None,
    max_chunk_chars: Optional[int] = None,
    crossfade_ms: Optional[int] = None,
    sentence_pause_ms: int = 0,
    speed: Optional[float] = None,
) -> tuple[np.ndarray, int, list[dict], Optional[dict]]:
    """Generate *text*; return ``(audio, sample_rate, segments, qc_report)``."""
    level = DEFAULT_INTENSITY if intensity is None else float(intensity)
    spans = parse_emotion_spans(text, emotion)
    if not spans:
        raise ValueError("Nothing to speak: the text only contains emotion tags")

    from ..utils.audio_post import effective_speed

    native_speed = effective_speed(speed) if engine in NATIVE_SPEED_ENGINES else 1.0
    qc = speech_qc.build_qc(engine, language)
    fade = 50 if crossfade_ms is None else crossfade_ms
    kwargs: dict = dict(
        language=language,
        trim_fn=trim_fn,
        runaway_detector=runaway_detector,
        text_transform=build_text_transform(language),
        crossfade_ms=fade,
        qc=qc,
        sentence_pause_ms=sentence_pause_ms,
    )
    if max_chunk_chars is not None:
        kwargs["max_chunk_chars"] = max_chunk_chars
    if engine in ENGINE_MAX_CHUNK_CHARS:
        kwargs["max_chunk_chars"] = min(kwargs.get("max_chunk_chars", 800), ENGINE_MAX_CHUNK_CHARS[engine])
    if engine == "qwen" and language in DENSE_SCRIPT_CHUNK_SCALE and "max_chunk_chars" in kwargs:
        kwargs["max_chunk_chars"] = int(kwargs["max_chunk_chars"] * DENSE_SCRIPT_CHUNK_SCALE[language])

    parts: list[np.ndarray] = []
    span_segments: list[list[dict]] = []
    sample_rate = 24000
    for i, (span_emotion, span_text) in enumerate(spans):
        segments: list[dict] = []
        if qc is not None:
            qc.reference = await reference_embedding(resolved, span_emotion)
        with use_style(SynthStyle(span_emotion, level, speed=native_speed)):
            audio, sample_rate = await generate_chunked(
                tts_model,
                span_text,
                resolved.prompts[span_emotion],
                seed=None if seed is None else seed + i * 100,
                instruct=compose_instruct(engine, span_emotion, level, instruct),
                segments_out=segments,
                **kwargs,
            )
        factor = prosody_factor(engine, resolved.has_sample.get(span_emotion, False))
        raw_len = len(audio)
        audio = await asyncio.to_thread(apply_prosody, audio, sample_rate, span_emotion, level, factor)
        if raw_len and len(audio) != raw_len:
            k = len(audio) / raw_len
            segments = [{**s, "start": s["start"] * k, "end": s["end"] * k} for s in segments]
        segments = [{**s, "emotion": span_emotion} for s in segments]
        parts.append(np.asarray(audio, dtype=np.float32))
        span_segments.append(segments)

    report = qc.report() if qc else None
    if len(parts) == 1:
        return parts[0], sample_rate, span_segments[0], report

    merged = concatenate_audio_chunks(parts, sample_rate, crossfade_ms=fade)
    offsets = chunk_segments(parts, [""] * len(parts), sample_rate, fade)
    all_segments = [
        {**s, "start": s["start"] + off["start"], "end": s["end"] + off["start"]}
        for off, segs in zip(offsets, span_segments)
        for s in segs
    ]
    return merged, sample_rate, all_segments, report


# ── Request style sidecar: lets Regenerate reuse emotion and speed ─────────


def _style_path(generation_id: str):
    return config.get_generations_dir() / f"{generation_id}.style.json"


def save_request_style(generation_id: str, style: dict) -> None:
    try:
        _style_path(generation_id).write_text(json.dumps(style), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not save request style: %s", e)


def load_request_style(generation_id: str) -> dict:
    try:
        return json.loads(_style_path(generation_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
