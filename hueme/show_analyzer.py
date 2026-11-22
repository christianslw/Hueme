"""Offline ShowGraph compiler: beats, downbeats, structure, and pre-drop cues.

Pure numpy + stdlib.  The result is a compact, cacheable ``TrackMap`` that the
live renderer can consume at zero analysis cost once a track has been seen.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import os
import tempfile
import wave

import numpy as np

CACHE_VERSION = 3

_SECTION_MOOD = {
    "intro": "calm",
    "outro": "calm",
    "build": "rising",
    "drop": "peak",
    "chorus": "euphoric",
    "breakdown": "ambient",
    "pre_drop": "tension",
    "verse": "groove",
}


# ---------------------------------------------------------------------------
# Small DSP helpers (no scipy/librosa)
# ---------------------------------------------------------------------------
def _read_wav(path):
    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if width != 2:
        raise ValueError(f"Only 16-bit PCM WAV is supported (got {width * 8}-bit)")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1 and len(data) >= channels:
        data = data[: len(data) // channels * channels].reshape(-1, channels).mean(axis=1)
    return data, int(rate)


def _stft_mag(y, n_fft=2048, hop=512):
    """Magnitude STFT computed in bounded chunks (memory-safe for full tracks)."""
    y = np.asarray(y, dtype=np.float32)
    if y.size < n_fft:
        y = np.pad(y, (0, n_fft - y.size))
    n_frames = 1 + (len(y) - n_fft) // hop
    window = np.hanning(n_fft).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(y, n_fft)[::hop][:n_frames]
    mag = np.empty((n_fft // 2 + 1, n_frames), dtype=np.float32)
    for start in range(0, n_frames, 256):
        end = min(n_frames, start + 256)
        block = frames[start:end] * window
        mag[:, start:end] = np.abs(np.fft.rfft(block, axis=1)).T.astype(np.float32)
    return mag


def _mel_filterbank(sr, n_fft, n_mels=40, fmin=25.0, fmax=None):
    fmax = fmax or min(8000.0, sr / 2.0)

    def hz_to_mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def mel_to_hz(m):
        return 700.0 * (10.0 ** (m / 2595.0) - 1.0)

    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    mel_edges = np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels + 2)
    hz_edges = mel_to_hz(mel_edges)
    weights = np.zeros((n_mels, len(freqs)), dtype=np.float32)
    for m in range(n_mels):
        lo, centre, hi = hz_edges[m], hz_edges[m + 1], hz_edges[m + 2]
        if hi <= lo:
            continue
        left = (freqs - lo) / max(centre - lo, 1e-9)
        right = (hi - freqs) / max(hi - centre, 1e-9)
        weights[m] = np.maximum(0.0, np.minimum(left, right))
    return weights


def _dct_matrix(n_out, n_in):
    rows = np.arange(n_out)[:, None]
    cols = np.arange(n_in)[None, :]
    mat = np.cos(np.pi / n_in * (cols + 0.5) * rows)
    mat[0] *= 1.0 / math.sqrt(2.0)
    return (mat * math.sqrt(2.0 / n_in)).astype(np.float32)


def _chroma_matrix(freqs, mag):
    mask = (freqs >= 55.0) & (freqs <= 4000.0)
    f = freqs[mask]
    midi = 69.0 + 12.0 * np.log2(np.maximum(f, 1e-6) / 440.0)
    pcs = np.mod(np.rint(midi).astype(np.int64), 12)
    masked = mag[mask]
    chroma = np.zeros((12, mag.shape[1]), dtype=np.float32)
    for pc in range(12):
        rows = np.flatnonzero(pcs == pc)
        if rows.size:
            chroma[pc] = np.sum(masked[rows] ** 2, axis=0)
    total = chroma.sum(axis=0, keepdims=True)
    return chroma / np.maximum(total, 1e-9)


def _estimate_tempo(onset, fps, bpm_min=60.0, bpm_max=190.0):
    x = np.asarray(onset, dtype=np.float64) - float(np.mean(onset))
    n = len(x)
    if n < 2:
        return 120.0, 0.0
    size = 1
    while size < 2 * n:
        size <<= 1
    spec = np.fft.rfft(x, n=size)
    ac = np.fft.irfft(spec * np.conj(spec))[:n]
    ac /= max(float(ac[0]), 1e-9)

    bpms = np.arange(bpm_min, bpm_max + 0.01, 0.1)
    lags = fps * 60.0 / bpms
    valid = (lags >= 1.0) & (lags < n - 1)
    scores = np.zeros_like(bpms)
    scores[valid] = np.interp(lags[valid], np.arange(n), ac)
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.9) ** 2)
    weighted = scores * prior
    best_i = int(np.argmax(weighted))
    best = float(bpms[best_i])
    best_score = float(scores[best_i])

    def score_at(tempo):
        lag = fps * 60.0 / tempo
        if lag <= 1.0 or lag >= n - 1:
            return 0.0
        return float(np.interp(lag, np.arange(n), ac))

    # Octave-error suppression: only fold when the alternative is nearly as
    # strong; the tempo prior already favours the musical 70-170 BPM band.
    if best < 70.0 and score_at(best * 2.0) > 0.78 * best_score:
        best *= 2.0
    elif best > 170.0 and score_at(best / 2.0) > 0.78 * best_score:
        best /= 2.0

    p90 = float(np.percentile(scores[valid], 90)) if np.any(valid) else 0.0
    confidence = max(0.0, min(1.0, (best_score / max(p90, 1e-9) - 1.0) / 1.5))
    return best, confidence


def _track_beats(onset, fps, tempo):
    """Ellis dynamic-programming beat tracker with a log-period transition penalty."""
    onset = np.asarray(onset, dtype=np.float64)
    n = len(onset)
    period = max(2.0, fps * 60.0 / max(tempo, 1.0))
    if n < 4:
        return np.zeros(0, dtype=np.int64)

    lo = max(1, int(round(period * 0.65)))
    hi = max(lo + 1, int(round(period * 1.55)))
    tightness = 100.0
    cum = np.zeros(n, dtype=np.float64)
    back = np.full(n, -1, dtype=np.int64)

    for i in range(n):
        j0 = max(0, i - hi)
        j1 = max(0, i - lo)
        if j1 <= j0:
            cum[i] = onset[i]
            continue
        js = np.arange(j0, j1, dtype=np.int64)
        lags = (i - js).astype(np.float64)
        candidates = cum[js] - tightness * (np.log(lags / period) ** 2)
        k = int(np.argmax(candidates))
        if candidates[k] > -np.inf:
            cum[i] = onset[i] + candidates[k]
            back[i] = int(js[k])
        else:
            cum[i] = onset[i]

    end_start = max(1, int(n - period * 1.5))
    end = int(np.argmax(cum[end_start:]) + end_start) if end_start < n else n - 1
    beats = []
    i = end
    while i >= 0:
        beats.append(i)
        i = int(back[i])
        if len(beats) > n:
            break
    beats.reverse()

    if len(beats) < 2:
        first = int(np.argmax(onset[: max(1, int(period * 2))]))
        beats = list(range(first, n, max(1, int(round(period)))))
    return np.asarray(beats, dtype=np.int64)


def _pick_downbeat_phase(beats, bass_env, chroma, onset, fps):
    if len(beats) < 4:
        return 0
    n_frames = len(onset)
    beat_idx = np.clip(beats, 0, n_frames - 1)
    bass = bass_env[beat_idx].astype(np.float64)
    bass /= max(float(np.percentile(bass, 90)), 1e-9)
    bass = np.clip(bass, 0.0, 1.0)

    novelty = np.zeros(len(beats), dtype=np.float64)
    for k in range(1, len(beats)):
        a = chroma[:, beat_idx[k - 1]]
        b = chroma[:, beat_idx[k]]
        novelty[k] = max(0.0, 1.0 - float(np.dot(a, b)) / max(float(np.linalg.norm(a) * np.linalg.norm(b)), 1e-9))
    novelty /= max(float(np.percentile(novelty, 90)), 1e-9)
    onsets = onset[beat_idx] / max(float(np.percentile(onset, 90)), 1e-9)

    best_phase, best_score = 0, -1e9
    for phase in range(4):
        idx = np.arange(phase, len(beats), 4)
        if idx.size == 0:
            continue
        score = float(np.mean(bass[idx]) + 0.75 * np.mean(novelty[idx]) + 0.25 * np.mean(onsets[idx]))
        if score > best_score:
            best_phase, best_score = phase, score
    return best_phase


def _smooth_rows(features, window=4):
    features = np.asarray(features, dtype=np.float32)
    if features.shape[0] == 0:
        return features
    window = max(1, min(int(window), features.shape[0]))
    if window == 1:
        return features
    pad = window // 2
    padded = np.pad(features, ((pad, pad), (0, 0)), mode="edge")
    kernel = np.ones(window, dtype=np.float32) / window
    out = np.empty_like(features)
    for col in range(features.shape[1]):
        out[:, col] = np.convolve(padded[:, col], kernel, mode="valid")[: features.shape[0]]
    return out


def _cosine_ssm(features):
    norms = np.linalg.norm(features, axis=1, keepdims=True)
    normalised = features / np.maximum(norms, 1e-9)
    return np.clip(1.0 - normalised @ normalised.T, 0.0, 2.0)


def _foote_novelty(ssm, kernel_bars=8):
    n = ssm.shape[0]
    k = min(int(kernel_bars), n - 1)
    if k < 2:
        return np.zeros(n, dtype=np.float64)
    if k % 2:
        k -= 1
    half = k // 2
    idx = np.arange(k) - half
    taper = np.exp(-0.5 * (idx / max(half / 1.5, 1e-9)) ** 2)
    # Gaussian-tapered checkerboard on a *distance* SSM: cross-block distance
    # positive, same-block distance negative, so a boundary peaks and a
    # homogeneous region cancels.
    kernel = -np.sign(idx[:, None] * idx[None, :]) * taper[:, None] * taper[None, :]
    padded = np.pad(ssm, half, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, (k, k))
    novelty = np.einsum("ijkl,kl->ij", windows, kernel)
    return np.maximum(0.0, np.diag(novelty))


def _hpss_energy(mag, freqs, time_width=17, freq_width=15):
    """Offline HPSS energies: time-median harmonic bed, frequency-median transients.

    Energies are accumulated chunk-by-chunk so full-length tracks never need
    two additional full-size spectrogram copies in memory.
    """
    n_bins, n_frames = mag.shape
    spec_mask = (freqs >= 50.0) & (freqs <= 12000.0)
    n_spec = max(1, int(np.count_nonzero(spec_mask)))

    tw = max(3, min(int(time_width), n_frames if n_frames % 2 else n_frames - 1))
    tw = max(3, tw - (1 - tw % 2))
    pad_t = tw // 2
    padded = np.pad(mag, ((0, 0), (pad_t, pad_t)), mode="edge")
    harm_sq = np.zeros(n_frames, dtype=np.float64)
    for b0 in range(0, n_bins, 16):
        block = padded[b0:b0 + 16]
        windows = np.lib.stride_tricks.sliding_window_view(block, tw, axis=1)
        med = np.median(windows, axis=-1)
        rows = spec_mask[b0:b0 + 16]
        if np.any(rows):
            harm_sq += np.sum(med[rows].astype(np.float64) ** 2, axis=0)

    fw = max(3, min(int(freq_width), n_bins if n_bins % 2 else n_bins - 1))
    fw = max(3, fw - (1 - fw % 2))
    pad_f = fw // 2
    padded = np.pad(mag, ((pad_f, pad_f), (0, 0)), mode="edge")
    perc_sq = np.zeros(n_frames, dtype=np.float64)
    for f0 in range(0, n_frames, 512):
        block = padded[:, f0:f0 + 512]
        windows = np.lib.stride_tricks.sliding_window_view(block, fw, axis=0)
        med = np.median(windows, axis=-1)
        take = med.shape[1]
        perc_sq[f0:f0 + take] += np.sum(med[spec_mask].astype(np.float64) ** 2, axis=0)

    return np.sqrt(harm_sq / n_spec), np.sqrt(perc_sq / n_spec)


def _pick_peaks(values, min_dist, threshold):
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return np.zeros(0, dtype=np.int64)
    local = np.flatnonzero(
        (values >= np.roll(values, 1))
        & (values >= np.roll(values, -1))
        & (values >= threshold)
    )
    local = local[(local > 0) & (local < values.size - 1)]
    if local.size == 0:
        local = np.asarray([int(np.argmax(values))])
    order = local[np.argsort(values[local])[::-1]]
    accepted = []
    for idx in order:
        if all(abs(int(idx) - a) >= min_dist for a in accepted):
            accepted.append(int(idx))
    return np.asarray(sorted(accepted), dtype=np.int64)


def _merge_short_sections(sections, bar_seconds, min_bars=8, protected=()):
    """Merge short neighbouring sections without touching structural anchors.

    Drops, builds and intros/outros are what the operator navigates by; merging
    a clear 4-bar drop into the following verse is what made the old analyzer
    call every important moment "verse".  Protected sections are never merged,
    and other sections merge only with other unprotected neighbours.
    """
    out = [dict(s) for s in sections]
    protected = set(protected)
    if bar_seconds <= 0:
        return out
    while len(out) > 1:
        lengths = [max(0.0, s["end"] - s["start"]) / bar_seconds for s in out]
        mergeable = [
            k
            for k, sec in enumerate(out)
            if sec.get("label") not in protected
            and (
                (k > 0 and out[k - 1].get("label") not in protected)
                or (k + 1 < len(out) and out[k + 1].get("label") not in protected)
            )
        ]
        if not mergeable:
            break
        i = min(mergeable, key=lambda k: lengths[k])
        if lengths[i] >= min_bars:
            break
        neighbours = [
            k
            for k in (i - 1, i + 1)
            if 0 <= k < len(out) and out[k].get("label") not in protected
        ]
        if not neighbours:
            break
        j = min(neighbours, key=lambda k: lengths[k])
        a, b = sorted((i, j))
        sa, sb = out[a], out[b]
        merged = dict(sa)
        merged["end"] = sb["end"]
        merged["label"] = (
            sa.get("label")
            if lengths[a] >= lengths[b]
            else sb.get("label")
        )
        total = lengths[a] + lengths[b]
        for key in ("energy", "onset", "bass"):
            merged[key] = (
                float(sa.get(key, 0.0)) * lengths[a] + float(sb.get(key, 0.0)) * lengths[b]
            ) / max(total, 1e-9)
        out[a] = merged
        del out[b]
    return out


def _detect_vacuum(drop_time, beat_period, onset, bass_env, fps):
    """Find the 0.5-2 beat sub-bass / transient collapse before an impact."""
    if not beat_period or beat_period <= 0:
        return None
    i0 = max(0, int((drop_time - 2.0 * beat_period) * fps))
    i1 = min(len(onset), int(drop_time * fps))
    if i1 - i0 < 4:
        return None
    ref0 = max(0, i0 - int(4.0 * beat_period * fps))
    ref_bass = bass_env[ref0:i0]
    bass_ref = max(
        float(np.percentile(ref_bass, 90)) if ref_bass.size else 0.0,
        float(np.median(bass_env)) * 1.5,
        1e-9,
    )
    onset_ref = max(float(np.mean(onset[ref0:i0])) if i0 > ref0 else 0.0, 1e-9)
    win_bass = bass_env[i0:i1]
    low = win_bass < max(bass_ref * 0.20, float(np.median(bass_env)) * 0.25)
    if not np.any(low):
        return None

    # Take the trailing low-bass run; extend back to its start.
    last = len(low) - 1
    while last >= 0 and not low[last]:
        last -= 1
    if last < 0:
        return None
    first = last
    while first > 0 and low[first - 1]:
        first -= 1
    length_beats = (last - first + 1) / max(1.0, fps * beat_period)
    if not (0.5 <= length_beats <= 2.0):
        return None
    if float(np.mean(onset[i0 + first : i0 + last + 1])) > 0.65 * onset_ref:
        return None
    start = (i0 + first) / fps
    return {"start": round(float(start), 4), "end": round(float(drop_time), 4), "impact": round(float(drop_time), 4)}


# ---------------------------------------------------------------------------
# TrackMap
# ---------------------------------------------------------------------------
@dataclass
class TrackMap:
    track_key: str
    duration: float
    tempo: float
    beats: list[float] = field(default_factory=list)
    downbeats: list[float] = field(default_factory=list)
    bar_times: list[float] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)
    drops: list[float] = field(default_factory=list)
    drop_windows: list[dict] = field(default_factory=list)
    pre_drops: list[dict] = field(default_factory=list)
    beat_harmonic: list[float] = field(default_factory=list)
    beat_percussive: list[float] = field(default_factory=list)
    beat_prominence: list[float] = field(default_factory=list)
    beat_energy: list[float] = field(default_factory=list)
    beat_bass: list[float] = field(default_factory=list)
    sample_rate: int = 48000
    version: int = CACHE_VERSION

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, raw):
        known = {f: raw.get(f) for f in cls.__dataclass_fields__ if f in raw}
        return cls(**known)

    def save(self, path):
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".show_", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, separators=(",", ":"), ensure_ascii=False)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    # -- live cue contract -------------------------------------------------
    def _beat_position(self, t):
        beats = self.beats
        if not beats:
            return 0.0
        idx = bisect_right(beats, t)
        if idx <= 0:
            return 0.0
        if idx >= len(beats):
            return float(len(beats) - 1)
        a, b = beats[idx - 1], beats[idx]
        frac = 0.0 if b <= a else (t - a) / (b - a)
        return (idx - 1) + max(0.0, min(1.0, frac))

    def _beat_value(self, values, beat_pos):
        if not values:
            return 0.0
        i = int(math.floor(beat_pos))
        i = max(0, min(len(values) - 1, i))
        j = min(len(values) - 1, i + 1)
        frac = max(0.0, min(1.0, beat_pos - math.floor(beat_pos)))
        return float(values[i] + (values[j] - values[i]) * frac)

    def cue_at(self, t):
        t = max(0.0, min(float(self.duration), float(t)))
        section = self.sections[-1] if self.sections else {
            "label": "intro", "start": 0.0, "end": self.duration
        }
        for sec in self.sections:
            if sec["start"] <= t < sec["end"]:
                section = sec
                break

        # Drop/pre-drop are events, not macro sections. They override the
        # active cue even though the stable section list stays >= 16 bars.
        for win in self.drop_windows:
            if float(win.get("start", 0.0)) <= t < float(win.get("end", 0.0)):
                section = {"label": "drop", "start": win["start"], "end": win["end"]}
                break
        else:
            for win in self.pre_drops:
                if float(win.get("start", 0.0)) <= t < float(win.get("end", 0.0)):
                    section = {"label": "pre_drop", "start": win["start"], "end": win["end"]}
                    break

        start = float(section.get("start", 0.0))
        end = float(section.get("end", self.duration))
        progress = 0.0 if end <= start else (t - start) / (end - start)
        progress = max(0.0, min(1.0, progress))

        beat_pos = self._beat_position(t)
        beat_period = 60.0 / max(self.tempo, 1.0)
        if section.get("label") == "drop":
            beats_to_drop = 0.0
        else:
            nxt = next((d for d in self.drops if d > t), None)
            beats_to_drop = 0.0 if nxt is None else max(0.0, (float(nxt) - t) / beat_period)

        return {
            "available": True,
            "section": str(section.get("label", "verse")),
            "section_progress": round(progress, 4),
            "beats_to_drop": round(beats_to_drop, 3),
            "beats_in_section": round(max(0.0, (end - start) / beat_period), 3),
            "harmonic_swell": round(max(0.0, min(1.0, self._beat_value(self.beat_harmonic, beat_pos))), 3),
            "percussive_punch": round(max(0.0, min(1.0, self._beat_value(self.beat_percussive, beat_pos))), 3),
            "prominence": round(max(0.0, min(1.0, self._beat_value(self.beat_prominence, beat_pos))), 3),
            "source": "showgraph",
        }


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------
class ShowAnalyzer:
    def __init__(self, cache_dir="config/show_cache", n_fft=2048, hop=512):
        self.cache_dir = cache_dir
        self.n_fft = int(n_fft)
        self.hop = int(hop)

    @staticmethod
    def hash_key(track_key):
        return hashlib.sha1(str(track_key).encode("utf-8")).hexdigest()

    def cache_path(self, track_key):
        return os.path.join(self.cache_dir, self.hash_key(track_key) + ".json")

    def load_cached(self, track_key):
        path = self.cache_path(track_key)
        if not os.path.exists(path):
            return None
        try:
            track_map = TrackMap.load(path)
            if int(getattr(track_map, "version", 0) or 0) < CACHE_VERSION:
                return None
            return track_map
        except (OSError, ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError):
            return None

    def save_cached(self, track_map):
        track_map.save(self.cache_path(track_map.track_key))

    def analyze_audio(
        self, samples, sample_rate=48000, track_key=None, force=False, cache=True
    ):
        key = str(track_key) if track_key else hashlib.sha1(
            np.asarray(samples, dtype=np.float32).tobytes()
        ).hexdigest()
        if cache and not force:
            cached = self.load_cached(key)
            if cached is not None:
                return cached
        track_map = self._analyze(samples, int(sample_rate), key)
        if cache:
            self.save_cached(track_map)
        return track_map

    def analyze_file(self, path, track_key=None, force=False):
        samples, rate = _read_wav(path)
        return self.analyze_audio(samples, rate, track_key=track_key or os.path.basename(path), force=force)

    def _analyze(self, samples, sample_rate, key):
        y = np.asarray(samples, dtype=np.float32)
        if y.ndim > 1:
            y = y.mean(axis=1)
        y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)
        duration = len(y) / float(sample_rate)
        if len(y) < self.n_fft:
            y = np.pad(y, (0, self.n_fft - len(y)))

        mag = _stft_mag(y, self.n_fft, self.hop)
        fps = sample_rate / float(self.hop)
        freqs = np.fft.rfftfreq(self.n_fft, 1.0 / sample_rate)
        harm_energy, perc_energy = _hpss_energy(mag, freqs)

        # Onset strength: log-mel flux, weighted to the low end for kick timing.
        mel_bank = _mel_filterbank(sample_rate, self.n_fft)
        mel = mel_bank @ mag
        log_mel = np.log1p(10.0 * mel)
        weights = np.linspace(1.4, 0.7, log_mel.shape[0], dtype=np.float32)[:, None]
        flux = np.maximum(0.0, np.diff(log_mel, axis=1)) * weights
        onset = np.concatenate([[0.0], np.sum(flux, axis=0)]).astype(np.float64)
        onset /= max(float(np.percentile(onset, 95)), 1e-9)
        onset = np.convolve(onset, np.ones(3) / 3.0, mode="same")

        frame_energy = np.sqrt(np.mean(mag.astype(np.float64) ** 2, axis=0))
        bass_mask = (freqs >= 20.0) & (freqs <= 120.0)
        if not np.any(bass_mask):
            bass_mask[int(np.argmin(np.abs(freqs - 70.0)))] = True
        bass_env = np.sqrt(np.mean(mag[bass_mask].astype(np.float64) ** 2, axis=0))
        chroma = _chroma_matrix(freqs, mag)

        mfcc = (_dct_matrix(13, mel.shape[0]) @ log_mel)[1:]  # drop loudness coefficient

        tempo, _ = _estimate_tempo(onset, fps)
        beat_frames = _track_beats(onset, fps, tempo)
        if len(beat_frames) < 2:
            beat_frames = np.arange(0, mag.shape[1], max(1, int(round(fps * 60.0 / max(tempo, 1.0)))))

        beat_times = (beat_frames / fps).astype(float)
        phase = _pick_downbeat_phase(beat_frames, bass_env, chroma, onset, fps)
        downbeat_frames = beat_frames[phase::4]
        if len(downbeat_frames) == 0:
            downbeat_frames = np.asarray([int(beat_frames[0]) if len(beat_frames) else 0], dtype=np.int64)
        downbeat_times = (downbeat_frames / fps).astype(float)
        bar_times = [round(float(t), 4) for t in downbeat_times]

        # Per-bar chroma/MFCC descriptors for the SSM.
        bar_features = []
        bar_energy = []
        bar_onset = []
        bar_bass = []
        for b in range(len(downbeat_frames)):
            a = int(downbeat_frames[b])
            z = int(downbeat_frames[b + 1]) if b + 1 < len(downbeat_frames) else mag.shape[1]
            z = max(a + 1, min(z, mag.shape[1]))
            c = np.mean(chroma[:, a:z], axis=1)
            c /= max(float(np.linalg.norm(c)), 1e-9)
            m = np.mean(mfcc[:, a:z], axis=1)
            m = (m - np.mean(mfcc, axis=1)) / np.maximum(np.std(mfcc, axis=1), 1e-6)
            bar_features.append(np.concatenate([c, 0.55 * m]).astype(np.float32))
            bar_energy.append(float(np.mean(frame_energy[a:z])))
            bar_onset.append(float(np.sum(onset[a:z])))
            bar_bass.append(float(np.mean(bass_env[a:z])))

        if not bar_features:
            bar_features = [np.array([1.0], dtype=np.float32)]
            bar_energy = [float(np.mean(frame_energy))]
            bar_onset = [float(np.sum(onset))]
            bar_bass = [float(np.mean(bass_env))]

        # Production changes (energy, transient density, sub weight) are part
        # of the structure even when chroma/MFCC stay on the same chord.
        def _z(values):
            arr = np.asarray(values, dtype=np.float64)
            return (arr - np.mean(arr)) / max(float(np.std(arr)), 1e-6)

        e_z = _z(bar_energy) * 1.4
        o_z = _z(bar_onset) * 1.0
        b_z = _z(bar_bass) * 0.8
        bar_features = [
            np.concatenate([f, [e_z[i], o_z[i], b_z[i]]]).astype(np.float32)
            for i, f in enumerate(bar_features)
        ]

        feat = _smooth_rows(np.stack(bar_features), window=4 if len(bar_features) >= 12 else 2)
        ssm = _cosine_ssm(feat)
        novelty = _foote_novelty(ssm, kernel_bars=8)
        threshold = float(np.mean(novelty) + 0.6 * np.std(novelty))
        boundaries = list(_pick_peaks(novelty, max(3, len(bar_features) // 12), threshold))

        # Production drops and section entries are visible as a sharp
        # energy/transient/sub jump even when the harmony barely changes.
        # Compare against both the previous bar and a 2-bar mean: a one-bar
        # pre-drop hit must not hide the impact that follows it.
        energy_arr = np.asarray(bar_energy, dtype=np.float64)
        onset_arr = np.asarray(bar_onset, dtype=np.float64)
        bass_arr = np.asarray(bar_bass, dtype=np.float64)
        impact_bars: set[int] = set()
        for b in range(1, max(1, len(bar_energy) - 1)):
            before_e = float(np.mean(energy_arr[max(0, b - 2):b]))
            before_o = float(np.mean(onset_arr[max(0, b - 2):b]))
            before_b = float(np.mean(bass_arr[max(0, b - 2):b]))
            last_e, last_o, last_b = (
                energy_arr[b - 1],
                onset_arr[b - 1],
                bass_arr[b - 1],
            )
            e_jump = (
                energy_arr[b] > last_e * 1.18 and energy_arr[b] > before_e * 1.05
            ) or energy_arr[b] > before_e * 1.12
            o_jump = onset_arr[b] > last_o * 1.10 and onset_arr[b] > before_o * 1.02
            b_jump = (
                bass_arr[b] > last_b * 1.12 and bass_arr[b] > before_b * 1.03
            ) or bass_arr[b] > before_b * 1.10
            if e_jump and o_jump and b_jump:
                boundaries.append(b)
                impact_bars.add(b)

        boundaries = [b for b in boundaries if 0 < b < len(bar_features)]
        boundaries = [0] + boundaries + [len(bar_features)]
        boundaries = sorted(set(boundaries))

        bar_energy = np.asarray(bar_energy, dtype=np.float64)
        bar_onset = np.asarray(bar_onset, dtype=np.float64)
        bar_bass = np.asarray(bar_bass, dtype=np.float64)
        median_energy = max(float(np.median(bar_energy)), 1e-9)
        median_onset = max(float(np.median(bar_onset)), 1e-9)
        median_bass = max(float(np.median(bar_bass)), 1e-9)
        p75_energy = float(np.percentile(bar_energy, 75)) if len(bar_energy) else median_energy

        raw = []
        for i in range(len(boundaries) - 1):
            a, z = int(boundaries[i]), int(boundaries[i + 1])
            if z <= a:
                continue
            start_t = 0.0 if a == 0 else float(bar_times[min(a, len(bar_times) - 1)])
            end_t = duration if z >= len(bar_times) else float(bar_times[z])
            raw.append({
                "start": start_t,
                "end": end_t,
                "start_bar": a,
                "energy": float(np.mean(bar_energy[a:z])),
                "onset": float(np.mean(bar_onset[a:z])),
                "bass": float(np.mean(bar_bass[a:z])),
                "label": "verse",
            })
        if not raw:
            raw = [{"start": 0.0, "end": duration, "start_bar": 0,
                    "energy": float(np.mean(bar_energy)),
                    "onset": float(np.mean(bar_onset)),
                    "bass": float(np.mean(bar_bass)), "label": "verse"}]

        beat_period = 60.0 / max(tempo, 1.0)
        bar_seconds = 4.0 * beat_period

        # Novelty sometimes fires a one-bar fragment; absorb those before
        # labelling so the fragment cannot steal a drop's true start.
        for index, section in enumerate(raw):
            section["label"] = (
                "intro" if index == 0 else "outro" if index == len(raw) - 1 else "verse"
            )
        raw = _merge_short_sections(
            raw, bar_seconds, min_bars=2, protected={"intro", "outro"}
        )

        drop_indices = set()
        for i, sec in enumerate(raw):
            if i == 0:
                continue
            if sec["end"] - sec["start"] < 1.5 * bar_seconds:
                continue
            prev = raw[i - 1]
            started_on_impact = int(sec.get("start_bar", -1)) in impact_bars
            high = sec["energy"] >= (
                max(p75_energy * 0.88, median_energy * 1.05)
                if started_on_impact
                else max(p75_energy * 0.92, median_energy * 1.08)
            )
            ratio = sec["energy"] / max(prev["energy"], 1e-9)
            impact = ratio >= 1.20 and sec["onset"] >= median_onset * 0.85
            quiet_before = prev["energy"] < median_energy * 0.88
            if high and (
                impact
                or started_on_impact
                or (quiet_before and sec["bass"] >= median_bass * 1.02)
            ):
                drop_indices.add(i)
        if not drop_indices and len(raw) >= 3:
            interior = [i for i in range(1, len(raw) - 1)]
            if interior:
                strongest = max(interior, key=lambda i: raw[i]["energy"])
                if raw[strongest]["energy"] > median_energy * 1.08:
                    drop_indices.add(strongest)

        for i, sec in enumerate(raw):
            if i in drop_indices:
                sec["label"] = "drop"
            elif i == 0:
                sec["label"] = "intro"
            elif i == len(raw) - 1:
                sec["label"] = "outro"
            elif sec["energy"] < median_energy * 0.72:
                sec["label"] = "breakdown"
            elif sec["energy"] >= p75_energy * 0.98 and sec["onset"] >= median_onset * 0.90:
                sec["label"] = "chorus"
            else:
                sec["label"] = "verse"
        for i in sorted(drop_indices):
            if i - 1 >= 0 and raw[i - 1]["label"] not in ("intro", "drop"):
                raw[i - 1]["label"] = "build"

        # Adjacent drop sections are one peak region, not two cues.
        joined: list[dict] = []
        for sec in raw:
            if joined and sec["label"] == "drop" and joined[-1]["label"] == "drop":
                head = joined[-1]
                length_head = head["end"] - head["start"]
                length_tail = sec["end"] - sec["start"]
                total = max(length_head + length_tail, 1e-9)
                for key in ("energy", "onset", "bass"):
                    head[key] = (
                        float(head.get(key, 0.0)) * length_head
                        + float(sec.get(key, 0.0)) * length_tail
                    ) / total
                head["end"] = sec["end"]
                continue
            joined.append(sec)
        raw = joined

        # Macro sections stay stable, but never at the cost of dropping a
        # structural anchor.  8 bars preserves real 8/16-bar phrases.
        raw = _merge_short_sections(
            raw,
            bar_seconds,
            min_bars=8,
            protected={"intro", "outro", "drop", "pre_drop", "build", "breakdown"},
        )
        # A one-bar start transient should not produce a one-bar "Intro" marker
        # in front of a long ambient breakdown; absorb it into that section and
        # keep the intro label.
        if (
            len(raw) > 1
            and raw[0]["label"] == "intro"
            and raw[0]["end"] - raw[0]["start"] < 2.0 * bar_seconds
            and raw[1]["label"] == "breakdown"
        ):
            head, tail = raw[0], raw[1]
            length_head = head["end"] - head["start"]
            length_tail = tail["end"] - tail["start"]
            total = max(length_head + length_tail, 1e-9)
            for key in ("energy", "onset", "bass"):
                tail[key] = (
                    float(head.get(key, 0.0)) * length_head
                    + float(tail.get(key, 0.0)) * length_tail
                ) / total
            tail["start"] = head["start"]
            tail["label"] = "intro"
            del raw[0]

        drop_indices = {i for i, sec in enumerate(raw) if sec["label"] == "drop"}
        drops = [float(raw[i]["start"]) for i in sorted(drop_indices)]
        drop_windows = [
            {"start": round(float(raw[i]["start"]), 4), "end": round(float(raw[i]["end"]), 4)}
            for i in sorted(drop_indices)
        ]

        # Pre-drop vacuum: split the last 0.5-2 beats off the section before a drop.
        pre_drops = []
        for drop_time in drops:
            vac = _detect_vacuum(drop_time, beat_period, onset, bass_env, fps)
            if not vac:
                continue
            pre_drops.append(vac)
            for i, sec in enumerate(raw):
                if abs(sec["end"] - drop_time) < 1e-3 and vac["start"] > sec["start"] + 1e-3:
                    tail = dict(sec, start=vac["start"], end=drop_time, label="pre_drop")
                    sec["end"] = vac["start"]
                    raw.insert(i + 1, tail)
                    break
        raw.sort(key=lambda s: s["start"])

        # Beat-indexed cue features.
        beat_idx = np.clip(beat_frames, 0, mag.shape[1] - 1)
        energy_win = max(3, int(round(fps)))
        energy_smooth = np.convolve(
            frame_energy, np.ones(energy_win) / energy_win, mode="same"
        )
        bass_win = max(3, int(round(fps * 0.5)))
        bass_smooth = np.convolve(bass_env, np.ones(bass_win) / bass_win, mode="same")
        energy_ref = max(
            float(np.percentile(energy_smooth[beat_idx], 95)), 1e-9
        )
        bass_ref = max(float(np.percentile(bass_smooth[beat_idx], 95)), 1e-9)
        beat_energy = np.clip(energy_smooth[beat_idx] / energy_ref, 0.0, 1.0)
        beat_bass = np.clip(bass_smooth[beat_idx] / bass_ref, 0.0, 1.0)
        smooth_harm = np.convolve(harm_energy, np.ones(31) / 31.0, mode="same")
        harmonic_norm = smooth_harm[beat_idx] / max(float(np.percentile(smooth_harm, 90)), 1e-9)
        perc_norm = (
            0.65 * perc_energy[beat_idx] / max(float(np.percentile(perc_energy, 90)), 1e-9)
            + 0.35 * onset[beat_idx] / max(float(np.percentile(onset, 95)), 1e-9)
        )
        prominence = np.zeros(len(beat_idx), dtype=np.float64)
        win = 16
        for i in range(len(beat_idx)):
            a = max(0, i - win // 2)
            z = min(len(beat_idx), i + win // 2 + 1)
            peak = max(float(np.max(frame_energy[beat_idx[a:z]])), 1e-9)
            prominence[i] = float(frame_energy[beat_idx[i]]) / peak

        energy_scale = max(float(np.percentile(bar_energy, 95)), 1e-9)
        bass_scale = max(float(np.percentile(bar_bass, 95)), 1e-9)
        onset_scale = max(float(np.percentile(bar_onset, 95)), 1e-9)
        sections = []
        for sec in raw:
            start_beat = float(np.searchsorted(beat_times, sec["start"]))
            end_beat = float(np.searchsorted(beat_times, sec["end"]))
            label = str(sec["label"])
            sections.append({
                "label": label,
                "start": round(float(sec["start"]), 4),
                "end": round(float(sec["end"]), 4),
                "start_beat": round(start_beat, 3),
                "end_beat": round(max(start_beat, end_beat), 3),
                "beats_in_section": round(max(0.0, (sec["end"] - sec["start"]) / beat_period), 3),
                "energy": round(min(1.0, float(sec["energy"]) / energy_scale), 4),
                "bass": round(min(1.0, float(sec["bass"]) / bass_scale), 4),
                "onset": round(min(1.0, float(sec["onset"]) / onset_scale), 4),
                "mood": _SECTION_MOOD.get(label, "groove"),
            })

        return TrackMap(
            track_key=key,
            duration=round(float(duration), 4),
            tempo=round(float(tempo), 3),
            beats=[round(float(t), 4) for t in beat_times],
            downbeats=[round(float(t), 4) for t in downbeat_times],
            bar_times=bar_times,
            sections=sections,
            drops=[round(float(t), 4) for t in drops],
            drop_windows=drop_windows,
            pre_drops=pre_drops,
            beat_harmonic=[round(float(v), 4) for v in np.clip(harmonic_norm, 0.0, 1.0)],
            beat_percussive=[round(float(v), 4) for v in np.clip(perc_norm, 0.0, 1.0)],
            beat_prominence=[round(float(v), 4) for v in np.clip(prominence, 0.0, 1.0)],
            beat_energy=[round(float(v), 4) for v in beat_energy],
            beat_bass=[round(float(v), 4) for v in beat_bass],
            sample_rate=int(sample_rate),
        )


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compile a Hueme ShowGraph TrackMap from a 16-bit WAV")
    ap.add_argument("wav", help="Path to a 16-bit PCM WAV")
    ap.add_argument("--key", default=None, help="Stable track URI/hash used as the cache key")
    ap.add_argument("--cache-dir", default="config/show_cache")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    analyzer = ShowAnalyzer(cache_dir=args.cache_dir)
    track_map = analyzer.analyze_file(args.wav, track_key=args.key, force=args.force)
    print(json.dumps({
        "key": track_map.track_key,
        "tempo": track_map.tempo,
        "duration": track_map.duration,
        "beats": len(track_map.beats),
        "sections": track_map.sections,
        "drops": track_map.drops,
        "pre_drops": track_map.pre_drops,
    }, indent=2))


if __name__ == "__main__":
    main()
