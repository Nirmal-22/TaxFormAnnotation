"""Checks the schema can't do: ids, bounds, paths, styles, overlaps, the pdf hash."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from . import jsonpath
from .fonts import STANDARD_FONTS
from .model import (
    AllOf,
    AnnotationError,
    AnyOf,
    CheckboxField,
    ChoiceField,
    CoalesceSource,
    Comparison,
    Condition,
    Field,
    FormAnnotation,
    Not,
    Offset,
    PathSource,
    Rect,
    RepeatField,
    Source,
    Style,
    TemplateSource,
    TextField,
)
from .pdf import check_source
from .plan import Diagnostic
from .styles import StyleResolver
from .values import PLACEHOLDER, json_key


@dataclass(frozen=True)
class _Box:
    page: int
    field: str
    rect: Rect


def lint(annotation: FormAnnotation, check_pdf: bool = True) -> list[Diagnostic]:
    return _Linter(annotation).run(check_pdf)


class _Linter:
    def __init__(self, a: FormAnnotation):
        self.a = a
        self.out: list[Diagnostic] = []
        self.boxes: list[_Box] = []
        self.fonts = set(STANDARD_FONTS) | set(a.fonts)
        self.resolver = StyleResolver(a.styles)

    def error(self, code, fid, msg):
        self.out.append(Diagnostic("error", code, fid, msg))

    def warn(self, code, fid, msg):
        self.out.append(Diagnostic("warning", code, fid, msg))

    def run(self, check_pdf: bool) -> list[Diagnostic]:
        for name, path in self.a.fonts.items():
            if not path.exists():
                self.error("FONT_NOT_FOUND", None, f"font {name}: {path} does not exist")
        for name, style in self.a.styles.items():
            self._style(style, f"style {name}")
            try:
                self.resolver.named(name)
            except AnnotationError as e:
                self.error("BAD_STYLE", None, "; ".join(e.problems))

        self._fields(self.a.fields, page=None, instances=[Offset(0, 0)], in_repeat=False, prefix="")
        self._overlaps()
        if check_pdf:
            for p in check_source(self.a):
                self.error("SOURCE_MISMATCH", None, p)
        return self.out

    def _fields(self, fields, page, instances, in_repeat, prefix):
        # instances = every offset this group of fields gets stamped at (1 normally,
        # maxItems inside a repeat, more when nested)
        seen: set[str] = set()
        for f in fields:
            fid = f"{prefix}{f.id}"
            if f.id in seen:
                self.error("DUPLICATE_ID", fid, "id is used more than once at this level")
            seen.add(f.id)
            p = f.page if page is None else page
            assert p is not None
            if not 1 <= p <= len(self.a.form.pages):
                self.error("PAGE_OUT_OF_RANGE", fid, f"page {p} does not exist (form has {len(self.a.form.pages)})")
                continue
            if f.when is not None:
                self._condition(f.when, fid, in_repeat)

            if isinstance(f, TextField):
                self._style_ref(f, fid)
                self._source(f.source, fid, in_repeat, single=True)
                self._rect(p, fid, f.rect, instances)
                if f.comb is not None:
                    for g in f.comb.groups:
                        if g.x + g.width > f.rect.width + 0.01:
                            self.error("COMB_OUT_OF_RECT", fid, "a comb group extends past the rect's right edge")
            elif isinstance(f, CheckboxField):
                self._style_ref(f, fid)
                self._condition(f.checked, fid, in_repeat)
                self._rect(p, fid, f.rect, instances)
            elif isinstance(f, ChoiceField):
                self._style_ref(f, fid)
                self._source(f.source, fid, in_repeat, single=True)
                vals = [o.value for o in f.options]
                for v in vals:
                    if sum(jsonpath.compare(v, "==", w) for w in vals) > 1:
                        self.error("DUPLICATE_OPTION", fid, f"option value {v!r} appears more than once")
                        break
                for o in f.options:
                    self._rect(p, f"{fid}={json_key(o.value)}", o.rect, instances)
            elif isinstance(f, RepeatField):
                self._source(f.source, fid, in_repeat, single=True)
                if f.offsets is not None and len(f.offsets) != f.max_items:
                    self.error("OFFSETS_LENGTH", fid, f"offsets has {len(f.offsets)} entries but maxItems is {f.max_items}")
                    continue
                child_instances = [
                    Offset(base.x + off.x, base.y + off.y)
                    for base in instances
                    for off in (f.item_offset(i) for i in range(f.max_items))
                ]
                self._fields(f.fields, p, child_instances, True, f"{fid}[].")

    def _rect(self, page: int, fid: str, rect: Rect, instances: list[Offset]) -> None:
        size = self.a.form.pages[page - 1]
        for i, off in enumerate(instances):
            r = rect.shifted(off.x, off.y)
            name = fid if len(instances) == 1 else f"{fid} (instance {i})"
            if r.x < -0.01 or r.y < -0.01 or r.right > size.width + 0.01 or r.bottom > size.height + 0.01:
                self.error("RECT_OUT_OF_BOUNDS", name, f"rect {r} is outside the {size.width:g}x{size.height:g}pt page")
            self.boxes.append(_Box(page, name, r))

    def _overlaps(self) -> None:
        by_page: dict[int, list[_Box]] = {}
        for b in self.boxes:
            by_page.setdefault(b.page, []).append(b)
        for page, boxes in by_page.items():
            boxes.sort(key=lambda b: b.rect.x)  # sweep left to right
            for i, a in enumerate(boxes):
                for b in boxes[i + 1 :]:
                    if b.rect.x >= a.rect.right:
                        break
                    if a.rect.overlaps(b.rect):
                        self.warn("OVERLAP", a.field, f"overlaps {b.field} on page {page}")

    def _style(self, style: Style, where: str) -> None:
        if style.font is not None and style.font not in self.fonts:
            self.error("UNKNOWN_FONT", None, f"{where}: font {style.font!r} is neither standard nor declared in fonts")

    def _style_ref(self, f, fid: str) -> None:
        if isinstance(f.style, Style):
            self._style(f.style, fid)
        try:
            self.resolver.resolve(f.style)
        except AnnotationError as e:
            self.error("BAD_STYLE", fid, "; ".join(e.problems))

    def _source(self, source: Source, fid: str, in_repeat: bool, single: bool) -> None:
        for path, one in _source_paths(source, single):
            self._path(path, fid, in_repeat, one)

    def _condition(self, cond: Condition, fid: str, in_repeat: bool) -> None:
        for src in _condition_sources(cond):
            # exists/truthy on $.deps[*] is fine, so don't demand a single value here
            self._source(src, fid, in_repeat, single=False)

    def _path(self, path: str, fid: str, in_repeat: bool, single: bool) -> None:
        try:
            q = jsonpath.parse(path)
        except jsonpath.PathError as e:
            self.error("BAD_PATH", fid, str(e))
            return
        if q.relative and not in_repeat:
            self.error("RELATIVE_PATH", fid, f"{path} uses @, which only has a meaning inside a repeat")
        if single and not q.singular:
            self.warn("MULTI_VALUE_PATH", fid, f"{path} can match several values but has no aggregate; it fails if it does")


def _source_paths(source: Source, single: bool) -> Iterator[tuple[str, bool]]:
    if isinstance(source, PathSource):
        yield source.path, single and source.aggregate is None
    elif isinstance(source, TemplateSource):
        for m in PLACEHOLDER.finditer(source.template):
            if m.group(1) is not None:
                yield m.group(1).strip(), True
    elif isinstance(source, CoalesceSource):
        for s in source.sources:
            yield from _source_paths(s, single)


def _condition_sources(cond: Condition) -> Iterator[Source]:
    if isinstance(cond, (AllOf, AnyOf)):
        for c in cond.conditions:
            yield from _condition_sources(c)
    elif isinstance(cond, Not):
        yield from _condition_sources(cond.condition)
    elif isinstance(cond, Comparison):
        yield cond.source
