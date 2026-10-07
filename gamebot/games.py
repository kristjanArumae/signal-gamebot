"""Parsers for daily-game share messages.

Each game turns a pasted share message into a Result. To add a game, subclass
Game, implement parse(), and append an instance to GAMES.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Result:
    game: str
    puzzle: str  # stable id for the day's puzzle, e.g. "2026-10-05" or "1234"
    puzzle_label: str  # human-friendly, e.g. "October 5" or "#1234"
    score: int  # raw score used for ranking
    display: str  # how the score is shown on the leaderboard


class Game:
    key: str
    name: str
    emoji: str
    url: str
    higher_is_better: bool

    def parse(self, text: str, today: date) -> Result | None:
        raise NotImplementedError


_MONTHS = {
    name.lower(): i
    for i in range(1, 13)
    for name in (calendar.month_name[i], calendar.month_abbr[i])
}


def _infer_date(month: int, day: int, today: date) -> date | None:
    """Share messages omit the year; pick the candidate closest to today."""
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            pass
    if not candidates:
        return None
    return min(candidates, key=lambda d: abs((d - today).days))


class MapTap(Game):
    key = "maptap"
    name = "MapTap"
    emoji = "🗺️"
    url = "https://maptap.gg"
    higher_is_better = True

    _header = re.compile(r"maptap\.gg\s+([A-Za-z]+)\.?\s+(\d{1,2})\b", re.I)
    _final = re.compile(r"Final score:\s*([\d,]+)", re.I)

    def parse(self, text: str, today: date) -> Result | None:
        header = self._header.search(text)
        final = self._final.search(text)
        if not header or not final:
            return None
        month = _MONTHS.get(header.group(1).lower())
        if not month:
            return None
        day = _infer_date(month, int(header.group(2)), today)
        if not day:
            return None
        score = int(final.group(1).replace(",", ""))
        return Result(
            game=self.key,
            puzzle=day.isoformat(),
            puzzle_label=f"{calendar.month_name[day.month]} {day.day}",
            score=score,
            display=str(score),
        )


class Framed(Game):
    """Framed #1234 / 🎥 🟥 🟥 🟩 ⬛ ⬛ ⬛ — score is guesses used, 7 = failed."""

    key = "framed"
    name = "Framed"
    emoji = "🎬"
    url = "https://framed.wtf"
    higher_is_better = False

    _header = re.compile(r"^\s*Framed\s+#\s*([\d,]+)\s*$", re.M)
    _squares = re.compile(r"[🟥🟩⬛🟨]")
    FAIL = 7

    def parse(self, text: str, today: date) -> Result | None:
        header = self._header.search(text)
        if not header:
            return None
        rest = text[header.end():]
        grid = next((line for line in rest.splitlines() if "🎥" in line), None)
        if grid is None:
            return None
        squares = self._squares.findall(grid)
        if not squares:
            return None
        number = header.group(1).replace(",", "")
        if "🟩" in squares:
            score = squares.index("🟩") + 1
            display = f"{score}/6"
        else:
            score = self.FAIL
            display = "X/6"
        return Result(
            game=self.key,
            puzzle=number,
            puzzle_label=f"#{number}",
            score=score,
            display=display,
        )


class TimeGuessr(Game):
    """TimeGuessr #1224 — 41,295/50,000 (five rounds follow)."""

    key = "timeguessr"
    name = "TimeGuessr"
    emoji = "🌍"
    url = "https://timeguessr.com"
    higher_is_better = True

    _header = re.compile(r"TimeGuessr\s+#\s*([\d,]+)\s*[—–-]\s*([\d,]+)\s*/\s*([\d,]+)", re.I)

    def parse(self, text: str, today: date) -> Result | None:
        m = self._header.search(text)
        if not m:
            return None
        number = m.group(1).replace(",", "")
        score = int(m.group(2).replace(",", ""))
        return Result(self.key, number, f"#{number}", score, f"{m.group(2)}/{m.group(3)}")


class ColorDaily(Game):
    """Color Daily — Oct 7 / [Day 2] / 43.82/50 🟨🟨🟨🟧🟩 (dialed.gg).

    Keyed by the date in the header, which every share has ("Day N" is sometimes missing).
    Scores have two decimals, so they're stored in hundredths to keep 43.82 vs 43.80 distinct.
    """

    key = "color"
    name = "Color Daily"
    emoji = "🎨"
    url = "https://dialed.gg/color"
    higher_is_better = True

    _header = re.compile(r"Color Daily\s*[—–-]\s*([A-Za-z]+)\.?\s+(\d{1,2})\b", re.I)
    _score = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2})?)\s*/\s*50\b", re.M)

    def parse(self, text: str, today: date) -> Result | None:
        header = self._header.search(text)
        score = self._score.search(text)
        if not header or not score:
            return None
        month = _MONTHS.get(header.group(1).lower())
        day = month and _infer_date(month, int(header.group(2)), today)
        if not day:
            return None
        value = score.group(1)
        return Result(
            game=self.key,
            puzzle=day.isoformat(),
            puzzle_label=f"{calendar.month_name[day.month]} {day.day}",
            score=round(float(value) * 100),
            display=f"{float(value):.2f}/50",
        )


GAMES: list[Game] = [MapTap(), Framed(), TimeGuessr(), ColorDaily()]
GAMES_BY_KEY = {g.key: g for g in GAMES}


def parse_any(text: str, today: date) -> Result | None:
    for game in GAMES:
        result = game.parse(text, today)
        if result:
            return result
    return None
