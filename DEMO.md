# Demo script

About 90 seconds, on the factory hall.

```bash
uv run python -m nav.web data/dev/proc-factory-dev-003
```

Open http://127.0.0.1:8765.

1. Search `coordinate measuring`. Set it as the destination. Set the start to `Main lobby` or the warehouse zone the search offers. Show the way. The red line walks through the doors. Say the length.
2. Turn on Stack floors. The route's other floors sit above the ground plan.
3. Close the office lift. Show the way again. The line changes, and the step list names the stair or the other lift.
4. Add a door between two neighbouring rooms, for example `Office 1.04` and `Office 1.05` if they share a wall. A blue dot marks the new opening. The page says where it was placed.
5. Press Sample question, then Send. The reply ends with one `Answer:` line naming the largest kitchen and its area. No key is required for that sentence.
6. If a voice key is set, click Voice, ask "where is the forklift charging station", and let the reply play back.

Stop the page with Ctrl-C.
