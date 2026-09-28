import pytest
from conftest import ROOT, annotation

from tfa import AnnotationError, lint, load_annotation

EXAMPLES = sorted(ROOT.glob("forms/*/*.tfa.json"))
RECT = {"x": 10, "y": 10, "width": 100, "height": 12}


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_example_annotations_are_clean(path):
    diagnostics = lint(load_annotation(path))
    assert diagnostics == []


def text(fid, path="$.x", **extra):
    return {"id": fid, "type": "text", "page": 1, "rect": RECT, "source": {"path": path}, **extra}


def lint_codes(fields, **kwargs):
    return sorted(d.code for d in lint(annotation(fields, **kwargs), check_pdf=False))


def test_schema_rejects_malformed_fields():
    with pytest.raises(AnnotationError) as e:
        annotation([{"id": "a", "type": "text", "page": 1, "rect": RECT}])  # no source
    assert any("source" in p for p in e.value.problems)
    with pytest.raises(AnnotationError):
        annotation([text("a") | {"colour": "red"}])  # unknown property
    with pytest.raises(AnnotationError):
        annotation([text("a", path="taxpayer.ssn")])  # path must start with $ or @
    with pytest.raises(AnnotationError):
        annotation([text("a") | {"source": {"path": "$.a", "literal": 1}}])  # two kinds of source
    with pytest.raises(AnnotationError):
        annotation([{"id": "c", "type": "checkbox", "page": 1, "rect": RECT, "checked": {"path": "$.a", "op": "eq"}}])


def test_schema_requires_page_at_top_level_only():
    with pytest.raises(AnnotationError):
        annotation([{k: v for k, v in text("a").items() if k != "page"}])
    child = text("child", "@.x")
    with pytest.raises(AnnotationError):
        annotation([{"id": "r", "type": "repeat", "page": 1, "source": {"path": "$.r"}, "maxItems": 2,
                     "offset": {"x": 0, "y": 12}, "fields": [child]}])  # child has a page


def test_duplicate_ids():
    assert lint_codes([text("a"), text("a") | {"rect": {"x": 10, "y": 40, "width": 10, "height": 10}}]) == ["DUPLICATE_ID"]


def test_page_and_bounds():
    assert lint_codes([text("a") | {"page": 3}]) == ["PAGE_OUT_OF_RANGE"]
    assert lint_codes([text("a") | {"rect": {"x": 600, "y": 10, "width": 50, "height": 12}}]) == ["RECT_OUT_OF_BOUNDS"]


def test_repeat_instances_are_bounds_checked():
    field = {"id": "r", "type": "repeat", "page": 1, "source": {"path": "$.r"}, "maxItems": 100,
             "offset": {"x": 0, "y": 12}, "fields": [{k: v for k, v in text("c", "@.x").items() if k != "page"}]}
    assert "RECT_OUT_OF_BOUNDS" in lint_codes([field])


def test_paths():
    assert lint_codes([text("a", path="$..deep")]) == ["BAD_PATH"]
    assert lint_codes([text("a", path="@.x")]) == ["RELATIVE_PATH"]
    assert lint_codes([text("a", path="$.w2[*].wages")]) == ["MULTI_VALUE_PATH"]
    assert lint_codes([text("a", path="$.w2[?@.owner == 'spouse'].wages")]) == ["MULTI_VALUE_PATH"]
    assert lint_codes([text("a") | {"source": {"path": "$.w2[*].wages", "aggregate": "sum"}}]) == []
    assert lint_codes([text("a") | {"source": {"template": "{$.a} {$..b}"}}]) == ["BAD_PATH"]


def test_font_that_cannot_be_loaded_is_an_annotation_error(tmp_path):
    bad = tmp_path / "bad.ttf"
    bad.write_bytes(b"not a font")
    doc = {
        "specVersion": "1.0",
        "form": {"id": "t", "title": "T", "source": {"file": "x.pdf", "sha256": "0" * 64}, "pages": [{"width": 612, "height": 792}]},
        "coordinates": {"unit": "pt", "origin": "top-left"},
        "fonts": {"Bad": {"file": "bad.ttf"}},
        "fields": [text("a", style={"font": "Bad"})],
    }
    from tfa import build_plan, parse_annotation

    a = parse_annotation(doc, base_dir=tmp_path)
    assert lint(a, check_pdf=False) == []  # the file exists, so lint is happy
    with pytest.raises(AnnotationError, match="cannot load"):
        build_plan(a, {"x": "1"})


def test_styles_and_fonts():
    assert lint_codes([text("a", style="nope")]) == ["BAD_STYLE"]
    assert lint_codes([text("a", style={"font": "Comic Sans"})]) == ["UNKNOWN_FONT"]
    assert lint_codes([text("a", style="x")], styles={"x": {"extends": "y"}, "y": {"extends": "x"}}) == [
        "BAD_STYLE", "BAD_STYLE", "BAD_STYLE"]


def test_overlap_and_comb_geometry():
    assert lint_codes([text("a"), text("b")]) == ["OVERLAP"]
    comb = {"groups": [{"x": 90, "width": 20, "cells": 2}]}
    assert lint_codes([text("a", comb=comb)]) == ["COMB_OUT_OF_RECT"]


def test_source_pdf_checks(blank):
    ok = annotation([text("a")], source=blank)
    assert lint(ok) == []
    wrong_size = annotation([text("a")], source=blank, pages=[(600, 800)])
    assert [d.code for d in lint(wrong_size)] == ["SOURCE_MISMATCH"]
    blank.write_bytes(blank.read_bytes() + b"\n% changed")
    assert [d.code for d in lint(ok)] == ["SOURCE_MISMATCH"]
