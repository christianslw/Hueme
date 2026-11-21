"""Run the Hueme Studio server: ``python -m hueme_studio``."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import uvicorn

from hueme_studio.ha import HaSettings, HomeAssistantBridge
from hueme_studio.server import PROJECT_ROOT, create_app


def _load_gateway(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"could not read gateway config {path}: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Hueme Studio preview server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=8090,
        help="default 8090; the legacy Hueme engine uses 8080",
    )
    parser.add_argument(
        "--rigs", default=str(PROJECT_ROOT / "config" / "rigs")
    )
    parser.add_argument(
        "--shows", default=str(PROJECT_ROOT / "config" / "shows")
    )
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument(
        "--gateway",
        default=None,
        help="optional legacy gateway.json; supplies MQTT + Sendspin defaults",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="lock the show playhead to Music Assistant via Sendspin",
    )
    parser.add_argument(
        "--sendspin-url",
        default=None,
        help="Sendspin server URL; implies --sync",
    )
    parser.add_argument(
        "--sync-offset-ms",
        type=float,
        default=0.0,
        help="positive delays the lights (ms)",
    )
    parser.add_argument(
        "--ha-discovery",
        action="store_true",
        help="publish Home Assistant MQTT discovery entities",
    )
    parser.add_argument("--ha-prefix", default="homeassistant")
    parser.add_argument("--mqtt-host", default=None)
    parser.add_argument("--mqtt-port", type=int, default=None)
    parser.add_argument("--mqtt-username", default=None)
    parser.add_argument("--mqtt-password", default=None)
    args = parser.parse_args()

    gateway_path = Path(args.gateway) if args.gateway else None
    gateway = _load_gateway(gateway_path)
    audio_cfg = gateway.get("audio", {}) if isinstance(gateway, dict) else {}
    if not isinstance(audio_cfg, dict):
        audio_cfg = {}
    sendspin_cfg = audio_cfg.get("sendspin", {})
    if not isinstance(sendspin_cfg, dict):
        sendspin_cfg = {}

    sync_source = None
    sendspin_url = args.sendspin_url or sendspin_cfg.get("url")
    if args.sync or args.sendspin_url:
        if not sendspin_url:
            print("sync enabled but no Sendspin URL configured; sync stays off")
        else:
            from hueme.sendspin_source import SendspinAudioSource

            state_dir = (
                str(gateway_path.resolve().parent)
                if gateway_path is not None
                else str(PROJECT_ROOT / "config")
            )
            sync_source = SendspinAudioSource(
                server_url=str(sendspin_url),
                client_name="Hueme-Studio",
                rate=int(audio_cfg.get("rate", 48000) or 48000),
                channels=int(audio_cfg.get("channels", 2) or 2),
                block=int(audio_cfg.get("block", 512) or 512),
                state_dir=state_dir,
            )

    ha_bridge = None
    if args.ha_discovery:
        mqtt_cfg = gateway.get("mqtt", {}) if isinstance(gateway, dict) else {}
        if not isinstance(mqtt_cfg, dict):
            mqtt_cfg = {}
        ha_bridge = HomeAssistantBridge(
            HaSettings(
                enabled=True,
                host=args.mqtt_host or str(mqtt_cfg.get("host") or "localhost"),
                port=int(args.mqtt_port or mqtt_cfg.get("port") or 1883),
                username=(
                    args.mqtt_username
                    if args.mqtt_username is not None
                    else str(mqtt_cfg.get("username") or "")
                ),
                password=(
                    args.mqtt_password
                    if args.mqtt_password is not None
                    else str(
                        os.environ.get("HUEME_MQTT_PASSWORD")
                        or mqtt_cfg.get("password")
                        or ""
                    )
                ),
                discovery_prefix=args.ha_prefix,
            )
        )

    app = create_app(
        rigs_dir=Path(args.rigs),
        shows_dir=Path(args.shows),
        fps=args.fps,
        persist_shows=not args.no_persist,
        sync_source=sync_source,
        sync_offset_s=args.sync_offset_ms / 1000.0,
        ha_bridge=ha_bridge,
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
