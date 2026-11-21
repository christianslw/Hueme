import { StudioClient, clamp, formatClock, formatTime, toast } from "./client.js";

const client = new StudioClient();
const $ = (selector) => document.querySelector(selector);

const dom = {
  showName: $("#show-name"),
  activeClip: $("#active-clip"),
  time: $("#live-time"),
  link: $("#link-status"),
  clock: $("#clock"),
  play: $("#play"),
  back: $("#back"),
  forward: $("#forward"),
  timeline: $("#timeline"),
  timelineValue: $("#timeline-value"),
  master: $("#master"),
  masterValue: $("#master-value"),
  blackout: $("#blackout"),
  auto: $("#auto"),
};

let state = null;
let scrubbing = false;
let gainTimer = 0;

function duration() {
  return Number(state?.show?.document?.duration_s || 0);
}

function currentTime() {
  return Number(client.frame?.time ?? state?.playback?.time_s ?? 0);
}

function activeClipLabel() {
  const doc = state?.show?.document;
  if (!doc) return "";
  const time = currentTime();
  for (const track of doc.tracks) {
    for (const clip of track.clips) {
      if (time >= clip.start_s && time < clip.end_s) {
        return `${track.name} / ${clip.name || clip.preset}`;
      }
    }
  }
  return "";
}

function updateTime() {
  const time = currentTime();
  dom.time.textContent = formatTime(time);
  if (!scrubbing) {
    dom.timeline.value = String(time);
    dom.timelineValue.textContent = formatClock(time);
  }
  dom.activeClip.textContent = activeClipLabel();
  dom.clock.textContent = new Date().toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function updateState() {
  state = client.state;
  if (!state) return;
  const doc = state.show?.document;
  dom.showName.textContent = doc?.name || "No show";
  dom.timeline.max = String(Math.max(0.1, duration()));
  dom.timeline.value = String(clamp(state.playback.time_s || 0, 0, duration()));
  dom.timelineValue.textContent = formatClock(state.playback.time_s || 0);
  dom.master.value = String(state.playback.gain ?? 1);
  dom.masterValue.textContent = `${Math.round((state.playback.gain ?? 1) * 100)}%`;
  dom.auto.checked = Boolean(state.playback.loop);
  dom.play.textContent = state.playback.playing ? "PAUSE" : "PLAY";
  updateTime();
}

function updateLink() {
  dom.link.textContent = client.connected ? "ONLINE" : "OFFLINE";
  dom.link.style.color = client.connected ? "var(--ok)" : "var(--text-2)";
}

function sendTransport(patch) {
  client.setTransport(patch).then((transport) => {
    if (state) state.playback = transport;
    dom.play.textContent = transport.playing ? "PAUSE" : "PLAY";
    dom.auto.checked = Boolean(transport.loop);
  }).catch((error) => toast(error.message, "danger"));
}

dom.play.addEventListener("click", () => client.toggle());
dom.back.addEventListener("click", () => client.seek(clamp(currentTime() - 10, 0, duration())));
dom.forward.addEventListener("click", () => client.seek(clamp(currentTime() + 10, 0, duration())));

dom.timeline.addEventListener("pointerdown", () => {
  scrubbing = true;
});
dom.timeline.addEventListener("pointerup", () => {
  scrubbing = false;
});
dom.timeline.addEventListener("pointercancel", () => {
  scrubbing = false;
});
dom.timeline.addEventListener("input", () => {
  const time = clamp(Number(dom.timeline.value), 0, duration());
  dom.timelineValue.textContent = formatClock(time);
  client.seek(time);
});

dom.master.addEventListener("input", () => {
  const gain = clamp(Number(dom.master.value), 0, 1);
  dom.masterValue.textContent = `${Math.round(gain * 100)}%`;
  window.clearTimeout(gainTimer);
  gainTimer = window.setTimeout(() => sendTransport({ gain }), 80);
});

dom.auto.addEventListener("change", () => sendTransport({ loop: dom.auto.checked }));

function setBlackout(active) {
  dom.blackout.classList.toggle("active", active);
  client.setTransport({ blackout: active }).catch((error) => toast(error.message, "danger"));
}

dom.blackout.addEventListener("pointerdown", (event) => {
  event.preventDefault();
  dom.blackout.setPointerCapture(event.pointerId);
  setBlackout(true);
});
dom.blackout.addEventListener("pointerup", () => setBlackout(false));
dom.blackout.addEventListener("pointercancel", () => setBlackout(false));
dom.blackout.addEventListener("contextmenu", (event) => event.preventDefault());
window.addEventListener("pointerup", () => {
  if (dom.blackout.classList.contains("active")) setBlackout(false);
});
window.addEventListener("blur", () => {
  if (dom.blackout.classList.contains("active")) setBlackout(false);
});
window.addEventListener("pagehide", () => {
  if (!dom.blackout.classList.contains("active")) return;
  fetch("/api/transport", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ blackout: false }),
    keepalive: true,
  }).catch(() => {});
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden && dom.blackout.classList.contains("active")) setBlackout(false);
});

client.addEventListener("state", updateState);
client.addEventListener("frame", updateTime);
client.addEventListener("transport", (event) => {
  if (state) state.playback = event.detail;
  if (event.detail) {
    dom.play.textContent = event.detail.playing ? "PAUSE" : "PLAY";
    dom.auto.checked = Boolean(event.detail.loop);
    dom.master.value = String(event.detail.gain ?? 1);
    dom.masterValue.textContent = `${Math.round((event.detail.gain ?? 1) * 100)}%`;
  }
  updateTime();
});
client.addEventListener("connection", updateLink);

window.setInterval(updateTime, 1000);

client.start().catch((error) => toast(error.message, "danger"));
