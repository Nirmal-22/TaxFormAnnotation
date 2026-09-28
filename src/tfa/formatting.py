"""Value -> the exact string that goes in the box."""

from __future__ import annotations

import datetime as dt
import re
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

from .model import DateFormat, Format, MaskFormat, NumberFormat, PercentFormat, TextFormat
from .values import FieldError, to_number, to_text


def format_value(value: Any, fmt: Format) -> str | None:
    # None = print nothing (zero: blank)
    if isinstance(fmt, TextFormat):
        s = to_text(value)
        if fmt.case == "upper":
            return s.upper()
        if fmt.case == "lower":
            return s.lower()
        return s
    if isinstance(fmt, NumberFormat):
        return format_number(to_number(value), fmt)
    if isinstance(fmt, PercentFormat):
        return format_percent(to_number(value), fmt)
    if isinstance(fmt, DateFormat):
        return format_date(value, fmt.pattern)
    if isinstance(fmt, MaskFormat):
        return format_mask(value, fmt.patterns)
    raise TypeError(fmt)


def round_half_up(value: Decimal, decimals: int) -> Decimal:
    # IRS rounding: .49 down, .50 up (away from zero). Not banker's rounding!
    return value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def _grouped(value: Decimal, decimals: int, thousands: bool) -> str:
    return f"{value:,.{decimals}f}" if thousands else f"{value:.{decimals}f}"


def format_number(value: Decimal, fmt: NumberFormat) -> str | None:
    if fmt.part != "whole":
        decimals = 2
    elif fmt.decimals is not None:
        decimals = fmt.decimals
    else:
        decimals = 2 if fmt.kind == "currency" else 0
    rounded = round_half_up(value, decimals)

    if rounded == 0:
        if fmt.zero == "blank":
            return None
        if fmt.zero == "dash":
            return "" if fmt.part == "cents" else "-0-"
        rounded = abs(rounded)  # -0.2 rounds to -0, nobody wants to see "-0"

    mag = abs(rounded)
    if fmt.part == "cents":
        return f"{mag % 1:.2f}"[2:]
    if fmt.part == "dollars":
        body = fmt.symbol + _grouped(mag.to_integral_value(rounding=ROUND_DOWN), 0, fmt.thousands)
    else:
        body = fmt.symbol + _grouped(mag, decimals, fmt.thousands)

    if rounded < 0:
        return f"({body})" if fmt.negative == "parens" else f"-{body}"
    return body


def format_percent(value: Decimal, fmt: PercentFormat) -> str:
    if fmt.input == "fraction":
        value = value * 100
    s = f"{round_half_up(value, fmt.decimals):.{fmt.decimals}f}"
    return s + "%" if fmt.symbol else s


_DATE_TOKENS = re.compile(r"YYYY|YY|MM|M|DD|D")


def parse_date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value[:10])  # drop any time part
        except ValueError:
            pass
    raise FieldError("INVALID_DATE", f"expected an ISO 8601 date (YYYY-MM-DD), got {value!r}")


def format_date(value: Any, pattern: str) -> str:
    d = parse_date(value)
    tok = {
        "YYYY": f"{d.year:04d}",
        "YY": f"{d.year % 100:02d}",
        "MM": f"{d.month:02d}",
        "M": str(d.month),
        "DD": f"{d.day:02d}",
        "D": str(d.day),
    }
    return _DATE_TOKENS.sub(lambda m: tok[m.group()], pattern)


def format_mask(value: Any, patterns: tuple[str, ...]) -> str:
    chars = [c for c in to_text(value) if c.isalnum()]
    for pat in patterns:
        if pat.count("#") == len(chars):
            it = iter(chars)
            return "".join(next(it) if p == "#" else p for p in pat)
    want = " or ".join(str(p.count("#")) for p in patterns)
    raise FieldError(
        "MASK_MISMATCH",
        f"value has {len(chars)} letters/digits but the mask expects {want}",
    )
