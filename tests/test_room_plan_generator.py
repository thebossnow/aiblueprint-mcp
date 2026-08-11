"""Tests for RoomFloorPlanGenerator."""

from __future__ import annotations

import pytest

from aiblueprint_mcp.room_plan_generator import RoomFloorPlanConfig, RoomFloorPlanGenerator


@pytest.mark.asyncio
async def test_basic_kitchen_plan(backend):
    cfg = RoomFloorPlanConfig(
        room_width=13.4,
        room_depth=10.75,
        room_type="kitchen",
        island_length=4.5,
        island_depth=2.5,
        island_orientation="NS",
        title="EXISTING KITCHEN FLOOR PLAN",
        address="123 Example St, Anytown, CA 92000",
        notes=["Example note — no real client data"],
    )
    gen = RoomFloorPlanGenerator(backend)
    result = await gen.generate(cfg)
    assert result.ok, result.error
    assert result.payload["room_type"] == "kitchen"
    assert result.payload["has_island"] is True
    assert result.payload["room_width_ft"] == 13.4


@pytest.mark.asyncio
async def test_no_island(backend):
    cfg = RoomFloorPlanConfig(
        room_width=10.0,
        room_depth=8.0,
        room_type="bath",
        include_range=False,
        include_dishwasher=False,
        include_fridge=False,
        sliding_door_start=None,
    )
    gen = RoomFloorPlanGenerator(backend)
    result = await gen.generate(cfg)
    assert result.ok, result.error
    assert result.payload["has_island"] is False
    assert result.payload["room_type"] == "bath"


@pytest.mark.asyncio
async def test_invalid_dimensions(backend):
    cfg = RoomFloorPlanConfig(room_width=-5, room_depth=10)
    gen = RoomFloorPlanGenerator(backend)
    result = await gen.generate(cfg)
    assert not result.ok
    assert "positive" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_island_orientation_ew(backend):
    cfg = RoomFloorPlanConfig(
        room_width=14.0,
        room_depth=11.0,
        island_length=5.0,
        island_depth=2.5,
        island_orientation="EW",
    )
    gen = RoomFloorPlanGenerator(backend)
    result = await gen.generate(cfg)
    assert result.ok, result.error
    assert result.payload["has_island"] is True


@pytest.mark.asyncio
async def test_rejects_appliance_layout_wider_than_north_run(backend):
    # Fixed-size appliances (DW+sink+range) need ~9 ft of north run; 4 ft isn't enough.
    cfg = RoomFloorPlanConfig(room_width=8.0, room_depth=10.0, north_run_length=4.0)
    gen = RoomFloorPlanGenerator(backend)
    result = await gen.generate(cfg)
    assert not result.ok
    assert "north run" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_rejects_cabinet_depth_exceeding_room_depth(backend):
    cfg = RoomFloorPlanConfig(room_width=10.0, room_depth=1.0)
    gen = RoomFloorPlanGenerator(backend)
    result = await gen.generate(cfg)
    assert not result.ok
    assert "cabinet_depth" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_rejects_fridge_overlapping_east_cabinet_run(backend):
    # Fridge occupies y=[2.2, 5.2]; a shallow room pulls the east run's
    # south edge (room_depth - east_run_length) into that range.
    cfg = RoomFloorPlanConfig(
        room_width=10.0, room_depth=8.0, east_run_length=6.0,
        include_dishwasher=False, include_sink=False, include_range=False,
    )
    gen = RoomFloorPlanGenerator(backend)
    result = await gen.generate(cfg)
    assert not result.ok
    assert "fridge" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_rejects_island_outside_room_bounds(backend):
    cfg = RoomFloorPlanConfig(
        room_width=10.0, room_depth=10.0,
        island_length=9.0, island_depth=9.0, island_orientation="EW",
        include_dishwasher=False, include_sink=False, include_range=False,
    )
    gen = RoomFloorPlanGenerator(backend)
    result = await gen.generate(cfg)
    assert not result.ok
    assert "island" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_rejects_island_with_only_one_dimension_set(backend):
    cfg = RoomFloorPlanConfig(room_width=10.0, room_depth=10.0, island_length=4.5)
    gen = RoomFloorPlanGenerator(backend)
    result = await gen.generate(cfg)
    assert not result.ok
    assert "island_length and island_depth" in (result.error or "")
