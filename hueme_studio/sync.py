"""Sendspin / Music Assistant show matching and playback-clock lock.

A ShowDoc carries a :class:`~hueme_studio.showdoc.MatchInfo` (artist, title,
Music Assistant item ids and normalised keys).  :class:`ShowMatcher` maps the
Sendspin metadata for the currently playing item onto one of those shows.

:class:`PlayheadPll` then locks the show timeline to the source's reported
track progress.  It is a deliberately small phase-locked loop: large offsets
snap, small offsets are corrected by trimming the playback rate a few percent
instead of visibly seeking every frame.  Both pieces are pure functions of
their inputs, so tests can advance them with a fake clock.

The runtime feeds this module through ``MaSync.tick()`` and applies the
returned :class:`SyncDecision`.  No audio data is consumed here; the source
only supplies metadata and a monotonic progress estimate.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

_PUNCT = re.compile(r"[\W_]+", re.UNICODE)
_WS = re.compile(r"\s+")

DEFAULT_MATCH_THRESHOLD = 0.75
DEFAULT_SNAP_S = 0.75
DEFAULT_LOCK_S = 0.05


def normalize_text(value: Any) -> str:
    """Case/accent/punctuation-insensitive text used for match keys."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _PUNCT.sub(" ", text.casefold())
    return _WS.sub(" ", text).strip()


def normalize_match_key(value: Any) -> str:
    """Normalise ``"Artist|Title"`` while keeping the separator."""
    parts = [normalize_text(part) for part in str(value or "").split("|")]
    return "|".join(part for part in parts if part)


def track_match_key(artist: Any, title: Any) -> str:
    return "|".join(
        part for part in (normalize_text(artist), normalize_text(title)) if part
    )


class ShowMatcher:
    """Score a Sendspin track against show match metadata.

    ``shows`` is the list of summaries returned by ``ShowStore.list_shows()``.
    The matcher is intentionally conservative: a title-only match must be
    unambiguous, and an unknown artist cannot override a conflicting artist on
    the show.
    """

    def __init__(self, threshold: float = DEFAULT_MATCH_THRESHOLD) -> None:
        self.threshold = float(threshold)

    @staticmethod
    def _show_fields(show: Mapping[str, Any]) -> tuple[set[str], str, str, float]:
        match = show.get("match")
        if not isinstance(match, Mapping):
            match = {}
        keys = {
            normalize_match_key(key)
            for key in (match.get("normalized_keys") or [])
            if str(key or "").strip()
        }
        artist = str(match.get("artist") or show.get("artist") or "")
        title = str(match.get("title") or show.get("title") or "")
        if artist or title:
            key = track_match_key(artist, title)
            if key:
                keys.add(key)
        duration = float(
            match.get("duration_s") or show.get("duration_s") or 0.0
        )
        return keys, normalize_text(artist), normalize_text(title), duration

    def score(
        self,
        show: Mapping[str, Any],
        *,
        key: str = "",
        artist: str = "",
        title: str = "",
        duration_s: float = 0.0,
        item_id: str | None = None,
    ) -> float:
        keys, show_artist, show_title, show_duration = self._show_fields(show)
        match = show.get("match") if isinstance(show.get("match"), Mapping) else {}
        item_id = str(item_id or "").strip()
        if item_id and item_id in {
            str(value) for value in (match.get("ma_item_ids") or [])
        }:
            base = 1.0
        else:
            track_key = normalize_match_key(key) or track_match_key(artist, title)
            artist_key = normalize_text(artist)
            title_key = normalize_text(title)
            if track_key and track_key in keys:
                base = 1.0
            elif artist_key and title_key:
                # Both known but no key match: never guess from the title alone.
                base = 0.0
            elif title_key and title_key == show_title:
                if artist_key and show_artist and artist_key != show_artist:
                    base = 0.5
                elif artist_key and show_artist:
                    base = 1.0
                elif show_artist:
                    base = 0.85
                else:
                    base = 0.8
            else:
                base = 0.0

        if base <= 0.0:
            return 0.0
        if duration_s > 0.0 and show_duration > 0.0:
            delta = abs(float(duration_s) - show_duration)
            if delta <= 1.5:
                base += 0.1
            elif delta <= 4.0:
                base += 0.02
            elif delta > 10.0:
                base -= 0.2
        return max(0.0, min(1.0, base))

    def match(
        self,
        shows: Iterable[Mapping[str, Any]],
        *,
        key: str = "",
        artist: str = "",
        title: str = "",
        duration_s: float = 0.0,
        item_id: str | None = None,
    ) -> str | None:
        scored: list[tuple[float, str]] = []
        for show in shows:
            show_id = str(show.get("id") or "").strip()
            if not show_id:
                continue
            score = self.score(
                show,
                key=key,
                artist=artist,
                title=title,
                duration_s=duration_s,
                item_id=item_id,
            )
            if score >= self.threshold:
                scored.append((score, show_id))
        if not scored:
            return None
        scored.sort(key=lambda item: (-item[0], item[1]))
        best_score, best_id = scored[0]
        if len(scored) > 1 and abs(scored[1][0] - best_score) < 1e-9:
            return None
        return best_id


@dataclass
class PlayheadPll:
    """Phase-locked playhead with rate trimming and hard-resync threshold."""

    kp: float = 0.25
    ki: float = 0.03
    max_rate_adjust: float = 0.04
    snap_s: float = DEFAULT_SNAP_S
    lock_s: float = DEFAULT_LOCK_S
    lock_updates: int = 3
    integral: float = 0.0
    last_error: float = 0.0
    last_rate: float = 1.0
    _lock_count: int = field(default=0, init=False)
    _snaps: int = field(default=0, init=False)

    @property
    def locked(self) -> bool:
        return self._lock_count >= self.lock_updates

    @property
    def snaps(self) -> int:
        return self._snaps

    def reset(self) -> None:
        self.integral = 0.0
        self.last_error = 0.0
        self.last_rate = 1.0
        self._lock_count = 0

    def update(self, playhead: float, target: float, dt: float) -> float:
        """Return the next playhead; snaps when the error is too large."""
        playhead = float(playhead)
        target = float(target)
        dt = max(0.0, min(0.25, float(dt)))
        error = target - playhead
        self.last_error = error
        if abs(error) >= self.snap_s:
            self.integral = 0.0
            self._lock_count = 0
            self._snaps += 1
            self.last_rate = 1.0
            self.last_error = 0.0
            return target
        if dt > 0.0:
            self.integral = max(
                -0.5, min(0.5, self.integral * 0.98 + error * dt)
            )
            rate = 1.0 + self.kp * error + self.ki * self.integral
            limit = max(0.0, float(self.max_rate_adjust))
            rate = max(1.0 - limit, min(1.0 + limit, rate))
            playhead += dt * rate
            self.last_rate = rate
        self.last_error = target - playhead
        if abs(self.last_error) <= self.lock_s:
            self._lock_count += 1
        else:
            self._lock_count = 0
        return playhead


@dataclass(slots=True)
class SyncDecision:
    """What the runtime should do with the transport this tick."""

    controlled: bool = False
    show_id: str | None = None
    playhead: float | None = None
    playing: bool | None = None


class MaSync:
    """Match Sendspin tracks to shows and lock the playhead to their progress."""

    def __init__(
        self,
        source: Any,
        *,
        enabled: bool = True,
        offset_s: float = 0.0,
        matcher: ShowMatcher | None = None,
        snap_s: float = DEFAULT_SNAP_S,
        lock_s: float = DEFAULT_LOCK_S,
    ) -> None:
        self.source = source
        self.enabled = bool(enabled)
        self.offset_s = float(offset_s)
        self.matcher = matcher or ShowMatcher()
        self.pll = PlayheadPll(snap_s=float(snap_s), lock_s=float(lock_s))
        self._matched_show: str | None = None
        self._track_key: str | None = None
        self._had_track = False
        self._state = "off" if not enabled else "waiting"
        self._status: dict[str, Any] = self._base_status()
        self._shows: list[dict[str, Any]] = []
        self._shows_signature: tuple[str, ...] = ()

    # -- configuration ----------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled != self.enabled:
            self.enabled = enabled
            self._matched_show = None
            self._track_key = None
            self._had_track = False
            self.pll.reset()
            self._state = "waiting" if enabled else "off"
            self._status = self._base_status()

    def set_offset_s(self, offset_s: float) -> None:
        value = float(offset_s)
        if not math.isfinite(value):
            raise ValueError("offset must be finite")
        self.offset_s = max(-2.0, min(2.0, value))
        self._status["offset_s"] = self.offset_s

    def state_key(self) -> tuple[str, str | None, str | None]:
        return (self._state, self._matched_show, self._track_key)

    def drop_match(self, show_id: str | None = None) -> None:
        """Forget a cached match, e.g. when its show was deleted mid-tick."""
        if show_id is None or self._matched_show == show_id:
            self._matched_show = None
            self._state = "no_match"
            self.pll.reset()

    # -- state ------------------------------------------------------------
    def _base_status(self) -> dict[str, Any]:
        source = self.source
        return {
            "enabled": self.enabled,
            "configured": source is not None,
            "state": self._state,
            "source": "sendspin" if source is not None else None,
            "connected": bool(getattr(source, "connected", False)) if source else False,
            "matched_show_id": self._matched_show,
            "track": None,
            "offset_s": self.offset_s,
            "error_s": 0.0,
            "rate": 1.0,
            "locked": False,
            "snaps": self.pll.snaps,
            "last_error": getattr(source, "last_error", None) if source else None,
        }

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    def _snapshot_track(self) -> dict[str, Any]:
        source = self.source
        if source is None:
            return {}
        return {
            "key": getattr(source, "track_key", None),
            "artist": str(getattr(source, "track_artist", "") or ""),
            "title": str(getattr(source, "track_title", "") or ""),
            "duration_s": float(getattr(source, "track_duration_s", 0.0) or 0.0),
            "progress_s": float(getattr(source, "track_progress_s", 0.0) or 0.0),
            "playing": bool(getattr(source, "track_playing", True)),
        }

    def _show_limit(self, show_id: str | None, track_duration: float) -> float:
        for show in self._shows:
            if str(show.get("id") or "") == str(show_id or ""):
                return float(show.get("duration_s") or track_duration or 0.0)
        return track_duration

    def _refresh_status(
        self,
        *,
        track: dict[str, Any] | None,
        connected: bool,
        duration_limit: float = 0.0,
        playhead: float = 0.0,
    ) -> None:
        status = self._base_status()
        status["state"] = self._state
        status["track"] = track
        status["connected"] = connected
        status["matched_show_id"] = self._matched_show
        status["error_s"] = round(float(self.pll.last_error), 5)
        status["rate"] = round(float(self.pll.last_rate), 5)
        status["locked"] = bool(self.pll.locked)
        status["snaps"] = self.pll.snaps
        status["duration_s"] = round(float(duration_limit), 4)
        status["playhead_s"] = round(float(playhead), 4)
        self._status = status

    # -- control loop ----------------------------------------------------
    def tick(
        self,
        *,
        now: float,
        dt: float,
        current_show_id: str | None,
        playhead: float,
        shows: list[dict[str, Any]] | None = None,
    ) -> SyncDecision:
        shows_changed = False
        if shows is not None:
            signature = tuple(
                json.dumps(
                    [show.get("id"), show.get("match"), show.get("duration_s")],
                    sort_keys=True,
                    default=str,
                )
                for show in shows
            )
            shows_changed = signature != self._shows_signature
            self._shows = list(shows)
            self._shows_signature = signature
        if not self.enabled:
            self._state = "off"
            self._refresh_status(track=None, connected=False)
            return SyncDecision()
        source = self.source
        if source is None:
            self._state = "unconfigured"
            self._refresh_status(track=None, connected=False)
            return SyncDecision()

        connected = bool(getattr(source, "connected", False))
        track = self._snapshot_track()
        key = str(track.get("key") or "").strip()
        artist = str(track.get("artist") or "")
        title = str(track.get("title") or "")
        duration = float(track.get("duration_s") or 0.0)
        progress = float(track.get("progress_s") or 0.0)
        source_playing = bool(track.get("playing", True))

        if not connected:
            self._state = "disconnected"
            self._matched_show = None
            self._track_key = None
            self._had_track = False
            self._refresh_status(track=track, connected=False)
            return SyncDecision()

        if not key and not artist and not title:
            self._state = "no_track"
            self._matched_show = None
            self._track_key = None
            self._refresh_status(track=None, connected=True)
            if self._had_track:
                self._had_track = False
                return SyncDecision(controlled=True, playing=False)
            return SyncDecision()

        self._had_track = True
        track_key = key or track_match_key(artist, title)
        if track_key != self._track_key or shows_changed:
            self._track_key = track_key
            self._matched_show = self.matcher.match(
                self._shows,
                key=key,
                artist=artist,
                title=title,
                duration_s=duration,
                item_id=getattr(source, "track_item_id", None),
            )
            self.pll.reset()

        if not self._matched_show:
            self._state = "no_match"
            self._refresh_status(track=track, connected=True, playhead=playhead)
            return SyncDecision(controlled=True, playing=False)

        duration_limit = self._show_limit(self._matched_show, duration)
        # Positive offset delays the lights, matching the legacy Sync trim.
        target = progress - self.offset_s
        if duration_limit > 0.0:
            target = max(0.0, min(duration_limit, target))
        else:
            target = max(0.0, target)

        switching = self._matched_show != current_show_id
        if switching:
            self.pll.reset()
            playing = source_playing and (duration <= 0.0 or progress < duration - 0.05)
            self._state = "acquiring" if playing else "paused" if not source_playing else "ended"
            self._refresh_status(
                track=track,
                connected=True,
                duration_limit=duration_limit,
                playhead=target,
            )
            return SyncDecision(
                controlled=True,
                show_id=self._matched_show,
                playhead=target,
                playing=playing,
            )

        track_ended = duration > 0.0 and progress >= duration - 0.05
        ended = track_ended or (
            duration_limit > 0.0 and target >= duration_limit - 1e-3
        )
        if ended:
            self._state = "ended"
            self.pll.reset()
            end_playhead = target if track_ended else duration_limit
            self._refresh_status(
                track=track,
                connected=True,
                duration_limit=duration_limit,
                playhead=end_playhead,
            )
            return SyncDecision(
                controlled=True, playhead=end_playhead, playing=False
            )

        if not source_playing:
            self._state = "paused"
            self.pll.reset()
            self.pll.last_error = target - playhead
            self._refresh_status(
                track=track,
                connected=True,
                duration_limit=duration_limit,
                playhead=target,
            )
            return SyncDecision(controlled=True, playhead=target, playing=False)

        next_playhead = self.pll.update(playhead, target, dt)
        self._state = "locked" if self.pll.locked else "acquiring"
        self._refresh_status(
            track=track,
            connected=True,
            duration_limit=duration_limit,
            playhead=next_playhead,
        )
        return SyncDecision(
            controlled=True, playhead=next_playhead, playing=True
        )


__all__ = [
    "DEFAULT_LOCK_S",
    "DEFAULT_MATCH_THRESHOLD",
    "DEFAULT_SNAP_S",
    "MaSync",
    "PlayheadPll",
    "ShowMatcher",
    "SyncDecision",
    "normalize_match_key",
    "normalize_text",
    "track_match_key",
]
