#!/usr/bin/env python3
"""
core.py -- shared machinery: JSON extraction, retrievable memory, budget.

Small on purpose. Everything here is load-bearing for safety:
  * extract_json: the ONLY way brain output becomes an action
  * Memory: lessons retrieved by relevance, so knowledge compounds
  * Budget: hard dollar/call stops so an unattended agent cannot overspend
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

HOME = Path.home() / "engineai"
SCENE = HOME / "scene"
MEMORY_FILE = HOME / "memory" / "lessons.md"


# --------------------------------------------------------------- json guard --

def extract_json(text: str) -> dict:
    """Pull the first balanced {...} object out of model output.
    Tolerates prose or code fences around it. Raises ValueError if none."""
    text = re.sub(r"```(json)?", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON in reply")


# ------------------------------------------------------------------- memory --

_WORD = re.compile(r"[a-zA-Z_]{4,}")


class Memory:
    """Lessons file: '- [mission] text' lines. Retrieval = keyword overlap.
    Deliberately simple; upgrade to embeddings only when this saturates."""

    def __init__(self, path: Path = MEMORY_FILE):
        self.path = path

    def _lines(self):
        if not self.path.exists():
            return []
        return [ln.strip() for ln in self.path.read_text().splitlines()
                if ln.strip().startswith("- ")]

    def top(self, query: str, k: int = 5) -> str:
        lines = self._lines()
        if not lines:
            return "(no lessons yet)"
        q = set(w.lower() for w in _WORD.findall(query))
        scored = sorted(
            lines,
            key=lambda ln: -len(q & set(w.lower() for w in _WORD.findall(ln))),
        )
        return "\n".join(scored[:k])

    def save(self, mission: str, lesson: str):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(f"- [{mission}] {lesson.strip()}\n")


# ------------------------------------------------------------------- budget --

class Budget:
    """Token/dollar/call accounting with HARD stops."""

    def __init__(self, cfg: dict):
        self.cap_usd = float(cfg["hard_cap_usd"])
        self.p_in = float(cfg["price_per_mtok_input_usd"]) / 1e6
        self.p_out = float(cfg["price_per_mtok_output_usd"]) / 1e6
        self.in_tok = 0
        self.out_tok = 0
        self.calls = 0

    def add(self, in_tok: int, out_tok: int):
        self.in_tok += in_tok
        self.out_tok += out_tok
        self.calls += 1

    @property
    def spent_usd(self) -> float:
        return self.in_tok * self.p_in + self.out_tok * self.p_out

    def allow(self) -> bool:
        return self.spent_usd < self.cap_usd

    def state(self) -> dict:
        return {"api_calls": self.calls,
                "spent_usd": round(self.spent_usd, 4),
                "hard_cap_usd": self.cap_usd}


# -------------------------------------------------------------------- audit --

def _np_safe(o):
    """numpy scalars leak into events; np.bool_ is not JSON serializable."""
    try:
        import numpy as np
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.bool_):
            return bool(o)
    except Exception:
        pass
    return str(o)


def audit(event: dict):
    """Append every brain decision / gate result to an audit log."""
    SCENE.mkdir(parents=True, exist_ok=True)
    event = dict(event)
    event["t"] = time.time()
    with (SCENE / "decisions.jsonl").open("a") as fh:
        fh.write(json.dumps(event, default=_np_safe) + "\n")
