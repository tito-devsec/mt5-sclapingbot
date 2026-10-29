"""Small project-written lexicon sentiment score in [-1, 1]; a candidate feature, never a signal."""

from __future__ import annotations

import re

HAWKISH = {
    "hike",
    "hikes",
    "raise",
    "raises",
    "raised",
    "tighten",
    "tightening",
    "hawkish",
    "inflationary",
    "accelerate",
    "accelerated",
    "strong",
    "stronger",
    "beat",
    "beats",
    "surge",
    "surged",
    "robust",
}
DOVISH = {
    "cut",
    "cuts",
    "lower",
    "lowered",
    "ease",
    "easing",
    "dovish",
    "slowdown",
    "weak",
    "weaker",
    "miss",
    "misses",
    "decline",
    "declined",
    "contraction",
    "recession",
    "stimulus",
    "pause",
}
_WORD = re.compile(r"[a-z]+")


def score(text: str) -> float:
    words = _WORD.findall(text.lower())
    h = sum(w in HAWKISH for w in words)
    d = sum(w in DOVISH for w in words)
    return 0.0 if h + d == 0 else (h - d) / (h + d)
