"""Pronunciation dictionary: rewrite words/names before they reach the TTS engine.

Rules come from ``services.extras`` (``pronunciation`` list). Matching is done
in a single pass with one combined regex so a replacement is never re-matched
by another rule, and longer source strings win over shorter overlapping ones
(``"TP.HCM"`` before ``"TP"``).
"""

from __future__ import annotations

import re
from typing import Any, Iterable


def _rule_pattern(src: str, whole_word: bool) -> str:
    pat = re.escape(src)
    if whole_word:
        # \w is Unicode-aware in Python 3, so Vietnamese letters count as word chars.
        pat = rf"(?<!\w){pat}(?!\w)"
    return pat


def apply_rules(text: str, rules: Iterable[dict[str, Any]]) -> str:
    """Return *text* with every enabled rule applied."""
    active = [r for r in rules if r.get("enabled", True) and r.get("from")]
    if not active or not text:
        return text

    # Longest first so the alternation prefers the most specific match.
    active.sort(key=lambda r: len(r["from"]), reverse=True)

    parts = []
    for i, r in enumerate(active):
        group = "(?:" if r.get("case_sensitive") else "(?i:"
        body = _rule_pattern(r["from"], r.get("whole_word", True))
        parts.append(f"(?P<r{i}>{group}{body}))")
    combined = re.compile("|".join(parts))

    def _sub(m: re.Match) -> str:
        name = m.lastgroup
        if name is None:
            return m.group(0)
        return active[int(name[1:])].get("to", "")

    return combined.sub(_sub, text)


def build_text_transform(language: str):
    """The per-chunk rewrite for *language*: dictionary first, then numbers."""

    def transform(text: str) -> str:
        text = apply_pronunciation(text)
        if language == "vi":
            from .vi_text import normalize_vietnamese

            text = normalize_vietnamese(text)
        return text

    return transform


def apply_pronunciation(text: str) -> str:
    """Apply the user's saved dictionary (no-op when disabled or empty)."""
    from ..services import extras

    settings = extras.get_all()
    if not settings.get("pronunciation_enabled", True):
        return text
    return apply_rules(text, settings.get("pronunciation") or [])
