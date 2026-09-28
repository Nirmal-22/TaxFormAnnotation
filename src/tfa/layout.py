"""Fit text into a rect. Everything here is TFA coords (pt, top-left, y down)
and a line is positioned by its baseline.

Vertical alignment goes by cap height, not the em box - most of what's on a
tax form is digits and capitals and they look off-centre otherwise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .fonts import FontMetrics
from .model import Comb, Rect
from .styles import ResolvedStyle
from .values import FieldError


@dataclass(frozen=True)
class TextRun:
    x: float
    baseline: float
    text: str
    font: str
    size: float
    color: str


@dataclass
class Layout:
    runs: list[TextRun]
    warnings: list[FieldError] = field(default_factory=list)


def inner_rect(rect: Rect, style: ResolvedStyle) -> Rect:
    p = style.padding
    return Rect(
        rect.x + p.left,
        rect.y + p.top,
        max(rect.width - p.left - p.right, 0),
        max(rect.height - p.top - p.bottom, 0),
    )


def layout_text(text, rect, style, metrics, comb=None) -> Layout:
    if comb is not None:
        return _comb(text, rect, style, metrics, comb)
    if style.overflow == "wrap":
        return _wrapped(text, rect, style, metrics)
    return _single_line(text.replace("\n", " "), rect, style, metrics)


def _baselines(n: int, inner: Rect, style: ResolvedStyle, metrics: FontMetrics, size: float) -> list[float]:
    cap = metrics.cap_height(style.font) * size
    step = style.lineHeight * size
    if style.valign == "top":
        first = inner.y + cap
    elif style.valign == "bottom":
        first = inner.bottom + metrics.descent(style.font) * size - (n - 1) * step
    else:
        # centre the block from first cap-top to last baseline
        block = cap + (n - 1) * step
        first = inner.y + (inner.height - block) / 2 + cap
    return [first + i * step for i in range(n)]


def _x(w: float, inner: Rect, align: str) -> float:
    if align == "right":
        return inner.right - w
    if align == "center":
        return inner.x + (inner.width - w) / 2
    return inner.x


def _single_line(text: str, rect: Rect, style: ResolvedStyle, metrics: FontMetrics) -> Layout:
    inner = inner_rect(rect, style)
    size = style.fontSize
    w = metrics.width(text, style.font, size)
    warnings = []
    if w > inner.width:
        if style.overflow == "shrink":
            # exact fit size, rounded DOWN to 0.1pt so it's guaranteed to fit
            fit = math.floor(size * inner.width / w * 10) / 10
            if fit < style.minFontSize:
                raise FieldError(
                    "TEXT_OVERFLOW",
                    f"{text!r} needs {w:.1f}pt at {size:g}pt but the box is "
                    f"{inner.width:.1f}pt wide; it would need {fit:g}pt "
                    f"(minFontSize {style.minFontSize:g})",
                )
            size = fit
            w = metrics.width(text, style.font, size)
        elif style.overflow == "truncate":
            orig = text
            while text and metrics.width(text, style.font, size) > inner.width:
                text = text[:-1]
            text = text.rstrip()
            w = metrics.width(text, style.font, size)
            warnings.append(FieldError("TEXT_TRUNCATED", f"{orig!r} was truncated to {text!r}"))
        else:
            raise FieldError(
                "TEXT_OVERFLOW",
                f"{text!r} needs {w:.1f}pt but the box is {inner.width:.1f}pt wide",
            )
    [baseline] = _baselines(1, inner, style, metrics, size)
    run = TextRun(_x(w, inner, style.align), baseline, text, style.font, size, style.color)
    return Layout([run], warnings)


def wrap_lines(text: str, font: str, size: float, max_width: float, metrics: FontMetrics) -> list[str]:
    """Greedy word wrap. Keeps explicit newlines, splits words that are too long on their own."""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            cand = f"{cur} {word}" if cur else word
            if metrics.width(cand, font, size) <= max_width:
                cur = cand
                continue
            if cur:
                lines.append(cur)
            cur = word
            while metrics.width(cur, font, size) > max_width and len(cur) > 1:
                cut = len(cur) - 1
                while cut > 1 and metrics.width(cur[:cut], font, size) > max_width:
                    cut -= 1
                lines.append(cur[:cut])
                cur = cur[cut:]
        lines.append(cur)
    return [ln for ln in lines if ln] or [""]


def _wrapped(text: str, rect: Rect, style: ResolvedStyle, metrics: FontMetrics) -> Layout:
    inner = inner_rect(rect, style)
    cap = metrics.cap_height(style.font)
    desc = metrics.descent(style.font)
    size = style.fontSize
    while True:
        lines = wrap_lines(text, style.font, size, inner.width, metrics)
        need = (cap - desc) * size + (len(lines) - 1) * style.lineHeight * size
        if need <= inner.height + 1e-6:
            break
        if size - 0.5 < style.minFontSize:
            raise FieldError(
                "TEXT_OVERFLOW",
                f"{len(lines)} lines need {need:.1f}pt of height but the box is "
                f"{inner.height:.1f}pt tall even at {size:g}pt",
            )
        size -= 0.5  # shrink a bit and re-wrap, fewer lines maybe
    baselines = _baselines(len(lines), inner, style, metrics, size)
    runs = [
        TextRun(_x(metrics.width(ln, style.font, size), inner, style.align), b, ln, style.font, size, style.color)
        for ln, b in zip(lines, baselines)
    ]
    return Layout(runs)


def _comb(text: str, rect: Rect, style: ResolvedStyle, metrics: FontMetrics, comb: Comb) -> Layout:
    chars = list(text.replace("\n", " "))
    cells = comb.cell_rects(rect)
    if len(chars) > len(cells):
        raise FieldError(
            "COMB_OVERFLOW", f"{text!r} has {len(chars)} characters but the box has {len(cells)} cells"
        )
    if style.align == "right":
        start = len(cells) - len(chars)
    elif style.align == "center":
        start = (len(cells) - len(chars)) // 2
    else:
        start = 0
    size = style.fontSize
    # horizontal padding is irrelevant for combs, vertical still applies
    [baseline] = _baselines(1, inner_rect(rect, style), style, metrics, size)
    runs = []
    for ch, cell in zip(chars, cells[start:]):
        if ch == " ":
            continue
        w = metrics.width(ch, style.font, size)
        runs.append(TextRun(cell.x + (cell.width - w) / 2, baseline, ch, style.font, size, style.color))
    return Layout(runs)
