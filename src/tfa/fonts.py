"""Font metrics. Just a thin wrapper over reportlab's tables."""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .model import AnnotationError

# reportlab has ascent/descent for the base-14 fonts but not cap height,
# these are from the Adobe AFM files (per 1000 em)
_CAP_HEIGHT = {
    "Helvetica": 718,
    "Helvetica-Bold": 718,
    "Helvetica-Oblique": 718,
    "Helvetica-BoldOblique": 718,
    "Courier": 562,
    "Courier-Bold": 562,
    "Courier-Oblique": 562,
    "Courier-BoldOblique": 562,
    "Times-Roman": 662,
    "Times-Bold": 676,
    "Times-Italic": 653,
    "Times-BoldItalic": 669,
}

STANDARD_FONTS = frozenset(_CAP_HEIGHT)


class FontMetrics:
    def __init__(self, custom_fonts: dict[str, Path] | None = None):
        self.known = set(STANDARD_FONTS)
        for name, path in (custom_fonts or {}).items():
            if name not in pdfmetrics.getRegisteredFontNames():
                try:
                    pdfmetrics.registerFont(TTFont(name, str(path)))
                except Exception as e:  # TTFError, OSError, whatever reportlab feels like
                    raise AnnotationError([f"font {name}: cannot load {path}: {e}"]) from e
            self.known.add(name)

    def is_known(self, font: str) -> bool:
        return font in self.known

    def width(self, text: str, font: str, size: float) -> float:
        return pdfmetrics.stringWidth(text, font, size)

    def ascent(self, font: str) -> float:
        return pdfmetrics.getFont(font).face.ascent / 1000

    def descent(self, font: str) -> float:
        # negative (below baseline)
        return pdfmetrics.getFont(font).face.descent / 1000

    def cap_height(self, font: str) -> float:
        if font in _CAP_HEIGHT:
            return _CAP_HEIGHT[font] / 1000
        face = pdfmetrics.getFont(font).face
        return getattr(face, "capHeight", 0.7 * face.ascent) / 1000
