"""FastAPI transport for Hueme Studio.

The server owns one live studio session: a working-copy rig, the active show,
the playhead and the preview subscribers.  REST edits mutate the session and
are pushed to every connected client; the binary ``/ws/preview`` socket carries
RGB voxel frames plus JSON invalidations.

Preview never requires hardware.  ``Hal`` stays opt-in and is intentionally not
wired to the transport in Sprint 2.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
import struct
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import (
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
from hueme_studio.compositor import Compositor, deterministic_envelope
from hueme_studio.filters import filter_catalog
from hueme_studio.ha import HomeAssistantBridge
from hueme_studio.lint import lint_show as lint_show_doc
from hueme_studio.mcp import McpBridge
from hueme_studio.presets import preset_catalog
from hueme_studio.rig import (
    CompiledRig,
    Fixture,
    Group,
    Rig,
    RigLibrary,
    RigPresetEntry,
    Room,
)
from hueme_studio.showdoc import (
    ShowDoc,
    ShowNotFound,
    ShowStore,
    ShowStoreError,
)
from hueme_studio.sync import MaSync

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
    offset_s: float | None = Field(default=None, ge=-2.0, le=2.0)


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
    ) -> None:
        self.rigs = RigLibrary(rigs_dir)
        self.shows = ShowStore(
            shows_dir, persist=persist_shows, on_change=self._show_changed
        )
        self.blueprints = BlueprintStore(
            blueprints_dir or Path(shows_dir).parent / "blueprints"
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
        self._clients: set[WebSocket] = set()
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENT_BACKLOG)
        self._needs_frame = False
        self._last_tick = time.monotonic()
        self._sync_shows: list[dict[str, Any]] = []
        self._sync_shows_at = 0.0
        self._ha_publish_at = 0.0
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
        return {
            "schema_version": 1,
            "version": __version__,
            "rig": {
                "active": self.rigs.active_id,
                "dirty": self.rig_dirty,
                "presets": self.rigs.list_presets(),
                "document": self.rig.model_dump(mode="json"),
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
            "presets": preset_catalog(),
            "filters": filter_catalog(),
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
        self._queue_event("mcp", **entry)

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

    def evaluate(self) -> bytes:
        count = self.compiled.voxel_count
        rgb = np.zeros((count, 3), dtype=np.float64)
        if self.show is not None and self.compositor is not None:
            rgb = self.compositor.evaluate(self.playhead).rgb.astype(np.float64)
        if self.blackout:
            rgb[:] = 0.0
        elif self.gain != 1.0:
            rgb *= self.gain
        rgb_u8 = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
        flags = 0
        if self.playing:
            flags |= FRAME_FLAG_PLAYING
        if self.blackout:
            flags |= FRAME_FLAG_BLACKOUT
        if self.loop:
            flags |= FRAME_FLAG_LOOP
        header = FRAME_HEADER.pack(
            FRAME_MAGIC, count, float(self.playhead), flags
        )
        return header + rgb_u8.tobytes()

    async def run(self) -> None:
        while True:
            await asyncio.sleep(1.0 / max(1.0, self.fps))
            self._apply_ha_commands()
            self._advance()
            self._publish_ha()
            events = self._drain_events()
            if events and self._clients:
                await self._broadcast_text(events)
            if not self._clients:
                self._needs_frame = False
                continue
            if self.playing or self._needs_frame:
                self._needs_frame = False
                await self._broadcast_bytes(self.evaluate())

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

    @app.get("/api/filters")
    async def filters() -> dict[str, Any]:
        return {"filters": filter_catalog()}

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
    ) -> dict[str, Any]:
        if not runtime.shows.has(show_id):
            raise ShowNotFound(f"show '{show_id}' not found")
        payload = await request.body()
        # Analysis is CPU-heavy; keep the preview ticker responsive.
        blueprint = await asyncio.to_thread(
            runtime.blueprints.extract, show_id, payload, source=source
        )
        runtime.attach_blueprint(show_id)
        return (
            BlueprintStore.summary(
                blueprint,
                audio_available=runtime.blueprints.audio_path(show_id) is not None,
            )
            or {}
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
        result = mcp.handle(payload)
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

    @app.get("/api/ha")
    async def get_ha() -> dict[str, Any]:
        return runtime.ha_status()

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
