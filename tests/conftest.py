from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from reportlab.pdfgen.canvas import Canvas

from tfa.model import FormAnnotation, parse_annotation

ROOT = Path(__file__).resolve().parents[1]


def annotation(
    fields: list[dict[str, Any]],
    styles: dict[str, Any] | None = None,
    pages: list[tuple[float, float]] = [(612, 792)],
    source: Path | None = None,
) -> FormAnnotation:
    """Build an annotation from field dicts, as if loaded from a file."""
    sha = hashlib.sha256(source.read_bytes()).hexdigest() if source else "0" * 64
    doc = {
        "specVersion": "1.0",
        "form": {
            "id": "test-form",
            "title": "Test Form",
            "source": {"file": source.name if source else "missing.pdf", "sha256": sha},
            "pages": [{"width": w, "height": h} for w, h in pages],
        },
        "coordinates": {"unit": "pt", "origin": "top-left"},
        "styles": styles or {},
        "fields": fields,
    }
    # Round-trip through JSON so numbers behave exactly as when loaded from disk.
    doc = json.loads(json.dumps(doc), parse_float=Decimal)
    return parse_annotation(doc, base_dir=source.parent if source else Path("."))


def blank_pdf(path: Path, size: tuple[float, float] = (612, 792)) -> Path:
    c = Canvas(str(path), pagesize=size)
    c.showPage()
    c.save()
    return path


@pytest.fixture
def blank(tmp_path: Path) -> Path:
    return blank_pdf(tmp_path / "blank.pdf")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(), parse_float=Decimal)
