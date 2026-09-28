"""Tax Form Annotation.

    from tfa import load_annotation, build_plan, render_pdf

    ann = load_annotation("forms/irs-1040-2025/f1040.tfa.json")
    plan = build_plan(ann, data)
    if not plan.errors:
        render_pdf(ann, plan, "out.pdf")
"""

from .lint import lint
from .model import AnnotationError, FormAnnotation, load_annotation, parse_annotation
from .pdf import render_pdf
from .plan import Diagnostic, RenderPlan, build_plan

__all__ = [
    "AnnotationError",
    "Diagnostic",
    "FormAnnotation",
    "RenderPlan",
    "build_plan",
    "lint",
    "load_annotation",
    "parse_annotation",
    "render_pdf",
]
