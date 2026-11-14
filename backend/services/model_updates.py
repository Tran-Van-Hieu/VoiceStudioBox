"""Manual model updates with a kept original and automatic rollback.

Every downloaded model is pinned to the commit it had when first seen. An
update downloads the new commit next to the old one, tries it, and only stays
on it when the trial passes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .. import config
from ..utils import hf_pin

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_jobs: dict[str, dict] = {}

# Trial sentences; the language must be one the model reads.
_TRIAL_TEXT = {
    "vi": "Xin chào, đây là câu đọc thử để kiểm tra phiên bản mới của mô hình.",
    "en": "Hello, this is a short test sentence for the new model version.",
    "zh": "你好，这是用来测试新模型版本的一句话。",
}
_TRIAL_MIN_SCORE = 0.6
_DISK_MARGIN = 1.2


def _hub_dir() -> Path:
    from huggingface_hub import constants

    return Path(constants.HF_HUB_CACHE)


def _repo_dir(repo: str) -> Path:
    return _hub_dir() / ("models--" + repo.replace("/", "--"))


def _refs_main(repo: str) -> Optional[str]:
    try:
        return (_repo_dir(repo) / "refs" / "main").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _file_count(snapshot: Path) -> int:
    return sum(1 for p in snapshot.rglob("*") if p.is_file() or p.is_symlink())


def _complete_snapshots(repo: str) -> list[tuple[int, str]]:
    """Snapshots holding most of the repo; a stray single-file fetch also leaves one."""
    folder = _repo_dir(repo) / "snapshots"
    if not folder.is_dir():
        return []
    rows = [(_file_count(p), p.name) for p in folder.iterdir() if p.is_dir()]
    largest = max((n for n, _ in rows), default=0)
    return [(n, c) for n, c in rows if n * 2 > largest]


def _best_local_commit(repo: str) -> Optional[str]:
    """The commit to pin: the current one unless it is only a partial download."""
    current = _refs_main(repo)
    complete = _complete_snapshots(repo)
    if not complete:
        return current
    if current in {c for _, c in complete}:
        return current
    return max(complete)[1]


def _state_path() -> Path:
    return config.get_data_dir() / "model_versions.json"


def _load() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    tmp = _state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_state_path())


def _configs_by_repo() -> dict[str, list]:
    from ..backends import get_all_model_configs

    out: dict[str, list] = {}
    for cfg in get_all_model_configs():
        out.setdefault(cfg.hf_repo_id, []).append(cfg)
    return out


def sync_pins() -> dict:
    """Record newly downloaded models and apply every pin."""
    with _lock:
        state = _load()
        changed = False
        for repo in _configs_by_repo():
            commit = _best_local_commit(repo)
            if commit is None:
                continue
            if commit != _refs_main(repo) and repo not in state:
                (_repo_dir(repo) / "refs" / "main").write_text(commit, encoding="utf-8")
            if repo not in state:
                state[repo] = {
                    "original": commit,
                    "active": commit,
                    "versions": {commit: {"status": "original", "installed_at": time.time()}},
                }
                changed = True
            hf_pin.set_pin(repo, state[repo]["active"])
        if changed:
            _save(state)
        return state


def _local_versions(repo: str, entry: dict) -> list[dict]:
    present = {c for _, c in _complete_snapshots(repo)}
    known = entry.get("versions", {})
    rows = []
    for commit in sorted(present | set(known)):
        meta = known.get(commit, {})
        rows.append(
            {
                "commit": commit,
                "present": commit in present,
                "active": commit == entry["active"],
                "original": commit == entry["original"],
                "status": meta.get("status", "unknown"),
                "note": meta.get("note"),
                "installed_at": meta.get("installed_at"),
            }
        )
    return rows


def list_models() -> list[dict]:
    state = sync_pins()
    by_repo = _configs_by_repo()
    out = []
    for repo, entry in state.items():
        cfgs = by_repo.get(repo, [])
        if not cfgs:  # a model the app no longer ships; its pin stays for a later reinstall
            continue
        out.append(
            {
                "repo": repo,
                "names": sorted({c.display_name for c in cfgs}),
                "active": entry["active"],
                "original": entry["original"],
                "versions": _local_versions(repo, entry),
                "update": entry.get("update"),
                "checked_at": entry.get("checked_at"),
                "job": _jobs.get(repo),
            }
        )
    return sorted(out, key=lambda r: r["repo"].lower())


def _check_repo(repo: str, active: str) -> dict:
    from huggingface_hub import HfApi

    api = HfApi()
    latest = api.model_info(repo, files_metadata=True)
    if latest.sha == active:
        return {"available": False, "latest": latest.sha}
    current = api.model_info(repo, revision=active, files_metadata=True)
    old = {f.rfilename: f.blob_id for f in current.siblings or []}
    changed = [f for f in latest.siblings or [] if old.get(f.rfilename) != f.blob_id]
    commits = api.list_repo_commits(repo, revision=latest.sha)
    head = commits[0] if commits else None
    license_of = lambda info: getattr(info.card_data, "license", None) if info.card_data else None  # noqa: E731
    return {
        "available": True,
        "latest": latest.sha,
        "date": latest.last_modified.isoformat() if latest.last_modified else None,
        "title": head.title if head else None,
        "changed_files": [f.rfilename for f in changed][:40],
        "download_bytes": sum(f.size or 0 for f in changed),
        "license": license_of(latest),
        "license_before": license_of(current),
    }


async def check_updates() -> list[dict]:
    """Ask Hugging Face for newer commits. Only the repo names are sent."""
    state = sync_pins()

    def work() -> None:
        now = time.time()
        for repo, entry in state.items():
            try:
                result = _check_repo(repo, entry["active"])
            except Exception as e:
                logger.warning("Update check failed for %s: %s", repo, e)
                result = {"available": False, "error": str(e)}
            with _lock:
                fresh = _load()
                if repo in fresh:
                    fresh[repo]["update"] = result
                    fresh[repo]["checked_at"] = now
                    _save(fresh)

    await asyncio.to_thread(work)
    return list_models()


def _assert_idle() -> None:
    from ..utils.tasks import get_task_manager

    if get_task_manager().get_active_generations():
        raise RuntimeError("Hãy chờ các bản đang tạo hoàn tất rồi đổi phiên bản model.")


def _activate(repo: str, commit: str) -> None:
    from ..backends import unload_model_by_config
    from ..utils.cache import clear_voice_prompt_cache

    for cfg in _configs_by_repo().get(repo, []):
        try:
            unload_model_by_config(cfg)
        except Exception as e:
            logger.warning("Unload failed for %s: %s", cfg.model_name, e)
    with _lock:
        state = _load()
        if not (_repo_dir(repo) / "snapshots" / commit).is_dir():
            raise ValueError("Phiên bản này không còn trên máy.")
        (_repo_dir(repo) / "refs" / "main").write_text(commit, encoding="utf-8")
        state[repo]["active"] = commit
        _save(state)
        hf_pin.set_pin(repo, commit)
    # Cached prompts were encoded by the other version of the weights.
    clear_voice_prompt_cache()


async def activate(repo: str, commit: str) -> list[dict]:
    from .task_queue import run_exclusive

    _assert_idle()

    # Through the GPU queue, so a running dub or clone never loses its model mid-step.
    async def work() -> None:
        await asyncio.to_thread(_activate, repo, commit)

    await run_exclusive(f"model-update-{repo}-activate", work)
    return list_models()


def delete_version(repo: str, commit: str) -> list[dict]:
    from huggingface_hub import scan_cache_dir

    with _lock:
        state = _load()
        entry = state.get(repo)
        if not entry:
            raise ValueError("Không tìm thấy model.")
        if commit in (entry["active"], entry["original"]):
            raise ValueError("Không thể xóa bản gốc hoặc bản đang dùng.")
        scan_cache_dir(_hub_dir()).delete_revisions(commit).execute()
        entry["versions"].pop(commit, None)
        _save(state)
    return list_models()


def _mark(repo: str, commit: str, status: str, note: Optional[str] = None) -> None:
    with _lock:
        state = _load()
        state[repo].setdefault("versions", {})[commit] = {
            "status": status,
            "note": note,
            "installed_at": time.time(),
        }
        _save(state)


async def _trial(repo: str) -> str:
    """Load the active version and use it once. Raises when it does not work."""
    import soundfile as sf

    from ..backends import (
        LANGUAGE_CODE_TO_NAME,
        get_llm_backend_for_engine,
        get_stt_backend,
        load_engine_model,
        unload_model_by_config,
    )
    from ..database import ProfileSample, VoiceProfile, get_db
    from ..utils.audio import has_tts_runaway
    from . import extras, profiles, transcribe
    from .generation import generate_audio_sync
    from .speech_qc import score_transcript

    cfg = _configs_by_repo()[repo][0]
    db = next(get_db())
    try:
        if cfg.engine == "qwen_llm":
            reply = await get_llm_backend_for_engine(cfg.engine).generate(
                "Reply with the single word OK.", max_tokens=8, temperature=0, model_size=cfg.model_size
            )
            if not reply.strip():
                raise RuntimeError("Model không trả lời.")
            return "Đã nạp và chạy thử."
        sample = db.query(ProfileSample).first()
        if cfg.engine == "whisper":
            if sample is None:
                await get_stt_backend().load_model_async(cfg.model_size)
                return "Đã nạp. Chưa có giọng mẫu nên chưa nghe thử."
            heard = await transcribe.get_whisper_model().transcribe(
                str(config.resolve_storage_path(sample.audio_path)), None, cfg.model_size
            )
            if not heard.strip():
                raise RuntimeError("Model không nghe được gì.")
            return "Đã nạp và nghe thử."
        await load_engine_model(cfg.engine, cfg.model_size)
        candidates = db.query(VoiceProfile).all()
        chosen = None
        for p in candidates:
            try:
                profiles.validate_profile_engine(p, cfg.engine)
            except ValueError:
                continue
            chosen = p
            break
        if chosen is None:
            return "Đã nạp. Chưa có giọng phù hợp nên chưa đọc thử."
        language = next((l for l in (chosen.language, "en", "zh") if l in cfg.languages and l in _TRIAL_TEXT), None)
        if language is None:
            return "Đã nạp. Chưa có câu thử cho ngôn ngữ của model."
        text = _TRIAL_TEXT[language]
        wav = await generate_audio_sync(
            profile_id=chosen.id, text=text, language=language, engine=cfg.engine, model_size=cfg.model_size, seed=7
        )
    finally:
        db.close()

    fd, name = tempfile.mkstemp(suffix=".wav")
    import os

    os.close(fd)
    try:
        Path(name).write_bytes(wav)
        audio, sr = sf.read(name, dtype="float32")
        if len(audio) < sr * 0.5 or has_tts_runaway(audio, sr):
            raise RuntimeError("Audio thử bị lỗi hoặc quá ngắn.")
        whisper_model = extras.get("qc_whisper_model")
        if language not in LANGUAGE_CODE_TO_NAME or not get_stt_backend()._is_model_cached(whisper_model):
            return "Đã đọc thử, chưa chấm được vì thiếu Whisper."
        heard = await transcribe.get_whisper_model().transcribe(name, language, whisper_model)
        score, _ = score_transcript(text, heard, language)
        if score < _TRIAL_MIN_SCORE:
            raise RuntimeError(f"Đọc thử sai nhiều (điểm {score:.2f}).")
        return f"Đã đọc thử, điểm đúng chữ {score:.2f}."
    finally:
        Path(name).unlink(missing_ok=True)
        for c in _configs_by_repo().get(repo, []):
            try:
                unload_model_by_config(c)
            except Exception:
                pass


async def _install(repo: str, commit: str, previous: str) -> None:
    from huggingface_hub import snapshot_download

    from .task_queue import run_exclusive

    job = _jobs[repo]

    async def switch_and_try() -> str:
        # Runs alone on the GPU: nothing else can use or unload the model meanwhile.
        await asyncio.to_thread(_activate, repo, commit)
        try:
            return await _trial(repo)
        except Exception:
            await asyncio.to_thread(_activate, repo, previous)
            raise

    try:
        job.update(stage="downloading", message="Đang tải bản mới, bản hiện tại không bị ảnh hưởng.")
        await asyncio.to_thread(snapshot_download, repo, revision=commit)
        job.update(stage="testing", message="Đang chạy thử bản mới.")
        try:
            note = await run_exclusive(f"model-update-{repo}-trial", switch_and_try)
        except Exception as e:
            logger.warning("Trial of %s@%s failed: %s", repo, commit, e)
            _mark(repo, commit, "failed", str(e))
            job.update(stage="rolled_back", message=f"Bản mới không đạt, đã quay lại bản cũ: {e}")
            return
        _mark(repo, commit, "verified", note)
        with _lock:
            state = _load()
            state[repo].pop("update", None)
            _save(state)
        job.update(stage="done", message=note)
    except Exception as e:
        logger.exception("Update of %s failed", repo)
        job.update(stage="failed", message=f"Không cập nhật được, vẫn giữ bản cũ: {e}")


def start_update(repo: str, commit: str) -> dict[str, Any]:
    from .task_queue import create_background_task

    state = sync_pins()
    entry = state.get(repo)
    if not entry:
        raise ValueError("Không tìm thấy model.")
    running = _jobs.get(repo)
    if running and running["stage"] in ("downloading", "testing"):
        raise ValueError("Model này đang được cập nhật.")
    need = int((entry.get("update") or {}).get("download_bytes") or 0) * _DISK_MARGIN
    if shutil.disk_usage(_hub_dir()).free < need:
        raise ValueError("Ổ đĩa không đủ chỗ cho bản mới.")
    _jobs[repo] = {"stage": "downloading", "message": "Đang bắt đầu.", "commit": commit}
    create_background_task(_install(repo, commit, entry["active"]))
    return _jobs[repo]
