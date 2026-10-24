"""Turn the kit's question templates into the same inputs the oracle scores."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from starter_agent.building_map import BuildingMap

FLOOR_PHRASE = r"(?:ground floor|floor -?\d+|basement \d+)"
FLOOR_SUFFIX = re.compile(rf"\s*\((?P<floor>{FLOOR_PHRASE})\)\s*$", re.IGNORECASE)

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "what_if_door",
        re.compile(
            rf"add a door between (?P<first>.+?) and (?P<second>.+?), in the middle of the wall they share\? "
            rf"How many rooms on the (?P<floor>{FLOOR_PHRASE})",
            re.IGNORECASE,
        ),
    ),
    (
        "shared_walls_count",
        re.compile(r"soundproofing (?P<start>.+?)\. How many other rooms", re.IGNORECASE),
    ),
    (
        "straight_line_3d",
        re.compile(
            r"wireless sensor link between (?P<first>.+?) and (?P<second>.+?)\. What is the straight-line",
            re.IGNORECASE,
        ),
    ),
    (
        "wide_delivery_count",
        re.compile(
            rf"moving a (?P<width>\d+(?:\.\d+)?) m wide .+? at least \d+(?:\.\d+)? m wide\. "
            rf"It arrives through the entrance into (?P<start>.+?)\. Into how many rooms on the (?P<floor>{FLOOR_PHRASE})",
            re.IGNORECASE,
        ),
    ),
    (
        "errand",
        re.compile(
            rf"I'm in (?P<start>.+?)\. I need to grab an? (?P<asset>.+?) from the room that has one closest to me, "
            rf"then carry it to the largest (?P<room>.+?) on the (?P<floor>{FLOOR_PHRASE})",
            re.IGNORECASE,
        ),
    ),
    (
        "room_stacked",
        re.compile(r"dripping from the ceiling in (?P<start>.+?)\. Which room is directly above", re.IGNORECASE),
    ),
    (
        "room_stacked",
        re.compile(r"leaking onto the floor of (?P<start>.+?)\. Which room directly below", re.IGNORECASE),
    ),
    (
        "step_free_reachable_count",
        re.compile(
            rf"(?P<closed>.+?) is out of service\. A wheelchair user comes in through the entrance into "
            rf"(?P<start>.+?)\. How many rooms on the (?P<floor>{FLOOR_PHRASE})",
            re.IGNORECASE,
        ),
    ),
    (
        "floor_area_total",
        re.compile(
            rf"total floor area of every (?P<room>.+?) on the (?P<floor>{FLOOR_PHRASE})",
            re.IGNORECASE,
        ),
    ),
    (
        "largest_room",
        re.compile(rf"largest (?P<room>.+?) on the (?P<floor>{FLOOR_PHRASE})", re.IGNORECASE),
    ),
]


class ParseError(ValueError):
    """The question is not one of the known templates, or a phrase does not resolve."""


@dataclass
class ParsedQuestion:
    kind: str
    inputs: dict


def floor_level(phrase: str) -> int:
    text = phrase.strip().lower()
    if text == "ground floor":
        return 0
    match = re.fullmatch(r"floor (-?\d+)", text)
    if match:
        return int(match.group(1))
    match = re.fullmatch(r"basement (\d+)", text)
    if match:
        return -int(match.group(1))
    raise ParseError(f"Unrecognised floor '{phrase}'.")


def floor_id_for_level(building: BuildingMap, level: int) -> str:
    matches = [floor["id"] for floor in building.floors.values() if floor["level"] == level]
    if len(matches) != 1:
        raise ParseError(f"Floor level {level} matches {matches or 'nothing'}.")
    return matches[0]


def split_floor_suffix(phrase: str) -> tuple[str, int | None]:
    match = FLOOR_SUFFIX.search(phrase.strip())
    if match is None:
        return phrase.strip(), None
    return phrase.strip()[: match.start()].strip(), floor_level(match.group("floor"))


def resolve_space(building: BuildingMap, phrase: str) -> str:
    name, level = split_floor_suffix(phrase)
    lowered = name.lower()

    def on_level(space: dict) -> bool:
        return level is None or building.floors[space["floor_id"]]["level"] == level

    by_name = [space["id"] for space in building.spaces.values() if on_level(space) and space["name"].lower() == lowered]
    if len(by_name) == 1:
        return by_name[0]
    by_ref = [
        space["id"]
        for space in building.spaces.values()
        if on_level(space) and (space.get("ref") or "").lower() == lowered
    ]
    if len(by_ref) == 1:
        return by_ref[0]
    if not by_name and not by_ref:
        raise ParseError(f"No space matches '{phrase}'.")
    raise ParseError(f"'{phrase}' matches {by_name or by_ref}.")


def match_code(phrase: str, codes: set[str], label: str) -> str:
    normalized = " ".join(phrase.lower().replace("_", " ").split())
    matches = [code for code in codes if code.replace("_", " ") == normalized]
    if len(matches) != 1:
        raise ParseError(f"{label} '{phrase}' matches {matches or 'nothing'}.")
    return matches[0]


def room_type_code(building: BuildingMap, phrase: str) -> str:
    return match_code(phrase, {space["room_type"] for space in building.spaces.values()}, "Room type")


def asset_type_code(building: BuildingMap, phrase: str) -> str:
    return match_code(phrase, {asset["type"] for asset in building.assets.values()}, "Asset type")


def inputs_from_slots(building: BuildingMap, slots: dict) -> ParsedQuestion:
    kind = slots.get("kind")
    if not isinstance(kind, str):
        raise ParseError("Slot extraction did not name a question kind.")

    def floor_id() -> str:
        phrase = slots.get("floor_phrase")
        if not phrase:
            raise ParseError(f"{kind} is missing a floor.")
        return floor_id_for_level(building, floor_level(str(phrase)))

    def place(key: str) -> str:
        phrase = slots.get(key)
        if not phrase:
            raise ParseError(f"{kind} is missing {key}.")
        return resolve_space(building, str(phrase))

    if kind in {"floor_area_total", "largest_room"}:
        phrase = slots.get("room_type_phrase")
        if not phrase:
            raise ParseError(f"{kind} is missing a room type.")
        return ParsedQuestion(kind, {"floor_id": floor_id(), "room_type": room_type_code(building, str(phrase))})
    if kind == "wide_delivery_count":
        if slots.get("min_width") is None:
            raise ParseError("Wide delivery is missing a width.")
        return ParsedQuestion(
            kind,
            {"start": place("start_phrase"), "floor_id": floor_id(), "min_width": float(slots["min_width"])},
        )
    if kind == "errand":
        asset = slots.get("asset_type_phrase")
        room = slots.get("room_type_phrase")
        if not asset or not room:
            raise ParseError("Errand is missing the asset or the destination room type.")
        return ParsedQuestion(
            kind,
            {
                "start": place("start_phrase"),
                "asset_type": asset_type_code(building, str(asset)),
                "floor_id": floor_id(),
                "room_type": room_type_code(building, str(room)),
            },
        )
    if kind == "straight_line_3d":
        return ParsedQuestion(kind, {"first": place("start_phrase"), "second": place("second_phrase")})
    if kind == "shared_walls_count":
        return ParsedQuestion(kind, {"start": place("start_phrase")})
    if kind == "room_stacked":
        direction = slots.get("direction")
        if direction not in {"above", "below"}:
            raise ParseError(f"Stack direction must be above or below, not {direction}.")
        return ParsedQuestion(kind, {"start": place("start_phrase"), "direction": direction})
    if kind == "what_if_door":
        first = place("start_phrase")
        second = place("second_phrase")
        return ParsedQuestion(
            kind,
            {
                "first": first,
                "second": second,
                "door_floor_id": building.spaces[first]["floor_id"],
                "floor_id": floor_id(),
            },
        )
    if kind == "step_free_reachable_count":
        closed = slots.get("closed_phrase")
        if not closed:
            raise ParseError("Step-free question is missing the closed element.")
        return ParsedQuestion(
            kind,
            {
                "closed": building.find_element(str(closed)),
                "start": place("start_phrase"),
                "floor_id": floor_id(),
            },
        )
    raise ParseError(f"Unknown question kind {kind}.")


def slots_from_template(question: str) -> dict:
    for kind, pattern in PATTERNS:
        match = pattern.search(question)
        if match is None:
            continue
        groups = match.groupdict()
        direction = None
        if kind == "room_stacked":
            direction = "above" if "directly above" in question.lower() else "below"
        return {
            "kind": kind,
            "floor_phrase": groups.get("floor"),
            "room_type_phrase": groups.get("room"),
            "start_phrase": groups.get("start") or groups.get("first"),
            "second_phrase": groups.get("second"),
            "asset_type_phrase": groups.get("asset"),
            "min_width": float(groups["width"]) if groups.get("width") else None,
            "direction": direction,
            "closed_phrase": groups.get("closed"),
        }
    raise ParseError("Question does not match a known template.")


def parse_question(building: BuildingMap, question: str) -> ParsedQuestion:
    return inputs_from_slots(building, slots_from_template(question))


def parse_json_object(text: str) -> dict:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match is None:
        raise ParseError(f"No JSON object in model output: {text[:200]}")
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise ParseError(f"Model returned invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ParseError("Model JSON was not an object.")
    return value


def parse_with_model(building: BuildingMap, question: str) -> ParsedQuestion:
    """One structured call. The model returns phrases, never the measured answer."""
    import anthropic

    catalog_rooms = sorted({space["room_type"].replace("_", " ") for space in building.spaces.values()})
    catalog_assets = sorted({asset["type"].replace("_", " ") for asset in building.assets.values()})
    catalog_closures = sorted(connector["name"] for connector in building.connectors.values())
    prompt = f"""Extract slots from this indoor-navigation question. Do not compute a distance, area, or count.

Return one JSON object with these keys:
kind, floor_phrase, room_type_phrase, start_phrase, second_phrase, asset_type_phrase, min_width, direction, closed_phrase.
Use null for unused keys.
kind is one of: floor_area_total, largest_room, wide_delivery_count, errand, straight_line_3d, shared_walls_count, room_stacked, what_if_door, step_free_reachable_count.
direction is "above" or "below" only for room_stacked.
Copy place phrases exactly as they appear, including a trailing floor in parentheses.
min_width is a number of metres, or null.

Room types in this building: {", ".join(catalog_rooms)}
Asset types in this building: {", ".join(catalog_assets)}
Stairs and lifts: {", ".join(catalog_closures)}

Question:
{question}"""
    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=700,
        system="You extract JSON slots from a question. You never calculate the answer.",
        messages=[{"role": "user", "content": prompt}],
    )
    text = "\n".join(block.text for block in message.content if block.type == "text")
    return inputs_from_slots(building, parse_json_object(text))


def check_against_dataset(directory: Path) -> int:
    """Compare template parses with the inputs stored on dev and train questions."""
    failures = 0
    total = 0
    cache: dict[Path, BuildingMap] = {}
    for path in sorted(directory.glob("*/questions.jsonl")):
        building = cache.get(path.parent)
        if building is None:
            building = BuildingMap.load(path.parent)
            cache[path.parent] = building
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            question = json.loads(line)
            total += 1
            try:
                parsed = parse_question(building, question["question"])
            except ParseError as error:
                failures += 1
                print(f"BAD {question['id']} parse {error}")
                continue
            inputs = question.get("inputs")
            if parsed.kind != question["kind"] or (inputs is not None and parsed.inputs != inputs):
                failures += 1
                print(f"BAD {question['id']} got {parsed.kind} {parsed.inputs}")
    print(f"{directory}: {total - failures} / {total} parses match the stored inputs")
    return 0 if failures == 0 else 1


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="nav.parse")
    parser.add_argument("directory", type=Path, nargs="?", default=Path("data/dev"))
    arguments = parser.parse_args()
    raise SystemExit(check_against_dataset(arguments.directory))


if __name__ == "__main__":
    main()
