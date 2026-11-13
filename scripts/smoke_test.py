"""End-to-end smoke test against a running Voice Studio Box backend (local, Colab or VM).

    python scripts/smoke_test.py --url http://127.0.0.1:17493 --engine omnivoice --language vi

Generates one clip through the real queue, waits for it, and prints how long it
took against the audio length (RTF < 1 means faster than real time).
"""

import argparse
import json
import sys
import time
import urllib.request

TEXTS = {
    "vi": "Xin chào, đây là bản thử giọng. Hôm nay chúng ta kiểm tra tốc độ và chất lượng của mô hình đọc.",
    "es": "El rumor es antiguo y persistente: se dice que en el ángulo superior de un sótano vive una criatura temible.",
    "en": "Hello, this is a voice test. Today we are checking the speed and quality of the speech model.",
}


def call(url, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read() or b"null")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:17493")
    ap.add_argument("--engine", default="omnivoice")
    ap.add_argument("--model-size", default=None)
    ap.add_argument("--language", default="vi")
    ap.add_argument("--speed", type=float, default=None)
    ap.add_argument("--text", default=None)
    ap.add_argument("--profile", default=None, help="profile id (default: first one)")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    base = args.url.rstrip("/")

    profile = args.profile or call(f"{base}/profiles")[0]["id"]
    for i in range(args.repeat):
        body = {"profile_id": profile, "text": args.text or TEXTS.get(args.language, TEXTS["en"]),
                "language": args.language, "engine": args.engine}
        if args.model_size:
            body["model_size"] = args.model_size
        if args.speed:
            body["speed"] = args.speed
        t0 = time.time()
        gen = call(f"{base}/generate", body)
        while True:
            row = call(f"{base}/history/{gen['id']}")
            if row.get("status") in ("completed", "failed"):
                break
            time.sleep(0.5)
        took = time.time() - t0
        if row["status"] == "failed":
            print(f"run {i + 1}: FAILED after {took:.1f}s: {row.get('error')}")
            return 1
        print(f"run {i + 1}: {took:.1f}s for {row['duration']:.1f}s of audio (RTF {took / row['duration']:.2f}) id={gen['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
