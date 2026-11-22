import assert from "node:assert/strict";
import test from "node:test";

import { nearestSnap } from "../hueme_studio/static/snap.js";

const options = {
  grid: 2.0,
  candidates: [2.07, 5.0],
  threshold: 0.1,
  min: 0,
  max: 10,
};

test("a nearby marker beats the grid line", () => {
  assert.equal(nearestSnap(2.05, options), 2.07);
});

test("a marker outside the threshold leaves the grid snap", () => {
  assert.equal(nearestSnap(2.5, options), 2.0);
});

test("the grid wins when it is closer than the magnet", () => {
  assert.equal(nearestSnap(2.01, options), 2.0);
});

test("candidates are clamped to the show range", () => {
  assert.equal(nearestSnap(0.02, { ...options, grid: 0, candidates: [-1] }), 0);
  assert.equal(nearestSnap(9.95, { ...options, grid: 10, candidates: [12] }), 10);
});
