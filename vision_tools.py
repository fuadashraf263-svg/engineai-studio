#!/usr/bin/env python3
"""Explicit, provenance-preserving vision calls for EngineAI project files.

Images stay local until the caller sets ``confirm_external_send=True`` at the
Project Hub endpoint.  A vision response is an unverified interpretation, not
a source of dimensions, safety limits or manufacturing approval.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path


MAX_VISION_BYTES = int(os.environ.get("ENGINEAI_VISION_MAX_BYTES", 8 * 1024 * 1024))
DEFAULT_MODEL = os.environ.get("ENGINEAI_VISION_MODEL", "nvidia/nemotron-nano-12b-v2-vl:free")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def image_message(path: Path, prompt: str) -> list[dict[str, object]]:
    path = path.resolve()
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError("vision accepts PNG, JPG, JPEG or WEBP only")
    size = path.stat().st_size
    if not 0 < size <= MAX_VISION_BYTES:
        raise ValueError(f"image must be between 1 byte and {MAX_VISION_BYTES // (1024 * 1024)} MiB")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    instruction = (prompt.strip() or "Describe the visible content.") + (
        "\n\nEngineering safety rules: distinguish visible facts from inference; do not invent dimensions, "
        "materials, performance figures, standards, or manufacturability. State what source data is missing.")
    return [{"role": "user", "content": [
        {"type": "text", "text": instruction},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
    ]}]


def analyze(path: Path, prompt: str, *, provider: str = "openrouter", model: str | None = None) -> dict[str, str]:
    """Send one confirmed image request; raises instead of silently falling back."""
    import providers
    selected = model or DEFAULT_MODEL
    text = providers.complete(
        "You are an engineering-image assistant. Return concise plain text with sections: visible facts, "
        "uncertain inferences, missing information, and safety/provenance notes.",
        image_message(path, prompt), provider=provider, model=selected, timeout=120,
        json_mode=False,
    )
    return {"provider": provider, "model": selected, "result": text}


def selftest() -> bool:
    import tempfile
    try:
        with tempfile.TemporaryDirectory(prefix="engineai_vision_") as temp:
            path = Path(temp) / "pixel.png"
            # A valid 1×1 PNG; the test constructs payload only and never calls a provider.
            path.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL5mQAAAABJRU5ErkJggg=="))
            messages = image_message(path, "read this drawing")
            url = messages[0]["content"][1]["image_url"]["url"]  # type: ignore[index]
            assert str(url).startswith("data:image/png;base64,")
            assert "do not invent dimensions" in str(messages[0]["content"][0])
        print("vision tools selftest: PASSED")
        return True
    except Exception as exc:
        print(f"vision tools selftest: FAILED: {type(exc).__name__}: {exc}")
        return False


def live_smoke() -> bool:
    """One non-sensitive provider smoke test using a generated 64×64 image."""
    import tempfile
    try:
        with tempfile.TemporaryDirectory(prefix="engineai_vision_live_") as temp:
            path = Path(temp) / "pixel.png"
            from PIL import Image
            Image.new("RGB", (64, 64), color=(128, 128, 128)).save(path, format="PNG")
            response = analyze(path, "State only that this is a deliberately blank gray test image.")
            assert response["result"].strip()
            print(json.dumps({"provider": response["provider"], "model": response["model"],
                              "result_preview": response["result"][:300]}, indent=2))
        print("vision tools live smoke: PASSED")
        return True
    except Exception as exc:
        print(f"vision tools live smoke: FAILED: {type(exc).__name__}: {exc}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--live-smoke", action="store_true", help="send a generated blank pixel; uses a real provider")
    args = parser.parse_args()
    raise SystemExit(0 if (live_smoke() if args.live_smoke else selftest()) else 1)
