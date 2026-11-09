"""Configuration schema, v1->v2 migration, validation, and atomic persistence."""

import copy
import json
import os
import shutil
import tempfile

DEFAULT_CONFIG_PATH = os.path.join("config", "gateway.json")
LEGACY_CONFIG_PATH = "gateway_config.json"


def default_config():
    return {
        "version": 2,
        "web": {"host": "0.0.0.0", "port": 8080},
        "audio": {
            "source": "auto",  # auto | parec | snapcast | wav | synthetic
            "device": "LedFx_Audio.monitor",
            "rate": 48000,
            "channels": 2,
            "block": 512,
            "window": 1024,
            "sensitivity": 1.3,
            "decay_kick": 10.0,
            "decay_snare": 12.0,
            "snapcast": {"host": "127.0.0.1", "port": 1704},
            "sendspin": {"url": "ws://192.168.10.13:8927/sendspin", "client_name": "Hueme-Visualizer"},
            "wav": "testdata/click_128.wav",
            "latency_ms": 0,  # manual audio-sync trim; + delays lights, - advances them
            "agc": {
                "enabled": True,
                "target_rms": 0.08,
                "tau_s": 20.0,
                "silence_db": -55.0,
            },
        },
        "mqtt": {
            "host": "192.168.10.13",
            "port": 1883,
            "username": "",
            "password": "",
            "base_topic": "zigbee2mqtt",
        },
        "hue": {
            "enabled": False,
            "proxies": [],
            "max_fixtures_per_frame": 10,
            "smoothing": "auto",  # "auto" or int (0..65535)
            "max_brightness": 2047,
            "brightness_mode": "max",  # "max" | "luminance"
            "light_balance": True,
        },
        "outputs": {
            "ddp": {"enabled": False, "host": "127.0.0.1", "port": 4048},
            "e131": {
                "enabled": False,
                "unicast": "",
                "multicast": True,
                "universe_start": 1,
                "priority": 100,
            },
            "wled": {
                "enabled": False,
                "host": "127.0.0.1",
                "port": 21324,
                "mode": "drgb",  # drgb | dnrgb | warls
            },
        },
        "effects": {"dir": "effects", "poll_s": 0.5, "timeout_ms": 80},
        "groups": [
            {
                "id": "group_main",
                "name": "Main Zone (6 Spots)",
                "enabled": True,
                "fps": 30.0,
                "idle_timeout": 3.0,
                "proxy": "",
                "fixtures": [
                    {"name": f"Spot {i + 1}", "addr": f"0x{0x1001 + i:04X}", "x": round(i / 5.0, 3), "y": 0.5}
                    for i in range(6)
                ],
                "effect": {
                    "id": "club_tech",
                    "enabled": True,
                    "params": {
                        "color": [255, 0, 85],
                        "color2": [0, 240, 255],
                        "palette": "cyberpunk",
                        "brightness": 1.0,
                        "bass_boost": 1.3,
                        "snare_flash": 1.0,
                    },
                },
                "inputs": {
                    "ddp": {"enabled": True, "port": 4048},
                    "e131": {"enabled": True, "universe": 1},
                    "raw": {"enabled": True, "port": 2391},
                    "wled": {"enabled": True, "port": 21324},
                },
                "outputs": {
                    "hue": True,
                    "ddp": False,
                    "e131": False,
                    "wled": False,
                },
            }
        ],
    }


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def migrate_v1_to_v2(v1):
    """Convert v1 gateway_config.json dict into v2 schema."""
    cfg = default_config()
    if "mqtt" in v1 and isinstance(v1["mqtt"], dict):
        cfg["mqtt"] = _deep_merge(cfg["mqtt"], v1["mqtt"])
    if v1.get("pulse_device"):
        cfg["audio"]["device"] = str(v1["pulse_device"])
    if "radar_global" in v1:
        cfg["_legacy_radar"] = copy.deepcopy(v1["radar_global"])

    proxies = []
    groups_v2 = []
    for idx, g in enumerate(v1.get("groups", [])):
        lamps = g.get("lamps", [])
        n = len(lamps)
        fixtures = []
        for i, l in enumerate(lamps):
            x = round(i / max(1, n - 1), 3) if n > 1 else 0.5
            fixtures.append({
                "name": str(l.get("name", f"Spot {i + 1}")).strip(),
                "addr": str(l.get("addr", "")).strip(),
                "x": float(l.get("x", x)),
                "y": float(l.get("y", 0.5)),
            })
        proxy = str(g.get("proxy", "")).strip()
        proxy_addr = str(g.get("proxy_addr", "")).strip()
        if proxy and proxy_addr:
            if not any(f["name"] == proxy for f in fixtures):
                fixtures.append({"name": proxy, "addr": proxy_addr, "x": 0.5, "y": 0.8})
        if proxy and proxy not in proxies:
            proxies.append(proxy)

        w = g.get("wled_state", {})
        bri_val = round(float(w.get("bri", 255)) / 255.0, 3)
        fx_id = str(w.get("effect", "club_tech"))
        if fx_id == "chase":
            fx_id = "diagnostic_chase"

        if "max_brightness" in g:
            cfg["hue"]["max_brightness"] = int(g["max_brightness"])
        if "brightness_mode" in g:
            cfg["hue"]["brightness_mode"] = str(g["brightness_mode"])

        groups_v2.append({
            "id": str(g.get("id", f"group_{idx + 1}")),
            "name": str(g.get("name", f"Group {idx + 1}")),
            "enabled": bool(g.get("enabled", True)),
            "fps": float(g.get("fps", 25.0)),
            "idle_timeout": float(g.get("idle_timeout", 3.0)),
            "proxy": proxy,
            "fixtures": fixtures,
            "effect": {
                "id": fx_id,
                "enabled": bool(w.get("on", False)),
                "params": {
                    "color": list(w.get("color", [255, 0, 85]))[:3],
                    "color2": list(w.get("color2", [0, 240, 255]))[:3],
                    "palette": str(w.get("palette", "cyberpunk")),
                    "brightness": bri_val,
                    "bass_boost": float(w.get("bass_boost", 1.3)),
                    "snare_flash": float(w.get("snare_flash", 1.0)),
                },
            },
            "inputs": {
                "ddp": {"enabled": bool(g.get("ddp_enabled", True)), "port": int(g.get("ddp_port", 4048 + idx))},
                "e131": {"enabled": bool(g.get("e131_enabled", True)), "universe": int(g.get("e131_universe", idx + 1))},
                "raw": {"enabled": bool(g.get("raw_enabled", True)), "port": int(g.get("raw_port", 2391 + idx))},
                "wled": {"enabled": bool(g.get("wled_udp_enabled", True)), "port": int(g.get("wled_udp_port", 21324 + idx))},
            },
            "outputs": {
                "hue": True,
                "ddp": False,
                "e131": False,
                "wled": False,
            },
        })

    if groups_v2:
        cfg["groups"] = groups_v2
    if proxies:
        cfg["hue"]["proxies"] = proxies
        cfg["hue"]["enabled"] = True
    return cfg


def validate_config(cfg, known_effects=None):
    """Validate v2 config dictionary. Returns (ok: bool, errors: list[str])."""
    errors = []
    if not isinstance(cfg, dict):
        return False, ["Config must be a JSON object"]

    audio = cfg.get("audio", {})
    if audio.get("source") not in ("auto", "parec", "snapcast", "sendspin", "wav", "synthetic"):
        errors.append(f"Invalid audio.source: {audio.get('source')}")
    if int(audio.get("rate", 48000)) not in (44100, 48000):
        errors.append("audio.rate must be 44100 or 48000")
    if int(audio.get("window", 1024)) not in (512, 1024, 2048):
        errors.append("audio.window must be 512, 1024, or 2048")

    hue = cfg.get("hue", {})
    max_fpf = int(hue.get("max_fixtures_per_frame", 10))
    if not (1 <= max_fpf <= 10):
        errors.append("hue.max_fixtures_per_frame must be between 1 and 10")
    if hue.get("brightness_mode") not in ("max", "luminance"):
        errors.append("hue.brightness_mode must be 'max' or 'luminance'")

    groups = cfg.get("groups", [])
    if not isinstance(groups, list):
        errors.append("groups must be a list")
        return False, errors

    seen_ids = set()
    used_ports = {}  # port -> (group_id, proto)
    for g in groups:
        gid = str(g.get("id", "")).strip()
        if not gid:
            errors.append("Every group must have a non-empty 'id'")
            continue
        if gid in seen_ids:
            errors.append(f"Duplicate group id: {gid}")
        seen_ids.add(gid)

        fps = float(g.get("fps", 30.0))
        if not (1.0 <= fps <= 120.0):
            errors.append(f"Group '{gid}' fps must be between 1 and 120")

        for f in g.get("fixtures", []):
            addr = str(f.get("addr", "")).strip()
            if addr:
                try:
                    val = int(addr, 0)
                    if not (0 <= val <= 0xFFFF):
                        errors.append(f"Fixture '{f.get('name')}' addr out of 16-bit range: {addr}")
                except ValueError:
                    errors.append(f"Fixture '{f.get('name')}' invalid hex/int address: {addr}")

        fx = g.get("effect", {})
        fx_id = fx.get("id")
        if known_effects is not None and fx_id and fx_id not in known_effects:
            errors.append(f"Group '{gid}' references unknown effect '{fx_id}'")

        if g.get("enabled", True):
            inputs = g.get("inputs", {})
            for proto in ("ddp", "raw", "wled"):
                p_cfg = inputs.get(proto, {})
                if p_cfg.get("enabled"):
                    port = int(p_cfg.get("port", 0))
                    if not (1024 <= port <= 65535):
                        errors.append(f"Group '{gid}' {proto} port must be 1024..65535")
                    elif port in used_ports:
                        other_gid, other_proto = used_ports[port]
                        errors.append(
                            f"UDP input port collision on {port}: "
                            f"group '{gid}' ({proto}) vs '{other_gid}' ({other_proto})"
                        )
                    else:
                        used_ports[port] = (gid, proto)

    return len(errors) == 0, errors


def save_config(cfg, path=DEFAULT_CONFIG_PATH):
    """Atomically write config JSON to disk (single JSON file, no DB)."""
    # ponytail: single JSON file rewrite per save; upgrade to SQLite only if multi-user history needed
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(path):
        try:
            shutil.copyfile(path, path + ".bak")
        except OSError:
            pass
    fd, tmp_path = tempfile.mkstemp(prefix=".cfg_", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def load_config(path=DEFAULT_CONFIG_PATH, legacy_path=LEGACY_CONFIG_PATH):
    """Load v2 config, migrating from legacy v1 if needed, or initializing default."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if int(raw.get("version", 1)) < 2:
            cfg = migrate_v1_to_v2(raw)
            save_config(cfg, path)
            return cfg
        return _deep_merge(default_config(), raw)

    if legacy_path and os.path.exists(legacy_path):
        with open(legacy_path, "r", encoding="utf-8") as f:
            raw_v1 = json.load(f)
        try:
            shutil.copyfile(legacy_path, legacy_path + ".bak")
        except OSError:
            pass
        cfg = migrate_v1_to_v2(raw_v1)
        save_config(cfg, path)
        return cfg

    cfg = default_config()
    save_config(cfg, path)
    return cfg
