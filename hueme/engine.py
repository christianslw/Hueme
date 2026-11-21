"""Core rendering engine, fixture/group state, and multi-protocol dispatch."""

from collections import deque
import copy
import json
import logging
import math
import threading
import time

from hueme.audio import AudioEngine
from hueme.config import DEFAULT_CONFIG_PATH, load_config, save_config, validate_config
from hueme.drivers import (
    DdpOutputDriver,
    E131OutputDriver,
    HueZ2MDriver,
    UdpInputManager,
    WledOutputDriver,
    parse_ddp,
    parse_wled_udp,
)
from hueme.effects import EffectManager

log = logging.getLogger("hueme.engine")


def grade_pixel(rgb):
    """Perceptual S-curve grading with deep-black floor & warm low-end preservation.
    Replaces the old 1/2.2 lift that boosted noise and killed contrast on Hue spots.
    """
    r, g, b = [max(0, min(255, int(v))) / 255.0 for v in rgb]
    peak = max(r, g, b)
    if peak <= 0.008:
        return (0, 0, 0)
    # Perceptual power curve (gamma 1.65) -> deep blacks, rich mids, clean highlights
    shaped_peak = (peak ** 1.65)
    # Subtle warm-amber shift at very low brightness (< 12%) so spots glow instead of turning grey
    warmth = max(0.0, (0.12 - shaped_peak) / 0.12) * 0.18
    scale = shaped_peak / peak
    ro = min(1.0, r * scale + warmth * shaped_peak)
    go = min(1.0, g * scale + warmth * 0.45 * shaped_peak)
    bo = max(0.0, min(1.0, b * scale * (1.0 - warmth * 0.6)))
    return (int(round(ro * 255)), int(round(go * 255)), int(round(bo * 255)))


def _lerp_px(a, b, t):
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def smooth_pixels(old, new, dt):
    """Fast, crisp pass-through: let the Hue ZCL hardware smoothing handle frame interpolation
    so beat-locked geometric patterns stay razor-sharp instead of turning into mush.
    """
    if len(old) != len(new):
        return list(new)
    out = []
    # Attacks pass through untouched: the ZCL frame owns the hardware ramp
    # (5 ms snap on transients, ~frame ramp on decay). Software rise smoothing
    # here too would shave peak transient brightness. Release stays 65 ms.
    a_rise = 1.0
    a_fall = 1.0 - math.exp(-max(0.0, dt) / 0.065)
    for (pr, pg, pb), (tr, tg, tb) in zip(old, new):
        ar = a_rise if tr >= pr else a_fall
        ag = a_rise if tg >= pg else a_fall
        ab = a_rise if tb >= pb else a_fall
        out.append((
            int(round(pr + (tr - pr) * ar)),
            int(round(pg + (tg - pg) * ag)),
            int(round(pb + (tb - pb) * ab)),
        ))
    return out


def _fixture_role(x, y, z, explicit=""):
    if explicit in ("center", "edge", "low", "high"):
        return explicit
    return "center" if abs(x - 0.5) < 0.25 else "edge"


def sync_test_pixels(n, features, now=None):
    """Plain white flash for audio/light alignment: kick first, beat as fallback."""
    env = features.get("env") or {}
    beat = features.get("beat") or {}
    conf = features.get("conf") or {}
    kick = max(0.0, min(1.0, float(env.get("kick", 0.0))))
    on_beat = bool(beat.get("on_beat")) and float(conf.get("bpm", 0.0)) >= 0.35
    level = 0.03 + 0.97 * kick
    if on_beat:
        level = max(level, 0.45)
    if now is not None and not features.get("alive", True):
        # No audio frames: slow heartbeat proves the overlay is active.
        phase = (float(now) * 0.5) % 1.0
        level = 0.06 + 0.25 * (1.0 - phase)
    value = int(round(max(0.0, min(1.0, level)) * 255))
    return [(value, value, value)] * max(1, int(n))


class MemoryLogHandler(logging.Handler):
    """Captures recent log records into a bounded deque for the Web UI Inspector."""

    def __init__(self, maxlen=250):
        super().__init__()
        self.records = deque(maxlen=maxlen)
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            self.records.append(self.format(record))
        except Exception:
            pass


class RuntimeGroup:
    """In-memory state for a single lighting group/zone."""

    def __init__(self, gcfg: dict):
        self.id = str(gcfg["id"])
        self.name = str(gcfg.get("name", self.id))
        self.enabled = bool(gcfg.get("enabled", True))
        self.fps = max(1.0, float(gcfg.get("fps", 30.0)))
        self.idle_timeout = float(gcfg.get("idle_timeout", 3.0))
        self.proxy = str(gcfg.get("proxy", "")).strip()

        self.fixtures = []
        raw_fixtures = gcfg.get("fixtures", [])
        n_raw = len(raw_fixtures)
        for i, f in enumerate(raw_fixtures):
            addr_str = str(f.get("addr", "")).strip()
            try:
                addr_int = int(addr_str, 0) if addr_str else 0
            except ValueError:
                addr_int = 0
            default_x = round(i / max(1, n_raw - 1), 3) if n_raw > 1 else 0.5
            x = float(f.get("x", default_x))
            y = float(f.get("y", 0.5))
            z = float(f.get("z", 0.0))
            self.fixtures.append({
                "name": str(f.get("name", f"Spot {i + 1}")).strip(),
                "addr": addr_str,
                "addr_int": addr_int,
                "x": x,
                "y": y,
                "z": z,
                "role": _fixture_role(x, y, z, str(f.get("role", "")).strip()),
            })

        self.n = len(self.fixtures)
        has_z = any("z" in f for f in raw_fixtures if isinstance(f, dict))
        if has_z:
            self.positions = [(f["x"], f["y"], f["z"]) for f in self.fixtures]
        else:
            self.positions = [(f["x"], f["y"]) for f in self.fixtures]
        self.roles = [f["role"] for f in self.fixtures]

        raw_origin = gcfg.get("origin")
        if isinstance(raw_origin, (list, tuple)) and len(raw_origin) >= 3:
            self.origin = tuple(float(v) for v in raw_origin[:3])
        else:
            self.origin = (0.0, 0.0, 0.0)
        ox, oy, oz = self.origin
        self.world_positions = [
            (ox + f["x"], oy + f["y"], oz + f["z"]) for f in self.fixtures
        ]

        fx = gcfg.get("effect", {})
        self.effect_id = str(fx.get("id", "club_tech"))
        self.effect_enabled = bool(fx.get("enabled", False))
        self.effect_params = copy.deepcopy(fx.get("params", {}))
        self.effect_state = {}
        self.last_error = None
        self._fx_sig = None
        self._xfade_from = None
        self._xfade_t0 = 0.0

        self.inputs = copy.deepcopy(gcfg.get("inputs", {}))
        self.outputs = copy.deepcopy(gcfg.get("outputs", {"hue": True, "ddp": False, "e131": False, "wled": False}))

        self.lock = threading.Lock()
        self.pixels = [(0, 0, 0)] * max(1, self.n)
        self._raw_pixels = [(0, 0, 0)] * max(1, self.n)
        self.input_pixels = [(0, 0, 0)] * max(1, self.n)
        self.last_rx = 0.0
        self.rx_stats = {"e131": 0, "ddp": 0, "raw": 0, "wled": 0, "bad": 0}

        self.next_due = 0.0
        self.last_render_t = 0.0
        self._frame_count = 0
        self._fps_t0 = time.monotonic()
        self.rendered_fps = 0.0

    def _apply_rgb(self, offset_px: int, payload: bytes, proto: str):
        n_px = len(payload) // 3
        if n_px <= 0 or self.n <= 0:
            return
        with self.lock:
            for i in range(n_px):
                idx = offset_px + i
                if idx >= self.n:
                    break
                self.input_pixels[idx] = (payload[3 * i], payload[3 * i + 1], payload[3 * i + 2])
            self.last_rx = time.monotonic()
            self.rx_stats[proto] += 1

    def ingest_e131(self, uni: int, dmx: bytes):
        if not self.enabled or not self.inputs.get("e131", {}).get("enabled"):
            return
        u0 = int(self.inputs["e131"].get("universe", 1))
        max_u = u0 + max(1, (3 * self.n + 509) // 510)
        if u0 <= uni < max_u:
            ch0 = (uni - u0) * 510
            px0, skip = divmod(ch0, 3)
            self._apply_rgb(px0, dmx[skip:], "e131")

    def ingest_udp(self, proto: str, data: bytes):
        try:
            if proto == "ddp":
                parsed = parse_ddp(data)
                if parsed:
                    self._apply_rgb(parsed[0] // 3, parsed[1], "ddp")
                else:
                    self.rx_stats["bad"] += 1
            elif proto == "raw":
                self._apply_rgb(0, data[: 3 * self.n], "raw")
            elif proto == "wled":
                res = parse_wled_udp(data)
                if not res:
                    self.rx_stats["bad"] += 1
                    return
                wtype, wdata = res
                if wtype == "drgb":
                    self._apply_rgb(0, wdata[: 3 * self.n], "wled")
                elif wtype == "dnrgb":
                    self._apply_rgb(wdata[0], wdata[1], "wled")
                elif wtype == "warls":
                    with self.lock:
                        for idx, rgb in wdata.items():
                            if 0 <= idx < self.n:
                                self.input_pixels[idx] = rgb
                        self.last_rx = time.monotonic()
                        self.rx_stats["wled"] += 1
                elif wtype == "sync":
                    with self.lock:
                        for i in range(self.n):
                            self.input_pixels[i] = wdata
                        self.last_rx = time.monotonic()
                        self.rx_stats["wled"] += 1
        except Exception:
            self.rx_stats["bad"] += 1

    def is_udp_fresh(self, now: float) -> bool:
        return self.last_rx > 0.0 and (now - self.last_rx) <= self.idle_timeout

    def is_active(self, now: float) -> bool:
        return self.enabled and self.n > 0 and (self.is_udp_fresh(now) or self.effect_enabled)

    def record_frame_tick(self, now: float):
        self._frame_count += 1
        elapsed = now - self._fps_t0
        if elapsed >= 1.0:
            self.rendered_fps = round(self._frame_count / elapsed, 1)
            self._frame_count = 0
            self._fps_t0 = now


class LightingEngine:
    """Top-level coordinator connecting Audio DSP, Effect Sandbox, UDP inputs, and Output Drivers."""

    def __init__(self, config_path=DEFAULT_CONFIG_PATH, cli_overrides=None):
        self.start_time = time.monotonic()
        self.config_path = config_path
        self.log_handler = MemoryLogHandler()
        root_logger = logging.getLogger("hueme")
        if not any(isinstance(h, MemoryLogHandler) for h in root_logger.handlers):
            root_logger.addHandler(self.log_handler)
        root_logger.setLevel(logging.INFO)

        self.config = load_config(config_path)
        if cli_overrides:
            for k, v in cli_overrides.items():
                if v is not None:
                    if k == "source":
                        self.config["audio"]["source"] = v
                    elif k == "web_port":
                        self.config["web"]["port"] = int(v)
                    elif k == "host":
                        self.config["mqtt"]["host"] = v

        self.lock = threading.Lock()
        self.z2m_devices = []

        fx_cfg = self.config.get("effects", {})
        self.effects = EffectManager(
            effects_dir=fx_cfg.get("dir", "effects"),
            poll_s=fx_cfg.get("poll_s", 0.5),
            timeout_ms=fx_cfg.get("timeout_ms", 40),
        )
        self.audio = AudioEngine(self.config.get("audio", {}))

        self.hue_driver = HueZ2MDriver(self.config.get("mqtt", {}), self.config.get("hue", {}))
        out_cfg = self.config.get("outputs", {})
        self.ddp_driver = DdpOutputDriver(out_cfg.get("ddp", {}))
        self.e131_driver = E131OutputDriver(out_cfg.get("e131", {}))
        self.wled_driver = WledOutputDriver(out_cfg.get("wled", {}))

        self.groups = {}
        self._build_groups_locked()
        self.udp_inputs = UdpInputManager(self.groups)

        self.stop_event = threading.Event()
        self.sync_test = False
        self.render_thread = None
        self._last_cue_section = None

        self.latencies_ms = deque(maxlen=120)
        self._latency_since = 0.0
        self.stats = {
            "fps": 0.0,
            "us": 0,
            "latency_p50_ms": 0.0,
            "latency_p95_ms": 0.0,
        }

    def _build_groups_locked(self):
        groups = {}
        for i, g in enumerate(self.config.get("groups", [])):
            if not g.get("id"):
                continue
            cfg = g
            origin = g.get("origin")
            if not (isinstance(origin, (list, tuple)) and len(origin) >= 3):
                cfg = dict(g)
                cfg["origin"] = [i * 1.3, 0.0, 0.0]
            groups[cfg["id"]] = RuntimeGroup(cfg)
        self.groups = groups

    @staticmethod
    def _scene_bounds(groups) -> dict:
        xs, ys, zs = [], [], []
        for g in groups:
            ox, oy, oz = g.origin
            xs.extend((ox, ox + 1.0))
            ys.extend((oy, oy + 1.0))
            zs.append(oz)
            for x, y, z in g.world_positions:
                xs.append(x)
                ys.append(y)
                zs.append(z)
        if not xs:
            return {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0], "center": [0.5, 0.5, 0.5]}
        lo = [min(xs), min(ys), min(zs)]
        hi = [max(xs), max(ys), max(zs)]
        return {"min": lo, "max": hi, "center": [(lo[i] + hi[i]) / 2 for i in range(3)]}

    def start(self):
        self.effects.start()
        self.audio.start()
        self.hue_driver.start()
        self.ddp_driver.start()
        self.e131_driver.start()
        self.wled_driver.start()
        self.udp_inputs.start()

        self.stop_event.clear()
        self.render_thread = threading.Thread(target=self._render_loop, name="hueme-render", daemon=True)
        self.render_thread.start()
        threading.Thread(target=self._auto_discover_devices, name="hueme-z2m-scan", daemon=True).start()
        log.info("LightingEngine started (%d groups)", len(self.groups))

    def stop(self):
        self.stop_event.set()
        if self.render_thread:
            self.render_thread.join(timeout=1.5)
            self.render_thread = None
        self.udp_inputs.stop()
        self.hue_driver.stop()
        self.ddp_driver.stop()
        self.e131_driver.stop()
        self.wled_driver.stop()
        self.audio.stop()
        self.effects.stop()
        log.info("LightingEngine stopped")

    def apply_config(self, new_cfg: dict) -> tuple[bool, list[str]]:
        ok, errs = validate_config(new_cfg, known_effects=self.effects.known_ids())
        if not ok:
            return False, errs

        save_config(new_cfg, self.config_path)
        self.udp_inputs.stop()

        with self.lock:
            old_cfg = self.config
            self.config = copy.deepcopy(new_cfg)
            self._build_groups_locked()

        self.audio.reconfigure(new_cfg.get("audio", {}))
        self.effects.timeout_ms = float(new_cfg.get("effects", {}).get("timeout_ms", 40))

        if new_cfg.get("mqtt") != old_cfg.get("mqtt") or new_cfg.get("hue") != old_cfg.get("hue"):
            self.hue_driver.stop()
            self.hue_driver = HueZ2MDriver(new_cfg.get("mqtt", {}), new_cfg.get("hue", {}))
            self.hue_driver.start()
            threading.Thread(target=self._auto_discover_devices, name="hueme-z2m-scan", daemon=True).start()

        out_cfg = new_cfg.get("outputs", {})
        self.ddp_driver.cfg = dict(out_cfg.get("ddp", {}))
        self.ddp_driver.start()
        self.e131_driver.cfg = dict(out_cfg.get("e131", {}))
        self.e131_driver.start()
        self.wled_driver.cfg = dict(out_cfg.get("wled", {}))
        self.wled_driver.start()

        self.udp_inputs = UdpInputManager(self.groups)
        self.udp_inputs.start()

        log.info("Configuration updated and hot-applied")
        return True, []

    def set_sync_test(self, enabled: bool) -> bool:
        with self.lock:
            self.sync_test = bool(enabled)
        log.info("Sync test %s", "enabled" if self.sync_test else "disabled")
        return self.sync_test

    def update_group_effect(self, group_id: str, payload: dict) -> bool:
        with self.lock:
            g = self.groups.get(group_id)
            if not g:
                return False
            if "enabled" in payload or "on" in payload:
                g.effect_enabled = bool(payload.get("enabled", payload.get("on")))
            if "effect" in payload or "id" in payload:
                new_eid = str(payload.get("id") or payload.get("effect"))
                if new_eid != g.effect_id:
                    g.effect_id = new_eid
                    g.effect_state = {}
                    defaults = self.effects.default_params_for(new_eid)
                    for k, v in defaults.items():
                        g.effect_params.setdefault(k, v)
            if "params" in payload and isinstance(payload["params"], dict):
                g.effect_params.update(payload["params"])
            for k in ("color", "color2", "palette", "brightness", "bass_boost", "snare_flash"):
                if k in payload:
                    g.effect_params[k] = payload[k]

            # Mirror into persisted config dict
            for gcfg in self.config.get("groups", []):
                if gcfg.get("id") == group_id:
                    gcfg.setdefault("effect", {})
                    gcfg["effect"]["id"] = g.effect_id
                    gcfg["effect"]["enabled"] = g.effect_enabled
                    gcfg["effect"]["params"] = copy.deepcopy(g.effect_params)
                    break
        return True

    def scan_z2m(self) -> list:
        devs = self.hue_driver.discover_devices(timeout=2.0)
        with self.lock:
            self.z2m_devices = devs
        return devs

    def get_z2m_devices(self) -> list:
        # _discovered is kept live by the always-on bridge/devices subscription.
        return self.hue_driver.discovered()

    def _auto_discover_devices(self):
        """Fetch the Zigbee2MQTT device list once shortly after startup/config change.

        ponytail: one-shot background scan + live subscription; no retry loop, add backoff if a flaky broker shows up."""
        try:
            time.sleep(1.5)
            devs = self.scan_z2m()
            if devs:
                log.info("Auto-discovered %d Zigbee devices from Zigbee2MQTT", len(devs))
            else:
                log.warning(
                    "Zigbee2MQTT device scan found nothing: %s",
                    self.hue_driver.stats.get("last_error")
                    or "no response on {0}/bridge/devices — is Zigbee2MQTT running on this broker?".format(self.hue_driver.base),
                )
        except Exception:
            log.warning("Zigbee2MQTT device scan failed", exc_info=True)

    def test_mqtt(self) -> dict:
        """Open a short-lived MQTT connection so setup can be verified before enabling output."""
        cfg = self.config.get("mqtt", {})
        host = str(cfg.get("host", "")).strip()
        port = int(cfg.get("port", 1883) or 1883)
        if not host:
            return {"ok": False, "message": "No MQTT broker host configured."}
        from hueme import drivers as drv

        if drv.mqtt is None:
            return {"ok": False, "message": "paho-mqtt is not installed."}
        try:
            try:
                client = drv.mqtt.Client(drv.mqtt.CallbackAPIVersion.VERSION2)
            except (AttributeError, TypeError):
                client = drv.mqtt.Client()
            if cfg.get("username"):
                client.username_pw_set(cfg.get("username"), cfg.get("password") or "")
            client.connect(host, port, keepalive=10)
            client.disconnect()
        except Exception as e:
            return {"ok": False, "message": f"Cannot reach {host}:{port} — {type(e).__name__}: {e}"}
        return {"ok": True, "message": f"Connected to MQTT broker {host}:{port}."}

    def test_output(self, name: str) -> dict:
        """Send one visible test frame through a single transport, using the saved config."""
        name = (name or "").lower()
        test_pixels = [(255, 190, 120)] * 12

        if name == "hue":
            if not self.hue_driver.hue_cfg.get("enabled"):
                return {"ok": False, "message": "Hue is disabled — tick Enabled and save first."}
            records = []
            for g in self.groups.values():
                if not g.enabled or not g.outputs.get("hue", True):
                    continue
                for f in g.fixtures:
                    if f["addr_int"] > 0:
                        records.append((f["name"], f["addr_int"], (255, 190, 120), g.proxy))
            if not records:
                return {"ok": False, "message": "No Hue fixtures with a Zigbee address. Add fixtures in Zones."}
            if not self.hue_driver.client:
                self.hue_driver.start()
            before = self.hue_driver.stats["tx_packets"]
            self.hue_driver.send_consolidated(records, fps=30.0)
            sent = self.hue_driver.stats["tx_packets"] - before
            if sent == 0:
                err = self.hue_driver.stats.get("last_error")
                return {"ok": False, "message": f"Nothing was sent{': ' + err if err else '. Check Test connection.'}"}
            return {"ok": True, "message": f"Sent {sent} ZCL frame(s) for {len(records)} fixtures."}

        drivers = {"ddp": self.ddp_driver, "e131": self.e131_driver, "wled": self.wled_driver}
        drv = drivers.get(name)
        if drv is None:
            return {"ok": False, "message": f"Unknown output '{name}'."}
        if not drv.cfg.get("enabled"):
            return {"ok": False, "message": f"{name.upper()} is disabled — tick Enabled and save first."}

        before = drv.stats["tx_packets"]
        errors_before = drv.stats["errors"]
        drv.send_pixels(test_pixels)
        sent = drv.stats["tx_packets"] - before
        if drv.stats["errors"] > errors_before:
            return {"ok": False, "message": f"{name.upper()} error: {drv.stats.get('last_error')}"}
        target = drv.stats.get("last_target") or "target"
        return {"ok": sent > 0, "message": f"Sent {sent} packet(s) to {target}." if sent else "No packets left the socket."}

    def _render_loop(self):
        next_tick = time.monotonic()
        frames = 0
        t_stat = time.monotonic()
        us_sum = 0.0
        us_n = 0

        while not self.stop_event.is_set():
            t0 = time.monotonic()
            with self.lock:
                groups_list = list(self.groups.values())

            max_fps = max((g.fps for g in groups_list if g.enabled), default=30.0)
            period = 1.0 / max(1.0, min(120.0, max_fps))

            af = self.audio.get_features(consume_triggers=True)
            cue = af.get("cue") or {}
            cue_section = cue.get("section")
            cue_switch = bool(
                cue_section
                and self._last_cue_section is not None
                and cue_section != self._last_cue_section
            )
            if cue_section:
                self._last_cue_section = cue_section
            snap = bool(af.get("kick") or af.get("snare_hit") or cue_switch)
            scene = self._scene_bounds([g for g in groups_list if g.enabled and g.n > 0])
            batch_reqs = []
            due_groups = []

            for g in groups_list:
                if not g.enabled or g.n == 0:
                    g.rendered_fps = 0.0
                    continue
                period_g = 1.0 / g.fps
                # Windows sleeps in ~15 ms steps, so a tick can arrive a few ms early.
                # Treat "almost due" as due, otherwise every other frame is skipped.
                if t0 + period_g * 0.35 < g.next_due:
                    continue
                dt = (t0 - g.last_render_t) if g.last_render_t > 0 else period_g
                g.last_render_t = t0
                g.next_due += period_g
                if g.next_due < t0 - period_g:
                    g.next_due = t0 + period_g
                due_groups.append(g)

                if self.sync_test:
                    with g.lock:
                        g.pixels = sync_test_pixels(g.n, af, t0)
                    g.last_error = None
                    g.record_frame_tick(t0)
                    continue

                if g.is_udp_fresh(t0):
                    with g.lock:
                        g.pixels = list(g.input_pixels)
                    g.last_error = None
                    g.record_frame_tick(t0)
                elif g.effect_enabled:
                    sig = (g.effect_id, json.dumps(g.effect_params, sort_keys=True, default=str))
                    if sig != g._fx_sig:
                        if g._fx_sig is not None:
                            g._xfade_from = list(g._raw_pixels)
                            g._xfade_t0 = t0
                        g._fx_sig = sig
                    batch_reqs.append({
                        "group_id": g.id,
                        "id": g.effect_id,
                        "n": g.n,
                        "now": t0,
                        "dt": dt,
                        "features": af,
                        "params": g.effect_params,
                        "state": g.effect_state,
                        "positions": g.positions,
                        "world_positions": g.world_positions,
                        "roles": g.roles,
                        "scene": scene,
                    })
                else:
                    with g.lock:
                        g.pixels = [(0, 0, 0)] * g.n
                    g.rendered_fps = 0.0

            if batch_reqs:
                results = self.effects.render_batch(batch_reqs)
                for g in due_groups:
                    res = results.get(g.id)
                    if not res:
                        continue
                    g.effect_state = res.get("state") or {}
                    g.last_error = res.get("error")
                    if res.get("pixels") is not None:
                        px = res["pixels"]
                        if g._xfade_from is not None:
                            a = min(1.0, (t0 - g._xfade_t0) / 0.4)
                            a = a * a * (3.0 - 2.0 * a)
                            old = g._xfade_from
                            px = [
                                _lerp_px(old[i] if i < len(old) else (0, 0, 0), px[i], a)
                                for i in range(len(px))
                            ]
                            if a >= 1.0:
                                g._xfade_from = None
                        g._raw_pixels = px
                        graded = [grade_pixel(p) for p in px]
                        with g.lock:
                            g.pixels = smooth_pixels(g.pixels, graded, dt)
                    g.record_frame_tick(t0)

            # Dispatch active groups to enabled hardware drivers
            try:
                hue_records = []
                for g in groups_list:
                    if not (g.is_active(t0) or (self.sync_test and g.enabled and g.n > 0)):
                        continue
                    with g.lock:
                        px_snapshot = list(g.pixels)

                    if g.outputs.get("hue", True):
                        for i, f in enumerate(g.fixtures):
                            rgb = px_snapshot[i] if i < len(px_snapshot) else (0, 0, 0)
                            hue_records.append((f["name"], f["addr_int"], rgb, g.proxy))

                    if g.outputs.get("ddp") or self.ddp_driver.cfg.get("enabled"):
                        self.ddp_driver.send_pixels(px_snapshot)
                    if g.outputs.get("e131") or self.e131_driver.cfg.get("enabled"):
                        u_start = int(g.inputs.get("e131", {}).get("universe", 1))
                        self.e131_driver.send_pixels(px_snapshot, universe_start=u_start)
                    if g.outputs.get("wled") or self.wled_driver.cfg.get("enabled"):
                        self.wled_driver.send_pixels(px_snapshot)

                self.hue_driver.send_consolidated(hue_records, fps=max_fps, snap=snap)
            except Exception:
                # A transport must never stop the render clock; drivers already record their own errors.
                log.exception("Dispatch error (engine keeps running)")

            t_done = time.monotonic()
            t_ingest = af.get("t_ingest", 0.0)
            if t_ingest > 0.0 and af.get("alive"):
                # Ignore the first 1.5 s of audio: worker startup and sandbox
                # load produce unrepresentative spikes in the p95.
                if self._latency_since == 0.0:
                    self._latency_since = t_done
                if t_done - self._latency_since >= 1.5:
                    lat_ms = max(0.0, (t_done - t_ingest) * 1000.0)
                    self.latencies_ms.append(lat_ms)
            else:
                self._latency_since = 0.0

            frames += 1
            us_sum += (t_done - t0) * 1e6
            us_n += 1

            if t_done - t_stat >= 1.0:
                self.stats["fps"] = round(frames / (t_done - t_stat), 1)
                self.stats["us"] = int(round(us_sum / max(1, us_n)))
                if self.latencies_ms:
                    s_lat = sorted(self.latencies_ms)
                    self.stats["latency_p50_ms"] = round(s_lat[len(s_lat) // 2], 2)
                    self.stats["latency_p95_ms"] = round(s_lat[min(len(s_lat) - 1, int(len(s_lat) * 0.95))], 2)
                frames, t_stat, us_sum, us_n = 0, t_done, 0.0, 0

            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                self.stop_event.wait(delay)
            else:
                next_tick = time.monotonic()

    def get_status(self) -> dict:
        now = time.monotonic()
        with self.lock:
            groups_list = list(self.groups.values())
        groups_out = []
        for g in groups_list:
            with g.lock:
                px = list(g.pixels)
            groups_out.append({
                "id": g.id,
                "name": g.name,
                "enabled": g.enabled,
                "active": g.is_active(now) or (self.sync_test and g.enabled and g.n > 0),
                "udp_fresh": g.is_udp_fresh(now),
                "fps": g.rendered_fps,
                "target_fps": g.fps,
                "effect_id": g.effect_id,
                "effect_enabled": g.effect_enabled,
                "effect_params": g.effect_params,
                "error": g.last_error,
                "rx_stats": dict(g.rx_stats),
                "pixels": px,
                "positions": g.positions,
                "world_positions": g.world_positions,
                "origin": list(g.origin),
                "roles": g.roles,
            })
        return {
            "fps": self.stats["fps"],
            "us": self.stats["us"],
            "latency_p50_ms": self.stats["latency_p50_ms"],
            "latency_p95_ms": self.stats["latency_p95_ms"],
            "sync_test": bool(self.sync_test),
            "sandbox_restarts": self.effects.restarts,
            "scene": self._scene_bounds([g for g in groups_list if g.enabled and g.n > 0]),
            "audio": self.audio.get_features(consume_triggers=False),
            "groups": groups_out,
        }

    def get_protocols_status(self) -> dict:
        with self.lock:
            rx_by_group = {gid: dict(g.rx_stats) for gid, g in self.groups.items()}
        return {
            "engine": dict(self.stats),
            "sandbox": {
                "restarts": self.effects.restarts,
                "last_error": self.effects.last_sandbox_error,
            },
            "drivers": {
                "hue": dict(self.hue_driver.stats, streaming=self.hue_driver.streaming, connected=self.hue_driver.connected),
                "ddp": dict(self.ddp_driver.stats, enabled=bool(self.ddp_driver.cfg.get("enabled"))),
                "e131": dict(self.e131_driver.stats, enabled=bool(self.e131_driver.cfg.get("enabled"))),
                "wled": dict(self.wled_driver.stats, enabled=bool(self.wled_driver.cfg.get("enabled"))),
            },
            "rx": rx_by_group,
        }
