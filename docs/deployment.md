# Deployment

Hueme Studio ships as one Python image and one Compose service. The container
runs the FastAPI preview server on port 8090; the legacy engine on 8080 remains
available only if started explicitly.

## Compose

```bash
cp docker-compose.example.yml docker-compose.yml
docker compose up -d --build
curl -s localhost:8090/api/health
```

Open `http://<host>:8090/studio` (desktop) or `/live` (phone/tablet).

The example mounts `./config` at `/app/config`. That directory holds the rig
presets, shows, AudioBlueprint cache, Sendspin pairing/identity and
`gateway.json`. The gateway file is gitignored because it contains broker
credentials; never bake it into the image.

Enable MA sync and Home Assistant discovery by uncommenting the optional
command lines in `docker-compose.example.yml`:

```yaml
command:
  - python
  - -m
  - hueme_studio
  - --host
  - 0.0.0.0
  - --port
  - "8090"
  - --gateway
  - /app/config/gateway.json
  - --sync
  - --ha-discovery
```

MQTT password override:

```bash
HUEME_MQTT_PASSWORD=secret docker compose up -d
```

## Standalone Docker

```bash
docker build -t hueme-studio .
docker run -d --name hueme-studio \
  -p 8090:8090 \
  -v "$PWD/config:/app/config" \
  hueme-studio
```

The image includes the repository's `config/rigs` and `config/shows` as
defaults. A bind mount replaces them with the host's live library.

## Networking

- `8090/tcp` is the HTTP/WebSocket UI and REST/MCP transport.
- The Python CLI defaults to `--host 127.0.0.1`, which is loopback-only. For
  direct LAN access use `--host 0.0.0.0` (or `--host 192.168.10.31`) and restart.
  A laptop that can open `127.0.0.1:8090` against a remote server is using an
  SSH/VS Code port forward, not reaching the server's LAN address.
- The HTTP API has no authentication. Bind to `0.0.0.0` only on a trusted LAN,
  or put an authenticating reverse proxy in front of it.
- `4048/udp` DDP/WLED, `5568/udp` E1.31 and `2391/udp` raw input are declared
  for future HAL transport. The Studio transport does not drive HAL yet.
- DDP/E1.31 broadcast/multicast often works better with `network_mode: host`.
  If you switch, remove the `ports:` mapping.
- The Sendspin client is outbound WebSocket (`ws://...:8927`) and needs no
  inbound port.

## Files and permissions

The image runs as UID/GID 1000. Either run the host with matching ownership:

```bash
chown -R 1000:1000 config
```

or change `USER` in the Dockerfile for your deployment. The server writes:

```text
config/shows/                ShowDoc files + index
config/rigs/                 rig presets + index
config/blueprints/           content-addressed AudioBlueprint cache
config/show_cache/           legacy analyzer cache
config/sendspin_identity.json
config/sendspin_pairings.json
```

`config/gateway.json`, caches, pairings, logs and the 5.8 MB demo video are
excluded from the build context by `.dockerignore`.

## Health and logs

The image healthcheck hits `/api/health` every 15 s. `/api/health` reports
uptime, clients, playhead, sync state and HA connection state.

```bash
docker compose logs -f hueme
docker compose exec hueme python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8090/api/health').read().decode())"
```

## Verification

```bash
python3 -m pytest -q -W error
python3 -m compileall -q hueme_studio
node --check hueme_studio/static/studio.js
docker compose -f docker-compose.example.yml config
```
