from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from hueme_studio.ha import HaSettings, HomeAssistantBridge
from hueme_studio.server import create_app

ROOT = Path(__file__).resolve().parents[1]
RIGS_DIR = ROOT / "config" / "rigs"
SHOWS_DIR = ROOT / "config" / "shows"


class FakeMqtt:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, bool]] = []
        self.subscriptions: list[str] = []
        self.started = False
        self.disconnected = False
        self.will: tuple[str, str, bool] | None = None

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, str(payload), bool(retain)))

    def subscribe(self, topic, qos=0):
        self.subscriptions.append(topic)

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, str(payload), bool(retain))

    def reconnect_delay_set(self, min_delay=1, max_delay=30):
        self.reconnect = (min_delay, max_delay)

    def connect_async(self, host, port, keepalive=30):
        self.target = (host, port, keepalive)

    def loop_start(self):
        self.started = True

    def loop_stop(self):
        self.started = False

    def disconnect(self):
        self.disconnected = True

    def payloads(self, topic):
        return [payload for name, payload, _ in self.published if name == topic]


def test_discovery_and_state_topics_are_published():
    fake = FakeMqtt()
    bridge = HomeAssistantBridge(
        HaSettings(
            enabled=True,
            host="broker.test",
            port=1883,
            node_id="studio",
        ),
        mqtt_client=fake,
    )
    bridge.start()
    assert fake.started
    assert fake.will == ("hueme_studio/availability", "offline", True)

    bridge._on_connect(fake, None, {}, 0)
    assert fake.payloads("hueme_studio/availability") == ["online"]
    assert set(fake.subscriptions) == {
        "hueme_studio/playback/set",
        "hueme_studio/show/set",
        "hueme_studio/gain/set",
    }

    configs = {
        topic: json.loads(payload) for topic, payload, _ in fake.published
        if topic.startswith("homeassistant/") and topic.endswith("/config")
    }
    assert set(configs) == {
        "homeassistant/switch/studio_playback/config",
        "homeassistant/select/studio_show/config",
        "homeassistant/number/studio_gain/config",
        "homeassistant/sensor/studio_status/config",
    }
    assert configs["homeassistant/switch/studio_playback/config"][
        "command_topic"
    ] == "hueme_studio/playback/set"
    assert configs["homeassistant/select/studio_show/config"]["options"] == [
        "none"
    ]

    bridge.publish_state(
        transport={
            "playing": True,
            "show_id": "dance_loft_v1",
            "gain": 0.8,
            "time_s": 12.5,
            "duration_s": 236.25,
            "blackout": False,
        },
        shows=[{"id": "dance_loft_v1"}, {"id": "other"}],
        sync={"state": "locked"},
    )
    assert fake.payloads("hueme_studio/playback")[-1] == "ON"
    assert fake.payloads("hueme_studio/show")[-1] == "dance_loft_v1"
    assert fake.payloads("hueme_studio/gain")[-1] == "80"
    assert fake.payloads("hueme_studio/status")[-1] == "playing"
    attributes = json.loads(fake.payloads("hueme_studio/status/attributes")[-1])
    assert attributes["time_s"] == 12.5
    assert attributes["sync"]["state"] == "locked"
    select_config = json.loads(
        fake.payloads("homeassistant/select/studio_show/config")[-1]
    )
    assert select_config["options"] == ["dance_loft_v1", "other"]

    bridge.stop()
    assert fake.payloads("hueme_studio/availability")[-1] == "offline"


def test_commands_are_queued_for_the_runtime_tick():
    fake = FakeMqtt()
    bridge = HomeAssistantBridge(HaSettings(enabled=True), mqtt_client=fake)
    bridge.start()
    bridge._on_connect(fake, None, {}, 0)

    bridge._on_message(
        fake,
        None,
        SimpleNamespace(topic="hueme_studio/playback/set", payload=b"ON"),
    )
    bridge._on_message(
        fake,
        None,
        SimpleNamespace(topic="hueme_studio/show/set", payload=b"other"),
    )
    bridge._on_message(
        fake,
        None,
        SimpleNamespace(topic="hueme_studio/gain/set", payload=b"42"),
    )
    bridge._on_message(
        fake,
        None,
        SimpleNamespace(topic="hueme_studio/gain/set", payload=b"not-a-number"),
    )
    assert bridge.drain_commands() == [
        {"entity": "playback", "value": True},
        {"entity": "show", "value": "other"},
        {"entity": "gain", "value": 0.42},
    ]


def test_runtime_applies_ha_commands_and_exposes_status(tmp_path):
    rigs = tmp_path / "rigs"
    shows = tmp_path / "shows"
    shutil.copytree(RIGS_DIR, rigs)
    shutil.copytree(SHOWS_DIR, shows)
    fake = FakeMqtt()
    bridge = HomeAssistantBridge(HaSettings(enabled=True), mqtt_client=fake)
    app = create_app(rigs_dir=rigs, shows_dir=shows, fps=1.0, ha_bridge=bridge)
    with TestClient(app) as client:
        runtime = app.state.studio
        bridge._on_connect(fake, None, {}, 0)
        assert client.get("/api/ha").json()["connected"] is True

        bridge._on_message(
            fake,
            None,
            SimpleNamespace(topic="hueme_studio/show/set", payload=b"dance_loft_v1"),
        )
        bridge._on_message(
            fake,
            None,
            SimpleNamespace(topic="hueme_studio/gain/set", payload=b"55"),
        )
        runtime._apply_ha_commands()
        assert runtime.show_id == "dance_loft_v1"
        assert runtime.gain == 0.55

        bridge._on_message(
            fake,
            None,
            SimpleNamespace(topic="hueme_studio/playback/set", payload=b"ON"),
        )
        runtime._apply_ha_commands()
        assert runtime.playing is True

        runtime._publish_ha(force=True)
        assert fake.payloads("hueme_studio/gain")[-1] == "55"
