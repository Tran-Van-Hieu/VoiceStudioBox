import asyncio

import pytest

from backend.services import task_queue


@pytest.mark.asyncio
async def test_cancel_queued_generation_skips_execution():
    task_queue.init_queue(force=True)

    running_started = asyncio.Event()
    release_running = asyncio.Event()
    queued_ran = asyncio.Event()

    async def running_job():
        running_started.set()
        await release_running.wait()

    async def queued_job():
        queued_ran.set()

    task_queue.enqueue_generation("gen-running", running_job())
    await asyncio.wait_for(running_started.wait(), timeout=1)

    task_queue.enqueue_generation("gen-queued", queued_job())
    assert task_queue.cancel_generation("gen-queued") == "queued"

    release_running.set()
    await asyncio.sleep(0.1)

    assert not queued_ran.is_set()


@pytest.mark.asyncio
async def test_cancel_running_generation_stops_it_and_then_clears_the_flag():
    """Stop raises out of the model's own step (abort.check), not by cancelling
    the task: a cancelled task would leave the torch thread holding the GPU."""
    from backend.utils import abort

    task_queue.init_queue(force=True)
    running_started = asyncio.Event()

    async def running_job():
        running_started.set()
        while True:
            abort.check()  # what the forward-pass hook does
            await asyncio.sleep(0.01)

    task_queue.enqueue_generation("gen-running", running_job())
    await asyncio.wait_for(running_started.wait(), timeout=1)

    assert task_queue.cancel_generation("gen-running") == "running"
    await asyncio.wait_for(task_queue._generation_queue.join(), timeout=1)
    # Left set, the flag would abort the next /generate/stream call.
    assert not abort.stopping()
