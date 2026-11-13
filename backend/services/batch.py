"""
Batch generation: many texts in, many audio files (or one merged file) out.

A batch is a thin manifest (JSON in ``<data>/batches``) that points at
ordinary generations. Each item goes through the normal ``/generate`` path,
so it shows up in History, honours the serial GPU queue, effects, speed,
dictionary and VRAM settings exactly like a manual generation.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .. import config

logger = logging.getLogger(__name__)

MAX_ITEMS = 1000
MAX_ITEM_CHARS = 50000


# ── Storage ────────────────────────────────────────────────────────────────


def _dir() -> Path:
    d = config.get_data_dir() / "batches"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(batch_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f\-]{36}", batch_id):
        raise ValueError("Invalid batch id")
    return _dir() / f"{batch_id}.json"


def save(batch: dict) -> None:
    _path(batch["id"]).write_text(json.dumps(batch, ensure_ascii=False, indent=1), encoding="utf-8")


def load(batch_id: str) -> dict | None:
    p = _path(batch_id)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_batches(limit: int = 50) -> list[dict]:
    files = sorted(_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files[:limit]:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def delete(batch_id: str) -> bool:
    p = _path(batch_id)
    if p.is_file():
        p.unlink()
        return True
    return False


def new_batch(name: str, settings: dict, items: list[dict]) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name or datetime.now().strftime("Batch %Y-%m-%d %H:%M"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings,
        "items": items,
    }


# ── Parsing uploaded files ────────────────────────────────────────────────


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1258", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            if enc == "utf-16" and not raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue
            return text
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


_TEXT_KEYS = ("text", "noi_dung", "nội dung", "noidung", "content", "script", "câu", "cau")
_NAME_KEYS = ("filename", "file", "name", "ten", "tên", "tên file", "ten_file", "id")


def parse_text_file(filename: str, raw: bytes, split: str = "paragraph") -> list[dict]:
    """Return ``[{text, filename}]`` from a .txt or .csv upload.

    * ``.txt`` — one item per paragraph (blank-line separated) or per line.
    * ``.csv`` / ``.tsv`` — a ``text`` column (or the first column) plus an
      optional ``filename`` column. Header row is auto-detected.
    """
    content = _decode(raw).replace("\r\n", "\n").replace("\r", "\n")
    ext = Path(filename or "").suffix.lower()
    items: list[dict] = []

    if ext in (".csv", ".tsv"):
        sample = content[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel_tab if ext == ".tsv" else csv.excel
        rows = [r for r in csv.reader(io.StringIO(content), dialect) if any(c.strip() for c in r)]
        if not rows:
            return []
        header = [c.strip().lower() for c in rows[0]]
        text_idx = next((i for i, h in enumerate(header) if h in _TEXT_KEYS), None)
        name_idx = next((i for i, h in enumerate(header) if h in _NAME_KEYS), None)
        has_header = text_idx is not None or name_idx is not None
        delim = getattr(dialect, "delimiter", ",")
        whole_line = False
        if text_idx is None:
            # No recognised header. Treat it as (filename, text) only when every
            # row has exactly two cells and the first looks like an id (no
            # spaces); otherwise the delimiter is just punctuation inside the
            # sentence, so keep the whole line as the text.
            id_like = re.compile(r"^[\w\-.]{1,40}$")
            body = rows[1:] if has_header else rows
            if body and all(len(r) == 2 and id_like.match(r[0].strip()) for r in body):
                name_idx, text_idx = 0, 1
            else:
                text_idx, whole_line = 0, True
        for r in rows[1:] if has_header else rows:
            if whole_line:
                text = delim.join(r).strip()
                if text:
                    items.append({"text": text, "filename": ""})
                continue
            if text_idx >= len(r):
                continue
            text = r[text_idx].strip()
            if not text:
                continue
            name = r[name_idx].strip() if name_idx is not None and name_idx < len(r) else ""
            items.append({"text": text, "filename": name})
    else:
        if split == "line":
            parts = content.split("\n")
        else:
            parts = re.split(r"\n\s*\n", content)
        for p in parts:
            text = " ".join(line.strip() for line in p.split("\n")).strip()
            if text:
                items.append({"text": text, "filename": ""})

    for it in items:
        it["text"] = it["text"][:MAX_ITEM_CHARS]
    return items[:MAX_ITEMS]


# ── Export ─────────────────────────────────────────────────────────────────

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_name(name: str, fallback: str) -> str:
    name = _UNSAFE.sub("_", name or "").strip(" ._")
    if not name:
        words = re.sub(r"\s+", " ", fallback).strip()[:40]
        name = _UNSAFE.sub("_", words).strip(" ._") or "audio"
    return name[:80]


def _read_audio(path: Path) -> tuple[np.ndarray, int]:
    import soundfile as sf

    audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.astype(np.float32), int(sr)


def collect(batch: dict, db) -> list[dict]:
    """Join manifest items with their generation rows."""
    from ..database import Generation as DBGeneration

    ids = [it.get("generation_id") for it in batch["items"] if it.get("generation_id")]
    rows = {g.id: g for g in db.query(DBGeneration).filter(DBGeneration.id.in_(ids)).all()} if ids else {}
    out = []
    for it in batch["items"]:
        g = rows.get(it.get("generation_id"))
        out.append(
            {
                **it,
                "status": (g.status or "completed") if g else ("failed" if it.get("error") else "missing"),
                "duration": g.duration if g else None,
                "error": (g.error if g else it.get("error")),
                "audio_path": g.audio_path if g else None,
                "gen_text": g.text if g else it.get("text"),
            }
        )
    return out


def build_zip(items: list[dict], fmt: str, bitrate: int, srt: bool) -> bytes:
    from ..utils.audio_post import build_cues, encode_file_bytes, load_segments, to_srt

    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in items:
            if it["status"] != "completed" or not it.get("audio_path"):
                continue
            path = config.resolve_storage_path(it["audio_path"])
            if path is None or not path.is_file():
                continue
            base = f"{it['index'] + 1:03d}_{safe_name(it.get('filename', ''), it['gen_text'])}"
            stem, n = base, 2
            while stem in used:
                stem = f"{base}_{n}"
                n += 1
            used.add(stem)
            zf.writestr(f"{stem}.{fmt}", encode_file_bytes(path, fmt, bitrate))
            if srt:
                segs = load_segments(it["generation_id"], it["gen_text"], it.get("duration") or 0)
                zf.writestr(f"{stem}.srt", to_srt(build_cues(segs)))
    return buf.getvalue()


def build_merged(items: list[dict], gap_ms: int) -> tuple[np.ndarray, int, list[dict]]:
    """Concatenate every item in number order with silence; return audio, sr, segments.

    Refuses while any item is unfinished: skipping one would shift every later
    line and the merged file would no longer follow the script.
    """
    from ..utils.audio_post import load_segments

    items = sorted(items, key=lambda it: it["index"])
    unfinished = [it["index"] + 1 for it in items if it["status"] != "completed" or not it.get("audio_path")]
    if unfinished:
        raise ValueError(f"Chưa xong các đoạn: {_numbers(unfinished)}. Tạo lại hoặc xoá chúng trước khi gộp.")
    pieces: list[np.ndarray] = []
    segments: list[dict] = []
    sr: int | None = None
    t = 0.0
    for it in items:
        if it["status"] != "completed" or not it.get("audio_path"):
            continue
        path = config.resolve_storage_path(it["audio_path"])
        if path is None or not path.is_file():
            continue
        audio, file_sr = _read_audio(path)
        if sr is None:
            sr = file_sr
        elif file_sr != sr:
            import librosa

            audio = librosa.resample(audio, orig_sr=file_sr, target_sr=sr).astype(np.float32)
        if pieces and gap_ms > 0:
            gap = np.zeros(int(sr * gap_ms / 1000), dtype=np.float32)
            pieces.append(gap)
            t += len(gap) / sr
        dur = len(audio) / sr
        for s in load_segments(it["generation_id"], it["gen_text"], dur):
            segments.append({"start": t + s["start"], "end": t + s["end"], "text": s["text"]})
        pieces.append(audio)
        t += dur
    if not pieces or sr is None:
        raise ValueError("No completed items to merge yet")
    return np.concatenate(pieces), sr, segments


def _numbers(numbers: list[int], limit: int = 20) -> str:
    shown = ", ".join(str(n) for n in numbers[:limit])
    return shown + (f" … (+{len(numbers) - limit})" if len(numbers) > limit else "")


def manifest_summary(batch: dict, items: list[dict]) -> dict[str, Any]:
    from ..utils.audio_post import load_final

    counts: dict[str, int] = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    return {
        "id": batch["id"],
        "name": batch["name"],
        "created_at": batch["created_at"],
        "settings": batch.get("settings", {}),
        "total": len(items),
        "counts": counts,
        "items": [
            {
                "index": it["index"],
                "text": it["gen_text"],
                "filename": it.get("filename", ""),
                "generation_id": it.get("generation_id"),
                "status": it["status"],
                "duration": it.get("duration"),
                "error": it.get("error"),
                "takes": it.get("takes", []),
                "final": load_final(it["generation_id"]) if it["status"] == "completed" else None,
            }
            for it in items
        ],
    }
