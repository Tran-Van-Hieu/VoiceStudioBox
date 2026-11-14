"""Dubbing options: imported SRT, burned-in subtitle style, ducking under dubbed lines."""

import numpy as np
import pytest
import soundfile as sf

from backend.services import dubbing

SRT = """﻿1
00:00:01,000 --> 00:00:02,500
[Anna] Hello <i>there</i>.

2
00:00:03,000 --> 00:00:04,000
Bob: How are you?

3
00:00:05,000 --> 00:00:06,200
Fine, thanks.
"""


def test_srt_cues_and_speaker_names():
    cues = dubbing.parse_srt(SRT)
    assert [(c["start"], c["end"]) for c in cues] == [(1.0, 2.5), (3.0, 4.0), (5.0, 6.2)]
    assert [c["speaker"] for c in cues] == ["Anna", "Bob", None]
    assert [c["text"] for c in cues] == ["Hello there.", "How are you?", "Fine, thanks."]


def test_a_lone_colon_is_not_a_speaker():
    srt = "1\n00:00:01,000 --> 00:00:02,000\nNote: this\n\n2\n00:00:03,000 --> 00:00:04,000\nplain\n\n3\n00:00:05,000 --> 00:00:06,000\nplain too\n"
    assert all(c["speaker"] is None for c in dubbing.parse_srt(srt))
    with pytest.raises(ValueError):
        dubbing.parse_srt("no cues here")


def test_subtitle_style_is_cleaned_before_reaching_ffmpeg(tmp_path):
    style = dubbing.clean_subtitle(
        {"burn": 1, "font": "Arial',x=1:", "size": 500, "color": "red", "outline_color": "#112233", "position": "top"}
    )
    assert style["font"] == "Arialx1" and style["size"] == 96 and style["color"] == "#FFFFFF"
    project = {"subtitle": style, "segments": [{"start": 0.0, "end": 1.0, "text": "a", "translation": "b"}]}
    vf = dubbing._subtitle_filter(project, tmp_path)
    assert "OutlineColour=&H00332211" in vf and "Alignment=6" in vf and "FontName=Arialx1" in vf
    assert "b" in (tmp_path / "burn.srt").read_text(encoding="utf-8")
    assert dubbing._subtitle_filter({"subtitle": {"burn": False}}, tmp_path) is None


def test_voice_and_music_drop_only_under_dubbed_lines(tmp_path):
    rate, total = dubbing._MIX_RATE, 2.0
    n = int(rate * total)
    ones = np.full((n, 2), 0.1, dtype=np.float32)
    (tmp_path / "stems").mkdir()
    (tmp_path / "clips").mkdir()
    sf.write(tmp_path / "stems" / "vocals.wav", ones, rate)
    sf.write(tmp_path / "stems" / "background.wav", ones, rate)
    sf.write(tmp_path / "decoded.wav", ones * 2, rate)
    sf.write(tmp_path / "clips" / "s1.wav", np.zeros(int(rate * 0.5), dtype=np.float32), rate)
    project = {
        "segments": [{"id": "s1", "start": 1.0, "end": 1.5}],
        "original_volume": 0.5,
        "music_volume": 0.2,
    }
    _, mix = dubbing._mix(project, tmp_path, total)
    assert mix[int(rate * 0.5), 0] == pytest.approx(0.2, abs=1e-3)  # untouched original
    assert mix[int(rate * 1.25), 0] == pytest.approx(0.1 * 0.2 + 0.1 * 0.5, abs=1e-3)


def test_brief_budget_counts_japanese_as_dense_and_samples_the_whole_video():
    assert dubbing._approx_tokens("こんにちは世界") >= 7
    assert dubbing._approx_tokens("hello world, how are you") < 10
    lines = [f"行{i}" + "あ" * 40 for i in range(1000)]  # about 45 tokens each
    text = dubbing._brief_sample(lines, 3000)
    assert dubbing._approx_tokens(text) <= 3100
    assert "行0" in text and "行750" in text  # the start and the last quarter both reach the brief


def test_brief_shrinks_on_out_of_memory_and_never_stops_the_dub(monkeypatch, tmp_path):
    import asyncio

    monkeypatch.setattr(dubbing, "save", lambda p: None)
    monkeypatch.setattr(dubbing.vram, "unload_tts", lambda *a: None)
    sizes = []

    class FakeLLM:
        def __init__(self, fits):
            self.fits = fits

        async def generate(self, prompt, **kw):
            sizes.append(dubbing._approx_tokens(prompt))
            if sizes[-1] > self.fits:
                raise RuntimeError("CUDA out of memory. Tried to allocate 10.36 GiB")
            return "topic: test"

    project = {"target_language": "vi", "segments": [{"text": "あ" * 50} for _ in range(400)]}
    asyncio.run(dubbing._make_brief(project, FakeLLM(fits=1600), "4B"))
    assert project["brief"] == "topic: test" and len(sizes) == 2  # 3000 failed, 1500 fit

    sizes.clear()
    project = {"target_language": "vi", "segments": [{"text": "あ" * 50} for _ in range(400)]}
    asyncio.run(dubbing._make_brief(project, FakeLLM(fits=0), "4B"))
    assert "brief" not in project and len(sizes) == 3  # gave up quietly after three tries


def test_translations_keyed_by_number_survive_extra_context_lines():
    reply = '{"1": "một", "2": "hai", "3": "ba (ngữ cảnh)"}'
    assert dubbing._parse_lines(reply, 2) == ["một", "hai"]
    assert dubbing._parse_lines('["a", "b", "c"]', 2) is None  # an array cannot say which two
    assert dubbing._parse_lines('["a", "b"]', 2) == ["a", "b"]


def test_speakers_start_on_the_library_voice_chosen_for_the_dub(tmp_path):
    from backend.services import dubbing

    (tmp_path / "stems").mkdir()
    sf.write(str(tmp_path / "stems" / "vocals.wav"), np.zeros(16000, dtype=np.float32), 16000)
    project = {"profile_id": "voice-1", "segments": []}
    dubbing._diarize(project, tmp_path)
    assert project["speakers"] == {"S1": {"label": "Người nói 1", "mode": "profile", "profile_id": "voice-1"}}
