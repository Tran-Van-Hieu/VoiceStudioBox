"""Designed voices keep one chosen clip; takes are picked by words, then naturalness."""

import json

import pytest

from backend import config
from backend.services import design_anchor


def test_pick_prefers_the_most_natural_take_that_was_read_right():
    takes = [
        {"words": 1.0, "mos": 2.9},
        {"words": 0.8, "mos": 4.5},  # most natural but misread
        {"words": 1.0, "mos": 4.2},
        {"words": 0.95, "mos": None},
    ]
    assert design_anchor._pick(takes) == 2
    assert design_anchor._pick([{"words": 0.5, "mos": 4.0}, {"words": 0.7, "mos": 3.0}]) == 1


def test_choosing_a_take_only_works_inside_the_profiles_own_groups(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_data_dir", tmp_path)
    pid = "11111111-2222-3333-4444-555555555555"
    stem = f"{pid}_0123456789ab"
    folder = design_anchor._dir()
    (folder / f"{stem}.json").write_text(json.dumps({"takes": [{}, {}], "chosen": 0, "text": "x"}), encoding="utf-8")
    for i in range(2):
        (folder / f"{stem}.take{i}.wav").write_bytes(b"RIFF")

    assert design_anchor.choose(pid, stem, 1)["chosen"] == 1
    assert design_anchor.groups(pid)[0]["picked_by"] == "user"
    with pytest.raises(ValueError):
        design_anchor.choose("99999999-2222-3333-4444-555555555555", stem, 1)  # someone else's group
    with pytest.raises(ValueError):
        design_anchor.take_path(pid, "../../etc", 0)
    with pytest.raises(ValueError):
        design_anchor.choose(pid, stem, 5)
