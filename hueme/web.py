"""HTTP server and REST JSON API using stdlib ThreadingHTTPServer."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import time
from urllib.parse import urlparse

from hueme import __version__
from hueme.effects import validate_effect_source

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class HuemeHTTPHandler(BaseHTTPRequestHandler):
    """Stateless request handler backed by a shared LightingEngine instance."""

    # ponytail: no auth/TLS on LAN HTTP server; put behind Caddy/Nginx with token if exposed beyond LAN
    engine = None

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, data: dict, status: int = 200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_static(self, filename: str, content_type: str):
        fpath = os.path.join(STATIC_DIR, filename)
        if not os.path.isfile(fpath):
            self.send_error(404)
            return
        with open(fpath, "rb") as f:
            raw = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        eng = self.engine

        if path in ("/", "/index.html"):
            return self._send_static("index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self._send_static("app.js", "application/javascript; charset=utf-8")
        if path == "/style.css":
            return self._send_static("style.css", "text/css; charset=utf-8")

        if path == "/api/health":
            return self._send_json({
                "ok": True,
                "version": __version__,
                "uptime_s": round(time.monotonic() - eng.start_time, 1),
            })

        if path == "/api/config":
            return self._send_json({
                "config": eng.config,
                "z2m_devices": eng.get_z2m_devices(),
                "effects": eng.effects.list_effects(),
            })

        if path == "/api/status":
            return self._send_json(eng.get_status())

        if path == "/api/protocols":
            return self._send_json(eng.get_protocols_status())

        if path == "/api/logs":
            return self._send_json({"logs": list(eng.log_handler.records)})

        if path == "/api/sendspin/token":
            return self._send_json(eng.audio.get_sendspin_token())

        if path == "/api/effects":
            return self._send_json({"effects": eng.effects.list_effects()})

        if path.startswith("/api/effects/"):
            eid = path.split("/")[3]
            fx = eng.effects.get_effect(eid)
            if not fx:
                return self._send_json({"ok": False, "error": f"Effect '{eid}' not found"}, status=404)
            return self._send_json({"ok": True, "effect": fx})

        self.send_error(404)

    def do_PUT(self):
        path = urlparse(self.path).path
        eng = self.engine
        payload = self._read_json()

        if path == "/api/config":
            ok, errs = eng.apply_config(payload)
            if not ok:
                return self._send_json({"ok": False, "errors": errs}, status=400)
            return self._send_json({"ok": True, "config": eng.config})

        if path.startswith("/api/effects/"):
            eid = path.split("/")[3]
            source = str(payload.get("source", ""))
            res = eng.effects.save_effect(eid, source)
            if not res["ok"]:
                return self._send_json(res, status=400)
            return self._send_json(res)

        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        eng = self.engine
        payload = self._read_json()

        if path == "/api/config":
            ok, errs = eng.apply_config(payload)
            if not ok:
                return self._send_json({"ok": False, "errors": errs}, status=400)
            return self._send_json({"ok": True, "config": eng.config})

        if path in ("/api/audio_sens", "/api/audio/sensitivity"):
            sens = float(payload.get("sens", payload.get("sensitivity", 1.3)))
            eng.audio.set_sensitivity(sens)
            eng.config["audio"]["sensitivity"] = sens
            return self._send_json({"ok": True, "sensitivity": sens})

        if path == "/api/audio/latency":
            ms = eng.audio.set_latency(payload.get("ms", 0))
            eng.config.setdefault("audio", {})["latency_ms"] = ms
            return self._send_json({"ok": True, "latency_ms": ms})

        if path == "/api/audio/reconnect":
            eng.audio.start()
            return self._send_json({"ok": True})

        if path == "/api/sync/test":
            return self._send_json({"ok": True, "enabled": eng.set_sync_test(bool(payload.get("enabled")))})

        if path in ("/api/z2m/scan", "/api/scan_z2m"):
            devs = eng.scan_z2m()
            error = eng.hue_driver.stats.get("last_error")
            return self._send_json({"ok": error is None, "z2m_devices": devs, "error": error})

        if path == "/api/mqtt/test":
            return self._send_json(eng.test_mqtt())

        if path == "/api/outputs/test":
            return self._send_json(eng.test_output(str(payload.get("output", ""))))

        if path in ("/api/wled_control", "/api/groups/effect"):
            gid = str(payload.get("group_id", ""))
            ok = eng.update_group_effect(gid, payload)
            return self._send_json({"ok": ok}, status=200 if ok else 404)

        if path.startswith("/api/groups/") and path.endswith("/effect"):
            gid = path.split("/")[3]
            ok = eng.update_group_effect(gid, payload)
            return self._send_json({"ok": ok}, status=200 if ok else 404)

        if path == "/api/effects/validate":
            src = str(payload.get("source", ""))
            eid = str(payload.get("id", "custom"))
            return self._send_json(validate_effect_source(src, eid))

        if path == "/api/effects/reload":
            eng.effects.reload_all(force=True)
            return self._send_json({"ok": True, "effects": eng.effects.list_effects()})

        if path.startswith("/api/effects/") and path.endswith("/reload"):
            eid = path.split("/")[3]
            res = eng.effects.reload_one(eid)
            return self._send_json(res, status=200 if res.get("ok") else 400)

        self.send_error(404)


def create_http_server(engine, host="0.0.0.0", port=8080) -> ThreadingHTTPServer:
    HuemeHTTPHandler.engine = engine
    ThreadingHTTPServer.allow_reuse_address = True
    return ThreadingHTTPServer((host, int(port)), HuemeHTTPHandler)
