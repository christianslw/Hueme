import {
  StudioClient,
  clamp,
  deepClone,
  el,
  formatClock,
  formatTime,
  toast,
} from "./client.js";

const client = new StudioClient();
const $ = (selector) => document.querySelector(selector);

const dom = {
  showSelect: $("#show-select"),
  timecode: $("#timecode"),
  play: $("#btn-play"),
  toStart: $("#btn-to-start"),
  toEnd: $("#btn-to-end"),
  loop: $("#btn-loop"),
  sync: $("#sync-status"),
  link: $("#link-status"),
  mcp: $("#btn-mcp"),
  mcpDrawer: $("#mcp-drawer"),
  mcpClose: $("#mcp-close"),
  mcpState: $("#mcp-state"),
  mcpTelemetry: $("#mcp-telemetry"),
  mcpLog: $("#mcp-log"),
  mcpFilter: $("#mcp-filter"),
  blueprintLoad: $("#blueprint-load"),
  blueprintFile: $("#blueprint-file"),
  blueprintStatus: $("#blueprint-status"),
  audio: $("#preview-audio"),
  audioVolume: $("#audio-volume"),
  presetList: $("#preset-list"),
  filterList: $("#filter-list"),
  rigSelect: $("#rig-select"),
  rigDirty: $("#rig-dirty"),
  fixtureList: $("#fixture-list"),
  fixtureCount: $("#fixture-count"),
  groupList: $("#group-list"),
  groupEditor: $("#group-editor"),
  viewport: $("#viewport"),
  viewportStatus: $("#viewport-status"),
  inspectorBody: $("#inspector-body"),
  timeline: $("#timeline"),
  history: $("#history-label"),
  grid: $("#grid-select"),
};

const TRACK_COLORS = ["#d06b4a", "#5e9aa8", "#9b7fb5", "#8fa65a", "#b3925a"];
const PROTOCOL_CLASS = {
  hue_z2m: "hue",
  wled_ddp: "ddp",
  e131: "e131",
  ha_light: "ha",
  none: "",
};
const PROTOCOL_LABEL = {
  hue_z2m: "HUE",
  wled_ddp: "DDP",
  e131: "E131",
  ha_light: "HA",
  none: "OFF",
};

let state = null;
let selectedFixtures = new Set();
let selectedClipId = null;
let selectedTrackId = null;
let selectedGroupId = null;
let inspectorTab = "clip";
let overrides = new Map();
const camera = {
  yaw: -0.78,
  pitch: 0.52,
  distance: 16,
  target: [3.25, 4.0, 2.55],
  fov: (50 * Math.PI) / 180,
  fitted: false,
};
const view = { start: 0, end: 0, labelW: 120, rulerH: 24, trackH: 28 };
let viewportSize = { w: 1, h: 1 };
let timelineSize = { w: 1, h: 1 };
let viewportDrag = null;
let timelineDrag = null;
let drawQueued = false;
let fieldSeq = 0;
let blueprint = null;
let blueprintKey = null;
let audioSourceUrl = null;
let audioFailedUrl = null;
let audioPendingSeek = null;
let audioWarned = false;
let audioNoticeShown = false;
let mcpLog = [];
let mcpTimer = 0;

function roomDoc() {
  return state?.rig?.document?.room || { min: [0, 0, 0], max: [6.5, 8, 5.1] };
}

function showDoc() {
  return state?.show?.document || null;
}

function activeShowId() {
  return state?.show?.id || null;
}

function duration() {
  return Number(showDoc()?.duration_s || 0);
}

function beatSeconds() {
  const timing = showDoc()?.timing;
  if (!timing) return 0.5;
  return Number(timing.beat_grid_s || 60 / Number(timing.bpm || 120));
}

function gridSeconds() {
  const beats = { "1/1": 4, "1/2": 2, "1/4": 1, "1/8": 0.5, "1/16": 0.25 };
  return beatSeconds() * (beats[dom.grid.value] || 1);
}

function snapTime(value) {
  const offset = Number(showDoc()?.timing?.beat_offset_s || 0);
  const step = gridSeconds();
  const snapped = offset + Math.round((value - offset) / step) * step;
  return clamp(snapped, 0, duration());
}

function uniqueId(prefix, existing) {
  let index = 1;
  let id = `${prefix}_${index}`;
  while (existing.has(id)) {
    index += 1;
    id = `${prefix}_${index}`;
  }
  return id;
}

function findFixture(id) {
  return state?.rig?.document?.fixtures?.find((fixture) => fixture.id === id);
}

function findClip(id = selectedClipId) {
  const doc = showDoc();
  if (!doc || !id) return null;
  for (const track of doc.tracks) {
    const clip = track.clips.find((item) => item.id === id);
    if (clip) return { track, clip };
  }
  return null;
}

function findTrack(id = selectedTrackId) {
  return showDoc()?.tracks?.find((track) => track.id === id) || null;
}

function presetById(id) {
  return state?.presets?.find((preset) => preset.id === id) || null;
}

function defaultParams(presetId) {
  const preset = presetById(presetId);
  const params = {};
  for (const spec of preset?.params || []) params[spec.key] = deepClone(spec.default);
  return params;
}

function trackColor(trackId) {
  const tracks = showDoc()?.tracks || [];
  const index = Math.max(0, tracks.findIndex((track) => track.id === trackId));
  return TRACK_COLORS[index % TRACK_COLORS.length];
}

function rgbAt(voxel) {
  const rgb = client.frame?.rgb;
  if (!rgb || voxel * 3 + 2 >= rgb.length) return [110, 112, 118];
  return [rgb[voxel * 3], rgb[voxel * 3 + 1], rgb[voxel * 3 + 2]];
}

function rgba(color, alpha = 1) {
  return `rgba(${color[0]},${color[1]},${color[2]},${alpha})`;
}

function voxelSlice(fixtureId) {
  return state?.rig?.compiled?.fixture_slices?.[fixtureId] || [0, 0];
}

function fixturePositions(fixtureId) {
  const slice = voxelSlice(fixtureId);
  const flat = state?.rig?.compiled?.positions || [];
  const out = [];
  for (let index = slice[0]; index < slice[1]; index += 1) {
    out.push([flat[index * 3], flat[index * 3 + 1], flat[index * 3 + 2]]);
  }
  return out;
}

function fixtureCenter(fixtureId) {
  const points = fixturePositions(fixtureId);
  if (!points.length) return [0, 0, 0];
  const sum = points.reduce((acc, point) => [acc[0] + point[0], acc[1] + point[1], acc[2] + point[2]], [0, 0, 0]);
  return sum.map((value) => value / points.length);
}

function overrideOffset(fixtureId) {
  return overrides.get(fixtureId) || [0, 0, 0];
}

function requestDraw() {
  if (drawQueued) return;
  drawQueued = true;
  window.requestAnimationFrame(() => {
    drawQueued = false;
    drawViewport();
    drawTimeline();
  });
}

function sizeCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.round(rect.width * dpr));
  const height = Math.max(1, Math.round(rect.height * dpr));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const context = canvas.getContext("2d");
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { w: rect.width, h: rect.height, context };
}

function resizeAll() {
  const viewport = sizeCanvas(dom.viewport);
  viewportSize = { w: viewport.w, h: viewport.h };
  const timeline = sizeCanvas(dom.timeline);
  timelineSize = { w: timeline.w, h: timeline.h };
  requestDraw();
}

/* ------------------------------------------------------------------ state */
function onState() {
  state = client.state;
  if (!state) return;
  sanitizeSelection();
  renderShowSelect();
  renderRigSelect();
  renderPresets();
  renderFilters();
  renderFixtures();
  renderGroups();
  renderInspector();
  updateTransport();
  updateDirty();
  const total = duration();
  if (!camera.fitted && state.rig.compiled.voxel_count >= 0) {
    fitCamera(false);
    camera.fitted = true;
  }
  if (view.end <= 0 || view.end > total + 0.001) {
    view.start = 0;
    view.end = Math.max(1, Math.min(total || 1, 60));
  }
  resizeAll();
  syncBlueprint();
  syncAudioSource();
  syncAudioPlayback();
}

function updateBlueprintStatus() {
  const meta = state?.show?.blueprint;
  const audio = Boolean(meta?.audio_available);
  dom.blueprintStatus.textContent = meta
    ? `${meta.bpm} BPM / ${meta.section_count} sections / ${audio ? "AUDIO" : "NO WAV"}`
    : "NO AUDIO";
  dom.blueprintStatus.title = audio
    ? "Attached WAV plays with the timeline"
    : "Upload a 16-bit WAV to hear the timeline";
  dom.blueprintStatus.style.color = meta ? "var(--text-1)" : "var(--text-2)";
}

function audioUrl() {
  const showId = activeShowId();
  const meta = state?.show?.blueprint;
  if (!showId || !meta?.audio_available) return null;
  return `/api/shows/${encodeURIComponent(showId)}/audio?v=${encodeURIComponent(meta.key || "0")}`;
}

function previewTime() {
  return Number(client.frame?.time ?? state?.playback?.time_s ?? 0);
}

function seekPreviewAudio(time) {
  if (!dom.audio || !audioSourceUrl) return;
  const mediaDuration = Number(dom.audio.duration);
  const target =
    Number.isFinite(mediaDuration) && mediaDuration > 0
      ? clamp(time, 0, mediaDuration)
      : Math.max(0, Number(time) || 0);
  if (!dom.audio.readyState) {
    audioPendingSeek = target;
    return;
  }
  try {
    dom.audio.currentTime = target;
    audioPendingSeek = null;
  } catch {
    audioPendingSeek = target;
  }
}

function syncAudioSource() {
  if (!dom.audio) return;
  const url = audioUrl();
  if (!url) {
    if (audioSourceUrl) {
      dom.audio.pause();
      dom.audio.removeAttribute("src");
      dom.audio.load();
      audioSourceUrl = null;
      audioPendingSeek = null;
    }
    return;
  }
  if (url === audioSourceUrl || url === audioFailedUrl) return;
  audioSourceUrl = url;
  audioPendingSeek = previewTime();
  dom.audio.src = url;
  dom.audio.load();
}

function playPreviewAudio() {
  if (!dom.audio || !audioSourceUrl || dom.audio.ended) return;
  const playing = dom.audio.play();
  if (playing && typeof playing.catch === "function") {
    playing.catch(() => {
      if (audioWarned) return;
      audioWarned = true;
      toast("Click the page once to enable audio preview", "info");
    });
  }
}

function syncAudioPlayback() {
  if (!dom.audio || !audioSourceUrl) return;
  dom.audio.volume = clamp(Number(dom.audioVolume?.value ?? 0.8), 0, 1);
  const time = previewTime();
  if (dom.audio.readyState && Math.abs(dom.audio.currentTime - time) > 0.12) {
    seekPreviewAudio(time);
  }
  const playing = Boolean(state?.playback?.playing);
  if (playing && !dom.audio.ended) {
    if (dom.audio.paused) playPreviewAudio();
  } else if (!dom.audio.paused) {
    dom.audio.pause();
  }
}

async function syncBlueprint() {
  const meta = state?.show?.blueprint;
  updateBlueprintStatus();
  if (!meta) {
    blueprint = null;
    blueprintKey = null;
    requestDraw();
    return;
  }
  if (meta.key === blueprintKey) return;
  blueprintKey = meta.key;
  blueprint = null;
  try {
    blueprint = await client.get(`/api/shows/${encodeURIComponent(activeShowId())}/blueprint`);
  } catch {
    blueprint = null;
  }
  requestDraw();
}

async function uploadBlueprint(file) {
  const showId = activeShowId();
  if (!file || !showId) return;
  try {
    const response = await fetch(
      `/api/shows/${encodeURIComponent(showId)}/blueprint?source=${encodeURIComponent(file.name)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: file,
      }
    );
    const text = await response.text();
    const data = text ? JSON.parse(text) : null;
    if (!response.ok) throw new Error(data?.detail || `${response.status} ${response.statusText}`);
    toast(`AudioBlueprint: ${data.bpm} BPM, ${data.beat_count} beats`, "ok");
    blueprintKey = null;
    audioFailedUrl = null;
    audioNoticeShown = false;
    await client.refresh();
  } catch (error) {
    toast(error.message, "danger");
  }
}

function sanitizeSelection() {
  const fixtureIds = new Set(state.rig.document.fixtures.map((fixture) => fixture.id));
  for (const id of [...selectedFixtures]) {
    if (!fixtureIds.has(id)) selectedFixtures.delete(id);
  }
  if (selectedClipId && !findClip(selectedClipId)) selectedClipId = null;
  if (selectedTrackId && !findTrack(selectedTrackId)) selectedTrackId = null;
  if (selectedGroupId && !state.rig.document.groups.some((group) => group.id === selectedGroupId)) {
    selectedGroupId = null;
  }
}

function updateDirty() {
  dom.rigDirty.classList.toggle("hidden", !state.rig.dirty);
  dom.rigSelect.value = state.rig.active || "";
  const active = state.rig.presets.find((preset) => preset.id === state.rig.active);
  dom.rigSelect.title = active ? `${active.fixture_count} fixtures / ${active.voxel_count} voxels` : "";
}

function updateTransport() {
  const playback = state?.playback || {};
  dom.timecode.textContent = formatTime(playback.time_s || 0);
  dom.play.textContent = playback.playing ? "PAUSE" : "PLAY";
  dom.play.classList.toggle("active", Boolean(playback.playing));
  dom.loop.classList.toggle("active", Boolean(playback.loop));
  dom.link.textContent = client.connected ? "PREVIEW LIVE" : "OFFLINE";
  dom.link.style.color = client.connected ? "var(--ok)" : "var(--text-2)";
  updateSync();
}

const SYNC_LABELS = {
  off: "MA OFF",
  waiting: "MA WAIT",
  unconfigured: "MA --",
  disconnected: "MA OFFLINE",
  no_track: "MA IDLE",
  no_match: "MA NO MATCH",
  acquiring: "MA ACQ",
  locked: "MA LOCK",
  paused: "MA PAUSED",
  ended: "MA END",
};

function updateSync() {
  if (!dom.sync) return;
  const sync = state?.sync || {};
  dom.sync.textContent = SYNC_LABELS[sync.state] || "MA --";
  dom.sync.disabled = !sync.configured;
  dom.sync.classList.toggle("active", sync.state === "locked");
  dom.sync.style.color =
    sync.state === "locked"
      ? "var(--ok)"
      : sync.state === "acquiring"
        ? "var(--warn)"
        : "";
  const track = sync.track;
  dom.sync.title = sync.configured
    ? [
        sync.enabled ? "MA sync enabled" : "MA sync disabled",
        track ? `${track.artist || "?"} - ${track.title || "?"}` : "no track",
        sync.matched_show_id ? `show ${sync.matched_show_id}` : "no matching show",
        `${Math.round((sync.error_s || 0) * 1000)} ms / ${(sync.rate || 1).toFixed(3)}x`,
      ].join(" | ")
    : "Sendspin/MA not configured";
}

function renderShowSelect() {
  const shows = state.show.shows || [];
  dom.showSelect.replaceChildren(
    ...shows.map((show) =>
      el("option", { value: show.id, text: show.name, selected: show.id === state.show.id })
    )
  );
  if (!shows.length) {
    dom.showSelect.append(el("option", { value: "", text: "No shows", selected: true }));
  }
}

function renderRigSelect() {
  dom.rigSelect.replaceChildren(
    ...(state.rig.presets || []).map((preset) =>
      el("option", { value: preset.id, text: preset.name, selected: preset.id === state.rig.active })
    )
  );
  if (!state.rig.presets?.length) {
    dom.rigSelect.append(el("option", { value: "", text: "No rig presets", selected: true }));
  }
}

function renderPresets() {
  dom.presetList.replaceChildren(
    ...(state.presets || []).map((preset) => {
      const selected = findClip()?.clip.preset === preset.id;
      const row = el(
        "button",
        {
          class: `list-row${selected ? " selected" : ""}`,
          type: "button",
          title: preset.description,
          onclick: () => applyPreset(preset.id),
        },
        el("span", { class: "row-name", text: preset.name }),
        el("span", { class: "row-meta", text: String(preset.params.length) })
      );
      return row;
    })
  );
}

function renderFilters() {
  dom.filterList.replaceChildren(
    ...(state.filters || []).map((filter) =>
      el(
        "button",
        {
          class: "list-row",
          type: "button",
          title: filter.description,
          onclick: () => addFilter(filter.id),
        },
        el("span", { class: "row-name", text: filter.name }),
        el("span", { class: "row-meta", text: filter.params.length ? `${filter.params.length}p` : "" })
      )
    )
  );
}

function renderFixtures() {
  const doc = state.rig.document;
  dom.fixtureCount.textContent = `${doc.fixtures.length} fx / ${state.rig.compiled.voxel_count} vox`;
  const rows = doc.fixtures.map((fixture) => {
    const selected = selectedFixtures.has(fixture.id);
    const protocol = fixture.output?.protocol || "none";
    const enabled = fixture.output?.enabled !== false;
    const visibility = el("button", {
      class: "button small icon-button",
      type: "button",
      title: enabled ? "Disable output" : "Enable output",
      text: enabled ? "ON" : "OFF",
      style: `width:30px;color:${enabled ? "var(--text-1)" : "var(--text-2)"}`,
      onclick: (event) => {
        event.stopPropagation();
        patchFixture(fixture.id, { output: { enabled: !enabled } });
      },
    });
    const row = el(
      "div",
      {
        class: `list-row${selected ? " selected" : ""}`,
        role: "button",
        tabindex: "0",
        draggable: "true",
        onclick: (event) => selectFixture(fixture.id, event.shiftKey),
        ondragstart: (event) => {
          const ids = selectedFixtures.has(fixture.id) ? [...selectedFixtures] : [fixture.id];
          event.dataTransfer.setData("application/x-hueme-fixtures", JSON.stringify(ids));
          event.dataTransfer.effectAllowed = "copy";
        },
      },
      visibility,
      el("span", {
        class: `badge ${PROTOCOL_CLASS[protocol] || ""}`,
        text: PROTOCOL_LABEL[protocol] || protocol.toUpperCase(),
      }),
      el("span", { class: "row-name", text: fixture.name }),
      el("span", {
        class: "row-meta",
        text: String(fixture.geometry?.type === "line" ? fixture.geometry.pixels : 1),
      })
    );
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.code === "Space") {
        event.preventDefault();
        selectFixture(fixture.id, event.shiftKey);
      }
    });
    return row;
  });
  dom.fixtureList.replaceChildren(...rows);
}

function renderGroups() {
  const doc = state.rig.document;
  const compiled = state.rig.compiled.groups || {};
  dom.groupList.replaceChildren(
    ...doc.groups.map((group) => {
      const count = compiled[group.id]?.length ?? 0;
      const row = el(
        "div",
        {
          class: `list-row${selectedGroupId === group.id ? " selected" : ""}`,
          role: "button",
          tabindex: "0",
          ondragover: (event) => {
            if (group.type === "list") {
              event.preventDefault();
              event.dataTransfer.dropEffect = "copy";
            }
          },
          ondrop: (event) => {
            if (group.type !== "list") return;
            event.preventDefault();
            try {
              const ids = JSON.parse(event.dataTransfer.getData("application/x-hueme-fixtures") || "[]");
              if (ids.length) assignGroup(group.id, ids);
            } catch {
              /* ignore malformed drag data */
            }
          },
          onclick: () => {
            selectedGroupId = group.id;
            renderGroups();
          },
        },
        el("span", { class: "row-name", text: group.name }),
        el("span", { class: "row-meta", text: `${group.type === "all" ? "ALL" : group.type.replace("spatial_", "")} ${count}` })
      );
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.code === "Space") {
          event.preventDefault();
          selectedGroupId = group.id;
          renderGroups();
        }
      });
      return row;
    })
  );
  renderGroupEditor();
}

/* --------------------------------------------------------------- viewport */
function normalize(vector) {
  const length = Math.hypot(...vector) || 1;
  return vector.map((value) => value / length);
}

function add(a, b) {
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}

function sub(a, b) {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function scale(vector, factor) {
  return vector.map((value) => value * factor);
}

function dot(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function cross(a, b) {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

function cameraBasis() {
  const target = camera.target;
  const cosPitch = Math.cos(camera.pitch);
  const sinPitch = Math.sin(camera.pitch);
  const eye = [
    target[0] + camera.distance * cosPitch * Math.sin(camera.yaw),
    target[1] - camera.distance * cosPitch * Math.cos(camera.yaw),
    target[2] + camera.distance * sinPitch,
  ];
  const forward = normalize(sub(target, eye));
  let right = normalize(cross(forward, [0, 0, 1]));
  if (!Number.isFinite(right[0]) || Math.hypot(...right) < 0.001) right = [1, 0, 0];
  const up = cross(right, forward);
  return { eye, forward, right, up };
}

function project(point) {
  const basis = cameraBasis();
  const relative = sub(point, basis.eye);
  const depth = dot(relative, basis.forward);
  if (depth <= 0.05) return null;
  const focal = (viewportSize.h * 0.5) / Math.tan(camera.fov * 0.5);
  return {
    x: viewportSize.w * 0.5 + (dot(relative, basis.right) * focal) / depth,
    y: viewportSize.h * 0.5 - (dot(relative, basis.up) * focal) / depth,
    depth,
    focal,
  };
}

function fitCamera(redraw = true) {
  const room = roomDoc();
  const min = room.min;
  const max = room.max;
  const center = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
  const radius = Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]) / 2;
  camera.target = center;
  camera.distance = Math.max(2, (radius / Math.tan(camera.fov * 0.5)) * 1.25);
  camera.yaw = -0.78;
  camera.pitch = 0.52;
  if (redraw) requestDraw();
}

function drawViewport() {
  const context = dom.viewport.getContext("2d");
  const { w, h } = viewportSize;
  context.clearRect(0, 0, w, h);
  context.fillStyle = "#0c0d0f";
  context.fillRect(0, 0, w, h);
  const room = roomDoc();
  const min = room.min;
  const max = room.max;
  const floor = min[2];
  const basis = cameraBasis();

  // Floor grid: 0.5 m minor, 2 m major.
  const step = 0.5;
  for (let x = min[0]; x <= max[0] + 1e-6; x += step) {
    const major = Math.abs(x / 2 - Math.round(x / 2)) < 1e-6;
    drawWorldLine(context, [x, min[1], floor], [x, max[1], floor], major ? "#24272c" : "#191b1f");
  }
  for (let y = min[1]; y <= max[1] + 1e-6; y += step) {
    const major = Math.abs(y / 2 - Math.round(y / 2)) < 1e-6;
    drawWorldLine(context, [min[0], y, floor], [max[0], y, floor], major ? "#24272c" : "#191b1f");
  }

  // Room box edges.
  const corners = [
    [min[0], min[1], min[2]],
    [max[0], min[1], min[2]],
    [max[0], max[1], min[2]],
    [min[0], max[1], min[2]],
    [min[0], min[1], max[2]],
    [max[0], min[1], max[2]],
    [max[0], max[1], max[2]],
    [min[0], max[1], max[2]],
  ];
  const edges = [
    [0, 1], [1, 2], [2, 3], [3, 0],
    [4, 5], [5, 6], [6, 7], [7, 4],
    [0, 4], [1, 5], [2, 6], [3, 7],
  ];
  for (const [a, b] of edges) {
    drawWorldLine(context, corners[a], corners[b], "#202329");
  }

  // Light cones and floor pools first so fixtures stay legible.
  for (const fixture of state.rig.document.fixtures) {
    if (fixture.geometry.type !== "point") continue;
    drawFloorPool(context, fixture);
    drawCone(context, fixture);
  }

  const fixtures = state.rig.document.fixtures;
  for (const fixture of fixtures) {
    const slice = voxelSlice(fixture.id);
    const offset = overrideOffset(fixture.id);
    const points = fixturePositions(fixture.id).map((point) => add(point, offset));
    if (!points.length) continue;
    const selected = selectedFixtures.has(fixture.id);
    if (fixture.geometry.type === "point") {
      const projected = project(points[0]);
      if (!projected) continue;
      const [r, g, b] = rgbAt(slice[0]);
      const radius = clamp(220 / projected.depth, 2.2, 7);
      const glow = context.createRadialGradient(projected.x, projected.y, 0, projected.x, projected.y, radius * 4);
      glow.addColorStop(0, `rgba(${r},${g},${b},0.5)`);
      glow.addColorStop(1, `rgba(${r},${g},${b},0)`);
      context.fillStyle = glow;
      context.beginPath();
      context.arc(projected.x, projected.y, radius * 4, 0, Math.PI * 2);
      context.fill();
      context.strokeStyle = "#4a4e56";
      context.lineWidth = 1;
      context.beginPath();
      context.arc(projected.x, projected.y, radius + 1.5, 0, Math.PI * 2);
      context.stroke();
      context.fillStyle = `rgb(${r},${g},${b})`;
      context.beginPath();
      context.arc(projected.x, projected.y, radius, 0, Math.PI * 2);
      context.fill();
      if (selected) drawFixtureBox(context, points, 0.12);
    } else {
      const ends = [project(points[0]), project(points[points.length - 1])];
      if (ends[0] && ends[1]) {
        context.strokeStyle = "#2b2e34";
        context.lineWidth = 4;
        context.beginPath();
        context.moveTo(ends[0].x, ends[0].y);
        context.lineTo(ends[1].x, ends[1].y);
        context.stroke();
      }
      let last = null;
      for (let index = 0; index < points.length; index += 1) {
        const projected = project(points[index]);
        if (!projected) continue;
        const [r, g, b] = rgbAt(slice[0] + index);
        if (last) {
          context.strokeStyle = `rgb(${r},${g},${b})`;
          context.lineWidth = clamp(160 / projected.depth, 1, 4);
          context.beginPath();
          context.moveTo(last.x, last.y);
          context.lineTo(projected.x, projected.y);
          context.stroke();
        }
        last = projected;
        if (index % Math.max(1, Math.floor(points.length / 24)) === 0) {
          context.fillStyle = `rgb(${r},${g},${b})`;
          context.fillRect(projected.x - 1, projected.y - 1, 2, 2);
        }
      }
      if (selected) drawFixtureBox(context, points, 0.1);
    }
    if (selected) {
      const center = points.reduce((acc, point) => add(acc, point), [0, 0, 0]).map((v) => v / points.length);
      drawAxisHandles(context, center);
    }
  }

  if (viewportDrag?.mode === "marquee") {
    const { x0, y0, x1, y1 } = viewportDrag;
    context.strokeStyle = "#e8862e";
    context.lineWidth = 1;
    context.setLineDash([4, 4]);
    context.strokeRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
    context.setLineDash([]);
  }

  drawAxisTriad(context, basis);
  dom.viewportStatus.textContent = `${camera.pitch > 1.3 ? "TOP" : camera.pitch < -1.3 ? "BOTTOM" : "PERSP"}  |  ${state.rig.compiled.voxel_count} VOX  |  ${selectedFixtures.size} SELECTED`;
}

function drawWorldLine(context, start, end, color) {
  const a = project(start);
  const b = project(end);
  if (!a || !b) return;
  context.strokeStyle = color;
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(a.x, a.y);
  context.lineTo(b.x, b.y);
  context.stroke();
}

function fixtureAim(fixture) {
  const compiledAim = state.rig.compiled.fixture_aims?.[fixture.id];
  return normalize(
    compiledAim || [
      fixture.geometry.aim?.[0] ?? 0,
      fixture.geometry.aim?.[1] ?? 0,
      fixture.geometry.aim?.[2] ?? -1,
    ]
  );
}

function drawFloorPool(context, fixture) {
  const offset = overrideOffset(fixture.id);
  const center = add(fixturePositions(fixture.id)[0] || [0, 0, 0], offset);
  const aim = fixtureAim(fixture);
  const floor = roomDoc().min[2];
  if (aim[2] > -0.05) return;
  const distance = (floor - center[2]) / aim[2];
  if (distance <= 0) return;
  const hit = add(center, scale(aim, distance));
  const room = roomDoc();
  if (
    hit[0] < room.min[0] || hit[0] > room.max[0] ||
    hit[1] < room.min[1] || hit[1] > room.max[1]
  ) {
    return;
  }
  const projected = project(hit);
  if (!projected) return;
  const radius = clamp(projected.focal / Math.max(1, projected.depth) * 0.55, 8, 180);
  const slice = voxelSlice(fixture.id);
  const [r, g, b] = rgbAt(slice[0]);
  context.save();
  context.translate(projected.x, projected.y);
  context.scale(1, 0.36);
  const gradient = context.createRadialGradient(0, 0, 0, 0, 0, radius);
  gradient.addColorStop(0, `rgba(${r},${g},${b},0.13)`);
  gradient.addColorStop(1, `rgba(${r},${g},${b},0)`);
  context.fillStyle = gradient;
  context.beginPath();
  context.arc(0, 0, radius, 0, Math.PI * 2);
  context.fill();
  context.restore();
}

function drawCone(context, fixture) {
  const offset = overrideOffset(fixture.id);
  const center = add(fixturePositions(fixture.id)[0] || [0, 0, 0], offset);
  const aim = fixtureAim(fixture);
  const beam = ((fixture.geometry.beam_deg ?? 45) * Math.PI) / 360;
  const length = 2.4;
  const radius = Math.tan(beam) * length;
  let u = normalize(cross(aim, [0, 0, 1]));
  if (!Number.isFinite(u[0]) || Math.hypot(...u) < 0.001) u = [1, 0, 0];
  const v = cross(aim, u);
  const points = [center];
  for (let index = 0; index < 16; index += 1) {
    const angle = (index / 16) * Math.PI * 2;
    points.push(
      add(
        add(center, scale(aim, length)),
        add(scale(u, Math.cos(angle) * radius), scale(v, Math.sin(angle) * radius))
      )
    );
  }
  const projected = points.map(project);
  if (projected.some((point) => !point)) return;
  const slice = voxelSlice(fixture.id);
  const [r, g, b] = rgbAt(slice[0]);
  context.save();
  context.globalCompositeOperation = "lighter";
  context.fillStyle = `rgba(${r},${g},${b},0.035)`;
  context.beginPath();
  context.moveTo(projected[0].x, projected[0].y);
  for (let index = 1; index < projected.length; index += 1) {
    context.lineTo(projected[index].x, projected[index].y);
  }
  context.closePath();
  context.fill();
  context.restore();
}

function drawFixtureBox(context, points, padding) {
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (const point of points) {
    for (let axis = 0; axis < 3; axis += 1) {
      min[axis] = Math.min(min[axis], point[axis] - padding);
      max[axis] = Math.max(max[axis], point[axis] + padding);
    }
  }
  const corners = [
    [min[0], min[1], min[2]],
    [max[0], min[1], min[2]],
    [max[0], max[1], min[2]],
    [min[0], max[1], min[2]],
    [min[0], min[1], max[2]],
    [max[0], min[1], max[2]],
    [max[0], max[1], max[2]],
    [min[0], max[1], max[2]],
  ];
  const edges = [
    [0, 1], [1, 2], [2, 3], [3, 0],
    [4, 5], [5, 6], [6, 7], [7, 4],
    [0, 4], [1, 5], [2, 6], [3, 7],
  ];
  context.strokeStyle = "#e8862e";
  context.lineWidth = 1;
  for (const [a, b] of edges) {
    const pa = project(corners[a]);
    const pb = project(corners[b]);
    if (!pa || !pb) continue;
    context.beginPath();
    context.moveTo(pa.x, pa.y);
    context.lineTo(pb.x, pb.y);
    context.stroke();
  }
}

function drawAxisHandles(context, center) {
  const axes = [
    { vector: [0.55, 0, 0], label: "X" },
    { vector: [0, 0.55, 0], label: "Y" },
    { vector: [0, 0, 0.55], label: "Z" },
  ];
  context.strokeStyle = "#b3b6bd";
  context.fillStyle = "#b3b6bd";
  context.font = "9px JetBrains Mono, monospace";
  context.lineWidth = 1;
  const origin = project(center);
  if (!origin) return;
  for (const axis of axes) {
    const end = project(add(center, axis.vector));
    if (!end) continue;
    context.beginPath();
    context.moveTo(origin.x, origin.y);
    context.lineTo(end.x, end.y);
    context.stroke();
    context.fillText(axis.label, end.x + 3, end.y + 3);
  }
}

function drawAxisTriad(context, basis) {
  const origin = [viewportSize.w - 42, viewportSize.h - 42];
  const length = 22;
  const axes = [
    { vector: [1, 0, 0], label: "X" },
    { vector: [0, 1, 0], label: "Y" },
    { vector: [0, 0, 1], label: "Z" },
  ];
  context.font = "9px JetBrains Mono, monospace";
  context.lineWidth = 1;
  for (const axis of axes) {
    const dx = dot(axis.vector, basis.right) * length;
    const dy = -dot(axis.vector, basis.up) * length;
    context.strokeStyle = "#7c8089";
    context.fillStyle = "#7c8089";
    context.beginPath();
    context.moveTo(origin[0], origin[1]);
    context.lineTo(origin[0] + dx, origin[1] + dy);
    context.stroke();
    context.fillText(axis.label, origin[0] + dx * 1.18, origin[1] + dy * 1.18);
  }
}

function fixtureAt(x, y) {
  let best = null;
  let bestDistance = 12;
  for (const fixture of state.rig.document.fixtures) {
    const offset = overrideOffset(fixture.id);
    const points = fixturePositions(fixture.id).map((point) => add(point, offset));
    if (!points.length) continue;
    const projected = points.map(project).filter(Boolean);
    for (const point of projected) {
      const distance = Math.hypot(point.x - x, point.y - y);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = fixture.id;
      }
    }
    if (fixture.geometry.type === "line") {
      for (let index = 1; index < projected.length; index += 1) {
        const distance = pointSegmentDistance(x, y, projected[index - 1], projected[index]);
        if (distance < bestDistance) {
          bestDistance = distance;
          best = fixture.id;
        }
      }
    }
  }
  return best;
}

function pointSegmentDistance(x, y, a, b) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq === 0) return Math.hypot(x - a.x, y - a.y);
  const t = clamp(((x - a.x) * dx + (y - a.y) * dy) / lengthSq, 0, 1);
  return Math.hypot(x - (a.x + t * dx), y - (a.y + t * dy));
}

/* --------------------------------------------------------------- timeline */
function timeToX(time) {
  const span = Math.max(0.001, view.end - view.start);
  return view.labelW + ((time - view.start) / span) * (timelineSize.w - view.labelW);
}

function xToTime(x) {
  const span = Math.max(0.001, view.end - view.start);
  return view.start + ((x - view.labelW) / Math.max(1, timelineSize.w - view.labelW)) * span;
}

function trackIndexAt(y) {
  return Math.floor((y - view.rulerH) / view.trackH);
}

function clipRect(trackIndex, clip) {
  const x0 = timeToX(clip.start_s);
  const x1 = timeToX(clip.end_s);
  const y = view.rulerH + trackIndex * view.trackH + 3;
  return { x0, x1, y, h: view.trackH - 6 };
}

function drawTimeline() {
  const context = dom.timeline.getContext("2d");
  const { w, h } = timelineSize;
  const doc = showDoc();
  context.clearRect(0, 0, w, h);
  context.fillStyle = "#17181b";
  context.fillRect(0, 0, w, h);
  if (!doc) {
    context.fillStyle = "#7c8089";
    context.font = "11px Inter, sans-serif";
    context.fillText("No show loaded", 12, 42);
    return;
  }

  const tracks = doc.tracks || [];
  const laneTop = view.rulerH;
  const laneBottom = Math.min(h, laneTop + tracks.length * view.trackH);

  context.fillStyle = "#141518";
  context.fillRect(0, 0, view.labelW, h);
  context.fillStyle = "#1d1f23";
  context.fillRect(view.labelW, 0, w - view.labelW, view.rulerH);

  // Beat grid. Coarsen until ticks are readable.
  let step = gridSeconds();
  while ((step / Math.max(0.001, view.end - view.start)) * (w - view.labelW) < 5) step *= 2;
  const offset = Number(doc.timing?.beat_offset_s || 0);
  for (let time = offset + Math.floor((view.start - offset) / step) * step; time <= view.end; time += step) {
    if (time < 0) continue;
    const x = timeToX(time);
    const bar = Math.abs(time / (beatSeconds() * 4) - Math.round(time / (beatSeconds() * 4))) < 1e-6;
    context.strokeStyle = bar ? "#2b2e34" : "#202329";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(x + 0.5, view.rulerH);
    context.lineTo(x + 0.5, laneBottom);
    context.stroke();
    if (w - view.labelW > 0 && Math.round((time - offset) / step) % 4 === 0) {
      context.fillStyle = "#7c8089";
      context.font = "9px JetBrains Mono, monospace";
      context.fillText(formatClock(time), x + 3, 11);
    }
  }

  for (let index = 0; index < tracks.length; index += 1) {
    const track = tracks[index];
    const y = laneTop + index * view.trackH;
    context.fillStyle = index % 2 === 0 ? "#17181b" : "#191b1e";
    context.fillRect(view.labelW, y, w - view.labelW, view.trackH);
    context.fillStyle = track.muted ? "#4a4e56" : "#b3b6bd";
    context.font = "11px Inter, sans-serif";
    context.fillText(track.name, 8, y + 18);
    context.fillStyle = track.muted ? "#c45a52" : "#4a4e56";
    context.font = "9px JetBrains Mono, monospace";
    context.fillText("M", 92, y + 17);
    if (track.solo) {
      context.fillStyle = "#c9a34a";
      context.fillText("S", 104, y + 17);
    }
    context.strokeStyle = "#2b2e34";
    context.beginPath();
    context.moveTo(0, y + view.trackH + 0.5);
    context.lineTo(w, y + view.trackH + 0.5);
    context.stroke();

    const color = trackColor(track.id);
    for (const clip of track.clips) {
      const preview = timelineDrag?.preview;
      const effective = preview?.clipId === clip.id ? { ...clip, ...preview } : clip;
      const rect = clipRect(index, effective);
      const x0 = Math.max(view.labelW, rect.x0);
      const x1 = Math.min(w, rect.x1);
      if (x1 <= view.labelW || x0 >= w) continue;
      context.fillStyle = `${color}26`;
      context.fillRect(x0, rect.y, x1 - x0, rect.h);
      context.fillStyle = color;
      context.fillRect(x0, rect.y, x1 - x0, 2);
      context.strokeStyle = clip.id === selectedClipId ? "#e8862e" : "#2b2e34";
      context.lineWidth = 1;
      context.strokeRect(x0 + 0.5, rect.y + 0.5, Math.max(0, x1 - x0 - 1), rect.h - 1);

      // Fades.
      const fadeInX = timeToX(effective.start_s + (effective.fade_in_s || 0));
      const fadeOutX = timeToX(effective.end_s - (effective.fade_out_s || 0));
      context.strokeStyle = "#00000088";
      if (effective.fade_in_s > 0) {
        context.beginPath();
        context.moveTo(x0, rect.y + 2);
        context.lineTo(fadeInX, rect.y + rect.h);
        context.stroke();
      }
      if (effective.fade_out_s > 0) {
        context.beginPath();
        context.moveTo(fadeOutX, rect.y + 2);
        context.lineTo(x1, rect.y + rect.h);
        context.stroke();
      }

      context.fillStyle = "#e8e9ec";
      context.font = "10px Inter, sans-serif";
      const label = clip.name || clip.preset;
      if (x1 - x0 > 26) {
        context.save();
        context.beginPath();
        context.rect(x0 + 3, rect.y + 2, x1 - x0 - 6, rect.h - 2);
        context.clip();
        context.fillText(label, x0 + 4, rect.y + 14);
        context.restore();
      }

      for (const keyframe of clip.keyframes || []) {
        const x = timeToX(effective.start_s + keyframe.t_s);
        if (x < x0 - 2 || x > x1 + 2) continue;
        context.fillStyle = keyframe.easing === "hold" ? "#b3b6bd" : keyframe.param === "opacity" ? "#e8862e" : "#9b7fb5";
        const cy = rect.y + rect.h - 5;
        if (keyframe.easing === "hold") {
          context.fillRect(x - 2, cy - 2, 4, 4);
        } else {
          context.beginPath();
          context.moveTo(x, cy - 3);
          context.lineTo(x + 3, cy);
          context.lineTo(x, cy + 3);
          context.lineTo(x - 3, cy);
          context.closePath();
          context.fill();
        }
      }
    }
  }

  drawBlueprintWaveform(context, w, laneTop, laneBottom);

  // Markers.
  for (const marker of doc.timing?.markers || []) {
    const x = timeToX(marker.t_s);
    if (x < view.labelW || x > w) continue;
    context.strokeStyle = marker.kind === "drop" ? "#d06b4a" : "#7c8089";
    context.setLineDash([3, 3]);
    context.beginPath();
    context.moveTo(x + 0.5, 12);
    context.lineTo(x + 0.5, laneBottom);
    context.stroke();
    context.setLineDash([]);
    context.fillStyle = "#7c8089";
    context.font = "9px Inter, sans-serif";
    context.fillText(marker.name, x + 3, 21);
  }

  // Playhead.
  const time = client.frame?.time ?? state.playback?.time_s ?? 0;
  const playhead = timeToX(time);
  if (playhead >= view.labelW && playhead <= w) {
    context.strokeStyle = "#e8862e";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(playhead + 0.5, 0);
    context.lineTo(playhead + 0.5, laneBottom);
    context.stroke();
    context.fillStyle = "#e8862e";
    context.beginPath();
    context.moveTo(playhead - 4, 0);
    context.lineTo(playhead + 5, 0);
    context.lineTo(playhead + 0.5, 7);
    context.closePath();
    context.fill();
  }

  context.strokeStyle = "#3a3e46";
  context.beginPath();
  context.moveTo(view.labelW + 0.5, 0);
  context.lineTo(view.labelW + 0.5, h);
  context.stroke();
  context.beginPath();
  context.moveTo(0, view.rulerH + 0.5);
  context.lineTo(w, view.rulerH + 0.5);
  context.stroke();
}

function drawBlueprintWaveform(context, w, laneTop, laneBottom) {
  if (!blueprint) return;
  const doc = showDoc();
  const duration = Number(blueprint.duration_s || doc?.duration_s || 0);
  const wave = blueprint.waveform || [];
  const laneHeight = Math.max(1, laneBottom - laneTop);
  if (wave.length && duration > 0) {
    const mid = laneTop + laneHeight / 2;
    context.fillStyle = "rgba(94, 154, 168, 0.16)";
    for (let x = Math.max(view.labelW, 0); x < w; x += 2) {
      const time = xToTime(x);
      if (time < 0 || time > duration) continue;
      const index = Math.min(wave.length - 1, Math.floor((time / duration) * wave.length));
      const amplitude = wave[index] * laneHeight * 0.34;
      context.fillRect(x, mid - amplitude, 1, Math.max(1, amplitude * 2));
    }
  }
  for (const section of blueprint.sections || []) {
    const x = timeToX(Number(section.start));
    if (x < view.labelW || x > w) continue;
    context.strokeStyle = "#25282d";
    context.beginPath();
    context.moveTo(x + 0.5, laneTop);
    context.lineTo(x + 0.5, laneBottom);
    context.stroke();
  }
  for (const window of blueprint.drop_windows || []) {
    const x0 = Math.max(view.labelW, timeToX(Number(window.start)));
    const x1 = Math.min(w, timeToX(Number(window.end)));
    if (x1 <= x0) continue;
    context.fillStyle = "rgba(208, 107, 74, 0.05)";
    context.fillRect(x0, laneTop, x1 - x0, laneHeight);
  }
}

function clipAtPoint(x, y) {
  const doc = showDoc();
  if (!doc) return null;
  const trackIndex = trackIndexAt(y);
  if (trackIndex < 0 || trackIndex >= doc.tracks.length) return null;
  const track = doc.tracks[trackIndex];
  for (const clip of track.clips) {
    const rect = clipRect(trackIndex, clip);
    if (x >= rect.x0 && x <= rect.x1 && y >= rect.y && y <= rect.y + rect.h) {
      return { track, clip, trackIndex, rect };
    }
  }
  return null;
}

/* ---------------------------------------------------------------- inspector */
function fieldRow(labelText, control, options = {}) {
  const id = `f_${(fieldSeq += 1)}`;
  const label = el("label", {
    for: control.id || id,
    text: labelText,
    "data-scrub-label": options.scrub ? control.id || id : undefined,
  });
  if (!control.id) control.id = id;
  const row = el("div", { class: "field-row" }, label, control);
  return row;
}

function numberInput(value, onChange, options = {}) {
  const input = el("input", {
    class: "input number",
    type: "number",
    value: value === null || value === undefined || value === "" ? "" : Number(value),
    step: options.step ?? 0.01,
    min: options.min,
    max: options.max,
  });
  input.dataset.step = String(options.step ?? 0.01);
  input.addEventListener("change", () => {
    if (input.value === "") {
      if (options.allowEmpty) onChange(null);
      else input.value = String(value ?? 0);
      return;
    }
    const parsed = Number(input.value);
    if (Number.isFinite(parsed)) onChange(parsed);
  });
  return input;
}

function textInput(value, onChange, options = {}) {
  const input = el("input", {
    class: "input",
    type: "text",
    value: value ?? "",
    placeholder: options.placeholder,
  });
  input.addEventListener("change", () => onChange(input.value));
  return input;
}

function selectInput(value, options, onChange) {
  const select = el("select", { class: "select" });
  for (const option of options) {
    select.append(
      el("option", {
        value: option[0],
        text: option[1],
        selected: option[0] === value,
      })
    );
  }
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

function colorField(value, onChange) {
  const hex = rgbToHex(value);
  const swatch = el("input", {
    type: "color",
    value: hex,
    style: "width:32px;height:24px;padding:1px;flex:none",
    "aria-label": "Colour",
  });
  const text = el("input", {
    class: "input mono",
    type: "text",
    value: hex,
    style: "width:72px;flex:none",
    "aria-label": "Colour hex",
  });
  const emit = (nextHex) => {
    swatch.value = nextHex;
    text.value = nextHex;
    onChange(hexToRgb(nextHex));
  };
  swatch.addEventListener("change", () => emit(swatch.value));
  text.addEventListener("change", () => {
    const candidate = text.value.trim();
    if (/^#[0-9a-fA-F]{6}$/.test(candidate)) emit(candidate.toLowerCase());
    else text.value = swatch.value;
  });
  return el("div", { style: "display:flex;gap:4px;align-items:center" }, swatch, text);
}

function toggleRow(labelText, checked, onChange) {
  const input = el("input", { type: "checkbox" });
  input.checked = Boolean(checked);
  input.addEventListener("change", () => onChange(input.checked));
  return el(
    "div",
    { class: "check-row" },
    el("span", { text: labelText }),
    el("label", { class: "toggle" }, input, el("span"))
  );
}

function vec3Field(labelText, value, onChange, options = {}) {
  const current = [...(value || [0, 0, 0])];
  const inputs = current.map((component, index) => {
    const input = numberInput(component, (parsed) => {
      current[index] = parsed;
      onChange([...current]);
    }, { step: options.step ?? 0.01 });
    return input;
  });
  return fieldRow(labelText, el("div", { class: "triple" }, ...inputs), { scrub: true });
}

function section(title, ...children) {
  return el(
    "div",
    { class: "inspector-section" },
    el("h4", { text: title }),
    ...children
  );
}

function renderInspector() {
  if (!state) return;
  if (inspectorTab === "fixture") renderFixtureInspector();
  else renderClipInspector();
}

function renderFixtureInspector() {
  const ids = [...selectedFixtures];
  if (ids.length > 1) {
    dom.inspectorBody.replaceChildren(
      el("div", { class: "inspector-title" }, el("strong", { text: `${ids.length} fixtures` }), el("span", { text: "multi-select" })),
      el("p", { class: "empty-state", text: "Drag the selection onto a list group to add it. Open a single fixture to edit transforms and output." }),
      el("button", {
        class: "button small",
        type: "button",
        text: "Delete Selection",
        onclick: () => deleteFixtures(ids),
      })
    );
    return;
  }
  const fixture = ids.length === 1 ? findFixture(ids[0]) : null;
  if (!fixture) {
    dom.inspectorBody.replaceChildren(
      el("div", { class: "inspector-title" }, el("strong", { text: "Fixture" }), el("span", { text: "none" })),
      el("p", { class: "empty-state", text: "Select a fixture in the viewport or the Rig Manager to edit its geometry, transform and output." })
    );
    return;
  }
  const patch = (body) => patchFixture(fixture.id, body);
  const geometry = fixture.geometry;
  const transform = fixture.transform || {};
  const output = fixture.output || {};
  const caps = fixture.capabilities || {};
  const children = [
    el(
      "div",
      { class: "inspector-title" },
      el("strong", { text: fixture.name }),
      el("span", { text: fixture.id })
    ),
    section(
      "Fixture",
      fieldRow("Name", textInput(fixture.name, (value) => patch({ name: value || fixture.id }))),
      toggleRow("Output enabled", output.enabled !== false, (value) => patch({ output: { enabled: value } })),
      fieldRow("Protocol", selectInput(output.protocol || "none", [
        ["none", "None"],
        ["hue_z2m", "Hue Z2M"],
        ["wled_ddp", "WLED DDP"],
        ["e131", "E1.31"],
        ["ha_light", "HA Light"],
      ], (value) => patch({ output: { protocol: value } }))),
      fieldRow("Address", textInput(output.address ?? "", (value) => patch({ output: { address: value } }))),
      fieldRow("Host", textInput(output.host ?? "", (value) => patch({ output: { host: value } }))),
      fieldRow("Port", numberInput(output.port ?? "", (value) => patch({ output: { port: value || null } }), { step: 1, min: 1, max: 65535, allowEmpty: true })),
      fieldRow("Universe", numberInput(output.universe ?? 1, (value) => patch({ output: { universe: value } }), { step: 1, min: 1 })),
      fieldRow("Channel", numberInput(output.channel ?? 1, (value) => patch({ output: { channel: value } }), { step: 1, min: 1, max: 512 })),
      fieldRow("Entity", textInput(output.entity_id ?? "", (value) => patch({ output: { entity_id: value || null } })))
    ),
  ];

  if (geometry.type === "point") {
    children.push(
      section(
        "Point Geometry",
        vec3Field("Position", geometry.pos, (value) => patch({ geometry: { pos: value } })),
        vec3Field("Aim", geometry.aim, (value) => patch({ geometry: { aim: value } })),
        fieldRow("Beam deg", numberInput(geometry.beam_deg, (value) => patch({ geometry: { beam_deg: value } }), { step: 1, min: 1, max: 179 }), { scrub: true })
      )
    );
  } else {
    children.push(
      section(
        "Line Geometry",
        vec3Field("Start", geometry.start, (value) => patch({ geometry: { start: value } })),
        vec3Field("End", geometry.end, (value) => patch({ geometry: { end: value } })),
        fieldRow("Pixels", numberInput(geometry.pixels, (value) => patch({ geometry: { pixels: Math.round(value) } }), { step: 1, min: 2, max: 4096 }), { scrub: true }),
        vec3Field("Aim", geometry.aim, (value) => patch({ geometry: { aim: value } })),
        fieldRow("Beam deg", numberInput(geometry.beam_deg, (value) => patch({ geometry: { beam_deg: value } }), { step: 1, min: 1, max: 179 }), { scrub: true })
      )
    );
  }

  children.push(
    section(
      "Transform",
      vec3Field("Translate", transform.translate, (value) => patch({ transform: { translate: value } })),
      vec3Field("Rotate", transform.rotate_deg, (value) => patch({ transform: { rotate_deg: value } })),
      vec3Field("Pivot", transform.pivot, (value) => patch({ transform: { pivot: value } })),
      vec3Field("Scale", transform.scale, (value) => patch({ transform: { scale: value } }), { step: 0.01 })
    ),
    section(
      "Capabilities",
      fieldRow("Colour space", selectInput(caps.color_space || "rgb", [
        ["rgb", "RGB"],
        ["rgb_amber_1800k", "RGB + amber"],
        ["dimmer_only", "Dimmer only"],
        ["cie_xy", "CIE xy"],
      ], (value) => patch({ capabilities: { color_space: value } }))),
      fieldRow("Max FPS", numberInput(caps.max_fps ?? 30, (value) => patch({ capabilities: { max_fps: value } }), { step: 1, min: 0.1, max: 240 }), { scrub: true }),
      fieldRow("Min glow", numberInput(caps.min_brightness ?? 0, (value) => patch({ capabilities: { min_brightness: value } }), { step: 0.01, min: 0, max: 1 }), { scrub: true }),
      toggleRow("ZCL snap", caps.zcl_snap, (value) => patch({ capabilities: { zcl_snap: value } }))
    ),
    el("button", {
      class: "button small danger",
      type: "button",
      text: "Delete Fixture",
      onclick: () => deleteFixtures([fixture.id]),
    })
  );
  dom.inspectorBody.replaceChildren(...children);
}

function renderClipInspector() {
  const found = findClip();
  const doc = showDoc();
  if (!doc) {
    dom.inspectorBody.replaceChildren(
      el("div", { class: "inspector-title" }, el("strong", { text: "Clip" }), el("span", { text: "no show" })),
      el("p", { class: "empty-state", text: "No ShowDoc is loaded. Create a show to begin." })
    );
    return;
  }
  const children = [];
  if (found) {
    const { clip, track } = found;
    const preset = presetById(clip.preset);
    children.push(
      el(
        "div",
        { class: "inspector-title" },
        el("strong", { text: clip.name || clip.preset }),
        el("span", { text: `${track.name} / ${clip.id}` })
      ),
      section(
        "Clip",
        fieldRow("Name", textInput(clip.name || "", (value) => commitClip(clip.id, "rename clip", (target) => { target.name = value || null; }))),
        fieldRow("Start", numberInput(clip.start_s, (value) => commitClip(clip.id, "trim clip", (target) => { target.start_s = clamp(value, 0, target.end_s - 0.1); }), { step: 0.01, min: 0 }), { scrub: true }),
        fieldRow("End", numberInput(clip.end_s, (value) => commitClip(clip.id, "trim clip", (target) => { target.end_s = clamp(value, target.start_s + 0.1, duration()); }), { step: 0.01, min: 0 }), { scrub: true }),
        fieldRow("Fade in", numberInput(clip.fade_in_s, (value) => commitClip(clip.id, "fade", (target) => { target.fade_in_s = clamp(value, 0, target.end_s - target.start_s); }), { step: 0.01, min: 0 }), { scrub: true }),
        fieldRow("Fade out", numberInput(clip.fade_out_s, (value) => commitClip(clip.id, "fade", (target) => { target.fade_out_s = clamp(value, 0, target.end_s - target.start_s); }), { step: 0.01, min: 0 }), { scrub: true }),
        fieldRow("Opacity", numberInput(clip.opacity, (value) => commitClip(clip.id, "opacity", (target) => { target.opacity = clamp(value, 0, 1); }), { step: 0.01, min: 0, max: 1 }), { scrub: true }),
        fieldRow("Preset", selectInput(clip.preset, (state.presets || []).map((item) => [item.id, item.name]), (value) => commitClip(clip.id, "change preset", (target) => { target.preset = value; target.params = defaultParams(value); }))),
        toggleRow("ZCL snap", clip.snap_zcl, (value) => commitClip(clip.id, "snap", (target) => { target.snap_zcl = value; }))
      )
    );

    if (preset) {
      const rows = preset.params.map((spec) => {
        const value = clip.params?.[spec.key] ?? spec.default;
        if (spec.type === "color") {
          return fieldRow(spec.label, colorField(value, (next) => {
            commitClip(clip.id, `param ${spec.key}`, (target) => {
              target.params = { ...target.params, [spec.key]: next };
            });
          }));
        }
        if (spec.type === "bool") {
          return toggleRow(spec.label, Boolean(value), (next) => {
            commitClip(clip.id, `param ${spec.key}`, (target) => {
              target.params = { ...target.params, [spec.key]: next };
            });
          });
        }
        if (spec.type === "select") {
          return fieldRow(spec.label, selectInput(String(value), spec.options || [], (next) => {
            commitClip(clip.id, `param ${spec.key}`, (target) => {
              target.params = { ...target.params, [spec.key]: next };
            });
          }));
        }
        return fieldRow(spec.label, numberInput(value, (next) => {
          commitClip(clip.id, `param ${spec.key}`, (target) => {
            target.params = { ...target.params, [spec.key]: next };
          });
        }, { step: spec.step ?? 0.01, min: spec.min, max: spec.max }), { scrub: true });
      });
      children.push(section("Preset Parameters", ...rows));
    }

    children.push(renderMaskSection(clip));
    children.push(renderFiltersSection(clip));
    children.push(renderKeyframeSection(clip));
    children.push(
      el("button", {
        class: "button small danger",
        type: "button",
        text: "Delete Clip",
        onclick: () => deleteClip(clip.id),
      })
    );
  } else {
    children.push(...renderShowSection(doc));
  }
  children.push(renderTracksSection(doc));
  dom.inspectorBody.replaceChildren(...children);
}

function renderShowSection(doc) {
  return [
    el(
      "div",
      { class: "inspector-title" },
      el("strong", { text: doc.name }),
      el("span", { text: `${doc.tracks.length} tracks / ${doc.tracks.reduce((sum, track) => sum + track.clips.length, 0)} clips` })
    ),
    section(
      "Show",
      fieldRow("Name", textInput(doc.name, (value) => commitShow("rename show", (target) => { target.name = value || target.id; }))),
      fieldRow("BPM", numberInput(doc.timing.bpm, (value) => commitShow("bpm", (target) => { target.timing.bpm = clamp(value, 1, 400); target.timing.beat_grid_s = 0; }), { step: 0.1, min: 1, max: 400 }), { scrub: true }),
      fieldRow("Beat offset", numberInput(doc.timing.beat_offset_s, (value) => commitShow("beat offset", (target) => { target.timing.beat_offset_s = value; }), { step: 0.01 }), { scrub: true }),
      fieldRow("Duration", numberInput(doc.duration_s, (value) => commitShow("duration", (target) => {
        const longest = target.tracks.reduce((max, track) => Math.max(max, ...track.clips.map((clip) => clip.end_s)), 0);
        target.duration_s = Math.max(value, longest + 1, 1);
      }), { step: 0.1, min: 1 }), { scrub: true }),
      el("p", { class: "empty-state", text: "Select a clip in the timeline to edit its preset, mask, filters and keyframes." })
    ),
    el("button", {
      class: "button small",
      type: "button",
      text: "Add Track",
      onclick: addTrack,
    }),
  ];
}

function renderTracksSection(doc) {
  const rows = doc.tracks.map((track) => {
    const selected = selectedTrackId === track.id;
    return el(
      "div",
      {
        class: `list-row${selected ? " selected" : ""}`,
        role: "button",
        tabindex: "0",
        onclick: () => {
          selectedTrackId = track.id;
          renderInspector();
          requestDraw();
        },
        onkeydown: (event) => {
          if (event.key === "Enter" || event.code === "Space") {
            event.preventDefault();
            selectedTrackId = track.id;
            renderInspector();
            requestDraw();
          }
        },
      },
      el("span", { class: "row-name", text: track.name }),
      el("span", { class: "row-meta", text: track.target_group }),
      el("button", {
        class: "button small icon-button",
        type: "button",
        text: track.muted ? "M" : "m",
        title: "Mute track",
        style: `width:20px;border-color:${track.muted ? "var(--danger)" : "var(--line-strong)"}`,
        onclick: (event) => {
          event.stopPropagation();
          commitShow("mute track", (target) => {
            const item = target.tracks.find((candidate) => candidate.id === track.id);
            if (item) item.muted = !item.muted;
          });
        },
      }),
      el("button", {
        class: "button small icon-button",
        type: "button",
        text: track.solo ? "S" : "s",
        title: "Solo track",
        style: `width:20px;border-color:${track.solo ? "var(--accent-dim)" : "var(--line-strong)"}`,
        onclick: (event) => {
          event.stopPropagation();
          commitShow("solo track", (target) => {
            const item = target.tracks.find((candidate) => candidate.id === track.id);
            if (item) item.solo = !item.solo;
          });
        },
      })
    );
  });
  return section("Tracks", ...rows);
}

function renderMaskSection(clip) {
  const mask = clip.spatial_mask || { kind: "none" };
  const set = (patch, label = "mask") => commitClip(clip.id, label, (target) => {
    target.spatial_mask = { ...target.spatial_mask, ...patch };
  });
  const rows = [
    fieldRow("Kind", selectInput(mask.kind, [
      ["none", "None"],
      ["plane_sweep", "Plane sweep"],
      ["sphere_narrow", "Sphere"],
      ["box", "Box"],
      ["parity", "Parity"],
    ], (value) => set({ kind: value }))),
  ];
  if (mask.kind === "plane_sweep") {
    rows.push(
      fieldRow("Axis", selectInput(mask.axis, [["x", "X"], ["y", "Y"], ["z", "Z"]], (value) => set({ axis: value }))),
      fieldRow("Position", numberInput(mask.position, (value) => set({ position: clamp(value, 0, 1) }), { step: 0.01, min: 0, max: 1 }), { scrub: true }),
      fieldRow("Width", numberInput(mask.width, (value) => set({ width: Math.max(0.001, value) }), { step: 0.01, min: 0.001, max: 2 }), { scrub: true })
    );
  } else if (mask.kind === "sphere_narrow") {
    rows.push(
      vec3Field("Centre", mask.center, (value) => set({ center: value.map((v) => clamp(v, 0, 1)) })),
      fieldRow("Radius", numberInput(mask.radius, (value) => set({ radius: Math.max(0.001, value) }), { step: 0.01, min: 0.001, max: 3 }), { scrub: true })
    );
  } else if (mask.kind === "box") {
    rows.push(
      vec3Field("Min", mask.box_min, (value) => set({ box_min: value.map((v) => clamp(v, 0, 1)) })),
      vec3Field("Max", mask.box_max, (value) => set({ box_max: value.map((v) => clamp(v, 0, 1)) }))
    );
  } else if (mask.kind === "parity") {
    rows.push(
      fieldRow("Modulo", numberInput(mask.modulo, (value) => set({ modulo: Math.round(clamp(value, 2, 64)) }), { step: 1, min: 2, max: 64 }), { scrub: true }),
      fieldRow("Remainder", numberInput(mask.remainder, (value) => set({ remainder: Math.round(clamp(value, 0, 63)) }), { step: 1, min: 0, max: 63 }), { scrub: true })
    );
  }
  if (mask.kind !== "none") {
    rows.push(
      fieldRow("Softness", numberInput(mask.softness, (value) => set({ softness: clamp(value, 0, 1) }), { step: 0.01, min: 0, max: 1 }), { scrub: true }),
      toggleRow("Invert", mask.invert, (value) => set({ invert: value }))
    );
  }
  return section("Spatial Mask", ...rows);
}

function renderFiltersSection(clip) {
  const filters = clip.filters || [];
  const chips = filters.map((filter, index) =>
    el(
      "span",
      { class: "chip" },
      el("button", {
        type: "button",
        text: filter.enabled ? "ON" : "OFF",
        title: "Toggle filter",
        style: `color:${filter.enabled ? "var(--ok)" : "var(--text-2)"};width:18px`,
        onclick: () => commitClip(clip.id, "filter", (target) => {
          target.filters[index].enabled = !target.filters[index].enabled;
        }),
      }),
      state.filters?.find((item) => item.id === filter.type)?.name || filter.type,
      el("button", {
        type: "button",
        text: "x",
        title: "Remove filter",
        onclick: () => commitClip(clip.id, "remove filter", (target) => {
          target.filters.splice(index, 1);
        }),
      })
    )
  );
  return section(
    "Filters",
    chips.length ? el("div", { class: "chip-list" }, ...chips) : el("p", { class: "empty-state", text: "No filters. Click a filter in the library to add it." })
  );
}

function renderKeyframeSection(clip) {
  const rows = (clip.keyframes || []).map((keyframe, index) =>
    el(
      "div",
      { class: "chip" },
      el("span", { class: "mono", text: `${keyframe.t_s.toFixed(2)}s ${keyframe.param}` }),
      el("button", {
        type: "button",
        text: "x",
        title: "Remove keyframe",
        onclick: () => commitClip(clip.id, "remove keyframe", (target) => {
          target.keyframes.splice(index, 1);
        }),
      })
    )
  );
  const local = client.frame ? client.frame.time - clip.start_s : 0;
  return section(
    "Keyframes",
    rows.length ? el("div", { class: "chip-list" }, ...rows) : el("p", { class: "empty-state", text: "No keyframes on this clip." }),
    el("button", {
      class: "button small",
      type: "button",
      text: "Key Opacity",
      onclick: () => addOpacityKeyframe(clip.id, clamp(local, 0, clip.end_s - clip.start_s)),
    })
  );
}

/* ----------------------------------------------------------------- actions */
function selectFixture(id, additive = false) {
  if (!additive) selectedFixtures.clear();
  if (additive && selectedFixtures.has(id)) selectedFixtures.delete(id);
  else selectedFixtures.add(id);
  inspectorTab = "fixture";
  updateTabButtons();
  renderFixtures();
  renderInspector();
  requestDraw();
}

function selectClip(id) {
  const found = findClip(id);
  if (!found) return;
  selectedClipId = id;
  selectedTrackId = found.track.id;
  inspectorTab = "clip";
  updateTabButtons();
  renderInspector();
  requestDraw();
}

function updateTabButtons() {
  document.querySelectorAll(".inspector-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === inspectorTab);
  });
}

async function patchFixture(id, patch, message) {
  try {
    await client.patch(`/api/rig/fixtures/${id}`, patch);
    await client.refresh();
    if (message) toast(message, "success");
  } catch (error) {
    toast(error.message, "danger");
    client.refresh().catch(() => {});
  }
}

async function deleteFixtures(ids) {
  const confirmed = await modalConfirm("Delete Fixtures", `Remove ${ids.length} fixture${ids.length === 1 ? "" : "s"} from the working rig? This does not save until you press Save.`, "Delete");
  if (!confirmed) return;
  for (const id of ids) {
    try {
      await client.del(`/api/rig/fixtures/${id}`);
    } catch (error) {
      toast(error.message, "danger");
      return;
    }
  }
  selectedFixtures.clear();
  await client.refresh();
}

async function assignGroup(groupId, fixtureIds) {
  try {
    await client.post(`/api/rig/groups/${groupId}/assign`, { fixture_ids: fixtureIds });
    await client.refresh();
    toast(`Added ${fixtureIds.length} fixture${fixtureIds.length === 1 ? "" : "s"} to ${groupId}`, "success");
  } catch (error) {
    toast(error.message, "danger");
  }
}

function applyPreset(presetId) {
  const found = findClip();
  if (!found) {
    toast("Select a clip in the timeline first.", "info");
    return;
  }
  commitClip(found.clip.id, "change preset", (target) => {
    target.preset = presetId;
    target.params = defaultParams(presetId);
  });
}

function addFilter(filterId) {
  const found = findClip();
  if (!found) {
    toast("Select a clip in the timeline first.", "info");
    return;
  }
  commitClip(found.clip.id, "add filter", (target) => {
    const existing = target.filters.find((filter) => filter.type === filterId);
    if (existing) existing.enabled = true;
    else target.filters.push({ type: filterId, enabled: true, params: {} });
  });
}

async function addFixture(kind) {
  const doc = state.rig.document;
  const room = doc.room;
  const ids = new Set(doc.fixtures.map((fixture) => fixture.id));
  const id = uniqueId(kind === "line" ? "line" : "spot", ids);
  const center = room.min.map((value, index) => (value + room.max[index]) / 2);
  const geometry = kind === "line"
    ? {
        type: "line",
        start: [room.min[0] + 0.4, center[1], Math.max(room.min[2] + 3, center[2])],
        end: [room.max[0] - 0.4, center[1], Math.max(room.min[2] + 3, center[2])],
        pixels: 60,
        aim: [0, 0, -1],
        beam_deg: 120,
      }
    : {
        type: "point",
        pos: center,
        aim: [0, 0, -1],
        beam_deg: 45,
      };
  try {
    const rig = await client.post("/api/rig/fixtures", {
      id,
      name: kind === "line" ? `Line ${ids.size + 1}` : `Spot ${ids.size + 1}`,
      geometry,
    });
    selectedFixtures = new Set([id]);
    selectedGroupId = null;
    inspectorTab = "fixture";
    updateTabButtons();
    await client.refresh();
    toast(`${kind === "line" ? "Line" : "Point"} fixture added`, "success");
  } catch (error) {
    toast(error.message, "danger");
  }
}

async function addOpacityKeyframe(clipId, localTime) {
  commitClip(clipId, "add keyframe", (target) => {
    target.keyframes.push({ t_s: clamp(localTime, 0, target.end_s - target.start_s), param: "opacity", value: target.opacity, easing: "linear" });
    target.keyframes.sort((a, b) => a.t_s - b.t_s);
  });
}

async function deleteClip(clipId) {
  const found = findClip(clipId);
  if (!found) return;
  await commitShow("delete clip", (doc) => {
    const track = doc.tracks.find((item) => item.id === found.track.id);
    if (track) track.clips = track.clips.filter((clip) => clip.id !== clipId);
  });
  selectedClipId = null;
  renderInspector();
  requestDraw();
}

async function commitClip(clipId, label, mutate) {
  const found = findClip(clipId);
  if (!found) return;
  await commitShow(label, (doc) => {
    for (const track of doc.tracks) {
      const clip = track.clips.find((item) => item.id === clipId);
      if (clip) {
        mutate(clip, track);
        return;
      }
    }
  });
}

async function commitShow(label, mutate) {
  const current = showDoc();
  if (!current) return;
  const doc = deepClone(current);
  mutate(doc);
  try {
    const updated = await client.put(`/api/shows/${doc.id}?label=${encodeURIComponent(label)}`, doc);
    state.show.document = updated;
    renderInspector();
    requestDraw();
    refreshHistory();
  } catch (error) {
    toast(error.message, "danger");
    client.refresh().catch(() => {});
  }
}

async function refreshHistory() {
  const id = activeShowId();
  if (!id) {
    dom.history.textContent = "";
    return;
  }
  try {
    const history = await client.get(`/api/shows/${id}/history`);
    dom.history.textContent = `undo ${history.undo_depth} / redo ${history.redo_depth}`;
    dom.history.title = history.undo_labels.join("\n") || "No undo history";
  } catch {
    dom.history.textContent = "";
  }
}

async function addTrack() {
  const doc = showDoc();
  if (!doc) return;
  const ids = new Set(doc.tracks.map((track) => track.id));
  const id = uniqueId("track", ids);
  await commitShow("add track", (target) => {
    target.tracks.push({
      id,
      name: `Track ${target.tracks.length + 1}`,
      target_group: "all",
      blend_mode: "normal",
      opacity: 1,
      muted: false,
      solo: false,
      clips: [],
    });
  });
  selectedTrackId = id;
  renderInspector();
}

async function addClip() {
  const doc = showDoc();
  if (!doc) return;
  const track = findTrack() || doc.tracks[0];
  if (!track) {
    toast("Add a track first.", "info");
    return;
  }
  const start = clamp(snapTime(client.frame?.time ?? state.playback.time_s ?? 0), 0, Math.max(0, doc.duration_s - 0.25));
  const length = Math.min(gridSeconds() * 4, doc.duration_s - start);
  if (length <= 0.05) {
    toast("No room for a clip at the playhead.", "info");
    return;
  }
  const ids = new Set(doc.tracks.flatMap((item) => item.clips.map((clip) => clip.id)));
  const id = uniqueId("clip", ids);
  await commitShow("add clip", (target) => {
    const targetTrack = target.tracks.find((item) => item.id === track.id);
    if (!targetTrack) return;
    targetTrack.clips.push({
      id,
      name: "New Clip",
      start_s: start,
      end_s: start + length,
      fade_in_s: 0,
      fade_out_s: 0,
      preset: "solid",
      opacity: 1,
      params: defaultParams("solid"),
      keyframes: [],
      spatial_mask: { kind: "none" },
      filters: [],
      snap_zcl: false,
    });
  });
  selectedClipId = id;
  selectedTrackId = track.id;
  inspectorTab = "clip";
  updateTabButtons();
  renderInspector();
  requestDraw();
}

async function splitClip() {
  const found = findClip();
  if (!found) {
    toast("Select a clip to split.", "info");
    return;
  }
  const time = client.frame?.time ?? state.playback.time_s ?? 0;
  const { clip } = found;
  if (time <= clip.start_s + 0.05 || time >= clip.end_s - 0.05) {
    toast("Move the playhead inside the selected clip.", "info");
    return;
  }
  const ids = new Set(showDoc().tracks.flatMap((track) => track.clips.map((item) => item.id)));
  const rightId = uniqueId(`${clip.id}_b`, ids);
  await commitShow("split clip", (doc) => {
    const track = doc.tracks.find((item) => item.id === found.track.id);
    if (!track) return;
    const index = track.clips.findIndex((item) => item.id === clip.id);
    if (index < 0) return;
    const original = track.clips[index];
    const splitLocal = time - original.start_s;
    const left = deepClone(original);
    left.end_s = time;
    left.fade_in_s = Math.min(left.fade_in_s, left.end_s - left.start_s);
    left.fade_out_s = 0;
    left.keyframes = (original.keyframes || []).filter((keyframe) => keyframe.t_s <= splitLocal);
    const right = deepClone(original);
    right.id = rightId;
    right.name = original.name ? `${original.name} B` : null;
    right.start_s = time;
    right.fade_in_s = 0;
    right.fade_out_s = Math.min(right.fade_out_s, right.end_s - right.start_s);
    right.keyframes = (original.keyframes || [])
      .filter((keyframe) => keyframe.t_s >= splitLocal)
      .map((keyframe) => ({ ...keyframe, t_s: keyframe.t_s - splitLocal }));
    track.clips.splice(index, 1, left, right);
  });
  selectedClipId = rightId;
  renderInspector();
  requestDraw();
}

async function undoShow() {
  const id = activeShowId();
  if (!id) return;
  try {
    const result = await client.post(`/api/shows/${id}/undo`);
    if (result.document) {
      state.show.document = result.document;
      sanitizeSelection();
      renderInspector();
      requestDraw();
      refreshHistory();
    } else {
      toast("Nothing to undo.", "info");
    }
  } catch (error) {
    toast(error.message, "danger");
  }
}

async function redoShow() {
  const id = activeShowId();
  if (!id) return;
  try {
    const result = await client.post(`/api/shows/${id}/redo`);
    if (result.document) {
      state.show.document = result.document;
      sanitizeSelection();
      renderInspector();
      requestDraw();
      refreshHistory();
    } else {
      toast("Nothing to redo.", "info");
    }
  } catch (error) {
    toast(error.message, "danger");
  }
}

async function toggleMute() {
  const track = findTrack();
  if (!track) {
    toast("Select a track first.", "info");
    return;
  }
  await commitShow("mute track", (doc) => {
    const target = doc.tracks.find((item) => item.id === track.id);
    if (target) target.muted = !target.muted;
  });
}

/* -------------------------------------------------------------- rig actions */
function slug(text) {
  return String(text).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "rig";
}

async function confirmDiscard() {
  if (!state?.rig?.dirty) return true;
  return modalConfirm("Unsaved Rig Changes", "The working rig has changes that have not been saved. Continue and discard them?", "Discard");
}

async function activateRig(id) {
  if (!id || id === state.rig.active) return;
  if (!(await confirmDiscard())) {
    dom.rigSelect.value = state.rig.active || "";
    return;
  }
  try {
    await client.post(`/api/rigs/${id}/activate?force=true`);
    selectedFixtures.clear();
    selectedGroupId = null;
    await client.refresh();
    toast(`Activated ${id}`, "success");
  } catch (error) {
    toast(error.message, "danger");
    dom.rigSelect.value = state.rig.active || "";
  }
}

async function newRig() {
  if (!(await confirmDiscard())) return;
  const values = await modalForm({
    title: "New Rig Preset",
    fields: [
      { name: "name", label: "Name", value: "New Rig" },
      { name: "preset_id", label: "Id", value: "new_rig", hint: "lowercase, no spaces" },
    ],
    confirmLabel: "Create",
  });
  if (!values) return;
  try {
    await client.post("/api/rigs", {
      name: values.name,
      preset_id: values.preset_id || slug(values.name),
      activate: true,
    });
    selectedFixtures.clear();
    selectedGroupId = null;
    await client.refresh();
    toast("Rig preset created", "success");
  } catch (error) {
    toast(error.message, "danger");
  }
}

async function saveRig() {
  try {
    const entry = await client.post("/api/rig/save");
    await client.refresh();
    toast(`Saved ${entry.name}`, "success");
  } catch (error) {
    toast(error.message, "danger");
  }
}

async function saveRigAs() {
  const active = state.rig.active || "hueme_loft";
  const values = await modalForm({
    title: "Save Rig As",
    fields: [
      { name: "id", label: "Id", value: `${active}_copy` },
      { name: "name", label: "Name", value: `${state.rig.document.name} Copy` },
    ],
    confirmLabel: "Save As",
  });
  if (!values) return;
  const exists = state.rig.presets.some((preset) => preset.id === values.id);
  if (exists && values.id !== state.rig.document.id) {
    const overwrite = await modalConfirm(
      "Overwrite Rig Preset",
      `A rig preset with the id "${values.id}" already exists. Overwrite it?`,
      "Overwrite"
    );
    if (!overwrite) return;
  }
  try {
    const suffix = exists && values.id !== state.rig.document.id ? "?force=true" : "";
    await client.post(`/api/rig/save-as${suffix}`, { id: values.id, name: values.name });
    await client.refresh();
    toast(`Saved as ${values.name}`, "success");
  } catch (error) {
    toast(error.message, "danger");
  }
}

async function cloneRig() {
  const active = state.rig.active;
  if (!active) return;
  const values = await modalForm({
    title: "Clone Rig Preset",
    fields: [
      { name: "new_id", label: "New id", value: `${active}_clone` },
      { name: "name", label: "Name", value: `${state.rig.document.name} Clone` },
    ],
    confirmLabel: "Clone",
  });
  if (!values) return;
  try {
    await client.post(`/api/rigs/${active}/clone`, {
      new_id: values.new_id,
      name: values.name,
      activate: true,
    });
    selectedFixtures.clear();
    selectedGroupId = null;
    await client.refresh();
    toast("Rig cloned", "success");
  } catch (error) {
    toast(error.message, "danger");
  }
}

async function deleteRig() {
  const active = state.rig.active;
  if (!active) return;
  const confirmed = await modalConfirm(
    "Delete Rig Preset",
    `Delete the rig preset "${state.rig.document.name}"? This removes the file from config/rigs and cannot be undone.`,
    "Delete"
  );
  if (!confirmed) return;
  try {
    await client.del(`/api/rigs/${active}?force=true`);
    selectedFixtures.clear();
    selectedGroupId = null;
    await client.refresh();
    toast("Rig preset deleted", "success");
  } catch (error) {
    toast(error.message, "danger");
  }
}

async function newGroup() {
  const values = await modalForm({
    title: "New Group",
    fields: [
      { name: "name", label: "Name", value: "New Group" },
      { name: "type", label: "Type", type: "select", options: [
        ["list", "List"],
        ["spatial_box", "Spatial box"],
        ["spatial_sphere", "Spatial sphere"],
      ] },
    ],
    confirmLabel: "Create",
  });
  if (!values) return;
  const room = state.rig.document.room;
  const id = uniqueId(slug(values.name), new Set(state.rig.document.groups.map((group) => group.id)));
  const group = { id, name: values.name, type: values.type, fixtures: [] };
  if (values.type === "list") {
    const ids = [...selectedFixtures];
    if (!ids.length) {
      toast("Select one or more fixtures before creating a list group.", "info");
      return;
    }
    group.fixtures = ids;
  } else if (values.type === "spatial_box") {
    group.min = [...room.min];
    group.max = [...room.max];
  } else {
    group.center = room.min.map((value, index) => (value + room.max[index]) / 2);
    group.radius = 1.0;
  }
  try {
    await client.put(`/api/rig/groups/${id}`, group);
    selectedGroupId = id;
    await client.refresh();
    toast("Group created", "success");
  } catch (error) {
    toast(error.message, "danger");
  }
}

function renderGroupEditor() {
  const group = state.rig.document.groups.find((item) => item.id === selectedGroupId);
  if (!group) {
    dom.groupEditor.className = "hidden";
    dom.groupEditor.replaceChildren();
    return;
  }
  dom.groupEditor.className = "";
  const rows = [];
  if (group.type === "all") {
    rows.push(
      el("p", { class: "empty-state", text: `Name: ${group.name}. The all group is immutable and always contains every voxel.` })
    );
  } else {
    rows.push(
      fieldRow("Name", textInput(group.name, (value) => {
        if (!value) return;
        saveGroup({ ...group, name: value });
      }))
    );
  }
  if (group.type === "all") {
    // No editable fields: all is an invariant of the rig.
  } else if (group.type === "list") {
    rows.push(
      el("div", { class: "chip-list" },
        ...group.fixtures.map((fixtureId) =>
          el("span", { class: "chip" }, fixtureId,
            el("button", {
              type: "button",
              text: "x",
              title: "Remove from group",
              onclick: () => {
                const next = group.fixtures.filter((id) => id !== fixtureId);
                if (!next.length) {
                  deleteGroup(group);
                  return;
                }
                saveGroup({ ...group, fixtures: next });
              },
            })
          )
        )
      ),
      el("p", { class: "empty-state", text: "Drag fixtures from the fixture list onto this group row." })
    );
  } else if (group.type === "spatial_box") {
    rows.push(
      vec3Field("Min", group.min, (value) => saveGroup({ ...group, min: value })),
      vec3Field("Max", group.max, (value) => saveGroup({ ...group, max: value }))
    );
  } else {
    rows.push(
      vec3Field("Centre", group.center, (value) => saveGroup({ ...group, center: value })),
      fieldRow("Radius", numberInput(group.radius, (value) => saveGroup({ ...group, radius: Math.max(0.01, value) }), { step: 0.01, min: 0.01 }), { scrub: true })
    );
  }
  if (group.type !== "all") {
    rows.push(
      el("button", {
        class: "button small danger",
        type: "button",
        text: "Delete Group",
        onclick: () => deleteGroup(group),
      })
    );
  }
  dom.groupEditor.replaceChildren(...rows);
}

async function saveGroup(group) {
  try {
    await client.put(`/api/rig/groups/${group.id}`, group);
    await client.refresh();
  } catch (error) {
    toast(error.message, "danger");
  }
}

async function deleteGroup(group) {
  const confirmed = await modalConfirm("Delete Group", `Delete the group "${group.name}"?`, "Delete");
  if (!confirmed) return;
  try {
    await client.del(`/api/rig/groups/${group.id}`);
    selectedGroupId = null;
    await client.refresh();
    toast("Group deleted", "success");
  } catch (error) {
    toast(error.message, "danger");
  }
}

/* ----------------------------------------------------------------- dialogs */
function modalConfirm(title, message, confirmLabel = "Confirm") {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    const form = document.createElement("form");
    form.method = "dialog";
    const head = el("div", { class: "dialog-head", text: title });
    const body = el("div", { class: "dialog-body" }, el("p", { text: message }));
    const actions = el(
      "div",
      { class: "dialog-actions" },
      el("button", { class: "button", type: "button", text: "Cancel", onclick: () => dialog.close("cancel") }),
      el("button", { class: "button primary", type: "submit", text: confirmLabel })
    );
    form.append(head, body, actions);
    dialog.append(form);
    dialog.addEventListener("close", () => {
      const confirmed = dialog.returnValue === "confirm";
      dialog.remove();
      resolve(confirmed);
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      dialog.close("confirm");
    });
    document.body.append(dialog);
    dialog.showModal();
  });
}

function modalForm({ title, fields, confirmLabel = "OK", hint }) {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    const form = document.createElement("form");
    form.method = "dialog";
    const controls = {};
    const rows = fields.map((field) => {
      let control;
      if (field.type === "select") {
        control = el("select", { class: "select" });
        for (const option of field.options || []) {
          control.append(
            el("option", {
              value: option[0],
              text: option[1],
              selected: field.value !== undefined && option[0] === field.value,
            })
          );
        }
      } else {
        control = el("input", {
          class: "input",
          type: field.type || "text",
          value: field.value ?? "",
          placeholder: field.placeholder || "",
          required: field.optional ? undefined : true,
        });
      }
      controls[field.name] = control;
      return fieldRow(field.label, control);
    });
    if (hint) rows.push(el("p", { class: "empty-state", text: hint }));
    const actions = el(
      "div",
      { class: "dialog-actions" },
      el("button", { class: "button", type: "button", text: "Cancel", onclick: () => dialog.close("cancel") }),
      el("button", { class: "button primary", type: "submit", text: confirmLabel })
    );
    form.append(el("div", { class: "dialog-head", text: title }), el("div", { class: "dialog-body" }, ...rows), actions);
    dialog.append(form);
    dialog.addEventListener("close", () => {
      if (dialog.returnValue !== "confirm") {
        dialog.remove();
        resolve(null);
        return;
      }
      const values = {};
      for (const [name, control] of Object.entries(controls)) values[name] = control.value;
      dialog.remove();
      resolve(values);
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      dialog.close("confirm");
    });
    document.body.append(dialog);
    dialog.showModal();
    fields[0] && controls[fields[0].name]?.focus();
  });
}

/* --------------------------------------------------------------- utilities */
function rgbToHex(value) {
  const [r, g, b] = Array.isArray(value) ? value : [255, 255, 255];
  return `#${[r, g, b].map((component) => Math.round(clamp(component, 0, 255)).toString(16).padStart(2, "0")).join("")}`;
}

function hexToRgb(value) {
  const text = String(value).replace("#", "");
  return [0, 2, 4].map((index) => parseInt(text.slice(index, index + 2), 16) || 0);
}

function bindScrub() {
  document.addEventListener("pointerdown", (event) => {
    const label = event.target.closest("[data-scrub-label]");
    if (!label) return;
    const input = document.getElementById(label.dataset.scrubLabel);
    if (!input) return;
    event.preventDefault();
    const startX = event.clientX;
    const start = Number(input.value) || 0;
    const step = Number(input.dataset.step) || 0.01;
    const decimals = Math.max(0, Math.min(4, Math.ceil(-Math.log10(step))));
    const move = (moveEvent) => {
      const next = start + (moveEvent.clientX - startX) * step;
      input.value = next.toFixed(decimals);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      input.dispatchEvent(new Event("change"));
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
  });
}

/* -------------------------------------------------------------------- mcp */
function mcpCell(label, value) {
  return el("div", {}, el("span", { class: "label", text: label }), String(value));
}

function renderMcpTelemetry(telemetry) {
  dom.mcpState.textContent = telemetry.transport ? telemetry.transport.toUpperCase() : "MCP";
  dom.mcpTelemetry.replaceChildren(
    mcpCell("CALLS", telemetry.calls || 0),
    mcpCell("ERRORS", telemetry.errors || 0),
    mcpCell("P50", `${telemetry.p50_ms || 0}ms`),
    mcpCell("P95", `${telemetry.p95_ms || 0}ms`)
  );
}

function renderMcpLog() {
  const filter = dom.mcpFilter.value.trim().toLowerCase();
  const rows = mcpLog
    .filter((entry) => {
      if (!filter) return true;
      return `${entry.tool || ""} ${entry.method || ""}`.toLowerCase().includes(filter);
    })
    .slice(-80)
    .map((entry) =>
      el(
        "div",
        { class: `entry ${entry.ok ? "ok" : "fail"}` },
        el("span", { class: "time", text: entry.time }),
        ` ${entry.tool || entry.method} `,
        `${entry.ms}ms`,
        entry.error ? ` ${entry.error}` : ""
      )
    );
  if (!rows.length) {
    rows.push(el("div", { class: "muted", text: "No MCP calls yet." }));
  }
  dom.mcpLog.replaceChildren(...rows);
}

async function refreshMcp() {
  try {
    const telemetry = await client.get("/api/mcp/telemetry");
    mcpLog = telemetry.log || [];
    renderMcpTelemetry(telemetry);
    renderMcpLog();
  } catch {
    dom.mcpState.textContent = "OFFLINE";
  }
}

function openMcpDrawer(open) {
  dom.mcpDrawer.classList.toggle("hidden", !open);
  window.clearInterval(mcpTimer);
  mcpTimer = 0;
  if (open) {
    refreshMcp();
    mcpTimer = window.setInterval(refreshMcp, 2000);
  }
}

/* ------------------------------------------------------------------ events */
function bindEvents() {
  dom.showSelect.addEventListener("change", async () => {
    try {
      await client.setTransport({ show_id: dom.showSelect.value });
      await client.refresh();
      view.start = 0;
      view.end = Math.max(1, Math.min(duration() || 1, 60));
      requestDraw();
    } catch (error) {
      toast(error.message, "danger");
    }
  });
  dom.play.addEventListener("click", () => {
    if (duration() <= 0) {
      toast(
        "No show loaded: check config/shows or restart without --no-persist",
        "info"
      );
      return;
    }
    if (!state?.playback?.playing) {
      syncAudioSource();
      if (!audioSourceUrl && !audioNoticeShown) {
        audioNoticeShown = true;
        toast(
          "No preview WAV attached; use Audio to load the show track",
          "info"
        );
      }
      playPreviewAudio();
    }
    client.toggle();
  });
  dom.audio?.addEventListener("loadedmetadata", () => {
    if (audioPendingSeek != null) seekPreviewAudio(audioPendingSeek);
    syncAudioPlayback();
  });
  dom.audio?.addEventListener("error", () => {
    if (!audioSourceUrl) return;
    audioFailedUrl = audioSourceUrl;
    audioSourceUrl = null;
    toast("Audio preview is unavailable for this show", "danger");
  });
  dom.audioVolume?.addEventListener("input", syncAudioPlayback);
  document.addEventListener(
    "pointerdown",
    () => {
      if (audioSourceUrl && state?.playback?.playing) playPreviewAudio();
    },
    { once: true }
  );
  dom.toStart.addEventListener("click", () => client.seek(0));
  dom.toEnd.addEventListener("click", () => client.seek(duration()));
  dom.loop.addEventListener("click", () => client.setTransport({ loop: !state.playback.loop }));
  dom.mcp.addEventListener("click", () => openMcpDrawer(dom.mcpDrawer.classList.contains("hidden")));
  dom.mcpClose.addEventListener("click", () => openMcpDrawer(false));
  dom.mcpFilter.addEventListener("input", renderMcpLog);
  dom.blueprintLoad.addEventListener("click", () => dom.blueprintFile.click());
  dom.blueprintFile.addEventListener("change", () => {
    const file = dom.blueprintFile.files?.[0];
    dom.blueprintFile.value = "";
    uploadBlueprint(file);
  });

  document.querySelectorAll(".section-head").forEach((button) => {
    button.addEventListener("click", () => button.parentElement.classList.toggle("open"));
  });
  document.querySelectorAll(".inspector-tab").forEach((button) => {
    button.addEventListener("click", () => {
      inspectorTab = button.dataset.tab;
      updateTabButtons();
      renderInspector();
    });
  });

  dom.rigSelect.addEventListener("change", () => activateRig(dom.rigSelect.value));
  $("#rig-new").addEventListener("click", newRig);
  $("#rig-save").addEventListener("click", saveRig);
  $("#rig-save-as").addEventListener("click", saveRigAs);
  $("#rig-clone").addEventListener("click", cloneRig);
  $("#rig-delete").addEventListener("click", deleteRig);
  $("#add-point").addEventListener("click", () => addFixture("point"));
  $("#add-line").addEventListener("click", () => addFixture("line"));
  $("#group-add").addEventListener("click", newGroup);
  $("#undo").addEventListener("click", undoShow);
  $("#redo").addEventListener("click", redoShow);
  $("#split").addEventListener("click", splitClip);
  $("#mute").addEventListener("click", toggleMute);
  $("#add-clip").addEventListener("click", addClip);
  dom.grid.addEventListener("change", requestDraw);
  // Optional: an older cached studio.html must not break timeline binding.
  dom.sync?.addEventListener("click", async () => {
    if (!state?.sync?.configured) {
      toast("Sendspin/MA sync is not configured", "info");
      return;
    }
    try {
      state.sync = await client.post("/api/sync", { enabled: !state.sync.enabled });
      updateSync();
    } catch (error) {
      toast(error.message, "danger");
    }
  });
  $("#zoom-out").addEventListener("click", () => zoomTimeline(1.4, (view.start + view.end) / 2));
  $("#zoom-in").addEventListener("click", () => zoomTimeline(0.7, (view.start + view.end) / 2));
  $("#zoom-fit").addEventListener("click", () => {
    view.start = 0;
    view.end = Math.max(1, duration());
    requestDraw();
  });

  document.querySelectorAll("[data-camera]").forEach((button) => {
    button.addEventListener("click", () => {
      const room = roomDoc();
      camera.target = room.min.map((value, index) => (value + room.max[index]) / 2);
      if (button.dataset.camera === "front") {
        camera.yaw = 0;
        camera.pitch = 0;
      } else if (button.dataset.camera === "top") {
        camera.yaw = 0;
        camera.pitch = 1.45;
      } else if (button.dataset.camera === "side") {
        camera.yaw = Math.PI / 2;
        camera.pitch = 0;
      } else {
        camera.yaw = -0.78;
        camera.pitch = 0.52;
      }
      fitCamera(false);
      if (button.dataset.camera !== "persp") {
        camera.yaw = button.dataset.camera === "side" ? Math.PI / 2 : 0;
        camera.pitch = button.dataset.camera === "top" ? 1.45 : 0;
      }
      requestDraw();
    });
  });
  $("#camera-fit").addEventListener("click", () => fitCamera());

  window.addEventListener("resize", resizeAll);
  bindScrub();
  bindViewport();
  bindTimeline();
  bindKeyboard();
}

function bindViewport() {
  const canvas = dom.viewport;

  canvas.addEventListener("pointerdown", (event) => {
    canvas.focus();
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (event.button === 1) {
      viewportDrag = {
        mode: "pan",
        x0: x,
        y0: y,
        startTarget: [...camera.target],
        distance: camera.distance,
      };
      canvas.setPointerCapture(event.pointerId);
      return;
    }
    if (event.altKey) {
      viewportDrag = { mode: "orbit", x0: x, y0: y, yaw: camera.yaw, pitch: camera.pitch };
      canvas.setPointerCapture(event.pointerId);
      return;
    }
    if (event.button !== 0) return;
    const hit = fixtureAt(x, y);
    if (hit) {
      if (!selectedFixtures.has(hit)) selectFixture(hit, event.shiftKey);
      else if (event.shiftKey) selectFixture(hit, true);
      viewportDrag = {
        mode: "fixture",
        x0: x,
        y0: y,
        lastX: x,
        lastY: y,
        offsets: new Map([...selectedFixtures].map((id) => [id, [0, 0, 0]])),
        origins: new Map([...selectedFixtures].map((id) => [id, fixtureCenter(id)])),
      };
      canvas.setPointerCapture(event.pointerId);
      return;
    }
    if (event.shiftKey) {
      viewportDrag = {
        mode: "pan",
        x0: x,
        y0: y,
        startTarget: [...camera.target],
        distance: camera.distance,
      };
      canvas.setPointerCapture(event.pointerId);
      return;
    }
    selectedFixtures.clear();
    renderFixtures();
    renderInspector();
    requestDraw();
    viewportDrag = { mode: "marquee", x0: x, y0: y, x1: x, y1: y };
    canvas.setPointerCapture(event.pointerId);
  });

  canvas.addEventListener("pointermove", (event) => {
    if (!viewportDrag) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const drag = viewportDrag;
    if (drag.mode === "orbit") {
      camera.yaw = drag.yaw + (x - drag.x0) * 0.007;
      camera.pitch = clamp(drag.pitch - (y - drag.y0) * 0.007, -1.45, 1.45);
      requestDraw();
    } else if (drag.mode === "pan") {
      const basis = cameraBasis();
      const focal = (viewportSize.h * 0.5) / Math.tan(camera.fov * 0.5);
      const scaleFactor = drag.distance / focal;
      const delta = add(
        scale(basis.right, -(x - drag.x0) * scaleFactor),
        scale(basis.up, (y - drag.y0) * scaleFactor)
      );
      camera.target = add(drag.startTarget, delta);
      requestDraw();
    } else if (drag.mode === "marquee") {
      drag.x1 = x;
      drag.y1 = y;
      requestDraw();
    } else if (drag.mode === "fixture") {
      const dx = x - drag.x0;
      const dy = y - drag.y0;
      const basis = cameraBasis();
      for (const [id, origin] of drag.origins) {
        const projected = project(origin);
        const focal = projected?.focal || (viewportSize.h * 0.5) / Math.tan(camera.fov * 0.5);
        const depth = projected?.depth || camera.distance;
        let offset;
        if (event.shiftKey) {
          offset = [0, 0, (-dy * depth) / focal];
        } else {
          offset = add(
            scale(basis.right, (dx * depth) / focal),
            scale(basis.up, (-dy * depth) / focal)
          );
        }
        drag.offsets.set(id, offset);
        overrides.set(id, offset);
      }
      requestDraw();
    }
  });

  canvas.addEventListener("pointerup", async (event) => {
    if (!viewportDrag) return;
    const drag = viewportDrag;
    viewportDrag = null;
    if (drag.mode === "marquee") {
      const x0 = Math.min(drag.x0, drag.x1);
      const x1 = Math.max(drag.x0, drag.x1);
      const y0 = Math.min(drag.y0, drag.y1);
      const y1 = Math.max(drag.y0, drag.y1);
      if (!event.shiftKey) selectedFixtures.clear();
      for (const fixture of state.rig.document.fixtures) {
        const projected = project(fixtureCenter(fixture.id));
        if (!projected) continue;
        if (projected.x >= x0 && projected.x <= x1 && projected.y >= y0 && projected.y <= y1) {
          selectedFixtures.add(fixture.id);
        }
      }
      inspectorTab = "fixture";
      updateTabButtons();
      renderFixtures();
      renderInspector();
      requestDraw();
      return;
    }
    if (drag.mode === "fixture") {
      const commits = [];
      for (const [id, offset] of drag.offsets) {
        if (Math.hypot(...offset) < 0.002) continue;
        const fixture = findFixture(id);
        if (!fixture) continue;
        commits.push(
          client.patch(`/api/rig/fixtures/${id}`, {
            transform: {
              translate: add(fixture.transform?.translate || [0, 0, 0], offset).map((value) => Number(value.toFixed(4))),
            },
          })
        );
      }
      overrides = new Map();
      if (commits.length) {
        try {
          await Promise.all(commits);
          await client.refresh();
        } catch (error) {
          toast(error.message, "danger");
          client.refresh().catch(() => {});
        }
      } else {
        requestDraw();
      }
    }
  });

  canvas.addEventListener("pointercancel", () => {
    viewportDrag = null;
    overrides = new Map();
    requestDraw();
  });

  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    camera.distance = clamp(camera.distance * (1 + Math.sign(event.deltaY) * 0.1), 1, 80);
    requestDraw();
  }, { passive: false });
}

function bindTimeline() {
  const canvas = dom.timeline;

  canvas.addEventListener("pointerdown", (event) => {
    canvas.focus();
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (y <= view.rulerH) {
      const time = clamp(xToTime(x), 0, duration());
      client.seek(time);
      timelineDrag = { mode: "scrub" };
      canvas.setPointerCapture(event.pointerId);
      return;
    }
    const hit = clipAtPoint(x, y);
    if (hit) {
      selectedClipId = hit.clip.id;
      selectedTrackId = hit.track.id;
      inspectorTab = "clip";
      updateTabButtons();
      renderInspector();
      requestDraw();
      const edge = 6;
      if (Math.abs(x - hit.rect.x0) <= edge) {
        timelineDrag = { mode: "trim-start", clip: deepClone(hit.clip), trackId: hit.track.id, x0: x };
      } else if (Math.abs(x - hit.rect.x1) <= edge) {
        timelineDrag = { mode: "trim-end", clip: deepClone(hit.clip), trackId: hit.track.id, x0: x };
      } else {
        timelineDrag = { mode: "move", clip: deepClone(hit.clip), trackId: hit.track.id, x0: x };
      }
      canvas.setPointerCapture(event.pointerId);
      return;
    }
    const trackIndex = trackIndexAt(y);
    if (trackIndex >= 0 && trackIndex < (showDoc()?.tracks.length || 0)) {
      selectedTrackId = showDoc().tracks[trackIndex].id;
      renderInspector();
      requestDraw();
    }
  });

  canvas.addEventListener("pointermove", (event) => {
    if (!timelineDrag) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    if (timelineDrag.mode === "scrub") {
      client.seek(clamp(xToTime(x), 0, duration()));
      return;
    }
    const time = xToTime(x);
    const snap = (value) => (event.altKey ? value : snapTime(value));
    const clip = timelineDrag.clip;
    if (timelineDrag.mode === "move") {
      const delta = snap(clip.start_s + (x - timelineDrag.x0) / Math.max(1, timelineSize.w - view.labelW) * (view.end - view.start)) - clip.start_s;
      const length = clip.end_s - clip.start_s;
      const start = clamp(clip.start_s + delta, 0, Math.max(0, duration() - length));
      timelineDrag.preview = { clipId: clip.id, start_s: start, end_s: start + length };
    } else if (timelineDrag.mode === "trim-start") {
      const start = clamp(snap(time), 0, clip.end_s - 0.1);
      timelineDrag.preview = { clipId: clip.id, start_s: start, end_s: clip.end_s };
    } else if (timelineDrag.mode === "trim-end") {
      const end = clamp(snap(time), clip.start_s + 0.1, duration());
      timelineDrag.preview = { clipId: clip.id, start_s: clip.start_s, end_s: end };
    }
    requestDraw();
  });

  canvas.addEventListener("pointerup", async () => {
    if (!timelineDrag) return;
    const drag = timelineDrag;
    timelineDrag = null;
    if (drag.mode === "scrub") {
      requestDraw();
      return;
    }
    const preview = drag.preview;
    if (!preview) {
      requestDraw();
      return;
    }
    await commitShow(drag.mode === "move" ? "move clip" : "trim clip", (doc) => {
      const track = doc.tracks.find((item) => item.id === drag.trackId);
      const clip = track?.clips.find((item) => item.id === preview.clipId);
      if (!clip) return;
      if (drag.mode === "move") {
        const length = clip.end_s - clip.start_s;
        clip.start_s = preview.start_s;
        clip.end_s = preview.start_s + length;
      } else if (drag.mode === "trim-start") {
        clip.start_s = preview.start_s;
      } else {
        clip.end_s = preview.end_s;
      }
      clip.fade_in_s = Math.min(clip.fade_in_s, clip.end_s - clip.start_s);
      clip.fade_out_s = Math.min(clip.fade_out_s, clip.end_s - clip.start_s);
      clip.keyframes = (clip.keyframes || []).filter((keyframe) => keyframe.t_s <= clip.end_s - clip.start_s);
    });
    requestDraw();
  });

  canvas.addEventListener("pointercancel", () => {
    timelineDrag = null;
    requestDraw();
  });

  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    if (event.ctrlKey || event.metaKey) {
      zoomTimeline(event.deltaY > 0 ? 1.15 : 0.87, xToTime(x));
    } else {
      const span = view.end - view.start;
      const shift = (event.deltaY || event.deltaX) > 0 ? span * 0.08 : -span * 0.08;
      const maxStart = Math.max(0, duration() - span);
      view.start = clamp(view.start + shift, 0, maxStart);
      view.end = view.start + span;
      requestDraw();
    }
  }, { passive: false });

  canvas.addEventListener("keydown", (event) => {
    const doc = showDoc();
    if (!doc) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const direction = event.key === "ArrowLeft" ? -1 : 1;
      const step = gridSeconds() * (event.shiftKey ? 4 : 1);
      client.seek(clamp((client.frame?.time || 0) + direction * step, 0, duration()));
      return;
    }
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault();
      if (!doc.tracks.length) return;
      const current = doc.tracks.findIndex((track) => track.id === selectedTrackId);
      const direction = event.key === "ArrowUp" ? -1 : 1;
      const index = clamp(
        (current < 0 ? 0 : current) + direction,
        0,
        doc.tracks.length - 1
      );
      selectedTrackId = doc.tracks[index].id;
      renderInspector();
      requestDraw();
      return;
    }
    if (event.key === "Enter" && selectedTrackId) {
      event.preventDefault();
      const track = findTrack();
      const time = client.frame?.time ?? state.playback.time_s ?? 0;
      const clip = track?.clips.find((item) => time >= item.start_s && time < item.end_s);
      if (clip) selectClip(clip.id);
    }
  });
}

function zoomTimeline(factor, anchorTime) {
  const current = view.end - view.start;
  const span = clamp(current * factor, 0.25, Math.max(0.25, duration()));
  const ratio = (anchorTime - view.start) / Math.max(0.001, current);
  const start = clamp(anchorTime - ratio * span, 0, Math.max(0, duration() - span));
  view.start = start;
  view.end = start + span;
  requestDraw();
}

function bindKeyboard() {
  window.addEventListener("keydown", (event) => {
    const target = event.target;
    if (target instanceof HTMLElement && (target.matches("input, textarea, select") || target.isContentEditable)) return;
    const meta = event.metaKey || event.ctrlKey;
    if (meta && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveRig();
      return;
    }
    if (meta && event.key.toLowerCase() === "z") {
      event.preventDefault();
      if (event.shiftKey) redoShow();
      else undoShow();
      return;
    }
    if (event.code === "Space") {
      event.preventDefault();
      client.toggle();
      return;
    }
    if (event.key.toLowerCase() === "s" && !meta) {
      event.preventDefault();
      splitClip();
      return;
    }
    if (event.key.toLowerCase() === "m" && !meta) {
      event.preventDefault();
      toggleMute();
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      client.seek(0);
      return;
    }
    if (event.key === "End") {
      event.preventDefault();
      client.seek(duration());
      return;
    }
    if ((event.key === "Delete" || event.key === "Backspace") && selectedClipId) {
      event.preventDefault();
      deleteClip(selectedClipId);
    }
  });
}

/* -------------------------------------------------------------------- boot */
async function boot() {
  bindEvents();
  client.addEventListener("state", onState);
  client.addEventListener("frame", () => {
    updateTransport();
    syncAudioPlayback();
    requestDraw();
  });
  client.addEventListener("transport", () => {
    updateTransport();
    syncAudioPlayback();
    requestDraw();
  });
  client.addEventListener("connection", () => {
    updateTransport();
    if (!client.connected) dom.audio?.pause();
  });
  client.addEventListener("invalidate", (event) => {
    if (event.detail?.scope === "show") refreshHistory();
  });
  client.addEventListener("mcp", (event) => {
    mcpLog.push(event.detail);
    if (mcpLog.length > 100) mcpLog.shift();
    if (!dom.mcpDrawer.classList.contains("hidden")) renderMcpLog();
  });
  updateTabButtons();
  try {
    await client.start();
    await refreshHistory();
  } catch (error) {
    toast(error.message, "danger");
  }
}

boot();
