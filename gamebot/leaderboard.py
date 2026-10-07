"""Ranking and formatting leaderboards."""

from __future__ import annotations

from .llm import GameInfo
from .store import Entry

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def rank(entries: list[Entry], higher_is_better: bool) -> list[tuple[int, Entry]]:
    """Competition ranking: ties share a rank and the next rank is skipped (1, 1, 3)."""
    ordered = sorted(entries, key=lambda e: e.score, reverse=higher_is_better)
    ranked: list[tuple[int, Entry]] = []
    for i, entry in enumerate(ordered):
        if i and entry.score == ordered[i - 1].score:
            ranked.append((ranked[-1][0], entry))
        else:
            ranked.append((i + 1, entry))
    return ranked


def format_board(game: GameInfo, puzzle_label: str, entries: list[Entry], so_far: bool = False) -> str:
    title = f"{game.emoji} {game.name} — {puzzle_label}"
    if so_far:
        title += " (so far)"
    lines = [title]
    for place, entry in rank(entries, game.higher_is_better):
        marker = MEDALS.get(place, f"{place}.")
        lines.append(f"{marker} {entry.player_name} — {entry.display}")
    return "\n".join(lines)
