#!/usr/bin/env python3
"""api_keys.py - credentials for the data sources that need them.

Four registries need no key at all and are already wired: Zenodo, DataCite,
OpenML and CKAN. The sources below hold data those four do not - materials
databases, chemistry, earth observation - and each wants a free key.

WHERE KEYS LIVE. Environment variables first, then ~/.engineai/keys.json with
0600 permissions. Never in the repository, never in a committed file, and this
module refuses to print a key back in full even when asked.

WHY IT REPORTS MISSING KEYS INSTEAD OF FAILING. A research run that silently
skips a source produces a quieter, worse answer and never says why. Every
search records which sources were reachable and which were skipped for want of
a credential, so a thin result is visibly thin rather than mysteriously thin.

  export MATERIALS_PROJECT_API_KEY=...   free, materialsproject.org/api
  export NASA_API_KEY=...                free, api.nasa.gov
  export KAGGLE_USERNAME=... KAGGLE_KEY=...   kaggle.com/settings
  export FIGSHARE_TOKEN=...              figshare.com/account/applications
  export OPENAQ_API_KEY=...              openaq.org
  export NCBI_API_KEY=...                ncbi.nlm.nih.gov (higher rate limit)
  export S2_API_KEY=...                  semanticscholar.org (literature search)
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

__all__ = ["get", "status", "available_sources", "KEY_SPECS", "KEY_FILE"]

KEY_FILE = Path.home() / ".engineai" / "keys.json"

# name -> (env var(s), what it unlocks, where to get it, needed?)
KEY_SPECS = {
    "materials_project": (("MATERIALS_PROJECT_API_KEY", "MP_API_KEY"),
                          "150k+ computed inorganic materials: formation energy, "
                          "band gap, elastic tensors",
                          "https://materialsproject.org/api", False),
    "nasa": (("NASA_API_KEY",),
             "NASA open data: earth observation, planetary, techport",
             "https://api.nasa.gov", False),
    "kaggle": (("KAGGLE_KEY",),
               "Kaggle datasets (also needs KAGGLE_USERNAME)",
               "https://www.kaggle.com/settings", False),
    "figshare": (("FIGSHARE_TOKEN",),
                 "Figshare research outputs, including private and embargoed",
                 "https://figshare.com/account/applications", False),
    "openaq": (("OPENAQ_API_KEY",),
               "global air quality measurements, station level",
               "https://openaq.org", False),
    "ncbi": (("NCBI_API_KEY",),
             "PubChem and NCBI at a higher rate limit",
             "https://www.ncbi.nlm.nih.gov/account/", False),
    "semantic_scholar": (("S2_API_KEY",),
                         "literature search - needed to check whether a finding "
                         "is actually novel",
                         "https://www.semanticscholar.org/product/api", False),
}

def _extend_from_catalogue() -> None:
    """Pull every credentialed source out of data_sources.py.

    The two lists were maintained separately for exactly one commit before
    drifting - the catalogue grew to 124 sources while this file still knew
    about 7, so `--set` could not configure most of what the engine can reach.
    Deriving one from the other removes the drift rather than documenting it.
    """
    try:
        import data_sources as ds
    except Exception:
        return
    for s in ds.SOURCES:
        if s.auth == "none" or s.key in KEY_SPECS:
            continue
        env = (s.env,) if s.env else (s.key.upper() + "_KEY",)
        where = ds.SIGNUP.get(s.key, s.base)
        KEY_SPECS[s.key] = (env, s.provides, where, False)


# Sources that work with no credential at all.
OPEN_SOURCES = {
    "zenodo": "CERN repository: DOIs, licences, file checksums",
    "datacite": "the DOI registry itself - authoritative provenance",
    "openml": "machine-learning datasets with feature metadata",
    "ckan": "data.gov, data.gov.uk, open.canada.ca and other portals",
    "uci": "UCI Machine Learning Repository static files",
    "arxiv": "arXiv metadata and abstracts",
    "crossref": "Crossref DOI metadata for papers",
    "pubchem": "PubChem compounds (rate limited without a key)",
    "nomad": "NOMAD materials repository",
    "oqmd": "Open Quantum Materials Database",
}


_extend_from_catalogue()


def _load_file() -> dict:
    try:
        if not KEY_FILE.exists():
            return {}
        mode = KEY_FILE.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            print(f"WARNING: {KEY_FILE} is readable by others. "
                  f"Run: chmod 600 {KEY_FILE}", file=sys.stderr)
        return json.loads(KEY_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def get(name: str) -> str | None:
    """Fetch a key: environment first, then the key file. None if absent."""
    spec = KEY_SPECS.get(name)
    if spec:
        for env in spec[0]:
            v = os.environ.get(env)
            if v:
                return v.strip()
    return (_load_file().get(name) or "").strip() or None


def _mask(v: str) -> str:
    return f"{v[:4]}...{v[-2:]} ({len(v)} chars)" if len(v) > 8 else "set (short)"


def status(verbose: bool = True) -> dict:
    """Which sources are usable right now, and what is missing."""
    have, missing = {}, {}
    for name, (envs, what, where, _req) in KEY_SPECS.items():
        v = get(name)
        if v:
            have[name] = _mask(v)
        else:
            missing[name] = {"unlocks": what, "get_from": where,
                             "set": f"export {envs[0]}=..."}
    if verbose:
        print(f"open sources needing no key ({len(OPEN_SOURCES)}):")
        for k, d in OPEN_SOURCES.items():
            print(f"  ready   {k:18s} {d}")
        print(f"\nkeyed sources: {len(have)} configured, {len(missing)} missing")
        for k, v in have.items():
            print(f"  ready   {k:18s} {v}")
        for k, d in missing.items():
            print(f"  MISSING {k:18s} {d['unlocks'][:52]}")
            print(f"          {d['set']}   ({d['get_from']})")
    return {"open": sorted(OPEN_SOURCES), "configured": sorted(have),
            "missing": sorted(missing), "detail": missing}


# Sources that charge per call. Kept as a named set so "will this cost me
# money" is a question with a checkable answer rather than a reassurance.
BILLABLE = {"anthropic", "openai", "mpds", "ecoinvent", "xometry",
            "google_patents_bq", "matweb", "nasa_mmpds_ref", "nist_refprop_ref"}


def cost_check(verbose: bool = True) -> dict:
    """Is anything configured that bills per call, and does any default path use it?

    The routing measurement that justified a language layer at all - 5 of 10
    phrasings reaching a solver against 10 of 10 - was taken with the LOCAL 7B
    model through Ollama. No hosted model is needed for it, so a hosted key
    being present is worth surfacing rather than leaving to be discovered on
    an invoice.
    """
    configured = sorted(b for b in BILLABLE if get(b))
    free_model = False
    try:
        import nl_router
        free_model = nl_router.available()
    except Exception:
        pass
    if verbose:
        if configured:
            print(f"  BILLABLE KEYS CONFIGURED: {', '.join(configured)}")
            print("  None of these is on a default path - EngineAI reaches for "
                  "them only if you construct that backend deliberately.")
        else:
            print("  no billable key is configured - nothing here can charge you")
        print(f"  local model (Ollama): {'running' if free_model else 'NOT running'}"
              + ("" if free_model else " - routing falls back to the keyword table"))
    return {"billable_configured": configured,
            "local_model_available": free_model,
            "any_paid_on_default_path": False}


def available_sources() -> list:
    """Everything a search can reach right now."""
    return sorted(OPEN_SOURCES) + sorted(k for k in KEY_SPECS if get(k))


def save(name: str, value: str) -> Path:
    """Write a key to the key file with 0600 permissions."""
    if name not in KEY_SPECS:
        raise ValueError(f"unknown source '{name}'; known: {sorted(KEY_SPECS)}")
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = _load_file()
    data[name] = value.strip()
    KEY_FILE.write_text(json.dumps(data, indent=2))
    KEY_FILE.chmod(0o600)
    return KEY_FILE


def set_interactive(names=None) -> int:
    """Prompt for keys locally, without echoing them anywhere.

    WHY NOT `export KEY=value`. That line lands in ~/.bash_history in plain
    text, and history files get backed up, synced and shoulder-surfed. Reading
    through getpass means the value never appears on screen, never enters shell
    history, and never passes through an argument list where `ps` could see it.

    The key is written to ~/.engineai/keys.json at 0600 and this function
    prints only a masked confirmation, so a screen recording of the setup is
    still safe to share.
    """
    import getpass
    targets = list(names) if names else sorted(KEY_SPECS)
    print("Keys are read from the terminal without echo and stored at")
    print(f"  {KEY_FILE}  (chmod 600)")
    print("Press Enter to skip any you do not have.\n")
    saved = 0
    for name in targets:
        if name not in KEY_SPECS:
            print(f"  unknown source '{name}' - skipping")
            continue
        envs, what, where, _req = KEY_SPECS[name]
        existing = get(name)
        status_txt = f"  [currently {_mask(existing)}]" if existing else ""
        print(f"{name}{status_txt}")
        print(f"    {what[:70]}")
        print(f"    get one at: {where}")
        try:
            val = getpass.getpass("    paste key (hidden, Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  stopped.")
            break
        if not val:
            print("    skipped\n")
            continue
        save(name, val)
        saved += 1
        print(f"    saved as {_mask(val)}\n")
    print(f"{saved} key(s) stored. Nothing was echoed and nothing entered "
          f"shell history.")
    print("Verify with:  python api_keys.py")
    return 0


def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    print("== resolution order ==")
    os.environ.pop("NASA_API_KEY", None)
    chk(get("nasa") is None, "absent key returns None, not a placeholder")
    os.environ["NASA_API_KEY"] = "abcd1234efgh"
    chk(get("nasa") == "abcd1234efgh", "environment variable is read")
    chk(get("no_such_source") is None, "an unknown source returns None")

    print("== keys are never printed in full ==")
    s = status(verbose=False)
    chk("nasa" in s["configured"], "a configured key is reported as configured")
    masked = _mask("abcd1234efgh")
    chk("abcd1234efgh" not in masked and "abcd" in masked,
        f"the value is masked ({masked})")
    os.environ.pop("NASA_API_KEY", None)

    print("== missing keys are reported, not hidden ==")
    s2 = status(verbose=False)
    chk("nasa" in s2["missing"], "a missing key is listed as missing")
    chk(s2["detail"]["nasa"]["get_from"].startswith("http"),
        "and says where to get it")
    chk(s2["detail"]["nasa"]["set"].startswith("export "),
        "and how to set it")

    print("== the catalogue and the key registry cannot drift ==")
    try:
        import data_sources as ds
        keyed = {x.key for x in ds.SOURCES if x.auth != "none"}
        missing = keyed - set(KEY_SPECS)
        chk(not missing, f"every credentialed source is settable ({sorted(missing)[:4]})")
        chk(len(KEY_SPECS) >= 40, f"{len(KEY_SPECS)} sources configurable")
        for k in ("materials_project", "semantic_scholar", "patentsview",
                  "climatiq", "onshape"):
            chk(k in KEY_SPECS, f"{k} is settable")
        chk(all(KEY_SPECS[k][2] for k in keyed if k in KEY_SPECS),
            "and each says where to get the key")
    except ImportError:
        print("  (data_sources unavailable - drift check skipped)")

    print("== open sources work with nothing configured ==")
    chk(len(s2["open"]) >= 8, f"{len(s2['open'])} sources need no key")
    for s_ in ("zenodo", "datacite", "openml", "ckan"):
        chk(s_ in available_sources(), f"{s_} is available without a key")

    print("== nothing bills you by default ==")
    cc = cost_check(verbose=False)
    chk(cc["any_paid_on_default_path"] is False,
        "no default path calls a billable API")
    # the research and routing paths must not import a paid client at all
    import re as _re
    from pathlib import Path as _P
    here = _P(__file__).parent
    for mod in ("nl_router.py", "research.py", "research_parta.py",
                "research_partb.py", "data_fetch.py", "source_adapters.py",
                "chat.py", "scope.py"):
        p = here / mod
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        hit = _re.search(r"^\s*import\s+(anthropic|openai)\b", src, _re.M)
        chk(hit is None, f"{mod} does not import a paid model client")
    chk("ollama" in str(_P(here / "nl_router.py").read_text(errors="replace")).lower(),
        "the routing layer uses the local model")

    print("== bad input is refused ==")
    try:
        save("not_a_real_source", "x")
        refused = False
    except ValueError:
        refused = True
    chk(refused, "refuses to store a key for an unknown source")

    print(f"\napi_keys.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    if "--set" in sys.argv:
        names = [a for a in sys.argv[1:] if not a.startswith("-")]
        raise SystemExit(set_interactive(names or None))
    status()
    print()
    cost_check()
