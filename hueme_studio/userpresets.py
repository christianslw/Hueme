"""Global user looks: preset, parameters, mask and filter stack."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from pydantic import Field

from hueme_studio.showdoc import FilterSpec, Model, SpatialMask

log = logging.getLogger("hueme_studio.userpresets")
PRESET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class UserPreset(Model):
    id: str = ""
    name: str = Field(min_length=1, max_length=80)
    preset: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    spatial_mask: SpatialMask = Field(default_factory=SpatialMask)
    filters: list[FilterSpec] = Field(default_factory=list)


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return text[:48] or "look"


class UserPresetStore:
    def __init__(self, path: str | os.PathLike[str], *, persist: bool = True) -> None:
        self.path = Path(path)
        self.persist = bool(persist)
        self._lock = threading.RLock()
        self._entries: dict[str, UserPreset] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable user preset library %s: %s", self.path, exc)
            return
        entries = payload.get("presets", []) if isinstance(payload, dict) else payload
        for entry in entries or []:
            try:
                preset = UserPreset.model_validate(entry)
            except ValueError:
                continue
            if preset.id and PRESET_ID.match(preset.id):
                self._entries[preset.id] = preset

    def _persist(self) -> None:
        if not self.persist:
            return
        payload = {
            "schema_version": 1,
            "presets": [entry.model_dump(mode="json") for entry in self._entries.values()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                entry.model_dump(mode="json")
                for entry in sorted(self._entries.values(), key=lambda item: item.name.lower())
            ]

    def get(self, preset_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(preset_id)
            return entry.model_dump(mode="json") if entry else None

    def upsert(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        if not data.get("id"):
            base = _slug(str(data.get("name") or "look"))
            data["id"] = base
            suffix = 1
            while data["id"] in self._entries:
                suffix += 1
                data["id"] = f"{base}_{suffix}"
        preset = UserPreset.model_validate(data)
        if not PRESET_ID.match(preset.id):
            raise ValueError("invalid user preset id")
        with self._lock:
            self._entries[preset.id] = preset
            self._persist()
            return preset.model_dump(mode="json")

    def delete(self, preset_id: str) -> None:
        with self._lock:
            if preset_id not in self._entries:
                raise KeyError(f"user preset '{preset_id}' not found")
            del self._entries[preset_id]
            self._persist()


__all__ = ["UserPreset", "UserPresetStore"]
