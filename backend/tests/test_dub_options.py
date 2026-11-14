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
