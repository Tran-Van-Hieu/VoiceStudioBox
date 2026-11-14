"""Turn any recording into the best possible cloning reference, and audition models on it."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import numpy as np
import soundfile as sf

from .. import config, models
from ..utils.audio_quality import estimate_snr_db, measure
from . import audio_clean, extras, profiles, speaker, speech_qc, transcribe
from .task_queue import create_background_task, run_exclusive

logger = logging.getLogger(__name__)

MAX_CLIP_SECONDS = 30.0
_MIN_CLIP_SECONDS = 2.0
_OMNIVOICE_MAX_REFERENCE = 20.0
_PEAK_POINTS = 1200
_RATE = 44100
_MUSIC_RATIO_DB = -22.0
# Clean voices measured 41-77 dB; samples at 33 dB (noise floor -45 dB) gave
# clones an audible hiss, and cleaning them cost nothing measurable.
_NOISY_SNR_DB = 40.0
_AUTO_DENOISE_STRENGTH = 0.7
_AUDITION_SEEDS = (11, 23, 37)
_MIN_AUDITION_SCORE = 0.7

_AUDITION_TEXT = {
    "vi": "Chào bạn, hôm nay trời thật đẹp. Mình đang đọc thử một đoạn ngắn để nghe giọng này có tự nhiên hay không.",
    "en": "Hello there, it is a beautiful day. I am reading a short passage to hear whether this voice sounds natural.",
    "zh": "你好，今天天气真好。我正在读一小段文字，听听这个声音是否自然。",
    "ja": "こんにちは、今日はとてもいい天気ですね。この声が自然に聞こえるか、短い文章を読んでいます。",
    "ko": "안녕하세요, 오늘 날씨가 정말 좋네요. 이 목소리가 자연스러운지 짧은 글을 읽어 보고 있습니다.",
    "fr": "Bonjour, il fait très beau aujourd'hui. Je lis un court passage pour entendre si cette voix semble naturelle.",
    "de": "Hallo, heute ist ein schöner Tag. Ich lese einen kurzen Text, um zu hören, ob diese Stimme natürlich klingt.",
    "es": "Hola, hoy hace un día precioso. Estoy leyendo un texto breve para escuchar si esta voz suena natural.",
}

_jobs: dict[str, dict[str, Any]] = {}


def _root() -> Path:
    path = config.get_data_dir() / "voicelab"
    path.mkdir(parents=True, exist_ok=True)
    return path


def clear_stale_jobs() -> None:
    """Delete prepare jobs left from an earlier run (jobs live in memory only).

    A one-hour video leaves several GB of decoded audio and stems here; saved
    voices hold their own copy under profiles/, so nothing else needs these.
    """
    for folder in _root().iterdir():
        if folder.is_dir() and folder.name != "auditions":
            shutil.rmtree(folder, ignore_errors=True)


def delete_auditions(profile_id: str) -> None:
    shutil.rmtree(_root() / "auditions" / profile_id, ignore_errors=True)


def decode_media(source: Path, target: Path, rate: int = _RATE, mono: bool = True) -> None:
    """Decode any audio or video file to a WAV file."""
    from ..utils import ffmpeg

    args = ["-loglevel", "error", "-y", "-i", str(source), "-vn", "-ar", str(rate)]
    if mono:
        args += ["-ac", "1"]
    args += ["-c:a", "pcm_f32le", str(target)]
    result = ffmpeg.run(args, "Cần cài ffmpeg để đọc file này (winget install Gyan.FFmpeg).")
    if result.returncode != 0:
        raise RuntimeError("Không đọc được file: " + result.stderr.decode(errors="replace")[-300:])


def _analysis(x: np.ndarray, rate: int, music: bool) -> dict:
    m = measure(x, rate)
    seconds = len(x) / rate
    snr = estimate_snr_db(x, rate)
    middle = len(x) // 2
    purity = speaker.similarity(speaker.embed(x[:middle], rate), speaker.embed(x[middle:], rate))
    issues: list[str] = []
    points = 100
    if seconds < 3:
        issues.append("too_short")
        points -= 30
    if m["lufs"] is not None and m["lufs"] < -32:
        issues.append("too_quiet")
        points -= 10
    if m["clip_ratio"] > 0.002:
        issues.append("clipping")
        points -= 25
    if snr < _NOISY_SNR_DB:
        issues.append("noisy")
        points -= 20
    if music:
        issues.append("music")
        points -= 10
    if purity is not None and purity < 0.7:
        issues.append("multi_speaker")
        points -= 35
    return {
        "duration": round(seconds, 2),
        "score": max(0, points),
        "issues": issues,
        "snr_db": round(snr, 1),
        "lufs": None if m["lufs"] is None else round(m["lufs"], 1),
        "clip_ratio": round(m["clip_ratio"], 4),
        "speaker_consistency": None if purity is None else round(purity, 2),
    }


async def check_sample(path: str) -> dict:
    """Quality report for a sample about to be added to a voice (see _analysis)."""
    from ..utils.audio import load_audio

    audio, rate = await asyncio.to_thread(load_audio, path, 24000)
    return await asyncio.to_thread(_analysis, np.asarray(audio, dtype=np.float32), rate, False)


async def clean_sample(audio: np.ndarray, rate: int) -> np.ndarray:
    """The sample with background noise removed, queued like any GPU job."""

    async def work() -> np.ndarray:
        return await asyncio.to_thread(audio_clean.denoise, audio, rate, _AUTO_DENOISE_STRENGTH)

    return await run_exclusive(f"clean-sample-{uuid.uuid4().hex[:8]}", work)


def pick_reference(samples: list) -> tuple[str, str]:
    """The sample OmniVoice clones from when a voice has several.

    OmniVoice takes one clip, and a transcript only up to 20 s. Nothing is cut:
    the clip is the one the user chose.
    """
    timed = []
    for sample in samples:
        path = config.resolve_storage_path(sample.audio_path)
        timed.append((sf.info(str(path)).duration, str(path), sample))
    fitting = [t for t in timed if t[0] <= _OMNIVOICE_MAX_REFERENCE] or timed
    _, path, sample = max(fitting, key=lambda t: t[0])
    return path, sample.reference_text


async def _detect_and_transcribe(path: Path, language: Optional[str]) -> tuple[str, Optional[str]]:
    model_size = extras.get("qc_whisper_model")
    text = await transcribe.get_whisper_model().transcribe(str(path), language=language, model_size=model_size)
    if language:
        return text, language
    return text, await detect_language(path, model_size)


async def detect_language(path: Path, model_size: str) -> Optional[str]:
    """The language Whisper hears in the first 30 seconds of *path*."""
    from ..backends import get_stt_backend
    from ..utils.audio import load_audio

    stt = get_stt_backend()
    await stt.load_model_async(model_size)

    def work() -> Optional[str]:
        import torch

        audio, _ = load_audio(str(path), sample_rate=16000)
        features = stt.processor(audio[: 16000 * 30], sampling_rate=16000, return_tensors="pt").input_features
        features = features.to(stt.device)
        if stt.device == "cuda":
            features = features.half()
        with torch.no_grad():
            token = int(stt.model.detect_language(features)[0])
        names = {v: k for k, v in stt.model.generation_config.lang_to_id.items()}
        return names.get(token, "").strip("<|>") or None

    return await asyncio.to_thread(work)


def _peaks(x: np.ndarray) -> list[float]:
    """A loudness outline of the whole recording for the waveform picker."""
    size = max(1, len(x) // _PEAK_POINTS)
    usable = x[: len(x) - len(x) % size]
    outline = np.abs(usable).reshape(-1, size).max(axis=1)
    top = float(outline.max()) or 1.0
    return [round(float(v) / top, 3) for v in outline]


def _pipeline(source: Path, folder: Path, isolate: str, denoise: str) -> dict:
    """Clean the whole recording; the user then picks the part to clone."""
    wav = folder / "decoded.wav"
    decode_media(source, wav)
    original, rate = sf.read(str(wav), dtype="float32")
    processed = original
    steps: list[str] = []

    music = False
    if isolate != "off":
        vocals_path, background_path = audio_clean.separate(wav, folder / "stems")
        vocals, stem_rate = sf.read(str(vocals_path), dtype="float32")
        background, _ = sf.read(str(background_path), dtype="float32")
        ratio = 20 * np.log10((np.sqrt(np.mean(background**2)) + 1e-9) / (np.sqrt(np.mean(vocals**2)) + 1e-9))
        music = bool(ratio > _MUSIC_RATIO_DB)
        if isolate == "on" or music:
            import librosa

            mono = vocals.mean(axis=1)
            processed = librosa.resample(mono, orig_sr=stem_rate, target_sr=rate) if stem_rate != rate else mono
            processed = processed[: len(original)]
            steps.append("isolated")
    if denoise == "on" or (denoise == "auto" and estimate_snr_db(processed, rate) < _NOISY_SNR_DB):
        processed = audio_clean.denoise(processed, rate, _AUTO_DENOISE_STRENGTH if denoise == "auto" else 1.0)
        steps.append("denoised")

    sf.write(str(folder / "full_raw.wav"), original, rate, subtype="PCM_16")
    sf.write(str(folder / "full_clean.wav"), processed, rate, subtype="PCM_16")
    return {
        "steps": steps,
        "music": music,
        "source_seconds": round(len(original) / rate, 2),
        "peaks": _peaks(processed),
    }


def _cut(folder: Path, start: float, end: float, music: bool) -> dict:
    """Write the chosen stretch of both versions, exactly as the user picked it."""
    out = {}
    for name in ("raw", "clean"):
        full, rate = sf.read(str(folder / f"full_{name}.wav"), dtype="float32")
        piece = np.array(full[int(start * rate) : int(end * rate)], dtype=np.float32)
        fade_length = int(rate * 0.01)
        fade = np.linspace(0, 1, fade_length, dtype=np.float32)
        piece[:fade_length] *= fade
        piece[-fade_length:] *= fade[::-1]
        peak = float(np.max(np.abs(piece)))
        piece = piece * (0.89 / peak) if peak > 0.89 else piece
        sf.write(str(folder / f"{name}.wav"), piece, rate, subtype="PCM_16")
        out[name] = _analysis(piece, rate, music and name == "raw")
    return out


async def _run_prepare(job_id: str, source: Path, isolate: str, denoise: str) -> None:
    job = _jobs[job_id]
    folder = source.parent
    try:
        job.update(stage="analyzing")

        async def gpu_work() -> dict:
            return await asyncio.to_thread(_pipeline, source, folder, isolate, denoise)

        job["source"] = await run_exclusive(f"voicelab-{job_id}", gpu_work)
        job["stage"] = "ready"
    except asyncio.CancelledError:
        job.update(stage="failed", error="Đã dừng theo yêu cầu")
    except Exception as e:
        logger.exception("Reference preparation failed")
        job.update(stage="failed", error=str(e))


def start_prepare(upload: Path, isolate: str, denoise: str) -> dict:
    job_id = uuid.uuid4().hex
    folder = _root() / job_id
    folder.mkdir(parents=True)
    source = folder / f"source{upload.suffix.lower()}"
    shutil.move(str(upload), source)
    _jobs[job_id] = {"id": job_id, "stage": "queued", "source": None, "result": None, "error": None}
    create_background_task(_run_prepare(job_id, source, isolate, denoise))
    return _jobs[job_id]


async def _run_select(job_id: str, start: float, end: float, language: Optional[str], text: str) -> None:
    job = _jobs[job_id]
    folder = _root() / job_id
    try:

        async def gpu_work() -> dict:
            clips = await asyncio.to_thread(_cut, folder, start, end, job["source"]["music"])
            transcript, detected = await _detect_and_transcribe(folder / "clean.wav", language)
            return {
                **clips,
                "window": [round(start, 2), round(end, 2)],
                "transcript": text.strip() or transcript,
                "auto_transcript": transcript,
                "language": detected,
            }

        job["result"] = await run_exclusive(f"voicelab-{job_id}-select", gpu_work)
        job["stage"] = "done"
    except asyncio.CancelledError:
        job.update(stage="ready", error="Đã dừng theo yêu cầu")
    except Exception as e:
        logger.exception("Clip selection failed")
        job.update(stage="ready", error=str(e))


def select_clip(job_id: str, start: float, end: float, language: Optional[str], text: str) -> dict:
    """Cut and transcribe the part of the recording the user marked."""
    job = _jobs.get(job_id)
    if job is None or not job.get("source"):
        raise ValueError("Bản ghi không còn trên máy, hãy tải lại.")
    start, end = max(0.0, start), min(float(job["source"]["source_seconds"]), end)
    if end - start < _MIN_CLIP_SECONDS:
        raise ValueError(f"Hãy chọn đoạn dài ít nhất {_MIN_CLIP_SECONDS:.0f} giây.")
    if end - start > MAX_CLIP_SECONDS:
        raise ValueError(f"Hãy chọn đoạn dài tối đa {MAX_CLIP_SECONDS:.0f} giây.")
    job.update(stage="transcribing", result=None, error=None)
    create_background_task(_run_select(job_id, start, end, language, text))
    return job


def get_job(job_id: str) -> Optional[dict]:
    return _jobs.get(job_id)


def clip_path(job_id: str, which: str) -> Path:
    if which not in ("raw", "clean", "full_clean"):
        raise ValueError("Unknown clip")
    return _root() / job_id / f"{which}.wav"


async def create_voice(
    job_id: str,
    db,
    *,
    which: str,
    name: str,
    language: str,
    reference_text: str,
    default_engine: Optional[str],
    profile_id: Optional[str],
    emotion: Optional[str],
) -> dict:
    """Save a prepared clip as a new voice, or as one more sample of an existing voice."""
    path = clip_path(job_id, which)
    if not path.is_file():
        raise ValueError("Bản đã xử lý không còn trên máy.")
    if profile_id is None:
        created = await profiles.create_profile(
            models.VoiceProfileCreate(name=name, language=language, default_engine=default_engine), db
        )
        profile_id = created.id
    sample = await profiles.add_profile_sample(profile_id, str(path), reference_text, db)
    if emotion and emotion != "neutral":
        tags = dict(extras.get("sample_emotions") or {})
        tags[sample.id] = emotion
        extras.update({"sample_emotions": tags})
    return {"profile_id": profile_id, "sample_id": sample.id}


# ── Model audition ─────────────────────────────────────────────────────────


def _results_path() -> Path:
    return config.get_data_dir() / "voice_models.json"


def _read_results() -> dict:
    try:
        return json.loads(_results_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def voice_models(profile_id: str) -> dict:
    return _read_results().get(profile_id, {})


def _save_result(profile_id: str, key: str, value: dict) -> None:
    data = _read_results()
    data.setdefault(profile_id, {})[key] = value
    _results_path().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def audition_options(language: str) -> list[dict]:
    """Every cloning model, with why it can or cannot read *language*."""
    from ..backends import get_tts_backend_for_engine, get_tts_model_configs

    rows = []
    for cfg in get_tts_model_configs():
        if cfg.engine not in profiles.CLONING_ENGINES:
            continue
        reason = None
        if language not in cfg.languages:
            reason = "language"
        else:
            try:
                if not get_tts_backend_for_engine(cfg.engine)._is_model_cached(cfg.model_size):
                    reason = "not_downloaded"
            except Exception:
                reason = "not_downloaded"
        rows.append(
            {
                "key": f"{cfg.engine}:{cfg.model_size}",
                "engine": cfg.engine,
                "model_size": cfg.model_size,
                "name": cfg.display_name,
                "unavailable": reason,
            }
        )
    return rows


async def _audition_one(job: dict, profile_id: str, language: str, text: str, option: dict, reference) -> None:
    from ..utils.audio import has_tts_runaway
    from .generation import generate_audio_sync

    folder = _root() / "auditions" / profile_id
    folder.mkdir(parents=True, exist_ok=True)
    best: Optional[dict] = None
    best_wav: Optional[bytes] = None
    for seed in _AUDITION_SEEDS:
        wav = await generate_audio_sync(
            profile_id=profile_id,
            text=text,
            language=language,
            engine=option["engine"],
            model_size=option["model_size"],
            seed=seed,
        )
        take = folder / "take.wav"
        take.write_bytes(wav)
        audio, rate = sf.read(str(take), dtype="float32")
        if has_tts_runaway(audio, rate):
            continue
        similarity = speaker.similarity(reference, speaker.embed(audio, rate)) or 0.0
        heard = await transcribe.get_whisper_model().transcribe(
            str(take), language=language, model_size=extras.get("qc_whisper_model")
        )
        score, _ = speech_qc.score_transcript(text, heard, language)
        rank = similarity if score >= _MIN_AUDITION_SCORE else similarity - 1
        if best is None or rank > best["rank"]:
            best = {"rank": rank, "similarity": round(similarity, 3), "text_score": round(score, 3), "seed": seed}
            best_wav = wav
    if best is None or best_wav is None:
        raise RuntimeError("Không tạo được bản thử.")
    file = folder / f"{option['key'].replace(':', '_')}.wav"
    file.write_bytes(best_wav)
    best.update(tested_at=time.time(), language=language, name=option["name"])
    _save_result(profile_id, option["key"], best)
    job["results"][option["key"]] = best


async def _run_audition(job_id: str, profile_id: str, language: str, keys: Optional[list[str]]) -> None:
    from ..database import ProfileSample, VoiceProfile, get_db
    from . import design_anchor

    job = _jobs[job_id]
    db = next(get_db())
    try:
        profile = db.query(VoiceProfile).filter_by(id=profile_id).first()
        samples = db.query(ProfileSample).filter_by(profile_id=profile_id).all()
        paths = [str(config.resolve_storage_path(s.audio_path)) for s in samples]
        if profile is not None and profile.voice_type == "designed":
            # A designed voice has no samples: compare every model with its fixed clip.
            try:
                clip, _ = await run_exclusive(
                    f"audition-{job_id}-clip",
                    lambda: design_anchor.ensure(profile, language, "neutral", 1.0, None),
                )
            except Exception as e:
                job.update(stage="done", current=None, error=str(e))
                return
            paths = [clip]
    finally:
        db.close()
    reference = await asyncio.to_thread(speaker.embed_files, paths)
    text = _AUDITION_TEXT.get(language, _AUDITION_TEXT["en"])
    options = [o for o in audition_options(language) if not o["unavailable"] and (keys is None or o["key"] in keys)]
    job["total"] = len(options)
    for option in options:
        job["current"] = option["name"]
        try:
            await run_exclusive(
                f"audition-{job_id}-{option['key']}",
                lambda o=option: _audition_one(job, profile_id, language, text, o, reference),
            )
        except Exception as e:
            logger.warning("Audition of %s failed: %s", option["key"], e)
            job["errors"][option["key"]] = str(e)
    job.update(stage="done", current=None)


def start_audition(profile_id: str, language: str, keys: Optional[list[str]]) -> dict:
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {"id": job_id, "stage": "running", "results": {}, "errors": {}, "total": 0, "current": None}
    create_background_task(_run_audition(job_id, profile_id, language, keys))
    return _jobs[job_id]


def audition_clip(profile_id: str, key: str) -> Path:
    return _root() / "auditions" / profile_id / f"{key.replace(':', '_')}.wav"
