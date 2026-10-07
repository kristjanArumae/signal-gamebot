"""Core bot behavior: track scores, post leaderboards early or at the daily cutoff."""

from __future__ import annotations

import json
import logging
import random
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from .games import GAMES_BY_KEY, parse_any
from .leaderboard import format_board
from .llm import Claude, GameInfo, looks_like_score
from .query import LbArgError, format_standings, parse_lb_args, puzzle_days, standings
from .signal_client import rest_group_id
from .store import PuzzleRef, Store

log = logging.getLogger(__name__)

TRACKED = "✅"
TRACKED_BY_AI = "🤖"
REPEAT = "🔁"
UNKNOWN_COMMAND = "❓"
NOTHING_FOUND = "🤷"
AI_BUDGET_SPENT = "⏳"
# Someone who played a game in this window is expected to play today's puzzle too.
REGULARS_WINDOW = timedelta(days=7)
# At the cutoff, post any unposted puzzle that got a submission this recently...
DUE_WINDOW = timedelta(hours=36)
# ...as long as the puzzle itself is today's or yesterday's. Late posts of older puzzles are
# tracked but never trigger a leaderboard post.
CURRENT_PUZZLE_DAYS = 1

FOOTER = "\n\n💬 !help for commands"

HELP = """🎮 Scorebot
Paste your daily game results here. I react ✅ when a score is tracked (🤖 = read by the AI backup, 🔁 = already had yours, ⏳ = AI budget used up for now).
Each game's board posts once everyone who's played it this week has played today (🔓 no more spoilers), or at {cutoff} ET.

Commands
!lb — latest board for each game
!lb framed — just one game ({games})
!lb oct 5 — a specific day (also: yesterday, 10/5)
!lb week — this week, Monday on
!lb month — this month
!lb all — all time
Mix them: !lb maptap month
!games — the games and their links
!joke — a dad joke
!usage — how much AI the bot has used

❓ = I don't know that command. 🤷 = no scores for that.
Week/month/all-time rank by daily wins, then average score.

🛠️ Source & setup: https://github.com/kristjanArumae/signal-gamebot"""

FALLBACK_JOKES = [
    "I was going to tell a Wordle joke, but I only had five letters and none of them were green.",
    "Why did the map go to therapy? It had too many issues with its boundaries.",
    "I told my friend a movie-frame pun. Framed for a crime I didn't commit.",
    "What do you call a puzzle that tells jokes? A pun-dle.",
]

# Water for data-center cooling, very roughly: published estimates run about 10-25 mL per chatbot
# response of ~1k tokens. Use the middle and a standard 500 mL bottle.
ML_PER_TOKEN = 0.015
BOTTLE_ML = 500
MODEL_NAMES = {"claude-haiku-4-5": "Haiku 4.5", "claude-sonnet-5": "Sonnet 5"}
DAYS_PER_BUDGET_MONTH = 30  # daily cap = monthly budget / 30, so one busy day can't spend the month


EVERYONE_IN = "🔓 Everyone's played — spoilers are fair game!"
DM_REPLY_EVERY = timedelta(days=1)  # per person
DM_REPLIES_PER_HOUR = 20  # across everyone
DM_REPLY = "👋 I'm Scorebot. Add me to a group and paste your daily game results there. Send !help in the group for details."


class Messenger(Protocol):
    async def send(self, group_id: str, text: str) -> None: ...
    async def react(self, group_id: str, author: str, timestamp: int, emoji: str) -> None: ...


class GameBot:
    def __init__(self, store: Store, messenger: Messenger, tz: str = "America/New_York",
                 post_hour: int = 18, llm: Claude | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.store = store
        self.messenger = messenger
        self.tz = ZoneInfo(tz)
        self.post_hour = post_hour
        self.llm = llm
        self.clock = clock
        self._dm_replied: dict[str, datetime] = {}

    async def handle_envelope(self, envelope: dict) -> None:
        data = envelope.get("dataMessage") or {}
        text = (data.get("message") or "").strip()
        group = data.get("groupInfo")
        if text and not group:
            await self._reply_to_dm(envelope.get("sourceUuid") or envelope.get("sourceNumber"))
            return
        if not text:
            return
        group_id = rest_group_id(group["groupId"])
        if not self.store.is_allowed(group_id):
            return  # not an approved group: ignore entirely (approve with scripts/admin.sh allow)
        author = envelope.get("sourceUuid") or envelope.get("sourceNumber") or envelope["source"]
        timestamp = data.get("timestamp") or envelope["timestamp"]

        async def react(emoji: str) -> None:
            await self.messenger.react(group_id, author, timestamp, emoji)

        if text.startswith("!"):
            await self.command(group_id, text, react)
            return

        now = self.clock()
        today = now.astimezone(self.tz).date()
        result = parse_any(text, today)
        tracked = TRACKED
        if not result and looks_like_score(text):
            status = self._ai_status()
            if status == "over_budget":
                await react(AI_BUDGET_SPENT)
                return
            if status == "ok":
                result = await self._parse_with_ai(text, today)
                tracked = TRACKED_BY_AI
        if not result:
            return  # conversation, or not a result we can read: stay silent
        name = envelope.get("sourceName") or envelope.get("sourceNumber") or "Someone"
        added = self.store.add(group_id, result, author, name, text, now)
        log.info("%s %s %s: %s (%s)", name, result.game, result.puzzle, result.display,
                 "tracked" if added else "repeat")
        await react(tracked if added else REPEAT)

        if (added and self._is_current(group_id, result.game, result.puzzle)
                and self._everyone_in(group_id, result.game, result.puzzle, now)):
            await self.post_final(PuzzleRef(group_id, result.game, result.puzzle, result.puzzle_label),
                                  everyone_in=True)

    async def _reply_to_dm(self, sender: str | None) -> None:
        """Replying shares the bot's profile, which lets that person add it straight to a group
        instead of sending an invite it may not be able to accept. Rate-limited against spam."""
        now = self.clock()
        if not sender or now - self._dm_replied.get(sender, datetime.min.replace(tzinfo=timezone.utc)) < DM_REPLY_EVERY:
            return
        recent = [t for t in self._dm_replied.values() if now - t < timedelta(hours=1)]
        if len(recent) >= DM_REPLIES_PER_HOUR:
            log.warning("DM reply limit reached; not replying")
            return
        self._dm_replied[sender] = now
        await self.messenger.send(sender, DM_REPLY)

    def _today(self):
        return self.clock().astimezone(self.tz).date()

    def _puzzle_days(self, group_id: str) -> dict[tuple[str, str], date]:
        return puzzle_days(self.store.puzzles(group_id), self.tz)

    def _is_current(self, group_id: str, game: str, puzzle: str) -> bool:
        day = self._puzzle_days(group_id)[(game, puzzle)]
        return day >= self._today() - timedelta(days=CURRENT_PUZZLE_DAYS)

    def _ai_status(self) -> str:
        """'ok', 'off' (no API key), or 'over_budget' (daily or monthly AI spend cap reached)."""
        if not self.llm:
            return "off"
        day_start = datetime.combine(self._today(), time.min, self.tz)
        month_start = day_start.replace(day=1)
        budget = self.llm.monthly_budget_usd
        if (self.store.spend_since(day_start) >= budget / DAYS_PER_BUDGET_MONTH
                or self.store.spend_since(month_start) >= budget):
            log.warning("AI budget reached; skipping AI call")
            return "over_budget"
        return "ok"

    async def command(self, group_id: str, text: str, react: Callable[[str], Awaitable[None]]) -> None:
        """Exact commands only; anything else gets a ❓ reaction and no message."""
        name, _, args = text.partition(" ")
        name = name.lower()
        if name in ("!lb", "!leaderboard"):
            outcome = await self.leaderboard(group_id, args)
            if outcome == "bad_args":
                await react(UNKNOWN_COMMAND)
            elif outcome == "empty":
                await react(NOTHING_FOUND)
        elif name == "!help" and not args:
            await self.help(group_id)
        elif name == "!usage" and not args:
            await self.usage(group_id)
        elif name == "!games" and not args:
            await self.games(group_id)
        elif name == "!joke" and not args:
            await self.joke(group_id)
        else:
            await react(UNKNOWN_COMMAND)

    async def help(self, group_id: str) -> None:
        cutoff = datetime(2000, 1, 1, self.post_hour).strftime("%-I%p").lower()
        games = ", ".join(g.key for g in self._all_games())
        await self.messenger.send(group_id, HELP.format(cutoff=cutoff, games=games))

    async def usage(self, group_id: str) -> None:
        rows = self.store.usage_by_model()
        if not rows:
            await self._send(group_id, "🤖 No AI usage yet.")
            return
        start_of_day = datetime.combine(self._today(), time.min, self.tz)
        today_total = sum(i + o for _, _, i, o in self.store.usage_by_model(since=start_of_day))
        total = sum(i + o for _, _, i, o in rows)
        lines = ["🤖 AI usage, all time"]
        for model, calls, i, o in rows:
            lines.append(f"{MODEL_NAMES.get(model, model)}: {i + o:,} tokens · {calls} calls")
        lines.append(f"Total: {total:,} tokens ({today_total:,} today)")
        if self.llm:
            month_spend = self.store.spend_since(start_of_day.replace(day=1))
            lines.append(f"Spent ${month_spend:.2f} of the ${self.llm.monthly_budget_usd:.2f} monthly budget "
                         f"(${self.store.spend_since(start_of_day):.2f} today)")
        bottles = total * ML_PER_TOKEN / BOTTLE_ML
        lines.append(f"💧 ≈ {bottles:.2f} bottles of water for data-center cooling (a very rough guess)")
        await self._send(group_id, "\n".join(lines))

    async def joke(self, group_id: str) -> None:
        recent = json.loads(self.store.get_meta("recent_jokes") or "[]")
        joke = await self.llm.joke(recent) if self._ai_status() == "ok" else None
        if not joke:
            joke = random.choice([j for j in FALLBACK_JOKES if j not in recent] or FALLBACK_JOKES)
        self.store.set_meta("recent_jokes", json.dumps((recent + [joke])[-20:]))
        await self._send(group_id, f"😄 {joke}")

    async def _parse_with_ai(self, text: str, today):
        """Backup parser for results the deterministic parsers missed (new games, format changes)."""
        parsed = await self.llm.parse_score(text, today, self._all_games())
        if not parsed:
            return None
        result, game = parsed
        if result.game not in GAMES_BY_KEY:
            self.store.learn_game(game)
        return result

    def _all_games(self) -> list[GameInfo]:
        builtin = [GameInfo(g.key, g.name, g.emoji, g.higher_is_better, g.url) for g in GAMES_BY_KEY.values()]
        return builtin + [g for g in self.store.learned_games() if g.key not in GAMES_BY_KEY]

    def _game(self, key: str) -> GameInfo:
        return next(g for g in self._all_games() if g.key == key)

    def _everyone_in(self, group_id: str, game: str, puzzle: str, now: datetime) -> bool:
        if self.store.is_posted(group_id, game, puzzle):
            return False
        submitted = {e.player_id for e in self.store.entries(group_id, game, puzzle)}
        regulars = self.store.players_since(group_id, game, now - REGULARS_WINDOW, exclude_puzzle=puzzle)
        # With no history yet we can't know who's playing, so wait for the cutoff.
        return len(submitted) >= 2 and bool(regulars) and regulars <= submitted

    async def _send(self, group_id: str, text: str) -> None:
        await self.messenger.send(group_id, text + FOOTER)

    async def post_final(self, ref: PuzzleRef, everyone_in: bool = False) -> None:
        entries = self.store.entries(ref.group_id, ref.game, ref.puzzle)
        board = format_board(self._game(ref.game), ref.puzzle_label, entries)
        if everyone_in:
            board += f"\n\n{EVERYONE_IN}"
        await self._send(ref.group_id, board)
        self.store.mark_posted(ref.group_id, ref.game, ref.puzzle, self.clock())

    def _daily_board(self, group_id: str, game: str, puzzle: str, label: str) -> str:
        entries = self.store.entries(group_id, game, puzzle)
        open_board = not self.store.is_posted(group_id, game, puzzle)
        board = format_board(self._game(game), label, entries, so_far=open_board)
        if open_board and self._is_current(group_id, game, puzzle):
            regulars = self.store.players_since(group_id, game, self.clock() - REGULARS_WINDOW,
                                                exclude_puzzle=puzzle)
            missing = regulars - {e.player_id for e in entries}
            if missing:
                names = self.store.player_names(group_id)
                board += "\n⏳ Waiting on: " + ", ".join(sorted(names.get(p, "?") for p in missing))
        return board

    async def games(self, group_id: str) -> None:
        played = self.store.games_played(group_id)
        lines = ["🎮 Games"] + [
            f"{g.emoji} {g.name}" + (f" — {g.url}" if g.url else "")
            for g in sorted(self._all_games(), key=lambda g: g.name.lower())
            if g.key in GAMES_BY_KEY or g.key in played
        ]
        await self._send(group_id, "\n".join(lines))

    async def leaderboard(self, group_id: str, args: str) -> str:
        """Post the requested board. Returns 'ok', 'empty' (nothing to show), or 'bad_args'."""
        try:
            query = parse_lb_args(args, self._today(), self._all_games())
        except LbArgError:
            return "bad_args"

        wanted = {g.key for g in query.games}
        days = self._puzzle_days(group_id)
        puzzles = [(game, puzzle, label, days[(game, puzzle)])
                   for game, puzzle, label, _ in self.store.puzzles(group_id)
                   if not wanted or game in wanted]

        if query.kind == "latest":
            latest = {}
            for game, puzzle, label, day in sorted(puzzles, key=lambda p: p[3]):
                latest[game] = (puzzle, label)  # by puzzle date, so a late post of an old one doesn't win
            boards = [self._daily_board(group_id, g, p, label) for g, (p, label) in sorted(latest.items())]
        elif query.kind == "day":
            boards = [self._daily_board(group_id, g, p, label)
                      for g, p, label, day in sorted(puzzles) if day == query.start]
        else:
            by_game: dict[str, list] = defaultdict(list)
            for game, puzzle, _, day in puzzles:
                if query.start <= day <= query.end:
                    by_game[game].append(self.store.entries(group_id, game, puzzle))
            boards = [
                format_standings(self._game(g), query.label, standings(days, self._game(g).higher_is_better))
                for g, days in sorted(by_game.items())
            ]

        if not boards:
            return "empty"
        await self._send(group_id, "\n\n".join(boards))
        return "ok"

    async def tick(self) -> None:
        """Called periodically; posts every pending leaderboard once per day at the cutoff."""
        now = self.clock()
        local = now.astimezone(self.tz)
        today = local.date().isoformat()
        if local.hour < self.post_hour or self.store.get_meta("last_cutoff") == today:
            return
        for ref in self.store.unposted_since(now - DUE_WINDOW):
            if self._is_current(ref.group_id, ref.game, ref.puzzle):
                await self.post_final(ref)
        self.store.set_meta("last_cutoff", today)
