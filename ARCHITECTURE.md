# Architecture

The guide is a deterministic map with a thin language layer on top. Distances, counts, and room choices are computed in Python. A model is only asked to name slots when a question does not match a known template, and it is never asked for the number.

## Approaches we considered

**Let the model do the arithmetic.** Extend the starter tools and let Claude add areas and counts. This is how the starter is written, and it scores under 5% on the benchmark because the model invents the measurement. We kept the tool loop for open conversation, and we did not use it for the scored number.

**A template solver with no model at all.** The published questions come from a few stable sentences, so a parser can fill the same `inputs` object the dataset stores. This is the scoring path. It is brittle if a future question is worded differently, which is why a slot-extraction call exists as a fallback.

**What we shipped.** One geometry core. The benchmark front parses the template, or falls back to one structured call that returns phrases. The chat front and the floor-plan page call the same functions. A what-if door and a door added from the page are placed at the same point.

We did not split the interface into a separate hosted app. The Sunday command has to be one `uv run` with no manual step.

## Route graph

`starter_agent/building_map.py` is the official cost model, unchanged in its weights:

- A space node sits at the polygon's representative point.
- An opening node sits at the opening's position. The edge weight is the straight-line distance.
- Stairs cost 12 m per floor. A lift costs 15 m plus 6 m per floor. Both edges are kept when they join the same pair of spaces.
- Entrances meet at one `outside` node at zero cost.
- `step_free` drops every edge marked `step_free: false`.
- `min_width` drops every opening narrower than the load. Lifts stay.

`BuildingMap.copy()` deep-copies the plan so a what-if door does not leak into the next question.

## Skills

`nav/skills.py` is one function per scored question kind. `nav/oracle.py` calls those functions with the `inputs` already stored on dev and train questions, so the algorithms are checked before any sentence is parsed.

- Area is the polygon area. Largest-room and total-area questions filter on `room_type`, so a lobby that is stored as a hall still counts.
- A room you can walk into has `type == "room"`. That is the count used for deliveries, wheelchairs, and what-if exits.
- A shared wall is at least 1 m of one outline within 0.35 m of the other outline. Corridors, stairs, lifts, and outdoor spaces are excluded.
- The room above or below is the space on the next existing level whose polygon covers the greatest area of this room.
- The 3D link uses polygon centroids and each floor's `elevation_m`. That is a different point from the route anchor.
- An errand walks to the nearest room that contains the asset type, then to the largest room of the requested type. Nearness is route distance. The two legs are summed with the official router, which rounds once.
- A what-if door is inserted at the midpoint of the longest close stretch of the two outlines. Exit distance is the route to `outside`, before and after. A room counts when that distance drops by more than 1 m.

Dev is 138/138 and train is 517/517 on those inputs. The text parser then reproduces the same inputs. `benchmark.evaluate` on dev, which only sees the question sentence and grades the `Answer:` line, is also 138/138.

Seven train questions name an OSM room whose name is shared by several spaces and do not include a ref. The sentence alone does not say which one. Dev and the 145 test questions resolve to one space.

## Answer line

`nav/format.py` writes a single `Answer:` line. Counts are integers. Metres and square metres use two decimal places. The largest-room line is the room's name and its area. The scorer ignores every line except the last `Answer:` line, and a count or distance on that line must be one number.

## Page

`python -m nav.web <building_dir>` serves one building. The canvas draws the polygons, assets, an animated route, and a stacked view offset by floor. Search, routing, closing a lift, and adding a door call the map directly, so they work with no API key. A measured question typed into the chat box uses the same parser. Open conversation uses the starter agent when `ANTHROPIC_API_KEY` is set. Voice uses ElevenLabs when `ELEVENLABS_API_KEY` is set: speech to text, then the same chat, then speech back.

## What we left out

Several buildings at once, AR, several people editing together, and rebuilding a plan from a photograph. The Sunday building arrives as `building.json`.
