"""Regenerate one flagged segment and splice it into the finished audio."""

from __future__ import annotations

import asyncio
import json
import uuid

import numpy as np
import soundfile as sf

from .. import config
from ..database import Generation, get_db
from ..utils.audio import save_audio
from ..utils.audio_post import change_speed, load_final, load_segments, save_segments, segments_path
from ..utils.expressive import NATIVE_SPEED_ENGINES
from . import audio_clean, extras, final_check, versions, vram
from .styled_synthesis import load_request_style, prepare_voice, reference_embedding, synthesize_styled
from .task_queue import run_exclusive

_CROSSFADE_S = 0.03


def _is_worse(new: dict | None, old: dict | None) -> bool:
    """A repair must never replace audio with something that checks out worse."""
    if not new or not old:
        return False
    rank = {"green": 0, "yellow": 1, "red": 2}
    if rank[new["level"]] != rank[old["level"]]:
        return rank[new["level"]] > rank[old["level"]]
    return (new.get("score") or 0) < (old.get("score") or 0) - 0.05


def _match_level(new: np.ndarray, old: np.ndarray) -> np.ndarray:
    a = float(np.sqrt(np.mean(new**2))) if len(new) else 0.0
    b = float(np.sqrt(np.mean(old**2))) if len(old) else 0.0
    return new * (b / a) if a > 1e-5 and b > 1e-5 else new


def _splice(audio: np.ndarray, start: int, end: int, patch: np.ndarray, fade: int) -> np.ndarray:
    head, tail = audio[:start], audio[end:]
    patch = np.array(patch, dtype=np.float32)
    if len(head) >= fade and len(patch) >= fade:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)
        patch[:fade] = patch[:fade] * ramp + audio[start : start + fade][: fade] * ramp[::-1]
    if len(tail) >= fade and len(patch) >= fade:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)
        patch[-fade:] = patch[-fade:] * ramp[::-1] + tail[:fade] * ramp
    return np.concatenate([head, patch, tail])


async def repair_segment(generation_id: str, index: int) -> dict:
    """Replace segment *index* with a fresh take and re-check the whole file."""
    from ..backends import engine_needs_trim
    from ..utils.audio import trim_tts_output

    db = next(get_db())
    try:
        gen = db.query(Generation).filter_by(id=generation_id).first()
        if gen is None or (gen.status or "completed") != "completed":
            raise ValueError("Bản tạo này chưa sẵn sàng để sửa.")
        segments = load_segments(generation_id, gen.text or "", float(gen.duration or 0))
        if not 0 <= index < len(segments):
            raise ValueError("Không tìm thấy đoạn cần sửa.")
        segment = segments[index]
        style = load_request_style(generation_id)
        engine, size, language, profile_id = gen.engine, gen.model_size or "1.7B", gen.language, gen.profile_id

        async def work() -> dict:
            with vram.engine_in_use(engine):
                emotion = segment.get("emotion") or style.get("emotion")
                model, speaker_engine, resolved = await prepare_voice(
                    profile_id=profile_id,
                    db=db,
                    engine=engine,
                    model_size=size,
                    text=segment["text"],
                    language=language,
                    emotion=emotion,
                    intensity=style.get("emotion_intensity"),
                    instruct=gen.instruct,
                )
                fresh, rate, _, _ = await synthesize_styled(
                    tts_model=model,
                    engine=speaker_engine,
                    resolved=resolved,
                    text=segment["text"],
                    language=language,
                    seed=None,
                    instruct=gen.instruct,
                    emotion=emotion,
                    intensity=style.get("emotion_intensity"),
                    trim_fn=trim_tts_output if engine_needs_trim(engine) else None,
                    speed=style.get("speed"),
                )
                if style.get("denoise"):
                    fresh = await asyncio.to_thread(audio_clean.denoise, fresh, rate, style["denoise"])
                if engine not in NATIVE_SPEED_ENGINES:
                    speed = style.get("speed") or extras.get("speed") or 1.0
                    fresh = await asyncio.to_thread(change_speed, fresh, rate, speed)

                path = config.resolve_storage_path(gen.audio_path)
                audio, stored_rate = sf.read(str(path), dtype="float32")
                if stored_rate != rate:
                    import librosa

                    fresh = librosa.resample(fresh, orig_sr=rate, target_sr=stored_rate)
                    rate = stored_rate
                start, end = int(segment["start"] * rate), int(segment["end"] * rate)
                fresh = _match_level(fresh, audio[start:end])
                merged = _splice(audio, start, end, fresh, int(rate * _CROSSFADE_S))
                delta = (len(merged) - len(audio)) / rate
                updated = []
                for i, s in enumerate(segments):
                    if i == index:
                        updated.append({**s, "end": s["start"] + len(fresh) / rate})
                    elif i > index:
                        updated.append({**s, "start": s["start"] + delta, "end": s["end"] + delta})
                    else:
                        updated.append(s)
                reference = await reference_embedding(resolved)
                try:
                    qc = json.loads(segments_path(generation_id).read_text(encoding="utf-8")).get("qc")
                except (OSError, ValueError):
                    qc = None
                final = await final_check.run(merged, rate, updated, language, engine, reference, None)
                previous = load_final(generation_id)
                if _is_worse(final, previous):
                    return {"final": previous, "duration": float(gen.duration or 0), "kept_previous": True}

            out = config.get_generations_dir() / f"{generation_id}_{uuid.uuid4().hex[:8]}.wav"
            save_audio(merged, str(out), rate)
            stored = config.to_storage_path(out)
            count = len(versions.list_versions(generation_id, db))
            versions.create_version(generation_id, f"fix-{count + 1}", stored, db, is_default=True)
            duration = len(merged) / rate
            gen.audio_path, gen.duration = stored, duration
            db.commit()
            save_segments(generation_id, updated, duration, qc, final)
            return {"final": final, "duration": duration}

        return await run_exclusive(f"repair-{generation_id}-{index}", work)
    finally:
        db.close()
