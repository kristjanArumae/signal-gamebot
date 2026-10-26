"""Ranking and formatting leaderboards."""

from __future__ import annotations

from .llm import GameInfo
from .store import Entry, Play

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


TOP_SCORES = 3


def top_plays(plays: list[Play], higher_is_better: bool) -> list[tuple[int, Play]]:
    """The best single scores, one person's several included. Ties go to whoever posted first."""
    ordered = sorted(plays, key=lambda p: -p.score if higher_is_better else p.score)  # stable: oldest first
    ranked: list[tuple[int, Play]] = []
    for i, play in enumerate(ordered[:TOP_SCORES]):
        tied = i and play.score == ordered[i - 1].score
        ranked.append((ranked[-1][0] if tied else i + 1, play))
    return ranked


def format_top_scores(game: GameInfo, label: str, ranked: list[tuple[int, Play, str, str]]) -> str:
    """ranked rows are (place, play, when, detail): when is the puzzle's day, detail its rounds/grid."""
    title = "Top scores" if label == "All time" else f"Top scores · {label}"
    lines = [f"{game.emoji} {game.name} — {title}"]
    for place, play, when, detail in ranked:
        lines.append(f"{MEDALS[place]} {play.player_name} — {play.display} · {when}")
        if detail:
            lines.append(f"   {detail}")
    return "\n".join(lines)


def format_board(game: GameInfo, puzzle_label: str, entries: list[Entry], so_far: bool = False) -> str:
    title = f"{game.emoji} {game.name} — {puzzle_label}"
    if so_far:
        title += " (so far)"
    lines = [title]
    for place, entry in rank(entries, game.higher_is_better):
        marker = MEDALS.get(place, f"{place}.")
        lines.append(f"{marker} {entry.player_name} — {entry.display}")
    return "\n".join(lines)
