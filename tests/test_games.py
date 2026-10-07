from datetime import date

from gamebot.games import parse_any

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
