import {
  StudioClient,
  clamp,
  deepClone,
  el,
  formatClock,
  formatTime,
  toast,
} from "./client.js";
import { MDI } from "./icons.js";
import { nearestSnap } from "./snap.js";
import { buildAutoShowTrack } from "./autoshow.js";

const client = new StudioClient();
const $ = (selector) => document.querySelector(selector);

// One icon language for every control.  Paths come from Material Design
// Icons (Pictogrammers, Apache-2.0) and are vendored in icons.js.
const ICONS = {
  play: MDI.play,
  pause: MDI.pause,
  start: MDI["skip-previous"],
  end: MDI["skip-next"],
  loop: MDI.repeat,
  undo: MDI.undo,
  redo: MDI.redo,
  split: MDI["content-cut"],
  mute: MDI["volume-off"],
  plus: MDI.plus,
  minus: MDI.minus,
  close: MDI.close,
  folder: MDI["folder-outline"],
  folderOpen: MDI["folder-open-outline"],
  chevron: MDI["chevron-right"],
  chevronLeft: MDI["chevron-left"],
  chevronRight: MDI["chevron-right"],
  chevronDown: MDI["chevron-down"],
  keyframe: MDI["rhombus-outline"],
  function: MDI["function-variant"],
  lock: MDI["lock-outline"],
  power: MDI.power,
  zoomIn: MDI["magnify-plus-outline"],
  zoomOut: MDI["magnify-minus-outline"],
  deck: MDI.monitor,
  solo: MDI.target,
  copy: MDI["content-copy"],
  cut: MDI["content-cut"],
  paste: MDI["content-paste"],
  delete: MDI["delete-outline"],
  duplicate: MDI["content-duplicate"],
  blend: MDI["layers-triple-outline"],
  layers: MDI["layers-plus"],
  palette: MDI["palette-outline"],
  auto: MDI["auto-fix"],
  fx: MDI.creation,
  settings: MDI["tune-variant"],
  rename: MDI["rename-outline"],
  eye: MDI["eye-outline"],
  eyeOff: MDI["eye-off-outline"],
  check: MDI.check,
  warning: MDI["alert-outline"],
  info: MDI["information-outline"],
  refresh: MDI.refresh,
  save: MDI["content-save-outline"],
  drag: MDI.drag,
  record: MDI.record,
  stop: MDI.stop,
  link: MDI["link-variant"],
  filter: MDI["filter-variant"],
  music: MDI["music-note"],
  camera: MDI.camera,
  grid: MDI.grid,
  waveform: MDI.waveform,
  color: MDI["format-color-fill"],
  sparkles: MDI.creation,
  star: MDI.star,
  starOutline: MDI["star-outline"],
};

function icon(name, className = "") {
  const span = el("span", {
    class: `icon${className ? ` ${className}` : ""}`,
    "aria-hidden": "true",
  });
  const path = ICONS[name] || MDI["help-circle-outline"] || "";
  span.innerHTML = `<svg viewBox="0 0 24 24"><path d="${path}"/></svg>`;
  return span;
}

function setIcon(button, name, label) {
  if (!button) return;
  if (button.dataset.icon !== name) {
    button.replaceChildren(icon(name, name === "play" || name === "pause" ? "svg-fill" : ""));
    button.dataset.icon = name;
  }
  if (label) button.setAttribute("aria-label", label);
}

const dom = {
  root: $("#studio-root"),
  showSelect: $("#show-select"),
  timecode: $("#timecode"),
  play: $("#btn-play"),
  toStart: $("#btn-to-start"),
  toEnd: $("#btn-to-end"),
  loop: $("#btn-loop"),
  sync: $("#ma-lock"),
  maPair: $("#ma-pair"),
  maRestart: $("#ma-restart"),
  proxyBadge: $("#proxy-badge"),
  hw: $("#hw-toggle"),
  link: $("#link-status"),
  undo: $("#undo"),
  redo: $("#redo"),
  undoCount: $("#undo-count"),
  redoCount: $("#redo-count"),
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
  librarySearch: $("#library-search"),
  librarySort: $("#library-sort"),
  libraryCount: $("#library-count"),
  splitLeft: $("#split-left"),
  splitRight: $("#split-right"),
  splitTimeline: $("#split-timeline"),
  syncMarkers: $("#sync-markers"),
  presetList: $("#preset-list"),
  filterList: $("#filter-list"),
  rigSelect: $("#rig-select"),
  rigDirty: $("#rig-dirty"),
  hardwareStatus: $("#hardware-status"),
  fixtureList: $("#fixture-list"),
  fixtureCount: $("#fixture-count"),
  groupList: $("#group-list"),
  groupEditor: $("#group-editor"),
  viewport: $("#viewport"),
  fxPreview: $("#fx-preview"),
  fxToggle: $("#fx-toggle"),
  viewportStatus: $("#viewport-status"),
  transformHud: $("#transform-hud"),
  inspectorBody: $("#inspector-body"),
  paletteList: $("#palette-list"),
  paletteNew: $("#palette-new"),
  timeline: $("#timeline"),
  timelineSection: $(".timeline"),
  timelineLayers: $("#timeline-layers"),
  timelineAnalysis: $("#timeline-analysis"),
  analysisLegend: $("#analysis-legend"),
  history: $("#history-label"),
  grid: $("#grid-select"),
  autoKeys: $("#auto-keys"),
  effectBuilder: $("#effect-builder"),
  addGlobal: $("#add-track-global"),
  addSub: $("#add-track-sub"),
};

const TRACK_COLORS = ["#d06b4a", "#5e9aa8", "#9b7fb5", "#8fa65a", "#b3925a"];
const ANALYSIS_CURVES = [
  { key: "pulse", label: "Pulse", color: "#e8862e" },
  { key: "kick", label: "Kick", color: "#d06b4a" },
  { key: "snare", label: "Snare", color: "#c45a52" },
  { key: "hat", label: "Hat", color: "#c9a34a" },
  { key: "energy", label: "Energy", color: "#8fa65a" },
  { key: "harmonic", label: "Harmonic", color: "#5e9aa8" },
  { key: "percussive", label: "Percussive", color: "#9b7fb5" },
  { key: "prominence", label: "Prominence", color: "#b3925a" },
  { key: "bass", label: "Bass", color: "#4f8fc0" },
  { key: "mid", label: "Mid", color: "#58a66b" },
  { key: "high", label: "High", color: "#e0b36a" },
  { key: "sectionProgress", label: "Section", color: "#7c8089" },
  { key: "beatsToDrop", label: "To Drop", color: "#c45a52" },
  { key: "inDrop", label: "In Drop", color: "#d06b4a" },
];
const ANALYSIS_STORAGE_KEY = "hueme.studio.analysisCurves.v1";
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
  ortho: false,
  orthoSize: 12,
};
let viewCubeHits = [];
let viewCubeHover = null;
let lastFlatView = { axis: 1, opposite: false };
const view = { start: 0, end: 0, labelW: 140, rulerH: 52, trackH: 28, audioH: 74, scrollY: 0 };
const RULER_TIME_H = 28;
const SECTION_COLORS = {
  intro: "#5e9aa8",
  verse: "#8fa65a",
  chorus: "#b3925a",
  build: "#c9a34a",
  drop: "#d06b4a",
  bridge: "#9b7fb5",
  break: "#6f7480",
  breakdown: "#6f7480",
  outro: "#5e9aa8",
  pre_drop: "#e8862e",
};
let viewportSize = { w: 1, h: 1 };
let timelineSize = { w: 1, h: 1 };
let timelineRows = [];
let lastShowId = null;
let viewportDrag = null;
let transformOp = null;
let lastPointerClient = null;
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
let timecodeMode = 0;
let workspace = "compose";
let proxyHealth = null;
let audioAnalysis = null;
let audioAnalysisKey = null;
let audioAnalysisLoading = false;
let audioLaneDrag = null;
let heightDrag = null;
let heightCommitTimer = 0;
const collapsedFolders = new Set();
const openLanes = new Set();
const laneFocus = new Map();
const openExpressions = new Set();
let timelineMode = "layers";
let analysisHover = null;
let analysisCurves = new Set(ANALYSIS_CURVES.map((curve) => curve.key));
try {
  const stored = JSON.parse(localStorage.getItem(ANALYSIS_STORAGE_KEY) || "null");
  if (Array.isArray(stored)) {
    analysisCurves = new Set(stored.filter((key) => ANALYSIS_CURVES.some((curve) => curve.key === key)));
  }
} catch {
  // A broken localStorage entry must not stop the Studio.
}
let lastHistory = { undo_depth: 0, redo_depth: 0 };
let libraryQuery = "";
const LIBRARY_FAVORITES_KEY = "hueme.studio.libraryFavorites.v1";
const LIBRARY_RECENTS_KEY = "hueme.studio.libraryRecents.v1";
const LIBRARY_SORT_KEY = "hueme.studio.librarySort.v1";
const libraryFavorites = new Set(
  (() => {
    try {
      const stored = JSON.parse(localStorage.getItem(LIBRARY_FAVORITES_KEY) || "[]");
      return Array.isArray(stored) ? stored.filter((item) => typeof item === "string") : [];
    } catch {
      return [];
    }
  })()
);
let libraryRecents = (() => {
  try {
    const stored = JSON.parse(localStorage.getItem(LIBRARY_RECENTS_KEY) || "[]");
    return Array.isArray(stored) ? stored.filter((item) => typeof item === "string").slice(0, 16) : [];
  } catch {
    return [];
  }
})();
let librarySort = (() => {
  try {
    return localStorage.getItem(LIBRARY_SORT_KEY) === "alpha" ? "alpha" : "category";
  } catch {
    return "category";
  }
})();
const collapsedCategories = new Set();
const openTracks = new Set();
let contextMenu = null;
let scrollDrag = null;
let fxMode = false;
let fxImage = null;
let fxImageKey = "";
let fxLoading = false;
let fxQueued = false;
let fxError = "";
let clipboard = null;
const LAYOUT_KEY = "hueme.studio.layout.v1";
const TRACK_EXPANDED_KEY = "hueme.studio.openTracks.v1";

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

function curveAt(values, index) {
  if (!values?.length) return 0;
  return clamp(Number(values[Math.min(index, values.length - 1)]) || 0, 0, 1);
}

// Mirrors BlueprintEnvelope/deterministic_envelope so timeline expression
// lanes show the same audio values the Python compositor will use.
function envelopeAt(t) {
  const time = Math.max(0, Number(t) || 0);
  const doc = showDoc();
  const timing = doc?.timing || {};
  const beatGrid = Number(timing.beat_grid_s || 60 / Number(timing.bpm || 120));
  const beatOffset = Number(timing.beat_offset_s || 0);
  const beatPos = (time - beatOffset) / Math.max(1e-6, beatGrid);
  const beatPhase = beatPos - Math.floor(beatPos);
  const barPos = ((Math.floor(beatPos) % 4) + 4) % 4;
  const bpm = Number(timing.bpm || 120);
  if (blueprint?.beats?.length) {
    const beats = blueprint.beats.map(Number);
    let index = 0;
    for (let i = 0; i < beats.length; i += 1) {
      if (beats[i] <= time) index = i;
      else break;
    }
    const start = beats[index];
    const period = beats[index + 1] > start
      ? beats[index + 1] - start
      : 60 / Math.max(1, bpm);
    const phase = ((time - start) / period) % 1;
    const curves = blueprint.curves || {};
    const perc = curveAt(curves.percussive, index);
    const harm = curveAt(curves.harmonic, index);
    const prom = curveAt(curves.prominence, index);
    const pulse = Math.exp(-phase * 3) * (0.45 + 0.55 * prom);
    const kick = perc * Math.exp(-phase * 6);
    const snare = index % 4 === 1 || index % 4 === 3
      ? (perc * 0.9 + 0.05) * Math.exp(-phase * 10)
      : 0.02;
    const eighth = (phase * 2) % 1;
    const hat = (0.25 + 0.75 * perc) * Math.exp(-eighth * 10);
    const energy = curveAt(curves.energy, index) || clamp(0.3 + 0.4 * harm + 0.3 * perc, 0, 1);
    const bass = curveAt(curves.bass, index) || clamp(0.15 + 0.85 * kick, 0, 1);
    const section = (blueprint.sections || []).find((item) => Number(item.start) <= time && time < Number(item.end));
    const sectionProgress = section && Number(section.end) > Number(section.start)
      ? clamp((time - Number(section.start)) / (Number(section.end) - Number(section.start)), 0, 1)
      : 0;
    const nextDrop = (blueprint.drops || []).map(Number).find((drop) => drop >= time);
    const beatsToDrop = nextDrop === undefined
      ? 1
      : clamp((nextDrop - time) / (Math.max(0.001, 60 / Math.max(1, bpm)) * 16), 0, 1);
    const inDrop = (blueprint.drop_windows || []).some(
      (window) => Number(window.start) <= time && time < Number(window.end)
    ) ? 1 : 0;
    return {
      pulse: clamp(pulse, 0, 1), kick: clamp(kick, 0, 1), snare: clamp(snare, 0, 1),
      hat: clamp(hat, 0, 1), energy, harmonic: harm, percussive: perc,
      prominence: prom, bass, mid: clamp(0.2 + 0.8 * snare, 0, 1), high: clamp(hat, 0, 1),
      sectionProgress, beatsToDrop, inDrop,
      beatPos, beatPhase, bar: Math.floor(beatPos / 4), barPos,
      barPhase: (barPos + beatPhase) / 4, phrase: Math.floor(beatPos / 16),
      phrasePhase: ((Math.floor(beatPos) % 16) + beatPhase) / 16, bpm,
    };
  }
  const eighth = (beatPos * 2) % 1;
  const pulse = Math.exp(-beatPhase * 3);
  const kick = Math.exp(-beatPhase * 8);
  const snare = barPos === 1 || barPos === 3 ? Math.exp(-beatPhase * 11) : 0.02;
  const hat = 0.5 * Math.exp(-eighth * 12);
  const duration = Math.max(1, Number(doc?.duration_s || 60));
  const energy = clamp(0.48 + 0.16 * Math.sin((2 * Math.PI * time) / (duration * 2)) + 0.1 * pulse, 0, 1);
  const harmonic = 0.5 + 0.5 * Math.sin((2 * Math.PI * beatPos) / 16);
  const phraseBeat = ((beatPos % 32) + 32) % 32;
  return {
    pulse: clamp(pulse, 0, 1), kick: clamp(kick, 0, 1), snare: clamp(snare, 0, 1),
    hat: clamp(hat, 0, 1), energy, harmonic, percussive: clamp(0.25 + 0.75 * kick, 0, 1),
    prominence: clamp(0.5 * pulse + 0.5 * energy, 0, 1),
    bass: clamp(0.2 + 0.8 * kick, 0, 1), mid: clamp(0.3 + 0.3 * snare, 0, 1),
    high: clamp(hat, 0, 1), sectionProgress: phraseBeat / 32,
    beatsToDrop: clamp((32 - phraseBeat) / 32, 0, 1), inDrop: phraseBeat >= 24 ? 1 : 0,
    beatPos, beatPhase, bar: Math.floor(beatPos / 4), barPos,
    barPhase: (barPos + beatPhase) / 4, phrase: Math.floor(beatPos / 16),
    phrasePhase: ((Math.floor(beatPos) % 16) + beatPhase) / 16, bpm,
  };
}

const SNAP_PIXELS = 8;

// Magnetic snap: grid first, then markers, clip edges and the playhead within
// SNAP_PIXELS of the pointer.  Callers pass the dragged clip id so a clip does
// not stick to its own edges.  Alt bypasses snapping at the call site.
function snapTime(value, excludeClipId = "") {
  const doc = showDoc();
  const offset = Number(doc?.timing?.beat_offset_s || 0);
  const step = gridSeconds();
  const grid = offset + Math.round((value - offset) / step) * step;
  const candidates = [grid, 0, duration(), currentTime()];
  for (const marker of doc?.timing?.markers || []) candidates.push(Number(marker.t_s));
  for (const track of doc?.tracks || []) {
    for (const clip of track.clips || []) {
      if (clip.id === excludeClipId) continue;
      candidates.push(Number(clip.start_s), Number(clip.end_s));
    }
  }
  const width = timelineSize.w - view.labelW;
  const threshold = width > 0 ? (SNAP_PIXELS * (view.end - view.start)) / width : 0;
  return nearestSnap(value, {
    grid,
    candidates,
    threshold,
    min: 0,
    max: duration(),
  });
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

function presetName(id) {
  return presetById(id)?.name || id;
}

function clipDisplayName(clip) {
  return clip?.name || presetName(clip?.preset);
}

function trackNameAuto(track) {
  return Boolean(track?.name_auto);
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
    if (fxMode) refreshFxPreview();
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
  if (fxMode && dom.fxPreview) sizeCanvas(dom.fxPreview);
  requestDraw();
}

/* ------------------------------------------------------------ resizable panes */
function layoutVar(name, value) {
  const px = `${Math.round(value)}px`;
  dom.root.style.setProperty(name, px);
  document.documentElement.style.setProperty(name, px);
}

function loadLayout() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "null");
  } catch {
    saved = null;
  }
  const maxSide = Math.max(240, window.innerWidth - 500);
  if (saved && Number.isFinite(saved.left)) layoutVar("--left-w", clamp(saved.left, 220, maxSide));
  if (saved && Number.isFinite(saved.right)) layoutVar("--right-w", clamp(saved.right, 240, maxSide));
  if (saved && Number.isFinite(saved.timeline)) {
    layoutVar("--timeline-h", clamp(saved.timeline, 180, Math.max(200, window.innerHeight - 200)));
  }
  try {
    const tracks = JSON.parse(localStorage.getItem(TRACK_EXPANDED_KEY) || "[]");
    if (Array.isArray(tracks)) tracks.forEach((id) => openTracks.add(id));
  } catch {
    /* ignore malformed layout cache */
  }
}

function saveLayout() {
  const style = getComputedStyle(dom.root);
  const left = parseFloat(style.getPropertyValue("--left-w")) || 300;
  const right = parseFloat(style.getPropertyValue("--right-w")) || 310;
  const timeline = parseFloat(style.getPropertyValue("--timeline-h")) || 360;
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ left, right, timeline }));
    localStorage.setItem(TRACK_EXPANDED_KEY, JSON.stringify([...openTracks]));
  } catch {
    /* private mode: layout is simply not persisted */
  }
}

function bindSplitters() {
  const bind = (element, orientation) => {
    if (!element) return;
    element.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      const style = getComputedStyle(dom.root);
      const startLeft = parseFloat(style.getPropertyValue("--left-w")) || 300;
      const startRight = parseFloat(style.getPropertyValue("--right-w")) || 310;
      const startTimeline = parseFloat(style.getPropertyValue("--timeline-h")) || 360;
      const startX = event.clientX;
      const startY = event.clientY;
      element.setPointerCapture(event.pointerId);
      element.classList.add("dragging");
      document.body.classList.add("resizing");
      const move = (moveEvent) => {
        const maxSide = Math.max(240, window.innerWidth - 500);
        if (orientation === "left") {
          layoutVar("--left-w", clamp(startLeft + moveEvent.clientX - startX, 220, maxSide));
        } else if (orientation === "right") {
          layoutVar("--right-w", clamp(startRight - (moveEvent.clientX - startX), 240, maxSide));
        } else {
          layoutVar("--timeline-h", clamp(startTimeline - (moveEvent.clientY - startY), 180, Math.max(200, window.innerHeight - 200)));
        }
      };
      const up = () => {
        element.classList.remove("dragging");
        document.body.classList.remove("resizing");
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        saveLayout();
        resizeAll();
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
    element.addEventListener("dblclick", () => {
      const defaults = { left: "--left-w", right: "--right-w", timeline: "--timeline-h" };
      const values = { left: 300, right: 310, timeline: 360 };
      layoutVar(defaults[orientation], values[orientation]);
      saveLayout();
      resizeAll();
    });
  };
  bind(dom.splitLeft, "left");
  bind(dom.splitRight, "right");
  bind(dom.splitTimeline, "timeline");
}

function updateBlueprintStatus() {
  const meta = state?.show?.blueprint;
  const audio = Boolean(meta?.audio_available);
  dom.blueprintStatus.textContent = meta
    ? `${meta.bpm} BPM / ${meta.section_count} sections / ${audio ? "AUDIO" : "ANALYSIS"}`
    : "NO AUDIO";
  dom.blueprintStatus.title = audio
    ? "Attached audio plays with the timeline"
    : "Upload audio (WAV, MP3, FLAC, M4A, MP4, OGG)";
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
    audioAnalysis = null;
    audioAnalysisKey = null;
    return;
  }
  if (url === audioSourceUrl || url === audioFailedUrl) return;
  audioSourceUrl = url;
  audioPendingSeek = previewTime();
  dom.audio.src = url;
  dom.audio.load();
}

async function syncAudioAnalysis() {
  const url = audioUrl();
  if (!url) {
    audioAnalysis = null;
    audioAnalysisKey = null;
    requestDraw();
    return;
  }
  if (url === audioAnalysisKey || audioAnalysisLoading) return;
  audioAnalysisKey = url;
  audioAnalysisLoading = true;
  requestDraw();
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`audio ${response.status}`);
    const bytes = await response.arrayBuffer();
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) throw new Error("Web Audio unavailable");
    const context = new AudioContextClass();
    const decoded = await context.decodeAudioData(bytes.slice(0));
    const channel = decoded.getChannelData(0);
    const buckets = Math.min(4096, Math.max(512, Math.floor(channel.length / 256)));
    const mins = new Float32Array(buckets);
    const maxs = new Float32Array(buckets);
    const rms = new Float32Array(buckets);
    const step = channel.length / buckets;
    let globalPeak = 1e-6;
    for (let bucket = 0; bucket < buckets; bucket += 1) {
      const start = Math.floor(bucket * step);
      const end = Math.min(channel.length, Math.floor((bucket + 1) * step));
      let lo = 0;
      let hi = 0;
      let sum = 0;
      for (let index = start; index < end; index += 1) {
        const value = channel[index];
        if (value < lo) lo = value;
        if (value > hi) hi = value;
        sum += value * value;
      }
      mins[bucket] = lo;
      maxs[bucket] = hi;
      rms[bucket] = Math.sqrt(sum / Math.max(1, end - start));
      globalPeak = Math.max(globalPeak, Math.abs(lo), Math.abs(hi));
    }
    for (let bucket = 0; bucket < buckets; bucket += 1) {
      mins[bucket] /= globalPeak;
      maxs[bucket] /= globalPeak;
      rms[bucket] /= globalPeak;
    }
    audioAnalysis = {
      buckets,
      min: mins,
      max: maxs,
      rms,
      duration: decoded.duration,
    };
    await context.close();
  } catch {
    audioAnalysis = null;
    audioAnalysisKey = null;
  } finally {
    audioAnalysisLoading = false;
    requestDraw();
  }
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
    updateAutoShowButton();
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
  updateAutoShowButton();
  requestDraw();
}

async function uploadBlueprint(file) {
  const showId = activeShowId();
  if (!file || !showId) return;
  dom.syncMarkers.disabled = true;
  try {
    const response = await fetch(
      `/api/shows/${encodeURIComponent(showId)}/blueprint?source=${encodeURIComponent(file.name)}&sync_show=true`,
      {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: file,
      }
    );
    const text = await response.text();
    const data = text ? JSON.parse(text) : null;
    if (!response.ok) throw new Error(data?.detail || `${response.status} ${response.statusText}`);
    const sections = Number(data.section_count || 0);
    const drops = Number(data.drop_count || 0);
    toast(
      `AudioBlueprint: ${data.bpm} BPM · ${sections} Sections · ${drops} Drops — Marker gesetzt`,
      "success"
    );
    blueprintKey = null;
    audioFailedUrl = null;
    audioNoticeShown = false;
    await client.refresh();
    const markers = showDoc()?.timing?.markers?.length || 0;
    if (!markers) {
      toast("Analyse ohne Sections: Marker manuell setzen oder Sync erneut ausführen.", "info", 6000);
    }
  } catch (error) {
    toast(error.message, "danger");
  } finally {
    dom.syncMarkers.disabled = false;
  }
}

async function syncMarkersFromBlueprint() {
  const showId = activeShowId();
  if (!showId) return;
  dom.syncMarkers.disabled = true;
  try {
    const result = await client.post(`/api/shows/${encodeURIComponent(showId)}/sync-from-blueprint`);
    if (state?.show && result?.document) state.show.document = result.document;
    blueprintKey = null;
    lastHistory = result.history || lastHistory;
    await syncBlueprint();
    renderInspector();
    requestDraw();
    await refreshHistory();
    const markers = state?.show?.document?.timing?.markers?.length || 0;
    toast(`Marker synchronisiert: ${markers}`, "success");
  } catch (error) {
    toast(error.message, "danger");
  } finally {
    dom.syncMarkers.disabled = false;
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

function onState() {
  state = client.state;
  if (!state) return;
  proxyHealth = state.proxy_health || state.rig?.proxy_health || null;
  if (lastShowId !== state.show.id) {
    lastShowId = state.show.id;
    view.scrollY = 0;
    view.start = 0;
    view.end = Math.max(1, Math.min(Number(state.show.document?.duration_s) || 1, 60));
  }
  sanitizeSelection();
  renderShowSelect();
  renderRigSelect();
  renderPresets();
  renderFilters();
  renderPalettes();
  renderFixtures();
  renderGroups();
  renderInspector();
  updateTransport();
  updateDirty();
  updateTopbar();
  syncAudioAnalysis();
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
function updateDirty() {
  dom.rigDirty.classList.toggle("hidden", !state.rig.dirty);
  dom.rigSelect.value = state.rig.active || "";
  const active = state.rig.presets.find((preset) => preset.id === state.rig.active);
  dom.rigSelect.title = active ? `${active.fixture_count} fixtures / ${active.voxel_count} voxels` : "";
}

function updateTopbar() {
  if (!state) return;
  const health = proxyHealth || { assigned_proxies: 0, required_proxies: 0, ok: false };
  if (dom.proxyBadge) {
    dom.proxyBadge.textContent = `PROXIES ${health.assigned_proxies}/${health.required_proxies} ${health.ok ? "OK" : "ERR"}`;
    dom.proxyBadge.classList.toggle("bad", !health.ok);
    dom.proxyBadge.title = [
      `${health.hue_count ?? 0} Hue bulbs`,
      `${health.assigned_proxies ?? 0} proxies assigned`,
      `${health.required_proxies ?? 0} required`,
      health.ok ? "proxy plan valid" : "proxy plan incomplete",
    ].join(" | ");
  }
  const hardware = state.hardware || {};
  if (dom.hw) {
    dom.hw.classList.toggle("active", Boolean(hardware.enabled));
    dom.hw.setAttribute("aria-pressed", String(Boolean(hardware.enabled)));
    dom.hw.title = hardware.enabled
      ? `Hardware output ON | ${hardware.tx_operations || 0} tx`
      : "Hardware output OFF";
    dom.hw.classList.toggle("bad", Boolean(hardware.enabled && hardware.last_error));
  }
  if (dom.hardwareStatus) {
    const tx = Number(hardware.tx_operations || 0);
    dom.hardwareStatus.textContent = hardware.enabled
      ? `HW OUT ${hardware.started ? "READY" : "STARTING"} · ${tx} tx${hardware.last_error ? ` · ${hardware.last_error}` : ""}`
      : "HW OUT OFF";
    dom.hardwareStatus.title = hardware.last_error || (hardware.enabled ? "Hardware frames are being sent" : "Physical output is disabled");
    dom.hardwareStatus.style.color = hardware.last_error
      ? "var(--danger)"
      : hardware.enabled
        ? "var(--ok)"
        : "var(--text-2)";
  }
  updateSync();
  if (dom.undoCount) dom.undoCount.textContent = String(lastHistory.undo_depth || 0);
  if (dom.redoCount) dom.redoCount.textContent = String(lastHistory.redo_depth || 0);
  if (dom.undo) dom.undo.disabled = !(lastHistory.undo_depth > 0);
  if (dom.redo) dom.redo.disabled = !(lastHistory.redo_depth > 0);
  updateTimecode();
}

function updateTransport() {
  const playback = state?.playback || {};
  setIcon(dom.play, playback.playing ? "pause" : "play", playback.playing ? "Pause" : "Play");
  dom.play.classList.toggle("active", Boolean(playback.playing));
  dom.loop.classList.toggle("active", Boolean(playback.loop));
  dom.link.textContent = client.connected ? "PREVIEW LIVE" : "OFFLINE";
  dom.link.style.color = client.connected ? "var(--ok)" : "var(--text-2)";
  updateTimecode();
  updateSync();
}

function currentTime() {
  return Number(client.frame?.time ?? state?.playback?.time_s ?? 0);
}

function timelineActive() {
  if (document.activeElement === dom.timeline) return true;
  if (!lastPointerClient) return false;
  const pane = dom.timeline.closest(".timeline") || dom.timeline;
  const rect = pane.getBoundingClientRect();
  return (
    lastPointerClient.x >= rect.left && lastPointerClient.x <= rect.right &&
    lastPointerClient.y >= rect.top && lastPointerClient.y <= rect.bottom
  );
}

function stepFrames(direction, count = 1) {
  const total = duration();
  if (total <= 0) return;
  const fps = Math.max(1, Number(state?.playback?.fps || 30));
  const frame = Math.round(currentTime() * fps) + direction * count;
  const time = clamp(frame / fps, 0, total);
  if (state?.playback?.playing) client.pause();
  client.seek(time);
}

function timecodeText(time = currentTime()) {
  if (timecodeMode === 1) {
    const fps = Math.max(1, Number(state?.playback?.fps || 30));
    return `F ${String(Math.max(0, Math.floor(time * fps))).padStart(6, "0")}`;
  }
  if (timecodeMode === 2) {
    const grid = beatSeconds();
    const offset = Number(showDoc()?.timing?.beat_offset_s || 0);
    const beats = Math.max(0, (time - offset) / Math.max(1e-6, grid));
    const bar = Math.floor(beats / 4) + 1;
    const beat = Math.floor(beats % 4) + 1;
    const tick = Math.floor((beats - Math.floor(beats)) * 100);
    return `${String(bar).padStart(3, "0")}.${beat}.${String(tick).padStart(2, "0")}`;
  }
  return formatTime(time);
}

function updateTimecode() {
  if (dom.timecode) dom.timecode.textContent = timecodeText();
}

const SYNC_LABELS = {
  off: "MA LOCK",
  waiting: "MA LOCK",
  unconfigured: "MA LOCK",
  disconnected: "MA OFFLINE",
  no_track: "MA IDLE",
  no_match: "MA NO MATCH",
  acquiring: "MA ACQ",
  locked: "MA LOCKED",
  paused: "MA PAUSED",
  ended: "MA END",
};

function updateSync() {
  if (!dom.sync) return;
  const sync = state?.sync || {};
  dom.sync.textContent = SYNC_LABELS[sync.state] || "MA LOCK";
  dom.sync.disabled = !sync.configured;
  const unpaired = Boolean(sync.configured && sync.connected && sync.paired === false);
  dom.maPair?.classList.toggle("hidden", !unpaired);
  dom.maRestart?.classList.toggle("hidden", !sync.configured);
  const active = Boolean(sync.enabled);
  dom.sync.classList.toggle("active", active);
  dom.sync.setAttribute("aria-pressed", String(active));
  dom.sync.style.color = active
    ? sync.state === "locked"
      ? "var(--ok)"
      : "var(--accent)"
    : "";
  const track = sync.track;
  dom.sync.title = sync.configured
    ? [
        sync.enabled ? "MA auto-show enabled" : "MA auto-show disabled",
        unpaired ? "UNPAIRED - PAIR button shows the Sendspin token" : "paired",
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

const FILTER_CATEGORIES = {
  spatial_blur: "Spatial",
  mirror_symmetry: "Spatial",
  grain_noise: "Texture",
  color_grade: "Colour",
  invert_color: "Colour",
  dim2warm: "Colour",
  hue_rotate: "Colour",
  saturation: "Colour",
  brightness_curve: "Colour",
  strobe_shutter: "Time",
  beat_gate: "Time",
  inertia: "Time",
  scanline: "Texture",
  pixelate: "Texture",
  posterize: "Texture",
  audio_mod: "Audio",
};

function libraryQueryMatch(text) {
  if (!libraryQuery) return true;
  return String(text || "").toLowerCase().includes(libraryQuery);
}

function libraryKey(kind, id) {
  return `${kind}:${id}`;
}

function saveLibraryList(key, values) {
  try {
    localStorage.setItem(key, JSON.stringify(values));
  } catch {
    // Library preferences are a convenience, not required for editing.
  }
}

function rememberLibraryItem(kind, id) {
  const key = libraryKey(kind, id);
  libraryRecents = [key, ...libraryRecents.filter((item) => item !== key)].slice(0, 16);
  saveLibraryList(LIBRARY_RECENTS_KEY, libraryRecents);
  if (kind === "preset") renderPresets();
  else renderFilters();
}

function toggleLibraryFavorite(kind, id) {
  const key = libraryKey(kind, id);
  if (libraryFavorites.has(key)) libraryFavorites.delete(key);
  else libraryFavorites.add(key);
  saveLibraryList(LIBRARY_FAVORITES_KEY, [...libraryFavorites]);
  if (kind === "preset") renderPresets();
  else renderFilters();
}

function updateLibrarySort() {
  if (!dom.librarySort) return;
  const alpha = librarySort === "alpha";
  dom.librarySort.textContent = alpha ? "A–Z" : "Cat";
  dom.librarySort.setAttribute("aria-pressed", String(alpha));
  dom.librarySort.title = alpha
    ? "Alphabetisch — Klick: nach Kategorie"
    : "Nach Kategorie — Klick: alphabetisch";
}

function highlightText(value) {
  const text = String(value ?? "");
  if (!libraryQuery) return text;
  const lower = text.toLowerCase();
  const chunks = [];
  let start = 0;
  while (start < lower.length) {
    const hit = lower.indexOf(libraryQuery, start);
    if (hit < 0) {
      chunks.push(text.slice(start));
      break;
    }
    if (hit > start) chunks.push(text.slice(start, hit));
    chunks.push(el("mark", { text: text.slice(hit, hit + libraryQuery.length) }));
    start = hit + libraryQuery.length;
  }
  return chunks.length ? chunks : text;
}

function libraryStar(kind, id) {
  const key = libraryKey(kind, id);
  const active = libraryFavorites.has(key);
  return el("button", {
    class: `library-star${active ? " active" : ""}`,
    type: "button",
    title: active ? "Aus Favoriten entfernen" : "Zu Favoriten hinzufügen",
    "aria-label": active ? "Remove from favorites" : "Add to favorites",
    "aria-pressed": String(active),
    "data-library-key": key,
    onclick: (event) => {
      event.stopPropagation();
      const keyboard = event.detail === 0;
      toggleLibraryFavorite(kind, id);
      if (keyboard) {
        requestAnimationFrame(() => {
          document
            .querySelector(`.library-star[data-library-key="${CSS.escape(key)}"]`)
            ?.focus({ preventScroll: true });
        });
      }
    },
  }, icon(active ? "star" : "starOutline"));
}

function libraryQuickGroups(kind, entries, buildRows) {
  const nodes = [];
  const keyOf = (entry) => libraryKey(kind, entry.id);
  const byKey = new Map(entries.map((entry) => [keyOf(entry), entry]));
  const favourites = entries.filter((entry) => libraryFavorites.has(keyOf(entry)));
  if (favourites.length) {
    nodes.push(categoryGroup("Favorites", favourites, buildRows, { key: `${kind}:favorites` }));
  }
  const recents = libraryRecents
    .filter((key) => key.startsWith(`${kind}:`))
    .map((key) => byKey.get(key))
    .filter(Boolean)
    .slice(0, 8);
  if (recents.length) {
    nodes.push(categoryGroup("Recently Used", recents, buildRows, { key: `${kind}:recents` }));
  }
  return nodes;
}

// A native double-click fires click twice. Apply a library action once per
// short window so a double-click cannot create two commits/undo entries.
const libraryActionTimes = new Map();
function libraryActionOnce(key, action) {
  const now = performance.now();
  const last = libraryActionTimes.get(key) || 0;
  if (now - last < 350) return;
  libraryActionTimes.set(key, now);
  action();
}

function categoryGroup(category, entries, buildBody, options = {}) {
  const key = options.key || category;
  const closed = !libraryQuery && collapsedCategories.has(key);
  const head = el(
    "button",
    {
      class: "preset-group-head",
      type: "button",
      "aria-expanded": String(!closed),
      onclick: () => {
        if (collapsedCategories.has(key)) collapsedCategories.delete(key);
        else collapsedCategories.add(key);
        renderPresets();
        renderFilters();
      },
    },
    el("span", { class: "group-toggle", text: closed ? "▸" : "▾" }),
    el("span", { text: category }),
    el("span", { class: "group-count", text: String(entries.length) })
  );
  return el(
    "div",
    { class: `preset-group${closed ? " closed" : ""}` },
    head,
    el("div", { class: "preset-group-body" }, ...buildBody(entries))
  );
}

function previewImage(kind, item) {
  const src = kind === "preset"
    ? `/api/presets/${encodeURIComponent(item.id)}/preview.png?t=0.65`
    : `/api/filters/${encodeURIComponent(item.id)}/preview.png?t=0.65`;
  const img = el("img", {
    class: "preset-preview",
    src,
    alt: "",
    loading: "lazy",
    decoding: "async",
    draggable: "false",
  });
  img.addEventListener("error", () => {
    img.classList.add("preview-missing");
    img.removeAttribute("src");
  });
  return img;
}

function presetRow(preset, selected) {
  const category = preset.category || "Presets";
  const favourite = libraryFavorites.has(libraryKey("preset", preset.id));
  return el(
    "div",
    {
      class: `list-row preset-row${selected === preset.id ? " selected" : ""}`,
      role: "button",
      tabindex: "0",
      title: preset.description,
      draggable: "true",
      onclick: () => libraryActionOnce(`preset:${preset.id}`, () => applyPreset(preset.id)),
      onkeydown: (event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.code === "Space") {
          event.preventDefault();
          libraryActionOnce(`preset:${preset.id}`, () => applyPreset(preset.id));
        }
      },
      oncontextmenu: (event) => {
        event.preventDefault();
        showContextMenu(event.clientX, event.clientY, [
          { label: "Auf ausgewählten Clip anwenden", fn: () => applyPreset(preset.id) },
          { label: "In Timeline am Playhead einfügen", fn: () => addPresetClipAtPlayhead(preset.id) },
          {
            label: favourite ? "Aus Favoriten entfernen" : "Zu Favoriten hinzufügen",
            fn: () => toggleLibraryFavorite("preset", preset.id),
          },
          { separator: true },
          { label: "Preset-Info", fn: () => modalConfirm(preset.name, preset.description || preset.name, "OK"), disabled: !preset.description },
        ]);
      },
      ondragstart: (event) => {
        event.dataTransfer.setData(
          "application/x-hueme-preset",
          JSON.stringify({ preset: preset.id })
        );
        event.dataTransfer.effectAllowed = "copy";
      },
    },
    previewImage("preset", preset),
    el("span", { class: "row-text" },
      el("span", { class: "row-name" }, highlightText(preset.name)),
      el("span", { class: "row-desc" }, highlightText(preset.description || category))
    ),
    el("span", { class: "row-meta", text: `${preset.params.length}p` }),
    libraryStar("preset", preset.id)
  );
}

function filterRow(filter, category) {
  const favourite = libraryFavorites.has(libraryKey("filter", filter.id));
  return el(
    "div",
    {
      class: "list-row filter-row",
      role: "button",
      tabindex: "0",
      title: filter.description,
      draggable: "true",
      onclick: () => libraryActionOnce(`filter:${filter.id}`, () => addFilter(filter.id)),
      onkeydown: (event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.code === "Space") {
          event.preventDefault();
          libraryActionOnce(`filter:${filter.id}`, () => addFilter(filter.id));
        }
      },
      oncontextmenu: (event) => {
        event.preventDefault();
        showContextMenu(event.clientX, event.clientY, [
          { label: "Auf ausgewählten Clip anwenden", fn: () => addFilter(filter.id) },
          {
            label: favourite ? "Aus Favoriten entfernen" : "Zu Favoriten hinzufügen",
            fn: () => toggleLibraryFavorite("filter", filter.id),
          },
          { label: "Filter-Info", fn: () => modalConfirm(filter.name, filter.description || filter.name, "OK"), disabled: !filter.description },
        ]);
      },
      ondragstart: (event) => {
        event.dataTransfer.setData(
          "application/x-hueme-filter",
          JSON.stringify({ filter: filter.id })
        );
        event.dataTransfer.effectAllowed = "copy";
      },
    },
    previewImage("filter", filter),
    el("span", { class: "row-text" },
      el("span", { class: "row-name" }, highlightText(filter.name)),
      el("span", { class: "row-desc" }, highlightText(filter.description || category))
    ),
    el("span", { class: "row-meta", text: filter.params.length ? `${filter.params.length}p` : "" }),
    libraryStar("filter", filter.id)
  );
}

function renderPresets() {
  const selected = findClip()?.clip.preset;
  const entries = (state.presets || []).filter((preset) => {
    const category = preset.category || "Presets";
    return libraryQueryMatch(`${preset.name} ${preset.description || ""} ${category}`);
  });
  const buildRows = (items) => items.map((preset) => presetRow(preset, selected));
  const nodes = libraryQuickGroups("preset", entries, buildRows);
  if (librarySort === "alpha") {
    const sorted = [...entries].sort((a, b) => a.name.localeCompare(b.name));
    if (sorted.length) {
      nodes.push(categoryGroup("A–Z", sorted, buildRows, { key: "preset:alpha" }));
    }
  } else {
    const groups = new Map();
    for (const preset of entries) {
      const category = preset.category || "Presets";
      if (!groups.has(category)) groups.set(category, []);
      groups.get(category).push(preset);
    }
    for (const [category, items] of groups) {
      nodes.push(categoryGroup(category, items, buildRows, { key: `preset:${category}` }));
    }
  }
  if (!nodes.length) nodes.push(el("div", { class: "library-empty", text: libraryQuery ? "Keine Presets gefunden." : "Keine Presets geladen." }));
  dom.presetList.replaceChildren(...nodes);
  updateLibraryCount();
}

function renderFilters() {
  const filterOrder = ["Spatial", "Colour", "Time", "Texture", "Audio", "Utility"];
  const categoryOf = (filter) => FILTER_CATEGORIES[filter.id] || "Utility";
  const entries = (state.filters || []).filter((filter) =>
    libraryQueryMatch(`${filter.name} ${filter.description || ""}`)
  );
  const buildRows = (items) => items.map((filter) => filterRow(filter, categoryOf(filter)));
  const nodes = libraryQuickGroups("filter", entries, buildRows);
  if (librarySort === "alpha") {
    const sorted = [...entries].sort((a, b) => a.name.localeCompare(b.name));
    if (sorted.length) {
      nodes.push(categoryGroup("A–Z", sorted, buildRows, { key: "filter:alpha" }));
    }
  } else {
    const groups = new Map();
    for (const filter of entries) {
      const category = categoryOf(filter);
      if (!groups.has(category)) groups.set(category, []);
      groups.get(category).push(filter);
    }
    const orderedGroups = [...groups.entries()].sort(
      (a, b) => filterOrder.indexOf(a[0]) - filterOrder.indexOf(b[0])
    );
    for (const [category, items] of orderedGroups) {
      nodes.push(categoryGroup(`${category} FX`, items, buildRows, { key: `filter:${category}` }));
    }
  }
  if (!nodes.length) nodes.push(el("div", { class: "library-empty", text: libraryQuery ? "Keine Effekte gefunden." : "Keine Effekte geladen." }));
  dom.filterList.replaceChildren(...nodes);
  updateLibraryCount();
}

function updateLibraryCount() {
  if (!dom.libraryCount) return;
  const presets = (state?.presets || []).filter((preset) =>
    libraryQueryMatch(`${preset.name} ${preset.description || ""} ${preset.category || ""}`)
  ).length;
  const filters = (state?.filters || []).filter((filter) =>
    libraryQueryMatch(`${filter.name} ${filter.description || ""}`)
  ).length;
  dom.libraryCount.textContent = `${presets} / ${filters}`;
}

function selectFixtures(ids) {
  selectedFixtures = new Set(ids);
  inspectorTab = "fixture";
  updateTabButtons();
  renderFixtures();
  renderGroups();
  renderInspector();
  requestDraw();
}

function fixtureRow(fixture) {
  const selected = selectedFixtures.has(fixture.id);
  const protocol = fixture.output?.protocol || "none";
  const enabled = fixture.output?.enabled !== false;
  const reasons = fixture.incomplete_reasons || [];
  const visibility = el("button", {
    class: `fixture-toggle${enabled ? " on" : ""}`,
    type: "button",
    title: enabled ? "Disable output" : "Enable output",
    onclick: (event) => {
      event.stopPropagation();
      patchFixture(fixture.id, { output: { enabled: !enabled } });
    },
  }, icon("power"));
  const row = el(
    "div",
    {
      class: `tree-row tree-indent${selected ? " selected" : ""}${reasons.length ? " incomplete" : ""}`,
      role: "button",
      tabindex: "0",
      draggable: "true",
      title: reasons.length ? reasons.join("\n") : fixture.name,
      onclick: (event) => selectFixture(fixture.id, event.shiftKey),
      ondragstart: (event) => {
        const ids = selectedFixtures.has(fixture.id) ? [...selectedFixtures] : [fixture.id];
        event.dataTransfer.setData("application/x-hueme-fixtures", JSON.stringify(ids));
        event.dataTransfer.effectAllowed = "copy";
      },
    },
    reasons.length ? el("span", { class: "warning-dot" }) : el("span", { class: "warning-dot hidden" }),
    visibility,
    el("span", { class: `badge fixture-badge ${PROTOCOL_CLASS[protocol] || ""}`, text: PROTOCOL_LABEL[protocol] || protocol.toUpperCase() }),
    el("span", { class: "row-name", text: fixture.name }),
    el("span", {
      class: "row-meta",
      text: [reasons.length ? "!" : "", fixture.geometry?.type === "line" ? `${fixture.geometry.pixels}px` : "1"].filter(Boolean).join(" "),
    })
  );
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.code === "Space") {
      event.preventDefault();
      selectFixture(fixture.id, event.shiftKey);
    }
  });
  return row;
}

function folderRow(folder, fixtures) {
  const collapsed = collapsedFolders.has(folder.id);
  const allSelected = fixtures.length > 0 && fixtures.every((fixture) => selectedFixtures.has(fixture.id));
  const row = el(
    "div",
    {
      class: `tree-folder${allSelected ? " active" : ""}`,
      role: "button",
      tabindex: "0",
      title: `${fixtures.length} fixtures`,
      onclick: () => selectFixtures(fixtures.map((fixture) => fixture.id)),
    },
    el("button", {
      class: "fixture-toggle",
      type: "button",
      title: collapsed ? "Open folder" : "Collapse folder",
      onclick: (event) => {
        event.stopPropagation();
        if (collapsed) collapsedFolders.delete(folder.id);
        else collapsedFolders.add(folder.id);
        renderFixtures();
      },
    }, icon("chevron", collapsed ? "" : "")),
    el("span", { class: "folder-icon" }, icon("folder")),
    el("span", { text: folder.name }),
    el("span", { class: "folder-count", text: String(fixtures.length) })
  );
  if (collapsed) row.querySelector(".icon")?.classList.add("collapsed");
  return row;
}

function renderFixtures() {
  const doc = state.rig.document;
  dom.fixtureCount.textContent = `${doc.fixtures.length} fx / ${state.rig.compiled.voxel_count} vox`;
  const fixturesByFolder = new Map();
  for (const fixture of doc.fixtures) {
    const key = fixture.folder_id || "";
    if (!fixturesByFolder.has(key)) fixturesByFolder.set(key, []);
    fixturesByFolder.get(key).push(fixture);
  }
  const nodes = [];
  const folders = doc.folders || [];
  const placed = new Set();
  for (const folder of folders) {
    const fixtures = fixturesByFolder.get(folder.id) || [];
    if (!fixtures.length) continue;
    nodes.push(folderRow(folder, fixtures));
    if (!collapsedFolders.has(folder.id)) {
      for (const fixture of fixtures) {
        nodes.push(fixtureRow(fixture));
        placed.add(fixture.id);
      }
    } else {
      for (const fixture of fixtures) placed.add(fixture.id);
    }
  }
  const loose = doc.fixtures.filter((fixture) => !placed.has(fixture.id));
  if (loose.length) {
    nodes.push(el("div", { class: "preset-category", text: "Unfiled" }));
    nodes.push(...loose.map(fixtureRow));
  }
  if (!nodes.length) nodes.push(el("div", { class: "tree-empty", text: "No fixtures" }));
  dom.fixtureList.replaceChildren(...nodes);
}

function groupFixtureIds(group) {
  const compiled = state.rig.compiled;
  if (group.type === "list") return group.fixtures || [];
  if (group.type === "all") return state.rig.document.fixtures.map((fixture) => fixture.id);
  const indices = compiled.groups?.[group.id] || [];
  const fixtures = state.rig.document.fixtures;
  const ids = new Set();
  for (const voxel of indices) {
    const fixtureIndex = compiled.voxel_fixture_index?.[voxel] ?? 0;
    const fixture = fixtures[fixtureIndex];
    if (fixture) ids.add(fixture.id);
  }
  return [...ids];
}

function renderGroups() {
  const doc = state.rig.document;
  const compiled = state.rig.compiled.groups || {};
  const nodes = [
    el("div", { class: "preset-category", text: "Gruppen" }),
    ...doc.groups.map((group) => {
      const ids = groupFixtureIds(group);
      const row = el(
        "div",
        {
          class: `tree-row${selectedGroupId === group.id ? " selected" : ""}`,
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
            if (ids.length) selectFixtures(ids);
            else {
              renderGroups();
              renderInspector();
              requestDraw();
            }
          },
        },
        el("span", { class: "row-name", text: group.name }),
        el("span", { class: "row-meta", text: `${group.type === "all" ? "ALL" : group.type.replace("spatial_", "")} ${ids.length}` })
      );
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.code === "Space") {
          event.preventDefault();
          row.click();
        }
      });
      return row;
    }),
  ];
  const smart = doc.smart_groups || [];
  if (smart.length) {
    nodes.push(el("div", { class: "preset-category", text: "Smart-Auswahl" }));
    for (const group of smart) {
      const ids = group.fixtures || [];
      const row = el(
        "div",
        {
          class: `tree-row smart-row${selectedGroupId === group.id ? " selected" : ""}`,
          role: "button",
          tabindex: "0",
          title: "Smart selection group",
          onclick: () => {
            selectedGroupId = group.id;
            selectFixtures(ids);
          },
        },
        el("span", { class: "smart-hash", text: "#" }),
        el("span", { class: "row-name", text: group.name }),
        el("span", { class: "row-meta", text: String((compiled[group.id] || ids).length) })
      );
      nodes.push(row);
    }
  }
  dom.groupList.replaceChildren(...nodes);
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
  if (!camera.ortho && depth <= 0.05) return null;
  const focal = (viewportSize.h * 0.5) / Math.tan(camera.fov * 0.5);
  if (camera.ortho) {
    const worldPerPixel = camera.orthoSize / Math.max(1, viewportSize.h);
    return {
      x: viewportSize.w * 0.5 + dot(relative, basis.right) / worldPerPixel,
      y: viewportSize.h * 0.5 - dot(relative, basis.up) / worldPerPixel,
      depth,
      focal: depth * viewportSize.h / Math.max(1e-6, camera.orthoSize),
      ortho: true,
      worldPerPixel,
    };
  }
  return {
    x: viewportSize.w * 0.5 + (dot(relative, basis.right) * focal) / depth,
    y: viewportSize.h * 0.5 - (dot(relative, basis.up) * focal) / depth,
    depth,
    focal,
    ortho: false,
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
  camera.orthoSize = Math.max(2, Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2]) * 1.3);
  camera.yaw = -0.78;
  camera.pitch = 0.52;
  if (redraw) requestDraw();
}

function setOrthoView(axis, opposite = false) {
  const room = roomDoc();
  const min = room.min;
  const max = room.max;
  const center = [
    (min[0] + max[0]) / 2,
    (min[1] + max[1]) / 2,
    (min[2] + max[2]) / 2,
  ];
  const radius = Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]) / 2;
  const extent = Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2]);
  camera.target = center;
  camera.distance = Math.max(4, radius * 4.5);
  camera.orthoSize = Math.max(2, extent * 1.3);
  camera.ortho = true;
  camera.pitch = 0;
  if (axis === 0) {
    camera.yaw = opposite ? -Math.PI / 2 : Math.PI / 2;
    camera.pitch = 0;
  } else if (axis === 1) {
    camera.yaw = opposite ? Math.PI : 0;
    camera.pitch = 0;
  } else {
    camera.yaw = 0;
    camera.pitch = opposite ? -Math.PI / 2 + 1e-4 : Math.PI / 2 - 1e-4;
  }
  lastFlatView = { axis, opposite };
  requestDraw();
}

function toggleCameraProjection() {
  camera.ortho = !camera.ortho;
  if (camera.ortho) {
    const room = roomDoc();
    const extent = Math.max(
      room.max[0] - room.min[0],
      room.max[1] - room.min[1],
      room.max[2] - room.min[2]
    );
    camera.orthoSize = Math.max(2, extent * 1.3);
  }
  requestDraw();
}

// Menu and keyboard camera commands; the view cube uses setOrthoView directly.
function setCameraNamed(name) {
  if (fxMode) setFxMode(false);
  fitCamera(false);
  camera.ortho = name !== "persp";
  if (name === "front") {
    camera.yaw = 0;
    camera.pitch = 0;
  } else if (name === "side") {
    camera.yaw = Math.PI / 2;
    camera.pitch = 0;
  } else if (name === "top") {
    camera.yaw = 0;
    camera.pitch = Math.PI / 2 - 1e-4;
  }
  requestDraw();
}

function cameraViewLabel() {
  if (Math.abs(camera.pitch) > 1.5) return camera.pitch > 0 ? "TOP" : "BOTTOM";
  const tau = Math.PI * 2;
  const yaw = ((camera.yaw % tau) + tau) % tau;
  if (yaw < 0.02 || yaw > tau - 0.02) return "FRONT";
  if (Math.abs(yaw - Math.PI) < 0.02) return "BACK";
  if (Math.abs(yaw - Math.PI / 2) < 0.02) return "RIGHT";
  if (Math.abs(yaw - (3 * Math.PI) / 2) < 0.02) return "LEFT";
  return "USER";
}

function fxTargetClip() {
  const selected = findClip();
  if (selected) return selected;
  const doc = showDoc();
  if (!doc) return null;
  const t = currentTime();
  for (const track of doc.tracks) {
    const clip = track.clips.find((item) => t >= item.start_s && t < item.end_s);
    if (clip) return { track, clip };
  }
  return null;
}

function fxRequestPayload() {
  const found = fxTargetClip();
  const doc = showDoc();
  if (!found || !doc) return null;
  const hasFilters = (found.clip.filters || []).some((filter) => filter.enabled);
  return {
    clip: found.clip,
    t: Number(currentTime().toFixed(3)),
    timing: doc.timing,
    width: hasFilters ? 48 : 128,
    height: hasFilters ? 27 : 72,
  };
}

function setFxMode(enabled) {
  fxMode = Boolean(enabled);
  dom.root.classList.toggle("fx-mode", fxMode);
  dom.fxToggle?.classList.toggle("active", fxMode);
  dom.fxPreview?.classList.toggle("hidden", !fxMode);
  if (!fxMode) {
    if (fxImage?.close) fxImage.close();
    fxImage = null;
    fxImageKey = "";
    fxError = "";
  } else {
    refreshFxPreview(true);
  }
  requestDraw();
}

async function refreshFxPreview(force = false) {
  if (!fxMode) return;
  const payload = fxRequestPayload();
  if (!payload) {
    if (fxImage?.close) fxImage.close();
    fxImage = null;
    fxImageKey = "";
    fxError = "";
    requestDraw();
    return;
  }
  const key = JSON.stringify(payload);
  if (!force && key === fxImageKey) return;
  if (fxLoading) {
    fxQueued = true;
    return;
  }
  fxLoading = true;
  try {
    const response = await fetch("/api/preview/effect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || `Preview HTTP ${response.status}`);
    }
    const bitmap = await createImageBitmap(await response.blob());
    if (fxMode) {
      if (fxImage?.close) fxImage.close();
      fxImage = bitmap;
      fxImageKey = key;
      fxError = "";
    } else if (bitmap.close) {
      bitmap.close();
    }
  } catch (error) {
    fxError = error.message || "Preview failed";
    fxImageKey = key;
  } finally {
    fxLoading = false;
    const again = fxQueued;
    fxQueued = false;
    if (again) refreshFxPreview();
    requestDraw();
  }
}

function drawFxPreview() {
  const canvas = dom.fxPreview;
  if (!canvas) return;
  const { w, h, context } = sizeCanvas(canvas);
  context.fillStyle = "#08090b";
  context.fillRect(0, 0, w, h);
  if (fxImage) {
    const ratio = Math.min(w / fxImage.width, h / fxImage.height);
    const dw = fxImage.width * ratio;
    const dh = fxImage.height * ratio;
    context.imageSmoothingEnabled = true;
    context.drawImage(fxImage, (w - dw) / 2, (h - dh) / 2, dw, dh);
  } else {
    context.fillStyle = "#2a2d33";
    context.font = "11px JetBrains Mono, monospace";
    context.textAlign = "center";
    context.fillText(
      fxError || (fxLoading ? "RENDERING FX …" : "KEIN CLIP UNTER DEM PLAYHEAD"),
      w / 2,
      h / 2
    );
    context.textAlign = "left";
  }
  const found = fxTargetClip();
  const label = found
    ? `FX FRONT · ${clipDisplayName(found.clip)} · ${presetName(found.clip.preset)} · OHNE FIXTURES`
    : "FX FRONT · OHNE FIXTURES";
  dom.viewportStatus.textContent = label;
}

function drawViewport() {
  if (fxMode) {
    viewCubeHits = [];
    viewCubeHover = null;
    drawFxPreview();
    return;
  }
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

  // Floor grid: 0.5 m minor, 2 m major. Draw far lines first with depth fade
  // so the box reads as a solid space instead of crossing wires.
  const step = 0.5;
  const gridLines = [];
  for (let x = min[0]; x <= max[0] + 1e-6; x += step) {
    const major = Math.abs(x / 2 - Math.round(x / 2)) < 1e-6;
    const a = [x, min[1], floor];
    const b = [x, max[1], floor];
    const pa = project(a);
    const pb = project(b);
    if (!pa || !pb) continue;
    gridLines.push({ a, b, color: major ? "#24272c" : "#191b1f", depth: (pa.depth + pb.depth) * 0.5, width: 1, alphaNear: 0.9, alphaFar: 0.3, widthGain: 0.2 });
  }
  for (let y = min[1]; y <= max[1] + 1e-6; y += step) {
    const major = Math.abs(y / 2 - Math.round(y / 2)) < 1e-6;
    const a = [min[0], y, floor];
    const b = [max[0], y, floor];
    const pa = project(a);
    const pb = project(b);
    if (!pa || !pb) continue;
    gridLines.push({ a, b, color: major ? "#24272c" : "#191b1f", depth: (pa.depth + pb.depth) * 0.5, width: 1, alphaNear: 0.9, alphaFar: 0.3, widthGain: 0.2 });
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
  const roomLines = [];
  for (const [a, b] of edges) {
    const pa = project(corners[a]);
    const pb = project(corners[b]);
    if (!pa || !pb) continue;
    roomLines.push({
      a: corners[a],
      b: corners[b],
      color: "#2b3038",
      depth: (pa.depth + pb.depth) * 0.5,
      width: 1,
      alphaNear: 1.0,
      alphaFar: 0.22,
      widthGain: 0.7,
    });
  }
  const allDepths = [...gridLines, ...roomLines].map((line) => line.depth);
  const nearDepth = allDepths.length ? Math.min(...allDepths) : 0;
  const farDepth = allDepths.length ? Math.max(...allDepths) : 1;
  const depthLines = [...gridLines, ...roomLines].sort((a, b) => b.depth - a.depth);
  for (const line of depthLines) {
    const ratio = clamp((line.depth - nearDepth) / Math.max(1e-6, farDepth - nearDepth), 0, 1);
    context.globalAlpha = line.alphaNear + (line.alphaFar - line.alphaNear) * ratio;
    drawWorldLine(context, line.a, line.b, line.color, line.width + (1 - ratio) * line.widthGain);
  }
  context.globalAlpha = 1;

  // Light cones and floor pools were visually noisy and often projected
  // through walls. Keep only the fixture's own glow and strip colours.

  const fixtures = state.rig.document.fixtures
    .map((fixture) => {
      const centre = project(fixtureCenter(fixture.id));
      return { fixture, depth: centre?.depth ?? camera.distance };
    })
    .sort((a, b) => b.depth - a.depth)
    .map((entry) => entry.fixture);
  for (const fixture of fixtures) {
    const slice = voxelSlice(fixture.id);
    const offset = overrideOffset(fixture.id);
    let points = fixturePositions(fixture.id).map((point) => add(point, offset));
    if (transformOp && transformOp.ids.includes(fixture.id)) {
      points = points.map((point) => previewTransformPoint(point, transformOp, fixture.id));
    }
    if (!points.length) continue;
    const selected = selectedFixtures.has(fixture.id);
    if (fixture.geometry.type === "point") {
      const projected = project(points[0]);
      if (!projected) continue;
      const [r, g, b] = rgbAt(slice[0]);
      const peak = Math.max(r, g, b) / 255;
      const radius = clamp(190 / projected.depth, 3, 9);
      if (peak > 0.015) {
        context.save();
        context.globalCompositeOperation = "lighter";
        const glow = context.createRadialGradient(
          projected.x, projected.y, 0,
          projected.x, projected.y, radius * (3.4 + peak * 2.2)
        );
        glow.addColorStop(0, `rgba(${r},${g},${b},${(0.42 * peak + 0.05).toFixed(3)})`);
        glow.addColorStop(0.35, `rgba(${r},${g},${b},${(0.18 * peak).toFixed(3)})`);
        glow.addColorStop(1, `rgba(${r},${g},${b},0)`);
        context.fillStyle = glow;
        context.beginPath();
        context.arc(projected.x, projected.y, radius * (3.4 + peak * 2.2), 0, Math.PI * 2);
        context.fill();
        context.restore();
      }
      // Tiny fixture reference dot; no beam geometry.
      context.fillStyle = peak > 0.015 ? `rgba(${r},${g},${b},0.9)` : "#343841";
      context.beginPath();
      context.arc(projected.x, projected.y, Math.max(1.6, radius * 0.34), 0, Math.PI * 2);
      context.fill();
      context.strokeStyle = selected ? "#e8862e" : "#565b66";
      context.lineWidth = selected ? 1.5 : 1;
      context.beginPath();
      context.arc(projected.x, projected.y, Math.max(2.4, radius * 0.55), 0, Math.PI * 2);
      context.stroke();
      if (selected) drawFixtureBox(context, points, 0.12);
    } else {
      const projectedPoints = [];
      const step = Math.max(1, Math.floor(points.length / 180));
      for (let index = 0; index < points.length; index += step) {
        const projected = project(points[index]);
        if (!projected) continue;
        const [r, g, b] = rgbAt(slice[0] + index);
        projectedPoints.push({ ...projected, color: [r, g, b], peak: Math.max(r, g, b) });
      }
      if (projectedPoints.length > 1) {
        const drawPass = (widthScale, alpha, additive) => {
          context.save();
          if (additive) context.globalCompositeOperation = "lighter";
          context.lineCap = "round";
          context.lineJoin = "round";
          for (let index = 1; index < projectedPoints.length; index += 1) {
            const a = projectedPoints[index - 1];
            const b = projectedPoints[index];
            const width = clamp(95 / ((a.depth + b.depth) / 2), 1.4, 4.2) * widthScale;
            const [r, g, bch] = b.color;
            context.strokeStyle = alpha == null
              ? `rgb(${r},${g},${bch})`
              : `rgba(${r},${g},${bch},${alpha})`;
            context.lineWidth = width;
            context.beginPath();
            context.moveTo(a.x, a.y);
            context.lineTo(b.x, b.y);
            context.stroke();
          }
          context.restore();
        };
        // Housing first, then two additive bloom passes, then the LED core.
        // Housing stays visible even when the LED output is near black.
        context.save();
        context.lineCap = "round";
        context.strokeStyle = "#2e333d";
        context.lineWidth = 7;
        context.beginPath();
        context.moveTo(projectedPoints[0].x, projectedPoints[0].y);
        for (const point of projectedPoints.slice(1)) context.lineTo(point.x, point.y);
        context.stroke();
        context.strokeStyle = "#14171c";
        context.lineWidth = 4;
        context.stroke();
        context.restore();
        drawPass(3.4, 0.10, true);
        drawPass(1.9, 0.28, true);
        drawPass(1.0, null, false);
        drawPass(0.75, 0.85, true);
        context.save();
        context.globalCompositeOperation = "lighter";
        for (let index = 0; index < projectedPoints.length; index += 4) {
          const point = projectedPoints[index];
          if (point.peak < 150) continue;
          const halo = context.createRadialGradient(point.x, point.y, 0, point.x, point.y, 7);
          halo.addColorStop(0, `rgba(${point.color[0]},${point.color[1]},${point.color[2]},${(point.peak / 255 * 0.35).toFixed(3)})`);
          halo.addColorStop(1, `rgba(${point.color[0]},${point.color[1]},${point.color[2]},0)`);
          context.fillStyle = halo;
          context.beginPath();
          context.arc(point.x, point.y, 7, 0, Math.PI * 2);
          context.fill();
        }
        context.restore();
      }
      if (selected) drawFixtureBox(context, points, 0.1);
    }
    if (selected && selectedFixtures.size === 1) {
      const center = points.reduce((acc, point) => add(acc, point), [0, 0, 0]).map((v) => v / points.length);
      drawAxisHandles(context, center);
    }
  }

  if (selectedFixtures.size > 1) {
    const selectedPoints = [...selectedFixtures].flatMap((id) =>
      fixturePositions(id).map((point) => add(point, overrideOffset(id)))
    );
    if (selectedPoints.length) drawFixtureBox(context, selectedPoints, 0.08);
  }

  if (viewportDrag?.mode === "marquee") {
    const { x0, y0, x1, y1 } = viewportDrag;
    context.strokeStyle = "#e8862e";
    context.lineWidth = 1;
    context.setLineDash([4, 4]);
    context.strokeRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
    context.setLineDash([]);
  }

  drawViewCube(context, basis);
  drawTransformGizmo(context);
  dom.viewportStatus.textContent = `${camera.ortho ? "ORTHO" : "PERSP"} ${cameraViewLabel()}  |  ${state.rig.compiled.voxel_count} VOX  |  ${selectedFixtures.size} SELECTED`;
}

function drawTransformGizmo(context) {
  if (!transformOp) return;
  const anchor = transformOp.anchor;
  const origin = project(anchor);
  if (!origin) return;
  const colors = ["#e05a5a", "#58a66b", "#5e9aa8"];
  const length = 0.85;
  for (let axis = 0; axis < 3; axis += 1) {
    const active = transformOp.axis === axis;
    const plane = transformOp.planeAxis === axis;
    const end = project(add(anchor, scale(AXIS_VECTORS[axis], length)));
    if (!end) continue;
    context.strokeStyle = colors[axis];
    context.globalAlpha = active ? 1 : plane ? 0.55 : 0.35;
    context.lineWidth = active ? 3 : 1.5;
    context.beginPath();
    context.moveTo(origin.x, origin.y);
    context.lineTo(end.x, end.y);
    context.stroke();
    context.globalAlpha = 1;
    context.fillStyle = colors[axis];
    context.font = "bold 9px JetBrains Mono, monospace";
    context.fillText("XYZ"[axis], end.x + 3, end.y + 3);
  }
  context.globalAlpha = 1;
  context.strokeStyle = "#e8862e";
  context.lineWidth = 1;
  context.beginPath();
  context.arc(origin.x, origin.y, 4, 0, Math.PI * 2);
  context.stroke();
}

function drawWorldLine(context, start, end, color, width = 1) {
  const a = project(start);
  const b = project(end);
  if (!a || !b) return;
  context.strokeStyle = color;
  context.lineWidth = width;
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

function drawViewCube(context, basis) {
  viewCubeHits = [];
  const cx = viewportSize.w - 64;
  const cy = 68;
  const size = 23;
  const active = camera.ortho ? cameraViewLabel() : "";
  const faces = [
    { key: "right", label: "RIGHT", normal: [1, 0, 0], axis: 0, opposite: false, corners: [[1, -1, -1], [1, 1, -1], [1, 1, 1], [1, -1, 1]] },
    { key: "left", label: "LEFT", normal: [-1, 0, 0], axis: 0, opposite: true, corners: [[-1, -1, -1], [-1, -1, 1], [-1, 1, 1], [-1, 1, -1]] },
    { key: "back", label: "BACK", normal: [0, 1, 0], axis: 1, opposite: true, corners: [[-1, 1, -1], [-1, 1, 1], [1, 1, 1], [1, 1, -1]] },
    { key: "front", label: "FRONT", normal: [0, -1, 0], axis: 1, opposite: false, corners: [[-1, -1, -1], [1, -1, -1], [1, -1, 1], [-1, -1, 1]] },
    { key: "top", label: "TOP", normal: [0, 0, 1], axis: 2, opposite: false, corners: [[-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]] },
    { key: "bottom", label: "BOTTOM", normal: [0, 0, -1], axis: 2, opposite: true, corners: [[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1]] },
  ];
  const toScreen = (point) => [
    cx + dot(point, basis.right) * size,
    cy - dot(point, basis.up) * size,
  ];

  context.save();
  context.beginPath();
  context.arc(cx, cy, 50, 0, Math.PI * 2);
  context.fillStyle = "rgba(12, 13, 15, 0.88)";
  context.fill();
  context.strokeStyle = "#2b2e34";
  context.lineWidth = 1;
  context.stroke();

  // Painter's order: faces pointing away from the camera first.
  const visible = faces
    .map((face) => ({
      face,
      depth: dot(face.normal, basis.forward),
      points: face.corners.map(toScreen),
    }))
    .filter((entry) => entry.depth < -0.08)
    .sort((a, b) => b.depth - a.depth);

  const labelBoxes = [];
  for (const { face, points, depth } of visible) {
    const hovered = viewCubeHover === face.key;
    const selected = active === face.label;
    context.beginPath();
    context.moveTo(points[0][0], points[0][1]);
    for (let index = 1; index < points.length; index += 1) {
      context.lineTo(points[index][0], points[index][1]);
    }
    context.closePath();
    const lit = face.normal[2] > 0 ? 8 : 0;
    context.fillStyle = selected
      ? "#3a2a18"
      : hovered
        ? "#3d4149"
        : `rgb(${30 + lit}, ${33 + lit}, ${38 + lit})`;
    context.fill();
    context.strokeStyle = selected ? "#e8862e" : hovered ? "#d4d7dd" : "#4a4e56";
    context.stroke();

    // Label only readable faces and skip collisions, so an isometric view does
    // not stack LEFT/FRONT text into the same pixels.
    if (depth < -0.35) {
      const area = Math.abs(
        points.reduce((sum, [x, y], index) => {
          const [nextX, nextY] = points[(index + 1) % points.length];
          return sum + x * nextY - nextX * y;
        }, 0) / 2
      );
      const centerX = points.reduce((sum, [x]) => sum + x, 0) / points.length;
      const centerY = points.reduce((sum, [, y]) => sum + y, 0) / points.length;
      context.font = "7px JetBrains Mono, monospace";
      const width = context.measureText(face.label).width;
      const box = { x0: centerX - width / 2 - 2, y0: centerY - 6, x1: centerX + width / 2 + 2, y1: centerY + 3 };
      const collides = labelBoxes.some((other) =>
        box.x0 < other.x1 && box.x1 > other.x0 && box.y0 < other.y1 && box.y1 > other.y0
      );
      if (area > 220 && !collides) {
        labelBoxes.push(box);
        context.fillStyle = selected ? "#f6c089" : "#b9bcc3";
        context.textAlign = "center";
        context.fillText(face.label, centerX, centerY + 2.5);
      }
    }
    viewCubeHits.push({ key: face.key, axis: face.axis, opposite: face.opposite, points });
  }

  const pillY = cy + 58;
  const pillHovered = viewCubeHover === "toggle";
  context.fillStyle = pillHovered ? "#34383f" : "#17191c";
  context.strokeStyle = pillHovered ? "#e8862e" : camera.ortho ? "#7a5a35" : "#33373e";
  context.beginPath();
  context.rect(cx - 24, pillY - 7, 48, 14);
  context.fill();
  context.stroke();
  context.fillStyle = camera.ortho ? "#e8862e" : "#9da1aa";
  context.font = "8px JetBrains Mono, monospace";
  context.textAlign = "center";
  context.fillText(camera.ortho ? "ORTHO" : "PERSP", cx, pillY + 3);
  context.textAlign = "left";
  viewCubeHits.push({ toggle: true, x: cx, y: pillY });
  context.restore();
}

function pointInPolygon(x, y, points) {
  let inside = false;
  for (let index = 0, previous = points.length - 1; index < points.length; previous = index, index += 1) {
    const [xi, yi] = points[index];
    const [xj, yj] = points[previous];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function viewCubeAt(x, y) {
  for (const hit of viewCubeHits) {
    if (hit.toggle) {
      if (Math.abs(x - hit.x) <= 26 && Math.abs(y - hit.y) <= 9) return hit;
    } else if (pointInPolygon(x, y, hit.points)) {
      return hit;
    }
  }
  return null;
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

function filterPathInfo(param) {
  if (!String(param).startsWith("filter:")) return null;
  const parts = String(param).split(":");
  return parts.length >= 3 ? { uid: parts[1], key: parts.slice(2).join(":") } : null;
}

function filterSpecForPath(track, param) {
  const parsed = filterPathInfo(param);
  if (!parsed) return null;
  for (const clip of track?.clips || []) {
    const filter = (clip.filters || []).find((item) => item.uid === parsed.uid);
    if (!filter) continue;
    const info = state?.filters?.find((item) => item.id === filter.type);
    return { filter, info, spec: info?.params?.find((item) => item.key === parsed.key), key: parsed.key };
  }
  return null;
}

function laneParamRange(track, param) {
  const found = filterSpecForPath(track, param);
  if (!found?.spec) return [0, 1];
  return [Number(found.spec.min ?? 0), Number(found.spec.max ?? 1)];
}

function laneParamNorm(track, param, value) {
  const [lo, hi] = laneParamRange(track, param);
  return clamp((Number(value) - lo) / Math.max(1e-9, hi - lo), 0, 1);
}

function cssColour(value, alpha = 1) {
  if (!Array.isArray(value) || value.length < 3) return `rgba(128, 130, 136, ${alpha})`;
  const [r, g, b] = value.slice(0, 3).map((part) => Math.round(clamp(Number(part) || 0, 0, 255)));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// Colour keyframes have no scalar range, so their lane is a gradient strip with
// swatch diamonds. The strip interpolates in sRGB; the compositor still blends
// the actual colour keyframes in Oklab.
function drawColourLane(context, clip, keys, x0, x1, laneY, laneH) {
  const stripH = 6;
  const stripY = laneY + Math.max(3, Math.round((laneH - stripH) / 2));
  const span = Math.max(1, x1 - x0);
  const gradient = context.createLinearGradient(x0, 0, x0 + span, 0);
  for (const keyframe of keys) {
    const offset = clamp((timeToX(clip.start_s + keyframe.t_s) - x0) / span, 0, 1);
    gradient.addColorStop(offset, cssColour(keyframe.value));
  }
  context.fillStyle = gradient;
  context.fillRect(x0, stripY, span, stripH);
  context.fillStyle = "#101216";
  context.fillRect(x0, stripY + stripH, span, 1);
  for (const keyframe of keys) {
    const x = timeToX(clip.start_s + keyframe.t_s);
    if (x < view.labelW || x > x1) continue;
    context.fillStyle = cssColour(keyframe.value);
    context.strokeStyle = "#101216";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(x, stripY - 2);
    context.lineTo(x + 4, stripY + 3);
    context.lineTo(x, stripY + 8);
    context.lineTo(x - 4, stripY + 3);
    context.closePath();
    context.fill();
    context.stroke();
  }
}

function laneParamLabel(track, param) {
  const found = filterSpecForPath(track, param);
  if (found) return `${found.info?.name || found.filter.type} / ${found.spec?.label || found.key}`;
  for (const clip of track.clips || []) {
    const spec = (presetById(clip.preset)?.params || []).find((item) => item.key === param);
    if (spec) return spec.label;
  }
  return param;
}

function paramLanes(track) {
  const params = new Set();
  if (!track) return [];
  for (const clip of track.clips || []) {
    for (const keyframe of clip.keyframes || []) params.add(keyframe.param);
    for (const param of Object.keys(clip.expressions || {})) params.add(param);
  }
  const ordered = [...params];
  const focus = laneFocus.get(track.id);
  if (focus && ordered.includes(focus)) {
    ordered.splice(ordered.indexOf(focus), 1);
    ordered.unshift(focus);
  }
  return ordered.slice(0, 6).map((param) => ({ param, expression: null }));
}

function revealParamLane(clip, param) {
  const found = findClip(clip.id);
  if (!found) return;
  openLanes.add(clip.id);
  openTracks.add(found.track.id);
  laneFocus.set(found.track.id, param);
  saveLayout();
  setTimelineMode("layers");
  renderInspector();
  requestDraw();
  const animated = (found.track.clips || []).some((item) =>
    (item.keyframes || []).some((keyframe) => keyframe.param === param) ||
    Object.hasOwn(item.expressions || {}, param)
  );
  if (!animated) toast(`Noch keine Keyframes für ${param}.`, "info", 1800);
}

function revealAnimatedParams(collapse) {
  if (collapse) {
    openTracks.clear();
    openLanes.clear();
    laneFocus.clear();
    saveLayout();
    requestDraw();
    return;
  }
  const found = findClip();
  const track = found?.track || findTrack();
  if (!track) {
    toast("Keine Spur ausgewählt.", "info");
    return;
  }
  openTracks.add(track.id);
  if (found) openLanes.add(found.clip.id);
  saveLayout();
  setTimelineMode("layers");
  renderInspector();
  requestDraw();
}

function buildTimelineRows() {
  const doc = showDoc();
  if (!doc) {
    timelineRows = [];
    return [];
  }
  const tracks = doc.tracks || [];
  const globals = tracks.filter((track) => track.kind === "global");
  const layers = tracks.filter((track) => track.kind !== "global");
  const emitted = new Set();
  const ordered = [];
  const emit = (track, depth = 0) => {
    if (emitted.has(track.id)) return;
    if (track.parent_id) {
      const parent = layers.find((item) => item.id === track.parent_id);
      if (parent) emit(parent, Math.max(0, depth - 1));
    }
    emitted.add(track.id);
    ordered.push({ track, depth: track.kind === "sub" ? 1 : depth });
    for (const child of layers) {
      if (child.parent_id === track.id && !emitted.has(child.id)) {
        emitted.add(child.id);
        ordered.push({ track: child, depth: 1 });
      }
    }
  };
  for (let index = layers.length - 1; index >= 0; index -= 1) emit(layers[index], 0);
  const visual = [
    ...globals.map((track) => ({ track, depth: 0 })),
    ...ordered,
  ];
  const selected = findClip();
  let y = view.rulerH + view.audioH + 4 - view.scrollY;
  const rows = [];
  for (const entry of visual) {
    const track = entry.track;
    let lanes = [];
    if (openTracks.has(track.id) || (selected && selected.track.id === track.id && openLanes.has(selected.clip.id))) {
      lanes = paramLanes(track);
    }
    const baseH = clamp(Number(track.height_px) || 36, 26, 240);
    const laneH = 18;
    const h = baseH + lanes.length * laneH;
    rows.push({
      track,
      depth: entry.depth,
      y,
      h,
      baseH,
      laneH,
      lanes,
      laneY: y + baseH,
      expanded: openTracks.has(track.id) || (selected && selected.track.id === track.id && openLanes.has(selected.clip.id)),
    });
    y += h + 2;
  }
  timelineRows = rows;
  const contentHeight = rows.reduce((sum, row) => sum + row.h + 2, 0);
  const viewportH = Math.max(1, timelineSize.h - (view.rulerH + view.audioH) - 6);
  const maxScroll = Math.max(0, contentHeight - viewportH);
  if (view.scrollY > maxScroll) view.scrollY = maxScroll;
  if (view.scrollY < 0) view.scrollY = 0;
  return rows;
}

function timelineContentHeight() {
  const rows = buildTimelineRows();
  return rows.reduce((sum, row) => sum + row.h + 2, 0);
}

function rowAtY(y) {
  const rows = buildTimelineRows();
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    if (y >= row.y && y < row.y + row.h) return { row, index };
  }
  return null;
}

function clipRect(rowIndex, clip) {
  const row = timelineRows[rowIndex];
  if (!row) return { x0: 0, x1: 0, y: 0, h: 1 };
  return {
    x0: timeToX(clip.start_s),
    x1: timeToX(clip.end_s),
    y: row.y + 3,
    h: Math.max(1, row.baseH - 6),
  };
}

function formatTimelineLabel(time) {
  return timecodeText(time);
}

function analysisHasAudio() {
  const length = Number(blueprint?.waveform?.length || 0);
  const audioDuration = Number(audioAnalysis?.duration || blueprint?.duration_s || 0);
  return Boolean((audioAnalysis || length > 0) && audioDuration > 0);
}

function saveAnalysisCurves() {
  try {
    localStorage.setItem(ANALYSIS_STORAGE_KEY, JSON.stringify([...analysisCurves]));
  } catch {
    // Persistence is a convenience, not a requirement for analysis.
  }
}

function renderAnalysisLegend() {
  if (!dom.analysisLegend) return;
  const all = el("button", {
    class: "button small", type: "button", text: "All",
    onclick: () => {
      analysisCurves = new Set(ANALYSIS_CURVES.map((curve) => curve.key));
      saveAnalysisCurves();
      renderAnalysisLegend();
      requestDraw();
    },
  });
  const none = el("button", {
    class: "button small", type: "button", text: "None",
    onclick: () => {
      analysisCurves.clear();
      saveAnalysisCurves();
      renderAnalysisLegend();
      requestDraw();
    },
  });
  const chips = ANALYSIS_CURVES.map((curve) => {
    const input = el("input", { type: "checkbox" });
    input.checked = analysisCurves.has(curve.key);
    input.addEventListener("change", () => {
      if (input.checked) analysisCurves.add(curve.key);
      else analysisCurves.delete(curve.key);
      saveAnalysisCurves();
      requestDraw();
    });
    return el(
      "label",
      { class: "analysis-curve" },
      input,
      el("span", { class: "analysis-swatch", style: `background:${curve.color}` }),
      el("span", { text: curve.label })
    );
  });
  dom.analysisLegend.replaceChildren(
    el("div", { class: "analysis-actions" }, all, none),
    ...chips,
    el("span", { id: "analysis-values", class: "mono muted analysis-values", text: "values at playhead" })
  );
}

function setTimelineMode(mode) {
  timelineMode = mode === "analysis" ? "analysis" : "layers";
  dom.timelineSection?.classList.toggle("analysis-mode", timelineMode === "analysis");
  dom.timelineLayers?.classList.toggle("active", timelineMode === "layers");
  dom.timelineAnalysis?.classList.toggle("active", timelineMode === "analysis");
  dom.analysisLegend?.classList.toggle("hidden", timelineMode !== "analysis");
  if (timelineMode === "analysis") renderAnalysisLegend();
  analysisHover = null;
  resizeAll();
  requestDraw();
}

function drawTimelineAnalysis(context, w, h, doc) {
  const top = view.rulerH;
  const spectrumH = 54;
  const bottom = Math.max(top + 40, h - spectrumH - 6);
  const left = view.labelW;
  context.fillStyle = "#101216";
  context.fillRect(0, 0, w, h);
  context.fillStyle = "#141518";
  context.fillRect(0, 0, left, h);
  context.fillStyle = "#1d1f23";
  context.fillRect(left, 0, w - left, top);

  // Beat grid.
  let step = gridSeconds();
  while ((step / Math.max(0.001, view.end - view.start)) * (w - left) < 6) step *= 2;
  const offset = Number(doc.timing?.beat_offset_s || 0);
  for (let time = offset + Math.floor((view.start - offset) / step) * step; time <= view.end; time += step) {
    if (time < 0) continue;
    const x = timeToX(time);
    context.strokeStyle = Math.abs(time / (beatSeconds() * 4) - Math.round(time / (beatSeconds() * 4))) < 1e-6 ? "#2b2e34" : "#202329";
    context.beginPath();
    context.moveTo(x + 0.5, top);
    context.lineTo(x + 0.5, bottom);
    context.stroke();
  }

  drawStructureBand(context, w, doc);

  for (const window of blueprint?.drop_windows || []) {
    const x0 = Math.max(left, timeToX(Number(window.start)));
    const x1 = Math.min(w, timeToX(Number(window.end)));
    if (x1 > x0) {
      context.fillStyle = "rgba(208, 107, 74, 0.10)";
      context.fillRect(x0, top, x1 - x0, bottom - top);
    }
  }
  for (const marker of doc.timing?.markers || []) {
    const x = timeToX(marker.t_s);
    if (x < left || x > w) continue;
    context.strokeStyle = marker.kind === "drop" ? "#d06b4a" : "#6f7480";
    context.setLineDash([3, 3]);
    context.beginPath();
    context.moveTo(x + 0.5, view.rulerH);
    context.lineTo(x + 0.5, bottom);
    context.stroke();
    context.setLineDash([]);
  }

  context.fillStyle = "#9da1aa";
  context.font = "9px JetBrains Mono, monospace";
  context.fillText("ANALYSIS · WAVEFORM + ENVELOPE BASIS · seek + snap", 8, 16);

  if (!analysisHasAudio()) {
    context.fillStyle = "#7c8089";
    context.font = "12px JetBrains Mono, monospace";
    context.fillText("NO AUDIO — attach a WAV, MP3 or video to analyse", left + 24, top + (bottom - top) / 2);
    const values = dom.analysisLegend?.querySelector(".analysis-values");
    if (values) values.textContent = "NO AUDIO";
    drawAnalysisPlayhead(context, w, h, bottom);
    return;
  }

  const source = audioAnalysis;
  const audioDuration = Number(source?.duration || blueprint?.duration_s || duration());
  const mid = top + (bottom - top) / 2;
  const span = (bottom - top) * 0.46;
  if (source && audioDuration > 0) {
    for (let x = left; x < w; x += 2) {
      const time = xToTime(x);
      if (time < 0 || time > audioDuration) continue;
      const index = Math.min(source.buckets - 1, Math.max(0, Math.floor((time / audioDuration) * source.buckets)));
      const lo = source.min[index];
      const hi = source.max[index];
      context.fillStyle = "rgba(94, 154, 168, 0.30)";
      context.fillRect(x, mid - hi * span, 1, Math.max(1, (hi - lo) * span));
      context.fillStyle = "rgba(232, 134, 46, 0.55)";
      const rms = source.rms[index];
      context.fillRect(x, mid - rms * span, 1, Math.max(1, rms * span * 2));
    }
  } else {
    const wave = blueprint?.waveform || [];
    for (let x = left; x < w; x += 2) {
      const time = xToTime(x);
      if (time < 0 || time > audioDuration) continue;
      const index = Math.min(wave.length - 1, Math.floor((time / audioDuration) * wave.length));
      const amp = wave[index] * span;
      context.fillStyle = "rgba(94, 154, 168, 0.32)";
      context.fillRect(x, mid - amp, 1, Math.max(1, amp * 2));
    }
  }

  context.save();
  context.beginPath();
  context.rect(left, top, w - left, bottom - top);
  context.clip();
  const samples = [];
  for (let x = left; x <= w; x += 2) {
    samples.push({ x, env: envelopeAt(xToTime(x)) });
  }
  for (const curve of ANALYSIS_CURVES) {
    if (!analysisCurves.has(curve.key)) continue;
    context.strokeStyle = curve.color;
    context.lineWidth = curve.key === "inDrop" ? 1 : 1.25;
    if (curve.key === "beatsToDrop" || curve.key === "inDrop") context.setLineDash([4, 3]);
    else context.setLineDash([]);
    context.beginPath();
    samples.forEach((sample, index) => {
      const value = clamp(Number(sample.env[curve.key]) || 0, 0, 1);
      const y = bottom - value * (bottom - top);
      if (index === 0) context.moveTo(sample.x, y);
      else context.lineTo(sample.x, y);
    });
    context.stroke();
  }
  context.setLineDash([]);
  context.restore();

  // Spectrum strip.
  const spectrumTop = bottom + 7;
  const spectrum = ["bass", "mid", "high", "energy"];
  const envNow = envelopeAt(currentTime());
  context.fillStyle = "#0c0d10";
  context.fillRect(left, spectrumTop, w - left, spectrumH - 6);
  context.fillStyle = "#4a4e56";
  context.font = "8px JetBrains Mono, monospace";
  context.fillText("SPECTRUM", 8, spectrumTop + 10);
  const barW = Math.max(18, Math.min(48, (w - left - 30) / spectrum.length - 6));
  spectrum.forEach((key, index) => {
    const value = clamp(Number(envNow[key]) || 0, 0, 1);
    const x = left + 10 + index * (barW + 18);
    const barH = spectrumH - 22;
    context.fillStyle = "#25282d";
    context.fillRect(x, spectrumTop + 12, barW, barH);
    context.fillStyle = "#5e9aa8";
    context.fillRect(x, spectrumTop + 12 + barH * (1 - value), barW, barH * value);
    context.fillStyle = "#b3b6bd";
    context.font = "8px JetBrains Mono, monospace";
    context.fillText(`${key.toUpperCase()} ${value.toFixed(2)}`, x, spectrumTop + barH + 20);
  });

  const bases = ANALYSIS_CURVES.filter((curve) => analysisCurves.has(curve.key));
  const dominant = bases.reduce(
    (best, curve) => {
      const value = clamp(Number(envNow[curve.key]) || 0, 0, 1);
      return value > best.value ? { label: curve.label, value } : best;
    },
    { label: "—", value: 0 }
  );
  context.fillStyle = "#e8862e";
  context.font = "9px JetBrains Mono, monospace";
  context.fillText(`DOMINANCE · ${dominant.label.toUpperCase()} ${dominant.value.toFixed(2)}`, left + 10, h - 4);
  const values = dom.analysisLegend?.querySelector(".analysis-values");
  if (values) {
    values.textContent = ANALYSIS_CURVES
      .filter((curve) => analysisCurves.has(curve.key))
      .map((curve) => `${curve.label} ${(Number(envNow[curve.key]) || 0).toFixed(2)}`)
      .join("  ");
  }

  if (analysisHover && analysisHover.x >= left) {
    const hoverX = timeToX(analysisHover.time);
    context.strokeStyle = "#e8862e88";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(hoverX + 0.5, top);
    context.lineTo(hoverX + 0.5, bottom);
    context.stroke();
    const hoverEnv = envelopeAt(analysisHover.time);
    const lines = ANALYSIS_CURVES
      .filter((curve) => analysisCurves.has(curve.key))
      .map((curve) => `${curve.label.padEnd(12, " ")} ${(Number(hoverEnv[curve.key]) || 0).toFixed(3)}`);
    const boxW = 132;
    const boxH = 14 + lines.length * 11;
    const boxX = hoverX + 8 + boxW > w ? hoverX - boxW - 8 : hoverX + 8;
    const boxY = top + 8;
    context.fillStyle = "#111214ee";
    context.fillRect(boxX, boxY, boxW, boxH);
    context.strokeStyle = "#3a3e46";
    context.strokeRect(boxX + 0.5, boxY + 0.5, boxW - 1, boxH - 1);
    context.fillStyle = "#e8862e";
    context.font = "8px JetBrains Mono, monospace";
    context.fillText(timecodeText(analysisHover.time), boxX + 5, boxY + 11);
    context.fillStyle = "#b3b6bd";
    lines.forEach((line, index) => {
      context.fillText(line, boxX + 5, boxY + 23 + index * 11);
    });
  }

  drawAnalysisPlayhead(context, w, h, bottom);
}

function drawAnalysisPlayhead(context, w, h, bottom) {
  const playhead = timeToX(currentTime());
  if (playhead < view.labelW || playhead > w) return;
  context.strokeStyle = "#e8862e";
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(playhead + 0.5, 0);
  context.lineTo(playhead + 0.5, bottom);
  context.stroke();
  context.fillStyle = "#e8862e";
  context.beginPath();
  context.moveTo(playhead - 4, 0);
  context.lineTo(playhead + 5, 0);
  context.lineTo(playhead + 0.5, 7);
  context.closePath();
  context.fill();
  context.strokeStyle = "#3a3e46";
  context.beginPath();
  context.moveTo(view.labelW + 0.5, 0);
  context.lineTo(view.labelW + 0.5, h);
  context.stroke();
}


function drawStructureBand(context, w, doc) {
  const top = RULER_TIME_H;
  const bandH = view.rulerH - top;
  context.fillStyle = "#101216";
  context.fillRect(view.labelW, top, w - view.labelW, bandH);
  context.strokeStyle = "#26292f";
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(0, view.rulerH + 0.5);
  context.lineTo(w, view.rulerH + 0.5);
  context.stroke();

  context.fillStyle = "#4a4e56";
  context.font = "8px JetBrains Mono, monospace";
  context.fillText("STRUCTURE", 8, top + 13);

  // Section strip: colour tells the part of the song at a glance.
  for (const section of blueprint?.sections || []) {
    const x0 = Math.max(view.labelW, timeToX(Number(section.start)));
    const x1 = Math.min(w, timeToX(Number(section.end)));
    if (x1 <= x0) continue;
    const color = SECTION_COLORS[section.label] || "#6f7480";
    context.fillStyle = `${color}40`;
    context.fillRect(x0, top + 2, x1 - x0, bandH - 4);
    context.fillStyle = color;
    context.fillRect(x0, top + 2, x1 - x0, 2);
  }

  // Cue markers sit on the lower half. Labels yield to each other instead of
  // stacking; the tick still marks every cue.
  const markers = [...(doc.timing?.markers || [])].sort((a, b) => a.t_s - b.t_s);
  let lastLabelRight = view.labelW - 2;
  context.font = "8px Inter, sans-serif";
  for (const marker of markers) {
    const x = timeToX(Number(marker.t_s));
    if (x < view.labelW || x > w) continue;
    const isDrop = marker.kind === "drop";
    const color = isDrop ? "#d06b4a" : "#8b8f98";
    context.strokeStyle = color;
    context.beginPath();
    context.moveTo(x + 0.5, top + 1);
    context.lineTo(x + 0.5, view.rulerH - 1);
    context.stroke();
    const label = String(marker.name || "");
    const width = context.measureText(label).width;
    if (label && x + 3 > lastLabelRight + 4 && x + width + 6 < w) {
      context.fillStyle = isDrop ? "#e59a7d" : "#a9adb5";
      context.fillText(label, x + 3, view.rulerH - 4);
      lastLabelRight = x + width + 8;
    }
  }
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
  if (timelineMode === "analysis") {
    drawTimelineAnalysis(context, w, h, doc);
    return;
  }
  const rows = buildTimelineRows();
  const laneTop = view.rulerH;
  const laneBottom = h;
  const audioBottom = laneTop + view.audioH;

  context.fillStyle = "#141518";
  context.fillRect(0, 0, view.labelW, h);
  context.fillStyle = "#1d1f23";
  context.fillRect(view.labelW, 0, w - view.labelW, view.rulerH);

  // Beat grid. Coarsen until ticks are readable.
  let step = gridSeconds();
  while ((step / Math.max(0.001, view.end - view.start)) * (w - view.labelW) < 6) step *= 2;
  const offset = Number(doc.timing?.beat_offset_s || 0);
  let lastLabelX = -999;
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
    if (Math.round((time - offset) / step) % 4 === 0 && x - lastLabelX > 82) {
      lastLabelX = x;
      context.fillStyle = "#7c8089";
      context.font = "9px JetBrains Mono, monospace";
      context.fillText(formatTimelineLabel(time), x + 3, 11);
    }
  }

  drawStructureBand(context, w, doc);
  drawAudioLane(context, w);

  // Track rows must never paint over the ruler or audio lane while scrolling.
  context.save();
  context.beginPath();
  context.rect(0, audioBottom, w, Math.max(0, h - audioBottom));
  context.clip();

  // Highlight the layer that would receive a dragged clip.
  const preview = timelineDrag?.preview;
  const targetRowIndex = preview?.trackId
    ? rows.findIndex((row) => row.track.id === preview.trackId)
    : -1;
  if (timelineDrag?.mode === "move" && targetRowIndex >= 0) {
    const target = rows[targetRowIndex];
    context.fillStyle = "rgba(232,134,46,0.07)";
    context.fillRect(view.labelW, target.y - 1, w - view.labelW, target.h + 2);
    context.strokeStyle = "#e8862e";
    context.setLineDash([4, 3]);
    context.strokeRect(view.labelW + 0.5, target.y - 0.5, w - view.labelW - 1, target.h + 1);
    context.setLineDash([]);
  }

  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    const track = row.track;
    const y = row.y;
    const baseH = row.baseH;
    context.fillStyle = index % 2 === 0 ? "#17181b" : "#191b1e";
    context.fillRect(view.labelW, y, w - view.labelW, row.h);
    context.strokeStyle = "#2b2e34";
    context.beginPath();
    context.moveTo(0, y + row.h + 0.5);
    context.lineTo(w, y + row.h + 0.5);
    context.stroke();

    // Header
    const selectedTrack = selectedTrackId === track.id && !selectedClipId;
    context.fillStyle = selectedTrack ? "#211a13" : "#141518";
    context.fillRect(0, y, view.labelW, row.h);
    context.fillStyle = trackColor(track.id);
    context.fillRect(0, y, 3, row.h);
    const indent = row.depth * 10;
    if (track.kind === "global") {
      context.fillStyle = "#c9a34a";
      context.font = "8px JetBrains Mono, monospace";
      context.fillText("GLOBAL", 8, y + 10);
    } else if (track.kind === "sub") {
      context.fillStyle = "#9b7fb5";
      context.font = "8px JetBrains Mono, monospace";
      context.fillText("SUB", 8 + indent, y + 10);
    }
    // Expand chevron (keyframe lanes)
    context.fillStyle = row.expanded ? "#e8862e" : "#7c8089";
    context.font = "10px JetBrains Mono, monospace";
    context.fillText(row.expanded ? "▾" : "▸", 6 + indent, y + 22);
    context.fillStyle = track.muted ? "#4a4e56" : "#e8e9ec";
    context.font = "11px Inter, sans-serif";
    context.fillText(track.name, 18 + indent, y + 22, view.labelW - 76);
    // Mute / solo hit buttons on the right of the header.
    const muteX = view.labelW - 38;
    context.fillStyle = track.muted ? "#c45a52" : "#25282d";
    context.fillRect(muteX, y + 5, 16, 15);
    context.fillStyle = track.muted ? "#fff" : "#9da1aa";
    context.font = "bold 9px JetBrains Mono, monospace";
    context.fillText("M", muteX + 5, y + 16);
    context.fillStyle = track.solo ? "#e8862e" : "#25282d";
    context.fillRect(muteX + 18, y + 5, 16, 15);
    context.fillStyle = track.solo ? "#111" : "#9da1aa";
    context.fillText("S", muteX + 23, y + 16);
    if (track.clipped_to_below) {
      context.fillStyle = "#e8862e";
      context.font = "12px JetBrains Mono, monospace";
      context.fillText("↴", 38 + indent, y + 32);
    }
    context.fillStyle = "#4a4e56";
    context.font = "8px JetBrains Mono, monospace";
    context.fillText(`${String(track.blend_mode || "normal").toUpperCase()} · ${track.target_group}`, 10 + indent, y + baseH - 5, view.labelW - 82);

    // Header bottom resize grip
    context.fillStyle = heightDrag?.trackId === track.id ? "#e8862e" : "#2b2e34";
    context.fillRect(0, y + row.h - 2, view.labelW, 2);

    const color = trackColor(track.id);
    for (const clip of track.clips) {
      const activePreview = timelineDrag?.preview;
      const isSourcePreview = activePreview?.clipId === clip.id;
      // When dragging vertically the ghost is drawn on the destination row.
      if (isSourcePreview && targetRowIndex >= 0 && targetRowIndex !== index) continue;
      const effective = isSourcePreview ? { ...clip, ...activePreview } : clip;
      const rect = clipRect(index, effective);
      const x0 = Math.max(view.labelW, rect.x0);
      const x1 = Math.min(w, rect.x1);
      if (x1 <= view.labelW || x0 >= w) continue;
      const dragging = isSourcePreview;
      context.fillStyle = dragging ? `${color}48` : `${color}26`;
      context.fillRect(x0, rect.y, x1 - x0, rect.h);
      context.fillStyle = color;
      context.fillRect(x0, rect.y, x1 - x0, 2);
      if (clip.id === selectedClipId) {
        context.fillStyle = "#e8862e";
        context.fillRect(x0, rect.y, 2, rect.h);
        context.fillRect(x1 - 2, rect.y, 2, rect.h);
        context.fillRect(x0, rect.y + rect.h - 2, x1 - x0, 2);
      }
      context.strokeStyle = dragging ? "#ffffff" : clip.id === selectedClipId ? "#e8862e" : "#2b2e34";
      context.lineWidth = dragging ? 1.5 : 1;
      if (dragging) context.setLineDash([4, 3]);
      context.strokeRect(x0 + 0.5, rect.y + 0.5, Math.max(0, x1 - x0 - 1), rect.h - 1);
      context.setLineDash([]);

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
      const label = clipDisplayName(clip);
      if (x1 - x0 > 26) {
        context.save();
        context.beginPath();
        context.rect(x0 + 3, rect.y + 2, x1 - x0 - 6, rect.h - 2);
        context.clip();
        context.fillText(label, x0 + 4, rect.y + 13);
        if (clip.expressions && Object.keys(clip.expressions).length) {
          context.fillStyle = "#e8862e";
          context.font = "9px JetBrains Mono, monospace";
          context.fillText("f(x)", x0 + 4, rect.y + 24);
        }
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

    // Parameter lanes under the selected/expanded track.
    for (let lane = 0; lane < row.lanes.length; lane += 1) {
      const info = row.lanes[lane];
      const laneY = row.laneY + lane * row.laneH;
      context.fillStyle = lane % 2 === 0 ? "#131417" : "#151619";
      context.fillRect(view.labelW, laneY, w - view.labelW, row.laneH);
      context.fillStyle = "#7c8089";
      context.font = "8px JetBrains Mono, monospace";
      context.fillText(`◇ ${laneParamLabel(track, info.param)}`, view.labelW + 4, laneY + 11, view.labelW - 8);
      for (const clip of track.clips) {
        const keys = (clip.keyframes || [])
          .filter((keyframe) => keyframe.param === info.param && typeof keyframe.value === "number")
          .sort((a, b) => a.t_s - b.t_s);
        const colourKeys = (clip.keyframes || [])
          .filter((keyframe) => keyframe.param === info.param && Array.isArray(keyframe.value))
          .sort((a, b) => a.t_s - b.t_s);
        const expression = clip.expressions?.[info.param];
        if (!keys.length && !colourKeys.length && !expression) continue;
        const cx0 = Math.max(view.labelW, timeToX(clip.start_s));
        const cx1 = Math.min(w, timeToX(clip.end_s));
        if (cx1 <= view.labelW || cx0 >= w) continue;
        if (colourKeys.length) {
          drawColourLane(context, clip, colourKeys, cx0, cx1, laneY, row.laneH);
          continue;
        }
        if (expression) {
          context.strokeStyle = "#e8862e99";
          context.beginPath();
          for (let px = cx0; px <= cx1; px += 2) {
            const previewT = xToTime(px);
            let value = Number(clip.params?.[info.param] ?? clip.opacity ?? 0.5);
            try {
              const env = envelopeAt(previewT);
              value = evalUiExpression(expression, {
                ...env,
                t: previewT,
                local_t: previewT - clip.start_s,
                val: value,
                beat: env.beatPos,
              });
            } catch {
              value = Number(clip.params?.[info.param] ?? 0.5);
            }
            const py = laneY + row.laneH - 3 - laneParamNorm(track, info.param, value) * (row.laneH - 5);
            if (px === cx0) context.moveTo(px, py);
            else context.lineTo(px, py);
          }
          context.stroke();
        }
        if (keys.length > 1) {
          context.strokeStyle = "rgba(232,134,46,0.55)";
          context.lineWidth = 1.25;
          context.beginPath();
          keys.forEach((keyframe, keyIndex) => {
            const x = timeToX(clip.start_s + keyframe.t_s);
            const py = laneY + row.laneH - 3 - laneParamNorm(track, info.param, keyframe.value) * (row.laneH - 5);
            if (keyIndex === 0) context.moveTo(x, py);
            else context.lineTo(x, py);
          });
          context.stroke();
        }
        for (const keyframe of keys) {
          const x = timeToX(clip.start_s + keyframe.t_s);
          if (x < view.labelW || x > w) continue;
          const py = laneY + row.laneH - 3 - laneParamNorm(track, info.param, keyframe.value) * (row.laneH - 5);
          context.fillStyle = "#e8862e";
          context.beginPath();
          context.moveTo(x, py - 3);
          context.lineTo(x + 3, py);
          context.lineTo(x, py + 3);
          context.lineTo(x - 3, py);
          context.closePath();
          context.fill();
        }
      }
    }
  }
  context.restore();

  // Ghost clip on the destination row while dragging vertically.
  if (timelineDrag?.mode === "move" && preview && targetRowIndex >= 0) {
    const source = findClip(preview.clipId)?.clip;
    if (source) {
      const ghost = { ...source, ...preview };
      const target = rows[targetRowIndex];
      const gx0 = Math.max(view.labelW, timeToX(ghost.start_s));
      const gx1 = Math.min(w, timeToX(ghost.end_s));
      if (gx1 > gx0) {
        context.save();
        context.globalAlpha = 0.75;
        context.fillStyle = `${trackColor(target.track.id)}40`;
        context.fillRect(gx0, target.y + 3, gx1 - gx0, target.baseH - 6);
        context.strokeStyle = "#ffffff";
        context.setLineDash([5, 3]);
        context.strokeRect(gx0 + 0.5, target.y + 3.5, gx1 - gx0 - 1, target.baseH - 7);
        context.setLineDash([]);
        context.fillStyle = "#ffffff";
        context.font = "10px Inter, sans-serif";
        if (gx1 - gx0 > 34) context.fillText(ghost.name || ghost.preset, gx0 + 4, target.y + 16);
        context.restore();
        // Time readout above the ghost.
        context.fillStyle = "#e8862e";
        context.font = "9px JetBrains Mono, monospace";
        context.fillText(`${ghost.start_s.toFixed(2)}s`, gx0 + 2, target.y - 4);
      }
    }
  }

  // Section markers and drop windows.
  for (const window of blueprint?.drop_windows || []) {
    const x0 = Math.max(view.labelW, timeToX(Number(window.start)));
    const x1 = Math.min(w, timeToX(Number(window.end)));
    if (x1 > x0) {
      context.fillStyle = "rgba(208, 107, 74, 0.06)";
      context.fillRect(x0, laneTop, x1 - x0, laneBottom - laneTop);
    }
  }
  for (const marker of doc.timing?.markers || []) {
    const x = timeToX(marker.t_s);
    if (x < view.labelW || x > w) continue;
    context.strokeStyle = marker.kind === "drop" ? "#d06b4a" : "#6f7480";
    context.setLineDash([3, 3]);
    context.beginPath();
    context.moveTo(x + 0.5, view.rulerH);
    context.lineTo(x + 0.5, laneBottom);
    context.stroke();
    context.setLineDash([]);
  }

  // Vertical scrollbar for the track area.
  const scroll = timelineScrollMetrics(rows, h);
  if (scroll) {
    context.fillStyle = "#0c0d10";
    context.fillRect(scroll.x - 1, scroll.y, scroll.w + 2, scroll.h);
    context.fillStyle = scrollDrag ? "#e8862e" : "#4a4e56";
    context.fillRect(scroll.x, scroll.thumbY, scroll.w, scroll.thumbH);
  }

  // Playhead.
  const playhead = timeToX(currentTime());
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
  context.beginPath();
  context.moveTo(0, audioBottom + 0.5);
  context.lineTo(w, audioBottom + 0.5);
  context.stroke();
}

function timelineScrollMetrics(rows = timelineRows, h = timelineSize.h) {
  const contentHeight = rows.reduce((sum, row) => sum + row.h + 2, 0);
  const top = view.rulerH + view.audioH + 4;
  const viewportH = Math.max(1, h - top - 4);
  if (contentHeight <= viewportH + 1) return null;
  const w = 7;
  const x = timelineSize.w - w - 2;
  const maxScroll = contentHeight - viewportH;
  const thumbH = Math.max(22, (viewportH / contentHeight) * (viewportH - 2));
  const thumbY = top + (view.scrollY / maxScroll) * (viewportH - thumbH - 2);
  return { x, y: top, w, h: viewportH, maxScroll, thumbH, thumbY, viewportH };
}

function scrollFromTimelineY(y) {
  const scroll = timelineScrollMetrics();
  if (!scroll) return;
  const ratio = clamp((y - scroll.y - scroll.thumbH / 2) / Math.max(1, scroll.viewportH - scroll.thumbH), 0, 1);
  view.scrollY = ratio * scroll.maxScroll;
  requestDraw();
}

function drawAudioLane(context, w) {
  const top = view.rulerH;
  const height = view.audioH;
  const bottom = top + height;
  context.fillStyle = "#101216";
  context.fillRect(0, top, w, height);
  context.fillStyle = "#141518";
  context.fillRect(0, top, view.labelW, height);
  context.fillStyle = "#5e9aa8";
  context.font = "9px Inter, sans-serif";
  context.fillText("AUDIO", 8, top + 14);
  context.fillStyle = "#4a4e56";
  context.font = "8px JetBrains Mono, monospace";
  const audioMeta = state?.show?.blueprint;
  context.fillText(
    audioAnalysis ? `${Number(audioMeta?.bpm || 0).toFixed(1)} BPM | MIN/MAX+RMS` : audioMeta ? `${Number(audioMeta.bpm || 0).toFixed(1)} BPM` : "NO AUDIO",
    8,
    top + 27
  );
  context.fillText("drag bottom = height", 8, top + 40);
  const mid = top + height * 0.55;
  const span = height * 0.36;
  const duration = Number(audioAnalysis?.duration || blueprint?.duration_s || showDoc()?.duration_s || 0);
  const analysis = audioAnalysis;
  if (analysis && duration > 0) {
    const buckets = analysis.buckets;
    for (let x = view.labelW; x < w; x += 2) {
      const time = xToTime(x);
      if (time < 0 || time > duration) continue;
      const index = Math.min(buckets - 1, Math.max(0, Math.floor((time / duration) * buckets)));
      const lo = analysis.min[index];
      const hi = analysis.max[index];
      context.fillStyle = "rgba(94, 154, 168, 0.34)";
      context.fillRect(x, mid - hi * span, 1, Math.max(1, (hi - lo) * span));
      context.fillStyle = "rgba(232, 134, 46, 0.72)";
      const rms = analysis.rms[index];
      context.fillRect(x, mid - rms * span, 1, Math.max(1, rms * span * 2));
    }
  } else if (blueprint?.waveform?.length && duration > 0) {
    const wave = blueprint.waveform;
    for (let x = view.labelW; x < w; x += 2) {
      const time = xToTime(x);
      if (time < 0 || time > duration) continue;
      const index = Math.min(wave.length - 1, Math.floor((time / duration) * wave.length));
      const amp = wave[index] * span;
      context.fillStyle = "rgba(94, 154, 168, 0.28)";
      context.fillRect(x, mid - amp, 1, Math.max(1, amp * 2));
    }
  }
  for (const section of blueprint?.sections || []) {
    const x = timeToX(Number(section.start));
    if (x < view.labelW || x > w) continue;
    const color = SECTION_COLORS[section.label] || "#6f7480";
    context.strokeStyle = color;
    context.globalAlpha = 0.7;
    context.beginPath();
    context.moveTo(x + 0.5, top);
    context.lineTo(x + 0.5, bottom);
    context.stroke();
    context.globalAlpha = 1;
    context.fillStyle = color;
    context.font = "8px Inter, sans-serif";
    context.fillText(String(section.label).toUpperCase(), x + 3, bottom - 4);
  }
  context.fillStyle = heightDrag === "audio" ? "#e8862e" : "#2b2e34";
  context.fillRect(0, bottom - 2, w, 2);
}

function clipAtPoint(x, y) {
  const doc = showDoc();
  if (!doc) return null;
  const hit = rowAtY(y);
  if (!hit) return null;
  const row = hit.row;
  if (y >= row.laneY) return null;
  for (const clip of row.track.clips) {
    const rect = clipRect(hit.index, clip);
    if (x >= rect.x0 && x <= rect.x1 && y >= rect.y && y <= rect.y + rect.h) {
      return { track: row.track, clip, trackIndex: hit.index, rect };
    }
  }
  return null;
}

function keyframeAtPoint(x, y) {
  const doc = showDoc();
  if (!doc) return null;
  const hit = rowAtY(y);
  if (!hit || y < hit.row.laneY) return null;
  const row = hit.row;
  const lane = Math.floor((y - row.laneY) / row.laneH);
  const info = row.lanes[lane];
  if (!info) return null;
  for (const track of [row.track]) {
    for (const clip of track.clips) {
      if (x < timeToX(clip.start_s) || x > timeToX(clip.end_s)) continue;
      const keyframes = (clip.keyframes || []).filter((keyframe) => keyframe.param === info.param);
      for (let index = 0; index < keyframes.length; index += 1) {
        const keyframe = keyframes[index];
        const kx = timeToX(clip.start_s + keyframe.t_s);
        if (Math.abs(kx - x) <= 5) {
          return { clip, track: row.track, param: info.param, index, keyframe, lane };
        }
      }
    }
  }
  return null;
}

function evalUiExpression(expression, context) {
  // Mirrors the backend's safe evaluator for the small expression lane preview.
  // The authoritative value still comes from the Python compositor.
  const names = [
    "sin", "cos", "abs", "exp", "min", "max", "floor", "round", "noise", "clamp",
    "smoothstep", "lerp", "step", "wrap", "pulse", "fbm", "env",
  ];
  const fract = (value) => value - Math.floor(value);
  const hash = (x, y, z) => fract(Math.sin(x * 127.1 + y * 311.7 + z * 74.7) * 43758.5453123);
  const fbm = (x, y, z, scale = 1) => {
    let total = 0;
    let amplitude = 1;
    let norm = 0;
    for (let octave = 0; octave < 3; octave += 1) {
      const frequency = (2 ** octave) * Number(scale);
      total += amplitude * hash(Number(x) * frequency + octave * 17, Number(y) * frequency, Number(z) * frequency);
      norm += amplitude;
      amplitude *= 0.5;
    }
    return total / Math.max(1e-9, norm);
  };
  const env = (name, phase) => Number(context[name] || 0) * Math.exp(-fract(Number(phase || 0)) * 3);
  const fn = {
    sin: Math.sin, cos: Math.cos, abs: Math.abs, exp: Math.exp, min: Math.min, max: Math.max,
    floor: Math.floor, round: Math.round, clamp: (value, lo, hi) => Math.max(lo, Math.min(hi, value)),
    smoothstep: (edge0, edge1, value) => {
      if (Math.abs(edge1 - edge0) < 1e-12) return value >= edge1 ? 1 : 0;
      const x = Math.max(0, Math.min(1, (value - edge0) / (edge1 - edge0)));
      return x * x * (3 - 2 * x);
    },
    lerp: (a, b, amount) => a + (b - a) * amount,
    step: (edge, value) => (value >= edge ? 1 : 0),
    wrap: (value, lo = 0, hi = 1) => {
      const span = hi - lo;
      return Math.abs(span) < 1e-12 ? lo : lo + fract((value - lo) / span) * span;
    },
    pulse: (phase, width = 0.5) => (fract(phase) < Math.max(0, Math.min(1, width)) ? 1 : 0),
    fbm,
    env,
    noise: (value) => fract(Math.sin(Number(value) * 12.9898) * 43758.5453123),
  };
  let text = String(expression || "").replace(/TAU/g, String(Math.PI * 2)).replace(/\bpi\b/g, String(Math.PI));
  for (const name of names) text = text.replace(new RegExp(`\\b${name}\\(`, "g"), `__fn.${name}(`);
  const keys = Object.keys(context);
  const values = keys.map((key) => context[key]);
  // eslint-disable-next-line no-new-func
  return Function("__fn", ...keys, `"use strict"; return (${text});`)(fn, ...values);
}

/* ---------------------------------------------------------------- inspector */
function fieldRow(labelText, control, options = {}) {
  const id = `f_${(fieldSeq += 1)}`;
  let field = control;
  if (control instanceof HTMLInputElement && control.type === "number") {
    field = stepperWrap(control);
  }
  const label = el("label", {
    for: control.id || id,
    text: labelText,
    title: options.title || undefined,
    "data-scrub-label": options.scrub ? control.id || id : undefined,
  });
  if (!control.id) control.id = id;
  const row = el("div", { class: "field-row" }, label, field);
  return row;
}

function stepperWrap(input) {
  input.classList.add("number");
  const step = Number(input.dataset.step || input.step || 0.01) || 0.01;
  const bump = (direction, event) => {
    const current = Number(input.value) || 0;
    const factor = event?.shiftKey ? 10 : 1;
    const next = current + direction * step * factor;
    const min = input.min === "" ? -Infinity : Number(input.min);
    const max = input.max === "" ? Infinity : Number(input.max);
    input.value = String(clamp(next, min, max));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  };
  return el(
    "div",
    { class: "stepper" },
    input,
    el(
      "div",
      { class: "stepper-buttons" },
      el("button", { type: "button", title: "Step up", "aria-label": "Step up", onclick: (event) => bump(1, event) }, (() => { const node = icon("chevron"); node.style.transform = "rotate(-90deg)"; return node; })()),
      el("button", { type: "button", title: "Step down", "aria-label": "Step down", onclick: (event) => bump(-1, event) }, (() => { const node = icon("chevron"); node.style.transform = "rotate(90deg)"; return node; })())
    )
  );
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
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    input.dispatchEvent(new Event("change", { bubbles: true }));
    input.blur();
  });
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

function rangeControl(value, onChange, options = {}) {
  const min = Number(options.min ?? 0);
  const max = Number(options.max ?? 1);
  const step = Number(options.step ?? 0.01);
  let current = clamp(Number(value), min, max);
  const input = numberInput(current, (next) => {
    current = clamp(Number(next), min, max);
    paint(current);
    onChange(current);
  }, { step, min, max });
  const fill = el("div", { class: "range-fill" });
  const head = el("div", { class: "range-head" });
  const track = el("div", { class: "range-track", role: "slider", tabindex: "0", "aria-valuemin": String(min), "aria-valuemax": String(max) }, fill, head);
  const paint = (next) => {
    const ratio = clamp((next - min) / Math.max(1e-9, max - min), 0, 1);
    fill.style.width = `${ratio * 100}%`;
    head.style.left = `${ratio * 100}%`;
    track.setAttribute("aria-valuenow", String(Number(next.toFixed(4))));
  };
  const setFromEvent = (event) => {
    const rect = track.getBoundingClientRect();
    const ratio = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1);
    current = clamp(min + ratio * (max - min), min, max);
    current = Math.round(current / step) * step;
    input.value = String(Number(current.toFixed(6)));
    paint(current);
  };
  track.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    track.setPointerCapture(event.pointerId);
    setFromEvent(event);
  });
  track.addEventListener("pointermove", (event) => {
    if (!track.hasPointerCapture(event.pointerId)) return;
    setFromEvent(event);
  });
  track.addEventListener("pointerup", (event) => {
    if (!track.hasPointerCapture(event.pointerId)) return;
    track.releasePointerCapture(event.pointerId);
    onChange(current);
  });
  track.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    current = clamp(current + (event.key === "ArrowRight" ? step : -step) * (event.shiftKey ? 10 : 1), min, max);
    input.value = String(Number(current.toFixed(6)));
    paint(current);
    onChange(current);
  });
  paint(current);
  return el("div", { class: "range-widget" }, track, stepperWrap(input));
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

function paletteById(id) {
  return (state?.palettes || []).find((palette) => palette.id === id) || null;
}

function paletteCss(colors) {
  const stops = (colors || []).filter((color) => Array.isArray(color) && color.length === 3);
  if (!stops.length) return "transparent";
  if (stops.length === 1) return `rgb(${stops[0].join(",")})`;
  return `linear-gradient(90deg, ${stops
    .map((color, index) => `rgb(${color.join(",")}) ${(index / (stops.length - 1)) * 100}%`)
    .join(", ")})`;
}

function paletteControl(clip, spec, value) {
  const palette = paletteById(value);
  const strip = el("span", {
    class: "palette-strip",
    style: `background:${palette ? paletteCss(palette.colors) : "transparent"}`,
    title: palette?.name || value,
  });
  const select = selectInput(value, (state.palettes || []).map((item) => [item.id, item.name]), (next) => {
    commitClip(clip.id, `palette ${spec.key}`, (target) => {
      target.params = { ...target.params, [spec.key]: next };
    });
  });
  const edit = el("button", {
    class: "button small",
    type: "button",
    title: "Palette bearbeiten oder sichern",
    "aria-label": "Edit palette",
    onclick: () => openPaletteEditor(palette, {
      onApply: (id) => commitClip(clip.id, `palette ${spec.key}`, (target) => {
        target.params = { ...target.params, [spec.key]: id };
      }),
    }),
  }, icon("palette"), el("span", { text: "Edit" }));
  return el(
    "div",
    { class: "palette-control" },
    strip,
    el("div", { class: "palette-control-row" }, select, edit)
  );
}

async function deletePalette(palette) {
  if (!palette || palette.builtin) {
    toast("Eingebaute Paletten können nicht gelöscht werden.", "info");
    return false;
  }
  const confirmed = await modalConfirm(
    "Palette löschen",
    `"${palette.name}" dauerhaft löschen?`,
    "Löschen"
  );
  if (!confirmed) return false;
  try {
    await client.del(`/api/palettes/${encodeURIComponent(palette.id)}`);
    await client.refresh();
    toast("Palette gelöscht", "success");
    return true;
  } catch (error) {
    toast(error.message, "danger");
    return false;
  }
}

function openPaletteEditor(palette, options = {}) {
  const editing = palette && !palette.builtin ? palette : null;
  const colors = (palette?.colors || [[255, 96, 24], [140, 30, 190], [0, 180, 255]])
    .map((color) => [...color]);
  let stops = colors.slice(0, 16);
  const dialog = document.createElement("dialog");
  const form = document.createElement("form");
  form.method = "dialog";
  const nameInput = el("input", {
    class: "input",
    type: "text",
    value: palette ? (palette.builtin ? `${palette.name} Kopie` : palette.name) : "Neue Palette",
    maxlength: "48",
    required: true,
  });
  const preview = el("div", { class: "palette-preview" });
  const stopList = el("div", { class: "palette-stops" });
  const addButton = el("button", { class: "button small", type: "button" }, icon("plus"), el("span", { text: "Farbe" }));
  const paint = () => {
    preview.style.background = paletteCss(stops);
    addButton.disabled = stops.length >= 16;
  };
  const rebuildStops = () => {
    stopList.replaceChildren(
      ...stops.map((color, index) => {
        const input = el("input", {
          type: "color",
          value: rgbToHex(color),
          "aria-label": `Farbe ${index + 1}`,
          oninput: (event) => {
            stops[index] = hexToRgb(event.target.value);
            paint();
          },
        });
        const remove = el("button", {
          class: "button small icon-button",
          type: "button",
          title: "Farbe entfernen",
          "aria-label": "Remove colour",
          disabled: stops.length <= 2,
          onclick: () => {
            stops.splice(index, 1);
            rebuildStops();
            paint();
          },
        }, icon("minus"));
        return el("div", { class: "palette-stop-row" }, input, el("span", { class: "mono", text: rgbToHex(color) }), remove);
      })
    );
  };
  addButton.addEventListener("click", () => {
    if (stops.length >= 16) return;
    stops.push([...stops[stops.length - 1]]);
    rebuildStops();
    paint();
  });
  rebuildStops();
  paint();

  const deleteButton = editing
    ? el("button", {
        class: "button small danger",
        type: "button",
        text: "Löschen",
        onclick: async () => {
          if (await deletePalette(editing)) dialog.close();
        },
      })
    : null;

  const save = el("button", {
    class: "button primary",
    type: "submit",
    text: editing ? "Speichern" : "Als neue Palette sichern",
  });
  const actions = el(
    "div",
    { class: "dialog-actions" },
    deleteButton,
    el("span", { class: "spacer", style: "flex:1" }),
    el("button", { class: "button", type: "button", text: "Abbrechen", onclick: () => dialog.close("cancel") }),
    save
  );
  form.append(
    el("div", { class: "dialog-head", text: editing ? "Palette bearbeiten" : "Neue Palette" }),
    el("div", { class: "dialog-body" },
      fieldRow("Name", nameInput),
      preview,
      el("div", { class: "list-subhead" }, el("span", { text: "Farben" }), addButton),
      stopList,
      el("p", { class: "empty-state", text: "2–16 Farben. Die Vorschau und alle Effekte interpolieren in Oklab." })
    ),
    actions
  );
  dialog.append(form);
  dialog.addEventListener("close", () => dialog.remove());
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = nameInput.value.trim();
    if (!name) {
      nameInput.focus();
      return;
    }
    save.disabled = true;
    try {
      const payload = { name, colors: stops };
      if (editing) payload.id = editing.id;
      const saved = await client.post("/api/palettes", payload);
      await client.refresh();
      dialog.close();
      toast(`Palette "${saved.name}" gespeichert`, "success");
      options.onApply?.(saved.id);
    } catch (error) {
      toast(error.message, "danger");
    } finally {
      save.disabled = false;
    }
  });
  document.body.append(dialog);
  dialog.showModal();
  nameInput.focus();
  nameInput.select();
}

function applyPaletteToClip(paletteId) {
  const found = findClip();
  if (!found) {
    toast("Clip in der Timeline auswählen, dann Palette anwenden.", "info");
    return;
  }
  const preset = presetById(found.clip.preset);
  const spec = preset?.params?.find((item) => item.type === "palette");
  if (!spec) {
    toast(`${preset?.name || found.clip.preset} hat keinen Palette-Parameter.`, "info");
    return;
  }
  commitClip(found.clip.id, `palette ${spec.key}`, (target) => {
    target.params = { ...target.params, [spec.key]: paletteId };
  });
  toast(`Palette auf ${clipDisplayName(found.clip)} angewendet`, "success", 1600);
}

function renderPalettes() {
  if (!dom.paletteList) return;
  const rows = (state?.palettes || []).map((palette) => {
    const strip = el("span", {
      class: "palette-strip",
      style: `background:${paletteCss(palette.colors)}`,
    });
    return el(
      "button",
      {
        class: "list-row palette-row",
        type: "button",
        title: palette.builtin ? "Eingebaute Palette" : "Eigene Palette",
        onclick: () => applyPaletteToClip(palette.id),
        ondblclick: () => openPaletteEditor(palette, { onApply: applyPaletteToClip }),
        oncontextmenu: (event) => {
          event.preventDefault();
          showContextMenu(event.clientX, event.clientY, [
            { label: "Auf Clip anwenden", icon: "palette", fn: () => applyPaletteToClip(palette.id) },
            { label: "Bearbeiten / kopieren", icon: "settings", fn: () => openPaletteEditor(palette, { onApply: applyPaletteToClip }) },
            { separator: true },
            { label: "Palette löschen", icon: "delete", danger: !palette.builtin, disabled: palette.builtin, fn: () => deletePalette(palette) },
          ]);
        },
      },
      strip,
      el("span", { class: "row-text" },
        el("span", { class: "row-name", text: palette.name }),
        el("span", { class: "row-desc", text: palette.builtin ? "BUILT-IN" : "EIGEN" })
      ),
      el("span", { class: "row-meta mono", text: `${palette.colors.length}` })
    );
  });
  if (!rows.length) rows.push(el("div", { class: "library-empty", text: "Keine Paletten geladen." }));
  dom.paletteList.replaceChildren(...rows);
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
    return stepperWrap(input);
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

function paramKey(clip, param) {
  return `${clip.id}:${param}`;
}

function keyframeAtPlayhead(clip, param) {
  const local = clamp(currentTime() - clip.start_s, 0, Math.max(0, clip.end_s - clip.start_s));
  return (clip.keyframes || []).find(
    (keyframe) => keyframe.param === param && Math.abs(keyframe.t_s - local) <= 0.08
  );
}

function paramKeyframes(clip, param) {
  return (clip.keyframes || [])
    .filter((keyframe) => keyframe.param === param)
    .sort((a, b) => a.t_s - b.t_s);
}

function jumpParamKeyframe(clip, param, direction) {
  const frames = paramKeyframes(clip, param);
  const local = currentTime() - clip.start_s;
  const target = direction < 0
    ? [...frames].reverse().find((frame) => frame.t_s < local - 1e-3)
    : frames.find((frame) => frame.t_s > local + 1e-3);
  if (!target) return;
  client.seek(clamp(clip.start_s + target.t_s, 0, duration()));
}

function toggleParamKeyframe(clip, param, value) {
  const existing = keyframeAtPlayhead(clip, param);
  if (existing) {
    const local = clamp(currentTime() - clip.start_s, 0, Math.max(0, clip.end_s - clip.start_s));
    commitClip(clip.id, `remove keyframe ${param}`, (target) => {
      target.keyframes = (target.keyframes || []).filter(
        (keyframe) => !(keyframe.param === param && Math.abs(keyframe.t_s - local) <= 0.08)
      );
    });
    return;
  }
  addParamKeyframe(clip, param, value);
}

function addParamKeyframe(clip, param, value) {
  const local = clamp(currentTime() - clip.start_s, 0, Math.max(0, clip.end_s - clip.start_s));
  let keyValue;
  if (Array.isArray(value) && [3, 4].includes(value.length) && value.every((part) => Number.isFinite(Number(part)))) {
    keyValue = value.map(Number);
  } else {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      toast("Keyframes need a numeric or colour value", "info");
      return;
    }
    keyValue = numeric;
  }
  commitClip(clip.id, `keyframe ${param}`, (target) => {
    target.keyframes = (target.keyframes || []).filter(
      (keyframe) => !(keyframe.param === param && Math.abs(keyframe.t_s - local) <= 1e-4)
    );
    target.keyframes.push({ t_s: Number(local.toFixed(4)), param, value: keyValue, easing: "linear" });
    target.keyframes.sort((a, b) => a.t_s - b.t_s);
  });
}

function expressionContext(clip, value) {
  const time = currentTime();
  const env = envelopeAt(time);
  return {
    ...env,
    t: time,
    local_t: clamp(time - clip.start_s, 0, Math.max(0, clip.end_s - clip.start_s)),
    val: Number(value) || 0,
    beat: env.beatPos,
  };
}

function openExpressionDialog(clip, param, value, onSaved = null) {
  const current = clip.expressions?.[param] || "";
  const dialog = document.createElement("dialog");
  const form = document.createElement("form");
  form.method = "dialog";
  const input = el("input", {
    class: "input mono",
    type: "text",
    value: current,
    placeholder: "0.5 + 0.5 * sin(beatPhase * TAU)",
    spellcheck: "false",
  });
  const diagnostic = el("div", { class: "expr-diagnostic mono" });
  const live = el("div", { class: "mono muted", style: "font-size:9px" });
  const generators = [
    ["Beat LFO", "0.5 + 0.5 * sin(beatPhase * TAU)"],
    ["Stem Follower", "clamp(val * (0.35 + 0.65 * energy), 0, 1)"],
    ["Phrase Riser", "clamp(0.25 + 0.75 * sectionProgress + 0.1 * env('kick', beatPhase), 0, 1)"],
    ["Emitter Wave", "0.5 + 0.5 * sin(beat * TAU + barPhase * TAU)"],
    ["Wiggle", "val + 0.12 * sin(t * 17.0) * noise(t * 3.0)"],
  ];
  const tokens = [
    "t", "beat", "beatPhase", "barPhase", "phrasePhase", "kick", "snare", "hat",
    "energy", "harmonic", "percussive", "prominence", "bass", "mid", "high",
    "sectionProgress", "beatsToDrop", "inDrop", "val", "sin(", "cos(", "clamp(",
    "smoothstep(", "lerp(", "step(", "wrap(", "pulse(", "env('kick', ",
    "fbm(", "noise(", "TAU",
  ];
  const insert = (text) => {
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    input.value = `${input.value.slice(0, start)}${text}${input.value.slice(end)}`;
    input.focus();
    input.selectionStart = input.selectionEnd = start + text.length;
    updatePreview();
  };
  const updatePreview = () => {
    const text = input.value.trim();
    if (!text) {
      live.textContent = `val = ${Number(value).toFixed(3)}`;
      diagnostic.textContent = "";
      return true;
    }
    try {
      const result = evalUiExpression(text, expressionContext(clip, value));
      live.textContent = `val = ${Number(value).toFixed(3)} → ${Number(result).toFixed(4)} at ${currentTime().toFixed(2)}s`;
      diagnostic.textContent = "";
      diagnostic.classList.remove("danger");
      return true;
    } catch (error) {
      live.textContent = `val = ${Number(value).toFixed(3)}`;
      diagnostic.textContent = error.message || String(error);
      diagnostic.classList.add("danger");
      return false;
    }
  };
  input.addEventListener("input", updatePreview);
  const save = async (text) => {
    const trimmed = text.trim();
    if (trimmed && !updatePreview()) return;
    if (trimmed) {
      try {
        const check = await client.post("/api/expressions/validate", {
          expression: trimmed,
          context: expressionContext(clip, value),
        });
        if (!check.ok) {
          diagnostic.textContent = check.error || "expression failed";
          diagnostic.classList.add("danger");
          return;
        }
      } catch (error) {
        diagnostic.textContent = error.message;
        diagnostic.classList.add("danger");
        return;
      }
    }
    await commitClip(clip.id, trimmed ? `expression ${param}` : `clear expression ${param}`, (target) => {
      target.expressions = { ...(target.expressions || {}) };
      if (trimmed) target.expressions[param] = trimmed;
      else delete target.expressions[param];
    });
    if (onSaved) onSaved();
    dialog.close("apply");
  };
  form.append(
    el("div", { class: "dialog-head", text: `ƒ ${param}` }),
    el(
      "div",
      { class: "dialog-body" },
      el("div", { class: "expr-row" }, input),
      el("div", { class: "expr-presets" },
        ...generators.map(([label, text]) =>
          el("button", {
            type: "button",
            text: label,
            onclick: () => { input.value = text; input.focus(); updatePreview(); },
          })
        )
      ),
      el("div", { class: "expr-tokens" },
        ...tokens.map((token) =>
          el("button", { type: "button", text: token, onclick: () => insert(token) })
        )
      ),
      live,
      diagnostic,
      el("p", { class: "empty-state", style: "padding:0", text: "Functions: sin cos abs exp min max floor round noise clamp smoothstep lerp step wrap pulse env fbm. Audio and structure variables stay deterministic; no script runtime." })
    ),
    el(
      "div",
      { class: "dialog-actions" },
      el("button", { class: "button", type: "button", text: "Abbrechen", onclick: () => dialog.close("cancel") }),
      el("button", { class: "button", type: "button", text: "Löschen", onclick: () => save("") }),
      el("button", { class: "button primary", type: "button", text: "Set", onclick: () => save(input.value) })
    )
  );
  dialog.append(form);
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  updatePreview();
  dialog.showModal();
  input.focus();
}

function expressionEditor(clip, param, value) {
  const expression = clip.expressions?.[param];
  if (!expression) return null;
  let live = "–";
  try {
    const result = evalUiExpression(expression, expressionContext(clip, value));
    live = Number.isFinite(Number(result)) ? Number(result).toFixed(3) : String(result);
  } catch {
    live = "error";
  }
  return el(
    "div",
    { class: "expression-row" },
    el(
      "button",
      {
        class: "expression-chip mono",
        type: "button",
        title: `${expression}\nKlick: bearbeiten`,
        onclick: () => openExpressionDialog(clip, param, value),
      },
      icon("function"),
      el("span", { text: expression })
    ),
    el("span", { class: "mono muted expression-value", text: `= ${live}` })
  );
}

function paramRow(clip, param, labelText, control, value, options = {}) {
  const key = paramKey(clip, param);
  const expression = Boolean(clip.expressions?.[param]);
  const keyed = Boolean(keyframeAtPlayhead(clip, param));
  const frames = paramKeyframes(clip, param);
  const local = currentTime() - clip.start_s;
  const previousKey = [...frames].reverse().find((frame) => frame.t_s < local - 1e-3);
  const nextKey = frames.find((frame) => frame.t_s > local + 1e-3);
  const label = el("label", {
    text: labelText,
    title: `${labelText} — Doppelklick: Lane zeigen${options.noExpression ? "" : " · Alt+Klick: Ausdruck"}`,
    ondblclick: (event) => {
      event.preventDefault();
      revealParamLane(clip, param);
    },
    onclick: (event) => {
      if (!event.altKey || options.noExpression) return;
      event.preventDefault();
      openExpressionDialog(clip, param, value);
    },
  });
  const actions = options.noActions
    ? null
    : el(
        "div",
        { class: "param-actions" },
        frames.length
          ? el("button", {
              class: "button small icon-button key-nav",
              type: "button",
              title: "Vorheriger Keyframe",
              "aria-label": `Previous keyframe ${param}`,
              disabled: !previousKey,
              onclick: () => jumpParamKeyframe(clip, param, -1),
            }, icon("chevronLeft"))
          : null,
        frames.length
          ? el("button", {
              class: "button small icon-button key-nav",
              type: "button",
              title: "Nächster Keyframe",
              "aria-label": `Next keyframe ${param}`,
              disabled: !nextKey,
              onclick: () => jumpParamKeyframe(clip, param, 1),
            }, icon("chevronRight"))
          : null,
        el("button", {
          class: `button small${keyed ? " keyed" : ""}`,
          type: "button",
          title: keyed ? `Keyframe ${param} am Playhead entfernen` : `Keyframe ${param} am Playhead setzen`,
          "aria-label": keyed ? `Remove keyframe ${param}` : `Keyframe ${param}`,
          "aria-pressed": String(keyed),
          onclick: () => toggleParamKeyframe(clip, param, value),
        }, icon("keyframe")),
        options.noExpression
          ? null
          : el("button", {
              class: `button small${openExpressions.has(key) || expression ? " active" : ""}`,
              type: "button",
              title: expression
                ? `Ausdruck für ${param} (Alt+Klick entfernt ihn)`
                : `Function generator for ${param}`,
              "aria-label": `Function generator ${param}`,
              onclick: (event) => {
                if (event.altKey && expression) {
                  event.preventDefault();
                  commitClip(clip.id, "remove expression", (target) => {
                    if (target.expressions) delete target.expressions[param];
                  });
                  return;
                }
                openExpressionDialog(clip, param, value);
              },
            }, icon("function"))
      );
  return el(
    "div",
    { class: "param-row" },
    label,
    control,
    actions
  );
}

function presetParamControl(clip, spec, value) {
  const update = (next) => {
    commitClip(clip.id, `param ${spec.key}`, (target) => {
      target.params = { ...target.params, [spec.key]: next };
    });
  };
  if (spec.type === "range") {
    return rangeControl(value, update, { min: spec.min ?? 0, max: spec.max ?? 1, step: spec.step ?? 0.01 });
  }
  return numberInput(value, update, { step: spec.step ?? 0.01, min: spec.min, max: spec.max });
}

function renderInspector() {
  if (!state) return;
  if (inspectorTab === "fixture") renderFixtureInspector();
  else if (inspectorTab === "show") renderShowInspector();
  else renderClipInspector();
}

function renderShowInspector() {
  const doc = showDoc();
  if (!doc) {
    dom.inspectorBody.replaceChildren(
      el("div", { class: "inspector-title" }, el("strong", { text: "Show" }), el("span", { text: "none" }))
    );
    return;
  }
  dom.inspectorBody.replaceChildren(
    ...renderShowSection(doc),
    renderTracksSection(doc)
  );
}

function renderFixtureInspector() {
  const ids = [...selectedFixtures];
  if (ids.length > 1) {
    const group = selectedGroupId
      ? (state.rig.document.groups || []).find((item) => item.id === selectedGroupId)
      : null;
    const dx = numberInput(0, () => {}, { step: 0.1 });
    const dy = numberInput(0, () => {}, { step: 0.1 });
    const dz = numberInput(0, () => {}, { step: 0.1 });
    const axisSelect = selectInput("z", [["x", "X"], ["y", "Y"], ["z", "Z"]], () => {});
    const angleInput = numberInput(90, () => {}, { step: 5, min: -360, max: 360 });
    const scaleInput = numberInput(1.1, () => {}, { step: 0.05, min: 0.05, max: 10 });
    const axisInput = axisSelect;
    const axis = () => ["x", "y", "z"].indexOf(axisInput.value);
    const applyMove = () => moveSelection(ids, [Number(dx.value) || 0, Number(dy.value) || 0, Number(dz.value) || 0]);
    const applyRotate = () => rotateSelection(ids, Number(angleInput.value) || 0);
    const applyScale = () => scaleSelection(ids, Number(scaleInput.value) || 1);
    const applyOnEnter = (input, action) => {
      input.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        action();
      });
    };
    applyOnEnter(dx, applyMove);
    applyOnEnter(dy, applyMove);
    applyOnEnter(dz, applyMove);
    applyOnEnter(angleInput, applyRotate);
    applyOnEnter(scaleInput, applyScale);
    dom.inspectorBody.replaceChildren(
      el(
        "div",
        { class: "inspector-title" },
        el("strong", { text: group ? `Gruppe: ${group.name}` : `${ids.length} Fixtures` }),
        el("span", { text: `${ids.length} ausgewählt` })
      ),
      el("p", {
        class: "empty-state",
        text: "Im Viewport ziehen bewegt die ganze Auswahl. Für präzise Gruppen-Transforms die Felder unten nutzen.",
      }),
      section(
        "Gruppe transformieren",
        el(
          "div",
          { class: "field-row" },
          el("label", { text: "Offset X/Y/Z" }),
          el("div", { class: "triple" }, dx, dy, dz)
        ),
        el("div", { class: "section-actions" },
          el("button", {
            class: "button small",
            type: "button",
            text: "Verschieben",
            onclick: () => moveSelection(ids, [Number(dx.value) || 0, Number(dy.value) || 0, Number(dz.value) || 0]),
          }),
          el("button", {
            class: "button small",
            type: "button",
            text: "Auf Auswahlzentrum",
            title: "Offsets relativ zum Gruppenmittelpunkt anwenden",
            onclick: () => moveSelection(ids, [Number(dx.value) || 0, Number(dy.value) || 0, Number(dz.value) || 0]),
          })
        ),
        fieldRow("Align-Achse", axisSelect),
        el("div", { class: "section-actions" },
          angleInput,
          el("button", {
            class: "button small",
            type: "button",
            text: "Rotieren Z",
            onclick: () => rotateSelection(ids, Number(angleInput.value) || 0),
          }),
          el("button", {
            class: "button small",
            type: "button",
            text: "−90°",
            onclick: () => rotateSelection(ids, -90),
          })
        ),
        el("div", { class: "section-actions" },
          scaleInput,
          el("button", {
            class: "button small",
            type: "button",
            text: "Skalieren",
            onclick: () => scaleSelection(ids, Number(scaleInput.value) || 1),
          })
        ),
        el("div", { class: "section-actions" },
          el("button", { class: "button small", type: "button", text: "Align Min", onclick: () => alignSelection(ids, axis(), "min") }),
          el("button", { class: "button small", type: "button", text: "Align Mitte", onclick: () => alignSelection(ids, axis(), "center") }),
          el("button", { class: "button small", type: "button", text: "Align Max", onclick: () => alignSelection(ids, axis(), "max") }),
          el("button", {
            class: "button small",
            type: "button",
            text: "Verteilen",
            title: "Gleichmäßig entlang der gewählten Achse verteilen",
            onclick: () => distributeSelection(ids, axis()),
          })
        )
      ),
        el("div", { class: "section-actions" },
          el("button", {
            class: "button small",
            type: "button",
            text: "Offsets einrechnen",
            title: "Alle Drag-Offsets in die lokale Geometrie übernehmen",
            onclick: () => bakeSelectionOffsets(ids),
          }),
          el("button", {
            class: "button small",
            type: "button",
            text: "Auswahl aufheben",
            onclick: () => {
              selectedFixtures.clear();
              renderFixtures();
              renderInspector();
              requestDraw();
            },
          }),
          el("button", {
            class: "button small danger",
            type: "button",
            text: "Delete Selection",
            onclick: () => deleteFixtures(ids),
          })
        )
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
  const reasons = fixture.incomplete_reasons || [];
  const children = [
    el(
      "div",
      { class: `inspector-title${reasons.length ? " incomplete-title" : ""}` },
      el("strong", { text: fixture.name }),
      el("span", { text: fixture.id })
    ),
    reasons.length
      ? el(
          "div",
          { class: "proxy-warning" },
          el("strong", { text: "Unvollständig" }),
          ...reasons.map((reason) => el("div", { text: reason }))
        )
      : null,
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
        vec3Field("Position (lokal)", geometry.pos, (value) => patch({ geometry: { pos: value } })),
        vec3Field("Aim", geometry.aim, (value) => patch({ geometry: { aim: value } })),
        fieldRow("Beam deg", numberInput(geometry.beam_deg, (value) => patch({ geometry: { beam_deg: value } }), { step: 1, min: 1, max: 179 }), { scrub: true })
      )
    );
  } else {
    children.push(
      section(
        "Line Geometry",
        vec3Field("Start (lokal)", geometry.start, (value) => patch({ geometry: { start: value } })),
        vec3Field("End (lokal)", geometry.end, (value) => patch({ geometry: { end: value } })),
        fieldRow("Pixels", numberInput(geometry.pixels, (value) => patch({ geometry: { pixels: Math.round(value) } }), { step: 1, min: 2, max: 4096 }), { scrub: true }),
        vec3Field("Aim", geometry.aim, (value) => patch({ geometry: { aim: value } })),
        fieldRow("Beam deg", numberInput(geometry.beam_deg, (value) => patch({ geometry: { beam_deg: value } }), { step: 1, min: 1, max: 179 }), { scrub: true })
      )
    );
  }

  children.push(
    section(
      "Transform",
      el(
        "div",
        { class: "field-row" },
        el("label", { text: "Weltposition" }),
        el("span", {
          class: "mono",
          style: "font-size:10px;color:var(--text-1)",
          text: fixtureCenter(fixture.id).map((value) => value.toFixed(2)).join(" / "),
        })
      ),
      vec3Field("Offset (Welt)", transform.translate, (value) => patch({ transform: { translate: value } })),
      vec3Field("Rotate", transform.rotate_deg, (value) => patch({ transform: { rotate_deg: value } })),
      vec3Field("Pivot", transform.pivot, (value) => patch({ transform: { pivot: value } })),
      vec3Field("Scale", transform.scale, (value) => patch({ transform: { scale: value } }), { step: 0.01 }),
      el("p", {
        class: "empty-state",
        style: "padding:2px 0 4px",
        text: "Position ist die lokale Geometrie-Koordinate. Offset ist ein zusätzlicher Welt-Versatz nach Rotation/Skalierung (Viewport-Drag schreibt hier hinein). Zum Aufräumen: Offset einrechnen.",
      }),
      el("button", {
        class: "button small",
        type: "button",
        text: "Offset einrechnen",
        title: "Offset in Position/Start/End übernehmen und Offset auf 0 setzen",
        onclick: () => bakeFixtureOffset(fixture.id),
      })
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
  dom.inspectorBody.replaceChildren(...children.filter(Boolean));
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
        el("strong", { text: clipDisplayName(clip) }),
        el("span", { text: `${track.name} / ${clip.id}` })
      ),
      section(
        "Clip",
        fieldRow("Name", textInput(clip.name || "", (value) => commitClip(clip.id, "rename clip", (target) => { target.name = value.trim() || null; }), { placeholder: `Auto: ${presetName(clip.preset)}` })),
        toggleRow("Name automatisch", !clip.name, (auto) => commitClip(clip.id, "auto clip name", (target) => {
          target.name = auto ? null : clipDisplayName(clip);
        })),
        fieldRow("Start", numberInput(clip.start_s, (value) => commitClip(clip.id, "trim clip", (target) => { target.start_s = clamp(value, 0, target.end_s - 0.1); }), { step: 0.01, min: 0 }), { scrub: true }),
        fieldRow("End", numberInput(clip.end_s, (value) => commitClip(clip.id, "trim clip", (target) => { target.end_s = clamp(value, target.start_s + 0.1, duration()); }), { step: 0.01, min: 0 }), { scrub: true }),
        fieldRow("Fade in", numberInput(clip.fade_in_s, (value) => commitClip(clip.id, "fade", (target) => { target.fade_in_s = clamp(value, 0, target.end_s - target.start_s); }), { step: 0.01, min: 0 }), { scrub: true }),
        fieldRow("Fade out", numberInput(clip.fade_out_s, (value) => commitClip(clip.id, "fade", (target) => { target.fade_out_s = clamp(value, 0, target.end_s - target.start_s); }), { step: 0.01, min: 0 }), { scrub: true }),
        paramRow(clip, "opacity", "Opacity", rangeControl(clip.opacity, (value) => commitClip(clip.id, "opacity", (target) => { target.opacity = clamp(value, 0, 1); }), { min: 0, max: 1, step: 0.01 }), clip.opacity),
        expressionEditor(clip, "opacity", clip.opacity),
        fieldRow("Preset", selectInput(clip.preset, (state.presets || []).map((item) => [item.id, `${item.category || "Preset"}: ${item.name}`]), (value) => commitClip(clip.id, "change preset", (target) => { target.preset = value; target.params = defaultParams(value); }))),
        toggleRow("ZCL snap", clip.snap_zcl, (value) => commitClip(clip.id, "snap", (target) => { target.snap_zcl = value; }))
      )
    );

    if (preset) {
      const rows = [];
      for (const spec of preset.params) {
        const value = clip.params?.[spec.key] ?? spec.default;
        if (spec.type === "color") {
          rows.push(paramRow(
            clip,
            spec.key,
            spec.label,
            colorField(value, (next) => {
              commitClip(clip.id, `param ${spec.key}`, (target) => {
                target.params = { ...target.params, [spec.key]: next };
              });
            }),
            value,
            { noExpression: true }
          ));
        } else if (spec.type === "palette") {
          rows.push(fieldRow(spec.label, paletteControl(clip, spec, value)));
        } else if (spec.type === "vec3") {
          rows.push(vec3Field(spec.label, value, (next) => {
            commitClip(clip.id, `param ${spec.key}`, (target) => {
              target.params = { ...target.params, [spec.key]: next };
            });
          }, { step: 0.01 }));
        } else if (spec.type === "bool") {
          rows.push(toggleRow(spec.label, Boolean(value), (next) => {
            commitClip(clip.id, `param ${spec.key}`, (target) => {
              target.params = { ...target.params, [spec.key]: next };
            });
          }));
        } else if (spec.type === "select") {
          rows.push(fieldRow(spec.label, selectInput(String(value), spec.options || [], (next) => {
            commitClip(clip.id, `param ${spec.key}`, (target) => {
              target.params = { ...target.params, [spec.key]: next };
            });
          })));
        } else {
          rows.push(
            paramRow(
              clip,
              spec.key,
              spec.label,
              presetParamControl(clip, spec, value),
              value
            )
          );
          rows.push(expressionEditor(clip, spec.key, value));
        }
      }
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

function bindCurrentMaTrack() {
  const track = state?.sync?.track;
  if (!track || (!track.artist && !track.title)) {
    toast("Kein Music-Assistant-Track aktiv. Erst Track starten, dann binden.", "info");
    return;
  }
  commitShow("bind MA track", (target) => {
    target.match = target.match || {};
    target.match.artist = track.artist || "";
    target.match.title = track.title || "";
    target.match.duration_s = Number(track.duration_s || 0);
    target.match.ma_item_ids = track.item_id ? [track.item_id] : [];
    target.match.normalized_keys = [];
  });
  toast(`Show gebunden an ${track.artist || "?"} – ${track.title || "?"}`, "success");
}

async function setSyncOffset(offsetMs) {
  if (!state?.sync?.configured) {
    toast("Sendspin/MA ist nicht konfiguriert.", "info");
    return;
  }
  const clamped = clamp(Number(offsetMs) || 0, -20000, 20000);
  try {
    state.sync = await client.post("/api/sync", { offset_s: clamped / 1000 });
    updateTopbar();
    renderInspector();
    toast(`MA Offset ${clamped > 0 ? "+" : ""}${Math.round(clamped)} ms`, "info", 1200);
  } catch (error) {
    toast(error.message, "danger");
  }
}


function addCurrentMaTrackAlias() {
  const track = state?.sync?.track;
  if (!track || (!track.artist && !track.title)) {
    toast("Kein Music-Assistant-Track aktiv.", "info");
    return;
  }
  commitShow("add MA alias", (target) => {
    target.match = target.match || {};
    const keys = new Set(target.match.normalized_keys || []);
    if (track.key) keys.add(track.key);
    if (track.artist && track.title) keys.add(`${track.artist}|${track.title}`);
    target.match.normalized_keys = [...keys];
    if (track.item_id) {
      target.match.ma_item_ids = [
        ...new Set([...(target.match.ma_item_ids || []), track.item_id]),
      ];
    }
  });
  toast(`MA-Alias hinzugefügt: ${track.artist || "?"} – ${track.title || "?"}`, "success");
}

function clearMaMatch() {
  commitShow("clear MA match", (target) => {
    target.match = target.match || {};
    target.match.artist = "";
    target.match.title = "";
    target.match.duration_s = 0;
    target.match.ma_item_ids = [];
    target.match.normalized_keys = [];
  });
}

async function showSendspinPairing() {
  try {
    const sync = await client.get("/api/sync");
    if (!sync?.configured) {
      toast("Sendspin/MA ist nicht konfiguriert (--no-sync oder keine URL).", "info");
      return;
    }
    const token = sync.pairing_token;
    if (!token) {
      toast("Pairing-Token noch nicht verfügbar: Sendspin verbindet noch.", "info");
      return;
    }
    const dialog = document.createElement("dialog");
    const form = document.createElement("form");
    form.method = "dialog";
    const input = el("input", {
      class: "input mono",
      readonly: true,
      value: token,
      style: "width:100%",
    });
    const copy = el("button", {
      class: "button small",
      type: "button",
      text: "Kopieren",
      onclick: () => {
        const done = () => toast("Pairing-Token kopiert", "success");
        if (navigator.clipboard?.writeText) {
          navigator.clipboard.writeText(token).then(done, () => {
            input.select();
            document.execCommand?.("copy");
            done();
          });
        } else {
          input.select();
          document.execCommand?.("copy");
          done();
        }
      },
    });
    form.append(
      el("div", { class: "dialog-head", text: "Sendspin Pairing-Token" }),
      el("div", { class: "dialog-body" },
        el("p", {
          class: "empty-state",
          text: "Music Assistant → Player „Hueme-Visualizer“ → Setup required → Pair device → Token einfügen.",
        }),
        input,
        el("div", { class: "section-actions", style: "margin-top:6px" },
          copy,
          el("button", {
            class: "button small",
            type: "button",
            text: "Sendspin neu starten",
            onclick: async () => {
              try {
                state.sync = await client.post("/api/sync/restart");
                updateTopbar();
                toast("Sendspin neu verbunden", "success");
              } catch (error) {
                toast(error.message, "danger");
              }
            },
          })
        )
      ),
      el("div", { class: "dialog-actions" },
        el("button", { class: "button primary", type: "submit", text: "Schließen" })
      )
    );
    dialog.append(form);
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
  } catch (error) {
    toast(error.message, "danger");
  }
}

function renderShowSection(doc) {
  const markers = doc.timing.markers || [];
  const markerChips = markers.map((marker, index) =>
    el("span", { class: "chip" },
      el("span", { class: "mono", text: formatTime(marker.t_s) }),
      el("span", { text: marker.name }),
      el("button", {
        type: "button",
        title: "Marker löschen",
        "aria-label": "Remove marker",
        onclick: () => commitShow("remove marker", (target) => {
          target.timing.markers.splice(index, 1);
        }),
      }, icon("close"))
    )
  );
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
      el("p", { class: "empty-state", text: "Clip in der Timeline wählen, um Preset, Maske, Filter und Keyframes zu bearbeiten." })
    ),
    section(
      "Music Assistant",
      fieldRow("Artist", textInput(doc.match?.artist || "", (value) => commitShow("MA artist", (target) => {
        target.match = target.match || {};
        target.match.artist = value;
        target.match.normalized_keys = [];
      }))),
      fieldRow("Titel", textInput(doc.match?.title || "", (value) => commitShow("MA title", (target) => {
        target.match = target.match || {};
        target.match.title = value;
        target.match.normalized_keys = [];
      }))),
      fieldRow("Dauer", numberInput(doc.match?.duration_s || 0, (value) => commitShow("MA duration", (target) => {
        target.match = target.match || {};
        target.match.duration_s = Math.max(0, value);
      }), { step: 0.1, min: 0 }), { scrub: true }),
      fieldRow("Sync-Offset", numberInput(Number((state?.sync?.offset_s || 0) * 1000), setSyncOffset, { step: 10, min: -20000, max: 20000 }), { scrub: true, title: "Positiv = Licht später, negativ = Licht früher (bis ±20 s). Wird automatisch aus der Sendspin-Zeit berechnet; der Offset gilt nur für die Speaker-/AVR-Kette." }),
      el("div", { class: "section-actions", style: "margin-top:5px" },
        el("button", { class: "button small", type: "button", text: "−500", onclick: () => setSyncOffset(Number((state?.sync?.offset_s || 0) * 1000) - 500) }),
        el("button", { class: "button small", type: "button", text: "−50", onclick: () => setSyncOffset(Number((state?.sync?.offset_s || 0) * 1000) - 50) }),
        el("button", { class: "button small", type: "button", text: "+50", onclick: () => setSyncOffset(Number((state?.sync?.offset_s || 0) * 1000) + 50) }),
        el("button", { class: "button small", type: "button", text: "+500", onclick: () => setSyncOffset(Number((state?.sync?.offset_s || 0) * 1000) + 500) })
      ),
      el("div", { class: "section-actions", style: "margin-top:5px" },
        el("button", { class: "button small", type: "button", text: "Aktuellen MA-Track binden", onclick: bindCurrentMaTrack }),
        el("button", { class: "button small", type: "button", text: "Als Alias hinzufügen", onclick: addCurrentMaTrackAlias }),
        el("button", { class: "button small", type: "button", text: "Match löschen", onclick: clearMaMatch })
      ),
      el("p", { class: "empty-state", text: state?.sync?.matched_show_id === doc.id ? "Auto-Show aktiv für diesen Track." : "Artist/Titel oder MA-Item-ID müssen zum laufenden Track passen." })
    ),
    section(
      "Marker",
      markerChips.length ? el("div", { class: "chip-list" }, ...markerChips) : el("p", { class: "empty-state", text: "Keine Marker. Audio importieren oder am Playhead setzen." }),
      el("div", { class: "section-actions", style: "margin-top:5px" },
        el("button", {
          class: "button small",
          type: "button",
          text: "+ Marker am Playhead",
          onclick: () => commitShow("add marker", (target) => {
            const t = currentTime();
            target.timing.markers.push({
              t_s: Number(clamp(t, 0, target.duration_s).toFixed(3)),
              name: `Cue ${target.timing.markers.length + 1}`,
              kind: "cue",
            });
            target.timing.markers.sort((a, b) => a.t_s - b.t_s);
          }),
        }),
        el("button", {
          class: "button small",
          type: "button",
          text: "Marker synchronisieren",
          onclick: syncMarkersFromBlueprint,
        })
      )
    ),
    el("button", {
      class: "button small",
      type: "button",
      text: "Add Track",
      onclick: () => addTrack("layer"),
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
      el("span", { class: "row-meta", text: `${track.kind === "global" ? "G " : track.kind === "sub" ? "S " : ""}${track.target_group}` }),
      el("button", {
        class: "button small icon-button",
        type: "button",
        title: "Mute track",
        "aria-label": "Mute track",
        style: `width:20px;color:${track.muted ? "var(--danger)" : "var(--text-2)"}`,
        onclick: (event) => {
          event.stopPropagation();
          commitShow("mute track", (target) => {
            const item = target.tracks.find((candidate) => candidate.id === track.id);
            if (item) item.muted = !item.muted;
          });
        },
      }, icon("mute")),
      el("button", {
        class: "button small icon-button",
        type: "button",
        title: "Solo track",
        "aria-label": "Solo track",
        style: `width:20px;color:${track.solo ? "var(--warn)" : "var(--text-2)"}`,
        onclick: (event) => {
          event.stopPropagation();
          commitShow("solo track", (target) => {
            const item = target.tracks.find((candidate) => candidate.id === track.id);
            if (item) item.solo = !item.solo;
          });
        },
      }, icon("solo")),
    );
  });
  const selected = doc.tracks.find((track) => track.id === selectedTrackId);
  const editor = [];
  if (selected) {
    const rigDoc = state?.rig?.document || {};
    const targets = [
      ["all", "All Fixtures"],
      ...(rigDoc.groups || []).filter((group) => group.type !== "all").map((group) => [group.id, `Group: ${group.name}`]),
      ...(rigDoc.smart_groups || []).map((group) => [group.id, `# ${group.name}`]),
      ...(rigDoc.fixtures || []).map((fixture) => [fixture.id, `Lamp: ${fixture.name}`]),
    ];
    const patchTrack = (body, label = "edit track") => {
      commitShow(label, (targetDoc) => {
        const item = targetDoc.tracks.find((track) => track.id === selected.id);
        if (item) Object.assign(item, body);
      });
    };
    editor.push(
      el("div", { class: "preset-category", text: `Ebene: ${selected.name}` }),
      fieldRow("Name", textInput(selected.name, (value) => patchTrack({ name: value.trim() || selected.id, name_auto: false }, "rename track"))),
      toggleRow("Name folgt Effekt", trackNameAuto(selected), (auto) => patchTrack({ name_auto: auto }, auto ? "auto track name" : "freeze track name")),
      fieldRow("Art", selectInput(selected.kind || "layer", [
        ["layer", "Lampenebene"],
        ["global", "Global"],
        ["sub", "Unterebene"],
      ], (value) => {
        const parent = value === "sub" ? (selected.parent_id || doc.tracks.find((track) => track.id !== selected.id)?.id || null) : null;
        patchTrack({ kind: value, parent_id: parent }, "change layer kind");
      })),
      fieldRow("Parent", selectInput(selected.parent_id || "", [
        ["", "None"],
        ...doc.tracks.filter((track) => track.id !== selected.id).map((track) => [track.id, track.name]),
      ], (value) => patchTrack({ parent_id: value || null }, "change parent"))),
      fieldRow("Target", selectInput(selected.target_group, targets, (value) => patchTrack({ target_group: value }, "change target"))),
      fieldRow("Blend", selectInput(selected.blend_mode, [
        ["normal", "Normal"],
        ["add", "Add"],
        ["max", "Max"],
        ["multiply", "Multiply"],
        ["override", "Override"],
      ], (value) => patchTrack({ blend_mode: value }))),
      paramRow(
        { id: selected.id, expressions: {}, start_s: 0, end_s: 1 },
        "track_opacity",
        "Opacity",
        rangeControl(selected.opacity, (value) => patchTrack({ opacity: clamp(value, 0, 1) }), { min: 0, max: 1, step: 0.01 }),
        selected.opacity,
        { noActions: true }
      ),
      fieldRow("Height", rangeControl(selected.height_px || 36, (value) => patchTrack({ height_px: Math.round(value) }, "resize track"), { min: 24, max: 240, step: 1 })),
      toggleRow("Clipping-Maske ↴", selected.clipped_to_below, (value) => patchTrack({ clipped_to_below: value }, "toggle clipping"))
    );
  }
  return section("Tracks", ...rows, ...editor);
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

function filterParamRows(clip, filter, index) {
  const info = state.filters?.find((item) => item.id === filter.type);
  const rows = [];
  for (const spec of info?.params || []) {
    const value = filter.params?.[spec.key] ?? spec.default;
    const setParam = (next) => commitClip(clip.id, `filter ${filter.type} ${spec.key}`, (target) => {
      const item = target.filters.find((entry) => entry.uid === filter.uid) || target.filters[index];
      if (!item) return;
      item.params = { ...(item.params || {}), [spec.key]: next };
    });
    let control;
    if (spec.type === "color") {
      control = colorField(value, setParam);
    } else if (spec.type === "vec3") {
      rows.push(vec3Field(spec.label, value, setParam, { step: spec.step ?? 0.01 }));
      continue;
    } else if (spec.type === "bool") {
      rows.push(toggleRow(spec.label, Boolean(value), setParam));
      continue;
    } else if (spec.type === "select") {
      rows.push(fieldRow(spec.label, selectInput(String(value), spec.options || [], setParam)));
      continue;
    } else {
      control = rangeControl(value, setParam, {
        min: spec.min ?? 0,
        max: spec.max ?? 1,
        step: spec.step ?? 0.01,
      });
    }
    const param = `filter:${filter.uid}:${spec.key}`;
    rows.push(paramRow(clip, param, spec.label, control, value, { noExpression: spec.type === "color" }));
    const expression = expressionEditor(clip, param, value);
    if (expression) rows.push(expression);
  }
  return rows;
}

function renderFiltersSection(clip) {
  const filters = clip.filters || [];
  const cards = filters.map((filter, index) => {
    const info = state.filters?.find((item) => item.id === filter.type);
    const patchFilter = (label, mutate) => commitClip(clip.id, label, (target) => {
      const item = target.filters.find((entry) => entry.uid === filter.uid) || target.filters[index];
      if (item) mutate(item);
    });
    const move = (delta) => commitClip(clip.id, "reorder filter", (target) => {
      const next = index + delta;
      if (next < 0 || next >= target.filters.length) return;
      const [item] = target.filters.splice(index, 1);
      target.filters.splice(next, 0, item);
    });
    const colours = ["", ...TRACK_COLORS];
    const cycleColor = () => {
      const next = colours[(colours.indexOf(filter.color || "") + 1) % colours.length];
      patchFilter("filter colour label", (item) => {
        item.color = next;
      });
    };
    const about = (event) => {
      const rect = event.currentTarget.getBoundingClientRect();
      showContextMenu(rect.left - 130, rect.bottom + 3, [
        { label: info?.name || filter.type, disabled: true },
        info?.description ? { note: info.description } : null,
        { note: `${info?.params?.length || 0} Parameter · ${FILTER_CATEGORIES[filter.type] || "Utility"}` },
      ]);
    };
    return el(
      "div",
      {
        class: `filter-card${filter.enabled ? " enabled" : ""}`,
        style: filter.color ? `box-shadow: inset 2px 0 0 ${filter.color}` : "",
        ondragover: (event) => {
          if (!event.dataTransfer.types.includes("application/x-hueme-filter-index")) return;
          event.preventDefault();
          event.currentTarget.classList.add("drop-target");
        },
        ondragleave: (event) => {
          if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
          event.currentTarget.classList.remove("drop-target");
        },
        ondrop: (event) => {
          event.currentTarget.classList.remove("drop-target");
          const raw = event.dataTransfer.getData("application/x-hueme-filter-index");
          if (raw === "") return;
          event.preventDefault();
          const from = Number(raw);
          if (!Number.isInteger(from) || from === index) return;
          commitClip(clip.id, "reorder filter", (target) => {
            if (from < 0 || from >= target.filters.length) return;
            const [item] = target.filters.splice(from, 1);
            target.filters.splice(index, 0, item);
          });
        },
      },
      el(
        "div",
        { class: "filter-card-head" },
        el("button", {
          class: "button small icon-button filter-enable",
          type: "button",
          title: filter.enabled ? "Filter ausschalten" : "Filter einschalten",
          "aria-label": filter.enabled ? "Disable filter" : "Enable filter",
          style: `color:${filter.enabled ? "var(--ok)" : "var(--text-2)"}`,
          onclick: () => patchFilter("toggle filter", (item) => {
            item.enabled = !item.enabled;
          }),
        }, icon(filter.enabled ? "eye" : "eyeOff")),
        el("span", {
          class: "filter-card-title",
          title: "Ziehen zum Umsortieren",
          draggable: "true",
          ondragstart: (event) => {
            event.dataTransfer.setData("application/x-hueme-filter-index", String(index));
            event.dataTransfer.effectAllowed = "move";
            event.currentTarget.closest(".filter-card")?.classList.add("dragging");
          },
          ondragend: (event) => event.currentTarget.closest(".filter-card")?.classList.remove("dragging"),
        },
          icon("drag"),
          el("strong", { text: info?.name || filter.type }),
          el("span", { class: "mono muted", text: info ? `${info.params.length}p` : "?" })
        ),
        el("button", {
          class: "button small icon-button filter-color",
          type: "button",
          title: filter.color ? `Farblabel ${filter.color}` : "Farblabel setzen",
          "aria-label": "Filter colour label",
          style: filter.color ? `color:${filter.color}` : "",
          onclick: cycleColor,
        }, icon("circle-outline")),
        el("button", {
          class: "button small icon-button", type: "button", title: "Filter-Info",
          "aria-label": "Filter info",
          onclick: about,
        }, icon("info")),
        el("button", {
          class: "button small icon-button", type: "button", title: "Nach oben",
          "aria-label": "Move filter up", disabled: index === 0,
          onclick: () => move(-1),
        }, (() => { const node = icon("chevron"); node.style.transform = "rotate(-90deg)"; return node; })()),
        el("button", {
          class: "button small icon-button", type: "button", title: "Nach unten",
          "aria-label": "Move filter down", disabled: index === filters.length - 1,
          onclick: () => move(1),
        }, (() => { const node = icon("chevron"); node.style.transform = "rotate(90deg)"; return node; })()),
        el("button", {
          class: "button small icon-button", type: "button", title: "Parameter zurücksetzen",
          "aria-label": "Reset filter parameters",
          onclick: () => patchFilter("reset filter", (item) => {
            item.params = {};
          }),
        }, icon("refresh")),
        el("button", {
          class: "button small icon-button danger", type: "button", title: "Filter entfernen",
          "aria-label": "Remove filter",
          onclick: () => commitClip(clip.id, "remove filter", (target) => {
            target.filters.splice(index, 1);
          }),
        }, icon("delete"))
      ),
      el("div", { class: "filter-card-body" }, ...filterParamRows(clip, filter, index))
    );
  });
  return section(
    "Filter Stack",
    cards.length
      ? el("div", { class: "filter-stack" }, ...cards)
      : el("p", { class: "empty-state", text: "No filters. Click a filter in the library to add it." }),
    el("p", { class: "empty-state", text: "Reihenfolge von oben nach unten. Jeder Parameter ist direkt editierbar." })
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
        title: "Remove keyframe",
        "aria-label": "Remove keyframe",
        onclick: () => commitClip(clip.id, "remove keyframe", (target) => {
          target.keyframes.splice(index, 1);
        }),
      }, icon("close"))
    )
  );
  const expressions = Object.entries(clip.expressions || {}).map(([param, expression]) =>
    el(
      "div",
      { class: "chip" },
      el("span", { class: "mono", text: `f ${param}` }),
      el("span", { class: "mono muted", style: "max-width:150px;overflow:hidden;text-overflow:ellipsis", text: expression }),
      el("button", {
        type: "button",
        title: "Remove expression",
        "aria-label": "Remove expression",
        onclick: () => commitClip(clip.id, "remove expression", (target) => {
          delete target.expressions[param];
        }),
      }, icon("close"))
    )
  );
  const lanesOpen = openLanes.has(clip.id);
  const ownerTrack = findClip(clip.id)?.track;
  const trackLanesOpen = Boolean(ownerTrack && openTracks.has(ownerTrack.id));
  const local = currentTime() - clip.start_s;
  return section(
    "Keyframes & Generatoren",
    rows.length ? el("div", { class: "chip-list" }, ...rows) : el("p", { class: "empty-state", text: "No keyframes on this clip." }),
    expressions.length ? el("div", { class: "chip-list" }, ...expressions) : null,
    el("label", { class: "lane-toggle" },
      el("input", {
        type: "checkbox",
        checked: lanesOpen,
        onchange: (event) => {
          if (event.target.checked) openLanes.add(clip.id);
          else openLanes.delete(clip.id);
          requestDraw();
        },
      }),
      el("span", { text: "Parameter-Lanes in der Timeline" })
    ),
    ownerTrack
      ? el("label", { class: "lane-toggle" },
          el("input", {
            type: "checkbox",
            checked: trackLanesOpen,
            onchange: (event) => {
              if (event.target.checked) openTracks.add(ownerTrack.id);
              else openTracks.delete(ownerTrack.id);
              saveLayout();
              renderInspector();
              requestDraw();
            },
          }),
          el("span", { text: "Alle Keyframe-Lanes dieser Spur (Doppelklick = Key setzen)" })
        )
      : null,
    el("div", { class: "section-actions", style: "margin-top:4px" },
      el("button", {
        class: "button small",
        type: "button",
        text: "Key Opacity",
        onclick: () => addOpacityKeyframe(clip.id, clamp(local, 0, clip.end_s - clip.start_s)),
      }),
      el("button", {
        class: "button small",
        type: "button",
        text: "Kick Keys",
        title: "Generate kick keyframes from the ML analysis",
        onclick: () => autoKeyframes("kick", "opacity"),
      }),
      el("button", {
        class: "button small",
        type: "button",
        text: "Snare Keys",
        title: "Generate snare accent keyframes from the ML analysis",
        onclick: () => autoKeyframes("snare", "opacity"),
      }),
      el("button", {
        class: "button small",
        type: "button",
        text: "Energy Keys",
        title: "Generate section-energy keyframes",
        onclick: () => autoKeyframes("energy", "brightness"),
      })
    )
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

async function bakeFixtureOffset(id) {
  const fixture = findFixture(id);
  if (!fixture) return;
  const translate = fixture.transform?.translate || [0, 0, 0];
  if (!translate.some((value) => Math.abs(Number(value)) > 1e-9)) return;
  const geometry = fixture.geometry || {};
  const body = geometry.type === "line"
    ? {
        geometry: {
          start: geometry.start.map((value, axis) => Number((Number(value) + Number(translate[axis])).toFixed(4))),
          end: geometry.end.map((value, axis) => Number((Number(value) + Number(translate[axis])).toFixed(4))),
        },
        transform: { translate: [0, 0, 0] },
      }
    : {
        geometry: {
          pos: geometry.pos.map((value, axis) => Number((Number(value) + Number(translate[axis])).toFixed(4))),
        },
        transform: { translate: [0, 0, 0] },
      };
  await patchFixture(id, body, "Offset in Position eingerechnet");
}

async function bakeSelectionOffsets(ids) {
  const body = (id) => {
    const fixture = findFixture(id);
    if (!fixture) return null;
    const translate = fixture.transform?.translate || [0, 0, 0];
    const geometry = fixture.geometry || {};
    if (geometry.type === "line") {
      return {
        geometry: {
          start: geometry.start.map((value, axis) => Number((Number(value) + Number(translate[axis])).toFixed(4))),
          end: geometry.end.map((value, axis) => Number((Number(value) + Number(translate[axis])).toFixed(4))),
        },
        transform: { translate: [0, 0, 0] },
      };
    }
    return {
      geometry: {
        pos: geometry.pos.map((value, axis) => Number((Number(value) + Number(translate[axis])).toFixed(4))),
      },
      transform: { translate: [0, 0, 0] },
    };
  };
  const patches = ids.map((id) => {
    const payload = body(id);
    return payload ? client.patch(`/api/rig/fixtures/${id}`, payload) : null;
  }).filter(Boolean);
  if (!patches.length) return;
  try {
    await Promise.all(patches);
    await client.refresh();
    toast(`Offsets eingerechnet · ${ids.length} Fixtures`, "success", 1800);
  } catch (error) {
    toast(error.message, "danger");
  }
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

function selectionCentre(ids) {
  const points = ids.map((id) => fixtureCenter(id)).filter((point) => point && Number.isFinite(point[0]));
  if (!points.length) return [0, 0, 0];
  const sum = points.reduce((acc, point) => add(acc, point), [0, 0, 0]);
  return sum.map((value) => value / points.length);
}

async function transformFixtures(ids, compute, label = "transform selection") {
  const centres = new Map(ids.map((id) => [id, fixtureCenter(id)]));
  const pivot = selectionCentre(ids);
  const patches = [];
  for (const id of ids) {
    const fixture = findFixture(id);
    const before = centres.get(id);
    if (!fixture || !before) continue;
    const after = compute([...before], pivot, id);
    if (!after) continue;
    const translate = fixture.transform?.translate || [0, 0, 0];
    const next = translate.map((value, axis) => Number((Number(value) + after[axis] - before[axis]).toFixed(4)));
    patches.push(client.patch(`/api/rig/fixtures/${id}`, { transform: { translate: next } }));
  }
  if (!patches.length) return;
  try {
    await Promise.all(patches);
    await client.refresh();
    toast(`${label} · ${ids.length} Fixtures`, "success", 1800);
  } catch (error) {
    toast(error.message, "danger");
    client.refresh().catch(() => {});
  }
}

function moveSelection(ids, delta) {
  return transformFixtures(
    ids,
    (point) => [point[0] + delta[0], point[1] + delta[1], point[2] + delta[2]],
    "Verschieben"
  );
}

function rotateSelection(ids, degrees) {
  const angle = (degrees * Math.PI) / 180;
  return transformFixtures(
    ids,
    (point, pivot) => {
      const dx = point[0] - pivot[0];
      const dy = point[1] - pivot[1];
      return [
        pivot[0] + dx * Math.cos(angle) - dy * Math.sin(angle),
        pivot[1] + dx * Math.sin(angle) + dy * Math.cos(angle),
        point[2],
      ];
    },
    `Rotieren ${degrees}°`
  );
}

function scaleSelection(ids, factor) {
  return transformFixtures(
    ids,
    (point, pivot) => [
      pivot[0] + (point[0] - pivot[0]) * factor,
      pivot[1] + (point[1] - pivot[1]) * factor,
      pivot[2] + (point[2] - pivot[2]) * factor,
    ],
    `Skalieren ×${factor}`
  );
}

function alignSelection(ids, axis, mode) {
  const points = new Map(ids.map((id) => [id, fixtureCenter(id)]));
  const values = [...points.values()].map((point) => point[axis]);
  if (!values.length) return;
  const target = mode === "min" ? Math.min(...values) : mode === "max" ? Math.max(...values) : (Math.min(...values) + Math.max(...values)) / 2;
  return transformFixtures(
    ids,
    (point) => {
      const next = [...point];
      next[axis] = target;
      return next;
    },
    `Ausrichten ${["X", "Y", "Z"][axis]}`
  );
}

function distributeSelection(ids, axis) {
  if (ids.length < 3) {
    toast("Verteilen braucht mindestens 3 Fixtures.", "info");
    return;
  }
  const entries = ids
    .map((id) => ({ id, point: fixtureCenter(id) }))
    .sort((a, b) => a.point[axis] - b.point[axis]);
  const first = entries[0].point[axis];
  const last = entries[entries.length - 1].point[axis];
  const step = (last - first) / (entries.length - 1);
  const targets = new Map(entries.map((entry, index) => [entry.id, first + step * index]));
  return transformFixtures(
    ids,
    (point, _pivot, id) => {
      const next = [...point];
      next[axis] = targets.get(id) ?? point[axis];
      return next;
    },
    `Verteilen ${["X", "Y", "Z"][axis]}`
  );
}

/* ---------------------------------------------------- Blender-style transforms */
const AXIS_VECTORS = [
  [1, 0, 0],
  [0, 1, 0],
  [0, 0, 1],
];

function previewTransformPoint(point, op, fixtureId) {
  const pivot = op.perFixturePivot ? fixtureCenter(fixtureId) : op.anchor;
  const relative = point.map((value, axis) => (value - pivot[axis]) * op.scale[axis]);
  const angle = (op.rotateDeg * Math.PI) / 180;
  if (op.rotateAxis === 0) {
    const y = relative[1] * Math.cos(angle) - relative[2] * Math.sin(angle);
    const z = relative[1] * Math.sin(angle) + relative[2] * Math.cos(angle);
    relative[1] = y;
    relative[2] = z;
  } else if (op.rotateAxis === 1) {
    const x = relative[0] * Math.cos(angle) + relative[2] * Math.sin(angle);
    const z = -relative[0] * Math.sin(angle) + relative[2] * Math.cos(angle);
    relative[0] = x;
    relative[2] = z;
  } else {
    const x = relative[0] * Math.cos(angle) - relative[1] * Math.sin(angle);
    const y = relative[0] * Math.sin(angle) + relative[1] * Math.cos(angle);
    relative[0] = x;
    relative[1] = y;
  }
  return relative.map(
    (value, axis) => value + pivot[axis] + op.translate[axis]
  );
}

function transformHudText() {
  if (!transformOp) return "";
  const op = transformOp;
  const axisLabel = op.axis == null ? "" : ` AXIS ${"XYZ"[op.axis]}`;
  const planeLabel = op.planeAxis == null ? "" : ` PLANE ${"XYZ"[op.planeAxis]}`;
  const pivotLabel = op.perFixturePivot ? " · EINZEL-PIVOT" : " · GRUPPEN-ZENTRUM";
  const modeLabels = { move: "G MOVE", rotate: "R ROTATE", scale: "S SCALE" };
  let value = "";
  if (op.mode === "move") value = `Δ ${op.translate.map((v) => v.toFixed(2)).join(" / ")} m`;
  else if (op.mode === "rotate") value = `${op.rotateDeg.toFixed(1)}° um ${"XYZ"[op.rotateAxis]}`;
  else value = `× ${op.scale.map((v) => v.toFixed(2)).join(" / ")}`;
  return `${modeLabels[op.mode]}${axisLabel}${planeLabel}${pivotLabel}  ${value}  |  Shift fein · Ctrl Snap · X/Y/Z Achse · 2× Achse = Ebene · Enter/LMB OK · Esc/RMB abbrechen`;
}

function updateTransformHud() {
  if (!dom.transformHud) return;
  if (!transformOp) {
    dom.transformHud.classList.add("hidden");
    return;
  }
  dom.transformHud.textContent = transformHudText();
  dom.transformHud.classList.remove("hidden");
}

function pointInViewport(client) {
  if (!client) return false;
  const rect = dom.viewport.getBoundingClientRect();
  return (
    client.x >= rect.left && client.x <= rect.right &&
    client.y >= rect.top && client.y <= rect.bottom
  );
}

function beginTransform(mode) {
  if (transformOp || selectedFixtures.size === 0) return;
  if (!pointInViewport(lastPointerClient)) {
    toast("Maus über den Viewport bewegen, dann G/R/S.", "info", 2200);
    return;
  }
  const ids = [...selectedFixtures];
  if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  const anchor = selectionCentre(ids);
  const rect = dom.viewport.getBoundingClientRect();
  const client = lastPointerClient || {
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2,
  };
  transformOp = {
    mode,
    ids,
    anchor,
    startX: client.x - rect.left,
    startY: client.y - rect.top,
    lastClient: client,
    axis: null,
    planeAxis: null,
    translate: [0, 0, 0],
    rotateDeg: 0,
    rotateAxis: 2,
    scale: [1, 1, 1],
    fine: false,
    snap: false,
    perFixturePivot: false,
    lastAxisAt: 0,
  };
  viewportDrag = null;
  window.addEventListener("pointermove", onTransformPointerMove);
  updateTransformHud();
  requestDraw();
  toast({ move: "G: Maus bewegen, X/Y/Z für Achse", rotate: "R: Maus kreisen oder X/Y/Z für Achse", scale: "S: Maus bewegen, X/Y/Z für Achse" }[mode], "info", 2600);
}

function onTransformPointerMove(event) {
  if (!transformOp) return;
  const rect = dom.viewport.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const op = transformOp;
  const dx = x - op.startX;
  const dy = y - op.startY;
  op.fine = event.shiftKey;
  op.snap = event.ctrlKey || event.metaKey;
  op.perFixturePivot = event.altKey;
  lastPointerClient = { x: event.clientX, y: event.clientY };
  const basis = cameraBasis();
  const anchorProjected = project(op.anchor);
  const focal = anchorProjected?.focal || (viewportSize.h * 0.5) / Math.tan(camera.fov * 0.5);
  const depth = anchorProjected?.depth || camera.distance;
  const scaleFactor = depth / Math.max(1, focal);
  const fine = op.fine ? 0.15 : 1.0;
  if (op.mode === "move") {
    let world = add(
      scale(basis.right, dx * scaleFactor * fine),
      scale(basis.up, -dy * scaleFactor * fine)
    );
    if (op.axis != null) {
      const unit = AXIS_VECTORS[op.axis];
      const amount = dot(world, unit);
      world = scale(unit, amount);
    }
    if (op.planeAxis != null) world[op.planeAxis] = 0;
    if (op.snap) {
      world = world.map((value) => Math.round(value / 0.1) * 0.1);
    }
    op.translate = world.map((value) => Number(value.toFixed(4)));
  } else if (op.mode === "rotate") {
    let degrees;
    if (anchorProjected) {
      const a0 = Math.atan2(op.startY - anchorProjected.y, op.startX - anchorProjected.x);
      const a1 = Math.atan2(y - anchorProjected.y, x - anchorProjected.x);
      degrees = ((a1 - a0) * 180) / Math.PI;
    } else {
      degrees = (dx - dy) * 0.4;
    }
    degrees *= fine;
    if (op.snap) degrees = Math.round(degrees / 15) * 15;
    op.rotateDeg = Number(degrees.toFixed(2));
    if (op.axis != null) op.rotateAxis = op.axis;
    op.scale = [1, 1, 1];
    op.translate = [0, 0, 0];
  } else {
    let factor = 1 + (dx - dy) * 0.006;
    factor = 1 + (factor - 1) * fine;
    factor = clamp(factor, 0.05, 20);
    if (op.snap) factor = Math.max(0.1, Math.round(factor * 10) / 10);
    const next = [1, 1, 1];
    if (op.axis != null) {
      next[op.axis] = factor;
    } else if (op.planeAxis != null) {
      next[0] = next[1] = next[2] = 1;
      next[op.planeAxis] = factor;
    } else {
      next[0] = next[1] = next[2] = factor;
    }
    op.scale = next.map((value) => Number(value.toFixed(4)));
    op.rotateDeg = 0;
    op.translate = [0, 0, 0];
  }
  updateTransformHud();
  requestDraw();
}

function cycleTransformAxis(axis) {
  if (!transformOp) return;
  const now = performance.now();
  if (transformOp.axis === axis && now - transformOp.lastAxisAt < 420) {
    transformOp.axis = null;
    transformOp.planeAxis = axis;
  } else {
    transformOp.axis = axis;
    transformOp.planeAxis = null;
  }
  transformOp.lastAxisAt = now;
  if (transformOp.mode === "rotate") transformOp.rotateAxis = axis;
  updateTransformHud();
  requestDraw();
}

async function commitTransform() {
  const op = transformOp;
  if (!op) return;
  window.removeEventListener("pointermove", onTransformPointerMove);
  transformOp = null;
  updateTransformHud();
  requestDraw();
  const changed =
    op.translate.some((value) => Math.abs(value) > 1e-6) ||
    Math.abs(op.rotateDeg) > 1e-6 ||
    op.scale.some((value) => Math.abs(value - 1) > 1e-6);
  if (!changed) return;
  try {
    await client.post("/api/rig/fixtures/transform", {
      fixture_ids: op.ids,
      translate: op.translate,
      rotate_axis: "xyz"[op.rotateAxis],
      rotate_deg: op.rotateDeg,
      scale: op.scale,
      pivot: op.perFixturePivot ? null : op.anchor.map((value) => Number(value.toFixed(4))),
    });
    await client.refresh();
    toast(`Transform übernommen · ${op.ids.length} Fixtures`, "success", 1800);
  } catch (error) {
    const missingEndpoint = /404|405|not found|method not allowed/i.test(error.message || "");
    const moveOnly = Math.abs(op.rotateDeg) < 1e-6 && op.scale.every((value) => Math.abs(value - 1) < 1e-6);
    if (missingEndpoint && moveOnly) {
      // Older studio servers only expose per-fixture PATCH. A pure G move can
      // still be saved through translate offsets without the bake endpoint.
      try {
        const patches = op.ids.map((id) => {
          const fixture = findFixture(id);
          const translate = fixture?.transform?.translate || [0, 0, 0];
          return client.patch(`/api/rig/fixtures/${id}`, {
            transform: {
              translate: translate.map((value, axis) => Number((Number(value) + op.translate[axis]).toFixed(4))),
            },
          });
        });
        await Promise.all(patches);
        await client.refresh();
        toast(`Transform übernommen (Offset) · ${op.ids.length} Fixtures`, "success", 2200);
        return;
      } catch (fallbackError) {
        toast(fallbackError.message, "danger");
        return;
      }
    }
    toast(
      missingEndpoint
        ? "Server unterstützt Gruppen-Transforms noch nicht — Studio-Server neu starten."
        : error.message,
      "danger",
      7000
    );
    client.refresh().catch(() => {});
  }
}

function cancelTransform() {
  if (!transformOp) return;
  window.removeEventListener("pointermove", onTransformPointerMove);
  transformOp = null;
  updateTransformHud();
  requestDraw();
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
  rememberLibraryItem("preset", presetId);
  commitClip(found.clip.id, "change preset", (target) => {
    target.preset = presetId;
    target.params = defaultParams(presetId);
  });
}

async function addPresetClipAtPlayhead(presetId) {
  const doc = showDoc();
  if (!doc) return;
  const track = findTrack() || doc.tracks[0];
  if (!track) {
    toast("Add a track first.", "info");
    return;
  }
  const start = clamp(snapTime(currentTime()), 0, Math.max(0, doc.duration_s - 0.25));
  const length = Math.min(Math.max(gridSeconds() * 4, 0.5), doc.duration_s - start);
  if (length <= 0.05) {
    toast("No room for a clip at the playhead.", "info");
    return;
  }
  rememberLibraryItem("preset", presetId);
  const ids = new Set(doc.tracks.flatMap((item) => item.clips.map((clip) => clip.id)));
  const id = uniqueId("clip", ids);
  await commitShow(`insert ${presetId}`, (target) => {
    const targetTrack = target.tracks.find((item) => item.id === track.id);
    if (!targetTrack) return;
    targetTrack.clips.push({
      id,
      name: null,
      start_s: start,
      end_s: start + length,
      preset: presetId,
      opacity: 1,
      params: defaultParams(presetId),
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
  toast(`${presetById(presetId)?.name || presetId} eingefügt`, "success");
}

async function duplicateClip(clipId) {
  const found = findClip(clipId);
  if (!found) return;
  const ids = new Set(showDoc().tracks.flatMap((track) => track.clips.map((clip) => clip.id)));
  const id = uniqueId("clip", ids);
  const length = found.clip.end_s - found.clip.start_s;
  const start = clamp(found.clip.start_s + length, 0, Math.max(0, duration() - length));
  await commitShow("duplicate clip", (doc) => {
    const track = doc.tracks.find((item) => item.id === found.track.id);
    if (!track) return;
    const copy = deepClone(found.clip);
    copy.id = id;
    copy.start_s = start;
    copy.end_s = start + length;
    copy.keyframes = (copy.keyframes || []).filter((keyframe) => keyframe.t_s <= length);
    track.clips.push(copy);
  });
  selectedClipId = id;
  selectedTrackId = found.track.id;
  renderInspector();
  requestDraw();
}

function nextClipboardId(prefix, set) {
  const id = uniqueId(prefix, set);
  set.add(id);
  return id;
}

function copyTrackToClipboard(track, cut = false) {
  if (!track) return;
  clipboard = { kind: "track", data: deepClone(track) };
  toast(`Ebene "${track.name}" kopiert`, "info", 1400);
  if (cut) deleteTrack(track.id);
}

async function pasteTrackData(source, label = "paste track", atIndex = null) {
  const doc = showDoc();
  if (!doc || !source) return null;
  const copy = deepClone(source);
  const trackIds = new Set(doc.tracks.map((track) => track.id));
  const clipIds = new Set(doc.tracks.flatMap((track) => track.clips.map((clip) => clip.id)));
  const newId = nextClipboardId("track", trackIds);
  copy.id = newId;
  if (!copy.name_auto) copy.name = `${copy.name} Kopie`;
  copy.clips = (copy.clips || []).map((clip) => ({
    ...clip,
    id: nextClipboardId("clip", clipIds),
    start_s: clamp(Number(clip.start_s) || 0, 0, Math.max(0, duration() - 0.1)),
    end_s: clamp(Number(clip.end_s) || 1, 0.1, duration()),
  }));
  if (copy.kind === "sub" && !doc.tracks.some((track) => track.id === copy.parent_id)) {
    copy.kind = "layer";
    copy.parent_id = null;
  }
  await commitShow(label, (target) => {
    const index = atIndex === null ? target.tracks.length : atIndex;
    target.tracks.splice(Math.max(0, Math.min(index, target.tracks.length)), 0, copy);
  });
  selectedTrackId = newId;
  selectedClipId = copy.clips[0]?.id || null;
  renderInspector();
  requestDraw();
  return newId;
}

async function pasteTrack() {
  if (clipboard?.kind !== "track") {
    toast("Keine Ebene in der Zwischenablage.", "info");
    return;
  }
  const doc = showDoc();
  const index = doc?.tracks?.findIndex((track) => track.id === selectedTrackId);
  await pasteTrackData(clipboard.data, "paste track", index == null || index < 0 ? null : index + 1);
}

async function duplicateTrack(trackId) {
  const track = findTrack(trackId);
  if (!track) return;
  const doc = showDoc();
  const index = doc?.tracks?.findIndex((item) => item.id === track.id);
  await pasteTrackData(track, "duplicate track", index == null || index < 0 ? null : index + 1);
}

async function renameTrackPrompt(track) {
  const values = await modalForm({
    title: "Ebene umbenennen",
    fields: [{ name: "name", label: "Name", value: track.name }],
    confirmLabel: "Umbenennen",
  });
  if (!values) return;
  const name = values.name.trim();
  if (!name) return;
  commitShow("rename track", (doc) => {
    const target = doc.tracks.find((item) => item.id === track.id);
    if (target) {
      target.name = name;
      target.name_auto = false;
    }
  });
}

function copyClipToClipboard(clip, track, cut = false) {
  if (!clip) return;
  clipboard = { kind: "clip", data: deepClone(clip), sourceTrackId: track?.id || null };
  toast(`Clip "${clipDisplayName(clip)}" kopiert`, "info", 1400);
  if (cut) deleteClip(clip.id);
}

async function pasteClip() {
  if (clipboard?.kind !== "clip") {
    toast("Kein Clip in der Zwischenablage.", "info");
    return;
  }
  const doc = showDoc();
  const track = findTrack() || doc?.tracks?.[0];
  if (!doc || !track) {
    toast("Keine Ebene zum Einfügen.", "info");
    return;
  }
  const source = clipboard.data;
  const length = Math.max(0.1, Number(source.end_s) - Number(source.start_s));
  const start = clamp(snapTime(currentTime()), 0, Math.max(0, duration() - length));
  const clipIds = new Set(doc.tracks.flatMap((item) => item.clips.map((clip) => clip.id)));
  const copy = deepClone(source);
  copy.id = nextClipboardId("clip", clipIds);
  copy.name = source.name || null;
  copy.start_s = start;
  copy.end_s = start + length;
  copy.keyframes = (copy.keyframes || []).filter((keyframe) => keyframe.t_s <= length);
  await commitShow("paste clip", (target) => {
    const item = target.tracks.find((candidate) => candidate.id === track.id);
    if (item) item.clips.push(copy);
  });
  selectedClipId = copy.id;
  selectedTrackId = track.id;
  inspectorTab = "clip";
  updateTabButtons();
  renderInspector();
  requestDraw();
}

async function renameClipPrompt(clip) {
  const values = await modalForm({
    title: "Clip umbenennen",
    fields: [{ name: "name", label: "Name", value: clip.name || "", placeholder: `Auto: ${presetName(clip.preset)}`, optional: true }],
    confirmLabel: "Umbenennen",
    hint: "Leer lassen = Name folgt automatisch dem Effekt.",
  });
  if (!values) return;
  commitClip(clip.id, "rename clip", (target) => {
    target.name = values.name.trim() || null;
  });
}

function resetClipName(clip) {
  commitClip(clip.id, "auto clip name", (target) => {
    target.name = null;
  });
}

async function deleteTrack(trackId) {
  const track = findTrack(trackId);
  if (!track) return;
  await commitShow("delete track", (doc) => {
    doc.tracks = doc.tracks.filter((item) => item.id !== trackId);
    for (const child of doc.tracks) {
      if (child.parent_id === trackId) {
        child.parent_id = null;
        if (child.kind === "sub") child.kind = "layer";
      }
    }
  });
  if (selectedTrackId === trackId) selectedTrackId = null;
  if (selectedClipId && !findClip(selectedClipId)) selectedClipId = null;
  renderInspector();
  requestDraw();
}

function addFilter(filterId) {
  const found = findClip();
  if (!found) {
    toast("Select a clip in the timeline first.", "info");
    return;
  }
  rememberLibraryItem("filter", filterId);
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
    lastHistory = history;
    dom.history.textContent = `undo ${history.undo_depth} / redo ${history.redo_depth}`;
    dom.history.title = history.undo_labels.join("\n") || "No undo history";
    updateTopbar();
  } catch {
    dom.history.textContent = "";
  }
}

async function addTrack(kind = "layer") {
  const doc = showDoc();
  if (!doc) return;
  const ids = new Set(doc.tracks.map((track) => track.id));
  const id = uniqueId(kind === "global" ? "global" : kind === "sub" ? "sub" : "track", ids);
  let parent = null;
  if (kind === "sub") {
    parent = findTrack() || doc.tracks[doc.tracks.length - 1];
    if (!parent) {
      toast("Create a lamp layer before adding a sub-layer.", "info");
      return;
    }
  }
  await commitShow(`add ${kind} track`, (target) => {
    const track = {
      id,
      name: kind === "global" ? `Global ${target.tracks.filter((item) => item.kind === "global").length + 1}` : kind === "sub" ? `Unterebene ${target.tracks.filter((item) => item.kind === "sub").length + 1}` : `Track ${target.tracks.length + 1}`,
      name_auto: true,
      target_group: kind === "sub" && parent ? parent.target_group : "all",
      blend_mode: "normal",
      opacity: 1,
      muted: false,
      solo: false,
      kind,
      parent_id: kind === "sub" && parent ? parent.id : null,
      clipped_to_below: kind === "sub",
      height_px: 36,
      clips: [],
    };
    if (kind === "global") target.tracks.unshift(track);
    else if (kind === "sub" && parent) {
      const index = target.tracks.findIndex((item) => item.id === parent.id);
      target.tracks.splice(index + 1, 0, track);
    } else target.tracks.push(track);
  });
  selectedTrackId = id;
  renderInspector();
  requestDraw();
}

async function toggleClipping() {
  const track = findTrack();
  if (!track) {
    toast("Select a track header first.", "info");
    return;
  }
  await commitShow("toggle clipping mask", (doc) => {
    const target = doc.tracks.find((item) => item.id === track.id);
    if (target) target.clipped_to_below = !target.clipped_to_below;
  });
  requestDraw();
}

function updateAutoShowButton() {
  const button = $("#auto-show");
  if (!button) return;
  const count = (blueprint?.sections || []).length;
  button.disabled = !count;
  button.title = count
    ? `Aus ${count} erkannten Sections einen bearbeitbaren Auto-Show-Track erzeugen`
    : "Audio importieren, um Sections zu erkennen";
}

async function autoShowFromAnalysis() {
  const doc = showDoc();
  const sections = blueprint?.sections || [];
  if (!doc || !sections.length) {
    updateAutoShowButton();
    toast("Kein Audio-Blueprint mit Sections. Erst Audio importieren.", "info");
    return;
  }
  const trackCount = doc.tracks.filter((track) => track.id.startsWith("auto_")).length;
  const trackName = trackCount ? `Auto Show ${trackCount + 1}` : "Auto Show";
  const trackId = uniqueId("auto", new Set(doc.tracks.map((track) => track.id)));
  const existingClipIds = new Set(
    doc.tracks.flatMap((track) => track.clips.map((clip) => clip.id))
  );
  const track = buildAutoShowTrack({
    sections,
    duration: doc.duration_s,
    trackId,
    trackName,
    idPrefix: trackId,
    existingClipIds,
    defaults: defaultParams,
  });
  if (!track) {
    toast("Die Analyse enthält keine nutzbaren Sections.", "info");
    return;
  }
  const confirmed = await modalConfirm(
    "Auto-Show aus Analyse",
    `${sections.length} Sections → neuer Track „${trackName}“ mit ${track.clips.length} Clips. Bestehende Spuren bleiben unverändert; ein Undo entfernt den Track.`,
    "Erzeugen"
  );
  if (!confirmed) return;
  await commitShow("auto show", (target) => {
    target.tracks.push(track);
  });
  selectedTrackId = track.id;
  selectedClipId = track.clips[0]?.id || null;
  if (selectedClipId) inspectorTab = "clip";
  updateTabButtons();
  renderInspector();
  requestDraw();
  toast(`Auto-Show: ${track.clips.length} Clips aus ${sections.length} Sections`, "success");
}

async function autoKeyframes(source = "kick", param = "opacity") {  const id = activeShowId();
  const found = findClip();
  if (!id) {
    toast("No active show.", "info");
    return;
  }
  try {
    const result = await client.post(`/api/shows/${id}/auto-keyframes`, {
      clip_id: found?.clip.id || null,
      source,
      param,
      division: dom.grid.value || "1/8",
      depth: 0.75,
    });
    state.show.document = result.document;
    await refreshHistory();
    renderInspector();
    requestDraw();
    toast(`${result.added} rhythm keyframes from ${result.analyzer}`, "success");
  } catch (error) {
    toast(error.message, "danger");
  }
}

async function clearClipFilters() {
  const found = findClip();
  if (!found) {
    toast("Select a clip first.", "info");
    return;
  }
  await commitClip(found.clip.id, "clear filters", (target) => {
    target.filters = [];
  });
}

async function newShow() {
  const values = await modalForm({
    title: "Neue Show",
    fields: [
      { name: "name", label: "Name", value: "New Show" },
      { name: "id", label: "Id", value: "new_show" },
      { name: "duration_s", label: "Dauer (s)", value: "60" },
      { name: "bpm", label: "BPM", value: "120" },
    ],
    confirmLabel: "Create",
  });
  if (!values) return;
  const doc = {
    schema_version: 1,
    id: values.id || slug(values.name),
    name: values.name || "New Show",
    duration_s: Math.max(1, Number(values.duration_s) || 60),
    match: { artist: "", title: values.name || "", normalized_keys: [], ma_item_ids: [], duration_s: 0 },
    timing: { bpm: Math.max(1, Number(values.bpm) || 120) },
    tracks: [
      {
        id: "base",
        name: "Base Field",
        name_auto: true,
        target_group: "all",
        blend_mode: "normal",
        opacity: 0.7,
        clips: [
          {
            id: "base_clip",
            start_s: 0,
            end_s: Math.max(1, Number(values.duration_s) || 60),
            preset: "solid",
            params: defaultParams("solid"),
          },
        ],
      },
    ],
  };
  try {
    await client.post("/api/shows", doc);
    await client.setTransport({ show_id: doc.id });
    await client.refresh();
    toast(`Created ${doc.name}`, "success");
  } catch (error) {
    toast(error.message, "danger");
  }
}

async function saveSnapshot() {
  const id = activeShowId();
  if (!id) return;
  const values = await modalForm({
    title: "Snapshot",
    fields: [{ name: "label", label: "Label", value: "manual snapshot" }],
    confirmLabel: "Save",
  });
  if (!values) return;
  try {
    await client.post(`/api/shows/${id}/snapshot`, { label: values.label || "manual snapshot" });
    await refreshHistory();
    toast("Snapshot saved", "success");
  } catch (error) {
    toast(error.message, "danger");
  }
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
      name: null,
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
              title: "Remove from group",
              "aria-label": "Remove from group",
              onclick: () => {
                const next = group.fixtures.filter((id) => id !== fixtureId);
                if (!next.length) {
                  deleteGroup(group);
                  return;
                }
                saveGroup({ ...group, fixtures: next });
              },
            }, icon("close"))
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

/* ----------------------------------------------------------------- builder */
function openEffectBuilder() {
  const doc = showDoc();
  if (!doc) {
    toast("Kein ShowDoc geladen.", "info");
    return;
  }
  const selected = findClip();
  const presetId = selected?.clip.preset || "solid";
  const recipe = {
    preset: presetId,
    params: deepClone(selected?.clip.params || defaultParams(presetId)),
    spatial_mask: deepClone(selected?.clip.spatial_mask || { kind: "none", softness: 0.08, invert: false }),
    filters: deepClone(selected?.clip.filters || []),
    keyframes: deepClone(selected?.clip.keyframes || []),
    expressions: deepClone(selected?.clip.expressions || {}),
    opacity: selected?.clip.opacity ?? 1,
  };
  const targetId = selected?.clip.id || null;
  const dialog = document.createElement("dialog");
  dialog.className = "effect-builder";
  let time = currentTime();
  let playing = false;
  let timer = 0;
  let previewBusy = false;
  let previewUrl = "";
  let filterSeq = 0;
  const preview = el("img", { class: "builder-preview", alt: "Effect Builder preview" });
  const timeSlider = el("input", {
    class: "slider",
    type: "range",
    min: 0,
    max: Math.max(0.1, duration()),
    step: 0.01,
    value: time,
    "aria-label": "Preview time",
  });
  const timeReadout = el("span", { class: "mono muted", text: `${time.toFixed(2)} s` });
  const playButton = el("button", { class: "button small", type: "button", text: "Play" });
  const body = el("div", { class: "dialog-body builder-body" });

  const currentTarget = () => (targetId ? findClip(targetId)?.clip || null : null);
  const syncAutomation = () => {
    const clip = currentTarget();
    if (!clip) return;
    recipe.params = deepClone(clip.params || recipe.params);
    recipe.keyframes = deepClone(clip.keyframes || []);
    recipe.expressions = deepClone(clip.expressions || {});
    render();
    refreshPreview();
  };
  const previewClip = () => ({
    ...deepClone(recipe),
    id: "builder_preview",
    start_s: 0,
    end_s: Math.max(1, duration()),
  });
  async function refreshPreview() {
    if (previewBusy) return;
    previewBusy = true;
    try {
      const response = await fetch("/api/preview/effect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clip: previewClip(),
          t: Number(time.toFixed(3)),
          timing: showDoc()?.timing,
          width: 320,
          height: 180,
        }),
      });
      if (response.ok) {
        const url = URL.createObjectURL(await response.blob());
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = url;
        preview.src = url;
      }
    } catch {
      // The preview is an editor aid; a failed frame must not close the dialog.
    } finally {
      previewBusy = false;
    }
  }
  const setTime = (next) => {
    time = clamp(Number(next) || 0, 0, Math.max(0, duration()));
    timeSlider.value = String(time);
    timeReadout.textContent = `${time.toFixed(2)} s`;
    refreshPreview();
  };
  const stop = () => {
    playing = false;
    if (timer) window.clearInterval(timer);
    timer = 0;
    playButton.textContent = "Play";
  };
  const togglePlay = () => {
    if (playing) {
      stop();
      return;
    }
    playing = true;
    playButton.textContent = "Pause";
    timer = window.setInterval(() => {
      const next = time + 0.1;
      setTime(next > duration() ? 0 : next);
    }, 120);
  };
  playButton.addEventListener("click", togglePlay);
  timeSlider.addEventListener("input", () => setTime(timeSlider.value));

  function builderParamControl(spec) {
    const value = recipe.params?.[spec.key] ?? spec.default;
    const update = (next) => {
      recipe.params = { ...recipe.params, [spec.key]: next };
      refreshPreview();
    };
    if (spec.type === "color") return colorField(value, update);
    if (spec.type === "palette") {
      return selectInput(String(value), (state?.palettes || []).map((item) => [item.id, item.name]), update);
    }
    if (spec.type === "vec3") {
      return vec3Field(spec.label, value, update, { step: spec.step ?? 0.01 }).querySelector(".triple");
    }
    if (spec.type === "bool") {
      const input = el("input", { type: "checkbox" });
      input.checked = Boolean(value);
      input.addEventListener("change", () => update(input.checked));
      return el("label", { class: "toggle" }, input, el("span"));
    }
    if (spec.type === "select") {
      return selectInput(String(value), spec.options || [], update);
    }
    return rangeControl(value, update, {
      min: spec.min ?? 0,
      max: spec.max ?? 1,
      step: spec.step ?? 0.01,
    });
  }

  function renderPresetSection() {
    const preset = presetById(recipe.preset);
    const rows = [
      fieldRow("Preset", selectInput(recipe.preset, (state.presets || []).map((item) => [item.id, item.name]), (next) => {
        recipe.preset = next;
        recipe.params = defaultParams(next);
        render();
        refreshPreview();
      })),
      toggleRow("Enabled", recipe.opacity > 0.001, (enabled) => {
        recipe.opacity = enabled ? 1 : 0;
        refreshPreview();
      }),
    ];
    const target = currentTarget();
    for (const spec of preset?.params || []) {
      const control = builderParamControl(spec);
      if (target && ["range", "number", "color"].includes(spec.type)) {
        rows.push(paramRow(target, spec.key, spec.label, control, recipe.params?.[spec.key] ?? spec.default));
      } else {
        rows.push(fieldRow(spec.label, control));
      }
    }
    return el("div", { class: "builder-column" }, el("h4", { text: "Preset / Parameter" }), ...rows);
  }

  function addFilter(filterId) {
    filterSeq += 1;
    recipe.filters.push({
      type: filterId,
      enabled: true,
      params: {},
      uid: `builder_${filterSeq}_${filterId}`,
    });
    render();
    refreshPreview();
  }

  function filterCard(filter, index) {
    const info = state.filters?.find((item) => item.id === filter.type);
    const rows = [];
    for (const spec of info?.params || []) {
      const value = filter.params?.[spec.key] ?? spec.default;
      const update = (next) => {
        filter.params = { ...(filter.params || {}), [spec.key]: next };
        refreshPreview();
      };
      if (spec.type === "color") rows.push(fieldRow(spec.label, colorField(value, update)));
      else if (spec.type === "bool") rows.push(toggleRow(spec.label, Boolean(value), update));
      else if (spec.type === "select") rows.push(fieldRow(spec.label, selectInput(String(value), spec.options || [], update)));
      else {
        rows.push(fieldRow(spec.label, rangeControl(value, update, {
          min: spec.min ?? 0,
          max: spec.max ?? 1,
          step: spec.step ?? 0.01,
        })));
      }
    }
    return el(
      "div",
      { class: `filter-card${filter.enabled ? " enabled" : ""}` },
      el(
        "div",
        { class: "filter-card-head" },
        el("button", {
          class: "button small icon-button", type: "button",
          title: filter.enabled ? "Filter ausschalten" : "Filter einschalten",
          "aria-label": "Toggle filter",
          onclick: () => { filter.enabled = !filter.enabled; render(); refreshPreview(); },
        }, icon(filter.enabled ? "eye" : "eyeOff")),
        el("span", { class: "filter-card-title" }, el("strong", { text: info?.name || filter.type })),
        el("button", {
          class: "button small icon-button", type: "button", title: "Nach oben",
          "aria-label": "Move filter up", disabled: index === 0,
          onclick: () => {
            const [item] = recipe.filters.splice(index, 1);
            recipe.filters.splice(index - 1, 0, item);
            render(); refreshPreview();
          },
        }, (() => { const node = icon("chevron"); node.style.transform = "rotate(-90deg)"; return node; })()),
        el("button", {
          class: "button small icon-button", type: "button", title: "Nach unten",
          "aria-label": "Move filter down", disabled: index === recipe.filters.length - 1,
          onclick: () => {
            const [item] = recipe.filters.splice(index, 1);
            recipe.filters.splice(index + 1, 0, item);
            render(); refreshPreview();
          },
        }, (() => { const node = icon("chevron"); node.style.transform = "rotate(90deg)"; return node; })()),
        el("button", {
          class: "button small icon-button danger", type: "button", title: "Filter entfernen",
          "aria-label": "Remove filter",
          onclick: () => { recipe.filters.splice(index, 1); render(); refreshPreview(); },
        }, icon("delete"))
      ),
      el("div", { class: "filter-card-body" }, ...rows)
    );
  }

  function renderRecipeSections() {
    const preset = el(
      "div",
      { class: "builder-column" },
      renderPresetSection()
    );
    const stack = el(
      "div",
      { class: "builder-column" },
      el("h4", { text: "Recipe" }),
      fieldRow("Maske", selectInput(recipe.spatial_mask.kind || "none", [
        ["none", "None"], ["plane_sweep", "Plane Sweep"], ["sphere_narrow", "Sphere"],
        ["box", "Box"], ["parity", "Parity"],
      ], (kind) => {
        recipe.spatial_mask = { ...recipe.spatial_mask, kind };
        render();
        refreshPreview();
      })),
      fieldRow("Softness", rangeControl(recipe.spatial_mask.softness ?? 0.08, (next) => {
        recipe.spatial_mask = { ...recipe.spatial_mask, softness: next };
        refreshPreview();
      }, { min: 0, max: 1, step: 0.01 })),
      toggleRow("Invert", Boolean(recipe.spatial_mask.invert), (next) => {
        recipe.spatial_mask = { ...recipe.spatial_mask, invert: next };
        refreshPreview();
      }),
      fieldRow("Filter hinzufügen", selectInput("", [
        ["", "—"],
        ...Object.values(state.filters || {}).map((info) => [info.id, info.name]),
      ], (filterId) => { if (filterId) addFilter(filterId); })),
      recipe.filters.length
        ? el("div", { class: "filter-stack" }, ...recipe.filters.map(filterCard))
        : el("p", { class: "empty-state", text: "Noch keine Filter im Rezept." })
    );
    return el("div", { class: "builder-columns" }, preset, stack);
  }

  function render() {
    body.replaceChildren(
      el("div", { class: "builder-transport" },
        playButton,
        el("button", { class: "button small", type: "button", text: "Start", onclick: () => { stop(); setTime(0); } }),
        timeSlider,
        timeReadout
      ),
      preview,
      renderRecipeSections()
    );
  }

  const nameInput = el("input", {
    class: "input",
    type: "text",
    value: `${presetName(recipe.preset)} Look`,
    placeholder: "Name des Looks",
  });
  const save = async () => {
    try {
      const saved = await client.post("/api/user-presets", {
        name: nameInput.value.trim() || `${presetName(recipe.preset)} Look`,
        preset: recipe.preset,
        params: recipe.params,
        spatial_mask: recipe.spatial_mask,
        filters: recipe.filters,
      });
      state.user_presets = [...(state.user_presets || []), saved];
      toast(`Look gespeichert: ${saved.name}`, "success");
    } catch (error) {
      toast(error.message, "danger");
    }
  };
  const apply = async () => {
    const clip = currentTarget();
    if (!clip) {
      toast("Kein Clip ausgewählt. Look speichern oder als neuen Clip einsetzen.", "info");
      return;
    }
    await commitClip(clip.id, "apply effect builder look", (target) => {
      target.preset = recipe.preset;
      target.params = deepClone(recipe.params);
      target.spatial_mask = deepClone(recipe.spatial_mask);
      target.filters = deepClone(recipe.filters);
      target.keyframes = deepClone(recipe.keyframes);
      target.expressions = deepClone(recipe.expressions);
    });
    stop();
    dialog.close("apply");
  };
  const insert = async () => {
    const docNow = showDoc();
    const track = findTrack() || docNow?.tracks?.[0];
    if (!docNow || !track) {
      toast("Erst eine Spur anlegen.", "info");
      return;
    }
    const start = clamp(snapTime(currentTime()), 0, Math.max(0, docNow.duration_s - 0.25));
    const length = Math.min(Math.max(gridSeconds() * 4, 0.5), docNow.duration_s - start);
    if (length <= 0.05) {
      toast("Kein Platz am Playhead.", "info");
      return;
    }
    const ids = new Set(docNow.tracks.flatMap((item) => item.clips.map((clip) => clip.id)));
    const id = uniqueId("clip", ids);
    await commitShow("insert builder look", (current) => {
      const targetTrack = current.tracks.find((item) => item.id === track.id);
      if (!targetTrack) return;
      targetTrack.clips.push({
        id,
        name: null,
        start_s: start,
        end_s: start + length,
        preset: recipe.preset,
        opacity: recipe.opacity,
        params: deepClone(recipe.params),
        keyframes: deepClone(recipe.keyframes),
        expressions: deepClone(recipe.expressions),
        spatial_mask: deepClone(recipe.spatial_mask),
        filters: deepClone(recipe.filters),
        snap_zcl: false,
      });
    });
    selectedClipId = id;
    selectedTrackId = track.id;
    inspectorTab = "clip";
    updateTabButtons();
    renderInspector();
    requestDraw();
    stop();
    dialog.close("insert");
  };

  const form = document.createElement("form");
  form.method = "dialog";
  form.append(
    el("div", { class: "dialog-head", text: "Effekt-Baukasten" }),
    body,
    el("div", { class: "builder-save" }, nameInput,
      el("button", { class: "button small", type: "button", text: "Save Look", onclick: save })
    ),
    el("div", { class: "dialog-actions" },
      el("button", { class: "button", type: "button", text: "Schließen", onclick: () => dialog.close("cancel") }),
      el("button", { class: "button", type: "button", text: "Als Clip einsetzen", onclick: insert }),
      el("button", { class: "button primary", type: "button", text: "Auf Clip anwenden", onclick: apply })
    )
  );
  dialog.append(form);
  dialog.addEventListener("close", () => {
    stop();
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    dialog.remove();
  });
  document.body.append(dialog);
  render();
  dialog.showModal();
  refreshPreview();
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

/* --------------------------------------------------------------- context menu */
function hideContextMenu() {
  if (!contextMenu) return;
  contextMenu.remove();
  contextMenu = null;
  document.removeEventListener("pointerdown", outsideContextMenu, true);
  document.removeEventListener("keydown", contextMenuKey, true);
  window.removeEventListener("blur", hideContextMenu);
  window.removeEventListener("resize", hideContextMenu);
}

function outsideContextMenu(event) {
  if (contextMenu && !contextMenu.contains(event.target)) hideContextMenu();
}

function contextMenuKey(event) {
  if (event.key === "Escape") hideContextMenu();
}

function contextMenuButton(item) {
  return el(
    "button",
    {
      type: "button",
      role: item.children ? "menu" : "menuitem",
      class: item.danger ? "danger" : undefined,
      disabled: item.disabled || undefined,
      "aria-haspopup": item.children ? "menu" : undefined,
      onclick: (event) => {
        if (item.children) {
          event.preventDefault();
          event.stopPropagation();
          const wrap = event.currentTarget.parentElement;
          wrap.classList.toggle("open");
          return;
        }
        hideContextMenu();
        item.fn?.();
      },
    },
    item.icon ? icon(item.icon) : null,
    el("span", { class: "ctx-text", text: item.label }),
    item.checked ? icon("check") : null,
    item.key ? el("span", { class: "ctx-key", text: item.key }) : null,
    item.children ? icon("chevron") : null
  );
}

function fillContextMenu(menu, items) {
  for (const item of items) {
    if (!item) continue;
    if (item.separator) {
      menu.append(el("div", { class: "ctx-sep" }));
      continue;
    }
    if (item.note) {
      menu.append(el("p", { class: "ctx-note", text: item.note }));
      continue;
    }
    if (item.children?.length) {
      const submenu = el("div", { class: "ctx-menu ctx-sub", role: "menu" });
      fillContextMenu(submenu, item.children);
      menu.append(
        el("div", { class: "ctx-sub-wrap" }, contextMenuButton(item), submenu)
      );
      continue;
    }
    menu.append(contextMenuButton(item));
  }
}

function showContextMenu(x, y, items) {
  hideContextMenu();
  const menu = el("div", { class: "ctx-menu", role: "menu" });
  fillContextMenu(menu, items);
  if (x > window.innerWidth * 0.55) menu.classList.add("flip");
  document.body.append(menu);
  const rect = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(4, Math.min(x, window.innerWidth - rect.width - 6))}px`;
  menu.style.top = `${Math.max(4, Math.min(y, window.innerHeight - rect.height - 6))}px`;
  contextMenu = menu;
  window.setTimeout(() => {
    document.addEventListener("pointerdown", outsideContextMenu, true);
  }, 0);
  document.addEventListener("keydown", contextMenuKey, true);
  window.addEventListener("blur", hideContextMenu);
  window.addEventListener("resize", hideContextMenu);
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
function runCommand(command) {
  switch (command) {
    case "new-show": newShow(); break;
    case "load-audio": dom.blueprintFile.click(); break;
    case "snapshot": saveSnapshot(); break;
    case "live-deck": window.location.href = "/live"; break;
    case "undo": undoShow(); break;
    case "redo": redoShow(); break;
    case "split": splitClip(); break;
    case "delete-clip": if (selectedClipId) deleteClip(selectedClipId); break;
    case "add-clip": addClip(); break;
    case "add-layer": addTrack("layer"); break;
    case "add-global": addTrack("global"); break;
    case "add-sub": addTrack("sub"); break;
    case "toggle-clip": toggleClipping(); break;
    case "toggle-mute": toggleMute(); break;
    case "focus-filters":
      $("#section-filters")?.classList.add("open");
      $("#section-filters")?.scrollIntoView({ block: "start" });
      break;
    case "clear-filters": clearClipFilters(); break;
    case "auto-keys": autoKeyframes("kick", "opacity"); break;
    case "auto-show": autoShowFromAnalysis(); break;
    case "camera-persp":
    case "camera-front":
    case "camera-top":
    case "camera-side":
      setCameraNamed(command.replace("camera-", ""));
      break;
    case "fit-show":
      view.start = 0;
      view.end = Math.max(1, duration());
      requestDraw();
      break;
    case "camera-fit": fitCamera(); break;
    case "ma-pairing":
      showSendspinPairing();
      break;
    case "sync-restart":
      client
        .post("/api/sync/restart")
        .then((sync) => {
          state.sync = sync;
          updateTopbar();
          toast("Sendspin neu verbunden", "success");
        })
        .catch((error) => toast(error.message, "danger"));
      break;
    case "system-restart":
      modalConfirm(
        "Studio neu starten",
        "Der Server-Prozess wird neu gestartet. Hardware und Sync werden kurz unterbrochen.",
        "Neu starten"
      ).then(async (confirmed) => {
        if (!confirmed) return;
        try {
          await client.post("/api/system/restart");
          toast("Studio startet neu …", "info", 4000);
          window.setTimeout(() => window.location.reload(), 3000);
        } catch (error) {
          toast(error.message, "danger");
        }
      });
      break;
    case "shortcuts":
      modalConfirm(
        "Tastenkürzel",
        "Space Play/Pause | ←/→ Frame vor/zurück (Timeline) | Shift+←/→ 10 Frames | G Move | R Rotate | S Scale (mit Auswahl) | X/Y/Z Achse | 2× Achse = Ebene | Shift fein | Ctrl Snap | Alt Einzel-Pivot | Enter/LMB bestätigen | Esc/RMB abbrechen | Numpad 1/3/7 = Front/Rechts/Top ortho | Numpad 9 = Gegenansicht | Numpad 5 = Persp/Ortho | Numpad 0 = Persp Fit | S Split (ohne Auswahl) | M Mute | Delete Clip löschen | Strg+Z Undo | Strg+Shift+Z Redo | Alt+Klick Track = Clipping | Alt+Wheel = Spurhöhe | Shift+Alt+Wheel = alle Spuren",
        "OK"
      );
      break;
    case "about":
      modalConfirm(
        "Hueme Studio",
        "Deterministische 3D-Show-Engine mit NLE-Timeline, ML-Audioanalyse, MCP-Co-Director und Hardware-HAL.",
        "OK"
      );
      break;
    default:
      break;
  }
}

function bindEvents() {
  setIcon(dom.toStart, "start", "Go to start");
  setIcon(dom.toEnd, "end", "Go to end");
  setIcon(dom.loop, "loop", "Loop");
  setIcon(dom.undo, "undo", "Undo");
  setIcon(dom.redo, "redo", "Redo");
  setIcon(dom.mcpClose, "close", "Close MCP drawer");
  setIcon($("#split"), "split", "Split clip");
  setIcon($("#mute"), "mute", "Mute track");
  setIcon($("#add-clip"), "plus", "Add clip");
  setIcon($("#zoom-in"), "zoomIn", "Zoom in");
  setIcon($("#zoom-out"), "zoomOut", "Zoom out");
  updateTransport();

  dom.timecode.addEventListener("click", () => {
    timecodeMode = (timecodeMode + 1) % 3;
    updateTimecode();
    toast(["Timecode", "Frames @ FPS", "Takt Bar.Beat.Sub"][timecodeMode], "info", 1200);
  });

  document.querySelectorAll(".menu-button").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const menu = button.closest(".menu");
      const open = menu.classList.contains("open");
      document.querySelectorAll(".menu.open").forEach((item) => item.classList.remove("open"));
      if (!open) menu.classList.add("open");
    });
  });
  document.querySelectorAll(".menu-drop [data-command]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".menu.open").forEach((item) => item.classList.remove("open"));
      runCommand(button.dataset.command);
    });
  });
  document.addEventListener("click", () => {
    document.querySelectorAll(".menu.open").forEach((item) => item.classList.remove("open"));
  });

  document.querySelectorAll("[data-workspace]").forEach((button) => {
    button.addEventListener("click", () => {
      workspace = button.dataset.workspace;
      dom.root.classList.toggle("stage-mode", workspace === "stage");
      document.querySelectorAll("[data-workspace]").forEach((item) => {
        item.classList.toggle("active", item.dataset.workspace === workspace);
        item.setAttribute("aria-selected", String(item.dataset.workspace === workspace));
      });
      resizeAll();
    });
  });

  dom.hw?.addEventListener("click", async () => {
    if (!state) return;
    try {
      state.hardware = await client.post("/api/hardware", {
        enabled: !state.hardware?.enabled,
      });
      updateTopbar();
      const hardware = state.hardware || {};
      if (hardware.enabled) {
        if (hardware.last_error) {
          toast(`HW an, aber Treiberfehler: ${hardware.last_error}`, "danger", 8000);
        } else {
          toast(
            `Hardware ON · ${hardware.started ? "Treiber verbunden" : "Treiber startet"} · ${hardware.tx_operations || 0} tx`,
            "success"
          );
        }
        // Let the HAL send a few frames, then report whether frames really left.
        window.setTimeout(async () => {
          try {
            state.hardware = await client.get("/api/hardware");
            updateTopbar();
            const hw = state.hardware || {};
            if (hw.enabled) {
              if (hw.last_error) toast(`HW Fehler: ${hw.last_error}`, "danger", 8000);
              else if (!hw.tx_operations) {
                toast(
                  "HW an, aber 0 Frames gesendet: Fixture-Output/Adresse prüfen (Rig & Output).",
                  "info",
                  8000
                );
              } else {
                toast(`HW sendet · ${hw.tx_operations} tx`, "success", 3000);
              }
            }
          } catch {
            /* status refresh is best-effort */
          }
        }, 1400);
      } else {
        toast("Hardware output disabled", "info");
      }
    } catch (error) {
      toast(error.message, "danger");
    }
  });

  dom.addGlobal?.addEventListener("click", () => addTrack("global"));
  dom.addSub?.addEventListener("click", () => addTrack("sub"));
  dom.autoKeys?.addEventListener("click", () => autoKeyframes("kick", "opacity"));
  $("#auto-show")?.addEventListener("click", autoShowFromAnalysis);

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
  dom.librarySearch?.addEventListener("input", () => {
    libraryQuery = dom.librarySearch.value.trim().toLowerCase();
    renderPresets();
    renderFilters();
  });
  dom.librarySort?.addEventListener("click", () => {
    librarySort = librarySort === "alpha" ? "category" : "alpha";
    try {
      localStorage.setItem(LIBRARY_SORT_KEY, librarySort);
    } catch {
      // Fall back to the session-only preference.
    }
    updateLibrarySort();
    renderPresets();
    renderFilters();
  });
  updateLibrarySort();
  dom.paletteNew?.addEventListener("click", () => openPaletteEditor(null, { onApply: applyPaletteToClip }));
  dom.fxToggle?.addEventListener("click", () => setFxMode(!fxMode));
  dom.syncMarkers?.addEventListener("click", syncMarkersFromBlueprint);
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
  dom.effectBuilder?.addEventListener("click", openEffectBuilder);
  dom.grid.addEventListener("change", requestDraw);
  // Optional: an older cached studio.html must not break timeline binding.
  dom.sync?.addEventListener("click", async () => {
    if (!state?.sync?.configured) {
      toast("Sendspin/MA sync is not configured", "info");
      return;
    }
    try {
      state.sync = await client.post("/api/sync", { enabled: !state.sync.enabled });
      updateTopbar();
    } catch (error) {
      toast(error.message, "danger");
    }
  });
  dom.maPair?.addEventListener("click", showSendspinPairing);
  dom.maRestart?.addEventListener("click", () => {
    client
      .post("/api/sync/restart")
      .then((sync) => {
        state.sync = sync;
        updateTopbar();
        toast("Sendspin neu verbunden", "success");
      })
      .catch((error) => toast(error.message, "danger"));
  });
  $("#zoom-out").addEventListener("click", () => zoomTimeline(1.4, (view.start + view.end) / 2));
  $("#zoom-in").addEventListener("click", () => zoomTimeline(0.7, (view.start + view.end) / 2));
  $("#zoom-fit").addEventListener("click", () => {
    view.start = 0;
    view.end = Math.max(1, duration());
    requestDraw();
  });

  $("#camera-fit").addEventListener("click", () => fitCamera());
  $("#viewport-help")?.addEventListener("click", () => runCommand("shortcuts"));

  window.addEventListener("resize", resizeAll);
  window.addEventListener("pointermove", (event) => {
    lastPointerClient = { x: event.clientX, y: event.clientY };
  }, { passive: true });
  bindScrub();
  bindSplitters();
  bindViewport();
  bindTimeline();
  bindKeyboard();
}

function bindViewport() {
  const canvas = dom.viewport;
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());

  canvas.addEventListener("pointerdown", (event) => {
    if (transformOp) {
      if (event.button === 0) commitTransform();
      else if (event.button === 2) cancelTransform();
      return;
    }
    canvas.focus();
    hideContextMenu();
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
    if (event.altKey || event.button === 2) {
      viewportDrag = {
        mode: "orbit",
        x0: x,
        y0: y,
        yaw: camera.yaw,
        pitch: camera.pitch,
        button: event.button,
        moved: false,
      };
      canvas.setPointerCapture(event.pointerId);
      return;
    }
    if (event.button !== 0) return;
    const cube = viewCubeAt(x, y);
    if (cube) {
      if (cube.toggle) toggleCameraProjection();
      else setOrthoView(cube.axis, cube.opposite !== event.shiftKey);
      return;
    }
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
    if (!viewportDrag) {
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const cube = viewCubeAt(x, y);
      const nextHover = cube ? (cube.toggle ? "toggle" : cube.key) : null;
      if (viewCubeHover !== nextHover) {
        viewCubeHover = nextHover;
        requestDraw();
      }
      canvas.style.cursor = cube ? "pointer" : fixtureAt(x, y) ? "move" : "crosshair";
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const drag = viewportDrag;
    if (drag.mode === "orbit") {
      if (Math.hypot(x - drag.x0, y - drag.y0) > 4) drag.moved = true;
      camera.yaw = drag.yaw + (x - drag.x0) * 0.007;
      camera.pitch = clamp(drag.pitch - (y - drag.y0) * 0.007, -1.45, 1.45);
      requestDraw();
    } else if (drag.mode === "pan") {
      const basis = cameraBasis();
      const focal = (viewportSize.h * 0.5) / Math.tan(camera.fov * 0.5);
      const scaleFactor = camera.ortho
        ? camera.orthoSize / Math.max(1, viewportSize.h)
        : drag.distance / focal;
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
    if (drag.mode === "orbit" && drag.button === 2 && !drag.moved) {
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const hit = fixtureAt(x, y);
      if (hit) {
        if (!selectedFixtures.has(hit)) selectFixture(hit, false);
        const fixture = findFixture(hit);
        showContextMenu(event.clientX, event.clientY, [
          { label: `${fixture?.name || hit} auswählen`, fn: () => selectFixture(hit, false) },
          { label: fixture?.output?.enabled === false ? "Output aktivieren" : "Output deaktivieren", fn: () => patchFixture(hit, { output: { enabled: fixture?.output?.enabled === false } }) },
          { label: "In Inspector öffnen", fn: () => { inspectorTab = "fixture"; updateTabButtons(); renderInspector(); } },
          { separator: true },
          { label: "Lampen-Kegel einpassen", fn: () => { const point = fixtureCenter(hit); camera.target = point; camera.distance = Math.max(2, camera.distance * 0.75); requestDraw(); } },
          { label: "Fixture löschen", danger: true, fn: () => deleteFixtures([hit]) },
        ]);
      } else {
        showContextMenu(event.clientX, event.clientY, [
          { label: "Raum einpassen", fn: () => fitCamera() },
          { label: "Perspektive", fn: () => { camera.yaw = -0.78; camera.pitch = 0.52; fitCamera(); } },
          { label: "Front", fn: () => { camera.yaw = 0; camera.pitch = 0; fitCamera(); } },
          { label: "Top", fn: () => { camera.yaw = 0; camera.pitch = 1.45; fitCamera(); } },
          { separator: true },
          { label: "Auswahl aufheben", fn: () => { selectedFixtures.clear(); renderFixtures(); renderInspector(); requestDraw(); } },
        ]);
      }
      requestDraw();
      return;
    }
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

  canvas.addEventListener("pointerleave", () => {
    if (viewCubeHover !== null) {
      viewCubeHover = null;
      requestDraw();
    }
  });

  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    if (camera.ortho) {
      camera.orthoSize = clamp(camera.orthoSize * (1 + Math.sign(event.deltaY) * 0.1), 0.5, 120);
    } else {
      camera.distance = clamp(camera.distance * (1 + Math.sign(event.deltaY) * 0.1), 1, 80);
    }
    requestDraw();
  }, { passive: false });
}

function persistTrackHeights(trackHeights) {
  const doc = showDoc();
  if (!doc) return;
  window.clearTimeout(heightCommitTimer);
  heightCommitTimer = window.setTimeout(() => {
    commitShow("resize tracks", (target) => {
      for (const [id, height] of Object.entries(trackHeights)) {
        const track = target.tracks.find((item) => item.id === id);
        if (track) track.height_px = Math.round(clamp(height, 24, 240));
      }
    });
  }, 180);
  requestDraw();
}

function timelineHeaderZone(x, y, row) {
  if (x > view.labelW) return null;
  const indent = row.depth * 10;
  if (Math.abs(y - (row.y + row.h)) <= 5) return "resize";
  if (x <= 18 + indent) return "expand";
  if (x >= view.labelW - 38 && x <= view.labelW - 22) return "mute";
  if (x >= view.labelW - 20 && x <= view.labelW - 4) return "solo";
  return "header";
}

function timelineCursorFor(x, y) {
  if (!showDoc()) return "default";
  const scroll = timelineScrollMetrics();
  if (scroll && x >= scroll.x - 2 && y >= scroll.y && y <= scroll.y + scroll.h) return "over-scroll";
  if (y <= view.rulerH) return "pointer";
  const audioBottom = view.rulerH + view.audioH;
  if (y <= audioBottom) {
    if (x <= view.labelW && Math.abs(y - audioBottom) <= 7) return "over-resize";
    return "pointer";
  }
  const hit = rowAtY(y);
  if (!hit) return "default";
  const zone = timelineHeaderZone(x, y, hit.row);
  if (zone === "resize") return "over-resize";
  if (zone) return "over-header";
  if (y >= hit.row.laneY) return "pointer";
  const clipHit = clipAtPoint(x, y);
  if (clipHit) {
    const edge = 6;
    if (Math.abs(x - clipHit.rect.x0) <= edge || Math.abs(x - clipHit.rect.x1) <= edge) return "over-trim";
    return "over-clip";
  }
  return "pointer";
}

function setTimelineCursor(cursor) {
  const cls = cursor === "dragging" ? "dragging-clip" : cursor;
  dom.timeline.className = `timeline-canvas${cls && cls !== "default" ? ` ${cls}` : ""}`;
}

function bindTimeline() {
  const canvas = dom.timeline;
  dom.timelineLayers?.addEventListener("click", () => setTimelineMode("layers"));
  dom.timelineAnalysis?.addEventListener("click", () => setTimelineMode("analysis"));

  canvas.addEventListener("pointerleave", () => {
    if (analysisHover) {
      analysisHover = null;
      requestDraw();
    }
  });

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
    if (timelineMode === "analysis") {
      const time = clamp(snapTime(xToTime(x)), 0, duration());
      client.seek(time);
      analysisHover = { x, y, time };
      timelineDrag = { mode: "scrub" };
      canvas.setPointerCapture(event.pointerId);
      requestDraw();
      return;
    }
    const audioBottom = view.rulerH + view.audioH;
    if (y <= audioBottom) {
      if (x <= view.labelW && Math.abs(y - audioBottom) <= 7) {
        audioLaneDrag = { startY: y, startH: view.audioH };
        canvas.setPointerCapture(event.pointerId);
        return;
      }
      const time = clamp(xToTime(x), 0, duration());
      client.seek(time);
      timelineDrag = { mode: "scrub" };
      canvas.setPointerCapture(event.pointerId);
      return;
    }
    const scroll = timelineScrollMetrics();
    if (scroll && x >= scroll.x - 2 && y >= scroll.y && y <= scroll.y + scroll.h) {
      scrollDrag = { startY: y, startScroll: view.scrollY };
      scrollFromTimelineY(y);
      canvas.setPointerCapture(event.pointerId);
      return;
    }
    const hit = rowAtY(y);
    if (!hit) return;
    const row = hit.row;
    const track = row.track;
    if (x <= view.labelW) {
      const zone = timelineHeaderZone(x, y, row);
      if (zone === "resize") {
        heightDrag = {
          startY: y,
          trackId: track.id,
          startH: row.baseH,
          all: event.shiftKey,
          heights: Object.fromEntries(buildTimelineRows().map((item) => [item.track.id, item.track.height_px || 36])),
        };
        canvas.setPointerCapture(event.pointerId);
        return;
      }
      if (zone === "expand") {
        if (openTracks.has(track.id)) openTracks.delete(track.id);
        else openTracks.add(track.id);
        saveLayout();
        requestDraw();
        return;
      }
      if (zone === "mute") {
        commitShow("mute track", (doc) => {
          const target = doc.tracks.find((item) => item.id === track.id);
          if (target) target.muted = !target.muted;
        });
        return;
      }
      if (zone === "solo") {
        commitShow("solo track", (doc) => {
          const target = doc.tracks.find((item) => item.id === track.id);
          if (target) target.solo = !target.solo;
        });
        return;
      }
      if (event.altKey) {
        commitShow("toggle clipping mask", (doc) => {
          const target = doc.tracks.find((item) => item.id === track.id);
          if (target) target.clipped_to_below = !target.clipped_to_below;
        });
        return;
      }
      selectedTrackId = track.id;
      selectedClipId = null;
      inspectorTab = "show";
      updateTabButtons();
      renderInspector();
      requestDraw();
      return;
    }

    const keyframeHit = keyframeAtPoint(x, y);
    if (keyframeHit && !event.altKey) {
      selectedClipId = keyframeHit.clip.id;
      selectedTrackId = keyframeHit.track.id;
      inspectorTab = "clip";
      updateTabButtons();
      renderInspector();
      timelineDrag = {
        mode: "keyframe",
        clip: deepClone(keyframeHit.clip),
        trackId: keyframeHit.track.id,
        param: keyframeHit.param,
        index: keyframeHit.index,
        t0: keyframeHit.keyframe.t_s,
        x0: x,
      };
      canvas.setPointerCapture(event.pointerId);
      return;
    }

    const clipHit = clipAtPoint(x, y);
    if (clipHit) {
      selectedClipId = clipHit.clip.id;
      selectedTrackId = clipHit.track.id;
      inspectorTab = "clip";
      updateTabButtons();
      renderInspector();
      requestDraw();
      const edge = 6;
      if (Math.abs(x - clipHit.rect.x0) <= edge) {
        timelineDrag = { mode: "trim-start", clip: deepClone(clipHit.clip), trackId: clipHit.track.id, x0: x };
      } else if (Math.abs(x - clipHit.rect.x1) <= edge) {
        timelineDrag = { mode: "trim-end", clip: deepClone(clipHit.clip), trackId: clipHit.track.id, x0: x };
      } else {
        timelineDrag = { mode: "move", clip: deepClone(clipHit.clip), trackId: clipHit.track.id, x0: x };
      }
      canvas.setPointerCapture(event.pointerId);
      return;
    }

    selectedTrackId = track.id;
    selectedClipId = null;
    inspectorTab = "clip";
    updateTabButtons();
    renderInspector();
    requestDraw();
    client.seek(clamp(xToTime(x), 0, duration()));
    timelineDrag = { mode: "scrub" };
    canvas.setPointerCapture(event.pointerId);
  });

  canvas.addEventListener("pointermove", (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (scrollDrag) {
      scrollFromTimelineY(y);
      return;
    }
    if (audioLaneDrag) {
      view.audioH = clamp(audioLaneDrag.startH + (y - audioLaneDrag.startY), 38, 240);
      setTimelineCursor("over-resize");
      requestDraw();
      return;
    }
    if (heightDrag) {
      const delta = y - heightDrag.startY;
      const rows = buildTimelineRows();
      for (const row of rows) {
        if (heightDrag.all || row.track.id === heightDrag.trackId) {
          row.track.height_px = Math.round(clamp((heightDrag.heights[row.track.id] || 36) + delta, 24, 240));
        }
      }
      setTimelineCursor("over-resize");
      requestDraw();
      return;
    }
    if (timelineMode === "analysis") {
      const time = clamp(snapTime(xToTime(x)), 0, duration());
      analysisHover = { x, y, time };
      if (timelineDrag?.mode === "scrub") client.seek(time);
      setTimelineCursor("pointer");
      requestDraw();
      return;
    }
    if (!timelineDrag) {
      setTimelineCursor(timelineCursorFor(x, y));
      return;
    }
    if (timelineDrag.mode === "scrub") {
      setTimelineCursor("pointer");
      client.seek(clamp(xToTime(x), 0, duration()));
      return;
    }
    if (timelineDrag.mode === "keyframe") {
      timelineDrag.previewX = x;
      setTimelineCursor("ew-resize");
      requestDraw();
      return;
    }
    setTimelineCursor(timelineDrag.mode === "move" ? "dragging" : "over-trim");
    const time = xToTime(x);
    const clip = timelineDrag.clip;
    const snap = (value) => (event.altKey ? value : snapTime(value, clip.id));
    if (timelineDrag.mode === "move") {
      const delta = snap(clip.start_s + (x - timelineDrag.x0) / Math.max(1, timelineSize.w - view.labelW) * (view.end - view.start)) - clip.start_s;
      const length = clip.end_s - clip.start_s;
      const start = clamp(clip.start_s + delta, 0, Math.max(0, duration() - length));
      const hit = rowAtY(y);
      timelineDrag.preview = {
        clipId: clip.id,
        start_s: start,
        end_s: start + length,
        trackId: hit?.row.track.id || timelineDrag.trackId,
      };
    } else if (timelineDrag.mode === "trim-start") {
      const start = clamp(snap(time), 0, clip.end_s - 0.1);
      timelineDrag.preview = { clipId: clip.id, start_s: start, end_s: clip.end_s };
    } else if (timelineDrag.mode === "trim-end") {
      const end = clamp(snap(time), clip.start_s + 0.1, duration());
      timelineDrag.preview = { clipId: clip.id, start_s: clip.start_s, end_s: end };
    }
    requestDraw();
  });

  canvas.addEventListener("pointerup", async (event) => {
    if (scrollDrag) {
      scrollDrag = null;
      setTimelineCursor(timelineCursorFor(event.clientX - canvas.getBoundingClientRect().left, event.clientY - canvas.getBoundingClientRect().top));
      requestDraw();
      return;
    }
    if (audioLaneDrag) {
      audioLaneDrag = null;
      setTimelineCursor("default");
      requestDraw();
      return;
    }
    if (heightDrag) {
      const heights = Object.fromEntries(
        buildTimelineRows().map((row) => [row.track.id, row.track.height_px || 36])
      );
      heightDrag = null;
      setTimelineCursor("default");
      persistTrackHeights(heights);
      return;
    }
    if (!timelineDrag) return;
    const drag = timelineDrag;
    timelineDrag = null;
    setTimelineCursor("default");
    if (drag.mode === "scrub") {
      requestDraw();
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    if (drag.mode === "keyframe") {
      const local = clamp(xToTime(x) - drag.clip.start_s, 0, drag.clip.end_s - drag.clip.start_s);
      await commitClip(drag.clip.id, "move keyframe", (target) => {
        const matches = (target.keyframes || []).filter((keyframe) => keyframe.param === drag.param);
        const keyframe = matches[drag.index];
        if (keyframe) keyframe.t_s = Number(local.toFixed(4));
        target.keyframes.sort((a, b) => a.t_s - b.t_s);
      });
      return;
    }
    const preview = drag.preview;
    if (!preview) {
      requestDraw();
      return;
    }
    await commitShow(drag.mode === "move" ? "move clip" : "trim clip", (doc) => {
      let sourceTrack = doc.tracks.find((item) => item.id === drag.trackId);
      let clip = sourceTrack?.clips.find((item) => item.id === preview.clipId);
      if (!clip) return;
      if (drag.mode === "move") {
        const length = clip.end_s - clip.start_s;
        clip.start_s = preview.start_s;
        clip.end_s = preview.start_s + length;
        if (preview.trackId && preview.trackId !== drag.trackId) {
          const targetTrack = doc.tracks.find((item) => item.id === preview.trackId);
          if (targetTrack) {
            sourceTrack.clips = sourceTrack.clips.filter((item) => item.id !== preview.clipId);
            targetTrack.clips.push(clip);
            sourceTrack = targetTrack;
            clip = targetTrack.clips[targetTrack.clips.length - 1];
          }
        }
      } else if (drag.mode === "trim-start") {
        clip.start_s = preview.start_s;
      } else {
        clip.end_s = preview.end_s;
      }
      clip.fade_in_s = Math.min(clip.fade_in_s, clip.end_s - clip.start_s);
      clip.fade_out_s = Math.min(clip.fade_out_s, clip.end_s - clip.start_s);
      clip.keyframes = (clip.keyframes || []).filter((keyframe) => keyframe.t_s <= clip.end_s - clip.start_s);
    });
    if (drag.mode === "move" && preview.trackId) selectedTrackId = preview.trackId;
    requestDraw();
  });

  canvas.addEventListener("pointercancel", () => {
    timelineDrag = null;
    heightDrag = null;
    audioLaneDrag = null;
    scrollDrag = null;
    setTimelineCursor("default");
    requestDraw();
  });

  canvas.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (y <= view.rulerH) {
      showContextMenu(event.clientX, event.clientY, [
        { label: "Playhead hierher", fn: () => client.seek(clamp(xToTime(x), 0, duration())) },
        { label: "Show einpassen", fn: () => { view.start = 0; view.end = Math.max(1, duration()); requestDraw(); } },
        { label: "Alle Spuren einpassen", fn: () => { openTracks.clear(); saveLayout(); requestDraw(); } },
      ]);
      return;
    }
    const hit = rowAtY(y);
    if (!hit) {
      showContextMenu(event.clientX, event.clientY, [
        { label: "Clip am Playhead hinzufügen", fn: () => addClip() },
        { label: "Neue Lampenebene", fn: () => addTrack("layer") },
        { label: "Globale Ebene", fn: () => addTrack("global") },
      ]);
      return;
    }
    const row = hit.row;
    const track = row.track;
    if (x <= view.labelW) {
      const zone = timelineHeaderZone(x, y, row);
      if (zone === "mute" || zone === "solo") return;
      selectedTrackId = track.id;
      selectedClipId = null;
      inspectorTab = "show";
      updateTabButtons();
      renderInspector();
      requestDraw();
      const blendModes = [
        ["normal", "Normal"],
        ["add", "Add"],
        ["max", "Max"],
        ["multiply", "Multiply"],
        ["override", "Override"],
      ];
      showContextMenu(event.clientX, event.clientY, [
        { label: `${track.name} einstellen`, icon: "settings", fn: () => { inspectorTab = "show"; updateTabButtons(); renderInspector(); } },
        { separator: true },
        { label: "Kopieren", icon: "copy", key: "Ctrl+C", fn: () => copyTrackToClipboard(track) },
        { label: "Ausschneiden", icon: "cut", key: "Ctrl+X", fn: () => copyTrackToClipboard(track, true) },
        { label: "Einfügen", icon: "paste", key: "Ctrl+V", disabled: clipboard?.kind !== "track", fn: pasteTrack },
        { label: "Duplizieren", icon: "duplicate", fn: () => duplicateTrack(track.id) },
        { label: "Umbenennen…", icon: "rename", fn: () => renameTrackPrompt(track) },
        { label: "Name folgt Effekt", icon: "auto", checked: trackNameAuto(track), fn: () => commitShow("toggle auto track name", (doc) => {
          const target = doc.tracks.find((item) => item.id === track.id);
          if (target) target.name_auto = !target.name_auto;
        }) },
        { label: "Blend-Modus", icon: "blend", children: blendModes.map(([value, label]) => ({
          label,
          checked: (track.blend_mode || "normal") === value,
          fn: () => commitShow("change blend mode", (doc) => {
            const target = doc.tracks.find((item) => item.id === track.id);
            if (target) target.blend_mode = value;
          }),
        })) },
        { label: track.muted ? "Stummschaltung aus" : "Stummschalten", icon: "mute", fn: () => commitShow("mute track", (doc) => { const target = doc.tracks.find((item) => item.id === track.id); if (target) target.muted = !target.muted; }) },
        { label: track.solo ? "Solo aus" : "Solo", icon: "solo", fn: () => commitShow("solo track", (doc) => { const target = doc.tracks.find((item) => item.id === track.id); if (target) target.solo = !target.solo; }) },
        { label: track.clipped_to_below ? "Clipping-Maske lösen" : "Als Clipping-Maske", icon: "layers", checked: track.clipped_to_below, fn: () => commitShow("toggle clipping mask", (doc) => { const target = doc.tracks.find((item) => item.id === track.id); if (target) target.clipped_to_below = !target.clipped_to_below; }) },
        { label: "Unterebene hinzufügen", icon: "layers", fn: () => { selectedTrackId = track.id; addTrack("sub"); } },
        { separator: true },
        { label: "Ebene löschen", icon: "delete", danger: true, key: "Del", fn: () => deleteTrack(track.id) },
      ]);
      return;
    }
    const clipHit = clipAtPoint(x, y);
    if (clipHit) {
      selectedClipId = clipHit.clip.id;
      selectedTrackId = clipHit.track.id;
      inspectorTab = "clip";
      updateTabButtons();
      renderInspector();
      requestDraw();
      showContextMenu(event.clientX, event.clientY, [
        { label: "Teilen", icon: "split", key: "S", fn: () => splitClip() },
        { label: "Kopieren", icon: "copy", key: "Ctrl+C", fn: () => copyClipToClipboard(clipHit.clip, clipHit.track) },
        { label: "Ausschneiden", icon: "cut", key: "Ctrl+X", fn: () => copyClipToClipboard(clipHit.clip, clipHit.track, true) },
        { label: "Einfügen", icon: "paste", key: "Ctrl+V", disabled: clipboard?.kind !== "clip", fn: pasteClip },
        { label: "Duplizieren", icon: "duplicate", fn: () => duplicateClip(clipHit.clip.id) },
        { label: "Umbenennen…", icon: "rename", fn: () => renameClipPrompt(clipHit.clip) },
        { label: "Name automatisch", icon: "auto", checked: !clipHit.clip.name, fn: () => resetClipName(clipHit.clip) },
        { label: "Preset wechseln…", icon: "fx", fn: () => { inspectorTab = "clip"; updateTabButtons(); renderInspector(); $("#library-search")?.focus(); } },
        { separator: true },
        { label: "ZCL Snap", icon: "lock", checked: clipHit.clip.snap_zcl, fn: () => commitClip(clipHit.clip.id, "snap", (target) => { target.snap_zcl = !target.snap_zcl; }) },
        { label: "Clip löschen", icon: "delete", danger: true, key: "Del", fn: () => deleteClip(clipHit.clip.id) },
      ]);
      return;
    }
    selectedTrackId = track.id;
    selectedClipId = null;
    renderInspector();
    showContextMenu(event.clientX, event.clientY, [
      { label: "Clip am Playhead hier einfügen", fn: () => addClip() },
      { label: "Neue Unterebene", fn: () => { selectedTrackId = track.id; addTrack("sub"); } },
      { label: openTracks.has(track.id) ? "Keyframe-Lanes schließen" : "Keyframe-Lanes öffnen", fn: () => { if (openTracks.has(track.id)) openTracks.delete(track.id); else openTracks.add(track.id); saveLayout(); requestDraw(); } },
    ]);
  });

  canvas.addEventListener("dragover", (event) => {
    if (
      event.dataTransfer.types.includes("application/x-hueme-preset") ||
      event.dataTransfer.types.includes("application/x-hueme-filter")
    ) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    }
  });

  canvas.addEventListener("drop", async (event) => {
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const presetData = event.dataTransfer.getData("application/x-hueme-preset");
    const filterData = event.dataTransfer.getData("application/x-hueme-filter");
    if (presetData) {
      let payload;
      try { payload = JSON.parse(presetData); } catch { return; }
      const hit = rowAtY(y);
      if (!hit) {
        toast("Drop the preset on a track row.", "info");
        return;
      }
      const trackId = hit.row.track.id;
      const start = clamp(snapTime(xToTime(x)), 0, Math.max(0, duration() - 0.2));
      const length = Math.min(Math.max(gridSeconds() * 4, 0.5), duration() - start);
      if (length <= 0.05) return;
      const ids = new Set(showDoc().tracks.flatMap((track) => track.clips.map((clip) => clip.id)));
      const id = uniqueId("clip", ids);
      await commitShow(`drop ${payload.preset}`, (doc) => {
        const track = doc.tracks.find((item) => item.id === trackId);
        if (!track) return;
        track.clips.push({
          id,
          name: null,
          start_s: start,
          end_s: start + length,
          preset: payload.preset,
          opacity: 1,
          params: defaultParams(payload.preset),
          keyframes: [],
          spatial_mask: { kind: "none" },
          filters: [],
          snap_zcl: false,
        });
      });
      rememberLibraryItem("preset", payload.preset);
      selectedClipId = id;
      selectedTrackId = trackId;
      renderInspector();
      requestDraw();
      return;
    }
    if (filterData) {
      let payload;
      try { payload = JSON.parse(filterData); } catch { return; }
      const hit = clipAtPoint(x, y);
      if (!hit) {
        toast("Drop the filter directly on a clip.", "info");
        return;
      }
      rememberLibraryItem("filter", payload.filter);
      await commitClip(hit.clip.id, "add filter", (target) => {
        target.filters = target.filters || [];
        target.filters.push({ type: payload.filter, enabled: true, params: {} });
      });
    }
  });

  canvas.addEventListener("dblclick", (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (y <= view.rulerH + view.audioH || x <= view.labelW) return;
    const hit = rowAtY(y);
    if (!hit || y < hit.row.laneY) return;
    const lane = Math.floor((y - hit.row.laneY) / hit.row.laneH);
    const info = hit.row.lanes[lane];
    if (!info) return;
    const time = xToTime(x);
    const clip = hit.row.track.clips.find((item) => time >= item.start_s && time <= item.end_s);
    if (!clip) return;
    const local = clamp(snapTime(time) - clip.start_s, 0, clip.end_s - clip.start_s);
    const numeric = Number(clip.params?.[info.param] ?? clip.opacity ?? 0.5);
    commitClip(clip.id, `keyframe ${info.param}`, (target) => {
      target.keyframes = (target.keyframes || []).filter(
        (keyframe) => !(keyframe.param === info.param && Math.abs(keyframe.t_s - local) <= 1e-4)
      );
      target.keyframes.push({ t_s: Number(local.toFixed(4)), param: info.param, value: Number.isFinite(numeric) ? numeric : 0.5, easing: "linear" });
      target.keyframes.sort((a, b) => a.t_s - b.t_s);
    });
    toast(`Keyframe ${info.param} @ ${local.toFixed(2)}s`, "success", 1800);
  });

  canvas.addEventListener("wheel", (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (event.altKey) {
      event.preventDefault();
      if (y > view.rulerH && y < view.rulerH + view.audioH) {
        view.audioH = clamp(view.audioH + (event.deltaY > 0 ? -6 : 6), 38, 240);
        requestDraw();
        return;
      }
      const hit = rowAtY(y);
      if (!hit) return;
      const delta = event.deltaY > 0 ? -4 : 4;
      const rows = buildTimelineRows();
      const heights = {};
      for (const row of rows) {
        if (event.shiftKey || row.track.id === hit.row.track.id) {
          row.track.height_px = Math.round(clamp((row.track.height_px || 36) + delta, 24, 240));
        }
        heights[row.track.id] = row.track.height_px || 36;
      }
      persistTrackHeights(heights);
      requestDraw();
      return;
    }
    event.preventDefault();
    if (event.ctrlKey || event.metaKey) {
      zoomTimeline(event.deltaY > 0 ? 1.15 : 0.87, xToTime(x));
      return;
    }
    if (!event.shiftKey && !event.ctrlKey && !event.metaKey) {
      view.scrollY = Math.max(0, view.scrollY + (event.deltaY > 0 ? 48 : -48));
      buildTimelineRows();
      requestDraw();
      return;
    }
    {
      const span = view.end - view.start;
      const shift = (event.deltaY || event.deltaX) > 0 ? span * 0.08 : -span * 0.08;
      const maxStart = Math.max(0, duration() - span);
      view.start = clamp(view.start + shift, 0, maxStart);
      view.end = view.start + span;
      requestDraw();
    }
  }, { passive: false });
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
    if (transformOp) {
      const key = event.key.toLowerCase();
      if (key === "escape") {
        event.preventDefault();
        cancelTransform();
        return;
      }
      if (key === "enter") {
        event.preventDefault();
        commitTransform();
        return;
      }
      if (key === "x" || key === "y" || key === "z") {
        event.preventDefault();
        cycleTransformAxis({ x: 0, y: 1, z: 2 }[key]);
        return;
      }
      if (key === "shift" || key === "control" || key === "alt" || key === "meta") {
        if (lastPointerClient) {
          onTransformPointerMove({
            clientX: lastPointerClient.x,
            clientY: lastPointerClient.y,
            shiftKey: event.shiftKey,
            ctrlKey: event.ctrlKey,
            metaKey: event.metaKey,
            altKey: event.altKey,
          });
        }
      }
      return;
    }
    const target = event.target;
    if (target instanceof HTMLElement && (target.matches("input, textarea, select") || target.isContentEditable)) return;
    const meta = event.metaKey || event.ctrlKey;
    if (
      !meta && !event.altKey &&
      (event.key === "ArrowLeft" || event.key === "ArrowRight") &&
      timelineActive()
    ) {
      event.preventDefault();
      stepFrames(
        event.key === "ArrowRight" ? 1 : -1,
        event.shiftKey ? 10 : 1
      );
      return;
    }
    if (["Numpad1", "Numpad3", "Numpad7"].includes(event.code)) {
      event.preventDefault();
      const axis = event.code === "Numpad1" ? 1 : event.code === "Numpad3" ? 0 : 2;
      setOrthoView(axis, event.ctrlKey || event.shiftKey);
      return;
    }
    if (event.code === "Numpad9") {
      event.preventDefault();
      setOrthoView(lastFlatView.axis, !lastFlatView.opposite);
      return;
    }
    if (event.code === "Numpad5") {
      event.preventDefault();
      toggleCameraProjection();
      return;
    }
    if (event.code === "Numpad0") {
      event.preventDefault();
      camera.ortho = false;
      fitCamera();
      return;
    }
    if (["Numpad2", "Numpad4", "Numpad6", "Numpad8"].includes(event.code)) {
      event.preventDefault();
      const step = (15 * Math.PI) / 180;
      if (event.code === "Numpad4") camera.yaw -= step;
      else if (event.code === "Numpad6") camera.yaw += step;
      else if (event.code === "Numpad8") camera.pitch = clamp(camera.pitch + step, -1.5, 1.5);
      else camera.pitch = clamp(camera.pitch - step, -1.5, 1.5);
      requestDraw();
      return;
    }
    if (!meta && selectedFixtures.size > 0 && pointInViewport(lastPointerClient)) {
      const key = event.key.toLowerCase();
      if (key === "g" || key === "r" || key === "s") {
        event.preventDefault();
        beginTransform(key === "g" ? "move" : key === "r" ? "rotate" : "scale");
        return;
      }
    }
    const key = event.key.toLowerCase();
    if (!meta && !event.altKey && key === "u") {
      event.preventDefault();
      revealAnimatedParams(event.shiftKey);
      return;
    }
    if (meta && (key === "c" || key === "x")) {
      const found = findClip();
      if (found) {
        event.preventDefault();
        copyClipToClipboard(found.clip, found.track, key === "x");
        return;
      }
      const track = findTrack();
      if (track) {
        event.preventDefault();
        copyTrackToClipboard(track, key === "x");
        return;
      }
    }
    if (meta && key === "v") {
      if (clipboard?.kind === "clip") {
        event.preventDefault();
        pasteClip();
        return;
      }
      if (clipboard?.kind === "track") {
        event.preventDefault();
        pasteTrack();
        return;
      }
    }
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
  loadLayout();
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
  window.setInterval(async () => {
    if (!state?.hardware?.enabled) return;
    try {
      state.hardware = await client.get("/api/hardware");
      updateTopbar();
    } catch {
      /* hardware status polling is best-effort */
    }
  }, 3000);
  try {
    await client.start();
    await refreshHistory();
  } catch (error) {
    toast(error.message, "danger");
  }
}

boot();
