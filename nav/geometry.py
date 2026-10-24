"""Polygon facts the route graph does not store: area, centroids, shared walls, stacking."""

import math

from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import nearest_points

from starter_agent.building_map import BuildingMap

WALL_PROXIMITY_M = 0.35
MIN_SHARED_WALL_M = 1.0
# Corridors, stairs and lifts are not rooms. Outdoor spaces are balconies and the like.
SHARED_WALL_EXCLUDED_TYPES = frozenset({"corridor", "stair", "lift", "outdoor"})


def as_polygon(coordinates: list) -> Polygon:
    polygon = Polygon(coordinates)
    if polygon.is_valid and polygon.area > 0:
        return polygon
    repaired = polygon.buffer(0)
    if isinstance(repaired, MultiPolygon):
        repaired = max(repaired.geoms, key=lambda item: item.area)
    elif isinstance(repaired, GeometryCollection):
        parts = [item for item in repaired.geoms if isinstance(item, Polygon)]
        repaired = max(parts, key=lambda item: item.area) if parts else Polygon()
    return repaired if isinstance(repaired, Polygon) else Polygon()


def space_polygon(building: BuildingMap, space_id: str) -> Polygon:
    cache: dict[str, Polygon] = building.__dict__.setdefault("_polygons", {})
    polygon = cache.get(space_id)
    if polygon is None:
        polygon = as_polygon(building.spaces[space_id]["polygon"])
        cache[space_id] = polygon
    return polygon


def space_area(building: BuildingMap, space_id: str) -> float:
    return space_polygon(building, space_id).area


def centroid_xyz(building: BuildingMap, space_id: str) -> tuple[float, float, float]:
    space = building.spaces[space_id]
    point = space_polygon(building, space_id).centroid
    elevation = float(building.floors[space["floor_id"]]["elevation_m"])
    return (point.x, point.y, elevation)


def straight_line_distance(building: BuildingMap, first_id: str, second_id: str) -> float:
    return math.dist(centroid_xyz(building, first_id), centroid_xyz(building, second_id))


def _longest_linestring(geometry) -> LineString | None:
    if geometry is None or geometry.is_empty:
        return None
    if isinstance(geometry, LineString):
        return geometry if geometry.length > 0 else None
    if isinstance(geometry, MultiLineString):
        return max(geometry.geoms, key=lambda item: item.length)
    if isinstance(geometry, GeometryCollection):
        lines = [line for item in geometry.geoms if (line := _longest_linestring(item)) is not None]
        return max(lines, key=lambda item: item.length) if lines else None
    return None


def shared_wall_length(first: Polygon, second: Polygon, proximity_m: float = WALL_PROXIMITY_M) -> float:
    """Length of either outline that runs within proximity_m of the other outline."""
    if first.is_empty or second.is_empty or first.distance(second) > proximity_m:
        return 0.0
    along_first = first.boundary.intersection(second.boundary.buffer(proximity_m)).length
    along_second = second.boundary.intersection(first.boundary.buffer(proximity_m)).length
    return max(along_first, along_second)


def shares_wall(first: Polygon, second: Polygon) -> bool:
    return shared_wall_length(first, second) >= MIN_SHARED_WALL_M


def shared_wall_midpoint(
    first: Polygon, second: Polygon, proximity_m: float = WALL_PROXIMITY_M
) -> tuple[float, float] | None:
    """Middle of the longest stretch where the two outlines run close to each other."""
    if first.is_empty or second.is_empty or first.distance(second) > proximity_m:
        return None
    along_first = _longest_linestring(first.boundary.intersection(second.boundary.buffer(proximity_m)))
    along_second = _longest_linestring(second.boundary.intersection(first.boundary.buffer(proximity_m)))
    if along_first is None and along_second is None:
        return None
    if along_second is None or (along_first is not None and along_first.length >= along_second.length):
        line, other = along_first, second.boundary
    else:
        line, other = along_second, first.boundary
    if line is None or line.length == 0:
        return None
    middle = line.interpolate(0.5, normalized=True)
    snapped = nearest_points(middle, other)[1]
    return ((middle.x + snapped.x) / 2.0, (middle.y + snapped.y) / 2.0)


def fallback_midpoint(first: Polygon, second: Polygon) -> tuple[float, float]:
    point_a, point_b = nearest_points(first, second)
    return ((point_a.x + point_b.x) / 2.0, (point_a.y + point_b.y) / 2.0)


def door_position(first: Polygon, second: Polygon) -> tuple[float, float]:
    return shared_wall_midpoint(first, second) or fallback_midpoint(first, second)


def shared_wall_neighbors(building: BuildingMap, space_id: str) -> list[str]:
    """Other spaces on the same floor that share at least 1 m of wall. Corridors, stairs, lifts and outdoors are out."""
    source = building.spaces[space_id]
    source_polygon = space_polygon(building, space_id)
    neighbors = []
    for other in building.spaces.values():
        if other["id"] == space_id or other["floor_id"] != source["floor_id"]:
            continue
        if other["type"] in SHARED_WALL_EXCLUDED_TYPES:
            continue
        if shares_wall(source_polygon, space_polygon(building, other["id"])):
            neighbors.append(other["id"])
    return neighbors


def stacked_space(building: BuildingMap, space_id: str, direction: str) -> str | None:
    """Space on the next floor up or down that covers the largest share of this outline."""
    source = building.spaces[space_id]
    source_level = building.floors[source["floor_id"]]["level"]
    levels = sorted({floor["level"] for floor in building.floors.values()})
    if direction == "above":
        candidates = [level for level in levels if level > source_level]
        target_level = candidates[0] if candidates else None
    elif direction == "below":
        candidates = [level for level in levels if level < source_level]
        target_level = candidates[-1] if candidates else None
    else:
        raise ValueError(f"direction must be above or below, not {direction}")
    if target_level is None:
        return None
    target_floors = {floor_id for floor_id, floor in building.floors.items() if floor["level"] == target_level}
    source_polygon = space_polygon(building, space_id)
    source_area = source_polygon.area
    best_id = None
    best_overlap = -1.0
    for other in building.spaces.values():
        if other["floor_id"] not in target_floors:
            continue
        overlap = source_polygon.intersection(space_polygon(building, other["id"])).area
        if overlap > best_overlap or (overlap == best_overlap and (best_id is None or other["id"] < best_id)):
            best_overlap = overlap
            best_id = other["id"]
    if best_id is None or source_area <= 0 or best_overlap <= 0:
        return best_id
    return best_id
