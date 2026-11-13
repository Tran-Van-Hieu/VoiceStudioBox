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
import json
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
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
CLOUDFLARED = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"


def pip(*args: str) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *args])


def install() -> None:
    # Colab ships TensorFlow and JAX; transformers imports them when present and
    # they crash against the numpy/ml_dtypes this app pins (Whisper and speaker
    # checks fail). Unused here.
    subprocess.call([sys.executable, "-m", "pip", "uninstall", "-y", "-q",
                     "tensorflow", "tf-keras", "keras", "jax", "jaxlib", "flax", "optax", "orbax-checkpoint"])
    # Same order as `just setup-python`: these two pin old torch/transformers,
    # so they go in without their dependency lists.
    pip("-r", str(ROOT / "backend" / "requirements.txt"))
    for package in ("chatterbox-tts", "omnivoice"):
        pip("--no-deps", package)


def build_web(cache: Optional[Path] = None) -> None:
    """Build the browser UI into ROOT/frontend, which the backend serves at /.

    Keyed by git commit, so a `git pull` rebuilds it; a copy in ``cache``
    (Drive) skips bun entirely on the next session.
    """
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = ROOT / "frontend"
    if (out / ".commit").exists() and (out / ".commit").read_text() == commit:
        return
    shutil.rmtree(out, ignore_errors=True)
    saved = cache / "web" if cache else None
    if saved and (saved / ".commit").exists() and (saved / ".commit").read_text() == commit:
        shutil.copytree(saved, out)
        return
    bun = shutil.which("bun") or str(Path.home() / ".bun" / "bin" / "bun")
    if not Path(bun).exists():
        subprocess.check_call("curl -fsSL https://bun.sh/install | bash", shell=True)
    subprocess.check_call([bun, "install"], cwd=ROOT)
    subprocess.check_call([bun, "x", "--bun", "vite", "build"], cwd=ROOT / "web")
    shutil.copytree(ROOT / "web" / "dist", out)
    (out / ".commit").write_text(commit)
    if saved:
        shutil.rmtree(saved, ignore_errors=True)
        shutil.copytree(out, saved)


# Models kept on Drive and downloaded on the first session.
PRELOAD_MODELS = ("omnivoice", "qwen-tts-1.7B")


def restore_models(saved: Path, hub: Path) -> None:
    """Copy model files saved on Drive that the local HF cache lacks."""
    if not saved.exists():
        return
    print("Copying saved models from Google Drive...", flush=True)
    started = time.time()
    shutil.copytree(saved, hub, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.tmp"),
                    copy_function=lambda s, d: None if os.path.exists(d) else shutil.copy2(s, d))
    print(f"Models ready ({time.time() - started:.0f}s)", flush=True)


def _api(port: int, path: str, body: Optional[dict] = None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def prepare_models(port: int, save_models) -> None:
    """Download the main models once, save them to Drive, then report newer versions."""
    try:
        _prepare_models(port, save_models)
    except Exception as e:  # the app works without this; models then download on first use
        print(f"Model preparation stopped: {e}", flush=True)


def _prepare_models(port: int, save_models) -> None:
    for name in PRELOAD_MODELS:
        status = {m["model_name"]: m for m in _api(port, "/models/status")["models"]}.get(name)
        if not status or status["downloaded"]:
            continue
        print(f"First run: downloading {status['display_name']}...", flush=True)
        if not status["downloading"]:
            _api(port, "/models/download", {"model_name": name})
        while True:
            time.sleep(5)
            status = {m["model_name"]: m for m in _api(port, "/models/status")["models"]}[name]
            if not status["downloading"]:
                break
        print(f"  {status['display_name']}: {'done' if status['downloaded'] else 'FAILED, see the log above'}", flush=True)
    if save_models:
        save_models()
        print("Models saved to Google Drive; next sessions skip the download.", flush=True)
    for model in _api(port, "/model-updates/check", {}):
        update = model.get("update") or {}
        if update.get("available"):
            print(f"Update available for {' / '.join(model['names'])}: "
                  "open the app -> Cài đặt -> Model to install it (or keep the current version).", flush=True)


# Rebuilt or re-downloaded on demand; not worth copying to Drive.
SKIP_SYNC = {"cache", "backends", "models", "voicebox.db", "voicebox.db-wal", "voicebox.db-shm"}


def restore(persist: Path, data: Path) -> None:
    """Copy a saved library (Drive) to the local data dir before the server starts."""
    if (persist / "voicebox.db").exists() and not (data / "voicebox.db").exists():
        # A stale -wal next to the DB would be replayed over it on open and roll it back.
        shutil.copytree(persist, data, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("link.txt", "*.tmp", "voicebox.db-wal", "voicebox.db-shm"))
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
    for leftover in ("voicebox.db-wal", "voicebox.db-shm"):  # from when the DB lived on Drive
        (persist / leftover).unlink(missing_ok=True)

    mirror(data, persist, lambda rel: rel.parts[0] in SKIP_SYNC or rel.name == "link.txt"
           or rel.name.endswith((".snapshot", ".tmp")))


def mirror(src: Path, dst: Path, skip) -> None:
    """Copy new or changed files from src to dst and delete files gone from src.

    ``skip(rel)`` leaves a path alone on both sides. Each file goes through a
    .tmp + rename, so a session killed mid-copy never leaves a torn file.
    """
    with _mirror_lock:
        _mirror(src, dst, skip)


_mirror_lock = threading.Lock()  # the minute loop and the first-run model save may overlap


def _mirror(src: Path, dst: Path, skip) -> None:
    keep = set()
    copied = 0
    for f in src.rglob("*"):
        rel = f.relative_to(src)
        if skip(rel) or not f.is_file():
            continue
        keep.add(rel)
        target = dst / rel
        st = f.stat()
        if not target.exists() or target.stat().st_size != st.st_size or target.stat().st_mtime + 2 < st.st_mtime:  # Drive rounds mtimes
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            shutil.copy2(f, tmp)
            os.replace(tmp, target)
            copied += st.st_size
    if copied > 50 * 2**20:
        print(f"Saved {copied / 2**20:.0f} MB to {dst}", flush=True)
    for f in dst.rglob("*"):  # deleted voices or model versions must not come back next session
        rel = f.relative_to(dst)
        if f.is_file() and rel not in keep and not skip(rel):
            f.unlink()


def _skip_model_file(rel: Path) -> bool:
    # blobs are what the snapshot symlinks point at; copying the snapshots
    # dereferences them, so Drive (no symlinks) holds each file once.
    return "blobs" in rel.parts or ".locks" in rel.parts or rel.name.endswith((".incomplete", ".tmp", ".lock"))


def backup_loop(syncs: list, stop: threading.Event, every: int = 60) -> None:
    while not stop.wait(every):
        for sync in syncs:
            try:
                sync()
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
    ap.add_argument("--cache-dir", help="keep models, pip wheels and the built UI here (e.g. Google Drive)")
    args = ap.parse_args()

    cache = Path(args.cache_dir) if args.cache_dir else None
    if cache:
        # Reuses wheels built from source (numpy on Python 3.13 takes minutes).
        os.environ["PIP_CACHE_DIR"] = str(cache / "pip")
    if not args.skip_install:
        install()
    if not args.no_web:
        build_web(cache)

    data = Path(args.data_dir)
    persist = Path(args.persist_dir) if args.persist_dir else None
    hub = Path(os.environ.get("HF_HUB_CACHE") or Path.home() / ".cache" / "huggingface" / "hub")
    syncs = []
    save_models = None
    if persist:
        restore(persist, data)
        syncs.append(lambda: backup(data, persist))
    if cache:
        restore_models(cache / "hf", hub)
        save_models = lambda: mirror(hub, cache / "hf", _skip_model_file)  # noqa: E731
        syncs.append(save_models)
    stop = threading.Event()
    threading.Thread(target=backup_loop, args=(syncs, stop), daemon=True).start()

    server = subprocess.Popen(
        [sys.executable, "-m", "backend.main", "--host", "0.0.0.0" if args.no_tunnel else "127.0.0.1",
         "--port", str(args.port), "--data-dir", args.data_dir],
        cwd=ROOT,
        env={**os.environ, "VOICEBOX_BACKEND_VARIANT": "cuda", "USE_TORCH": "1", "USE_TF": "0", "USE_FLAX": "0", "USE_JAX": "0",
             "TRANSFORMERS_NO_TF": "1"},
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
        threading.Thread(target=prepare_models, args=(args.port, save_models), daemon=True).start()
        server.wait()
    except KeyboardInterrupt:  # the ■ button in Colab
        print("\nStopping...")
    finally:
        stop.set()
        server.terminate()
        try:
            server.wait(timeout=20)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):  # open browser tabs keep event streams alive
            server.kill()
            server.wait()
        for sync in syncs:
            sync()
        if syncs:
            print("Saved voices, history and models to Google Drive")


if __name__ == "__main__":
    main()
