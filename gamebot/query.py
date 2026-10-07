"""!lb command: argument parsing plus multi-day (week / month / all-time) standings."""

from __future__ import annotations

import calendar
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo

from .leaderboard import MEDALS, rank
from .llm import GameInfo
from .store import Entry

_MONTHS = {
    name.lower(): i
    for i in range(1, 13)
    for name in (calendar.month_name[i], calendar.month_abbr[i])
}
_MONTHS["sept"] = 9


@dataclass(frozen=True)
class LbQuery:
    games: list[GameInfo]  # empty = every game
    kind: str  # "latest" | "day" | "range"
    start: date | None = None
    end: date | None = None  # inclusive
    label: str = ""


class LbArgError(ValueError):
    pass


def _parse_date(text: str, today: date) -> date | None:
    text = text.strip().lower().rstrip(".,")
    if text == "today":
        return today
    if text == "yesterday":
        return today - timedelta(days=1)
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text):
        return date(int(m[1]), int(m[2]), int(m[3]))
    month = day = None
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})", text):
        month, day = int(m[1]), int(m[2])
    elif m := re.fullmatch(r"([a-z]+)\.?\s+(\d{1,2})", text):
        month, day = _MONTHS.get(m[1]), int(m[2])
    if not month:
        return None
    # No year given: the most recent such date that isn't in the future.
    for year in (today.year, today.year - 1):
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        if d <= today:
            return d
    return None


def parse_lb_args(args: str, today: date, games: list[GameInfo]) -> LbQuery:
    words = args.lower().split()
    picked: list[GameInfo] = []
    rest: list[str] = []
    for word in words:
        game = next((g for g in games if word in (g.key, g.name.lower())), None)
        if game:
            picked.append(game)
        else:
            rest.append(word)
    when = " ".join(rest)

    if not when:
        return LbQuery(picked, "latest")
    if when in ("week", "weekly"):
        start = today - timedelta(days=today.weekday())
        return LbQuery(picked, "range", start, today,
                       f"This week ({start:%b} {start.day}–{today:%b} {today.day})")
    if when in ("month", "monthly"):
        return LbQuery(picked, "range", today.replace(day=1), today, f"{today:%B %Y}")
    if when == "last week":
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
        return LbQuery(picked, "range", start, end, f"Last week ({start:%b} {start.day}–{end:%b} {end.day})")
    if when == "last month":
        end = today.replace(day=1) - timedelta(days=1)
        return LbQuery(picked, "range", end.replace(day=1), end, f"{end:%B %Y}")
    if when in ("all", "lifetime", "alltime", "all-time", "all time"):
        return LbQuery(picked, "range", date.min, today, "All time")
    day = _parse_date(when, today)
    if day:
        return LbQuery(picked, "day", day, day, f"{day:%B} {day.day}")
    raise LbArgError(when)


def puzzle_days(puzzles: list[tuple[str, str, str, datetime]], tz: tzinfo) -> dict[tuple[str, str], date]:
    """The calendar day each puzzle belongs to, so late posts land on the right day.

    Dated puzzles (MapTap's "2026-10-05") use their own date. Numbered puzzles go up by one a day,
    so each game's number→day offset is the most common (first post day − number) in the group's
    history; a few late posts don't move it. Ties go to the smaller offset, since posting late is
    far more common than early. Anything else falls back to the day it was first posted.
    """
    offsets: dict[str, Counter] = defaultdict(Counter)
    for game, puzzle, _, first in puzzles:
        if puzzle.isdigit():
            offsets[game][first.astimezone(tz).date().toordinal() - int(puzzle)] += 1

    days = {}
    for game, puzzle, _, first in puzzles:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", puzzle):
            days[(game, puzzle)] = date.fromisoformat(puzzle)
        elif puzzle.isdigit():
            offset = min(offsets[game].items(), key=lambda kv: (-kv[1], kv[0]))[0]
            days[(game, puzzle)] = date.fromordinal(int(puzzle) + offset)
        else:
            days[(game, puzzle)] = first.astimezone(tz).date()
    return days


@dataclass(frozen=True)
class Standing:
    player_name: str
    wins: int
    played: int
    average: float


def standings(puzzles: list[list[Entry]], higher_is_better: bool) -> list[tuple[int, Standing]]:
    """Rank players across many puzzles: most daily wins first, then best average score."""
    wins: dict[str, int] = defaultdict(int)
    scores: dict[str, list[int]] = defaultdict(list)
    names: dict[str, str] = {}
    for entries in puzzles:
        for place, entry in rank(entries, higher_is_better):
            wins[entry.player_id] += place == 1
            scores[entry.player_id].append(entry.score)
            names[entry.player_id] = entry.player_name

    rows = [Standing(names[p], wins[p], len(s), sum(s) / len(s)) for p, s in scores.items()]
    sign = -1 if higher_is_better else 1
    rows.sort(key=lambda r: (-r.wins, sign * r.average))
    ranked: list[tuple[int, Standing]] = []
    for i, row in enumerate(rows):
        prev = rows[i - 1] if i else None
        tied = prev and (prev.wins, prev.average) == (row.wins, row.average)
        ranked.append((ranked[-1][0] if tied else i + 1, row))
    return ranked


def format_standings(game: GameInfo, label: str, ranked: list[tuple[int, Standing]]) -> str:
    lines = [f"{game.emoji} {game.name} — {label}"]
    for place, row in ranked:
        marker = MEDALS.get(place, f"{place}.")
        avg = f"{row.average:.1f}" if not game.higher_is_better else f"{row.average:.0f}"
        win_word = "win" if row.wins == 1 else "wins"
        lines.append(f"{marker} {row.player_name} — {row.wins} {win_word} · avg {avg} · {row.played} played")
    return "\n".join(lines)
