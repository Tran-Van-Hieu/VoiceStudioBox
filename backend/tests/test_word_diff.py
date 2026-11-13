"""The word check behind the red "wrong words" mark (services/speech_qc.word_diff)."""

from backend.services.speech_qc import word_diff


def test_exact_match_ignores_case_punctuation_and_number_form():
    assert word_diff("No necesitas levantarte a las cuatro de la mañana.", "no necesitas levantarte a las 4 de la mañana", "es") == []
    assert word_diff("Tengo 1.000 euros y 2,5 kilos.", "tengo mil euros y dos punto cinco kilos", "es") == []


def test_reports_missing_extra_and_wrong_words_in_order():
    ops = word_diff("No necesitas meditar durante una hora.", "end necesitas meditar durante dos horas", "es")
    assert [o["op"] for o in ops] == ["wrong", "wrong"]
    assert ops[0]["expected"] == "no" and ops[0]["heard"] == "end"
    assert [o["op"] for o in word_diff("uno dos tres", "uno tres", "es")] == ["missing"]
    assert [o["op"] for o in word_diff("uno dos", "end uno dos", "es")] == ["extra"]


def test_vietnamese_tone_marks_only_count_when_asked():
    assert word_diff("Hôm nay trời đẹp", "hom nay troi dep", "vi") == []
    assert [o["op"] for o in word_diff("Hôm nay trời đẹp", "hôm nay trời đép", "vi", tones=True)] == ["wrong"]


def test_batch_merge_refuses_unfinished_items_and_keeps_number_order(tmp_path, monkeypatch):
    import numpy as np
    import pytest
    import soundfile as sf

    from backend.services import batch

    def item(i, status="completed"):
        path = tmp_path / f"{i}.wav"
        sf.write(path, np.full(100, i / 10, dtype=np.float32), 1000)
        return {"index": i, "status": status, "audio_path": str(path), "generation_id": f"g{i}", "gen_text": "x"}

    monkeypatch.setattr(batch.config, "resolve_storage_path", lambda p: __import__("pathlib").Path(p))
    monkeypatch.setattr("backend.utils.audio_post.load_segments", lambda *a: [])
    with pytest.raises(ValueError, match="2"):
        batch.build_merged([item(0), item(1, "failed"), item(2)], 0)
    audio, _sr, _ = batch.build_merged([item(2), item(0), item(1)], 0)
    assert [round(float(audio[k * 100]), 1) for k in range(3)] == [0.0, 0.1, 0.2]
