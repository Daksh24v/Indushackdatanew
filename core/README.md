# core — the deterministic brain

Everything the agent states about a building is computed here, never guessed. `core.building.Building`
extends the starter's `BuildingMap` (same route model, same costs) with every fact the benchmark questions
need. Proven against **517/517 train and 138/138 dev** benchmark answers and **229/229** warm-up routes.

```bash
uv run python -m core.oracle_check data/dev                  # all nine kinds, seconds, no API calls
uv run python -m core.oracle_check data/train --kinds errand  # one kind, with the first failures printed
uv run python -m core.oracle_check data/dev --warmup          # route model on the warm-up questions
uv run python -m core.check_questions data/test data/test/questions.csv   # do all names in a question file resolve?
uv run python -m core.validate_building path/to/building.json            # is a (generated) building well formed?
```

## Using it from the agent

```python
from pathlib import Path
from core import Building, MapError

b = Building.load(Path("data/dev/proc-factory-dev-003"))
b.total_area("ground floor", "office")          # {'total_m2': 412.5, 'count': 14, 'rooms': [...], ...}
b.largest_room(1, "meeting room")               # {'label': 'Meeting room 1.07', 'area_m2': 48.2, 'runner_up': {...}, ...}
b.reachable_rooms("Lobby 0.01", "floor 1", step_free=True, min_width_m=1.1)   # {'count': 6, 'rooms': [...]}
b.errand("Office 1.12 (Floor 1)", "ups unit", "ground floor", "meeting room") # {'total_m': 143.2, 'via': {...}, 'target': {...}}
```

Every method raises `MapError` with a helpful message (candidates, available floors or types) when a
reference is unknown or ambiguous. Tools should turn `MapError` into a tool error so the model can retry.

### References the methods accept
- **space**: a space id, an asset id, an exact name (`"Office 1.12"`), a room number (`"1.12"`), a name
  with its floor as written in questions (`"Kitchen 2.09 (apartment 6) (Floor 2)"`), or a unique part of a
  name. Optional `floor=` argument disambiguates rooms that share a name (OSM "WC", "Büro").
- **floor**: a level (`1`, `-1`), a floor id, or words (`"ground floor"`, `"Floor 2"`, `"Basement 1"`, `"first floor"`).
- **room_type**: as in the data (`"generic_room"`) or as people say it (`"generic room"`, `"offices"`, `"meeting room"`).
- **asset_type**: `"ups unit"`, `"office chair"`, `"3d printer"`, singular or plural.

### Methods

| Method | Returns | Benchmark kind |
|---|---|---|
| `rooms(floor, room_type=None)` | list of `{id, name, label, ref, type, room_type, floor, level, area_m2}` largest first | |
| `area_m2(space)` | float | |
| `largest_room(floor, room_type=None)` | `{space_id, name, label, ref, area_m2, floor, room_type, runner_up, candidates}` | `largest_room` |
| `total_area(floor, room_type=None)` | `{total_m2, count, floor, room_type, rooms}` | `floor_area_total` |
| `centroid_3d(space)` | `{space_id, name, floor, x, y, z}` | |
| `straight_line_3d(a, b)` | `{distance_m, horizontal_m, vertical_m, a, b, warning?}` | `straight_line_3d` |
| `shared_wall_rooms(space)` | `{count, room, floor, rooms: [{name, room_type, shared_m}]}` | `shared_walls_count` |
| `stacked_room(space, "above"\|"below")` | `{space_id, name, label, ref, floor, overlap_m2, overlap_share, runner_up}` | `room_stacked` |
| `reachable_rooms(start, floor, step_free=True, min_width_m=None, closed=())` | `{count, floor, start, rooms, closed}` | `wide_delivery_count`, `step_free_reachable_count` |
| `nearest_room_with_asset(start, asset_type, step_free=False)` | `{space_id, name, floor, distance_m, asset_id, asset_name}` | |
| `errand(start, asset_type, floor, room_type, step_free=False)` | `{total_m, legs_m, start, via, target, steps}` | `errand` |
| `distance_to_exit(space, step_free=False)` | float (metres to the nearest entrance) | |
| `what_if_door(a, b, floor, threshold_m=1.0)` | `{count, door, between, floor, rooms_improved}` | `what_if_door` |
| `route(start, dest, step_free=False, via=None)` | `RouteResult` (starter) | warm-up routes |
| `route_geometry(route=None)` | `{length_m, floor_order, floors: {"0": [[x, y], ...]}, start, destination}` for the UI | |
| `set_closed(ref, closed)`, `add_door(a, b)` | starter behaviour; graph caches are refreshed | |
| `room_types(floor=None)`, `asset_types(floor=None)`, `assets_of_type(type, floor=None)` | lists for lookups | |
| `search(query, floor_level=None)`, `overview()`, `describe_space(id)` | starter behaviour | |

### Answer lines the scorer accepts (use these exact shapes)
| Kind | Answer line |
|---|---|
| `floor_area_total` | `Answer: {total_m2:.2f}` |
| `largest_room` | `Answer: {label}, {area_m2:.1f} m²` (`label`, not `name`: OSM rooms share names) |
| `straight_line_3d` | `Answer: {distance_m:.2f}` |
| `wide_delivery_count`, `step_free_reachable_count`, `shared_walls_count`, `what_if_door` | `Answer: {count}` |
| `room_stacked` | `Answer: {label}` |
| `errand` | `Answer: {total_m:.2f}` |

Never write `m2` (the 2 counts as a second number), never a comma decimal, never two numbers.

## Definitions, as calibrated on the data
- **Area**: shapely polygon area of the space outline; room type must match exactly.
- **Room centre**: polygon centroid at the floor's `elevation_m`.
- **Reachable rooms**: walk from the start through openings that are `step_free` (when required) and at
  least `min_width_m` wide, and through step-free connectors (lifts); count reached spaces with
  `type == "room"` on the target floor. The start counts if it is a room. All entrances meet at `outside`.
- **Shared wall**: a neighbour counts when it is a room or a hall (not corridor, stairwell, lift or outdoor
  space such as a balcony) and at least 1 m of its outline runs within 0.35 m of ours.
- **Directly above/below**: the space on the next floor with the largest overlap area; any space type.
  Refused for Swiss Dwellings, whose floors do not share one coordinate frame.
- **Errand**: nearest room holding the item by route length (stairs allowed, the start room excluded),
  then the largest room of the type on the floor; total = both legs.
- **What-if door**: 0.9 m step-free door at the middle of the shared wall; count rooms (`type == room`) on
  the target floor whose route-model distance to `outside` drops by more than 1 m.

## Files
- `building.py` — `Building`
- `oracle_check.py` — proves every method against train/dev answers
- `check_questions.py` — resolves every name in a question file (run on the Sunday building first)
- `validate_building.py` — schema, connectivity and overlap checks for generated buildings

Running anything that imports `anthropic` is not needed here; the core has no API dependency.
