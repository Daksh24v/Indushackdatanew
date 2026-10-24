"""Check that a building.json is well formed and navigable. Used on generated buildings (Level 4) before loading.

  uv run python -m core.validate_building path/to/building.json [--strict]

Exit code 0 when there are no errors. Warnings (unreachable spaces, overlapping rooms) do not fail unless --strict."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import networkx as nx
from shapely.geometry import Point, Polygon

from core.building import OUTSIDE, Building

REQUIRED_TOP = ("building", "floors", "spaces", "openings", "vertical_connectors", "assets")
REQUIRED_SPACE = ("id", "floor_id", "type", "room_type", "name", "polygon")
REQUIRED_OPENING = ("id", "floor_id", "space_ids", "kind", "position", "width_m", "step_free")
REQUIRED_CONNECTOR = ("id", "kind", "floor_ids", "space_ids", "step_free")
SPACE_TYPES = {"room", "corridor", "stair", "lift", "hall", "outdoor"}
OPENING_KINDS = {"door", "opening", "entrance"}
OVERLAP_WARN_M2 = 0.5


def validate(data: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for key in REQUIRED_TOP:
        if key not in data:
            errors.append(f"missing top-level key '{key}'")
    if errors:
        return errors, warnings
    floor_ids = {floor["id"] for floor in data["floors"]}
    levels = Counter(floor.get("level") for floor in data["floors"])
    for level, count in levels.items():
        if count > 1:
            errors.append(f"{count} floors share level {level}")
    for floor in data["floors"]:
        for key in ("id", "level", "name", "elevation_m"):
            if key not in floor:
                errors.append(f"floor {floor.get('id')} missing '{key}'")
    space_ids = set()
    polygons: dict[str, Polygon] = {}
    for space in data["spaces"]:
        for key in REQUIRED_SPACE:
            if key not in space:
                errors.append(f"space {space.get('id')} missing '{key}'")
        if space.get("id") in space_ids:
            errors.append(f"duplicate space id {space['id']}")
        space_ids.add(space.get("id"))
        if space.get("floor_id") not in floor_ids:
            errors.append(f"space {space.get('id')} has unknown floor_id {space.get('floor_id')}")
        if space.get("type") not in SPACE_TYPES:
            warnings.append(f"space {space.get('id')} has unusual type {space.get('type')}")
        polygon = space.get("polygon") or []
        if len(polygon) < 3:
            errors.append(f"space {space.get('id')} polygon has fewer than 3 points")
            continue
        shape = Polygon(polygon)
        if shape.area <= 0:
            errors.append(f"space {space.get('id')} polygon has zero area")
        if not shape.is_valid:
            warnings.append(f"space {space.get('id')} polygon is self-intersecting")
        polygons[space["id"]] = shape
    names = Counter((space.get("floor_id"), space.get("name")) for space in data["spaces"] if space.get("type") in ("room", "hall"))
    for (floor_id, name), count in names.items():
        if count > 1:
            warnings.append(f"{count} rooms on {floor_id} are named '{name}' (answers must include the ref)")
    for opening in data["openings"]:
        for key in REQUIRED_OPENING:
            if key not in opening:
                errors.append(f"opening {opening.get('id')} missing '{key}'")
        sides = opening.get("space_ids") or []
        if len(sides) != 2:
            errors.append(f"opening {opening.get('id')} must join exactly two spaces (or one space and 'outside')")
        for side in sides:
            if side != OUTSIDE and side not in space_ids:
                errors.append(f"opening {opening.get('id')} refers to unknown space {side}")
        if opening.get("kind") not in OPENING_KINDS:
            warnings.append(f"opening {opening.get('id')} has unusual kind {opening.get('kind')}")
        if OUTSIDE in sides and opening.get("kind") != "entrance":
            warnings.append(f"opening {opening.get('id')} joins outside but is not kind 'entrance'")
        if (opening.get("width_m") or 0) <= 0:
            errors.append(f"opening {opening.get('id')} has no positive width_m")
        if sides and len(sides) == 2 and all(side in polygons for side in sides):
            position = opening.get("position")
            if position:
                far = [side for side in sides if polygons[side].distance(Point(position)) > 1.0]
                if far:
                    warnings.append(f"opening {opening.get('id')} is more than 1 m away from {far}")
    for connector in data["vertical_connectors"]:
        for key in REQUIRED_CONNECTOR:
            if key not in connector:
                errors.append(f"connector {connector.get('id')} missing '{key}'")
        if len(connector.get("floor_ids") or []) != len(connector.get("space_ids") or []):
            errors.append(f"connector {connector.get('id')} needs one stop space per floor")
        for floor_id in connector.get("floor_ids") or []:
            if floor_id not in floor_ids:
                errors.append(f"connector {connector.get('id')} refers to unknown floor {floor_id}")
        for space_id in connector.get("space_ids") or []:
            if space_id not in space_ids:
                errors.append(f"connector {connector.get('id')} refers to unknown space {space_id}")
        if connector.get("kind") == "stairs" and connector.get("step_free"):
            warnings.append(f"connector {connector.get('id')} is stairs but marked step_free")
    for asset in data["assets"]:
        if asset.get("space_id") not in space_ids:
            errors.append(f"asset {asset.get('id')} refers to unknown space {asset.get('space_id')}")
    if not any(OUTSIDE in (opening.get("space_ids") or []) for opening in data["openings"]):
        errors.append("no entrance: no opening joins 'outside'")
    # overlaps on one floor
    by_floor: dict[str, list[str]] = {}
    for space in data["spaces"]:
        if space.get("id") in polygons:
            by_floor.setdefault(space["floor_id"], []).append(space["id"])
    for floor_id, ids in by_floor.items():
        for i, first in enumerate(ids):
            for second in ids[i + 1:]:
                if polygons[first].intersects(polygons[second]):
                    overlap = polygons[first].intersection(polygons[second]).area
                    if overlap > OVERLAP_WARN_M2:
                        warnings.append(f"{first} and {second} overlap by {overlap:.1f} m² on {floor_id}")
    return errors, warnings


def reachability(building: Building) -> list[str]:
    graph = building.graph(step_free=False)
    if OUTSIDE not in graph:
        return ["no space can reach outside"]
    reached = nx.node_connected_component(graph, OUTSIDE)
    unreachable = [sid for sid in building.spaces if sid not in reached and building.spaces[sid]["type"] != "outdoor"]
    return [f"{len(unreachable)} spaces cannot be reached from any entrance: {unreachable[:8]}{' ...' if len(unreachable) > 8 else ''}"] if unreachable else []


def main() -> None:
    parser = argparse.ArgumentParser(prog="core.validate_building")
    parser.add_argument("path", type=Path, help="a building.json or a building directory")
    parser.add_argument("--strict", action="store_true", help="warnings also fail")
    arguments = parser.parse_args()
    path = arguments.path / "building.json" if arguments.path.is_dir() else arguments.path
    data = json.loads(path.read_text(encoding="utf-8"))
    errors, warnings = validate(data)
    if not errors:
        try:
            warnings += reachability(Building(data))
        except Exception as error:  # noqa: BLE001 - report anything that stops the building from loading
            errors.append(f"Building failed to load: {error}")
    for message in errors:
        print(f"ERROR   {message}")
    for message in warnings:
        print(f"warning {message}")
    print(f"{path}: {len(errors)} errors, {len(warnings)} warnings; "
          f"{len(data.get('floors', []))} floors, {len(data.get('spaces', []))} spaces, {len(data.get('openings', []))} openings")
    raise SystemExit(1 if errors or (arguments.strict and warnings) else 0)


if __name__ == "__main__":
    main()
