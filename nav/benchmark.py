"""One-command benchmark runner.

  uv run python run_benchmark <building_dir> <questions_file> <answers_file>
"""

import argparse
import csv
import json
from pathlib import Path

from benchmark.score import committed_answer, score_reply
from starter_agent.building_map import BuildingMap, MapError

from nav.format import format_reply
from nav.parse import ParseError, parse_question, parse_with_model
from nav.skills import run_skill

DISTANCE_KINDS = {"errand", "straight_line_3d"}
SUBMISSION_COLUMNS = ("question_id", "answer")


def load_question_file(path: Path) -> tuple[list[dict], dict[str, dict]]:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return [], {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        items = [json.loads(line) for line in raw.splitlines() if line.strip()]
    else:
        if isinstance(data, dict) and "questions" in data:
            items = data["questions"]
        elif isinstance(data, dict):
            items = [data]
        else:
            items = data
    questions = []
    by_id = {}
    for item in items:
        question_id = str(item["id"])
        by_id[question_id] = item
        questions.append(
            {
                "id": question_id,
                "text": item.get("question") or item.get("text") or "",
                "constraints": item.get("constraints") or {},
            }
        )
    return questions, by_id


def answer_line(reply: str) -> str | None:
    line = committed_answer(reply)
    return f"Answer: {line}" if line else None


def route_record(building: BuildingMap, constraints: dict) -> dict:
    start = constraints.get("start") or constraints.get("from")
    destination = constraints.get("destination") or constraints.get("to")
    if not start or not destination:
        raise MapError("A route constraint needs start and destination.")
    closed = constraints.get("closed")
    if isinstance(closed, str):
        building.set_closed(closed, True)
    elif isinstance(closed, list):
        for element in closed:
            building.set_closed(element, True)
    route = building.route(
        start,
        destination,
        step_free=bool(constraints.get("step_free")),
        via=constraints.get("via"),
        min_width=constraints.get("min_width"),
    )
    reply = f"The walk is {route.length_m:.2f} m.\nAnswer: {route.length_m:.2f}"
    return {
        "room_id": route.destination_space_id,
        "asset_id": None,
        "node_ids": [node.removeprefix("door:") for node in route.node_path],
        "length_m": route.length_m,
        "answer_line": answer_line(reply),
        "reply": reply,
    }


def record_from_parsed(building: BuildingMap, parsed) -> dict:
    result = run_skill(building, parsed.kind, parsed.inputs)
    reply = format_reply(building, parsed.kind, result)
    return {
        "room_id": result.space_id,
        "asset_id": None,
        "node_ids": result.node_ids,
        "length_m": result.value if parsed.kind in DISTANCE_KINDS else None,
        "answer_line": answer_line(reply),
        "reply": reply,
    }


def solve(building_directory: Path, text: str, constraints: dict) -> dict:
    building = BuildingMap.load(building_directory)
    if text.strip():
        try:
            return record_from_parsed(building, parse_question(building, text))
        except ParseError:
            pass
    if constraints.get("start") or constraints.get("from"):
        return route_record(building, constraints)
    if not text.strip():
        raise ParseError("Question has no text and no route constraints.")
    return record_from_parsed(building, parse_with_model(building, text))


def write_answers(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "id": row["id"],
            "room_id": row.get("room_id"),
            "asset_id": row.get("asset_id"),
            "node_ids": row.get("node_ids") or [],
            "length_m": row.get("length_m"),
            "answer_line": row.get("answer_line"),
        }
        for row in rows
    ]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_submission(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(SUBMISSION_COLUMNS)
        for row in rows:
            writer.writerow((row["id"], row.get("reply") or ""))


def run(building_directory: Path, questions_file: Path, answers_file: Path, submission_csv: Path | None) -> None:
    if not (building_directory / "building.json").exists() and (questions_file.parent / "building.json").exists():
        building_directory = questions_file.parent
    questions, by_id = load_question_file(questions_file)
    rows = []
    scored = 0
    correct = 0
    for question in questions:
        try:
            record = solve(building_directory, question["text"], question["constraints"])
        except (ParseError, MapError, KeyError, ValueError) as error:
            record = {
                "room_id": None,
                "asset_id": None,
                "node_ids": [],
                "length_m": None,
                "answer_line": None,
                "reply": f"I could not measure that ({error}).",
            }
        record["id"] = question["id"]
        rows.append(record)
        gold = by_id.get(question["id"], {}).get("answer")
        if gold:
            scored += 1
            ok = score_reply(gold, record.get("reply") or "")
            correct += int(ok)
            print(f"[benchmark] {question['id']:34s} {'OK' if ok else 'BAD'}")
        else:
            print(f"[benchmark] {question['id']:34s} saved")
    write_answers(answers_file, rows)
    print(f"wrote {len(rows)} answers to {answers_file}")
    if submission_csv is not None:
        write_submission(submission_csv, rows)
        print(f"wrote submission CSV to {submission_csv}")
    if scored:
        print(f"scored {correct} / {scored}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="run_benchmark")
    parser.add_argument("building_dir", type=Path, help="directory containing building.json")
    parser.add_argument("questions_file", type=Path, help="questions.json or questions.jsonl")
    parser.add_argument("answers_file", type=Path, help="where to write answers.json")
    parser.add_argument("--submission-csv", type=Path, default=None, help="also write question_id,answer")
    arguments = parser.parse_args()
    run(arguments.building_dir, arguments.questions_file, arguments.answers_file, arguments.submission_csv)


if __name__ == "__main__":
    main()
