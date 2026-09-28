"""Draw a plan onto the blank PDF.

Per page: draw the ops on a reportlab canvas, merge that over the original
page with pypdf. TFA coords are top-left/y-down, PDF is bottom-left/y-up, the
flip happens in to_pdf() and nowhere else. Rotated pages get their rotation
baked into the content first so "top-left" means what you see on screen.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Callable

from pypdf import PageObject, PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.pdfgen.canvas import Canvas

from .fonts import FontMetrics
from .layout import wrap_lines
from .model import FormAnnotation
from .plan import FieldBox, MarkOp, RenderPlan, Statement, TextOp


class SourceMismatch(Exception):
    """Wrong PDF for this annotation."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_source(annotation: FormAnnotation, reader: PdfReader | None = None) -> list[str]:
    path = annotation.source_pdf
    if not path.exists():
        return [f"source PDF not found: {path}"]
    problems = []
    actual = sha256_file(path)
    if actual != annotation.form.source_sha256:
        problems.append(f"sha256 of {path.name} is {actual}, annotation expects {annotation.form.source_sha256}")
    reader = reader or PdfReader(path)
    if len(reader.pages) != len(annotation.form.pages):
        problems.append(f"PDF has {len(reader.pages)} pages, annotation declares {len(annotation.form.pages)}")
    for i, (page, size) in enumerate(zip(reader.pages, annotation.form.pages), start=1):
        w, h = display_size(page)
        if abs(w - size.width) > 0.01 or abs(h - size.height) > 0.01:
            problems.append(f"page {i} is {w:g}x{h:g}pt, annotation declares {size.width:g}x{size.height:g}pt")
    return problems


def display_size(page: PageObject) -> tuple[float, float]:
    w, h = float(page.cropbox.width), float(page.cropbox.height)
    return (h, w) if page.rotation % 180 else (w, h)


def render_pdf(
    annotation: FormAnnotation,
    plan: RenderPlan,
    output: str | Path,
    *,
    debug: bool = False,
    verify_source: bool = True,
    keep_form_fields: bool = False,
) -> None:
    """debug=True outlines every box (blue printed / grey empty / dashed hidden / red error).
    keep_form_fields=False strips the PDF's own widgets so viewers can't paint them over our text."""
    reader = PdfReader(annotation.source_pdf)
    if verify_source:
        problems = check_source(annotation, reader)
        if problems:
            raise SourceMismatch("; ".join(problems))

    writer = PdfWriter(clone_from=reader)
    beyond = sorted({op.page for op in plan.ops if op.page > len(writer.pages)})
    if beyond:
        raise SourceMismatch(f"the plan draws on page(s) {beyond} but the PDF has {len(writer.pages)} page(s)")
    for n, page in enumerate(writer.pages, start=1):
        ops = plan.ops_for_page(n)
        boxes = [b for b in plan.boxes if b.page == n] if debug else []
        if ops or boxes:
            _draw_on(page, lambda c, to_pdf: _draw_page(c, to_pdf, ops, boxes))

    if not keep_form_fields:
        _remove_form_fields(writer)

    if plan.statements:
        w, h = display_size(writer.pages[0])
        for p in PdfReader(io.BytesIO(_statement_pages(annotation, plan.statements, w, h))).pages:
            writer.add_page(p)

    with open(output, "wb") as f:
        writer.write(f)


def render_grid(pdf: str | Path, output: str | Path, step: float = 10) -> None:
    """Labelled grid in TFA coords, for measuring boxes by hand."""
    writer = PdfWriter(clone_from=PdfReader(pdf))
    for page in writer.pages:
        _draw_on(page, lambda c, to_pdf, page=page: _draw_grid(c, to_pdf, *display_size(page), step))
    with open(output, "wb") as f:
        writer.write(f)


ToPdf = Callable[[float, float], tuple[float, float]]


def _draw_on(page: PageObject, draw: Callable[[Canvas, ToPdf], None]) -> None:
    if page.rotation % 360:
        page.transfer_rotation_to_content()
    left, top = float(page.cropbox.left), float(page.cropbox.top)

    def to_pdf(x, y):
        return left + x, top - y

    buf = io.BytesIO()
    c = Canvas(buf, pagesize=(float(page.mediabox.right), float(page.mediabox.top)))
    draw(c, to_pdf)
    c.showPage()
    c.save()
    page.merge_page(PdfReader(buf).pages[0])


def _draw_page(c: Canvas, to_pdf: ToPdf, ops, boxes) -> None:
    for op in ops:
        if isinstance(op, TextOp):
            r = op.run
            c.setFont(r.font, r.size)
            c.setFillColor(HexColor(r.color))
            c.drawString(*to_pdf(r.x, r.baseline), r.text)
        else:
            _draw_mark(c, to_pdf, op)
    for b in boxes:
        _draw_debug_box(c, to_pdf, b)


def _draw_mark(c: Canvas, to_pdf: ToPdf, op: MarkOp) -> None:
    r = op.rect
    side = max(min(r.width, r.height) - 2 * op.inset, 1)
    x0 = r.x + (r.width - side) / 2
    y0 = r.y + (r.height - side) / 2
    col = HexColor(op.color)
    c.setStrokeColor(col)
    c.setFillColor(col)
    c.setLineWidth(max(0.6, side * 0.12))
    c.setLineCap(1)
    c.setLineJoin(1)

    def pt(fx, fy):
        # fractions of the mark square -> pdf coords
        return to_pdf(x0 + fx * side, y0 + fy * side)

    if op.mark == "cross":
        c.line(*pt(0, 0), *pt(1, 1))
        c.line(*pt(0, 1), *pt(1, 0))
    elif op.mark == "check":
        p = c.beginPath()
        p.moveTo(*pt(0.05, 0.55))
        p.lineTo(*pt(0.4, 0.9))
        p.lineTo(*pt(0.95, 0.1))
        c.drawPath(p, stroke=1, fill=0)
    elif op.mark == "dot":
        cx, cy = pt(0.5, 0.5)
        c.circle(cx, cy, side * 0.3, stroke=0, fill=1)
    else:  # fill
        x, y = pt(0, 1)
        c.rect(x, y, side, side, stroke=0, fill=1)


_DEBUG_COLORS = {"printed": "#1f6feb", "empty": "#8b949e", "hidden": "#c0c4c8", "error": "#d1242f"}


def _draw_debug_box(c: Canvas, to_pdf: ToPdf, box: FieldBox) -> None:
    col = HexColor(_DEBUG_COLORS[box.status])
    r = box.rect
    x, y = to_pdf(r.x, r.bottom)
    c.setStrokeColor(col)
    c.setFillColor(col)
    c.setLineWidth(0.5 if box.status == "error" else 0.35)
    if box.status == "hidden":
        c.setDash(1.5, 1.5)
    c.rect(x, y, r.width, r.height, stroke=1, fill=0)
    c.setDash()
    c.setFont("Helvetica", 3.2)  # tiny, but readable when zoomed and doesn't cover the value
    c.drawString(*to_pdf(r.x + 0.5, r.y + 3.4), box.field)


def _draw_grid(c: Canvas, to_pdf: ToPdf, width: float, height: float, step: float) -> None:
    c.setFont("Helvetica", 4)
    n = 0
    x = 0.0
    while x <= width:
        major = n % 5 == 0
        c.setStrokeColor(HexColor("#d1242f" if major else "#f2a0a6"))
        c.setLineWidth(0.3 if major else 0.15)
        c.line(*to_pdf(x, 0), *to_pdf(x, height))
        if major:
            c.setFillColor(HexColor("#d1242f"))
            c.drawString(*to_pdf(x + 1, 5), f"{x:g}")
        x += step
        n += 1
    n = 0
    y = 0.0
    while y <= height:
        major = n % 5 == 0
        c.setStrokeColor(HexColor("#1f6feb" if major else "#9ec2f7"))
        c.setLineWidth(0.3 if major else 0.15)
        c.line(*to_pdf(0, y), *to_pdf(width, y))
        if major and y > 0:
            c.setFillColor(HexColor("#1f6feb"))
            c.drawString(*to_pdf(1, y - 1), f"{y:g}")
        y += step
        n += 1
    # coords at every major crossing too, otherwise a zoomed-in crop is unreadable
    c.setFont("Helvetica", 3)
    c.setFillColor(HexColor("#6e40c9"))
    major = step * 5
    for i in range(1, int(width // major) + 1):
        for j in range(1, int(height // major) + 1):
            c.drawString(*to_pdf(i * major + 0.8, j * major - 0.8), f"{i * major:g},{j * major:g}")


def _remove_form_fields(writer: PdfWriter) -> None:
    writer.remove_annotations(subtypes="/Widget")
    root = writer.root_object
    if "/AcroForm" in root:
        del root["/AcroForm"]  # takes the XFA with it


# ---- continuation statements


def _statement_pages(annotation: FormAnnotation, statements: list[Statement], width: float, height: float) -> bytes:
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=(width, height))
    metrics = FontMetrics()
    margin = 54.0
    form = annotation.form
    subtitle = " ".join(
        str(p) for p in (form.issuer, f"Form {form.form_number}" if form.form_number else None, form.tax_year) if p
    )
    for st in statements:

        def header(cont: bool) -> float:
            c.setFillColor(HexColor("#000000"))
            c.setFont("Helvetica-Bold", 12)
            c.drawString(margin, height - margin, st.title + (" (cont.)" if cont else ""))
            c.setFont("Helvetica", 9)
            c.drawString(margin, height - margin - 14, f"Attachment to {subtitle}".strip())
            return margin + 40

        y = header(False)
        col_w = (width - 2 * margin) / max(len(st.columns), 1)
        size, leading = 8.0, 9.5

        def wrapped(cells, font):
            # wrap, don't truncate - an attachment has to show the whole value
            return [wrap_lines(t, font, size, col_w - 4, metrics) for t in cells]

        def row(cells, bold, y):
            font = "Helvetica-Bold" if bold else "Helvetica"
            lines = wrapped(cells, font)
            row_h = max(len(ls) for ls in lines) * leading + 4
            c.setFillColor(HexColor("#000000"))
            c.setFont(font, size)
            for i, cell_lines in enumerate(lines):
                for j, ln in enumerate(cell_lines):
                    c.drawString(margin + i * col_w + 2, height - y - 9 - j * leading, ln)
            c.setStrokeColor(HexColor("#999999"))
            c.setLineWidth(0.3)
            c.line(margin, height - y - row_h, width - margin, height - y - row_h)
            return y + row_h

        def row_height(cells):
            return max(len(ls) for ls in wrapped(cells, "Helvetica")) * leading + 4

        y = row(st.columns, True, y)
        for cells in st.rows:
            if y + row_height(cells) > height - margin:
                c.showPage()
                y = row(st.columns, True, header(True))
            y = row(cells, False, y)
        c.showPage()
    c.save()
    return buf.getvalue()
