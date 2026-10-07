"""Claude helpers: reading score messages the parsers don't recognize, and !joke."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import anthropic

from .games import Result

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"
# USD per million tokens (input, output). Unknown models are priced high to stay under budget.
PRICES = {"claude-sonnet-5": (2.0, 10.0), "claude-haiku-4-5": (1.0, 5.0)}
UNKNOWN_PRICE = (5.0, 25.0)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES.get(model, UNKNOWN_PRICE)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000

# Only share-format signals (an emoji grid or a puzzle number) reach the AI, so conversation,
# links, and casual mentions of scores never leave the box.
_SCORE_HINTS = re.compile(r"[🟩🟨🟥🟦🟪🟧🟫⬛⬜🟢🟡🔴🟠🔵🟣⚫⚪]|#\s?\d+")
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


@dataclass(frozen=True)
class GameInfo:
    key: str
    name: str
    emoji: str
    higher_is_better: bool
    url: str = ""


def _clean_url(url: str) -> str:
    """Keep only an https link to the site, never anything the model invented beyond that."""
    m = re.match(r"^(https://[a-z0-9.-]+\.[a-z]{2,}(?:/[A-Za-z0-9._~/-]*)?)", url.strip())
    return m.group(1).rstrip("/")[:80] if m else ""


def looks_like_score(text: str) -> bool:
    return len(text) <= 1500 and bool(_SCORE_HINTS.search(text))


TOOL = {
    "name": "record_result",
    "description": "Record what a group-chat message says about a daily game result.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["is_game_result", "game_key", "game_name", "emoji", "puzzle_id",
                     "puzzle_label", "score", "score_display", "higher_is_better", "url"],
        "properties": {
            "is_game_result": {
                "type": "boolean",
                "description": "True only if this is a pasted share result from a daily puzzle game.",
            },
            "game_key": {
                "type": "string",
                "description": "Lowercase slug for the game, e.g. 'wordle'. Reuse a known key when it matches.",
            },
            "game_name": {"type": "string"},
            "emoji": {"type": "string", "description": "One emoji representing the game."},
            "puzzle_id": {
                "type": "string",
                "description": "The puzzle number printed in the message, digits only (e.g. '1234'). "
                               "If there is no number, the puzzle's date as YYYY-MM-DD.",
            },
            "puzzle_label": {"type": "string", "description": "Short label, e.g. '#1234' or 'October 5'."},
            "score": {
                "type": "integer",
                "description": "The single number to rank by. For guess-count games, guesses used; "
                               "if failed, max guesses + 1.",
            },
            "score_display": {"type": "string", "description": "How to show the score, e.g. '945' or '3/6' or 'X/6'."},
            "higher_is_better": {"type": "boolean"},
            "url": {
                "type": "string",
                "description": "The game's website from the message without query string, e.g. "
                               "'https://dialed.gg/color'. Empty string if there's no link.",
            },
        },
    },
}

SYSTEM = """You read messages from a group chat where friends paste their daily puzzle game results \
(Wordle, Framed, MapTap, Connections, Costcodle, etc.) and extract the result.

If the message is not a pasted game result (chatter, a question, a link to something else), \
set is_game_result to false and fill the other fields with empty strings, 0, and false.

Everyone in the group pastes the same game's share text, so derive puzzle_id only from the \
message text itself so the same puzzle gets the same id for everyone."""


JOKE_SYSTEM = """You write one short, clean dad joke for a group chat of friends who play daily puzzle \
games. Puns welcome; game or puzzle themes are a bonus but not required. Reply with only the joke: \
at most two lines, no preamble, no emoji explanation."""


UsageRecorder = Callable[[str, str, int, int], None]  # (model, purpose, input_tokens, output_tokens)


class Claude:
    def __init__(self, api_key: str, monthly_budget_usd: float = 15.0, model: str = MODEL,
                 record_usage: UsageRecorder | None = None):
        self.client = anthropic.AsyncAnthropic(api_key=api_key, timeout=30.0, max_retries=2)
        self.monthly_budget_usd = monthly_budget_usd
        self.model = model
        self.record_usage = record_usage

    async def _create(self, purpose: str, **params):
        try:
            response = await self.client.messages.create(**params)
        except anthropic.APIError as exc:
            log.warning("%s call failed: %s", purpose, exc)
            return None
        if self.record_usage:
            self.record_usage(params["model"], purpose, response.usage.input_tokens,
                              response.usage.output_tokens)
        return response

    async def parse_score(self, text: str, today: date, known: list[GameInfo]) -> tuple[Result, GameInfo] | None:
        known_lines = "\n".join(
            f"- key={g.key} name={g.name} ({'higher' if g.higher_is_better else 'lower'} is better)"
            for g in known
        )
        prompt = (
            f"Today is {today.isoformat()}.\n\nKnown games:\n{known_lines}\n\n"
            f"Message:\n<message>\n{text}\n</message>"
        )
        response = await self._create(
            "score",
            model=self.model,
            max_tokens=1024,
            # Thinking off so the call can require the tool; extraction doesn't need it.
            thinking={"type": "disabled"},
            system=SYSTEM,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": TOOL["name"]},
            messages=[{"role": "user", "content": prompt}],
        )
        if response is None:
            return None
        block = next((b for b in response.content if b.type == "tool_use"), None)
        if block is None:
            log.warning("score parse returned no tool call (stop_reason=%s)", response.stop_reason)
            return None
        data = block.input
        if not data.get("is_game_result"):
            return None

        key = str(data.get("game_key", "")).strip().lower()
        puzzle = str(data.get("puzzle_id", "")).strip().lstrip("#")
        score = data.get("score")
        if not _KEY.match(key) or not puzzle or not isinstance(score, int):
            log.warning("score parse failed validation: %s", data)
            return None

        game = GameInfo(
            key=key,
            name=str(data.get("game_name") or key).strip()[:40],
            emoji=str(data.get("emoji") or "🎮").strip()[:4],
            higher_is_better=bool(data.get("higher_is_better")),
            url=_clean_url(str(data.get("url") or "")),
        )
        result = Result(
            game=key,
            puzzle=puzzle,
            puzzle_label=str(data.get("puzzle_label") or puzzle).strip()[:40],
            score=score,
            display=str(data.get("score_display") or score).strip()[:20],
        )
        return result, game

    async def joke(self, recent: list[str]) -> str | None:
        avoid = "\n".join(f"- {j}" for j in recent) or "(none yet)"
        response = await self._create(
            "joke",
            model=self.model,
            max_tokens=1024,
            output_config={"effort": "low"},
            system=JOKE_SYSTEM,
            messages=[{"role": "user", "content": f"Recent jokes (don't repeat these):\n{avoid}\n\nOne new joke please."}],
        )
        if response is None or response.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text[:400] or None
