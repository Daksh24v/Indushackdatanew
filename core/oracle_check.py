"""Prove the core against the dataset's known answers. No API calls, no cost, seconds to run.

  uv run python -m core.oracle_check data/dev                 # all benchmark kinds
  uv run python -m core.oracle_check data/train --kinds errand what_if_door
  uv run python -m core.oracle_check data/dev --warmup        # route model: warm-up questions through Building

Each train/dev question carries the exact `inputs` behind it. We call the matching Building method with those
inputs, format the Answer line exactly as the agent will, and score it with the official benchmark.score."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from benchmark.score import score_reply
from core.building import Building, MapError

ROUTE_TOLERANCE = 0.01


def answer_line(building: Building, question: dict) -> str:
    """The Answer line for a benchmark question computed from its inputs."""
    kind, inputs = question["kind"], question["inputs"]
    if kind == "floor_area_total":
        return f"Answer: {building.total_area(inputs['floor_id'], inputs['room_type'])['total_m2']:.2f}"
    if kind == "largest_room":
        best = building.largest_room(inputs["floor_id"], inputs["room_type"])
        return f"Answer: {best['label']}, {best['area_m2']:.1f} m²"
    if kind == "straight_line_3d":
        return f"Answer: {building.straight_line_3d(inputs['first'], inputs['second'])['distance_m']:.2f}"
    if kind == "wide_delivery_count":
        result = building.reachable_rooms(inputs["start"], inputs["floor_id"], step_free=True, min_width_m=inputs["min_width"])
        return f"Answer: {result['count']}"
    if kind == "step_free_reachable_count":
        result = building.reachable_rooms(inputs["start"], inputs["floor_id"], step_free=True, closed=[inputs["closed"]])
        return f"Answer: {result['count']}"
    if kind == "shared_walls_count":
        return f"Answer: {building.shared_wall_rooms(inputs['start'])['count']}"
    if kind == "room_stacked":
        return f"Answer: {building.stacked_room(inputs['start'], inputs['direction'])['label']}"
    if kind == "errand":
        result = building.errand(inputs["start"], inputs["asset_type"], inputs["floor_id"], inputs["room_type"])
        return f"Answer: {result['total_m']:.2f}"
    if kind == "what_if_door":
        return f"Answer: {building.what_if_door(inputs['first'], inputs['second'], inputs['floor_id'])['count']}"
    raise NotImplementedError(kind)


def warmup_correct(building: Building, question: dict) -> bool:
    """Warm-up questions: routes through Building.route, item names through resolve_space, counts through assets_of_type."""
    answer, kind = question["answer"], question["kind"]
    if kind == "locate":
        # the item's name must resolve to the room the dataset says it is in
        asset = building.assets.get(answer["asset_id"])
        return asset is not None and building.resolve_space(asset["name"]) == answer["space_id"]
    if kind == "count":
        return len(building.assets_of_type(answer["asset_type"], answer["floor_level"])) == answer["count"]
    building.closed.clear()
    if "closed_element_id" in answer:
        building.set_closed(answer["closed_element_id"], True)
    try:
        if kind == "tour":
            stops = answer["stops"]
            route = building.route(stops[0], stops[-1], via=stops[1:-1])
        else:
            route = building.route(answer["from_space_id"], answer["to_space_id"], step_free=kind == "route_step_free")
    except MapError:
        return answer.get("reachable") is False
    if answer.get("reachable") is False:
        return False
    optimal = answer["optimal_length_m"]
    return abs(route.length_m - optimal) <= max(ROUTE_TOLERANCE * optimal, ROUTE_TOLERANCE)


def load(directory: Path, filename: str) -> dict[Path, list[dict]]:
    by_building: dict[Path, list[dict]] = {}
    for path in sorted(directory.glob(f"*/{filename}")):
        with path.open(encoding="utf-8") as handle:
            by_building[path.parent] = [json.loads(line) for line in handle if line.strip()]
    return by_building


def run(directory: Path, kinds: list[str] | None, warmup: bool, max_failures: int, buildings: list[str] | None) -> int:
    filename = "questions_basic.jsonl" if warmup else "questions.jsonl"
    results: Counter[tuple[str, str]] = Counter()
    failures: list[str] = []
    slowest: list[tuple[float, str]] = []
    started = time.perf_counter()
    for building_dir, questions in load(directory, filename).items():
        if buildings and building_dir.name not in buildings:
            continue
        questions = [q for q in questions if not kinds or q["kind"] in kinds]
        if not questions:
            continue
        building = Building.load(building_dir)
        tick = time.perf_counter()
        for question in questions:
            kind = question["kind"]
            try:
                if warmup:
                    if kind not in {"route", "route_step_free", "route_with_closure", "tour", "locate", "count"}:
                        results[(kind, "skipped")] += 1
                        continue
                    correct, detail = warmup_correct(building, question), ""
                else:
                    reply = answer_line(building, question)
                    correct = score_reply(question["answer"], reply)
                    detail = f"got {reply!r}, expected {expected(question['answer'])}"
            except NotImplementedError:
                results[(kind, "skipped")] += 1
                continue
            except MapError as error:
                correct, detail = False, f"MapError: {error}"
            results[(kind, "ok" if correct else "bad")] += 1
            if not correct:
                failures.append(f"  {question['id']:32s} {kind:26s} {detail}")
        slowest.append((time.perf_counter() - tick, building_dir.name))
    print(f"{'kind':28s} {'ok':>5s} {'bad':>5s} {'skipped':>8s}")
    total_ok = total_bad = 0
    for kind in sorted({kind for kind, _ in results}):
        ok, bad, skipped = results[(kind, "ok")], results[(kind, "bad")], results[(kind, "skipped")]
        total_ok += ok
        total_bad += bad
        print(f"  {kind:26s} {ok:5d} {bad:5d} {skipped:8d}")
    scored = total_ok + total_bad
    print(f"  {'all':26s} {total_ok:5d} {total_bad:5d}   ({total_ok / max(scored, 1):.1%} of {scored} scored)")
    if failures:
        print(f"\nfailures (first {max_failures} of {len(failures)}):")
        print("\n".join(failures[:max_failures]))
    slowest.sort(reverse=True)
    print(f"\n{time.perf_counter() - started:.1f}s total; slowest buildings: "
          + ", ".join(f"{name} {seconds:.1f}s" for seconds, name in slowest[:3]))
    return 0 if total_bad == 0 else 1


def expected(answer: dict) -> str:
    if answer["type"] == "number":
        return f"{answer['value']} (±{answer['relative_tolerance']:.0%} / ±{answer['absolute_tolerance']})"
    text = " | ".join(answer["accept"])
    if "area" in answer:
        text += f", area {answer['area']['value']}"
    return text


def main() -> None:
    parser = argparse.ArgumentParser(prog="core.oracle_check")
    parser.add_argument("directory", type=Path, help="a split directory such as data/dev")
    parser.add_argument("--kinds", nargs="+", default=None, help="only these question kinds")
    parser.add_argument("--buildings", nargs="+", default=None, help="only these building ids")
    parser.add_argument("--warmup", action="store_true", help="check the route model on questions_basic.jsonl")
    parser.add_argument("--failures", type=int, default=10, help="how many failures to print")
    arguments = parser.parse_args()
    raise SystemExit(run(arguments.directory, arguments.kinds, arguments.warmup, arguments.failures, arguments.buildings))


if __name__ == "__main__":
    main()
