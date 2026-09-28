"""Dataclasses for an annotation file + the loader.

The JSON Schema (schema/tfa.schema.json) is the real definition of the format.
load_annotation() validates against it first so the parsing code below can
just assume things are there.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Union

from jsonschema import Draft202012Validator

# installed wheel has a copy next to this file, source checkout uses schema/
_HERE = Path(__file__).resolve().parent
SCHEMA_PATH = next(
    (p for p in (_HERE / "tfa.schema.json", _HERE.parents[1] / "schema" / "tfa.schema.json") if p.exists()),
    _HERE.parents[1] / "schema" / "tfa.schema.json",
)

Scalar = Union[str, int, Decimal, bool]


class AnnotationError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("invalid annotation:\n  " + "\n  ".join(problems))
        self.problems = problems


# ---- geometry


@dataclass(frozen=True)
class Rect:
    x: float  # top-left corner, from the page's top-left
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def shifted(self, dx: float, dy: float) -> Rect:
        return Rect(self.x + dx, self.y + dy, self.width, self.height)

    def overlaps(self, other: Rect, tolerance: float = 0.5) -> bool:
        return (
            self.x < other.right - tolerance
            and other.x < self.right - tolerance
            and self.y < other.bottom - tolerance
            and other.y < self.bottom - tolerance
        )


@dataclass(frozen=True)
class Offset:
    x: float
    y: float


@dataclass(frozen=True)
class CombGroup:
    x: float
    width: float
    cells: int


@dataclass(frozen=True)
class Comb:
    groups: tuple[CombGroup, ...]

    @property
    def cells(self) -> int:
        return sum(g.cells for g in self.groups)

    def cell_rects(self, rect: Rect) -> list[Rect]:
        out = []
        for g in self.groups:
            w = g.width / g.cells
            out += [Rect(rect.x + g.x + i * w, rect.y, w, rect.height) for i in range(g.cells)]
        return out


# ---- sources / transforms / conditions


@dataclass(frozen=True)
class Transform:
    op: Literal["digits", "slice", "map", "abs", "negate"]
    start: int | None = None
    end: int | None = None
    values: dict[str, Any] | None = None
    default: Any = None


@dataclass(frozen=True, kw_only=True)
class _SourceBase:
    default: Scalar | None = None
    transform: tuple[Transform, ...] = ()


@dataclass(frozen=True, kw_only=True)
class PathSource(_SourceBase):
    path: str
    aggregate: Literal["sum", "count", "min", "max", "first", "last", "join"] | None = None
    separator: str = ", "


@dataclass(frozen=True, kw_only=True)
class LiteralSource(_SourceBase):
    literal: Scalar


@dataclass(frozen=True, kw_only=True)
class TemplateSource(_SourceBase):
    template: str


@dataclass(frozen=True, kw_only=True)
class CoalesceSource(_SourceBase):
    sources: tuple[Source, ...]


Source = Union[PathSource, LiteralSource, TemplateSource, CoalesceSource]


@dataclass(frozen=True)
class Comparison:
    source: Source
    op: str
    value: Any = None


@dataclass(frozen=True)
class AllOf:
    conditions: tuple[Condition, ...]


@dataclass(frozen=True)
class AnyOf:
    conditions: tuple[Condition, ...]


@dataclass(frozen=True)
class Not:
    condition: Condition


Condition = Union[Comparison, AllOf, AnyOf, Not]


# ---- formats / styles


@dataclass(frozen=True)
class TextFormat:
    case: Literal["none", "upper", "lower"] = "none"


@dataclass(frozen=True)
class NumberFormat:
    kind: Literal["number", "currency"] = "number"
    decimals: int | None = None  # None -> 0 for number, 2 for currency
    thousands: bool = True
    negative: Literal["minus", "parens"] = "minus"
    zero: Literal["show", "blank", "dash"] = "show"
    symbol: str = ""
    part: Literal["whole", "dollars", "cents"] = "whole"


@dataclass(frozen=True)
class PercentFormat:
    decimals: int = 0
    input: Literal["fraction", "percent"] = "fraction"
    symbol: bool = True


@dataclass(frozen=True)
class DateFormat:
    pattern: str = "MM/DD/YYYY"


@dataclass(frozen=True)
class MaskFormat:
    patterns: tuple[str, ...]


Format = Union[TextFormat, NumberFormat, PercentFormat, DateFormat, MaskFormat]


@dataclass(frozen=True)
class Padding:
    top: float
    right: float
    bottom: float
    left: float


@dataclass(frozen=True)
class Style:
    # everything optional - None means "inherit"
    extends: str | None = None
    font: str | None = None
    fontSize: float | None = None
    minFontSize: float | None = None
    color: str | None = None
    align: Literal["left", "center", "right"] | None = None
    valign: Literal["top", "middle", "bottom"] | None = None
    padding: Padding | None = None
    overflow: Literal["shrink", "wrap", "truncate", "error"] | None = None
    lineHeight: float | None = None
    format: Format | None = None
    mark: Literal["cross", "check", "dot", "fill"] | None = None
    markInset: float | None = None


StyleRef = Union[str, Style, None]


# ---- fields


@dataclass(frozen=True, kw_only=True)
class _FieldBase:
    id: str
    page: int | None = None
    label: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()
    when: Condition | None = None


@dataclass(frozen=True, kw_only=True)
class TextField(_FieldBase):
    rect: Rect
    source: Source
    style: StyleRef = None
    comb: Comb | None = None
    required: bool = False


@dataclass(frozen=True, kw_only=True)
class CheckboxField(_FieldBase):
    rect: Rect
    checked: Condition
    style: StyleRef = None


@dataclass(frozen=True)
class ChoiceOption:
    value: Scalar
    rect: Rect
    label: str | None = None


@dataclass(frozen=True, kw_only=True)
class ChoiceField(_FieldBase):
    source: Source
    options: tuple[ChoiceOption, ...]
    style: StyleRef = None
    required: bool = False


@dataclass(frozen=True, kw_only=True)
class RepeatField(_FieldBase):
    source: Source
    max_items: int
    fields: tuple[Field, ...]
    offset: Offset | None = None
    offsets: tuple[Offset, ...] | None = None
    overflow: Literal["error", "truncate", "statement"] = "error"
    statement_title: str | None = None

    def item_offset(self, i: int) -> Offset:
        if self.offsets is not None:
            return self.offsets[i]
        assert self.offset is not None
        return Offset(self.offset.x * i, self.offset.y * i)


Field = Union[TextField, CheckboxField, ChoiceField, RepeatField]


# ---- the document


@dataclass(frozen=True)
class PageSize:
    width: float
    height: float


@dataclass(frozen=True)
class FormInfo:
    id: str
    title: str
    source_file: str
    source_sha256: str
    pages: tuple[PageSize, ...]
    source_url: str | None = None
    issuer: str | None = None
    form_number: str | None = None
    tax_year: int | None = None
    revision: str | None = None


@dataclass(frozen=True)
class FormAnnotation:
    spec_version: str
    form: FormInfo
    fields: tuple[Field, ...]
    styles: dict[str, Style] = field(default_factory=dict)
    fonts: dict[str, Path] = field(default_factory=dict)
    base_dir: Path = Path(".")

    @property
    def source_pdf(self) -> Path:
        return self.base_dir / self.form.source_file


# ---- loading


_validator: Draft202012Validator | None = None


def schema_validator() -> Draft202012Validator:
    global _validator
    if _validator is None:
        _validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))
    return _validator


def schema_errors(doc: Any) -> list[str]:
    errs = sorted(schema_validator().iter_errors(doc), key=lambda e: list(e.absolute_path))
    out = []
    for e in errs:
        # for oneOf/anyOf the top-level message is useless ("not valid under any of..."),
        # the shallowest sub-error is usually the real reason
        leaf = min(e.context, key=lambda c: len(list(c.schema_path)), default=e) if e.context else e
        where = "/" + "/".join(str(p) for p in e.absolute_path)
        out.append(f"{where}: {leaf.message}")
    return out


def load_annotation(path: str | Path) -> FormAnnotation:
    path = Path(path)
    doc = json.loads(path.read_text(), parse_float=Decimal)
    return parse_annotation(doc, base_dir=path.parent)


def parse_annotation(doc: dict[str, Any], base_dir: Path = Path(".")) -> FormAnnotation:
    problems = schema_errors(_jsonable(doc))
    if problems:
        raise AnnotationError(problems)
    f = doc["form"]
    form = FormInfo(
        id=f["id"],
        title=f["title"],
        source_file=f["source"]["file"],
        source_sha256=f["source"]["sha256"],
        source_url=f["source"].get("url"),
        pages=tuple(PageSize(float(p["width"]), float(p["height"])) for p in f["pages"]),
        issuer=f.get("issuer"),
        form_number=f.get("formNumber"),
        tax_year=f.get("taxYear"),
        revision=f.get("revision"),
    )
    return FormAnnotation(
        spec_version=doc["specVersion"],
        form=form,
        fields=tuple(_field(x) for x in doc["fields"]),
        styles={k: _style(v) for k, v in doc.get("styles", {}).items()},
        fonts={k: base_dir / v["file"] for k, v in doc.get("fonts", {}).items()},
        base_dir=base_dir,
    )


def _jsonable(v: Any) -> Any:
    # we load with parse_float=Decimal, the schema validator wants real floats
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def _rect(d) -> Rect:
    return Rect(float(d["x"]), float(d["y"]), float(d["width"]), float(d["height"]))


def _offset(d) -> Offset:
    return Offset(float(d["x"]), float(d["y"]))


def _transform(d) -> Transform:
    return Transform(op=d["op"], start=d.get("start"), end=d.get("end"), values=d.get("values"), default=d.get("default"))


def _source(d) -> Source:
    common = {
        "default": d.get("default"),
        "transform": tuple(_transform(t) for t in d.get("transform", [])),
    }
    if "path" in d:
        return PathSource(path=d["path"], aggregate=d.get("aggregate"), separator=d.get("separator", ", "), **common)
    if "literal" in d:
        return LiteralSource(literal=d["literal"], **common)
    if "template" in d:
        return TemplateSource(template=d["template"], **common)
    return CoalesceSource(sources=tuple(_source(s) for s in d["coalesce"]), **common)


def _condition(d) -> Condition:
    if "all" in d:
        return AllOf(tuple(_condition(c) for c in d["all"]))
    if "any" in d:
        return AnyOf(tuple(_condition(c) for c in d["any"]))
    if "not" in d:
        return Not(_condition(d["not"]))
    src = PathSource(path=d["path"]) if "path" in d else _source(d["source"])
    return Comparison(source=src, op=d["op"], value=d.get("value"))


def _format(d) -> Format:
    t = d["type"]
    if t == "text":
        return TextFormat(case=d.get("case", "none"))
    if t in ("number", "currency"):
        return NumberFormat(
            kind=t,
            decimals=d.get("decimals"),
            thousands=d.get("thousands", True),
            negative=d.get("negative", "minus"),
            zero=d.get("zero", "show"),
            symbol=d.get("symbol", ""),
            part=d.get("part", "whole"),
        )
    if t == "percent":
        return PercentFormat(decimals=d.get("decimals", 0), input=d.get("input", "fraction"), symbol=d.get("symbol", True))
    if t == "date":
        return DateFormat(pattern=d.get("pattern", "MM/DD/YYYY"))
    pat = d["pattern"]
    return MaskFormat(patterns=(pat,) if isinstance(pat, str) else tuple(pat))


def _padding(v) -> Padding:
    if isinstance(v, dict):
        return Padding(
            top=float(v.get("top", 0)),
            right=float(v.get("right", 0)),
            bottom=float(v.get("bottom", 0)),
            left=float(v.get("left", 0)),
        )
    p = float(v)
    return Padding(p, p, p, p)


def _style(d) -> Style:
    num = lambda k: float(d[k]) if k in d else None  # noqa: E731
    return Style(
        extends=d.get("extends"),
        font=d.get("font"),
        fontSize=num("fontSize"),
        minFontSize=num("minFontSize"),
        color=d.get("color"),
        align=d.get("align"),
        valign=d.get("valign"),
        padding=_padding(d["padding"]) if "padding" in d else None,
        overflow=d.get("overflow"),
        lineHeight=num("lineHeight"),
        format=_format(d["format"]) if "format" in d else None,
        mark=d.get("mark"),
        markInset=num("markInset"),
    )


def _style_ref(v) -> StyleRef:
    if v is None or isinstance(v, str):
        return v
    return _style(v)


def _comb(d, rect: Rect) -> Comb | None:
    if d is None:
        return None
    if "cells" in d:
        return Comb((CombGroup(0.0, rect.width, d["cells"]),))
    return Comb(tuple(CombGroup(float(g["x"]), float(g["width"]), g["cells"]) for g in d["groups"]))


def _field(d) -> Field:
    common = {
        "id": d["id"],
        "page": d.get("page"),
        "label": d.get("label"),
        "description": d.get("description"),
        "tags": tuple(d.get("tags", ())),
        "when": _condition(d["when"]) if "when" in d else None,
    }
    t = d["type"]
    if t == "text":
        rect = _rect(d["rect"])
        return TextField(
            rect=rect,
            source=_source(d["source"]),
            style=_style_ref(d.get("style")),
            comb=_comb(d.get("comb"), rect),
            required=d.get("required", False),
            **common,
        )
    if t == "checkbox":
        return CheckboxField(rect=_rect(d["rect"]), checked=_condition(d["checked"]), style=_style_ref(d.get("style")), **common)
    if t == "choice":
        return ChoiceField(
            source=_source(d["source"]),
            options=tuple(ChoiceOption(value=o["value"], rect=_rect(o["rect"]), label=o.get("label")) for o in d["options"]),
            style=_style_ref(d.get("style")),
            required=d.get("required", False),
            **common,
        )
    # repeat
    ov = d.get("overflow", {})
    return RepeatField(
        source=_source(d["source"]),
        max_items=d["maxItems"],
        fields=tuple(_field(c) for c in d["fields"]),
        offset=_offset(d["offset"]) if "offset" in d else None,
        offsets=tuple(_offset(o) for o in d["offsets"]) if "offsets" in d else None,
        overflow=ov.get("strategy", "error"),
        statement_title=ov.get("title"),
        **common,
    )
