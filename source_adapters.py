#!/usr/bin/env python3
"""source_adapters.py - parsers for the eight credentialed sources now available.

Materials Project, PubChem/NCBI, NASA, EIA, OpenAQ, Kaggle, Hugging Face,
Figshare. Each is one function that calls a REST endpoint and returns the same
`Dataset` shape data_fetch.py already uses, so licence gating, checksum
verification, quarantine and authenticity assessment are inherited rather than
reimplemented eight times.

THREE THINGS THESE ADAPTERS DO THAT A PLAIN API CLIENT DOES NOT

  They carry the licence forward. Kaggle datasets in particular range from CC0
  to "all rights reserved" and the difference decides whether a result can be
  published. The licence travels with the record instead of being looked up
  later, or not at all.

  They separate a MEASUREMENT from a CALCULATION. Materials Project is DFT
  output - computed, not measured - and is labelled so at the source, because a
  model validated against DFT has been validated against another model. PubChem
  mixes experimental and predicted values in the same field and the adapter
  keeps the distinction.

  They fail loudly on a missing key. A source silently skipped produces a
  thinner answer with no explanation, which is worse than an error.

KAGGLE reads ~/.kaggle/kaggle.json where the official client already put it,
rather than asking for the credential a second time. Two copies of a secret is
one more place to leak it from.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import api_keys
from data_fetch import Dataset, USER_AGENT

__all__ = ["materials_project", "pubchem", "nasa_techport", "eia", "openaq",
           "kaggle", "huggingface", "figshare", "ADAPTERS",
           "DIRECT_ADAPTERS", "search_all"]

TIMEOUT = 30


class MissingKey(RuntimeError):
    """Raised instead of returning nothing, so a skipped source is visible."""


def _get(url: str, headers: dict | None = None, timeout: int = TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json",
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _need(name: str) -> str:
    k = api_keys.get(name)
    if not k:
        raise MissingKey(
            f"{name} needs a key. Set it with: python api_keys.py --set {name}")
    return k


# --------------------------------------------------------------------------
# Materials Project - DFT, so COMPUTED and labelled as such
# --------------------------------------------------------------------------

def materials_project(query: str, limit: int = 10) -> list[Dataset]:
    """Inorganic materials by formula or element set.

    Everything here is density functional theory output. That makes it
    enormously useful for screening and useless as experimental validation -
    a model checked against DFT has been checked against another model, and
    the `kind` field says so rather than leaving it to be assumed.
    """
    key = _need("materials_project")
    fields = ("material_id,formula_pretty,energy_above_hull,band_gap,density,"
              "formation_energy_per_atom,symmetry,bulk_modulus,shear_modulus")
    q = urllib.parse.urlencode({"formula": query, "_limit": limit,
                                "_fields": fields})
    data = _get(f"https://api.materialsproject.org/materials/summary/?{q}",
                {"X-API-KEY": key})
    out = []
    for d in data.get("data", []):
        mid = d.get("material_id", "")
        props = {k: v for k, v in d.items() if k != "material_id"}
        out.append(Dataset(
            source="materials_project", identifier=str(mid),
            title=f"{d.get('formula_pretty', query)} ({mid})",
            url=f"https://materialsproject.org/materials/{mid}",
            files=[], licence="cc-by-4.0", doi="", publisher="Materials Project",
            description="COMPUTED (DFT), not measured: "
                        + ", ".join(f"{k}={v}" for k, v in list(props.items())[:6])))
    return out


# --------------------------------------------------------------------------
# PubChem - mixed experimental and predicted, kept distinct
# --------------------------------------------------------------------------

def pubchem(query: str, limit: int = 5) -> list[Dataset]:
    """Compound properties by name. No key required; NCBI_API_KEY lifts the rate limit."""
    props = ("MolecularFormula,MolecularWeight,XLogP,ExactMass,TPSA,Complexity,"
             "CanonicalSMILES")
    name = urllib.parse.quote(query)
    url = (f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}"
           f"/property/{props}/JSON")
    key = api_keys.get("ncbi")
    if key:
        url += f"?api_key={key}"
    data = _get(url)
    out = []
    for p in data.get("PropertyTable", {}).get("Properties", [])[:limit]:
        cid = p.get("CID")
        out.append(Dataset(
            source="pubchem", identifier=str(cid),
            title=f"{query} (CID {cid}) {p.get('MolecularFormula','')}",
            url=f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
            files=[], licence="public domain", doi="", publisher="NCBI PubChem",
            description="MIXED: XLogP and Complexity are PREDICTED; molecular "
                        "formula and exact mass are derived from structure. "
                        + ", ".join(f"{k}={v}" for k, v in p.items()
                                    if k not in ("CID", "CanonicalSMILES"))))
    return out


# --------------------------------------------------------------------------
# NASA TechPort - engineering projects and technology readiness
# --------------------------------------------------------------------------

def nasa_techport(query: str, limit: int = 10) -> list[Dataset]:
    """NASA technology projects. Useful for TRL and prior-art context."""
    key = _need("nasa_open")
    data = _get(f"https://api.nasa.gov/techport/api/projects?api_key={key}")
    projects = (data.get("projects", {}).get("projects")
                or data.get("projects") or [])
    ql = query.lower()
    out = []
    for p in projects:
        title = str(p.get("title", ""))
        if ql and ql not in title.lower():
            continue
        pid = p.get("projectId") or p.get("id")
        out.append(Dataset(
            source="nasa_techport", identifier=str(pid), title=title,
            url=f"https://techport.nasa.gov/view/{pid}", files=[],
            licence="public domain", doi="", publisher="NASA",
            description=str(p.get("description", ""))[:280]))
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# EIA - energy measurements, genuinely measured
# --------------------------------------------------------------------------

def eia(route: str = "electricity/rto/region-data", limit: int = 100,
        **params) -> list[Dataset]:
    """US energy data. These ARE measurements - metered generation and demand."""
    key = _need("eia")
    q = {"api_key": key, "frequency": params.pop("frequency", "hourly"),
         "data[0]": params.pop("data", "value"), "length": limit}
    q.update(params)
    url = f"https://api.eia.gov/v2/{route}/data/?{urllib.parse.urlencode(q)}"
    data = _get(url)
    rows = data.get("response", {}).get("data", [])
    if not rows:
        return []
    return [Dataset(
        source="eia", identifier=route, title=f"EIA {route} ({len(rows)} rows)",
        url=f"https://www.eia.gov/opendata/browser/{route}",
        files=[], licence="public domain", doi="",
        publisher="US Energy Information Administration",
        description=f"MEASURED. Columns: {', '.join(list(rows[0].keys())[:8])}")]


# --------------------------------------------------------------------------
# OpenAQ - measured air quality, station level
# --------------------------------------------------------------------------

def openaq(query: str = "", limit: int = 20) -> list[Dataset]:
    """Air quality monitoring stations and their measurements.

    Directly relevant to the corpus A8 study, which calibrated low-cost sensors
    against reference analysers on a single Italian record. OpenAQ makes that
    testable on other cities, which is the transfer question that study
    explicitly could NOT answer from its own data.
    """
    key = _need("openaq")
    q = urllib.parse.urlencode({"limit": limit})
    data = _get(f"https://api.openaq.org/v3/locations?{q}",
                {"X-API-Key": key})
    ql = query.lower()
    out = []
    for r in data.get("results", []):
        name = str(r.get("name", ""))
        country = (r.get("country") or {}).get("name", "")
        if ql and ql not in f"{name} {country}".lower():
            continue
        params_ = ", ".join(s.get("parameter", {}).get("name", "")
                            for s in r.get("sensors", [])[:6])
        out.append(Dataset(
            source="openaq", identifier=str(r.get("id")),
            title=f"{name}, {country}",
            url=f"https://explore.openaq.org/locations/{r.get('id')}",
            files=[], licence="cc-by-4.0", doi="", publisher="OpenAQ",
            description=f"MEASURED at a fixed station. Parameters: {params_}"))
    return out


# --------------------------------------------------------------------------
# Kaggle - reads the credential the official client already stored
# --------------------------------------------------------------------------

def _kaggle_auth() -> tuple:
    """Read ~/.kaggle/kaggle.json rather than asking for the key twice."""
    for p in (Path.home() / ".kaggle" / "kaggle.json",
              Path(os.environ.get("KAGGLE_CONFIG_DIR", "")) / "kaggle.json"):
        try:
            if p.is_file():
                d = json.loads(p.read_text())
                if d.get("username") and d.get("key"):
                    return d["username"], d["key"]
        except (OSError, json.JSONDecodeError):
            continue
    u, k = os.environ.get("KAGGLE_USERNAME"), api_keys.get("kaggle")
    if u and k:
        return u, k
    raise MissingKey(
        "Kaggle needs ~/.kaggle/kaggle.json (from kaggle.com/settings > "
        "Create New Token), or KAGGLE_USERNAME plus the kaggle key")


def kaggle(query: str, limit: int = 20) -> list[Dataset]:
    """Kaggle datasets. Licences vary wildly here, so the licence is carried."""
    user, key = _kaggle_auth()
    tok = base64.b64encode(f"{user}:{key}".encode()).decode()
    q = urllib.parse.urlencode({"search": query, "pageSize": limit})
    data = _get(f"https://www.kaggle.com/api/v1/datasets/list?{q}",
                {"Authorization": f"Basic {tok}"})
    out = []
    for d in (data if isinstance(data, list) else []):
        ref = d.get("ref", "")
        lic = str(d.get("licenseName", "")).lower()
        out.append(Dataset(
            source="kaggle", identifier=ref, title=str(d.get("title", ref)),
            url=f"https://www.kaggle.com/datasets/{ref}", files=[],
            licence=lic, doi="", publisher=str(d.get("ownerName", "")),
            description=f"{d.get('totalBytes', 0)} bytes. "
                        f"PROVENANCE IS OFTEN WEAK HERE - verify with "
                        f"data_authenticity before using. {d.get('subtitle','')}"))
    return out


# --------------------------------------------------------------------------
# Hugging Face - streaming matters for data larger than RAM
# --------------------------------------------------------------------------

def huggingface(query: str, limit: int = 20) -> list[Dataset]:
    """Hugging Face datasets. Token optional for public ones."""
    q = urllib.parse.urlencode({"search": query, "limit": limit,
                                "full": "true"})
    key = api_keys.get("huggingface")
    hdr = {"Authorization": f"Bearer {key}"} if key else {}
    data = _get(f"https://huggingface.co/api/datasets?{q}", hdr)
    out = []
    for d in (data if isinstance(data, list) else []):
        did = d.get("id", "")
        card = d.get("cardData") or {}
        lic = card.get("license") or ""
        if isinstance(lic, list):
            lic = lic[0] if lic else ""
        out.append(Dataset(
            source="huggingface", identifier=did, title=did,
            url=f"https://huggingface.co/datasets/{did}", files=[],
            licence=str(lic), doi="", publisher=did.split("/")[0] if "/" in did else "",
            description=f"{d.get('downloads', 0)} downloads. Supports streaming - "
                        f"use bigdata.py rather than loading it whole."))
    return out


# --------------------------------------------------------------------------
# Figshare
# --------------------------------------------------------------------------

def figshare(query: str, limit: int = 20) -> list[Dataset]:
    """Figshare research outputs, with DOIs and file checksums."""
    key = api_keys.get("figshare")
    hdr = {"Authorization": f"token {key}"} if key else {}
    q = urllib.parse.urlencode({"search_for": query, "page_size": limit,
                                "item_type": 3})
    data = _get(f"https://api.figshare.com/v2/articles?{q}", hdr)
    out = []
    for a in (data if isinstance(data, list) else []):
        aid = a.get("id")
        out.append(Dataset(
            source="figshare", identifier=str(aid),
            title=str(a.get("title", "")),
            url=str(a.get("url_public_html", "")), files=[],
            licence=str((a.get("license") or {}).get("name", "")),
            doi=str(a.get("doi", "")), publisher="Figshare",
            description=str(a.get("description", ""))[:240]))
    return out


ADAPTERS = {
    "materials_project": materials_project,
    "pubchem": pubchem,
    "nasa_techport": nasa_techport,
    "openaq": openaq,
    "kaggle": kaggle,
    "huggingface": huggingface,
    "figshare": figshare,
}

# EIA is a real adapter, but unlike the catalogue-search APIs it requires a
# concrete v2 route (for example ``electricity/rto/region-data``) and optional
# facets.  Putting it in ADAPTERS caused search_all("steel") to treat "steel"
# as an API route.  Keep the interfaces explicit: it is available directly as
# eia(route=..., ...) and counted as wired, but is never sent a fake route by
# the generic metadata search.
DIRECT_ADAPTERS = {"eia": eia}


def search_all(query: str, limit: int = 5, verbose: bool = True) -> dict:
    """Query every adapter whose credential is present. Skips are REPORTED."""
    found, skipped, failed = {}, {}, {}
    for name, fn in ADAPTERS.items():
        try:
            hits = fn(query, limit)
            found[name] = hits
            if verbose:
                print(f"  {name:18s} {len(hits)} hit(s)")
        except MissingKey as e:
            skipped[name] = str(e)
            if verbose:
                print(f"  {name:18s} SKIPPED - no credential")
        except Exception as e:
            failed[name] = f"{type(e).__name__}: {e}"
            if verbose:
                print(f"  {name:18s} FAILED - {type(e).__name__}")
    return {"found": found, "skipped": skipped, "failed": failed,
            "n_results": sum(len(v) for v in found.values())}


# --------------------------------------------------------------------------
# selftest - offline, with the network seam replaced
# --------------------------------------------------------------------------

def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    global _get
    real_get = _get
    stubs = {
        "api.materialsproject.org": {"data": [
            {"material_id": "mp-149", "formula_pretty": "Si", "band_gap": 0.61,
             "density": 2.28, "formation_energy_per_atom": 0.0}]},
        "pubchem.ncbi.nlm.nih.gov": {"PropertyTable": {"Properties": [
            {"CID": 962, "MolecularFormula": "H2O", "MolecularWeight": "18.015",
             "XLogP": -0.5, "ExactMass": "18.010"}]}},
        "api.nasa.gov/techport": {"projects": {"projects": [
            {"projectId": 12345, "title": "Advanced Lattice Structures",
             "description": "AM lattice research"}]}},
        "api.eia.gov": {"response": {"data": [
            {"period": "2024-01-01T00", "respondent": "CAL", "value": 25000,
             "value-units": "megawatthours"}]}},
        "api.openaq.org": {"results": [
            {"id": 42, "name": "Dhaka US Embassy",
             "country": {"name": "Bangladesh"},
             "sensors": [{"parameter": {"name": "pm25"}},
                         {"parameter": {"name": "pm10"}}]}]},
        "kaggle.com/api": [
            {"ref": "user/concrete", "title": "Concrete strength",
             "licenseName": "CC0: Public Domain", "ownerName": "user",
             "totalBytes": 12345, "subtitle": "mix designs"}],
        "huggingface.co/api": [
            {"id": "org/beams", "downloads": 900,
             "cardData": {"license": "cc-by-4.0"}}],
        "api.figshare.com": [
            {"id": 77, "title": "Fatigue data", "doi": "10.6084/m9.figshare.77",
             "url_public_html": "https://figshare.com/articles/77",
             "license": {"name": "CC BY 4.0"}, "description": "S-N curves"}],
    }

    def fake_get(url, headers=None, timeout=TIMEOUT):
        for host, payload in stubs.items():
            if host in url:
                return payload
        raise urllib.error.URLError(f"no stub for {url}")

    _get = fake_get
    os.environ["MP_API_KEY"] = "test"
    os.environ["NASA_API_KEY"] = "test"
    os.environ["EIA_API_KEY"] = "test"
    os.environ["OPENAQ_API_KEY"] = "test"

    print("== each adapter returns the common Dataset shape ==")
    mp = materials_project("Si")
    chk(len(mp) == 1 and mp[0].source == "materials_project", "Materials Project parses")
    chk(mp[0].licence == "cc-by-4.0", "and carries its licence")
    chk("COMPUTED (DFT)" in mp[0].description,
        "and labels itself computed, not measured - a model validated against "
        "DFT is validated against another model")

    pc = pubchem("water")
    chk(len(pc) == 1 and "962" in pc[0].identifier, "PubChem parses")
    chk("PREDICTED" in pc[0].description,
        "and keeps predicted values distinct from derived ones")

    nt = nasa_techport("lattice")
    chk(len(nt) == 1 and "Lattice" in nt[0].title, "NASA TechPort parses and filters")
    chk(nasa_techport("nothing-matches-this") == [], "and returns nothing on no match")

    ei = eia()
    chk(len(ei) == 1 and "MEASURED" in ei[0].description,
        "EIA parses and is labelled measured")

    chk(set(ADAPTERS) | set(DIRECT_ADAPTERS) == {
        "materials_project", "pubchem", "nasa_techport", "eia", "openaq",
        "kaggle", "huggingface", "figshare"},
        "all eight implemented sources are exposed through an adapter registry")
    chk("eia" not in ADAPTERS and DIRECT_ADAPTERS["eia"] is eia,
        "route-based EIA access is kept out of generic text search")

    oa = openaq("dhaka")
    chk(len(oa) == 1 and "Bangladesh" in oa[0].title, "OpenAQ parses and filters")
    chk("pm25" in oa[0].description, "and lists the parameters measured")

    kg_home = Path.home() / ".kaggle" / "kaggle.json"
    if kg_home.is_file():
        kg = kaggle("concrete")
        chk(len(kg) == 1 and kg[0].licence.startswith("cc0"),
            "Kaggle parses and carries the licence")
        chk("PROVENANCE IS OFTEN WEAK" in kg[0].description,
            "and warns that Kaggle provenance needs verifying")
    else:
        try:
            kaggle("concrete")
            chk(False, "Kaggle should have raised MissingKey")
        except MissingKey as e:
            chk("kaggle.json" in str(e),
                "Kaggle points at the file the official client uses")

    hf = huggingface("beams")
    chk(len(hf) == 1 and hf[0].licence == "cc-by-4.0", "Hugging Face parses")
    chk("streaming" in hf[0].description, "and points at bigdata.py for large sets")

    fs = figshare("fatigue")
    chk(len(fs) == 1 and fs[0].doi.startswith("10."), "Figshare parses with a DOI")

    print("== a missing credential is an ERROR, not a silent skip ==")
    for var in ("MP_API_KEY", "NASA_API_KEY", "EIA_API_KEY", "OPENAQ_API_KEY"):
        os.environ.pop(var, None)
    for fn, nm in ((materials_project, "materials_project"), (eia, "eia"),
                   (openaq, "openaq")):
        try:
            fn("x") if fn is not eia else fn()
            raised = False
        except MissingKey as e:
            raised = "api_keys.py --set" in str(e)
        except Exception:
            raised = False
        chk(raised, f"{nm} raises MissingKey naming the fix")

    print("== search_all reports skips rather than hiding them ==")
    res = search_all("test", verbose=False)
    chk(res["skipped"], f"{len(res['skipped'])} source(s) reported as skipped")
    chk("pubchem" in res["found"], "and the keyless ones still ran")
    chk(set(res) == {"found", "skipped", "failed", "n_results"},
        "the result separates found, skipped and failed")

    _get = real_get
    print(f"\nsource_adapters.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    q = " ".join(a for a in sys.argv[1:] if not a.startswith("-")) or "concrete"
    print(f"searching every configured source for: {q}\n")
    r = search_all(q, limit=3)
    print(f"\n{r['n_results']} result(s)")
    for src, rows in r["found"].items():
        for d in rows[:2]:
            print(f"\n  [{src}] {d.title[:60]}")
            print(f"      licence {d.licence or 'UNKNOWN'}  {d.url}")
            print(f"      {d.description[:110]}")
    for src, why in r["skipped"].items():
        print(f"\n  SKIPPED {src}: {why}")
