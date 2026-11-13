"""Colab keeps voices between sessions by mirroring the data dir to Drive (scripts/remote_server.py)."""

import importlib.util
import sqlite3
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "remote_server", Path(__file__).resolve().parents[2] / "scripts" / "remote_server.py"
)
remote_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(remote_server)


def _db(path: Path, rows: list[str]) -> None:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS p (name TEXT)")
    con.executemany("INSERT INTO p VALUES (?)", [(r,) for r in rows])
    con.commit()
    con.close()


def test_backup_mirrors_and_restore_brings_voices_back(tmp_path):
    data, drive = tmp_path / "data", tmp_path / "drive"
    (data / "profiles" / "a").mkdir(parents=True)
    (data / "cache").mkdir()
    _db(data / "voicebox.db", ["voice A"])
    (data / "profiles" / "a" / "sample.wav").write_bytes(b"A" * 10)
    (data / "profiles" / "old.wav").write_bytes(b"old")
    (data / "cache" / "big.bin").write_bytes(b"x")

    remote_server.backup(data, drive)
    assert (drive / "profiles" / "a" / "sample.wav").read_bytes() == b"A" * 10
    assert not (drive / "cache").exists()

    (data / "profiles" / "old.wav").unlink()  # deleted voice must stay deleted
    _db(data / "voicebox.db", ["voice B"])
    (drive / "link.txt").write_text("https://x.trycloudflare.com")
    remote_server.backup(data, drive)
    assert not (drive / "profiles" / "old.wav").exists()
    assert (drive / "link.txt").exists()

    fresh = tmp_path / "next-session"
    remote_server.restore(drive, fresh)
    names = [r[0] for r in sqlite3.connect(fresh / "voicebox.db").execute("SELECT name FROM p")]
    assert names == ["voice A", "voice B"]
    assert (fresh / "profiles" / "a" / "sample.wav").exists()
    assert not (fresh / "link.txt").exists()
