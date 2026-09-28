from decimal import Decimal

import pytest

from tfa.model import (
    AllOf,
    AnyOf,
    CoalesceSource,
    Comparison,
    LiteralSource,
    Not,
    PathSource,
    TemplateSource,
    Transform,
)
from tfa.values import MISSING, Context, FieldError, evaluate, resolve

DATA = {
    "status": "married_filing_jointly",
    "primary": {"first": "Jordan", "mi": "A", "last": "Rivera", "ssn": "400-00-1001", "dob": "1958-11-03"},
    "spouse": {"first": "Sam", "last": "Rivera"},
    "w2": [{"wages": Decimal("100.25")}, {"wages": 50}, {"wages": None}],
    "dependents": [{"name": "Maya"}, {"name": "Leo"}],
    "flags": {"blind": False, "zero": 0},
}
CTX = Context(DATA)


def test_path_resolves_a_single_value():
    assert resolve(PathSource(path="$.primary.first"), CTX) == "Jordan"
    assert resolve(PathSource(path="$.primary.middle"), CTX) is MISSING


def test_multiple_values_need_an_aggregate():
    with pytest.raises(FieldError) as e:
        resolve(PathSource(path="$.w2[*].wages"), CTX)
    assert e.value.code == "AMBIGUOUS_VALUE"


@pytest.mark.parametrize(
    "aggregate, expected",
    [
        ("sum", Decimal("150.25")),
        ("count", 2),  # nulls are not counted
        ("min", Decimal("50")),
        ("max", Decimal("100.25")),
        ("first", Decimal("100.25")),
        ("last", 50),
        ("join", "100.25, 50"),
    ],
)
def test_aggregates(aggregate, expected):
    assert resolve(PathSource(path="$.w2[*].wages", aggregate=aggregate), CTX) == expected


def test_aggregate_of_nothing_is_missing_except_count():
    assert resolve(PathSource(path="$.nope[*]", aggregate="sum"), CTX) is MISSING
    assert resolve(PathSource(path="$.nope[*]", aggregate="count"), CTX) == 0


def test_template_tidies_missing_parts():
    t = TemplateSource(template="{$.primary.first} {$.primary.middle} {$.primary.last}")
    assert resolve(t, CTX) == "Jordan Rivera"
    assert resolve(TemplateSource(template="{$.nope} {$.nope2}"), CTX) is MISSING
    assert resolve(TemplateSource(template="{{literal}} {$.primary.mi}"), CTX) == "{literal} A"


def test_template_keeps_line_breaks():
    t = TemplateSource(template="{$.primary.first}\n{$.nope}\n{$.primary.last}")
    assert resolve(t, CTX) == "Jordan\nRivera"


def test_coalesce_and_default():
    s = CoalesceSource(sources=(PathSource(path="$.primary.nickname"), PathSource(path="$.primary.first")))
    assert resolve(s, CTX) == "Jordan"
    assert resolve(PathSource(path="$.primary.nickname", default="N/A"), CTX) == "N/A"
    assert resolve(LiteralSource(literal="See attached"), CTX) == "See attached"


def test_transforms():
    ssn = PathSource(path="$.primary.ssn", transform=(Transform("digits"), Transform("slice", start=0, end=3)))
    assert resolve(ssn, CTX) == "400"
    status = PathSource(path="$.status", transform=(Transform("map", values={"married_filing_jointly": "MFJ"}),))
    assert resolve(status, CTX) == "MFJ"
    boolean = PathSource(path="$.flags.blind", transform=(Transform("map", values={"true": "Yes", "false": "No"}),))
    assert resolve(boolean, CTX) == "No"
    assert resolve(LiteralSource(literal=-5, transform=(Transform("abs"),)), CTX) == 5


def test_map_without_match_is_an_error():
    s = PathSource(path="$.status", transform=(Transform("map", values={"single": "S"}),))
    with pytest.raises(FieldError, match="no mapping"):
        resolve(s, CTX)


def test_relative_path_outside_repeat_is_an_error():
    with pytest.raises(FieldError) as e:
        resolve(PathSource(path="@.name"), CTX)
    assert e.value.code == "RELATIVE_PATH"
    assert resolve(PathSource(path="@.name"), Context(DATA, {"name": "Leo"}, in_repeat=True)) == "Leo"


def cmp(path, op, value=None):
    return Comparison(PathSource(path=path), op, value)


def test_conditions():
    assert evaluate(cmp("$.status", "eq", "married_filing_jointly"), CTX)
    assert evaluate(cmp("$.status", "in", ["single", "married_filing_jointly"]), CTX)
    assert evaluate(cmp("$.primary.dob", "lt", "1961-01-02"), CTX)
    assert not evaluate(cmp("$.spouse.dob", "lt", "1961-01-02"), CTX)  # missing -> false
    assert evaluate(cmp("$.spouse.dob", "ne", "1961-01-02"), CTX)  # missing -> ne is true
    assert evaluate(cmp("$.dependents[*]", "exists"), CTX)
    assert evaluate(cmp("$.w2[*].wages", "truthy"), CTX)
    assert evaluate(cmp("$.flags.blind", "falsy"), CTX)
    assert evaluate(cmp("$.flags.zero", "falsy"), CTX)
    assert evaluate(Not(cmp("$.flags.blind", "truthy")), CTX)
    assert evaluate(AllOf((cmp("$.status", "exists"), cmp("$.primary.first", "eq", "Jordan"))), CTX)
    assert evaluate(AnyOf((cmp("$.nope", "exists"), cmp("$.primary.first", "exists"))), CTX)


def test_condition_on_an_aggregate():
    count = PathSource(path="$.dependents[*]", aggregate="count")
    assert evaluate(Comparison(count, "gt", 1), CTX)
    assert not evaluate(Comparison(count, "gt", 4), CTX)
