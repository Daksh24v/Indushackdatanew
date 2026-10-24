"""Score nav skills against questions.jsonl inputs. No language model.

  uv run python -m nav.oracle data/dev
  uv run python -m nav.oracle data/train --kinds shared_walls_count
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from starter_agent.building_map import BuildingMap

from nav.skills import run_skill

QUESTIONS_FILENAME = "questions.jsonl"


def within(value: float, spec: dict) -> bool:
    expected = float(spec["value"])
    tolerance = max(abs(expected) * float(spec["relative_tolerance"]), float(spec["absolute_tolerance"]))
    return abs(value - expected) <= tolerance + 1e-6


def load_questions(directory: Path) -> list[tuple[Path, dict]]:
    questions = []
    for path in sorted(directory.glob(f"*/{QUESTIONS_FILENAME}")):
        with path.open(encoding="utf-8") as handle:
            questions += [(path.parent, json.loads(line)) for line in handle if line.strip()]
    return questions


def matches(result, answer: dict) -> tuple[bool, str]:
    if answer["type"] == "number":
        if result.value is None:
            return False, "no number"
        ok = within(result.value, answer)
        extra = ""
        if "via" in answer and result.via_space_id != answer["via"]:
            ok = False
            extra = f" via {result.via_space_id} != {answer['via']}"
        if "target" in answer and result.target_space_id != answer["target"]:
            ok = False
            extra += f" target {result.target_space_id} != {answer['target']}"
        return ok, f"got {result.value} expected {answer['value']}{extra} {result.detail}"
    if answer["type"] == "space":
        ok = result.space_id == answer["space_id"]
        detail = f"got {result.space_id} expected {answer['space_id']}"
        if ok and "area" in answer:
            ok = result.area_m2 is not None and within(result.area_m2, answer["area"])
            detail += f" area {result.area_m2} expected {answer['area']['value']}"
        return ok, detail
    return False, f"unknown answer type {answer['type']}"


def evaluate(directory: Path, limit: int | None, kinds: list[str] | None) -> int:
    cache: dict[Path, BuildingMap] = {}
    results: Counter[tuple[str, bool]] = Counter()
    failures = 0
    questions = load_questions(directory)
    if kinds:
        questions = [item for item in questions if item[1]["kind"] in kinds]
    if limit is not None:
        questions = questions[:limit]
    for building_directory, question in questions:
        building = cache.get(building_directory)
        if building is None:
            building = BuildingMap.load(building_directory)
            cache[building_directory] = building
        kind = question["kind"]
        try:
            # Skills that edit a copy must not see a previous question's closures.
            working = building.copy() if kind in {"step_free_reachable_count", "what_if_door", "errand"} else building
            result = run_skill(working, kind, question["inputs"])
            correct, detail = matches(result, question["answer"])
        except Exception as error:  # noqa: BLE001 — one bad polygon should not stop the sweep
            correct, detail = False, f"{type(error).__name__}: {error}"
        results[(kind, correct)] += 1
        if not correct:
            failures += 1
            print(f"BAD {question['id']:32s} {kind:24s} {detail}")
    print("\nkind                  correct / total")
    for kind in sorted({item for item, _correct in results}):
        correct = results[(kind, True)]
        total = correct + results[(kind, False)]
        print(f"  {kind:22s} {correct:4d} / {total}")
    total_correct = sum(count for (_kind, correct), count in results.items() if correct)
    total = sum(results.values())
    print(f"  {'all':22s} {total_correct:4d} / {total}")
    return 0 if failures == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="nav.oracle")
    parser.add_argument("directory", type=Path, nargs="?", default=Path("data/dev"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--kinds", nargs="+", default=None)
    arguments = parser.parse_args()
    raise SystemExit(evaluate(arguments.directory, arguments.limit, arguments.kinds))


if __name__ == "__main__":
    main()
