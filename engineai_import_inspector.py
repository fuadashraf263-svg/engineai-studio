#!/usr/bin/env python3
"""Inspect a user-supplied engineering exchange file without changing it.

Supported formats: STEP/STP, STL, DXF and IFC.  The report preserves SHA-256,
file size, detected units/schema and conservative structural metadata.  It is a
preflight check for a later converter/reviewer, not a claim of editability or
manufacturing correctness.

  conda activate eng
  python engineai_import_inspector.py --input part.step --out ~/engineai/out/part.inspect.json
  python engineai_import_inspector.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


ALLOWED = {".step", ".stp", ".stl", ".dxf", ".ifc", ".ifczip"}
MAX_BYTES = 500 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_text_head(path: Path, limit: int = 256 * 1024) -> str:
    return path.open("rb").read(limit).decode("latin-1", errors="replace")


def inspect_step(path: Path) -> dict[str, Any]:
    head = safe_text_head(path)
    if "ISO-10303-21" not in head:
        raise ValueError("not an ISO-10303-21 STEP exchange file")
    schemas = re.findall(r"FILE_SCHEMA\s*\(\s*\((.*?)\)\s*\)", head, flags=re.I | re.S)
    names = re.findall(r"'([^']+)'", schemas[0]) if schemas else []
    return {"format": "STEP", "exchange_header": True, "schemas": names,
            "editable_state": "reference_only", "converter_requirement": "round-trip test with target CAD kernel"}


def inspect_stl(path: Path) -> dict[str, Any]:
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError("trimesh is needed for STL inspection: python -m pip install trimesh") from exc
    mesh = trimesh.load_mesh(path, process=False)
    if not hasattr(mesh, "vertices"):
        raise ValueError("STL did not contain a triangle mesh")
    bounds = mesh.bounds.tolist() if len(mesh.vertices) else None
    return {"format": "STL", "vertices": int(len(mesh.vertices)), "faces": int(len(mesh.faces)),
            "bounds_native_units": bounds, "watertight": bool(mesh.is_watertight),
            "units": "unknown - STL has no standard unit field", "editable_state": "mesh/reference_only"}


def inspect_dxf(path: Path) -> dict[str, Any]:
    try:
        import ezdxf
    except ImportError as exc:
        raise RuntimeError("ezdxf is needed for DXF inspection: python -m pip install ezdxf") from exc
    doc = ezdxf.readfile(path)
    modelspace = doc.modelspace()
    entities = Counter(entity.dxftype() for entity in modelspace)
    units = doc.units
    return {"format": "DXF", "dxfversion": doc.dxfversion, "units_code": int(units),
            "units_name": str(units), "modelspace_entities": dict(sorted(entities.items())),
            "layers": [layer.dxf.name for layer in doc.layers], "editable_state": "2D exchange/reference_only"}


def property_sets(element: Any, max_values: int = 25) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for rel in getattr(element, "IsDefinedBy", []) or []:
        definition = getattr(rel, "RelatingPropertyDefinition", None)
        if not definition or not definition.is_a("IfcPropertySet"):
            continue
        values: dict[str, str] = {}
        for prop in getattr(definition, "HasProperties", []) or []:
            if len(values) >= max_values:
                break
            if prop.is_a("IfcPropertySingleValue"):
                val = getattr(prop, "NominalValue", None)
                values[str(prop.Name)] = str(getattr(val, "wrappedValue", val))
        output[str(definition.Name)] = values
    return output


def inspect_ifc(path: Path) -> dict[str, Any]:
    try:
        import ifcopenshell
    except ImportError as exc:
        raise RuntimeError("IfcOpenShell is needed: conda install -n eng -c conda-forge ifcopenshell") from exc
    model = ifcopenshell.open(str(path))
    classes = Counter(obj.is_a() for obj in model)
    elements = model.by_type("IfcElement")
    sample = []
    for element in elements[:20]:
        sample.append({"global_id": element.GlobalId, "class": element.is_a(), "name": element.Name,
                       "predefined_type": str(getattr(element, "PredefinedType", "") or ""),
                       "property_sets": property_sets(element)})
    spatial = []
    for cls in ("IfcProject", "IfcSite", "IfcBuilding", "IfcBuildingStorey"):
        for item in model.by_type(cls):
            spatial.append({"class": cls, "name": item.Name, "global_id": getattr(item, "GlobalId", None)})
    return {"format": "IFC", "schema": model.schema, "entities": len(model),
            "class_counts": dict(sorted(classes.items())), "spatial_structure": spatial,
            "element_sample": sample, "editable_state": "BIM reference/property extraction; edit via a round-trip-tested IFC workflow"}


def inspect(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix not in ALLOWED:
        raise ValueError("supported formats: " + ", ".join(sorted(ALLOWED)))
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"file exceeds {MAX_BYTES // (1024 * 1024)} MiB inspection limit")
    common: dict[str, Any] = {"schema": 1, "source_filename": path.name, "bytes": path.stat().st_size,
                              "sha256": sha256(path), "extension": suffix}
    if suffix in {".step", ".stp"}:
        details = inspect_step(path)
    elif suffix == ".stl":
        details = inspect_stl(path)
    elif suffix == ".dxf":
        details = inspect_dxf(path)
    elif suffix in {".ifc", ".ifczip"}:
        details = inspect_ifc(path)
    else:  # kept for static type completeness
        raise ValueError("unsupported format")
    return {**common, **details,
            "disclaimer": "Inspection metadata only. Verify dimensions, units, geometry, material, loads, tolerances and rights before engineering use."}


def selftest() -> bool:
    try:
        with tempfile.TemporaryDirectory(prefix="engineai_import_") as tmp:
            step = Path(tmp) / "sample.step"
            step.write_text("ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('AP214'));\nENDSEC;\nEND-ISO-10303-21;\n", encoding="ascii")
            report = inspect(step)
            assert report["format"] == "STEP" and report["schemas"] == ["AP214"] and len(report["sha256"]) == 64
        print("import inspector selftest: PASSED")
        return True
    except Exception as exc:
        print(f"import inspector selftest: FAILED: {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return 0 if selftest() else 1
    if not args.input or not args.out:
        parser.error("--input and --out are required unless --selftest is used")
    try:
        result = inspect(args.input)
        args.out.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        args.out.expanduser().resolve().write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(f"inspect: FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
