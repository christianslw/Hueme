"""Audio ingestion (Snapcast TCP, PulseAudio/PipeWire parec, WAV, Synthetic) and FFT DSP pipeline."""

from collections import deque
import json
import logging
import math
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import wave

import numpy as np

from hueme.sendspin_source import SendspinAudioSource
from hueme.show_analyzer import ShowAnalyzer

log = logging.getLogger("hueme.audio")

BANDS = {
    "bass": (20.0, 120.0),
    "snare": (150.0, 900.0),
    "mids": (400.0, 2800.0),
    "highs": (3000.0, 12000.0),
}

# Analysed audio is rendered this far ahead of the audible onset. Sendspin
# buffers future PCM anyway, so this is anticipation, not added latency. The
# user's Sync trim (audio.latency_ms) still shifts the final alignment.
LOOKAHEAD_S = 0.060


def pcm_s16le_to_mono_float32(data: bytes, channels: int = 2) -> np.ndarray:
    """Convert raw s16le bytes into mono float32 array in [-1.0, 1.0]."""
    n_shorts = len(data) // 2
    if n_shorts <= 0:
        return np.zeros(0, dtype=np.float32)
    arr = np.frombuffer(data[: n_shorts * 2], dtype="<i2").astype(np.float32) / 32768.0
    if channels >= 2 and len(arr) >= channels:
        frames = len(arr) // channels
        arr = arr[: frames * channels].reshape(frames, channels).mean(axis=1)
    return arr


class SyntheticSource:
    """128-BPM electronic drum + bass generator for zero-hardware testing on Windows/CI."""

    # ponytail: no Windows WASAPI loopback dep; upgrade to sounddevice if desktop audio capture needed
    def __init__(self, rate=48000, block=512, bpm=128.0, realtime=True):
        self.rate = int(rate)
        self.block = int(block)
        self.bpm = float(bpm)
        self.realtime = bool(realtime)
        self.sample_pos = 0
        self.next_t = time.monotonic()
        self.connected = True
        self.last_error = None

    def read(self) -> np.ndarray:
        if self.realtime:
            self.next_t += self.block / self.rate
            delay = self.next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.2:
                self.next_t = time.monotonic()

        idx = np.arange(self.sample_pos, self.sample_pos + self.block, dtype=np.float64)
        self.sample_pos += self.block
        t = idx / self.rate

        beat_period = 60.0 / self.bpm
        beat_phase = np.mod(t, beat_period) / beat_period
        beat_idx = np.floor(t / beat_period).astype(np.int64)

        # Kick on every beat: fast pitch drop 110->52 Hz with tight exponential decay
        kick_env = np.exp(-beat_phase * 14.0)
        kick_freq = 52.0 + 58.0 * np.exp(-beat_phase * 35.0)
        kick = 0.65 * kick_env * np.sin(2.0 * np.pi * kick_freq * (beat_phase * beat_period))

        # Snare on odd beats (beats 1, 3 in 0-indexed 4/4): 280 Hz body + band-limited burst
        is_snare_beat = (beat_idx % 2) == 1
        snare_env = np.where(is_snare_beat, np.exp(-beat_phase * 18.0), 0.0)
        rng = np.sin(idx * 12.9898) * 43758.5453
        noise = (rng - np.floor(rng)) * 2.0 - 1.0
        snare_tone = np.sin(2.0 * np.pi * 280.0 * t) + 0.7 * np.sin(2.0 * np.pi * 520.0 * t)
        snare = 0.40 * snare_env * (0.55 * snare_tone + 0.45 * noise)

        # Mid synth off-beat + High hat 8th notes
        eighth_phase = np.mod(t, beat_period * 0.5) / (beat_period * 0.5)
        hat_env = np.exp(-eighth_phase * 28.0)
        hat = 0.12 * hat_env * np.sin(2.0 * np.pi * 6400.0 * t)
        mid_env = 0.5 * (1.0 - np.cos(2.0 * np.pi * beat_phase))
        mids = 0.14 * mid_env * np.sin(2.0 * np.pi * 880.0 * t)

        # Slow amplitude modulation to exercise AGC
        mod = 0.75 + 0.25 * np.sin(2.0 * np.pi * 0.05 * t)
        sig = (kick + snare + mids + hat) * mod
        return np.clip(sig, -1.0, 1.0).astype(np.float32)

    def close(self):
        self.connected = False


class WavSource:
    """Looping WAV reader using stdlib wave."""

    def __init__(self, path, rate=48000, block=512, realtime=True):
        self.path = path
        self.rate = int(rate)
        self.block = int(block)
        self.realtime = bool(realtime)
        self.wf = None
        self.channels = 2
        self.connected = False
        self.last_error = None
        self.next_t = time.monotonic()
        # Lets `python -m hueme.show_analyzer track.wav` (default key =
        # basename) match a WAV source without Sendspin metadata.
        self.track_key = os.path.basename(str(path)) if path else None
        self._open()

    def _open(self):
        if self.wf:
            try:
                self.wf.close()
            except Exception:
                pass
        if not os.path.exists(self.path):
            self.connected = False
            self.last_error = f"WAV file not found: {self.path}"
            return
        self.wf = wave.open(self.path, "rb")
        self.channels = self.wf.getnchannels()
        self.rate = self.wf.getframerate()
        self.track_key = os.path.basename(str(self.path))
        self.connected = True
        self.last_error = None

    @property
    def track_progress_s(self):
        if not self.wf:
            return 0.0
        try:
            return float(self.wf.tell()) / max(1, self.rate)
        except Exception:
            return 0.0

    def read(self) -> np.ndarray:
        if not self.wf:
            self._open()
            if not self.wf:
                time.sleep(0.1)
                return np.zeros(self.block, dtype=np.float32)
        if self.realtime:
            self.next_t += self.block / self.rate
            delay = self.next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.2:
                self.next_t = time.monotonic()
        raw = self.wf.readframes(self.block)
        if len(raw) < self.block * 2 * self.channels:
            self.wf.rewind()
            raw += self.wf.readframes(self.block)
        return pcm_s16le_to_mono_float32(raw, self.channels)[: self.block]

    def close(self):
        self.connected = False
        if self.wf:
            try:
                self.wf.close()
            except Exception:
                pass
            self.wf = None


class SnapcastSource:
    """Snapcast binary-protocol client (stream socket, Music Assistant on tcp/1704).

    Speaks Hello, takes the CodecHeader + WireChunk messages.
    The server must stream codec 'pcm' (Music Assistant's embedded snapserver does:
    pipe:///tmp/snapfifo?codec=pcm&sampleformat=48000:16:2).
    """

    def __init__(self, host="127.0.0.1", port=1704, rate=48000, channels=2, block=512, stop_event=None):
        self.host = host
        self.port = int(port)
        self.rate = int(rate)
        self.channels = int(channels)
        self.block = int(block)
        self.bytes_needed = self.block * self.channels * 2
        self.stop_event = stop_event or threading.Event()
        self.connected = False
        self.last_error = None
        self.backoff = 1.0
        self.latency = 0.0
        self._lat_samples = deque(maxlen=11)
        self._client_id = "hueme-" + socket.gethostname().lower().replace(" ", "")[:12]
        self._sock = None
        self._buf = b""
        self._last_time_sent = 0.0

    def _pack_base(self, mtype, size):
        now = time.time()
        return struct.pack("<HHHiiiiI", mtype, 1, 0, int(now), 0, 0, 0, size)

    def _connect(self):
        self._close_sock()
        self._lat_samples.clear()
        self.latency = 0.0
        try:
            s = socket.create_connection((self.host, self.port), timeout=2.5)
            s.settimeout(2.0)
            hello = json.dumps({
                "Arch": "x86_64",
                "ClientName": "Hueme Visualizer",
                "HostName": socket.gethostname()[:32],
                "ID": self._client_id,
                "Instance": 1,
                "MAC": self._client_id,
                "OS": sys.platform,
                "SnapStreamProtocolVersion": 2,
                "Version": "1.0",
            }).encode()
            payload = struct.pack("<I", len(hello)) + hello
            s.sendall(self._pack_base(5, len(payload)) + payload)  # Hello
            self._sock = s
            self.connected = True
            self.last_error = None
            log.info("Snapcast connected -> %s:%d (client id %s)", self.host, self.port, self._client_id)
        except OSError as e:
            self.connected = False
            self.last_error = f"connect {self.host}:{self.port} — {e}"
            self.stop_event.wait(self.backoff)
            self.backoff = min(8.0, self.backoff * 1.5)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            c = self._sock.recv(n - len(buf))
            if not c:
                raise ConnectionError("snapserver closed the stream socket")
            buf += c
        return buf

    def _read_message(self):
        hdr = self._recv_exact(26)
        mtype = struct.unpack("<H", hdr[:2])[0]
        size = struct.unpack("<I", hdr[22:26])[0]
        return mtype, self._recv_exact(size)

    def _handle_message(self, mtype, body) -> bool:
        """Return True for a WireChunk that was ingested into the PCM buffer."""
        if mtype == 2:  # WireChunk: timestamp.sec i32 + timestamp.usec i32 + size u32 + PCM
            if len(body) >= 12:
                sec, usec = struct.unpack("<ii", body[:8])
                # Snapcast schedules chunks with a server timestamp; measuring
                # it lets the renderer delay visuals to match the speakers.
                if sec > 1_000_000_000:
                    latency = sec + usec / 1e6 - time.time()
                    if -0.1 <= latency <= 3.0:
                        self._lat_samples.append(latency)
                        ordered = sorted(self._lat_samples)
                        self.latency = ordered[len(ordered) // 2]
            self._buf += body[12:]
            return True
        if mtype == 1:  # CodecHeader: u32 codec len + codec + u32 payload len + WAV header
            clen = struct.unpack("<I", body[:4])[0]
            codec = body[4:4 + clen].decode(errors="replace")
            if codec != "pcm":
                self.last_error = f"snapcast stream codec is '{codec}', only 'pcm' is supported"
                log.warning("%s", self.last_error)
                raise ConnectionError(self.last_error)
            self.backoff = 1.0  # valid handshake: reconnects can be fast again
            wav = body[4 + clen + 4:]
            if len(wav) >= 36 and wav[:4] in (b"RIFF", b"RIFX"):
                fmt_ch, fmt_rate = struct.unpack("<HI", wav[22:28])
                fmt_bits = struct.unpack("<H", wav[34:36])[0]
                log.info("Snapcast stream format: %d Hz, %d ch, %d bit", fmt_rate, fmt_ch, fmt_bits)
                if fmt_rate != self.rate or fmt_ch != self.channels:
                    # ponytail: DSP stays on configured rate for now; mismatch only affects Hz-band edges
                    log.warning(
                        "Snapcast format differs from configured audio settings (%d Hz, %d ch) — "
                        "align audio.rate/channels with the stream for exact band edges",
                        self.rate, self.channels,
                    )
            return False
        # 3 ServerSettings (volume/latency), 4 Time (responses keepalive), 5, 7, 8: ignore
        return False

    def _send_time(self):
        """Periodic Time keepalive: Music Assistant marks players unavailable when
        the snapserver's lastSeen for our client freezes >15 s (no TCP keepalive)."""
        if self._sock and time.monotonic() - self._last_time_sent >= 2.0:
            try:
                self._sock.sendall(self._pack_base(4, 8) + struct.pack("<ii", 0, 0))
                self._last_time_sent = time.monotonic()
            except OSError:
                pass  # next recv will fail and trigger the reconnect path

    def read(self) -> np.ndarray:
        need = self.bytes_needed
        while not self.stop_event.is_set():
            if not self._sock:
                self._connect()
                if not self._sock:
                    return np.zeros(0, dtype=np.float32)
            try:
                while len(self._buf) < need:
                    mtype, body = self._read_message()
                    self._handle_message(mtype, body)
                    self._send_time()
                raw, self._buf = self._buf[:need], self._buf[need:]
                return pcm_s16le_to_mono_float32(raw, self.channels)
            except socket.timeout:
                # idle stream: send keepalive, report silence, keep the connection
                self._send_time()
                return np.zeros(0, dtype=np.float32)
            except OSError as e:
                self.last_error = str(e)
                self._close_sock()
                self.connected = False
                # ponytail: escalate on post-handshake errors (wrong host/codec) too,
                # else a flac server gets hammered with a reconnect every second
                self.backoff = min(8.0, self.backoff * 2)
                self.stop_event.wait(self.backoff)
        return np.zeros(0, dtype=np.float32)

    def _close_sock(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def close(self):
        self.connected = False
        self._close_sock()


class ParecSource:
    """Native PulseAudio/PipeWire monitor capture via parec with non-blocking backoff."""

    def __init__(self, device="LedFx_Audio.monitor", rate=48000, channels=2, block=512, stop_event=None):
        self.device = device or "LedFx_Audio.monitor"
        self.rate = int(rate)
        self.channels = int(channels)
        self.block = int(block)
        self.bytes_needed = self.block * self.channels * 2
        self.stop_event = stop_event or threading.Event()
        self.proc = None
        self.buf = b""
        self.connected = False
        self.last_error = None
        self.backoff = 1.0

    def _spawn(self):
        self.close()
        try:
            cmd = [
                "parec",
                f"--device={self.device}",
                "--format=s16le",
                f"--rate={self.rate}",
                f"--channels={self.channels}",
                "--latency-msec=20",
                "--raw",
            ]
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self.bytes_needed * 4,
            )
            self.connected = True
            self.last_error = None
            self.backoff = 1.0
            log.info("parec started -> %s", self.device)
        except Exception as e:
            self.connected = False
            self.last_error = str(e)
            self.stop_event.wait(self.backoff)
            self.backoff = min(8.0, self.backoff * 1.5)

    def read(self) -> np.ndarray:
        while not self.stop_event.is_set():
            if not self.proc:
                self._spawn()
                if not self.proc:
                    return np.zeros(0, dtype=np.float32)
            try:
                while len(self.buf) < self.bytes_needed:
                    chunk = self.proc.stdout.read(self.bytes_needed - len(self.buf))
                    if not chunk:
                        raise EOFError("parec stdout closed")
                    self.buf += chunk
                raw = self.buf[: self.bytes_needed]
                self.buf = self.buf[self.bytes_needed :]
                return pcm_s16le_to_mono_float32(raw, self.channels)
            except Exception as e:
                self.last_error = str(e)
                self.close()
                self.stop_event.wait(min(1.0, self.backoff))
        return np.zeros(0, dtype=np.float32)

    def close(self):
        self.connected = False
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=0.5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None


def _log01(x: float) -> float:
    """Perceptual 0..1 compression: log10(1 + 9x), so quiet passages keep detail."""
    return math.log10(1.0 + 9.0 * max(0.0, min(1.0, float(x))))


def _follow(current: float, target: float, dt: float, attack: float, release: float) -> float:
    """Exponential envelope follower with separate attack/release time constants."""
    tau = attack if target > current else release
    if tau <= 1e-6:
        return target
    return current + (target - current) * (1.0 - math.exp(-dt / tau))


def _wrap_pm(x: float) -> float:
    """Wrap to [-0.5, 0.5)."""
    return (x + 0.5) % 1.0 - 0.5


def _hpss_frame(mag, history, freq_width=15):
    """Split one magnitude frame into harmonic and percussive components.

    Harmonic: median over recent frames per frequency bin (sustained tones).
    Percussive: median over neighbouring frequency bins (broadband transients).
    """
    history.append(np.asarray(mag, dtype=np.float32))
    frames = np.stack(history, axis=0)
    harmonic = np.median(frames, axis=0).astype(np.float32)

    width = min(max(1, int(freq_width)), len(mag))
    if width % 2 == 0:
        width -= 1
    if width <= 1:
        return harmonic, np.asarray(mag, dtype=np.float32).copy()
    pad = width // 2
    padded = np.pad(np.asarray(mag, dtype=np.float32), (pad, pad), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, width)
    percussive = np.median(windows, axis=-1).astype(np.float32)
    return harmonic, percussive


class AudioProcessor:
    """Multi-resolution FFT, onset peak-picking, rhythm/tempo lock, and musical envelopes."""

    BASS_WINDOW = 4096
    TEMPO_SR = 200  # onset-strength envelope sample rate (Hz)

    def __init__(
        self,
        rate=48000,
        window_size=1024,
        sensitivity=1.3,
        decay_kick=10.0,
        decay_snare=12.0,
        agc_cfg=None,
    ):
        self.rate = int(rate)
        self.window_size = int(window_size)  # treble window (transient timing)
        self.bass_window_size = max(2048, min(8192, self.BASS_WINDOW))
        self.sensitivity = float(sensitivity)
        self.decay_kick = float(decay_kick)  # kept for config compatibility
        self.decay_snare = float(decay_snare)

        agc_cfg = agc_cfg or {}
        self.agc_enabled = bool(agc_cfg.get("enabled", True))
        self.target_rms = float(agc_cfg.get("target_rms", 0.08))
        self.agc_tau = float(agc_cfg.get("tau_s", 20.0))
        self.silence_db = float(agc_cfg.get("silence_db", -45.0))
        self.silence_linear = 10.0 ** (self.silence_db / 20.0)

        # Treble FFT (21 ms) — snare/transient timing.
        self.ring = np.zeros(self.window_size, dtype=np.float32)
        self.hann = np.hanning(self.window_size).astype(np.float32)
        self.freqs = np.fft.rfftfreq(self.window_size, d=1.0 / self.rate)
        # Bass FFT (85 ms) — sub-bass resolution needs the long window.
        self.bass_ring = np.zeros(self.bass_window_size, dtype=np.float32)
        self.bass_hann = np.hanning(self.bass_window_size).astype(np.float32)
        self.bass_freqs = np.fft.rfftfreq(self.bass_window_size, d=1.0 / self.rate)

        self.band_masks = {}
        self.bass_band_masks = {}
        for name, (lo, hi) in BANDS.items():
            freqs = self.bass_freqs if name == "bass" else self.freqs
            masks = self.bass_band_masks if name == "bass" else self.band_masks
            mask = (freqs >= lo) & (freqs <= hi)
            if not np.any(mask):
                idx = int(np.argmin(np.abs(freqs - (lo + hi) * 0.5)))
                mask[idx] = True
            masks[name] = mask

        # Fast 1024-window low-band flux for kick *timing* (the 4096 window
        # keeps the sub-bass level, but smears transients by ~80 ms).
        fast = (self.freqs >= 20.0) & (self.freqs <= 120.0)
        if not np.any(fast):
            fast[int(np.argmin(np.abs(self.freqs - 70.0)))] = True
        self.fast_bass_mask = fast
        self.spectral_mask = (self.freqs >= 50.0) & (self.freqs <= 12000.0)
        self.chroma_mask = (self.freqs >= 55.0) & (self.freqs <= 4000.0)
        cf = self.freqs[self.chroma_mask]
        midi = 69.0 + 12.0 * np.log2(np.maximum(cf, 1e-6) / 440.0)
        self.chroma_pc = np.mod(np.rint(midi).astype(np.int64), 12)

        # HPSS history: ~180 ms at the nominal 512/48k hop.
        self.mag_hist = deque(maxlen=17)
        self.bass_mag_hist = deque(maxlen=17)
        self.prev_p_mag = np.zeros(len(self.freqs), dtype=np.float32)
        self.prev_p_mag_bass = np.zeros(len(self.bass_freqs), dtype=np.float32)
        self.prev_flux = {k: 0.0 for k in BANDS}
        self.total_flux = 0.0
        self.total_flux_hist = deque(maxlen=150)
        self.flux_centroid = 0.0
        # >= 1.5 s of flux history at the nominal 512/48k hop.
        self.flux_hist = {k: deque(maxlen=150) for k in BANDS}

        self.band_ref = {"bass": 1e-3, "snare": 1e-3, "mids": 1e-3, "highs": 1e-3}
        self.levels = {"bass": 0.0, "snare": 0.0, "mids": 0.0, "highs": 0.0}
        self.perc_levels = {"bass": 0.0, "snare": 0.0, "mids": 0.0, "highs": 0.0}

        self.rms_ema = self.target_rms
        self.agc_gain = 1.0
        self.last_rms = 0.0
        self.energy_rms = 0.0
        self.energy_ref = max(self.target_rms, 1e-4)

        # Musical envelope set (§4).
        self.pulse_env = 0.0
        self.kick_env = 0.0
        self.snare_env = 0.0
        self.bright_env = 0.0
        self.energy_env = 0.0
        self.harmonic_swell = 0.0
        self.percussive_punch = 0.0

        self.kick_trig = False
        self.snare_trig = False
        self.aligned_kick = False
        self.aligned_snare = False
        self.last_kick_t = -1.0
        self.last_snare_t = -1.0
        self.last_mid_t = -1.0
        self.last_hat_t = -1.0
        self.hat_trig = False
        self.hat_env = 0.0
        self.aligned_hat = False

        # Rhythm stage (§3).
        self.onset_hist = deque()  # (t, strength, band) last 8 s
        self.bass_onsets = deque()  # (t, strength) last 8 s
        self.bpm_est = 120.0
        self.bpm_conf = 0.0
        self.music_conf = 0.0
        self.grid_conf = 0.0
        self.beat_phase = 0.0
        self.beat_count = 0
        self.on_downbeat = False
        self._pending_bpm = None
        self._pending_bpm_t = 0.0
        self._last_tempo_t = -1.0
        self._grid_bad = 0

        # Perception layer: groove, mood, harmony, section.
        self.onset_marks = deque(maxlen=256)  # (t, kind, strength, beat_phase, bar_pos)
        self.groove = {"density": 0.0, "syncopation": 0.0, "swing": 0.0, "punch": 0.0, "backbeat": 0.0}
        self.mood = {"energy": 0.0, "brightness": 0.0, "warmth": 0.0, "complexity": 0.0, "harmonic": 0.0}
        self.chroma = np.zeros(12, dtype=np.float64)
        self.key = 0
        self.key_conf = 0.0
        self.spectral_centroid = 3000.0
        self.spectral_flatness = 0.0
        self.energy_fast = 0.0
        self.energy_slow = 0.0
        self.build = 0.0
        self.drop_until = 0.0
        self.section_name = "idle"
        self.section_drop = False

        self.sim_time = 0.0
        self._analysis_blocks = 0

    # -- analysis helpers -------------------------------------------------
    def _band_rms(self, mag, mask):
        b = mag[mask]
        return float(np.sqrt(np.mean(b * b))) if b.size else 0.0

    def _detect_onset(self, name, flux, now, k, refractory, floor, min_level, last_t, gate=True):
        hist = self.flux_hist[name]
        prev = self.prev_flux[name]
        hist.append(flux)
        self.prev_flux[name] = flux
        if len(hist) < 4:
            return 0.0
        med = float(np.median(hist))
        # Flux scales with the AGC gain; keep the absolute floor proportional
        # so quiet-but-loud-in-the-room tracks still trigger.
        floor_eff = floor * max(0.2, self.agc_gain)
        thr = max(floor_eff, med * k + floor_eff * 0.5)
        # Local-max approximation: current must be above the previous hop.
        level_gate = max(self.levels.get(name, 1.0), self.perc_levels.get(name, 1.0))
        if gate and flux > thr and flux >= prev and level_gate > min_level and (now - last_t) > refractory:
            strength = min(1.0, max(0.0, flux / max(thr, 1e-9) - 1.0))
            return strength
        return 0.0

    def _detect_snare_onset(self, total_flux, centroid, now):
        """Broadband, high-centroid transient detector.

        Band flux alone cannot separate a snare from a kick or an 880 Hz synth
        stab (both leak into 150–900 Hz). Snares carry a loud, wide spectrum,
        so require high total novelty *and* a high spectral centroid.
        """
        prev = self.total_flux_hist[-1] if self.total_flux_hist else 0.0
        self.total_flux_hist.append(total_flux)
        self.total_flux = total_flux
        self.flux_centroid = centroid
        if len(self.total_flux_hist) < 4:
            return 0.0
        med = float(np.median(self.total_flux_hist))
        thr = max((0.32 / max(0.5, self.sensitivity)) * max(0.2, self.agc_gain), med * 1.8)
        if (
            total_flux > thr
            and total_flux >= prev
            and centroid > 3000.0
            and (now - self.last_snare_t) > 0.09
        ):
            return min(1.0, max(0.0, total_flux / max(thr, 1e-9) - 1.0))
        return 0.0

    def _aligned(self, latency=0.0) -> bool:
        """Onset near a 16th of the current beat grid (within 0.07 beat)."""
        phase = self.beat_phase - latency * self.bpm_est / 60.0
        q = phase * 4.0
        return abs(q - round(q)) / 4.0 < 0.07

    def _advance_beat(self, dt):
        self.on_downbeat = False
        self.beat_phase += dt * self.bpm_est / 60.0
        while self.beat_phase >= 1.0:
            self.beat_phase -= 1.0
            self.beat_count += 1
            if self.beat_count % 4 == 0:
                self.on_downbeat = True

    def _phase_correct(self, strength, is_bass, aligned, latency=0.0):
        weight = strength * self.bpm_conf * (2.0 if is_bass else 1.0)
        if weight <= 0.0:
            return
        self.grid_conf += ((1.0 if aligned else 0.0) - self.grid_conf) * 0.15
        # Compensate detector latency: the physical onset happened earlier
        # than this block (FFT window smear + one hop).
        onset_phase = self.beat_phase - latency * self.bpm_est / 60.0
        delta = _wrap_pm(-onset_phase)
        corr = math.copysign(min(abs(delta), 0.15), delta) * 0.12 * weight
        self.beat_phase = (self.beat_phase + corr) % 1.0
        if abs(delta) > 0.18:
            self._grid_bad += 1
            if self._grid_bad >= 3:
                self.grid_conf *= 0.5
                self._grid_bad = 0
        else:
            self._grid_bad = 0

    def _update_groove(self, now):
        while self.onset_marks and self.onset_marks[0][0] < now - 4.0:
            self.onset_marks.popleft()
        marks = list(self.onset_marks)
        kicks = snares = backbeats = 0
        strength_sum = sync_sum = 0.0
        swing_sum = 0.0
        swing_n = 0
        for _t, kind, strength, phase, bar_pos in marks:
            strength_sum += strength
            p16 = (phase * 4.0) % 1.0
            d16 = min(p16, 1.0 - p16) / 4.0
            on_beat = min(phase, 1.0 - phase) < 0.07
            if d16 > 0.07 and not on_beat:
                sync_sum += strength
            if kind != "kick":
                dev = ((phase * 2.0) % 1.0) - 0.5
                if abs(dev) < 0.3:
                    swing_sum += dev
                    swing_n += 1
            if kind == "kick":
                kicks += 1
            elif kind == "snare":
                snares += 1
                if bar_pos in (1, 3):
                    backbeats += 1

        density = len(marks) / 4.0
        targets = {
            "density": min(1.0, density / 6.0),
            "syncopation": sync_sum / max(strength_sum, 1e-9),
            "swing": max(-1.0, min(1.0, (swing_sum / max(swing_n, 1)) / 0.125)),
            "punch": min(1.0, kicks / 4.0),
            "backbeat": backbeats / max(snares, 1),
        }
        for key, target in targets.items():
            self.groove[key] += (target - self.groove[key]) * 0.5

        total = float(np.sum(self.chroma))
        if total > 1e-9:
            norm = self.chroma / total
            self.key = int(np.argmax(norm))
            self.key_conf = max(0.0, min(1.0, (float(norm[self.key]) - 1.0 / 12.0) / (1.0 - 1.0 / 12.0)))
            self._chroma_norm = norm
        levels_sum = sum(self.levels.values())
        warmth = self.levels["bass"] / max(levels_sum, 1e-6)
        target_mood = {
            "energy": max(0.0, min(1.0, self.energy_env)),
            "brightness": max(0.0, min(1.0, self.spectral_centroid / 8000.0)),
            "warmth": max(0.0, min(1.0, warmth)),
            "complexity": min(1.0, density / 6.0),
            "harmonic": self.key_conf,
        }
        for key, target in target_mood.items():
            self.mood[key] += (target - self.mood[key]) * 0.5

    def _update_tempo(self, now):
        # Broadband noise has a flat spectrum and no musical pulse; never let
        # its accidental autocorrelation peaks be promoted to a tempo lock.
        if self.spectral_flatness > 0.55:
            self.bpm_conf *= 0.5
            self.music_conf *= 0.5
            self.grid_conf *= 0.5
            return
        while self.onset_hist and self.onset_hist[0][0] < now - 8.0:
            self.onset_hist.popleft()
        while self.bass_onsets and self.bass_onsets[0][0] < now - 8.0:
            self.bass_onsets.popleft()

        # Nothing played recently: drop the lock instead of reporting stale
        # confidence forever after the track ends.
        if not self.onset_hist or self.onset_hist[-1][0] < now - 2.0:
            self.bpm_conf *= 0.5
            self.music_conf *= 0.5
            self.grid_conf *= 0.5
            return

        # Onset density (music-vs-noise proxy).
        recent = sum(1 for t, _, _ in self.onset_hist if t >= now - 4.0)
        density = recent / 4.0
        target_music = 1.0 if 1.5 <= density <= 8.0 else 0.0
        self.music_conf += (target_music - self.music_conf) * 0.5

        if len(self.onset_hist) < 6:
            self.bpm_conf *= 0.85
            return

        # Onset-strength envelope at 200 Hz over the last 8 s.
        sr = self.TEMPO_SR
        n = int(8.0 * sr)
        env = np.zeros(n, dtype=np.float64)
        t0 = now - 8.0
        half = int(0.06 * sr * 0.5) + 1  # 60 ms raised-cosine kernel
        for t, strength, _band in self.onset_hist:
            idx = int((t - t0) * sr)
            if idx < 0 or idx >= n:
                continue
            lo, hi = max(0, idx - half), min(n, idx + half + 1)
            for j in range(lo, hi):
                env[j] += strength * (0.5 + 0.5 * math.cos(math.pi * (j - idx) / half))

        # Comb-filter score: fold the envelope by each candidate period and
        # take the best phase bin (§3.2).
        def fold_score(tempo):
            period = int(round(sr * 60.0 / tempo))
            if period < 2 or n // period < 2:
                return 0.0
            folded = env[: (n // period) * period].reshape(-1, period).sum(axis=0)
            return float(folded.max())

        temps = np.arange(60.0, 190.0001, 0.25)
        scores = np.array([fold_score(t) for t in temps], dtype=np.float64)
        # Gentle preference for musically common tempos.
        bias = np.where((temps >= 70.0) & (temps <= 170.0), 1.03, 0.97)
        weighted = scores * bias
        best_i = int(np.argmax(weighted))
        best_score = float(scores[best_i])
        if best_score <= 1e-9:
            self.bpm_conf *= 0.85
            return
        p90 = float(np.percentile(scores, 90))
        # Peak prominence: the winner must stand out from the top decile of
        # tempo candidates, otherwise random onsets (noise/speech) score high.
        conf = max(0.0, min(1.0, (best_score / max(p90, 1e-9) - 1.0) / 1.2))

        winner = float(temps[best_i])
        # Octave folding / disambiguation (§3.2.3): only pull a tempo that
        # sits outside the preferred 70–170 band. Inside it, similar scores at
        # half/double time are expected and the candidate itself wins.
        if winner > 170.0 and winner / 2.0 >= 60.0 and fold_score(winner / 2.0) > 0.75 * best_score:
            winner /= 2.0
        elif winner < 70.0 and winner * 2.0 <= 190.0 and fold_score(winner * 2.0) > 0.75 * best_score:
            winner *= 2.0

        self.bpm_conf = conf
        if conf < 0.12:
            return
        # Smooth within ±5 %, otherwise require 2 s of confirmation.
        switched = False
        if abs(winner - self.bpm_est) / max(self.bpm_est, 1.0) <= 0.05:
            self.bpm_est += 0.2 * (winner - self.bpm_est)
            self._pending_bpm = None
        elif self._pending_bpm is not None and abs(winner - self._pending_bpm) / max(self._pending_bpm, 1.0) <= 0.03:
            if now - self._pending_bpm_t >= 2.0:
                self.bpm_est = winner
                self._pending_bpm = None
                switched = True
        else:
            self._pending_bpm = winner
            self._pending_bpm_t = now

        # Re-anchor the grid to the latest kick when the tempo hypothesis
        # changes; otherwise the phase PLL needs many beats to catch up.
        if switched and self.bass_onsets:
            t_k = self.bass_onsets[-1][0]
            latency = (self.bass_window_size / self.rate) * 0.25
            beats_since = (now - (t_k - latency)) * self.bpm_est / 60.0
            self.beat_phase = beats_since % 1.0
            self._grid_bad = 0

    # -- main entry -------------------------------------------------------
    def process_block(self, samples: np.ndarray, now: float = None) -> dict:
        if len(samples) == 0:
            return self.snapshot(alive=False)

        dt = len(samples) / float(self.rate)
        if now is None:
            self.sim_time += dt
            now = self.sim_time

        raw_rms = float(np.sqrt(np.mean(samples * samples)))
        self.last_rms = raw_rms

        # Passage intensity: 3 s RMS relative to a 30 s reference.
        self.energy_rms += (raw_rms - self.energy_rms) * min(1.0, dt / 3.0)
        self.energy_ref += (raw_rms - self.energy_ref) * min(1.0, dt / 30.0)
        self.energy_fast += (raw_rms - self.energy_fast) * min(1.0, dt / 1.0)
        self.energy_slow += (raw_rms - self.energy_slow) * min(1.0, dt / 8.0)
        energy_target = _log01(min(1.0, 0.5 * self.energy_rms / max(self.energy_ref, 1e-6)))

        if self._analysis_blocks == 0:
            need = max(40, int(1.6 / max(dt, 1e-6)))
            if self.flux_hist["bass"].maxlen != need:
                self.flux_hist = {k: deque(maxlen=need) for k in BANDS}
                self.total_flux_hist = deque(maxlen=need)

        # Silence gate + AGC (§2.A): clip tight, release slow, and NEVER ramp
        # gain up into residual noise when a song fades out or ends.
        quiet_floor = max(self.silence_linear, min(0.012, self.energy_slow * 0.12))
        is_silent = raw_rms < quiet_floor
        if is_silent:
            self.agc_gain = 1.0
        elif self.agc_enabled:
            # Noise-floor clamp: below 0.012 RMS the block is mostly
            # quantisation noise, so hold the last gain instead of ramping
            # 2.5x into hiss during fade-outs and quiet passages.
            if raw_rms >= 0.012:
                alpha_agc = min(1.0, dt / max(0.5, self.agc_tau))
                if raw_rms >= self.rms_ema or raw_rms > max(self.silence_linear * 4.0, 0.012):
                    self.rms_ema = (1.0 - alpha_agc) * self.rms_ema + alpha_agc * raw_rms
                self.agc_gain = float(np.clip(self.target_rms / max(self.rms_ema, 1e-3), 0.4, 2.5))
            else:
                self.agc_gain = float(np.clip(self.agc_gain, 0.4, 2.5))
        else:
            self.agc_gain = 1.0

        gained = np.clip(samples * self.agc_gain, -1.0, 1.0)
        n = len(gained)

        # Update both sliding windows.
        if n >= self.window_size:
            self.ring[:] = gained[-self.window_size :]
        else:
            self.ring[:-n] = self.ring[n:]
            self.ring[-n:] = gained
        if n >= self.bass_window_size:
            self.bass_ring[:] = gained[-self.bass_window_size :]
        else:
            self.bass_ring[:-n] = self.bass_ring[n:]
            self.bass_ring[-n:] = gained

        spec = np.fft.rfft(self.ring * self.hann)
        mag = np.abs(spec).astype(np.float32) / (self.window_size * 0.5)
        # HPSS: harmonic beds vs. percussive transients. Only the
        # percussive component is allowed to fire onsets.
        harm_mag, perc_mag = _hpss_frame(mag, self.mag_hist)
        pos_diff = np.maximum(0.0, perc_mag - self.prev_p_mag)
        self.prev_p_mag = perc_mag
        total_diff = float(np.sum(pos_diff))
        centroid = float(np.dot(pos_diff, self.freqs) / total_diff) if total_diff > 1e-9 else 0.0
        fast_bass_flux = float(np.mean(pos_diff[self.fast_bass_mask]))

        # Timbre + harmony (slow, stable features).
        sm = mag[self.spectral_mask]
        se = float(np.sum(sm))
        if se > 1e-9:
            sf = self.freqs[self.spectral_mask]
            centroid = float(np.dot(sf, sm) / se)
            flat = float(np.exp(np.mean(np.log(sm + 1e-9))) / (np.mean(sm) + 1e-9))
            self.spectral_centroid += (centroid - self.spectral_centroid) * min(1.0, dt / 2.0)
            self.spectral_flatness += (flat - self.spectral_flatness) * min(1.0, dt / 2.0)
        cm = mag[self.chroma_mask].astype(np.float64) ** 2
        if float(np.sum(cm)) > 1e-9:
            raw_chroma = np.bincount(self.chroma_pc, weights=cm, minlength=12)
            self.chroma += (raw_chroma - self.chroma) * min(1.0, dt / 4.0)

        bass_spec = np.fft.rfft(self.bass_ring * self.bass_hann)
        bass_mag = np.abs(bass_spec).astype(np.float32) / (self.bass_window_size * 0.5)
        bass_harm, bass_perc = _hpss_frame(bass_mag, self.bass_mag_hist)
        bass_pos_diff = np.maximum(0.0, bass_perc - self.prev_p_mag_bass)
        self.prev_p_mag_bass = bass_perc

        self.kick_trig = False
        self.snare_trig = False
        self.hat_trig = False
        self.aligned_kick = False
        self.aligned_snare = False
        self.aligned_hat = False

        self._advance_beat(dt)

        if is_silent:
            for k in self.levels:
                self.levels[k] = _follow(self.levels[k], 0.0, dt, 0.06, 0.40)
                self.perc_levels[k] = _follow(self.perc_levels[k], 0.0, dt, 0.06, 0.40)
            self.pulse_env = _follow(self.pulse_env, 0.0, dt, 0.08, 0.40)
            self.kick_env = _follow(self.kick_env, 0.0, dt, 0.0, 0.26)
            self.snare_env = _follow(self.snare_env, 0.0, dt, 0.0, 0.18)
            self.hat_env = _follow(self.hat_env, 0.0, dt, 0.0, 0.12)
            self.bright_env = _follow(self.bright_env, 0.0, dt, 0.10, 0.30)
            self.energy_env = _follow(self.energy_env, energy_target, dt, 3.0, 3.0)
            self.harmonic_swell = _follow(self.harmonic_swell, 0.0, dt, 0.10, 0.60)
            self.percussive_punch = _follow(self.percussive_punch, 0.0, dt, 0.0, 0.18)
            # Keep the grid clock running through short gaps; sustained silence
            # drops confidence so effects fall back to their idle state.
            self.on_downbeat = False
            self.bpm_conf *= math.exp(-dt / 2.0)
            self.music_conf *= math.exp(-dt / 2.0)
            self.grid_conf *= math.exp(-dt / 1.5)
            if now - self._last_tempo_t >= 1.0:
                self._last_tempo_t = now
                self._update_groove(now)
                self._update_tempo(now)
            return self.snapshot(alive=True, t_ingest=now)

        # Per-band level + flux (§2.C).
        band_floors = {"bass": 0.008, "snare": 0.005, "mids": 0.005, "highs": 0.003}
        # Soft noise-knee: smoothly attenuate near the quiet floor so fade-outs
        # glide to black instead of boosting residual hiss.
        quiet_knee = min(1.0, max(0.0, (raw_rms - quiet_floor) / max(quiet_floor * 2.5, 1e-5)))
        for name in BANDS:
            if name == "bass":
                mask = self.bass_band_masks[name]
                b_rms = self._band_rms(bass_harm, mask)
                p_rms = self._band_rms(bass_perc, mask)
                flux = float(np.mean(bass_pos_diff[mask]))
            else:
                mask = self.band_masks[name]
                b_rms = self._band_rms(harm_mag, mask)
                p_rms = self._band_rms(perc_mag, mask)
                flux = float(np.mean(pos_diff[mask]))

            b_floor = band_floors[name]
            if b_rms > b_floor:
                if self._analysis_blocks == 0:
                    self.band_ref[name] = max(b_floor, b_rms)
                else:
                    # Asymmetric reference: fast attack into loud passages
                    # (2.5 s), very slow release (18 s) with a hard b_floor so
                    # fade-outs glide to black instead of self-normalising.
                    tau_ref = 2.5 if b_rms > self.band_ref[name] else 18.0
                    self.band_ref[name] = max(b_floor, self.band_ref[name] + (b_rms - self.band_ref[name]) * min(1.0, dt / tau_ref))
            ratio = (b_rms / max(self.band_ref[name], b_floor)) * 0.30 * self.sensitivity
            target = _log01(ratio) * quiet_knee
            self.levels[name] = _follow(self.levels[name], target, dt, 0.06, 0.40)
            # Percussive gate level: onset peak-picking rides the isolated
            # drum component, not the harmonic bed.
            p_ratio = (p_rms / max(self.band_ref[name], b_floor)) * 0.30 * self.sensitivity
            self.perc_levels[name] = _follow(self.perc_levels[name], _log01(p_ratio) * quiet_knee, dt, 0.02, 0.25)

            if name == "bass":
                lat = dt + 0.25 * self.window_size / self.rate
                b_strength = self._detect_onset("bass", fast_bass_flux, now, k=3.0, refractory=0.18, floor=0.004, min_level=0.18, last_t=self.last_kick_t)
                if b_strength > 0.0:
                    self.last_kick_t = now
                    self.kick_trig = True
                    if self.build > 0.25 and self.energy_fast > 1.1 * max(self.energy_slow, 1e-6):
                        self.drop_until = now + 1.5
                    self.aligned_kick = self._aligned(lat)
                    eff = b_strength if (self.aligned_kick or self.bpm_conf < 0.25) else b_strength * 0.15
                    self.kick_env = max(self.kick_env, eff)
                    self.bass_onsets.append((now, b_strength))
                    self.onset_hist.append((now, b_strength, "bass"))
                    self.onset_marks.append((now, "kick", b_strength, self.beat_phase, self.beat_count % 4))
                    self._phase_correct(b_strength, True, self.aligned_kick, lat)
            elif name == "snare":
                lat = dt + 0.25 * self.window_size / self.rate
                self.flux_hist["snare"].append(flux)
                s_strength = self._detect_snare_onset(total_diff, centroid, now)
                if s_strength > 0.0:
                    self.last_snare_t = now
                    self.snare_trig = True
                    self.aligned_snare = self._aligned(lat)
                    eff = s_strength if (self.aligned_snare or self.bpm_conf < 0.25) else s_strength * 0.15
                    self.snare_env = max(self.snare_env, eff)
                    self.onset_hist.append((now, s_strength, "snare"))
                    self.onset_marks.append((now, "snare", s_strength, self.beat_phase, self.beat_count % 4))
                    self._phase_correct(s_strength, False, self.aligned_snare, lat)
            elif name == "highs":
                lat = dt + 0.25 * self.window_size / self.rate
                h_strength = self._detect_onset("highs", flux, now, k=2.5, refractory=0.06, floor=0.0005, min_level=0.05, last_t=self.last_hat_t, gate=centroid > 3500.0)
                if h_strength > 0.0:
                    self.last_hat_t = now
                    self.hat_trig = True
                    self.aligned_hat = self._aligned(lat)
                    eff = h_strength if (self.aligned_hat or self.bpm_conf < 0.25) else h_strength * 0.15
                    self.hat_env = max(self.hat_env, eff)
                    self.onset_marks.append((now, "hat", h_strength, self.beat_phase, self.beat_count % 4))
            elif name == "mids":
                lat = dt + 0.25 * self.window_size / self.rate
                m_strength = self._detect_onset("mids", flux, now, k=2.0, refractory=0.12, floor=0.001, min_level=0.15, last_t=self.last_mid_t)
                if m_strength > 0.0:
                    self.last_mid_t = now
                    self.onset_hist.append((now, m_strength * 0.6, "mids"))
                    self.onset_marks.append((now, "mid", m_strength * 0.6, self.beat_phase, self.beat_count % 4))
                    self._phase_correct(m_strength * 0.6, False, self._aligned(lat), lat)

        self.kick_env = _follow(self.kick_env, 0.0, dt, 0.0, 0.26)
        self.snare_env = _follow(self.snare_env, 0.0, dt, 0.0, 0.18)
        self.hat_env = _follow(self.hat_env, 0.0, dt, 0.0, 0.12)
        self.pulse_env = _follow(self.pulse_env, self.levels["bass"], dt, 0.08, 0.40)
        bright_target = max(0.0, min(1.0, (0.4 * self.levels["highs"] + 0.35 * self.snare_env + 0.25 * self.hat_env) * (0.25 + 0.75 * self.music_conf)))
        self.bright_env = _follow(self.bright_env, bright_target, dt, 0.10, 0.30)
        self.energy_env = _follow(self.energy_env, energy_target, dt, 3.0, 3.0)
        harmonic_target = max(0.0, min(1.0, 0.50 * self.levels["bass"] + 0.32 * self.levels["mids"] + 0.18 * self.levels["highs"]))
        self.harmonic_swell = _follow(self.harmonic_swell, harmonic_target, dt, 0.20, 0.80)
        percussive_target = max(self.kick_env, self.snare_env, 0.5 * self.hat_env)
        self.percussive_punch = _follow(self.percussive_punch, percussive_target, dt, 0.01, 0.18)

        target_build = max(0.0, min(1.0, (self.energy_fast / max(self.energy_slow, 1e-6) - 1.0) * 1.5))
        self.build += (target_build - self.build) * min(1.0, dt / 0.5)
        if self.music_conf < 0.08 and self.energy_fast < 0.01:
            self.section_name = "idle"
        elif now < self.drop_until:
            self.section_name = "drop"
        elif self.build > 0.35:
            self.section_name = "build"
        else:
            self.section_name = "groove"
        self.section_drop = bool(now < self.drop_until)

        if now - self._last_tempo_t >= 1.0:
            self._last_tempo_t = now
            self._update_groove(now)
            self._update_tempo(now)
        else:
            self.bpm_conf *= math.exp(-dt / 8.0)

        self._analysis_blocks += 1
        return self.snapshot(alive=True, t_ingest=now)

    def _live_cue(self, alive=True):
        """Best-effort ShowGraph cue when no cached TrackMap is loaded."""
        cue = {
            "available": False,
            "section": "intro",
            "section_progress": 0.0,
            "beats_to_drop": 0.0,
            "beats_in_section": 0.0,
            "harmonic_swell": 0.0,
            "percussive_punch": 0.0,
            "prominence": 0.0,
            "source": "live",
        }
        if not alive:
            return cue

        available = bool(self.music_conf > 0.05 and self.section_name != "idle")
        if self.section_name == "drop":
            name = "drop"
        elif self.section_name == "build":
            name = "build"
        elif not available:
            name = "intro"
        elif self.energy_env < 0.12:
            name = "breakdown"
        else:
            name = "verse"

        if name == "build":
            progress = max(0.0, min(1.0, self.build))
            beats_to_drop = max(0.0, (1.0 - progress) * 8.0)
        elif name == "drop":
            progress = 1.0 - max(0.0, min(1.5, self.drop_until - self.sim_time)) / 1.5
            beats_to_drop = 0.0
        else:
            progress = ((self.beat_count % 16) + self.beat_phase) / 16.0
            beats_to_drop = 0.0

        cue.update({
            "available": available,
            "section": name,
            "section_progress": round(max(0.0, min(1.0, progress)), 4),
            "beats_to_drop": round(beats_to_drop, 2),
            "beats_in_section": 16.0,
            "harmonic_swell": round(float(self.harmonic_swell), 3),
            "percussive_punch": round(float(self.percussive_punch), 3),
            "prominence": round(min(1.0, float(self.energy_fast / max(self.energy_slow, 1e-6))), 3),
        })
        return cue

    def snapshot(self, alive=True, connected=True, source_name="synthetic", error=None, t_ingest=0.0):
        bpm = max(1.0, float(self.bpm_est))
        beat_period = 60.0 / bpm
        on_beat = min(self.beat_phase, 1.0 - self.beat_phase) * beat_period <= 0.035
        env = {
            "pulse": round(float(self.pulse_env), 3),
            "kick": round(float(self.kick_env), 3),
            "snare": round(float(self.snare_env), 3),
            "hat": round(float(self.hat_env), 3),
            "bright": round(float(self.bright_env), 3),
            "energy": round(float(self.energy_env), 3),
            "harmonic": round(float(self.harmonic_swell), 3),
            "percussive": round(float(self.percussive_punch), 3),
        }
        beat = {
            "phase": round(float(self.beat_phase), 4),
            # Fixed 4/4 assumption (§3.3): no meter detection in v1.
            "bar_phase": round(((self.beat_count % 4) + self.beat_phase) / 4.0, 4),
            "count": int(self.beat_count),
            "bar": int(self.beat_count // 4),
            "bar_pos": int(self.beat_count % 4),
            "phrase": round((self.beat_count % 16) / 16.0, 4),
            "next_in": round(max(0.0, (1.0 - self.beat_phase)) * beat_period, 3),
            "on_beat": bool(on_beat),
            "on_downbeat": bool(self.on_downbeat),
        }
        conf = {
            "bpm": round(float(self.bpm_conf), 3),
            "music": round(float(self.music_conf), 3),
            "grid": round(float(self.grid_conf), 3),
        }
        total_chroma = float(np.sum(self.chroma))
        chroma_norm = (self.chroma / total_chroma) if total_chroma > 1e-9 else np.zeros(12)
        harmony = {
            "key": int(self.key),
            "key_conf": round(float(self.key_conf), 3),
            "chroma": [round(float(v), 3) for v in chroma_norm],
        }
        groove = {k: round(float(v), 3) for k, v in self.groove.items()}
        mood = {k: round(float(v), 3) for k, v in self.mood.items()}
        section = {
            "name": self.section_name,
            "build": round(float(self.build), 3),
            "drop": bool(self.section_drop),
        }
        if not alive:
            return {
                "alive": False,
                "connected": bool(connected),
                "source": source_name,
                "bass": 0.0,
                "snare": 0.0,
                "mids": 0.0,
                "highs": 0.0,
                "kick": False,
                "snare_hit": False,
                "hat_hit": False,
                "kick_env": 0.0,
                "snare_env": 0.0,
                "beat_count": int(self.beat_count),
                "bpm": 0,
                "rms": 0.0,
                "agc_gain": round(self.agc_gain, 2),
                "t_ingest": t_ingest,
                "error": error,
                "aligned_kick": False,
                "aligned_snare": False,
                "aligned_hat": False,
                "env": {k: 0.0 for k in env},
                "beat": {**beat, "on_beat": False, "on_downbeat": False},
                "conf": conf,
                "groove": {k: 0.0 for k in groove},
                "mood": {k: 0.0 for k in mood},
                "harmony": {"key": 0, "key_conf": 0.0, "chroma": [0.0] * 12},
                "section": {"name": "idle", "build": 0.0, "drop": False},
                "cue": self._live_cue(False),
            }
        return {
            "alive": True,
            "connected": bool(connected),
            "source": source_name,
            "bass": round(float(self.levels["bass"]), 3),
            "snare": round(float(self.levels["snare"]), 3),
            "mids": round(float(self.levels["mids"]), 3),
            "highs": round(float(self.levels["highs"]), 3),
            "kick": bool(self.kick_trig),
            "snare_hit": bool(self.snare_trig),
            "hat_hit": bool(self.hat_trig),
            "kick_env": round(float(self.kick_env), 3),
            "snare_env": round(float(self.snare_env), 3),
            "beat_count": int(self.beat_count),
            "bpm": int(round(self.bpm_est)),
            "rms": round(float(self.last_rms), 4),
            "agc_gain": round(float(self.agc_gain), 2),
            "t_ingest": t_ingest,
            "error": error,
            "aligned_kick": bool(self.aligned_kick),
            "aligned_snare": bool(self.aligned_snare),
            "aligned_hat": bool(self.aligned_hat),
            "env": env,
            "beat": beat,
            "conf": conf,
            "groove": groove,
            "mood": mood,
            "harmony": harmony,
            "section": section,
            "cue": self._live_cue(True),
        }


class AudioEngine:
    """Threaded audio ingestion + inline FFT DSP engine."""

    def __init__(self, audio_cfg: dict):
        self.cfg = dict(audio_cfg)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.source = None
        self.active_source_name = "synthetic"
        self.dsp = self._build_dsp(self.cfg)
        self._latest = self.dsp.snapshot(alive=False)
        self._history = deque(maxlen=512)
        self._delayed = None
        self.last_update = 0.0
        show_cfg = self.cfg.get("show") or {}
        self.show = ShowAnalyzer(cache_dir=show_cfg.get("cache_dir", "config/show_cache"))
        self._track_key = None
        self._track_map = None
        self._capture_key = None
        self._capture_parts = []
        self._capture_frames = 0
        self._capture_start_progress = 0.0
        self._capture_last_progress = 0.0
        self._capture_duration = 0.0
        self._capture_enabled = False
        self._capture_max_s = float(show_cfg.get("capture_max_s", 480.0))
        self._analysis_lock = threading.Lock()
        self._analysis_keys = set()

    @staticmethod
    def _resolve_source_name(src):
        if src == "auto":
            if sys.platform != "win32" and shutil.which("parec"):
                return "parec"
            return "synthetic"
        return src

    def _build_dsp(self, cfg):
        agc = dict(cfg.get("agc", {}))
        # §2.A: the AGC must not pump through quiet -> loud transitions.
        agc["tau_s"] = max(20.0, float(agc.get("tau_s", 20.0)))
        return AudioProcessor(
            rate=cfg.get("rate", 48000),
            window_size=cfg.get("window", 1024),
            sensitivity=cfg.get("sensitivity", 1.3),
            decay_kick=cfg.get("decay_kick", 10.0),
            decay_snare=cfg.get("decay_snare", 12.0),
            agc_cfg=agc,
        )

    def _build_source(self, cfg):
        name = self._resolve_source_name(cfg.get("source", "auto"))
        self.active_source_name = name
        rate = int(cfg.get("rate", 48000))
        channels = int(cfg.get("channels", 2))
        block = int(cfg.get("block", 512))
        if name == "parec":
            return ParecSource(cfg.get("device"), rate=rate, channels=channels, block=block, stop_event=self.stop_event)
        if name == "snapcast":
            sc = cfg.get("snapcast", {})
            port = int(sc.get("port", 1704))
            if port == 4953:  # stale default from before the binary-protocol client existed
                port = 1704
            return SnapcastSource(
                host=sc.get("host", "127.0.0.1"),
                port=port,
                rate=rate,
                channels=channels,
                block=block,
                stop_event=self.stop_event,
            )
        if name == "wav":
            wav_path = cfg.get("wav", "testdata/click_128.wav")
            if not os.path.exists(wav_path):
                log.warning("WAV '%s' not found -> falling back to synthetic source", wav_path)
                self.active_source_name = "synthetic"
                return SyntheticSource(rate=rate, block=block, realtime=True)
            return WavSource(wav_path, rate=rate, block=block, realtime=True)
        if name == "sendspin":
            sc = cfg.get("sendspin", {})
            src = SendspinAudioSource(
                server_url=sc.get("url", "ws://192.168.10.13:8927/sendspin"),
                client_name=sc.get("client_name", "Hueme-Visualizer"),
                rate=rate,
                channels=channels,
                block=block,
                stop_event=self.stop_event,
                state_dir=cfg.get("sendspin_state_dir", "config"),
            )
            src.start()
            return src
        return SyntheticSource(rate=rate, block=block, realtime=True)

    def start(self):
        self.stop()
        self.stop_event.clear()
        self.dsp = self._build_dsp(self.cfg)
        self.source = self._build_source(self.cfg)
        self.thread = threading.Thread(target=self._loop, name="hueme-audio", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.source:
            try:
                self.source.close()
            except Exception:
                pass
        if self.thread:
            self.thread.join(timeout=1.5)
            self.thread = None

    def reconfigure(self, new_cfg: dict):
        restart_needed = any(
            new_cfg.get(k) != self.cfg.get(k)
            for k in ("source", "device", "rate", "channels", "block", "window", "snapcast", "wav", "sendspin", "sendspin_state_dir")
        )
        self.cfg = dict(new_cfg)
        with self.lock:
            self.dsp.sensitivity = float(new_cfg.get("sensitivity", 1.3))
            self.dsp.decay_kick = float(new_cfg.get("decay_kick", 10.0))
            self.dsp.decay_snare = float(new_cfg.get("decay_snare", 12.0))
            agc = new_cfg.get("agc", {})
            self.dsp.agc_enabled = bool(agc.get("enabled", True))
            self.dsp.target_rms = float(agc.get("target_rms", 0.08))
            self.dsp.agc_tau = max(20.0, float(agc.get("tau_s", 20.0)))
            self.dsp.silence_db = float(agc.get("silence_db", -45.0))
            self.dsp.silence_linear = 10.0 ** (self.dsp.silence_db / 20.0)
        if restart_needed:
            self.start()

    def set_sensitivity(self, sens: float):
        with self.lock:
            self.cfg["sensitivity"] = float(sens)
            self.dsp.sensitivity = float(sens)

    def set_latency(self, ms: float) -> float:
        """Apply the visual sync trim live (no config save)."""
        ms = max(-500.0, min(2000.0, float(ms)))
        with self.lock:
            self.cfg["latency_ms"] = ms
        return ms

    def get_sendspin_token(self) -> dict:
        """Return the pairing token for the active Sendspin client, if any."""
        src = self.source
        token = getattr(src, "pairing_token", None)
        return {
            "available": bool(token),
            "token": token,
            "client_id": getattr(src, "client_id", None),
            "connected": bool(getattr(src, "connected", False)),
            "source": self.active_source_name,
        }

    def _attach_show_cue(self, feat):
        """Override the live cue with a cached TrackMap when the source knows the track."""
        key = getattr(self.source, "track_key", None)
        if not key:
            return
        if key != self._track_key:
            self._track_key = key
            self._track_map = self.show.load_cached(key)
            if self._track_map is not None:
                log.info("ShowGraph cue loaded for '%s' (%.1f BPM, %d sections)",
                         key, self._track_map.tempo, len(self._track_map.sections))
        if self._track_map is None:
            return
        try:
            progress = float(getattr(self.source, "track_progress_s", 0.0) or 0.0)
        except (TypeError, ValueError):
            progress = 0.0
        feat["cue"] = self._track_map.cue_at(progress)
        feat["show_track"] = {
            "key": key,
            "progress_s": round(progress, 3),
            "tempo": round(float(self._track_map.tempo), 2),
        }

    def _update_show_capture(self, samples):
        """Record the streamed track once, then compile its TrackMap in the background.

        This removes the manual WAV/ffmpeg step: the first full play of a track
        populates the cache, subsequent plays get the full ShowGraph instantly.
        """
        key = getattr(self.source, "track_key", None)
        if not key:
            self._finish_show_capture()
            return
        try:
            progress = float(getattr(self.source, "track_progress_s", 0.0) or 0.0)
            duration = float(getattr(self.source, "track_duration_s", 0.0) or 0.0)
        except (TypeError, ValueError):
            progress, duration = 0.0, 0.0

        if key != self._capture_key:
            self._finish_show_capture()
            self._capture_key = key
            self._capture_parts = []
            self._capture_frames = 0
            self._capture_start_progress = progress
            self._capture_last_progress = progress
            self._capture_duration = duration
            cached = self._track_key == key and self._track_map is not None
            self._capture_enabled = bool(key and progress <= 2.0 and not cached)
            if self._capture_enabled:
                log.info("ShowGraph capture started for '%s'", key)

        if not self._capture_enabled:
            return

        if duration > 0.0 and self._capture_duration <= 0.0:
            self._capture_duration = duration
        self._capture_last_progress = progress
        if self._capture_frames < int(self._capture_max_s * self.dsp.rate):
            self._capture_parts.append(np.asarray(samples, dtype=np.float32).copy())
            self._capture_frames += len(samples)

        if (
            self._capture_frames >= int(self._capture_max_s * self.dsp.rate)
            or (duration > 0.0 and progress >= duration - 1.0)
        ):
            self._finish_show_capture()

    def _finish_show_capture(self):
        if not self._capture_key:
            return
        key = self._capture_key
        parts = self._capture_parts
        frames = self._capture_frames
        duration = self._capture_duration
        last_progress = self._capture_last_progress
        self._capture_key = None
        self._capture_parts = []
        self._capture_frames = 0
        self._capture_enabled = False

        if not parts or frames <= 0:
            return
        captured_s = frames / float(self.dsp.rate)
        min_s = min(120.0, duration * 0.85) if duration > 0.0 else 60.0
        if captured_s < min_s:
            log.info("ShowGraph capture for '%s' discarded (only %.0fs)", key, captured_s)
            return
        if duration > 0.0 and last_progress < duration - 3.0 and captured_s < duration * 0.90:
            log.info("ShowGraph capture for '%s' discarded (track skipped)", key)
            return

        with self._analysis_lock:
            if key in self._analysis_keys:
                return
            self._analysis_keys.add(key)
        log.info("ShowGraph compiling '%s' from %.0fs of captured audio ...", key, captured_s)
        threading.Thread(
            target=self._analyze_show_capture,
            args=(key, parts, captured_s),
            name="hueme-show-analyze",
            daemon=True,
        ).start()

    def _analyze_show_capture(self, key, parts, captured_s):
        try:
            samples = np.concatenate(parts)
            track_map = self.show.analyze_audio(samples, self.dsp.rate, track_key=key, force=False)
            log.info(
                "ShowGraph compiled '%s': %.1f BPM, %d sections, %d drops",
                key, track_map.tempo, len(track_map.sections), len(track_map.drops),
            )
        except Exception as e:
            log.warning("ShowGraph compile failed for '%s': %s", key, e)
        finally:
            with self._analysis_lock:
                self._analysis_keys.discard(key)

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                samples = self.source.read()
                t_now = time.monotonic()
                if len(samples) > 0:
                    # CRITICAL: run DSP on the exact sample clock (sim_time +=
                    # len(samples)/rate), not on bursty TCP arrival timestamps.
                    feat = self.dsp.process_block(samples, now=None)
                    feat["t_ingest"] = t_now
                    # Prefer the source's playback clock (Sendspin maps server
                    # timestamps to the local monotonic clock). Key history by
                    # *when the audio plays*, not when the TCP burst arrived.
                    play_t = float(getattr(self.source, "last_play_time", 0.0) or 0.0)
                    if not (t_now - 4.0 <= play_t <= t_now + 4.0):
                        play_t = t_now
                    feat["t_play"] = play_t
                    feat["play_lead_s"] = round(play_t - t_now, 4)
                    feat["connected"] = getattr(self.source, "connected", True)
                    feat["source"] = self.active_source_name
                    feat["error"] = getattr(self.source, "last_error", None)
                    try:
                        self._attach_show_cue(feat)
                        self._update_show_capture(samples)
                    except Exception:
                        log.debug("ShowGraph cue lookup/capture failed", exc_info=True)
                    with self.lock:
                        self._latest = feat
                        if self._history and play_t < self._history[-1][0] - 0.05:
                            # Clock resync/reconnect: drop stale ordering.
                            self._history.clear()
                        self._history.append((play_t, t_now, feat))
                        while self._history and self._history[0][0] < t_now - 4.0:
                            self._history.popleft()
                        self.last_update = t_now
                else:
                    self.stop_event.wait(0.05)
            except Exception as e:
                log.warning("Audio loop error: %s", e)
                self.stop_event.wait(0.2)

    def get_features(self, consume_triggers=False) -> dict:
        now = time.monotonic()
        try:
            stream_latency = float(getattr(self.source, "latency", 0.0) or 0.0)
        except (TypeError, ValueError):
            stream_latency = 0.0
        # Automatic Snapcast buffer compensation made lights land hundreds of
        # ms late on real setups. Only apply the explicit user trim; on top of
        # that, render LOOKAHEAD_S into the buffered future for hit anticipation.
        latency = float(self.cfg.get("latency_ms", 0) or 0) / 1000.0
        latency = max(-0.5, min(2.0, latency))
        target = now + LOOKAHEAD_S - latency

        with self.lock:
            alive = (now - self.last_update) < 1.5 and getattr(self.source, "connected", False)
            if not alive:
                return self.dsp.snapshot(
                    alive=False,
                    connected=getattr(self.source, "connected", False),
                    source_name=self.active_source_name,
                    error=getattr(self.source, "last_error", None),
                )

            kicks = False
            snares = False
            aligned_kick = False
            aligned_snare = False
            entry = None
            if consume_triggers:
                # Deliver the snapshot for the audio that will be audible at
                # target (now + lookahead - trim). Drop triggers older than
                # 80 ms when a network burst arrives, otherwise the lights
                # flash a backlog of events late.
                cutoff = target - 0.08
                while self._history and self._history[0][0] <= target:
                    t_play, t_proc, feat = self._history.popleft()
                    if t_play >= cutoff:
                        kicks = kicks or bool(feat.get("kick"))
                        snares = snares or bool(feat.get("snare_hit"))
                        aligned_kick = aligned_kick or bool(feat.get("aligned_kick"))
                        aligned_snare = aligned_snare or bool(feat.get("aligned_snare"))
                    entry = (t_play, t_proc, feat)
                if entry is not None:
                    self._delayed = entry
                else:
                    entry = self._delayed
            else:
                for candidate in reversed(self._history):
                    if candidate[0] <= target:
                        entry = candidate
                        break
                if entry is None:
                    entry = self._delayed

            if entry is None:
                entry = (self.last_update, self.last_update, self._latest)
            t_play, t_proc, feat = entry
            out = dict(feat)
            out["kick"] = kicks
            out["snare_hit"] = snares
            out["aligned_kick"] = aligned_kick
            out["aligned_snare"] = aligned_snare
            out["t_ingest"] = t_proc + max(0.0, latency)
            age = max(0.0, min(0.5, now - t_proc - max(0.0, latency)))
            out["latency_s"] = round(latency, 3)
            out["stream_latency_s"] = round(stream_latency, 3)
            out["t_play"] = round(t_play, 3)
            out["play_lead_ms"] = round((t_play - now) * 1000.0, 1)
            out["lookahead_ms"] = int(LOOKAHEAD_S * 1000)
            out["age_ms"] = round(age * 1000.0, 1)

            # A negative Sync trim means "advance the lights". Extrapolate only
            # the part not already covered by playback-time snapshots, so the
            # fixed lookahead and the trim cannot double-advance the grid.
            if latency < 0.0:
                bpm = float(out.get("bpm", 0) or 0)
                beat = out.get("beat")
                lead = min(0.5, max(0.0, (now - latency) - t_play))
                if bpm > 0 and isinstance(beat, dict) and lead > 0.0:
                    beat = dict(beat)
                    phase = float(beat.get("phase", 0.0)) + lead * bpm / 60.0
                    wraps = int(phase)
                    phase %= 1.0
                    count = int(beat.get("count", 0)) + wraps
                    beat["phase"] = round(phase, 4)
                    beat["count"] = count
                    beat["bar_phase"] = round(((count % 4) + phase) / 4.0, 4)
                    beat["on_beat"] = min(phase, 1.0 - phase) * 60.0 / max(1.0, bpm) <= 0.035
                    out["beat"] = beat
            return out
