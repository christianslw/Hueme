/* Hueme UI — plain JS, no build step.
   Rules: no flashing animation, no glow, everything readable, setup is a checklist. */

const S = {
  cfg: null,
  effects: [],
  status: null,
  proto: null,
  z2m: [],
  scanMsg: "",
  page: "overview",
  effectId: null,
  fxSource: "",
  fxNew: false,
  fxStatus: null,
  timer: null,
  saving: false,
  dirty: false,
  lastProto: 0,
  lastLogs: 0,
  lastZ2mCheck: 0,
  lastToken: 0,
  syncTest: false,
  liveTrimWarned: false,
  errors: [],
};

const $ = (sel) => document.querySelector(sel);
const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function getPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function setPath(obj, path, val) {
  const keys = path.split(".");
  let o = obj;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i];
    if (o[k] == null) o[k] = /^\d+$/.test(keys[i + 1]) ? [] : {};
    o = o[k];
  }
  o[keys[keys.length - 1]] = val;
}

const rgbToHex = (arr) => {
  const c = Array.isArray(arr) && arr.length >= 3 ? arr : [0, 0, 0];
  return "#" + c.slice(0, 3).map((v) => Math.max(0, Math.min(255, Math.round(Number(v) || 0))).toString(16).padStart(2, "0")).join("");
};

const hexToRgb = (hex) => {
  const s = String(hex || "#000000").replace("#", "");
  return [parseInt(s.slice(0, 2), 16) || 0, parseInt(s.slice(2, 4), 16) || 0, parseInt(s.slice(4, 6), 16) || 0];
};

const effectById = (id) => S.effects.find((e) => e.id === id) || { id, meta: { name: id }, params: [] };

const App = {
  // ------------------------------------------------------------------ state
  edit(path, val) {
    setPath(S.cfg, path, val);
    this.markDirty();
  },
  num(el, path) {
    const v = parseFloat(el.value);
    this.edit(path, Number.isFinite(v) ? v : 0);
  },
  int(el, path) {
    const v = parseInt(el.value, 10);
    this.edit(path, Number.isFinite(v) ? v : 0);
  },
  color(el, path) {
    this.edit(path, hexToRgb(el.value));
  },
  range(el, path, digits) {
    const v = parseFloat(el.value);
    this.edit(path, v);
    const out = el.closest(".field")?.querySelector(".range-val");
    if (out) out.textContent = Number(v).toFixed(digits ?? 2);
  },
  nudgeDelay(delta) {
    S.cfg.audio = S.cfg.audio || {};
    const cur = Number(S.cfg.audio.latency_ms ?? 0);
    S.cfg.audio.latency_ms = Math.max(-500, Math.min(2000, Math.round(cur + delta)));
    const el = document.getElementById("sync-trim-val");
    if (el) el.textContent = `${S.cfg.audio.latency_ms} ms`;
    const input = document.getElementById("visual-delay-input");
    if (input) input.value = S.cfg.audio.latency_ms;
    fetch("/api/audio/latency", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ms: S.cfg.audio.latency_ms }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        S.liveTrimWarned = false;
      })
      .catch(() => {
        if (!S.liveTrimWarned) {
          S.liveTrimWarned = true;
          this.toast("Live trim unavailable — restart the Hueme engine");
        }
      });
    this.markDirty();
  },
  setDelay(el) {
    const v = parseFloat(el.value);
    const ms = Number.isFinite(v) ? Math.round(v) : 0;
    S.cfg.audio = S.cfg.audio || {};
    this.nudgeDelay(ms - Number(S.cfg.audio.latency_ms ?? 0));
  },
  async toggleSyncTest() {
    const next = !S.syncTest;
    S.syncTest = next;
    const btn = document.getElementById("sync-test-btn");
    if (btn) btn.textContent = next ? "■ Stop sync test" : "▶ Sync test";
    try {
      const res = await fetch("/api/sync/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      if (typeof d.enabled !== "boolean") throw new Error("sync test unavailable");
      S.syncTest = !!d.enabled;
    } catch (e) {
      S.syncTest = !next;
      this.toast("Sync test unavailable — restart the Hueme engine");
    }
    if (btn) btn.textContent = S.syncTest ? "■ Stop sync test" : "▶ Sync test";
  },
  async refreshSendspinToken() {
    try {
      const res = await fetch("/api/sendspin/token");
      const d = await res.json();
      const box = document.getElementById("sendspin-pair");
      const input = document.getElementById("sendspin-token");
      if (box && input && d.token) {
        input.value = d.token;
        box.style.display = "block";
      }
    } catch (e) {}
  },
  copySendspinToken() {
    const input = document.getElementById("sendspin-token");
    if (!input || !input.value) return;
    navigator.clipboard?.writeText(input.value).then(
      () => this.toast("Sendspin token copied"),
      () => {
        input.select();
        document.execCommand?.("copy");
        this.toast("Sendspin token selected — press Ctrl+C");
      },
    );
  },
  setEffect(zoneIdx, effectId) {
    const g = S.cfg.groups[zoneIdx];
    const schema = effectById(effectId);
    const params = {};
    (schema.params || []).forEach((p) => (params[p.key] = p.default));
    // keep colors the user already picked, they usually mean the same thing
    const old = (g.effect && g.effect.params) || {};
    ["color", "color2", "color3"].forEach((k) => {
      if (params[k] !== undefined && old[k] !== undefined) params[k] = old[k];
    });
    g.effect = { id: effectId, enabled: true, params };
    this.markDirty();
    this.render();
  },
  setEffectAll(zoneIdx) {
    const src = S.cfg.groups[zoneIdx];
    if (!src || !src.effect) return;
    const fx = JSON.parse(JSON.stringify(src.effect));
    fx.enabled = true;
    (S.cfg.groups || []).forEach((g, i) => {
      if (i === zoneIdx) return;
      g.effect = JSON.parse(JSON.stringify(fx));
    });
    this.markDirty();
    this.render();
    const name = effectById(fx.id).meta.name || fx.id;
    this.toast(`"${name}" is now active in all ${(S.cfg.groups || []).length} zones`);
  },
  setProxy(zoneIdx, fixtureIdx) {
    S.cfg.groups[zoneIdx].proxy = S.cfg.groups[zoneIdx].fixtures[fixtureIdx].name;
    this.syncProxies();
    this.markDirty();
    this.render();
  },
  syncProxies() {
    S.cfg.hue = S.cfg.hue || {};
    S.cfg.hue.proxies = [...new Set((S.cfg.groups || []).map((g) => (g.proxy || "").trim()).filter(Boolean))];
  },
  ensureOrigins() {
    let changed = false;
    (S.cfg.groups || []).forEach((g, i) => {
      if (!Array.isArray(g.origin) || g.origin.length < 3) {
        g.origin = [i * 1.3, 0, 0];
        changed = true;
      }
    });
    if (changed) this.markDirty();
  },
  moveFixture(zoneIdx, fixtureIdx, dir) {
    const arr = S.cfg.groups[zoneIdx].fixtures;
    const to = fixtureIdx + dir;
    if (to < 0 || to >= arr.length) return;
    [arr[fixtureIdx], arr[to]] = [arr[to], arr[fixtureIdx]];
    this.markDirty();
    this.render();
  },
  addBlank(zoneIdx) {
    const arr = S.cfg.groups[zoneIdx].fixtures || (S.cfg.groups[zoneIdx].fixtures = []);
    const n = arr.length;
    arr.push({ name: `Spot ${n + 1}`, addr: "", x: 0.1 + (n % 3) * 0.35, y: 0.1 + Math.floor(n / 3) * 0.35, z: 0 });
    this.markDirty();
    this.render();
  },
  addFromZ2M(zoneIdx) {
    const sel = document.getElementById(`z2m-${zoneIdx}`);
    if (!sel || !sel.value) return;
    let d;
    try { d = JSON.parse(sel.value); } catch { return; }
    const arr = S.cfg.groups[zoneIdx].fixtures || (S.cfg.groups[zoneIdx].fixtures = []);
    const n = arr.length;
    arr.push({ name: d.n, addr: d.a, x: 0.1 + (n % 3) * 0.35, y: 0.1 + Math.floor(n / 3) * 0.35, z: 0 });
    if (!S.cfg.groups[zoneIdx].proxy) S.cfg.groups[zoneIdx].proxy = d.n;
    this.syncProxies();
    this.markDirty();
    this.render();
  },
  delFixture(zoneIdx, fixtureIdx) {
    const g = S.cfg.groups[zoneIdx];
    const [gone] = g.fixtures.splice(fixtureIdx, 1);
    if (gone && g.proxy === gone.name) g.proxy = "";
    this.syncProxies();
    this.markDirty();
    this.render();
  },
  addZone() {
    const n = (S.cfg.groups || []).length + 1;
    const idx = (S.cfg.groups || []).length;
    S.cfg.groups = S.cfg.groups || [];
    S.cfg.groups.push({
      id: `zone_${Date.now()}`,
      name: `Zone ${n}`,
      enabled: true,
      fps: 30,
      idle_timeout: 3,
      proxy: "",
      origin: [idx * 1.3, 0, 0],
      fixtures: [],
      effect: { id: "solid", enabled: true, params: { color: [255, 180, 100], brightness: 1 } },
      inputs: {
        ddp: { enabled: false, port: 4048 + n },
        e131: { enabled: false, universe: n },
        raw: { enabled: false, port: 2391 + n },
        wled: { enabled: false, port: 21324 + n },
      },
      outputs: { hue: true, ddp: false, e131: false, wled: false },
    });
    this.markDirty();
    this.render();
  },
  delZone(zoneIdx) {
    const g = S.cfg.groups[zoneIdx];
    if (!confirm(`Delete "${g.name}"? Its ${(g.fixtures || []).length} fixtures will be removed from the config.`)) return;
    S.cfg.groups.splice(zoneIdx, 1);
    this.syncProxies();
    this.markDirty();
    this.render();
  },

  // ------------------------------------------------------------------ saving
  markDirty() {
    S.dirty = true;
    this.setSaveState("Unsaved changes", "dirty");
    clearTimeout(S.timer);
    S.timer = setTimeout(() => this.save(), 450);
  },
  setSaveState(text, cls) {
    const el = $("#save-state");
    el.className = "save-state" + (cls ? ` ${cls}` : "");
    $("#save-text").textContent = text;
  },
  async saveNow() {
    clearTimeout(S.timer);
    return this.save();
  },
  async save() {
    if (!S.cfg || S.saving) return false;
    S.saving = true;
    this.setSaveState("Saving…");
    try {
      const res = await fetch("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(S.cfg),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || !d.ok) {
        S.errors = d.errors || ["The server rejected this configuration."];
        this.showErrors(S.errors);
        this.setSaveState(`Not saved — ${S.errors.length} problem${S.errors.length === 1 ? "" : "s"}`, "error");
        return false;
      }
      S.errors = [];
      S.dirty = false;
      this.showErrors([]);
      this.setSaveState("Saved " + new Date().toLocaleTimeString(), "");
      return true;
    } catch (e) {
      S.errors = [String(e)];
      this.showErrors(S.errors);
      this.setSaveState("Not saved — server unreachable", "error");
      return false;
    } finally {
      S.saving = false;
    }
  },
  showErrors(errors) {
    const box = $("#save-errors");
    if (!errors || !errors.length) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;
    box.innerHTML = `<strong>Configuration not applied</strong><ul>${errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`;
  },

  // ------------------------------------------------------------------ boot / nav
  async boot() {
    try {
      const res = await fetch("/api/config");
      const d = await res.json();
      S.cfg = d.config;
      S.effects = d.effects || [];
      S.z2m = d.z2m_devices || [];
      this.syncProxies();
      this.ensureOrigins();
      this.setSaveState("Loaded from config/gateway.json", "");
    } catch (e) {
      $("#page").innerHTML = `<div class="notice bad">Cannot reach the Hueme engine: ${esc(e)}</div>`;
      return;
    }
    this.render();
    await this.poll();
    setInterval(() => this.poll(), 100);
  },
  go(page) {
    S.page = page;
    this.sceneDrag = null;
    if (location.hash !== `#${page}`) history.replaceState(null, "", `#${page}`);
    document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
    this.render();
    window.scrollTo({ top: 0 });
  },
  render() {
    if (!S.cfg) return;
    const page = S.page;
    document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
    if (page === "overview") this.renderOverview();
    else if (page === "zones") this.renderZones();
    else if (page === "effects") this.renderEffects();
    else if (page === "outputs") this.renderOutputs();
    else if (page === "logs") this.renderLogs();
    this.updateLive();
  },

  // ------------------------------------------------------------------ overview
  setupRows() {
    const audio = (S.status && S.status.audio) || {};
    const hue = S.cfg.hue || {};
    const outputs = S.cfg.outputs || {};
    const zones = S.cfg.groups || [];
    const enabledZones = zones.filter((z) => z.enabled !== false);
    const fixtures = enabledZones.flatMap((z) => z.fixtures || []);
    const badAddr = enabledZones.some((z) => (z.fixtures || []).some((f) => !String(f.addr || "").trim()));
    const noFixtures = enabledZones.some((z) => !(z.fixtures || []).length);
    const missingProxy = hue.enabled && enabledZones.some((z) => (z.fixtures || []).length && !String(z.proxy || "").trim());
    const outNames = [
      hue.enabled ? "Hue" : null,
      (outputs.ddp || {}).enabled ? "DDP" : null,
      (outputs.e131 || {}).enabled ? "E1.31" : null,
      (outputs.wled || {}).enabled ? "WLED" : null,
    ].filter(Boolean);

    const rows = [
      {
        done: !!audio.alive,
        optional: false,
        title: "Audio input",
        detail: audio.alive
          ? `${audio.source} · ${audio.bpm || "–"} BPM · level ${Math.round((audio.rms || 0) * 100)}%`
          : esc(audio.error || "No audio frames arriving yet."),
        action: ["Audio settings", "App.openAudio()"],
      },
      {
        done: !badAddr && !noFixtures,
        optional: false,
        title: "Fixtures have Zigbee addresses",
        detail: badAddr
          ? "At least one fixture has no short address. Open Zones and fill it in."
          : noFixtures
          ? "At least one enabled zone has no fixtures yet."
          : `${fixtures.length} fixtures in ${enabledZones.length} enabled zone${enabledZones.length === 1 ? "" : "s"}.`,
        action: ["Edit zones", "App.go('zones')"],
      },
      {
        done: !!outNames.length,
        optional: false,
        title: "An output is enabled",
        detail: outNames.length ? outNames.join(", ") : "Hue, DDP, E1.31 and WLED are all off — nothing is being transmitted.",
        action: ["Configure outputs", "App.go('outputs')"],
      },
      {
        done: !!hue.enabled ? !missingProxy : true,
        optional: !hue.enabled,
        title: "Hue proxy per zone",
        detail: !hue.enabled
          ? "Only needed when Hue output is enabled."
          : missingProxy
          ? "Hue is on, but a zone has no proxy lamp selected. Choose one in Zones."
          : "Every zone has a proxy lamp.",
        action: ["Choose proxies", "App.go('zones')"],
      },
    ];

    return rows
      .map((r) => {
        const cls = r.done ? "done" : r.optional ? "" : "todo";
        return `<div class="check-row ${cls}">
          <span class="check-mark">✓</span>
          <div>
            <div class="check-title">${esc(r.title)}${r.optional ? ' <span class="muted" style="font-weight:400">(optional)</span>' : ""}</div>
            <div class="check-detail">${r.detail}</div>
          </div>
          <button class="small check-action" onclick="${r.action[1]}">${esc(r.action[0])}</button>
        </div>`;
      })
      .join("");
  },

  renderOverview() {
    const z = S.status || {};
    const audio = z.audio || {};
    const zones = S.cfg.groups || [];
    const html = `
      <h1>Overview</h1>
      <p class="muted small" style="margin-bottom:18px">What is running right now, and what still needs setting up.</p>

      <div class="card">
        <div class="card-head">
          <div><h2>Setup</h2><div class="sub">Each line is either done or tells you where to go.</div></div>
        </div>
        <div class="checklist" id="setup-list">${this.setupRows()}</div>
      </div>

      <div class="card">
        <div class="card-head">
          <div><h2>Audio</h2><div class="sub">Live bands and beat detection.</div></div>
          <div class="badges">
            <span class="badge" id="live-kick">KICK</span>
            <span class="badge" id="live-snare">SNARE</span>
            <span class="badge" id="live-bpm">– BPM</span>
            <span class="badge" id="live-key">KEY –</span>
            <span class="badge" id="live-section">–</span>
            <span class="badge" id="live-agc">AGC 1.0×</span>
          </div>
        </div>

        <div class="meters">
          ${this.meterHtml("bass", "Sub-bass", "20–120 Hz")}
          ${this.meterHtml("snare", "Snare / transients", "150–900 Hz")}
          ${this.meterHtml("mids", "Midrange", "400–2800 Hz")}
          ${this.meterHtml("highs", "Highs", "3–12 kHz")}
        </div>

        <div class="grid grid-3" style="margin-top:18px">
          <label class="field">Sensitivity
            <span class="row" style="justify-content:space-between"><span class="range-val">${Number((S.cfg.audio || {}).sensitivity ?? 1.3).toFixed(1)}×</span></span>
            <input type="range" min="0.4" max="3" step="0.1" value="${Number((S.cfg.audio || {}).sensitivity ?? 1.3)}"
              oninput="App.range(this,'audio.sensitivity',1)">
          </label>
          <div>
            <div class="hint">Source</div>
            <div style="font-weight:600" id="audio-source">${esc(audio.source || (S.cfg.audio || {}).source || "–")}</div>
          </div>
          <div>
            <div class="hint">State</div>
            <div style="font-weight:600" id="audio-state">${audio.alive ? "Receiving" : "No frames"}</div>
          </div>
        </div>

        <div class="row" style="margin-top:14px">
          <strong class="small">Sync trim</strong>
          <button class="small" onclick="App.nudgeDelay(-50)">−50 ms</button>
          <button class="small" onclick="App.nudgeDelay(-10)">−10 ms</button>
          <span class="badge" id="sync-trim-val">${Number((S.cfg.audio || {}).latency_ms ?? 0)} ms</span>
          <button class="small" onclick="App.nudgeDelay(10)">+10 ms</button>
          <button class="small" onclick="App.nudgeDelay(50)">+50 ms</button>
          <button class="small" id="sync-test-btn" onclick="App.toggleSyncTest()">▶ Sync test</button>
        </div>
        <div class="hint" id="sync-test-hint" style="margin-top:6px">negative = lights earlier · positive = later · sync test flashes white on each kick (beat fallback)</div>

        <div id="sendspin-pair" class="notice" style="margin-top:14px;display:none">
          <div class="row" style="justify-content:space-between">
            <strong>Sendspin pairing token</strong>
            <button class="small" onclick="App.copySendspinToken()">Copy</button>
          </div>
          <input id="sendspin-token" readonly style="width:100%;margin-top:8px;font-family:monospace;font-size:12px">
          <div class="hint" style="margin-top:6px">Music Assistant → player “Hueme Visualizer” → Setup required → Pair device → paste this token.</div>
        </div>

        <details class="adv" id="audio-adv">
          <summary>Audio input settings</summary>
          <div class="adv-body">
            <div class="grid grid-3">
              <label class="field">Source
                <select onchange="App.edit('audio.source', this.value)">
                  ${this.opt("auto", "Automatic (recommended)", S.cfg.audio.source)}
                  ${this.opt("synthetic", "Synthetic test beat", S.cfg.audio.source)}
                  ${this.opt("wav", "WAV file (looping)", S.cfg.audio.source)}
                  ${this.opt("snapcast", "Snapcast TCP stream", S.cfg.audio.source)}
                  ${this.opt("sendspin", "Sendspin (Music Assistant)", S.cfg.audio.source)}
                  ${this.opt("parec", "PulseAudio / PipeWire (parec)", S.cfg.audio.source)}
                </select>
              </label>
              <label class="field">PulseAudio device / monitor
                <input type="text" value="${esc(S.cfg.audio.device)}" onchange="App.edit('audio.device', this.value)">
              </label>
              <label class="field">WAV file path
                <input type="text" value="${esc(S.cfg.audio.wav || "")}" onchange="App.edit('audio.wav', this.value)">
              </label>
              <label class="field">Snapcast host
                <input type="text" value="${esc((S.cfg.audio.snapcast || {}).host || "")}" onchange="App.edit('audio.snapcast.host', this.value)">
              </label>
              <label class="field">Snapcast port
                <input type="number" value="${esc((S.cfg.audio.snapcast || {}).port ?? 4953)}" onchange="App.int(this,'audio.snapcast.port')">
              </label>
              <label class="field">Sendspin URL
                <input type="text" value="${esc((S.cfg.audio.sendspin || {}).url || "ws://192.168.10.13:8927/sendspin")}" onchange="App.edit('audio.sendspin.url', this.value)">
              </label>
              <label class="field">Sample rate
                <select onchange="App.int(this,'audio.rate')">
                  ${this.opt(48000, "48000 Hz", S.cfg.audio.rate)}
                  ${this.opt(44100, "44100 Hz", S.cfg.audio.rate)}
                </select>
              </label>
              <label class="field">FFT window
                <select onchange="App.int(this,'audio.window')">
                  ${this.opt(512, "512 samples (~11 ms)", S.cfg.audio.window)}
                  ${this.opt(1024, "1024 samples (~21 ms)", S.cfg.audio.window)}
                  ${this.opt(2048, "2048 samples (~43 ms)", S.cfg.audio.window)}
                </select>
              </label>
              <label class="field">Block size
                <select onchange="App.int(this,'audio.block')">
                  ${this.opt(256, "256 samples", S.cfg.audio.block)}
                  ${this.opt(512, "512 samples", S.cfg.audio.block)}
                  ${this.opt(1024, "1024 samples", S.cfg.audio.block)}
                </select>
              </label>
              <label class="field">Channels
                <select onchange="App.int(this,'audio.channels')">
                  ${this.opt(1, "Mono", S.cfg.audio.channels)}
                  ${this.opt(2, "Stereo", S.cfg.audio.channels)}
                </select>
              </label>
              <label class="field">Kick decay (higher = shorter)
                <input type="number" step="0.5" value="${esc(S.cfg.audio.decay_kick ?? 10)}" onchange="App.num(this,'audio.decay_kick')">
              </label>
              <label class="field">Snare decay (higher = shorter)
                <input type="number" step="0.5" value="${esc(S.cfg.audio.decay_snare ?? 12)}" onchange="App.num(this,'audio.decay_snare')">
              </label>
              <label class="field" title="Below this level audio counts as silence. −55 dBFS is a good default; −45 can gate quiet passages.">
                Silence gate (dBFS)
                <input type="number" step="1" value="${esc((S.cfg.audio.agc || {}).silence_db ?? -55)}" onchange="App.num(this,'audio.agc.silence_db')">
              </label>
              <label class="field" title="Positive delays the lights so they land with the speaker; negative advances them.">
                Visual delay (ms)
                <input id="visual-delay-input" type="number" step="10" value="${esc(S.cfg.audio.latency_ms ?? 0)}" onchange="App.setDelay(this)">
                <span class="hint" id="audio-latency"></span>
              </label>
            </div>
            <div class="row" style="margin-top:14px">
              <label class="check"><input type="checkbox" ${(S.cfg.audio.agc || {}).enabled !== false ? "checked" : ""}
                onchange="App.edit('audio.agc.enabled', this.checked)"> Automatic gain control</label>
              <label class="field" style="flex-direction:row;align-items:center;gap:8px"
                title="Linear RMS target: 0.08 ≈ −22 dBFS (default). Use 0.05–0.12; watch the live AGC × — it should not sit at 2.5×.">
                Target RMS
                <input type="number" step="0.01" style="width:90px" value="${esc((S.cfg.audio.agc || {}).target_rms ?? 0.08)}"
                  onchange="App.num(this,'audio.agc.target_rms')">
              </label>
              <button class="small row-end" onclick="App.reconnectAudio()">Restart audio input</button>
            </div>
            ${audio.error ? `<div class="notice warn" style="margin-top:12px">Last audio error: ${esc(audio.error)}</div>` : ""}
          </div>
        </details>
      </div>

      <div class="card">
        <div class="card-head">
          <div><h2>Zones</h2><div class="sub">What each zone is showing right now.</div></div>
          <button class="small" onclick="App.go('zones')">Edit zones</button>
        </div>
        <div class="grid grid-2">
          ${zones.length ? zones.map((g, i) => this.zoneSummaryHtml(g, i)).join("") : `<div class="empty">No zones configured.</div>`}
        </div>
      </div>`;
    $("#page").innerHTML = html;
  },

  meterHtml(id, label, range) {
    return `<div class="meter">
      <div class="meter-top"><strong>${esc(label)}</strong><span class="meter-val" id="mv-${id}">0%</span></div>
      <div class="track"><div class="fill ${id}" id="mb-${id}"></div></div>
      <div class="hint">${esc(range)}</div>
    </div>`;
  },

  opt(value, label, current) {
    return `<option value="${esc(value)}" ${String(current) === String(value) ? "selected" : ""}>${esc(label)}</option>`;
  },

  zoneSummaryHtml(g, i) {
    const eff = effectById((g.effect || {}).id);
    return `<div class="zone-card" style="border:1px solid var(--line);border-radius:9px;padding:14px">
      <div class="zone-head">
        <h2 style="font-size:15.5px">${esc(g.name)}</h2>
        <span class="badge" data-zbadge="${esc(g.id)}">–</span>
        <label class="check row-end">
          <input type="checkbox" ${((g.effect || {}).enabled) ? "checked" : ""} onchange="App.edit('groups.${i}.effect.enabled', this.checked)">
          Effect
        </label>
      </div>
      <div class="swatches" data-sw="${esc(g.id)}"></div>
      <div class="row small muted">
        <span>${esc((eff.meta || {}).name || (g.effect || {}).id || "no effect")}</span>
        <span>·</span>
        <span>${esc(g.fps || 30)} fps</span>
        <span>·</span>
        <span>${(g.fixtures || []).length} fixtures</span>
      </div>
      <div class="badge bad" data-zerr="${esc(g.id)}" style="display:none"></div>
    </div>`;
  },

  openAudio() {
    this.go("overview");
    const d = document.getElementById("audio-adv");
    if (d) d.open = true;
    document.getElementById("audio-adv")?.scrollIntoView({ block: "center" });
  },

  async reconnectAudio() {
    await fetch("/api/audio/reconnect", { method: "POST" });
    this.toast("Audio input restarted");
  },

  // ------------------------------------------------------------------ zones
  paramsHtml(zoneIdx, group) {
    const fx = group.effect || {};
    const schema = effectById(fx.id);
    const params = schema.params || [];
    const cur = fx.params || {};
    if (!params.length) return `<div class="hint">This effect has no adjustable parameters.</div>`;
    return params
      .map((p) => {
        const val = cur[p.key] !== undefined ? cur[p.key] : p.default;
        const path = `groups.${zoneIdx}.effect.params.${p.key}`;
        if (p.type === "color") {
          return `<label class="field">${esc(p.label || p.key)}
            <input type="color" value="${rgbToHex(val)}" oninput="App.color(this,'${path}')">
          </label>`;
        }
        if (p.type === "range") {
          const step = p.step ?? 0.05;
          const digits = String(step).includes(".") ? String(step).split(".")[1].length : 0;
          return `<label class="field">
            <span class="row" style="justify-content:space-between"><span>${esc(p.label || p.key)}</span><span class="range-val">${Number(val).toFixed(digits)}</span></span>
            <input type="range" min="${esc(p.min ?? 0)}" max="${esc(p.max ?? 1)}" step="${esc(step)}" value="${esc(val)}"
              oninput="App.range(this,'${path}',${digits})">
          </label>`;
        }
        if (p.type === "select") {
          const opts = (p.options || [])
            .map((o) => (Array.isArray(o) ? { v: o[0], l: o[1] } : { v: o, l: o }))
            .map((o) => `<option value="${esc(o.v)}" ${String(val) === String(o.v) ? "selected" : ""}>${esc(o.l)}</option>`)
            .join("");
          return `<label class="field">${esc(p.label || p.key)}
            <select onchange="App.edit('${path}', this.value)">${opts}</select>
          </label>`;
        }
        if (p.type === "bool") {
          return `<label class="check" style="margin-top:22px"><input type="checkbox" ${val ? "checked" : ""}
            onchange="App.edit('${path}', this.checked)"> ${esc(p.label || p.key)}</label>`;
        }
        return `<label class="field">${esc(p.label || p.key)}
          <input type="number" step="${esc(p.step ?? 1)}" value="${esc(val)}" onchange="App.num(this,'${path}')">
        </label>`;
      })
      .join("");
  },

  fixtureRowsHtml(zoneIdx, group) {
    const fx = group.fixtures || [];
    if (!fx.length) return "";
    const roles = ["", "center", "edge", "low", "high"];
    return fx
      .map(
        (f, fi) => `<tr>
        <td class="move">
          <div class="row" style="gap:4px;flex-wrap:nowrap">
            <button class="icon" title="Move up" onclick="App.moveFixture(${zoneIdx},${fi},-1)">↑</button>
            <button class="icon" title="Move down" onclick="App.moveFixture(${zoneIdx},${fi},1)">↓</button>
          </div>
        </td>
        <td><input type="text" value="${esc(f.name)}" onchange="App.edit('groups.${zoneIdx}.fixtures.${fi}.name', this.value)"></td>
        <td style="width:130px"><input type="text" class="mono" placeholder="0x1234" value="${esc(f.addr || "")}"
          onchange="App.edit('groups.${zoneIdx}.fixtures.${fi}.addr', this.value)"></td>
        <td style="width:78px"><input type="number" id="fx-x-${zoneIdx}-${fi}" step="0.05" value="${Number(f.x ?? 0.5)}"
          onchange="App.num(this,'groups.${zoneIdx}.fixtures.${fi}.x');App.drawScene()"></td>
        <td style="width:78px"><input type="number" id="fx-y-${zoneIdx}-${fi}" step="0.05" value="${Number(f.y ?? 0.5)}"
          onchange="App.num(this,'groups.${zoneIdx}.fixtures.${fi}.y');App.drawScene()"></td>
        <td style="width:78px"><input type="number" id="fx-z-${zoneIdx}-${fi}" step="0.05" value="${Number(f.z ?? 0)}"
          onchange="App.num(this,'groups.${zoneIdx}.fixtures.${fi}.z');App.drawScene()"></td>
        <td style="width:110px"><select onchange="App.edit('groups.${zoneIdx}.fixtures.${fi}.role', this.value)">
          ${roles.map((r) => `<option value="${r}" ${String(f.role || "") === r ? "selected" : ""}>${r || "auto"}</option>`).join("")}
        </select></td>
        <td class="narrow">
          <label class="check" title="Hue broadcasts this zone through this lamp">
            <input type="radio" name="proxy-${zoneIdx}" ${group.proxy === f.name ? "checked" : ""}
              onchange="App.setProxy(${zoneIdx},${fi})"> proxy
          </label>
        </td>
        <td class="narrow"><button class="icon danger" title="Remove fixture" onclick="App.delFixture(${zoneIdx},${fi})">✕</button></td>
      </tr>`
      )
      .join("");
  },

  zoneBounds(g, gi = 0) {
    const o = Array.isArray(g.origin) && g.origin.length >= 3 ? g.origin : [gi * 1.3, 0, 0];
    const ox = Number(o[0]) || 0;
    const oy = Number(o[1]) || 0;
    const oz = Number(o[2]) || 0;
    const fx = g.fixtures || [];
    if (!fx.length) return { gi, ox, oy, oz, x0: ox, x1: ox + 1, y0: oy, y1: oy + 1, zmin: oz, zmax: oz };
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity, zmin = Infinity, zmax = -Infinity;
    fx.forEach((f) => {
      const x = ox + (Number(f.x ?? 0.5) || 0);
      const y = oy + (Number(f.y ?? 0.5) || 0);
      const z = oz + (Number(f.z ?? 0) || 0);
      x0 = Math.min(x0, x); x1 = Math.max(x1, x);
      y0 = Math.min(y0, y); y1 = Math.max(y1, y);
      zmin = Math.min(zmin, z); zmax = Math.max(zmax, z);
    });
    const minSize = 0.45;
    if (x1 - x0 < minSize) { const c = (x0 + x1) / 2; x0 = c - minSize / 2; x1 = c + minSize / 2; }
    if (y1 - y0 < minSize) { const c = (y0 + y1) / 2; y0 = c - minSize / 2; y1 = c + minSize / 2; }
    const pad = 0.12;
    return { gi, ox, oy, oz, x0: x0 - pad, x1: x1 + pad, y0: y0 - pad, y1: y1 + pad, zmin, zmax };
  },

  distributeZone(zoneIdx, axis) {
    const g = S.cfg.groups[zoneIdx];
    if (!g) return;
    const fx = g.fixtures || [];
    if (fx.length < 2) return;
    const key = axis === "y" ? "y" : "x";
    const vals = fx.map((f) => Number(f[key] ?? 0.5));
    let lo = Math.min(...vals);
    let hi = Math.max(...vals);
    if (hi - lo < 0.05) { lo = 0.1; hi = 0.9; }
    fx.forEach((f, i) => {
      f[key] = Math.round((lo + (hi - lo) * i / (fx.length - 1)) * 100) / 100;
    });
    this.markDirty();
    this.render();
  },

  drawScene() {
    const cv = document.getElementById("scene-canvas");
    if (!cv || !cv.getContext) return;
    const groups = S.cfg.groups || [];
    const W = cv.width;
    const H = cv.height;
    const ctx = cv.getContext("2d");
    const colors = ["#4fd1c5", "#f6ad55", "#9f7aea", "#63b3ed", "#f687b3", "#68d391", "#f6e05e", "#a0aec0"];
    const cos = Math.cos(Math.PI / 6);
    const sin = Math.sin(Math.PI / 6);

    const boxes = groups.map((g, gi) => this.zoneBounds(g, gi));
    const points = [];
    groups.forEach((g, gi) => {
      const b = boxes[gi];
      (g.fixtures || []).forEach((f, fi) => {
        points.push({
          gi, fi,
          x: b.ox + (Number(f.x ?? 0.5) || 0),
          y: b.oy + (Number(f.y ?? 0.5) || 0),
          z: b.oz + (Number(f.z ?? 0) || 0),
        });
      });
    });

    // Fixed isometric camera: X right, Y into depth, Z up.
    let view = this.sceneDrag && cv._view ? { ...cv._view } : null;
    if (!view) {
      const raw = [];
      const add = (x, y, z) => raw.push([(x - y) * cos, (x + y) * sin - z]);
      boxes.forEach((b) => {
        add(b.x0, b.y0, b.oz);
        add(b.x1, b.y0, b.oz);
        add(b.x1, b.y1, b.oz);
        add(b.x0, b.y1, b.oz);
        add(b.x0, b.y0, 0);
        add(b.x1, b.y1, 0);
        add((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2, b.oz + 0.22);
        add(b.x0, b.y0, b.oz + 0.9);
      });
      points.forEach((p) => add(p.x, p.y, p.z));
      if (!raw.length) {
        add(0, 0, 0);
        add(1, 1, 1);
      }
      const minX = Math.min(...raw.map((v) => v[0]));
      const maxX = Math.max(...raw.map((v) => v[0]));
      const minY = Math.min(...raw.map((v) => v[1]));
      const maxY = Math.max(...raw.map((v) => v[1]));
      const pad = 58;
      const scale = Math.min(
        (W - pad * 2) / Math.max(0.55, maxX - minX),
        (H - pad * 2) / Math.max(0.55, maxY - minY),
      );
      view = {
        cos, sin, scale,
        padX: (W - (maxX - minX) * scale) / 2,
        padY: (H - (maxY - minY) * scale) / 2,
        minPX: minX, maxPY: maxY,
        points: [], handles: [],
      };
    }
    view.points = [];
    view.handles = [];
    // screen = pad + (projected - minProjected) * scale; screen Y grows down.
    const project = (x, y, z) => {
      const px = (x - y) * cos;
      const py = (x + y) * sin - z;
      return [view.padX + (px - view.minPX) * view.scale, view.padY + (view.maxPY - py) * view.scale];
    };

    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = "#0a1018";
    ctx.fillRect(0, 0, W, H);

    let gx0 = -1, gx1 = 2, gy0 = -1, gy1 = 2;
    if (boxes.length) {
      gx0 = Math.floor(Math.min(...boxes.map((b) => b.x0))) - 1;
      gx1 = Math.ceil(Math.max(...boxes.map((b) => b.x1))) + 1;
      gy0 = Math.floor(Math.min(...boxes.map((b) => b.y0))) - 1;
      gy1 = Math.ceil(Math.max(...boxes.map((b) => b.y1))) + 1;
    }
    const step = gx1 - gx0 <= 8 && gy1 - gy0 <= 8 ? 0.5 : 1;
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(120,160,200,0.10)";
    for (let x = gx0; x <= gx1 + 1e-6; x += step) {
      const a = project(x, gy0, 0);
      const b = project(x, gy1, 0);
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
    }
    for (let y = gy0; y <= gy1 + 1e-6; y += step) {
      const a = project(gx0, y, 0);
      const b = project(gx1, y, 0);
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
    }

    // Axis triad + Z ticks.
    const origin = project(gx0, gy0, 0);
    const ax = project(gx1, gy0, 0);
    const ay = project(gx0, gy1, 0);
    const az = project(gx0, gy0, 1);
    ctx.strokeStyle = "#e06c6c";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(origin[0], origin[1]); ctx.lineTo(ax[0], ax[1]); ctx.stroke();
    ctx.fillStyle = "#e06c6c";
    ctx.font = "bold 12px sans-serif";
    ctx.fillText("X", ax[0] + 5, ax[1] + 4);
    ctx.strokeStyle = "#6cc78d";
    ctx.beginPath(); ctx.moveTo(origin[0], origin[1]); ctx.lineTo(ay[0], ay[1]); ctx.stroke();
    ctx.fillStyle = "#6cc78d";
    ctx.fillText("Y", ay[0] - 4, ay[1] - 6);
    ctx.strokeStyle = "#6c9ee0";
    ctx.beginPath(); ctx.moveTo(origin[0], origin[1]); ctx.lineTo(az[0], az[1]); ctx.stroke();
    ctx.fillStyle = "#6c9ee0";
    ctx.fillText("Z", az[0] - 4, az[1] - 6);
    const zTop = Math.max(1, Math.ceil(Math.max(...boxes.map((b) => b.zmax), 1)));
    for (let z = 0; z <= zTop; z++) {
      const p = project(gx0, gy0, z);
      ctx.strokeStyle = "rgba(108,158,224,0.7)";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(p[0] - 4, p[1]); ctx.lineTo(p[0] + 4, p[1]); ctx.stroke();
      ctx.fillStyle = "rgba(160,190,225,0.8)";
      ctx.font = "10px sans-serif";
      ctx.fillText(String(z), p[0] - 18, p[1] + 3);
    }

    // Auto zone boundaries from fixture positions, with height drop lines.
    boxes.forEach((b, gi) => {
      const c = colors[gi % colors.length];
      const corners = [
        project(b.x0, b.y0, b.oz), project(b.x1, b.y0, b.oz),
        project(b.x1, b.y1, b.oz), project(b.x0, b.y1, b.oz),
      ];
      const ground = [
        project(b.x0, b.y0, 0), project(b.x1, b.y0, 0),
        project(b.x1, b.y1, 0), project(b.x0, b.y1, 0),
      ];
      ctx.fillStyle = c + "12";
      ctx.strokeStyle = c + "99";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(corners[0][0], corners[0][1]);
      corners.slice(1).forEach((p) => ctx.lineTo(p[0], p[1]));
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      if (b.oz > 0.01) {
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = c + "55";
        ctx.beginPath();
        corners.forEach((p, i) => {
          ctx.moveTo(p[0], p[1]);
          ctx.lineTo(ground[i][0], ground[i][1]);
        });
        ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.fillStyle = c;
      ctx.font = "bold 12px sans-serif";
      const zs = Math.abs(b.zmax - b.zmin) < 0.01
        ? `z ${b.zmin.toFixed(2)}`
        : `z ${b.zmin.toFixed(2)}–${b.zmax.toFixed(2)}`;
      ctx.fillText(`${groups[gi].name || "Zone " + (gi + 1)}  ·  ${zs}`, corners[0][0] + 4, corners[0][1] - 6);
    });

    // Fixture dots.
    points.forEach((p, idx) => {
      const pos = project(p.x, p.y, p.z);
      const sg = ((S.status || {}).groups || []).find((item) => item.id === groups[p.gi].id);
      const px = sg && sg.pixels && sg.pixels[p.fi];
      const c = colors[p.gi % colors.length];
      ctx.beginPath();
      ctx.arc(pos[0], pos[1], 7, 0, Math.PI * 2);
      ctx.fillStyle = px ? `rgb(${px[0]},${px[1]},${px[2]})` : c;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#0a1018";
      ctx.stroke();
      ctx.fillStyle = "#d7e6f5";
      ctx.font = "11px sans-serif";
      ctx.fillText(`${p.gi + 1}.${p.fi + 1}`, pos[0] + 10, pos[1] - 6);
      ctx.fillStyle = "rgba(190,210,230,0.65)";
      ctx.font = "9px sans-serif";
      ctx.fillText(`z ${p.z.toFixed(2)}`, pos[0] + 10, pos[1] + 9);
      view.points.push({ x: pos[0], y: pos[1], gi: p.gi, fi: p.fi, z: p.z });
    });

    // Draggable zone handles at the group base.
    boxes.forEach((b, gi) => {
      const pos = project((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2, b.oz + 0.12);
      const c = colors[gi % colors.length];
      view.handles.push({ x: pos[0], y: pos[1], gi, z: b.oz });
      ctx.beginPath();
      ctx.moveTo(pos[0], pos[1] - 8);
      ctx.lineTo(pos[0] + 8, pos[1]);
      ctx.lineTo(pos[0], pos[1] + 8);
      ctx.lineTo(pos[0] - 8, pos[1]);
      ctx.closePath();
      ctx.fillStyle = c;
      ctx.fill();
      ctx.strokeStyle = "#e8f1fa";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.fillStyle = c;
      ctx.font = "11px sans-serif";
      ctx.fillText("move zone", pos[0] + 11, pos[1] + 4);
    });

    cv._view = view;
    if (!cv._wired) {
      cv._wired = true;
      const posOf = (ev) => {
        const r = cv.getBoundingClientRect();
        return [(ev.clientX - r.left) * (cv.width / r.width), (ev.clientY - r.top) * (cv.height / r.height)];
      };
      const readout = (text) => {
        const el = document.getElementById("scene-readout");
        if (el) el.textContent = text;
      };
      cv.addEventListener("pointerdown", (ev) => {
        const v = cv._view;
        if (!v) return;
        const [sx, sy] = posOf(ev);
        let hit = null;
        let best = 17 * 17;
        v.points.forEach((p) => {
          const d = (p.x - sx) ** 2 + (p.y - sy) ** 2;
          if (d < best) { best = d; hit = { kind: "fixture", gi: p.gi, fi: p.fi, z: p.z }; }
        });
        v.handles.forEach((h) => {
          const d = (h.x - sx) ** 2 + (h.y - sy) ** 2;
          if (d < best) { best = d; hit = { kind: "group", gi: h.gi, z: h.z }; }
        });
        if (!hit) return;
        const g = S.cfg.groups[hit.gi];
        const o = Array.isArray(g.origin) ? g.origin : [0, 0, 0];
        App.sceneDrag = {
          kind: hit.kind, gi: hit.gi, fi: hit.fi,
          sx, sy, view: v,
          ox: Number(o[0]) || 0, oy: Number(o[1]) || 0,
          fx: Number((g.fixtures[hit.fi] || {}).x ?? 0.5),
          fy: Number((g.fixtures[hit.fi] || {}).y ?? 0.5),
        };
        readout(hit.kind === "group"
          ? `Zone ${hit.gi + 1} origin: x ${Number(o[0]).toFixed(2)} · y ${Number(o[1]).toFixed(2)} · z ${Number(o[2]).toFixed(2)}`
          : `Spot ${hit.gi + 1}.${hit.fi + 1}: x ${Number((g.fixtures[hit.fi] || {}).x ?? 0.5).toFixed(2)} · y ${Number((g.fixtures[hit.fi] || {}).y ?? 0.5).toFixed(2)} · z ${Number((g.fixtures[hit.fi] || {}).z ?? 0).toFixed(2)}`);
        cv.setPointerCapture(ev.pointerId);
        ev.preventDefault();
      });
      cv.addEventListener("pointermove", (ev) => {
        const d = App.sceneDrag;
        if (!d) return;
        const [sx, sy] = posOf(ev);
        const dsx = (sx - d.sx) / Math.max(1e-6, d.view.scale);
        const dsy = (sy - d.sy) / Math.max(1e-6, d.view.scale);
        const c = d.view.cos;
        const s = d.view.sin;
        const dx = (dsx / c + dsy / s) / 2;
        const dy = (dsy / s - dsx / c) / 2;
        const snap = (val, grid) => Math.round(val / grid) * grid;
        const g = S.cfg.groups[d.gi];
        if (d.kind === "group") {
          const grid = ev.shiftKey ? 0.01 : 0.1;
          g.origin = g.origin || [0, 0, 0];
          g.origin[0] = snap(d.ox + dx, grid);
          g.origin[1] = snap(d.oy + dy, grid);
          const xi = document.getElementById(`grp-x-${d.gi}`);
          const yi = document.getElementById(`grp-y-${d.gi}`);
          if (xi) xi.value = g.origin[0];
          if (yi) yi.value = g.origin[1];
          readout(`Zone ${d.gi + 1} origin: x ${g.origin[0].toFixed(2)} · y ${g.origin[1].toFixed(2)} · z ${Number(g.origin[2] ?? 0).toFixed(2)}`);
        } else {
          const grid = ev.shiftKey ? 0.01 : 0.05;
          const f = (g.fixtures || [])[d.fi];
          if (!f) return;
          f.x = snap(d.fx + dx, grid);
          f.y = snap(d.fy + dy, grid);
          const xi = document.getElementById(`fx-x-${d.gi}-${d.fi}`);
          const yi = document.getElementById(`fx-y-${d.gi}-${d.fi}`);
          if (xi) xi.value = f.x;
          if (yi) yi.value = f.y;
          readout(`Spot ${d.gi + 1}.${d.fi + 1}: x ${f.x.toFixed(2)} · y ${f.y.toFixed(2)} · z ${Number(f.z ?? 0).toFixed(2)}`);
        }
        App.drawScene();
        App.markDirty();
      });
      const end = () => { App.sceneDrag = null; App.drawScene(); };
      cv.addEventListener("pointerup", end);
      cv.addEventListener("pointercancel", end);
    }
  },

  renderZones() {
    const zones = S.cfg.groups || [];
    const effectOptions = (current) =>
      S.effects
        .map((e) => `<option value="${esc(e.id)}" ${e.id === current ? "selected" : ""}>${esc((e.meta || {}).name || e.id)}${e.error ? " (broken)" : ""}</option>`)
        .join("");

    const cards = zones.map((g, i) => {
      const fx = g.effect || {};
      const schemaErr = effectById(fx.id).error;
      const inputs = g.inputs || {};
      const ddp = inputs.ddp || {}; const e131 = inputs.e131 || {}; const raw = inputs.raw || {}; const wled = inputs.wled || {};
      const opts = S.z2m
        .map((d) => `<option value='${esc(JSON.stringify({ n: d.friendly_name, a: d.network_address }))}'>${esc(d.friendly_name)} — ${esc(d.network_address)}</option>`)
        .join("");
      return `<div class="card">
        <div class="card-head">
          <div class="row" style="gap:12px;flex:1">
            <label class="check"><input type="checkbox" ${g.enabled !== false ? "checked" : ""}
              onchange="App.edit('groups.${i}.enabled', this.checked)"> Enabled</label>
            <input type="text" style="font-size:16px;font-weight:600;min-width:220px;flex:1" value="${esc(g.name)}"
              onchange="App.edit('groups.${i}.name', this.value)">
            ${g.proxy ? "" : `<span class="badge warn">no proxy</span>`}
          </div>
          <button class="small danger" onclick="App.delZone(${i})">Delete zone</button>
        </div>

        <div class="grid grid-3" style="margin-bottom:16px">
          <label class="field">Frames per second
            <input type="number" min="1" max="120" value="${esc(g.fps ?? 30)}" onchange="App.num(this,'groups.${i}.fps')">
          </label>
          <label class="field">Effect
            <span class="row">
              <select onchange="App.setEffect(${i}, this.value)">${effectOptions(fx.id)}</select>
              <button class="small" title="Copy this effect and its settings to every zone" onclick="App.setEffectAll(${i})">All zones</button>
            </span>
          </label>
          <label class="check" style="margin-top:24px">
            <input type="checkbox" ${fx.enabled ? "checked" : ""}
              onchange="App.edit('groups.${i}.effect.enabled', this.checked)"> Effect active
          </label>
        </div>

        ${schemaErr ? `<div class="notice bad" style="margin-bottom:14px">Effect file has an error: ${esc(schemaErr)}</div>` : ""}
        <div class="grid grid-3">${this.paramsHtml(i, g)}</div>

        <h3 style="margin:20px 0 8px">Zone position in the scene <span class="muted small" style="font-weight:400">— origin used by the fixed isometric view</span></h3>
        <div class="grid grid-3">
          <label class="field">Zone X
            <input id="grp-x-${i}" type="number" step="0.1" value="${Number((g.origin || [])[0] ?? 0)}"
              onchange="App.num(this,'groups.${i}.origin.0');App.drawScene()">
          </label>
          <label class="field">Zone Y
            <input id="grp-y-${i}" type="number" step="0.1" value="${Number((g.origin || [])[1] ?? 0)}"
              onchange="App.num(this,'groups.${i}.origin.1');App.drawScene()">
          </label>
          <label class="field">Zone Z (height)
            <input id="grp-z-${i}" type="number" step="0.1" value="${Number((g.origin || [])[2] ?? 0)}"
              onchange="App.num(this,'groups.${i}.origin.2');App.drawScene()">
          </label>
        </div>

        <h3 style="margin:20px 0 8px">Fixtures <span class="muted small" style="font-weight:400">— coordinates are local to the zone; drag them in the Scene view above</span></h3>
        ${(g.fixtures || []).length
          ? `<div class="table-wrap"><table>
              <thead><tr><th></th><th>Name</th><th>Zigbee address</th><th>X</th><th>Y</th><th>Z</th><th>Role</th><th>Proxy</th><th></th></tr></thead>
              <tbody>${this.fixtureRowsHtml(i, g)}</tbody>
            </table></div>`
          : `<div class="notice warn">No fixtures yet. Add a discovered Zigbee device or a blank fixture below.</div>`}

        <div class="row" style="margin-top:12px">
          ${S.z2m.length ? `<select id="z2m-${i}" style="min-width:240px"><option value="">Choose a Zigbee2MQTT device…</option>${opts}</select>
          <button class="small" onclick="App.addFromZ2M(${i})">Add device</button>`
          : `<button class="small" onclick="App.scanZ2M()">Scan Zigbee devices</button>
             <span class="hint">All devices on the Zigbee2MQTT broker will be listed for this zone.</span>`}
          <button class="small" onclick="App.addBlank(${i})">Add blank fixture</button>
        </div>
        <div class="row" style="margin-top:8px">
          <button class="small" onclick="App.distributeZone(${i}, 'x')">Space evenly on X</button>
          <button class="small" onclick="App.distributeZone(${i}, 'y')">Space evenly on Y</button>
          <span class="hint">keeps the zone's current light boundary</span>
        </div>

        <details class="adv" style="margin-top:16px">
          <summary>Incoming UDP overrides (advanced)</summary>
          <div class="adv-body grid grid-4">
            <label class="field">DDP
              <span class="row"><input type="checkbox" ${ddp.enabled ? "checked" : ""} onchange="App.edit('groups.${i}.inputs.ddp.enabled', this.checked)">
              <input type="number" value="${esc(ddp.port ?? 4048)}" onchange="App.int(this,'groups.${i}.inputs.ddp.port')" style="width:90px"></span>
            </label>
            <label class="field">E1.31 universe
              <span class="row"><input type="checkbox" ${e131.enabled ? "checked" : ""} onchange="App.edit('groups.${i}.inputs.e131.enabled', this.checked)">
              <input type="number" value="${esc(e131.universe ?? 1)}" onchange="App.int(this,'groups.${i}.inputs.e131.universe')" style="width:90px"></span>
            </label>
            <label class="field">Raw UDP
              <span class="row"><input type="checkbox" ${raw.enabled ? "checked" : ""} onchange="App.edit('groups.${i}.inputs.raw.enabled', this.checked)">
              <input type="number" value="${esc(raw.port ?? 2391)}" onchange="App.int(this,'groups.${i}.inputs.raw.port')" style="width:90px"></span>
            </label>
            <label class="field">WLED UDP
              <span class="row"><input type="checkbox" ${wled.enabled ? "checked" : ""} onchange="App.edit('groups.${i}.inputs.wled.enabled', this.checked)">
              <input type="number" value="${esc(wled.port ?? 21324)}" onchange="App.int(this,'groups.${i}.inputs.wled.port')" style="width:90px"></span>
            </label>
          </div>
          <div class="hint" style="margin-top:10px">While packets arrive on an enabled port, that zone shows the incoming pixels instead of the effect. Useful for Scenes/other controllers.</div>
        </details>
      </div>`;
    });

    $("#page").innerHTML = `
      <div class="row" style="justify-content:space-between;margin-bottom:18px">
        <div><h1>Zones &amp; fixtures</h1><p class="muted small">A zone is a group of lamps that share one effect. All zones live in one scene.</p></div>
        <button class="primary" onclick="App.addZone()">Add zone</button>
      </div>
      <div class="card" style="margin-bottom:18px">
        <div class="card-head">
          <div><h2>Scene</h2><div class="sub">Isometric view: X right, Y depth, Z up. Drag a fixture dot or a zone's diamond handle; heights (Z) are in the cards below and on the labels.</div></div>
          <span class="hint">X red · Y green · Z blue</span>
        </div>
        <canvas id="scene-canvas" class="scene-canvas" width="1000" height="500"></canvas>
        <div class="hint" id="scene-readout" style="margin-top:8px">Drag a fixture or a zone handle. Shift-drag for fine steps.</div>
      </div>
      ${cards.length ? cards.join("") : `<div class="card"><div class="empty">No zones yet. Add one to get started.</div></div>`}`;
    this.drawScene();
  },

  // ------------------------------------------------------------------ effects
  renderEffects() {
    if (!S.effects.length) {
      $("#page").innerHTML = `<div class="card"><div class="empty">No effect files found in <code>effects/</code>.</div></div>`;
      return;
    }
    if (!S.effectId) S.effectId = S.effects[0].id;
    const list = S.effects
      .map(
        (e) => `<div class="fx-item ${e.id === S.effectId ? "active" : ""}" onclick="App.loadEffect('${esc(e.id)}')">
          <div>
            <div class="fx-name">${esc((e.meta || {}).name || e.id)}</div>
            <div class="fx-file">${esc(e.id)}.py</div>
          </div>
          <span class="fx-badge badge ${e.error ? "bad" : "ok"}">${e.error ? "error" : "ok"}</span>
        </div>`
      )
      .join("");

    $("#page").innerHTML = `
      <h1 style="margin-bottom:16px">Effect editor</h1>
      <div class="ide">
        <div class="card">
          <div class="card-head"><h2>Effects</h2><button class="small" onclick="App.newEffect()">New</button></div>
          <div class="fx-list">${list}</div>
          <button class="small" style="margin-top:12px;width:100%" onclick="App.reloadEffects()">Reload all from disk</button>
        </div>
        <div class="card stack-sm">
          <div class="card-head" style="margin:0">
            <div>
              <h2 id="fx-title">${esc(S.effectId)}.py</h2>
              <div class="sub">Changes are live as soon as you save.</div>
            </div>
            <div class="row">
              <button class="small" onclick="App.validateEffect()">Validate</button>
              <button class="small primary" onclick="App.saveEffect()">Save &amp; reload</button>
            </div>
          </div>
          <textarea class="editor" id="fx-src" spellcheck="false" wrap="off" placeholder="Effect source…"></textarea>
          <div class="editor-status" id="fx-status">Pick an effect on the left.</div>
          <div>
            <h3 style="margin-bottom:8px">Parameters this effect exposes</h3>
            <div class="grid grid-3" id="fx-params"></div>
          </div>
        </div>
      </div>
      <div class="card" style="margin-top:18px">
        <div class="card-head"><div><h2>Scene preview</h2><div class="sub">Live pixels in the isometric scene. Drag fixtures or zone handles to reposition.</div></div></div>
        <canvas id="scene-canvas" class="scene-canvas" width="1000" height="500"></canvas>
      </div>`;

    this.drawScene();
    const ta = $("#fx-src");
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Tab") {
        e.preventDefault();
        const s = ta.selectionStart;
        ta.value = ta.value.slice(0, s) + "    " + ta.value.slice(ta.selectionEnd);
        ta.selectionStart = ta.selectionEnd = s + 4;
      }
    });
    if (S.fxSource && !S.fxNew) {
      ta.value = S.fxSource;
      this.showFxStatus(S.fxStatus);
      this.renderFxParams(S.fxStatus && S.fxStatus.params);
    } else if (S.fxNew) {
      ta.value = S.fxSource;
      this.showFxStatus({ ok: false, error: "New effect — not saved yet. Validate, then save." });
    } else {
      this.loadEffect(S.effectId);
    }
  },

  async loadEffect(id) {
    S.effectId = id;
    S.fxNew = false;
    S.fxSource = "";
    const res = await fetch(`/api/effects/${encodeURIComponent(id)}`);
    if (!res.ok) {
      this.toast("Could not load effect", true);
      return;
    }
    const d = await res.json();
    const fx = d.effect;
    S.fxSource = fx.source || "";
    S.fxStatus = fx.error ? { ok: false, error: fx.error } : { ok: true, params: fx.params };
    document.querySelectorAll(".fx-item").forEach((el) => el.classList.remove("active"));
    this.render();
  },

  showFxStatus(res) {
    const el = $("#fx-status");
    if (!el) return;
    if (!res) {
      el.className = "editor-status";
      el.textContent = "Not validated yet.";
      return;
    }
    if (res.ok) {
      el.className = "editor-status ok";
      el.textContent = "Syntax and parameter schema are valid.";
    } else {
      el.className = "editor-status bad";
      el.textContent = `${res.line ? `Line ${res.line}${res.col ? `, column ${res.col}` : ""}: ` : ""}${res.error || "Invalid effect"}`;
    }
  },

  renderFxParams(params) {
    const el = $("#fx-params");
    if (!el) return;
    if (!params || !params.length) {
      el.innerHTML = `<div class="hint">No parameters declared.</div>`;
      return;
    }
    el.innerHTML = params
      .map(
        (p) => `<div class="notice">
          <div class="row" style="justify-content:space-between"><strong>${esc(p.label || p.key)}</strong><span class="badge">${esc(p.type)}</span></div>
          <div class="hint" style="margin-top:4px">key <code>${esc(p.key)}</code> · default <code>${esc(JSON.stringify(p.default))}</code></div>
        </div>`
      )
      .join("");
  },

  async validateEffect() {
    const src = ($("#fx-src") || {}).value || "";
    const res = await fetch("/api/effects/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: S.effectId || "custom", source: src }),
    });
    const d = await res.json();
    S.fxSource = src;
    S.fxStatus = d;
    this.showFxStatus(d);
    this.renderFxParams(d.params);
  },

  async saveEffect() {
    if (!S.effectId) return;
    const src = ($("#fx-src") || {}).value || "";
    const res = await fetch(`/api/effects/${encodeURIComponent(S.effectId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: src }),
    });
    const d = await res.json();
    S.fxStatus = d;
    this.showFxStatus(d);
    if (!d.ok) {
      this.toast(`Not saved: ${d.line ? `line ${d.line}: ` : ""}${d.error}`, true);
      return;
    }
    S.fxSource = src;
    S.fxNew = false;
    this.renderFxParams(d.params);
    this.toast("Saved and hot-reloaded");
    const r = await fetch("/api/effects");
    const dd = await r.json();
    S.effects = dd.effects || S.effects;
    this.render();
  },

  newEffect() {
    const raw = prompt("New effect id (letters, digits, underscore):");
    if (!raw) return;
    const id = raw.toLowerCase().replace(/[^a-z0-9_]/g, "_").replace(/^_+|_+$/g, "");
    if (!id) return;
    S.effectId = id;
    S.fxNew = true;
    S.fxStatus = null;
    S.fxSource = `META = {
    "id": "${id}",
    "name": "${id.replace(/_/g, " ").replace(/\\b\\w/g, (c) => c.toUpperCase())}",
    "description": "Describe what this effect does.",
}

PARAMS = [
    {"key": "color", "label": "Primary color", "type": "color", "default": [255, 0, 85]},
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
]


def render(ctx):
    n = ctx["n"]
    features = ctx["features"]
    params = ctx["params"]
    utils = ctx["utils"]

    level = max(float(features.get("kick_env", 0.0)), float(features.get("bass", 0.0)))
    color = utils.scale(params.get("color", [255, 0, 85]), level * float(params.get("brightness", 1.0)))
    return [color] * n
`;
    this.render();
  },

  async reloadEffects() {
    const res = await fetch("/api/effects/reload", { method: "POST" });
    const d = await res.json();
    S.effects = d.effects || [];
    this.toast("Reloaded effects from disk");
    this.render();
  },

  // ------------------------------------------------------------------ outputs
  outputResult(id, text, cls) {
    const el = document.getElementById(`out-res-${id}`);
    if (!el) return;
    el.className = "notice " + (cls || "");
    el.textContent = text;
    el.hidden = false;
  },

  async testMqtt() {
    this.outputResult("hue", "Connecting to " + (S.cfg.mqtt.host || "?") + "…");
    if (!(await this.saveNow())) return this.outputResult("hue", "Fix the configuration problems above first.", "bad");
    const res = await fetch("/api/mqtt/test", { method: "POST" });
    const d = await res.json();
    this.outputResult("hue", d.message, d.ok ? "ok" : "bad");
  },

  async scanZ2M() {
    this.outputResult("hue", "Scanning Zigbee2MQTT devices…");
    if (!(await this.saveNow())) return this.outputResult("hue", "Fix the configuration problems above first.", "bad");
    const res = await fetch("/api/z2m/scan", { method: "POST" });
    const d = await res.json();
    S.z2m = d.z2m_devices || [];
    S.scanMsg = d.error ? `Scan failed: ${d.error}` : `Found ${S.z2m.length} devices.`;
    this.toast(S.scanMsg, !!d.error);
    this.outputResult("hue", S.scanMsg, d.error ? "bad" : S.z2m.length ? "ok" : "warn");
    if (S.page === "zones") this.render();
  },

  async testOutput(name) {
    if (!(await this.saveNow())) return this.outputResult(name, "Fix the configuration problems above first.", "bad");
    this.outputResult(name, "Sending test frame…");
    const res = await fetch("/api/outputs/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ output: name }),
    });
    const d = await res.json();
    this.outputResult(name, d.message || (d.ok ? "Test frame sent." : "Test failed."), d.ok ? "ok" : "bad");
    this.refreshProtocols();
  },

  renderOutputs() {
    const cl = S.cfg;
    const hue = cl.hue || {};
    const mqtt = cl.mqtt || {};
    const out = cl.outputs || {};
    const ddp = out.ddp || {}; const e131 = out.e131 || {}; const wled = out.wled || {};
    const zones = cl.groups || [];

    const routing = zones
      .map(
        (g, i) => `<tr>
          <td>${esc(g.name)}</td>
          <td><input type="checkbox" ${(g.outputs || {}).hue !== false ? "checked" : ""} onchange="App.edit('groups.${i}.outputs.hue', this.checked)"></td>
          <td><input type="checkbox" ${(g.outputs || {}).ddp ? "checked" : ""} onchange="App.edit('groups.${i}.outputs.ddp', this.checked)"></td>
          <td><input type="checkbox" ${(g.outputs || {}).e131 ? "checked" : ""} onchange="App.edit('groups.${i}.outputs.e131', this.checked)"></td>
          <td><input type="checkbox" ${(g.outputs || {}).wled ? "checked" : ""} onchange="App.edit('groups.${i}.outputs.wled', this.checked)"></td>
        </tr>`
      )
      .join("");

    $("#page").innerHTML = `
      <h1>Outputs</h1>
      <p class="muted small" style="margin-bottom:18px">Pick where the pixels go. Hue and UDP outputs can run at the same time.</p>

      <div class="card">
        <div class="card-head"><div><h2>Per-zone routing</h2><div class="sub">Which transports each zone feeds.</div></div></div>
        <div class="table-wrap"><table class="route-table">
          <thead><tr><th>Zone</th><th>Hue</th><th>DDP</th><th>E1.31</th><th>WLED</th></tr></thead>
          <tbody>${routing || `<tr><td colspan="5" class="muted">No zones yet.</td></tr>`}</tbody>
        </table></div>
      </div>

      <div class="card">
        <div class="card-head">
          <div><h2>Philips Hue</h2><div class="sub">Entertainment frames (ZCL 0xFC01) through Zigbee2MQTT.</div></div>
          <label class="check"><input type="checkbox" ${hue.enabled ? "checked" : ""} onchange="App.edit('hue.enabled', this.checked)"> Enabled</label>
        </div>

        <div class="grid grid-3">
          <label class="field">MQTT broker host
            <input type="text" value="${esc(mqtt.host || "")}" onchange="App.edit('mqtt.host', this.value)">
          </label>
          <label class="field">MQTT port
            <input type="number" value="${esc(mqtt.port ?? 1883)}" onchange="App.int(this,'mqtt.port')">
          </label>
          <label class="field">Base topic
            <input type="text" value="${esc(mqtt.base_topic || "zigbee2mqtt")}" onchange="App.edit('mqtt.base_topic', this.value)">
          </label>
          <label class="field">Username (optional)
            <input type="text" value="${esc(mqtt.username || "")}" onchange="App.edit('mqtt.username', this.value)">
          </label>
          <label class="field">Password (optional)
            <input type="password" value="${esc(mqtt.password || "")}" onchange="App.edit('mqtt.password', this.value)">
          </label>
          <label class="field">Max fixtures per frame
            <input type="number" min="1" max="10" value="${esc(hue.max_fixtures_per_frame ?? 10)}" onchange="App.int(this,'hue.max_fixtures_per_frame')">
          </label>
          <label class="field">Brightness ceiling (1–2047)
            <input type="number" min="1" max="2047" value="${esc(hue.max_brightness ?? 2047)}" onchange="App.int(this,'hue.max_brightness')">
          </label>
          <label class="field">Brightness calculation
            <select onchange="App.edit('hue.brightness_mode', this.value)">
              ${this.opt("max", "Maximum channel (vivid)", hue.brightness_mode)}
              ${this.opt("luminance", "Luminance (CIE Y, more even)", hue.brightness_mode)}
            </select>
          </label>
          <label class="field">Transition smoothing
            <input type="text" placeholder="auto" value="${esc(hue.smoothing ?? "auto")}" onchange="App.edit('hue.smoothing', this.value)">
          </label>
        </div>

        <div class="row" style="margin-top:14px">
          <label class="check"><input type="checkbox" ${hue.light_balance !== false ? "checked" : ""}
            onchange="App.edit('hue.light_balance', this.checked)"> Send light-balance attribute</label>
          <div class="row-end row">
            <button class="small" onclick="App.testMqtt()">Test connection</button>
            <button class="small" onclick="App.scanZ2M()">Scan devices</button>
            <button class="small primary" onclick="App.testOutput('hue')">Send test frame</button>
          </div>
        </div>

        <div class="hint" style="margin-top:10px">
          Proxies: ${(hue.proxies || []).length ? esc((hue.proxies || []).join(", ")) : "none selected — choose one fixture per zone in Zones"}.
          Max 10 fixtures per frame; above that, chunks are spread over the listed proxies or interleaved.
        </div>
        ${S.z2m.length ? `<div class="hint">${S.z2m.length} devices discovered. Add them to a zone on the Zones page.</div>` : ""}
        <div class="notice" id="out-res-hue" hidden></div>
      </div>

      <div class="card">
        <div class="card-head">
          <div><h2>DDP</h2><div class="sub">UDP port 4048, per-pixel RGB.</div></div>
          <label class="check"><input type="checkbox" ${ddp.enabled ? "checked" : ""} onchange="App.edit('outputs.ddp.enabled', this.checked)"> Enabled</label>
        </div>
        <div class="grid grid-3">
          <label class="field">Target host
            <input type="text" value="${esc(ddp.host || "127.0.0.1")}" onchange="App.edit('outputs.ddp.host', this.value)">
          </label>
          <label class="field">Port
            <input type="number" value="${esc(ddp.port ?? 4048)}" onchange="App.int(this,'outputs.ddp.port')">
          </label>
          <div class="row" style="align-items:flex-end"><button class="small" onclick="App.testOutput('ddp')">Send test frame</button></div>
        </div>
        <div class="notice" id="out-res-ddp" hidden></div>
      </div>

      <div class="card">
        <div class="card-head">
          <div><h2>E1.31 / sACN</h2><div class="sub">UDP port 5568, 170 pixels per universe. Leave unicast empty for multicast.</div></div>
          <label class="check"><input type="checkbox" ${e131.enabled ? "checked" : ""} onchange="App.edit('outputs.e131.enabled', this.checked)"> Enabled</label>
        </div>
        <div class="grid grid-4">
          <label class="field">Unicast host
            <input type="text" placeholder="empty = multicast" value="${esc(e131.unicast || "")}" onchange="App.edit('outputs.e131.unicast', this.value)">
          </label>
          <label class="field">First universe
            <input type="number" min="1" value="${esc(e131.universe_start ?? 1)}" onchange="App.int(this,'outputs.e131.universe_start')">
          </label>
          <label class="field">Priority
            <input type="number" min="0" max="200" value="${esc(e131.priority ?? 100)}" onchange="App.int(this,'outputs.e131.priority')">
          </label>
          <div class="row" style="align-items:flex-end"><button class="small" onclick="App.testOutput('e131')">Send test frame</button></div>
        </div>
        <div class="notice" id="out-res-e131" hidden></div>
      </div>

      <div class="card">
        <div class="card-head">
          <div><h2>WLED</h2><div class="sub">Realtime UDP to WLED controllers.</div></div>
          <label class="check"><input type="checkbox" ${wled.enabled ? "checked" : ""} onchange="App.edit('outputs.wled.enabled', this.checked)"> Enabled</label>
        </div>
        <div class="grid grid-4">
          <label class="field">Target host
            <input type="text" value="${esc(wled.host || "127.0.0.1")}" onchange="App.edit('outputs.wled.host', this.value)">
          </label>
          <label class="field">Port
            <input type="number" value="${esc(wled.port ?? 21324)}" onchange="App.int(this,'outputs.wled.port')">
          </label>
          <label class="field">Protocol
            <select onchange="App.edit('outputs.wled.mode', this.value)">
              ${this.opt("drgb", "DRGB (most common)", wled.mode)}
              ${this.opt("dnrgb", "DNRGB (long strips)", wled.mode)}
              ${this.opt("warls", "WARLS (per-LED)", wled.mode)}
            </select>
          </label>
          <div class="row" style="align-items:flex-end"><button class="small" onclick="App.testOutput('wled')">Send test frame</button></div>
        </div>
        <div class="notice" id="out-res-wled" hidden></div>
      </div>`;
  },

  // ------------------------------------------------------------------ logs
  renderLogs() {
    $("#page").innerHTML = `
      <h1>Diagnostics</h1>
      <p class="muted small" style="margin-bottom:18px">Engine timing, transport counters and the exact bytes that were transmitted.</p>

      <div class="card">
        <div class="grid grid-4">
          <div><div class="hint">Render rate</div><div id="d-fps" style="font-size:20px;font-weight:600">–</div></div>
          <div><div class="hint">Frame time</div><div id="d-us" style="font-size:20px;font-weight:600">–</div></div>
          <div><div class="hint">Latency p50 / p95</div><div id="d-lat" style="font-size:20px;font-weight:600">–</div></div>
          <div><div class="hint">Effect sandbox restarts</div><div id="d-restarts" style="font-size:20px;font-weight:600">–</div></div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div><h2>Transports</h2><div class="sub">Updated once per second.</div></div></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Transport</th><th>State</th><th>Packets</th><th>Bytes</th><th>Errors</th><th>Last frame</th></tr></thead>
          <tbody id="d-drivers"></tbody>
        </table></div>
      </div>

      <div class="card">
        <div class="card-head"><div><h2>Log</h2></div><button class="small" onclick="App.refreshLogs(true)">Refresh</button></div>
        <div class="log" id="d-logs">Loading…</div>
      </div>`;
    this.refreshProtocols();
    this.refreshLogs();
  },

  async refreshProtocols() {
    try {
      const res = await fetch("/api/protocols");
      const d = await res.json();
      S.proto = d;
      const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
      set("d-fps", `${d.engine.fps} fps`);
      set("d-us", `${d.engine.us} µs`);
      set("d-lat", `${d.engine.latency_p50_ms} / ${d.engine.latency_p95_ms} ms`);
      set("d-restarts", String(d.sandbox.restarts));
      const rows = document.getElementById("d-drivers");
      if (!rows) return;
      const rowsHtml = [
        { key: "hue", label: "Hue (ZCL 0xFC01)", st: d.drivers.hue, state: d.drivers.hue.streaming ? "streaming" : d.drivers.hue.connected ? "connected" : "idle" },
        { key: "ddp", label: "DDP", st: d.drivers.ddp, state: d.drivers.ddp.enabled ? "enabled" : "off" },
        { key: "e131", label: "E1.31", st: d.drivers.e131, state: d.drivers.e131.enabled ? "enabled" : "off" },
        { key: "wled", label: "WLED", st: d.drivers.wled, state: d.drivers.wled.enabled ? "enabled" : "off" },
      ]
        .map(
          (r) => `<tr>
            <td><strong>${r.label}</strong><div class="hint">${esc(r.st.last_target || "no target yet")}</div></td>
            <td>${esc(r.state)}</td>
            <td class="mono">${r.st.tx_packets || 0}</td>
            <td class="mono">${r.st.tx_bytes || 0}</td>
            <td class="mono">${r.st.errors || 0}${r.st.last_error ? ` <span class="hint">${esc(r.st.last_error)}</span>` : ""}</td>
            <td><div class="hex">${esc(r.st.last_hex || "nothing sent yet")}</div>${r.st.last_hex ? `<button class="small" onclick="App.copyHex('${r.key}')">Copy</button>` : ""}</td>
          </tr>`
        )
        .join("");
      rows.innerHTML = rowsHtml;
    } catch (e) {}
  },

  copyHex(key) {
    const st = (S.proto && S.proto.drivers && S.proto.drivers[key]) || {};
    if (!st.last_hex) return;
    navigator.clipboard.writeText(st.last_hex).then(() => this.toast("Hex copied"));
  },

  async refreshLogs(manual) {
    try {
      const res = await fetch("/api/logs");
      const d = await res.json();
      const box = document.getElementById("d-logs");
      if (!box) return;
      const text = (d.logs || []).join("\n") || "No log entries yet.";
      if (box.textContent !== text) {
        box.textContent = text;
        box.scrollTop = box.scrollHeight;
      }
      if (manual) this.toast("Log refreshed");
    } catch (e) {}
  },

  // ------------------------------------------------------------------ live updates
  async poll() {
    try {
      const res = await fetch("/api/status");
      if (res.ok) {
        S.status = await res.json();
        this.updateLive();
        const now = Date.now();
        if (S.page === "overview") {
          const l = document.getElementById("setup-list");
          if (l) l.innerHTML = this.setupRows();
        }
        if (S.page === "logs") {
          if (now - S.lastProto > 1000) { S.lastProto = now; this.refreshProtocols(); }
          if (now - S.lastLogs > 2000) { S.lastLogs = now; this.refreshLogs(); }
        }
        if (now - S.lastToken > 2000) {
          S.lastToken = now;
          this.refreshSendspinToken();
        }
        if (S.z2m.length === 0 && now - S.lastZ2mCheck > 10000) {
          S.lastZ2mCheck = now;
          fetch("/api/config")
            .then((r) => r.json())
            .then((d) => {
              const got = d.z2m_devices || [];
              if (got.length && got.length !== S.z2m.length) {
                S.z2m = got;
                if (S.page === "zones" || S.page === "outputs") this.render();
              }
            })
            .catch(() => {});
        }
      }
    } catch (e) {}
  },

  updateLive() {
    const st = S.status;
    if (!st) return;
    const a = st.audio || {};
    const runDot = document.getElementById("run-dot");
    if (runDot) {
      runDot.className = "dot " + (a.alive ? "ok" : "bad");
      document.getElementById("run-text").textContent = a.alive ? "Running" : "Audio offline";
      document.getElementById("stat-audio").textContent = a.alive
        ? `audio ${a.source} · ${a.bpm || "–"} BPM`
        : `audio ${a.source || "–"}${a.error ? ` · ${a.error}` : ""}`;
      document.getElementById("stat-engine").textContent = `${st.fps} fps · ${st.us} µs/frame`;
      document.getElementById("stat-latency").textContent = `latency p50 ${st.latency_p50_ms} ms`;
    }

    ["bass", "snare", "mids", "highs"].forEach((band) => {
      const bar = document.getElementById(`mb-${band}`);
      const val = document.getElementById(`mv-${band}`);
      if (bar) bar.style.width = `${Math.round((a[band] || 0) * 100)}%`;
      if (val) val.textContent = `${Math.round((a[band] || 0) * 100)}%`;
    });

    const kick = document.getElementById("live-kick");
    if (kick) kick.className = "badge" + (a.kick || a.kick_env > 0.5 ? " live" : "");
    const snare = document.getElementById("live-snare");
    if (snare) snare.className = "badge" + (a.snare_hit || a.snare_env > 0.5 ? " live" : "");
    const bpm = document.getElementById("live-bpm");
    if (bpm) {
      const conf = a.conf && a.conf.bpm !== undefined ? Math.round(a.conf.bpm * 100) : null;
      bpm.textContent = `${a.bpm || "–"} BPM${conf !== null ? ` · ${conf}%` : ""}`;
      bpm.title = a.beat ? `beat ${Number(a.beat.phase ?? 0).toFixed(2)} · bar ${Number(a.beat.bar_phase ?? 0).toFixed(2)}` : "";
    }
    const keyEl = document.getElementById("live-key");
    if (keyEl) {
      const names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
      const h = a.harmony || {};
      keyEl.textContent = h.key !== undefined ? `KEY ${names[h.key] || h.key} · ${Math.round((h.key_conf || 0) * 100)}%` : "KEY –";
    }
    const sectionEl = document.getElementById("live-section");
    if (sectionEl) {
      const cue = a.cue || {};
      const sec = cue.available ? (cue.section || "–") : ((a.section || {}).name || "–");
      const progress = cue.available ? ` ${Math.round(Number(cue.section_progress || 0) * 100)}%` : "";
      sectionEl.textContent = String(sec).toUpperCase() + progress;
      sectionEl.title = cue.available
        ? `cue: ${cue.section} · ${Number(cue.beats_to_drop || 0).toFixed(1)} beats to drop · harmonic ${Number(cue.harmonic_swell || 0).toFixed(2)} · percussive ${Number(cue.percussive_punch || 0).toFixed(2)}`
        : "live cue fallback";
      sectionEl.className = "badge" + (sec === "drop" || sec === "build" || sec === "pre_drop" ? " live" : "");
    }
    const agc = document.getElementById("live-agc");
    if (agc) agc.textContent = `AGC ${Number(a.agc_gain ?? 1).toFixed(1)}×`;

    const srcEl = document.getElementById("audio-source");
    if (srcEl) {
      const configured = (S.cfg.audio || {}).source || "–";
      srcEl.textContent = a.source && a.source !== configured ? `${a.source} (configured: ${configured})` : a.source || configured;
    }
    const stateEl = document.getElementById("audio-state");
    if (stateEl) {
      stateEl.textContent = a.alive ? "Receiving" : a.connected ? "Connected — waiting for audio" : "No frames";
    }
    const latEl = document.getElementById("audio-latency");
    if (latEl) {
      const lead = a.stream_latency_s;
      latEl.textContent = a.alive && lead !== undefined
        ? `server stream lead ${Math.round(lead * 1000)} ms · analysis lookahead ${a.lookahead_ms ?? 60} ms`
        : "";
    }
    const trimEl = document.getElementById("sync-trim-val");
    if (trimEl) trimEl.textContent = `${Number((S.cfg.audio || {}).latency_ms ?? 0)} ms`;
    S.syncTest = !!st.sync_test;
    const syncBtn = document.getElementById("sync-test-btn");
    if (syncBtn) syncBtn.textContent = S.syncTest ? "■ Stop sync test" : "▶ Sync test";
    const syncHint = document.getElementById("sync-test-hint");
    if (syncHint) {
      syncHint.textContent = S.syncTest
        ? a.alive
          ? "sync test active — nudge until the white flash lands on the audible kick"
          : "sync test active — waiting for audio frames (slow heartbeat)"
        : "negative = lights earlier · positive = later · sync test flashes white on each kick (beat fallback)";
    }

    (st.groups || []).forEach((g) => {
      const badge = document.querySelector(`[data-zbadge="${CSS.escape(g.id)}"]`);
      if (badge) {
        if (g.udp_fresh) { badge.className = "badge warn"; badge.textContent = "UDP override"; }
        else if (g.active) { badge.className = "badge live"; badge.textContent = `Live · ${g.fps} fps`; }
        else { badge.className = "badge"; badge.textContent = "Standby"; }
      }
      const err = document.querySelector(`[data-zerr="${CSS.escape(g.id)}"]`);
      if (err) {
        err.style.display = g.error ? "inline-flex" : "none";
        if (g.error) err.textContent = g.error;
      }
      const sw = document.querySelector(`[data-sw="${CSS.escape(g.id)}"]`);
      if (sw) {
        const px = g.pixels || [];
        if (sw.childElementCount !== px.length) sw.innerHTML = px.map(() => "<i></i>").join("");
        const items = sw.children;
        for (let i = 0; i < px.length; i++) {
          items[i].style.background = g.active ? `rgb(${px[i][0]},${px[i][1]},${px[i][2]})` : "#1a2431";
        }
      }
    });

    if ((S.page === "zones" || S.page === "effects") && !this.sceneDrag) this.drawScene();
  },

  toast(msg, bad) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.className = "toast show" + (bad ? " bad" : "");
    clearTimeout(el._t);
    el._t = setTimeout(() => (el.className = "toast"), 3200);
  },
};

document.addEventListener("DOMContentLoaded", () => {
  const hash = location.hash.slice(1);
  S.page = ["overview", "zones", "effects", "outputs", "logs"].includes(hash) ? hash : "overview";
  App.go(S.page);
  App.boot();
});
