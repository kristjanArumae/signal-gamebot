"""SQLite persistence for submissions and which leaderboards were posted."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .games import Result
from .llm import GameInfo, cost_usd

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    group_id     TEXT NOT NULL,
    game         TEXT NOT NULL,
    puzzle       TEXT NOT NULL,
    puzzle_label TEXT NOT NULL,
    player_id    TEXT NOT NULL,
    player_name  TEXT NOT NULL,
    score        INTEGER NOT NULL,
    display      TEXT NOT NULL,
    raw          TEXT NOT NULL,
    received_at  TEXT NOT NULL,
    PRIMARY KEY (group_id, game, puzzle, player_id)
);
CREATE INDEX IF NOT EXISTS submissions_recent ON submissions (group_id, game, received_at);
CREATE TABLE IF NOT EXISTS posted (
    group_id  TEXT NOT NULL,
    game      TEXT NOT NULL,
    puzzle    TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    PRIMARY KEY (group_id, game, puzzle)
);
CREATE TABLE IF NOT EXISTS groups (
    group_id   TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    allowed    INTEGER NOT NULL DEFAULT 0  -- the bot ignores groups until they're allowed
);
CREATE TABLE IF NOT EXISTS games (
    key              TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    emoji            TEXT NOT NULL,
    higher_is_better INTEGER NOT NULL,
    url              TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS llm_usage (
    at            TEXT NOT NULL,
    model         TEXT NOT NULL,
    purpose       TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Entry:
    player_id: str
    player_name: str
    score: int
    display: str


@dataclass(frozen=True)
class PuzzleRef:
    group_id: str
    game: str
    puzzle: str
    puzzle_label: str


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        if "url" not in {row[1] for row in self.db.execute("PRAGMA table_info(games)")}:
            self.db.execute("ALTER TABLE games ADD COLUMN url TEXT NOT NULL DEFAULT ''")
            self.db.commit()
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(groups)")}
        if "allowed" not in columns:
            # Allowlist introduced: grandfather in every group the bot was already in.
            self.db.execute("ALTER TABLE groups ADD COLUMN allowed INTEGER NOT NULL DEFAULT 0")
            self.db.execute("UPDATE groups SET allowed = 1")
            self.db.commit()

    def add(self, group_id: str, result: Result, player_id: str, player_name: str,
            raw: str, now: datetime) -> bool:
        """Record a submission. The first one per player per puzzle counts; returns False for repeats."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (group_id, result.game, result.puzzle, result.puzzle_label, player_id,
             player_name, result.score, result.display, raw, now.isoformat()),
        )
        # Keep the display name current even when the score is a repeat.
        self.db.execute(
            "UPDATE submissions SET player_name = ? WHERE group_id = ? AND player_id = ?",
            (player_name, group_id, player_id),
        )
        self.db.commit()
        return cur.rowcount == 1

    def entries(self, group_id: str, game: str, puzzle: str) -> list[Entry]:
        rows = self.db.execute(
            "SELECT player_id, player_name, score, display FROM submissions "
            "WHERE group_id = ? AND game = ? AND puzzle = ? ORDER BY received_at",
            (group_id, game, puzzle),
        )
        return [Entry(*row) for row in rows]

    def players_since(self, group_id: str, game: str, since: datetime,
                      exclude_puzzle: str) -> set[str]:
        rows = self.db.execute(
            "SELECT DISTINCT player_id FROM submissions "
            "WHERE group_id = ? AND game = ? AND received_at >= ? AND puzzle != ?",
            (group_id, game, since.isoformat(), exclude_puzzle),
        )
        return {row[0] for row in rows}

    def is_posted(self, group_id: str, game: str, puzzle: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM posted WHERE group_id = ? AND game = ? AND puzzle = ?",
            (group_id, game, puzzle),
        ).fetchone()
        return row is not None

    def mark_posted(self, group_id: str, game: str, puzzle: str, now: datetime) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO posted VALUES (?,?,?,?)",
            (group_id, game, puzzle, now.isoformat()),
        )
        self.db.commit()

    def unposted_since(self, since: datetime) -> list[PuzzleRef]:
        rows = self.db.execute(
            "SELECT s.group_id, s.game, s.puzzle, MIN(s.puzzle_label) FROM submissions s "
            "LEFT JOIN posted p USING (group_id, game, puzzle) "
            "WHERE p.puzzle IS NULL GROUP BY s.group_id, s.game, s.puzzle "
            "HAVING MAX(s.received_at) >= ? ORDER BY MIN(s.received_at)",
            (since.isoformat(),),
        )
        return [PuzzleRef(*row) for row in rows]

    def puzzles(self, group_id: str) -> list[tuple[str, str, str, datetime]]:
        """Every puzzle played in a group as (game, puzzle, label, first submission time)."""
        rows = self.db.execute(
            "SELECT game, puzzle, MIN(puzzle_label), MIN(received_at) FROM submissions "
            "WHERE group_id = ? GROUP BY game, puzzle ORDER BY MIN(received_at)",
            (group_id,),
        )
        return [(g, p, label, datetime.fromisoformat(t)) for g, p, label, t in rows]

    def upsert_group(self, group_id: str, name: str, now: datetime) -> None:
        self.db.execute(
            "INSERT INTO groups (group_id, name, first_seen) VALUES (?,?,?) "
            "ON CONFLICT(group_id) DO UPDATE SET name = excluded.name",
            (group_id, name, now.isoformat()),
        )
        self.db.commit()

    def is_allowed(self, group_id: str) -> bool:
        row = self.db.execute("SELECT allowed FROM groups WHERE group_id = ?", (group_id,)).fetchone()
        return bool(row and row[0])

    def set_allowed(self, group_id: str, allowed: bool) -> None:
        self.db.execute("UPDATE groups SET allowed = ? WHERE group_id = ?", (int(allowed), group_id))
        self.db.commit()

    def list_groups(self) -> list[tuple[str, str, bool]]:
        rows = self.db.execute("SELECT group_id, name, allowed FROM groups ORDER BY first_seen")
        return [(gid, name, bool(allowed)) for gid, name, allowed in rows]

    def player_names(self, group_id: str) -> dict[str, str]:
        rows = self.db.execute("SELECT DISTINCT player_id, player_name FROM submissions WHERE group_id = ?",
                               (group_id,))
        return dict(rows.fetchall())

    def games_played(self, group_id: str) -> set[str]:
        rows = self.db.execute("SELECT DISTINCT game FROM submissions WHERE group_id = ?", (group_id,))
        return {row[0] for row in rows}

    def learned_games(self) -> list[GameInfo]:
        rows = self.db.execute("SELECT key, name, emoji, higher_is_better, url FROM games ORDER BY key")
        return [GameInfo(k, n, e, bool(h), url) for k, n, e, h, url in rows]

    def learn_game(self, game: GameInfo) -> GameInfo:
        """Save a game the first time it's seen; afterwards the saved definition wins."""
        self.db.execute(
            "INSERT OR IGNORE INTO games VALUES (?,?,?,?,?)",
            (game.key, game.name, game.emoji, int(game.higher_is_better), game.url),
        )
        self.db.commit()
        return next(g for g in self.learned_games() if g.key == game.key)

    def record_usage(self, model: str, purpose: str, input_tokens: int, output_tokens: int,
                     at: datetime | None = None) -> None:
        self.db.execute(
            "INSERT INTO llm_usage VALUES (?,?,?,?,?)",
            ((at or datetime.now(timezone.utc)).isoformat(), model, purpose, input_tokens, output_tokens),
        )
        self.db.commit()

    def usage_by_model(self, since: datetime | None = None) -> list[tuple[str, int, int, int]]:
        """(model, calls, input_tokens, output_tokens) per model, optionally since a time."""
        rows = self.db.execute(
            "SELECT model, COUNT(*), SUM(input_tokens), SUM(output_tokens) FROM llm_usage "
            "WHERE at >= ? GROUP BY model ORDER BY model",
            ((since or datetime.min.replace(tzinfo=timezone.utc)).isoformat(),),
        )
        return list(rows)

    def spend_since(self, since: datetime) -> float:
        return sum(cost_usd(model, i, o) for model, _, i, o in self.usage_by_model(since))

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
        self.db.commit()
