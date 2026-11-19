#!/usr/bin/env python3
"""qbank_parse.py - turn the supplied Engineering Question Bank PDF text into
structured questions.

The bank arrived as flattened PDF text: page furniture interleaved with the
content, questions wrapped across lines, and mojibake from a UTF-8 file that
was read as cp1252 somewhere upstream. This recovers the questions under their
branch and topic, and CHECKS the recovery against the counts the document
states about itself - if the parse drifts, the self-declared totals catch it
rather than me eyeballing a sample.

That cross-check matters more than it looks. A parser that silently drops the
last question of every topic still "works", and a battery built on it would
report a coverage number for a corpus that is not the one the user gave me.

THE SUPPLIED TEXT IS TRUNCATED and this module says so loudly rather than
quietly benchmarking 64% of a corpus as if it were the whole. The paste was cut
at a 50,000-character limit partway through "Programming and OOP", so
Electronics & Communication (175) and Chemical (175) are absent entirely. The
contents list survived, so the manifest of all 48 topics and their counts is
known even where the questions are not: `missing()` reports exactly which
topics are short and by how much, and the battery reports coverage against
what is PRESENT while naming what is absent.
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "qbank_raw.txt")
OUT = os.path.join(HERE, "data", "question_bank.json")

BRANCHES = [
    "Mechanical Engineering",
    "Electrical Engineering",
    "Civil Engineering",
    "Computer Science & Engineering",
    "Electronics & Communication Engineering",
    "Chemical Engineering",
]

# Page furniture, dropped wholesale.
FOOTER = re.compile(r"^\s*(1062 questions \| 6 branches|Page \d+)\s*$")
HEADER = "Engineering Question Bank"
NUMBERED = re.compile(r"^(\d+)\.\s+(.*)$")
# "Thermodynamics (25)" in the contents list
CONTENTS_TOPIC = re.compile(r"^(.+?)\s+\((\d+)\)\s*$")
CONTENTS_BRANCH = re.compile(r"^(.+?)\s+[-—]\s+(\d+) questions\s*$")
# Cover-page furniture, which a re-pasted chunk brings along with it.
FRONT = re.compile(
    r"^(\d+ questions|1000\+ practice.*|Generated \w+ \d{4}|"
    r"Contents|Note: questions only.*|Mechanical\s+•.*)\s*$")

_MOJI = {
    "â€”": "—", "â€“": "–",
    "â€™": "'", "â€˜": "'",
    "â€œ": '"', "â€": '"',
    "â€¢": "•", "Ã—": "×",
    "Â°": "°", "Âµ": "µ",
    "â€¦": "...", "Â ": " ",
}


def demojibake(s: str) -> str:
    """Undo UTF-8-read-as-cp1252. Try the clean round trip, fall back to a table.

    The round trip is exact when it works and silently wrong when the text has
    genuine cp1252 characters, so it is only accepted if it decodes cleanly.
    """
    if "â" in s or "Ã" in s or "Â" in s:
        try:
            return s.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        for bad, good in _MOJI.items():
            s = s.replace(bad, good)
    return s


def sources() -> list:
    """The raw paste, plus any later chunk dropped in as data/qbank_extra_*.txt.

    The 50k paste limit cut the document mid-corpus, so the remaining branches
    have to arrive separately. Merging by file rather than by edit means the
    missing 385 can be added with a copy, not a code change.
    """
    import glob
    extra = sorted(glob.glob(os.path.join(HERE, "data", "qbank_extra_*.txt")))
    return [RAW] + extra


def parse(path=None) -> dict:
    paths = sources() if path is None else ([path] if isinstance(path, str) else list(path))
    lines: list = []
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as fh:
            lines += [demojibake(ln.rstrip()) for ln in fh]

    # ---- pass 1: the contents list, which is the document's own claim about
    # what it contains. Parsed FIRST so it can referee the question parse.
    declared_branch: dict = {}
    declared_topic: list = []
    cur = None
    for ln in lines[:80]:
        m = CONTENTS_BRANCH.match(ln)
        if m and m.group(1).strip() in BRANCHES:
            cur = m.group(1).strip()
            declared_branch[cur] = int(m.group(2))
            continue
        m = CONTENTS_TOPIC.match(ln)
        if m and cur:
            declared_topic.append((cur, m.group(1).strip(), int(m.group(2))))

    # ---- pass 2: the questions themselves.
    body_start = 0
    for i, ln in enumerate(lines):
        if ln.strip().startswith("Note: questions only"):
            body_start = i + 1
            break

    # Strip page furniture first so "followed by" means followed in the
    # content, not followed after a page break. Front matter and the contents
    # list are stripped too: a later chunk pasted in as qbank_extra_*.txt will
    # carry them again, and an unnumbered line that is not a heading gets
    # glued onto the previous question.
    def furniture(t: str) -> bool:
        return (not t or t == HEADER or bool(FOOTER.match(t))
                or bool(CONTENTS_TOPIC.match(t)) or bool(CONTENTS_BRANCH.match(t))
                or bool(FRONT.match(t)))

    body = [ln.strip() for ln in lines[body_start:] if not furniture(ln.strip())]

    out: list = []
    branch = topic = None
    last = None

    for i, t in enumerate(body):
        if t in BRANCHES:
            branch = t
            topic = None
            continue

        m = NUMBERED.match(t)
        if m:
            last = {"branch": branch, "topic": topic,
                    "n": int(m.group(1)), "question": m.group(2).strip()}
            out.append(last)
            continue

        # A topic header is an unnumbered line whose NEXT line is question 1.
        # Position relative to the page break is not a reliable signal: topics
        # start mid-page as often as they start after one, and using the page
        # break silently merged Electromagnetic Fields with Signals and Systems
        # into a single 40-question topic.
        nxt = body[i + 1] if i + 1 < len(body) else ""
        if re.match(r"^1\.\s+", nxt):
            topic = t
            continue

        # Anything else is the tail of a question that wrapped across lines.
        if last is not None:
            last["question"] = (last["question"] + " " + t).strip()

    for i, q in enumerate(out):
        q["id"] = i + 1
    return {"questions": out,
            "declared_branch": declared_branch,
            "declared_topic": declared_topic}


def missing(r: dict | None = None) -> list:
    """Topics the contents list declares that the supplied text does not deliver.

    Returned as (branch, topic, declared, parsed) so a partial corpus can never
    be mistaken for a complete one downstream.
    """
    from collections import Counter
    r = r or parse()
    got = Counter((q["branch"], q["topic"]) for q in r["questions"])
    out = []
    for b, t, n in r["declared_topic"]:
        have = got[(b, t)]
        if have < n:
            out.append((b, t, n, have))
    return out


def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    r = parse()
    qs = r["questions"]
    from collections import Counter

    # The contents list is the document's own manifest and it survived the
    # truncation intact, so it is the reference for what SHOULD be here.
    chk(len(r["declared_topic"]) == 48,
        f"manifest lists 48 topics (got {len(r['declared_topic'])})")
    chk(sum(r["declared_branch"].values()) == 1062,
        f"manifest totals 1062 (got {sum(r['declared_branch'].values())})")

    chk(all(q["branch"] for q in qs), "no question orphaned from its branch")
    chk(all(q["topic"] for q in qs), "every question has a topic")

    got_t = Counter((q["branch"], q["topic"]) for q in qs)
    declared = {(b, t): n for b, t, n in r["declared_topic"]}

    # OVER-delivery is the merge bug: two topics collapsing into one because a
    # heading was missed. It is the failure that matters, and it is the one
    # that a "did we get 1062?" check cannot see once the text is truncated.
    over = [(b, t, declared[(b, t)], n) for (b, t), n in got_t.items()
            if n > declared.get((b, t), 0)]
    chk(not over, f"no topic over-delivers, i.e. no two topics merged ({over[:3]})")

    unknown = [k for k in got_t if k not in declared]
    chk(not unknown, f"no invented topic outside the manifest ({unknown[:3]})")

    # Every topic present is either complete or is the one the cut landed in.
    partial = [(b, t, declared[(b, t)], n) for (b, t), n in got_t.items()
               if n < declared[(b, t)]]
    chk(len(partial) <= 1,
        f"at most one partially-delivered topic, the truncation point ({partial})")

    chk(len(qs) == 677, f"677 questions survived the 50k paste cut (got {len(qs)})")
    miss = missing(r)
    chk(sum(n - h for _, _, n, h in miss) == 385,
        f"385 questions accounted for as missing (got {sum(n - h for _, _, n, h in miss)})")

    # Numbering must restart at 1 in each topic and be gapless - the way a
    # wrapped line gets swallowed is by breaking this.
    from itertools import groupby
    broken = []
    for (b, t), grp in groupby(qs, key=lambda q: (q["branch"], q["topic"])):
        ns = [q["n"] for q in grp]
        if ns != list(range(1, len(ns) + 1)):
            broken.append((b, t, ns[:5]))
    chk(not broken, f"numbering gapless within every topic ({broken[:2]})")

    chk(all(len(q["question"]) > 15 for q in qs),
        "no question truncated to a stub")
    chk(all(q["question"][0].isupper() or q["question"][0].isdigit()
            for q in qs), "no question starts mid-sentence")
    chk(not any("â€" in q["question"] for q in qs),
        "mojibake cleaned out")

    # A wrapped question really was rejoined, not left in two pieces.
    joined = [q for q in qs if q["question"].endswith("temperature.")
              and "8 bar" in q["question"]]
    chk(bool(joined), "a line-wrapped question was rejoined into one")

    print(f"\nqbank_parse.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


def main() -> int:
    if "--selftest" in sys.argv:
        return _selftest()
    r = parse()
    qs = r["questions"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(qs, fh, indent=1, ensure_ascii=False)
    from collections import Counter
    c = Counter(q["branch"] for q in qs)
    print(f"  wrote {len(qs)} questions -> {OUT}")
    for b in BRANCHES:
        ts = sorted({q["topic"] for q in qs if q["branch"] == b})
        print(f"    {b:45s} {c[b]:4d}  ({len(ts)} topics)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
