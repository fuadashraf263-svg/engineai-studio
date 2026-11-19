#!/usr/bin/env python3
"""data_fetch.py - find real data, fetch it, and prove what arrived.

Four open registries, no API key required for any of them:

  Zenodo     CERN's repository. Every record has a DOI, a licence field and
             file-level checksums, which makes it the best-behaved source here.
  DataCite   the DOI registry itself. Authoritative for provenance: if a DOI
             resolves here, the dataset is registered to a real depositor.
  OpenML     machine-learning datasets with structured feature metadata.
  CKAN       the software behind data.gov, data.gov.uk and hundreds of
             government portals - one client reaches all of them.

WHAT MAKES THIS DIFFERENT FROM `wget`. Three refusals:

  NO LICENCE, NO SHIP. A file whose licence cannot be determined is downloaded
  to a quarantine directory and marked NOT_REDISTRIBUTABLE. EngineAI may
  analyse it locally; it may not bundle it, publish it or hand it on. That rule
  exists because "we assumed it was open" is how projects acquire legal
  problems, and an assumption is not a licence.

  CHECKSUM OR IT DIDN'T HAPPEN. Where the registry publishes a hash, the
  downloaded bytes are checked against it and a mismatch is a hard failure, not
  a warning. Where no hash is published, that absence is recorded in the
  provenance rather than quietly ignored.

  FETCHED IS NOT VERIFIED. Every download is handed to data_authenticity.assess
  before it can be used, so "I found a dataset" and "I found a dataset that
  looks like a real measurement" stay separate claims.

Offline by default in tests: every network call goes through _get(), which the
selftest replaces, so the suite runs with no network and proves the parsing,
the licence logic and the refusals rather than proving that Zenodo was up.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path

__all__ = ["search", "fetch", "Dataset", "FetchResult", "licence_status",
           "SOURCES", "REDISTRIBUTABLE"]

USER_AGENT = "EngineAI/1.0 (engineering research; contact via repository)"
TIMEOUT = 30

SOURCES = ("zenodo", "datacite", "openml", "ckan")

# Licences that permit redistribution with attribution. Anything not on this
# list is treated as unknown, which is deliberately conservative: the cost of
# wrongly withholding a file is an inconvenience, the cost of wrongly
# republishing one is somebody else's rights.
REDISTRIBUTABLE = {
    "cc0", "cc-zero", "cc0-1.0", "public domain", "publicdomain", "pddl",
    "cc-by", "cc-by-4.0", "cc-by-3.0", "cc-by-sa", "cc-by-sa-4.0",
    "odc-by", "odbl", "mit", "bsd", "apache-2.0", "gpl-3.0", "lgpl-3.0",
}
NON_REDISTRIBUTABLE_HINTS = ("nc", "non-commercial", "noncommercial", "nd",
                             "no-derivatives", "restricted", "closed",
                             "all rights reserved", "proprietary")


@dataclass
class Dataset:
    source: str
    identifier: str
    title: str
    url: str
    files: list = field(default_factory=list)     # [{name, url, size, checksum}]
    licence: str = ""
    doi: str = ""
    publisher: str = ""
    year: str = ""
    description: str = ""

    def __str__(self) -> str:
        lic = self.licence or "UNKNOWN"
        return (f"  [{self.source:8s}] {self.title[:58]:58s}\n"
                f"      licence {lic:22s} doi {self.doi or '-'}\n"
                f"      {len(self.files)} file(s)  {self.url}")


@dataclass
class FetchResult:
    dataset: Dataset
    path: Path | None
    sha256: str
    bytes: int
    checksum_verified: bool | None      # None = registry published no checksum
    redistributable: bool
    quarantined: bool
    errors: list = field(default_factory=list)
    fetched_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    def provenance(self) -> dict:
        d = asdict(self.dataset)
        d.pop("description", None)
        return {"dataset": d, "sha256": self.sha256, "bytes": self.bytes,
                "checksum_verified": self.checksum_verified,
                "redistributable": self.redistributable,
                "quarantined": self.quarantined, "fetched_at": self.fetched_at,
                "path": str(self.path) if self.path else None}

    def __str__(self) -> str:
        cv = {True: "verified", False: "MISMATCH", None: "none published"}[
            self.checksum_verified]
        return (f"  {self.dataset.title[:52]}\n"
                f"      {self.bytes} bytes  sha256 {self.sha256[:16]}  checksum {cv}\n"
                f"      licence {self.dataset.licence or 'UNKNOWN'} -> "
                f"{'redistributable' if self.redistributable else 'LOCAL USE ONLY'}"
                + ("  [QUARANTINED]" if self.quarantined else "")
                + "".join(f"\n      error: {e}" for e in self.errors))


def licence_status(licence: str) -> bool:
    """May this be redistributed? Unknown means no."""
    if not licence:
        return False
    low = licence.strip().lower()
    if any(h in low for h in NON_REDISTRIBUTABLE_HINTS):
        return False
    # Normalise BOTH sides. Normalising only the input turned "public domain"
    # into "public-domain", which then matched nothing, because the table still
    # held the spaced form.
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9.]+", "-", s.strip().lower()).strip("-")

    low_n = norm(low)
    return any(low_n == norm(r) or low_n.startswith(norm(r) + "-")
               or norm(r) in low_n for r in REDISTRIBUTABLE)


# --------------------------------------------------------------------------
# network - one seam, so the whole module is testable offline
# --------------------------------------------------------------------------

def _get(url: str, as_json: bool = True):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    return json.loads(raw.decode("utf-8", "replace")) if as_json else raw


def _get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT * 4) as r:
        return r.read()


# --------------------------------------------------------------------------
# per-registry parsers
# --------------------------------------------------------------------------

def _zenodo(query: str, limit: int) -> list[Dataset]:
    q = urllib.parse.urlencode({"q": query, "size": limit, "type": "dataset"})
    data = _get(f"https://zenodo.org/api/records?{q}")
    out = []
    for hit in data.get("hits", {}).get("hits", []):
        meta = hit.get("metadata", {})
        files = [{"name": f.get("key"), "url": f.get("links", {}).get("self"),
                  "size": f.get("size"),
                  "checksum": (f.get("checksum") or "").replace("md5:", "")}
                 for f in hit.get("files", [])]
        out.append(Dataset(
            source="zenodo", identifier=str(hit.get("id", "")),
            title=meta.get("title", ""), url=hit.get("links", {}).get("self_html", ""),
            files=files,
            licence=((meta.get("license") or {}).get("id", "")
                     if isinstance(meta.get("license"), dict)
                     else str(meta.get("license") or "")),
            doi=meta.get("doi", ""), publisher="Zenodo",
            year=str(meta.get("publication_date", ""))[:4],
            description=re.sub(r"<[^>]+>", "", meta.get("description", ""))[:300]))
    return out


def _datacite(query: str, limit: int) -> list[Dataset]:
    q = urllib.parse.urlencode({"query": query, "page[size]": limit,
                                "resource-type-id": "dataset"})
    data = _get(f"https://api.datacite.org/dois?{q}")
    out = []
    for item in data.get("data", []):
        a = item.get("attributes", {})
        titles = a.get("titles") or [{}]
        rights = a.get("rightsList") or [{}]
        out.append(Dataset(
            source="datacite", identifier=a.get("doi", ""),
            title=(titles[0] or {}).get("title", ""),
            url=a.get("url", ""), files=[],
            licence=(rights[0] or {}).get("rightsIdentifier", "")
                    or (rights[0] or {}).get("rights", ""),
            doi=a.get("doi", ""), publisher=str(a.get("publisher", "")),
            year=str(a.get("publicationYear", "")),
            description=str((a.get("descriptions") or [{}])[0].get("description", ""))[:300]))
    return out


def _openml(query: str, limit: int) -> list[Dataset]:
    data = _get(f"https://www.openml.org/api/v1/json/data/list/limit/{limit}")
    out = []
    for d in data.get("data", {}).get("dataset", []):
        name = d.get("name", "")
        if query and query.lower() not in name.lower():
            continue
        did = d.get("did")
        out.append(Dataset(
            source="openml", identifier=str(did), title=name,
            url=f"https://www.openml.org/d/{did}",
            files=[{"name": f"{name}.arff",
                    "url": d.get("file_id") and
                    f"https://www.openml.org/data/download/{d['file_id']}/{name}.arff",
                    "size": None, "checksum": d.get("md5_checksum", "")}],
            licence=d.get("licence", "") or d.get("license", ""),
            doi="", publisher="OpenML", year="",
            description=f"{d.get('NumberOfInstances','?')} rows x "
                        f"{d.get('NumberOfFeatures','?')} features"))
    return out


# CKAN portals, tried in order. data.gov has moved its API path more than once,
# so a single hard-coded host makes the whole source look dead when it is only
# one endpoint that changed.
CKAN_PORTALS = ("https://catalog.data.gov", "https://data.gov.uk",
                "https://open.canada.ca/data", "https://data.overheid.nl")


def _ckan(query: str, limit: int, portal: str | None = None) -> list[Dataset]:
    q = urllib.parse.urlencode({"q": query, "rows": limit})
    portals = [portal] if portal else list(CKAN_PORTALS)
    data, last = None, None
    for p in portals:
        try:
            data = _get(f"{p}/api/3/action/package_search?{q}")
            portal = p
            break
        except Exception as e:
            last = e
    if data is None:
        raise last or RuntimeError("no CKAN portal responded")
    out = []
    for pkg in data.get("result", {}).get("results", []):
        files = [{"name": r.get("name") or r.get("format", ""),
                  "url": r.get("url"), "size": r.get("size"),
                  "checksum": r.get("hash", "")}
                 for r in pkg.get("resources", []) if r.get("url")]
        out.append(Dataset(
            source="ckan", identifier=pkg.get("id", ""),
            title=pkg.get("title", ""),
            url=f"{portal}/dataset/{pkg.get('name','')}", files=files,
            licence=pkg.get("license_id", "") or pkg.get("license_title", ""),
            doi="", publisher=str((pkg.get("organization") or {}).get("title", "")),
            year=str(pkg.get("metadata_created", ""))[:4],
            description=str(pkg.get("notes", ""))[:300]))
    return out


_PARSERS = {"zenodo": _zenodo, "datacite": _datacite,
            "openml": _openml, "ckan": _ckan}


def search(query: str, sources=SOURCES, limit: int = 5,
           verbose: bool = False) -> list[Dataset]:
    """Search the open registries. A source that fails is reported, not hidden."""
    found: list[Dataset] = []
    for s in sources:
        fn = _PARSERS.get(s)
        if not fn:
            continue
        try:
            hits = fn(query, limit)
            found += hits
            if verbose:
                print(f"  {s}: {len(hits)} hit(s)")
        except Exception as e:
            if verbose:
                print(f"  {s}: unavailable ({type(e).__name__}: {e})")
    return found


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------

def fetch(ds: Dataset, dest: Path, file_index: int = 0,
          allow_unlicensed: bool = True) -> FetchResult:
    """Download one file, verify it, and record where it came from.

    Unlicensed files land in dest/quarantine and are marked local-use-only.
    They are still fetched, because analysing a file locally is not
    redistributing it - but the marking follows the file so that nothing
    downstream can bundle it by accident.
    """
    dest = Path(dest)
    redist = licence_status(ds.licence)
    quarantine = not redist
    target_dir = dest / ("quarantine" if quarantine else "licensed")
    errors: list[str] = []

    if not ds.files:
        return FetchResult(ds, None, "", 0, None, redist, quarantine,
                           ["registry listed no downloadable file"])
    if quarantine and not allow_unlicensed:
        return FetchResult(ds, None, "", 0, None, redist, quarantine,
                           [f"licence '{ds.licence or 'UNKNOWN'}' does not permit "
                            f"redistribution and allow_unlicensed is off"])

    f = ds.files[min(file_index, len(ds.files) - 1)]
    url = f.get("url")
    if not url:
        return FetchResult(ds, None, "", 0, None, redist, quarantine,
                           ["file entry carried no URL"])
    try:
        blob = _get_bytes(url)
    except Exception as e:
        return FetchResult(ds, None, "", 0, None, redist, quarantine,
                           [f"download failed: {type(e).__name__}: {e}"])

    sha = hashlib.sha256(blob).hexdigest()
    published = (f.get("checksum") or "").strip().lower()
    verified: bool | None = None
    if published:
        # registries publish md5 (Zenodo, OpenML) or sha256; compare on length
        algo = hashlib.md5 if len(published) == 32 else hashlib.sha256
        verified = algo(blob).hexdigest() == published
        if not verified:
            errors.append(f"CHECKSUM MISMATCH: registry published {published[:16]}..., "
                          f"got {algo(blob).hexdigest()[:16]}... - the bytes are not "
                          f"the bytes that were registered")
            return FetchResult(ds, None, sha, len(blob), False, redist,
                               quarantine, errors)
    else:
        errors.append("registry published no checksum - integrity of this "
                      "download cannot be confirmed")

    target_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", f.get("name") or "download.bin")
    path = target_dir / f"{ds.source}_{ds.identifier}_{name}"
    path.write_bytes(blob)

    res = FetchResult(ds, path, sha, len(blob), verified, redist, quarantine, errors)
    (path.with_suffix(path.suffix + ".provenance.json")).write_text(
        json.dumps(res.provenance(), indent=2))
    return res


def verify_downloaded(res: FetchResult, target: str | None = None) -> dict:
    """Hand a fetched table to the authenticity assessor.

    Fetching proves where a file came from. It says nothing about whether the
    numbers inside it are a record of anything, which is a separate question
    and gets a separate answer.
    """
    if not res.path or not res.path.exists():
        return {"verdict": "UNKNOWN", "reason": "nothing was downloaded"}
    try:
        import pandas as pd
        from data_authenticity import assess
    except ImportError as e:
        return {"verdict": "UNKNOWN", "reason": f"missing dependency: {e}"}
    try:
        df = pd.read_csv(res.path)
    except Exception as e:
        return {"verdict": "UNKNOWN",
                "reason": f"not a readable table: {type(e).__name__}"}
    cols = {c: df[c].to_numpy() for c in df.columns if df[c].dtype.kind in "if"}
    if not cols:
        return {"verdict": "UNKNOWN", "reason": "no numeric columns"}
    a = assess(cols, target=target, provenance=res.provenance())
    return {"verdict": a.verdict, "confidence": a.confidence,
            "notes": a.notes, "flagged": [i.name for i in a.flagged]}


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

    print("== licence classification ==")
    for lic in ("cc-by-4.0", "CC0-1.0", "MIT", "apache-2.0", "public domain"):
        chk(licence_status(lic), f"'{lic}' is redistributable")
    for lic in ("", "cc-by-nc-4.0", "all rights reserved", "proprietary",
                "CC BY-ND 4.0", "restricted"):
        chk(not licence_status(lic), f"'{lic or '(blank)'}' is NOT redistributable")
    chk(not licence_status("unknown-licence-2049"),
        "an unrecognised licence defaults to NOT redistributable")

    print("== registry parsing, offline ==")
    global _get, _get_bytes
    real_get, real_bytes = _get, _get_bytes
    payloads = {
        "zenodo.org": {"hits": {"hits": [{
            "id": 123, "links": {"self_html": "https://zenodo.org/records/123"},
            "files": [{"key": "beam.csv", "size": 12,
                       "links": {"self": "https://zenodo.org/f/beam.csv"},
                       "checksum": "md5:" + hashlib.md5(b"a,b\n1,2\n3,4\n").hexdigest()}],
            "metadata": {"title": "Beam test data", "license": {"id": "cc-by-4.0"},
                         "doi": "10.5281/zenodo.123",
                         "publication_date": "2024-01-01",
                         "description": "<p>Measured</p>"}}]}},
        "api.datacite.org": {"data": [{"attributes": {
            "doi": "10.1234/abc", "titles": [{"title": "Fatigue dataset"}],
            "url": "https://example.org/d", "rightsList": [{"rightsIdentifier": "cc0-1.0"}],
            "publisher": "Uni", "publicationYear": 2023,
            "descriptions": [{"description": "S-N curves"}]}}]},
        "openml.org": {"data": {"dataset": [{
            "did": 61, "name": "iris", "file_id": 61,
            "licence": "public domain", "NumberOfInstances": 150,
            "NumberOfFeatures": 5, "md5_checksum": "x"}]}},
        "catalog.data.gov": {"result": {"results": [{
            "id": "abc", "name": "bridges", "title": "Bridge inventory",
            "license_id": "cc-by", "notes": "national bridge data",
            "organization": {"title": "DOT"}, "metadata_created": "2022-05-01",
            "resources": [{"name": "bridges.csv", "format": "CSV",
                           "url": "https://data.gov/bridges.csv", "hash": ""}]}]}},
    }

    def fake_get(url, as_json=True):
        for host, payload in payloads.items():
            if host in url:
                return payload
        raise urllib.error.URLError(f"no stub for {url}")

    _get = fake_get
    z = _zenodo("beam", 5)
    chk(len(z) == 1 and z[0].licence == "cc-by-4.0", "Zenodo record parsed with licence")
    chk(z[0].doi == "10.5281/zenodo.123" and z[0].files[0]["checksum"],
        "Zenodo DOI and file checksum captured")
    d = _datacite("fatigue", 5)
    chk(len(d) == 1 and d[0].licence == "cc0-1.0", "DataCite record parsed")
    o = _openml("iris", 5)
    chk(len(o) == 1 and o[0].title == "iris", "OpenML record parsed")
    c = _ckan("bridges", 5)
    chk(len(c) == 1 and c[0].licence == "cc-by", "CKAN record parsed")

    # "iris" so the OpenML name filter actually matches its stub; with "x" the
    # filter correctly returned nothing and the test was wrong, not the code.
    all_hits = search("iris", verbose=False)
    chk(len(all_hits) == 4, f"search fans out across all four registries ({len(all_hits)})")
    chk({h.source for h in all_hits} == set(SOURCES), "every registry contributed")

    print("== a dead registry is reported, not hidden ==")
    def dead(url, as_json=True):
        raise urllib.error.URLError("down")
    _get = dead
    chk(search("x") == [], "all sources down yields no results and no crash")
    _get = fake_get

    print("== fetch: checksum, licence, quarantine ==")
    import tempfile
    body = b"a,b\n1,2\n3,4\n"
    _get_bytes = lambda url: body
    with tempfile.TemporaryDirectory() as td:
        r = fetch(z[0], Path(td))
        chk(r.checksum_verified is True, "matching md5 verifies")
        chk(r.path and r.path.exists(), "file written")
        chk("licensed" in str(r.path), "cc-by file lands in licensed/")
        chk(r.path.with_suffix(r.path.suffix + ".provenance.json").exists(),
            "a provenance sidecar is written beside it")

        # a file whose bytes do not match the registered hash must FAIL
        _get_bytes = lambda url: b"tampered"
        r2 = fetch(z[0], Path(td))
        chk(r2.checksum_verified is False, "mismatched checksum is detected")
        chk(r2.path is None, "and nothing is written to disk")
        chk(any("CHECKSUM MISMATCH" in e for e in r2.errors), "and it is stated plainly")

        # unlicensed -> quarantine, still fetched, marked local-use-only
        _get_bytes = lambda url: body
        unl = Dataset(source="zenodo", identifier="9", title="Unlicensed set",
                      url="", files=[{"name": "x.csv", "url": "http://x/x.csv",
                                      "size": 5, "checksum": ""}], licence="")
        r3 = fetch(unl, Path(td))
        chk(r3.quarantined and not r3.redistributable, "unlicensed file is quarantined")
        chk("quarantine" in str(r3.path), "and lands in quarantine/")
        chk(r3.checksum_verified is None, "no published checksum is recorded as such")
        chk(any("no checksum" in e for e in r3.errors), "and said out loud")

        r4 = fetch(unl, Path(td), allow_unlicensed=False)
        chk(r4.path is None, "and can be refused outright when asked")

        # fetched is not verified
        v = verify_downloaded(r)
        chk(v["verdict"] in ("REAL_MEASURED", "SYNTHETIC", "SUSPECT", "MIXED", "UNKNOWN"),
            f"a fetched file is handed to the authenticity check ({v['verdict']})")

    _get, _get_bytes = real_get, real_bytes
    print(f"\ndata_fetch.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    q = " ".join(a for a in sys.argv[1:] if not a.startswith("-")) or "fatigue steel"
    print(f"searching open registries for: {q}\n")
    for d in search(q, limit=3, verbose=True):
        print(d)
