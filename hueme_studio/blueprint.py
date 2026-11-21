"""Deterministic AudioBlueprint extraction, caching and envelope playback.

A blueprint is a compact, content-addressed analysis of one audio file: tempo,
beats, downbeats, sections, drops, three beat-indexed stem curves and a
downsampled waveform.  Extraction is pure numpy (via :mod:`hueme.show_analyzer`)
and the same input bytes always produce the same JSON, so a show can be
evaluated offline and at any timestamp without touching audio hardware.

Cache layout::

    config/blueprints/<sha1>.json   analysed blueprint
    config/blueprints/index.json    show_id -> cache key

The live compositor keeps using the ShowDoc beat grid for snapping; the
blueprint only supplies musical envelope curves, replacing the synthetic
:func:`hueme_studio.compositor.deterministic_envelope` when one is attached.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import tempfile
import threading
import wave
from bisect import bisect_right
from pathlib import Path
from typing import Any

import numpy as np

from hueme.show_analyzer import ShowAnalyzer

CACHE_VERSION = 1
WAVEFORM_BUCKETS = 1024


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Decode a 16-bit PCM WAV blob to mono float32.

    The analyzer deliberately supports one format.  Converting other formats
    belongs to the caller (or a future ffmpeg bridge), not to the cache.
    """
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"not a readable WAV file: {exc}") from exc
    if width != 2:
        raise ValueError(f"only 16-bit PCM WAV is supported (got {width * 8}-bit)")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1 and samples.size >= channels:
        usable = samples.size // channels * channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)
    return samples, int(rate)


def _waveform(samples: np.ndarray, buckets: int = WAVEFORM_BUCKETS) -> list[float]:
    if samples.size == 0:
        return [0.0] * buckets
    buckets = max(1, min(int(buckets), samples.size))
    edges = np.linspace(0, samples.size, buckets + 1, dtype=np.int64)
    peaks = np.empty(buckets, dtype=np.float64)
    for i in range(buckets):
        chunk = samples[edges[i] : edges[i + 1]]
        peaks[i] = float(np.max(np.abs(chunk))) if chunk.size else 0.0
    scale = max(float(np.percentile(peaks, 95)), 1e-6)
    return [round(min(1.0, value / scale), 3) for value in peaks]


class BlueprintEnvelope:
    """Callable ``(show, t) -> env`` backed by one blueprint.

    Keys match :func:`hueme_studio.compositor.deterministic_envelope` so the
    presets and filters do not need a second contract.  Values are stateless
    functions of time: scrubbing and hardware output stay deterministic.
    """

    def __init__(self, blueprint: dict[str, Any]) -> None:
        self.bpm = float(blueprint.get("bpm") or 120.0)
        self.beats = [float(v) for v in blueprint.get("beats", [])]
        self.downbeats = set(float(v) for v in blueprint.get("downbeats", []))
        curves = blueprint.get("curves", {})
        self.harmonic = np.asarray(curves.get("harmonic", []), dtype=np.float64)
        self.percussive = np.asarray(curves.get("percussive", []), dtype=np.float64)
        self.prominence = np.asarray(curves.get("prominence", []), dtype=np.float64)

    def _at(self, values: np.ndarray, index: int) -> float:
        if values.size == 0:
            return 0.0
        return float(np.clip(values[min(index, values.size - 1)], 0.0, 1.0))

    def __call__(self, show: Any, t: float) -> dict[str, float]:
        if not self.beats:
            return {k: 0.0 for k in ("pulse", "kick", "snare", "hat", "energy",
                                     "harmonic", "bass", "mid", "high")}
        index = max(0, bisect_right(self.beats, float(t)) - 1)
        start = self.beats[index]
        if index + 1 < len(self.beats):
            period = max(1e-3, self.beats[index + 1] - start)
        else:
            period = max(1e-3, 60.0 / self.bpm)
        phase = max(0.0, (float(t) - start) / period)
        phase = phase - math.floor(phase)

        perc = self._at(self.percussive, index)
        harm = self._at(self.harmonic, index)
        prom = self._at(self.prominence, index)
        pulse = math.exp(-phase * 3.0) * (0.45 + 0.55 * prom)
        kick = perc * math.exp(-phase * 6.0)
        snare = (perc * 0.9 + 0.05) * math.exp(-phase * 10.0) if index % 4 in (1, 3) else 0.02
        eighth = (phase * 2.0) % 1.0
        hat = (0.25 + 0.75 * perc) * math.exp(-eighth * 10.0)
        energy = float(np.clip(0.30 + 0.40 * harm + 0.30 * perc, 0.0, 1.0))
        return {
            "pulse": _clip(pulse),
            "kick": _clip(kick),
            "snare": _clip(snare),
            "hat": _clip(hat),
            "energy": energy,
            "harmonic": _clip(harm),
            "bass": _clip(0.15 + 0.85 * kick),
            "mid": _clip(0.20 + 0.80 * snare),
            "high": _clip(hat),
        }


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class BlueprintStore:
    """Content-addressed blueprint cache plus a show -> key index."""

    def __init__(self, cache_dir: str | Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.index_path = self.cache_dir / "index.json"
        self._lock = threading.Lock()
        self._analyzer = ShowAnalyzer(cache_dir=str(self.cache_dir / "tracks"))

    # -- index ------------------------------------------------------------
    def _read_index(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_index(self, show_id: str, key: str, source: str) -> None:
        index = self._read_index()
        index[show_id] = {"key": key, "source": source}
        _atomic_json(self.index_path, index)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _audio_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.wav"

    def audio_path(self, show_id: str) -> Path | None:
        """Original WAV for browser preview, when it was stored with the cache."""
        entry = self._read_index().get(show_id)
        if not isinstance(entry, dict):
            return None
        key = str(entry.get("key", ""))
        if not key:
            return None
        path = self._audio_path(key)
        return path if path.is_file() else None

    def has(self, show_id: str) -> bool:
        return self.get(show_id) is not None

    # -- cache ------------------------------------------------------------
    def get(self, show_id: str) -> dict[str, Any] | None:
        entry = self._read_index().get(show_id)
        if not isinstance(entry, dict):
            return None
        key = str(entry.get("key", ""))
        if not key:
            return None
        try:
            payload = json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if payload.get("schema_version") != CACHE_VERSION:
            return None
        return payload

    def extract(
        self, show_id: str, wav_bytes: bytes, *, source: str = "upload"
    ) -> dict[str, Any]:
        if not wav_bytes:
            raise ValueError("empty audio payload")
        key = hashlib.sha1(wav_bytes).hexdigest()
        with self._lock:
            cached = self._load_key(key)
            if cached is None:
                samples, rate = decode_wav(wav_bytes)
                cached = self._analyze(samples, rate, key, source)
                _atomic_json(self._path(key), cached)
            # Keep the original WAV beside the analysis so the Studio can play
            # it in the browser; the analysis JSON alone is not audible.
            if not self._audio_path(key).is_file():
                _atomic_bytes(self._audio_path(key), wav_bytes)
            self._write_index(show_id, key, source)
            return cached

    def extract_file(
        self, show_id: str, path: str | Path, *, source: str | None = None
    ) -> dict[str, Any]:
        file_path = Path(path)
        return self.extract(
            show_id,
            file_path.read_bytes(),
            source=source or file_path.name,
        )

    def _load_key(self, key: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if payload.get("schema_version") != CACHE_VERSION:
            return None
        return payload

    def _analyze(
        self, samples: np.ndarray, sample_rate: int, key: str, source: str
    ) -> dict[str, Any]:
        track = self._analyzer.analyze_audio(
            samples, sample_rate, track_key=key, cache=False
        )
        beats = [round(float(v), 4) for v in track.beats]
        curves = {
            "harmonic": [round(float(v), 4) for v in track.beat_harmonic],
            "percussive": [round(float(v), 4) for v in track.beat_percussive],
            "prominence": [round(float(v), 4) for v in track.beat_prominence],
        }
        return {
            "schema_version": CACHE_VERSION,
            "key": key,
            "source": source,
            "duration_s": round(float(track.duration), 4),
            "sample_rate": int(track.sample_rate),
            "bpm": round(float(track.tempo), 3),
            "beat_grid_s": round(60.0 / max(float(track.tempo), 1e-6), 6),
            "beats": beats,
            "downbeats": [round(float(v), 4) for v in track.downbeats],
            "sections": track.sections,
            "drops": track.drops,
            "drop_windows": track.drop_windows,
            "pre_drops": track.pre_drops,
            "curves": curves,
            "waveform": _waveform(np.asarray(samples, dtype=np.float32)),
        }

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def summary(
        blueprint: dict[str, Any] | None, *, audio_available: bool = False
    ) -> dict[str, Any] | None:
        if not blueprint:
            return None
        return {
            "key": blueprint.get("key"),
            "source": blueprint.get("source"),
            "duration_s": blueprint.get("duration_s"),
            "bpm": blueprint.get("bpm"),
            "beat_count": len(blueprint.get("beats", [])),
            "section_count": len(blueprint.get("sections", [])),
            "drop_count": len(blueprint.get("drops", [])),
            "audio_available": bool(audio_available),
        }

    @staticmethod
    def envelope(blueprint: dict[str, Any]) -> BlueprintEnvelope:
        return BlueprintEnvelope(blueprint)


__all__ = [
    "BlueprintEnvelope",
    "BlueprintStore",
    "CACHE_VERSION",
    "WAVEFORM_BUCKETS",
    "decode_wav",
]
