"""Hardware abstraction layer for Hueme Studio.

The HAL keeps the proven protocol drivers in ``hueme/drivers.py`` and adds the
Studio-specific routing:

* fixture -> protocol mapping from ``rig.json``
* per-fixture frame-rate throttling
* RGB+1800K amber-channel extraction for SK6812 strips (4-channel DDP)
* per-fixture ZCL snap propagation, split into snap and smooth Hue frames
* optional HA light sink for direct integrations
* an async wrapper so the server never blocks its event loop on UDP/MQTT

Physical output is deliberately opt-in.  Preview previews always run through
the compositor; only :class:`Hal` touches hardware.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from hueme.drivers import DdpOutputDriver, E131OutputDriver, HueZ2MDriver
from hueme_studio.compositor import FrameResult
from hueme_studio.rig import CompiledRig, Fixture

HASink = Callable[[str, tuple[int, int, int]], None]


class HalSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    fps: float = Field(default=60.0, gt=0.0, le=240.0)
    mqtt: dict[str, Any] = Field(default_factory=dict)
    hue: dict[str, Any] = Field(
        default_factory=lambda: {
            "enabled": True,
            "proxies": [],
            "max_fixtures_per_frame": 10,
            "smoothing": "auto",
            "max_brightness": 2047,
            "brightness_mode": "max",
            "light_balance": True,
        }
    )
    ddp: dict[str, Any] = Field(default_factory=lambda: {"enabled": True, "port": 4048})
    e131: dict[str, Any] = Field(
        default_factory=lambda: {"enabled": True, "universe_start": 1, "priority": 100}
    )
    wled: dict[str, Any] = Field(
        default_factory=lambda: {"enabled": True, "port": 21324}
    )

    @classmethod
    def from_gateway_config(cls, config: dict[str, Any]) -> "HalSettings":
        """Adapt the legacy ``gateway.json`` shape without copying credentials."""
        outputs = config.get("outputs", {}) if isinstance(config, dict) else {}
        ddp = dict(outputs.get("ddp", {}) or {})
        e131 = dict(outputs.get("e131", {}) or {})
        wled = dict(outputs.get("wled", {}) or {})
        # Fixture-level output settings decide routing; these configs only
        # carry transport defaults. Force the driver's internal gate open.
        ddp["enabled"] = True
        e131["enabled"] = True
        wled["enabled"] = True
        return cls(
            enabled=False,
            mqtt=dict(config.get("mqtt", {}) or {}),
            hue=dict(config.get("hue", {}) or {}),
            ddp=ddp,
            e131=e131,
            wled=wled,
        )


@dataclass(slots=True)
class OutputReport:
    sent: bool
    hue_records: int = 0
    ddp_frames: int = 0
    e131_frames: int = 0
    ha_lights: int = 0
    skipped_disabled: int = 0
    errors: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "hue_records": self.hue_records,
            "ddp_frames": self.ddp_frames,
            "e131_frames": self.e131_frames,
            "ha_lights": self.ha_lights,
            "skipped_disabled": self.skipped_disabled,
            "errors": list(self.errors or []),
        }


def extract_warm_channel(
    rgb: tuple[int, int, int] | list[int] | np.ndarray,
    *,
    white_gain: float = 1.0,
) -> tuple[int, int, int, int]:
    """Extract the warm-white/amber channel for a 5V SK6812 RGB+1800K strip.

    The amber LED carries the neutral component; the RGB dies carry the
    saturated residual.  This preserves hue and keeps warm whites on the
    1800K channel instead of mixing three dies to fake them.
    """
    arr = np.clip(np.asarray(rgb, dtype=np.float64)[:3], 0.0, 255.0)
    r, g, b = float(arr[0]), float(arr[1]), float(arr[2])
    warm = min(r, g, b) * max(0.0, float(white_gain))
    warm = max(0.0, min(255.0, warm))
    return (
        int(round(max(0.0, r - warm))),
        int(round(max(0.0, g - warm))),
        int(round(max(0.0, b - warm))),
        int(round(warm)),
    )


def apply_color_space(rgb: np.ndarray, color_space: str) -> np.ndarray:
    """Transform one voxel RGB row to the fixture's wire colour space."""
    px = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 255.0)
    if color_space == "dimmer_only":
        lum = 0.2126 * px[0] + 0.7152 * px[1] + 0.0722 * px[2]
        return np.asarray([lum, lum, lum], dtype=np.float64)
    if color_space == "rgb_amber_1800k":
        return np.asarray(extract_warm_channel(px), dtype=np.float64)
    return px[:3].astype(np.float64)


def _collapse_warm_to_rgb(rgb: np.ndarray) -> np.ndarray:
    """Approximate a 4-channel RGBW value as RGB for protocols that need 3."""
    arr = np.asarray(rgb, dtype=np.float64)
    if arr.shape[-1] == 3:
        return arr[..., :3]
    warm = arr[..., 3:4]
    # 1800K amber is roughly sRGB [255, 147, 41].
    return np.clip(arr[..., :3] + warm * np.asarray([1.0, 0.576, 0.161]), 0.0, 255.0)


class Hal:
    """Route compositor frames to physical fixtures."""

    def __init__(
        self,
        rig: CompiledRig,
        settings: HalSettings | None = None,
        *,
        hue_driver: Any | None = None,
        ddp_driver: Any | None = None,
        e131_driver: Any | None = None,
        wled_driver: Any | None = None,
        ha_sink: HASink | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rig = rig
        self.settings = settings or HalSettings()
        self.enabled = bool(self.settings.enabled)
        self._hue = hue_driver
        self._ddp = ddp_driver
        self._e131 = e131_driver
        self._wled = wled_driver
        self._ha_sink = ha_sink
        self._clock = clock
        self._started = False
        self._last_sent: dict[str, float] = {}
        self.last_error: str | None = None
        self._tx_count = 0
        self._snap_splits = 0

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._started or not self.enabled:
            return
        try:
            if self._hue is None and self._uses_protocol("hue_z2m"):
                self._hue = HueZ2MDriver(self.settings.mqtt, self.settings.hue)
                self._hue.start()
            if self._ddp is None and self._uses_protocol("wled_ddp"):
                self._ddp = DdpOutputDriver(self.settings.ddp)
                self._ddp.start()
            if self._e131 is None and self._uses_protocol("e131"):
                self._e131 = E131OutputDriver(self.settings.e131)
                self._e131.start()
            self._started = True
            self.last_error = None
        except Exception as exc:  # keep the lighting engine alive
            self.last_error = f"{type(exc).__name__}: {exc}"

    def stop(self) -> None:
        for driver in (self._hue, self._ddp, self._e131, self._wled):
            if driver is None:
                continue
            try:
                driver.stop()
            except Exception:
                pass
        self._started = False

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if self.enabled:
            self.start()
        else:
            self.stop()

    def _uses_protocol(self, protocol: str) -> bool:
        return any(
            f.output.enabled and f.output.protocol == protocol
            for f in self.rig.rig.fixtures
        )

    # -- frame output -----------------------------------------------------
    def output_frame(
        self, frame: FrameResult, *, now: float | None = None
    ) -> dict[str, Any]:
        if not self.enabled:
            return OutputReport(
                sent=False, skipped_disabled=len(self.rig.fixture_ids)
            ).as_dict()
        if not self._started:
            self.start()
        now = float(self._clock() if now is None else now)
        report = OutputReport(sent=False, errors=[])
        u8 = frame.rgb_u8

        hue_groups: dict[bool, list[tuple[str, int, tuple[int, int, int], str]]] = {
            False: [],
            True: [],
        }
        due_ids: list[str] = []

        for fi, fixture in enumerate(self.rig.rig.fixtures):
            output = fixture.output
            if not output.enabled or output.protocol == "none":
                continue
            if not self._due(fixture, now):
                continue
            voxels = u8[self.rig.fixture_voxels(fixture.id)]
            if voxels.size == 0:
                continue
            due_ids.append(fixture.id)
            self._last_sent[fixture.id] = now

            if output.protocol == "hue_z2m":
                rgb = self._aggregate(voxels)
                rgb = self._min_brightness(rgb, fixture)
                rec = (
                    output.friendly_name or fixture.name,
                    output.address_int,
                    (int(rgb[0]), int(rgb[1]), int(rgb[2])),
                    output.proxy or "",
                )
                snap = bool(frame.snap_zcl[fi]) and fixture.capabilities.zcl_snap
                hue_groups[snap].append(rec)
            elif output.protocol == "wled_ddp":
                pixels = self._pixel_payload(voxels, fixture)
                try:
                    if self._ddp is None:
                        self._ddp = DdpOutputDriver(self.settings.ddp)
                        self._ddp.start()
                    self._ddp.send_pixels(
                        pixels,
                        host=output.host,
                        port=output.port or 4048,
                    )
                    report.ddp_frames += 1
                    report.sent = True
                    self._tx_count += 1
                except Exception as exc:
                    self._record_error(report, "ddp", exc)
            elif output.protocol == "e131":
                payload = self._pixel_payload(voxels, fixture)
                payload = _collapse_warm_to_rgb(payload)[:, :3]
                channel = int(output.channel)
                offset_px, remainder = divmod(max(0, channel - 1), 3)
                if remainder:
                    self._record_error(
                        report,
                        "e131",
                        ValueError(
                            "E1.31 start channel must align to an RGB pixel boundary"
                        ),
                    )
                    continue
                if offset_px:
                    payload = np.vstack([np.zeros((offset_px, 3)), payload])
                try:
                    if self._e131 is None:
                        self._e131 = E131OutputDriver(self.settings.e131)
                        self._e131.start()
                    self._e131.send_pixels(
                        [tuple(int(c) for c in px) for px in payload],
                        universe_start=output.universe,
                        unicast_host=output.host or None,
                        port=output.port or 5568,
                    )
                    report.e131_frames += 1
                    report.sent = True
                    self._tx_count += 1
                except Exception as exc:
                    self._record_error(report, "e131", exc)
            elif output.protocol == "ha_light":
                rgb = self._aggregate(voxels)
                if self._ha_sink is not None:
                    try:
                        self._ha_sink(
                            output.entity_id or fixture.id,
                            (int(rgb[0]), int(rgb[1]), int(rgb[2])),
                        )
                        report.ha_lights += 1
                        report.sent = True
                    except Exception as exc:
                        self._record_error(report, "ha_light", exc)

        if any(hue_groups.values()):
            if self._hue is None:
                try:
                    self._hue = HueZ2MDriver(self.settings.mqtt, self.settings.hue)
                    self._hue.start()
                except Exception as exc:
                    self._record_error(report, "hue", exc)
            if self._hue is not None:
                if hue_groups[False] and hue_groups[True]:
                    self._snap_splits += 1
                hue_fps = float(self.settings.hue.get("fps", 30.0) or 30.0)
                for snap_flag, records in hue_groups.items():
                    if not records:
                        continue
                    try:
                        self._hue.send_consolidated(
                            records, fps=hue_fps, snap=snap_flag
                        )
                        report.hue_records += len(records)
                        report.sent = True
                        self._tx_count += len(records)
                    except Exception as exc:
                        self._record_error(report, "hue", exc)

        if not due_ids:
            report.skipped_disabled = len(self.rig.fixture_ids)
        return report.as_dict()

    async def output_frame_async(
        self, frame: FrameResult, *, now: float | None = None
    ) -> dict[str, Any]:
        """Run blocking MQTT/UDP output off the ASGI event loop."""
        return await asyncio.to_thread(self.output_frame, frame, now=now)

    # -- helpers ----------------------------------------------------------
    def _due(self, fixture: Fixture, now: float) -> bool:
        last = self._last_sent.get(fixture.id)
        if last is None:
            return True
        interval = 1.0 / max(0.1, float(fixture.capabilities.max_fps))
        return (now - last) >= interval - 1e-9

    @staticmethod
    def _aggregate(voxels: np.ndarray) -> np.ndarray:
        if voxels.ndim == 1:
            return voxels.astype(np.float64)
        return np.mean(voxels, axis=0).astype(np.float64)

    @staticmethod
    def _min_brightness(rgb: np.ndarray, fixture: Fixture) -> np.ndarray:
        floor = float(fixture.capabilities.min_brightness)
        peak = float(np.max(rgb)) if rgb.size else 0.0
        if floor > 0.0 and peak > 0.0 and peak < floor * 255.0:
            rgb = rgb * (floor * 255.0 / peak)
        return np.clip(rgb, 0.0, 255.0)

    def _pixel_payload(self, voxels: np.ndarray, fixture: Fixture) -> np.ndarray:
        out = []
        for row in voxels:
            transformed = apply_color_space(row, fixture.capabilities.color_space)
            transformed = self._min_brightness(transformed, fixture)
            out.append(transformed)
        return np.asarray(out, dtype=np.float64)

    def _record_error(self, report: OutputReport, where: str, exc: Exception) -> None:
        message = f"{where}: {type(exc).__name__}: {exc}"
        self.last_error = message
        if report.errors is None:
            report.errors = []
        report.errors.append(message)

    def snapshot_stats(self) -> dict[str, Any]:
        def driver_stats(driver: Any) -> dict[str, Any]:
            if driver is None:
                return {}
            stats = getattr(driver, "stats", None)
            return dict(stats) if isinstance(stats, dict) else {}

        return {
            "enabled": self.enabled,
            "started": self._started,
            "tx_operations": self._tx_count,
            "snap_splits": self._snap_splits,
            "last_error": self.last_error,
            "drivers": {
                "hue": driver_stats(self._hue),
                "ddp": driver_stats(self._ddp),
                "e131": driver_stats(self._e131),
            },
            "last_sent": dict(self._last_sent),
        }


__all__ = [
    "Hal",
    "HalSettings",
    "OutputReport",
    "apply_color_space",
    "extract_warm_channel",
]
