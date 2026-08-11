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
