import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from gamebot.bot import FOOTER, REPEAT, TRACKED, GameBot
from gamebot.signal_client import rest_group_id
from gamebot.store import Store

ET = ZoneInfo("America/New_York")
GROUP = "rawgroupid=="
GID = rest_group_id(GROUP)


class FakeMessenger:
    def __init__(self):
        self.sent: list[str] = []
        self.raw: list[str] = []
        self.reactions: list[tuple[str, int, str]] = []

    async def send(self, group_id, text):
        assert group_id == GID
        self.raw.append(text)
        self.sent.append(text.removesuffix(FOOTER))

    async def react(self, group_id, author, timestamp, emoji):
        self.reactions.append((author, timestamp, emoji))


class Clock:
    def __init__(self, local: datetime):
        self.now = local.replace(tzinfo=ET).astimezone(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


def maptap(day: int, score: int) -> str:
    return f"www.maptap.gg October {day}\n98🔥 99🔥 92🏆 98🔥 90👑\nFinal score: {score}"


def envelope(who: str, text: str, ts: int = 1) -> dict:
    return {"sourceUuid": f"uuid-{who}", "sourceName": who, "timestamp": ts,
            "dataMessage": {"timestamp": ts, "message": text, "groupInfo": {"groupId": GROUP}}}


def allow(bot, group):
    bot.store.upsert_group(rest_group_id(group), group, bot.clock())
    bot.store.set_allowed(rest_group_id(group), True)


@pytest.fixture
def setup():
    clock = Clock(datetime(2026, 10, 5, 9, 0))
    messenger = FakeMessenger()
    bot = GameBot(Store(":memory:"), messenger, clock=clock)
    allow(bot, GROUP)
    run = lambda coro: asyncio.run(coro)  # noqa: E731
    return bot, messenger, clock, run


def test_reacts_and_ignores_chatter(setup):
    bot, messenger, _, run = setup
    run(bot.handle_envelope(envelope("Alice", "morning all", ts=10)))
    run(bot.handle_envelope(envelope("Alice", maptap(5, 945), ts=11)))
    run(bot.handle_envelope(envelope("Alice", maptap(5, 999), ts=12)))
    assert messenger.reactions == [("uuid-Alice", 11, TRACKED), ("uuid-Alice", 12, REPEAT)]
    assert messenger.sent == []


def test_no_early_post_without_history_then_cutoff_posts(setup):
    bot, messenger, clock, run = setup
    run(bot.handle_envelope(envelope("Alice", maptap(5, 945))))
    run(bot.handle_envelope(envelope("Bob", maptap(5, 990))))
    assert messenger.sent == []

    clock.advance(hours=8, minutes=59)  # 5:59pm
    run(bot.tick())
    assert messenger.sent == []

    clock.advance(minutes=1)  # 6:00pm
    run(bot.tick())
    run(bot.tick())  # only once per day
    assert messenger.sent == ["🗺️ MapTap — October 5\n🥇 Bob — 990\n🥈 Alice — 945"]


def test_early_post_when_all_regulars_in(setup):
    bot, messenger, clock, run = setup
    for who in ("Alice", "Bob", "Carol"):
        run(bot.handle_envelope(envelope(who, maptap(5, 900))))
    clock.advance(hours=9)  # Oct 5 6pm cutoff posts day one
    run(bot.tick())
    messenger.sent.clear()
    clock.advance(hours=15)  # Oct 6 9am
    run(bot.handle_envelope(envelope("Alice", maptap(6, 950))))
    run(bot.handle_envelope(envelope("Bob", maptap(6, 950))))
    assert messenger.sent == []
    run(bot.handle_envelope(envelope("Carol", maptap(6, 800))))
    early = ("🗺️ MapTap — October 6\n🥇 Alice — 950\n🥇 Bob — 950\n🥉 Carol — 800\n\n"
             "🔓 Everyone's played — spoilers are fair game!")
    assert messenger.sent == [early]

    # Late joiner after the early post doesn't trigger another post, nor does the cutoff.
    run(bot.handle_envelope(envelope("Dan", maptap(6, 999))))
    clock.advance(hours=10)
    run(bot.tick())
    assert messenger.sent == [early]


def test_framed_lower_is_better_and_leaderboard_command(setup):
    bot, messenger, _, run = setup
    run(bot.handle_envelope(envelope("Alice", "Framed #1234\n🎥 🟥 🟥 🟥 🟥 🟥 🟥\n\nhttps://framed.wtf")))
    run(bot.handle_envelope(envelope("Bob", "Framed #1234\n🎥 🟥 🟩 ⬛ ⬛ ⬛ ⬛\n\nhttps://framed.wtf")))
    run(bot.handle_envelope(envelope("Carol", maptap(5, 900))))
    run(bot.handle_envelope(envelope("Alice", "!leaderboard")))
    assert messenger.sent == [
        "🎬 Framed — #1234 (so far)\n🥇 Bob — 2/6\n🥈 Alice — X/6\n\n"
        "🗺️ MapTap — October 5 (so far)\n🥇 Carol — 900"
    ]


class FakeClaude:
    """Stands in for llm.Claude: reads Wordle-style messages, tells one joke, records every call."""

    monthly_budget_usd = 15.0

    def __init__(self):
        self.calls: list[str] = []

    async def joke(self, recent):
        self.calls.append("joke")
        return "I'm reading a book about anti-gravity. It's impossible to put down."

    async def parse_score(self, text, today, known):
        from gamebot.games import Result
        from gamebot.llm import GameInfo
        self.calls.append(text)
        if not text.startswith("Wordle"):
            return None
        number, guesses = text.split()[1], text.split()[2][0]
        score = 7 if guesses == "X" else int(guesses)
        # Deliberately claim "higher is better" on later calls: the first definition must stick.
        return (Result("wordle", number, f"#{number}", score, f"{guesses}/6"),
                GameInfo("wordle", "Wordle", "🟩", higher_is_better=len(self.calls) > 1))


def test_ai_fallback_learns_new_game(setup):
    bot, messenger, clock, run = setup
    bot.llm = claude = FakeClaude()
    run(bot.handle_envelope(envelope("Alice", "Wordle 1,570 4/6\n\n⬛🟨⬛⬛⬛\n🟩🟩🟩🟩🟩", ts=1)))
    run(bot.handle_envelope(envelope("Bob", "Wordle 1,570 2/6\n\n🟩🟩⬛🟩🟩\n🟩🟩🟩🟩🟩", ts=2)))
    run(bot.handle_envelope(envelope("Dan", maptap(5, 900), ts=4)))  # parsed deterministically
    assert len(claude.calls) == 2
    assert [r[2] for r in messenger.reactions] == ["🤖", "🤖", "✅"]

    clock.advance(hours=9)
    run(bot.tick())
    assert "🟩 Wordle — #1,570\n🥇 Bob — 2/6\n🥈 Alice — 4/6" in messenger.sent


def test_conversation_never_reaches_ai(setup):
    bot, messenger, _, run = setup
    bot.llm = claude = FakeClaude()
    for text in ["anyone up for lunch?", "I got 4/6 today, brutal", "my score was awful lol",
                 "check this out https://framed.wtf", "www.maptap.gg is fun"]:
        run(bot.handle_envelope(envelope("Carol", text)))
    assert claude.calls == [] and messenger.reactions == [] and messenger.sent == []


def test_ai_rejection_is_silent(setup):
    bot, messenger, _, run = setup
    bot.llm = claude = FakeClaude()
    run(bot.handle_envelope(envelope("Alice", "look at this 🟩🟩🟩 lawn")))
    assert len(claude.calls) == 1 and messenger.reactions == [] and messenger.sent == []


def test_ai_budget_caps(setup):
    bot, messenger, clock, run = setup
    bot.llm = claude = FakeClaude()
    wordle = "Wordle 1,570 4/6\n\n🟩🟩🟩🟩🟩"
    # $0.50/day cap: $0.49 spent today still allows a call...
    bot.store.record_usage("claude-sonnet-5", "score", 245_000, 0, at=clock())
    run(bot.handle_envelope(envelope("Alice", wordle, ts=1)))
    assert len(claude.calls) == 1
    # ...but at $0.50 the next score-looking message gets ⏳ and no AI call.
    bot.store.record_usage("claude-sonnet-5", "score", 5_000, 0, at=clock())
    run(bot.handle_envelope(envelope("Bob", wordle, ts=2)))
    assert len(claude.calls) == 1 and messenger.reactions[-1] == ("uuid-Bob", 2, "⏳")
    # A new day resets the daily cap, but the $15 monthly cap still applies.
    clock.advance(days=1)
    run(bot.handle_envelope(envelope("Bob", wordle, ts=3)))
    assert len(claude.calls) == 2
    bot.store.record_usage("claude-sonnet-5", "score", 7_500_000, 0, at=clock() - timedelta(hours=2))
    clock.advance(days=1)
    run(bot.handle_envelope(envelope("Carol", wordle, ts=4)))
    assert len(claude.calls) == 2 and messenger.reactions[-1][2] == "⏳"
    # Jokes fall back to canned ones instead of spending.
    run(bot.handle_envelope(envelope("Carol", "!joke", ts=5)))
    assert "joke" not in claude.calls and messenger.sent[-1].startswith("😄")


def test_direct_message_gets_a_reply():
    sent = []

    class DMMessenger(FakeMessenger):
        async def send(self, recipient, text):
            sent.append((recipient, text))

    bot = GameBot(Store(":memory:"), DMMessenger())
    asyncio.run(bot.handle_envelope({"sourceUuid": "uuid-Alice", "timestamp": 1,
                                     "dataMessage": {"timestamp": 1, "message": "hi"}}))
    assert len(sent) == 1 and sent[0][0] == "uuid-Alice"


def framed(n: int, guess: int) -> str:
    squares = " ".join(["🟥"] * (guess - 1) + ["🟩"] + ["⬛"] * (6 - guess)) if guess <= 6 else " ".join(["🟥"] * 6)
    return f"Framed #{n}\n🎥 {squares}\n\nhttps://framed.wtf"


def play_week(bot, clock, run):
    """Mon Oct 5 – Wed Oct 7: Alice wins MapTap twice, Bob once; Bob wins the one Framed."""
    days = [(5, 950, 900), (6, 980, 990), (7, 970, 960)]
    for i, (day, alice, bob) in enumerate(days):
        run(bot.handle_envelope(envelope("Alice", maptap(day, alice))))
        run(bot.handle_envelope(envelope("Bob", maptap(day, bob))))
        if i == 0:
            run(bot.handle_envelope(envelope("Alice", framed(100, 4))))
            run(bot.handle_envelope(envelope("Bob", framed(100, 2))))
        clock.advance(days=1)
    clock.advance(days=-1)


def test_bot_messages_carry_help_footer(setup):
    bot, messenger, _, run = setup
    run(bot.handle_envelope(envelope("Alice", maptap(5, 945))))
    run(bot.handle_envelope(envelope("Alice", "!lb")))
    assert messenger.raw[-1].endswith("💬 !help for commands")


def test_lb_week_standings(setup):
    bot, messenger, clock, run = setup
    play_week(bot, clock, run)
    run(bot.handle_envelope(envelope("Alice", "!lb week")))
    assert messenger.sent[-1] == (
        "🎬 Framed — This week (Oct 5–Oct 7)\n"
        "🥇 Bob — 1 win · avg 2.0 · 1 played\n"
        "🥈 Alice — 0 wins · avg 4.0 · 1 played\n\n"
        "🗺️ MapTap — This week (Oct 5–Oct 7)\n"
        "🥇 Alice — 2 wins · avg 967 · 3 played\n"
        "🥈 Bob — 1 win · avg 950 · 3 played"
    )


def test_lb_game_and_day_filters(setup):
    bot, messenger, clock, run = setup
    play_week(bot, clock, run)
    run(bot.handle_envelope(envelope("Alice", "!lb framed")))
    assert messenger.sent[-1].startswith("🎬 Framed — #100")
    run(bot.handle_envelope(envelope("Alice", "!lb maptap oct 6")))
    assert messenger.sent[-1] == "🗺️ MapTap — October 6\n🥇 Bob — 990\n🥈 Alice — 980"
    run(bot.handle_envelope(envelope("Alice", "!lb framed month")))
    assert messenger.sent[-1].startswith("🎬 Framed — October 2026\n🥇 Bob")
    sent_before = len(messenger.sent)
    run(bot.handle_envelope(envelope("Alice", "!lb sept 1", ts=50)))
    run(bot.handle_envelope(envelope("Alice", "!lb banana", ts=51)))
    assert len(messenger.sent) == sent_before  # reactions only, no messages
    assert messenger.reactions[-2:] == [("uuid-Alice", 50, "🤷"), ("uuid-Alice", 51, "❓")]


def test_only_exact_commands(setup):
    bot, messenger, clock, run = setup
    play_week(bot, clock, run)
    bot.llm = claude = FakeClaude()
    sent_before, reactions_before = len(messenger.sent), len(messenger.reactions)
    for i, text in enumerate(["!dance", "!leaderbord", "!help me", "!lb best streak", "!JOKE please"]):
        run(bot.handle_envelope(envelope("Alice", text, ts=100 + i)))
    assert claude.calls == [] and len(messenger.sent) == sent_before
    assert [r[2] for r in messenger.reactions[reactions_before:]] == ["❓"] * 5

    for text in ["!LB", "!help", "!lb framed week", "!leaderboard maptap all"]:
        run(bot.handle_envelope(envelope("Alice", text)))
    assert len(messenger.sent) == sent_before + 4


def test_joke_uses_ai_then_falls_back(setup):
    from gamebot.bot import FALLBACK_JOKES
    bot, messenger, _, run = setup
    run(bot.handle_envelope(envelope("Alice", "!joke")))
    assert messenger.sent[-1].removeprefix("😄 ") in FALLBACK_JOKES
    bot.llm = FakeClaude()
    run(bot.handle_envelope(envelope("Alice", "!joke")))
    assert "anti-gravity" in messenger.sent[-1]


def test_usage_report(setup):
    bot, messenger, _, run = setup
    run(bot.handle_envelope(envelope("Alice", "!usage")))
    assert messenger.sent[-1] == "🤖 No AI usage yet."
    bot, messenger, clock, run = bot, messenger, _, run
    bot.llm = FakeClaude()
    bot.store.record_usage("claude-haiku-4-5", "score", 40_000, 2_000, at=clock() - timedelta(days=1))
    bot.store.record_usage("claude-sonnet-5", "joke", 50_000, 5_000, at=clock())
    run(bot.handle_envelope(envelope("Alice", "!usage")))
    assert messenger.sent[-1] == (
        "🤖 AI usage, all time\n"
        "Haiku 4.5: 42,000 tokens · 1 calls\n"
        "Sonnet 5: 55,000 tokens · 1 calls\n"
        "Total: 97,000 tokens (55,000 today)\n"
        "Spent $0.20 of the $15.00 monthly budget ($0.15 today)\n"
        "💧 ≈ 2.91 bottles of water for data-center cooling (a very rough guess)"
    )


def test_groups_are_isolated():
    """Scores, boards, early posts, and the cutoff never mix between groups."""
    clock = Clock(datetime(2026, 10, 5, 9, 0))
    sent: list[tuple[str, str]] = []

    class TwoGroupMessenger(FakeMessenger):
        async def send(self, group_id, text):
            sent.append((group_id, text.removesuffix(FOOTER)))

    bot = GameBot(Store(":memory:"), TwoGroupMessenger(), clock=clock)
    run = asyncio.run

    def env(who, text, group):
        e = envelope(who, text)
        e["dataMessage"]["groupInfo"]["groupId"] = group
        return e

    test, real = GROUP, "realgroup=="
    allow(bot, test)
    allow(bot, real)
    test_id, real_id = rest_group_id(test), rest_group_id(real)
    run(bot.handle_envelope(env("Alice", maptap(5, 900), test)))
    run(bot.handle_envelope(env("Bob", maptap(5, 950), test)))
    run(bot.handle_envelope(env("Carol", maptap(5, 800), real)))

    run(bot.handle_envelope(env("Carol", "!lb", real)))
    assert sent[-1] == (real_id, "🗺️ MapTap — October 5 (so far)\n🥇 Carol — 800")
    run(bot.handle_envelope(env("Carol", "!lb all", real)))
    assert "Alice" not in sent[-1][1] and "Bob" not in sent[-1][1]

    # The cutoff posts each group's own Oct 5 board to that group only.
    sent.clear()
    clock.advance(hours=9)
    run(bot.tick())
    assert dict(sent) == {
        test_id: "🗺️ MapTap — October 5\n🥇 Bob — 950\n🥈 Alice — 900",
        real_id: "🗺️ MapTap — October 5\n🥇 Carol — 800",
    }

    # Real group's only regular is Carol, so Carol + Dan trigger the early post. If the test
    # group's Alice and Bob leaked in, they'd count as missing regulars and block it.
    sent.clear()
    clock.advance(hours=15)
    run(bot.handle_envelope(env("Carol", maptap(6, 990), real)))
    run(bot.handle_envelope(env("Dan", maptap(6, 700), real)))
    assert sent == [(real_id, "🗺️ MapTap — October 6\n🥇 Carol — 990\n🥈 Dan — 700\n\n"
                              "🔓 Everyone's played — spoilers are fair game!")]


def test_late_posts_use_the_puzzle_date(setup):
    bot, messenger, clock, run = setup
    play_week(bot, clock, run)  # MapTap Oct 5-7 and Framed #100 on Oct 5; clock is Wed Oct 7, 9am
    run(bot.handle_envelope(envelope("Alice", framed(101, 3))))  # Framed #101 posted Oct 7 (late)
    run(bot.handle_envelope(envelope("Alice", framed(102, 1))))  # today's Framed
    sent_before = len(messenger.sent)

    # Late posts of older puzzles: tracked, but no early post.
    run(bot.handle_envelope(envelope("Alice", maptap(3, 999))))
    run(bot.handle_envelope(envelope("Bob", maptap(3, 500))))
    run(bot.handle_envelope(envelope("Alice", framed(98, 2))))
    assert len(messenger.sent) == sent_before
    assert [r[2] for r in messenger.reactions[-3:]] == ["✅", "✅", "✅"]

    # !lb "latest" goes by puzzle date, not posting time.
    run(bot.handle_envelope(envelope("Alice", "!lb")))
    assert messenger.sent[-1].startswith("🎬 Framed — #102 (so far)")
    assert "🗺️ MapTap — October 7" in messenger.sent[-1]

    # Day and week filters file late posts under their real day.
    run(bot.handle_envelope(envelope("Alice", "!lb oct 3")))
    assert messenger.sent[-1] == ("🎬 Framed — #98 (so far)\n🥇 Alice — 2/6\n\n"
                                  "🗺️ MapTap — October 3 (so far)\n🥇 Alice — 999\n🥈 Bob — 500")
    run(bot.handle_envelope(envelope("Alice", "!lb maptap week")))  # week = Oct 5-7: Oct 3 excluded
    assert "3 played" in messenger.sent[-1] and "4 played" not in messenger.sent[-1]
    run(bot.handle_envelope(envelope("Alice", "!lb oct 6")))
    assert "#101" in messenger.sent[-1]  # posted on the 7th, but it's the 6th's puzzle

    # The 6pm cutoff posts today's Framed but not the late Oct 3 boards.
    messenger.sent.clear()
    clock.advance(hours=9)
    run(bot.tick())
    assert any("#102" in m for m in messenger.sent)
    assert not any("October 3" in m or "#98" in m for m in messenger.sent)


def test_puzzle_days_ignore_a_few_late_posts():
    from gamebot.query import puzzle_days
    et = ZoneInfo("America/New_York")
    at = lambda d: datetime(2026, 10, d, 12, tzinfo=et)  # noqa: E731
    puzzles = [("framed", "100", "#100", at(5)), ("framed", "101", "#101", at(6)),
               ("framed", "99", "#99", at(8)),  # three days late
               ("maptap", "2026-10-01", "October 1", at(8))]
    days = puzzle_days(puzzles, et)
    assert days[("framed", "99")].day == 4 and days[("framed", "101")].day == 6
    assert days[("maptap", "2026-10-01")].day == 1


def test_unapproved_groups_are_ignored(setup):
    bot, messenger, _, run = setup
    stranger = envelope("Mallory", maptap(5, 999))
    stranger["dataMessage"]["groupInfo"]["groupId"] = "strangers=="
    bot.store.upsert_group(rest_group_id("strangers=="), "Strangers", bot.clock())  # seen, not allowed
    for text in [maptap(5, 999), "!lb", "!joke", "!nonsense"]:
        stranger["dataMessage"]["message"] = text
        run(bot.handle_envelope(stranger))
    assert messenger.sent == [] and messenger.reactions == []
    assert bot.store.entries(rest_group_id("strangers=="), "maptap", "2026-10-05") == []

    bot.store.set_allowed(rest_group_id("strangers=="), True)
    stranger["dataMessage"]["message"] = maptap(5, 999)
    run(bot.handle_envelope(stranger))
    assert messenger.reactions[-1][2] == "✅"


def test_existing_groups_grandfathered_into_allowlist(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE groups (group_id TEXT PRIMARY KEY, name TEXT NOT NULL, first_seen TEXT NOT NULL)")
    old.execute("INSERT INTO groups VALUES ('group.abc', 'Test chat', '2026-10-05T20:00:00+00:00')")
    old.commit()
    old.close()
    store = Store(str(path))
    assert store.is_allowed("group.abc")
    store.upsert_group("group.new", "New chat", datetime.now(timezone.utc))
    assert not store.is_allowed("group.new")
    assert Store(str(path)).is_allowed("group.abc")  # migration runs once, not on every start


def test_dm_replies_are_rate_limited():
    sent = []

    class DMMessenger(FakeMessenger):
        async def send(self, recipient, text):
            sent.append(recipient)

    clock = Clock(datetime(2026, 10, 5, 9, 0))
    bot = GameBot(Store(":memory:"), DMMessenger(), clock=clock)
    dm = lambda who: {"sourceUuid": who, "timestamp": 1, "dataMessage": {"timestamp": 1, "message": "hi"}}  # noqa: E731
    for _ in range(5):
        asyncio.run(bot.handle_envelope(dm("uuid-spammer")))
    assert sent == ["uuid-spammer"]  # once per person per day
    for i in range(30):
        asyncio.run(bot.handle_envelope(dm(f"uuid-{i}")))
    assert len(sent) == 20  # 20 replies an hour across everyone
    clock.advance(days=1, minutes=1)
    asyncio.run(bot.handle_envelope(dm("uuid-spammer")))
    assert sent[-1] == "uuid-spammer"


def test_open_board_shows_who_we_are_waiting_on(setup):
    bot, messenger, clock, run = setup
    for who in ("Alice", "Bob", "Carol"):
        run(bot.handle_envelope(envelope(who, maptap(5, 900))))
    clock.advance(days=1)
    run(bot.handle_envelope(envelope("Bob", maptap(6, 950))))
    run(bot.handle_envelope(envelope("Alice", "!lb maptap")))
    assert messenger.sent[-1] == ("🗺️ MapTap — October 6 (so far)\n🥇 Bob — 950\n"
                                  "⏳ Waiting on: Alice, Carol")


def test_games_command(setup):
    bot, messenger, _, run = setup
    from gamebot.llm import GameInfo
    bot.store.learn_game(GameInfo("costcodle", "Costcodle", "🛒", False, "https://costcodle.com"))
    bot.store.learn_game(GameInfo("elsewhere", "Other Group Game", "❔", True))
    bot.store.add(GID, __import__("gamebot.games").games.Result("costcodle", "5", "#5", 3, "3/6"),
                  "uuid-Alice", "Alice", "raw", bot.clock())
    run(bot.handle_envelope(envelope("Alice", "!games")))
    assert messenger.sent[-1] == (
        "🎮 Games\n"
        "🎨 Color Daily — https://dialed.gg/color\n"
        "🛒 Costcodle — https://costcodle.com\n"
        "🎬 Framed — https://framed.wtf\n"
        "🗺️ MapTap — https://maptap.gg\n"
        "🌍 TimeGuessr — https://timeguessr.com"
    )  # games learned only in other groups aren't listed


def test_learned_urls_are_sanitized():
    from gamebot.llm import _clean_url
    assert _clean_url("https://dialed.gg/color?d=1&s=43.82") == "https://dialed.gg/color"
    assert _clean_url("https://timeguessr.com/") == "https://timeguessr.com"
    assert _clean_url("javascript:alert(1)") == ""
    assert _clean_url("http://plain.example.com") == ""


def test_reparse_merges_ai_read_rows(capsys):
    from gamebot.admin import reparse
    from gamebot.games import Result
    store = Store(":memory:")
    at = datetime(2026, 10, 7, 13, tzinfo=timezone.utc)
    day2 = "Color Daily — Oct 7\nDay 2\n43.82/50 🟨🟨🟨🟧🟩\nhttps://dialed.gg/color?d=1&s=43.82"
    plain = "Color Daily — Oct 7\n42.48/50 🟨🟨🟨🟨🟨\nhttps://dialed.gg/color?d=1&s=42.48"
    store.add("g", Result("color", "2", "Day 2", 44, "43.82/50"), "p1", "Alice", day2, at)
    store.add("g", Result("color", "2026-10-07", "Oct 7", 42, "42.48/50"), "p2", "Bob", plain, at)
    store.add("g", Result("color", "2026-10-07", "Oct 7", 44, "43.82/50"), "p1", "Alice", day2, at)  # dup
    reparse(store, apply=False)
    assert len(store.entries("g", "color", "2026-10-07")) == 2  # dry run changes nothing
    reparse(store, apply=True)
    entries = store.entries("g", "color", "2026-10-07")
    assert sorted((e.player_name, e.score, e.display) for e in entries) == [
        ("Alice", 4382, "43.82/50"), ("Bob", 4248, "42.48/50")]
    assert store.entries("g", "color", "2") == []


def test_help_links_the_repo(setup):
    bot, messenger, _, run = setup
    run(bot.handle_envelope(envelope("Alice", "!help")))
    assert messenger.sent[-1].endswith("🛠️ Source & setup: https://github.com/kristjanArumae/signal-gamebot")
