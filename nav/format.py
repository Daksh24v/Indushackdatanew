"""Commit a skill result to the single Answer: line the scorer reads."""

from starter_agent.building_map import BuildingMap

from nav.skills import COUNT_KINDS, SkillResult


def format_reply(building: BuildingMap, kind: str, result: SkillResult) -> str:
    if kind == "largest_room":
        name = building.spaces[result.space_id]["name"]
        area = f"{result.area_m2:.2f}"
        return f"{name} is the largest of its type, at {area} m².\nAnswer: {name}, {area} m²"
    if kind == "room_stacked":
        name = building.spaces[result.space_id]["name"]
        return f"That room is {name}.\nAnswer: {name}"
    if kind in COUNT_KINDS:
        return f"Answer: {int(round(result.value))}"
    return f"Answer: {result.value:.2f}"
