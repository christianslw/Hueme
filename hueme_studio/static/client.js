const FRAME_MAGIC = "HUE1";
const FRAME_HEADER = 16;

export function clamp(value, lo, hi) {
  return Math.max(lo, Math.min(hi, value));
}

export function formatTime(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(value / 60);
  const rest = value - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${rest.toFixed(3).padStart(6, "0")}`;
}

export function formatClock(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(value / 60);
  const rest = value % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

export function deepClone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "style") node.style.cssText = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function toast(message, kind = "info", timeout = 4000) {
  const closeIcon =
    '<svg viewBox="0 0 12 12" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.5"><path d="m2.5 2.5 7 7m0-7-7 7"/></svg>';
  let host = document.getElementById("toasts");
  if (!host) {
    host = el("div", { id: "toasts", class: "toasts", "aria-live": "polite" });
    document.body.append(host);
  }
  while (host.children.length >= 3) host.firstElementChild.remove();
  const node = el(
    "div",
    { class: `toast toast-${kind}`, role: "status" },
    el("span", { class: "toast-text", text: message }),
    el("button", {
      class: "toast-close",
      type: "button",
      "aria-label": "Dismiss",
      html: closeIcon,
      onclick: () => node.remove(),
    })
  );
  host.append(node);
  window.setTimeout(() => node.remove(), timeout);
  return node;
}

export class StudioClient extends EventTarget {
  constructor() {
    super();
    this.state = null;
    this.frame = null;
    this.ws = null;
    this.connected = false;
    this._refreshTimer = 0;
    this._reconnectTimer = 0;
  }

  async start() {
    await this.refresh();
    this.connect();
  }

  async refresh() {
    const state = await this.get("/api/studio/state");
    this.state = state;
    this.dispatchEvent(new Event("state"));
    return state;
  }

  scheduleRefresh() {
    if (this._refreshTimer) return;
    this._refreshTimer = window.setTimeout(() => {
      this._refreshTimer = 0;
      this.refresh().catch((error) => toast(error.message, "danger"));
    }, 120);
  }

  connect() {
    window.clearTimeout(this._reconnectTimer);
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${location.host}/ws/preview`);
    socket.binaryType = "arraybuffer";
    this.ws = socket;

    socket.addEventListener("open", () => {
      this.connected = true;
      this.dispatchEvent(new Event("connection"));
    });
    socket.addEventListener("close", () => {
      this.connected = false;
      this.dispatchEvent(new Event("connection"));
      this._reconnectTimer = window.setTimeout(() => this.connect(), 1200);
    });
    socket.addEventListener("error", () => {
      this.connected = false;
      this.dispatchEvent(new Event("connection"));
    });
    socket.addEventListener("message", (event) => {
      if (typeof event.data === "string") {
        let message;
        try {
          message = JSON.parse(event.data);
        } catch {
          return;
        }
        this._handleMessage(message);
      } else {
        this._decodeFrame(event.data);
      }
    });
  }

  _handleMessage(message) {
    if (message.type === "hello") {
      this.state = message.state;
      this.dispatchEvent(new Event("state"));
      return;
    }
    if (message.type === "transport") {
      if (this.state) this.state.playback = message.transport;
      this.dispatchEvent(new CustomEvent("transport", { detail: message.transport }));
      return;
    }
    if (message.type === "invalidate") {
      this.dispatchEvent(new CustomEvent("invalidate", { detail: message }));
      this.scheduleRefresh();
      return;
    }
    if (message.type === "mcp") {
      this.dispatchEvent(new CustomEvent("mcp", { detail: message }));
      return;
    }
    if (message.type === "error") {
      toast(message.message, "danger");
    }
  }

  _decodeFrame(buffer) {
    if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < FRAME_HEADER) return;
    const magic = String.fromCharCode(
      ...new Uint8Array(buffer, 0, FRAME_MAGIC.length)
    );
    if (magic !== FRAME_MAGIC) return;
    const view = new DataView(buffer);
    const count = view.getUint32(4, true);
    if (buffer.byteLength < FRAME_HEADER + count * 3) return;
    const time = view.getFloat32(8, true);
    const flags = view.getUint32(12, true);
    this.frame = {
      count,
      time,
      flags,
      rgb: new Uint8Array(buffer, FRAME_HEADER, count * 3),
      playing: Boolean(flags & 1),
      blackout: Boolean(flags & 2),
      loop: Boolean(flags & 4),
    };
    if (this.state?.playback) {
      this.state.playback.time_s = time;
      this.state.playback.playing = this.frame.playing;
      this.state.playback.blackout = this.frame.blackout;
      this.state.playback.loop = this.frame.loop;
    }
    this.dispatchEvent(new Event("frame"));
  }

  send(message) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  play() {
    if (this.connected) this.send({ type: "play" });
    else this.setTransport({ playing: true }).catch(() => {});
  }

  pause() {
    if (this.connected) this.send({ type: "pause" });
    else this.setTransport({ playing: false }).catch(() => {});
  }

  toggle() {
    const playing = this.state?.playback?.playing;
    if (this.connected) this.send({ type: "toggle" });
    else this.setTransport({ playing: !playing }).catch(() => {});
  }

  seek(time) {
    if (this.connected) this.send({ type: "seek", time_s: time });
    else this.setTransport({ time_s: time }).catch(() => {});
  }

  render(time) {
    this.send({ type: "render", time_s: time });
  }

  setTransport(patch) {
    return this.post("/api/transport", patch);
  }

  async get(path) {
    return this._json(await fetch(path));
  }

  async post(path, body) {
    return this._request("POST", path, body);
  }

  async put(path, body) {
    return this._request("PUT", path, body);
  }

  async patch(path, body) {
    return this._request("PATCH", path, body);
  }

  async del(path) {
    return this._request("DELETE", path);
  }

  async _request(method, path, body) {
    const options = { method, headers: {} };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return this._json(await fetch(path, options));
  }

  async _json(response) {
    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }
    }
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      if (data && typeof data.detail === "string") message = data.detail;
      else if (data && data.detail) message = JSON.stringify(data.detail);
      else if (typeof data === "string") message = data;
      throw new Error(message);
    }
    return data;
  }
}
