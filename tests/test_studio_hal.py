from __future__ import annotations

import socket

import numpy as np

from hueme.drivers import DDP_TYPE_RGBW32, DdpOutputDriver, parse_ddp
from hueme_studio.compositor import FrameResult
from hueme_studio.hal import Hal, HalSettings, apply_color_space, extract_warm_channel
from hueme_studio.rig import (
    Capabilities,
    Fixture,
    FixtureOutput,
    Group,
    LineGeometry,
    PointGeometry,
    Rig,
    compile_rig,
)


class FakeHue:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_consolidated(self, records, fps=30.0, snap=False):
        self.calls.append({"records": list(records), "fps": fps, "snap": bool(snap)})


class FakeDdp:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_pixels(self, pixels, host=None, port=None):
        self.calls.append({"pixels": np.asarray(pixels), "host": host, "port": port})


def hue_rig() -> object:
    jump = Fixture(
        id="hue_a",
        name="Hue A",
        geometry=PointGeometry(pos=[1.0, 2.0, 4.0]),
        capabilities=Capabilities(color_space="cie_xy", max_fps=25.0, zcl_snap=True),
        output=FixtureOutput(
            protocol="hue_z2m",
            friendly_name="Hue A",
            address="0x1010",
            proxy="Hue A",
        ),
    )
    return compile_rig(
        Rig(
            id="hue_test",
            name="Hue Test",
            room={"min": [0, 0, 0], "max": [10, 10, 10]},
            fixtures=[
                jump,
                Fixture(
                    id="hue_b",
                    name="Hue B",
                    geometry=PointGeometry(pos=[5.0, 2.0, 4.0]),
                    capabilities=Capabilities(
                        color_space="cie_xy", max_fps=25.0, zcl_snap=True
                    ),
                    output=FixtureOutput(
                        protocol="hue_z2m",
                        friendly_name="Hue B",
                        address="0x2020",
                        proxy="Hue B",
                    ),
                ),
            ],
            groups=[Group(id="all", name="All", type="all")],
        )
    )


def strip_rig(max_fps: float = 60.0) -> object:
    return compile_rig(
        Rig(
            id="strip_test",
            name="Strip Test",
            room={"min": [0, 0, 0], "max": [10, 10, 10]},
            fixtures=[
                Fixture(
                    id="strip",
                    name="Strip",
                    geometry=LineGeometry(start=[0, 5, 5], end=[9, 5, 5], pixels=4),
                    capabilities=Capabilities(
                        color_space="rgb_amber_1800k",
                        max_fps=max_fps,
                        zcl_snap=False,
                    ),
                    output=FixtureOutput(
                        protocol="wled_ddp", host="127.0.0.1", port=9999
                    ),
                )
            ],
            groups=[Group(id="all", name="All", type="all")],
        )
    )


def frame_for(rig, snap: list[bool] | None = None) -> FrameResult:
    rgb = np.zeros((rig.voxel_count, 3), dtype=np.float32)
    rgb[:] = np.array([200.0, 150.0, 60.0], dtype=np.float32)
    snaps = np.zeros(rig.fixture_count, dtype=bool)
    if snap:
        snaps[:] = snap
    return FrameResult(time_s=1.0, rgb=rgb, snap_zcl=snaps)


def test_warm_channel_extraction_and_colour_spaces():
    assert extract_warm_channel([200, 150, 60]) == (0, 32, 23, 200)
    assert extract_warm_channel([0, 0, 0]) == (0, 0, 0, 0)
    # Pure 1800K amber belongs on the W die with no RGB residue.
    assert extract_warm_channel([255, 147, 41]) == (0, 0, 0, 255)
    # Neutral white must not leave a blue cast on the RGB dies.
    white = extract_warm_channel([255, 255, 255])
    assert white == (0, 0, 0, 255)
    dim = apply_color_space(np.array([100, 50, 10]), "dimmer_only")
    assert abs(float(dim[0]) - float(dim[1])) < 1e-6
    assert dim[0] == dim[1] == dim[2]
    assert apply_color_space(np.array([200, 150, 60]), "rgb_amber_1800k").tolist() == [
        0,
        32,
        23,
        200,
    ]


def test_hue_snap_and_smooth_frames_are_split():
    rig = hue_rig()
    fake = FakeHue()
    hal = Hal(rig, HalSettings(enabled=True), hue_driver=fake)
    report = hal.output_frame(frame_for(rig, [True, False]), now=1.0)
    assert report["sent"] is True
    assert report["hue_records"] == 2
    assert len(fake.calls) == 2
    assert {call["snap"] for call in fake.calls} == {True, False}
    assert all(len(call["records"]) == 1 for call in fake.calls)
    # Smoothing must follow the fixture limit (25 fps), not the 30 fps default.
    assert all(call["fps"] == 25.0 for call in fake.calls)
    assert fake.calls[0]["records"][0][0] in {"Hue A", "Hue B"}
    assert hal.snapshot_stats()["snap_splits"] == 1


def test_ddp_amber_strip_emits_four_channels():
    rig = strip_rig()
    fake = FakeDdp()
    hal = Hal(rig, HalSettings(enabled=True), ddp_driver=fake)
    report = hal.output_frame(frame_for(rig), now=1.0)
    assert report["ddp_frames"] == 1
    pixels = fake.calls[0]["pixels"]
    assert pixels.shape == (4, 4)
    assert pixels[0].tolist() == [0, 32, 23, 200]
    assert fake.calls[0]["host"] == "127.0.0.1"


def test_fixture_rate_limit_and_hardware_switch():
    rig = strip_rig(max_fps=5.0)
    fake = FakeDdp()
    hal = Hal(rig, HalSettings(enabled=True), ddp_driver=fake)
    hal.output_frame(frame_for(rig), now=1.0)
    hal.output_frame(frame_for(rig), now=1.1)
    assert len(fake.calls) == 1
    hal.output_frame(frame_for(rig), now=1.2)
    assert len(fake.calls) == 2

    disabled = Hal(rig, HalSettings(enabled=False), ddp_driver=FakeDdp())
    report = disabled.output_frame(frame_for(rig), now=1.0)
    assert report["sent"] is False


def test_fixture_deadlines_track_fixture_rate_not_preview_rate():
    rig = hue_rig()
    fake = FakeHue()
    hal = Hal(rig, HalSettings(enabled=True), hue_driver=fake)
    now = 0.0
    hal.output_frame(frame_for(rig), now=now)
    for _ in range(25):
        wait = hal.seconds_until_due(now=now)
        assert abs(wait - 0.04) < 1e-9, "25 fps fixture must ask for 40 ms"
        now += wait
        hal.output_frame(frame_for(rig), now=now)
    assert len(fake.calls) == 26
    assert abs(now - 1.0) < 1e-9


def test_ddp_driver_accepts_hal_float_array():
    """HAL sends float64 (N, 4); the driver must not use array truthiness or
    bitwise ops on numpy floats."""
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    port = rx.getsockname()[1]
    rx.settimeout(1.0)
    driver = DdpOutputDriver({"enabled": True, "host": "127.0.0.1", "port": port})
    driver.start()
    try:
        pixels = np.asarray([[140.2, 90.9, 0.0, 60.1], [10.0, 20.0, 30.0, 40.0]])
        driver.send_pixels(pixels, host="127.0.0.1", port=port)
        data, _ = rx.recvfrom(2048)
        parsed = parse_ddp(data)
        assert parsed is not None
        assert data[2] == DDP_TYPE_RGBW32
        assert list(parsed[1]) == [140, 90, 0, 60, 10, 20, 30, 40]
        assert driver.stats["errors"] == 0
    finally:
        driver.stop()
        rx.close()


def test_ddp_rgbw_packet_extension_roundtrip():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    port = rx.getsockname()[1]
    rx.settimeout(1.0)
    driver = DdpOutputDriver({"enabled": True, "host": "127.0.0.1", "port": port})
    driver.start()
    try:
        driver.send_pixels([(140, 90, 0, 60), (10, 20, 30, 40)])
        data, _ = rx.recvfrom(2048)
        assert data[2] == DDP_TYPE_RGBW32  # WLED only reads 4ch for 0x1B
        parsed = parse_ddp(data)
        assert parsed is not None
        offset, payload = parsed
        assert offset == 0
        assert list(payload) == [140, 90, 0, 60, 10, 20, 30, 40]
    finally:
        driver.stop()
        rx.close()
