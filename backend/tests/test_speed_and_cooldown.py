"""Native speed for OmniVoice (no time-stretch) and the rest between scripts."""

import asyncio
import time

import numpy as np
import pytest

from backend.services import extras, task_queue
from backend.utils import audio_post


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    values = dict(extras.DEFAULTS, speed=0.9)
    monkeypatch.setattr(extras, "get_all", lambda: dict(values))
    monkeypatch.setattr(extras, "get", lambda key: values.get(key))
    return values


def test_native_speed_engine_is_never_time_stretched(monkeypatch):
    calls = []
    monkeypatch.setattr(audio_post, "change_speed", lambda a, sr, s: calls.append(s) or a)
    audio = np.zeros(2400, dtype=np.float32)

    audio_post.apply_output_processing(audio, 24000, None, "omnivoice")
    assert calls == []

    audio_post.apply_output_processing(audio, 24000, None, "qwen")
    audio_post.apply_output_processing(audio, 24000, 1.2, "qwen")
    assert calls == [0.9, 1.2]  # the user's default, then the request's own


def test_omnivoice_gets_speed_and_steps_as_model_arguments(_settings):
    from backend.backends.omnivoice_backend import OmniVoiceBackend
    from backend.utils.expressive import SynthStyle, use_style

    seen = {}

    class FakeModel:
        sampling_rate = 24000

        def generate(self, **kwargs):
            seen.update(kwargs)
            return [np.zeros(10, dtype=np.float32)]

    _settings["model_params"] = {"omnivoice": {"guidance_scale": 3.0, "denoise": False}}
    backend = OmniVoiceBackend()
    backend.model = FakeModel()
    backend._device = "cpu"
    with use_style(SynthStyle(speed=0.9, length_scale=1.15)):
        asyncio.run(backend.generate("xin chào", {}, "vi"))
    assert seen["num_step"] == 32
    assert seen["speed"] == pytest.approx(0.9 / 1.15)
    assert seen["guidance_scale"] == 3.0 and seen["denoise"] is False


def test_model_params_are_clamped_and_unknown_ones_dropped():
    clean = extras._sanitize(
        "model_params",
        {
            "omnivoice": {"guidance_scale": 99, "bogus": 1, "denoise": 0},
            "qwen": {"top_k": "500", "temperature": None},
            "chatterbox": {"exaggeration": 1},
            "qwen_voice_design": {},
        },
    )
    assert clean == {"omnivoice": {"guidance_scale": 5.0, "denoise": False}, "qwen": {"top_k": 200}}
    assert extras._sanitize("omnivoice_steps", 8) == 16


def test_queue_rests_only_between_different_scripts(monkeypatch, _settings):
    from backend.services import vram

    _settings["queue_cooldown_seconds"] = 1
    released = []
    monkeypatch.setattr(vram, "release_now", lambda: released.append(1))
    order = []

    async def job(name):
        order.append((name, len(released)))

    async def main():
        task_queue.init_queue(force=True)
        task_queue._last_group = None
        for name, group in [("a1", "A"), ("a2", "A"), ("b1", "B"), ("single", None)]:
            task_queue.enqueue_generation(name, job(name), group)
        await task_queue._generation_queue.join()

    asyncio.run(main())
    # Models are released exactly once: after script A, before script B.
    assert order == [("a1", 0), ("a2", 0), ("b1", 1), ("single", 1)]


def test_no_rest_when_the_queue_was_already_idle_long_enough(monkeypatch, _settings):
    from backend.services import vram

    _settings["queue_cooldown_seconds"] = 60
    released = []
    monkeypatch.setattr(vram, "release_now", lambda: released.append(1))

    async def main():
        task_queue.init_queue(force=True)
        task_queue._last_group = "A"
        task_queue._last_end = time.time() - 61  # script A finished a minute ago

        async def job():
            pass

        task_queue.enqueue_generation("b1", job(), "B")
        await asyncio.wait_for(task_queue._generation_queue.join(), timeout=2)

    asyncio.run(main())
    assert released == []


def test_history_dates_compare_as_utc_instants():
    from datetime import datetime

    from backend.services.history_search import _utc

    # 06:00 on 10 Nov in Vietnam is 23:00 on 9 Nov in UTC, as the database stores it.
    assert _utc("2026-11-09T23:00:00.000Z") == datetime(2026, 11, 9, 23, 0)
    assert _utc("2026-11-10T06:00:00+07:00") == datetime(2026, 11, 9, 23, 0)
