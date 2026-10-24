"""Benchmark entry point: question text in, scored reply out.

  uv run python -m benchmark.evaluate data/dev --agent nav.answer:answer
"""

from pathlib import Path

from starter_agent.building_map import BuildingMap

from nav.format import format_reply
from nav.parse import ParseError, parse_question, parse_with_model
from nav.skills import run_skill


def answer(building_directory: Path, question_text: str) -> str:
    building = BuildingMap.load(building_directory)
    try:
        try:
            parsed = parse_question(building, question_text)
        except ParseError:
            parsed = parse_with_model(building, question_text)
        result = run_skill(building, parsed.kind, parsed.inputs)
    except Exception as error:
        return f"I could not measure that ({error})."
    return format_reply(building, parsed.kind, result)
