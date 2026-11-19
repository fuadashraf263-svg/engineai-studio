#!/usr/bin/env python3
"""
test_browser.py - drive the real UI in a real browser.

WHY
---
Every claim about this UI so far has been "the served HTML contains the
string X". That proves the markup shipped, not that anything works. A
reviewer put it plainly: Playwright is what proves that buttons, uploads,
face selection and results actually function in the browser.

So this launches chromium against the running server and checks what a person
would see: the app loads, WebGL gets triangles, a slider rebuild changes the
geometry, a command comes back with an answer, the queue panel reports the
worker, and an estimate never sits where a verified result should be.

It needs the server up:
    bash _start_wb.sh
    ./run_in_eng.sh python test_browser.py
"""

from __future__ import annotations

import os
import subprocess
import sys

URL = os.environ.get("ENGINEAI_URL", "http://127.0.0.1:8100/")


def main() -> int:
    from playwright.sync_api import sync_playwright

    ok = True

    def chk(cond, label, extra=""):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label
              + (f"   {extra}" if extra else ""))
        ok = ok and bool(cond)

    print("browser test against " + URL)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-gl=swiftshader",
                                          "--enable-unsafe-swiftshader"])
        # Sec-Fetch-* are what the loopback auto sign-in keys off, and a real
        # navigation sends them. If they were missing this would land on the
        # sign-in screen, which is itself worth knowing.
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text)
                if m.type == "error" else None)

        page.goto(URL, wait_until="networkidle", timeout=60000)

        print(" the app loads and signs itself in")
        chk("Workbench" in page.title(), f"title is {page.title()!r}")
        chk(not page.locator("#fatal").is_visible(),
            "no fatal panel: it signed in without being asked")
        for pane in ("Command", "Parametric CAD", "Script editor",
                     "Pick faces", "Stress field"):
            chk(page.locator(f"text={pane}").first.is_visible(),
                f"the {pane} tab is visible")

        print(" the queue panel reports real state")
        page.wait_for_timeout(1500)
        q = page.locator("#queuebox").inner_text()
        chk(("worker" in q) or ("broker" in q),
            f"queue panel says something concrete ({q.splitlines()[0][:44]!r})")

        print(" parametric CAD: a slider actually rebuilds the solid")
        page.locator("button[data-p='cad']").click()
        page.wait_for_selector("#params input[type=range]", timeout=30000)
        page.wait_for_function(
            "() => document.querySelector('#hud') && "
            "document.querySelector('#hud').textContent.includes('triangles')",
            timeout=60000)
        hud1 = page.locator("#hud").inner_text()
        chk("triangles" in hud1, f"the viewport has geometry ({hud1[:40]!r})")
        chk(page.locator("#vchk").inner_text().lower().startswith("volume"),
            f"and a live volume check ({page.locator('#vchk').inner_text()[:34]!r})")

        first = page.locator("#params input[type=range]").first
        before = page.locator("#hud").inner_text()
        first.evaluate(
            "el => { el.value = String(Number(el.max) * 0.8);"
            "        el.dispatchEvent(new Event('input', {bubbles:true})); }")
        page.wait_for_timeout(3500)
        after = page.locator("#hud").inner_text()
        chk(after != before,
            f"moving a slider changed the model ({before[-22:]!r} -> "
            f"{after[-22:]!r})")

        print(" the two tiers are distinguishable on screen")
        tier = page.locator("#tierbox").inner_text()
        chk("ESTIMATE" in tier, "the fast tier is labelled ESTIMATE")
        chk("no verified run" in tier.lower() or "VERIFIED" in tier,
            "and it states whether a verified run exists yet")

        print(" the command pane answers")
        page.locator("button[data-p='chat']").click()
        page.locator("#cmd").fill("otto cycle with a compression ratio of 11")
        page.locator("#cmdgo").click()
        page.wait_for_function(
            "() => document.querySelectorAll('#thread .msg').length >= 2",
            timeout=90000)
        page.wait_for_timeout(9000)
        thread = page.locator("#thread").inner_text()
        chk("otto" in thread.lower(), "the command was echoed")
        chk("efficien" in thread.lower() or "cycle" in thread.lower(),
            f"and answered ({thread[-90:].strip()[:70]!r})")

        print(" a question it must ask about")
        page.locator("#cmd").fill("how much will this beam deflect")
        page.locator("#cmdgo").click()
        page.wait_for_selector("text=What load does it carry", timeout=60000)
        chk(True, "an underspecified request asks for the load instead of "
                  "inventing one")

        print(" nothing threw in the browser")
        real = [e for e in errors if "favicon" not in e.lower()]
        chk(not real, f"{len(real)} javascript error(s)",
            "; ".join(real[:2])[:110])

        browser.close()

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
