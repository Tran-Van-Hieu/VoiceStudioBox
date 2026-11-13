"""i18n helpers for the vi-upgrades fork.

usage:
  python merge.py add-extras        # merge extras.en.json into en/translation.json
  python merge.py flatten OUT N     # write en keys as N flat JSON chunks OUT_<i>.json
  python merge.py build-vi CHUNK... # combine translated flat chunks into vi/translation.json
  python merge.py check             # verify vi covers every en key with matching placeholders
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "app" / "src" / "i18n" / "locales"
EN = LOCALES / "en" / "translation.json"
VI = LOCALES / "vi" / "translation.json"
HERE = Path(__file__).resolve().parent


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def dump(p, data):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        else:
            out[key] = v
    return out


def unflatten_like(template, flat, prefix=""):
    """Rebuild nested dict in the same key order as *template*."""
    out = {}
    for k, v in template.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out[k] = unflatten_like(v, flat, key)
        elif key.endswith("_one") and key[:-4] + "_other" in flat:
            # Vietnamese has no singular plural form; i18next uses _other.
            continue
        else:
            out[k] = flat.get(key, v)
    return out


PH = re.compile(r"\{\{\s*([\w.]+)\s*\}\}|<(\d+)>|<(/?\w+)>")


def placeholders(s):
    return sorted(m.group(0).replace(" ", "") for m in PH.finditer(s)) if isinstance(s, str) else []


def main():
    cmd = sys.argv[1]
    if cmd == "add-extras":
        en = load(EN)
        deep_merge(en, load(HERE / "extras.en.json"))
        dump(EN, en)
        print("en keys:", len(flatten(en)))
    elif cmd == "flatten":
        out, n = sys.argv[2], int(sys.argv[3])
        flat = list(flatten(load(EN)).items())
        size = -(-len(flat) // n)
        for i in range(n):
            dump(f"{out}_{i}.json", dict(flat[i * size : (i + 1) * size]))
        print(len(flat), "keys in", n, "chunks")
    elif cmd == "build-vi":
        flat = {}
        for p in sys.argv[2:]:
            flat.update(load(p))
        dump(VI, unflatten_like(load(EN), flat))
        print("vi written")
    elif cmd == "check":
        en, vi = flatten(load(EN)), flatten(load(VI))
        missing = [k for k in en if k not in vi and not (k.endswith("_one") and k[:-4] + "_other" in en)]
        same = [k for k in en if vi.get(k) == en[k] and isinstance(en[k], str) and re.search(r"[a-z]{4}", en[k])]
        bad_ph = [k for k in en if k in vi and placeholders(en[k]) != placeholders(vi[k])]
        print("missing:", len(missing), missing[:20])
        print("placeholder mismatch:", len(bad_ph))
        for k in bad_ph[:30]:
            print("  ", k, "|", en[k], "|", vi[k])
        print("identical to English:", len(same))
        for k in same[:60]:
            print("  ", k, "=", en[k])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
