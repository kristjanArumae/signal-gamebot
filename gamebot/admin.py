"""Admin, run on the instance:
  python -m gamebot.admin list | allow <name or id> | deny <name or id>
  python -m gamebot.admin reparse [--apply]   re-read stored results with the current parsers
"""

import os
import sqlite3
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .games import parse_any
from .store import Store


def reparse(store: Store, apply: bool) -> None:
    """Re-read every stored result with today's parsers (e.g. after adding a parser for a game the
    AI was handling). Oldest first, so if two rows collapse into one puzzle the first post wins."""
    tz = ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))
    rows = store.db.execute(
        "SELECT rowid, group_id, game, puzzle, player_id, score, display, raw, received_at "
        "FROM submissions ORDER BY received_at").fetchall()
    changed = removed = 0
    for rowid, group_id, game, puzzle, player_id, score, display, raw, at in rows:
        result = parse_any(raw, datetime.fromisoformat(at).astimezone(tz).date())
        if not result or (result.game, result.puzzle, result.score, result.display) == (game, puzzle, score, display):
            continue
        print(f"{game} {puzzle} {display!r} -> {result.game} {result.puzzle} {result.display!r}")
        if not apply:
            continue
        try:
            store.db.execute(
                "UPDATE submissions SET game=?, puzzle=?, puzzle_label=?, score=?, display=? WHERE rowid=?",
                (result.game, result.puzzle, result.puzzle_label, result.score, result.display, rowid))
            changed += 1
        except sqlite3.IntegrityError:  # this player already has a row for that puzzle: keep the first
            store.db.execute("DELETE FROM submissions WHERE rowid=?", (rowid,))
            removed += 1
    store.db.commit()
    print(f"{'applied' if apply else 'dry run'}: {changed} updated, {removed} duplicates removed")


def main() -> None:
    store = Store(os.environ.get("DB_PATH", "/data/gamebot.db"))
    args = sys.argv[1:]
    command, target = (args[0] if args else "list"), " ".join(args[1:])
    if command == "reparse":
        reparse(store, apply=target == "--apply")
        return
    groups = store.list_groups()
    if command == "list":
        for group_id, name, allowed in groups:
            print(f"{'ALLOWED' if allowed else 'blocked'}  {name or '(no name)'}  {group_id}")
        return
    if command not in ("allow", "deny") or not target:
        sys.exit(__doc__)
    matches = [g for g in groups if target in (g[0], g[1])]
    if len(matches) != 1:
        sys.exit(f"expected exactly one group named or with id {target!r}, found {len(matches)}")
    store.set_allowed(matches[0][0], command == "allow")
    print(f"{command}ed {matches[0][1] or matches[0][0]}")


if __name__ == "__main__":
    main()
