"""Global colour palette library.

Built-in palettes keep the original cosine gradients used by the presets.
User palettes are discrete Oklab-interpolated stops stored in one JSON file
next to the shows directory.  The registry is module-level because preset
rendering is stateless and called from worker threads.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

import numpy as np

from hueme_studio.showdoc import lerp_oklab

PALETTE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
MAX_COLORS = 16
MIN_COLORS = 2

BUILTIN_COSINE: dict[str, tuple[tuple[float, float, float], ...]] = {
    "ember": (
        (0.55, 0.18, 0.05),
        (0.40, 0.25, 0.05),
        (1.0, 1.0, 1.0),
        (0.00, 0.10, 0.15),
    ),
    "synth": (
        (0.50, 0.12, 0.50),
        (0.50, 0.10, 0.50),
        (1.0, 1.0, 1.0),
        (0.00, 0.25, 0.50),
    ),
    "sunset": (
        (0.60, 0.20, 0.12),
        (0.35, 0.18, 0.10),
        (1.0, 1.0, 1.0),
        (0.00, 0.05, 0.10),
    ),
    "moonlight": (
        (0.30, 0.40, 0.55),
        (0.25, 0.25, 0.30),
        (1.0, 1.0, 1.0),
        (0.10, 0.20, 0.30),
    ),
    "acid": (
        (0.35, 0.50, 0.15),
        (0.30, 0.40, 0.15),
        (1.0, 1.0, 1.0),
        (0.00, 0.10, 0.20),
    ),
    "ice": (
        (0.35, 0.55, 0.70),
        (0.20, 0.25, 0.25),
        (1.0, 1.0, 1.0),
        (0.00, 0.10, 0.20),
    ),
    "aurora": (
        (0.25, 0.55, 0.45),
        (0.30, 0.30, 0.35),
        (1.0, 1.0, 1.0),
        (0.00, 0.15, 0.25),
    ),
}

BUILTIN_NAMES = {
    "synth": "Nebula Silk",
    "ember": "Molten Amber",
    "sunset": "Golden Hour",
    "ice": "Glacier Cave",
    "acid": "Emerald Canopy",
    "moonlight": "Bioluminescent Reef",
    "aurora": "Aurora Veil",
}

_CUSTOM_COLORS: dict[str, list[list[int]]] = {}
_REGISTRY_LOCK = threading.RLock()
log = logging.getLogger("hueme_studio.palettes")


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def cosine_stops(
    coeffs: tuple[tuple[float, float, float], ...], count: int = 7
) -> list[list[int]]:
    a, b, c, d = coeffs
    stops: list[list[int]] = []
    for index in range(max(2, count)):
        phase = index / (count - 1)
        rgb = [
            _clamp01(a[channel] + b[channel] * math.cos(
                2.0 * math.pi * (c[channel] * phase + d[channel])
            ))
            for channel in range(3)
        ]
        stops.append([int(round(value * 255.0)) for value in rgb])
    return stops


def builtin_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": key,
            "name": BUILTIN_NAMES[key],
            "colors": cosine_stops(coeffs),
            "builtin": True,
        }
        for key, coeffs in BUILTIN_COSINE.items()
    ]


def set_custom_palettes(palettes: dict[str, list[list[int]]]) -> None:
    with _REGISTRY_LOCK:
        _CUSTOM_COLORS.clear()
        _CUSTOM_COLORS.update(
            {key: [list(color) for color in colors] for key, colors in palettes.items()}
        )


def custom_palette_colors(name: str) -> list[list[int]] | None:
    with _REGISTRY_LOCK:
        colors = _CUSTOM_COLORS.get(str(name))
        return [list(color) for color in colors] if colors else None


def palette_gradient(colors: list[list[int]], t: np.ndarray | float) -> np.ndarray:
    """Sample a discrete palette with Oklab interpolation.  Period is 1.0."""
    stops = np.asarray(colors, dtype=np.float64)
    if stops.ndim != 2 or stops.shape[1] != 3 or len(stops) < 1:
        return np.zeros(np.asarray(t).shape + (3,), dtype=np.float64)
    if len(stops) == 1:
        out = np.broadcast_to(stops[0], np.asarray(t).shape + (3,)).copy()
        return np.clip(out, 0.0, 255.0)
    tt = np.mod(np.asarray(t, dtype=np.float64), 1.0)
    position = tt * (len(stops) - 1)
    index = np.floor(position).astype(np.int64)
    index = np.clip(index, 0, len(stops) - 2)
    fraction = position - index
    c0 = stops[index]
    c1 = stops[index + 1]
    return np.clip(
        lerp_oklab(c0, c1, fraction[..., None] if np.ndim(fraction) else fraction),
        0.0,
        255.0,
    )


def _clean_colors(raw: Any) -> list[list[int]]:
    if not isinstance(raw, (list, tuple)):
        raise ValueError("palette colors must be a list")
    if len(raw) < MIN_COLORS or len(raw) > MAX_COLORS:
        raise ValueError(
            f"palette needs {MIN_COLORS}..{MAX_COLORS} colours (got {len(raw)})"
        )
    colors: list[list[int]] = []
    for index, color in enumerate(raw):
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise ValueError(f"colour {index + 1} must be [r, g, b]")
        try:
            values = [int(round(float(component))) for component in color]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"colour {index + 1} is not numeric") from exc
        if any(value < 0 or value > 255 for value in values):
            raise ValueError(f"colour {index + 1} is outside 0..255")
        colors.append(values)
    return colors


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return text[:48] or "palette"


class PaletteStore:
    """Thread-safe user palette library with atomic persistence."""

    def __init__(
        self, path: str | os.PathLike[str], *, persist: bool = True
    ) -> None:
        self.path = Path(path)
        self.persist = bool(persist)
        self._lock = threading.RLock()
        self._custom: dict[str, dict[str, Any]] = {}
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            set_custom_palettes({})
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # A hand-edited/broken palette library must not stop the Studio.
            log.warning("Ignoring unreadable palette library %s: %s", self.path, exc)
            set_custom_palettes({})
            return
        entries = payload.get("palettes", []) if isinstance(payload, dict) else payload
        loaded: dict[str, dict[str, Any]] = {}
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            palette_id = str(entry.get("id") or "").strip()
            if not PALETTE_ID.match(palette_id) or palette_id in BUILTIN_COSINE:
                continue
            try:
                colors = _clean_colors(entry.get("colors"))
            except ValueError:
                continue
            loaded[palette_id] = {
                "id": palette_id,
                "name": str(entry.get("name") or palette_id)[:48],
                "colors": colors,
                "builtin": False,
            }
        with self._lock:
            self._custom = loaded
            set_custom_palettes(
                {key: item["colors"] for key, item in self._custom.items()}
            )

    def _write(self) -> None:
        if not self.persist:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "palettes": [self._custom[key] for key in sorted(self._custom)],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        fd, tmp = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # -- public API -------------------------------------------------------
    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            custom = [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "colors": [list(color) for color in item["colors"]],
                    "builtin": False,
                }
                for item in sorted(self._custom.values(), key=lambda x: x["name"].lower())
            ]
        return builtin_catalog() + custom

    def has(self, palette_id: str) -> bool:
        with self._lock:
            return palette_id in BUILTIN_COSINE or palette_id in self._custom

    def upsert(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("palette name is required")
        if len(name) > 48:
            raise ValueError("palette name is too long")
        colors = _clean_colors(payload.get("colors"))
        requested = str(payload.get("id") or "").strip()
        with self._lock:
            if requested and requested in BUILTIN_COSINE:
                raise ValueError("built-in palettes cannot be overwritten; save as a copy")
            palette_id = requested or _slug(name)
            if not PALETTE_ID.match(palette_id):
                raise ValueError("palette id is invalid")
            if not requested:
                base = palette_id
                suffix = 2
                while palette_id in BUILTIN_COSINE or palette_id in self._custom:
                    palette_id = f"{base}_{suffix}"
                    suffix += 1
            item = {
                "id": palette_id,
                "name": name,
                "colors": colors,
                "builtin": False,
            }
            self._custom[palette_id] = item
            set_custom_palettes(
                {key: value["colors"] for key, value in self._custom.items()}
            )
            self._write()
            return {
                "id": palette_id,
                "name": name,
                "colors": [list(color) for color in colors],
                "builtin": False,
            }

    def delete(self, palette_id: str) -> None:
        with self._lock:
            if palette_id in BUILTIN_COSINE:
                raise ValueError("built-in palettes cannot be deleted")
            if palette_id not in self._custom:
                raise KeyError(palette_id)
            del self._custom[palette_id]
            set_custom_palettes(
                {key: value["colors"] for key, value in self._custom.items()}
            )
            self._write()


__all__ = [
    "BUILTIN_COSINE",
    "BUILTIN_NAMES",
    "PaletteStore",
    "builtin_catalog",
    "cosine_stops",
    "custom_palette_colors",
    "palette_gradient",
    "set_custom_palettes",
]
