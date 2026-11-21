"""Home Assistant MQTT discovery for the Studio runtime.

The bridge is intentionally a thin transport adapter: it publishes four
entities (playback switch, show select, master gain number and status sensor)
through retained MQTT topics and queues inbound commands for the runtime tick.
Home Assistant never calls into the asyncio loop from an MQTT callback.

Topics (``base_topic`` defaults to ``hueme_studio``)::

    hueme_studio/availability          online|offline
    hueme_studio/playback              ON|OFF
    hueme_studio/playback/set          ON|OFF command
    hueme_studio/show                  active show id
    hueme_studio/show/set              show id command
    hueme_studio/gain                  0..100
    hueme_studio/gain/set              0..100 command
    hueme_studio/status                idle|playing|blackout
    hueme_studio/status/attributes     JSON attributes

Discovery configs are published under ``<discovery_prefix>/<component>/...``.
The bridge degrades to a no-op when paho-mqtt is not installed or the broker is
unreachable; the preview server keeps running.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("hueme_studio.ha")

try:  # optional at runtime; paho is in requirements but tests inject a fake
    import paho.mqtt.client as mqtt
except Exception:  # pragma: no cover - optional dependency
    mqtt = None


class HaSettings(BaseModel):
    """Broker + entity naming for MQTT discovery."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    host: str = "localhost"
    port: int = Field(default=1883, gt=0, lt=65536)
    username: str = ""
    password: str = ""
    discovery_prefix: str = "homeassistant"
    base_topic: str = "hueme_studio"
    node_id: str = "studio"
    name: str = "Hueme Studio"
    keepalive_s: int = Field(default=30, gt=0, le=600)


class HomeAssistantBridge:
    """Publish discovery/state and collect commands from an MQTT broker."""

    def __init__(
        self,
        settings: HaSettings,
        *,
        mqtt_client: Any | None = None,
    ) -> None:
        self.settings = settings
        self._client = mqtt_client
        self._started = False
        self._connected = False
        self.last_error: str | None = None
        self._commands: deque[dict[str, Any]] = deque()
        self._published: dict[str, str] = {}
        self._lock = threading.Lock()
        self._last_state: dict[str, Any] = {}
        self._shows_signature: tuple[str, ...] = ()

    # -- topics -----------------------------------------------------------
    @property
    def base(self) -> str:
        return self.settings.base_topic.strip("/") or "hueme_studio"

    @property
    def prefix(self) -> str:
        return self.settings.discovery_prefix.strip("/") or "homeassistant"

    def topic(self, suffix: str) -> str:
        return f"{self.base}/{suffix.lstrip('/')}"

    def config_topic(self, component: str, object_id: str) -> str:
        return f"{self.prefix}/{component}/{object_id}/config"

    @property
    def connected(self) -> bool:
        return self._connected

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if not self.settings.enabled or self._started:
            return
        if self._client is None:
            self._client = self._make_client()
        if self._client is None:
            self.last_error = "paho-mqtt not installed"
            log.warning("HA discovery disabled: %s", self.last_error)
            return
        client = self._client
        try:
            client.on_connect = self._on_connect
            client.on_message = self._on_message
            client.on_disconnect = self._on_disconnect
            if hasattr(client, "reconnect_delay_set"):
                client.reconnect_delay_set(min_delay=1, max_delay=30)
            if self.settings.username and hasattr(client, "username_pw_set"):
                client.username_pw_set(
                    self.settings.username, self.settings.password
                )
            if hasattr(client, "will_set"):
                client.will_set(
                    self.topic("availability"), "offline", qos=0, retain=True
                )
            if hasattr(client, "connect_async"):
                client.connect_async(
                    self.settings.host,
                    int(self.settings.port),
                    keepalive=int(self.settings.keepalive_s),
                )
            else:
                client.connect(
                    self.settings.host,
                    int(self.settings.port),
                    keepalive=int(self.settings.keepalive_s),
                )
            if hasattr(client, "loop_start"):
                client.loop_start()
            self._started = True
            self.last_error = None
        except Exception as exc:  # keep the lighting server alive
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("HA MQTT start failed: %s", self.last_error)

    def stop(self) -> None:
        if not self._started:
            # Still let an injected client see the offline marker in tests.
            if self._connected:
                self._publish(self.topic("availability"), "offline", retain=True)
            self._connected = False
            return
        try:
            if self._connected:
                self._publish(self.topic("availability"), "offline", retain=True)
            if self._client is not None:
                if hasattr(self._client, "loop_stop"):
                    self._client.loop_stop()
                if hasattr(self._client, "disconnect"):
                    self._client.disconnect()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            self._connected = False
            self._started = False

    @staticmethod
    def _make_client() -> Any | None:
        if mqtt is None:
            return None
        try:
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except Exception:  # paho-mqtt < 2.0
            try:
                return mqtt.Client()
            except Exception:
                return None

    # -- MQTT callbacks ---------------------------------------------------
    def _on_connect(
        self,
        client: Any,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        if reason_code not in (0, None) and str(reason_code).lower() not in (
            "0",
            "success",
        ):
            self.last_error = f"connect rejected: {reason_code}"
            self._connected = False
            log.warning("HA MQTT connection rejected: %s", reason_code)
            return
        self._connected = True
        self.last_error = None
        self._published.clear()
        self._shows_signature = ()
        log.info(
            "HA MQTT connected to %s:%s", self.settings.host, self.settings.port
        )
        for suffix in ("playback/set", "show/set", "gain/set"):
            if hasattr(client, "subscribe"):
                client.subscribe(self.topic(suffix), qos=0)
        self._publish(self.topic("availability"), "online", retain=True)
        self._publish_discovery()
        with self._lock:
            state = dict(self._last_state)
        self._publish_state(state)

    def _on_disconnect(self, client: Any, userdata: Any, *args: Any) -> None:
        self._connected = False
        log.info("HA MQTT disconnected; paho will retry")

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        topic = str(getattr(message, "topic", ""))
        raw = getattr(message, "payload", b"")
        try:
            payload = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        except Exception:
            payload = ""
        payload = payload.strip()
        command: dict[str, Any] | None = None
        if topic == self.topic("playback/set"):
            value = payload.upper()
            if value in {"ON", "OFF"}:
                command = {"entity": "playback", "value": value == "ON"}
        elif topic == self.topic("show/set"):
            if payload:
                command = {"entity": "show", "value": payload}
        elif topic == self.topic("gain/set"):
            try:
                value = max(0.0, min(100.0, float(payload)))
            except ValueError:
                return
            command = {"entity": "gain", "value": value / 100.0}
        if command is not None:
            with self._lock:
                self._commands.append(command)

    def drain_commands(self) -> list[dict[str, Any]]:
        with self._lock:
            commands = list(self._commands)
            self._commands.clear()
        return commands

    # -- publishing -------------------------------------------------------
    def _publish(self, topic: str, payload: str, *, retain: bool = True) -> None:
        if self._client is None:
            return
        try:
            self._client.publish(topic, payload=payload, qos=0, retain=retain)
        except Exception as exc:
            self.last_error = f"publish {topic}: {type(exc).__name__}: {exc}"

    def _publish_config(self, topic: str, payload: dict[str, Any]) -> None:
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        if self._published.get(topic) == text:
            return
        self._published[topic] = text
        self._publish(topic, text, retain=True)

    def _device(self) -> dict[str, Any]:
        return {
            "identifiers": [self.settings.node_id],
            "name": self.settings.name,
            "manufacturer": "Hueme",
            "model": "Studio",
        }

    def _common(self, name: str, object_id: str) -> dict[str, Any]:
        return {
            "name": name,
            "unique_id": f"{self.settings.node_id}_{object_id}",
            "availability_topic": self.topic("availability"),
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": self._device(),
        }

    def _show_options(self) -> list[str]:
        with self._lock:
            shows = list(self._last_state.get("shows") or [])
        ids = [str(show.get("id")) for show in shows if show.get("id")]
        return ids or ["none"]

    def _publish_discovery(self) -> None:
        if self._client is None:
            return
        switch = {
            **self._common("Playback", "playback"),
            "state_topic": self.topic("playback"),
            "command_topic": self.topic("playback/set"),
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": "mdi:play-pause",
        }
        self._publish_config(
            self.config_topic("switch", f"{self.settings.node_id}_playback"),
            switch,
        )
        select = {
            **self._common("Show", "show"),
            "state_topic": self.topic("show"),
            "command_topic": self.topic("show/set"),
            "options": self._show_options(),
            "icon": "mdi:playlist-music",
        }
        self._publish_config(
            self.config_topic("select", f"{self.settings.node_id}_show"),
            select,
        )
        number = {
            **self._common("Gain", "gain"),
            "state_topic": self.topic("gain"),
            "command_topic": self.topic("gain/set"),
            "min": 0,
            "max": 100,
            "step": 1,
            "mode": "slider",
            "unit_of_measurement": "%",
            "icon": "mdi:brightness-6",
        }
        self._publish_config(
            self.config_topic("number", f"{self.settings.node_id}_gain"),
            number,
        )
        sensor = {
            **self._common("Status", "status"),
            "state_topic": self.topic("status"),
            "json_attributes_topic": self.topic("status/attributes"),
            "icon": "mdi:lightning-bolt",
        }
        self._publish_config(
            self.config_topic("sensor", f"{self.settings.node_id}_status"),
            sensor,
        )

    def publish_state(
        self,
        *,
        transport: dict[str, Any],
        shows: list[dict[str, Any]],
        sync: dict[str, Any] | None = None,
    ) -> None:
        state = {
            "transport": dict(transport or {}),
            "shows": list(shows or []),
            "sync": dict(sync or {}),
        }
        with self._lock:
            self._last_state = state
        if not self._connected:
            return
        signature = tuple(self._show_options())
        if signature != self._shows_signature:
            self._shows_signature = signature
            self._publish_discovery()
        self._publish_state(state)

    def _publish_state(self, state: dict[str, Any]) -> None:
        if self._client is None:
            return
        transport = state.get("transport") or {}
        playing = bool(transport.get("playing"))
        blackout = bool(transport.get("blackout"))
        playback = "ON" if playing and not blackout else "OFF"
        show_id = str(transport.get("show_id") or "")
        gain = int(round(max(0.0, min(1.0, float(transport.get("gain", 1.0)))) * 100.0))
        status = "blackout" if blackout else ("playing" if playing else "idle")
        attributes = {
            "show_id": show_id,
            "time_s": round(float(transport.get("time_s") or 0.0), 3),
            "duration_s": round(float(transport.get("duration_s") or 0.0), 3),
            "blackout": blackout,
            "sync": state.get("sync") or {},
        }
        self._publish(self.topic("playback"), playback)
        self._publish(self.topic("show"), show_id)
        self._publish(self.topic("gain"), str(gain))
        self._publish(self.topic("status"), status)
        self._publish(
            self.topic("status/attributes"),
            json.dumps(attributes, separators=(",", ":"), ensure_ascii=False),
        )

    # -- diagnostics ------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.settings.enabled),
            "started": self._started,
            "connected": self._connected,
            "host": self.settings.host,
            "port": int(self.settings.port),
            "discovery_prefix": self.prefix,
            "base_topic": self.base,
            "node_id": self.settings.node_id,
            "entities": [
                f"{self.settings.node_id}_{object_id}"
                for object_id in ("playback", "show", "gain", "status")
            ],
            "last_error": self.last_error,
        }


__all__ = ["HaSettings", "HomeAssistantBridge"]
