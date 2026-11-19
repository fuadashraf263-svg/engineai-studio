# What I Verified, What I Only Assumed

You asked the right question. Here is the honest split, item by item.

**The rule I used:** "verified" means I executed it and checked the *result was correct*, not that the code ran without error. A file existing is not evidence it is right.

---

## 1. Genuinely verified — I ran it and checked the answer

| Thing | How it was verified |
|---|---|
| Textbook PDFs readable | Opened all 26 with PyMuPDF, sampled pages, counted text. Found Roark's hollow (927 blank pages) |
| BM25 retrieval | Ran real queries, read the returned pages, confirmed the right chapter came back |
| Retrieval rejection | Reproduced your Docker/Coder-Proprement failures with stand-in books; confirmed they now return nothing |
| Guard equivalence | 16 cases including your model's actual answers, plus algebraic rearrangements and LaTeX |
| sympify RCE | Wrote the exploit, watched it execute, then confirmed 9 exploit variants blocked and 6 real formulas still parse |
| Security controls | 20/20 — forged token rejected, expired token rejected, rate limit throttles, lockout fires |
| Numeric results | Hand-checked against closed form: cantilever 2.00008e-4 m, Euler 1.64487e7 N, conduction 67500 W, simply-supported 2.00008e-4 m, torsion 3.984e7 Pa |
| CalculiX FEA | Installed CalculiX in my own container and ran it: 0.40% vs PL³/(3EI), 0.57% vs PL/(AE) |
| STL watertightness | Edge-manifold check. **Found `bracket.stl` broken, fixed it** |
| beam_diagram, render_image | **I looked at the images.** Deflection curve correct, L-bracket profile correct from four views |
| CFD analytical model + parser | Centreline = 1.5·Ub, zero at wall, integrates back to Ub; parser round-trips to 3.3e-9 |
| patch_workspace / patch_brain | Ran against copies of your real files, confirmed the edits applied and still parse |

---

## 2. Verified structurally only — the file is valid, the *content* is not checked

This is the category you were right to suspect. I confirmed bytes, headers and part counts. I did **not** confirm the content is correct or even sensible.

| Thing | What I actually checked | What is unverified |
|---|---|---|
| `report.docx` | Valid OOXML, 17 parts | Never opened in Word. Layout, table rendering unknown |
| `calc.xlsx` | Valid OOXML, 9 parts | **The live formula `=B3*B4^3/(3*B5*B6)` was never evaluated.** openpyxl writes formulas, it does not compute them. Excel may show an error |
| `deck.pptx` | Valid OOXML, 40 parts | Never opened in PowerPoint |
| `bracket_drawing.dxf` | 3 DIMENSION entities present | **Never opened in a CAD program.** Dimensions may be misplaced, mis-scaled, or invisible |
| `rc_lowpass.png` | Valid PNG, 3.5 KB | **Never looked at it.** Whether it draws a correct RC low-pass is unverified |
| `engineai_pipeline.png` | Valid PNG | Never looked at it |
| `sandbox_3d.html` | three.js reference, base64 STL decodes to 24 triangles, orbit handlers present | **Never rendered in a browser.** It may not display at all |
| `sensitivity.png` | Valid PNG | Never looked at it. The maths is a simple ratio, but the plot itself is unchecked |
| Report PDFs | 3 pages, 2 images embedded, text extracted correctly | Visual layout never inspected |
| `bracket.step` | Produced on **your** machine, 24.9 KB | Never opened in any CAD system, by me or by you |

---

## 3. Never verified at all — assumed

### 3.1 The one that matters most: the reference citations

`engineai.py` contains 17 closed-form solutions, each tagged with a source like *"Roark's Formulas for Stress and Strain, Table 8.1 case 1a"* or *"Shigley's, eq. 4-43"*.

**The formulas I am confident in** — they are standard, and I checked them numerically against independent calculation.

**The specific table, case and equation numbers I did not verify.** I wrote them from knowledge. I never opened Roark's to confirm that case 1a is the cantilever tip deflection — and I *couldn't* have, because your copy is 927 blank pages. Some of those citations may point at the wrong table or the wrong edition.

This matters more than any other item here, because the guard's entire authority is "this came from a cited reference". If a citation is wrong, the number is still right but the provenance is fiction. **Spot-check them against your real books before you show this to a paying engineer.**

### 3.2 The CFD case has never been executed

Every dictionary in `cfd_validate.py` was written from knowledge of OpenFOAM, not tested. Unverified specifically:

- whether `fvSchemes` / `fvSolution` syntax is right for **OpenFOAM 13** (the .org release renamed things between 11 and 13)
- whether `noSlip`, `momentumTransport` with `model Stokes`, and `physicalProperties` are the correct spellings for your version
- whether `simpleFoam` still exists as a real solver in 13 or is a wrapper around `foamRun`
- whether the solution converges in 2000 iterations
- whether the cell-ordering assumption (x-fastest) holds for your blockMesh build

The analytical target and the parser are verified. **The OpenFOAM half is a hypothesis until you run it.** If it errors, the error text tells us exactly which dictionary is wrong and it's a small fix.

### 3.3 The database migration

`kb.py` adds a `field` column to an existing database via `ALTER TABLE`. I tested this on a **fresh** database. I did **not** test it against a `kb.db` created by the older version — which is exactly what you have, with 70 books in it.

It should work. But back it up first, and that takes one second:

```bash
cp ~/engineai/library/kb.db ~/engineai/library/kb_backup.db
```

### 3.4 Classification accuracy at scale

The engineering/programming classifier was tested on 6 books — 4 real, 2 stand-ins I built. Your library has 70. Borderline cases I expect to get wrong: *"instrumentation course book"*, Boylestad *Electronic Devices*, the differential equations texts, *"A Genetic-Algorithm-Based Approach…"*. **Run `python kb.py stats` after reclassifying and read the list.** Anything mislabelled `programming` silently vanishes from search.

### 3.5 The relevance floor is a guess

`FLOOR = 9.0` was chosen from about ten queries. It is not calibrated. Too high and real questions get "I don't know"; too low and nonsense creeps back. Tune it with `ENGINEAI_KB_FLOOR=7 python kb.py search "..."` on questions you care about.

### 3.6 `brain_local.py` with an actual model

The identity path is tested (returns EngineAI, $0.000, never says Claude). The **model path is untested** — there is no ollama in my sandbox. Whether qwen produces sensible engineering prose through my system prompt is unknown until you run it.

### 3.7 Deliberately not done, and I said so

- `owner.py` auth is **not wired into your three servers**. They still accept any request
- No TLS
- No unit checking (`pint` installed, unused)
- `workspace_server.py` — never run by me; you ran it

---

## 4. Verified by your run, not by me

Your machine confirmed things I couldn't: CadQuery producing a real STEP file, all 18 generators executing, 36/36 Python capabilities, the guard passing on your hardware, and the tube generating with 0.000% re-measurement error.

Conversely, **your run disproved two of my claims** — that the guard had no false positives (it blocked two correct answers) and that retrieval was fixed (it cited Docker for a CAD question). Both are now fixed. Your testing found what my testing missed, which is the argument for you being the beta user.

---

## 5. Things I got wrong and corrected

| Claim | Reality |
|---|---|
| "All ten textbooks are zero-filled" (from an earlier session) | 23 of 26 were perfectly readable. The corruption was in the copy, not the download |
| "The guard has no false positives" | It blocked `π²EI/l_e²` and Fourier's law, both correct. Fixed |
| "`bracket.stl` is a valid solid" | Valid file, **not watertight**. Fixed today |
| "`workspace_server.py` does not exist" (five sessions) | It was in Downloads the whole time |
| `processors=4` in `.wslconfig` | You have 16 cores. My careless default cost you 75% of your CPU |

---

## 6. What to check yourself, in order

1. **Open `calc.xlsx` in Excel.** Does the deflection cell show a number or `#VALUE!`?
2. **Open `sandbox_3d.html` in a browser.** Does the part render and rotate?
3. **Open `bracket_drawing.dxf`** in FreeCAD or LibreCAD. Are the dimensions readable and correct?
4. **Look at `rc_lowpass.png`.** Is it a correct RC low-pass?
5. **Run `python cfd_validate.py`.** This is the big one.
6. **Spot-check five reference citations** against your real Shigley and Gere.
7. **Run `python kb.py stats`** after reclassifying and read every line of the book list.

Each takes under a minute except the CFD run, and together they convert most of §2 and §3 into either "verified" or "found a bug" — which is exactly what a beta user is for.
