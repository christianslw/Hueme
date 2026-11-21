# Hueme Studio — AudioBlueprint

`hueme_studio/blueprint.py` turns one audio file into a deterministic,
cacheable score that the compositor can sample at any timestamp. It wraps the
existing offline analyzer (`hueme/show_analyzer.py`, numpy only) and adds a
content-addressed cache plus the live envelope adapter.

## Format

```jsonc
{
  "schema_version": 1,
  "key": "<sha1 of wav bytes>",
  "source": "track.wav",
  "duration_s": 236.25,
  "sample_rate": 48000,
  "bpm": 128.0,
  "beat_grid_s": 0.46875,
  "beats": [0.0, 0.4688, ...],
  "downbeats": [0.0, 1.875, ...],
  "sections": [{"label": "intro", "start": 0.0, "end": 33.75, ...}],
  "drops": [33.75, 101.25],
  "drop_windows": [{"start": 33.75, "end": 67.5}],
  "pre_drops": [{"start": 33.28, "end": 33.75, "impact": 33.75}],
  "curves": {
    "harmonic": [0.0, 0.62, ...],     // one value per beat
    "percussive": [0.0, 0.91, ...],
    "prominence": [0.0, 0.74, ...]
  },
  "waveform": [0.0, 0.12, ...]        // 1024 normalized peak buckets
}
```

The analysis is pure numpy and deterministic: identical WAV bytes produce an
identical blueprint, regardless of file name or mtime.

## Cache

```
config/blueprints/<sha1>.json    analysed blueprint
config/blueprints/<sha1>.wav     original WAV for browser preview
config/blueprints/index.json     show_id -> {key, source}
```

The cache directory is gitignored. `BlueprintStore.extract()` hashes the WAV,
returns a cached blueprint when present, and only runs the analyzer on a miss.
The original WAV is copied beside the analysis once, so the browser can play and
seek it without re-uploading. Show files stay untouched; attachment lives in the
index, so switching shows does not rewrite a ShowDoc.

## Browser preview

When a WAV is attached, the Studio timeline plays it with the transport:

- play/pause follows the server transport,
- scrubbing the ruler seeks both the show playhead and the `<audio>` element,
- the **Vol** slider in the timeline toolbar sets local monitor volume.

The WAV is served by `GET /api/shows/{id}/audio` with byte-range support, so
seeking is instant. `GET /api/studio/state` exposes
`show.blueprint.audio_available`. Older cached blueprints have no `.wav`; upload
the track once more to enable preview.

This is a **local monitor**, separate from Music Assistant playback. MA audio
stays on the MA zone; the browser WAV is for editing. Set **Vol** to zero when
monitoring the MA speakers, otherwise both will be heard.

Only **16-bit PCM WAV** is accepted. Decoding other containers is an upstream
concern (ffmpeg/MA), not the cache's.

## Live envelope

When a blueprint is attached, `StudioRuntime` sets
`Compositor.env_provider = BlueprintEnvelope(blueprint)`. It supplies the same
keys as the synthetic `deterministic_envelope`:

`pulse`, `kick`, `snare`, `hat`, `energy`, `harmonic`, `bass`, `mid`, `high`.

Values are stateless functions of time: scrubbing, preview, hardware output and
MCP inspection all evaluate the same envelope. ShowDoc timing remains the
authority for the editing grid and snapping; the blueprint informs the musical
curves. Detaching (or a show without a blueprint) restores the synthetic
envelope exactly as before.

## API

- `POST /api/shows/{id}/blueprint?source=name` — raw WAV body; returns summary.
- `GET /api/shows/{id}/blueprint` — full blueprint or 404.
- `GET /api/shows/{id}/audio` — attached WAV with Range support, or 404.
- MCP `blueprint_extract` / `blueprint_get` — same operations for agents.
- Studio timeline **Audio** button — upload a WAV for the active show.
- Studio timeline **Vol** slider — local monitor volume.

The timeline draws the waveform, section boundaries and drop windows from this
document. It never draws a placeholder waveform when none is attached.
