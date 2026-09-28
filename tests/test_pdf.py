"""End-to-end rendering, including the coordinate contract on unusual pages."""

import pypdfium2 as pdfium
import pytest
from conftest import ROOT, annotation, blank_pdf, load_json
from pypdf import PdfReader, PdfWriter

from tfa import build_plan, load_annotation, render_pdf
from tfa.bootstrap import bootstrap
from tfa.pdf import SourceMismatch, render_grid


def dark(image, x, y):
    """Is the pixel at (x, y) (in points, top-left origin, scale 1) dark?"""
    return sum(image.getpixel((int(x), int(y)))[:3]) < 200


def fill_box(rect):
    return {"id": "box", "type": "checkbox", "page": 1, "rect": rect, "checked": {"path": "$.on", "op": "truthy"},
            "style": {"mark": "fill", "markInset": 0}}


def render_image(tmp_path, a, data):
    out = tmp_path / "out.pdf"
    render_pdf(a, build_plan(a, data), out)
    return pdfium.PdfDocument(str(out))[0].render(scale=1).to_pil().convert("RGB")


def test_top_left_origin(tmp_path, blank):
    a = annotation([fill_box({"x": 100, "y": 50, "width": 20, "height": 20})], source=blank)
    image = render_image(tmp_path, a, {"on": True})
    assert dark(image, 110, 60)
    assert not dark(image, 110, 792 - 60)  # not measured from the bottom
    assert not dark(image, 130, 60)


def test_rotated_page_uses_displayed_orientation(tmp_path):
    src = blank_pdf(tmp_path / "rotated.pdf")
    writer = PdfWriter(clone_from=PdfReader(src))
    writer.pages[0].rotate(90)
    writer.write(src)
    # Displayed landscape: 792 wide, 612 tall.
    a = annotation([fill_box({"x": 700, "y": 20, "width": 20, "height": 20})], source=src, pages=[(792, 612)])
    image = render_image(tmp_path, a, {"on": True})
    assert image.size == (792, 612)
    assert dark(image, 710, 30)
    assert not dark(image, 30, 30)


def test_offset_crop_box(tmp_path):
    src = blank_pdf(tmp_path / "offset.pdf", size=(700, 900))
    writer = PdfWriter(clone_from=PdfReader(src))
    page = writer.pages[0]
    page.cropbox.lower_left = (50, 60)
    page.cropbox.upper_right = (662, 852)
    writer.write(src)
    a = annotation([fill_box({"x": 0, "y": 0, "width": 20, "height": 20})], source=src, pages=[(612, 792)])
    image = render_image(tmp_path, a, {"on": True})
    assert image.size == (612, 792)
    assert dark(image, 5, 5)  # (0, 0) is the visible top-left corner


def test_source_mismatch_is_refused(tmp_path, blank):
    a = annotation([fill_box({"x": 0, "y": 0, "width": 10, "height": 10})], source=blank)
    blank.write_bytes(blank.read_bytes() + b"\n% different revision")
    with pytest.raises(SourceMismatch):
        render_pdf(a, build_plan(a, {"on": True}), tmp_path / "out.pdf")


def test_1040_example_end_to_end(tmp_path):
    a = load_annotation(ROOT / "forms/irs-1040-2025/f1040.tfa.json")
    plan = build_plan(a, load_json(ROOT / "examples/data/rivera-2025.json"))
    assert plan.errors == []
    out = tmp_path / "f1040.pdf"
    render_pdf(a, plan, out)
    reader = PdfReader(out)
    assert len(reader.pages) == 3  # two form pages plus the dependents statement
    assert "/AcroForm" not in reader.trailer["/Root"]  # the form's own fields are removed
    page1 = reader.pages[0].extract_text()
    for expected in ("Jordan A", "Rivera-Okafor", "229,671", "(1,250)", "94102-1234"):
        assert expected in page1
    assert "Theodore" in reader.pages[2].extract_text()


def test_w9_example_end_to_end(tmp_path):
    a = load_annotation(ROOT / "forms/irs-w9-2024/fw9.tfa.json")
    plan = build_plan(a, load_json(ROOT / "examples/data/northwind-w9.json"))
    assert plan.errors == []
    render_pdf(a, plan, tmp_path / "fw9.pdf", debug=True)
    text = PdfReader(tmp_path / "fw9.pdf").pages[0].extract_text()
    assert "Northwind Analytics LLC" in text and "09/26/2025" in text


def test_plan_on_a_missing_page_is_refused(tmp_path, blank):
    a = annotation([fill_box({"x": 0, "y": 0, "width": 10, "height": 10}) | {"page": 2}], source=blank, pages=[(612, 792), (612, 792)])
    plan = build_plan(a, {"on": True})
    with pytest.raises(SourceMismatch, match="page"):
        render_pdf(a, plan, tmp_path / "out.pdf")


def test_grid(tmp_path, blank):
    render_grid(blank, tmp_path / "grid.pdf")
    assert "100" in PdfReader(tmp_path / "grid.pdf").pages[0].extract_text()


def test_bootstrap_from_irs_1040():
    doc = bootstrap(ROOT / "forms/irs-1040-2025/f1040.pdf", "irs-1040-2025")
    by_id = {f["id"]: f for f in doc["fields"]}
    filing_status = by_id["c1_8"]
    assert filing_status["type"] == "choice" and len(filing_status["options"]) == 5
    assert by_id["f1_16"]["comb"] == {"cells": 9}  # taxpayer SSN
    assert by_id["f1_47"]["style"] == {"align": "right"}  # line 1a
    assert by_id["f1_47"]["rect"] == {"x": 504.0, "y": 450.0, "width": 72.0, "height": 12.0}
    # The draft is itself a valid annotation.
    from tfa import parse_annotation
    parse_annotation(doc)


def test_bootstrap_on_a_rotated_page(tmp_path):
    from reportlab.pdfgen.canvas import Canvas

    src = tmp_path / "form.pdf"
    c = Canvas(str(src), pagesize=(612, 792))
    c.acroForm.textfield(name="f", x=100, y=700, width=50, height=20)
    c.showPage()
    c.save()
    writer = PdfWriter(clone_from=PdfReader(src))
    writer.pages[0].rotate(90)
    writer.write(src)
    [field] = bootstrap(src, "t")["fields"]
    # Unrotated top-left (100, 72) becomes displayed (792 - 72 - 20, 100) after a clockwise turn.
    assert field["rect"] == {"x": 700.0, "y": 100.0, "width": 20.0, "height": 50.0}
