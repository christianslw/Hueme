# Home Assistant MQTT discovery

The Studio can publish four Home Assistant entities through MQTT discovery and
accept commands on the same broker. It is opt-in with `--ha-discovery`; without
it the server never opens an MQTT connection for HA.

## Setup

```bash
python3 -m hueme_studio \
  --gateway config/gateway.json \
  --ha-discovery
```

The broker comes from `gateway.json` -> `mqtt` (`host`, `port`, `username`,
`password`), or from the flags:

```bash
python3 -m hueme_studio --ha-discovery \
  --mqtt-host 192.168.10.13 --mqtt-port 1883 \
  --mqtt-username hass --mqtt-password secret \
  --ha-prefix homeassistant
```

Prefer `HUEME_MQTT_PASSWORD` over `--mqtt-password` so the secret does not
appear in the process list. Defaults: discovery prefix `homeassistant`, base
topic `hueme_studio`, node id `studio`, device name `Hueme Studio`.

## Entities

All entities share one Home Assistant device (`identifiers: ["studio"]`) and
one availability topic.

| Entity name | Unique id | State topic | Command topic | Purpose |
|---|---|---|---|---|
| Playback | `studio_playback` | `hueme_studio/playback` (`ON`/`OFF`) | `hueme_studio/playback/set` | play / pause |
| Show | `studio_show` | `hueme_studio/show` (show id) | `hueme_studio/show/set` | active show |
| Gain | `studio_gain` | `hueme_studio/gain` (0–100) | `hueme_studio/gain/set` | master preview gain |
| Status | `studio_status` | `hueme_studio/status` | — | `idle`, `playing`, `blackout` |

Home Assistant generates the entity id from the device/entity names; the unique
ids above are stable and are what automations should target through the UI.

`sensor.studio_status` also publishes JSON attributes on
`hueme_studio/status/attributes`: active show, playhead, duration, blackout and
the MA sync status.

Availability is `hueme_studio/availability` (`online` / `offline`) with an MQTT
last-will set to `offline`. State topics and discovery configs are retained, so
Home Assistant restores the last known state after a broker restart.

Discovery configs are published at:

```text
homeassistant/switch/studio_playback/config
homeassistant/select/studio_show/config
homeassistant/number/studio_gain/config
homeassistant/sensor/studio_status/config
```

The select options are refreshed when the show library changes. Commands are
queued at the MQTT callback and applied by the Studio runtime tick, never from
paho's network thread.

When MA sync is enabled, the active show and playhead follow Music Assistant;
manual `Show`/`Playback` commands are temporary until the next sync tick. Use
the Studio `MA` chip (or `POST /api/sync {"enabled": false}`) to hand control
back to manual transport.

## Diagnostics

`GET /api/ha` returns connection state, topics, entity ids and the last error:

```jsonc
{
  "configured": true,
  "enabled": true,
  "started": true,
  "connected": true,
  "host": "192.168.10.13",
  "port": 1883,
  "discovery_prefix": "homeassistant",
  "base_topic": "hueme_studio",
  "node_id": "studio",
  "entities": ["studio_playback", "studio_show",
               "studio_gain", "studio_status"],
  "last_error": null
}
```

## Deliberate limits

- No per-fixture light entities yet; the rig is runtime data and HAL output is
  still opt-in. `ha_light` fixtures continue to work through their own sink.
- No MQTT TLS/CA flags; paho supports it, add when a broker requires TLS.
- No HA WebSocket API, no auto-discovery of the broker (mDNS); give the host.
- Blackout is reported in attributes but not exposed as a fifth switch.
