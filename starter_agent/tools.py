"""Tools the agent can call. Each one is a thin, typed wrapper around BuildingMap that returns JSON."""

import json
from dataclasses import asdict
from pathlib import Path

from anthropic import beta_tool
from anthropic.lib.tools import ToolError

from nav.parse import ParseError, asset_type_code, floor_id_for_level, room_type_code
from nav.skills import (
    count_rooms_on_floor,
    floor_area_total,
    largest_room,
    nearest_asset_space,
    room_stacked,
    shared_walls_count,
    straight_line_3d,
    what_if_door as what_if_door_skill,
)
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

    def floor_of(level: int) -> str:
        try:
            return floor_id_for_level(building_map, level)
        except ParseError as error:
            raise ToolError(str(error)) from error

    @beta_tool
    def room_area(room_type: str, floor_level: int) -> str:
        """Largest room of one type on a floor, and the total area of every room of that type.

        Args:
            room_type: Words such as "office", "meeting room", or "generic room".
            floor_level: 0 is the ground floor, -1 the first basement.
        """
        try:
            code = room_type_code(building_map, room_type)
            floor_id = floor_of(floor_level)
            largest = largest_room(building_map, {"floor_id": floor_id, "room_type": code})
            total = floor_area_total(building_map, {"floor_id": floor_id, "room_type": code})
        except (MapError, ParseError) as error:
            raise ToolError(str(error)) from error
        winner = building_map.spaces[largest.space_id]
        return as_json(
            {
                "room_type": code,
                "floor_level": floor_level,
                "largest_room": winner["name"],
                "largest_room_id": winner["id"],
                "largest_area_m2": round(largest.area_m2, 2),
                "total_area_m2": round(total.value, 2),
                "room_count": len(
                    [space for space in building_map.spaces.values() if space["floor_id"] == floor_id and space["room_type"] == code]
                ),
            }
        )

    @beta_tool
    def reachable_rooms(
        start: str, floor_level: int, step_free: bool = False, min_width_m: float | None = None
    ) -> str:
        """How many rooms on a floor can be reached. Honours doors already closed with set_closed.

        Args:
            start: Space id or exact room name where the person or the load is.
            floor_level: Count rooms on this floor only.
            step_free: True to refuse stairs and steps. Lifts stay available.
            min_width_m: When set, every door on the path must be at least this wide, in metres.
        """
        try:
            start_id = building_map.resolve_space(start)
            floor_id = floor_of(floor_level)
            distances = building_map.distances_from(start_id, step_free=step_free, min_width=min_width_m)
        except (MapError, ParseError) as error:
            raise ToolError(str(error)) from error
        return as_json(
            {
                "start": building_map.describe_space(start_id),
                "floor_level": floor_level,
                "step_free": step_free,
                "min_width_m": min_width_m,
                "rooms": count_rooms_on_floor(building_map, floor_id, distances),
            }
        )

    @beta_tool
    def shared_wall_count(space: str) -> str:
        """How many other rooms on the same floor share at least 1 m of wall. Corridors, stairs, lifts and outdoors are excluded.

        Args:
            space: Space id or exact room name.
        """
        try:
            space_id = building_map.resolve_space(space)
            result = shared_walls_count(building_map, {"start": space_id})
        except MapError as error:
            raise ToolError(str(error)) from error
        return as_json({"space": building_map.spaces[space_id]["name"], "rooms": int(result.value), "neighbours": result.detail})

    @beta_tool
    def stacked_room(space: str, direction: str) -> str:
        """The space on the next floor up or down that covers most of this room.

        Args:
            space: Space id or exact room name.
            direction: "above" or "below".
        """
        try:
            space_id = building_map.resolve_space(space)
            result = room_stacked(building_map, {"start": space_id, "direction": direction})
        except (MapError, ValueError) as error:
            raise ToolError(str(error)) from error
        other = building_map.spaces[result.space_id]
        return as_json({"direction": direction, "room": other["name"], "room_id": other["id"]})

    @beta_tool
    def straight_line_distance(first: str, second: str) -> str:
        """3D straight-line distance between two room centroids at their floor elevations.

        Args:
            first: Space id or exact room name.
            second: Space id or exact room name.
        """
        try:
            first_id = building_map.resolve_space(first)
            second_id = building_map.resolve_space(second)
            result = straight_line_3d(building_map, {"first": first_id, "second": second_id})
        except MapError as error:
            raise ToolError(str(error)) from error
        return as_json({"metres": round(result.value, 2)})

    @beta_tool
    def what_if_door(first_space: str, second_space: str, floor_level: int) -> str:
        """How many rooms on another floor would get a shorter walk to the nearest entrance if a door were added. Does not edit the building.

        Args:
            first_space: Space id or exact room name.
            second_space: Space id or exact room name.
            floor_level: Floor to count, which is not the floor of the new door.
        """
        try:
            first_id = building_map.resolve_space(first_space)
            second_id = building_map.resolve_space(second_space)
            floor_id = floor_of(floor_level)
            result = what_if_door_skill(
                building_map, {"first": first_id, "second": second_id, "floor_id": floor_id}
            )
        except (MapError, ParseError) as error:
            raise ToolError(str(error)) from error
        return as_json({"floor_level": floor_level, "rooms_improved": int(result.value)})

    @beta_tool
    def nearest_asset(start: str, asset_type: str) -> str:
        """Room containing the requested asset type that is closest by walking distance.

        Args:
            start: Space id or exact room name.
            asset_type: Words such as "stove" or "office chair", matching the asset type.
        """
        try:
            start_id = building_map.resolve_space(start)
            code = asset_type_code(building_map, asset_type)
            space_id, length = nearest_asset_space(building_map, start_id, code)
        except (MapError, ParseError) as error:
            raise ToolError(str(error)) from error
        return as_json(
            {
                "asset_type": code,
                "room": building_map.spaces[space_id]["name"],
                "room_id": space_id,
                "walk_m": round(length, 2),
            }
        )

    @beta_tool
    def add_door(first_space: str, second_space: str, step_free: bool = True) -> str:
        """Add a door at the middle of the shared wall between two neighbouring rooms (edits the building).

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

    return [
        building_overview,
        search_building,
        find_route,
        set_closed,
        add_door,
        show_route,
        room_area,
        reachable_rooms,
        shared_wall_count,
        stacked_room,
        straight_line_distance,
        what_if_door,
        nearest_asset,
    ]
