"""FastAPI transport for Hueme Studio.

The server owns one live studio session: a working-copy rig, the active show,
the playhead and the preview subscribers.  REST edits mutate the session and
are pushed to every connected client; the binary ``/ws/preview`` socket carries
RGB voxel frames plus JSON invalidations.

Preview never requires hardware.  ``Hal`` stays opt-in and runs on its own
monotonic output clock so fixture cadence does not follow the preview ticker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import secrets
import struct
import sys
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import (
    BackgroundTasks,
    Body,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hueme_studio import __version__
from hueme_studio.blueprint import BlueprintStore
from hueme_studio.compositor import Compositor, FrameResult, deterministic_envelope
from hueme_studio.expressions import (
    ExpressionError,
    evaluate_expression,
    expression_names,
)
from hueme_studio.filters import (
    FILTERS,
    FilterContext,
    filter_catalog,
)
from hueme_studio.fxpreview import preview_png, render_clip_rgb, render_preset_rgb
from hueme_studio.ha import HomeAssistantBridge
from hueme_studio.hal import Hal, HalSettings
from hueme_studio.lint import lint_show as lint_show_doc
from hueme_studio.mcp import McpBridge
from hueme_studio.palettes import PaletteStore
from hueme_studio.presets import (
    PRESETS,
    PresetContext,
    get_preset,
    preset_catalog,
)
from hueme_studio.rig import (
    CompiledRig,
    Fixture,
    Group,
    Rig,
    RigLibrary,
    RigPresetEntry,
    Room,
    fixture_incomplete_reasons,
    hue_proxy_health,
)
from hueme_studio.showdoc import (
    Clip,
    EXPRESSION_VARIABLES,
    FilterSpec,
    Keyframe,
    Marker,
    ShowDoc,
    ShowNotFound,
    ShowStore,
    ShowStoreError,
    Timing,
)
from hueme_studio.sync import MaSync
from hueme_studio.userpresets import UserPresetStore

log = logging.getLogger("hueme_studio.server")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
FRAME_MAGIC = b"HUE1"
FRAME_HEADER = struct.Struct("<4sIfI")
FRAME_FLAG_PLAYING = 1
FRAME_FLAG_BLACKOUT = 2
FRAME_FLAG_LOOP = 4
MAX_EVENT_BACKLOG = 200


class TransportPatch(BaseModel):
    """Incremental transport edit; omitted fields are left unchanged."""

    model_config = ConfigDict(extra="forbid")

    playing: bool | None = None
    time_s: float | None = Field(default=None, allow_inf_nan=False)
    loop: bool | None = None
    show_id: str | None = None
    fps: float | None = Field(default=None, allow_inf_nan=False)
    gain: float | None = Field(default=None, allow_inf_nan=False)
    blackout: bool | None = None


class RigCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    preset_id: str | None = None
    room: Room | None = None
    fixtures: list[Fixture] = Field(default_factory=list)
    groups: list[Group] | None = None
    activate: bool = True


class RigClone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_id: str
    name: str | None = None
    activate: bool = True


class RigSaveAs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class GroupAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_ids: list[str] = Field(min_length=1)


class ShowSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = "manual snapshot"


class SyncPatch(BaseModel):
    """Live MA sync tuning; omitted fields are left unchanged."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    offset_s: float | None = Field(default=None, ge=-20.0, le=20.0)


class HardwarePatch(BaseModel):
    """Toggle physical output; the HAL stays opt-in."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class FixtureSelectionTransform(BaseModel):
    """Blender-style transform for the active fixture selection."""

    model_config = ConfigDict(extra="forbid")

    fixture_ids: list[str] = Field(min_length=1)
    translate: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3)
    rotate_axis: str = "z"
    rotate_deg: float = 0.0
    scale: list[float] = Field(default_factory=lambda: [1.0, 1.0, 1.0], min_length=3, max_length=3)
    pivot: list[float] | None = Field(default=None, min_length=3, max_length=3)


class AutoKeyframesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip_id: str | None = None
    source: str = "kick"
    param: str = "opacity"
    division: str = "1/4"
    depth: float = Field(default=0.65, ge=0.0, le=1.0)
    threshold: float = Field(default=0.18, ge=0.0, le=1.0)


class ExpressionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expression: str = Field(min_length=1, max_length=500)
    context: dict[str, float] = Field(default_factory=dict)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _empty_rig() -> Rig:
    return Rig(
        id="empty",
        name="No Rig",
        groups=[Group(id="all", name="All Fixtures", type="all")],
    )


class StudioRuntime:
    """One live studio: working-copy rig, active show, playhead, subscribers."""

    def __init__(
        self,
        rigs_dir: str | Path = PROJECT_ROOT / "config" / "rigs",
        shows_dir: str | Path = PROJECT_ROOT / "config" / "shows",
        *,
        blueprints_dir: str | Path | None = None,
        fps: float = 30.0,
        persist_shows: bool = True,
        sync: MaSync | None = None,
        ha_bridge: HomeAssistantBridge | None = None,
        hal_settings: HalSettings | None = None,
        analyzer_url: str | None = None,
    ) -> None:
        self.rigs = RigLibrary(rigs_dir)
        self.shows = ShowStore(
            shows_dir, persist=persist_shows, on_change=self._show_changed
        )
        self.blueprints = BlueprintStore(
            blueprints_dir or Path(shows_dir).parent / "blueprints",
            analyzer_url=analyzer_url,
        )
        self.palettes = PaletteStore(
            Path(shows_dir).parent / "palettes.json",
            persist=persist_shows,
        )
        self.user_presets = UserPresetStore(
            Path(shows_dir).parent / "user-presets.json",
            persist=persist_shows,
        )
        self.blueprint: dict[str, Any] | None = None
        self.fps = self._clamp(float(fps), 1.0, 120.0)
        self.rig: Rig = _empty_rig()
        self.compiled: CompiledRig = self.rig.compile()
        self.compositor: Compositor | None = None
        self.show_id: str | None = None
        self.show: ShowDoc | None = None
        self.playhead = 0.0
        self.playing = False
        self.loop = False
        self.gain = 1.0
        self.blackout = False
        self.rig_dirty = False
        self.started_at = time.monotonic()
        self.sync = sync
        self.ha_bridge = ha_bridge
        self.hal_settings = hal_settings
        self.hal: Hal | None = None
        self._clients: set[WebSocket] = set()
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENT_BACKLOG)
        self._needs_frame = False
        self._last_tick = time.monotonic()
        self._sync_shows: list[dict[str, Any]] = []
        self._sync_shows_at = 0.0
        self._ha_publish_at = 0.0
        self._preview_lock = threading.Lock()
        self._preview_cache: dict[str, bytes] = {}
        self._load_initial()
        self._publish_ha(force=True)

    # -- initial state ----------------------------------------------------
    def _load_initial(self) -> None:
        try:
            self.rig = self.rigs.load_active()
        except (KeyError, ValueError):
            self.rig = _empty_rig()
        show_ids = self.shows.ids()
        if show_ids:
            self.show_id = show_ids[0]
            self.show = self.shows.get(self.show_id)
        self._rebuild()

    def _rebuild(self) -> None:
        self.compiled = self.rig.compile()
        self.compositor = Compositor(self.compiled, self.show, fps=self.fps)
        self.attach_blueprint(self.show_id)
        if self.hal is not None:
            self.hal.set_rig(self.compiled)
        elif self.hal_settings is not None and self.hal_settings.enabled:
            self.hal = Hal(self.compiled, self.hal_settings)

    # -- hardware ---------------------------------------------------------
    def hardware_status(self) -> dict[str, Any]:
        if self.hal is None:
            return {
                "enabled": bool(
                    self.hal_settings.enabled if self.hal_settings is not None else False
                ),
                "started": False,
                "tx_operations": 0,
                "last_error": None,
            }
        stats = self.hal.snapshot_stats()
        return {
            "enabled": bool(stats.get("enabled")),
            "started": bool(stats.get("started")),
            "tx_operations": int(stats.get("tx_operations", 0)),
            "last_error": stats.get("last_error"),
        }

    def set_hardware(self, enabled: bool) -> dict[str, Any]:
        if self.hal_settings is None:
            # The UI toggle is also used when the server was started without
            # ``--hardware``. In that case the legacy gateway config still owns
            # the broker/proxy credentials, so load it instead of silently
            # talking to localhost with an empty Hue/MQTT config.
            gateway: dict[str, Any] = {}
            candidate = PROJECT_ROOT / "config" / "gateway.json"
            try:
                if candidate.is_file():
                    payload = json.loads(candidate.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        gateway = payload
            except (OSError, ValueError) as exc:
                log.warning("could not read gateway config for hardware output: %s", exc)
            self.hal_settings = HalSettings.from_gateway_config(gateway)
        if self.hal is None:
            self.hal = Hal(self.compiled, self.hal_settings)
        self.hal_settings.enabled = bool(enabled)
        self.hal.set_enabled(bool(enabled))
        self._queue_event("hardware", enabled=bool(enabled))
        return self.hardware_status()

    @staticmethod
    def _preview_projection(
        rig: Any, max_points: int = 140
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return sampled voxel indices and fitted isometric 2D positions."""
        n = rig.voxel_count
        max_points = max(8, min(260, int(max_points)))
        indices = np.linspace(0, max(0, n - 1), min(n, max_points)).astype(np.int64)
        norm = rig.normalized_positions[indices].astype(np.float64)
        sx = (norm[:, 0] - norm[:, 1]) * 0.866
        sy = (norm[:, 0] + norm[:, 1]) * 0.34 - norm[:, 2] * 0.72
        for values in (sx, sy):
            lo = float(values.min())
            hi = float(values.max())
            span = max(1e-6, hi - lo)
            values[:] = 0.06 + (values - lo) / span * 0.88
        return indices, np.stack((sx, sy), axis=1)

    def _preview_frame(self, rgb: np.ndarray, points: np.ndarray) -> list[list[Any]]:
        out: list[list[Any]] = []
        for (x, y), (r, g, b) in zip(points, rgb):
            peak = int(max(r, g, b))
            if peak <= 8:
                continue
            out.append(
                [
                    round(float(x), 4),
                    round(float(y), 4),
                    f"#{int(r):02x}{int(g):02x}{int(b):02x}",
                ]
            )
        return out

    def filter_previews(
        self, *, frames: int = 6, samples: int = 140, span_s: float = 2.0
    ) -> dict[str, Any]:
        """Render before/after filter strips on top of a common gradient base."""
        doc = self.show
        rig = self.compiled
        n = rig.voxel_count
        if doc is None or n == 0:
            return {}
        frames = max(2, min(12, int(frames)))
        samples = max(8, min(260, int(samples)))
        indices, points = self._preview_projection(rig, samples)
        group_idx = rig.groups.get("all", np.arange(n, dtype=np.int64))
        rank = rig.group_rank("all") if "all" in rig.groups else np.arange(n, dtype=np.int64)
        size = max(1, len(group_idx))
        base_preset = get_preset("gradient_3d")
        out: dict[str, list[list[list[Any]]]] = {}
        for filter_id, info in FILTERS.items():
            sequence: list[list[list[Any]]] = []
            for frame in range(frames):
                t = (frame / max(1, frames - 1)) * span_s
                beat = doc.beat_context(t)
                env = {
                    key: max(0.0, min(1.0, float(value)))
                    for key, value in deterministic_envelope(doc, t).items()
                }
                pctx = PresetContext(
                    positions=rig.positions,
                    normalized=rig.normalized_positions,
                    scene_min=np.asarray(rig.room.min, dtype=np.float32),
                    scene_max=np.asarray(rig.room.max, dtype=np.float32),
                    room_size=rig.room.size.astype(np.float32),
                    t=float(t),
                    local_t=float(t),
                    beat=beat,
                    env=env,
                    params=base_preset.default_params(),
                    target_indices=group_idx,
                    target_rank=rank,
                    target_size=size,
                )
                base = base_preset.render(pctx)
                fctx = FilterContext(
                    positions=rig.positions,
                    normalized=rig.normalized_positions,
                    scene_min=np.asarray(rig.room.min, dtype=np.float32),
                    scene_max=np.asarray(rig.room.max, dtype=np.float32),
                    room_size=rig.room.size.astype(np.float32),
                    t=float(t),
                    local_t=float(t),
                    timing=doc.timing,
                    beat=beat,
                    env=env,
                    target_indices=group_idx,
                    target_rank=rank,
                    fps=float(self.fps),
                    inertia_reference=np.clip(base * 0.35, 0.0, 255.0),
                    mirror_sources={},
                )
                filtered = info.apply(base, fctx, info.default_params())
                rgb = np.clip(filtered, 0.0, 255.0).astype(np.uint8)
                sequence.append(
                    [f"#{int(r):02x}{int(g):02x}{int(b):02x}" for r, g, b in rgb[indices]]
                )
            out[filter_id] = sequence
        return out

    def preset_previews(
        self, *, frames: int = 6, samples: int = 140, span_s: float = 2.0
    ) -> dict[str, list[list[list[Any]]]]:
        """Render real isometric point-cloud previews for every preset."""
        doc = self.show
        rig = self.compiled
        n = rig.voxel_count
        if doc is None or n == 0:
            return {}
        frames = max(2, min(12, int(frames)))
        samples = max(8, min(260, int(samples)))
        indices, points = self._preview_projection(rig, samples)
        group_idx = rig.groups.get("all", np.arange(n, dtype=np.int64))
        rank = rig.group_rank("all") if "all" in rig.groups else np.arange(n, dtype=np.int64)
        size = max(1, len(group_idx))
        out: dict[str, list[list[list[Any]]]] = {}
        env_provider = self._envelope_provider()
        for preset_id, preset in PRESETS.items():
            rendered: list[np.ndarray] = []
            scores: list[int] = []
            for frame in range(frames):
                t = (frame / max(1, frames - 1)) * span_s
                beat = doc.beat_context(t)
                env = env_provider(doc, t)
                ctx = PresetContext(
                    positions=rig.positions,
                    normalized=rig.normalized_positions,
                    scene_min=np.asarray(rig.room.min, dtype=np.float32),
                    scene_max=np.asarray(rig.room.max, dtype=np.float32),
                    room_size=rig.room.size.astype(np.float32),
                    t=float(t),
                    local_t=float(t),
                    beat=beat,
                    env={key: max(0.0, min(1.0, float(value))) for key, value in env.items()},
                    params=preset.default_params(),
                    target_indices=group_idx,
                    target_rank=rank,
                    target_size=size,
                )
                rendered.append(
                    np.clip(preset.render(ctx), 0.0, 255.0).astype(np.uint8)
                )
            if not rendered:
                out[preset_id] = []
                continue
            sequence = [self._preview_frame(frame[indices], points) for frame in rendered]
            scores = [int(np.sum(frame[indices].astype(np.int64))) for frame in rendered]
            pivot = max(range(len(scores)), key=lambda index: scores[index])
            out[preset_id] = sequence[pivot:] + sequence[:pivot]
        return out

    def invalidate_preview_cache(self) -> None:
        with self._preview_lock:
            self._preview_cache.clear()

    def _envelope_provider(self) -> Any:
        if self.compositor is not None:
            return self.compositor.env_provider
        return deterministic_envelope

    def effect_preview_png(
        self,
        clip: Clip,
        t: float,
        *,
        width: int = 128,
        height: int = 72,
        timing: Timing | None = None,
    ) -> bytes:
        # One preview render at a time: the spatial filters are O(n^2) and a
        # burst of library thumbnails must not balloon memory.
        with self._preview_lock:
            image = render_clip_rgb(
                clip,
                float(t),
                timing=timing or (self.show.timing if self.show else Timing()),
                width=width,
                height=height,
                room_size=self.rig.room.size,
                env_provider=self._envelope_provider(),
            )
            return preview_png(image)

    def preset_preview_png(
        self,
        preset_id: str,
        *,
        t: float = 0.65,
        width: int = 128,
        height: int = 72,
    ) -> bytes:
        key = f"preset:{preset_id}:{t}:{width}:{height}"
        with self._preview_lock:
            cached = self._preview_cache.get(key)
            if cached is not None:
                return cached
            timing = self.show.timing if self.show else Timing()
            # Library thumbnails should show the effect at a representative
            # moment.  Pick the brightest of three nearby frames instead of
            # freezing whichever phase happens to be dark.
            candidates = [
                render_preset_rgb(
                    preset_id,
                    t=t + offset,
                    timing=timing,
                    width=width,
                    height=height,
                    room_size=self.rig.room.size,
                    env_provider=self._envelope_provider(),
                )
                for offset in (0.0, 0.55, 1.1)
            ]
            image = max(candidates, key=lambda frame: int(frame.sum()))
            data = preview_png(image)
            self._preview_cache[key] = data
            return data

    def filter_preview_png(
        self,
        filter_id: str,
        *,
        t: float = 0.65,
        width: int = 48,
        height: int = 27,
    ) -> bytes:
        key = f"filter:{filter_id}:{t}:{width}:{height}"
        with self._preview_lock:
            cached = self._preview_cache.get(key)
            if cached is not None:
                return cached
            image = render_preset_rgb(
                "gradient_3d",
                t=t,
                timing=self.show.timing if self.show else Timing(),
                width=width,
                height=height,
                room_size=self.rig.room.size,
                filters=[FilterSpec(type=filter_id)],
            )
            data = preview_png(image)
            self._preview_cache[key] = data
            return data

    def _set_show_doc(self, doc: ShowDoc | None, show_id: str | None) -> None:
        self.show = doc
        self.show_id = show_id
        self.compositor = Compositor(self.compiled, doc, fps=self.fps)
        self.attach_blueprint(show_id)
        duration = self.duration
        if duration <= 0.0:
            self.playhead = 0.0
            self.playing = False
        elif self.playhead > duration:
            self.playhead = 0.0
        self._queue_event("show", action="switch", show_id=show_id)
        self._dirty()

    # -- state ------------------------------------------------------------
    @property
    def duration(self) -> float:
        return float(self.show.duration_s) if self.show is not None else 0.0

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, float(value)))

    def transport(self) -> dict[str, Any]:
        return {
            "playing": self.playing,
            "time_s": round(self.playhead, 4),
            "duration_s": round(self.duration, 4),
            "loop": self.loop,
            "fps": self.fps,
            "gain": round(self.gain, 4),
            "blackout": self.blackout,
            "show_id": self.show_id,
            "voxel_count": self.compiled.voxel_count,
        }

    def snapshot(self) -> dict[str, Any]:
        compiled = self.compiled
        proxy_health = hue_proxy_health(self.rig)
        fixture_health = fixture_incomplete_reasons(self.rig)
        rig_document = self.rig.model_dump(mode="json")
        for fixture in rig_document.get("fixtures", []):
            fixture["incomplete_reasons"] = fixture_health.get(
                str(fixture.get("id")), []
            )
        return {
            "schema_version": 1,
            "version": __version__,
            "rig": {
                "active": self.rigs.active_id,
                "dirty": self.rig_dirty,
                "presets": self.rigs.list_presets(),
                "document": rig_document,
                "proxy_health": proxy_health,
                "compiled": {
                    "voxel_count": compiled.voxel_count,
                    "fixture_ids": list(compiled.fixture_ids),
                    "fixture_slices": {
                        fid: [sl.start, sl.stop]
                        for fid, sl in compiled.fixture_slices.items()
                    },
                    "fixture_aims": {
                        fid: [round(float(c), 4) for c in aim]
                        for fid, aim in compiled.fixture_aims.items()
                    },
                    "positions": [
                        round(float(c), 4) for c in compiled.positions.ravel()
                    ],
                    "voxel_fixture_index": compiled.voxel_fixture_index.tolist(),
                    "groups": {
                        gid: indices.tolist()
                        for gid, indices in compiled.groups.items()
                    },
                },
            },
            "show": {
                "id": self.show_id,
                "shows": self.shows.list_shows(),
                "document": (
                    self.show.model_dump(mode="json") if self.show is not None else None
                ),
                "blueprint": BlueprintStore.summary(
                    self.blueprint,
                    audio_available=(
                        self.show_id is not None
                        and self.blueprints.audio_path(self.show_id) is not None
                    ),
                ),
            },
            "playback": self.transport(),
            "sync": self.sync_status(),
            "home_assistant": self.ha_status(),
            "hardware": self.hardware_status(),
            "proxy_health": proxy_health,
            "presets": preset_catalog(),
            "filters": filter_catalog(),
            "palettes": self.palettes.list(),
            "user_presets": self.user_presets.list(),
        }

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "uptime_s": round(time.monotonic() - self.started_at, 1),
            "clients": len(self._clients),
            "playing": self.playing,
            "time_s": round(self.playhead, 3),
            "voxel_count": self.compiled.voxel_count,
            "rig_dirty": self.rig_dirty,
            "sync_state": self.sync_status().get("state"),
            "ha_connected": bool(
                self.ha_bridge.connected if self.ha_bridge is not None else False
            ),
            "hardware": self.hardware_status(),
            "proxy_health": hue_proxy_health(self.rig),
        }

    # -- events -----------------------------------------------------------
    def _queue_event(self, scope: str, **extra: Any) -> None:
        self._events.append({"type": "invalidate", "scope": scope, **extra})

    def _queue_transport(self) -> None:
        self._events.append({"type": "transport", "transport": self.transport()})

    def _dirty(self) -> None:
        self._needs_frame = True

    def _drain_events(self) -> list[str]:
        out = [json.dumps(event) for event in self._events]
        self._events.clear()
        return out

    # -- show callback (may be called from a worker thread) ----------------
    def _show_changed(
        self, show_id: str, doc: ShowDoc, action: str, label: str
    ) -> None:
        if show_id != self.show_id or action == "delete":
            return
        self.show = doc
        if self.compositor is not None:
            self.compositor.set_show(doc)
        if self.playhead > doc.duration_s:
            self.playhead = 0.0
        self._queue_event("show", action=action, label=label, show_id=show_id)
        self._dirty()

    # -- audio blueprint --------------------------------------------------
    def attach_blueprint(self, show_id: str | None = None) -> dict[str, Any] | None:
        """Load the cached blueprint for ``show_id`` and arm the compositor."""
        target = show_id or self.show_id
        blueprint = self.blueprints.get(target) if target else None
        if target == self.show_id:
            previous = self.blueprint.get("key") if self.blueprint else None
            self.blueprint = blueprint
            if self.compositor is not None:
                self.compositor.env_provider = (
                    BlueprintStore.envelope(blueprint)
                    if blueprint is not None
                    else deterministic_envelope
                )
            key = blueprint.get("key") if blueprint else None
            if key != previous:
                self._queue_event("blueprint", show_id=target, key=key)
                self._dirty()
        return blueprint

    def sync_show_from_blueprint(
        self, show_id: str, blueprint: dict[str, Any] | None = None
    ) -> ShowDoc:
        """Align duration, tempo and markers with an audio blueprint.

        Runs as one regular ShowStore transaction, so Ctrl+Z restores the
        arrangement exactly.
        """
        if not self.shows.has(show_id):
            raise ShowNotFound(f"show '{show_id}' not found")
        bp = blueprint if blueprint is not None else self.blueprints.get(show_id)
        if bp is None:
            raise ShowNotFound("no AudioBlueprint attached")
        duration = float(bp.get("duration_s") or 0.0)
        if not math.isfinite(duration) or duration <= 0.0:
            raise ValueError("blueprint has no usable duration")
        bpm = float(bp.get("bpm") or 0.0)
        if not math.isfinite(bpm) or bpm <= 0.0:
            bpm = 120.0

        def mutate(current: ShowDoc) -> ShowDoc:
            current.duration_s = round(duration, 4)
            current.timing.bpm = max(1.0, min(400.0, bpm))
            grid = float(bp.get("beat_grid_s") or 0.0)
            if not math.isfinite(grid) or grid <= 0.0:
                grid = 60.0 / max(1e-6, current.timing.bpm)
            current.timing.beat_grid_s = round(grid, 6)
            current.match.duration_s = round(duration, 4)
            markers: list[Marker] = []
            seen: set[tuple[float, str]] = set()

            def add_marker(t_s: float, name: str, kind: str) -> None:
                t = round(max(0.0, min(duration, float(t_s))), 4)
                key = (t, name)
                if key in seen:
                    return
                seen.add(key)
                markers.append(Marker(t_s=t, name=name, kind=kind))

            for section in bp.get("sections") or []:
                if not isinstance(section, dict):
                    continue
                label = str(section.get("label") or "section")
                kind = "drop" if label == "drop" else "section"
                add_marker(
                    float(section.get("start") or 0.0),
                    label.replace("_", " ").title(),
                    kind,
                )
            for drop in bp.get("drops") or []:
                try:
                    add_marker(float(drop), "Drop", "drop")
                except (TypeError, ValueError):
                    continue
            current.timing.markers = sorted(markers, key=lambda marker: marker.t_s)
            for track in current.tracks:
                kept = []
                for clip in track.clips:
                    if clip.start_s >= duration - 1e-4:
                        continue
                    end = min(clip.end_s, duration)
                    if end <= clip.start_s + 1e-4:
                        continue
                    clip.end_s = round(end, 4)
                    length = max(0.0, clip.end_s - clip.start_s)
                    fade_sum = clip.fade_in_s + clip.fade_out_s
                    if fade_sum > length + 1e-9:
                        scale_fade = length / fade_sum if fade_sum > 0 else 0.0
                        clip.fade_in_s *= scale_fade
                        clip.fade_out_s *= scale_fade
                    clip.fade_in_s = min(clip.fade_in_s, length)
                    clip.fade_out_s = min(clip.fade_out_s, length)
                    clip.keyframes = [
                        keyframe
                        for keyframe in clip.keyframes
                        if keyframe.t_s <= clip.end_s - clip.start_s + 1e-9
                    ]
                    kept.append(clip)
                track.clips = kept
            return current

        updated = self.shows.apply(show_id, "sync from blueprint", mutate)
        if self.show_id == show_id:
            self.show = updated
        return updated

    def auto_keyframes(
        self,
        show_id: str,
        *,
        clip_id: str | None = None,
        source: str = "kick",
        param: str = "opacity",
        division: str = "1/4",
        depth: float = 0.65,
        threshold: float = 0.18,
    ) -> dict[str, Any]:
        """Stamp deterministic musical keyframes from the ML/deterministic envelope."""
        doc = self.shows.get(show_id)
        blueprint = self.blueprints.get(show_id)
        envelope = (
            BlueprintStore.envelope(blueprint)
            if blueprint is not None
            else deterministic_envelope
        )
        source = str(source or "kick")
        param = str(param or "opacity")
        depth = max(0.0, min(1.0, float(depth)))
        step = doc.timing.grid_interval(division)
        times: list[float] = []
        t = max(0.0, float(doc.timing.beat_offset_s))
        while t <= doc.duration_s + 1e-6:
            times.append(round(t, 4))
            t += step
            if len(times) > 20000:
                break

        added = 0

        def mutate(current: ShowDoc) -> ShowDoc:
            nonlocal added
            for track in current.tracks:
                for clip in track.clips:
                    if clip_id and clip.id != clip_id:
                        continue
                    length = clip.end_s - clip.start_s
                    keyframes = [
                        keyframe
                        for keyframe in clip.keyframes
                        if not (
                            keyframe.param == param
                            and any(
                                abs(keyframe.t_s - (time - clip.start_s)) <= 1e-4
                                for time in times
                            )
                        )
                    ]
                    base = 1.0
                    raw = clip.params.get(param, clip.opacity)
                    try:
                        base = float(raw)
                    except (TypeError, ValueError):
                        base = 1.0
                    for time in times:
                        local = time - clip.start_s
                        if local < -1e-6 or local > length + 1e-6:
                            continue
                        env = envelope(current, time)
                        value = max(0.0, min(1.0, float(env.get(source, 0.0))))
                        if source in {"snare", "hat"} and value < threshold:
                            continue
                        level = max(
                            0.0,
                            min(1.0, base * (0.18 + depth * value)),
                        )
                        keyframes.append(
                            Keyframe(
                                t_s=round(max(0.0, min(length, local)), 4),
                                param=param,
                                value=round(level, 4),
                                easing="ease_out",
                            )
                        )
                        added += 1
                    keyframes.sort(key=lambda keyframe: keyframe.t_s)
                    clip.keyframes = keyframes
            return current

        updated = self.shows.apply(
            show_id, f"auto keyframes ({source} / {param})", mutate
        )
        return {
            "document": updated.model_dump(mode="json"),
            "added": added,
            "source": source,
            "param": param,
            "division": division,
            "analyzer": blueprint.get("analyzer") if blueprint else "deterministic",
        }

    def lint_show(self, show_id: str | None = None) -> dict[str, Any]:
        target = show_id or self.show_id
        if not target:
            raise ShowNotFound("no active show")
        doc = (
            self.show
            if self.show is not None and target == self.show_id
            else self.shows.get(target)
        )
        return lint_show_doc(
            doc, self.rig, blueprint=self.blueprints.get(target)
        )

    # -- MCP telemetry ----------------------------------------------------
    def note_mcp(self, entry: dict[str, Any]) -> None:
        self._events.append({"type": "mcp", **entry})

    # -- transport --------------------------------------------------------
    def apply_transport(self, patch: TransportPatch | dict[str, Any]) -> dict[str, Any]:
        data = patch.model_dump(exclude_none=True) if isinstance(
            patch, TransportPatch
        ) else dict(patch)
        if "show_id" in data and data["show_id"] != self.show_id:
            show_id = str(data["show_id"])
            doc = self.shows.get(show_id)
            self._set_show_doc(doc, show_id)
            self.playhead = 0.0
        if "time_s" in data:
            self.playhead = self._clamp(float(data["time_s"]), 0.0, self.duration)
        if "playing" in data:
            self.playing = bool(data["playing"])
        if "loop" in data:
            self.loop = bool(data["loop"])
        if "fps" in data:
            self.fps = self._clamp(float(data["fps"]), 1.0, 120.0)
            if self.compositor is not None:
                self.compositor.fps = self.fps
        if "gain" in data:
            self.gain = self._clamp(float(data["gain"]), 0.0, 1.0)
        if "blackout" in data:
            self.blackout = bool(data["blackout"])
        if not self.playing:
            self._last_tick = time.monotonic()
        self._queue_transport()
        self._dirty()
        return self.transport()

    def toggle(self) -> dict[str, Any]:
        return self.apply_transport({"playing": not self.playing})

    # -- MA sync ----------------------------------------------------------
    def sync_status(self) -> dict[str, Any]:
        if self.sync is None:
            return {
                "configured": False,
                "enabled": False,
                "state": "unconfigured",
                "source": None,
                "connected": False,
                "matched_show_id": None,
                "track": None,
                "locked": False,
                "pairing_token": None,
                "client_id": None,
                "paired": False,
            }
        return {"configured": True, **self.sync.status()}

    def set_sync(self, patch: SyncPatch | dict[str, Any]) -> dict[str, Any]:
        if self.sync is None:
            raise ValueError("MA sync is not configured")
        data = (
            patch.model_dump(exclude_none=True)
            if isinstance(patch, SyncPatch)
            else dict(patch)
        )
        if "enabled" in data:
            self.sync.set_enabled(bool(data["enabled"]))
            self._sync_shows_at = 0.0
        if "offset_s" in data:
            self.sync.set_offset_s(float(data["offset_s"]))
        self._queue_event("sync", state=self.sync.status()["state"])
        self._queue_transport()
        return self.sync_status()

    def restart_sync(self) -> dict[str, Any]:
        if self.sync is None:
            raise ValueError("MA sync is not configured")
        source = self.sync.source
        restart = getattr(source, "restart", None)
        if callable(restart):
            restart()
        self.sync.reset_link()
        self._sync_shows_at = 0.0
        self._queue_event("sync", state=self.sync.status()["state"])
        self._dirty()
        return self.sync_status()

    # -- Home Assistant bridge --------------------------------------------
    def ha_status(self) -> dict[str, Any]:
        if self.ha_bridge is None:
            return {"configured": False, "enabled": False, "connected": False}
        return {"configured": True, **self.ha_bridge.status()}

    def _apply_ha_commands(self) -> None:
        if self.ha_bridge is None:
            return
        for command in self.ha_bridge.drain_commands():
            entity = str(command.get("entity") or "")
            value = command.get("value")
            try:
                if entity == "playback":
                    self.apply_transport({"playing": bool(value)})
                elif entity == "show":
                    target = str(value or "")
                    if self.shows.has(target):
                        self.apply_transport({"show_id": target})
                elif entity == "gain":
                    self.apply_transport({"gain": float(value)})
                elif entity == "auto_show":
                    if self.sync is not None:
                        self.set_sync({"enabled": bool(value)})
            except Exception as exc:
                log.warning("HA command %s failed: %s", command, exc)

    def _publish_ha(self, *, force: bool = False) -> None:
        if self.ha_bridge is None:
            return
        now = time.monotonic()
        if not force and (now - self._ha_publish_at) < 1.0:
            return
        self._ha_publish_at = now
        self.ha_bridge.publish_state(
            transport=self.transport(),
            shows=self.shows.list_shows(),
            sync=self.sync_status(),
        )

    # -- playback ---------------------------------------------------------
    def _advance(self) -> None:
        now = time.monotonic()
        dt = max(0.0, min(0.25, now - self._last_tick))
        self._last_tick = now
        if self.sync is not None and self.sync.enabled:
            if now - self._sync_shows_at >= 1.0:
                self._sync_shows = self.shows.list_shows()
                self._sync_shows_at = now
            before = self.sync.state_key()
            decision = self.sync.tick(
                now=now,
                dt=dt,
                current_show_id=self.show_id,
                playhead=self.playhead,
                shows=self._sync_shows,
            )
            if decision.show_id and decision.show_id != self.show_id:
                try:
                    doc = self.shows.get(decision.show_id)
                except ShowNotFound:
                    # The 1 Hz show cache briefly outlived a deleted show.
                    self.sync.drop_match(decision.show_id)
                    self._sync_shows_at = 0.0
                    return
                self._set_show_doc(doc, decision.show_id)
                self._queue_transport()
            if decision.controlled:
                if decision.playhead is not None:
                    self.playhead = self._clamp(
                        decision.playhead, 0.0, self.duration
                    )
                if decision.playing is not None:
                    self.playing = bool(decision.playing)
                after = self.sync.state_key()
                if after != before:
                    self._queue_event(
                        "sync", state=after[0], show_id=after[1]
                    )
                    self._queue_transport()
                self._dirty()
                return
            if decision.playing is not None:
                self.playing = bool(decision.playing)
        if not self.playing:
            return
        duration = self.duration
        if duration <= 0.0:
            self.playing = False
            self._queue_transport()
            return
        self.playhead += dt
        if self.playhead >= duration:
            if self.loop:
                self.playhead %= duration
            else:
                self.playhead = duration
                self.playing = False
                self._queue_transport()
        self._dirty()

    def evaluate_frame(self) -> FrameResult:
        count = self.compiled.voxel_count
        if self.show is not None and self.compositor is not None:
            frame = self.compositor.evaluate(self.playhead)
        else:
            frame = FrameResult(
                time_s=self.playhead,
                rgb=np.zeros((count, 3), dtype=np.float32),
                snap_zcl=np.zeros(self.compiled.fixture_count, dtype=bool),
            )
        rgb = frame.rgb.astype(np.float64, copy=True)
        if self.blackout:
            rgb[:] = 0.0
        elif self.gain != 1.0:
            rgb *= self.gain
        frame.rgb = np.clip(rgb, 0.0, 255.0).astype(np.float32)
        return frame

    @staticmethod
    def encode_frame(frame: FrameResult, *, playing: bool, blackout: bool, loop: bool) -> bytes:
        rgb_u8 = frame.rgb_u8
        count = int(rgb_u8.shape[0])
        flags = 0
        if playing:
            flags |= FRAME_FLAG_PLAYING
        if blackout:
            flags |= FRAME_FLAG_BLACKOUT
        if loop:
            flags |= FRAME_FLAG_LOOP
        header = FRAME_HEADER.pack(
            FRAME_MAGIC, count, float(frame.time_s), flags
        )
        return header + rgb_u8.tobytes()

    def evaluate(self) -> bytes:
        return self.encode_frame(
            self.evaluate_frame(),
            playing=self.playing,
            blackout=self.blackout,
            loop=self.loop,
        )

    async def _hardware_loop(self) -> None:
        """Send each fixture on its own monotonic deadline, independent of preview.

        The preview ticker used to throttle fixtures with ``elapsed >= 1/fps``.
        At 30 fps preview and 25 fps lamps that produced 67 ms gaps with a
        33 ms ZCL ramp, i.e. a visible hold on every other frame.  Sleeping
        until the exact next deadline keeps the lamp cadence regular even when
        the preview rate, client count or render cost changes.
        """
        while True:
            hal = self.hal
            if hal is None or not hal.enabled:
                await asyncio.sleep(0.25)
                continue
            wait = hal.seconds_until_due()
            if wait > 0.0:
                await asyncio.sleep(min(wait, 0.25))
                continue
            frame = self.evaluate_frame()
            try:
                # Blocking MQTT/UDP stays off the ASGI event loop.
                await hal.output_frame_async(frame)
            except Exception as exc:  # keep the clock alive after driver errors
                log.warning("hardware frame failed: %s", exc)

    async def run(self) -> None:
        hardware = asyncio.create_task(self._hardware_loop())
        try:
            deadline = time.monotonic()
            while True:
                # Deadline-based sleep: render cost must not stretch the tick.
                period = 1.0 / max(1.0, self.fps)
                deadline += period
                delay = deadline - time.monotonic()
                if delay > 0.0:
                    await asyncio.sleep(delay)
                else:
                    deadline = time.monotonic()
                self._apply_ha_commands()
                self._advance()
                self._publish_ha()
                events = self._drain_events()
                if events and self._clients:
                    await self._broadcast_text(events)
                if self._clients and (self.playing or self._needs_frame):
                    self._needs_frame = False
                    await self._broadcast_bytes(
                        self.encode_frame(
                            self.evaluate_frame(),
                            playing=self.playing,
                            blackout=self.blackout,
                            loop=self.loop,
                        )
                    )
        finally:
            hardware.cancel()
            try:
                await hardware
            except asyncio.CancelledError:
                pass

    # -- websocket --------------------------------------------------------
    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        await ws.send_text(json.dumps({"type": "hello", "state": self.snapshot()}))
        await ws.send_bytes(self.evaluate())

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def _broadcast_text(self, messages: list[str]) -> None:
        for message in messages:
            await self._broadcast(message, binary=False)

    async def _broadcast_bytes(self, payload: bytes) -> None:
        await self._broadcast(payload, binary=True)

    async def _broadcast(self, payload: str | bytes, *, binary: bool) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                if binary:
                    await ws.send_bytes(payload)  # type: ignore[arg-type]
                else:
                    await ws.send_text(payload)  # type: ignore[arg-type]
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def handle_message(self, ws: WebSocket, raw: str) -> None:
        try:
            message = json.loads(raw)
        except ValueError:
            await ws.send_text(
                json.dumps({"type": "error", "message": "invalid JSON"})
            )
            return
        if not isinstance(message, dict):
            await ws.send_text(
                json.dumps({"type": "error", "message": "expected an object"})
            )
            return
        kind = str(message.get("type", ""))
        try:
            if kind == "ping":
                await ws.send_text(
                    json.dumps({"type": "pong", "time_s": self.playhead})
                )
                return
            if kind == "snapshot":
                await ws.send_text(
                    json.dumps({"type": "hello", "state": self.snapshot()})
                )
                return
            if kind == "render":
                if "time_s" in message:
                    raw_time = message["time_s"]
                    if not isinstance(raw_time, (int, float)) or not math.isfinite(
                        float(raw_time)
                    ):
                        raise ValueError("time_s must be a finite number")
                    self.playhead = self._clamp(
                        float(raw_time), 0.0, self.duration
                    )
                    self._dirty()
                await ws.send_bytes(self.evaluate())
                return
            if kind == "toggle":
                self.toggle()
                return
            if kind == "play":
                self.apply_transport(TransportPatch(playing=True))
                return
            if kind == "pause":
                self.apply_transport(TransportPatch(playing=False))
                return
            if kind in {"transport", "seek"}:
                if kind == "transport":
                    payload = {
                        key: value
                        for key, value in message.items()
                        if key in TransportPatch.model_fields and value is not None
                    }
                else:
                    payload = {
                        "time_s": (
                            message["time_s"]
                            if message.get("time_s") is not None
                            else self.playhead
                        )
                    }
                self.apply_transport(TransportPatch.model_validate(payload))
                return
            await ws.send_text(
                json.dumps({"type": "error", "message": f"unknown type '{kind}'"})
            )
        except (KeyError, ValueError, ShowNotFound, ShowStoreError) as exc:
            await ws.send_text(
                json.dumps({"type": "error", "message": str(exc)})
            )

    # -- rig working copy -------------------------------------------------
    def _commit_rig(self, rig: Rig, *, dirty: bool = True) -> Rig:
        self.rig = rig
        self.rig_dirty = dirty
        self._rebuild()
        self._queue_event("rig", active=self.rigs.active_id)
        self._dirty()
        return rig

    def replace_rig(self, rig: Rig) -> Rig:
        return self._commit_rig(Rig.model_validate(rig.model_dump(mode="json")))

    def add_fixture(self, fixture: Fixture) -> Rig:
        return self._commit_rig(self.rig.with_fixture(fixture))

    def update_fixture(self, fixture_id: str, patch: dict[str, Any]) -> Rig:
        existing = next((f for f in self.rig.fixtures if f.id == fixture_id), None)
        if existing is None:
            raise KeyError(f"fixture '{fixture_id}' not found")
        if "id" in patch and patch["id"] != fixture_id:
            raise ValueError("fixture id cannot be changed")
        merged = _deep_merge(existing.model_dump(mode="json"), patch)
        updated = Fixture.model_validate(merged)
        payload = self.rig.model_dump(mode="json")
        payload["fixtures"] = [
            updated.model_dump(mode="json") if f["id"] == fixture_id else f
            for f in payload["fixtures"]
        ]
        return self._commit_rig(Rig.model_validate(payload))

    def transform_fixtures(
        self,
        fixture_ids: list[str],
        *,
        translate: list[float],
        rotate_axis: str,
        rotate_deg: float,
        scale: list[float],
        pivot: list[float] | None,
    ) -> Rig:
        """Bake a Blender-style group transform into fixture geometry.

        The selection is transformed in world space. Geometry is written back
        and per-fixture transforms are reset, so Position becomes the single
        source of truth after a G/R/S operation.
        """
        axis = {"x": 0, "y": 1, "z": 2}.get(str(rotate_axis).lower())
        if axis is None:
            raise ValueError("rotate_axis must be x, y or z")
        if len(scale) != 3 or any(
            not math.isfinite(float(value)) or float(value) <= 0.0 for value in scale
        ):
            raise ValueError("scale must contain three positive finite numbers")
        scale_vec = np.asarray([float(value) for value in scale], dtype=np.float64)
        if any(not math.isfinite(float(value)) for value in translate):
            raise ValueError("translate must contain finite numbers")
        angle = math.radians(float(rotate_deg))
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        if axis == 0:
            rotation = np.asarray(
                [[1.0, 0.0, 0.0], [0.0, cos_a, -sin_a], [0.0, sin_a, cos_a]]
            )
        elif axis == 1:
            rotation = np.asarray(
                [[cos_a, 0.0, sin_a], [0.0, 1.0, 0.0], [-sin_a, 0.0, cos_a]]
            )
        else:
            rotation = np.asarray(
                [[cos_a, -sin_a, 0.0], [sin_a, cos_a, 0.0], [0.0, 0.0, 1.0]]
            )
        payload = self.rig.model_dump(mode="json")
        known = {fixture["id"]: fixture for fixture in payload["fixtures"]}
        missing = [fixture_id for fixture_id in fixture_ids if fixture_id not in known]
        if missing:
            raise KeyError(f"fixtures not found: {missing}")
        offset = np.asarray([float(value) for value in translate], dtype=np.float64)
        for fixture_id in fixture_ids:
            fixture = known[fixture_id]
            try:
                voxels = self.compiled.fixture_voxels(fixture_id)
            except KeyError as exc:
                raise KeyError(str(exc)) from exc
            pts = self.compiled.positions[voxels].astype(np.float64)
            centre = np.asarray(pivot, dtype=np.float64) if pivot is not None else pts.mean(axis=0)
            transformed = ((pts - centre) * scale_vec) @ rotation.T + centre + offset
            geometry = fixture["geometry"]
            if geometry["type"] == "point":
                geometry["pos"] = [round(float(value), 4) for value in transformed[0]]
            else:
                geometry["start"] = [round(float(value), 4) for value in transformed[0]]
                geometry["end"] = [round(float(value), 4) for value in transformed[-1]]
            aim = self.compiled.fixture_aims.get(fixture_id)
            if aim is not None:
                rotated_aim = rotation @ np.asarray(aim, dtype=np.float64)
                geometry["aim"] = [round(float(value), 4) for value in rotated_aim]
            fixture["transform"] = {
                "translate": [0.0, 0.0, 0.0],
                "rotate_deg": [0.0, 0.0, 0.0],
                "pivot": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            }
        return self.replace_rig(Rig.model_validate(payload))

    def delete_fixture(self, fixture_id: str) -> Rig:
        return self._commit_rig(self.rig.without_fixture(fixture_id))

    def upsert_group(self, group: Group) -> Rig:
        return self._commit_rig(self.rig.with_group(group))

    def delete_group(self, group_id: str) -> Rig:
        return self._commit_rig(self.rig.without_group(group_id))

    def assign_group(self, group_id: str, fixture_ids: list[str]) -> Rig:
        return self._commit_rig(self.rig.assign_to_group(fixture_ids, group_id))

    def save_rig(self) -> RigPresetEntry:
        entry = self.rigs.save(self.rig, set_active=True)
        self.rig_dirty = False
        self._queue_event("rig", active=self.rigs.active_id)
        self._dirty()
        return entry

    def save_rig_as(
        self, preset_id: str, name: str, *, force: bool = False
    ) -> RigPresetEntry:
        exists = any(
            entry["id"] == preset_id for entry in self.rigs.list_presets()
        )
        if exists and preset_id != self.rig.id and not force:
            raise ValueError(
                f"rig preset '{preset_id}' exists; pass force=true to overwrite"
            )
        payload = self.rig.model_dump(mode="json")
        payload["id"] = preset_id
        payload["name"] = name
        self.rig = Rig.model_validate(payload)
        self._rebuild()
        return self.save_rig()

    def create_rig(self, body: RigCreate) -> Rig:
        rig = self.rigs.create(
            body.name,
            preset_id=body.preset_id,
            room=body.room,
            fixtures=body.fixtures,
            groups=body.groups,
            set_active=body.activate,
        )
        if body.activate:
            self._commit_rig(rig, dirty=False)
        else:
            self._queue_event("rig", active=self.rigs.active_id)
        return rig

    def clone_rig(self, preset_id: str, body: RigClone) -> Rig:
        rig = self.rigs.clone(preset_id, body.new_id, body.name)
        if body.activate:
            self._commit_rig(rig, dirty=False)
        else:
            self._queue_event("rig", active=self.rigs.active_id)
        return rig

    def activate_rig(self, preset_id: str, *, force: bool = False) -> Rig:
        if self.rig_dirty and not force:
            raise ValueError(
                "rig has unsaved changes; save or pass force=true to discard"
            )
        rig = self.rigs.set_active(preset_id)
        return self._commit_rig(rig, dirty=False)

    def delete_rig(self, preset_id: str, *, force: bool = False) -> None:
        if preset_id == self.rigs.active_id and self.rig_dirty and not force:
            raise ValueError(
                "active rig has unsaved changes; save or pass force=true to delete"
            )
        self.rigs.delete(preset_id)
        if preset_id == self.rig.id or preset_id == self.rigs.active_id:
            try:
                rig = self.rigs.load_active()
            except (KeyError, ValueError):
                rig = _empty_rig()
            self._commit_rig(rig, dirty=False)
        else:
            self._queue_event("rig", active=self.rigs.active_id)

    # -- shows ------------------------------------------------------------
    def create_show(self, doc: ShowDoc) -> ShowDoc:
        created = self.shows.create(doc)
        self._set_show_doc(created, created.id)
        return created

    def commit_show(self, show_id: str, doc: ShowDoc, label: str) -> ShowDoc:
        if doc.id != show_id:
            raise ValueError("document id does not match the URL")
        return self.shows.commit(show_id, doc, label=label)

    def delete_show(self, show_id: str) -> None:
        self.shows.delete(show_id)
        if show_id == self.show_id:
            remaining = self.shows.ids()
            if remaining:
                self._set_show_doc(self.shows.get(remaining[0]), remaining[0])
            else:
                self._set_show_doc(None, None)

    def undo_show(self, show_id: str) -> ShowDoc | None:
        return self.shows.undo(show_id)

    def redo_show(self, show_id: str) -> ShowDoc | None:
        return self.shows.redo(show_id)


def create_app(
    *,
    rigs_dir: str | Path = PROJECT_ROOT / "config" / "rigs",
    shows_dir: str | Path = PROJECT_ROOT / "config" / "shows",
    blueprints_dir: str | Path | None = None,
    fps: float = 30.0,
    persist_shows: bool = True,
    static_dir: str | Path | None = None,
    sync_source: Any | None = None,
    sync_enabled: bool = True,
    sync_offset_s: float = 0.0,
    ha_bridge: HomeAssistantBridge | None = None,
    hal_settings: HalSettings | None = None,
    analyzer_url: str | None = None,
) -> FastAPI:
    sync = (
        MaSync(
            sync_source,
            enabled=sync_enabled,
            offset_s=sync_offset_s,
        )
        if sync_source is not None
        else None
    )
    runtime = StudioRuntime(
        rigs_dir,
        shows_dir,
        blueprints_dir=blueprints_dir,
        fps=fps,
        persist_shows=persist_shows,
        sync=sync,
        ha_bridge=ha_bridge,
        hal_settings=hal_settings,
        analyzer_url=analyzer_url,
    )
    mcp = McpBridge(runtime)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if runtime.sync is not None:
            starter = getattr(runtime.sync.source, "start", None)
            if callable(starter):
                try:
                    starter()
                except Exception as exc:
                    log.warning("Sendspin source start failed: %s", exc)
        if runtime.ha_bridge is not None:
            try:
                await asyncio.to_thread(runtime.ha_bridge.start)
            except Exception as exc:
                log.warning("HA bridge start failed: %s", exc)
        if runtime.hal is not None and runtime.hal.enabled:
            try:
                await asyncio.to_thread(runtime.hal.start)
            except Exception as exc:
                log.warning("HAL start failed: %s", exc)
        task = asyncio.create_task(runtime.run())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            if runtime.ha_bridge is not None:
                try:
                    await asyncio.to_thread(runtime.ha_bridge.stop)
                except Exception:
                    pass
            if runtime.hal is not None:
                try:
                    await asyncio.to_thread(runtime.hal.stop)
                except Exception:
                    pass
            if runtime.sync is not None:
                closer = getattr(runtime.sync.source, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        pass

    app = FastAPI(
        title="Hueme Studio",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.studio = runtime
    app.state.mcp = mcp

    @app.middleware("http")
    async def _revalidate_ui(request: Request, call_next: Any) -> Response:
        """Keep HTML and ES modules in sync during local development."""
        response = await call_next(request)
        path = request.url.path
        if path in {"/", "/studio", "/live"} or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    @app.exception_handler(ShowNotFound)
    async def _show_not_found(_request: Any, exc: ShowNotFound) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(KeyError)
    async def _key_error_handler(_request: Any, exc: KeyError) -> JSONResponse:
        message = exc.args[0] if exc.args else str(exc)
        return JSONResponse({"detail": str(message)}, status_code=404)

    @app.exception_handler(ShowStoreError)
    async def _show_store_error(_request: Any, exc: ShowStoreError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ValueError)
    async def _value_error(_request: Any, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ValidationError)
    async def _validation_error(_request: Any, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            {"detail": jsonable_encoder(exc.errors())}, status_code=400
        )

    # -- meta -------------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return runtime.health()

    @app.get("/api/studio/state")
    async def studio_state() -> dict[str, Any]:
        return runtime.snapshot()

    @app.get("/api/presets")
    async def presets() -> dict[str, Any]:
        return {"presets": preset_catalog()}

    @app.get("/api/presets/previews")
    def preset_previews() -> dict[str, Any]:
        return {"frames": runtime.preset_previews()}

    @app.get("/api/filters")
    async def filters() -> dict[str, Any]:
        return {"filters": filter_catalog()}

    @app.get("/api/filters/previews")
    def filter_previews() -> dict[str, Any]:
        return {"frames": runtime.filter_previews()}

    @app.get("/api/presets/{preset_id}/preview.png")
    def preset_preview_png(
        preset_id: str,
        t: float = Query(default=0.65, ge=0.0, le=3600.0),
        width: int = Query(default=128, ge=16, le=192),
        height: int = Query(default=72, ge=9, le=108),
    ) -> Response:
        if preset_id not in PRESETS:
            raise HTTPException(status_code=404, detail="preset not found")
        return Response(
            runtime.preset_preview_png(
                preset_id, t=t, width=width, height=height
            ),
            media_type="image/png",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/api/filters/{filter_id}/preview.png")
    def filter_preview_png(
        filter_id: str,
        t: float = Query(default=0.65, ge=0.0, le=3600.0),
        width: int = Query(default=48, ge=16, le=192),
        height: int = Query(default=27, ge=9, le=108),
    ) -> Response:
        if filter_id not in FILTERS:
            raise HTTPException(status_code=404, detail="filter not found")
        return Response(
            runtime.filter_preview_png(
                filter_id, t=t, width=width, height=height
            ),
            media_type="image/png",
            headers={"Cache-Control": "no-cache"},
        )

    @app.post("/api/preview/effect")
    def effect_preview(payload: dict[str, Any] = Body(...)) -> Response:
        clip_payload = payload.get("clip")
        if not isinstance(clip_payload, dict):
            raise HTTPException(status_code=400, detail="clip is required")
        clip = Clip.model_validate(clip_payload)
        timing_payload = payload.get("timing")
        timing = (
            Timing.model_validate(timing_payload)
            if isinstance(timing_payload, dict)
            else None
        )
        return Response(
            runtime.effect_preview_png(
                clip,
                float(payload.get("t") or 0.0),
                width=int(payload.get("width") or 128),
                height=int(payload.get("height") or 72),
                timing=timing,
            ),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/expressions/validate")
    async def validate_expression(payload: ExpressionCheck) -> dict[str, Any]:
        try:
            symbols = sorted(expression_names(payload.expression))
            unknown = sorted(set(symbols) - EXPRESSION_VARIABLES)
            if unknown:
                return {
                    "ok": False,
                    "error": f"unknown symbols: {', '.join(unknown)}",
                    "symbols": symbols,
                }
            context = {name: 0.0 for name in EXPRESSION_VARIABLES}
            context.update(payload.context)
            value = evaluate_expression(payload.expression, context)
            return {"ok": True, "value": value, "symbols": symbols, "error": None}
        except ExpressionError as exc:
            return {"ok": False, "error": str(exc), "symbols": []}

    # -- palette library --------------------------------------------------
    @app.get("/api/palettes")
    async def palettes() -> dict[str, Any]:
        return {"palettes": runtime.palettes.list()}

    @app.post("/api/palettes", status_code=201)
    async def save_palette(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        palette = await asyncio.to_thread(runtime.palettes.upsert, payload)
        runtime.invalidate_preview_cache()
        runtime._queue_event("palettes")
        return palette

    @app.delete("/api/palettes/{palette_id}", status_code=204)
    async def delete_palette(palette_id: str) -> Response:
        await asyncio.to_thread(runtime.palettes.delete, palette_id)
        runtime.invalidate_preview_cache()
        runtime._queue_event("palettes")
        return Response(status_code=204)

    # -- user looks -------------------------------------------------------
    @app.get("/api/user-presets")
    async def user_presets() -> dict[str, Any]:
        return {"presets": runtime.user_presets.list()}

    @app.post("/api/user-presets", status_code=201)
    async def save_user_preset(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        preset = await asyncio.to_thread(runtime.user_presets.upsert, payload)
        runtime._queue_event("user-presets")
        return preset

    @app.delete("/api/user-presets/{preset_id}", status_code=204)
    async def delete_user_preset(preset_id: str) -> Response:
        await asyncio.to_thread(runtime.user_presets.delete, preset_id)
        runtime._queue_event("user-presets")
        return Response(status_code=204)

    # -- rig preset library ----------------------------------------------
    @app.get("/api/rigs")
    async def list_rigs() -> dict[str, Any]:
        return {
            "active": runtime.rigs.active_id,
            "presets": runtime.rigs.list_presets(),
        }

    @app.get("/api/rigs/{preset_id}")
    async def get_rig(preset_id: str) -> Rig:
        return runtime.rigs.load(preset_id)

    @app.post("/api/rigs", status_code=201)
    async def create_rig(body: RigCreate) -> Rig:
        return runtime.create_rig(body)

    @app.post("/api/rigs/{preset_id}/clone", status_code=201)
    async def clone_rig(preset_id: str, body: RigClone) -> Rig:
        return runtime.clone_rig(preset_id, body)

    @app.post("/api/rigs/{preset_id}/activate")
    async def activate_rig(
        preset_id: str, force: bool = Query(default=False)
    ) -> Rig:
        return runtime.activate_rig(preset_id, force=force)

    @app.delete("/api/rigs/{preset_id}", status_code=204)
    async def delete_rig(
        preset_id: str, force: bool = Query(default=False)
    ) -> None:
        runtime.delete_rig(preset_id, force=force)

    # -- rig working copy -------------------------------------------------
    @app.get("/api/rig")
    async def get_working_rig() -> dict[str, Any]:
        return {
            "active": runtime.rigs.active_id,
            "dirty": runtime.rig_dirty,
            "document": runtime.rig.model_dump(mode="json"),
        }

    @app.put("/api/rig")
    async def replace_rig(rig: Rig) -> dict[str, Any]:
        runtime.replace_rig(rig)
        return {
            "active": runtime.rigs.active_id,
            "dirty": runtime.rig_dirty,
            "document": runtime.rig.model_dump(mode="json"),
        }

    @app.post("/api/rig/save")
    async def save_rig() -> RigPresetEntry:
        return runtime.save_rig()

    @app.post("/api/rig/save-as")
    async def save_rig_as(
        body: RigSaveAs, force: bool = Query(default=False)
    ) -> RigPresetEntry:
        return runtime.save_rig_as(body.id, body.name, force=force)

    @app.post("/api/rig/fixtures", status_code=201)
    async def add_fixture(fixture: Fixture) -> Rig:
        return runtime.add_fixture(fixture)

    @app.post("/api/rig/fixtures/transform")
    async def transform_fixtures(body: FixtureSelectionTransform) -> dict[str, Any]:
        rig = await asyncio.to_thread(
            runtime.transform_fixtures,
            body.fixture_ids,
            translate=body.translate,
            rotate_axis=body.rotate_axis,
            rotate_deg=body.rotate_deg,
            scale=body.scale,
            pivot=body.pivot,
        )
        return {"rig": rig.model_dump(mode="json"), "dirty": runtime.rig_dirty}

    @app.patch("/api/rig/fixtures/{fixture_id}")
    async def patch_fixture(
        fixture_id: str, patch: dict[str, Any] = Body(...)
    ) -> Rig:
        return runtime.update_fixture(fixture_id, patch)

    @app.put("/api/rig/fixtures/{fixture_id}")
    async def replace_fixture(fixture_id: str, fixture: Fixture) -> Rig:
        if fixture.id != fixture_id:
            raise HTTPException(status_code=400, detail="fixture id mismatch")
        return runtime.update_fixture(fixture_id, fixture.model_dump(mode="json"))

    @app.delete("/api/rig/fixtures/{fixture_id}")
    async def delete_fixture(fixture_id: str) -> Rig:
        return runtime.delete_fixture(fixture_id)

    @app.put("/api/rig/groups/{group_id}")
    async def upsert_group(group_id: str, group: Group) -> Rig:
        if group.id != group_id:
            raise HTTPException(status_code=400, detail="group id mismatch")
        return runtime.upsert_group(group)

    @app.delete("/api/rig/groups/{group_id}")
    async def delete_group(group_id: str) -> Rig:
        return runtime.delete_group(group_id)

    @app.post("/api/rig/groups/{group_id}/assign")
    async def assign_group(group_id: str, body: GroupAssign) -> Rig:
        return runtime.assign_group(group_id, body.fixture_ids)

    # -- shows ------------------------------------------------------------
    @app.get("/api/shows")
    async def list_shows() -> dict[str, Any]:
        return {"active": runtime.show_id, "shows": runtime.shows.list_shows()}

    @app.get("/api/shows/{show_id}")
    async def get_show(show_id: str) -> ShowDoc:
        return runtime.shows.get(show_id)

    @app.post("/api/shows", status_code=201)
    async def create_show(doc: ShowDoc) -> ShowDoc:
        return runtime.create_show(doc)

    @app.put("/api/shows/{show_id}")
    async def commit_show(
        show_id: str,
        doc: ShowDoc,
        label: str = Query(default="edit"),
    ) -> ShowDoc:
        return runtime.commit_show(show_id, doc, label)

    @app.delete("/api/shows/{show_id}", status_code=204)
    async def delete_show(show_id: str) -> None:
        runtime.delete_show(show_id)

    @app.post("/api/shows/{show_id}/undo")
    async def undo_show(show_id: str) -> dict[str, Any]:
        doc = runtime.undo_show(show_id)
        return {
            "document": doc.model_dump(mode="json") if doc else None,
            "history": runtime.shows.history(show_id),
        }

    @app.post("/api/shows/{show_id}/redo")
    async def redo_show(show_id: str) -> dict[str, Any]:
        doc = runtime.redo_show(show_id)
        return {
            "document": doc.model_dump(mode="json") if doc else None,
            "history": runtime.shows.history(show_id),
        }

    @app.get("/api/shows/{show_id}/history")
    async def show_history(show_id: str) -> dict[str, Any]:
        if not runtime.shows.has(show_id):
            raise ShowNotFound(f"show '{show_id}' not found")
        return runtime.shows.history(show_id)

    @app.post("/api/shows/{show_id}/snapshot")
    async def snapshot_show(
        show_id: str, body: ShowSnapshot
    ) -> dict[str, Any]:
        runtime.shows.snapshot(show_id, body.label)
        return runtime.shows.history(show_id)

    # -- lint + audio blueprint ------------------------------------------
    @app.get("/api/shows/{show_id}/lint")
    async def lint_show(show_id: str) -> dict[str, Any]:
        if not runtime.shows.has(show_id):
            raise ShowNotFound(f"show '{show_id}' not found")
        return runtime.lint_show(show_id)

    @app.get("/api/shows/{show_id}/blueprint")
    async def get_blueprint(show_id: str) -> dict[str, Any]:
        if not runtime.shows.has(show_id):
            raise ShowNotFound(f"show '{show_id}' not found")
        blueprint = runtime.blueprints.get(show_id)
        if blueprint is None:
            raise HTTPException(status_code=404, detail="no AudioBlueprint attached")
        return blueprint

    @app.post("/api/shows/{show_id}/blueprint")
    async def upload_blueprint(
        show_id: str,
        request: Request,
        source: str = Query(default="upload"),
        sync_show: bool = Query(default=True),
    ) -> dict[str, Any]:
        if not runtime.shows.has(show_id):
            raise ShowNotFound(f"show '{show_id}' not found")
        payload = await request.body()
        # Analysis is CPU-heavy and may call an external ML container; keep the
        # preview ticker responsive.
        blueprint = await asyncio.to_thread(
            runtime.blueprints.extract, show_id, payload, source=source
        )
        runtime.attach_blueprint(show_id)
        if sync_show:
            await asyncio.to_thread(runtime.sync_show_from_blueprint, show_id, blueprint)
        summary = BlueprintStore.summary(
            blueprint,
            audio_available=runtime.blueprints.audio_path(show_id) is not None,
        ) or {}
        summary["synced"] = bool(sync_show)
        return summary

    @app.post("/api/shows/{show_id}/sync-from-blueprint")
    async def sync_from_blueprint(show_id: str) -> dict[str, Any]:
        if not runtime.shows.has(show_id):
            raise ShowNotFound(f"show '{show_id}' not found")
        doc = await asyncio.to_thread(runtime.sync_show_from_blueprint, show_id)
        return {
            "document": doc.model_dump(mode="json"),
            "history": runtime.shows.history(show_id),
        }

    @app.post("/api/shows/{show_id}/auto-keyframes")
    async def auto_keyframes(
        show_id: str, body: AutoKeyframesPatch
    ) -> dict[str, Any]:
        if not runtime.shows.has(show_id):
            raise ShowNotFound(f"show '{show_id}' not found")
        return await asyncio.to_thread(
            runtime.auto_keyframes,
            show_id,
            clip_id=body.clip_id,
            source=body.source,
            param=body.param,
            division=body.division,
            depth=body.depth,
            threshold=body.threshold,
        )

    @app.get("/api/shows/{show_id}/audio")
    async def get_show_audio(show_id: str) -> FileResponse:
        if not runtime.shows.has(show_id):
            raise ShowNotFound(f"show '{show_id}' not found")
        path = runtime.blueprints.audio_path(show_id)
        if path is None:
            raise HTTPException(
                status_code=404, detail="no preview audio attached"
            )
        return FileResponse(
            path,
            media_type="audio/wav",
            headers={"Cache-Control": "no-cache"},
        )

    # -- MCP --------------------------------------------------------------
    @app.post("/mcp")
    async def mcp_endpoint(request: Request) -> Response:
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "parse error"},
                },
                status_code=400,
            )
        result = await mcp.handle_async(payload)
        headers: dict[str, str] = {}
        if (
            isinstance(payload, dict)
            and payload.get("method") == "initialize"
            and isinstance(result, dict)
            and "result" in result
        ):
            headers["Mcp-Session-Id"] = secrets.token_hex(16)
        if result is None or result == []:
            return Response(status_code=202, headers=headers)
        return JSONResponse(result, headers=headers)

    @app.get("/mcp")
    async def mcp_stream() -> Response:
        # No server-initiated SSE stream in Sprint 3.
        return Response(status_code=405, headers={"Allow": "POST, DELETE"})

    @app.delete("/mcp")
    async def mcp_session_end() -> Response:
        return Response(status_code=204)

    @app.get("/api/mcp/telemetry")
    async def mcp_telemetry() -> dict[str, Any]:
        return mcp.telemetry()

    # -- transport --------------------------------------------------------
    @app.get("/api/transport")
    async def get_transport() -> dict[str, Any]:
        return runtime.transport()

    @app.post("/api/transport")
    async def set_transport(patch: TransportPatch) -> dict[str, Any]:
        return runtime.apply_transport(patch)

    # -- MA sync + Home Assistant -----------------------------------------
    @app.get("/api/sync")
    async def get_sync() -> dict[str, Any]:
        return runtime.sync_status()

    @app.post("/api/sync")
    async def set_sync(patch: SyncPatch) -> dict[str, Any]:
        return runtime.set_sync(patch)

    @app.post("/api/sync/restart")
    async def restart_sync() -> dict[str, Any]:
        return await asyncio.to_thread(runtime.restart_sync)

    @app.post("/api/system/restart")
    async def restart_system(background: BackgroundTasks) -> dict[str, Any]:
        """Re-exec the Studio process after the HTTP response is flushed."""
        def _exec() -> None:
            try:
                args = list(sys.argv[1:])
                if (
                    runtime.hal is not None
                    and runtime.hal.enabled
                    and "--hardware" not in args
                ):
                    args.append("--hardware")
                os.execv(
                    sys.executable,
                    [sys.executable, "-m", "hueme_studio", *args],
                )
            except Exception:
                log.exception("Studio restart failed")

        async def _restart() -> None:
            # BackgroundTasks run after the response has been sent.  The short
            # grace period lets the client read the JSON body before execv.
            await asyncio.sleep(0.75)
            _exec()

        background.add_task(_restart)
        return {"ok": True, "restarting": True}

    @app.get("/api/ha")
    async def get_ha() -> dict[str, Any]:
        return runtime.ha_status()

    @app.post("/api/hardware")
    async def set_hardware(patch: HardwarePatch) -> dict[str, Any]:
        return await asyncio.to_thread(runtime.set_hardware, patch.enabled)

    @app.get("/api/hardware")
    async def get_hardware() -> dict[str, Any]:
        return runtime.hardware_status()

    # -- preview socket ---------------------------------------------------
    @app.websocket("/ws/preview")
    async def preview_socket(ws: WebSocket) -> None:
        await runtime.connect(ws)
        try:
            while True:
                await runtime.handle_message(ws, await ws.receive_text())
        except WebSocketDisconnect:
            pass
        finally:
            runtime.disconnect(ws)

    # -- static UI --------------------------------------------------------
    static_root = Path(static_dir) if static_dir is not None else STATIC_DIR
    if static_root.is_dir():
        app.mount("/static", StaticFiles(directory=static_root), name="static")

        @app.get("/")
        async def root() -> RedirectResponse:
            return RedirectResponse("/studio")

        @app.get("/studio")
        async def studio_page() -> FileResponse:
            return FileResponse(static_root / "studio.html")

        @app.get("/live")
        async def live_page() -> FileResponse:
            return FileResponse(static_root / "live.html")

    return app


__all__ = [
    "FRAME_FLAG_BLACKOUT",
    "FRAME_FLAG_LOOP",
    "FRAME_FLAG_PLAYING",
    "FRAME_HEADER",
    "FRAME_MAGIC",
    "StudioRuntime",
    "create_app",
]
