# Benchmark: how to reproduce the submission

One command, no API key, no manual steps. This is the SiteTwin agent, the same core the app uses:

```bash
uv run python -m benchmark.evaluate data/test --agent agent.answer:answer --submission-csv submission/team_sitetwin.csv
```

It writes `submission/team_sitetwin.csv` (145 rows, `question_id,answer`).
The same command on `data/dev` or `data/train` (without `--submission-csv`) scores the replies with
`benchmark/score.py` and prints the result per question kind.

## What it does

`agent/answer.py` exports `answer(building_directory, question_text) -> str`. For each question it:

1. Parses the question with templates for the 9 benchmark kinds. Room names (with the floor in
   parentheses), floors ("ground floor", "floor 2", "basement 1"), room types and asset types are
   resolved against the building's own `building.json`.
2. Solves it deterministically with `core/` (the same code the SiteTwin app uses):
   - `largest_room`, `floor_area_total`: polygon areas of the spaces with that `room_type`.
   - `errand`: route distance to the nearest room holding the asset, then to the largest room of the type.
   - `wide_delivery_count`: rooms reachable with no stairs or steps, through doors at least as wide as the load.
   - `step_free_reachable_count`: same, step-free, with the named lift or stair closed.
   - `room_stacked`: the space on the next floor up or down with the largest overlap.
   - `shared_walls_count`: rooms on the same floor where at least 1 m of outline runs within 0.35 m. Corridors, stairwells, lifts and outdoor spaces do not count.
   - `straight_line_3d`: distance between polygon centroids at each floor's `elevation_m`.
   - `what_if_door`: a door at the middle of the shared wall, then rooms whose walk to the nearest entrance drops by more than 1 m.
   Routing uses the starter cost model: representative-point anchors, door nodes, 12 m per stair floor, 15 m plus 6 m per lift floor.
3. Replies with a short explanation and a final `Answer:` line.

If no template matches and `ANTHROPIC_API_KEY` is set, `claude-haiku-5-5` extracts the same parameters as JSON. The benchmark questions do not need that fallback.

## Results

| Split | Score |
|---|---|
| dev | 138 / 138 |
| train | 515 / 517 |

The two train misses name a room that exists more than once on the same floor, such as three rooms called "Büro". The sentence does not say which one. No dev or test question has that duplicate.

```bash
uv run pytest tests -q
uv run python -m starter_agent.evaluate data/dev --oracle
```

## Sunday building

For one new building and a question file, write `answers.json` as well as the kit CSV:

```bash
uv run python run_benchmark <building_dir> <questions_file> <answers_file>
```

Each answer has `id`, `room_id`, `asset_id`, `node_ids`, `length_m`, and `answer_line`. Door nodes in `node_ids` are opening ids from `building.json`. The question file may be JSONL or a JSON list. Optional `constraints` (`start`, `destination`, `step_free`, `via`, `closed`, `min_width`) drive a route when the text is not one of the measured templates.

`nav.answer:answer` is a second solver with the same measured results. The submission command above uses `agent.answer`, which is the one the app calls.
