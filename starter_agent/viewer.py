"""Flat 2D route view: one PNG per floor the latest route passes through."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import itertools

import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as PolygonPatch

from starter_agent.building_map import BuildingMap

ROOM_COLOUR = "#f3efe6"
CLOSED_COLOUR = "#f4cccc"
ROUTE_COLOUR = "#d32f2f"
OUTLINE_COLOUR = "#555555"
FIGURE_INCHES = 9
ROUTE_LINE_WIDTH = 2.5
MARKER_SIZE = 9


def route_points_on_floor(building_map: BuildingMap, floor_id: str) -> list[tuple[float, float]]:
    """Room anchors along the route joined through the doors between them."""
    route = building_map.last_route
    points: list[tuple[float, float]] = []
    indoor_path = [space_id for space_id in route.space_path if space_id in building_map.spaces]
    for previous, current in itertools.pairwise(indoor_path):
        if building_map.spaces[previous]["floor_id"] != floor_id:
            continue
        points.append(building_map.anchor(previous))
        door = next(
            (
                opening
                for opening in building_map.openings.values()
                if set(opening["space_ids"]) == {previous, current}
            ),
            None,
        )
        if door is not None:
            points.append(tuple(door["position"]))
    last = indoor_path[-1]
    if building_map.spaces[last]["floor_id"] == floor_id:
        points.append(building_map.anchor(last))
    return points


def draw_route(building_map: BuildingMap, output_directory: Path) -> list[Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    route = building_map.last_route
    floor_ids = list(
        dict.fromkeys(
            building_map.spaces[space_id]["floor_id"]
            for space_id in route.space_path
            if space_id in building_map.spaces
        )
    )
    written = []
    for floor_id in floor_ids:
        figure, axes = plt.subplots(figsize=(FIGURE_INCHES, FIGURE_INCHES))
        try:
            for space in building_map.spaces.values():
                if space["floor_id"] != floor_id:
                    continue
                colour = CLOSED_COLOUR if space["id"] in building_map.closed else ROOM_COLOUR
                axes.add_patch(
                    PolygonPatch(space["polygon"], facecolor=colour, edgecolor=OUTLINE_COLOUR, linewidth=0.5)
                )
            points = route_points_on_floor(building_map, floor_id)
            if points:
                xs, ys = zip(*points, strict=True)
                axes.plot(xs, ys, color=ROUTE_COLOUR, linewidth=ROUTE_LINE_WIDTH)
                axes.plot(xs[0], ys[0], "o", color=ROUTE_COLOUR, markersize=MARKER_SIZE)
                axes.plot(xs[-1], ys[-1], "*", color=ROUTE_COLOUR, markersize=MARKER_SIZE * 1.5)
            axes.autoscale_view()
            axes.set_aspect("equal")
            axes.axis("off")
            axes.set_title(f"{building_map.data['building']['name']} — {building_map.floor_name(floor_id)}")
            path = output_directory / f"route_{building_map.floors[floor_id]['level']}.png"
            figure.savefig(path, bbox_inches="tight")
            written.append(path)
        finally:
            plt.close(figure)
    return written
