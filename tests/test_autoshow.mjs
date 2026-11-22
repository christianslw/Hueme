import assert from "node:assert/strict";
import test from "node:test";

import {
  buildAutoShowTrack,
  recipeForSection,
} from "../hueme_studio/static/autoshow.js";

const sections = [
  { label: "intro", start: 0, end: 8 },
  { label: "verse", start: 8, end: 16 },
  { label: "chorus", start: 16, end: 24 },
  { label: "drop", start: 24, end: 32 },
];

test("section labels map to a preset and optional filter", () => {
  assert.equal(recipeForSection("intro").preset, "turrell_horizon");
  assert.deepEqual(recipeForSection("chorus").filters, ["beat_gate"]);
  assert.deepEqual(recipeForSection("unknown").filters, []);
});

test("one clip per section with numbered repeated names", () => {
  const track = buildAutoShowTrack({
    sections: [...sections, { label: "verse", start: 32, end: 40 }],
    duration: 40,
    trackId: "auto_1",
    idPrefix: "auto_1",
    defaults: () => ({ tone: 1 }),
  });
  assert.equal(track.clips.length, 5);
  assert.deepEqual(track.clips.map((clip) => clip.name), [
    "Intro", "Verse 1", "Chorus", "Drop", "Verse 2",
  ]);
  assert.equal(track.clips[0].params.tone, 1);
});

test("clips are clamped to the show and never overlap", () => {
  const track = buildAutoShowTrack({
    sections: [
      { label: "intro", start: -2, end: 5 },
      { label: "verse", start: 4, end: 12 },
      { label: "outro", start: 12, end: 99 },
    ],
    duration: 20,
    trackId: "auto_1",
    idPrefix: "auto_1",
  });
  assert.deepEqual(track.clips.map((clip) => [clip.start_s, clip.end_s]), [
    [0, 5], [5, 12], [12, 20],
  ]);
});

test("degenerate or duplicate ids are skipped and made unique", () => {
  const track = buildAutoShowTrack({
    sections: [
      { label: "intro", start: 0, end: 0.01 },
      { label: "verse", start: 1, end: 4 },
      { label: "chorus", start: 1, end: 4 },
    ],
    duration: 10,
    trackId: "auto_1",
    idPrefix: "auto_1",
    existingClipIds: ["auto_1_1"],
  });
  assert.equal(track.clips.length, 1);
  assert.equal(track.clips[0].id, "auto_1_1_2");
});

test("filter uids are stable and local to the clip", () => {
  const track = buildAutoShowTrack({
    sections: [{ label: "drop", start: 0, end: 4 }],
    duration: 4,
    trackId: "auto_1",
    idPrefix: "auto_1",
  });
  assert.equal(track.clips[0].filters[0].uid, "auto_1_1_f0_beat_gate");
});
