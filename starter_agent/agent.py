"""Conversational building guide: Claude talks to the person, BuildingMap supplies every fact.

Run:  uv run python -m starter_agent data/dev/<building_id>
"""

import argparse
import json
from pathlib import Path

import anthropic

from starter_agent.building_map import BuildingMap
from starter_agent.tools import make_tools

MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000
MAX_TOOL_ITERATIONS = 20
EFFORT = "medium"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_OUTPUT_DIRECTORY = Path("agent_output")
EXIT_WORDS = {"exit", "quit", "bye"}

SYSTEM_PROMPT = """You are the indoor guide for one building, like Google Maps but inside: people ask where \
things are, how to get somewhere, and tell you about changes (closed lifts, new doors).

How to work:
- Every fact (rooms, things, routes, distances) must come from your tools. Never guess a room or a route.
- Search before routing. If a search gives several plausible matches and the person's words don't settle \
it, ask one short question listing the options. If one match is clearly meant, go ahead.
- Only ask about the start point when you need it to answer; once you know where someone is, remember it.
- Say in one line what you understood (from where, to where, any constraint such as step-free or a closure), \
then give the directions as a short numbered list using room names and floor names, and the total distance.
- Use step_free=true when the person mentions a wheelchair, pram, trolley, crutches or "no stairs".
- When told something is closed or added, apply it with the tools and say what changes for routes.

Building overview:
{overview}"""


class BuildingAgent:
    """Keeps the conversation history and runs Claude's tool loop for each user turn."""

    def __init__(
        self, building_map: BuildingMap, output_directory: Path, client: anthropic.Anthropic | None = None
    ) -> None:
        self.map = building_map
        self.client = client or anthropic.Anthropic()
        self.tools = make_tools(building_map, output_directory)
        self.system = [
            {
                "type": "text",
                "text": SYSTEM_PROMPT.format(
                    overview=json.dumps(building_map.overview(), ensure_ascii=False)
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ]
        self.messages: list[dict] = []
        self.usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

    def ask(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        runner = self.client.beta.messages.tool_runner(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            max_iterations=MAX_TOOL_ITERATIONS,
            system=self.system,
            tools=self.tools,
            messages=list(self.messages),
            output_config={"effort": EFFORT},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        final = None
        for message in runner:
            final = message
            self.track_usage(message)
            # The runner keeps its own history; mirror it so the next turn has the full conversation.
            self.messages.append({"role": "assistant", "content": message.content})
            tool_response = runner.generate_tool_call_response()
            if tool_response is not None:
                self.messages.append(tool_response)
        if final is None:
            return "(no response)"
        if final.stop_reason == "refusal":
            return "Sorry, I can't help with that request."
        return "\n".join(block.text for block in final.content if block.type == "text").strip()

    def track_usage(self, message: object) -> None:
        usage = message.usage
        self.usage["input_tokens"] += usage.input_tokens
        self.usage["output_tokens"] += usage.output_tokens
        self.usage["cache_read_input_tokens"] += usage.cache_read_input_tokens or 0
        self.usage["cache_creation_input_tokens"] += usage.cache_creation_input_tokens or 0


def chat(building_directory: Path, output_directory: Path) -> None:
    agent = BuildingAgent(BuildingMap.load(building_directory), output_directory)
    print(f"Guide for {agent.map.data['building']['name']}. Ask me anything (type 'exit' to stop).")
    while True:
        try:
            user_text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_text:
            continue
        if user_text.lower() in EXIT_WORDS:
            break
        try:
            print(f"\nguide> {agent.ask(user_text)}")
        except anthropic.AuthenticationError:
            print("No valid API credentials: set ANTHROPIC_API_KEY or run `ant auth login`.")
            return
        except anthropic.RateLimitError:
            print("Rate limited - wait a moment and try again.")
        except anthropic.APIConnectionError:
            print("Network error - check your connection.")
        except anthropic.APIStatusError as error:
            print(f"API error {error.status_code}: {error.message}")
    print(f"\nTokens used: {agent.usage}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="starter_agent", description="Chat with an indoor building guide.")
    parser.add_argument(
        "building", type=Path, help="building directory (with building.json) or a building.json file"
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_DIRECTORY, help="where route images and edits go"
    )
    arguments = parser.parse_args()
    chat(arguments.building, arguments.output)
