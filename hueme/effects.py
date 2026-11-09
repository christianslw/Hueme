"""Effect discovery, AST/schema validation, hot-reload watcher, and sandbox process supervisor."""

import ast
import logging
import multiprocessing
import os
import tempfile
import threading
import time

from hueme.sandbox import SAFE_BUILTINS, PALETTES, UTILS, sandbox_worker

log = logging.getLogger("hueme.effects")

VALID_PARAM_TYPES = {"range", "number", "color", "select", "bool"}


def validate_effect_source(source: str, effect_id: str = "custom") -> dict:
    """Validate syntax, META, PARAMS schema, and callable render(ctx)."""
    try:
        tree = ast.parse(source, filename=f"{effect_id}.py")
    except SyntaxError as e:
        return {
            "ok": False,
            "error": f"SyntaxError: {e.msg}",
            "line": e.lineno or 1,
            "col": e.offset or 1,
            "meta": None,
            "params": None,
        }

    # Check for forbidden imports or open/eval calls at AST level
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names]
            allowed = {"math", "random", "colorsys"}
            bad = [n for n in names if n not in allowed]
            if bad:
                return {
                    "ok": False,
                    "error": f"Forbidden import(s): {', '.join(bad)} (use ctx['utils'], math, random, colorsys)",
                    "line": getattr(node, "lineno", 1),
                    "col": getattr(node, "col_offset", 1),
                    "meta": None,
                    "params": None,
                }

    try:
        code = compile(source, f"{effect_id}.py", "exec")
        import colorsys
        import math
        import random

        ns = {
            "__builtins__": SAFE_BUILTINS,
            "math": math,
            "random": random,
            "colorsys": colorsys,
            "utils": UTILS,
            "PALETTES": PALETTES,
        }
        exec(code, ns, ns)
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "line": 1,
            "col": 1,
            "meta": None,
            "params": None,
        }

    meta = ns.get("META")
    if not isinstance(meta, dict) or not meta.get("name"):
        return {
            "ok": False,
            "error": "META dictionary with 'name' is required",
            "line": 1,
            "col": 1,
            "meta": None,
            "params": None,
        }
    meta = dict(meta)
    meta.setdefault("id", effect_id)
    meta.setdefault("description", "")

    params = ns.get("PARAMS", [])
    if not isinstance(params, list):
        return {
            "ok": False,
            "error": "PARAMS must be a list of parameter schema dicts",
            "line": 1,
            "col": 1,
            "meta": None,
            "params": None,
        }

    seen_keys = set()
    for p in params:
        if not isinstance(p, dict) or not p.get("key") or not p.get("type"):
            return {
                "ok": False,
                "error": "Each item in PARAMS must have 'key' and 'type'",
                "line": 1,
                "col": 1,
                "meta": None,
                "params": None,
            }
        k = str(p["key"])
        if k in seen_keys:
            return {
                "ok": False,
                "error": f"Duplicate parameter key '{k}' in PARAMS",
                "line": 1,
                "col": 1,
                "meta": None,
                "params": None,
            }
        seen_keys.add(k)
        if p["type"] not in VALID_PARAM_TYPES:
            return {
                "ok": False,
                "error": f"Unknown param type '{p['type']}' for '{k}' (valid: {sorted(VALID_PARAM_TYPES)})",
                "line": 1,
                "col": 1,
                "meta": None,
                "params": None,
            }

    render_fn = ns.get("render")
    if not callable(render_fn):
        return {
            "ok": False,
            "error": "Effect must define a callable render(ctx) function",
            "line": 1,
            "col": 1,
            "meta": None,
            "params": None,
        }

    return {
        "ok": True,
        "error": None,
        "line": None,
        "col": None,
        "meta": meta,
        "params": params,
    }


class EffectManager:
    """Supervises effect plugin files, hot-reloading, and the persistent sandbox child process."""

    # ponytail: one sandbox subprocess for all groups; upgrade to process-per-effect if untrusted multi-tenant
    def __init__(self, effects_dir="effects", poll_s=0.5, timeout_ms=40):
        self.effects_dir = effects_dir
        self.poll_s = float(poll_s)
        self.timeout_ms = float(timeout_ms)

        self.lock = threading.Lock()
        self.effects = {}  # id -> {id, meta, params, source, mtime, error, loaded_at}
        self.mtimes = {}
        self.frame_seq = 0
        self.restarts = 0
        self.last_sandbox_error = None

        self._ctx = multiprocessing.get_context("spawn")
        self._proc = None
        self._conn = None
        self._stop_event = threading.Event()
        self._watch_thread = None

    def start(self):
        os.makedirs(self.effects_dir, exist_ok=True)
        self._stop_event.clear()
        self._spawn_sandbox()
        self.reload_all(force=True)
        self._watch_thread = threading.Thread(target=self._watch_loop, name="hueme-fx-watch", daemon=True)
        self._watch_thread.start()

    def stop(self):
        self._stop_event.set()
        if self._watch_thread:
            self._watch_thread.join(timeout=1.0)
            self._watch_thread = None
        with self.lock:
            self._kill_sandbox()

    def _spawn_sandbox(self):
        self._kill_sandbox()
        parent_conn, child_conn = self._ctx.Pipe(duplex=True)
        proc = self._ctx.Process(target=sandbox_worker, args=(child_conn,), daemon=True)
        proc.start()
        child_conn.close()
        self._proc = proc
        self._conn = parent_conn

    def _kill_sandbox(self):
        if self._conn:
            try:
                self._conn.send({"op": "stop"})
            except Exception:
                pass
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        if self._proc:
            try:
                if self._proc.is_alive():
                    self._proc.terminate()
                    self._proc.join(timeout=0.4)
                if self._proc.is_alive():
                    self._proc.kill()
            except Exception:
                pass
            self._proc = None

    def _push_to_sandbox_locked(self, items):
        if not items:
            return
        if not self._proc or not self._proc.is_alive():
            self._spawn_sandbox()
        try:
            self._conn.send({"op": "load", "effects": items})
            if self._conn.poll(1.5):
                resp = self._conn.recv()
                errs = resp.get("errors", {}) if isinstance(resp, dict) else {}
                for eid, msg in errs.items():
                    if eid in self.effects:
                        self.effects[eid]["error"] = msg
            else:
                raise TimeoutError("Sandbox load timed out")
        except Exception as e:
            self.last_sandbox_error = str(e)
            self.restarts += 1
            self._spawn_sandbox()

    def reload_all(self, force=False):
        if not os.path.isdir(self.effects_dir):
            return
        to_load = []
        with self.lock:
            for fname in sorted(os.listdir(self.effects_dir)):
                if not fname.endswith(".py") or fname.startswith("_"):
                    continue
                eid = fname[:-3]
                fpath = os.path.join(self.effects_dir, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                except OSError:
                    continue
                if not force and self.mtimes.get(eid) == mtime:
                    continue
                self.mtimes[eid] = mtime
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        src = f.read()
                except OSError as e:
                    log.warning("Could not read effect %s: %s", fpath, e)
                    continue

                val = validate_effect_source(src, eid)
                if val["ok"]:
                    self.effects[eid] = {
                        "id": eid,
                        "meta": val["meta"],
                        "params": val["params"],
                        "source": src,
                        "mtime": mtime,
                        "error": None,
                        "loaded_at": time.time(),
                    }
                    to_load.append({"id": eid, "source": src})
                    log.info("Loaded effect plugin '%s'", eid)
                else:
                    err_str = f"Line {val['line']}: {val['error']}"
                    log.warning("Effect '%s' validation failed: %s", eid, err_str)
                    if eid in self.effects:
                        # Keep previous valid source running in sandbox, surface error for UI
                        self.effects[eid]["error"] = err_str
                    else:
                        self.effects[eid] = {
                            "id": eid,
                            "meta": {"id": eid, "name": eid, "description": "Invalid script"},
                            "params": [],
                            "source": src,
                            "mtime": mtime,
                            "error": err_str,
                            "loaded_at": time.time(),
                        }

            if to_load:
                self._push_to_sandbox_locked(to_load)

    def reload_one(self, eid: str) -> dict:
        fpath = os.path.join(self.effects_dir, f"{eid}.py")
        if not os.path.exists(fpath):
            return {"ok": False, "error": f"Effect '{eid}' file not found"}
        self.mtimes.pop(eid, None)
        self.reload_all(force=False)
        with self.lock:
            info = self.effects.get(eid, {})
            return {"ok": info.get("error") is None, "error": info.get("error")}

    def save_effect(self, eid: str, source: str) -> dict:
        eid = "".join(c for c in eid if c.isalnum() or c == "_").strip("_")
        if not eid:
            return {"ok": False, "error": "Invalid effect ID", "line": 1, "col": 1}

        val = validate_effect_source(source, eid)
        if not val["ok"]:
            return val

        os.makedirs(self.effects_dir, exist_ok=True)
        fpath = os.path.join(self.effects_dir, f"{eid}.py")
        fd, tmp = tempfile.mkstemp(prefix=f".fx_{eid}_", suffix=".py", dir=self.effects_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(source)
            os.replace(tmp, fpath)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

        with self.lock:
            mtime = os.path.getmtime(fpath)
            self.mtimes[eid] = mtime
            self.effects[eid] = {
                "id": eid,
                "meta": val["meta"],
                "params": val["params"],
                "source": source,
                "mtime": mtime,
                "error": None,
                "loaded_at": time.time(),
            }
            self._push_to_sandbox_locked([{"id": eid, "source": source}])
        return val

    def _watch_loop(self):
        while not self._stop_event.is_set():
            try:
                self.reload_all(force=False)
            except Exception as e:
                log.warning("Effect watcher error: %s", e)
            self._stop_event.wait(self.poll_s)

    def render_batch(self, requests: list) -> dict:
        """Render a batch of group effect requests in the sandbox with hard timeout protection.
        Returns {group_id: {"pixels": [...], "state": {...}, "error": str|None}}."""
        if not requests:
            return {}
        with self.lock:
            self.frame_seq += 1
            frame_id = self.frame_seq
            if not self._proc or not self._proc.is_alive():
                self._spawn_sandbox()
                valid = [{"id": k, "source": v["source"]} for k, v in self.effects.items() if not v.get("error")]
                self._push_to_sandbox_locked(valid)

            try:
                self._conn.send({"op": "render", "frame": frame_id, "requests": requests})
                if not self._conn.poll(self.timeout_ms / 1000.0):
                    raise TimeoutError(f"Effect sandbox exceeded {self.timeout_ms:.0f}ms budget")
                resp = self._conn.recv()
                if not isinstance(resp, dict) or resp.get("op") != "rendered":
                    raise RuntimeError("Malformed response from sandbox")
                out = {}
                for r in resp.get("results", []):
                    gid = r.get("group_id")
                    eid = r.get("id")
                    if r.get("error") and eid in self.effects:
                        self.effects[eid]["error"] = r["error"].splitlines()[0]
                    out[gid] = r
                return out
            except Exception as e:
                self.last_sandbox_error = str(e)
                self.restarts += 1
                log.warning("Effect sandbox fault (%s) -> restarting worker (#%d)", e, self.restarts)
                self._spawn_sandbox()
                valid = [{"id": k, "source": v["source"]} for k, v in self.effects.items() if not v.get("error")]
                self._push_to_sandbox_locked(valid)
                return {
                    req["group_id"]: {
                        "group_id": req["group_id"],
                        "id": req["id"],
                        "pixels": None,
                        "state": req.get("state") or {},
                        "error": str(e),
                    }
                    for req in requests
                }

    def list_effects(self) -> list:
        with self.lock:
            return [
                {
                    "id": v["id"],
                    "meta": v["meta"],
                    "params": v["params"],
                    "error": v["error"],
                    "loaded_at": v["loaded_at"],
                }
                for v in self.effects.values()
            ]

    def get_effect(self, eid: str) -> dict | None:
        with self.lock:
            v = self.effects.get(eid)
            return dict(v) if v else None

    def known_ids(self) -> set:
        with self.lock:
            return set(self.effects.keys())

    def default_params_for(self, eid: str) -> dict:
        with self.lock:
            v = self.effects.get(eid)
            if not v:
                return {}
            return {p["key"]: p.get("default") for p in v.get("params", []) if "key" in p}
