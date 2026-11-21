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
    Group,
    PointGeometry,
    Rig,
    RigLibrary,
    compile_rig,
    load_rig,
    save_rig,
)

ROOT = Path(__file__).resolve().parents[1]
RIG_PATH = ROOT / "config" / "rigs" / "hueme_loft.json"
RIGS_DIR = ROOT / "config" / "rigs"


def test_default_rig_expands_to_expected_voxels():
    rig = load_rig(RIG_PATH)
    compiled = rig.compile()

    assert compiled.fixture_count == 11
    assert compiled.voxel_count == 7 + 3 + 180
    assert tuple(compiled.fixture_ids[:7]) == tuple(
        f"galerie_{i:02d}" for i in range(1, 8)
    )
    assert compiled.fixture_slices["stahltraeger"].start == 10
    assert compiled.fixture_slices["stahltraeger"].stop == 190
    assert len(compiled.groups["all"]) == 190
    assert len(compiled.groups["galerie_oben"]) == 7
    assert len(compiled.groups["decke_unten"]) == 3
    assert len(compiled.groups["stahltraeger"]) == 180

    strip = compiled.fixture_positions("stahltraeger")
    assert np.allclose(strip[0], [0.35, 4.40, 4.05])
    assert np.allclose(strip[-1], [6.15, 4.40, 4.05])
    assert np.allclose(
        compiled.normalized_positions[0], [0.55 / 6.5, 1.15 / 8.0, 4.45 / 5.1]
    )


def test_group_rank_and_spatial_groups_are_deterministic():
    compiled = load_rig(RIG_PATH).compile()
    rank = compiled.group_rank("galerie_oben")
    assert list(rank[:7]) == list(range(7))
    assert np.all(rank[7:] == -1)

    centre = compiled.group_indices("loft_center")
    for index in centre:
        position = compiled.positions[int(index)]
        assert np.linalg.norm(position - np.array([3.25, 4.0, 2.4])) <= 2.4 + 1e-6

    rear = compiled.group_indices("rear_half")
    assert rear.size == 3  # only the three lower depth spots live in the rear half
    assert set(compiled.voxel_fixture_index[rear].tolist()) == {7, 8, 9}


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
    updated = rig.model_copy(deep=True)
    updated.fixtures.append(extra)
    compiled = updated.compile()

    assert compiled.voxel_count == 191
    assert compiled.groups["all"].size == 191
    assert compiled.fixture_voxels("extra_spot").start == 190


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
