"""Sendspin audio source for Hueme.

Connects to a Sendspin server (Music Assistant) with the official
``aiosendspin`` client, registers as a player/visualizer, and exposes the
received PCM as a blocking ``read()`` compatible with :class:`AudioEngine`.

The Sendspin protocol is encrypted (Noise KKpsk2), so using the SDK instead of
raw WebSocket frames is required. The client identity and pairing records are
persisted under ``config/`` so the server keeps treating Hueme as one device.
"""

import asyncio
import base64
from collections import deque
import json
import logging
import os
import threading
import time

import numpy as np

log = logging.getLogger("hueme.sendspin")

try:
    from aiosendspin.client import SendspinClient
    from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
    from aiosendspin.models.types import AudioCodec, Roles
    from aiosendspin.noise.keys import Identity, generate_psk, psk_id_for
    from aiosendspin.noise.trust_store import FileClientPairingStore, PairingPsk
    _AVAILABLE = True
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - optional dependency
    _AVAILABLE = False
    _IMPORT_ERROR = str(exc)


def _load_or_create_identity(state_dir):
    path = os.path.join(state_dir, "sendspin_identity.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("private", "")
            private = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            return Identity.from_private_bytes(private)
        except Exception as exc:
            log.warning("Could not load Sendspin identity, generating a new one: %s", exc)
    ident = Identity.generate()
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"private": ident.private_b64u, "client_id": ident.peer_id}, f)
    except OSError as exc:
        log.warning("Could not persist Sendspin identity: %s", exc)
    return ident


class SendspinAudioSource:
    """Blocking PCM source backed by the Sendspin client SDK."""

    def __init__(
        self,
        server_url="ws://192.168.10.13:8927/sendspin",
        client_name="Hueme-Visualizer",
        rate=48000,
        channels=2,
        block=512,
        stop_event=None,
        state_dir="config",
        metadata_only=False,
        drain_audio=False,
    ):
        self.server_url = server_url
        self.client_name = client_name
        self.rate = int(rate)
        self.channels = int(channels)
        self.block = int(block)
        self.stop_event = stop_event or threading.Event()
        self.state_dir = state_dir
        self.metadata_only = bool(metadata_only)
        self.drain_audio = bool(drain_audio)
        self._drain_thread = None
        self.connected = False
        self.last_error = _IMPORT_ERROR
        self.latency = 0.0
        self.bytes_per_frame = self.channels * 2
        self.bytes_per_second = self.rate * self.bytes_per_frame
        self.bytes_needed = self.block * self.bytes_per_frame
        self._buf = bytearray()
        self._buf_lock = threading.Lock()
        self._max_buffer = self.rate * self.bytes_per_frame * 2  # 2 s
        # PCM queue entries: [remaining bytes, play time (s), bytes consumed].
        # Play time is expressed in Hueme's time.monotonic() domain, converted
        # from aiosendspin's clock difference (server -> client).
        self._chunks = deque()
        self._timing_logged = False
        self.last_play_time = 0.0
        self.play_lead_s = 0.0
        self.last_send_ahead_s = 0.0
        self._client = None
        self._loop = None
        self._thread = None
        self.pairing_token = None
        self.client_id = None
        self.paired = False
        self.track_key = None
        self.track_title = ""
        self.track_artist = ""
        self.track_item_id = None
        self.track_duration_s = 0.0
        self._track_lock = threading.Lock()
        self._track_progress_base = 0.0
        self._track_progress_t0 = time.monotonic()
        self._track_speed = 1.0
        self.meta_lead_s = 0.0

    # -- lifecycle --------------------------------------------------------
    def start(self):
        if not _AVAILABLE:
            log.warning("aiosendspin not installed: %s", _IMPORT_ERROR)
            return
        if self._thread and self._thread.is_alive():
            return
        self.stop_event.clear()
        if self.drain_audio and not self.metadata_only:
            if self._drain_thread is None or not self._drain_thread.is_alive():
                self._drain_thread = threading.Thread(
                    target=self._drain_loop, name="hueme-sendspin-drain", daemon=True
                )
                self._drain_thread.start()
        self._thread = threading.Thread(target=self._run_loop, name="hueme-sendspin", daemon=True)
        self._thread.start()

    def _drain_loop(self):
        """Keep the PLAYER role fed even though Studio does not render PCM.

        A player that never reads its stream lets the Sendspin send-ahead/buffer
        grow (``audio_lead_s`` was 20+ s) and can disturb the MA group timing.
        The legacy AudioEngine reads the source itself; this drain is opt-in.
        """
        while not self.stop_event.is_set():
            try:
                self.read()
            except Exception:
                time.sleep(0.05)
            else:
                time.sleep(0.005)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("Sendspin loop stopped: %s", exc)
        finally:
            self.connected = False
            try:
                self._loop.close()
            except Exception:
                pass

    async def _main(self):
        backoff = 1.0
        while not self.stop_event.is_set():
            try:
                await self._connect_once()
                backoff = 1.0
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.connected = False
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("Sendspin connection failed: %s (retry in %.0fs)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(10.0, backoff * 1.5)

    async def _connect_once(self):
        self._timing_logged = False
        os.makedirs(self.state_dir, exist_ok=True)
        identity = _load_or_create_identity(self.state_dir)
        # ``FileClientPairingStore`` only reads the persisted records through its
        # async ``open()`` classmethod. Constructing it directly started with an
        # empty store on every reconnect: the old pairing record vanished, and a
        # new pairing PSK/token was generated each time, so MA could never keep
        # the client paired.
        pairing_store = await FileClientPairingStore.open(
            os.path.join(self.state_dir, "sendspin_pairings.json")
        )
        pairing_psk = await pairing_store.pairing_psk()
        if pairing_psk is None:
            psk = generate_psk()
            pairing_psk = PairingPsk(psk_id_for(psk), psk)
            await pairing_store.set_pairing_psk(pairing_psk)
        payload = identity.public_bytes + pairing_psk.psk
        body = base64.b32encode(payload).decode("ascii").rstrip("=").replace("2", "9")
        self.pairing_token = "SP:0" + body
        self.client_id = identity.peer_id
        log.info("Sendspin pairing token: %s", self.pairing_token)
        player_support = None
        roles = [Roles.METADATA]
        if not self.metadata_only:
            roles = [Roles.PLAYER, Roles.METADATA]
            player_support = ClientHelloPlayerSupport(
                supported_formats=[
                    SupportedAudioFormat(
                        codec=AudioCodec.PCM,
                        channels=self.channels,
                        sample_rate=self.rate,
                        bit_depth=16,
                    )
                ],
                # Advertise ~1 s, not 4 MiB (~22 s at 48 kHz stereo). The server
                # fills the advertised capacity ahead of playback; a huge value
                # inflated the MA group lead and dominated the perceived delay.
                buffer_capacity=max(self.bytes_per_second, 256 * 1024),
            )
        client = SendspinClient(
            identity,
            self.client_name,
            roles,
            pairing_store=pairing_store,
            player_support=player_support,
        )
        client.add_audio_chunk_listener(self._on_audio)
        client.add_metadata_listener(self._on_metadata)
        self._client = client
        log.info("Connecting to Sendspin server at %s ...", self.server_url)
        await client.connect(self.server_url)
        self.connected = True
        self.last_error = None
        psk = getattr(client, "noise_psk", None)
        self.paired = str(getattr(psk, "category", "")) == "long_term"
        log.info(
            "Sendspin connected (server: %s, paired: %s)",
            getattr(client.server_info, "name", "?"),
            self.paired,
        )
        try:
            while not self.stop_event.is_set():
                psk = getattr(client, "noise_psk", None)
                self.paired = str(getattr(psk, "category", "")) == "long_term"
                await asyncio.sleep(0.2)
        finally:
            self.connected = False
            self.paired = False
            try:
                await client.disconnect()
            except Exception:
                pass
            self._client = None

    def close(self):
        self.connected = False
        self.stop_event.set()
        client = self._client
        loop = self._loop
        if client is not None and loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(client.disconnect(), loop)
            except Exception:
                pass

    def restart(self):
        """Drop the current Sendspin session and reconnect with the same identity."""
        self.close()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        drain = self._drain_thread
        if drain is not None and drain.is_alive() and drain is not threading.current_thread():
            drain.join(timeout=1.5)
        self._drain_thread = None
        self._thread = None
        self._client = None
        self.connected = False
        self.paired = False
        self.stop_event.clear()
        self.start()

    # -- audio ------------------------------------------------------------
    def _drop_chunk_bytes_locked(self, n):
        """Drop `n` buffered bytes plus their timing metadata (front of queue)."""
        while n > 0 and self._chunks:
            head = self._chunks[0]
            take = min(n, head[0])
            head[0] -= take
            head[2] += take
            n -= take
            if head[0] <= 0:
                self._chunks.popleft()

    def _on_audio(self, server_timestamp_us, audio_data, audio_format, send_ahead):
        try:
            if audio_format.codec != AudioCodec.PCM:
                return
            pcm = audio_format.pcm_format
            if pcm.channels != self.channels or pcm.sample_rate != self.rate or pcm.bit_depth != 16:
                self.last_error = (
                    f"Sendspin stream format {pcm.sample_rate} Hz/{pcm.channels} ch/"
                    f"{pcm.bit_depth} bit differs from configured {self.rate}/{self.channels}/16"
                )
                return
            mono_now = time.monotonic()
            play_t = mono_now
            client = self._client
            if client is not None:
                try:
                    # Both values are in aiosendspin's clock domain (perf_counter
                    # on Windows, MONOTONIC_RAW on Linux); only their difference
                    # is used, so it maps cleanly onto time.monotonic().
                    client_now_us = int(client.now_us())
                    play_us = int(client.compute_play_time(int(server_timestamp_us)))
                    play_t = mono_now + (play_us - client_now_us) / 1e6
                except Exception:
                    play_t = mono_now
            self.last_play_time = play_t
            self.play_lead_s = play_t - mono_now
            self.latency = max(0.0, self.play_lead_s)
            valid_ahead = 0 < int(send_ahead) < 0xFFFFFFFF
            self.last_send_ahead_s = int(send_ahead) / 1e6 if valid_ahead else 0.0
            if not self._timing_logged:
                self._timing_logged = True
                log.info(
                    "Sendspin timing: first chunk play lead %.0f ms%s",
                    self.play_lead_s * 1000.0,
                    f", send-ahead {self.last_send_ahead_s * 1000.0:.0f} ms" if valid_ahead else "",
                )
            with self._buf_lock:
                self._buf.extend(audio_data)
                self._chunks.append([len(audio_data), play_t, 0])
                overflow = len(self._buf) - self._max_buffer
                if overflow > 0:
                    del self._buf[:overflow]
                    self._drop_chunk_bytes_locked(overflow)
        except Exception as exc:
            self.last_error = f"Sendspin audio decode: {exc}"

    def _on_metadata(self, payload):
        """Track identity + progress for instant ShowGraph cache lookup."""
        meta = getattr(payload, "metadata", None) if payload is not None else None
        title = str(getattr(meta, "title", "") or "").strip()
        artist = str(getattr(meta, "artist", "") or "").strip()
        item_id = str(
            getattr(meta, "item_id", "") or getattr(meta, "id", "") or ""
        ).strip()
        if not title and not artist:
            with self._track_lock:
                self.track_key = None
                self.track_title = ""
                self.track_artist = ""
                self.track_item_id = None
                self.track_duration_s = 0.0
            return

        progress = getattr(meta, "progress", None)
        base = 0.0
        speed = 1.0
        duration = 0.0
        if progress is not None:
            try:
                base = max(0.0, int(progress.track_progress) / 1000.0)
                speed = max(0.0, int(progress.playback_speed) / 1000.0)
                duration = max(0.0, int(progress.track_duration) / 1000.0)
            except (AttributeError, TypeError, ValueError):
                pass
        # ``metadata.timestamp`` is the server-clock time at which that
        # ``track_progress`` is valid.  Mapping it through the Sendspin clock to
        # the local monotonic domain lines the show up with the buffered audio
        # instead of assuming the metadata was valid the instant it arrived.
        now = time.monotonic()
        base_time = now
        lead = 0.0
        meta_ts = getattr(meta, "timestamp", None)
        client = self._client
        if meta_ts is not None and client is not None:
            try:
                play_us = int(client.compute_play_time(int(meta_ts)))
                delta = (play_us - int(client.now_us())) / 1e6
                # Ignore absurd clock samples; a normal buffered lead is < 5 s.
                if -5.0 <= delta <= 5.0:
                    lead = delta
                    base_time = now + delta
            except Exception:
                pass
        key = "|".join(part for part in (artist, title) if part) or title
        with self._track_lock:
            changed = key != self.track_key
            self.track_key = key
            self.track_title = title
            self.track_artist = artist
            self.track_item_id = item_id or None
            self.track_duration_s = duration
            self._track_progress_base = base
            self._track_progress_t0 = base_time
            self._track_speed = speed
            self.meta_lead_s = lead
        if changed:
            log.info("Sendspin track: '%s' (%.0fs)", key, duration)

    @property
    def track_playing(self) -> bool:
        """Best-effort playback state from metadata speed.

        Without progress metadata the source is treated as playing; a paused
        Sendspin item reports ``playback_speed == 0``.
        """
        with self._track_lock:
            if not self.track_key:
                return False
            return self._track_speed > 0.01

    @property
    def track_progress_s(self):
        with self._track_lock:
            if not self.track_key:
                return 0.0
            elapsed = max(0.0, time.monotonic() - self._track_progress_t0)
            progress = self._track_progress_base + elapsed * self._track_speed
            if self.track_duration_s > 0.0:
                progress = min(progress, self.track_duration_s)
            return max(0.0, progress)

    def read(self) -> np.ndarray:
        if not _AVAILABLE:
            time.sleep(0.05)
            return np.zeros(0, dtype=np.float32)
        deadline = time.monotonic() + 0.1
        play_t = None
        while not self.stop_event.is_set():
            with self._buf_lock:
                if len(self._buf) >= self.bytes_needed:
                    raw = bytes(self._buf[: self.bytes_needed])
                    del self._buf[: self.bytes_needed]
                    if self._chunks:
                        head = self._chunks[0]
                        # Play time of the first byte in this block, accounting
                        # for a partially consumed server chunk.
                        play_t = head[1] + head[2] / self.bytes_per_second
                        remaining = self.bytes_needed
                        while remaining > 0 and self._chunks:
                            chunk = self._chunks[0]
                            take = min(remaining, chunk[0])
                            chunk[0] -= take
                            chunk[2] += take
                            remaining -= take
                            if chunk[0] <= 0:
                                self._chunks.popleft()
                    break
            if time.monotonic() > deadline:
                return np.zeros(0, dtype=np.float32)
            time.sleep(0.002)
        else:
            return np.zeros(0, dtype=np.float32)
        if not raw:
            return np.zeros(0, dtype=np.float32)
        if play_t is not None:
            self.last_play_time = play_t
        arr = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if self.channels >= 2 and len(arr) >= self.channels:
            frames = len(arr) // self.channels
            arr = arr[: frames * self.channels].reshape(frames, self.channels).mean(axis=1)
        return arr
