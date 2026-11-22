from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hueme_studio.server import create_app
from hueme_studio.sync import (
    MaSync,
    PlayheadPll,
    ShowMatcher,
    normalize_match_key,
    normalize_text,
)

ROOT = Path(__file__).resolve().parents[1]
RIGS_DIR = ROOT / "config" / "rigs"
SHOWS_DIR = ROOT / "config" / "shows"

SHOW = {
    "id": "dance_loft_v1",
    "name": "Dance Loft V1",
    "duration_s": 236.25,
    "match": {
        "artist": "Demo Artist",
        "title": "Dance Loft 128",
        "normalized_keys": ["demo artist|dance loft 128"],
        "ma_item_ids": ["ma-item-1"],
        "duration_s": 236.25,
    },
}


class FakeSource:
    def __init__(self) -> None:
        self.connected = True
        self.track_key = "demo artist|dance loft 128"
        self.track_title = "Dance Loft 128"
        self.track_artist = "Demo Artist"
        self.track_item_id = None
        self.track_duration_s = 236.25
        self.progress = 42.0
        self.track_playing = True
        self.last_error = None
        self.started = False
        self.closed = False

    @property
    def track_progress_s(self) -> float:
        return self.progress

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True


def test_text_normalisation_is_accent_and_punctuation_insensitive():
    assert normalize_text("  Björk — Jóga (Live) ") == "bjork joga live"
    assert normalize_match_key("Demo Artist | Dance Loft 128") == (
        "demo artist|dance loft 128"
    )


def test_matcher_exact_key_item_id_and_title_only():
    matcher = ShowMatcher()
    assert (
        matcher.match(
            [SHOW],
            key="Demo Artist|Dance Loft 128",
            duration_s=236.25,
        )
        == "dance_loft_v1"
    )
    assert matcher.match([SHOW], item_id="ma-item-1") == "dance_loft_v1"
    assert matcher.match([SHOW], title="Dance Loft 128") == "dance_loft_v1"
    assert matcher.match([SHOW], artist="Someone Else", title="Dance Loft 128") is None
    # MA often appends live/remix tags to the title. Same artist + title prefix
    # is still the same song; a different artist never matches.
    assert (
        matcher.match(
            [SHOW], artist="Demo Artist", title="Dance Loft 128 (Live)"
        )
        == "dance_loft_v1"
    )
    assert (
        matcher.match(
            [SHOW], artist="Demo Artist", title="Dance Loft 128 VIP Mix"
        )
        == "dance_loft_v1"
    )
    assert (
        matcher.match(
            [SHOW], artist="Demo Artist, Guest Vocalist", title="Dance Loft 128"
        )
        == "dance_loft_v1"
    )
    assert (
        matcher.match(
            [SHOW], artist="Someone Else", title="Dance Loft 128 (Live)"
        )
        is None
    )
    assert matcher.match([SHOW], title="Dance Loft 128", duration_s=999.0) is None


def test_matcher_refuses_ambiguous_title_match():
    other = {
        **SHOW,
        "id": "other",
        "match": {
            "artist": "Demo Artist",
            "title": "Dance Loft 128",
            "normalized_keys": ["demo artist|dance loft 128"],
            "ma_item_ids": [],
            "duration_s": 236.25,
        },
    }
    assert ShowMatcher().match([SHOW, other], title="Dance Loft 128") is None
    assert (
        ShowMatcher().match(
            [SHOW, other], title="Dance Loft 128", prefer_show_id="other"
        )
        == "other"
    )


def test_pll_snaps_large_jumps_and_trims_small_error():
    pll = PlayheadPll(snap_s=0.5)
    assert pll.update(0.0, 10.0, 1 / 30) == 10.0
    assert pll.snaps == 1

    trimmed = pll.update(0.0, 0.2, 0.1)
    assert trimmed > 0.1
    assert pll.last_rate > 1.0


def test_pll_reports_lock_when_error_stays_small():
    pll = PlayheadPll(lock_updates=5)
    playhead = 0.0
    for index in range(20):
        playhead = pll.update(playhead, index / 30.0, 1 / 30)
    assert pll.locked
    assert pll.last_error == pytest.approx(0.0, abs=0.04)


def test_sync_matches_track_and_applies_positive_offset():
    source = FakeSource()
    source.progress = 10.0
    sync = MaSync(source, offset_s=0.1)
    decision = sync.tick(
        now=100.0,
        dt=1 / 30,
        current_show_id=None,
        playhead=0.0,
        shows=[SHOW],
    )
    assert decision.controlled
    assert decision.show_id == "dance_loft_v1"
    assert decision.playhead == pytest.approx(9.9)
    assert decision.playing is True
    assert sync.status()["state"] == "acquiring"


def test_sync_retries_when_the_show_library_changes():
    source = FakeSource()
    sync = MaSync(source)
    first = sync.tick(
        now=1.0,
        dt=1 / 30,
        current_show_id=None,
        playhead=0.0,
        shows=[],
    )
    assert first.playing is False
    assert sync.status()["state"] == "no_match"

    second = sync.tick(
        now=1.1,
        dt=1 / 30,
        current_show_id=None,
        playhead=0.0,
        shows=[SHOW],
    )
    assert second.controlled
    assert second.show_id == "dance_loft_v1"


def test_sync_stops_at_track_end_even_when_the_show_is_longer():
    source = FakeSource()
    source.track_duration_s = 100.0
    source.progress = 100.0
    shows = [{**SHOW, "duration_s": 300.0}]
    sync = MaSync(source)
    sync.tick(
        now=1.0,
        dt=1 / 30,
        current_show_id=None,
        playhead=0.0,
        shows=shows,
    )
    decision = sync.tick(
        now=1.1,
        dt=1 / 30,
        current_show_id="dance_loft_v1",
        playhead=100.0,
        shows=shows,
    )
    assert decision.playing is False
    assert decision.playhead == pytest.approx(100.0)
    assert sync.status()["state"] == "ended"


def test_sync_pauses_on_unknown_track_and_stops_controlling_when_disconnected():
    source = FakeSource()
    sync = MaSync(source)
    sync.tick(
        now=1.0,
        dt=1 / 30,
        current_show_id="dance_loft_v1",
        playhead=42.0,
        shows=[SHOW],
    )
    source.track_key = "other artist|other song"
    source.track_artist = "Other Artist"
    source.track_title = "Other Song"
    paused = sync.tick(
        now=1.1,
        dt=1 / 30,
        current_show_id="dance_loft_v1",
        playhead=42.0,
        shows=[SHOW],
    )
    assert paused.controlled
    assert paused.playing is False
    assert sync.status()["state"] == "no_match"

    source.connected = False
    source.track_key = "demo artist|dance loft 128"
    disconnected = sync.tick(
        now=1.2,
        dt=1 / 30,
        current_show_id="dance_loft_v1",
        playhead=42.0,
        shows=[SHOW],
    )
    assert disconnected.controlled is False
    assert sync.status()["state"] == "disconnected"


def test_runtime_switches_show_and_locks_playhead_to_sendspin(tmp_path):
    rigs = tmp_path / "rigs"
    shows = tmp_path / "shows"
    shutil.copytree(RIGS_DIR, rigs)
    shutil.copytree(SHOWS_DIR, shows)
    source = FakeSource()
    # The show is user data: bind the fake source to whatever this show's
    # match block currently says instead of assuming the demo track.
    show_payload = json.loads((shows / "dance_loft_v1.json").read_text())
    match = show_payload.get("match") or {}
    source.track_artist = match.get("artist") or ""
    source.track_title = match.get("title") or ""
    source.track_key = normalize_match_key(
        f"{source.track_artist}|{source.track_title}"
    )
    source.track_duration_s = float(
        match.get("duration_s") or show_payload.get("duration_s") or 0
    )
    app = create_app(
        rigs_dir=rigs,
        shows_dir=shows,
        fps=1.0,
        sync_source=source,
    )
    with TestClient(app) as client:
        runtime = app.state.studio
        assert runtime.sync is not None
        assert runtime.sync.enabled

        # Add a second show and make it active; the MA match must switch back.
        other = runtime.shows.get("dance_loft_v1").model_copy(deep=True)
        other.id = "other_show"
        other.name = "Other Show"
        other.match.artist = "Other Artist"
        other.match.title = "Other Song"
        other.match.normalized_keys = ["other artist|other song"]
        runtime.create_show(other)
        runtime.apply_transport({"show_id": "other_show"})

        source.progress = 12.5
        runtime._last_tick -= 0.1
        runtime._advance()
        assert runtime.show_id == "dance_loft_v1"
        assert runtime.playhead == pytest.approx(12.5)
        assert runtime.playing is True

        status = client.get("/api/sync").json()
        assert status["configured"] is True
        assert status["matched_show_id"] == "dance_loft_v1"
        assert status["state"] in {"acquiring", "locked"}

        tuned = client.post("/api/sync", json={"offset_s": 0.05}).json()
        assert tuned["offset_s"] == pytest.approx(0.05)

        disabled = client.post("/api/sync", json={"enabled": False}).json()
        assert disabled["enabled"] is False


def test_runtime_without_sync_source_reports_unconfigured(tmp_path):
    rigs = tmp_path / "rigs"
    shows = tmp_path / "shows"
    shutil.copytree(RIGS_DIR, rigs)
    shutil.copytree(SHOWS_DIR, shows)
    app = create_app(rigs_dir=rigs, shows_dir=shows, fps=1.0)
    with TestClient(app) as client:
        status = client.get("/api/sync").json()
        assert status["configured"] is False
        assert status["enabled"] is False
        assert client.get("/api/studio/state").json()["sync"]["configured"] is False
