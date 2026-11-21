"""CLI entrypoint for Hueme daemon."""

import argparse
import logging
import multiprocessing
import os
import signal
import sys
import threading

from hueme.config import DEFAULT_CONFIG_PATH
from hueme.engine import LightingEngine
from hueme.web import create_http_server

_LOCK_FILE = None


def _acquire_single_instance(config_path):
    """Refuse a second Hueme process: duplicate senders fight over the same lamps."""
    global _LOCK_FILE
    lock_path = os.path.join(os.path.dirname(os.path.abspath(config_path)), "hueme.lock")
    try:
        handle = open(lock_path, "a+")
    except OSError:
        return True
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        print(f"Hueme is already running (lock {lock_path}). Stop the other instance first.", file=sys.stderr)
        return False
    _LOCK_FILE = handle
    return True


def main(argv=None):
    multiprocessing.freeze_support()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    ap = argparse.ArgumentParser(description="Hueme — Modular Reactive Lighting Platform")
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to gateway JSON config")
    ap.add_argument("--source", choices=["auto", "parec", "snapcast", "wav", "synthetic"], default=None)
    ap.add_argument("--web-port", type=int, default=None)
    ap.add_argument("--host", default=None, help="MQTT broker host override")
    args = ap.parse_args(argv)
    if not _acquire_single_instance(args.config):
        raise SystemExit(1)

    overrides = {
        "source": args.source,
        "web_port": args.web_port,
        "host": args.host,
    }
    engine = LightingEngine(config_path=args.config, cli_overrides=overrides)
    engine.start()

    web_cfg = engine.config.get("web", {})
    bind_host = web_cfg.get("host", "0.0.0.0")
    bind_port = int(web_cfg.get("port", 8080))
    server = create_http_server(engine, host=bind_host, port=bind_port)

    stop_ev = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_ev.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_ev.set())

    http_thread = threading.Thread(target=server.serve_forever, name="hueme-http", daemon=True)
    http_thread.start()
    print(f"[Hueme] Web UI & Reactive Lighting Engine running on http://{bind_host}:{bind_port}", flush=True)

    try:
        while not stop_ev.is_set():
            stop_ev.wait(0.3)
    finally:
        server.shutdown()
        server.server_close()
        engine.stop()


if __name__ == "__main__":
    main()
