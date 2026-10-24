# Benchmark

One command reads a building and a question file and writes the answers. Nothing is filled in by hand between those two files.

## Sunday building

```bash
uv run python run_benchmark <building_dir> <questions_file> <answers_file>
```

Example:

```bash
uv run python run_benchmark data/dev/proc-factory-dev-003 data/dev/proc-factory-dev-003/questions.jsonl agent_output/answers.json
```

`<building_dir>` contains `building.json` in the same format as the training buildings.

`<questions_file>` is either:

- JSONL, one object per line, as in the kit (`id`, `question`, optional `kind`)
- a JSON list, or `{"questions": [...]}`, with `id` and `text` (or `question`)

Optional `constraints` on a JSON object: `start`, `destination`, `step_free`, `via`, `closed`, `min_width`. Those drive a route when the text is not one of the measured question templates.

## Output

`answers.json` is a list. Each object has:

| Field | Meaning |
|---|---|
| `id` | Question id |
| `room_id` | Room the question located, when the answer is a room |
| `asset_id` | Reserved for an asset lookup. Null for the measured question kinds |
| `node_ids` | Route nodes in order. Door nodes are opening ids from `building.json` |
| `length_m` | Walking or straight-line length in metres, when the question asks for one |
| `answer_line` | The committed `Answer:` line |

The same run can also write the kit's two-column CSV:

```bash
uv run python run_benchmark <building_dir> <questions_file> <answers_file> --submission-csv answers.csv
```

## Full test split

The 145 public test questions live in many building directories. The kit evaluator runs the same answer function and writes the submission CSV:

```bash
uv run python -m benchmark.evaluate data/test --agent nav.answer:answer --submission-csv submission.csv
```

That command does not call a model when the question matches a known template. A template miss makes one structured call that returns slots, not a number. The number still comes from the geometry code.

`submission.csv` in this repository is that file.
