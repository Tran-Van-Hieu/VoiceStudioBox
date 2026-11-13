"""Run Voice Studio Box on a GPU machine (Google Colab, Kaggle, a cloud VM) and open it in a browser.

From the repo root on the remote machine:

    python scripts/remote_server.py            # install, build the web UI, start, open a tunnel
    python scripts/remote_server.py --no-tunnel --port 17493   # VM with its own address

It prints a public https URL. Open it in any browser: the full app runs there,
the GPU work happens on the remote machine. The desktop app can also use it
(Cài đặt -> Địa chỉ server).

shortcut: the tunnel URL is the only protection (no login), keep it private and
stop the cell when done; add an auth proxy before sharing a server with others.
"""

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLOUDFLARED = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"


def pip(*args: str) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *args])


def install() -> None:
    # Colab ships TensorFlow; transformers then imports it and it crashes against
    # the numpy/protobuf this app pins (Whisper and speaker checks fail). Unused here.
    subprocess.call([sys.executable, "-m", "pip", "uninstall", "-y", "-q", "tensorflow", "tf-keras", "keras"])
    # Same order as `just setup-python`: these two pin old torch/transformers,
    # so they go in without their dependency lists.
    pip("-r", str(ROOT / "backend" / "requirements.txt"))
    for package in ("chatterbox-tts", "omnivoice"):
        pip("--no-deps", package)


def build_web() -> None:
    """Build the browser UI into ROOT/frontend, which the backend serves at /."""
    if (ROOT / "frontend" / "index.html").exists():
        return
    bun = shutil.which("bun") or str(Path.home() / ".bun" / "bin" / "bun")
    if not Path(bun).exists():
        subprocess.check_call("curl -fsSL https://bun.sh/install | bash", shell=True)
    subprocess.check_call([bun, "install"], cwd=ROOT)
    subprocess.check_call([bun, "x", "--bun", "vite", "build"], cwd=ROOT / "web")
    shutil.copytree(ROOT / "web" / "dist", ROOT / "frontend")


# Rebuilt or re-downloaded on demand; not worth copying to Drive.
SKIP_SYNC = {"cache", "backends", "models", "voicebox.db", "voicebox.db-wal", "voicebox.db-shm"}


def restore(persist: Path, data: Path) -> None:
    """Copy a saved library (Drive) to the local data dir before the server starts."""
    if (persist / "voicebox.db").exists() and not (data / "voicebox.db").exists():
        shutil.copytree(persist, data, dirs_exist_ok=True, ignore=shutil.ignore_patterns("link.txt", "*.tmp"))
        print(f"Restored voices and history from {persist}")


def backup(data: Path, persist: Path) -> None:
    """Mirror the local data dir to ``persist``.

    SQLite runs on local disk (Drive's FUSE mount breaks its locking); the DB is
    copied with the backup API so a write in progress never yields a torn file.
    """
    db = data / "voicebox.db"
    if not db.exists():
        return
    persist.mkdir(parents=True, exist_ok=True)
    snap = data / "voicebox.db.snapshot"
    src, dst = sqlite3.connect(db), sqlite3.connect(snap)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    shutil.copyfile(snap, persist / "voicebox.db.tmp")
    os.replace(persist / "voicebox.db.tmp", persist / "voicebox.db")
    snap.unlink()

    keep = set()
    for f in data.rglob("*"):
        rel = f.relative_to(data)
        if rel.parts[0] in SKIP_SYNC or not f.is_file() or f.name.endswith(".snapshot"):
            continue
        keep.add(rel)
        target = persist / rel
        st = f.stat()
        if not target.exists() or target.stat().st_size != st.st_size or target.stat().st_mtime < st.st_mtime:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
    for f in persist.rglob("*"):  # deleted voices must not come back next session
        rel = f.relative_to(persist)
        if f.is_file() and rel not in keep and rel.parts[0] not in SKIP_SYNC and rel.name != "link.txt":
            f.unlink()


def backup_loop(data: Path, persist: Path, stop: threading.Event, every: int = 60) -> None:
    while not stop.wait(every):
        try:
            backup(data, persist)
        except Exception as e:  # a failed round must not kill the server; the next one retries
            print(f"Drive backup failed, retrying in {every}s: {e}")


def wait_healthy(port: int, seconds: int = 300) -> None:
    for _ in range(seconds):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            return
        except OSError:
            time.sleep(1)
    raise SystemExit("Server did not become healthy; see the log above.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=17493)
    ap.add_argument("--data-dir", default=str(ROOT / "remote-data"))
    ap.add_argument("--skip-install", action="store_true")
    ap.add_argument("--no-web", action="store_true", help="API only, for the desktop app")
    ap.add_argument("--no-tunnel", action="store_true", help="bind 0.0.0.0 and print no public URL")
    ap.add_argument("--persist-dir", help="mirror voices and history here (e.g. Google Drive) and restore from it")
    args = ap.parse_args()

    if not args.skip_install:
        install()
    if not args.no_web:
        build_web()

    data = Path(args.data_dir)
    persist = Path(args.persist_dir) if args.persist_dir else None
    stop = threading.Event()
    if persist:
        restore(persist, data)
        threading.Thread(target=backup_loop, args=(data, persist, stop), daemon=True).start()

    server = subprocess.Popen(
        [sys.executable, "-m", "backend.main", "--host", "0.0.0.0" if args.no_tunnel else "127.0.0.1",
         "--port", str(args.port), "--data-dir", args.data_dir],
        cwd=ROOT,
        env={**os.environ, "VOICEBOX_BACKEND_VARIANT": "cuda", "USE_TF": "0", "TRANSFORMERS_NO_TF": "1"},
    )
    try:
        wait_healthy(args.port)
        if args.no_tunnel:
            print(f"\nVoice Studio Box ready on port {args.port}. Open http://<this machine's address>:{args.port}\n")
            server.wait()
            return

        binary = shutil.which("cloudflared") or str(ROOT / "cloudflared")
        if not Path(binary).exists():
            urllib.request.urlretrieve(CLOUDFLARED, binary)
            os.chmod(binary, 0o755)
        tunnel = subprocess.Popen(
            [binary, "tunnel", "--url", f"http://127.0.0.1:{args.port}"],
            stderr=subprocess.PIPE, text=True,
        )
        for line in tunnel.stderr:  # cloudflared prints the URL on stderr
            match = re.search(r"https://[\w-]+\.trycloudflare\.com", line)
            if match:
                print(f"\nVoice Studio Box ready. Open this link in your browser:\n\n    {match[0]}\n")
                if persist:
                    persist.mkdir(parents=True, exist_ok=True)
                    (persist / "link.txt").write_text(match[0] + "\n")
                break
        # Keep reading: a full stderr pipe would block cloudflared and freeze the tunnel.
        threading.Thread(target=lambda: [None for _ in tunnel.stderr], daemon=True).start()
        server.wait()
    finally:
        stop.set()
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:  # open browser tabs keep event streams alive
            server.kill()
            server.wait()
        if persist:
            backup(data, persist)
            print(f"Saved voices and history to {persist}")


if __name__ == "__main__":
    main()
