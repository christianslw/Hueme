// Build a first-pass show from blueprint sections. The result is ordinary
// ShowDoc data: one layer track with one clip per section, so every clip stays
// editable and the whole scaffold is a single undo step.
export const AUTO_SHOW_PRESETS = {
  intro: "turrell_horizon",
  verse: "gradient_3d",
  build: "wave_interference",
  pre_drop: "shutter_matrix",
  drop: "emitter_swarm",
  chorus: "noise_plasma",
  bridge: "caustic_abyss",
  breakdown: "caustic_abyss",
  break: "candle_flicker",
  outro: "turrell_horizon",
};

const AUTO_SHOW_FILTERS = {
  build: ["beat_gate"],
  pre_drop: ["beat_gate"],
  drop: ["beat_gate"],
  chorus: ["beat_gate"],
};

const round = (value) => Math.round(value * 10000) / 10000;

export function titleForSection(label) {
  return String(label || "section")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function recipeForSection(label) {
  const key = String(label || "").toLowerCase();
  return {
    preset: AUTO_SHOW_PRESETS[key] || "gradient_3d",
    filters: AUTO_SHOW_FILTERS[key] || [],
  };
}

export function buildAutoShowTrack({
  sections = [],
  duration = 0,
  trackId,
  trackName = "Auto Show",
  idPrefix = "auto",
  existingClipIds = [],
  defaults = () => ({}),
}) {
  const total = Number(duration) || 0;
  if (!trackId || total <= 0) return null;
  const valid = sections
    .map((section) => ({
      label: String(section.label || "section").toLowerCase(),
      start: Number(section.start),
      end: Number(section.end),
    }))
    .filter(
      (section) =>
        Number.isFinite(section.start) &&
        Number.isFinite(section.end) &&
        section.end > section.start + 0.05
    )
    .sort((a, b) => a.start - b.start);
  if (!valid.length) return null;

  const totals = {};
  for (const section of valid) totals[section.label] = (totals[section.label] || 0) + 1;
  const used = new Set(existingClipIds);
  const clips = [];
  let lastEnd = 0;
  let index = 0;
  for (const section of valid) {
    const start = Math.max(lastEnd, Math.max(0, Math.min(section.start, total)));
    const end = Math.max(start, Math.min(section.end, total));
    if (end - start < 0.1) continue;
    index += 1;
    let clipId = `${idPrefix}_${index}`;
    let suffix = 2;
    while (used.has(clipId)) clipId = `${idPrefix}_${index}_${suffix++}`;
    used.add(clipId);
    const recipe = recipeForSection(section.label);
    const count = clips.filter((clip) => clip.section === section.label).length + 1;
    const fade = Math.min(0.25, (end - start) / 4);
    clips.push({
      id: clipId,
      name: totals[section.label] > 1
        ? `${titleForSection(section.label)} ${count}`
        : titleForSection(section.label),
      section: section.label,
      start_s: round(start),
      end_s: round(end),
      fade_in_s: round(fade),
      fade_out_s: round(fade),
      preset: recipe.preset,
      opacity: 1,
      params: defaults(recipe.preset),
      keyframes: [],
      spatial_mask: { kind: "none" },
      filters: recipe.filters.map((type, filterIndex) => ({
        type,
        enabled: true,
        uid: `${clipId}_f${filterIndex}_${type}`,
        params: {},
      })),
      snap_zcl: false,
    });
    lastEnd = end;
  }
  if (!clips.length) return null;
  return {
    id: trackId,
    name: trackName,
    name_auto: false,
    target_group: "all",
    blend_mode: "normal",
    opacity: 1,
    muted: false,
    solo: false,
    kind: "layer",
    parent_id: null,
    clipped_to_below: false,
    height_px: 44,
    clips: clips.map(({ section, ...clip }) => clip),
  };
}
