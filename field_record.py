#!/usr/bin/env python3
"""field_record.py - the two credibility factors software cannot award itself.

NASA-STD-7009A caps EngineAI at LEVEL 0 on two factors, and both caps are
correct:

  use_history            zero recorded runs. A system nobody has used has no
                         track record, and no amount of internal testing
                         substitutes for one.
  people_qualifications  software cannot certify the competence of its own
                         authors or reviewers.

Neither can be fixed by writing more code. They can only be fixed by TIME and
by a NAMED PERSON, and what code can do is make both accumulate honestly
instead of staying at zero forever.

  LOG    every research run is appended to an immutable-ish ledger: what was
         asked, what came back, which attacks survived, and a hash chain so a
         later edit to an earlier entry is detectable. Use history then grows
         from evidence rather than from a claim.

  SIGN   a named, qualified engineer records a review against a specific
         result hash. The signature binds to THAT result: change the result
         and the signature stops matching, which is the entire point.

WHAT THIS DOES NOT DO. It does not verify that a signatory is who they say they
are, and it says so in the record. That needs an institution, not a file.
Recording an unverified name is still worth more than recording nothing,
provided nobody pretends otherwise - so every signature carries
identity_verified: false unless an external check is supplied.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

__all__ = ["log_run", "history", "sign", "signatures", "credibility_inputs",
           "RunRecord", "Signature"]

LEDGER = Path(__file__).parent / "research_output" / "field_record.jsonl"
SIGNATURES = Path(__file__).parent / "research_output" / "signatures.jsonl"

GENESIS = "0" * 64


@dataclass
class RunRecord:
    when: str
    kind: str
    question: str
    verdict: str
    attacks: int
    survived: int
    receipt: str
    prev_hash: str
    entry_hash: str = ""

    def compute_hash(self) -> str:
        payload = (f"{self.when}|{self.kind}|{self.question}|{self.verdict}|"
                   f"{self.attacks}|{self.survived}|{self.receipt}|{self.prev_hash}")
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class Signature:
    when: str
    name: str
    qualification: str
    organisation: str
    result_hash: str
    scope_reviewed: str
    verdict: str                      # ACCEPTED | ACCEPTED_WITH_COMMENT | REJECTED
    comment: str = ""
    identity_verified: bool = False

    def digest(self) -> str:
        return hashlib.sha256(
            f"{self.name}|{self.result_hash}|{self.verdict}|{self.when}".encode()
        ).hexdigest()[:16]


def _append(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, sort_keys=True) + "\n")


def _read(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# --------------------------------------------------------------------------
# use history
# --------------------------------------------------------------------------

def log_run(kind: str, question: str, verdict: str, attacks: int,
            survived: int, receipt: str = "", ledger: Path | None = None) -> RunRecord:
    """Append one run to the hash-chained ledger.

    The chain is not tamper-PROOF - anyone with write access can rebuild it
    from scratch. It is tamper-EVIDENT, which is the achievable property for a
    local file, and it is enough to catch a quiet edit of one old entry.
    """
    p = ledger or LEDGER
    prev = _read(p)
    prev_hash = prev[-1]["entry_hash"] if prev else GENESIS
    r = RunRecord(time.strftime("%Y-%m-%dT%H:%M:%S"), kind, question[:200],
                  verdict, int(attacks), int(survived), receipt, prev_hash)
    r.entry_hash = r.compute_hash()
    _append(p, asdict(r))
    return r


def history(ledger: Path | None = None) -> dict:
    """Read the ledger back and check the chain."""
    rows = _read(ledger or LEDGER)
    broken = []
    prev = GENESIS
    for i, row in enumerate(rows):
        rec = RunRecord(**{k: row.get(k) for k in
                           ("when", "kind", "question", "verdict", "attacks",
                            "survived", "receipt", "prev_hash", "entry_hash")})
        if rec.prev_hash != prev:
            broken.append(f"entry {i}: chain break (prev_hash does not match)")
        if rec.compute_hash() != rec.entry_hash:
            broken.append(f"entry {i}: content does not match its hash - edited")
        prev = rec.entry_hash or GENESIS
    verdicts: dict = {}
    for row in rows:
        verdicts[row.get("verdict", "?")] = verdicts.get(row.get("verdict", "?"), 0) + 1
    return {"n_runs": len(rows), "intact": not broken, "problems": broken,
            "verdicts": verdicts,
            "first": rows[0]["when"] if rows else None,
            "last": rows[-1]["when"] if rows else None}


# --------------------------------------------------------------------------
# sign-off
# --------------------------------------------------------------------------

def sign(name: str, qualification: str, organisation: str, result_hash: str,
         scope_reviewed: str, verdict: str, comment: str = "",
         identity_verified: bool = False, store: Path | None = None) -> Signature:
    """Record a named engineer's review of a specific result.

    Refuses an empty name or an empty qualification. An anonymous sign-off is
    not a sign-off, and recording one would let the credibility score rise on
    nothing.
    """
    if not name.strip():
        raise ValueError("a signature needs a name - anonymous sign-off is not sign-off")
    if not qualification.strip():
        raise ValueError("a signature needs a stated qualification")
    if verdict not in ("ACCEPTED", "ACCEPTED_WITH_COMMENT", "REJECTED"):
        raise ValueError("verdict must be ACCEPTED, ACCEPTED_WITH_COMMENT or REJECTED")
    if not result_hash.strip():
        raise ValueError("a signature must bind to a specific result hash")
    s = Signature(time.strftime("%Y-%m-%dT%H:%M:%S"), name.strip(),
                  qualification.strip(), organisation.strip(), result_hash.strip(),
                  scope_reviewed.strip(), verdict, comment.strip(), identity_verified)
    _append(store or SIGNATURES, asdict(s) | {"digest": s.digest()})
    return s


def signatures(result_hash: str | None = None, store: Path | None = None) -> list:
    rows = _read(store or SIGNATURES)
    if result_hash:
        rows = [r for r in rows if r.get("result_hash") == result_hash]
    return rows


def credibility_inputs(ledger: Path | None = None,
                       store: Path | None = None) -> dict:
    """The two numbers research_credibility needs, computed from evidence."""
    h = history(ledger)
    sigs = _read(store or SIGNATURES)
    accepted = [s for s in sigs if s.get("verdict", "").startswith("ACCEPTED")]
    verified = [s for s in accepted if s.get("identity_verified")]
    return {
        "use_history_runs": h["n_runs"] if h["intact"] else 0,
        "ledger_intact": h["intact"],
        "n_signatures": len(sigs),
        "n_accepted": len(accepted),
        "n_identity_verified": len(verified),
        "people_qualifications_level": (
            3 if verified else 2 if accepted else 0),
        "note": ("people_qualifications is scored from RECORDED SIGN-OFF, never "
                 "self-assessed. Level 3 needs a signatory whose identity was "
                 "checked externally; without that check the ceiling is 2, "
                 "however senior the name."),
    }


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def _selftest() -> int:
    import tempfile
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / "ledger.jsonl"
        sig = Path(td) / "sigs.jsonl"

        print("== use history accumulates ==")
        chk(history(led)["n_runs"] == 0, "an empty ledger reports zero runs")
        log_run("law_discovery", "what law gives I?", "DISCOVERED", 6, 6, "abc", led)
        log_run("law_discovery", "overshoot?", "REJECTED", 5, 2, "def", led)
        log_run("research", "CO2 pareto", "DISCOVERED", 2, 2, "ghi", led)
        h = history(led)
        chk(h["n_runs"] == 3, f"three runs recorded ({h['n_runs']})")
        chk(h["intact"], "the chain verifies")
        chk(h["verdicts"] == {"DISCOVERED": 2, "REJECTED": 1},
            f"verdicts counted, failures included ({h['verdicts']})")

        print("== an edited entry is detectable ==")
        rows = led.read_text().splitlines()
        bad = json.loads(rows[0])
        bad["verdict"] = "DISCOVERED"          # was already, so change something real
        bad["survived"] = 99
        rows[0] = json.dumps(bad, sort_keys=True)
        led.write_text("\n".join(rows) + "\n")
        h2 = history(led)
        chk(not h2["intact"], "a quietly edited entry breaks the chain")
        chk(any("edited" in p or "chain break" in p for p in h2["problems"]),
            f"and is named ({h2['problems'][:1]})")
        chk(credibility_inputs(led, sig)["use_history_runs"] == 0,
            "a broken ledger contributes ZERO use history, not three")

        print("== sign-off ==")
        led2 = Path(td) / "l2.jsonl"
        log_run("research", "q", "DISCOVERED", 3, 3, "hash1", led2)
        ci = credibility_inputs(led2, sig)
        chk(ci["people_qualifications_level"] == 0, "no signature scores 0")

        for bad_args in (("", "CEng", "X", "h", "s", "ACCEPTED"),
                         ("A Name", "", "X", "h", "s", "ACCEPTED"),
                         ("A Name", "CEng", "X", "", "s", "ACCEPTED"),
                         ("A Name", "CEng", "X", "h", "s", "LOOKS_FINE")):
            try:
                sign(*bad_args, store=sig)
                refused = False
            except ValueError:
                refused = True
            chk(refused, f"refused a bad signature {bad_args[:2]}")

        s = sign("R. Patel", "CEng MIStructE", "Example Consulting",
                 "hash1", "the EN 1993 beam results only", "ACCEPTED_WITH_COMMENT",
                 "checked three worked examples by hand", store=sig)
        chk(len(signatures("hash1", sig)) == 1, "the signature is stored")
        chk(not s.identity_verified, "identity is NOT claimed as verified by default")
        ci2 = credibility_inputs(led2, sig)
        chk(ci2["people_qualifications_level"] == 2,
            f"an unverified named sign-off reaches 2, not 3 ({ci2['people_qualifications_level']})")

        sign("Dr K. Osei", "CEng FIMechE", "University", "hash1",
             "the thermal results", "ACCEPTED", identity_verified=True, store=sig)
        chk(credibility_inputs(led2, sig)["people_qualifications_level"] == 3,
            "an externally verified identity reaches 3")

        print("== a signature binds to its result ==")
        chk(signatures("hash_other", sig) == [],
            "a signature does not carry over to a different result")
        s2 = Signature(s.when, s.name, s.qualification, s.organisation,
                       "different_hash", s.scope_reviewed, s.verdict)
        chk(s2.digest() != s.digest(), "changing the result changes the digest")

        print("== a rejection is recorded too ==")
        sign("A Reviewer", "CEng", "Org", "hash1", "everything", "REJECTED",
             "the CO2 factors are not sourced", store=sig)
        rows = signatures("hash1", sig)
        chk(any(r["verdict"] == "REJECTED" for r in rows),
            "a rejection is kept in the record, not dropped")
        ci3 = credibility_inputs(led2, sig)
        chk(ci3["n_signatures"] == 3 and ci3["n_accepted"] == 2,
            f"accepted and total are counted separately ({ci3['n_accepted']}/{ci3['n_signatures']})")

    print(f"\nfield_record.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    h = history()
    print(f"  runs recorded : {h['n_runs']}  (chain {'intact' if h['intact'] else 'BROKEN'})")
    print(f"  verdicts      : {h['verdicts']}")
    ci = credibility_inputs()
    print(f"  signatures    : {ci['n_signatures']} "
          f"({ci['n_accepted']} accepted, {ci['n_identity_verified']} identity-verified)")
    print(f"  people_qual   : level {ci['people_qualifications_level']}")
