#!/usr/bin/env python3
"""question_bank.py - EngineAI against the supplied Engineering Question Bank.

The user's own corpus, not mine. 1062 questions across 6 branches and 48
subjects, of which 677 survived the paste limit and are here; the rest arrive
as data/qbank_extra_*.txt and need no code change.

WHY THIS IS A HARDER TEST THAN definitions_battery.py. My 137 definitions were
written by me, so they lean toward what I knew the system could reach. These
were written by someone else, they are phrased as an examiner phrases them
("Derive...", "State and prove...", "Compare..."), and whole subjects here -
Theory of Computation, Database Management, Surveying, Transportation - are
fields the law library has never had a single entry for. If coverage collapses
on those, that is the honest finding and the useful one, because it names what
to build next.

WHAT IS SCORED. Each question is scored by what EngineAI DOES with it, on the
same scale as definitions_battery so the two numbers are comparable:

  EXPLAINED   substantive engineering content in the answer
  COMPUTED    routed to a solver - right for a calculation, wrong for "explain"
  DEFLECTED   no substance, or asked for inputs it did not need
  GAP         honestly named as out of scope
  REFUSED     declined

WHAT IS NOT SCORED, and why. This does NOT mark answers right or wrong. There
is no answer key in the supplied document - it is "questions only" by its own
note - so any correctness number would be me grading my own homework against
my own recollection. correctness_battery.py does grading, deliberately over a
small set of facts with independently checkable values. Conflating the two
would produce a big impressive percentage that means nothing. This measures
REACH: how much of the corpus the system engages with at all, and which
subjects it is blind to.

RUNNING IT. A 7B model answers in about 20 s, so the full 677 is roughly four
hours. Results are appended to data/qbank_results.jsonl as they arrive and the
run resumes from there, so it can be interrupted, and a partial run still
reports honestly over what it has actually done.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.join(HERE, "data", "question_bank.json")
RESULTS = os.path.join(HERE, "data", "qbank_results.jsonl")

EXPLAINED, COMPUTED, DEFLECTED, GAP, REFUSED = (
    "EXPLAINED", "COMPUTED", "DEFLECTED", "GAP", "REFUSED")
OUTCOMES = (EXPLAINED, COMPUTED, DEFLECTED, GAP, REFUSED)


def load_bank(path: str = BANK) -> list:
    if not os.path.exists(path):
        import qbank_parse
        qbank_parse.main()
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def stratified(qs: list, n: int, seed: int = 7) -> list:
    """n questions spread across every subject, not n from the front.

    Sampling the front means sampling Thermodynamics, which is the library's
    strongest area - the number would come out flattering and mean nothing.
    Every subject contributes in proportion, and every subject contributes at
    least one so no subject can hide.
    """
    if n >= len(qs):
        return list(qs)
    rng = random.Random(seed)
    by_topic = defaultdict(list)
    for q in qs:
        by_topic[(q["branch"], q["topic"])].append(q)

    picked, keys = [], sorted(by_topic)
    for k in keys:                       # floor: one from each subject
        picked.append(rng.choice(by_topic[k]))
    remaining = [q for q in qs if q not in picked]
    rng.shuffle(remaining)
    picked += remaining[:max(0, n - len(picked))]
    picked.sort(key=lambda q: q["id"])
    return picked[:n]


def classify(r: dict) -> str:
    """Outcome from the response's own basis label.

    Length thresholds are the same as definitions_battery so the two batteries
    produce comparable numbers; a lower bar here would make this look better
    for no reason other than that I chose the bar.
    """
    import chat
    basis = r.get("basis")
    ans = str(r.get("answer") or "")

    if basis == getattr(chat, "EXPLAINED", "EXPLAINED"):
        return EXPLAINED if len(ans) > 80 else DEFLECTED
    if basis == chat.COMPUTED:
        return COMPUTED
    if basis == chat.REFERENCE:
        return EXPLAINED
    if r.get("is_engineering") and basis == chat.MODEL_REQUIRED:
        return GAP
    if basis == chat.CONVERSATIONAL:
        return EXPLAINED if len(ans) > 120 else DEFLECTED
    if basis == chat.CLARIFY:
        return DEFLECTED
    return EXPLAINED if len(ans) > 120 else DEFLECTED


def _grounding(q: str) -> dict:
    """Did the verified library have anything to say about this question?

    Recorded separately from the outcome because it is the actionable half: an
    EXPLAINED answer with zero retrieved laws is the model recalling from
    weights, which is exactly the failure mode the retrieval layer exists to
    remove. Per-subject retrieval hit rate is the build list for the library.
    """
    try:
        import understand
        hits = understand.retrieve(q, k=3)
        return {"retrieved": len(hits),
                "top": (hits[0].get("name") if hits and isinstance(hits[0], dict)
                        else (str(hits[0])[:60] if hits else None))}
    except Exception as e:
        return {"retrieved": 0, "top": None, "retrieval_error": f"{type(e).__name__}"}


def answer_one(q: dict, model_fn=None) -> dict:
    import chat
    t0 = time.time()
    try:
        r = chat.respond(q["question"], model_fn=model_fn)
        err = None
    except Exception as e:
        r, err = {}, f"{type(e).__name__}: {e}"
    dt = time.time() - t0

    out = {"id": q["id"], "branch": q["branch"], "topic": q["topic"],
           "n": q["n"], "question": q["question"],
           "outcome": DEFLECTED if err else classify(r),
           "basis": r.get("basis"), "seconds": round(dt, 2),
           "answer_chars": len(str(r.get("answer") or "")),
           "answer": str(r.get("answer") or "")[:400]}
    if err:
        out["error"] = err
    out.update(_grounding(q["question"]))
    return out


def done_ids(path: str = RESULTS) -> set:
    if not os.path.exists(path):
        return set()
    ids = set()
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            try:
                ids.add(json.loads(ln)["id"])
            except Exception:
                continue          # a half-written line from a killed run
    return ids


def load_results(path: str = RESULTS) -> list:
    """Results, deduplicated by question id, first answer wins.

    Two runners briefly shared this file once. Append-only survived it, but a
    report that counts the same question twice reports a number for a corpus
    that does not exist, so the dedupe lives here rather than in a cleanup.
    """
    if not os.path.exists(path):
        return []
    rows, seen = [], set()
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            try:
                r = json.loads(ln)
            except Exception:
                continue          # a half-written line from a killed run
            if r.get("id") in seen:
                continue
            seen.add(r.get("id"))
            rows.append(r)
    return rows


def run(questions: list, model_fn=None, results_path: str = RESULTS,
        resume: bool = True, progress: bool = True) -> list:
    seen = done_ids(results_path) if resume else set()
    todo = [q for q in questions if q["id"] not in seen]
    if progress and seen:
        print(f"  resuming: {len(seen)} already answered, {len(todo)} to go")

    os.makedirs(os.path.dirname(results_path), exist_ok=True)
    # Append and flush per question. A four-hour run that loses everything to
    # a power cut has happened here before.
    with open(results_path, "a", encoding="utf-8") as fh:
        for i, q in enumerate(todo, 1):
            row = answer_one(q, model_fn=model_fn)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            if progress and (i % 10 == 0 or i == len(todo)):
                print(f"    {i}/{len(todo)}  last: {row['outcome']:9s} "
                      f"{row['seconds']:5.1f}s  {q['topic'][:28]}")
    return load_results(results_path)


def report(rows: list, bank: list | None = None) -> dict:
    if not rows:
        print("  no results yet")
        return {}
    n = len(rows)
    counts = Counter(r["outcome"] for r in rows)
    by_branch = defaultdict(Counter)
    by_topic = defaultdict(Counter)
    ground = defaultdict(list)
    for r in rows:
        by_branch[r["branch"]][r["outcome"]] += 1
        by_topic[(r["branch"], r["topic"])][r["outcome"]] += 1
        ground[(r["branch"], r["topic"])].append(bool(r.get("retrieved")))

    print(f"\n  {n} questions answered across {len(by_topic)} subjects\n")
    for k in OUTCOMES:
        print(f"    {k:10s} {counts[k]:4d}  ({counts[k]/n*100:5.1f}%)")

    print("\n  by branch:")
    for b in sorted(by_branch):
        c = by_branch[b]
        tot = sum(c.values())
        print(f"    {b:42s} {c[EXPLAINED]:4d}/{tot:<4d} explained "
              f"({c[EXPLAINED]/tot*100:5.1f}%)")

    ranked = sorted(by_topic.items(),
                    key=lambda kv: kv[1][EXPLAINED] / max(sum(kv[1].values()), 1))
    print("\n  weakest subjects (these are the build list):")
    for (b, t), c in ranked[:10]:
        tot = sum(c.values())
        hit = sum(ground[(b, t)]) / max(len(ground[(b, t)]), 1)
        print(f"    {t[:38]:38s} {c[EXPLAINED]:3d}/{tot:<3d} explained,"
              f" library hit {hit*100:3.0f}%   [{b.split()[0]}]")

    hits = sum(1 for r in rows if r.get("retrieved"))
    secs = sum(r.get("seconds", 0) for r in rows)
    print(f"\n  verified library reached {hits}/{n} questions "
          f"({hits/n*100:.0f}%) - the rest were answered without a source to read")
    print(f"  wall {secs/60:.1f} min, {secs/n:.1f} s per question")

    if bank:
        pct = n / len(bank) * 100
        print(f"  progress: {n}/{len(bank)} of the supplied corpus ({pct:.0f}%)")
    return {"n": n, "counts": dict(counts),
            "explained": counts[EXPLAINED],
            "explained_rate": counts[EXPLAINED] / n,
            "library_hit_rate": hits / n,
            "by_branch": {k: dict(v) for k, v in by_branch.items()},
            "by_topic": {f"{k[0]}|{k[1]}": dict(v) for k, v in by_topic.items()}}


# --------------------------------------------------------------------------
def _selftest() -> int:
    """Tests the HARNESS, with a stub in place of the model.

    Deliberately not a model test. Running 677 real questions takes four hours,
    and a selftest that calls the live model would make `verify_all.sh` depend
    on Ollama being warm - which is the exact cold-start trap that has already
    reported a false 3% once and a false 11% once.
    """
    import tempfile
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    bank = load_bank()
    chk(len(bank) >= 677, f"bank loaded ({len(bank)} questions)")
    chk(len({(q['branch'], q['topic']) for q in bank}) >= 27,
        "subjects present")

    # --- stratification
    s = stratified(bank, 60)
    chk(len(s) == 60, f"stratified sample is the size asked for ({len(s)})")
    subj = {(q["branch"], q["topic"]) for q in s}
    chk(len(subj) == len({(q["branch"], q["topic"]) for q in bank}),
        f"every subject represented in the sample ({len(subj)})")
    chk(stratified(bank, 60)[0]["id"] == s[0]["id"], "sampling is reproducible")
    chk(len(stratified(bank, 10_000)) == len(bank), "n larger than the bank is the bank")
    chk(len({q["id"] for q in s}) == len(s), "no duplicate questions in a sample")

    # --- classification maps every basis to a definite outcome
    import chat
    cases = [
        ({"basis": chat.EXPLAINED, "answer": "x" * 200}, EXPLAINED),
        ({"basis": chat.EXPLAINED, "answer": "short"}, DEFLECTED),
        ({"basis": chat.COMPUTED, "answer": "42"}, COMPUTED),
        ({"basis": chat.CLARIFY, "answer": "y" * 300}, DEFLECTED),
        ({"basis": chat.MODEL_REQUIRED, "answer": "", "is_engineering": True}, GAP),
    ]
    for r, want in cases:
        got = classify(r)
        chk(got == want, f"basis {r['basis']} -> {want} (got {got})")
    chk(classify({"basis": "SOMETHING_NEW", "answer": "z" * 400}) in OUTCOMES,
        "an unknown basis still lands on a definite outcome")

    # --- checkpoint + resume, with a stub so it is fast and offline
    tmp = os.path.join(tempfile.mkdtemp(), "r.jsonl")
    two = bank[:2]

    calls = []

    def fake_respond(text, model_fn=None, lang=None):
        calls.append(text)
        return {"basis": chat.EXPLAINED, "answer": "grounded explanation. " * 12}

    real = chat.respond
    chat.respond = fake_respond
    try:
        run(two, results_path=tmp, progress=False)
        chk(len(calls) == 2, f"answered both ({len(calls)})")
        run(two, results_path=tmp, progress=False)
        chk(len(calls) == 2, "resume skipped work already done, did not redo it")
        rows = load_results(tmp)
        chk(len(rows) == 2, f"one row per question, no duplicates ({len(rows)})")
        chk(all(r["outcome"] == EXPLAINED for r in rows), "stub scored as explained")
        chk(all("seconds" in r and "branch" in r for r in rows),
            "each row carries its timing and its subject")

        # A crash mid-write must not poison the resume set.
        with open(tmp, "a", encoding="utf-8") as fh:
            fh.write('{"id": 3, "outcome": "EXPL')
        chk(done_ids(tmp) == {1, 2}, "a truncated line is ignored, not crashed on")
        chk(len(load_results(tmp)) == 2, "a truncated line is not counted as a result")

        # An exception from chat must be recorded, never lost.
        def boom(text, model_fn=None, lang=None):
            raise RuntimeError("model down")
        chat.respond = boom
        tmp2 = os.path.join(os.path.dirname(tmp), "r2.jsonl")
        run(bank[5:6], results_path=tmp2, progress=False)
        rows2 = load_results(tmp2)
        chk(len(rows2) == 1 and "error" in rows2[0],
            "a model failure is recorded as a row with the error, not a silent skip")
        chk(rows2[0]["outcome"] == DEFLECTED,
            "a failed question is never scored as explained")
    finally:
        chat.respond = real

    r = report(load_results(tmp), bank=bank)
    chk(r["n"] == 2 and r["explained"] == 2, "report totals match the rows")

    print(f"\nquestion_bank.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--n", type=int, default=0,
                    help="stratified sample size (default: report only)")
    ap.add_argument("--full", action="store_true", help="every question")
    ap.add_argument("--branch", default=None, help="restrict to one branch")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore existing results and start over")
    ap.add_argument("--results", default=RESULTS)
    a = ap.parse_args()

    if a.selftest:
        return _selftest()

    bank = load_bank()
    if a.branch:
        bank = [q for q in bank if a.branch.lower() in q["branch"].lower()]

    if a.full:
        todo = bank
    elif a.n:
        todo = stratified(bank, a.n)
    else:
        report(load_results(a.results), bank=bank)
        return 0

    if a.fresh and os.path.exists(a.results):
        os.remove(a.results)
    print(f"  running {len(todo)} questions "
          f"(model {os.environ.get('ENGINEAI_MODEL', 'default')})")
    rows = run(todo, results_path=a.results)
    ids = {q["id"] for q in todo}
    report([r for r in rows if r["id"] in ids], bank=bank)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
