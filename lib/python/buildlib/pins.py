from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# The single, canonical pins file: ``<repo-root>/pins.json``. It is the only source
# of truth for what every pinned build fetches, so all builds in the repo resolve
# identical pins. Its location is hardcoded, not searched: ``pins.py`` lives at
# ``<repo-root>/lib/python/buildlib/pins.py``, so the repo root is three parents up
# from the package directory.
PINS_FILE = Path(__file__).resolve().parents[3] / "pins.json"


def load_pin_entry(name: str) -> dict[str, Any]:
    """Return the raw entry named ``name`` from the repo-root ``pins.json``.

    The committed :data:`PINS_FILE` is the only source of truth for what each pinned
    build module fetches; its location is fixed so every build in the repo sees the
    same pins. The entry's shape is owned by the consuming module -- e.g. the ROCm
    pin carries a ``version`` and a gfx-templated ``url_template`` -- so this loader
    stays schema-agnostic and only resolves the file and the top-level key. Raises
    ``KeyError`` if the pin is absent and ``FileNotFoundError`` if ``pins.json`` is
    missing.
    """
    data = json.loads(PINS_FILE.read_text(encoding="utf-8"))
    if name not in data:
        raise KeyError(f"pin {name!r} not found in {PINS_FILE} (have: {', '.join(sorted(data))})")
    return data[name]
