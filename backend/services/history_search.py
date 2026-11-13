"""History search: accent-insensitive text match, filters, pins and tags."""

from __future__ import annotations

import io
import json
import re
import threading
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import config
from ..database import Generation, VoiceProfile
from ..utils.audio_post import encode_file_bytes, load_final
from . import batch as batch_service

_lock = threading.Lock()


def _meta_path() -> Path:
    return config.get_data_dir() / "history_meta.json"


def _read_meta() -> dict:
    try:
        return json.loads(_meta_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def update_meta(generation_id: str, pinned: Optional[bool], tags: Optional[list[str]]) -> dict:
    with _lock:
        data = _read_meta()
        row = data.get(generation_id, {})
        if pinned is not None:
            row["pinned"] = pinned
        if tags is not None:
            row["tags"] = sorted({t.strip() for t in tags if t.strip()})
        data[generation_id] = row
        _meta_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return row


def _utc(instant: str) -> datetime:
    """An ISO instant from the UI as the naive UTC datetime the database stores."""
    value = datetime.fromisoformat(instant.replace("Z", "+00:00"))
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def fold(text: str) -> str:
    """Lower-case and strip accents, so "ha noi" finds "Hà Nội"."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).replace("đ", "d")


def search(
    db, q, profile_id, engine, language, date_from, date_to, favorite, pinned, level, tag, status, limit, offset
) -> dict:
    query = db.query(Generation, VoiceProfile.name).join(VoiceProfile, Generation.profile_id == VoiceProfile.id)
    if profile_id:
        query = query.filter(Generation.profile_id == profile_id)
    if engine:
        query = query.filter(Generation.engine == engine)
    if language:
        query = query.filter(Generation.language == language)
    if date_from:
        query = query.filter(Generation.created_at >= _utc(date_from))
    if date_to:
        query = query.filter(Generation.created_at <= _utc(date_to))
    if favorite:
        query = query.filter(Generation.is_favorited.is_(True))
    if status:
        query = query.filter(Generation.status == status)
    rows = query.order_by(Generation.created_at.desc()).all()

    meta = _read_meta()
    words = fold(q).split()
    matched = []
    for gen, name in rows:
        info = meta.get(gen.id, {})
        if pinned and not info.get("pinned"):
            continue
        if tag and tag not in info.get("tags", []):
            continue
        if words:
            haystack = fold(f"{gen.text or ''} {name}")
            if not all(re.search(re.escape(w), haystack) for w in words):
                continue
        matched.append((gen, name, info))
    if level:
        matched = [m for m in matched if (load_final(m[0].id) or {}).get("level") == level]
    matched.sort(key=lambda m: not m[2].get("pinned"))

    page = matched[offset : offset + limit]
    items = []
    for gen, name, info in page:
        final = load_final(gen.id)
        items.append(
            {
                "id": gen.id,
                "text": gen.text,
                "profile_id": gen.profile_id,
                "profile_name": name,
                "engine": gen.engine,
                "model_size": gen.model_size,
                "language": gen.language,
                "duration": gen.duration,
                "status": gen.status or "completed",
                "error": gen.error,
                "created_at": gen.created_at.isoformat() if gen.created_at else None,
                "is_favorited": bool(gen.is_favorited),
                "seed": gen.seed,
                "pinned": bool(info.get("pinned")),
                "tags": info.get("tags", []),
                "level": final["level"] if final else None,
                "final": final,
            }
        )
    all_tags = sorted({t for row in meta.values() for t in row.get("tags", [])})
    return {"items": items, "total": len(matched), "tags": all_tags}


def export_zip(db, ids: list[str], fmt: str, bitrate: int) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for gen in db.query(Generation).filter(Generation.id.in_(ids)).all():
            path = config.resolve_storage_path(gen.audio_path) if gen.audio_path else None
            if path is None or not path.is_file():
                continue
            stem = batch_service.safe_name("", gen.text or "generation")
            archive.writestr(f"{stem}-{gen.id[:8]}.{fmt}", encode_file_bytes(path, fmt, bitrate))
    return buffer.getvalue()
