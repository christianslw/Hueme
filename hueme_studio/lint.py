"""Pre-flight lint for a ShowDoc against the live rig and blueprint.

The compositor degrades gracefully when a show references a group the current
rig does not have.  That is right for playback but wrong for authoring, so the
studio surfaces the same problems before the operator hits play.  MCP uses this
as its read-only ``show_lint`` tool; the UI shows the counts in the timeline.
"""

from __future__ import annotations

from typing import Any

from hueme_studio.filters import FILTERS
from hueme_studio.presets import LEGACY_PRESETS, preset_catalog
from hueme_studio.rig import Rig, fixture_incomplete_reasons, hue_proxy_health
from hueme_studio.showdoc import ShowDoc

ERROR = "error"
WARNING = "warning"
INFO = "info"


def _issue(level: str, code: str, message: str, path: str = "") -> dict[str, str]:
    return {"level": level, "code": code, "message": message, "path": path}


def lint_show(
    doc: ShowDoc,
    rig: Rig,
    *,
    blueprint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return ``{ok, errors, warnings, issues}``; ``ok`` means no errors."""
    issues: list[dict[str, str]] = []
    presets = {entry["id"] for entry in preset_catalog()}
    presets.update(LEGACY_PRESETS)
    groups = {group.id for group in rig.groups}
    groups.update(fixture.id for fixture in rig.fixtures)
    groups.update(group.id for group in rig.smart_groups)
    compiled = rig.compile()
    group_sizes = {
        group_id: int(compiled.group_indices(group_id).size)
        for group_id in groups
        if group_id in compiled.groups
    }
    proxy_health = hue_proxy_health(rig)
    fixture_health = fixture_incomplete_reasons(rig)
    for fixture_id, reasons in fixture_health.items():
        for reason in reasons:
            issues.append(
                _issue(
                    ERROR,
                    "hue_fixture_incomplete",
                    f"fixture '{fixture_id}': {reason}",
                    f"rig.fixtures.{fixture_id}.output",
                )
            )
    if not proxy_health["ok"]:
        issues.append(
            _issue(
                ERROR,
                "hue_proxy_plan",
                (
                    f"Hue proxy plan incomplete: {proxy_health['assigned_proxies']} "
                    f"of {proxy_health['required_proxies']} required proxies"
                ),
                "rig.proxy_health",
            )
        )

    if not doc.tracks:
        issues.append(_issue(WARNING, "no_tracks", "show has no tracks", "tracks"))

    for ti, track in enumerate(doc.tracks):
        base = f"tracks[{ti}]"
        if track.target_group not in groups:
            issues.append(
                _issue(
                    ERROR,
                    "missing_group",
                    f"track '{track.id}' targets missing group '{track.target_group}'",
                    f"{base}.target_group",
                )
            )
        elif group_sizes.get(track.target_group, 0) == 0:
            issues.append(
                _issue(
                    WARNING,
                    "empty_group",
                    f"track '{track.id}' targets group '{track.target_group}' with no voxels",
                    f"{base}.target_group",
                )
            )
        for ci, clip in enumerate(track.clips):
            cbase = f"{base}.clips[{ci}]"
            if clip.preset not in presets:
                issues.append(
                    _issue(
                        ERROR,
                        "missing_preset",
                        f"clip '{clip.id}' uses unknown preset '{clip.preset}'",
                        f"{cbase}.preset",
                    )
                )
            for fi, spec in enumerate(clip.filters):
                if spec.enabled and spec.type not in FILTERS:
                    issues.append(
                        _issue(
                            ERROR,
                            "missing_filter",
                            f"clip '{clip.id}' uses unknown filter '{spec.type}'",
                            f"{cbase}.filters[{fi}]",
                        )
                    )

    if doc.match.duration_s > 0 and abs(doc.match.duration_s - doc.duration_s) > 1.0:
        issues.append(
            _issue(
                WARNING,
                "duration_mismatch",
                "show duration differs from the matched track duration",
                "duration_s",
            )
        )

    if blueprint is None:
        issues.append(
            _issue(
                INFO,
                "no_blueprint",
                "no AudioBlueprint attached; envelopes use the ShowDoc beat grid",
                "blueprint",
            )
        )
    else:
        if abs(float(blueprint.get("duration_s", 0.0)) - doc.duration_s) > 1.0:
            issues.append(
                _issue(
                    WARNING,
                    "blueprint_duration",
                    "AudioBlueprint duration differs from the show duration",
                    "blueprint.duration_s",
                )
            )
        if abs(float(blueprint.get("bpm", 0.0)) - doc.timing.bpm) > 2.0:
            issues.append(
                _issue(
                    WARNING,
                    "blueprint_bpm",
                    "AudioBlueprint tempo differs from the show beat grid",
                    "timing.bpm",
                )
            )

    errors = sum(1 for issue in issues if issue["level"] == ERROR)
    warnings = sum(1 for issue in issues if issue["level"] == WARNING)
    return {
        "ok": errors == 0,
        "errors": errors,
        "warnings": warnings,
        "issues": issues,
        "proxy_health": proxy_health,
        "fixtures": fixture_health,
    }


__all__ = ["ERROR", "INFO", "WARNING", "lint_show"]
