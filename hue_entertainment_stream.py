import argparse
import colorsys
import json
import math
import random
import re
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Protokollkonstanten (Cluster 0xFC01 "Entertainment", Herstellercode Philips)
# ---------------------------------------------------------------------------
CLUSTER = 0xFC01
MFC = 0x100B                 # 4107 = Signify / Philips
CMD_FRAME = 0x01             # Entertainment-Frame (Lampe rebroadcastet es)
CMD_SYNC_STOP = 0x03         # Counter-Sync bzw. Session-Stop
ATTR_LIGHT_BALANCE = 0x0005  # optional: Light-Balance-Faktor (0x01..0xFE)

MODE_DEVICE = 0b01011        # normale Birne
MODE_SEGMENT = 0b00000       # Segment eines Gradient-Strip (braucht cmd 0x07!)

MAX_X = 0.7347               # Wide-Gamut-Skalierung der 12-bit-Koordinaten
MAX_Y = 0.8264
SMOOTHING_FULLSCALE_US = 2_560_000   # 0xFFFF entspricht ca. 2,56 s Uebergang

# Wide-Gamut-Dreieck (Gamut C) zur Begrenzung der Testmuster-Farben
GAMUT = ((0.7347, 0.2653), (0.1704, 0.7099), (0.0366, 0.0856))


# ---------------------------------------------------------------------------
# Frame-Bau (exakt das verifizierte Wire-Format)
# ---------------------------------------------------------------------------
def quant_xy(x, y):
    """CIE-xy -> 3 Bytes, 12 bit/Koordinate, nibble-gepackt, Wide-Gamut-skaliert."""
    x12 = max(0, min(4095, int(x / MAX_X * 4095)))
    y12 = max(0, min(4095, int(y / MAX_Y * 4095)))
    return bytes((
        x12 & 0xFF,
        ((x12 >> 8) & 0x0F) | ((y12 & 0x0F) << 4),
        (y12 >> 4) & 0xFF,
    ))


def record(addr, brightness, x, y, mode=MODE_DEVICE):
    """7-Byte-Light-Record: addr16 | (brightness11 << 5 | mode5) | xy24."""
    brightness = max(1, min(2047, int(brightness)))
    bf = (brightness << 5) | mode
    return bytes((addr & 0xFF, (addr >> 8) & 0xFF, bf & 0xFF, (bf >> 8) & 0xFF)) + quant_xy(x, y)


def frame_payload(counter, smoothing, records):
    return counter.to_bytes(4, "little") + smoothing.to_bytes(2, "little") + b"".join(records)


def sync_payload(counter):
    """cmd 0x03: [x0=0][x1=1][counter u32 LE]  (Sync bzw. Stop)."""
    return bytes((0x00, 0x01)) + counter.to_bytes(4, "little")


def zclcommand_msg(cmd, data):
    """Z2M-/set-Payload fuer rohe ZCL-Commands (Z2M >= 2.1.1)."""
    return {
        "zclcommand": {
            "cluster": CLUSTER,
            "command": cmd,
            "frametype": 1,                      # cluster-spezifisch
            "payload": {"data": list(data)},
            "options": {
                "manufacturerCode": MFC,
                "disableDefaultResponse": True,
                "direction": 0,                  # client -> server
                "timeout": 100.0,
            },
        }
    }


def light_balance_msg(value=0xFE):
    """Optionaler Attribut-Write: Light-Balance auf 0xFE (volle Ent.-Helligkeit)."""
    return {
        "write": {
            "cluster": CLUSTER,
            "payload": {
                str(ATTR_LIGHT_BALANCE): {
                    "manufacturerCode": MFC,
                    "type": 0x20,                # ZCL u8
                    "value": value,
                }
            },
        }
    }


# ---------------------------------------------------------------------------
# Farben: sRGB -> CIE-xy, aufs Gamut-Dreieck geclamped (nur fuer Testmuster)
# ---------------------------------------------------------------------------
def rgb_to_xy(r, g, b):
    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = lin(r), lin(g), lin(b)
    X = r * 0.4124 + g * 0.3576 + b * 0.1805
    Y = r * 0.2126 + g * 0.7152 + b * 0.0722
    Z = r * 0.0193 + g * 0.1192 + b * 0.9505
    s = X + Y + Z
    if s <= 1e-9:
        return 0.3127, 0.3290
    return clamp_gamut(X / s, Y / s)


def clamp_gamut(px, py):
    (ax, ay), (bx, by), (cx, cy) = GAMUT
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    w1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
    w2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
    w3 = 1 - w1 - w2
    if w1 >= 0 and w2 >= 0 and w3 >= 0:
        return px, py
    # Punkt liegt ausserhalb: naechsten Punkt auf einer Kante nehmen
    best = None
    for (x1, y1), (x2, y2) in ((GAMUT[0], GAMUT[1]), (GAMUT[1], GAMUT[2]), (GAMUT[2], GAMUT[0])):
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        qx, qy = x1 + t * dx, y1 + t * dy
        dist = (px - qx) ** 2 + (py - qy) ** 2
        if best is None or dist < best[0]:
            best = (dist, qx, qy)
    return best[1], best[2]


# ---------------------------------------------------------------------------
# MQTT / Z2M-Anbindung
# ---------------------------------------------------------------------------
class Z2mEntStreamer:
    def __init__(self, args):
        self.base = args.base_topic
        self.client = self._new_client()
        if args.username:
            self.client.username_pw_set(args.username, args.password or "")
        self.host, self.port = args.host, args.port
        self._devices_event = threading.Event()
        self._devices = []
        self._info_event = threading.Event()
        self._z2m_version = None

    @staticmethod
    def _new_client():
        try:  # paho-mqtt >= 2.0
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except (AttributeError, TypeError):
            return mqtt.Client()

    def connect(self):
        self.client.message_callback_add(
            f"{self.base}/bridge/response/devices", self._on_devices)
        self.client.message_callback_add(
            f"{self.base}/bridge/info", self._on_info)
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()
        self.client.subscribe(f"{self.base}/bridge/response/devices")
        self.client.subscribe(f"{self.base}/bridge/info")

    def _on_devices(self, _c, _u, msg):
        try:
            data = json.loads(msg.payload)
            if data.get("status") == "ok":
                self._devices = data.get("data", [])
                self._devices_event.set()
        except Exception:
            pass

    def _on_info(self, _c, _u, msg):
        try:
            self._z2m_version = json.loads(msg.payload).get("version")
        except Exception:
            pass
        self._info_event.set()

    def check_version(self):
        self._info_event.wait(timeout=3)
        if not self._z2m_version:
            print("Hinweis: Z2M-Version nicht ermittelbar (bridge/info). "
                  "Stelle sicher: Zigbee2MQTT >= 2.1.1")
            return
        m = re.match(r"(\d+)\.(\d+)\.(\d+)", self._z2m_version)
        if m and tuple(map(int, m.groups())) < (2, 1, 1):
            print(f"FEHLER: Zigbee2MQTT {self._z2m_version} ist zu alt â€” "
                  f"zclcommand/0xFC01 braucht >= 2.1.1", file=sys.stderr)
            sys.exit(2)
        print(f"Zigbee2MQTT {self._z2m_version} â€” OK (>= 2.1.1)")

    def resolve(self, names):
        """Friendly-Names -> aktuelle Zigbee-Kurzadressen (network_address)."""
        self._devices_event.clear()
        self.client.publish(f"{self.base}/bridge/request/devices", "{}")
        if not self._devices_event.wait(timeout=10):
            print(
                "FEHLER: keine Antwort auf bridge/request/devices.\n"
                "Wahrscheinlichste Ursache: Der MQTT-User darf auf "
                f"'{self.base}/#' nicht SCHREIBEN (ACL am Broker).\n"
                "Test:  mosquitto_pub -h <broker> -u <user> -P <pw> "
                f"-t '{self.base}/bridge/request/devices' -m '{{}}'\n"
                "Fix:   ACL 'topic readwrite zigbee2mqtt/#' fuer den User.\n"
                "Umgehung der Adressabfrage: --addr name=0xADDR ...\n"
                "(ACHTUNG: das Streaming auf <lampe>/set braucht die "
                "Schreibrechte trotzdem!)",
                file=sys.stderr,
            )
            sys.exit(2)
        by_name = {}
        for d in self._devices:
            if d.get("type") in ("Router", "EndDevice") and d.get("supported"):
                by_name[d["friendly_name"]] = d
        out = {}
        for n in names:
            d = by_name.get(n)
            if not d:
                print(f"FEHLER: Geraet '{n}' nicht in Z2M gefunden.", file=sys.stderr)
                sys.exit(2)
            na = d["network_address"]
            addr = na if isinstance(na, int) else int(str(na), 0)
            out[n] = addr
        return out

    def send(self, friendly, payload):
        self.client.publish(
            f"{self.base}/{friendly}/set",
            json.dumps(payload, separators=(",", ":")),
            qos=0,
        )

    # -- Session-Phasen ------------------------------------------------------
    def setup(self, lamps, counter, do_balance=True):
        """Optionale Light-Balance + Counter-Sync an jede Lampe."""
        for name in lamps:
            if do_balance:
                self.send(name, light_balance_msg())      # Fehler unkritisch
                time.sleep(0.03)
            self.send(name, zclcommand_msg(CMD_SYNC_STOP, sync_payload(counter)))
            time.sleep(0.03)

    def stop(self, lamps, counter, repeats=5):
        """Stop: cmd 0x03 mehrfach an jede Lampe (wie die echte Bridge)."""
        for _ in range(repeats):
            for name in lamps:
                self.send(name, zclcommand_msg(CMD_SYNC_STOP, sync_payload(counter)))
            time.sleep(0.05)


# ---------------------------------------------------------------------------
# Streaming-Loop
# ---------------------------------------------------------------------------
def smoothing_for_fps(fps):
    us = 1_000_000 / fps
    return max(0, min(0xFFFF, int(us * 0x10000 / SMOOTHING_FULLSCALE_US)))


def run(args):
    stop_flag = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_flag.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_flag.set())

    st = Z2mEntStreamer(args)
    st.connect()
    st.check_version()

    # Manuelle Adressen (--addr name=0x1234) haben Vorrang; der Rest wird
    # ueber bridge/request/devices aufgeloest.
    lamps = {}
    for spec in args.addr or []:
        n, sep, a = spec.partition("=")
        if not sep:
            print(f"FEHLER: --addr-Format 'name=0xADDR', bekam '{spec}'", file=sys.stderr)
            sys.exit(2)
        lamps[n.strip()] = int(a.strip(), 0)

    needed = list(dict.fromkeys(list(args.lamps) + ([args.proxy] if args.proxy else [])))
    missing = [n for n in needed if n not in lamps]
    if missing:
        lamps.update(st.resolve(missing))

    proxy = args.proxy or args.lamps[0]
    if proxy not in lamps:
        print(f"FEHLER: Proxy '{proxy}' hat keine Adresse.", file=sys.stderr)
        sys.exit(2)

    print("Lampen (Kurzadressen): " +
          ", ".join(f"{n}=0x{a:04X}" for n, a in lamps.items()))
    print(f"Proxy-Lampe: {proxy}   FPS: {args.fps}   Smoothing: 0x{smoothing_for_fps(args.fps):04X}")

    counter = int(time.time()) & 0xFFFF
    st.setup(lamps, counter, do_balance=not args.no_balance)

    names = list(args.lamps)
    period = 1.0 / args.fps
    smoothing = int(args.smoothing, 0) if args.smoothing else smoothing_for_fps(args.fps)
    t0 = time.monotonic()
    next_tick = t0
    csv_fh = open(args.csv_log, "w") if args.csv_log else None
    if csv_fh:
        csv_fh.write("counter,t_mono,t_wall,x,y,brightness\n")
    intervals = []
    print(f"Streaming laeuft ({len(names)} Lampe(n) ueber Proxy '{proxy}') â€” Ctrl-C zum Stoppen.")

    try:
        while not stop_flag.is_set():
            now = time.monotonic() - t0
            n = len(names)
            recs = []
            for i, name in enumerate(names):
                full = args.brightness / 254.0 * 2047.0
                if args.pattern == "rainbow":
                    hue = (now * args.speed + i / n) % 1.0
                    x, y = rgb_to_xy(*colorsys.hsv_to_rgb(hue, 1.0, 1.0))
                    bri = full
                elif args.pattern == "chase":
                    # Ping-Pong-Komet ueber die Lampenreihe (Reihenfolge = --lamps!)
                    m = 2 * (n - 1) if n > 1 else 1
                    phase = (now * args.speed * n) % m
                    pos = phase if phase < n else m - phase
                    d = abs(i - pos)
                    bri = (0.06 + 0.94 * math.exp(-(d * d) / (2 * 0.6 ** 2))) * full
                    hue = (now * 0.05) % 1.0
                    x, y = rgb_to_xy(*colorsys.hsv_to_rgb(hue, 1.0, 1.0))
                elif args.pattern == "wave":
                    # steile, schnell laufende Helligkeits-/Farbwelle
                    phase = (i / n - now * args.speed) % 1.0
                    w = max(0.0, math.cos(2 * math.pi * (phase - 0.5))) ** 6
                    bri = (0.08 + 0.92 * w) * full
                    hue = (i / n + now * args.speed * 0.2) % 1.0
                    x, y = rgb_to_xy(*colorsys.hsv_to_rgb(hue, 1.0, 1.0))
                elif args.pattern == "random":
                    # haertester Test: JEDE Lampe JEDEN Frame neue Farbe/Helligkeit
                    x, y = rgb_to_xy(*colorsys.hsv_to_rgb(random.random(), 1.0, 1.0))
                    bri = random.uniform(0.15, 1.0) * full
                elif args.pattern == "strobe":
                    # harter Farbwechsel aller Lampen alle N Frames (Latenztest)
                    phase = int(now * args.fps / max(1, args.strobe_period)) % 2
                    rgb = (1.0, 0.0, 0.0) if phase == 0 else (0.0, 0.25, 1.0)
                    x, y = rgb_to_xy(*rgb)
                    bri = full
                else:  # "pulse": weiss, Helligkeitswelle
                    x, y = 0.3127, 0.3290
                    bri = (0.5 + 0.5 * math.sin(2 * math.pi * (now * args.speed + i / n))) * full
                recs.append(record(lamps[name], bri, x, y))

            t_pub_mono = time.monotonic()
            payload = frame_payload(counter, smoothing, recs)
            st.send(proxy, zclcommand_msg(CMD_FRAME, payload))
            if csv_fh:
                csv_fh.write(f"{counter},{t_pub_mono:.6f},{time.time():.6f},{x:.5f},{y:.5f},{int(bri)}\n")
            intervals.append(t_pub_mono)
            counter = (counter + 1) & 0xFFFFFFFF

            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                stop_flag.wait(delay)
            else:                      # Ueberlast: Takt neu setzen
                next_tick = time.monotonic()

            if args.duration and (time.monotonic() - t0) >= args.duration:
                break
    finally:
        print("Stoppe Session ...")
        st.stop(lamps, counter)
        st.client.loop_stop()
        st.client.disconnect()
        if csv_fh:
            csv_fh.close()
        if len(intervals) > 2:
            deltas = [b - a for a, b in zip(intervals, intervals[1:])]
            mean = sum(deltas) / len(deltas)
            jit = (sum((d - mean) ** 2 for d in deltas) / len(deltas)) ** 0.5
            print(f"Sender-Statistik: {len(deltas)} Frames, {1/mean:.1f} fps effektiv, "
                  f"Jitter (Std) {jit*1000:.2f} ms, max. Luecke {max(deltas)*1000:.1f} ms")
        print("Fertig.")


def main():
    ap = argparse.ArgumentParser(description="Hue Entertainment via Zigbee2MQTT (PoC)")
    ap.add_argument("--host", default="localhost", help="MQTT-Broker")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--username", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--base-topic", default="zigbee2mqtt")
    ap.add_argument("--lamps", nargs="+", required=True,
                    help="Friendly Names der Entertainment-Lampen")
    ap.add_argument("--addr", nargs="*", default=None, metavar="NAME=0xADDR",
                    help="Kurzadressen manuell angeben (umgeht bridge/request/devices)")
    ap.add_argument("--proxy", default=None,
                    help="Friendly Name der Proxy-Lampe (Default: erste Lampe)")
    ap.add_argument("--fps", type=float, default=20.0, help="20-25 empfohlen")
    ap.add_argument("--brightness", type=int, default=254, help="1-254")
    ap.add_argument("--pattern",
                    choices=["rainbow", "pulse", "strobe", "chase", "wave", "random"],
                    default="rainbow",
                    help="chase=Komet ueber die Reihe, wave=Welle, random=Stress/Show")
    ap.add_argument("--strobe-period", type=int, default=12,
                    help="strobe: Frames pro Farbphase (Latenztest)")
    ap.add_argument("--csv-log", default=None, metavar="DATEI.csv",
                    help="Frame-Log (counter, Timestamps, xy) fuer Latenzanalyse")
    ap.add_argument("--smoothing", default=None, metavar="0x0400",
                    help="Smoothing manuell (Default: automatisch = Frame-Intervall; "
                         "kleiner = haertere Kanten, z. B. 0x0200 fuer Strobe-Tests)")
    ap.add_argument("--speed", type=float, default=0.1,
                    help="Muster-Geschwindigkeit (chase/wave: Durchlaeufe pro Sekunde)")
    ap.add_argument("--duration", type=float, default=0, help="Sekunden, 0 = endlos")
    ap.add_argument("--no-balance", action="store_true",
                    help="Light-Balance-Write (Attr 0x0005) weglassen")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
