from decimal import Decimal

import pytest

from tfa.formatting import format_value
from tfa.model import DateFormat, MaskFormat, NumberFormat, PercentFormat, TextFormat
from tfa.values import FieldError

USD = NumberFormat(kind="currency")
WHOLE = NumberFormat(kind="currency", decimals=0, negative="parens")


@pytest.mark.parametrize(
    "value, fmt, expected",
    [
        (Decimal("1234.5"), USD, "1,234.50"),
        (1234567, WHOLE, "1,234,567"),
        (Decimal("0.49"), WHOLE, "0"),
        (Decimal("0.50"), WHOLE, "1"),  # IRS rounding: 50 cents rounds up
        (Decimal("-1250.50"), WHOLE, "(1,251)"),  # ... away from zero
        (2.675, USD, "2.68"),  # floats are read as decimals, not binary
        ("1,050.25", USD, "1,050.25"),  # numeric strings are accepted
        (Decimal("-3"), NumberFormat(), "-3"),
        (1000, NumberFormat(thousands=False), "1000"),
        (1000, NumberFormat(kind="currency", decimals=0, symbol="$"), "$1,000"),
        (-1000, NumberFormat(kind="currency", decimals=0, symbol="$", negative="parens"), "($1,000)"),
        (Decimal("-0.2"), WHOLE, "0"),  # never -0
    ],
)
def test_numbers(value, fmt, expected):
    assert format_value(value, fmt) == expected


def test_zero_policies():
    assert format_value(0, NumberFormat(zero="show")) == "0"
    assert format_value(0, NumberFormat(zero="blank")) is None
    assert format_value(Decimal("0.3"), NumberFormat(zero="dash")) == "-0-"


def test_dollars_and_cents_parts():
    dollars = NumberFormat(kind="currency", part="dollars")
    cents = NumberFormat(kind="currency", part="cents")
    assert format_value(Decimal("12345.678"), dollars) == "12,345"
    assert format_value(Decimal("12345.678"), cents) == "68"
    assert format_value(Decimal("12345.995"), dollars) == "12,346"  # cents carried into dollars
    assert format_value(Decimal("12345.995"), cents) == "00"
    assert format_value(Decimal("-7.5"), NumberFormat(kind="currency", part="dollars", negative="parens")) == "(7)"  # dollars carry the sign


def test_percent():
    assert format_value(Decimal("0.2575"), PercentFormat(decimals=1)) == "25.8%"
    assert format_value(40, PercentFormat(input="percent", symbol=False)) == "40"


def test_dates():
    assert format_value("2025-04-15", DateFormat()) == "04/15/2025"
    assert format_value("2025-04-05T10:00:00Z", DateFormat("M/D/YY")) == "4/5/25"
    assert format_value("2025-04-15", DateFormat("YYYY")) == "2025"
    with pytest.raises(FieldError) as e:
        format_value("15/04/2025", DateFormat())
    assert e.value.code == "INVALID_DATE"


def test_masks():
    assert format_value("400001001", MaskFormat(("###-##-####",))) == "400-00-1001"
    assert format_value("415.555.0142", MaskFormat(("(###) ###-####",))) == "(415) 555-0142"
    zip_mask = MaskFormat(("#####", "#####-####"))
    assert format_value("94102", zip_mask) == "94102"
    assert format_value("941021234", zip_mask) == "94102-1234"
    with pytest.raises(FieldError) as e:
        format_value("9410", zip_mask)
    assert e.value.code == "MASK_MISMATCH"


def test_text():
    assert format_value("Rivera", TextFormat(case="upper")) == "RIVERA"
    assert format_value(Decimal("12.50"), TextFormat()) == "12.50"
    assert format_value(Decimal("100"), TextFormat()) == "100"


def test_type_mismatches_are_errors():
    with pytest.raises(FieldError) as e:
        format_value("abc", USD)
    assert e.value.code == "TYPE_MISMATCH"
    with pytest.raises(FieldError):
        format_value(True, USD)  # booleans are not numbers
    with pytest.raises(FieldError):
        format_value(True, TextFormat())  # nor text; use a map transform
