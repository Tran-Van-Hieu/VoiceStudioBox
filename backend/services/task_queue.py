"""
Serial generation queue — ensures only one TTS inference runs at a time
to avoid GPU contention.
"""

import asyncio
import time
import traceback
from dataclasses import dataclass
from typing import Coroutine, Literal

from ..utils import abort

# Keep references to fire-and-forget background tasks to prevent GC
_background_tasks: set = set()


@dataclass
class GenerationJob:
    """Queued generation work plus the generation ID it belongs to."""

    generation_id: str
    coro: Coroutine
    group: str | None = None  # batch / script the job belongs to


_last_group: str | None = None
_last_end = 0.0
_cooldown_until = 0.0


def cooldown_remaining() -> int:
    """Seconds left of the rest between two scripts, 0 when not resting."""
    return max(0, int(round(_cooldown_until - time.time())))


async def _rest_between_groups(job: GenerationJob) -> None:
    """Unload every model and wait before a different script starts.

    Lets the GPU cool and returns RAM/VRAM between long scripts. Cancelling the
    waiting job (Stop / Clear queue) ends the rest at once.
    """
    global _cooldown_until
    from . import extras, vram

    seconds = int(extras.get("queue_cooldown_seconds") or 0)
    if seconds <= 0 or not _last_group or not job.group or job.group == _last_group:
        return
    # Time already spent idle since the last script counts as rest.
    if time.time() - _last_end >= seconds:
        return
    await asyncio.to_thread(vram.release_now)
    _cooldown_until = _last_end + seconds
    try:
        while cooldown_remaining() > 0 and job.generation_id not in _cancelled_generation_ids:
            await asyncio.sleep(1)
    finally:
        _cooldown_until = 0.0


# Generation queue — serializes TTS inference to avoid GPU contention
_generation_queue: asyncio.Queue = None  # type: ignore  # initialized at startup
_generation_worker_task: asyncio.Task | None = None
_queued_generation_ids: set[str] = set()
_running_generation_tasks: dict[str, asyncio.Task] = {}
_cancelled_generation_ids: set[str] = set()
_exclusive_futures: dict[str, asyncio.Future] = {}


def create_background_task(coro) -> asyncio.Task:
    """Create a background task and prevent it from being garbage collected."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def _generation_worker():
    """Worker that processes generation tasks one at a time."""
    while True:
        job = await _generation_queue.get()
        global _last_group, _last_end
        try:
            await _rest_between_groups(job)
            _last_group = job.group
            if job.generation_id in _cancelled_generation_ids:
                _cancelled_generation_ids.discard(job.generation_id)
                job.coro.close()
                future = _exclusive_futures.pop(job.generation_id, None)
                if future is not None and not future.done():
                    future.cancel()
                continue

            abort.reset()
            task = asyncio.create_task(job.coro)
            _running_generation_tasks[job.generation_id] = task
            _queued_generation_ids.discard(job.generation_id)
            try:
                await task
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise
        except Exception:
            traceback.print_exc()
            await _force_fail_if_active(
                job.generation_id,
                "Worker exited without writing terminal status",
            )
        finally:
            _running_generation_tasks.pop(job.generation_id, None)
            _queued_generation_ids.discard(job.generation_id)
            _last_end = time.time()
            # A Stop meant for this job must not abort generations that do not
            # go through the queue (/generate/stream, the OpenAI-compatible API).
            abort.reset()
            _generation_queue.task_done()


async def _force_fail_if_active(generation_id: str, error: str) -> None:
    """Best-effort recovery — flip an active row to failed if the worker
    bailed before writing a terminal status. Catches the case where the gen
    coroutine's own status-write raised (e.g. SQLite lock contention)."""
    try:
        from ..database import Generation as DBGeneration, get_db
        from . import history

        db = next(get_db())
        try:
            gen = db.query(DBGeneration).filter_by(id=generation_id).first()
            if gen is None:
                return
            if (gen.status or "completed") not in ("loading_model", "generating"):
                return
            await history.update_generation_status(
                generation_id=generation_id,
                status="failed",
                db=db,
                error=error,
            )
        finally:
            db.close()
    except Exception:
        traceback.print_exc()


def enqueue_generation(generation_id: str, coro, group: str | None = None):
    """Add a generation coroutine to the serial queue."""
    if _generation_queue is None:
        raise RuntimeError("Generation queue has not been initialized")

    _queued_generation_ids.add(generation_id)
    _generation_queue.put_nowait(GenerationJob(generation_id=generation_id, coro=coro, group=group))


async def run_exclusive(key: str, factory):
    """Run ``await factory()`` through the same queue as TTS, and return its result.

    Anything that holds the GPU for a while goes through here, so it can never
    run beside a generation.
    """
    future = asyncio.get_running_loop().create_future()
    _exclusive_futures[key] = future

    async def runner() -> None:
        try:
            result = await factory()
        except asyncio.CancelledError:
            future.cancel()
            raise
        except Exception as e:
            future.set_exception(e)
        else:
            future.set_result(result)
        finally:
            _exclusive_futures.pop(key, None)

    enqueue_generation(key, runner())
    return await future


def running_keys() -> list[str]:
    return list(_running_generation_tasks)


def queued_keys() -> list[str]:
    return list(_queued_generation_ids)


def cancel_generation(generation_id: str) -> Literal["queued", "running"] | None:
    """Cancel a queued or running generation if it is still active."""
    if generation_id in _running_generation_tasks:
        # The model raises out of its own step, so the GPU is free when the
        # worker moves on; cancelling the awaiting task would not free it.
        abort.request_stop()
        return "running"

    if generation_id in _queued_generation_ids:
        _queued_generation_ids.discard(generation_id)
        _cancelled_generation_ids.add(generation_id)
        return "queued"

    return None


def init_queue(force: bool = False):
    """Initialize the generation queue and start the worker.

    Must be called once during application startup (inside a running event loop).
    """
    global _generation_queue, _generation_worker_task
    global _queued_generation_ids, _running_generation_tasks, _cancelled_generation_ids

    if _generation_worker_task is not None and not _generation_worker_task.done():
        if not force:
            return
        _generation_worker_task.cancel()
        for task in list(_running_generation_tasks.values()):
            task.cancel()

    _generation_queue = asyncio.Queue()
    _queued_generation_ids = set()
    _running_generation_tasks = {}
    _cancelled_generation_ids = set()
    _generation_worker_task = create_background_task(_generation_worker())
