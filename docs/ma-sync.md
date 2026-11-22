# Hueme Studio — Music Assistant sync

Sprint 4 locks the Studio playhead to the track Music Assistant is playing
through Sendspin. The match is declarative: a ShowDoc says which artist/title
it belongs to, and the runtime follows the Sendspin metadata clock.

## Show side

`ShowDoc.match` already exists in the v1 schema:

```jsonc
"match": {
  "artist": "Demo Artist",
  "title": "Dance Loft 128",
  "normalized_keys": ["demo artist|dance loft 128"],
  "ma_item_ids": [],
  "duration_s": 236.25
}
```

`normalized_keys` is filled from `artist|title` when omitted. Matching ignores
case, accents, punctuation and repeated whitespace, so
`"Björk — Jóga (Live)"` and `"bjork joga live"` produce the same key.

Rules, in order:

1. Exact normalised key or `ma_item_ids` entry: score 1.0.
2. Artist **and** title known but no key match: no match. The matcher never
   guesses across a conflicting artist.
3. Title-only metadata: the best-scoring show must be unambiguous. A show with
   a known artist scores above a show with no artist, and a conflicting known
   artist scores below the threshold.
4. Duration within 1.5 s adds 0.1; within 4 s adds 0.02; more than 10 s away
   subtracts 0.2.
5. Two shows with the same best score are ambiguous and match nothing.

The show list is refreshed at 1 Hz while sync is enabled, so new/renamed shows
are picked up without restarting.

## Latency trim

Sendspin metadata carries a server timestamp for the reported progress; the
source maps it through the client clock to local playback time before the PLL
sees it. That is the automatic part. If the MA speaker chain still has a fixed
delay, open a show's **Show** tab → **Music Assistant** → **Sync-Offset**:
positive delays the lights, negative advances them; the value is kept in the
browser and re-applied on load. `--sync-offset-ms` remains available for a
fixed startup offset.

## Playhead lock

`hueme_studio/sync.py` contains two small deterministic pieces:

- `ShowMatcher` maps today's Sendspin metadata to a show id.
- `PlayheadPll` drives the show playhead from the source's reported track
  progress.

The PLL is deliberately not a seek loop:

- `|error| >= 750 ms` -> hard snap to the target.
- Smaller errors -> playback rate is trimmed by at most ±4 % until the phase
  error closes.
- Locked means `|error| <= 50 ms` for three consecutive updates.
- `offset_s` positive delays the lights, matching the legacy Sync trim.
- A paused item (`playback_speed == 0`) parks the playhead at its position;
  a cleared item pauses playback; a disconnected source leaves manual
  transport alone and resyncs on the next valid track.

Only metadata and the progress estimate are consumed by the Studio. The
existing `hueme.SendspinAudioSource` still owns PCM buffering for the legacy
engine; the Studio does not read or analyse live audio.

MA audio is played by Music Assistant on the MA zone. The Studio browser monitor
plays the show's attached WAV instead; set **Vol** to zero when listening to the
MA speakers so the two copies do not overlap. Upload the show WAV with the
timeline **Audio** button if you want to hear and scrub it locally while
editing. See `docs/audio-blueprint.md`.

## Runtime

`MaSync.tick()` returns a `SyncDecision`; `StudioRuntime._advance()` applies it
before the normal playhead integrator. On a match it switches the active show,
sets `playing`, and writes the locked playhead. On an unknown track it pauses
instead of running the previous show against new music.

`GET /api/sync` returns:

```jsonc
{
  "configured": true,
  "enabled": true,
  "state": "locked",              // off|waiting|disconnected|no_track|
                                  // no_match|acquiring|locked|paused|ended
  "connected": true,
  "matched_show_id": "dance_loft_v1",
  "track": {
    "key": "demo artist|dance loft 128",
    "artist": "Demo Artist",
    "title": "Dance Loft 128",
    "duration_s": 236.25,
    "progress_s": 42.31,
    "playing": true
  },
  "offset_s": 0.0,
  "error_s": 0.004,
  "rate": 1.001,
  "locked": true,
  "snaps": 0
}
```

`POST /api/sync` accepts `{"enabled": bool, "offset_s": seconds}`. The Studio
header has an `MA ...` chip; click it to toggle sync. Sync events ride the
normal `/ws/preview` invalidation bus (`scope: "sync"`).

## CLI

```bash
# gateway.json is loaded automatically; a configured audio.sendspin.url
# enables MA auto-show by default.
python3 -m hueme_studio

# Explicit URL (implies sync)
python3 -m hueme_studio --sendspin-url ws://ma.local:8927/sendspin

# Keep auto-show off for this run
python3 -m hueme_studio --no-sync

# Calibrate: positive delays lights
python3 -m hueme_studio --sync-offset-ms 40
```

Without a Sendspin URL, `/api/sync` reports `configured: false` and playback
stays fully manual. Code paths that call `create_app()` in tests never connect
to a remote MA server.

The Sendspin client must be paired in Music Assistant. Studio → **Hilfe →
Sendspin Pairing-Token** shows the token; paste it in Music Assistant under the
`Hueme-Visualizer` player → Setup required → Pair device. The token is stable
for the persisted identity/pairing file.

## Deliberate limits

- Progress comes from Sendspin metadata, not beat-level audio analysis; drift
  inside one metadata interval is handled by the PLL rate trim.
- No automatic AudioBlueprint extraction from MA; attach a WAV in the timeline
  as before.
- No sub-beat sync or per-fixture delay offsets yet.
- No automatic show creation from metadata; the matcher only chooses existing
  shows.
