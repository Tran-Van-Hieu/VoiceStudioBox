"""Endpoints for the redesigned workspaces: voice lab, quality, studio, dubbing."""

from __future__ import annotations

import asyncio
import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import VoiceProfile, get_db
from ..services import dubbing, extras, repair, studio_projects, voice_lab
from ..utils.http import safe_content_disposition

router = APIRouter()

_LANG = "^(zh|en|ja|ko|de|fr|ru|pt|es|it|he|ar|da|el|fi|hi|ms|nl|no|pl|sv|sw|tr|vi)$"
_UPLOAD_CHUNK = 4 * 1024 * 1024


async def _store_upload(file: UploadFile) -> Path:
    suffix = Path(file.filename or "").suffix.lower() or ".bin"
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    with handle:
        while chunk := await file.read(_UPLOAD_CHUNK):
            handle.write(chunk)
    return Path(handle.name)


def _bad_request(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


# ── Voice lab ──────────────────────────────────────────────────────────────


@router.post("/voicelab/prepare")
async def prepare_reference(
    file: UploadFile = File(...),
    isolate: str = Form("auto"),
    denoise: str = Form("auto"),
) -> dict[str, Any]:
    if isolate not in ("auto", "on", "off") or denoise not in ("auto", "on", "off"):
        raise HTTPException(status_code=400, detail="Invalid option")
    path = await _store_upload(file)
    return voice_lab.start_prepare(path, isolate, denoise)


class SelectClip(BaseModel):
    start: float = Field(..., ge=0)
    end: float = Field(..., gt=0)
    language: Optional[str] = Field(None, pattern=_LANG)
    reference_text: str = Field("", max_length=5000)


@router.post("/voicelab/jobs/{job_id}/select")
async def voicelab_select(job_id: str, data: SelectClip) -> dict[str, Any]:
    try:
        return voice_lab.select_clip(job_id, data.start, data.end, data.language, data.reference_text)
    except ValueError as e:
        raise _bad_request(e)


@router.get("/voicelab/jobs/{job_id}")
async def voicelab_job(job_id: str) -> dict[str, Any]:
    job = voice_lab.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/voicelab/jobs/{job_id}/audio/{which}")
async def voicelab_clip(job_id: str, which: str):
    try:
        path = voice_lab.clip_path(job_id, which)
    except ValueError as e:
        raise _bad_request(e)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(path, media_type="audio/wav")


class CreateVoice(BaseModel):
    which: str = Field("clean", pattern="^(raw|clean)$")
    name: str = Field("", max_length=100)
    language: str = Field("vi", pattern=_LANG)
    reference_text: str = Field(..., min_length=1, max_length=5000)
    default_engine: Optional[str] = Field(None, max_length=50)
    profile_id: Optional[str] = None
    emotion: Optional[str] = None


@router.post("/voicelab/jobs/{job_id}/create-voice")
async def voicelab_create_voice(job_id: str, data: CreateVoice, db: Session = Depends(get_db)) -> dict[str, Any]:
    if not data.profile_id and not data.name.strip():
        raise HTTPException(status_code=400, detail="Hãy đặt tên cho giọng.")
    try:
        return await voice_lab.create_voice(job_id, db, **data.model_dump())
    except ValueError as e:
        raise _bad_request(e)


@router.get("/voicelab/models")
async def audition_options(language: str = Query(..., pattern=_LANG)) -> list[dict[str, Any]]:
    return await asyncio.to_thread(voice_lab.audition_options, language)


class Audition(BaseModel):
    profile_id: str
    language: str = Field(..., pattern=_LANG)
    keys: Optional[List[str]] = None


@router.post("/voicelab/audition")
async def start_audition(data: Audition) -> dict[str, Any]:
    return voice_lab.start_audition(data.profile_id, data.language, data.keys)


@router.get("/voicelab/profiles/{profile_id}/models")
async def voice_models(profile_id: str) -> dict[str, Any]:
    return voice_lab.voice_models(profile_id)


@router.get("/voicelab/profiles/{profile_id}/models/{key}/audio")
async def voice_model_audio(profile_id: str, key: str):
    path = voice_lab.audition_clip(profile_id, key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(path, media_type="audio/wav")


# ── Quality ────────────────────────────────────────────────────────────────


@router.post("/quality/{generation_id}/repair/{index}")
async def repair_segment(generation_id: str, index: int) -> dict[str, Any]:
    try:
        return await repair.repair_segment(generation_id, index)
    except ValueError as e:
        raise _bad_request(e)


# ── History search ─────────────────────────────────────────────────────────


@router.get("/library/history")
async def history_search(
    q: str = "",
    profile_id: Optional[str] = None,
    engine: Optional[str] = None,
    language: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    favorite: bool = False,
    pinned: bool = False,
    level: Optional[str] = None,
    tag: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from ..services import history_search as search

    try:
        return await asyncio.to_thread(
            search.search, db, q, profile_id, engine, language, date_from, date_to, favorite, pinned, level, tag, status, limit, offset
        )
    except ValueError as e:  # malformed date
        raise _bad_request(e)


class HistoryMeta(BaseModel):
    pinned: Optional[bool] = None
    tags: Optional[List[str]] = None


@router.put("/library/history/{generation_id}/meta")
async def history_meta(generation_id: str, data: HistoryMeta) -> dict[str, Any]:
    from ..services import history_search as search

    return search.update_meta(generation_id, data.pinned, data.tags)


class HistoryExport(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=500)
    format: str = Field("wav", pattern="^(wav|mp3|flac)$")


@router.post("/library/history/export")
async def history_export(data: HistoryExport, db: Session = Depends(get_db)):
    from ..services import history_search as search

    payload = await asyncio.to_thread(search.export_zip, db, data.ids, data.format, extras.get("mp3_bitrate"))
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": safe_content_disposition("attachment", "lich-su.zip")},
    )


# ── Advanced text-to-speech ────────────────────────────────────────────────


class StudioCreate(BaseModel):
    name: str = Field("", max_length=200)
    language: str = Field("vi", pattern=_LANG)
    defaults: dict[str, Any] = Field(default_factory=dict)


def _project(project_id: str) -> dict:
    try:
        project = studio_projects.load(project_id)
    except ValueError:
        project = None
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/studio")
async def studio_list() -> list[dict[str, Any]]:
    return studio_projects.list_all()


@router.post("/studio")
async def studio_create(data: StudioCreate) -> dict[str, Any]:
    return studio_projects.create(data.name, data.language, data.defaults)


@router.get("/studio/{project_id}")
async def studio_get(project_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return studio_projects.view(_project(project_id), db)


class StudioUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    language: Optional[str] = Field(None, pattern=_LANG)
    defaults: Optional[dict[str, Any]] = None
    segments: Optional[List[dict[str, Any]]] = None
    music_speech_pct: Optional[float] = Field(None, ge=0, le=100)
    music_pause_pct: Optional[float] = Field(None, ge=0, le=100)


_SEGMENT_FIELDS = {
    "text", "speaker", "profile_id", "engine", "model_size", "language", "emotion",
    "emotion_intensity", "speed", "seed", "pause_after_ms", "filename", "active",
}


@router.put("/studio/{project_id}")
async def studio_update(project_id: str, data: StudioUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = _project(project_id)
    for key in ("name", "language", "defaults"):
        value = getattr(data, key)
        if value is not None:
            project[key] = value
    if data.segments is not None:
        existing = {s["id"]: s for s in project["segments"]}
        merged = []
        for row in data.segments:
            base = existing.get(row.get("id")) or studio_projects.new_segment()
            merged.append({**base, **{k: v for k, v in row.items() if k in _SEGMENT_FIELDS}})
        project["segments"] = studio_projects.expand_pause_marks(merged)
    if project.get("music"):
        if data.music_speech_pct is not None:
            project["music"]["speech_pct"] = data.music_speech_pct
        if data.music_pause_pct is not None:
            project["music"]["pause_pct"] = data.music_pause_pct
    studio_projects.save(project)
    return studio_projects.view(project, db)


@router.delete("/studio/{project_id}")
async def studio_delete(project_id: str) -> dict[str, bool]:
    _project(project_id)
    studio_projects.delete(project_id)
    return {"deleted": True}


@router.post("/studio/{project_id}/import")
async def studio_import(
    project_id: str,
    file: Optional[UploadFile] = File(None),
    script: str = Form(""),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = _project(project_id)
    voices = db.query(VoiceProfile).all()
    if file is not None:
        raw = await file.read()
        if (file.filename or "").lower().endswith((".csv", ".tsv")):
            added = studio_projects.parse_csv(raw, voices)
        else:
            added = studio_projects.parse_script(raw.decode("utf-8-sig", errors="replace"), voices)
    else:
        added = studio_projects.parse_script(script, voices)
    project["segments"].extend(added)
    studio_projects.save(project)
    return studio_projects.view(project, db)


class ScriptSplit(BaseModel):
    text: str = Field(..., min_length=1, max_length=2_000_000)
    max_chars: int = Field(..., ge=20, le=20000)


@router.post("/studio/{project_id}/split")
async def studio_split(project_id: str, data: ScriptSplit, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Replace the project's segments with the script cut at whole sentences."""
    project = _project(project_id)
    project["segments"] = studio_projects.split_into_segments(data.text, data.max_chars)
    studio_projects.save(project)
    return studio_projects.view(project, db)


@router.post("/studio/{project_id}/stop")
async def studio_stop(project_id: str, db: Session = Depends(get_db)) -> dict[str, int]:
    return {"stopped": await studio_projects.stop(_project(project_id), db)}


class StudioRender(BaseModel):
    segment_ids: Optional[List[str]] = None


@router.post("/studio/{project_id}/render")
async def studio_render(project_id: str, data: StudioRender, db: Session = Depends(get_db)) -> dict[str, Any]:
    return await studio_projects.render(_project(project_id), data.segment_ids, db)


@router.post("/studio/{project_id}/music")
async def studio_music(
    project_id: str,
    file: UploadFile = File(...),
    speech_pct: float = Form(25.0, ge=0, le=100),
    pause_pct: float = Form(100.0, ge=0, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = _project(project_id)
    upload = await _store_upload(file)
    folder = Path(studio_projects._path(project_id)).parent
    target = folder / "music.wav"
    try:
        await asyncio.to_thread(voice_lab.decode_media, upload, target, 44100, False)
    except RuntimeError as e:
        raise _bad_request(e)
    finally:
        upload.unlink(missing_ok=True)
    project["music"] = {"path": str(target), "speech_pct": speech_pct, "pause_pct": pause_pct, "name": file.filename}
    studio_projects.save(project)
    return studio_projects.view(project, db)


@router.delete("/studio/{project_id}/music")
async def studio_music_remove(project_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = _project(project_id)
    project["music"] = None
    studio_projects.save(project)
    return studio_projects.view(project, db)


@router.get("/studio/{project_id}/export")
async def studio_export(
    project_id: str,
    mode: str = "merged",
    format_: Optional[str] = Query(None, alias="format"),
    bitrate: Optional[int] = None,
    srt: bool = True,
    db: Session = Depends(get_db),
):
    from ..services import batch as batch_service
    from ..utils.audio_post import MIME, build_cues, encode_audio_bytes, load_segments, to_srt

    project = _project(project_id)
    settings = extras.get_all()
    fmt = (format_ or settings["export_format"]).lower()
    if fmt not in MIME:
        raise HTTPException(status_code=400, detail="Unsupported format")
    br = int(bitrate or settings["mp3_bitrate"])
    stem = batch_service.safe_name(project["name"], "project")
    buffer = io.BytesIO()
    try:
        if mode == "merged":
            audio, rate, cues = await asyncio.to_thread(studio_projects.merge, project, db)
            data = await asyncio.to_thread(encode_audio_bytes, audio, rate, fmt, br)
            if not srt:
                return Response(
                    content=data,
                    media_type=MIME[fmt],
                    headers={"Content-Disposition": safe_content_disposition("attachment", f"{stem}.{fmt}")},
                )
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr(f"{stem}.{fmt}", data)
                z.writestr(f"{stem}.srt", to_srt(build_cues(cues)).encode("utf-8-sig"))
        else:
            clips = await asyncio.to_thread(studio_projects.clips_for_zip, project, db)
            if not clips:
                raise ValueError("Chưa có đoạn nào hoàn tất.")
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
                for name, audio, rate, gen in clips:
                    z.writestr(f"{name}.{fmt}", await asyncio.to_thread(encode_audio_bytes, audio, rate, fmt, br))
                    if srt:
                        cues = load_segments(gen.id, gen.text or "", float(gen.duration or 0))
                        z.writestr(f"{name}.srt", to_srt(build_cues(cues)).encode("utf-8-sig"))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": safe_content_disposition("attachment", f"{stem}.zip")},
    )


# ── Dubbing ────────────────────────────────────────────────────────────────


def _dub(project_id: str) -> dict:
    try:
        project = dubbing.load(project_id)
    except ValueError:
        project = None
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/dubbing")
async def dub_create(
    files: List[UploadFile] = File(...),
    target_language: str = Form("vi"),
    source_language: Optional[str] = Form(None),
    engine: str = Form("omnivoice"),
    model_size: str = Form("1.7B"),
    mode: str = Form("dub"),
    auto_continue: bool = Form(False),
    speaker_count: int = Form(0),
    glossary: str = Form(""),
    original_volume: float = Form(0.0),
    music_volume: float = Form(1.0),
    llm_size: str = Form("4B"),
    profile_id: str = Form(""),
    subtitle: str = Form("{}"),
    srt: Optional[UploadFile] = File(None),
    srt_role: str = Form("source"),
) -> list[dict[str, Any]]:
    if mode not in ("dub", "transcribe") or srt_role not in ("source", "target"):
        raise HTTPException(status_code=400, detail="Invalid mode")
    if mode == "dub" and not profile_id:
        raise HTTPException(status_code=400, detail="Chọn một giọng trong Thư viện để lồng tiếng.")
    cues = None
    if srt is not None and srt.filename:
        if len(files) > 1:
            raise HTTPException(status_code=400, detail="File SRT chỉ dùng được khi lồng tiếng một video.")
        try:
            cues = dubbing.parse_srt((await srt.read()).decode("utf-8-sig", errors="replace"))
        except ValueError as e:
            raise _bad_request(e)
    try:
        style = dubbing.clean_subtitle(json.loads(subtitle or "{}"))
    except (ValueError, TypeError) as e:
        raise _bad_request(e)
    created = []
    for file in files:
        upload = await _store_upload(file)
        project = dubbing.create(
            upload,
            file.filename or "video.mp4",
            {
                "target_language": target_language,
                "source_language": source_language or None,
                "engine": engine,
                "model_size": model_size,
                "mode": mode,
                "auto_continue": auto_continue,
                "speaker_count": speaker_count,
                "glossary": glossary,
                "original_volume": max(0.0, min(1.0, original_volume)),
                "music_volume": max(0.0, min(1.0, music_volume)),
                "llm_size": llm_size,
                "profile_id": profile_id or None,
                "subtitle": style,
                "srt_cues": cues,
                "srt_role": srt_role,
            },
        )
        dubbing.start(project["id"])
        created.append(project)
    return created


@router.get("/dubbing")
async def dub_list() -> list[dict[str, Any]]:
    return dubbing.list_all()


@router.get("/dubbing/{project_id}")
async def dub_get(project_id: str) -> dict[str, Any]:
    return _dub(project_id)


@router.put("/dubbing/{project_id}")
async def dub_update(project_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    _dub(project_id)
    try:
        return dubbing.update(project_id, patch)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/dubbing/{project_id}/run")
async def dub_run(project_id: str) -> dict[str, Any]:
    project = _dub(project_id)
    dubbing.start(project_id)
    return project


@router.post("/dubbing/{project_id}/stop")
async def dub_stop(project_id: str) -> dict[str, bool]:
    _dub(project_id)
    dubbing.stop(project_id)
    return {"stopping": True}


@router.post("/dubbing/{project_id}/retranslate")
async def dub_retranslate(project_id: str) -> dict[str, Any]:
    _dub(project_id)
    return dubbing.retranslate(project_id)


@router.delete("/dubbing/{project_id}")
async def dub_delete(project_id: str) -> dict[str, bool]:
    _dub(project_id)
    try:
        dubbing.delete(project_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"deleted": True}


@router.get("/dubbing/{project_id}/file/{which}")
async def dub_file(project_id: str, which: str):
    project = _dub(project_id)
    if which not in ("video", "audio", "voices", "background"):
        raise HTTPException(status_code=400, detail="Unknown output")
    path = dubbing.output_path(project_id, which)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not ready")
    return FileResponse(
        path,
        media_type="video/mp4" if which == "video" else "audio/wav",
        headers={"Content-Disposition": safe_content_disposition("attachment", f"{project['name']}-{which}{path.suffix}")},
    )


@router.get("/dubbing/{project_id}/subtitles")
async def dub_subtitles(project_id: str, which: str = "target", fmt: str = "srt"):
    project = _dub(project_id)
    if which not in ("source", "target") or fmt not in ("srt", "vtt", "txt"):
        raise HTTPException(status_code=400, detail="Invalid option")
    text = dubbing.subtitles(project, which, fmt)
    return Response(
        content=text.encode("utf-8-sig"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": safe_content_disposition("attachment", f"{project['name']}-{which}.{fmt}")},
    )
