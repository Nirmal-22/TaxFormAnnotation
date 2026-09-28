"""Tiny JSONPath (RFC 9535-ish) parser + evaluator.

What we support:

    $                      root
    @                      current item (repeat rows, filters)
    .name   ['name']       member
    [3]     [-1]           index
    .*      [*]            all children
    [?@.type == 'INT']     filter
    [?@.middleName]        filter: exists

No `..`, no slices, no functions. On purpose - a path in an annotation should
be obvious at a glance, and `$..amount` is the opposite of obvious.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any, Union


class PathError(ValueError):
    def __init__(self, path: str, position: int, message: str):
        super().__init__(f"{message} at position {position} in {path!r}")
        self.path = path
        self.position = position


@dataclass(frozen=True)
class Name:
    name: str


@dataclass(frozen=True)
class Index:
    index: int


@dataclass(frozen=True)
class Wildcard:
    pass


@dataclass(frozen=True)
class Filter:
    path: tuple[Union[Name, Index], ...]
    op: str | None  # None -> just "does it exist"
    literal: Any = None


Segment = Union[Name, Index, Wildcard, Filter]

NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUM_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?")
INT_RE = re.compile(r"-?(?:0|[1-9][0-9]*)")
OPS = ("==", "!=", "<=", ">=", "<", ">")  # two-char ones first, order matters


@dataclass(frozen=True)
class Query:
    text: str
    relative: bool  # starts with @
    segments: tuple[Segment, ...]

    @property
    def singular(self) -> bool:
        # can this ever return more than one thing?
        return all(isinstance(s, (Name, Index)) for s in self.segments)

    def evaluate(self, root: Any, current: Any = None) -> list[Any]:
        nodes = [current if self.relative else root]
        for seg in self.segments:
            nodes = [c for n in nodes for c in _select(seg, n)]
        return nodes


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def error(self, msg: str) -> PathError:
        return PathError(self.text, self.pos, msg)

    def peek(self, n: int = 1) -> str:
        return self.text[self.pos : self.pos + n]

    def skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in " \t":
            self.pos += 1

    def expect(self, s: str) -> None:
        if not self.text.startswith(s, self.pos):
            raise self.error(f"expected {s!r}")
        self.pos += len(s)

    def parse(self) -> Query:
        root = self.peek()
        if root not in ("$", "@"):
            raise self.error("a path must start with $ or @")
        self.pos += 1
        segs = self.segments(allow_multi=True)
        if self.pos != len(self.text):
            raise self.error("unexpected character")
        return Query(self.text, root == "@", tuple(segs))

    def segments(self, allow_multi: bool) -> list[Segment]:
        out: list[Segment] = []
        while self.pos < len(self.text):
            c = self.peek()
            if c == ".":
                if self.peek(2) == "..":
                    raise self.error("descendant segments (..) are not supported")
                self.pos += 1
                if self.peek() == "*":
                    if not allow_multi:
                        raise self.error("wildcards are not allowed in filter paths")
                    self.pos += 1
                    out.append(Wildcard())
                    continue
                m = NAME_RE.match(self.text, self.pos)
                if not m:
                    raise self.error("expected a member name after '.'")
                self.pos = m.end()
                out.append(Name(m.group()))
            elif c == "[":
                self.pos += 1
                self.skip_ws()
                out.append(self.bracket(allow_multi))
                self.skip_ws()
                self.expect("]")
            else:
                break
        return out

    def bracket(self, allow_multi: bool) -> Segment:
        c = self.peek()
        if c in ("'", '"'):
            return Name(self.string())
        if c in ("*", "?"):
            if not allow_multi:
                raise self.error("wildcards and filters are not allowed in filter paths")
            self.pos += 1
            return Wildcard() if c == "*" else self.filter()
        m = INT_RE.match(self.text, self.pos)
        if m:
            self.pos = m.end()
            return Index(int(m.group()))
        raise self.error("expected a quoted name, an index, * or a ?filter")

    def filter(self) -> Filter:
        self.skip_ws()
        if self.peek() != "@":
            raise self.error("a filter must start with @")
        self.pos += 1
        path = self.segments(allow_multi=False)
        self.skip_ws()
        for op in OPS:
            if self.text.startswith(op, self.pos):
                self.pos += len(op)
                self.skip_ws()
                return Filter(tuple(path), op, self.literal())  # type: ignore[arg-type]
        return Filter(tuple(path), None)  # type: ignore[arg-type]

    def literal(self) -> Any:
        c = self.peek()
        if c in ("'", '"'):
            return self.string()
        for word, val in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(word, self.pos):
                self.pos += len(word)
                return val
        m = NUM_RE.match(self.text, self.pos)
        if m:
            self.pos = m.end()
            return Decimal(m.group())
        raise self.error("expected a string, number, true, false or null")

    def string(self) -> str:
        quote = self.peek()
        self.pos += 1
        buf = []
        while self.pos < len(self.text):
            c = self.text[self.pos]
            if c == "\\":
                if self.pos + 1 >= len(self.text):
                    break
                buf.append(self.text[self.pos + 1])
                self.pos += 2
            elif c == quote:
                self.pos += 1
                return "".join(buf)
            else:
                buf.append(c)
                self.pos += 1
        raise self.error("unterminated string")


@lru_cache(maxsize=4096)
def parse(text: str) -> Query:
    return _Parser(text).parse()


def _select(seg: Segment, node: Any) -> list[Any]:
    if isinstance(seg, Name):
        if isinstance(node, dict) and seg.name in node:
            return [node[seg.name]]
        return []
    if isinstance(seg, Index):
        if isinstance(node, list) and -len(node) <= seg.index < len(node):
            return [node[seg.index]]
        return []
    kids = _children(node)
    if isinstance(seg, Wildcard):
        return kids
    return [k for k in kids if _filter_matches(seg, k)]


def _children(node: Any) -> list[Any]:
    if isinstance(node, list):
        return list(node)
    if isinstance(node, dict):
        return list(node.values())
    return []


_NOTHING = object()


def _filter_matches(f: Filter, item: Any) -> bool:
    val: Any = item
    for seg in f.path:
        found = _select(seg, val)
        if not found:
            val = _NOTHING
            break
        val = found[0]
    if f.op is None:
        return val is not _NOTHING
    return compare(val, f.op, f.literal)


def _is_number(v: Any) -> bool:
    # bool is an int in python, don't want that here
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def to_decimal(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if isinstance(v, float):
        return Decimal(repr(v))  # repr = shortest round-trip string, so 0.1 stays 0.1
    return Decimal(v)


def compare(left: Any, op: str, right: Any) -> bool:
    """RFC 9535 comparison rules. Numbers vs numbers, strings vs strings,
    everything else only == / !=. Mixed types -> False (or True for !=)."""
    if _is_number(left) and _is_number(right):
        a, b = to_decimal(left), to_decimal(right)
    elif isinstance(left, str) and isinstance(right, str):
        a, b = left, right
    else:
        same = left is right or (type(left) is type(right) and left == right)
        if op == "==":
            return same
        if op == "!=":
            return not same
        return False
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    raise ValueError(f"unknown operator {op}")
