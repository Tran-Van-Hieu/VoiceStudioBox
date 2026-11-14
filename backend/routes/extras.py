"""Endpoints for the vi-upgrades features.

* ``/extras/settings``          speed, dictionary, export, refine prompt, VRAM options
* ``/extras/pronunciation/test`` preview the dictionary on a sample text
* ``/vram``                     live VRAM usage + unload
* ``/history/{id}/export-file`` WAV / MP3 / FLAC with bitrate
* ``/history/{id}/export-srt``  subtitles timed to the generated audio
* ``/batch*``                   batch generation from .txt / .csv
"""

from __future__ import annotations

import asyncio
import io
import uuid
import zipfile
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import config, models
from ..database import Generation as DBGeneration, get_db
from ..services import batch as batch_service
from ..services import extras, vram
from ..utils.http import safe_content_disposition

router = APIRouter()


# ── Settings ───────────────────────────────────────────────────────────────


@router.get("/extras/settings")
async def get_extras_settings() -> dict[str, Any]:
    return extras.get_all()


@router.put("/extras/settings")
async def update_extras_settings(patch: dict[str, Any]) -> dict[str, Any]:
    try:
        return extras.update(patch)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))


class SampleEmotion(BaseModel):
    emotion: str = Field(..., pattern="^(neutral|happy|sad|angry|excited|calm|serious|fearful|whisper|storytelling)$")


@router.put("/extras/samples/{sample_id}/emotion")
async def set_sample_emotion(sample_id: str, data: SampleEmotion) -> dict[str, Any]:
    """Tag a voice sample with the emotion it was recorded in."""
    tags = dict(extras.get("sample_emotions") or {})
    tags[sample_id] = data.emotion
    return extras.update({"sample_emotions": tags})


class TextPreview(BaseModel):
    text: str = Field(..., max_length=100000)
    language: str = Field("vi", max_length=10)


@router.post("/extras/text-preview")
async def text_preview(data: TextPreview) -> dict[str, str]:
    """The text as it will be spoken: dictionary applied, numbers written out."""
    from ..utils.text_rules import build_text_transform

    transform = build_text_transform(data.language)
    return {"result": transform(data.text) if transform else data.text}


class PronunciationTest(BaseModel):
    text: str = Field(..., max_length=5000)


@router.post("/extras/pronunciation/test")
async def test_pronunciation(data: PronunciationTest) -> dict[str, str]:
    from ..utils.text_rules import apply_pronunciation

    return {"original": data.text, "result": apply_pronunciation(data.text)}


# ── VRAM ───────────────────────────────────────────────────────────────────


@router.get("/vram")
async def get_vram_status() -> dict[str, Any]:
    from ..services import jobs

    return {**await asyncio.to_thread(vram.status), "job": jobs.status()}


@router.post("/jobs/stop")
async def stop_jobs(db: Session = Depends(get_db)) -> dict[str, int]:
    """Stop what is running and everything waiting behind it."""
    from ..services import jobs

    return await jobs.stop_all(db)


@router.post("/jobs/clear-queue")
async def clear_job_queue(db: Session = Depends(get_db)) -> dict[str, int]:
    """Keep the running job; drop what is waiting."""
    from ..services import jobs

    return {"cleared": await jobs.clear_queue(db)}


@router.post("/jobs/skip-check")
async def skip_job_check() -> dict[str, bool]:
    """Finish the running job without its remaining verification."""
    from ..utils import abort

    abort.skip_checks()
    return {"skipping": True}


@router.post("/vram/release")
async def release_memory() -> dict[str, Any]:
    return await asyncio.to_thread(vram.release_all)


@router.post("/vram/unload")
async def unload_vram(include_all: bool = False) -> dict[str, Any]:
    unloaded = await asyncio.to_thread(vram.unload_tts, None, include_all)
    return {"unloaded": unloaded}


# ── Single generation export ──────────────────────────────────────────────


def _generation_or_404(generation_id: str, db: Session) -> DBGeneration:
    gen = db.query(DBGeneration).filter_by(id=generation_id).first()
    if not gen:
        raise HTTPException(status_code=404, detail="Generation not found")
    return gen


def _export_name(gen: DBGeneration, ext: str) -> str:
    stem = batch_service.safe_name("", gen.text or "generation")
    return f"{stem}-{gen.id[:8]}.{ext}"


@router.get("/history/{generation_id}/export-file")
async def export_generation_file(
    generation_id: str,
    format_: Optional[str] = Query(None, alias="format"),
    bitrate: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Download a generation as WAV / MP3 / FLAC (defaults from extras settings)."""
    from ..utils.audio_post import MIME, encode_file_bytes

    settings = extras.get_all()
    fmt = (format_ or settings["export_format"]).lower()
    if fmt not in MIME:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {fmt}")
    br = int(bitrate or settings["mp3_bitrate"])

    gen = _generation_or_404(generation_id, db)
    path = config.resolve_storage_path(gen.audio_path) if gen.audio_path else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    try:
        data = await asyncio.to_thread(encode_file_bytes, path, fmt, br)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Encoding failed: {e}")
    return Response(
        content=data,
        media_type=MIME[fmt],
        headers={"Content-Disposition": safe_content_disposition("attachment", _export_name(gen, fmt))},
    )


@router.get("/history/{generation_id}/quality")
async def get_generation_quality(generation_id: str, db: Session = Depends(get_db)):
    """The speech-QC report for a generation (null when it was not checked)."""
    from ..utils.audio_post import load_final, load_quality

    _generation_or_404(generation_id, db)
    return {"qc": load_quality(generation_id), "final": load_final(generation_id)}


@router.get("/history/{generation_id}/export-srt")
async def export_generation_srt(generation_id: str, db: Session = Depends(get_db)):
    """Subtitles (.srt) timed to the generation's audio."""
    from ..utils.audio_post import build_cues, load_segments, to_srt

    gen = _generation_or_404(generation_id, db)
    if (gen.status or "completed") != "completed":
        raise HTTPException(status_code=400, detail="Generation is not completed yet")
    segs = load_segments(gen.id, gen.text or "", float(gen.duration or 0))
    srt = to_srt(build_cues(segs))
    return Response(
        content=srt.encode("utf-8-sig"),
        media_type="application/x-subrip; charset=utf-8",
        headers={"Content-Disposition": safe_content_disposition("attachment", _export_name(gen, "srt"))},
    )


# ── Batch ──────────────────────────────────────────────────────────────────


@router.post("/batch/parse")
async def parse_batch_file(
    file: UploadFile = File(...),
    split: str = Form("paragraph"),
) -> dict[str, Any]:
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 20 MB)")
    items = batch_service.parse_text_file(file.filename or "", raw, split)
    return {"items": items, "count": len(items)}


class BatchItemIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=batch_service.MAX_ITEM_CHARS)
    filename: str = Field(default="", max_length=200)


class BatchCreate(BaseModel):
    name: str = Field(default="", max_length=200)
    profile_id: str
    language: str = "en"
    engine: Optional[str] = None
    model_size: Optional[str] = None
    instruct: Optional[str] = Field(None, max_length=500)
    speed: Optional[float] = Field(None, ge=0.5, le=2.0)
    emotion: Optional[str] = Field(None, pattern="^(neutral|happy|sad|angry|excited|calm|serious|fearful|whisper|storytelling)$")
    emotion_intensity: float = Field(0.6, ge=0.0, le=1.0)
    seed: Optional[int] = Field(None, ge=0)
    denoise: Optional[float] = Field(None, ge=0.0, le=1.0)
    sentence_pause_ms: int = Field(0, ge=0, le=3000)
    # None = the voice's own effects, [] = none.
    effects_chain: Optional[List[dict]] = None
    items: List[BatchItemIn] = Field(..., min_length=1, max_length=batch_service.MAX_ITEMS)


def _batch_request(settings: dict, text: str, db: Session) -> models.GenerationRequest:
    """One item's request, from the settings snapshot the batch was started with."""
    from ..services import settings as settings_service

    gen_settings = settings_service.get_generation_settings(db)
    return models.GenerationRequest(
        profile_id=settings["profile_id"],
        text=text,
        language=settings["language"],
        engine=settings.get("engine"),
        model_size=settings.get("model_size") or "1.7B",
        instruct=settings.get("instruct"),
        seed=settings.get("seed"),
        speed=settings.get("speed"),
        emotion=settings.get("emotion"),
        emotion_intensity=settings.get("emotion_intensity", 0.6),
        denoise=settings.get("denoise"),
        sentence_pause_ms=settings.get("sentence_pause_ms", 0),
        effects_chain=settings.get("effects_chain"),
        max_chunk_chars=gen_settings.max_chunk_chars,
        crossfade_ms=gen_settings.crossfade_ms,
        normalize=gen_settings.normalize_audio,
    )


@router.post("/batch")
async def create_batch(data: BatchCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    from .generations import generate_speech

    settings_snapshot = data.model_dump(exclude={"items"})
    manifest_items: list[dict] = []
    group = f"batch-{uuid.uuid4().hex}"

    for i, item in enumerate(data.items):
        entry: dict[str, Any] = {"index": i, "text": item.text, "filename": item.filename}
        try:
            gen = await generate_speech(_batch_request(settings_snapshot, item.text, db), db, group)
            entry["generation_id"] = gen.id
        except HTTPException as e:
            if i == 0:
                raise  # bad profile / engine: fail the whole batch up front
            entry["error"] = str(e.detail)
        except Exception as e:
            if i == 0:
                raise HTTPException(status_code=400, detail=str(e))
            entry["error"] = str(e)
        manifest_items.append(entry)

    b = batch_service.new_batch(data.name, settings_snapshot, manifest_items)
    batch_service.save(b)
    return batch_service.manifest_summary(b, batch_service.collect(b, db))


@router.get("/batch")
async def list_batches(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    out = []
    for b in batch_service.list_batches():
        s = batch_service.manifest_summary(b, batch_service.collect(b, db))
        s.pop("items", None)
        out.append(s)
    return out


def _batch_or_404(batch_id: str) -> dict:
    try:
        b = batch_service.load(batch_id)
    except ValueError:
        b = None
    if b is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return b


@router.get("/batch/{batch_id}")
async def get_batch(batch_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    b = _batch_or_404(batch_id)
    return batch_service.manifest_summary(b, batch_service.collect(b, db))


class BatchRegenerate(BaseModel):
    indices: List[int] = Field(..., min_length=1)
    # Edited text per index; anything not listed keeps its text.
    texts: dict[int, str] = Field(default_factory=dict)


@router.post("/batch/{batch_id}/regenerate")
async def regenerate_batch_items(batch_id: str, data: BatchRegenerate, db: Session = Depends(get_db)) -> dict[str, Any]:
    """New take for the chosen items with the batch's own settings; earlier takes are kept."""
    from .generations import generate_speech

    b = _batch_or_404(batch_id)
    by_index = {it["index"]: it for it in b["items"]}
    unknown = [i for i in data.indices if i not in by_index]
    if unknown:
        raise HTTPException(status_code=404, detail=f"No item {unknown[0] + 1} in this batch")
    group = f"batch-{uuid.uuid4().hex}"
    for i in data.indices:
        item = by_index[i]
        text = (data.texts.get(i) or item["text"]).strip()
        try:
            gen = await generate_speech(_batch_request(b["settings"], text, db), db, group)
        except HTTPException as e:
            item["error"] = str(e.detail)
            continue
        if item.get("generation_id"):
            item.setdefault("takes", [item["generation_id"]])
        item["text"] = text
        item["generation_id"] = gen.id
        item["takes"] = [*item.get("takes", []), gen.id]
        item.pop("error", None)
    batch_service.save(b)
    return batch_service.manifest_summary(b, batch_service.collect(b, db))


class BatchTake(BaseModel):
    index: int
    generation_id: str


@router.put("/batch/{batch_id}/take")
async def choose_batch_take(batch_id: str, data: BatchTake, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Make one of an item's earlier takes the one that is played, downloaded and merged."""
    b = _batch_or_404(batch_id)
    item = next((it for it in b["items"] if it["index"] == data.index), None)
    if item is None or data.generation_id not in item.get("takes", []):
        raise HTTPException(status_code=404, detail="No such take")
    item["generation_id"] = data.generation_id
    batch_service.save(b)
    return batch_service.manifest_summary(b, batch_service.collect(b, db))


@router.delete("/batch/{batch_id}")
async def delete_batch(batch_id: str) -> dict[str, bool]:
    _batch_or_404(batch_id)
    return {"deleted": batch_service.delete(batch_id)}


@router.get("/batch/{batch_id}/download")
async def download_batch(
    batch_id: str,
    mode: str = "files",  # files | merged
    format_: Optional[str] = Query(None, alias="format"),
    bitrate: Optional[int] = None,
    srt: bool = True,
    gap_ms: int = Query(500, ge=0, le=10000),
    db: Session = Depends(get_db),
):
    """Download a batch as a ZIP of individual files, or one merged file."""
    from ..utils.audio_post import MIME, build_cues, encode_audio_bytes, to_srt

    settings = extras.get_all()
    fmt = (format_ or settings["export_format"]).lower()
    if fmt not in MIME:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {fmt}")
    br = int(bitrate or settings["mp3_bitrate"])

    b = _batch_or_404(batch_id)
    items = batch_service.collect(b, db)
    if not any(it["status"] == "completed" for it in items):
        raise HTTPException(status_code=409, detail="No completed items yet")
    stem = batch_service.safe_name(b["name"], "batch")

    if mode == "merged":
        try:
            audio, sr, segments = await asyncio.to_thread(batch_service.build_merged, items, gap_ms)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        data = await asyncio.to_thread(encode_audio_bytes, audio, sr, fmt, br)
        if not srt:
            return Response(
                content=data,
                media_type=MIME[fmt],
                headers={"Content-Disposition": safe_content_disposition("attachment", f"{stem}.{fmt}")},
            )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{stem}.{fmt}", data)
            zf.writestr(f"{stem}.srt", to_srt(build_cues(segments)).encode("utf-8-sig"))
        payload = buf.getvalue()
    else:
        payload = await asyncio.to_thread(batch_service.build_zip, items, fmt, br, srt)

    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": safe_content_disposition("attachment", f"{stem}.zip")},
    )
