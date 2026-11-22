from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from hueme_studio.rig import (
    Capabilities,
    Fixture,
    FixtureOutput,
    FixtureTransform,
    Folder,
    Group,
    PointGeometry,
    Rig,
    RigLibrary,
    SmartGroup,
    compile_rig,
    fixture_incomplete_reasons,
    hue_proxy_health,
    load_rig,
    save_rig,
)

ROOT = Path(__file__).resolve().parents[1]
RIG_PATH = ROOT / "config" / "rigs" / "hueme_loft.json"
RIGS_DIR = ROOT / "config" / "rigs"


def test_default_rig_expands_to_expected_voxels():
    rig = load_rig(RIG_PATH)
    compiled = rig.compile()

    expected = {
        fixture.id: (
            int(fixture.geometry.pixels)
            if fixture.geometry.type == "line"
            else 1
        )
        for fixture in rig.fixtures
    }
    assert compiled.fixture_count == len(expected)
    assert compiled.voxel_count == sum(expected.values())
    for fixture_id, count in expected.items():
        span = compiled.fixture_slices[fixture_id]
        assert span.stop - span.start == count
    assert len(compiled.groups["all"]) == compiled.voxel_count
    gallery = [fixture_id for fixture_id in expected if fixture_id.startswith("galerie_")]
    assert len(compiled.groups["galerie_oben"]) == sum(
        expected[fixture_id] for fixture_id in gallery
    )
    lower = [
        fixture_id for fixture_id in expected if fixture_id.startswith("decke_unten_")
    ]
    assert len(compiled.groups["decke_unten"]) == sum(
        expected[fixture_id] for fixture_id in lower
    )

    strip = compiled.fixture_positions("stahltraeger")
    assert strip.shape[0] == expected["stahltraeger"]
    assert np.allclose(strip[0][1:], strip[-1][1:])
    normalized = compiled.normalized_positions
    assert (normalized >= -1e-6).all() and (normalized <= 1.0 + 1e-6).all()


def test_group_rank_and_spatial_groups_are_deterministic():
    first = load_rig(RIG_PATH).compile()
    second = load_rig(RIG_PATH).compile()
    for group in ("all", "galerie_oben", "decke_unten", "stahltraeger",
                  "loft_center", "rear_half"):
        assert np.array_equal(
            first.group_indices(group), second.group_indices(group)
        )
        assert np.array_equal(first.group_rank(group), second.group_rank(group))
    rank = first.group_rank("galerie_oben")
    assert sorted(rank[first.group_indices("galerie_oben")].tolist()) == list(
        range(len(first.group_indices("galerie_oben")))
    )
    rear = first.group_indices("rear_half")
    assert set(rear.tolist()).issubset(set(first.group_indices("all").tolist()))


def test_unknown_group_and_fixture_raise_readable_errors():
    compiled = load_rig(RIG_PATH).compile()
    with pytest.raises(KeyError, match="unknown group"):
        compiled.group_indices("no_such_group")
    with pytest.raises(KeyError, match="unknown fixture"):
        compiled.fixture_voxels("no_such_fixture")


def test_new_fixture_automatically_enters_all_group():
    rig = load_rig(RIG_PATH)
    extra = Fixture(
        id="extra_spot",
        name="Extra Spot",
        geometry=PointGeometry(pos=[3.0, 4.0, 4.0]),
        capabilities=Capabilities(color_space="rgb", max_fps=30.0, zcl_snap=False),
        output=FixtureOutput(protocol="none"),
    )
    base = rig.compile().voxel_count
    updated = rig.model_copy(deep=True)
    updated.fixtures.append(extra)
    compiled = updated.compile()

    assert compiled.voxel_count == base + 1
    assert compiled.groups["all"].size == base + 1
    assert compiled.fixture_voxels("extra_spot").start == base


def test_fixture_transform_moves_and_rotates_geometry():
    fixture = Fixture(
        id="transformed",
        name="Transformed Spot",
        geometry=PointGeometry(pos=[1.0, 1.0, 1.0], aim=[0.0, 0.0, -1.0]),
        transform=FixtureTransform(
            translate=[1.0, 2.0, 0.5],
            rotate_deg=[0.0, 0.0, 90.0],
            scale=[2.0, 2.0, 2.0],
        ),
        output=FixtureOutput(protocol="none"),
    )
    compiled = compile_rig(Rig(fixtures=[fixture]))
    assert np.allclose(compiled.positions[0], [-1.0, 4.0, 2.5])
    # Aim [0,0,-1] is unchanged by a Z rotation, so the beam still points down.
    assert np.allclose(compiled.fixture_aims["transformed"], [0.0, 0.0, -1.0])

    pivoted = Fixture(
        id="pivoted",
        name="Pivoted Spot",
        geometry=PointGeometry(pos=[2.0, 2.0, 2.0]),
        transform=FixtureTransform(pivot=[2.0, 2.0, 2.0], rotate_deg=[0.0, 0.0, 90.0]),
        output=FixtureOutput(protocol="none"),
    )
    pivot_compiled = compile_rig(Rig(fixtures=[pivoted]))
    assert np.allclose(pivot_compiled.positions[0], [2.0, 2.0, 2.0])


def test_rig_presets_are_user_data_not_code(tmp_path: Path):
    lib = RigLibrary(tmp_path)
    assert lib.snapshot()["presets"] == []
    custom = Fixture(
        id="custom_spot",
        name="Custom Spot",
        geometry=PointGeometry(pos=[2.0, 3.0, 4.0]),
        capabilities=Capabilities(color_space="rgb_amber_1800k", max_fps=60.0),
        output=FixtureOutput(protocol="wled_ddp", host="127.0.0.1", port=4048),
    )
    rig = lib.create(
        "Small Studio",
        fixtures=[custom],
        groups=[
            Group(id="all", name="All", type="all"),
            Group(id="spots", name="Spots", type="list", fixtures=["custom_spot"]),
        ],
        set_active=True,
    )
    assert lib.active_id == rig.id
    assert (tmp_path / f"{rig.id}.json").exists()
    loaded = lib.load_active()
    assert loaded.id == rig.id
    assert loaded.fixtures[0].id == "custom_spot"
    assert loaded.compile().voxel_count == 1

    clone = lib.clone(rig.id, "small_studio_b", "Small Studio B")
    assert clone.id == "small_studio_b"
    assert {e["id"] for e in lib.list_presets()} == {rig.id, "small_studio_b"}
    lib.set_active("small_studio_b")
    assert lib.active_id == "small_studio_b"
    lib.delete("small_studio_b")
    assert lib.active_id == rig.id


def test_rig_fixture_and_group_editing_is_transactional_data():
    fixture_a = Fixture(
        id="a",
        name="A",
        geometry=PointGeometry(pos=[0.0, 2.0, 3.0]),
        output=FixtureOutput(protocol="none"),
    )
    fixture_b = Fixture(
        id="b",
        name="B",
        geometry=PointGeometry(pos=[1.0, 2.0, 3.0]),
        output=FixtureOutput(protocol="none"),
    )
    rig = Rig(
        id="editable",
        name="Editable",
        fixtures=[fixture_a],
        groups=[
            Group(id="all", name="All", type="all"),
            Group(id="left", name="Left", type="list", fixtures=["a"]),
        ],
    )
    rig = rig.with_fixture(fixture_b).assign_to_group(["b"], "left")
    left = next(g for g in rig.groups if g.id == "left")
    assert left.fixtures == ["a", "b"]
    compiled = rig.compile()
    assert compiled.groups["left"].size == 2
    assert compiled.groups["all"].size == 2

    removed = rig.without_fixture("b")
    assert "b" not in removed.fixture_ids
    assert next(g for g in removed.groups if g.id == "left").fixtures == ["a"]
    assert removed.compile().groups["all"].size == 1

    spatial = Group(
        id="box",
        name="Box",
        type="spatial_box",
        min=[0.0, 0.0, 0.0],
        max=[2.0, 2.0, 2.0],
    )
    with pytest.raises(ValueError):
        removed.with_group(spatial).assign_to_group(["a"], "box")
    with pytest.raises(ValueError):
        removed.without_group("all")


def test_empty_rig_preset_compiles_to_an_empty_sandbox():
    lib = RigLibrary(RIGS_DIR)
    rig = lib.load("empty_loft")
    compiled = rig.compile()
    assert compiled.voxel_count == 0
    assert compiled.groups["all"].size == 0


def test_rig_rejects_duplicate_ids_and_bad_group_references():
    with pytest.raises(ValidationError):
        Rig(
            fixtures=[
                Fixture(id="a", name="A", geometry=PointGeometry(pos=[1, 1, 1])),
                Fixture(id="a", name="B", geometry=PointGeometry(pos=[2, 2, 2])),
            ]
        )
    with pytest.raises(ValidationError):
        Rig(
            fixtures=[Fixture(id="a", name="A", geometry=PointGeometry(pos=[1, 1, 1]))],
            groups=[Group(id="g", name="G", type="list", fixtures=["missing"])],
        )


def test_rig_save_roundtrip(tmp_path: Path):
    rig = load_rig(RIG_PATH)
    out = tmp_path / "rig.json"
    save_rig(rig, out)
    loaded = load_rig(out)
    assert loaded == rig
    assert "stahltraeger" in out.read_text(encoding="utf-8")


def test_floor_fixtures_folders_smart_groups_and_hue_health():
    rig = load_rig(RIG_PATH)
    by_id = {fixture.id: fixture for fixture in rig.fixtures}
    assert by_id["boden_01"].output.address == "0x19C7"
    assert by_id["boden_02"].output.address == "0x0C7F"
    assert by_id["boden_01"].folder_id == "boden"
    assert {folder.id for folder in rig.folders} >= {"galerie", "decke", "struktur", "boden"}
    assert {group.id for group in rig.smart_groups} >= {"smart_hue_all", "smart_boden"}
    compiled = rig.compile()
    assert compiled.group_indices("smart_boden").size == 2
    assert compiled.group_indices("boden_01").size == 1
    health = hue_proxy_health(rig)
    assert health["hue_count"] == 12
    assert health["required_proxies"] == 2
    assert health["assigned_proxies"] == 3
    assert health["ok"] is True
    assert all(not reasons for reasons in fixture_incomplete_reasons(rig).values())


def test_hue_proxy_and_address_violations_are_reported_per_fixture():
    fixtures = []
    for index in range(12):
        fixtures.append(
            Fixture(
                id=f"hue_{index}",
                name=f"Hue {index}",
                geometry=PointGeometry(pos=[float(index), 1.0, 2.0]),
                output=FixtureOutput(
                    protocol="hue_z2m",
                    address="0xZZZZ" if index == 0 else f"0x{index + 1:04X}",
                    proxy=None if index == 1 else "proxy_a",
                ),
            )
        )
    rig = Rig(
        fixtures=fixtures,
        folders=[Folder(id="f", name="Folder")],
        groups=[Group(id="all", name="All", type="all")],
        smart_groups=[SmartGroup(id="hues", name="Hues", fixtures=["hue_0"])],
    )
    health = hue_proxy_health(rig)
    reasons = fixture_incomplete_reasons(rig)
    assert health["required_proxies"] == 2
    assert health["assigned_proxies"] == 1
    assert health["ok"] is False
    assert any("address" in reason for reason in reasons["hue_0"])
    assert any("proxy" in reason for reason in reasons["hue_1"])
    assert any("11" in reason or "exceeds" in reason for reason in reasons["hue_2"])
