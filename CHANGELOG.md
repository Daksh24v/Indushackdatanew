# Changelog

## 1.1.0 (2026-10-24)

**The expected answers now follow the judges' route rules exactly.** The questions are unchanged.

| | 1.0.0 | 1.1.0 (graded) |
|---|---|---|
| Walking inside a space | Straight lines from each door to one centre point of the room, so a corridor you pass through was walked via its centre and could cut through walls | **Door to door, 15 cm off the walls, round corners, never through a wall**; a door lands 30 cm inside the space |
| Stairs | 12 m per floor | **2 m of walking per metre climbed** |
| Lift | 15 m + 6 m per floor, with the 15 m charged again at every floor passed | **15 m (waiting and doors) once per ride + 1 m per metre climbed** |
| Outside | All entrances joined at zero cost, so a route could leave by one entrance and come back in by another | **No way round the outside.** If two parts of a building only meet outside, there is no route between them |

Height is the difference between the floors' `elevation_m`. A 3.8 m floor now costs 7.6 m by stairs (12 m before). A lift ride from the ground floor to floor 3 with 3.8 m floors now costs 15 + 11.4 = 26.4 m (63 m before). Section 3 of the README ("The route model") describes the walking rule precisely enough to reproduce the answers to the centimetre.

What changed:
- **Expected answers only.** 1,314 answers in the train and dev `questions.jsonl` and `questions_basic.jsonl` were recomputed: routes, step-free routes, closures and tours in the warm-up set, and errands and what-if doors in the benchmark.
  - 112 warm-up routes and tours that only worked by walking outside now have the answer `reachable: false` ("there is no route"). A tour with an unreachable stop is `{"stops": [...], "reachable": false}`.
  - In route answers, `space_path` now lists every space the route passes through (including corridors crossed door to door), and `element_path` lists each opening, stair and lift used once.
- **Questions are unchanged.** Every question id, its wording and its `inputs` are exactly as in 1.0.0, in all three splits, so `test_questions.jsonl` / `.csv` are identical. The hidden test answers were recomputed the same way and are graded with the new rules.
- **Starter agent:** `starter_agent/building_map.py` implements the new rules: door-to-door walking (`walking_lengths`, `door_landing`), the vertical costs (`STAIRS_M_PER_RISE`, `LIFT_FIXED_M`, `LIFT_M_PER_RISE`), every pair of lift stops joined directly, and no outdoor link. Its directions now say "walk about N m through the door into …" for each door. `add_door` takes an optional `position`.
- **README:** section 3 ("The route model") describes the new rules.

What didn't change: buildings (`building.json`), equipment lists, plan images, the manifest and the test questions.

**What to do:** replace the `data/train/*/questions*.jsonl` and `data/dev/*/questions*.jsonl` files, or just unpack this kit again. Then bring your router in line with README section 3, or copy the routing code from the new `starter_agent/building_map.py`.

## 1.0.0 (2026-10-22)

First release.
