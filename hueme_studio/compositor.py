"""Deterministic multi-track 3D lighting compositor.

Tracks are evaluated bottom-to-top into a float32 sRGB voxel buffer.  Clips
within a track are layered with source-over alpha, then the whole track is
blended into the master using the track's blend mode.  All timing comes from
ShowDoc, never from wall-clock audio callbacks, so any timestamp can be
rendered on demand for preview, hardware output, MCP inspection or tests.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from hueme_studio.filters import FilterContext, apply_filter_stack
from hueme_studio.presets import PresetContext, render_preset
from hueme_studio.rig import CompiledRig
from hueme_studio.showdoc import Clip, ShowDoc, Track, evaluate_spatial_mask

EnvProvider = Callable[[ShowDoc, float], dict[str, float]]

log = logging.getLogger("hueme_studio.compositor")


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def deterministic_envelope(show: ShowDoc, t: float) -> dict[str, float]:
    """A stable, musical envelope derived only from ShowDoc timing.

    Sprint 3 replaces this with the AudioBlueprint stem curves.  Until then the
    default is intentionally deterministic and beat-shaped: four-on-the-floor
    kick, backbeat snare, eighth-note hats, slow section energy.  No microphone
    or reactive loop is involved.
    """
    beat = show.beat_context(t)
    phase = float(beat["beat_phase"])
    bar_pos = int(beat["bar_pos"])
    beat_pos = (float(t) - show.timing.beat_offset_s) / show.timing.beat_grid_s
    eighth_phase = (beat_pos * 2.0) % 1.0

    pulse = math.exp(-phase * 3.0)
    kick = math.exp(-phase * 8.0)
    snare = math.exp(-phase * 11.0) if bar_pos in (1, 3) else 0.02
    hat = 0.5 * math.exp(-eighth_phase * 12.0)
    energy = _clip01(
        0.48
        + 0.16 * math.sin(2.0 * math.pi * t / max(1.0, show.duration_s * 2.0))
        + 0.10 * pulse
    )
    harmonic = 0.5 + 0.5 * math.sin(2.0 * math.pi * beat_pos / 16.0)
    return {
        "pulse": pulse,
        "kick": kick,
        "snare": snare,
        "hat": hat,
        "energy": energy,
        "harmonic": harmonic,
        "bass": _clip01(0.2 + 0.8 * kick),
        "mid": _clip01(0.3 + 0.3 * snare),
        "high": hat,
    }


@dataclass(slots=True)
class FrameResult:
    """One deterministic compositor frame."""

    time_s: float
    rgb: np.ndarray
    snap_zcl: np.ndarray
    active_clips: list[dict[str, Any]] = field(default_factory=list)
    track_levels: dict[str, float] = field(default_factory=dict)
    env: dict[str, float] = field(default_factory=dict)
    beat: dict[str, float | int | bool] = field(default_factory=dict)

    @property
    def rgb_u8(self) -> np.ndarray:
        return np.clip(np.rint(self.rgb), 0, 255).astype(np.uint8)

    def fixture_colors(self, rig: CompiledRig) -> dict[str, np.ndarray]:
        return {fid: self.rgb_u8[rig.fixture_voxels(fid)] for fid in rig.fixture_ids}

    def voxel_snap_zcl(self, rig: CompiledRig) -> np.ndarray:
        return self.snap_zcl[rig.voxel_fixture_index]


class Compositor:
    """Evaluate a :class:`ShowDoc` against a compiled rig."""

    def __init__(
        self,
        rig: CompiledRig,
        show: ShowDoc | None = None,
        *,
        fps: float = 60.0,
        env_provider: EnvProvider | None = None,
        snap_window_s: float | None = None,
    ) -> None:
        self.rig = rig
        self.show = show
        self.fps = float(fps)
        self.env_provider = env_provider or deterministic_envelope
        self.snap_window_s = float(
            snap_window_s
            if snap_window_s is not None
            else max(1.0 / 240.0, 0.75 / self.fps)
        )
        self._group_cache: dict[str, tuple[np.ndarray, np.ndarray, int]] = {}
        self._mirror_sources: dict[int, np.ndarray] = {}

    # -- public API -------------------------------------------------------
    def set_show(self, show: ShowDoc | None) -> None:
        self.show = show

    def evaluate(
        self,
        t: float,
        *,
        show: ShowDoc | None = None,
        env: dict[str, float] | None = None,
    ) -> FrameResult:
        doc = show or self.show
        if doc is None:
            raise ValueError("compositor has no show")
        t = float(t)
        beat = doc.beat_context(t)
        env_values = dict(self.env_provider(doc, t))
        if env is not None:
            env_values.update({k: float(v) for k, v in env.items()})
        env_values = {k: _clip01(float(v)) for k, v in env_values.items()}

        master = np.zeros((self.rig.voxel_count, 3), dtype=np.float64)
        active_tracks = doc.active_tracks()
        active_clips: list[dict[str, Any]] = []
        track_levels: dict[str, float] = {}

        for track in active_tracks:
            group_idx, group_rank, group_size = self._target_info(track.target_group)
            track_rgb, track_alpha, clip_infos = self._render_track(
                doc, track, t, beat, env_values, group_idx, group_rank, group_size
            )
            if clip_infos:
                active_clips.extend(clip_infos)
            if track_alpha.size == 0 or float(track_alpha.max()) <= 1e-6:
                track_levels[track.id] = 0.0
                continue
            track_levels[track.id] = float(np.mean(track_alpha[group_idx]))
            self._blend_track(master, track, group_idx, track_rgb, track_alpha)

        snap = self._collect_snap_events(doc, active_tracks, t, beat)
        master = np.clip(master, 0.0, 255.0).astype(np.float32)
        return FrameResult(
            time_s=t,
            rgb=master,
            snap_zcl=snap,
            active_clips=active_clips,
            track_levels=track_levels,
            env=env_values,
            beat=beat,
        )

    # -- track / clip rendering ------------------------------------------
    def _target_info(self, group_id: str) -> tuple[np.ndarray, np.ndarray, int]:
        cached = self._group_cache.get(group_id)
        if cached is not None:
            return cached
        try:
            indices = self.rig.group_indices(group_id)
            ranks = self.rig.group_rank(group_id)
        except KeyError:
            # A show may outlive the rig preset it was authored on.  An absent
            # group renders nothing instead of taking the whole preview down;
            # the Sprint 3 linter reports it before playback.
            log.warning(
                "show references group '%s' missing from rig '%s'; rendering no voxels",
                group_id,
                self.rig.rig.id,
            )
            indices = np.zeros((0,), dtype=np.int32)
            ranks = np.full(self.rig.voxel_count, -1, dtype=np.int32)
        info = (indices, ranks, int(indices.size))
        self._group_cache[group_id] = info
        return info

    def _render_track(
        self,
        doc: ShowDoc,
        track: Track,
        t: float,
        beat: dict[str, float | int | bool],
        env: dict[str, float],
        group_idx: np.ndarray,
        group_rank: np.ndarray,
        group_size: int,
    ) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
        n = self.rig.voxel_count
        track_rgb = np.zeros((n, 3), dtype=np.float64)
        track_alpha = np.zeros(n, dtype=np.float64)
        infos: list[dict[str, Any]] = []

        clips = sorted(track.active_clips(t), key=lambda c: (c.start_s, c.id))
        for clip in clips:
            local_t = clip.local_time(t)
            opacity = clip.effective_opacity(local_t)
            clip_alpha = clip.fade_alpha(local_t) * opacity
            if clip_alpha <= 1e-6:
                continue

            rgb = self._render_clip(
                doc, clip, local_t, t, beat, env, group_idx, group_rank, group_size
            )
            mask = evaluate_spatial_mask(
                clip.spatial_mask,
                self.rig.normalized_positions,
                np.arange(n, dtype=np.int64),
            )
            coverage = np.zeros(n, dtype=np.float64)
            coverage[group_idx] = mask[group_idx]
            alpha = clip_alpha * coverage

            # Correct straight-alpha source-over accumulation inside the
            # track.  Storing premultiplied colour separately would make the
            # final track blend multiply alpha twice.
            new_alpha = alpha + track_alpha * (1.0 - alpha)
            premul = rgb * alpha[:, None] + (track_rgb * track_alpha[:, None]) * (
                1.0 - alpha[:, None]
            )
            track_rgb = np.divide(
                premul,
                np.maximum(new_alpha[:, None], 1e-9),
                out=np.zeros_like(premul),
                where=new_alpha[:, None] > 1e-9,
            )
            track_alpha = new_alpha
            infos.append(
                {
                    "track_id": track.id,
                    "clip_id": clip.id,
                    "preset": clip.preset,
                    "local_t": float(local_t),
                    "opacity": float(opacity),
                    "alpha": float(clip_alpha),
                }
            )
        return track_rgb, track_alpha, infos

    def _render_clip(
        self,
        doc: ShowDoc,
        clip: Clip,
        local_t: float,
        t: float,
        beat: dict[str, float | int | bool],
        env: dict[str, float],
        group_idx: np.ndarray,
        group_rank: np.ndarray,
        group_size: int,
    ) -> np.ndarray:
        inertia_filters = [f for f in clip.filters if f.enabled and f.type == "inertia"]
        base = self._render_base(
            doc, clip, local_t, t, beat, env, group_idx, group_rank, group_size
        )
        if not inertia_filters:
            inertia_reference = None
        else:
            tau = 0.35
            raw_tau = inertia_filters[0].params.get("tau_s", tau)
            try:
                tau = max(0.05, float(raw_tau))
            except (TypeError, ValueError):
                tau = 0.35
            sample_local = [
                local_t,
                max(0.0, local_t - tau * 0.5),
                max(0.0, local_t - tau),
            ]
            samples = [
                self._render_base(
                    doc,
                    clip,
                    lt,
                    clip.start_s + lt,
                    None,
                    None,
                    group_idx,
                    group_rank,
                    group_size,
                )
                for lt in sample_local
            ]
            # A fixed, symmetric look-back keeps inertia deterministic when the
            # playhead is scrubbed instead of played linearly.
            inertia_reference = (
                0.50 * samples[0] + 0.30 * samples[1] + 0.20 * samples[2]
            )

        filter_ctx = FilterContext(
            positions=self.rig.positions,
            normalized=self.rig.normalized_positions,
            scene_min=np.asarray(self.rig.room.min, dtype=np.float32),
            scene_max=np.asarray(self.rig.room.max, dtype=np.float32),
            room_size=self.rig.room.size.astype(np.float32),
            t=t,
            local_t=local_t,
            timing=doc.timing,
            beat=beat,
            env=env,
            target_indices=group_idx,
            target_rank=group_rank,
            fps=self.fps,
            inertia_reference=inertia_reference,
            mirror_sources=self._mirror_sources,
        )
        return apply_filter_stack(base, clip.filters, filter_ctx)

    def _render_base(
        self,
        doc: ShowDoc,
        clip: Clip,
        local_t: float,
        sample_t: float,
        beat: dict[str, float | int | bool] | None,
        env: dict[str, float] | None,
        group_idx: np.ndarray,
        group_rank: np.ndarray,
        group_size: int,
    ) -> np.ndarray:
        params = clip.effective_params(max(0.0, local_t))
        sample_beat = beat if beat is not None else doc.beat_context(sample_t)
        sample_env = env if env is not None else self.env_provider(doc, sample_t)
        pctx = PresetContext(
            positions=self.rig.positions,
            normalized=self.rig.normalized_positions,
            scene_min=np.asarray(self.rig.room.min, dtype=np.float32),
            scene_max=np.asarray(self.rig.room.max, dtype=np.float32),
            room_size=self.rig.room.size.astype(np.float32),
            t=float(sample_t),
            local_t=float(local_t),
            beat=sample_beat,
            env={k: _clip01(float(v)) for k, v in sample_env.items()},
            params=params,
            target_indices=group_idx,
            target_rank=group_rank,
            target_size=group_size,
        )
        return render_preset(clip.preset, pctx)

    # -- blending ---------------------------------------------------------
    @staticmethod
    def _blend_track(
        master: np.ndarray,
        track: Track,
        group_idx: np.ndarray,
        track_rgb: np.ndarray,
        track_alpha: np.ndarray,
    ) -> None:
        if group_idx.size == 0:
            return
        src = track_rgb[group_idx]
        alpha = track_alpha[group_idx] * track.opacity
        if float(alpha.max()) <= 1e-6:
            return
        dst = master[group_idx]
        a = alpha[:, None]
        if track.blend_mode == "normal":
            master[group_idx] = src * a + dst * (1.0 - a)
        elif track.blend_mode == "add":
            master[group_idx] = dst + src * a
        elif track.blend_mode == "max":
            master[group_idx] = np.maximum(dst, src * a)
        elif track.blend_mode == "multiply":
            factor = (1.0 - a) + a * (src / 255.0)
            master[group_idx] = dst * factor
        elif track.blend_mode == "override":
            # Override deliberately discards lower layers in the target group;
            # fades therefore rise from black rather than cross-fading content.
            master[group_idx] = src * a
        else:  # pragma: no cover - guarded by Pydantic
            raise ValueError(f"unknown blend mode '{track.blend_mode}'")

    # -- ZCL snap propagation --------------------------------------------
    def _collect_snap_events(
        self,
        doc: ShowDoc,
        active_tracks: list[Track],
        t: float,
        beat: dict[str, float | int | bool],
    ) -> np.ndarray:
        snap = np.zeros(self.rig.fixture_count, dtype=bool)
        eps = self.snap_window_s
        grid = max(1e-6, doc.timing.beat_grid_s)
        phase = float(beat["beat_phase"])
        beat_window = eps / grid
        on_beat = phase <= beat_window or phase >= 1.0 - beat_window

        for track in active_tracks:
            group_idx, _rank, _size = self._target_info(track.target_group)
            for clip in track.clips:
                if not clip.snap_zcl:
                    continue
                local = clip.local_time(t)
                active = clip.contains(t)
                near_start = abs(t - clip.start_s) <= eps
                near_end = abs(t - clip.end_s) <= eps
                hold_tick = active and any(
                    kf.easing == "hold" and abs(local - kf.t_s) <= eps
                    for kf in clip.keyframes
                )
                if not (near_start or near_end or hold_tick or (active and on_beat)):
                    continue
                mask = evaluate_spatial_mask(
                    clip.spatial_mask,
                    self.rig.normalized_positions,
                    np.arange(self.rig.voxel_count, dtype=np.int64),
                )
                covered = group_idx[mask[group_idx] > 0.01]
                if covered.size == 0:
                    continue
                fixture_indices = np.unique(self.rig.voxel_fixture_index[covered])
                for fi in fixture_indices:
                    fixture = self.rig.rig.fixtures[int(fi)]
                    if fixture.capabilities.zcl_snap:
                        snap[int(fi)] = True
        return snap


__all__ = ["Compositor", "FrameResult", "deterministic_envelope"]
