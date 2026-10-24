"""Local floor-plan page for one building.

  uv run python -m nav.web data/dev/proc-factory-dev-003
"""

import argparse
import base64
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from starter_agent.building_map import DOOR_PREFIX, MAX_SEARCH_RESULTS, BuildingMap, MapError

from nav.format import format_reply
from nav.parse import ParseError, parse_question
from nav.skills import run_skill
from nav.voice import VoiceError, api_key, synthesize, transcribe

PAGE = Path(__file__).parent / "static" / "index.html"
PLAN_NAME = re.compile(r"floor_-?\d+(_plain)?\.(png|svg)")
OUTPUT = Path("agent_output") / "web"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class Guide:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.map = BuildingMap.load(directory)
        self.agent = None

    def route_view(self) -> dict | None:
        route = self.map.last_route
        if route is None:
            return None
        points = []
        for node in route.node_path:
            if node.startswith(DOOR_PREFIX):
                opening = self.map.openings.get(node[len(DOOR_PREFIX) :])
                if opening is None:
                    continue
                points.append(
                    {
                        "floor_id": opening["floor_id"],
                        "x": opening["position"][0],
                        "y": opening["position"][1],
                        "kind": "door",
                    }
                )
            elif node in self.map.spaces:
                x_coord, y_coord = self.map.anchor(node)
                space = self.map.spaces[node]
                points.append(
                    {
                        "floor_id": space["floor_id"],
                        "x": x_coord,
                        "y": y_coord,
                        "kind": "space",
                        "id": node,
                        "name": space["name"],
                    }
                )
        return {
            "length_m": route.length_m,
            "steps": route.steps,
            "step_free": route.step_free,
            "points": points,
        }

    def state(self) -> dict:
        info = self.map.data["building"]
        floors = sorted(self.map.floors.values(), key=lambda floor: floor["level"])
        return {
            "name": info["name"],
            "id": info["id"],
            "floors": [
                {
                    "id": floor["id"],
                    "level": floor["level"],
                    "name": floor["name"],
                    "elevation_m": floor.get("elevation_m", 0),
                    "plan": f"/plan/floor_{floor['level']}.png",
                }
                for floor in floors
            ],
            "spaces": [
                {
                    "id": space["id"],
                    "floor_id": space["floor_id"],
                    "name": space["name"],
                    "type": space["type"],
                    "room_type": space["room_type"],
                    "polygon": space["polygon"],
                    "closed": space["id"] in self.map.closed,
                }
                for space in self.map.spaces.values()
            ],
            "assets": [
                {
                    "id": asset["id"],
                    "name": asset["name"],
                    "type": asset["type"],
                    "floor_id": asset["floor_id"],
                    "space_id": asset["space_id"],
                    "position": asset["position"],
                }
                for asset in self.map.assets.values()
            ],
            "openings": [
                {
                    "id": opening["id"],
                    "floor_id": opening["floor_id"],
                    "position": opening["position"],
                    "kind": opening["kind"],
                    "width_m": opening.get("width_m"),
                    "synthetic": opening.get("synthetic", False),
                }
                for opening in self.map.openings.values()
            ],
            "connectors": [
                {
                    "id": connector["id"],
                    "name": connector["name"],
                    "kind": connector["kind"],
                    "closed": connector["id"] in self.map.closed,
                }
                for connector in self.map.connectors.values()
            ],
            "route": self.route_view(),
            "voice": api_key() is not None,
            "chat": bool(os.environ.get("ANTHROPIC_API_KEY")),
        }

    def search(self, query: str, floor_level: int | None) -> dict:
        results = self.map.search(query, floor_level)
        return {"results": results[:MAX_SEARCH_RESULTS], "total": len(results)}

    def route(self, start: str, destination: str, step_free: bool) -> dict:
        result = self.map.route(start, destination, step_free=step_free)
        return {"length_m": result.length_m, "steps": result.steps, "route": self.route_view()}

    def set_closed(self, element: str, closed: bool) -> dict:
        element_id = self.map.set_closed(element, closed)
        self.map.last_route = None
        return {"element_id": element_id, "closed": closed}

    def add_door(self, first: str, second: str) -> dict:
        opening = self.map.add_door(first, second)
        OUTPUT.mkdir(parents=True, exist_ok=True)
        self.map.save(OUTPUT / "building_edited.json")
        return {"added": opening}

    def chat(self, text: str) -> str:
        try:
            parsed = parse_question(self.map, text)
            result = run_skill(self.map, parsed.kind, parsed.inputs)
            return format_reply(self.map, parsed.kind, result)
        except ParseError:
            pass
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return (
                "Measured questions are answered here without a key. "
                "Open conversation needs ANTHROPIC_API_KEY. Search, route, close, and add-door already work."
            )
        if self.agent is None:
            from starter_agent.agent import BuildingAgent

            OUTPUT.mkdir(parents=True, exist_ok=True)
            self.agent = BuildingAgent(self.map, OUTPUT)
        return self.agent.ask(text)


def make_handler(guide: Guide):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            print(f"[web] {self.address_string()} {fmt % args}")

        def _json(self, payload: object, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length))

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                body = PAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/state":
                self._json(guide.state())
                return
            if path.startswith("/plan/"):
                name = unquote(path.removeprefix("/plan/"))
                file_path = guide.directory / name
                if PLAN_NAME.fullmatch(name) and file_path.is_file():
                    body = file_path.read_bytes()
                    kind = "image/svg+xml" if name.endswith(".svg") else "image/png"
                    self.send_response(200)
                    self.send_header("Content-Type", kind)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self._json({"error": "No plan image"}, 404)
                return
            self._json({"error": "Not found"}, 404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            try:
                body = self._read()
                if path == "/api/search":
                    floor = body.get("floor_level")
                    self._json(guide.search(body.get("query", ""), floor))
                elif path == "/api/route":
                    self._json(guide.route(body["start"], body["destination"], bool(body.get("step_free"))))
                elif path == "/api/closed":
                    self._json(guide.set_closed(body["element"], bool(body["closed"])))
                elif path == "/api/door":
                    self._json(guide.add_door(body["first"], body["second"]))
                elif path == "/api/chat":
                    self._json({"reply": guide.chat(body.get("text", "")), "route": guide.route_view()})
                elif path == "/api/transcribe":
                    audio = base64.b64decode(body["audio_base64"])
                    self._json({"text": transcribe(audio, body.get("mime", "audio/webm"))})
                elif path == "/api/speak":
                    audio = synthesize(body.get("text", ""))
                    self._json({"audio_base64": base64.b64encode(audio).decode()})
                else:
                    self._json({"error": "Not found"}, 404)
            except (MapError, ParseError, VoiceError, KeyError, ValueError) as error:
                self._json({"error": str(error)}, 400)
            except Exception as error:  # noqa: BLE001 — show the failure in the page instead of dropping the connection
                self._json({"error": str(error)}, 500)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(prog="nav.web")
    parser.add_argument("building", type=Path, help="building directory with building.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args()
    load_env_file(Path(".env"))
    guide = Guide(arguments.building)
    server = ThreadingHTTPServer((arguments.host, arguments.port), make_handler(guide))
    print(f"Guide for {guide.map.data['building']['name']} at http://{arguments.host}:{arguments.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
