"""Getting values out of the data: sources, transforms, conditions.

Pipeline is  source -> default -> transforms -> format -> layout -> draw,
this file is the first three steps. Values are plain JSON stuff (numbers as
int/Decimal) or MISSING. Anything wrong raises FieldError and the planner
turns that into a diagnostic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from . import jsonpath
from .model import (
    AllOf,
    AnyOf,
    CoalesceSource,
    Comparison,
    Condition,
    LiteralSource,
    Not,
    PathSource,
    Source,
    TemplateSource,
    Transform,
)


class _Missing:
    def __repr__(self):
        return "MISSING"

    def __bool__(self):
        return False


MISSING: Any = _Missing()


class FieldError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Context:
    root: Any
    item: Any = MISSING  # current row when inside a repeat
    in_repeat: bool = False


def is_missing(v: Any) -> bool:
    return v is MISSING or v is None


def is_number(v: Any) -> bool:
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def to_number(v: Any, what: str = "value") -> Decimal:
    if is_number(v):
        return jsonpath.to_decimal(v)
    if isinstance(v, str):
        # "1,234.50" from a spreadsheet export is fine too
        try:
            d = Decimal(v.strip().replace(",", ""))
            if d.is_finite():
                return d
        except InvalidOperation:
            pass
    raise FieldError("TYPE_MISMATCH", f"expected a number for {what}, got {v!r}")


def to_text(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        raise FieldError(
            "TYPE_MISMATCH",
            f"cannot print boolean {v!r} as text; use a map transform or a checkbox",
        )
    if is_number(v):
        d = jsonpath.to_decimal(v)
        # 100 not 100.0, but keep 12.50 as written
        return format(d.normalize() if d == d.to_integral() else d, "f")
    raise FieldError("TYPE_MISMATCH", f"cannot print {type(v).__name__} as text: {v!r}")


# ---- paths


def select(path: str, ctx: Context) -> list[Any]:
    q = jsonpath.parse(path)
    if q.relative and not ctx.in_repeat:
        raise FieldError("RELATIVE_PATH", f"{path} uses @ outside a repeat")
    return q.evaluate(ctx.root, ctx.item)


def select_one(path: str, ctx: Context) -> Any:
    vals = [v for v in select(path, ctx) if v is not None]
    if not vals:
        return MISSING
    if len(vals) > 1:
        raise FieldError(
            "AMBIGUOUS_VALUE",
            f"{path} matched {len(vals)} values; add an aggregate (e.g. sum or first)",
        )
    return vals[0]


# ---- sources


def resolve(source: Source, ctx: Context) -> Any:
    val = _raw(source, ctx)
    if is_missing(val) or val == "":
        val = MISSING if source.default is None else source.default
    if val is MISSING:
        return MISSING
    for t in source.transform:
        val = apply_transform(t, val)
        if is_missing(val):
            return MISSING
    return val


def _raw(source: Source, ctx: Context) -> Any:
    if isinstance(source, PathSource):
        if source.aggregate is None:
            return select_one(source.path, ctx)
        return aggregate(source.aggregate, select(source.path, ctx), source.separator)
    if isinstance(source, LiteralSource):
        return source.literal
    if isinstance(source, TemplateSource):
        return render_template(source.template, ctx)
    if isinstance(source, CoalesceSource):
        for s in source.sources:
            v = resolve(s, ctx)
            if not is_missing(v):
                return v
        return MISSING
    raise TypeError(source)


def aggregate(kind: str, values: list[Any], separator: str = ", ") -> Any:
    present = [v for v in values if v is not None]
    if kind == "count":
        return len(present)
    if not present:
        return MISSING  # sum of nothing is blank, not 0 - the form wants an empty box
    if kind == "first":
        return present[0]
    if kind == "last":
        return present[-1]
    if kind == "join":
        return separator.join(to_text(v) for v in present)
    nums = [to_number(v, f"aggregate {kind}") for v in present]
    if kind == "sum":
        return sum(nums, Decimal(0))
    if kind == "min":
        return min(nums)
    if kind == "max":
        return max(nums)
    raise ValueError(f"unknown aggregate {kind}")


PLACEHOLDER = re.compile(r"\{\{|\}\}|\{([^{}]*)\}")


def render_template(template: str, ctx: Context) -> Any:
    def sub(m: re.Match[str]) -> str:
        if m.group(0) == "{{":
            return "{"
        if m.group(0) == "}}":
            return "}"
        v = select_one(m.group(1).strip(), ctx)
        return "" if is_missing(v) else to_text(v)

    text = PLACEHOLDER.sub(sub, template)
    # tidy up: "Jordan  Rivera" when there's no middle initial, blank lines etc
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    text = "\n".join(ln for ln in lines if ln)
    return text if text else MISSING


def apply_transform(t: Transform, v: Any) -> Any:
    if t.op == "digits":
        return re.sub(r"[^0-9]", "", to_text(v))
    if t.op == "slice":
        return to_text(v)[t.start : t.end]
    if t.op == "map":
        key = json_key(v)
        assert t.values is not None
        if key in t.values:
            return t.values[key]
        if t.default is not None:
            return t.default
        raise FieldError("MAP_NO_MATCH", f"no mapping for {key!r}")
    if t.op == "abs":
        return abs(to_number(v))
    if t.op == "negate":
        return -to_number(v)
    raise ValueError(f"unknown transform {t.op}")


def json_key(v: Any) -> str:
    # map keys are strings in JSON so look bools/numbers up by their JSON spelling
    if isinstance(v, bool):
        return "true" if v else "false"
    return to_text(v)


# ---- conditions


def truthy(v: Any) -> bool:
    if is_missing(v):
        return False
    if isinstance(v, (str, list, dict)):
        return len(v) > 0
    if is_number(v):
        return jsonpath.to_decimal(v) != 0
    return bool(v)


_OPS = {"eq": "==", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
_UNARY = ("exists", "notExists", "truthy", "falsy")


def evaluate(cond: Condition, ctx: Context) -> bool:
    if isinstance(cond, AllOf):
        return all(evaluate(c, ctx) for c in cond.conditions)
    if isinstance(cond, AnyOf):
        return any(evaluate(c, ctx) for c in cond.conditions)
    if isinstance(cond, Not):
        return not evaluate(cond.condition, ctx)
    assert isinstance(cond, Comparison)
    op = cond.op

    if isinstance(cond.source, PathSource) and cond.source.aggregate is None:
        # plain path: might be multi-valued ($.deps[*]), exists/truthy mean "any of them"
        vals = [v for v in select(cond.source.path, ctx) if v is not None]
        if op in _UNARY:
            return _unary(op, vals)
        if len(vals) > 1:
            raise FieldError(
                "AMBIGUOUS_VALUE",
                f"{cond.source.path} matched {len(vals)} values in a comparison",
            )
        val = vals[0] if vals else MISSING
    else:
        val = resolve(cond.source, ctx)
        if op in _UNARY:
            return _unary(op, [] if is_missing(val) else [val])

    if op in ("in", "notIn"):
        hit = any(jsonpath.compare(val, "==", x) for x in cond.value)
        return hit if op == "in" else not hit
    return jsonpath.compare(val, _OPS[op], cond.value)


def _unary(op: str, vals: list[Any]) -> bool:
    if op == "exists":
        return bool(vals)
    if op == "notExists":
        return not vals
    if op == "truthy":
        return any(truthy(v) for v in vals)
    return not any(truthy(v) for v in vals)
