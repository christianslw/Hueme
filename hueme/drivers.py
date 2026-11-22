"""Hardware output drivers (Hue ZCL 0xFC01 via Z2M, DDP, E1.31/sACN, WLED UDP) and UDP input receivers."""

import json
import logging
import select
import socket
import struct
import threading
import time
import uuid

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

log = logging.getLogger("hueme.drivers")

# ---------------------------------------------------------------------------
# Philips Hue Entertainment ZCL 0xFC01 Wire Format Constants & Helpers
# ---------------------------------------------------------------------------
CLUSTER = 0xFC01
MFC = 0x100B                 # 4107 = Signify / Philips
CMD_FRAME = 0x01             # Entertainment frame (rebroadcast by proxy lamp)
CMD_SYNC_STOP = 0x03         # Counter sync / session stop
ATTR_LIGHT_BALANCE = 0x0005  # Light-balance attribute (0x01..0xFE)

MODE_DEVICE = 0b01011        # Single bulb/spot
MODE_SEGMENT = 0b00000       # Gradient strip segment

MAX_X = 0.7347
MAX_Y = 0.8264
SMOOTHING_FULLSCALE_US = 2_560_000
ZCL_SNAP_SMOOTHING = 0x0080  # ~5 ms: hard snap for transients and cue cuts
GAMUT = ((0.7347, 0.2653), (0.1704, 0.7099), (0.0366, 0.0856))

DDP_TYPE_RGB24 = 0x0B   # DDP spec: RGB, 8 bit/channel
DDP_TYPE_RGBW32 = 0x1B  # DDP spec: RGBW, 8 bit/channel (WLED reads 4ch only for this type)

E131_PORT = 5568
ACN_PID = b"ASC-E1.17\x00\x00\x00"
HUEME_CID = uuid.uuid5(uuid.NAMESPACE_DNS, "hueme.lighting.local").bytes


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def clamp_gamut(px: float, py: float):
    (ax, ay), (bx, by), (cx, cy) = GAMUT
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    w1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
    w2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
    w3 = 1.0 - w1 - w2
    if w1 >= 0.0 and w2 >= 0.0 and w3 >= 0.0:
        return px, py
    best = None
    for (x1, y1), (x2, y2) in ((GAMUT[0], GAMUT[1]), (GAMUT[1], GAMUT[2]), (GAMUT[2], GAMUT[0])):
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        qx, qy = x1 + t * dx, y1 + t * dy
        dist = (px - qx) ** 2 + (py - qy) ** 2
        if best is None or dist < best[0]:
            best = (dist, qx, qy)
    return best[1], best[2]


def rgb_to_xy(r: float, g: float, b: float):
    r, g, b = _lin(r), _lin(g), _lin(b)
    X = r * 0.4124 + g * 0.3576 + b * 0.1805
    Y = r * 0.2126 + g * 0.7152 + b * 0.0722
    Z = r * 0.0193 + g * 0.1192 + b * 0.9505
    s = X + Y + Z
    if s <= 1e-9:
        return 0.3127, 0.3290
    return clamp_gamut(X / s, Y / s)


def rgb_to_xy_bri(r: int, g: int, b: int, max_bri: int = 2047, mode: str = "max"):
    if r <= 0 and g <= 0 and b <= 0:
        return 0.3127, 0.3290, 0
    r = min(255, max(0, int(r)))
    g = min(255, max(0, int(g)))
    b = min(255, max(0, int(b)))
    x, y = rgb_to_xy(r / 255.0, g / 255.0, b / 255.0)
    if mode == "max":
        v = max(r, g, b) / 255.0
    else:
        Y = 0.2126 * _lin(r / 255.0) + 0.7152 * _lin(g / 255.0) + 0.0722 * _lin(b / 255.0)
        v = Y ** (1.0 / 2.4)
    bri = int(max(0, min(max_bri, round(v * max_bri))))
    return x, y, bri


def quant_xy(x: float, y: float) -> bytes:
    x12 = max(0, min(4095, int(x / MAX_X * 4095)))
    y12 = max(0, min(4095, int(y / MAX_Y * 4095)))
    return bytes((
        x12 & 0xFF,
        ((x12 >> 8) & 0x0F) | ((y12 & 0x0F) << 4),
        (y12 >> 4) & 0xFF,
    ))


def record(addr: int, brightness: int, x: float, y: float, mode: int = MODE_DEVICE) -> bytes:
    """7-byte light record: addr16 LE | (brightness11 << 5 | mode5) LE | xy24."""
    # Brightness 0 is valid and means "off"; clamping it to 1 left idle lamps
    # at ~0.05 % white forever.
    brightness = max(0, min(2047, int(brightness)))
    bf = (brightness << 5) | mode
    return bytes((addr & 0xFF, (addr >> 8) & 0xFF, bf & 0xFF, (bf >> 8) & 0xFF)) + quant_xy(x, y)


def frame_payload(counter: int, smoothing: int, records: list[bytes]) -> bytes:
    return (counter & 0xFFFFFFFF).to_bytes(4, "little") + (smoothing & 0xFFFF).to_bytes(2, "little") + b"".join(records)


def sync_payload(counter: int) -> bytes:
    return bytes((0x00, 0x01)) + (counter & 0xFFFFFFFF).to_bytes(4, "little")


def zclcommand_msg(cmd: int, data: bytes) -> dict:
    return {
        "zclcommand": {
            "cluster": CLUSTER,
            "command": cmd,
            "frametype": 1,
            "payload": {"data": list(data)},
            "options": {
                "manufacturerCode": MFC,
                "disableDefaultResponse": True,
                "direction": 0,
                "timeout": 100.0,
            },
        }
    }


def light_balance_msg(value: int = 0xFE) -> dict:
    return {
        "write": {
            "cluster": CLUSTER,
            "payload": {
                str(ATTR_LIGHT_BALANCE): {
                    "manufacturerCode": MFC,
                    "type": 0x20,
                    "value": value,
                }
            },
        }
    }


def smoothing_for_fps(fps: float, headroom: float = 1.0) -> int:
    """ZCL trailing transition in 1/65536 of 2.56 s.

    ``headroom`` > 1 lets the ramp overlap the next frame slightly.  Zigbee and
    MQTT delivery jitter can add tens of milliseconds; a ramp of exactly one
    frame period then visibly holds between updates.
    """
    us = 1_000_000.0 * max(0.0, float(headroom)) / max(1.0, float(fps))
    return max(0, min(0xFFFF, int(us * 0x10000 / SMOOTHING_FULLSCALE_US)))


def format_hex(data: bytes, max_bytes: int = 96) -> str:
    sliced = data[:max_bytes]
    hx = " ".join(f"{b:02X}" for b in sliced)
    if len(data) > max_bytes:
        hx += f" ... (+{len(data) - max_bytes}B)"
    return hx


# ---------------------------------------------------------------------------
# Protocol Packet Builders & Parsers (DDP, E1.31, WLED)
# ---------------------------------------------------------------------------
def build_ddp_packet(
    rgb_bytes: bytes,
    offset_bytes: int = 0,
    seq: int = 1,
    push: bool = True,
    dtype: int = DDP_TYPE_RGB24,
) -> bytes:
    flags1 = 0x41 if push else 0x40  # DDP v1 + push flag
    flags2 = seq & 0x0F
    dest_id = 0x01
    header = struct.pack(">BBBBIH", flags1, flags2, dtype & 0xFF, dest_id, offset_bytes, len(rgb_bytes))
    return header + rgb_bytes


def parse_ddp(data: bytes):
    if len(data) < 10 or (data[0] & 0xC0) != 0x40:
        return None
    offset = struct.unpack_from(">I", data, 4)[0]
    length = struct.unpack_from(">H", data, 8)[0]
    payload = data[10 : 10 + length]
    return (offset, payload) if len(payload) >= length else None


def build_e131_packet(universe: int, dmx_bytes: bytes, seq: int = 0, priority: int = 100, source_name: str = "Hueme") -> bytes:
    """Build standard ANSI E1.31-2018 sACN Data Packet (126-byte header + DMX slots)."""
    dmx = dmx_bytes[:512]
    prop_val_count = 1 + len(dmx)  # Start code (0x00) + DMX slots
    dmp_len = 10 + prop_val_count
    framing_len = 77 + dmp_len
    root_len = 22 + framing_len

    src_bytes = source_name.encode("utf-8")[:63].ljust(64, b"\x00")
    root = (
        struct.pack(">HH", 0x0010, 0x0000)
        + ACN_PID
        + struct.pack(">HI", 0x7000 | (root_len & 0x0FFF), 0x00000004)
        + HUEME_CID
    )
    framing = (
        struct.pack(">HI", 0x7000 | (framing_len & 0x0FFF), 0x00000002)
        + src_bytes
        + struct.pack(">BHBBH", int(priority) & 0xFF, 0, int(seq) & 0xFF, 0, int(universe) & 0xFFFF)
    )
    dmp = (
        struct.pack(">HBBHHH", 0x7000 | (dmp_len & 0x0FFF), 0x02, 0xA1, 0x0000, 0x0001, prop_val_count)
        + b"\x00"
        + dmx
    )
    return root + framing + dmp


def parse_e131(data: bytes):
    if len(data) < 126 or data[4:16] != ACN_PID:
        return None
    if struct.unpack_from(">I", data, 18)[0] != 0x00000004 or data[117] != 0x02 or data[118] != 0xA1:
        return None
    universe = struct.unpack_from(">H", data, 113)[0]
    count = struct.unpack_from(">H", data, 123)[0]
    if data[125] != 0x00 or count < 1:
        return None
    return universe, data[126 : 126 + count - 1]


def build_wled_packet(pixels: list[tuple[int, int, int]], mode: str = "drgb", start_idx: int = 0, timeout_s: int = 2) -> bytes:
    t_byte = max(1, min(255, int(timeout_s)))
    if mode == "warls":
        body = bytearray([1, t_byte])
        for i, (r, g, b) in enumerate(pixels[:255]):
            body.extend((i & 0xFF, r & 0xFF, g & 0xFF, b & 0xFF))
        return bytes(body)
    if mode == "dnrgb" or len(pixels) > 490:
        hi = (start_idx >> 8) & 0xFF
        lo = start_idx & 0xFF
        body = bytearray([4, t_byte, hi, lo])
        for r, g, b in pixels[:489]:
            body.extend((r & 0xFF, g & 0xFF, b & 0xFF))
        return bytes(body)
    # Default DRGB (protocol 2)
    body = bytearray([2, t_byte])
    for r, g, b in pixels[:490]:
        body.extend((r & 0xFF, g & 0xFF, b & 0xFF))
    return bytes(body)


def parse_wled_udp(data: bytes):
    if len(data) < 2:
        return None
    p = data[0]
    if p == 1:
        out = {}
        for i in range(2, len(data) - 3, 4):
            out[data[i]] = (data[i + 1], data[i + 2], data[i + 3])
        return ("warls", out)
    if p == 2:
        return ("drgb", data[2:])
    if p == 4 and len(data) >= 4:
        start = (data[2] << 8) | data[3]
        return ("dnrgb", (start, data[4:]))
    if p == 0 and len(data) >= 12:
        bri = data[2] / 255.0
        return ("sync", (int(data[3] * bri), int(data[4] * bri), int(data[5] * bri)))
    return None


# ---------------------------------------------------------------------------
# Output Drivers
# ---------------------------------------------------------------------------
class HueZ2MDriver:
    """Zigbee2MQTT Hue Entertainment ZCL 0xFC01 streamer with multi-group consolidation."""

    # ponytail: synchronous MQTT publish in render thread (paho queues in memory); upgrade if broker stalls
    def __init__(self, mqtt_cfg: dict, hue_cfg: dict, mqtt_client=None):
        self.mqtt_cfg = dict(mqtt_cfg)
        self.hue_cfg = dict(hue_cfg)
        self.base = self.mqtt_cfg.get("base_topic", "zigbee2mqtt")
        self.client = mqtt_client
        self._owns_client = mqtt_client is None
        self.connected = False
        self.streaming = False
        self.counter = int(time.time()) & 0xFFFF
        self.frame_tick = 0

        self._devices_event = threading.Event()
        self._discovered = []
        self.active_lamps = {}  # friendly_name -> int short addr

        self.stats = {
            "tx_packets": 0,
            "tx_bytes": 0,
            "chunks_sent": 0,
            "interleaved_drops": 0,
            "errors": 0,
            "last_error": None,
            "last_hex": "",
            "last_len": 0,
            "last_target": "",
        }

    def start(self):
        # Always connect: device discovery and the scan button work even when Hue output is off.
        # Streaming frames only go out when hue_cfg.enabled (checked in send_consolidated).
        if self.client is None:
            if mqtt is None:
                self.stats["last_error"] = "paho-mqtt not installed"
                return
            try:
                self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            except (AttributeError, TypeError):
                self.client = mqtt.Client()

            user = self.mqtt_cfg.get("username")
            if user:
                self.client.username_pw_set(user, self.mqtt_cfg.get("password") or "")

        try:
            self.client.message_callback_add(f"{self.base}/bridge/devices", self._on_bridge_devices)
            self.client.message_callback_add(f"{self.base}/bridge/response/devices", self._on_bridge_response)
            if self._owns_client:
                self.client.connect(
                    self.mqtt_cfg.get("host", "localhost"),
                    int(self.mqtt_cfg.get("port", 1883)),
                    keepalive=30,
                )
                self.client.loop_start()
            self.client.subscribe(f"{self.base}/bridge/devices")
            self.client.subscribe(f"{self.base}/bridge/response/devices")
            # Ask Z2M for its device list so fixtures can be picked without a manual scan.
            self.client.publish(f"{self.base}/bridge/request/devices", "{}", qos=0)
            self.connected = True
            self.stats["last_error"] = None
        except Exception as e:
            self.connected = False
            self.stats["errors"] += 1
            self.stats["last_error"] = str(e)
            log.warning("Hue MQTT connect failed: %s", e)

    def stop(self):
        if self.streaming and self.active_lamps:
            try:
                self.stop_session(self.active_lamps, repeats=2)
            except Exception:
                pass
        self.streaming = False
        if self.client and self._owns_client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
            self.client = None
        self.connected = False

    def _on_bridge_devices(self, _c, _u, msg):
        try:
            devs = json.loads(msg.payload.decode("utf-8"))
            if isinstance(devs, list):
                self._parse_dev_list(devs)
        except Exception:
            pass

    def _on_bridge_response(self, _c, _u, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            if payload.get("status") == "ok" and isinstance(payload.get("data"), list):
                self._parse_dev_list(payload["data"])
        except Exception:
            pass

    def _parse_dev_list(self, devs):
        found = []
        for d in devs:
            if d.get("type") in ("Router", "EndDevice") and d.get("friendly_name"):
                na = d.get("network_address")
                addr_int = na if isinstance(na, int) else (int(str(na), 0) if na else 0)
                found.append({
                    "friendly_name": d["friendly_name"],
                    "network_address": f"0x{addr_int:04X}" if addr_int else "",
                    "addr_int": addr_int,
                    "model": (d.get("definition") or {}).get("model", ""),
                })
        self._discovered = sorted(found, key=lambda x: x["friendly_name"].lower())
        self._devices_event.set()

    def discover_devices(self, timeout=2.0) -> list:
        need_temp = self.client is None
        if need_temp:
            self.start()
        if not self.client:
            return self._discovered
        try:
            self._devices_event.clear()
            self.client.publish(f"{self.base}/bridge/request/devices", "{}")
            self._devices_event.wait(timeout=timeout)
        except Exception as e:
            self.stats["last_error"] = str(e)
        finally:
            if need_temp and not self.hue_cfg.get("enabled", False):
                self.stop()
        return list(self._discovered)

    def discovered(self) -> list:
        # Kept live by the bridge/devices subscription.
        return list(self._discovered)

    def _publish(self, friendly_name: str, payload: dict):
        if not self.client:
            return
        topic = f"{self.base}/{friendly_name}/set"
        raw = json.dumps(payload, separators=(",", ":"))
        self.client.publish(topic, raw, qos=0)
        self.stats["tx_packets"] += 1
        self.stats["tx_bytes"] += len(raw)
        self.stats["last_target"] = topic

    def setup_session(self, lamps: dict, do_balance: bool = True):
        if not self.client:
            return
        for name in lamps:
            if do_balance:
                self._publish(name, light_balance_msg())
            self._publish(name, zclcommand_msg(CMD_SYNC_STOP, sync_payload(self.counter)))
        self.active_lamps = dict(lamps)
        self.streaming = True

    def stop_session(self, lamps: dict = None, repeats: int = 2):
        lamps = lamps or self.active_lamps
        if not self.client or not lamps:
            self.streaming = False
            return
        for _ in range(repeats):
            for name in lamps:
                self._publish(name, zclcommand_msg(CMD_SYNC_STOP, sync_payload(self.counter)))
        self.streaming = False

    def send_consolidated(self, fixture_records: list[tuple[str, int, tuple[int, int, int], str]], fps: float = 30.0, snap: bool = False):
        """Consolidate multi-group fixtures into <=max_fixtures_per_frame ZCL frames.
        fixture_records: list of (fixture_name, addr_int, (r,g,b), group_proxy).
        snap: transient/cue tick -> 5 ms ZCL smoothing instead of the frame ramp."""
        if not self.hue_cfg.get("enabled", False):
            return
        if not self.client:
            self.start()
            if not self.client:
                return

        if not fixture_records:
            if self.streaming:
                self.stop_session()
            return

        lamps_map = {name: addr for name, addr, _, _ in fixture_records if addr > 0}
        if not self.streaming and lamps_map:
            self.setup_session(lamps_map, do_balance=bool(self.hue_cfg.get("light_balance", True)))

        max_bri = int(self.hue_cfg.get("max_brightness", 2047))
        bri_mode = self.hue_cfg.get("brightness_mode", "max")
        sm_cfg = self.hue_cfg.get("smoothing", "auto")
        try:
            headroom = float(self.hue_cfg.get("smoothing_headroom", 1.5))
        except (TypeError, ValueError):
            headroom = 1.5
        if sm_cfg == "auto" or sm_cfg is None or sm_cfg == "":
            trailing_smoothing = smoothing_for_fps(fps, headroom)
        else:
            try:
                trailing_smoothing = int(str(sm_cfg), 0) if isinstance(sm_cfg, str) else int(sm_cfg)
            except (TypeError, ValueError):
                # User typed something that is not a number: fall back instead of killing the render loop.
                trailing_smoothing = smoothing_for_fps(fps, headroom)
        smoothing = ZCL_SNAP_SMOOTHING if snap else trailing_smoothing

        # Build 7-byte records. Keep the proven consolidation: one small proxy
        # broadcast at a time. Per-proxy parallel frames at 25 fps saturated the
        # Zigbee coordinator and took every lamp offline; stability beats wishful
        # routing here.
        raw_recs = []
        proxies_in_order = list(self.hue_cfg.get("proxies") or [])
        for name, addr, (r, g, b), grp_proxy in fixture_records:
            if addr <= 0:
                continue
            if grp_proxy and grp_proxy not in proxies_in_order:
                proxies_in_order.append(grp_proxy)
            x, y, bri = rgb_to_xy_bri(r, g, b, max_bri=max_bri, mode=bri_mode)
            raw_recs.append(record(addr, bri, x, y, MODE_DEVICE))

        if not raw_recs:
            return

        if not proxies_in_order:
            proxies_in_order = [fixture_records[0][0]]

        chunk_size = max(1, min(10, int(self.hue_cfg.get("max_fixtures_per_frame", 10))))
        chunks = [raw_recs[i : i + chunk_size] for i in range(0, len(raw_recs), chunk_size)]
        self.frame_tick += 1

        try:
            if len(chunks) == 1:
                # <= 10 fixtures total across all groups: single consolidated ZCL transmission to primary proxy
                payload_bytes = frame_payload(self.counter, smoothing, chunks[0])
                self._publish(proxies_in_order[0], zclcommand_msg(CMD_FRAME, payload_bytes))
                self.counter = (self.counter + 1) & 0xFFFFFFFF
                self.stats["chunks_sent"] += 1
                self.stats["last_hex"] = format_hex(payload_bytes)
                self.stats["last_len"] = len(payload_bytes)
            elif len(proxies_in_order) >= 2:
                # Secondary proxy multiplexing: send chunk k to proxies_in_order[k % len]
                for k, ch in enumerate(chunks):
                    target_proxy = proxies_in_order[k % len(proxies_in_order)]
                    payload_bytes = frame_payload(self.counter, smoothing, ch)
                    self._publish(target_proxy, zclcommand_msg(CMD_FRAME, payload_bytes))
                    self.counter = (self.counter + 1) & 0xFFFFFFFF
                    self.stats["chunks_sent"] += 1
                    self.stats["last_hex"] = format_hex(payload_bytes)
                    self.stats["last_len"] = len(payload_bytes)
            else:
                # Single proxy with >10 fixtures: deterministic frame interleaving to prevent Zigbee buffer exhaustion
                active_chunk_idx = (self.frame_tick - 1) % len(chunks)
                ch = chunks[active_chunk_idx]
                payload_bytes = frame_payload(self.counter, smoothing, ch)
                self._publish(proxies_in_order[0], zclcommand_msg(CMD_FRAME, payload_bytes))
                self.counter = (self.counter + 1) & 0xFFFFFFFF
                self.stats["chunks_sent"] += 1
                self.stats["interleaved_drops"] += len(chunks) - 1
                self.stats["last_hex"] = format_hex(payload_bytes)
                self.stats["last_len"] = len(payload_bytes)
        except Exception as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = str(e)


class DdpOutputDriver:
    """UDP DDP (port 4048) output sender."""

    def __init__(self, cfg: dict):
        self.cfg = dict(cfg)
        self.sock = None
        self.seq = 0
        self.stats = {
            "tx_packets": 0,
            "tx_bytes": 0,
            "errors": 0,
            "last_error": None,
            "last_hex": "",
            "last_len": 0,
            "last_target": "",
        }

    def start(self):
        self.stop()
        if not self.cfg.get("enabled", False):
            return
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def stop(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def send_pixels(self, pixels: list[tuple[int, int, int]] | list[tuple[int, int, int, int]], host: str = None, port: int = None):
        if not self.cfg.get("enabled", False) and host is None:
            return
        if not self.sock:
            self.start()
            if not self.sock:
                return
        target_host = host or self.cfg.get("host", "127.0.0.1")
        target_port = int(port or self.cfg.get("port", 4048))

        # DDP supports RGB (type 1) and RGBW (type 3). The 4-channel path is
        # used by SK6812 RGB+1800K strips; existing 3-tuple callers are
        # byte-for-byte unchanged.
        # HAL passes a numpy array of float voxel values, not a list of ints.
        # ``if pixels`` and ``px[0] & 0xFF`` both fail on that (ambiguous truth
        # value, and float bitwise-and). Cast explicitly on the way to bytes.
        channels = len(pixels[0]) if len(pixels) > 0 else int(self.cfg.get("channels", 3))
        channels = 4 if channels == 4 else 3
        dtype = DDP_TYPE_RGBW32 if channels == 4 else DDP_TYPE_RGB24

        raw = bytearray()
        for px in pixels:
            if channels == 4:
                raw.extend(
                    (
                        int(px[0]) & 0xFF,
                        int(px[1]) & 0xFF,
                        int(px[2]) & 0xFF,
                        int(px[3]) & 0xFF,
                    )
                )
            else:
                raw.extend((int(px[0]) & 0xFF, int(px[1]) & 0xFF, int(px[2]) & 0xFF))

        max_chunk = 1440 - (1440 % channels)  # multiple of pixel width
        total = len(raw)
        offset = 0
        try:
            while offset < total or total == 0:
                chunk = bytes(raw[offset : offset + max_chunk])
                self.seq = (self.seq % 15) + 1
                is_last = (offset + max_chunk) >= total
                pkt = build_ddp_packet(chunk, offset_bytes=offset, seq=self.seq, push=is_last, dtype=dtype)
                self.sock.sendto(pkt, (target_host, target_port))
                self.stats["tx_packets"] += 1
                self.stats["tx_bytes"] += len(pkt)
                self.stats["last_hex"] = format_hex(pkt)
                self.stats["last_len"] = len(pkt)
                self.stats["last_target"] = f"{target_host}:{target_port}"
                offset += max_chunk
                if total == 0:
                    break
        except OSError as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = str(e)


class E131OutputDriver:
    """UDP E1.31 / sACN (port 5568) output sender supporting unicast and multicast."""

    # ponytail: single shared UDP socket without per-universe rate limiter; upgrade if >20 universes
    def __init__(self, cfg: dict):
        self.cfg = dict(cfg)
        self.sock = None
        self.seq = 0
        self.stats = {
            "tx_packets": 0,
            "tx_bytes": 0,
            "errors": 0,
            "last_error": None,
            "last_hex": "",
            "last_len": 0,
            "last_target": "",
        }

    def start(self):
        self.stop()
        if not self.cfg.get("enabled", False):
            return
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        except OSError:
            pass

    def stop(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def send_pixels(self, pixels: list[tuple[int, int, int]], universe_start: int = None, unicast_host: str = None, port: int = E131_PORT):
        if not self.cfg.get("enabled", False) and unicast_host is None and universe_start is None:
            return
        if not self.sock:
            self.start()
            if not self.sock:
                return

        u_start = int(universe_start or self.cfg.get("universe_start", 1))
        u_host = (unicast_host if unicast_host is not None else self.cfg.get("unicast", "")).strip()
        prio = int(self.cfg.get("priority", 100))

        # 170 RGB pixels (510 DMX channels) per universe
        n_px = len(pixels)
        n_unis = max(1, (n_px + 169) // 170)
        try:
            for u_idx in range(n_unis):
                uni = u_start + u_idx
                sub_px = pixels[u_idx * 170 : (u_idx + 1) * 170]
                dmx = bytearray()
                for r, g, b in sub_px:
                    dmx.extend((r & 0xFF, g & 0xFF, b & 0xFF))
                self.seq = (self.seq + 1) & 0xFF
                pkt = build_e131_packet(uni, bytes(dmx), seq=self.seq, priority=prio)
                dest = u_host if u_host else f"239.255.{(uni >> 8) & 0xFF}.{uni & 0xFF}"
                self.sock.sendto(pkt, (dest, int(port)))
                self.stats["tx_packets"] += 1
                self.stats["tx_bytes"] += len(pkt)
                self.stats["last_hex"] = format_hex(pkt)
                self.stats["last_len"] = len(pkt)
                self.stats["last_target"] = f"{dest}:{port} (U{uni})"
        except OSError as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = str(e)


class WledOutputDriver:
    """WLED Realtime UDP (WARLS, DRGB, DNRGB) output sender."""

    # ponytail: WLED UDP push only; add HTTP /json/state discovery emulation if WLED ecosystem polling needed
    def __init__(self, cfg: dict):
        self.cfg = dict(cfg)
        self.sock = None
        self.stats = {
            "tx_packets": 0,
            "tx_bytes": 0,
            "errors": 0,
            "last_error": None,
            "last_hex": "",
            "last_len": 0,
            "last_target": "",
        }

    def start(self):
        self.stop()
        if not self.cfg.get("enabled", False):
            return
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def stop(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def send_pixels(self, pixels: list[tuple[int, int, int]], host: str = None, port: int = None, mode: str = None):
        if not self.cfg.get("enabled", False) and host is None:
            return
        if not self.sock:
            self.start()
            if not self.sock:
                return
        target_host = host or self.cfg.get("host", "127.0.0.1")
        target_port = int(port or self.cfg.get("port", 21324))
        w_mode = (mode or self.cfg.get("mode", "drgb")).lower()

        try:
            pkt = build_wled_packet(pixels, mode=w_mode)
            self.sock.sendto(pkt, (target_host, target_port))
            self.stats["tx_packets"] += 1
            self.stats["tx_bytes"] += len(pkt)
            self.stats["last_hex"] = format_hex(pkt)
            self.stats["last_len"] = len(pkt)
            self.stats["last_target"] = f"{target_host}:{target_port} ({w_mode.upper()})"
        except OSError as e:
            self.stats["errors"] += 1
            self.stats["last_error"] = str(e)


# ---------------------------------------------------------------------------
# UDP Input Receivers (DDP, RAW, WLED per-group + global E1.31)
# ---------------------------------------------------------------------------
class UdpInputManager:
    """Manages global E1.31 receiver and per-group DDP/RAW/WLED UDP receivers."""

    def __init__(self, groups_dict: dict, e131_port: int = E131_PORT):
        self.groups = groups_dict  # group_id -> RuntimeGroup
        self.e131_port = int(e131_port)
        self.stop_event = threading.Event()
        self.thread = None
        self.socks = []  # list of (proto, group_id_or_None, socket)

    def start(self):
        self.stop()
        self.stop_event.clear()
        self.socks = []

        # 1. Global E1.31 receiver if any group has e131 input enabled
        e131_unis = set()
        for g in self.groups.values():
            if g.enabled and g.inputs.get("e131", {}).get("enabled"):
                u0 = int(g.inputs["e131"].get("universe", 1))
                n_u = max(1, (3 * g.n + 509) // 510)
                for u in range(u0, u0 + n_u):
                    e131_unis.add(u)

        if e131_unis:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", self.e131_port))
                for u in e131_unis:
                    mreq = socket.inet_aton(f"239.255.{(u >> 8) & 0xFF}.{u & 0xFF}") + socket.inet_aton("0.0.0.0")
                    try:
                        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                    except OSError:
                        pass
                s.setblocking(False)
                self.socks.append(("e131", None, s))
            except OSError as e:
                log.warning("E1.31 input port %d busy: %s", self.e131_port, e)

        # 2. Per-group DDP, RAW, WLED sockets
        for gid, g in self.groups.items():
            if not g.enabled or g.n == 0:
                continue
            for proto in ("ddp", "raw", "wled"):
                pcfg = g.inputs.get(proto, {})
                if pcfg.get("enabled"):
                    port = int(pcfg.get("port", 0))
                    if port <= 0:
                        continue
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        s.bind(("0.0.0.0", port))
                        s.setblocking(False)
                        self.socks.append((proto, gid, s))
                    except OSError as e:
                        log.warning("Group '%s' %s port %d busy: %s", g.name, proto, port, e)

        if self.socks:
            self.thread = threading.Thread(target=self._rx_loop, name="hueme-udp-rx", daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1.0)
            self.thread = None
        for _, _, s in self.socks:
            try:
                s.close()
            except OSError:
                pass
        self.socks = []

    def _rx_loop(self):
        sock_map = {s: (proto, gid) for proto, gid, s in self.socks}
        sock_list = list(sock_map.keys())
        while not self.stop_event.is_set() and sock_list:
            try:
                ready, _, _ = select.select(sock_list, [], [], 0.15)
            except Exception:
                continue
            for s in ready:
                proto, gid = sock_map[s]
                try:
                    data, _ = s.recvfrom(65535)
                except OSError:
                    continue
                if proto == "e131":
                    parsed = parse_e131(data)
                    if parsed:
                        uni, dmx = parsed
                        for g in self.groups.values():
                            g.ingest_e131(uni, dmx)
                else:
                    g = self.groups.get(gid)
                    if g:
                        g.ingest_udp(proto, data)
