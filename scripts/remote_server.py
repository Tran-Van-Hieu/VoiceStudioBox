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
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLOUDFLARED = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"


def pip(*args: str) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *args])


def install() -> None:
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
    args = ap.parse_args()

    if not args.skip_install:
        install()
    if not args.no_web:
        build_web()

    server = subprocess.Popen(
        [sys.executable, "-m", "backend.main", "--host", "0.0.0.0" if args.no_tunnel else "127.0.0.1",
         "--port", str(args.port), "--data-dir", args.data_dir],
        cwd=ROOT,
        env={**os.environ, "VOICEBOX_BACKEND_VARIANT": "cuda"},
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
                break
        server.wait()
    finally:
        server.terminate()


if __name__ == "__main__":
    main()
