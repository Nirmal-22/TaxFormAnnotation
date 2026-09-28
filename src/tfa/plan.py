"""annotation + data -> a list of things to draw, plus diagnostics.

No PDF library in here on purpose. A plan is just "put this string at
(x, baseline)" and "put a mark in this rect", which anything can draw.

Rule: if a field errors it doesn't get drawn at all. Half a value on a tax
form is worse than an empty box.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from . import jsonpath
from .fonts import FontMetrics
from .formatting import format_value
from .layout import TextRun, layout_text
from .model import CheckboxField, ChoiceField, Field, FormAnnotation, Rect, RepeatField, TextField
from .styles import ResolvedStyle, StyleResolver
from .values import Context, FieldError, evaluate, is_missing, json_key, resolve

Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    code: str
    field: str | None
    message: str

    def __str__(self) -> str:
        where = f" {self.field}" if self.field else ""
        return f"{self.severity.upper():7} {self.code}{where}: {self.message}"


@dataclass(frozen=True)
class TextOp:
    page: int
    field: str
    run: TextRun


@dataclass(frozen=True)
class MarkOp:
    page: int
    field: str
    rect: Rect
    mark: str
    inset: float
    color: str


@dataclass(frozen=True)
class FieldBox:
    # one per field instance, used by the debug overlay + explain
    page: int
    field: str
    rect: Rect
    status: Literal["printed", "empty", "hidden", "error"]


@dataclass
class Statement:
    # continuation sheet for repeat rows that didn't fit
    title: str
    columns: list[str]
    rows: list[list[str]]


@dataclass
class RenderPlan:
    ops: list[TextOp | MarkOp] = field(default_factory=list)
    boxes: list[FieldBox] = field(default_factory=list)
    statements: list[Statement] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    def ops_for_page(self, page: int) -> list[TextOp | MarkOp]:
        return [op for op in self.ops if op.page == page]

    def to_dict(self) -> dict[str, Any]:
        """JSON-able version, for people drawing the plan with something else."""
        ops: list[dict[str, Any]] = []
        for op in self.ops:
            if isinstance(op, TextOp):
                r = op.run
                ops.append({"kind": "text", "page": op.page, "field": op.field, "x": round(r.x, 3),
                            "baseline": round(r.baseline, 3), "text": r.text, "font": r.font,
                            "size": round(r.size, 3), "color": r.color})
            else:
                rect = {k: round(v, 3) for k, v in asdict(op.rect).items()}
                ops.append({"kind": "mark", "page": op.page, "field": op.field, "rect": rect,
                            "mark": op.mark, "inset": op.inset, "color": op.color})
        return {
            "ops": ops,
            "statements": [asdict(s) for s in self.statements],
            "diagnostics": [asdict(d) for d in self.diagnostics],
        }


@dataclass(frozen=True)
class _Scope:
    ctx: Context
    page: int
    dx: float = 0.0
    dy: float = 0.0
    prefix: str = ""  # "dependents[2]" etc

    def qualify(self, fid: str) -> str:
        return f"{self.prefix}.{fid}" if self.prefix else fid


class Planner:
    def __init__(self, annotation: FormAnnotation, metrics: FontMetrics | None = None):
        self.annotation = annotation
        self.metrics = metrics or FontMetrics(annotation.fonts)
        self.styles = StyleResolver(annotation.styles)

    def build(self, data: Any) -> RenderPlan:
        plan = RenderPlan()
        for f in self.annotation.fields:
            assert f.page is not None
            self._field(f, _Scope(Context(root=data), f.page), plan)
        return plan

    def _field(self, f: Field, scope: _Scope, plan: RenderPlan) -> None:
        fid = scope.qualify(f.id)
        try:
            visible = f.when is None or evaluate(f.when, scope.ctx)
        except FieldError as e:
            self._error(plan, fid, e)
            self._boxes(f, scope, plan, fid, "error")
            return
        if not visible:
            self._boxes(f, scope, plan, fid, "hidden")
            return
        try:
            if isinstance(f, TextField):
                self._text(f, scope, plan, fid)
            elif isinstance(f, CheckboxField):
                self._checkbox(f, scope, plan, fid)
            elif isinstance(f, ChoiceField):
                self._choice(f, scope, plan, fid)
            else:
                self._repeat(f, scope, plan, fid)
        except FieldError as e:
            self._error(plan, fid, e)
            self._boxes(f, scope, plan, fid, "error")

    def _text(self, f: TextField, scope: _Scope, plan: RenderPlan, fid: str) -> None:
        rect = f.rect.shifted(scope.dx, scope.dy)
        val = resolve(f.source, scope.ctx)
        if is_missing(val):
            self._require(f.required, plan, fid)
            plan.boxes.append(FieldBox(scope.page, fid, rect, "empty"))
            return
        style = self.styles.resolve(f.style)
        text = format_value(val, style.format)
        if not text:  # zero: blank
            plan.boxes.append(FieldBox(scope.page, fid, rect, "empty"))
            return
        lay = layout_text(text, rect, style, self.metrics, f.comb)
        for w in lay.warnings:
            plan.diagnostics.append(Diagnostic("warning", w.code, fid, w.message))
        plan.ops += [TextOp(scope.page, fid, r) for r in lay.runs]
        plan.boxes.append(FieldBox(scope.page, fid, rect, "printed"))

    def format_text(self, f: TextField, ctx: Context) -> str | None:
        val = resolve(f.source, ctx)
        if is_missing(val):
            return None
        return format_value(val, self.styles.resolve(f.style).format) or None

    def _checkbox(self, f: CheckboxField, scope: _Scope, plan: RenderPlan, fid: str) -> None:
        rect = f.rect.shifted(scope.dx, scope.dy)
        if evaluate(f.checked, scope.ctx):
            self._mark(plan, scope.page, fid, rect, self.styles.resolve(f.style))
            plan.boxes.append(FieldBox(scope.page, fid, rect, "printed"))
        else:
            plan.boxes.append(FieldBox(scope.page, fid, rect, "empty"))

    def selected_options(self, f: ChoiceField, ctx: Context) -> list[int]:
        val = resolve(f.source, ctx)
        if is_missing(val):
            return []
        wanted = val if isinstance(val, list) else [val]
        picked = []
        for w in wanted:
            hits = [i for i, o in enumerate(f.options) if jsonpath.compare(o.value, "==", w)]
            if not hits:
                allowed = ", ".join(repr(o.value) for o in f.options)
                raise FieldError("CHOICE_NO_MATCH", f"{w!r} is not one of the options ({allowed})")
            picked += hits
        return picked

    def _choice(self, f: ChoiceField, scope: _Scope, plan: RenderPlan, fid: str) -> None:
        picked = self.selected_options(f, scope.ctx)
        if not picked:
            self._require(f.required, plan, fid)
        style = self.styles.resolve(f.style)
        for i, opt in enumerate(f.options):
            rect = opt.rect.shifted(scope.dx, scope.dy)
            if i in picked:
                self._mark(plan, scope.page, fid, rect, style)
            plan.boxes.append(FieldBox(scope.page, f"{fid}={json_key(opt.value)}", rect, "printed" if i in picked else "empty"))

    def _repeat(self, f: RepeatField, scope: _Scope, plan: RenderPlan, fid: str) -> None:
        items = resolve(f.source, scope.ctx)
        if is_missing(items):
            items = []
        if not isinstance(items, list):
            raise FieldError("NOT_A_LIST", f"the source of a repeat must be a list, got {type(items).__name__}")

        extra = items[f.max_items :]
        if extra:
            msg = f"{len(items)} items but only {f.max_items} fit on the form"
            if f.overflow == "error":
                raise FieldError("REPEAT_OVERFLOW", msg)
            if f.overflow == "truncate":
                plan.diagnostics.append(Diagnostic("warning", "REPEAT_TRUNCATED", fid, f"{msg}; {len(extra)} dropped"))
            else:
                plan.diagnostics.append(
                    Diagnostic("info", "REPEAT_STATEMENT", fid, f"{msg}; {len(extra)} listed on a statement")
                )
                plan.statements.append(self._statement(f, scope, extra, plan, fid))

        for i, item in enumerate(items[: f.max_items]):
            off = f.item_offset(i)
            child = _Scope(
                Context(scope.ctx.root, item, in_repeat=True),
                scope.page,
                scope.dx + off.x,
                scope.dy + off.y,
                f"{fid}[{i}]",
            )
            for c in f.fields:
                self._field(c, child, plan)

    def _statement(self, f: RepeatField, scope: _Scope, items: list[Any], plan: RenderPlan, fid: str) -> Statement:
        # columns = the repeat's own child fields, so the statement mirrors the form
        cols = [c for c in f.fields if not isinstance(c, RepeatField)]
        rows = []
        for n, item in enumerate(items, start=f.max_items):
            ctx = Context(scope.ctx.root, item, in_repeat=True)
            row = []
            for c in cols:
                try:
                    row.append(self._cell(c, ctx))
                except FieldError as e:
                    self._error(plan, f"{fid}[{n}].{c.id}", e)
                    row.append("")
            rows.append(row)
        title = f.statement_title or f"{self.annotation.form.title}: {f.label or f.id} (continued)"
        return Statement(title, [c.label or c.id for c in cols], rows)

    def _cell(self, f: Field, ctx: Context) -> str:
        if f.when is not None and not evaluate(f.when, ctx):
            return ""
        if isinstance(f, TextField):
            return self.format_text(f, ctx) or ""
        if isinstance(f, CheckboxField):
            return "X" if evaluate(f.checked, ctx) else ""
        if isinstance(f, ChoiceField):
            return ", ".join(f.options[i].label or json_key(f.options[i].value) for i in self.selected_options(f, ctx))
        return ""

    # helpers

    def _mark(self, plan, page, fid, rect, style: ResolvedStyle):
        plan.ops.append(MarkOp(page, fid, rect, style.mark, style.markInset, style.color))

    def _require(self, required: bool, plan: RenderPlan, fid: str) -> None:
        if required:
            plan.diagnostics.append(Diagnostic("error", "MISSING_REQUIRED", fid, "a value is required"))

    def _error(self, plan: RenderPlan, fid: str, e: FieldError) -> None:
        plan.diagnostics.append(Diagnostic("error", e.code, fid, e.message))

    def _boxes(self, f: Field, scope: _Scope, plan: RenderPlan, fid: str, status: str) -> None:
        # record where the boxes would have been, so the debug overlay can show hidden/error ones
        if isinstance(f, (TextField, CheckboxField)):
            plan.boxes.append(FieldBox(scope.page, fid, f.rect.shifted(scope.dx, scope.dy), status))  # type: ignore[arg-type]
        elif isinstance(f, ChoiceField):
            for o in f.options:
                plan.boxes.append(FieldBox(scope.page, f"{fid}={json_key(o.value)}", o.rect.shifted(scope.dx, scope.dy), status))  # type: ignore[arg-type]


def build_plan(annotation: FormAnnotation, data: Any, metrics: FontMetrics | None = None) -> RenderPlan:
    return Planner(annotation, metrics).build(data)
