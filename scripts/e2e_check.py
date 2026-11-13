"""Walk every main feature of a running Voice Studio Box backend and report PASS/FAIL.

    python scripts/e2e_check.py --url http://127.0.0.1:17493 --sample path/to/voice.wav --language es

Creates real voices, projects and generations, so point it at a test data dir.
Needs `requests` (already in the backend venv).
"""

import argparse
import io
import sys
import time
import traceback
import zipfile

import requests

sys.stdout.reconfigure(encoding="utf-8")
results: list[tuple[str, bool, str]] = []


def step(name):
    def wrap(fn):
        started = time.time()
        try:
            detail = fn() or ""
            results.append((name, True, f"{detail} ({time.time() - started:.0f}s)"))
        except Exception as e:  # report and keep walking
            traceback.print_exc()
            results.append((name, False, f"{type(e).__name__}: {str(e)[:300]}"))
        print(("PASS " if results[-1][1] else "FAIL ") + name + " — " + results[-1][2], flush=True)
        return fn
    return wrap


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:17493")
    ap.add_argument("--sample", required=True, help="a clean 10-30 s voice recording")
    ap.add_argument("--language", default="vi")
    ap.add_argument("--text", default="Primera frase de prueba. Segunda frase de prueba, un poco más larga.")
    ap.add_argument("--skip-dub", action="store_true")
    args = ap.parse_args()
    B = args.url.rstrip("/")
    s = requests.Session()

    def ok(r):
        if r.status_code >= 400:
            raise RuntimeError(f"{r.request.method} {r.url} -> {r.status_code} {r.text[:300]}")
        return r

    def wait(fn, done, timeout=900, every=1.0):
        end = time.time() + timeout
        while time.time() < end:
            value = fn()
            if done(value):
                return value
            time.sleep(every)
        raise TimeoutError("timed out")

    def wait_generation(gid):
        row = wait(lambda: ok(s.get(f"{B}/history/{gid}")).json(), lambda r: r["status"] in ("completed", "failed"))
        if row["status"] == "failed":
            raise RuntimeError(row.get("error"))
        return row

    state: dict = {}

    @step("Clone: làm sạch bản ghi")
    def _():
        with open(args.sample, "rb") as f:
            job = ok(s.post(f"{B}/voicelab/prepare", files={"file": ("sample.wav", f, "audio/wav")})).json()
        state["job"] = job["id"]
        job = wait(lambda: ok(s.get(f"{B}/voicelab/jobs/{state['job']}")).json(), lambda j: j["stage"] in ("ready", "failed"))
        if job["stage"] == "failed":
            raise RuntimeError(job.get("error"))
        state["duration"] = job["source"]["source_seconds"]
        return f"{state['duration']:.1f}s, music={job['source']['music']}"

    @step("Clone: chọn đoạn và tự chép lời")
    def _():
        ok(s.post(f"{B}/voicelab/jobs/{state['job']}/select",
                  json={"start": 0, "end": min(state["duration"], 15), "language": args.language}))
        # A failed selection goes back to "ready" with an error so the user can pick again.
        job = wait(lambda: ok(s.get(f"{B}/voicelab/jobs/{state['job']}")).json(),
                   lambda j: j["stage"] in ("done", "failed") or (j["stage"] == "ready" and j.get("error")))
        if job["stage"] != "done":
            raise RuntimeError(job.get("error"))
        state["ref_text"] = job["result"]["transcript"]
        if not state["ref_text"].strip():
            raise RuntimeError("Whisper returned no transcript")
        return state["ref_text"][:70]

    @step("Clone: tạo giọng mới")
    def _():
        v = ok(s.post(f"{B}/voicelab/jobs/{state['job']}/create-voice",
                      json={"name": "E2E test voice", "language": args.language,
                            "reference_text": state["ref_text"], "default_engine": "omnivoice"})).json()
        state["profile"] = v["profile_id"]
        return state["profile"]

    @step("Tạo một bản bằng giọng vừa clone")
    def _():
        g = ok(s.post(f"{B}/generate", json={"profile_id": state["profile"], "text": args.text,
                                             "language": args.language, "engine": "omnivoice", "speed": 1.1})).json()
        row = wait_generation(g["id"])
        state["gen"] = g["id"]
        return f"{row['duration']:.1f}s"

    @step("Lịch sử: tìm, chất lượng, xuất MP3/FLAC/SRT, ZIP")
    def _():
        found = ok(s.get(f"{B}/library/history", params={"q": "primera frase"})).json()
        rows = found["items"] if isinstance(found, dict) else found
        if not any(r["id"] == state["gen"] for r in rows):
            raise RuntimeError("generation not found by search")
        quality = ok(s.get(f"{B}/history/{state['gen']}/quality")).json()
        sizes = {f: len(ok(s.get(f"{B}/history/{state['gen']}/export-file", params={"format": f})).content) for f in ("mp3", "flac")}
        srt = ok(s.get(f"{B}/history/{state['gen']}/export-srt")).text
        if "-->" not in srt:
            raise RuntimeError("empty SRT")
        z = ok(s.post(f"{B}/library/history/export", json={"ids": [state["gen"]], "format": "mp3"})).content
        names = zipfile.ZipFile(io.BytesIO(z)).namelist()
        ok(s.put(f"{B}/library/history/{state['gen']}/meta", json={"pinned": True, "tags": ["e2e"]}))
        level = (quality.get("final") or {}).get("level")
        return f"quality={level} {sizes} zip={names}"

    @step("Sửa một đoạn (repair)")
    def _():
        r = ok(s.post(f"{B}/quality/{state['gen']}/repair/0")).json()
        return f"kept_previous={r.get('kept_previous', False)} duration={r.get('duration')}"

    @step("Tạo nâng cao: nhập kịch bản vai, tạo, xuất gộp + ZIP")
    def _():
        p = ok(s.post(f"{B}/studio", json={"name": "E2E", "language": args.language,
                                           "defaults": {"profile_id": state["profile"], "engine": "omnivoice", "speed": 1.0}})).json()
        pid = p["id"]
        script = "E2E test voice: Hola, esta es la primera línea. [dừng 1s]\nNarrador: Y esta es la segunda línea del guion."
        p = ok(s.post(f"{B}/studio/{pid}/import", data={"script": script})).json()
        count = len(p["segments"])
        ok(s.post(f"{B}/studio/{pid}/render", json={}))
        p = wait(lambda: ok(s.get(f"{B}/studio/{pid}")).json(), lambda v: not v["pending"])
        statuses = [x["status"] for x in p["segments"]]
        if set(statuses) != {"completed"}:
            raise RuntimeError(f"{statuses} {[x.get('error') for x in p['segments']]}")
        merged = ok(s.get(f"{B}/studio/{pid}/export", params={"mode": "merged", "format": "mp3", "srt": "false"})).content
        files = ok(s.get(f"{B}/studio/{pid}/export", params={"mode": "files", "format": "wav", "srt": "true"})).content
        names = zipfile.ZipFile(io.BytesIO(files)).namelist()
        if "001.srt" not in names:
            raise RuntimeError(f"no subtitles in the per-file ZIP: {names}")
        ok(s.delete(f"{B}/studio/{pid}"))
        return f"{count} segments, merged {len(merged)} bytes, zip={names}"

    @step("So sánh model cho giọng (audition OmniVoice)")
    def _():
        options = ok(s.get(f"{B}/voicelab/models", params={"language": args.language})).json()
        r = ok(s.post(f"{B}/voicelab/audition", json={"profile_id": state["profile"], "language": args.language,
                                                      "keys": ["omnivoice:default"]})).json()
        models = wait(lambda: ok(s.get(f"{B}/voicelab/profiles/{state['profile']}/models")).json(),
                      lambda m: "omnivoice:default" in m, timeout=300)
        m = models["omnivoice:default"]
        return f"{len(options)} options, similarity={m.get('similarity')} text={m.get('text_score')} resp={str(r)[:60]}"

    if not args.skip_dub:
        @step("Lồng tiếng: chỉ chép lời (transcribe)")
        def _():
            with open(args.sample, "rb") as f:
                r = ok(s.post(f"{B}/dubbing", files=[("files", ("sample.wav", f, "audio/wav"))],
                              data={"mode": "transcribe", "source_language": args.language, "auto_continue": "true"})).json()
            project = (r["projects"] if isinstance(r, dict) and "projects" in r else r if isinstance(r, list) else [r])[0]
            did = project["id"]
            p = wait(lambda: ok(s.get(f"{B}/dubbing/{did}")).json(),
                     lambda v: v.get("stage") in ("done", "failed", "review", "stopped"), timeout=600, every=2)
            if p.get("stage") == "failed":
                raise RuntimeError(p.get("error"))
            srt = ok(s.get(f"{B}/dubbing/{did}/subtitles", params={"which": "source"})).text
            if "-->" not in srt:
                raise RuntimeError("empty SRT")
            ok(s.delete(f"{B}/dubbing/{did}"))
            return f"stage={p.get('stage')} lines={len(p.get('segments') or [])} srt={len(srt)} chars"

    @step("Dọn: xóa bản tạo và giọng thử")
    def _():
        ok(s.delete(f"{B}/history/{state['gen']}"))
        ok(s.delete(f"{B}/profiles/{state['profile']}"))

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
