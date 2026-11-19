#!/usr/bin/env python3
"""sign_off.py - the command a named engineer runs to record a review.

    python sign_off.py --list
    python sign_off.py --show verify_all
    python sign_off.py --sign \
        --name "Fuad Ashraf" --qualification "BSc Eng" --org "EngineAI" \
        --scope "verify_all.sh full gate" --verdict ACCEPTED \
        --comment "ran the gate myself and hand-checked five numbers"

WHAT SIGNING MEANS HERE. It binds a named person to a SPECIFIC state of the
software - the hash of the current gate result - and not to the software in
general. If a single test changes afterwards, the hash changes and the
signature no longer matches, which is the property that makes it worth
anything. A signature you can carry forward onto later work is not a
signature, it is a decoration.

WHAT IT DOES NOT MEAN. It is not certification, it does not make any output
certified, and it does not transfer liability. It records that a named person
with a stated qualification looked at a stated scope and reached a stated
verdict. REJECTED is a first-class outcome and is kept in the record.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

import field_record

HERE = Path(__file__).parent


def gate_hash(verbose: bool = True) -> tuple:
    """Run the full gate and hash its result.

    The hash covers the OUTCOME, not the timestamp, so re-running an unchanged
    tree reproduces it - otherwise nobody could check that what they signed is
    what is there now.
    """
    if verbose:
        print("running verify_all.sh - this takes several minutes ...")
    r = subprocess.run(["bash", str(HERE / "verify_all.sh")],
                       capture_output=True, text=True, cwd=HERE)
    lines = [ln for ln in r.stdout.splitlines()
             if ln.startswith("==>") or "passed," in ln or "PASS" in ln]
    body = "\n".join(lines)
    return hashlib.sha256(body.encode()).hexdigest(), r.returncode == 0, body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show recorded signatures")
    ap.add_argument("--show", metavar="SCOPE", help="show signatures for a scope")
    ap.add_argument("--sign", action="store_true")
    ap.add_argument("--name"); ap.add_argument("--qualification")
    ap.add_argument("--org", default="")
    ap.add_argument("--scope", default="verify_all.sh full gate")
    ap.add_argument("--verdict", default="ACCEPTED",
                    choices=["ACCEPTED", "ACCEPTED_WITH_COMMENT", "REJECTED"])
    ap.add_argument("--comment", default="")
    ap.add_argument("--identity-verified", action="store_true",
                    help="only if identity was checked by an external party")
    ap.add_argument("--hash", help="sign a specific hash instead of re-running the gate")
    a = ap.parse_args()

    if a.list or a.show:
        rows = field_record.signatures()
        if a.show:
            rows = [r for r in rows if a.show.lower() in r.get("scope_reviewed", "").lower()]
        if not rows:
            print("no signatures recorded yet")
            print("nobody has signed off on this software. The NASA-STD-7009A "
                  "people_qualifications factor stays refused until someone does.")
            return 0
        for r in rows:
            print(f"  {r['when']}  {r['verdict']}")
            print(f"    {r['name']}, {r['qualification']}"
                  + (f", {r['organisation']}" if r.get("organisation") else ""))
            print(f"    scope  {r['scope_reviewed']}")
            print(f"    result {r['result_hash'][:32]}")
            print(f"    identity externally verified: {r.get('identity_verified')}")
            if r.get("comment"):
                print(f"    comment {r['comment']}")
        ci = field_record.credibility_inputs()
        print(f"\n  people_qualifications would score "
              f"{ci['people_qualifications_level']}  ({ci['note']})")
        return 0

    if not a.sign:
        ap.print_help()
        return 0
    if not a.name or not a.qualification:
        print("ERROR: --name and --qualification are both required.\n"
              "An anonymous or unqualified sign-off is not a sign-off, and "
              "recording one would let the credibility score rise on nothing.")
        return 2

    if a.hash:
        h, passed = a.hash, True
    else:
        h, passed, body = gate_hash()
        print(f"\ngate {'PASSED' if passed else 'FAILED'}   result hash {h[:32]}")
        if not passed and a.verdict.startswith("ACCEPTED"):
            print("\nREFUSING: the gate did not pass, so an ACCEPTED signature "
                  "would be recording approval of a failing build.\n"
                  "Fix the failure, or sign with --verdict REJECTED.")
            return 3

    s = field_record.sign(a.name, a.qualification, a.org, h, a.scope,
                          a.verdict, a.comment, a.identity_verified)
    print(f"\nrecorded: {s.name} ({s.qualification}) -> {s.verdict}")
    print(f"  bound to result hash {h[:32]}")
    print(f"  digest {s.digest()}")
    print("\nThis signature applies to THIS state of the software. Change a test "
          "and the hash changes and this signature stops matching - which is the "
          "point of it.")
    ci = field_record.credibility_inputs()
    print(f"\npeople_qualifications now scores {ci['people_qualifications_level']}"
          + ("" if a.identity_verified else
             " - level 3 needs identity verified by an external party"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
