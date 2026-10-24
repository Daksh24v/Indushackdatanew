"""Dry run for a question file without any LLM: can every room, floor, room type and item type it mentions be
resolved against its building? Run it on the Sunday building the moment it arrives.

  uv run python -m core.check_questions data/test data/test/questions.csv
  uv run python -m core.check_questions data/sunday/<id> data/sunday/<id>/questions.jsonl"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from core.building import Building, MapError

MENTION = re.compile(r"([A-ZÄÖÜ][^()]*?(?:\s*\(apartment \d+\))?)\s*\((Ground floor|Floor -?\d+|Basement \d+)\)")
FLOOR_WORD = re.compile(r"on the (ground floor|floor -?\d+|basement \d+)", re.IGNORECASE)
ROOM_TYPE = re.compile(r"(?:every|largest) ([a-z][a-z ]+?) on the", re.IGNORECASE)
ASSET_TYPE = re.compile(r"grab an? ([a-z0-9][a-z0-9 ]+?) from", re.IGNORECASE)
WIDTH = re.compile(r"(\d+(?:\.\d+)?) m wide")


def load_questions(path: Path) -> list[dict]:
    if path.suffix == ".csv":
        with path.open(encoding="utf-8") as handle:
            return [{"id": r.get("question_id") or r.get("id"), "building_id": r["building_id"], "question": r["question"]}
                    for r in csv.DictReader(handle)]
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def building_dir(root: Path, building_id: str) -> Path:
    if (root / "building.json").exists():
        return root
    return root / building_id


def resolve_mention(building: Building, words: list[str], floor: str) -> tuple[str | None, str]:
    """Try progressively shorter suffixes of the capitalised phrase, since the regex may catch sentence words."""
    error = ""
    for start in range(len(words)):
        candidate = " ".join(words[start:]) + f" ({floor})"
        try:
            return building.resolve_space(candidate), ""
        except MapError as exc:
            error = str(exc)
    return None, error


def main() -> None:
    parser = argparse.ArgumentParser(prog="core.check_questions")
    parser.add_argument("root", type=Path, help="a building directory or a split directory")
    parser.add_argument("questions", type=Path, help="questions.csv or questions.jsonl")
    arguments = parser.parse_args()
    questions = load_questions(arguments.questions)
    buildings: dict[str, Building] = {}
    problems: list[str] = []
    counts = {"questions": len(questions), "rooms": 0, "floors": 0, "room_types": 0, "asset_types": 0}
    for question in questions:
        text = question["question"]
        building = buildings.get(question["building_id"])
        if building is None:
            building = buildings[question["building_id"]] = Building.load(building_dir(arguments.root, question["building_id"]))
        for match in MENTION.finditer(text):
            counts["rooms"] += 1
            resolved, error = resolve_mention(building, match.group(1).split(), match.group(2))
            if resolved is None:
                problems.append(f"{question['id']}: room '{match.group(0)}' -> {error[:120]}")
        for match in FLOOR_WORD.finditer(text):
            counts["floors"] += 1
            try:
                building.resolve_floor(match.group(1))
            except MapError as exc:
                problems.append(f"{question['id']}: floor '{match.group(1)}' -> {exc}")
        for match in ROOM_TYPE.finditer(text):
            counts["room_types"] += 1
            try:
                building.normalize_room_type(match.group(1))
            except MapError as exc:
                problems.append(f"{question['id']}: room type '{match.group(1)}' -> {str(exc)[:120]}")
        for match in ASSET_TYPE.finditer(text):
            counts["asset_types"] += 1
            try:
                building.normalize_asset_type(match.group(1))
            except MapError as exc:
                problems.append(f"{question['id']}: item type '{match.group(1)}' -> {str(exc)[:120]}")
    print(f"{counts['questions']} questions in {len(buildings)} buildings: {counts['rooms']} room mentions, "
          f"{counts['floors']} floor words, {counts['room_types']} room types, {counts['asset_types']} item types")
    if problems:
        print(f"{len(problems)} problems:")
        print("\n".join("  " + line for line in problems))
    else:
        print("everything resolves")
    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
