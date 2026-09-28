import pytest
from conftest import annotation

from tfa.plan import MarkOp, TextOp, build_plan

RECT = {"x": 10, "y": 10, "width": 100, "height": 12}


def text(fid, path, **extra):
    return {"id": fid, "type": "text", "page": 1, "rect": RECT, "source": {"path": path}, **extra}


def printed(plan):
    out = {}
    for op in plan.ops:
        out.setdefault(op.field, []).append(op.run.text if isinstance(op, TextOp) else "X")
    return {k: "".join(v) for k, v in out.items()}


def codes(plan):
    return [d.code for d in plan.diagnostics]


def test_text_field_is_formatted_and_placed():
    a = annotation(
        [text("wages", "$.w2[*].wages", style="amount") | {"source": {"path": "$.w2[*].wages", "aggregate": "sum"}}],
        styles={"amount": {"align": "right", "format": {"type": "currency", "decimals": 0}}},
    )
    plan = build_plan(a, {"w2": [{"wages": 1000.4}, {"wages": 2000.2}]})
    assert printed(plan) == {"wages": "3,001"}
    assert plan.diagnostics == []


def test_missing_required_value_is_an_error():
    a = annotation([text("name", "$.name", required=True), text("nick", "$.nick")])
    plan = build_plan(a, {})
    assert codes(plan) == ["MISSING_REQUIRED"]
    assert plan.ops == []


def test_zero_blank_is_not_missing():
    a = annotation(
        [text("n", "$.n", required=True, style={"format": {"type": "number", "zero": "blank"}})]
    )
    plan = build_plan(a, {"n": 0})
    assert plan.diagnostics == [] and plan.ops == []


def test_errors_fail_closed():
    """A field with a bad value is not printed at all; others still are."""
    a = annotation(
        [
            text("amount", "$.amount", style={"format": {"type": "currency"}}),
            text("name", "$.name") | {"rect": {"x": 10, "y": 40, "width": 100, "height": 12}},
        ]
    )
    plan = build_plan(a, {"amount": "twelve", "name": "Jordan"})
    assert codes(plan) == ["TYPE_MISMATCH"]
    assert printed(plan) == {"name": "Jordan"}
    assert {b.field: b.status for b in plan.boxes} == {"amount": "error", "name": "printed"}


def test_when_hides_fields():
    a = annotation([text("spouse", "$.spouse", when={"path": "$.status", "op": "eq", "value": "mfj"})])
    assert printed(build_plan(a, {"status": "single", "spouse": "Sam"})) == {}
    assert printed(build_plan(a, {"status": "mfj", "spouse": "Sam"})) == {"spouse": "Sam"}


def test_checkbox():
    a = annotation(
        [{"id": "blind", "type": "checkbox", "page": 1, "rect": RECT, "checked": {"path": "$.blind", "op": "truthy"}}],
        styles={"default": {"mark": "check"}},
    )
    plan = build_plan(a, {"blind": True})
    [op] = plan.ops
    assert isinstance(op, MarkOp) and op.mark == "check"
    assert build_plan(a, {"blind": False}).ops == []


CHOICE = {
    "id": "status",
    "type": "choice",
    "page": 1,
    "source": {"path": "$.status"},
    "options": [
        {"value": "single", "rect": {"x": 10, "y": 10, "width": 8, "height": 8}},
        {"value": "mfj", "rect": {"x": 10, "y": 30, "width": 8, "height": 8}},
    ],
}


def test_choice_marks_the_matching_option():
    plan = build_plan(annotation([CHOICE]), {"status": "mfj"})
    [op] = plan.ops
    assert op.rect.y == 30


def test_choice_with_unknown_value_is_an_error():
    plan = build_plan(annotation([CHOICE]), {"status": "married"})
    assert codes(plan) == ["CHOICE_NO_MATCH"]
    assert plan.ops == []


def test_choice_with_list_marks_several():
    plan = build_plan(annotation([CHOICE]), {"status": ["single", "mfj"]})
    assert len(plan.ops) == 2


def repeat(**extra):
    return {
        "id": "deps",
        "type": "repeat",
        "page": 1,
        "label": "Dependents",
        "source": {"path": "$.deps"},
        "maxItems": 2,
        "offset": {"x": 0, "y": 20},
        "fields": [
            {"id": "name", "type": "text", "label": "Name", "rect": RECT, "source": {"path": "@.name"}},
            {"id": "student", "type": "checkbox", "label": "Student", "rect": {"x": 120, "y": 10, "width": 8, "height": 8},
             "checked": {"path": "@.student", "op": "truthy"}},
        ],
        **extra,
    }


def test_repeat_offsets_each_item():
    plan = build_plan(annotation([repeat()]), {"deps": [{"name": "Maya"}, {"name": "Leo", "student": True}]})
    runs = {op.field: op for op in plan.ops}
    assert printed(plan) == {"deps[0].name": "Maya", "deps[1].name": "Leo", "deps[1].student": "X"}
    assert runs["deps[1].name"].run.baseline - runs["deps[0].name"].run.baseline == pytest.approx(20)


def test_repeat_explicit_offsets():
    field = repeat(offsets=[{"x": 0, "y": 0}, {"x": 5, "y": 33}])
    del field["offset"]
    plan = build_plan(annotation([field]), {"deps": [{"name": "Maya"}, {"name": "Leo"}]})
    first, second = (op.run for op in plan.ops)
    assert (second.x - first.x, second.baseline - first.baseline) == pytest.approx((5, 33))


def test_repeat_overflow_error_prints_nothing():
    plan = build_plan(annotation([repeat()]), {"deps": [{"name": n} for n in "ABC"]})
    assert codes(plan) == ["REPEAT_OVERFLOW"]
    assert plan.ops == []


def test_repeat_overflow_truncate():
    plan = build_plan(annotation([repeat(overflow={"strategy": "truncate"})]), {"deps": [{"name": n} for n in "ABC"]})
    assert codes(plan) == ["REPEAT_TRUNCATED"]
    assert printed(plan) == {"deps[0].name": "A", "deps[1].name": "B"}


def test_repeat_overflow_statement():
    plan = build_plan(
        annotation([repeat(overflow={"strategy": "statement", "title": "Dependents (continued)"})]),
        {"deps": [{"name": "A"}, {"name": "B"}, {"name": "C", "student": True}, {"name": "D"}]},
    )
    assert codes(plan) == ["REPEAT_STATEMENT"]
    [st] = plan.statements
    assert st.title == "Dependents (continued)"
    assert st.columns == ["Name", "Student"]
    assert st.rows == [["C", "X"], ["D", ""]]


def test_repeat_source_must_be_a_list():
    plan = build_plan(annotation([repeat()]), {"deps": {"name": "A"}})
    assert codes(plan) == ["NOT_A_LIST"]


def test_map_to_null_leaves_the_box_empty():
    src = {"path": "$.kind", "transform": [{"op": "map", "values": {"none": None, "c": "C"}}]}
    a = annotation([text("k", "$.kind") | {"source": src}])
    assert printed(build_plan(a, {"kind": "none"})) == {}
    assert build_plan(a, {"kind": "none"}).diagnostics == []
    assert printed(build_plan(a, {"kind": "c"})) == {"k": "C"}
    assert codes(build_plan(a, {"kind": "x"})) == ["MAP_NO_MATCH"]


def test_plan_to_dict_is_json_serialisable():
    import json

    a = annotation([text("name", "$.name"), CHOICE | {"options": [o | {"rect": {"x": 200, "y": 10, "width": 8, "height": 8}} for o in CHOICE["options"][:1]]}])
    plan = build_plan(a, {"name": "Jordan", "status": "single"})
    doc = json.loads(json.dumps(plan.to_dict()))
    kinds = {op["kind"] for op in doc["ops"]}
    assert kinds == {"text", "mark"}
    text_op = next(op for op in doc["ops"] if op["kind"] == "text")
    assert text_op["text"] == "Jordan" and text_op["page"] == 1 and "baseline" in text_op
    mark_op = next(op for op in doc["ops"] if op["kind"] == "mark")
    assert mark_op["rect"] == {"x": 200, "y": 10, "width": 8, "height": 8}
    assert doc["statements"] == [] and doc["diagnostics"] == []
