from datetime import date

from gamebot.games import Result, parse_any

TODAY = date(2026, 10, 5)

MAPTAP = """www.maptap.gg October 5
98🔥 99🔥 92🏆 98🔥 90👑
Final score: 945"""


def test_maptap():
    r = parse_any(MAPTAP, TODAY)
    assert (r.game, r.puzzle, r.puzzle_label, r.score, r.display) == (
        "maptap", "2026-10-05", "October 5", 945, "945")


def test_maptap_year_rollover():
    r = parse_any(MAPTAP.replace("October 5", "December 31"), date(2027, 1, 1))
    assert r.puzzle == "2026-12-31"


def test_maptap_abbreviated_month_and_comma_score():
    r = parse_any("maptap.gg Oct 5\n...\nFinal score: 1,000", TODAY)
    assert (r.puzzle, r.score) == ("2026-10-05", 1000)


def test_framed_solved():
    r = parse_any("Framed #1234\n🎥 🟥 🟥 🟩 ⬛ ⬛ ⬛\n\nhttps://framed.wtf", TODAY)
    assert (r.game, r.puzzle, r.puzzle_label, r.score, r.display) == (
        "framed", "1234", "#1234", 3, "3/6")


def test_framed_first_try():
    r = parse_any("Framed #1234\n🎥 🟩 ⬛ ⬛ ⬛ ⬛ ⬛\n\nhttps://framed.wtf", TODAY)
    assert (r.score, r.display) == (1, "1/6")


def test_framed_failed():
    r = parse_any("Framed #1234\n🎥 🟥 🟥 🟥 🟥 🟥 🟥\n\nhttps://framed.wtf", TODAY)
    assert (r.score, r.display) == (7, "X/6")


def test_framed_variants_ignored():
    assert parse_any("Framed - One Frame Challenge #500\n🎥 🟩\n\nhttps://framed.wtf", TODAY) is None


def test_chatter_ignored():
    assert parse_any("lol I got destroyed on maptap today", TODAY) is None
    assert parse_any("Final score: 900", TODAY) is None


def test_last_week_and_last_month_ranges():
    from gamebot.query import parse_lb_args
    q = parse_lb_args("last week", TODAY, [])  # TODAY is Monday Oct 5
    assert (q.start, q.end) == (date(2026, 9, 28), date(2026, 10, 4))
    q = parse_lb_args("last month", TODAY, [])
    assert (q.start, q.end) == (date(2026, 9, 1), date(2026, 9, 30))


def test_timeguessr():
    text = ("TimeGuessr #1224 — 41,295/50,000\n\n1️⃣ 🏆9,072 · 📅 1y · 🌍 234.7mi\n"
            "2️⃣ 🏆9,123 · 📅 4y · 🌍 12.0mi\n\nhttps://timeguessr.com")
    r = parse_any(text, TODAY)
    assert (r.game, r.puzzle, r.puzzle_label, r.score, r.display) == (
        "timeguessr", "1224", "#1224", 41295, "41,295/50,000")


def test_color_daily_with_and_without_day_line():
    with_day = "Color Daily — Oct 7\nDay 2\n43.82/50 🟨🟨🟨🟧🟩\nhttps://dialed.gg/color?d=1&s=43.82"
    without = "Color Daily — Oct 7\n42.4/50 🟨🟨🟨🟨🟨\nhttps://dialed.gg/color?d=1&s=42.4"
    a, b = parse_any(with_day, date(2026, 10, 7)), parse_any(without, date(2026, 10, 7))
    assert (a.game, a.puzzle, a.puzzle_label, a.score, a.display) == (
        "color", "2026-10-07", "October 7", 4382, "43.82/50")
    assert (b.puzzle, b.score, b.display) == ("2026-10-07", 4240, "42.40/50")


def test_adjusted_average_beats_a_lucky_day_and_breaks_ties_by_wins():
    from gamebot.llm import GameInfo
    from gamebot.query import format_standings, standings
    from gamebot.store import Entry
    e = lambda pid, s: Entry(pid, pid.title(), s, str(s))  # noqa: E731
    # Lucky Bob plays once (900, the best single score); steady Cam plays 4 days at 850;
    # Alice, Dan, and Eve play all 7 days at 800, 720, 720.
    day = lambda *extra: [e("alice", 800), e("dan", 720), e("eve", 720), *extra]  # noqa: E731
    days = [day(e("cam", 850), e("bob", 900))] + [day(e("cam", 850))] * 3 + [day()] * 3
    ranked = {r.player_name: r for _, r in standings(days, higher_is_better=True)}
    group_avg = (7 * 800 + 4 * 850 + 900 + 14 * 720) / 26
    assert round(ranked["Bob"].adjusted, 2) == round((900 + 3 * group_avg) / 4, 2)
    assert [p for p, _ in sorted(ranked.items(), key=lambda kv: -kv[1].adjusted)][:3] == ["Cam", "Bob", "Alice"]

    # Same adjusted score: more daily wins ranks higher; identical everything shares a place.
    tied = standings([[e("a", 5), e("b", 5)]], higher_is_better=False)
    assert [p for p, _ in tied] == [1, 1]

    color = GameInfo("color", "Color Daily", "🎨", True, scale=100)
    out = format_standings(color, "All time", standings([[e("x", 4382)]], True))
    assert out == "🎨 Color Daily — All time\n🥇 X — 43.82 (avg 43.82 · 1 played · 1 win)"


def test_episode():
    from gamebot.games import Episode
    text = "Episode #215\n📺 🟥 🟩 ⬛ ⬛ ⬛ ⬛ ⬛ ⬛ ⬛ ⬛\n\nhttps://episode.wtf"
    assert parse_any(text, date(2026, 10, 15)) == Result("episode", "215", "#215", 2, "2/10")
    failed = "Episode #215\n📺 " + " ".join(["🟥"] * 10) + "\n\nhttps://episode.wtf"
    assert parse_any(failed, date(2026, 10, 15)).display == "X/10"
    assert Episode().puzzle_day("215") == date(2026, 10, 12)  # a Monday


def test_plotwords():
    text = "Plotwords #1554\nClues used: 3/10\n\nhttps://plotwords.com/daily"
    assert parse_any(text, TODAY) == Result("plotwords", "1554", "#1554", 3, "3/10")
    assert parse_any("Plotwords #1555\nClues used: 9/9", TODAY).display == "9/9"
    assert parse_any("Plotwords #1555\nno clue line", TODAY) is None


def test_record_details():
    from gamebot.games import ColorDaily, MapTap, TimeGuessr
    assert MapTap().detail(MAPTAP) == "98🔥 99🔥 92🏆 98🔥 90👑"
    tg = "TimeGuessr #1239 — 47,801/50,000\n\n1️⃣ 🏆9,858 · 📅 1y · 🌍 7.7mi\n2️⃣ 🏆9,993 · 📅 0y · 🌍 657ft"
    assert TimeGuessr().detail(tg) == "9,858 · 9,993"
    color = "Color Daily — Oct 14\nDay 9\n46.75/50 🟨🟩🟩🟨🟩\nhttps://dialed.gg/color?d=1&s=46.75"
    assert ColorDaily().detail(color) == "🟨🟩🟩🟨🟩"
