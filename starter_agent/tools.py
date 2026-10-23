"""Tools the agent can call. Each one is a thin, typed wrapper around BuildingMap that returns JSON."""

import json
from dataclasses import asdict
from pathlib import Path

from anthropic import beta_tool
from anthropic.lib.tools import ToolError

from starter_agent.building_map import MAX_SEARCH_RESULTS, BuildingMap, MapError
from starter_agent.viewer import draw_route

JSON_INDENT = 1


def as_json(value: object) -> str:
    return json.dumps(value, indent=JSON_INDENT, ensure_ascii=False)


def make_tools(building_map: BuildingMap, output_directory: Path) -> list:
    """Build the tool list bound to one building. Errors become is_error tool results via ToolError."""

    @beta_tool
    def building_overview() -> str:
        """Floors, stairs and lifts, entrances and current closures of the building. Call this first."""
        return as_json(building_map.overview())

    @beta_tool
    def search_building(query: str, floor_level: int | None = None) -> str:
        """Find rooms and things (furniture, machines, equipment) by name, room number or type.

        Args:
            query: Words to look for, e.g. "3d printer", "2.03", "toilet", "cnc mill", "lecture hall".
            floor_level: Only search this floor (0 = ground floor, -1 = basement). Omit to search all floors.
        """
        results = building_map.search(query, floor_level)
        if not results:
            raise ToolError(f"Nothing matches '{query}'. Try a shorter or more general query.")
        return as_json({"matches": len(results), "results": results[:MAX_SEARCH_RESULTS]})

    @beta_tool
    def find_route(
        start: str, destination: str, step_free: bool = False, via: list[str] | None = None
    ) -> str:
        """Shortest walking route between two places, with step-by-step directions and length in metres.

        Args:
            start: Space id, asset id, or exact room number/name of where the person is.
            destination: Space id, asset id, or exact room number/name of where they want to go.
            step_free: True to avoid stairs and steps (wheelchair, trolley, pram).
            via: Optional list of places to visit in order between start and destination (multi-stop tour).
        """
        try:
            result = building_map.route(start, destination, step_free, via)
        except MapError as error:
            raise ToolError(str(error)) from error
        return as_json(asdict(result))

    @beta_tool
    def set_closed(element: str, closed: bool) -> str:
        """Close or reopen a lift, staircase, door or room, e.g. for maintenance. Affects all later routes.

        Args:
            element: Name or id of a lift/staircase ("Lift 1"), a door/opening id, or a room.
            closed: True to close it, False to reopen it.
        """
        try:
            element_id = building_map.set_closed(element, closed)
        except MapError as error:
            raise ToolError(str(error)) from error
        return as_json(
            {"element_id": element_id, "closed": closed, "all_closed": sorted(building_map.closed)}
        )

    @beta_tool
    def add_door(first_space: str, second_space: str, step_free: bool = True) -> str:
        """Add a door between two neighbouring rooms on the same floor (edits the building).

        Args:
            first_space: Space id or exact room number/name.
            second_space: Space id or exact room number/name.
            step_free: Whether the new door has no step.
        """
        try:
            opening = building_map.add_door(first_space, second_space, step_free)
        except MapError as error:
            raise ToolError(str(error)) from error
        building_map.save(output_directory / "building_edited.json")
        return as_json({"added": opening, "saved_to": str(output_directory / "building_edited.json")})

    @beta_tool
    def show_route() -> str:
        """Draw the most recent route on the floor plans and save the images; returns the file paths."""
        if building_map.last_route is None:
            raise ToolError("No route yet. Call find_route first.")
        return as_json({"images": [str(path) for path in draw_route(building_map, output_directory)]})

    return [building_overview, search_building, find_route, set_closed, add_door, show_route]
