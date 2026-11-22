from __future__ import annotations

import numpy as np

from hueme_studio.fxpreview import (
    MAX_HEIGHT,
    MAX_WIDTH,
    _front_grid,
    _grid_mirror_sources,
    render_preset_rgb,
)


def test_grid_mirror_sources_match_brute_force():
    width, height = 7, 5
    _, normalized, _, _ = _front_grid(width, height, (1.0, 1.0, 1.0))
    sources = _grid_mirror_sources(width, height)
    for axis in (0, 1, 2):
        target = normalized.copy()
        target[:, axis] = 1.0 - target[:, axis]
        d2 = ((target[:, None, :] - normalized[None, :, :]) ** 2).sum(axis=2)
        chosen = d2[np.arange(len(target)), sources[axis]]
        assert np.allclose(chosen, d2.min(axis=1))


def test_preview_supports_full_editor_resolution():
    image = render_preset_rgb("gradient_3d", width=MAX_WIDTH, height=MAX_HEIGHT)
    assert image.shape == (MAX_HEIGHT, MAX_WIDTH, 3)

    clamped = render_preset_rgb("gradient_3d", width=4000, height=4000)
    assert clamped.shape == (MAX_HEIGHT, MAX_WIDTH, 3)
