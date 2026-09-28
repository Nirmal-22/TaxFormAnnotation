"""Style cascade: builtin defaults < "default" style < extends chain < the style itself."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace

from .model import AnnotationError, Format, Padding, Style, StyleRef, TextFormat


@dataclass(frozen=True)
class ResolvedStyle:
    font: str
    fontSize: float
    minFontSize: float
    color: str
    align: str
    valign: str
    padding: Padding
    overflow: str
    lineHeight: float
    format: Format
    mark: str
    markInset: float


BUILTIN = ResolvedStyle(
    font="Helvetica",
    fontSize=9,
    minFontSize=6,
    color="#000000",
    align="left",
    valign="middle",
    padding=Padding(top=1, right=2, bottom=1, left=2),
    overflow="shrink",
    lineHeight=1.15,
    format=TextFormat(),
    mark="cross",
    markInset=1,
)

_PROPS = [f.name for f in fields(ResolvedStyle)]


class StyleResolver:
    def __init__(self, styles: dict[str, Style]):
        self.styles = styles
        self._cache: dict[str, ResolvedStyle] = {}

    def resolve(self, ref: StyleRef) -> ResolvedStyle:
        if ref is None:
            return self.named("default")
        if isinstance(ref, str):
            return self.named(ref)
        # inline style
        base = self.named(ref.extends or "default")
        return _apply(base, ref)

    def named(self, name: str, _seen: tuple[str, ...] = ()) -> ResolvedStyle:
        if name in self._cache:
            return self._cache[name]
        if name in _seen:
            raise AnnotationError([f"style cycle: {' -> '.join(_seen + (name,))}"])
        style = self.styles.get(name)
        if style is None:
            if name == "default":
                return BUILTIN
            raise AnnotationError([f"unknown style {name!r}"])
        if name == "default":
            if style.extends is not None:
                raise AnnotationError(["the default style cannot extend another style"])
            base = BUILTIN
        else:
            base = self.named(style.extends or "default", _seen + (name,))
        out = _apply(base, style)
        self._cache[name] = out
        return out


def _apply(base: ResolvedStyle, style: Style) -> ResolvedStyle:
    # whole-property override, no deep merging of format/padding
    overrides = {p: getattr(style, p) for p in _PROPS if getattr(style, p) is not None}
    return replace(base, **overrides)
