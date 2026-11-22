#!/usr/bin/env python3
"""
hue_gateway_web.py â€” Multi-Group Hue Entertainment Gateway + PulseAudio DSP (parec)
"""

import argparse
import colorsys
import json
import math
import os
import random
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from hue_entertainment_stream import (
    Z2mEntStreamer, CMD_FRAME, MODE_DEVICE,
    record, frame_payload, zclcommand_msg, rgb_to_xy, smoothing_for_fps,
)

try:
    import radar_layer as _rl
    HAS_RADAR = True
except ImportError:
    HAS_RADAR = False

CONFIG_FILE = "gateway_config.json"
E131_PORT = 5568


def _lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_xy_bri(r, g, b, max_bri=2047, mode="luminance"):
    if r <= 0 and g <= 0 and b <= 0:
        return 0.3127, 0.3290, 0
    r, g, b = min(255, max(0, int(r))), min(255, max(0, int(g))), min(255, max(0, int(b)))
    x, y = rgb_to_xy(r / 255.0, g / 255.0, b / 255.0)
    if mode == "max":
        v = max(r, g, b) / 255.0
    else:
        Y = 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)
        v = Y ** (1 / 2.4)
    bri = int(max(0, min(2047, round(v * max_bri))))
    return x, y, bri


def lerp_color(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def scale_color(c, factor):
    factor = max(0.0, min(1.0, factor))
    return (int(c[0] * factor), int(c[1] * factor), int(c[2] * factor))


def add_color(c1, c2):
    return (min(255, c1[0] + c2[0]), min(255, c1[1] + c2[1]), min(255, c1[2] + c2[2]))


PALETTES = {
    "cyberpunk": [(255, 0, 85), (0, 240, 255), (180, 0, 255)],
    "fire_ice":  [(255, 40, 0), (255, 160, 0), (0, 180, 255)],
    "toxic":     [(0, 255, 60), (200, 255, 0), (140, 0, 255)],
    "sunset":    [(255, 20, 60), (255, 110, 0), (255, 210, 80)],
    "rave":      [(255, 0, 0), (0, 255, 0), (0, 80, 255)],
    "warm_gold": [(255, 90, 0), (255, 180, 60), (255, 240, 180)],
}


# ---------------------------------------------------------------------------
# PulseAudio Capture via parec
# ---------------------------------------------------------------------------
class PulseAudioEngine:
    def __init__(self, device="LedFx_Audio.monitor", sample_rate=44100, channels=2):
        self.device = device or "LedFx_Audio.monitor"
        self.sample_rate = sample_rate
        self.channels = channels
        self.bytes_per_frame = 2 * channels

        self.lock = threading.Lock()
        self.bass = 0.0
        self.mids = 0.0
        self.highs = 0.0
        self.snare = 0.0
        self.kick_env = 0.0
        self.snare_env = 0.0
        self.beat_count = 0
        self.last_kick_t = 0.0
        self.last_update = 0.0

        self.hist_bass = 0.15
        self.hist_snare = 0.12
        self.prev_bass_raw = 0.0
        self.prev_snare_raw = 0.0
        self.bpm_est = 120.0
        self.sensitivity = 1.3

        self.stop_flag = threading.Event()
        self.thread = None
        self.proc = None
        self.connected = False
        self.last_error = None

    def start(self):
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1.0)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        if self.thread:
            self.thread.join(timeout=1.5)
            self.thread = None
        self.connected = False

    def _capture_loop(self):
        block_frames = 1024
        block_bytes = block_frames * self.bytes_per_frame
        backoff = 1.0

        while not self.stop_flag.is_set():
            try:
                cmd = [
                    "parec",
                    f"--device={self.device}",
                    "--format=s16le",
                    f"--rate={self.sample_rate}",
                    f"--channels={self.channels}",
                    "--latency-msec=30",
                    "--raw",
                ]
                self.proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=block_bytes * 4,
                )
                self.connected = True
                self.last_error = None
                print(f"[Pulse/parec] Capture gestartet â†’ {self.device}", flush=True)

                buf = b""
                while not self.stop_flag.is_set():
                    chunk = self.proc.stdout.read(block_bytes)
                    if not chunk:
                        break
                    buf += chunk
                    while len(buf) >= block_bytes:
                        data = buf[:block_bytes]
                        buf = buf[block_bytes:]
                        self._process_pcm_s16le(data)

            except Exception as e:
                self.connected = False
                self.last_error = str(e)
                if not self.stop_flag.is_set():
                    print(f"[Pulse/parec] Fehler: {e} â€“ Retry in {backoff:.1f}s", flush=True)
                    time.sleep(backoff)
                    backoff = min(8.0, backoff * 1.5)
            finally:
                if self.proc:
                    try:
                        self.proc.terminate()
                        self.proc.wait(timeout=0.8)
                    except Exception:
                        try:
                            self.proc.kill()
                        except Exception:
                            pass
                    self.proc = None
                self.connected = False

    def _process_pcm_s16le(self, data: bytes):
        n_samples = len(data) // 2
        if n_samples < 8:
            return
        try:
            samples = struct.unpack(f"<{n_samples}h", data)
        except Exception:
            return

        if self.channels == 2:
            mono = [(samples[i] + samples[i + 1]) * 0.5 for i in range(0, n_samples - 1, 2)]
        else:
            mono = list(samples)

        if len(mono) < 4:
            return

        # RMS
        rms = (sum(x * x for x in mono) / len(mono)) ** 0.5 / 32768.0
        # Highs
        diffs = [abs(mono[i] - mono[i-1]) for i in range(1, len(mono))]
        high = (sum(diffs) / len(diffs)) / 32768.0 if diffs else 0.0

        self._update_dsp(rms * 6.0, high * 8.0, rms * 4.5, high * 9.0)

    def _update_dsp(self, raw_bass, raw_snare, raw_mids, raw_highs):
        now = time.monotonic()
        with self.lock:
            dt = max(0.008, min(0.15, now - self.last_update)) if self.last_update else 0.03
            self.last_update = now

            alpha = 0.04
            self.hist_bass = max(0.008, (1 - alpha) * self.hist_bass + alpha * raw_bass)
            self.hist_snare = max(0.008, (1 - alpha) * self.hist_snare + alpha * raw_snare)

            nb = min(1.0, (raw_bass / (self.hist_bass * 1.4)) * self.sensitivity)
            ns = min(1.0, (raw_snare / (self.hist_snare * 1.5)) * self.sensitivity)
            nm = min(1.0, raw_mids * self.sensitivity * 0.9)
            nh = min(1.0, raw_highs * self.sensitivity * 0.9)

            self.bass  = nb if nb > self.bass  else self.bass  * math.exp(-dt * 6.0)
            self.snare = ns if ns > self.snare else self.snare * math.exp(-dt * 8.0)
            self.mids  = nm if nm > self.mids  else self.mids  * math.exp(-dt * 5.5)
            self.highs = nh if nh > self.highs else self.highs * math.exp(-dt * 9.0)

            bass_flux = raw_bass - self.prev_bass_raw
            self.prev_bass_raw = raw_bass
            if bass_flux > self.hist_bass * 0.25 and nb > 0.35 and (now - self.last_kick_t) > 0.15:
                if self.last_kick_t > 0:
                    interval = now - self.last_kick_t
                    if 0.25 < interval < 1.3:
                        self.bpm_est = 0.8 * self.bpm_est + 0.2 * (60.0 / interval)
                self.last_kick_t = now
                self.kick_env = 1.0
                self.beat_count += 1
            else:
                self.kick_env *= math.exp(-dt * 10.0)

            snare_flux = raw_snare - self.prev_snare_raw
            self.prev_snare_raw = raw_snare
            if snare_flux > self.hist_snare * 0.3 and ns > 0.35:
                self.snare_env = 1.0
            else:
                self.snare_env *= math.exp(-dt * 12.0)

    def get_features(self):
        now = time.monotonic()
        with self.lock:
            alive = self.connected and ((now - self.last_update) < 1.5)
            if not alive:
                return {
                    "alive": False, "connected": self.connected,
                    "bass": 0.0, "snare": 0.0, "mids": 0.0, "highs": 0.0,
                    "kick_env": 0.0, "snare_env": 0.0,
                    "beat_count": self.beat_count, "bpm": 0,
                    "error": self.last_error,
                }
            return {
                "alive": True, "connected": self.connected,
                "bass": round(self.bass, 3), "snare": round(self.snare, 3),
                "mids": round(self.mids, 3), "highs": round(self.highs, 3),
                "kick_env": round(self.kick_env, 3), "snare_env": round(self.snare_env, 3),
                "beat_count": self.beat_count, "bpm": round(self.bpm_est),
                "error": None,
            }


# ---------------------------------------------------------------------------
# Protokoll-Parser & Gruppen-Logik
# ---------------------------------------------------------------------------
def parse_e131(data):
    if len(data) < 126 or data[4:16] != b"ASC-E1.17\x00\x00\x00":
        return None
    if struct.unpack_from(">I", data, 18)[0] != 0x00000004 or data[117] != 0x02 or data[118] != 0xA1:
        return None
    universe = struct.unpack_from(">H", data, 113)[0]
    count = struct.unpack_from(">H", data, 123)[0]
    if data[125] != 0x00:
        return None
    return universe, data[126:126 + count - 1]


def parse_ddp(data):
    if len(data) < 10 or (data[0] & 0xC0 != 0x40):
        return None
    offset = struct.unpack_from(">I", data, 4)[0]
    length = struct.unpack_from(">H", data, 8)[0]
    payload = data[10:10 + length]
    return (offset, payload) if len(payload) >= length else None


def parse_wled_udp(data):
    if len(data) < 2:
        return None
    p = data[0]
    if p == 1:
        out = {}
        for i in range(2, len(data) - 3, 4):
            out[data[i]] = (data[i+1], data[i+2], data[i+3])
        return ("warls", out)
    elif p == 2:
        return ("drgb", data[2:])
    elif p == 4 and len(data) >= 4:
        start = (data[2] << 8) | data[3]
        return ("dnrgb", (start, data[4:]))
    elif p == 0 and len(data) >= 12:
        bri = data[2] / 255.0
        return ("sync", (int(data[3]*bri), int(data[4]*bri), int(data[5]*bri)))
    return None


class GroupState:
    def __init__(self, cfg):
        self.cfg = cfg
        self.id = cfg["id"]
        self.name = cfg.get("name", "Gruppe")
        self.enabled = bool(cfg.get("enabled", True))
        self.use_radar = bool(cfg.get("use_radar", False))

        self.lamp_list = cfg.get("lamps", [])
        self.names = [l["name"].strip() for l in self.lamp_list if l.get("name", "").strip()]
        self.lamps = {}
        for l in self.lamp_list:
            n = l.get("name", "").strip()
            a = str(l.get("addr", "")).strip()
            if n and a:
                try:
                    self.lamps[n] = int(a, 0)
                except ValueError:
                    pass

        self.proxy = (cfg.get("proxy") or "").strip() or (self.names[0] if self.names else "")
        p_addr = str(cfg.get("proxy_addr") or "").strip()
        if self.proxy and p_addr:
            try:
                self.lamps[self.proxy] = int(p_addr, 0)
            except ValueError:
                pass

        self.n = len(self.names)
        self.pixels = [(0, 0, 0)] * max(1, self.n)
        self.lock = threading.Lock()

        self.fps = float(cfg.get("fps", 25.0))
        self.max_brightness = int(cfg.get("max_brightness", 2047))
        self.brightness_mode = cfg.get("brightness_mode", "max")
        self.idle_timeout = float(cfg.get("idle_timeout", 3.0))

        self.e131_enabled = bool(cfg.get("e131_enabled", True))
        self.e131_universe = int(cfg.get("e131_universe", 1))
        self.ddp_enabled = bool(cfg.get("ddp_enabled", True))
        self.ddp_port = int(cfg.get("ddp_port", 4048))
        self.raw_enabled = bool(cfg.get("raw_enabled", True))
        self.raw_port = int(cfg.get("raw_port", 2391))
        self.wled_udp_enabled = bool(cfg.get("wled_udp_enabled", True))
        self.wled_udp_port = int(cfg.get("wled_udp_port", 21324))

        wled_cfg = cfg.get("wled_state", {})
        self.wled_on = bool(wled_cfg.get("on", False))
        self.wled_bri = int(wled_cfg.get("bri", 255))
        self.wled_effect = wled_cfg.get("effect", "club_tech")
        self.wled_palette = wled_cfg.get("palette", "cyberpunk")
        self.wled_color = tuple(wled_cfg.get("color", [255, 0, 85]))
        self.wled_color2 = tuple(wled_cfg.get("color2", [0, 240, 255]))
        self.bass_boost = float(wled_cfg.get("bass_boost", 1.3))
        self.snare_flash = float(wled_cfg.get("snare_flash", 1.0))

        self.scanner_pos = 0.0
        self.scanner_dir = 1.0
        self.last_beat_seen = -1
        self.strobe_idx = 0

        self.last_rx = 0.0
        self.rx_stats = {"e131": 0, "ddp": 0, "raw": 0, "wled": 0, "bad": 0}
        self.rx_thread = None
        self.stop_flag = threading.Event()

    def start_rx(self):
        if not self.enabled or self.n == 0:
            return
        self.stop_flag.clear()
        self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.rx_thread.start()

    def stop_rx(self):
        self.stop_flag.set()
        if self.rx_thread:
            self.rx_thread.join(timeout=1.0)
            self.rx_thread = None

    def _apply_rgb(self, offset_px, payload, proto):
        n_px = len(payload) // 3
        if n_px == 0:
            return
        with self.lock:
            for i in range(n_px):
                idx = offset_px + i
                if idx >= self.n:
                    break
                self.pixels[idx] = (payload[3 * i], payload[3 * i + 1], payload[3 * i + 2])
            self.last_rx = time.monotonic()
        self.rx_stats[proto] += 1

    def handle_e131(self, uni, dmx):
        if not self.enabled or not self.e131_enabled:
            return
        max_u = self.e131_universe + max(1, (3 * self.n + 509) // 510)
        if self.e131_universe <= uni < max_u:
            ch0 = (uni - self.e131_universe) * 510
            px0, skip = divmod(ch0, 3)
            self._apply_rgb(px0, dmx[skip:], "e131")

    def _rx_loop(self):
        socks = []
        for proto, enabled, port in [
            ("ddp", self.ddp_enabled, self.ddp_port),
            ("raw", self.raw_enabled, self.raw_port),
            ("wled", self.wled_udp_enabled, self.wled_udp_port),
        ]:
            if enabled:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("0.0.0.0", port))
                    s.setblocking(False)
                    socks.append((proto, s))
                except OSError as e:
                    print(f"[{self.name}] Port {port} belegt: {e}", flush=True)

        while not self.stop_flag.is_set():
            if not socks:
                self.stop_flag.wait(0.5)
                continue
            try:
                ready, _, _ = select.select([s for _, s in socks], [], [], 0.2)
            except Exception:
                continue
            for s in ready:
                proto = next(p for p, x in socks if x is s)
                try:
                    data, _ = s.recvfrom(65535)
                except OSError:
                    continue
                try:
                    if proto == "ddp":
                        parsed = parse_ddp(data)
                        if parsed:
                            self._apply_rgb(parsed[0] // 3, parsed[1], "ddp")
                    elif proto == "raw":
                        self._apply_rgb(0, data[: 3 * self.n], "raw")
                    elif proto == "wled":
                        res = parse_wled_udp(data)
                        if res:
                            wtype, wdata = res
                            if wtype == "drgb":
                                self._apply_rgb(0, wdata[: 3 * self.n], "wled")
                            elif wtype == "dnrgb":
                                self._apply_rgb(wdata[0], wdata[1], "wled")
                            elif wtype == "warls":
                                with self.lock:
                                    for idx, rgb in wdata.items():
                                        if idx < self.n:
                                            self.pixels[idx] = rgb
                                    self.last_rx = time.monotonic()
                                self.rx_stats["wled"] += 1
                            elif wtype == "sync":
                                with self.lock:
                                    for i in range(self.n):
                                        self.pixels[i] = wdata
                                    self.last_rx = time.monotonic()
                                self.rx_stats["wled"] += 1
                except Exception:
                    self.rx_stats["bad"] += 1

        for _, s in socks:
            s.close()

    def _render_music_effect(self, now, af):
        scale = self.wled_bri / 255.0
        pal = PALETTES.get(self.wled_palette, PALETTES["cyberpunk"])
        c_kick = self.wled_color
        c_snare = self.wled_color2
        c_high = pal[2]

        bass = min(1.0, af["bass"] * self.bass_boost)
        kick = min(1.0, af["kick_env"] * self.bass_boost)
        snare = min(1.0, af["snare_env"] * self.snare_flash)
        mids = af["mids"]
        highs = af["highs"]
        b_cnt = af["beat_count"]

        fx = self.wled_effect
        px = [(0, 0, 0)] * self.n

        if fx == "club_tech":
            center = (self.n - 1) / 2.0
            for i in range(self.n):
                is_anchor = (i == 0 or i == self.n - 1 or abs(i - center) < 0.6)
                if is_anchor:
                    lvl = max(kick, bass ** 1.6) * scale
                    col = scale_color(c_kick, lvl)
                else:
                    lvl = max(snare, (mids * 0.7) ** 1.8) * scale
                    col = scale_color(c_snare, lvl)
                if highs > 0.45 and ((i + b_cnt) % 2 == 0):
                    col = add_color(col, scale_color(c_high, (highs - 0.4) * 0.8 * scale))
                px[i] = col

        elif fx == "bass_cannon":
            center = (self.n - 1) / 2.0
            max_dist = max(1.0, center)
            reach = max(kick, bass) * (max_dist + 0.6)
            for i in range(self.n):
                dist = abs(i - center)
                if dist <= reach:
                    intensity = max(0.0, min(1.0, (reach - dist) * 1.4)) * scale
                    base_c = lerp_color(c_kick, c_snare, dist / max_dist)
                    px[i] = scale_color(base_c, intensity)
                else:
                    if snare > 0.35 and (i == 0 or i == self.n - 1):
                        px[i] = scale_color(c_high, snare * scale)
                    else:
                        px[i] = scale_color(c_kick, highs * 0.12 * scale)

        elif fx == "band_matrix":
            for i in range(self.n):
                pos = i / max(1, self.n - 1) if self.n > 1 else 0.0
                if pos < 0.34:
                    v = max(kick, bass ** 1.4) * scale
                    px[i] = scale_color(c_kick, v)
                elif pos < 0.67:
                    v = max(snare, mids ** 1.3) * scale
                    px[i] = scale_color(c_snare, v)
                else:
                    v = (highs ** 1.2) * scale
                    px[i] = scale_color(c_high, v)

        elif fx == "ping_pong":
            if b_cnt != self.last_beat_seen:
                self.last_beat_seen = b_cnt
                self.scanner_dir *= -1.0
            speed = (0.08 + bass * 0.35) * self.scanner_dir
            self.scanner_pos = (self.scanner_pos + speed) % max(1, self.n)
            active_c = pal[b_cnt % len(pal)]
            for i in range(self.n):
                d = abs(i - self.scanner_pos)
                d = min(d, self.n - d)
                v = max(0.0, 1.0 - d * 1.15) ** 2.0
                col = scale_color(active_c, v * scale)
                if snare > 0.5:
                    col = add_color(col, scale_color(c_snare, snare * 0.7 * scale))
                px[i] = col

        elif fx == "ambient_uplight":
            for i in range(self.n):
                phase_offset = (i % 2) * 0.25
                base_glow = max(0.12, (bass ** 1.3))
                c_base = lerp_color(c_kick, c_high, min(1.0, mids + phase_offset))
                col = scale_color(c_base, base_glow * scale)
                if snare > 0.25 and (i % 2 == (b_cnt % 2)):
                    col = lerp_color(col, scale_color(c_snare, scale), snare)
                px[i] = col

        elif fx == "strobe_drop":
            if b_cnt != self.last_beat_seen:
                self.last_beat_seen = b_cnt
                self.strobe_idx = (self.strobe_idx + random.randint(1, max(1, self.n - 1))) % max(1, self.n)
            for i in range(self.n):
                if i == self.strobe_idx and kick > 0.25:
                    px[i] = scale_color(c_kick, kick * scale)
                elif snare > 0.45 and ((i + b_cnt) % 2 == 1):
                    px[i] = scale_color(c_snare, snare * scale)
                else:
                    px[i] = scale_color(c_high, highs * 0.15 * scale)

        elif fx == "rainbow":
            px = [tuple(int(c * 255) for c in colorsys.hsv_to_rgb((now * 0.25 + i / max(1, self.n)) % 1.0, 1.0, scale)) for i in range(self.n)]
        elif fx == "breathe":
            f = (math.sin(now * 2.5) + 1.0) / 2.0 * scale
            px = [scale_color(c_kick, f)] * self.n
        elif fx == "chase":
            idx = int(now * 4.0) % max(1, self.n)
            px = [scale_color(c_kick, scale if i == idx else scale * 0.08) for i in range(self.n)]
        else:
            px = [scale_color(c_kick, scale)] * self.n

        return px

    def compute_pixels(self, now, radar_obj, audio_features):
        udp_fresh = (self.last_rx != 0.0 and (now - self.last_rx) <= self.idle_timeout)
        active_stream = udp_fresh or self.wled_on

        radar_active = False
        if self.use_radar and radar_obj is not None:
            radar_obj.set_gateway_active(active_stream)
            radar_active = radar_obj.enable and (radar_obj.force or radar_obj.active)

        if not active_stream and not radar_active:
            return None, False

        if udp_fresh:
            with self.lock:
                px = list(self.pixels)
        elif self.wled_on:
            px = self._render_music_effect(now, audio_features)
        else:
            px = [(0, 0, 0)] * self.n

        recs = []
        ui_px = []
        for i, name in enumerate(self.names):
            if name not in self.lamps:
                continue
            r, g, b = px[i] if i < len(px) else (0, 0, 0)
            if self.use_radar and radar_obj is not None:
                ox, oy, olvl, override = radar_obj.override_xy(name)
                if override:
                    bri = int(olvl * self.max_brightness)
                    recs.append(record(self.lamps[name], bri, ox, oy, MODE_DEVICE))
                    ui_px.append((int(255 * olvl), int(180 * olvl), int(90 * olvl)))
                    continue
                r, g, b, _ = radar_obj.blend(name, r, g, b)

            ui_px.append((r, g, b))
            x, y, bri = rgb_to_xy_bri(r, g, b, self.max_brightness, self.brightness_mode)
            recs.append(record(self.lamps[name], bri, x, y, MODE_DEVICE))

        with self.lock:
            self.pixels = ui_px
        return recs, True


# ---------------------------------------------------------------------------
# Gateway Manager
# ---------------------------------------------------------------------------
class GatewayManager:
    def __init__(self, cli_args):
        self.cli_args = cli_args
        self.config = self.load_or_init_config()

        pulse_device = self.config.get("pulse_device", "LedFx_Audio.monitor")
        self.audio = PulseAudioEngine(device=pulse_device)
        self.audio.start()

        self.groups = {}
        self.z2m_devices = []
        self.st = None
        self.radar = None
        self.streaming = False
        self.counter = int(time.time()) & 0xFFFF
        self.all_lamps = {}
        self.primary_proxy = None
        self.tx_thread = None
        self.stop_flag = threading.Event()
        self.e131_stop = threading.Event()
        self.e131_thread = None
        self.stats = {"fps": 0.0, "us": 0}

    def load_or_init_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        cfg = {
            "mqtt": {
                "host": self.cli_args.host,
                "port": self.cli_args.port,
                "username": self.cli_args.username,
                "password": self.cli_args.password,
                "base_topic": self.cli_args.base_topic,
            },
            "pulse_device": "LedFx_Audio.monitor",
            "radar_global": {
                "enabled": True,
                "host": "192.168.10.114",
                "key": "4jUS9ydOye/yzw6IezgCf8fWCT0yVHc0Q0Sb0o4/mx8=",
                "mode": "automatic",
                "mqtt": True,
                "ha_topic": "homeassistant",
                "low_hex": "#241000",
                "high_hex": "#FFD9A0",
                "fps": 20.0,
                "amount": 1.0,
                "bloom_mm": 800.0
            },
            "groups": []
        }
        self.save_config(cfg)
        return cfg

    def save_config(self, cfg=None):
        if cfg is not None:
            self.config = cfg
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    def discover_z2m_devices(self):
        mcfg = self.config["mqtt"]
        st_args = argparse.Namespace(
            host=mcfg.get("host", "192.168.10.13"),
            port=int(mcfg.get("port", 1883)),
            username=mcfg.get("username") or None,
            password=mcfg.get("password") or None,
            base_topic=mcfg.get("base_topic", "zigbee2mqtt"),
            lamps=["dummy"], proxy="dummy", fps=20.0, no_balance=False,
        )
        found = []
        ev = threading.Event()
        base = st_args.base_topic
        try:
            temp_st = Z2mEntStreamer(st_args)
            temp_st.connect()
            def _on_msg(client, userdata, msg):
                if msg.topic == f"{base}/bridge/devices":
                    try:
                        devs = json.loads(msg.payload.decode("utf-8"))
                        for d in devs:
                            if d.get("type") in ("Router", "EndDevice") and d.get("friendly_name"):
                                naddr = d.get("network_address")
                                found.append({
                                    "friendly_name": d["friendly_name"],
                                    "network_address": f"0x{naddr:04X}" if isinstance(naddr, int) else "",
                                    "model": (d.get("definition") or {}).get("model", ""),
                                })
                        ev.set()
                    except Exception:
                        pass
            temp_st.client.message_callback_add(f"{base}/bridge/devices", _on_msg)
            temp_st.client.subscribe(f"{base}/bridge/devices")
            temp_st.client.publish(f"{base}/bridge/request/devices", "")
            ev.wait(2.0)
            temp_st.client.loop_stop()
            temp_st.client.disconnect()
        except Exception:
            pass
        if found:
            self.z2m_devices = sorted(found, key=lambda x: x["friendly_name"].lower())
        return self.z2m_devices

    def start_all(self):
        self.stop_all()
        self.groups = {gcfg["id"]: GroupState(gcfg) for gcfg in self.config.get("groups", [])}
        for g in self.groups.values():
            g.start_rx()

        self.all_lamps = {}
        for g in self.groups.values():
            self.all_lamps.update(g.lamps)
            if not self.primary_proxy and g.proxy:
                self.primary_proxy = g.proxy

        if not self.primary_proxy and self.all_lamps:
            self.primary_proxy = next(iter(self.all_lamps.keys()))

        mcfg = self.config["mqtt"]
        st_args = argparse.Namespace(
            host=mcfg.get("host", "192.168.10.13"),
            port=int(mcfg.get("port", 1883)),
            username=mcfg.get("username") or None,
            password=mcfg.get("password") or None,
            base_topic=mcfg.get("base_topic", "zigbee2mqtt"),
            lamps=list(self.all_lamps.keys()),
            proxy=self.primary_proxy,
            fps=25.0,
            no_balance=False,
        )
        try:
            self.st = Z2mEntStreamer(st_args)
            self.st.connect()
            missing = [n for n, a in self.all_lamps.items() if not a or a == 0]
            if missing:
                try:
                    resolved = self.st.resolve(missing)
                    self.all_lamps.update(resolved)
                    for g in self.groups.values():
                        for l in g.lamp_list:
                            if l["name"] in resolved:
                                l["addr"] = f"0x{resolved[l['name']]:04X}"
                        g.lamps.update({k: v for k, v in resolved.items() if k in g.names})
                except SystemExit:
                    print("[Z2M] Warnung: resolve() Timeout, nutze vorhandene Adressen.", flush=True)
            else:
                print(f"[Z2M] Alle {len(self.all_lamps)} Adressen geladen.", flush=True)
        except Exception as e:
            print(f"[Z2M] Verbindungsfehler: {e}", flush=True)

        rcfg = self.config.get("radar_global", {})
        if HAS_RADAR and rcfg.get("enabled") and rcfg.get("host") and rcfg.get("key"):
            try:
                ap = argparse.ArgumentParser()
                _rl.add_cli_args(ap)
                cli_list = [
                    "--radar-layer", "--radar-host", str(rcfg["host"]), "--radar-key", str(rcfg["key"]),
                    "--radar-mode", str(rcfg.get("mode", "automatic")),
                    "--radar-low-hex", str(rcfg.get("low_hex", "#241000")),
                    "--radar-high-hex", str(rcfg.get("high_hex", "#FFD9A0")),
                ]
                if rcfg.get("mqtt", True):
                    cli_list.append("--radar-mqtt")
                if rcfg.get("ha_topic"):
                    cli_list.extend(["--radar-ha-topic", str(rcfg["ha_topic"])])
                parsed, _ = ap.parse_known_args(cli_list)
                parsed.fps = 20.0
                rargs = _rl.args_from_cli(parsed)
                self.radar = _rl.RadarLayer(rargs, list(self.all_lamps.keys()), self.all_lamps)
                if parsed.radar_mqtt and self.st:
                    self.radar.setup_mqtt(self.st, parsed.radar_ha_topic)
                if self.st:
                    self.radar.start(self.st)
            except Exception as e:
                print(f"[Radar] Init-Fehler: {e}", flush=True)

        self.stop_flag.clear()
        self.tx_thread = threading.Thread(target=self._tx_loop, daemon=True)
        self.tx_thread.start()
        self.start_e131()

    def stop_all(self):
        self.stop_flag.set()
        if self.audio:
            self.audio.stop()
        if self.tx_thread:
            self.tx_thread.join(timeout=1.5)
            self.tx_thread = None
        self.e131_stop.set()
        if self.e131_thread:
            self.e131_thread.join(timeout=1.5)
            self.e131_thread = None
        for g in self.groups.values():
            g.stop_rx()
        self.groups.clear()
        if self.streaming and self.st:
            try:
                self.st.stop(self.all_lamps, self.counter)
            except Exception:
                pass
            self.streaming = False
        if self.st:
            try:
                self.st.client.loop_stop()
                self.st.client.disconnect()
            except Exception:
                pass
            self.st = None
        self.radar = None

    def start_e131(self):
        self.e131_stop.clear()
        unis = {g.e131_universe for g in self.groups.values() if g.enabled and g.e131_enabled}
        if not unis:
            return

        def _loop():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", E131_PORT))
                for u in unis:
                    grp = socket.inet_aton(f"239.255.0.{u & 0xFF}") + socket.inet_aton("0.0.0.0")
                    try:
                        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, grp)
                    except OSError:
                        pass
                s.setblocking(False)
            except Exception:
                return

            while not self.e131_stop.is_set():
                try:
                    ready, _, _ = select.select([s], [], [], 0.2)
                except Exception:
                    continue
                if ready:
                    try:
                        data, _ = s.recvfrom(65535)
                        parsed = parse_e131(data)
                        if parsed:
                            uni, dmx = parsed
                            for g in self.groups.values():
                                g.handle_e131(uni, dmx)
                    except Exception:
                        pass
            s.close()

        self.e131_thread = threading.Thread(target=_loop, daemon=True)
        self.e131_thread.start()

    def _tx_loop(self):
        target_fps = max([g.fps for g in self.groups.values()] or [25.0])
        period = 1.0 / max(1.0, target_fps)
        smoothing = smoothing_for_fps(target_fps)
        next_tick = time.monotonic()
        frames = 0
        t_stat = time.monotonic()
        tx_sum = 0.0
        tx_n = 0

        while not self.stop_flag.is_set():
            now = time.monotonic()
            af = self.audio.get_features()
            combined_recs = []
            any_active = False

            for g in self.groups.values():
                if not g.enabled:
                    continue
                recs, active = g.compute_pixels(now, self.radar, af)
                if active and recs:
                    any_active = True
                    combined_recs.extend(recs)

            if not any_active and self.streaming:
                try:
                    self.st.stop(self.all_lamps, self.counter)
                except Exception:
                    pass
                self.streaming = False
                print("[Gateway] >> Standby (Lampen freigegeben)", flush=True)

            elif any_active and not self.streaming:
                try:
                    self.st.setup(self.all_lamps, self.counter, do_balance=True)
                    self.streaming = True
                    print(f"[Gateway] >> Session gestartet ({len(self.all_lamps)} Lampen via '{self.primary_proxy}')", flush=True)
                except Exception as e:
                    print(f"[Gateway] Setup Fehler: {e}", flush=True)

            if self.streaming and combined_recs and self.st and self.primary_proxy:
                t0 = time.monotonic()
                chunk1 = combined_recs[:10]
                payload1 = frame_payload(self.counter, smoothing, chunk1)
                self.st.send(self.primary_proxy, zclcommand_msg(CMD_FRAME, payload1))
                self.counter = (self.counter + 1) & 0xFFFFFFFF

                if len(combined_recs) > 10:
                    chunk2 = combined_recs[10:20]
                    sec_proxy = next((g.proxy for g in self.groups.values() if g.proxy and g.proxy != self.primary_proxy), self.primary_proxy)
                    payload2 = frame_payload(self.counter, smoothing, chunk2)
                    self.st.send(sec_proxy, zclcommand_msg(CMD_FRAME, payload2))
                    self.counter = (self.counter + 1) & 0xFFFFFFFF

                frames += 1
                tx_sum += time.monotonic() - t0
                tx_n += 1

            if now - t_stat >= 2.0:
                self.stats["fps"] = round(frames / (now - t_stat), 1) if frames > 0 else 0.0
                self.stats["us"] = round((tx_sum / tx_n) * 1e6) if tx_n > 0 else 0
                frames, t_stat, tx_sum, tx_n = 0, now, 0.0, 0

            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                self.stop_flag.wait(delay)
            else:
                next_tick = time.monotonic()


# ---------------------------------------------------------------------------
# Web-OberflÃ¤che
# ---------------------------------------------------------------------------
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="de" class="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hue Multi-Group Gateway & PulseAudio DSP</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body { background-color: #090d16; color: #e2e8f0; font-family: system-ui, -apple-system, sans-serif; }
  .card { background-color: #131b2e; border: 1px solid #23304d; border-radius: 0.75rem; }
  .pixel-dot { width: 24px; height: 24px; border-radius: 50%; display: inline-block; border: 2px solid #334155; transition: background-color 0.04s linear, box-shadow 0.04s linear; }
  input, select { background-color: #0b111e !important; border: 1px solid #2e3d5c !important; color: #f8fafc !important; border-radius: 0.375rem; padding: 0.35rem 0.5rem; }
  .bar-bg { background-color: #0b111e; border-radius: 0.25rem; overflow: hidden; height: 10px; }
  .bar-fill { height: 100%; transition: width 0.05s linear; }
</style>
</head>
<body class="min-h-screen p-4 md:p-6">
<div class="max-w-7xl mx-auto space-y-5">

  <div class="card p-4 space-y-4">
    <div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
      <div>
        <h1 class="text-xl font-bold text-white flex items-center gap-2">
          <span id="snap-dot" class="w-3 h-3 rounded-full bg-rose-500"></span>
          Hue Gateway & PulseAudio DSP
        </h1>
        <p class="text-xs text-slate-400">Lokaler Pulse-Monitor (LedFx_Audio.monitor)</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button onclick="scanZ2M()" class="px-3 py-2 bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold rounded-lg">ðŸ” Z2M Scan</button>
        <button onclick="addGroup()" class="px-3 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-lg">+ Gruppe</button>
        <button onclick="saveAll()" class="px-4 py-2 bg-amber-500 hover:bg-amber-400 text-slate-950 text-xs font-bold rounded-lg">ðŸ’¾ Speichern</button>
      </div>
    </div>

    <div class="grid grid-cols-2 md:grid-cols-6 gap-3 pt-2 border-t border-slate-800 text-xs items-center">
      <div>
        <div class="flex justify-between text-rose-400 font-semibold mb-1"><span>SUB-BASS</span><span id="val-bass">0%</span></div>
        <div class="bar-bg"><div id="bar-bass" class="bar-fill bg-rose-500" style="width:0%"></div></div>
      </div>
      <div>
        <div class="flex justify-between text-amber-400 font-semibold mb-1"><span>SNARE</span><span id="val-snare">0%</span></div>
        <div class="bar-bg"><div id="bar-snare" class="bar-fill bg-amber-400" style="width:0%"></div></div>
      </div>
      <div>
        <div class="flex justify-between text-emerald-400 font-semibold mb-1"><span>MIDS</span><span id="val-mids">0%</span></div>
        <div class="bar-bg"><div id="bar-mids" class="bar-fill bg-emerald-400" style="width:0%"></div></div>
      </div>
      <div>
        <div class="flex justify-between text-sky-400 font-semibold mb-1"><span>HIGHS</span><span id="val-highs">0%</span></div>
        <div class="bar-bg"><div id="bar-highs" class="bar-fill bg-sky-400" style="width:0%"></div></div>
      </div>
      <div class="flex items-center justify-center gap-2">
        <span id="pill-kick" class="px-2.5 py-1 rounded font-bold bg-slate-800 text-slate-500">KICK</span>
        <span id="pill-snare" class="px-2.5 py-1 rounded font-bold bg-slate-800 text-slate-500">SNARE</span>
        <span id="lbl-bpm" class="font-mono text-slate-400">-- BPM</span>
      </div>
      <div>
        <label class="block text-slate-400 mb-0.5">Empfindlichkeit (<span id="lbl-sens">1.3x</span>)</label>
        <input id="audio-sens" type="range" min="0.4" max="3.0" step="0.1" value="1.3" oninput="document.getElementById('lbl-sens').innerText=this.value+'x'" class="w-full p-0">
      </div>
    </div>
  </div>

  <div id="groups-container" class="space-y-5"></div>
</div>

<script>
let cfg = { groups: [] };
let z2mDevices = [];

async function init() {
  const res = await fetch('/api/config');
  const d = await res.json();
  cfg = d.config;
  z2mDevices = d.z2m_devices || [];
  render();
}

function render() {
  const container = document.getElementById('groups-container');
  container.innerHTML = cfg.groups.map((g, gIdx) => {
    const w = g.wled_state || {};
    const c1 = '#' + (w.color || [255,0,85]).map(x => Number(x).toString(16).padStart(2,'0')).join('');
    const c2 = '#' + (w.color2 || [0,240,255]).map(x => Number(x).toString(16).padStart(2,'0')).join('');
    const z2mOpts = z2mDevices.map(d => `<option value="${d.friendly_name}|${d.network_address}">${d.friendly_name} (${d.network_address})</option>`).join('');

    const lampRows = (g.lamps || []).map((l, lIdx) => {
      const isProxy = (g.proxy === l.name);
      return `
      <div class="flex items-center gap-2 bg-slate-900/90 p-1.5 rounded border ${isProxy ? 'border-sky-500' : 'border-slate-800'} text-xs">
        <span class="w-4 text-center font-mono text-slate-500">${lIdx}</span>
        <span id="px-${g.id}-${lIdx}" class="pixel-dot flex-shrink-0" style="background:#000"></span>
        <input value="${l.name}" onchange="cfg.groups[${gIdx}].lamps[${lIdx}].name=this.value" class="flex-1 text-xs">
        <input value="${l.addr || ''}" onchange="cfg.groups[${gIdx}].lamps[${lIdx}].addr=this.value" class="w-16 font-mono text-xs">
        <button onclick="setProxy(${gIdx},${lIdx})" class="px-2 py-0.5 rounded text-[11px] ${isProxy ? 'bg-sky-600 text-white font-bold' : 'bg-slate-800 text-slate-400'}">Proxy</button>
        <button onclick="moveLamp(${gIdx},${lIdx},-1)" class="px-1.5 py-0.5 bg-slate-800 rounded">â†‘</button>
        <button onclick="moveLamp(${gIdx},${lIdx},1)" class="px-1.5 py-0.5 bg-slate-800 rounded">â†“</button>
        <button onclick="delLamp(${gIdx},${lIdx})" class="px-2 py-0.5 bg-rose-950 text-rose-300 rounded text-[11px]">âœ•</button>
      </div>`;
    }).join('');

    return `
    <div class="card p-4 space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
        <div class="flex items-center gap-3">
          <input type="checkbox" ${g.enabled ? 'checked' : ''} onchange="cfg.groups[${gIdx}].enabled=this.checked" class="w-4 h-4">
          <input value="${g.name}" onchange="cfg.groups[${gIdx}].name=this.value" class="text-base font-bold bg-transparent border-none w-64">
          <span id="badge-${g.id}" class="px-2 py-0.5 text-xs font-semibold rounded bg-slate-800 text-slate-400">Standby</span>
        </div>
        <div class="flex items-center gap-3 text-xs font-mono text-slate-400">
          <span id="stats-${g.id}">DDP:0 E1.31:0</span>
          <button onclick="delGroup(${gIdx})" class="px-2 py-1 bg-rose-950 text-rose-300 rounded font-sans">Gruppe lÃ¶schen</button>
        </div>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-12 gap-5 text-xs">
        <div class="lg:col-span-5 space-y-2">
          <div class="flex justify-between text-slate-400">
            <span>Spots (${(g.lamps||[]).length})</span>
            <span>Proxy: <strong class="text-sky-400">${g.proxy || 'Auto'}</strong></span>
          </div>
          <div class="space-y-1">${lampRows || '<div class="text-slate-500 py-2">Keine Spots zugeordnet.</div>'}</div>
          <div class="flex gap-1 pt-1">
            <select id="z2m-sel-${gIdx}" class="flex-1 text-xs"><option value="">-- Lampe aus Z2M wÃ¤hlen --</option>${z2mOpts}</select>
            <button onclick="addFromZ2M(${gIdx})" class="px-2.5 py-1 bg-sky-600 text-white font-semibold rounded">+ Spot</button>
          </div>
        </div>

        <div class="lg:col-span-3 space-y-2 border-t lg:border-t-0 lg:border-l border-slate-800 pt-3 lg:pt-0 lg:pl-4">
          <div class="font-semibold text-slate-300">Protokolle & Timing</div>
          <div class="grid grid-cols-2 gap-2">
            <div><label class="text-slate-400">DDP Port</label><input type="number" value="${g.ddp_port}" onchange="cfg.groups[${gIdx}].ddp_port=parseInt(this.value)" class="w-full font-mono"></div>
            <div><label class="text-slate-400">E1.31 Uni</label><input type="number" value="${g.e131_universe}" onchange="cfg.groups[${gIdx}].e131_universe=parseInt(this.value)" class="w-full font-mono"></div>
            <div><label class="text-slate-400">FPS</label><input type="number" value="${g.fps||25}" onchange="cfg.groups[${gIdx}].fps=parseFloat(this.value)" class="w-full"></div>
            <div>
              <label class="text-slate-400">Bri-Modus</label>
              <select onchange="cfg.groups[${gIdx}].brightness_mode=this.value" class="w-full">
                <option value="max" ${g.brightness_mode==='max'?'selected':''}>max</option>
                <option value="luminance" ${g.brightness_mode==='luminance'?'selected':''}>luminance</option>
              </select>
            </div>
          </div>
          <label class="flex items-center gap-1.5 text-slate-300 pt-1">
            <input type="checkbox" ${g.use_radar ? 'checked' : ''} onchange="cfg.groups[${gIdx}].use_radar=this.checked">
            Radar-Overlay zulassen
          </label>
        </div>

        <div class="lg:col-span-4 space-y-2.5 border-t lg:border-t-0 lg:border-l border-slate-800 pt-3 lg:pt-0 lg:pl-4">
          <div class="flex items-center justify-between">
            <span class="font-bold text-fuchsia-400">ðŸ”¥ PulseAudio Musik-Engine</span>
            <label class="flex items-center gap-1.5 cursor-pointer">
              <input type="checkbox" id="wled-on-${g.id}" ${w.on ? 'checked' : ''} onchange="triggerFx('${g.id}',${gIdx})" class="w-4 h-4">
              <span class="font-bold text-white">Aktiv</span>
            </label>
          </div>

          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="text-slate-400 block mb-0.5">Effekt / Algorithmus</label>
              <select id="wled-fx-${g.id}" onchange="triggerFx('${g.id}',${gIdx})" class="w-full text-xs">
                <optgroup label="ðŸŽµ PulseAudio Musik (Low-Pixel)">
                  <option value="club_tech" ${w.effect==='club_tech'?'selected':''}>Club Tech (Kick + Snare)</option>
                  <option value="bass_cannon" ${w.effect==='bass_cannon'?'selected':''}>Bass Cannon (Center-Out)</option>
                  <option value="band_matrix" ${w.effect==='band_matrix'?'selected':''}>3-Band EQ (Bass/Mid/High)</option>
                  <option value="ping_pong" ${w.effect==='ping_pong'?'selected':''}>Beat Ping-Pong Scanner</option>
                  <option value="ambient_uplight" ${w.effect==='ambient_uplight'?'selected':''}>Ambient Uplight (Boden)</option>
                  <option value="strobe_drop" ${w.effect==='strobe_drop'?'selected':''}>Strobe / Drop Blinder</option>
                </optgroup>
                <optgroup label="ðŸŒˆ Statisch / Test">
                  <option value="rainbow" ${w.effect==='rainbow'?'selected':''}>Rainbow Wave</option>
                  <option value="breathe" ${w.effect==='breathe'?'selected':''}>Breathe</option>
                  <option value="chase" ${w.effect==='chase'?'selected':''}>Chase</option>
                  <option value="solid" ${w.effect==='solid'?'selected':''}>Solid Color</option>
                </optgroup>
              </select>
            </div>
            <div>
              <label class="text-slate-400 block mb-0.5">Palette</label>
              <select id="wled-pal-${g.id}" onchange="applyPalette('${g.id}',${gIdx},this.value)" class="w-full text-xs">
                <option value="cyberpunk" ${w.palette==='cyberpunk'?'selected':''}>Cyberpunk</option>
                <option value="fire_ice" ${w.palette==='fire_ice'?'selected':''}>Fire & Ice</option>
                <option value="rave" ${w.palette==='rave'?'selected':''}>Laser Rave</option>
                <option value="sunset" ${w.palette==='sunset'?'selected':''}>Sunset Club</option>
                <option value="warm_gold" ${w.palette==='warm_gold'?'selected':''}>Warm Gold</option>
              </select>
            </div>
          </div>

          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="text-slate-400 block mb-0.5">Kick / PrimÃ¤rfarbe</label>
              <input type="color" id="wled-col-${g.id}" value="${c1}" onchange="triggerFx('${g.id}',${gIdx})" class="w-full h-7 p-0 cursor-pointer">
            </div>
            <div>
              <label class="text-slate-400 block mb-0.5">Snare / SekundÃ¤rfarbe</label>
              <input type="color" id="wled-col2-${g.id}" value="${c2}" onchange="triggerFx('${g.id}',${gIdx})" class="w-full h-7 p-0 cursor-pointer">
            </div>
          </div>
        </div>
      </div>
    </div>`;
  }).join('');
}

const PAL_PRESETS = {
  cyberpunk: ['#ff0055', '#00f0ff'],
  fire_ice:  ['#ff2800', '#00b4ff'],
  rave:      ['#ff0000', '#00ff3c'],
  sunset:    ['#ff143c', '#ffaa00'],
  warm_gold: ['#ff5a00', '#ffd264']
};

function applyPalette(gid, gIdx, pal) {
  if (PAL_PRESETS[pal]) {
    document.getElementById(`wled-col-${gid}`).value = PAL_PRESETS[pal][0];
    document.getElementById(`wled-col2-${gid}`).value = PAL_PRESETS[pal][1];
  }
  triggerFx(gid, gIdx);
}

function setProxy(g, l) {
  cfg.groups[g].proxy = cfg.groups[g].lamps[l].name;
  cfg.groups[g].proxy_addr = cfg.groups[g].lamps[l].addr;
  render();
}
function moveLamp(g, l, d) {
  const arr = cfg.groups[g].lamps;
  if (l + d < 0 || l + d >= arr.length) return;
  [arr[l], arr[l+d]] = [arr[l+d], arr[l]];
  render();
}
function delLamp(g, l) { cfg.groups[g].lamps.splice(l, 1); render(); }
function addFromZ2M(g) {
  const sel = document.getElementById(`z2m-sel-${g}`);
  if (!sel.value) return;
  const [name, addr] = sel.value.split('|');
  cfg.groups[g].lamps.push({ name, addr });
  if (!cfg.groups[g].proxy) { cfg.groups[g].proxy = name; cfg.groups[g].proxy_addr = addr; }
  render();
}
function addGroup() {
  const n = cfg.groups.length + 1;
  cfg.groups.push({
    id: "group_" + Date.now(),
    name: "Gruppe " + n,
    enabled: true,
    use_radar: false,
    proxy: "",
    proxy_addr: "",
    fps: 25.0,
    max_brightness: 2047,
    brightness_mode: "max",
    idle_timeout: 3.0,
    ddp_enabled: true,
    ddp_port: 4048 + n - 1,
    e131_enabled: true,
    e131_universe: n,
    raw_enabled: true,
    raw_port: 2391 + n - 1,
    wled_udp_enabled: true,
    wled_udp_port: 21324 + n - 1,
    lamps: [],
    wled_state: { on: false, bri: 255, effect: "ambient_uplight", palette: "cyberpunk", color: [255,0,85], color2: [0,240,255], bass_boost: 1.3, snare_flash: 1.0 }
  });
  render();
}
function delGroup(g) { cfg.groups.splice(g, 1); saveAll(); }

async function saveAll() {
  const sens = parseFloat(document.getElementById('audio-sens').value) || 1.3;
  await fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg) });
  await fetch('/api/audio_sens', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sens }) });
  await init();
}

async function scanZ2M() {
  const res = await fetch('/api/scan_z2m', { method: 'POST' });
  const d = await res.json();
  z2mDevices = d.z2m_devices || [];
  render();
}

function hexToRgb(h) {
  return [parseInt(h.slice(1,3),16), parseInt(h.slice(3,5),16), parseInt(h.slice(5,7),16)];
}

async function triggerFx(gid, gIdx) {
  const on = document.getElementById(`wled-on-${gid}`).checked;
  const effect = document.getElementById(`wled-fx-${gid}`).value;
  const palette = document.getElementById(`wled-pal-${gid}`).value;
  const color = hexToRgb(document.getElementById(`wled-col-${gid}`).value);
  const color2 = hexToRgb(document.getElementById(`wled-col2-${gid}`).value);

  cfg.groups[gIdx].wled_state = { on, bri: 255, effect, palette, color, color2, bass_boost: 1.3, snare_flash: 1.0 };
  await fetch('/api/wled_control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ group_id: gid, on, bri: 255, effect, palette, color, color2 })
  });
}

setInterval(async () => {
  try {
    const res = await fetch('/api/status');
    const d = await res.json();

    if (d.audio) {
      const a = d.audio;
      document.getElementById('snap-dot').className = a.connected
        ? "w-3 h-3 rounded-full bg-emerald-400 animate-pulse"
        : "w-3 h-3 rounded-full bg-rose-500 animate-ping";

      document.getElementById('bar-bass').style.width = Math.round(a.bass * 100) + '%';
      document.getElementById('val-bass').innerText = Math.round(a.bass * 100) + '%';
      document.getElementById('bar-snare').style.width = Math.round(a.snare * 100) + '%';
      document.getElementById('val-snare').innerText = Math.round(a.snare * 100) + '%';
      document.getElementById('bar-mids').style.width = Math.round(a.mids * 100) + '%';
      document.getElementById('val-mids').innerText = Math.round(a.mids * 100) + '%';
      document.getElementById('bar-highs').style.width = Math.round(a.highs * 100) + '%';
      document.getElementById('val-highs').innerText = Math.round(a.highs * 100) + '%';

      document.getElementById('pill-kick').className = a.kick_env > 0.45
        ? "px-2.5 py-1 rounded font-bold bg-rose-500 text-white shadow-lg shadow-rose-500/50"
        : "px-2.5 py-1 rounded font-bold bg-slate-800 text-slate-500";
      document.getElementById('pill-snare').className = a.snare_env > 0.45
        ? "px-2.5 py-1 rounded font-bold bg-amber-400 text-slate-950 shadow-lg shadow-amber-400/50"
        : "px-2.5 py-1 rounded font-bold bg-slate-800 text-slate-500";
      document.getElementById('lbl-bpm').innerText = a.bpm ? `${a.bpm} BPM` : '-- BPM';
    }

    (d.groups || []).forEach(g => {
      const b = document.getElementById(`badge-${g.id}`);
      if (b) {
        b.className = g.active
          ? "px-2 py-0.5 text-xs font-semibold rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
          : "px-2 py-0.5 text-xs font-semibold rounded bg-slate-800 text-slate-400";
        b.innerText = g.active ? `AKTIV (${d.fps} fps)` : "Standby";
      }
      const st = document.getElementById(`stats-${g.id}`);
      if (st) st.innerText = `DDP:${g.rx_stats.ddp} E1.31:${g.rx_stats.e131}`;
      (g.pixels || []).forEach((rgb, i) => {
        const dot = document.getElementById(`px-${g.id}-${i}`);
        if (dot) {
          dot.style.backgroundColor = g.active ? `rgb(${rgb[0]},${rgb[1]},${rgb[2]})` : '#000';
          dot.style.boxShadow = (g.active && (rgb[0]+rgb[1]+rgb[2] > 40)) ? `0 0 10px rgba(${rgb[0]},${rgb[1]},${rgb[2]},0.8)` : 'none';
        }
      });
    });
  } catch (e) {}
}, 120);

init();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------
class WebHandler(BaseHTTPRequestHandler):
    mgr = None

    def log_message(self, format, *args):
        pass

    def _send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            raw = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if parsed.path == "/api/config":
            self._send_json({"config": self.mgr.config, "z2m_devices": self.mgr.z2m_devices})
            return

        if parsed.path == "/api/status":
            now = time.monotonic()
            groups_status = []
            for g in self.mgr.groups.values():
                with g.lock:
                    px_copy = list(g.pixels)
                fresh = (g.last_rx != 0.0 and (now - g.last_rx) <= g.idle_timeout)
                is_active = (fresh or g.wled_on or (g.use_radar and self.mgr.radar and self.mgr.radar.enable and (self.mgr.radar.force or self.mgr.radar.active)))
                groups_status.append({
                    "id": g.id,
                    "active": is_active,
                    "rx_stats": g.rx_stats,
                    "pixels": px_copy,
                })
            self._send_json({
                "fps": self.mgr.stats["fps"],
                "us": self.mgr.stats["us"],
                "audio": self.mgr.audio.get_features(),
                "groups": groups_status
            })
            return

        self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        if self.path == "/api/audio_sens":
            self.mgr.audio.sensitivity = float(payload.get("sens", 1.3))
            self._send_json({"ok": True})
            return

        if self.path == "/api/config":
            self.mgr.save_config(payload)
            new_dev = payload.get("pulse_device", "LedFx_Audio.monitor")
            if new_dev != getattr(self.mgr.audio, "device", None):
                self.mgr.audio.stop()
                self.mgr.audio.device = new_dev
                self.mgr.audio.start()
            self.mgr.start_all()
            self._send_json({"ok": True})
            return

        if self.path == "/api/scan_z2m":
            devs = self.mgr.discover_z2m_devices()
            self._send_json({"ok": True, "z2m_devices": devs})
            return

        if self.path == "/api/wled_control":
            gid = payload.get("group_id")
            g = self.mgr.groups.get(gid)
            if g:
                g.wled_on = bool(payload.get("on", False))
                g.wled_effect = payload.get("effect", "club_tech")
                g.wled_palette = payload.get("palette", "cyberpunk")
                g.wled_bri = int(payload.get("bri", 255))
                if "color" in payload:
                    g.wled_color = tuple(payload["color"])
                if "color2" in payload:
                    g.wled_color2 = tuple(payload["color2"])
            self._send_json({"ok": True})
            return

        self.send_error(404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.10.13")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--username", default="nodered")
    ap.add_argument("--password", default="72147214")
    ap.add_argument("--base-topic", default="zigbee2mqtt")
    ap.add_argument("--web-port", type=int, default=8080)
    args = ap.parse_args()

    mgr = GatewayManager(args)
    mgr.discover_z2m_devices()
    mgr.start_all()

    WebHandler.mgr = mgr
    server = ThreadingHTTPServer(("0.0.0.0", args.web_port), WebHandler)

    stop_ev = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_ev.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_ev.set())

    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"ðŸŒ Web-UI & PulseAudio DSP lÃ¤uft auf http://0.0.0.0:{args.web_port}", flush=True)

    try:
        while not stop_ev.is_set():
            stop_ev.wait(0.5)
    finally:
        server.shutdown()
        mgr.stop_all()


if __name__ == "__main__":
    main()
