"""What is running on the GPU, and the Stop / Skip check / Clear queue actions."""

from __future__ import annotations

import re
from typing import Any, Optional

from ..database import Generation
from ..utils import abort
from ..utils.tasks import get_task_manager
from . import history, task_queue

_GENERATION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-")
STOPPED_MESSAGE = "Đã dừng theo yêu cầu"


def _kind(key: str) -> str:
    if key.startswith("dub-"):
        return "dub"
    if key.startswith("voicelab-"):
        return "voicelab"
    if key.startswith("audition-"):
        return "audition"
    return "generate"


def status() -> dict[str, Any]:
    running = task_queue.running_keys()
    return {
        "running": _kind(running[0]) if running else None,
        "checking": abort.is_checking(),
        "stopping": abort.stopping(),
        "queued": len(task_queue.queued_keys()),
        "cooldown_seconds": task_queue.cooldown_remaining(),
    }


async def _fail_queued(generation_id: str, db) -> None:
    get_task_manager().complete_generation(generation_id)
    await history.update_generation_status(
        generation_id=generation_id, status="failed", db=db, error=STOPPED_MESSAGE
    )


async def cancel_generation(generation_id: str, db) -> Optional[str]:
    """Stop one generation, whether it is running or still waiting."""
    state = task_queue.cancel_generation(generation_id)
    if state == "queued":
        await _fail_queued(generation_id, db)
    return state


async def clear_queue(db) -> int:
    """Drop everything waiting; what is running keeps the GPU to itself."""
    from . import dubbing

    keys = task_queue.queued_keys()
    for key in keys:
        task_queue.cancel_generation(key)
        if _GENERATION_ID.match(key) and db.query(Generation).filter_by(id=key).first():
            await _fail_queued(key, db)
    dubbing.stop_waiting()
    return len(keys)


async def stop_all(db) -> dict[str, int]:
    """Stop the running job and everything behind it."""
    from . import dubbing

    dubbing.stop_all()
    cleared = await clear_queue(db)
    running = task_queue.running_keys()
    if running:
        abort.request_stop()
    return {"stopped": len(running), "cleared": cleared}


async def cancel_studio(project: dict, db) -> int:
    """Stop every take of one project that has not finished."""
    active = set(task_queue.running_keys()) | set(task_queue.queued_keys())
    count = 0
    for segment in project["segments"]:
        for take in segment["takes"]:
            if take in active:
                await cancel_generation(take, db)
                count += 1
    return count
