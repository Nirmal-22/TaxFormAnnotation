import re
from decimal import Decimal

import pytest

from tfa import jsonpath

DATA = {
    "taxpayer": {"name": {"first": "Jordan"}, "ssn": "400-00-1001"},
    "documents": {
        "w2": [{"boxes": {"1": Decimal("100.10")}}, {"boxes": {"1": Decimal("50")}}],
        "1099": [
            {"form": "INT", "boxes": {"1": 10}},
            {"form": "DIV", "boxes": {"1a": 20}},
            {"form": "INT", "boxes": {"1": 5}, "corrected": False},
        ],
    },
    "weird key": {"a.b": 1},
}


def q(path, current=None):
    return jsonpath.parse(path).evaluate(DATA, current)


def test_member_and_index_access():
    assert q("$.taxpayer.name.first") == ["Jordan"]
    assert q("$['taxpayer']['ssn']") == ["400-00-1001"]
    assert q("$.documents.w2[1].boxes['1']") == [Decimal("50")]
    assert q("$.documents.w2[-1].boxes['1']") == [Decimal("50")]


def test_bracket_names_allow_any_key():
    assert q("$['weird key']['a.b']") == [1]
    assert q('$["weird key"]["a.b"]') == [1]


def test_missing_values_select_nothing():
    assert q("$.taxpayer.middleName") == []
    assert q("$.documents.w2[5]") == []
    assert q("$.taxpayer.ssn.first") == []  # member of a string
    assert q("$.taxpayer[0]") == []  # index into an object


def test_wildcards():
    assert q("$.documents.w2[*].boxes['1']") == [Decimal("100.10"), Decimal("50")]
    assert q("$.taxpayer.name.*") == ["Jordan"]


def test_filters():
    assert q("$.documents['1099'][?@.form == 'INT'].boxes['1']") == [10, 5]
    assert q("$.documents['1099'][?@.form != 'INT'].form") == ["DIV"]
    assert q("$.documents['1099'][?@.boxes['1'] > 7].form") == ["INT"]
    assert q("$.documents['1099'][?@.corrected].form") == ["INT"]  # existence, even though false
    assert q("$.documents['1099'][?@.corrected == false].form") == ["INT"]


def test_relative_paths_use_the_current_item():
    assert q("@.boxes['1']", current={"boxes": {"1": 7}}) == [7]


def test_singular():
    assert jsonpath.parse("$.a.b[0]").singular
    assert not jsonpath.parse("$.a[*]").singular
    assert not jsonpath.parse("$.a[?@.b == 1]").singular


@pytest.mark.parametrize(
    "path, message",
    [
        ("taxpayer.ssn", "must start with $ or @"),
        ("$..ssn", "descendant segments"),
        ("$.a[", "expected a quoted name"),
        ("$.a['b", "unterminated string"),
        ("$.a[?@.b[*] == 1]", "not allowed in filter paths"),
        ("$.a b", "unexpected character"),
    ],
)
def test_parse_errors_are_specific(path, message):
    with pytest.raises(jsonpath.PathError, match=re.escape(message)):
        jsonpath.parse(path)


def test_compare_semantics():
    assert jsonpath.compare(1, "==", Decimal("1.0"))
    assert jsonpath.compare("1961-01-01", "<", "1961-01-02")  # ISO dates order as strings
    assert not jsonpath.compare("10", "==", 10)  # no type coercion
    assert not jsonpath.compare("10", ">", 5)
    assert jsonpath.compare(True, "==", True)
    assert not jsonpath.compare(True, "==", 1)
    assert jsonpath.compare(None, "!=", "x")
