"""Fixture-agnostic 3D room rig and deterministic voxel compilation.

The rig is the physical truth of the installation.  A fixture is expanded into
one or more *voxels* (point emitters or pixels on a line).  Every downstream
component -- presets, filters, compositor, preview, HAL -- addresses those
voxels by index, which is what makes existing shows automatically work on new
fixtures added to the rig.

Coordinate convention: metres, right-handed, Z up.  Rooms are axis-aligned and
normalised voxel coordinates are ``(position - room.min) / room.size``.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

ColorSpace = Literal["cie_xy", "rgb", "rgb_amber_1800k", "dimmer_only"]
OutputProtocol = Literal["none", "hue_z2m", "wled_ddp", "e131", "ha_light"]
GroupType = Literal["all", "list", "spatial_box", "spatial_sphere"]
GeometryType = Literal["point", "line"]


def _finite_vec3(value: list[float]) -> list[float]:
    if len(value) != 3:
        raise ValueError("expected exactly three coordinates")
    if any(not math.isfinite(float(v)) for v in value):
        raise ValueError("coordinates must be finite")
    return [float(v) for v in value]


Vec3 = Annotated[
    list[float],
    Field(min_length=3, max_length=3),
    AfterValidator(_finite_vec3),
]
PositiveFloat = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]


class Model(BaseModel):
    """Project-wide Pydantic base: reject unknown keys so typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


class Room(Model):
    """Axis-aligned loft volume in metres."""

    min: Vec3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    max: Vec3 = Field(default_factory=lambda: [6.5, 8.0, 5.1])

    @model_validator(mode="after")
    def _check_extent(self) -> "Room":
        if any(hi <= lo for lo, hi in zip(self.min, self.max)):
            raise ValueError("room.max must be greater than room.min on every axis")
        return self

    @property
    def size(self) -> np.ndarray:
        return np.asarray(self.max, dtype=np.float64) - np.asarray(
            self.min, dtype=np.float64
        )

    @property
    def center(self) -> np.ndarray:
        return (
            np.asarray(self.max, dtype=np.float64)
            + np.asarray(self.min, dtype=np.float64)
        ) / 2.0

    def contains(
        self, position: np.ndarray | list[float], tolerance: float = 1e-6
    ) -> bool:
        p = np.asarray(position, dtype=np.float64)
        lo = np.asarray(self.min) - tolerance
        hi = np.asarray(self.max) + tolerance
        return bool(np.all(p >= lo) and np.all(p <= hi))


class PointGeometry(Model):
    type: Literal["point"] = "point"
    pos: Vec3
    aim: Vec3 = Field(default_factory=lambda: [0.0, 0.0, -1.0])
    beam_deg: Annotated[float, Field(gt=0.0, lt=180.0, allow_inf_nan=False)] = 45.0

    @model_validator(mode="after")
    def _check_aim(self) -> "PointGeometry":
        if float(np.linalg.norm(self.aim)) < 1e-9:
            raise ValueError("aim must be a non-zero direction")
        return self


class LineGeometry(Model):
    type: Literal["line"] = "line"
    start: Vec3
    end: Vec3
    pixels: Annotated[int, Field(ge=2, le=4096)] = 180
    aim: Vec3 = Field(default_factory=lambda: [0.0, 0.0, -1.0])
    beam_deg: Annotated[float, Field(gt=0.0, lt=180.0, allow_inf_nan=False)] = 120.0

    @model_validator(mode="after")
    def _check_line(self) -> "LineGeometry":
        if float(np.linalg.norm(np.asarray(self.end) - np.asarray(self.start))) < 1e-9:
            raise ValueError("line start and end must differ")
        if float(np.linalg.norm(self.aim)) < 1e-9:
            raise ValueError("aim must be a non-zero direction")
        return self


Geometry = Annotated[PointGeometry | LineGeometry, Field(discriminator="type")]


class Capabilities(Model):
    color_space: ColorSpace = "rgb"
    max_fps: Annotated[float, Field(gt=0.0, le=240.0, allow_inf_nan=False)] = 30.0
    zcl_snap: bool = False
    min_brightness: Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)] = 0.0


class FixtureOutput(Model):
    """Where and how a fixture is driven.

    ``protocol`` is intentionally separate from ``color_space``: a WLED strip
    with an amber channel still uses the DDP/UDP transport.
    """

    protocol: OutputProtocol = "none"
    enabled: bool = True
    friendly_name: str | None = None
    address: str | int | None = None
    proxy: str | None = None
    host: str | None = None
    port: Annotated[int, Field(ge=1, le=65535)] | None = None
    universe: Annotated[int, Field(ge=1, le=63999)] = 1
    channel: Annotated[int, Field(ge=1, le=512)] = 1
    entity_id: str | None = None

    @property
    def address_int(self) -> int:
        """Parse Hue short addresses such as ``"0x8B3C"`` safely."""
        if isinstance(self.address, int):
            return self.address
        if isinstance(self.address, str) and self.address.strip():
            try:
                return int(self.address, 0)
            except ValueError:
                return 0
        return 0


class FixtureTransform(Model):
    """Runtime-editable fixture transform applied to its local geometry.

    Order: per-axis scale, XYZ Euler rotation (degrees), then translate, with
    rotation/scale anchored at ``pivot``.  A point's ``geometry.pos`` acts as
    its local origin; a line's endpoints are transformed in local space.
    Identity means "use geometry as authored".
    """

    translate: Vec3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotate_deg: Vec3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    pivot: Vec3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    scale: Vec3 = Field(default_factory=lambda: [1.0, 1.0, 1.0])

    @field_validator("scale")
    @classmethod
    def _positive_scale(cls, value: list[float]) -> list[float]:
        if any(v <= 0.0 for v in value):
            raise ValueError("fixture scale must be positive on every axis")
        return value


class Fixture(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    name: str = Field(min_length=1)
    geometry: Geometry
    transform: FixtureTransform = Field(default_factory=FixtureTransform)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    output: FixtureOutput = Field(default_factory=FixtureOutput)
    tags: list[str] = Field(default_factory=list)
    notes: str = ""

    @property
    def voxel_count(self) -> int:
        if isinstance(self.geometry, LineGeometry):
            return int(self.geometry.pixels)
        return 1


class Group(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    name: str = Field(min_length=1)
    type: GroupType
    fixtures: list[str] = Field(default_factory=list)
    min: Vec3 | None = None
    max: Vec3 | None = None
    center: Vec3 | None = None
    radius: PositiveFloat | None = None

    @model_validator(mode="after")
    def _check_definition(self) -> "Group":
        if self.type == "list" and not self.fixtures:
            raise ValueError("list groups need at least one fixture id")
        if self.type == "spatial_box":
            if self.min is None or self.max is None:
                raise ValueError("spatial_box groups need min and max")
            if any(hi <= lo for lo, hi in zip(self.min, self.max)):
                raise ValueError(
                    "spatial_box max must be greater than min on every axis"
                )
        if self.type == "spatial_sphere":
            if self.center is None or self.radius is None:
                raise ValueError("spatial_sphere groups need center and radius")
        return self


class Rig(Model):
    schema_version: Literal[1] = 1
    id: str = Field(default="hueme_loft", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    name: str = Field(default="Hueme Loft", min_length=1)
    room: Room = Field(default_factory=Room)
    fixtures: list[Fixture] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_ids(self) -> "Rig":
        fixture_ids = [f.id for f in self.fixtures]
        if len(fixture_ids) != len(set(fixture_ids)):
            raise ValueError("fixture ids must be unique")
        group_ids = [g.id for g in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("group ids must be unique")
        known = set(fixture_ids)
        for group in self.groups:
            missing = [fid for fid in group.fixtures if fid not in known]
            if missing:
                raise ValueError(
                    f"group '{group.id}' references unknown fixtures: {missing}"
                )
        return self

    def compile(self) -> "CompiledRig":
        return compile_rig(self)

    @property
    def fixture_ids(self) -> list[str]:
        return [f.id for f in self.fixtures]

    def with_fixture(
        self, fixture: Fixture, *, group_ids: list[str] | None = None
    ) -> "Rig":
        """Return a validated copy with a fixture added and optionally assigned."""
        if any(f.id == fixture.id for f in self.fixtures):
            raise ValueError(f"fixture '{fixture.id}' already exists")
        payload = self.model_dump(mode="json")
        payload["fixtures"].append(fixture.model_dump(mode="json"))
        updated = Rig.model_validate(payload)
        if group_ids:
            for group_id in group_ids:
                updated = updated.assign_to_group([fixture.id], group_id)
        return updated

    def without_fixture(self, fixture_id: str) -> "Rig":
        """Remove a fixture and prune it from list groups (empty lists are dropped)."""
        if fixture_id not in self.fixture_ids:
            raise KeyError(f"fixture '{fixture_id}' not found")
        payload = self.model_dump(mode="json")
        payload["fixtures"] = [f for f in payload["fixtures"] if f["id"] != fixture_id]
        groups = []
        for group in payload["groups"]:
            if group["type"] == "list":
                group["fixtures"] = [
                    fid for fid in group["fixtures"] if fid != fixture_id
                ]
                if not group["fixtures"]:
                    continue
            groups.append(group)
        payload["groups"] = groups
        return Rig.model_validate(payload)

    def with_group(self, group: Group) -> "Rig":
        """Upsert a logical or spatial group."""
        if group.id == "all" and group.type != "all":
            raise ValueError("the 'all' group must remain an all-fixtures group")
        payload = self.model_dump(mode="json")
        payload["groups"] = [g for g in payload["groups"] if g["id"] != group.id]
        payload["groups"].append(group.model_dump(mode="json"))
        return Rig.model_validate(payload)

    def without_group(self, group_id: str) -> "Rig":
        if group_id == "all":
            raise ValueError("the implicit 'all' group cannot be deleted")
        if not any(g.id == group_id for g in self.groups):
            raise KeyError(f"group '{group_id}' not found")
        payload = self.model_dump(mode="json")
        payload["groups"] = [g for g in payload["groups"] if g["id"] != group_id]
        return Rig.model_validate(payload)

    def assign_to_group(self, fixture_ids: list[str], group_id: str) -> "Rig":
        """Add fixture ids to an explicit list group (used by drag-to-group)."""
        if group_id == "all":
            return self.model_copy(deep=True)
        group = next((g for g in self.groups if g.id == group_id), None)
        if group is None:
            raise KeyError(f"group '{group_id}' not found")
        if group.type != "list":
            raise ValueError("fixtures can only be explicitly assigned to list groups")
        known = set(self.fixture_ids)
        missing = [fid for fid in fixture_ids if fid not in known]
        if missing:
            raise KeyError(f"unknown fixtures: {missing}")
        merged = list(dict.fromkeys([*group.fixtures, *fixture_ids]))
        return self.with_group(group.model_copy(update={"fixtures": merged}))


@dataclass(slots=True)
class CompiledRig:
    """Immutable, numeric projection of a :class:`Rig`.

    Arrays are indexed by the global voxel index.  A fixture occupies a
    contiguous slice in rig order; a line's pixels are ordered start -> end.
    """

    rig: Rig
    positions: np.ndarray
    normalized_positions: np.ndarray
    fixture_ids: tuple[str, ...]
    fixture_index: dict[str, int]
    fixture_slices: dict[str, slice]
    fixture_aims: dict[str, np.ndarray]
    voxel_fixture_index: np.ndarray
    voxel_local_index: np.ndarray
    groups: dict[str, np.ndarray]

    @property
    def voxel_count(self) -> int:
        return int(self.positions.shape[0])

    @property
    def fixture_count(self) -> int:
        return len(self.fixture_ids)

    @property
    def room(self) -> Room:
        return self.rig.room

    @property
    def group_ids(self) -> tuple[str, ...]:
        return tuple(self.groups)

    def group_indices(self, group_id: str) -> np.ndarray:
        try:
            return self.groups[group_id]
        except KeyError as exc:
            available = ", ".join(sorted(self.groups))
            raise KeyError(
                f"unknown group '{group_id}' (available: {available})"
            ) from exc

    def fixture_voxels(self, fixture_id: str) -> np.ndarray:
        try:
            return self.fixture_slices[fixture_id]
        except KeyError as exc:
            raise KeyError(f"unknown fixture '{fixture_id}'") from exc

    def fixture_positions(self, fixture_id: str) -> np.ndarray:
        return self.positions[self.fixture_voxels(fixture_id)]

    def fixture_rgb(self, rgb: np.ndarray, fixture_id: str) -> np.ndarray:
        return rgb[self.fixture_voxels(fixture_id)]

    def fixture_luminance(self, rgb: np.ndarray, fixture_id: str) -> np.ndarray:
        px = self.fixture_rgb(rgb, fixture_id).astype(np.float64)
        return 0.2126 * px[..., 0] + 0.7152 * px[..., 1] + 0.0722 * px[..., 2]

    def group_rank(self, group_id: str) -> np.ndarray:
        """Return 0..n-1 rank inside a group and -1 for voxels outside it."""
        indices = self.group_indices(group_id)
        ranks = np.full(self.voxel_count, -1, dtype=np.int32)
        ranks[indices] = np.arange(indices.size, dtype=np.int32)
        return ranks

    def to_dict(self) -> dict[str, Any]:
        return self.rig.model_dump(mode="json")


def _rotation_matrix(rotate_deg: list[float]) -> np.ndarray:
    rx, ry, rz = (math.radians(float(v)) for v in rotate_deg)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    rot_x = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]], dtype=np.float64)
    rot_y = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]], dtype=np.float64)
    rot_z = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
    return rot_z @ rot_y @ rot_x


def _apply_fixture_transform(
    points: np.ndarray, transform: FixtureTransform
) -> np.ndarray:
    scale = np.asarray(transform.scale, dtype=np.float64)
    pivot = np.asarray(transform.pivot, dtype=np.float64)
    rotation = _rotation_matrix(transform.rotate_deg)
    relative = np.asarray(points, dtype=np.float64) - pivot
    return (
        (relative * scale) @ rotation.T
        + pivot
        + np.asarray(transform.translate, dtype=np.float64)
    )


def _fixture_aim(fixture: Fixture) -> np.ndarray:
    aim = np.asarray(fixture.geometry.aim, dtype=np.float64)
    rotation = _rotation_matrix(fixture.transform.rotate_deg)
    out = rotation @ aim
    norm = float(np.linalg.norm(out))
    return (out / norm if norm > 1e-9 else np.asarray([0.0, 0.0, -1.0])).astype(
        np.float32
    )


def _expand_fixture(fixture: Fixture) -> np.ndarray:
    geometry = fixture.geometry
    if isinstance(geometry, PointGeometry):
        points = np.asarray([geometry.pos], dtype=np.float64)
    else:
        start = np.asarray(geometry.start, dtype=np.float64)
        end = np.asarray(geometry.end, dtype=np.float64)
        points = np.linspace(
            start, end, int(geometry.pixels), endpoint=True, dtype=np.float64
        )
    return _apply_fixture_transform(points, fixture.transform)


def compile_rig(rig: Rig) -> CompiledRig:
    """Expand every fixture into voxels and resolve all logical/spatial groups."""
    positions: list[np.ndarray] = []
    fixture_slices: dict[str, slice] = {}
    fixture_index: dict[str, int] = {}
    fixture_aims: dict[str, np.ndarray] = {}
    voxel_fixture: list[np.ndarray] = []
    voxel_local: list[np.ndarray] = []

    offset = 0
    for fi, fixture in enumerate(rig.fixtures):
        pts = _expand_fixture(fixture)
        n = int(pts.shape[0])
        fixture_index[fixture.id] = fi
        fixture_slices[fixture.id] = slice(offset, offset + n)
        fixture_aims[fixture.id] = _fixture_aim(fixture)
        positions.append(pts)
        voxel_fixture.append(np.full(n, fi, dtype=np.int32))
        voxel_local.append(np.arange(n, dtype=np.int32))
        offset += n

    if positions:
        all_positions = np.concatenate(positions, axis=0).astype(np.float32)
        vfixture = np.concatenate(voxel_fixture)
        vlocal = np.concatenate(voxel_local)
    else:
        all_positions = np.zeros((0, 3), dtype=np.float32)
        vfixture = np.zeros((0,), dtype=np.int32)
        vlocal = np.zeros((0,), dtype=np.int32)

    room_min = np.asarray(rig.room.min, dtype=np.float64)
    room_size = rig.room.size
    norm = ((all_positions.astype(np.float64) - room_min) / room_size).astype(
        np.float32
    )

    groups: dict[str, np.ndarray] = {}
    for group in rig.groups:
        if group.type == "all":
            idx = np.arange(all_positions.shape[0], dtype=np.int32)
        elif group.type == "list":
            parts = [
                np.arange(
                    fixture_slices[fid].start, fixture_slices[fid].stop, dtype=np.int32
                )
                for fid in group.fixtures
            ]
            idx = np.concatenate(parts) if parts else np.zeros((0,), dtype=np.int32)
        elif group.type == "spatial_box":
            assert group.min is not None and group.max is not None
            lo = np.asarray(group.min, dtype=np.float32)
            hi = np.asarray(group.max, dtype=np.float32)
            inside = np.all((all_positions >= lo) & (all_positions <= hi), axis=1)
            idx = np.flatnonzero(inside).astype(np.int32)
        elif group.type == "spatial_sphere":
            assert group.center is not None and group.radius is not None
            centre = np.asarray(group.center, dtype=np.float32)
            dist = np.linalg.norm(all_positions - centre, axis=1)
            idx = np.flatnonzero(dist <= float(group.radius)).astype(np.int32)
        else:  # pragma: no cover - guarded by the Pydantic literal
            continue
        groups[group.id] = np.unique(idx)

    # "all" is always available, even for hand-written rigs that omit it.
    groups.setdefault("all", np.arange(all_positions.shape[0], dtype=np.int32))

    return CompiledRig(
        rig=rig,
        positions=all_positions,
        normalized_positions=norm,
        fixture_ids=tuple(f.id for f in rig.fixtures),
        fixture_index=fixture_index,
        fixture_slices=fixture_slices,
        fixture_aims=fixture_aims,
        voxel_fixture_index=vfixture,
        voxel_local_index=vlocal,
        groups=groups,
    )


def load_rig(path: str | os.PathLike[str]) -> Rig:
    """Load and validate a rig preset file.  Raises a readable JSON/Pydantic error."""
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return Rig.model_validate(payload)


def save_rig(rig: Rig, path: str | os.PathLike[str]) -> None:
    """Atomically persist a rig so a failed save can never truncate the live one."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(rig.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
    )
    fd, tmp = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Loadable rig presets: the installation layout is runtime data, never code.
# ---------------------------------------------------------------------------
class RigPresetEntry(Model):
    id: str
    name: str
    file: str
    fixture_count: int
    voxel_count: int
    room_min: list[float]
    room_max: list[float]


class RigPresetIndex(Model):
    schema_version: Literal[1] = 1
    active: str | None = None
    presets: list[RigPresetEntry] = Field(default_factory=list)


def _slug(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in text).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "rig"


class RigLibrary:
    """CRUD for arbitrary, user-created rig presets.

    ``config/rigs/index.json`` holds the list and the active preset.  Every
    preset is a complete, self-contained ``Rig`` document, so a studio can be
    moved between a loft, a gallery or a temporary installation without code
    changes.  The shipped Hueme layout is simply one preset.
    """

    def __init__(self, rigs_dir: str | os.PathLike[str] = "config/rigs") -> None:
        self.rigs_dir = Path(rigs_dir)
        self.rigs_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.rigs_dir / "index.json"
        self._lock = threading.RLock()
        self._entries: dict[str, RigPresetEntry] = {}
        self.active_id: str | None = None
        self._load_index()

    # -- index ------------------------------------------------------------
    def _load_index(self) -> None:
        indexed = False
        if self.index_path.exists():
            try:
                index = RigPresetIndex.model_validate_json(
                    self.index_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                index = None
            if index is not None:
                indexed = True
                for entry in index.presets:
                    path = self.rigs_dir / entry.file
                    if path.exists():
                        self._entries[entry.id] = entry
                self.active_id = index.active if index.active in self._entries else None
        if not indexed or not self._entries:
            self._rebuild_from_files()
        if self.active_id not in self._entries:
            self.active_id = next(iter(self._entries), None)
        self._persist_index()

    def _rebuild_from_files(self) -> None:
        self._entries.clear()
        for path in sorted(self.rigs_dir.glob("*.json")):
            if path.name == "index.json":
                continue
            try:
                rig = load_rig(path)
            except (OSError, ValueError):
                continue
            self._entries[rig.id] = self._entry_for(rig, path.name)

    def _entry_for(self, rig: Rig, filename: str | None = None) -> RigPresetEntry:
        voxels = sum(f.voxel_count for f in rig.fixtures)
        return RigPresetEntry(
            id=rig.id,
            name=rig.name,
            file=filename or f"{rig.id}.json",
            fixture_count=len(rig.fixtures),
            voxel_count=voxels,
            room_min=list(rig.room.min),
            room_max=list(rig.room.max),
        )

    def _persist_index(self) -> None:
        payload = RigPresetIndex(
            active=self.active_id,
            presets=sorted(self._entries.values(), key=lambda e: e.name.lower()),
        ).model_dump(mode="json")
        target = self.index_path
        fd, tmp = tempfile.mkstemp(
            prefix=".index.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def refresh(self) -> list[dict[str, Any]]:
        with self._lock:
            current_active = self.active_id
            self._rebuild_from_files()
            self.active_id = (
                current_active
                if current_active in self._entries
                else next(iter(self._entries), None)
            )
            self._persist_index()
            return [e.model_dump(mode="json") for e in self._entries.values()]

    # -- reads ------------------------------------------------------------
    def list_presets(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                entry.model_dump(mode="json")
                for entry in sorted(
                    self._entries.values(), key=lambda e: e.name.lower()
                )
            ]

    def get_entry(self, preset_id: str | None = None) -> RigPresetEntry:
        with self._lock:
            pid = preset_id or self.active_id
            if not pid or pid not in self._entries:
                raise KeyError(f"rig preset '{pid}' not found")
            return self._entries[pid].model_copy(deep=True)

    def load(self, preset_id: str | None = None) -> Rig:
        with self._lock:
            entry = self.get_entry(preset_id)
            rig = load_rig(self.rigs_dir / entry.file)
        if rig.id != entry.id:
            raise ValueError(f"rig preset '{entry.id}' contains id '{rig.id}'")
        return rig

    def load_active(self) -> Rig:
        return self.load(self.active_id)

    def to_compiled(self, preset_id: str | None = None) -> CompiledRig:
        return self.load(preset_id).compile()

    # -- writes -----------------------------------------------------------
    def save(self, rig: Rig, *, set_active: bool = False) -> RigPresetEntry:
        with self._lock:
            # Re-validate the full document: UI/MCP code may have mutated fields
            # on an existing model instance, bypassing assignment validation.
            validated = Rig.model_validate(rig.model_dump(mode="json"))
            save_rig(validated, self.rigs_dir / f"{validated.id}.json")
            entry = self._entry_for(validated)
            self._entries[validated.id] = entry
            if set_active or self.active_id is None:
                self.active_id = validated.id
            self._persist_index()
            return entry.model_copy(deep=True)

    def create(
        self,
        name: str,
        *,
        preset_id: str | None = None,
        room: Room | None = None,
        fixtures: list[Fixture] | None = None,
        groups: list[Group] | None = None,
        set_active: bool = False,
    ) -> Rig:
        with self._lock:
            base_id = preset_id or _slug(name)
            candidate = base_id
            n = 2
            while candidate in self._entries:
                candidate = f"{base_id}_{n}"
                n += 1
            rig = Rig(
                id=candidate,
                name=name,
                room=room or Room(),
                fixtures=fixtures or [],
                groups=groups
                if groups is not None
                else [Group(id="all", name="All Fixtures", type="all")],
            )
            self.save(rig, set_active=set_active)
            return rig.model_copy(deep=True)

    def clone(self, source_id: str, new_id: str, name: str | None = None) -> Rig:
        with self._lock:
            if new_id in self._entries:
                raise KeyError(f"rig preset '{new_id}' already exists")
            source = self.load(source_id)
            payload = source.model_dump(mode="json")
            payload["id"] = new_id
            payload["name"] = name or f"{source.name} Copy"
            clone = Rig.model_validate(payload)
            self.save(clone)
            return clone.model_copy(deep=True)

    def delete(self, preset_id: str) -> None:
        with self._lock:
            entry = self.get_entry(preset_id)
            (self.rigs_dir / entry.file).unlink(missing_ok=True)
            self._entries.pop(preset_id, None)
            if self.active_id == preset_id:
                self.active_id = next(iter(self._entries), None)
            self._persist_index()

    def set_active(self, preset_id: str) -> Rig:
        with self._lock:
            if preset_id not in self._entries:
                raise KeyError(f"rig preset '{preset_id}' not found")
            self.active_id = preset_id
            self._persist_index()
            return self.load(preset_id)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active": self.active_id,
                "presets": self.list_presets(),
            }


def load_active_rig(rigs_dir: str | os.PathLike[str] = "config/rigs") -> Rig:
    return RigLibrary(rigs_dir).load_active()


__all__ = [
    "Capabilities",
    "CompiledRig",
    "Fixture",
    "FixtureOutput",
    "FixtureTransform",
    "Geometry",
    "Group",
    "LineGeometry",
    "PointGeometry",
    "Rig",
    "RigLibrary",
    "RigPresetEntry",
    "RigPresetIndex",
    "Room",
    "compile_rig",
    "load_active_rig",
    "load_rig",
    "save_rig",
]
