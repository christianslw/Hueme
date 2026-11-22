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
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import wave
from bisect import bisect_right
from pathlib import Path
from typing import Any

import numpy as np

from hueme.show_analyzer import ShowAnalyzer

CACHE_VERSION = 2
WAVEFORM_BUCKETS = 1024
ANALYZER_TIMEOUT_S = 120.0
SECTION_LABELS = {
    "intro",
    "verse",
    "chorus",
    "build",
    "drop",
    "bridge",
    "break",
    "outro",
    "pre_drop",
}
SECTION_ALIASES = {
    "breakdown": "break",
    "pre-drop": "pre_drop",
    "predrop": "pre_drop",
    "build_up": "build",
    "buildup": "build",
    "inst": "verse",
    "instrumental": "verse",
    "solo": "bridge",
}


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


def _looks_like_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def convert_to_wav(data: bytes, source: str = "upload") -> bytes:
    """Return a 16-bit PCM WAV, transcoding arbitrary formats with ffmpeg.

    MP3, FLAC, M4A/AAC, MP4, OGG/Opus and WAV all arrive here.  WAV bytes are
    passed through untouched so existing content caches stay valid.  ffmpeg is
    an optional system dependency: a clear error is raised when it is absent.
    """
    if not data:
        raise ValueError("empty audio payload")
    if _looks_like_wav(data):
        return data
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError(
            "ffmpeg is required for MP3/FLAC/M4A/MP4/OGG audio imports"
        )
    suffix = Path(str(source or "upload")).suffix or ".bin"
    with tempfile.TemporaryDirectory(prefix="hueme-audio-") as tmp:
        src = Path(tmp) / f"input{suffix}"
        out = Path(tmp) / "output.wav"
        src.write_bytes(data)
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-sample_fmt",
            "s16",
            "-f",
            "wav",
            str(out),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"ffmpeg could not decode {source}: {exc}") from exc
        if result.returncode != 0 or not out.is_file() or out.stat().st_size < 44:
            detail = (result.stderr or "ffmpeg produced no audio").strip()
            raise ValueError(f"ffmpeg could not decode {source}: {detail[:400]}")
        return out.read_bytes()


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
        self.energy = np.asarray(curves.get("energy", []), dtype=np.float64)
        self.bass = np.asarray(curves.get("bass", []), dtype=np.float64)
        self.sections = [
            section
            for section in (blueprint.get("sections") or [])
            if isinstance(section, dict)
        ]
        self.drops = [float(value) for value in blueprint.get("drops", [])]
        self.drop_windows = [
            window
            for window in (blueprint.get("drop_windows") or [])
            if isinstance(window, dict)
        ]

    def _at(self, values: np.ndarray, index: int) -> float:
        if values.size == 0:
            return 0.0
        return float(np.clip(values[min(index, values.size - 1)], 0.0, 1.0))

    def _section_progress(self, t: float) -> float:
        for section in self.sections:
            start = float(section.get("start", 0.0))
            end = float(section.get("end", start))
            if start <= t < end and end > start:
                return _clip((t - start) / (end - start))
        return 0.0

    def _in_drop(self, t: float) -> float:
        for window in self.drop_windows:
            start = float(window.get("start", 0.0))
            end = float(window.get("end", start))
            if start <= t < end:
                return 1.0
        return 0.0

    def __call__(self, show: Any, t: float) -> dict[str, float]:
        if not self.beats:
            return {
                key: 0.0
                for key in (
                    "pulse", "kick", "snare", "hat", "energy", "harmonic",
                    "percussive", "prominence", "bass", "mid", "high",
                    "sectionProgress", "beatsToDrop", "inDrop",
                )
            }
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
        if self.energy.size:
            energy = self._at(self.energy, index)
        else:
            energy = float(np.clip(0.30 + 0.40 * harm + 0.30 * perc, 0.0, 1.0))
        if self.bass.size:
            bass = self._at(self.bass, index)
        else:
            bass = _clip(0.15 + 0.85 * kick)
        next_drop = next((drop for drop in self.drops if drop >= float(t)), None)
        beats_to_drop = (
            1.0
            if next_drop is None
            else _clip((next_drop - float(t)) / (period * 16.0))
        )
        return {
            "pulse": _clip(pulse),
            "kick": _clip(kick),
            "snare": _clip(snare),
            "hat": _clip(hat),
            "energy": energy,
            "harmonic": _clip(harm),
            "percussive": _clip(perc),
            "prominence": _clip(prom),
            "bass": bass,
            "mid": _clip(0.20 + 0.80 * snare),
            "high": _clip(hat),
            "sectionProgress": self._section_progress(float(t)),
            "beatsToDrop": beats_to_drop,
            "inDrop": self._in_drop(float(t)),
        }


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _float_list(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)):
        return []
    out: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            out.append(round(max(0.0, number), 4))
    return sorted(out)


def _normalize_label(label: Any) -> str:
    text = str(label or "verse").strip().lower().replace(" ", "_")
    text = SECTION_ALIASES.get(text, text)
    return text if text in SECTION_LABELS else "verse"


def _normalize_sections(payload: Any, duration: float) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return sections
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        try:
            start = max(0.0, float(raw.get("start", raw.get("start_s", 0.0))))
            end = float(raw.get("end", raw.get("end_s", duration)))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            continue
        sections.append(
            {
                "label": _normalize_label(raw.get("label") or raw.get("name")),
                "start": round(min(start, duration), 4),
                "end": round(min(end, duration), 4),
            }
        )
    sections.sort(key=lambda item: item["start"])
    return sections


def _beat_curves(
    wav_bytes: bytes, samples: np.ndarray, rate: int, beats: list[float]
) -> dict[str, list[float]]:
    """Derive compact per-beat curves from the audio when an ML server omits them."""
    if not beats:
        return {
            "harmonic": [],
            "percussive": [],
            "prominence": [],
            "energy": [],
            "bass": [],
        }
    mono = np.asarray(samples, dtype=np.float32)
    duration = max(1e-6, mono.size / float(max(1, rate)))
    harmonic: list[float] = []
    percussive: list[float] = []
    prominence: list[float] = []
    energy: list[float] = []
    bass: list[float] = []
    low_window = max(1, rate // 240)
    low_kernel = np.ones(low_window, dtype=np.float32) / low_window
    for index, beat in enumerate(beats):
        end = beats[index + 1] if index + 1 < len(beats) else min(duration, beat + 0.5)
        a = int(max(0.0, beat) * rate)
        b = int(max(beat + 1e-3, end) * rate)
        chunk = mono[a:b]
        rms = float(np.sqrt(np.mean(chunk**2))) if chunk.size else 0.0
        peak = float(np.max(np.abs(chunk))) if chunk.size else 0.0
        low = np.convolve(chunk, low_kernel, mode="same") if chunk.size else chunk
        low_rms = float(np.sqrt(np.mean(low**2))) if low.size else 0.0
        harmonic.append(round(min(1.0, rms * 2.4), 4))
        percussive.append(round(min(1.0, peak), 4))
        prominence.append(round(min(1.0, max(rms, peak * 0.5) * 2.0), 4))
        energy.append(round(min(1.0, rms * 2.8), 4))
        bass.append(round(min(1.0, low_rms * 3.2), 4))
    return {
        "harmonic": harmonic,
        "percussive": percussive,
        "prominence": prominence,
        "energy": energy,
        "bass": bass,
    }


class HttpAnalyzer:
    """Small deterministic client for an allin1 analyzer container."""

    def __init__(self, url: str, *, timeout_s: float = ANALYZER_TIMEOUT_S) -> None:
        self.url = str(url).rstrip("/")
        self.timeout_s = float(timeout_s)

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def analyze(self, wav_bytes: bytes, *, source: str = "upload") -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.url}/analyze",
            data=wav_bytes,
            method="POST",
            headers={
                "Content-Type": "audio/wav",
                "X-Hueme-Source": str(source),
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            text = response.read().decode("utf-8")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("analyzer returned a non-object payload")
        return payload


class BlueprintStore:
    """Content-addressed blueprint cache plus a show -> key index."""

    def __init__(
        self,
        cache_dir: str | Path,
        *,
        analyzer_url: str | None = None,
        analyzer_timeout_s: float = ANALYZER_TIMEOUT_S,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.index_path = self.cache_dir / "index.json"
        self._lock = threading.Lock()
        self._analyzer = ShowAnalyzer(cache_dir=str(self.cache_dir / "tracks"))
        url = analyzer_url if analyzer_url is not None else os.environ.get("ANALYZER_URL", "")
        self.http_analyzer = HttpAnalyzer(url, timeout_s=analyzer_timeout_s) if url else None
        self.last_analyzer_error: str | None = None

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
        wav_bytes = convert_to_wav(wav_bytes, source)
        key = hashlib.sha1(wav_bytes).hexdigest()
        with self._lock:
            cached = self._load_key(key)
            if cached is None:
                samples, rate = decode_wav(wav_bytes)
                cached = self._analyze(samples, rate, key, source, wav_bytes)
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
        self,
        samples: np.ndarray,
        sample_rate: int,
        key: str,
        source: str,
        wav_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        duration = round(float(samples.size / max(1, sample_rate)), 4)
        if self.http_analyzer is not None and wav_bytes:
            try:
                return self._analyze_http(
                    samples, sample_rate, key, source, wav_bytes, duration
                )
            except Exception as exc:
                # A missing ML container must never take the Studio offline.
                self.last_analyzer_error = f"{type(exc).__name__}: {exc}"
        track = self._analyzer.analyze_audio(
            samples, sample_rate, track_key=key, cache=False
        )
        beats = [round(float(v), 4) for v in track.beats]
        curves = {
            "harmonic": [round(float(v), 4) for v in track.beat_harmonic],
            "percussive": [round(float(v), 4) for v in track.beat_percussive],
            "prominence": [round(float(v), 4) for v in track.beat_prominence],
            "energy": [round(float(v), 4) for v in track.beat_energy],
            "bass": [round(float(v), 4) for v in track.beat_bass],
        }
        return {
            "schema_version": CACHE_VERSION,
            "key": key,
            "source": source,
            "analyzer": "numpy",
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

    def _analyze_http(
        self,
        samples: np.ndarray,
        sample_rate: int,
        key: str,
        source: str,
        wav_bytes: bytes,
        local_duration: float,
    ) -> dict[str, Any]:
        assert self.http_analyzer is not None
        payload = self.http_analyzer.analyze(wav_bytes, source=source)
        bpm = float(payload.get("bpm") or payload.get("tempo") or 120.0)
        if not math.isfinite(bpm) or bpm <= 0.0:
            bpm = 120.0
        beats = _float_list(payload.get("beats") or payload.get("beat_times"))
        duration = float(payload.get("duration_s") or payload.get("duration") or local_duration)
        duration = local_duration if not math.isfinite(duration) or duration <= 0 else duration
        beats = [value for value in beats if value <= duration + 1e-3]
        downbeats = _float_list(payload.get("downbeats") or payload.get("downbeat_times"))
        sections = _normalize_sections(
            payload.get("sections") or payload.get("segments"), duration
        )
        drops = _float_list(payload.get("drops"))
        if not drops:
            drops = [
                float(section["start"])
                for section in sections
                if section["label"] == "drop"
            ]
        drop_windows = [
            {"start": window["start"], "end": window["end"]}
            for window in _normalize_sections(payload.get("drop_windows"), duration)
        ]
        if not drop_windows:
            drop_windows = [
                {"start": float(section["start"]), "end": float(section["end"])}
                for section in sections
                if section["label"] == "drop"
            ]
        pre_drops = []
        for window in _normalize_sections(payload.get("pre_drops"), duration):
            pre_drops.append(
                {
                    "start": window["start"],
                    "end": window["end"],
                    "impact": window["end"],
                }
            )
        raw_curves = payload.get("curves") or payload.get("stems") or {}
        if not isinstance(raw_curves, dict):
            raw_curves = {}
        curve_names = ("harmonic", "percussive", "prominence", "energy", "bass")
        curves = {}
        for name in curve_names:
            values = raw_curves.get(name) or raw_curves.get(f"{name}_curve") or []
            curves[name] = _float_list(values)
        if any(len(curves[name]) < len(beats) for name in curves) or not beats:
            derived = _beat_curves(wav_bytes, samples, sample_rate, beats)
            for name in curve_names:
                if len(curves[name]) < len(beats):
                    curves[name] = derived[name]
        return {
            "schema_version": CACHE_VERSION,
            "key": key,
            "source": source,
            "analyzer": "allin1",
            "model": str(payload.get("model") or "allin1"),
            "duration_s": round(float(duration), 4),
            "sample_rate": int(sample_rate),
            "bpm": round(bpm, 3),
            "beat_grid_s": round(60.0 / max(bpm, 1e-6), 6),
            "beats": beats,
            "downbeats": downbeats,
            "sections": sections,
            "drops": drops,
            "drop_windows": drop_windows,
            "pre_drops": pre_drops,
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
            "analyzer": blueprint.get("analyzer"),
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
    "HttpAnalyzer",
    "WAVEFORM_BUCKETS",
    "convert_to_wav",
    "decode_wav",
]
