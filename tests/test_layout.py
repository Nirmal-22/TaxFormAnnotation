from dataclasses import replace

import pytest

from tfa.fonts import FontMetrics
from tfa.layout import layout_text
from tfa.model import Comb, CombGroup, Padding, Rect
from tfa.styles import BUILTIN
from tfa.values import FieldError

M = FontMetrics()
BOX = Rect(100, 200, 80, 12)
STYLE = replace(BUILTIN, padding=Padding(0, 0, 0, 0), fontSize=10)


def width(text, size=10, font="Helvetica"):
    return M.width(text, font, size)


def test_horizontal_alignment():
    [left] = layout_text("123", BOX, STYLE, M).runs
    [right] = layout_text("123", BOX, replace(STYLE, align="right"), M).runs
    [center] = layout_text("123", BOX, replace(STYLE, align="center"), M).runs
    assert left.x == 100
    assert right.x == pytest.approx(180 - width("123"))
    assert center.x == pytest.approx(100 + (80 - width("123")) / 2)


def test_padding_shrinks_the_box():
    [run] = layout_text("1", BOX, replace(STYLE, padding=Padding(0, 4, 0, 3), align="right"), M).runs
    assert run.x == pytest.approx(176 - width("1"))


def test_vertical_alignment_uses_cap_height():
    cap = 0.718 * 10  # Helvetica cap height at 10pt
    [middle] = layout_text("X", BOX, STYLE, M).runs
    [top] = layout_text("X", BOX, replace(STYLE, valign="top"), M).runs
    [bottom] = layout_text("X", BOX, replace(STYLE, valign="bottom"), M).runs
    assert middle.baseline == pytest.approx(200 + 6 + cap / 2)  # caps centred in the box
    assert top.baseline == pytest.approx(200 + cap)
    assert bottom.baseline == pytest.approx(212 - 0.207 * 10)  # descenders stay inside


def test_shrink_to_fit():
    text = "x" * 20  # 100pt at 10pt, fits at 8pt
    [run] = layout_text(text, BOX, STYLE, M).runs
    assert run.size < 10
    assert width(text, run.size) <= 80


def test_shrink_stops_at_min_font_size():
    with pytest.raises(FieldError) as e:
        layout_text("x" * 200, BOX, STYLE, M)
    assert e.value.code == "TEXT_OVERFLOW"


def test_truncate_warns():
    layout = layout_text("x" * 200, BOX, replace(STYLE, overflow="truncate"), M)
    assert width(layout.runs[0].text) <= 80
    assert [w.code for w in layout.warnings] == ["TEXT_TRUNCATED"]


def test_error_policy():
    with pytest.raises(FieldError):
        layout_text("x" * 50, BOX, replace(STYLE, overflow="error"), M)


def test_wrap():
    box = Rect(0, 0, 60, 40)
    layout = layout_text("one two three four five", box, replace(STYLE, overflow="wrap", valign="top"), M)
    lines = [r.text for r in layout.runs]
    assert len(lines) > 1 and " ".join(lines) == "one two three four five"
    baselines = [r.baseline for r in layout.runs]
    assert baselines == sorted(baselines)
    assert baselines[1] - baselines[0] == pytest.approx(11.5)  # lineHeight 1.15


def test_wrap_honours_newlines_and_shrinks():
    box = Rect(0, 0, 200, 20)
    layout = layout_text("a\nb\nc", box, replace(STYLE, overflow="wrap"), M)
    assert [r.text for r in layout.runs] == ["a", "b", "c"]
    assert layout.runs[0].size < 10


def test_comb_places_one_character_per_cell():
    comb = Comb((CombGroup(0, 90, 9),))
    runs = layout_text("123456789", Rect(0, 0, 90, 12), STYLE, M, comb).runs
    centres = [r.x + width(r.text) / 2 for r in runs]
    assert centres == pytest.approx([5 + 10 * i for i in range(9)])


def test_comb_groups_and_alignment():
    comb = Comb((CombGroup(0, 30, 3), CombGroup(40, 20, 2)))
    runs = layout_text("12345", Rect(0, 0, 60, 12), STYLE, M, comb).runs
    centres = [r.x + width(r.text) / 2 for r in runs]
    assert centres == pytest.approx([5, 15, 25, 45, 55])
    right = layout_text("12", Rect(0, 0, 60, 12), replace(STYLE, align="right"), M, comb).runs
    assert [r.x + width(r.text) / 2 for r in right] == pytest.approx([45, 55])


def test_comb_overflow():
    with pytest.raises(FieldError) as e:
        layout_text("1234", Rect(0, 0, 30, 12), STYLE, M, Comb((CombGroup(0, 30, 3),)))
    assert e.value.code == "COMB_OVERFLOW"
