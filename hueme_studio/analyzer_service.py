"""Optional allin1 analyzer sidecar (``Dockerfile.analyzer``).

The Studio itself never imports torch.  This tiny HTTP service runs in its own
container and exposes ``POST /analyze`` with a 16-bit PCM WAV body.  It returns
beats, downbeats, structure labels, drops and per-beat harmonic/percussive
curves.  If allin1 is not installed the service still answers with a local
librosa analysis, so the Studio always has a usable backend during development.

Run locally::

    uvicorn hueme_studio.analyzer_service:app --host 0.0.0.0 --port 8700
"""

from __future__ import annotations

import asyncio
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

app = FastAPI(title="Hueme allin1 analyzer", version="1.0")

LABEL_MAP = {
    "breakdown": "break",
    "pre-drop": "pre_drop",
    "predrop": "pre_drop",
    "build_up": "build",
    "buildup": "build",
    "inst": "verse",
    "instrumental": "verse",
}


def _label(value: Any) -> str:
    text = str(value or "verse").strip().lower().replace(" ", "_")
    return LABEL_MAP.get(text, text)


def _number_list(value: Any) -> list[float]:
    if value is None:
        return []
    try:
        return [round(float(item), 4) for item in value]
    except (TypeError, ValueError):
        return []


def _segment_dict(segment: Any) -> dict[str, Any] | None:
    if isinstance(segment, dict):
        start = segment.get("start")
        end = segment.get("end")
        label = segment.get("label")
    else:
        start = getattr(segment, "start", None)
        end = getattr(segment, "end", None)
        label = getattr(segment, "label", None)
    try:
        start_f = float(start)
        end_f = float(end)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(start_f) or not math.isfinite(end_f) or end_f <= start_f:
        return None
    return {"start": round(start_f, 4), "end": round(end_f, 4), "label": _label(label)}


def _librosa_curves(path: str, beats: list[float]) -> dict[str, list[float]]:
    """Per-beat harmonic/percussive RMS using librosa HPSS."""
    try:
        import librosa
        import numpy as np
    except Exception:
        return {"harmonic": [], "percussive": [], "prominence": []}
    try:
        y, sr = librosa.load(path, sr=None, mono=True)
        harmonic, percussive = librosa.effects.hpss(y)
    except Exception:
        return {"harmonic": [], "percussive": [], "prominence": []}
    duration = max(1e-6, len(y) / float(sr))
    curves: dict[str, list[float]] = {
        "harmonic": [],
        "percussive": [],
        "prominence": [],
    }
    for index, beat in enumerate(beats):
        end = beats[index + 1] if index + 1 < len(beats) else min(duration, beat + 0.5)
        a = int(max(0.0, beat) * sr)
        b = int(max(beat + 1e-3, end) * sr)
        h = harmonic[a:b]
        p = percussive[a:b]
        h_rms = float(np.sqrt(np.mean(h**2))) if h.size else 0.0
        p_rms = float(np.sqrt(np.mean(p**2))) if p.size else 0.0
        peak = float(np.max(np.abs(p))) if p.size else 0.0
        scale = max(h_rms, p_rms, peak, 1e-9)
        curves["harmonic"].append(round(min(1.0, h_rms / scale), 4))
        curves["percussive"].append(round(min(1.0, p_rms / scale), 4))
        curves["prominence"].append(round(min(1.0, peak / scale), 4))
    return curves


def _analyze_file(path: str) -> dict[str, Any]:
    try:
        import allin1
    except Exception:
        allin1 = None
    result = None
    if allin1 is not None:
        analyzer = getattr(allin1, "analyze", None)
        if analyzer is not None:
            result = analyzer(path)
        else:
            cls = getattr(allin1, "Analyzer", None)
            if cls is not None:
                result = cls().analyze(path)
    if result is None:
        # Development fallback: keep the endpoint useful without torch.
        import soundfile as sf

        y, sr = sf.read(path, always_2d=False)
        duration = len(y) / float(sr)
        bpm = 120.0
        beats = [round(t, 4) for t in _range_steps(duration, 60.0 / bpm)]
        return {
            "model": "librosa-fallback",
            "duration_s": round(duration, 4),
            "bpm": bpm,
            "beats": beats,
            "downbeats": beats[::4],
            "sections": [{"start": 0.0, "end": duration, "label": "verse"}],
            "drops": [],
            "drop_windows": [],
            "pre_drops": [],
            "curves": _librosa_curves(path, beats),
        }
    beats = _number_list(getattr(result, "beats", None))
    downbeats = _number_list(getattr(result, "downbeats", None))
    bpm = float(getattr(result, "bpm", 0.0) or 0.0)
    if not math.isfinite(bpm) or bpm <= 0.0:
        bpm = 120.0
    segments = getattr(result, "segments", None) or []
    sections = [
        section
        for section in (_segment_dict(segment) for segment in segments)
        if section is not None
    ]
    duration = float(getattr(result, "duration", 0.0) or 0.0)
    if duration <= 0.0 and sections:
        duration = float(sections[-1]["end"])
    if duration <= 0.0:
        try:
            import soundfile as sf

            info = sf.info(path)
            duration = float(info.frames) / float(info.samplerate)
        except Exception:
            duration = max(beats[-1] + 0.5 if beats else 0.0, 1.0)
    drops = [
        float(section["start"])
        for section in sections
        if section["label"] == "drop"
    ]
    drop_windows = [
        {"start": section["start"], "end": section["end"]}
        for section in sections
        if section["label"] == "drop"
    ]
    return {
        "model": "allin1",
        "duration_s": round(duration, 4),
        "bpm": round(bpm, 3),
        "beats": [beat for beat in beats if 0.0 <= beat <= duration + 1e-3],
        "downbeats": [beat for beat in downbeats if 0.0 <= beat <= duration + 1e-3],
        "sections": sections,
        "drops": drops,
        "drop_windows": drop_windows,
        "pre_drops": [],
        "curves": _librosa_curves(path, beats),
    }


def _range_steps(duration: float, step: float) -> list[float]:
    out = []
    t = 0.0
    while t <= duration + 1e-6:
        out.append(t)
        t += step
    return out


@app.post("/analyze")
async def analyze(request: Request) -> dict[str, Any]:
    payload = await request.body()
    if len(payload) < 44:
        raise HTTPException(status_code=400, detail="empty or invalid WAV payload")
    with tempfile.TemporaryDirectory(prefix="allin1-") as tmp:
        path = Path(tmp) / "track.wav"
        path.write_bytes(payload)
        try:
            return await asyncio.to_thread(_analyze_file, str(path))
        except Exception as exc:  # keep the JSON error readable for the Studio
            raise HTTPException(
                status_code=500, detail=f"analysis failed: {type(exc).__name__}: {exc}"
            ) from exc


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "hueme-allin1", "pid": os.getpid()}
