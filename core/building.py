"""Deterministic core of the building: geometry, graph and the facts the benchmark questions need.

Building extends the starter's BuildingMap (same route model, same costs, same answers for warm-up
questions) with areas, door-width reachability, shared walls, stacking, 3D distances, errands and
what-if doors. No printing, no network. Public methods return plain dicts and lists so that tools can
json.dumps them, and accept human references: a space id, an asset id, an exact name such as
"Office 1.12", a room number such as "1.12", a unique part of a name, or a name followed by its floor
such as "Office 1.12 (Floor 1)". Floors may be given as a level (1), a floor id, or words such as
"ground floor", "Floor 2" or "Basement 1"."""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Iterable

import networkx as nx
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import linemerge, nearest_points, unary_union
from shapely.validation import make_valid

from starter_agent.building_map import (
    DOOR_PREFIX,
    LIFT,
    LIFT_COST_PER_FLOOR_M,
    LIFT_WAIT_COST_M,
    OUTSIDE,
    STAIRS_COST_PER_FLOOR_M,
    BuildingMap,
    MapError,
    RouteResult,
)

ROOM = "room"
HALL = "hall"
# A "room" for counting purposes (reachability, what-if) is a space with type == "room".
# For shared walls, a neighbour counts when it is a room or a hall; corridors, stairwells, lifts and outdoor
# spaces (balconies) never count. Swiss stairwells have type "room" but room_type "stairwell", so both
# fields are checked. Calibrated: 143/143 train+dev questions.
ROOM_LIKE_TYPES = {"room", "hall"}
NOT_ROOM_ROOM_TYPES = {"corridor", "stairwell", "lift"}
WALL_TOLERANCE_M = 0.35
MIN_SHARED_WALL_M = 1.0
NEW_DOOR_WIDTH_M = 0.9
ADJACENT_DISTANCE_M = 0.6
MAX_CANDIDATES = 10
SWISS = "swiss_dwellings"

FLOOR_WORDS = {"ground floor": 0, "ground": 0, "erdgeschoss": 0, "eg": 0, "first floor": 1, "second floor": 2,
               "third floor": 3, "fourth floor": 4, "fifth floor": 5, "sixth floor": 6, "seventh floor": 7}
FLOOR_SUFFIX = re.compile(
    r"^(.*?)\s*\(\s*((?:ground floor|floor\s*-?\d+|basement\s*\d*|level\s*-?\d+))\s*\)\s*$", re.IGNORECASE
)
ROOM_TYPE_ALIASES = {
    "generic": "generic_room", "wc": "toilet", "restroom": "toilet", "bath": "bathroom", "elevator": "lift",
    "stairs": "stairwell", "staircase": "stairwell", "hallway": "corridor", "lobby_area": "lobby",
    "production": "production_zone", "warehouse": "warehouse_zone", "assembly": "assembly_zone",
    "logistics": "logistics_zone", "additive": "additive_zone", "quality": "quality_lab", "laboratory": "lab",
    "meeting": "meeting_room", "seminar": "seminar_room", "server": "server_room", "technical": "technical_room",
    "print": "print_room", "locker": "locker_room", "living": "living_room",
}


def parse_level(text: str) -> int | None:
    """'ground floor' -> 0, 'Floor 2' -> 2, 'Basement 1' -> -1, '-1' -> -1, 'first floor' -> 1."""
    low = re.sub(r"\s+", " ", str(text).strip().lower())
    low = re.sub(r"^(the|on the|on)\s+", "", low)
    if low in FLOOR_WORDS:
        return FLOOR_WORDS[low]
    match = re.fullmatch(r"basement\s*(\d*)", low)
    if match:
        return -int(match.group(1) or 1)
    match = re.fullmatch(r"(?:floor|level|storey|story|og|f)\s*(-?\d+)", low)
    if match:
        return int(match.group(1))
    match = re.fullmatch(r"(-?\d+)(?:st|nd|rd|th)?(?:\s*(?:floor|level))?", low)
    if match:
        return int(match.group(1))
    return None


def normalize_token(text: str) -> str:
    return re.sub(r"\s+", "_", re.sub(r"[-\s]+", " ", str(text).strip().lower()))


def valid_polygon(polygon: Polygon) -> Polygon | MultiPolygon:
    """A polygon safe for intersections and buffers (OSM outlines are sometimes self-intersecting)."""
    if polygon.is_valid:
        return polygon
    fixed = make_valid(polygon)
    pieces = [geom for geom in getattr(fixed, "geoms", [fixed]) if isinstance(geom, Polygon | MultiPolygon)]
    return unary_union(pieces) if pieces else polygon.buffer(0)


class Building(BuildingMap):
    """BuildingMap plus every fact the benchmark and the chat need."""

    def __init__(self, building: dict) -> None:
        super().__init__(building)
        self.source: str = building["building"].get("source", "")
        self.levels: dict[str, int] = {floor_id: floor["level"] for floor_id, floor in self.floors.items()}
        self.floor_by_level: dict[int, str] = {floor["level"]: floor_id for floor_id, floor in self.floors.items()}
        self.polygons: dict[str, Polygon] = {sid: Polygon(space["polygon"]) for sid, space in self.spaces.items()}
        self._valid: dict[str, Polygon | MultiPolygon] = {}
        self._anchors: dict[str, tuple[float, float]] = {
            sid: (point.x, point.y) for sid, point in ((sid, poly.representative_point()) for sid, poly in self.polygons.items())
        }
        self._graphs: dict[tuple, nx.MultiGraph] = {}
        self.spaces_by_floor: dict[str, list[dict]] = {}
        for space in self.spaces.values():
            self.spaces_by_floor.setdefault(space["floor_id"], []).append(space)
        self.assets_by_space: dict[str, list[dict]] = {}
        for asset in self.assets.values():
            self.assets_by_space.setdefault(asset["space_id"], []).append(asset)

    # ---------- geometry helpers ----------

    def anchor(self, space_id: str) -> tuple[float, float]:
        return self._anchors[space_id]

    def valid(self, space_id: str) -> Polygon | MultiPolygon:
        polygon = self._valid.get(space_id)
        if polygon is None:
            polygon = self._valid[space_id] = valid_polygon(self.polygons[space_id])
        return polygon

    def is_room_like(self, space: dict) -> bool:
        return space["type"] in ROOM_LIKE_TYPES and space["room_type"] not in NOT_ROOM_ROOM_TYPES

    def label(self, space_id: str) -> str:
        """Name plus room number when the number is not already part of the name (OSM rooms share names)."""
        space = self.spaces[space_id]
        ref = space.get("ref")
        if ref and ref.lower() not in space["name"].lower():
            return f"{space['name']} ({ref})"
        return space["name"]

    def room_info(self, space_id: str) -> dict:
        space = self.spaces[space_id]
        return {
            "id": space_id,
            "name": space["name"],
            "label": self.label(space_id),
            "ref": space.get("ref"),
            "type": space["type"],
            "room_type": space["room_type"],
            "floor": self.floor_name(space["floor_id"]),
            "level": self.levels[space["floor_id"]],
            "area_m2": round(self.polygons[space_id].area, 2),
        }

    # ---------- resolving references ----------

    def resolve_floor(self, floor: int | str) -> str:
        if isinstance(floor, bool):
            raise MapError("A floor must be a level number or a floor name.")
        if isinstance(floor, int | float):
            return self._floor_by_level(int(floor))
        text = str(floor).strip()
        if text in self.floors:
            return text
        for floor_id, data in self.floors.items():
            if data["name"].lower() == text.lower():
                return floor_id
        level = parse_level(text)
        if level is None:
            raise MapError(f"Unknown floor '{floor}'. Floors: {self.floor_list()}.")
        return self._floor_by_level(level)

    def _floor_by_level(self, level: int) -> str:
        floor_id = self.floor_by_level.get(level)
        if floor_id is None:
            raise MapError(f"There is no floor at level {level}. Floors: {self.floor_list()}.")
        return floor_id

    def floor_list(self) -> str:
        return ", ".join(
            f"{floor['name']} (level {floor['level']})" for floor in sorted(self.floors.values(), key=lambda f: f["level"])
        )

    def resolve_space(self, reference: str, floor: int | str | None = None) -> str:
        """Space id, asset id, exact name or ref, 'Name (Floor 1)', or a unique part of a name."""
        ref = str(reference).strip()
        if ref in self.spaces:
            return ref
        if ref in self.assets:
            return self.assets[ref]["space_id"]
        floor_id = self.resolve_floor(floor) if floor is not None else None
        match = FLOOR_SUFFIX.match(ref)
        if match:
            try:
                suffix_floor = self.resolve_floor(match.group(2))
            except MapError:
                suffix_floor = None
            if suffix_floor is not None:
                floor_id = floor_id or suffix_floor
                ref = match.group(1).strip()
        low = ref.lower()
        pool = [space for space in self.spaces.values() if floor_id is None or space["floor_id"] == floor_id]
        exact = [s["id"] for s in pool if s["name"].lower() == low or (s.get("ref") or "").lower() == low]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise MapError(f"'{reference}' matches several places: {self._list(exact)}. Say which floor.")
        asset_spaces = sorted(
            {a["space_id"] for a in self.assets.values() if a["name"].lower() == low and (floor_id is None or a["floor_id"] == floor_id)}
        )
        if len(asset_spaces) == 1:
            return asset_spaces[0]
        partial = [s["id"] for s in pool if low and low in s["name"].lower()]
        if len(partial) == 1:
            return partial[0]
        # an item ("cnc mill", "coffee machine", "CNC mill H-A1-A"): the room that holds it
        words = low.replace("_", " ").split()
        items = [a for a in self.assets.values() if (floor_id is None or a["floor_id"] == floor_id)
                 and all(w in f"{a['name']} {a['type'].replace('_', ' ')}".lower() for w in words)]
        item_spaces = sorted({a["space_id"] for a in items})
        if len(item_spaces) == 1:
            return item_spaces[0]
        if item_spaces:
            shown = "; ".join(f"{a['name']} in {self.spaces[a['space_id']]['name']} ({self.floor_name(a['floor_id'])})" for a in items[:MAX_CANDIDATES])
            raise MapError(f"There are {len(items)} items matching '{reference}' in {len(item_spaces)} rooms: {shown}. Say which one.")
        candidates = [item["id"] for item in self.search(ref) if item["kind"] == "space"][:MAX_CANDIDATES]
        if not candidates and not partial:
            raise MapError(f"No place matches '{reference}'. Try search_building with fewer words.")
        raise MapError(f"'{reference}' is ambiguous. Did you mean: {self._list(partial[:MAX_CANDIDATES] or candidates)}?")

    def _list(self, space_ids: Iterable[str]) -> str:
        return "; ".join(f"{self.spaces[sid]['name']} ({self.floor_name(self.spaces[sid]['floor_id'])}) [{sid}]" for sid in space_ids)

    def room_types(self, floor: int | str | None = None) -> list[str]:
        spaces = self.spaces_by_floor[self.resolve_floor(floor)] if floor is not None else self.spaces.values()
        return sorted({space["room_type"] for space in spaces})

    def normalize_room_type(self, room_type: str) -> str:
        available = set(self.room_types())
        token = normalize_token(room_type)
        for candidate in (token, ROOM_TYPE_ALIASES.get(token), token[:-1] if token.endswith("s") else None,
                          token[:-2] if token.endswith("es") else None, token + "_room", token + "_zone",
                          ROOM_TYPE_ALIASES.get(token[:-1] if token.endswith("s") else token)):
            if candidate and candidate in available:
                return candidate
        raise MapError(f"Unknown room type '{room_type}'. Room types in this building: {sorted(available)}.")

    def asset_types(self, floor: int | str | None = None) -> list[str]:
        floor_id = self.resolve_floor(floor) if floor is not None else None
        return sorted({a["type"] for a in self.assets.values() if floor_id is None or a["floor_id"] == floor_id})

    def normalize_asset_type(self, asset_type: str) -> str:
        available = set(self.asset_types())
        token = normalize_token(asset_type)
        for candidate in (token, token[:-1] if token.endswith("s") else None, token[:-2] if token.endswith("es") else None):
            if candidate and candidate in available:
                return candidate
        partial = sorted(t for t in available if token in t or t in token)
        if len(partial) == 1:
            return partial[0]
        raise MapError(f"Unknown item type '{asset_type}'. Item types here: {sorted(available)[:60]}.")

    # ---------- rooms and areas ----------

    def rooms(self, floor: int | str, room_type: str | None = None) -> list[dict]:
        """Rooms on a floor, largest first. With room_type: exact match; without: every room or hall."""
        floor_id = self.resolve_floor(floor)
        wanted = self.normalize_room_type(room_type) if room_type else None
        spaces = [
            space for space in self.spaces_by_floor.get(floor_id, [])
            if (space["room_type"] == wanted if wanted else space["type"] in (ROOM, HALL))
        ]
        return sorted((self.room_info(space["id"]) for space in spaces), key=lambda item: -item["area_m2"])

    def area_m2(self, space: str) -> float:
        return round(self.polygons[self.resolve_space(space)].area, 2)

    def largest_room(self, floor: int | str, room_type: str | None = None) -> dict:
        rooms = self.rooms(floor, room_type)
        if not rooms:
            raise MapError(
                f"No {room_type or 'room'} on {self.floor_name(self.resolve_floor(floor))}. "
                f"Room types there: {self.room_types(floor)}."
            )
        best = rooms[0]
        return {
            "space_id": best["id"],
            "name": best["name"],
            "label": best["label"],
            "ref": best["ref"],
            "area_m2": best["area_m2"],
            "floor": best["floor"],
            "room_type": best["room_type"],
            "runner_up": {"name": rooms[1]["name"], "area_m2": rooms[1]["area_m2"]} if len(rooms) > 1 else None,
            "candidates": len(rooms),
        }

    def total_area(self, floor: int | str, room_type: str | None = None) -> dict:
        rooms = self.rooms(floor, room_type)
        if not rooms:
            raise MapError(
                f"No {room_type or 'room'} on {self.floor_name(self.resolve_floor(floor))}. "
                f"Room types there: {self.room_types(floor)}."
            )
        total = sum(self.polygons[item["id"]].area for item in rooms)
        return {
            "total_m2": round(total, 2),
            "count": len(rooms),
            "floor": rooms[0]["floor"],
            "room_type": rooms[0]["room_type"] if room_type else "room or hall",
            "rooms": [{"name": item["name"], "area_m2": item["area_m2"]} for item in rooms],
        }

    # ---------- 3D ----------

    def centroid_3d(self, space: str) -> dict:
        space_id = self.resolve_space(space)
        data = self.spaces[space_id]
        centroid = self.polygons[space_id].centroid
        return {
            "space_id": space_id,
            "name": data["name"],
            "floor": self.floor_name(data["floor_id"]),
            "x": round(centroid.x, 3),
            "y": round(centroid.y, 3),
            "z": self.floors[data["floor_id"]]["elevation_m"],
        }

    def straight_line_3d(self, first: str, second: str) -> dict:
        a, b = self.centroid_3d(first), self.centroid_3d(second)
        horizontal = math.dist((a["x"], a["y"]), (b["x"], b["y"]))
        vertical = abs(a["z"] - b["z"])
        result = {"distance_m": round(math.hypot(horizontal, vertical), 2), "horizontal_m": round(horizontal, 2),
                  "vertical_m": round(vertical, 2), "a": a, "b": b}
        if self.source == SWISS and a["floor"] != b["floor"]:
            result["warning"] = "Swiss Dwellings floors may not share one coordinate frame; cross-floor distances are unreliable."
        return result

    # ---------- shared walls and stacking ----------

    def shared_wall_length(self, first_id: str, second_id: str, tol_m: float = WALL_TOLERANCE_M) -> float:
        """Metres of the second room's outline that run within tol_m of the first room's outline."""
        band = self.valid(first_id).boundary.buffer(tol_m)
        return float(self.valid(second_id).boundary.intersection(band).length)

    def shared_wall_rooms(self, space: str, min_len_m: float = MIN_SHARED_WALL_M, tol_m: float = WALL_TOLERANCE_M) -> dict:
        """Other rooms on the same floor sharing at least min_len_m of wall; corridors, stairwells, lifts excluded."""
        space_id = self.resolve_space(space)
        data = self.spaces[space_id]
        neighbours = []
        for other in self.spaces_by_floor[data["floor_id"]]:
            if other["id"] == space_id or not self.is_room_like(other):
                continue
            if self.valid(space_id).distance(self.valid(other["id"])) > tol_m:
                continue
            shared = self.shared_wall_length(space_id, other["id"], tol_m)
            if shared >= min_len_m:
                neighbours.append({"space_id": other["id"], "name": other["name"], "room_type": other["room_type"],
                                   "shared_m": round(shared, 2)})
        neighbours.sort(key=lambda item: -item["shared_m"])
        return {"count": len(neighbours), "room": data["name"], "floor": self.floor_name(data["floor_id"]),
                "rooms": neighbours}

    def stacked_room(self, space: str, direction: str) -> dict:
        """The space directly above or below: the one on the next floor whose outline overlaps ours most."""
        space_id = self.resolve_space(space)
        data = self.spaces[space_id]
        step = 1 if str(direction).lower() in ("above", "up", "over") else -1
        level = self.levels[data["floor_id"]]
        target_floor = self.floor_by_level.get(level + step)
        if target_floor is None:
            raise MapError(f"There is no floor {'above' if step > 0 else 'below'} {self.floor_name(data['floor_id'])}.")
        if self.source == SWISS:
            raise MapError("Swiss Dwellings floors are not drawn in one shared coordinate frame, so stacking cannot be read from the plans.")
        polygon = self.valid(space_id)
        overlaps = []
        for other in self.spaces_by_floor.get(target_floor, []):
            area = polygon.intersection(self.valid(other["id"])).area
            if area > 0:
                overlaps.append((area, other))
        if not overlaps:
            raise MapError(f"Nothing lies directly {'above' if step > 0 else 'below'} {data['name']}.")
        overlaps.sort(key=lambda item: -item[0])
        best_area, best = overlaps[0]
        result = {"space_id": best["id"], "name": best["name"], "label": self.label(best["id"]), "ref": best.get("ref"), "room_type": best["room_type"],
                  "floor": self.floor_name(target_floor), "overlap_m2": round(best_area, 2),
                  "overlap_share": round(best_area / polygon.area, 2), "runner_up": None}
        if len(overlaps) > 1:
            result["runner_up"] = {"name": overlaps[1][1]["name"], "overlap_m2": round(overlaps[1][0], 2)}
        return result

    # ---------- graph ----------

    def graph(self, step_free: bool) -> nx.MultiGraph:
        return self.cached_graph(step_free, None, frozenset(self.closed))

    def cached_graph(self, step_free: bool, min_width: float | None, closed: frozenset[str]) -> nx.MultiGraph:
        key = (step_free, min_width, closed)
        graph = self._graphs.get(key)
        if graph is None:
            graph = self._graphs[key] = self.build_graph(step_free, min_width, closed)
        return graph

    def build_graph(self, step_free: bool, min_width: float | None, closed: frozenset[str],
                    extra_openings: Iterable[dict] = ()) -> nx.MultiGraph:
        """The dataset's route model, with optional door-width filter and extra (hypothetical) openings."""
        graph = nx.MultiGraph()
        anchors = {sid: self._anchors[sid] for sid in self.spaces if sid not in closed}
        graph.add_nodes_from(anchors)
        for opening in itertools.chain(self.openings.values(), extra_openings):
            if opening["id"] in closed or (step_free and not opening["step_free"]):
                continue
            if min_width is not None and opening["width_m"] < min_width - 1e-9:
                continue
            door = DOOR_PREFIX + opening["id"]
            for space_id in opening["space_ids"]:
                if space_id == OUTSIDE:
                    graph.add_edge(OUTSIDE, door, weight=0.0, via=opening["id"])
                elif space_id in anchors:
                    graph.add_edge(space_id, door, weight=math.dist(anchors[space_id], opening["position"]), via=opening["id"])
        for connector in self.connectors.values():
            if connector["id"] in closed or (step_free and not connector["step_free"]):
                continue
            stops = sorted(zip(connector["floor_ids"], connector["space_ids"], strict=True), key=lambda stop: self.levels[stop[0]])
            for (low_floor, low_space), (high_floor, high_space) in itertools.pairwise(stops):
                if low_space not in anchors or high_space not in anchors:
                    continue
                span = abs(self.levels[high_floor] - self.levels[low_floor])
                cost = LIFT_WAIT_COST_M + LIFT_COST_PER_FLOOR_M * span if connector["kind"] == LIFT else STAIRS_COST_PER_FLOOR_M * span
                graph.add_edge(low_space, high_space, weight=cost, via=connector["id"])
        return graph

    def closed_set(self, closed: Iterable[str] = ()) -> frozenset[str]:
        return frozenset(self.closed | {self.find_element(item) for item in closed})

    def add_door(self, first: str, second: str, step_free: bool = True) -> dict:
        opening = super().add_door(first, second, step_free)
        self._graphs.clear()
        return opening

    def set_closed(self, reference: str, closed: bool) -> str:
        return super().set_closed(reference, closed)

    # ---------- reachability ----------

    def reachable_rooms(self, start: str, floor: int | str, step_free: bool = True, min_width_m: float | None = None,
                        closed: Iterable[str] = ()) -> dict:
        """Rooms (type == room) on a floor reachable from start under the constraints."""
        start_id = self.resolve_space(start)
        floor_id = self.resolve_floor(floor)
        closed_ids = self.closed_set(closed)
        graph = self.cached_graph(step_free, min_width_m, closed_ids)
        reached = nx.node_connected_component(graph, start_id) if start_id in graph else set()
        rooms = sorted(
            (self.spaces[sid]["name"] for sid in reached if sid in self.spaces
             and self.spaces[sid]["floor_id"] == floor_id and self.spaces[sid]["type"] == ROOM)
        )
        return {"count": len(rooms), "floor": self.floor_name(floor_id), "start": self.spaces[start_id]["name"],
                "step_free": step_free, "min_width_m": min_width_m, "closed": sorted(closed_ids), "rooms": rooms}

    def distance_to_exit(self, space: str, step_free: bool = False) -> float:
        """Route-model distance from a space to the nearest entrance (all entrances meet at 'outside')."""
        space_id = self.resolve_space(space)
        try:
            return round(nx.dijkstra_path_length(self.graph(step_free), space_id, OUTSIDE, weight="weight"), 2)
        except (nx.NetworkXNoPath, nx.NodeNotFound) as error:
            raise MapError(f"No {'step-free ' if step_free else ''}way out of the building from {self.describe_space(space_id)}.") from error

    # ---------- errands ----------

    def assets_of_type(self, asset_type: str, floor: int | str | None = None) -> list[dict]:
        wanted = self.normalize_asset_type(asset_type)
        floor_id = self.resolve_floor(floor) if floor is not None else None
        return [
            {"asset_id": a["id"], "name": a["name"], "type": a["type"], "space_id": a["space_id"],
             "room": self.spaces[a["space_id"]]["name"], "floor": self.floor_name(a["floor_id"])}
            for a in self.assets.values() if a["type"] == wanted and (floor_id is None or a["floor_id"] == floor_id)
        ]

    def nearest_room_with_asset(self, start: str, asset_type: str, step_free: bool = False, include_start: bool = False) -> dict:
        """The room holding an item of this type that is closest to start by walking distance."""
        start_id = self.resolve_space(start)
        wanted = self.normalize_asset_type(asset_type)
        candidates = {a["space_id"] for a in self.assets.values() if a["type"] == wanted}
        if not include_start:
            candidates.discard(start_id)
        if not candidates:
            raise MapError(f"No {asset_type} anywhere else in the building.")
        distances = nx.single_source_dijkstra_path_length(self.graph(step_free), start_id, weight="weight")
        reachable = [(distances[sid], sid) for sid in candidates if sid in distances]
        if not reachable:
            raise MapError(f"No {'step-free ' if step_free else ''}route from {self.describe_space(start_id)} to any room with a {asset_type}.")
        distance, space_id = min(reachable)
        asset = next(a for a in self.assets_by_space[space_id] if a["type"] == wanted)
        return {"space_id": space_id, "name": self.spaces[space_id]["name"], "floor": self.floor_name(self.spaces[space_id]["floor_id"]),
                "distance_m": round(distance, 2), "asset_id": asset["id"], "asset_name": asset["name"], "candidates": len(candidates)}

    def errand(self, start: str, asset_type: str, floor: int | str, room_type: str | None = None, step_free: bool = False) -> dict:
        """Walk from start to the nearest room with the item, then to the largest room of a type on a floor."""
        start_id = self.resolve_space(start)
        via = self.nearest_room_with_asset(start_id, asset_type, step_free)
        target = self.largest_room(floor, room_type)
        first = self.route(start_id, via["space_id"], step_free)
        second = self.route(via["space_id"], target["space_id"], step_free)
        return {"total_m": round(first.length_m + second.length_m, 2), "legs_m": [first.length_m, second.length_m],
                "start": self.spaces[start_id]["name"], "via": via, "target": target,
                "steps": first.steps + second.steps[1:], "step_free": step_free}

    # ---------- what-if ----------

    def shared_wall_point(self, first_id: str, second_id: str, tol_m: float = WALL_TOLERANCE_M) -> tuple[float, float]:
        """The middle of the wall two spaces share."""
        first, second = self.valid(first_id), self.valid(second_id)
        shared = first.boundary.intersection(second.boundary.buffer(tol_m))
        if isinstance(shared, MultiLineString):
            shared = linemerge(shared)
        if isinstance(shared, MultiLineString):
            shared = max(shared.geoms, key=lambda geom: geom.length)
        if isinstance(shared, LineString) and shared.length > 0:
            point = shared.interpolate(0.5, normalized=True)
        else:
            if first.distance(second) > ADJACENT_DISTANCE_M:
                raise MapError(f"{self.spaces[first_id]['name']} and {self.spaces[second_id]['name']} do not share a wall.")
            point_a, point_b = nearest_points(first, second)
            point = Point((point_a.x + point_b.x) / 2, (point_a.y + point_b.y) / 2)
        return (round(point.x, 3), round(point.y, 3))

    def what_if_door(self, first: str, second: str, floor: int | str, threshold_m: float = 1.0, step_free: bool = False) -> dict:
        """Add a door in the middle of the shared wall; count rooms on a floor whose way out gets shorter by > threshold."""
        first_id, second_id = self.resolve_space(first), self.resolve_space(second)
        if self.spaces[first_id]["floor_id"] != self.spaces[second_id]["floor_id"]:
            raise MapError("A door can only join two spaces on the same floor.")
        floor_id = self.resolve_floor(floor)
        opening = {"id": f"{self.spaces[first_id]['floor_id']}-O-whatif", "floor_id": self.spaces[first_id]["floor_id"],
                   "space_ids": [first_id, second_id], "kind": "door", "position": list(self.shared_wall_point(first_id, second_id)),
                   "width_m": NEW_DOOR_WIDTH_M, "step_free": True, "synthetic": True}
        closed = frozenset(self.closed)
        before = nx.single_source_dijkstra_path_length(self.cached_graph(step_free, None, closed), OUTSIDE, weight="weight")
        after = nx.single_source_dijkstra_path_length(self.build_graph(step_free, None, closed, [opening]), OUTSIDE, weight="weight")
        improved = []
        for space in self.spaces_by_floor.get(floor_id, []):
            if space["type"] != ROOM:
                continue
            old, new = before.get(space["id"], math.inf), after.get(space["id"], math.inf)
            if old - new > threshold_m:
                improved.append({"name": space["name"], "before_m": round(old, 2), "after_m": round(new, 2), "saved_m": round(old - new, 2)})
        improved.sort(key=lambda item: -item["saved_m"])
        return {"count": len(improved), "door": opening, "between": [self.spaces[first_id]["name"], self.spaces[second_id]["name"]],
                "floor": self.floor_name(floor_id), "threshold_m": threshold_m, "rooms_improved": improved}

    # ---------- for the UI ----------

    def route_geometry(self, route: RouteResult | None = None) -> dict:
        """Per floor level, the polyline of the route: room anchor, door, room anchor, ... in metres."""
        route = route or self.last_route
        if route is None:
            raise MapError("No route yet. Call route first.")
        floors: dict[str, list[list[float]]] = {}
        order: list[str] = []

        def add(level: str, point: tuple[float, float]) -> None:
            points = floors.setdefault(level, [])
            if level not in order:
                order.append(level)
            rounded = [round(point[0], 3), round(point[1], 3)]
            if not points or points[-1] != rounded:
                points.append(rounded)

        indoor = [sid for sid in route.space_path if sid in self.spaces]
        for previous, current in itertools.pairwise(route.space_path):
            if previous not in self.spaces or current not in self.spaces:
                continue
            level_prev = str(self.levels[self.spaces[previous]["floor_id"]])
            level_cur = str(self.levels[self.spaces[current]["floor_id"]])
            add(level_prev, self._anchors[previous])
            if level_prev == level_cur:
                doors = [o for o in self.openings.values() if set(o["space_ids"]) == {previous, current}]
                if doors:
                    door = min(doors, key=lambda o: math.dist(self._anchors[previous], o["position"]) + math.dist(o["position"], self._anchors[current]))
                    add(level_prev, tuple(door["position"]))
            add(level_cur, self._anchors[current])
        if indoor:
            add(str(self.levels[self.spaces[indoor[-1]]["floor_id"]]), self._anchors[indoor[-1]])
        return {"length_m": route.length_m, "floor_order": order, "floors": floors,
                "start": self.spaces[route.start_space_id]["name"], "destination": self.spaces[route.destination_space_id]["name"]}
