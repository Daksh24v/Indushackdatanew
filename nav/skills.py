"""One deterministic answer per benchmark question kind. Inputs use the same ids as questions.jsonl."""

from dataclasses import dataclass, field

from starter_agent.building_map import DOOR_PREFIX, OUTSIDE, BuildingMap, MapError

from nav.geometry import (
    door_position,
    shared_wall_neighbors,
    space_area,
    space_polygon,
    stacked_space,
    straight_line_distance,
)

COUNT_KINDS = frozenset(
    {
        "wide_delivery_count",
        "step_free_reachable_count",
        "shared_walls_count",
        "what_if_door",
    }
)
SPACE_KINDS = frozenset({"largest_room", "room_stacked"})


@dataclass
class SkillResult:
    value: float | None = None
    space_id: str | None = None
    area_m2: float | None = None
    via_space_id: str | None = None
    target_space_id: str | None = None
    node_ids: list[str] = field(default_factory=list)
    detail: str = ""


def public_node_ids(nodes: list[str]) -> list[str]:
    return [node[len(DOOR_PREFIX) :] if node.startswith(DOOR_PREFIX) else node for node in nodes]


def spaces_of_type(building: BuildingMap, floor_id: str, room_type: str) -> list[dict]:
    return [
        space
        for space in building.spaces.values()
        if space["floor_id"] == floor_id and space["room_type"] == room_type
    ]


def floor_area_total(building: BuildingMap, inputs: dict) -> SkillResult:
    spaces = spaces_of_type(building, inputs["floor_id"], inputs["room_type"])
    return SkillResult(value=sum(space_area(building, space["id"]) for space in spaces))


def largest_room(building: BuildingMap, inputs: dict) -> SkillResult:
    spaces = spaces_of_type(building, inputs["floor_id"], inputs["room_type"])
    if not spaces:
        raise MapError(f"No {inputs['room_type']} on {inputs['floor_id']}.")
    winner = min(spaces, key=lambda space: (-space_area(building, space["id"]), space["id"]))
    area = space_area(building, winner["id"])
    return SkillResult(value=area, space_id=winner["id"], area_m2=area)


def count_rooms_on_floor(building: BuildingMap, floor_id: str, reachable: dict[str, float]) -> int:
    return sum(
        1
        for space in building.spaces.values()
        if space["id"] in reachable and space["floor_id"] == floor_id and space["type"] == "room"
    )


def wide_delivery_count(building: BuildingMap, inputs: dict) -> SkillResult:
    distances = building.distances_from(inputs["start"], step_free=True, min_width=float(inputs["min_width"]))
    return SkillResult(value=count_rooms_on_floor(building, inputs["floor_id"], distances))


def step_free_reachable_count(building: BuildingMap, inputs: dict) -> SkillResult:
    clone = building.copy()
    clone.set_closed(inputs["closed"], closed=True)
    distances = clone.distances_from(inputs["start"], step_free=True)
    return SkillResult(value=count_rooms_on_floor(clone, inputs["floor_id"], distances))


def shared_walls_count(building: BuildingMap, inputs: dict) -> SkillResult:
    neighbors = shared_wall_neighbors(building, inputs["start"])
    names = [building.spaces[space_id]["name"] for space_id in neighbors]
    return SkillResult(value=len(neighbors), detail=", ".join(names))


def straight_line_3d(building: BuildingMap, inputs: dict) -> SkillResult:
    return SkillResult(value=straight_line_distance(building, inputs["first"], inputs["second"]))


def room_stacked(building: BuildingMap, inputs: dict) -> SkillResult:
    space_id = stacked_space(building, inputs["start"], inputs["direction"])
    if space_id is None:
        raise MapError(f"No room {inputs['direction']} {inputs['start']}.")
    return SkillResult(space_id=space_id)


def nearest_asset_space(building: BuildingMap, start: str, asset_type: str) -> tuple[str, float]:
    spaces = {
        asset["space_id"]
        for asset in building.assets.values()
        if asset["type"] == asset_type and asset["space_id"] in building.spaces
    }
    if not spaces:
        raise MapError(f"No {asset_type} in this building.")
    distances = building.distances_from(start)
    reachable = [(distances[space_id], space_id) for space_id in spaces if space_id in distances]
    if not reachable:
        raise MapError(f"No reachable {asset_type} from {start}.")
    length, space_id = min(reachable, key=lambda item: (item[0], item[1]))
    return space_id, length


def errand(building: BuildingMap, inputs: dict) -> SkillResult:
    via_id, _via_length = nearest_asset_space(building, inputs["start"], inputs["asset_type"])
    rooms = spaces_of_type(building, inputs["floor_id"], inputs["room_type"])
    if not rooms:
        raise MapError(f"No {inputs['room_type']} on {inputs['floor_id']}.")
    target = min(rooms, key=lambda space: (-space_area(building, space["id"]), space["id"]))
    via = None if via_id in (inputs["start"], target["id"]) else [via_id]
    route = building.route(inputs["start"], target["id"], via=via)
    return SkillResult(
        value=route.length_m,
        via_space_id=via_id,
        target_space_id=target["id"],
        node_ids=public_node_ids(route.node_path),
    )


def what_if_door(building: BuildingMap, inputs: dict) -> SkillResult:
    first_id, second_id = inputs["first"], inputs["second"]
    before = building.distances_from(OUTSIDE)
    clone = building.copy()
    position = door_position(space_polygon(building, first_id), space_polygon(building, second_id))
    clone.insert_door(first_id, second_id, position)
    after = clone.distances_from(OUTSIDE)
    improved = 0
    for space in building.spaces.values():
        if space["floor_id"] != inputs["floor_id"] or space["type"] != "room":
            continue
        old = before.get(space["id"], float("inf"))
        new = after.get(space["id"], float("inf"))
        if old - new > 1.0:
            improved += 1
    return SkillResult(value=improved)


SKILLS = {
    "floor_area_total": floor_area_total,
    "largest_room": largest_room,
    "wide_delivery_count": wide_delivery_count,
    "step_free_reachable_count": step_free_reachable_count,
    "shared_walls_count": shared_walls_count,
    "straight_line_3d": straight_line_3d,
    "room_stacked": room_stacked,
    "errand": errand,
    "what_if_door": what_if_door,
}


def run_skill(building: BuildingMap, kind: str, inputs: dict) -> SkillResult:
    if kind not in SKILLS:
        raise MapError(f"Unknown question kind {kind}.")
    return SKILLS[kind](building, inputs)
