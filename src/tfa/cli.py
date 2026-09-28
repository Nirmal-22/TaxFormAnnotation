"""tfa render | plan | explain | lint | bootstrap | grid"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

from .bootstrap import bootstrap
from .lint import lint
from .model import SCHEMA_PATH, AnnotationError, load_annotation
from .pdf import SourceMismatch, render_grid, render_pdf
from .plan import Diagnostic, TextOp, build_plan


def load_data(path: str | Path):
    # Decimal, never float - 0.1 + 0.2 on a tax form is not ok
    return json.loads(Path(path).read_text(), parse_float=Decimal)


def _print_diagnostics(diags: list[Diagnostic]) -> None:
    order = {"error": 0, "warning": 1, "info": 2}
    for d in sorted(diags, key=lambda d: order[d.severity]):
        print(d, file=sys.stderr)


def _output(path: str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def cmd_render(args) -> int:
    ann = load_annotation(args.annotation)
    plan = build_plan(ann, load_data(args.data))
    _print_diagnostics(plan.diagnostics)
    if plan.errors and not args.allow_errors:
        print(f"{len(plan.errors)} error(s); nothing written (use --allow-errors to write anyway)", file=sys.stderr)
        return 1
    render_pdf(ann, plan, _output(args.output), debug=args.debug, verify_source=not args.no_verify)
    printed = len({op.field for op in plan.ops})
    print(f"wrote {args.output}: {printed} fields printed, {len(plan.statements)} statement(s)")
    return 1 if plan.errors else 0


def cmd_plan(args) -> int:
    ann = load_annotation(args.annotation)
    plan = build_plan(ann, load_data(args.data))
    _print_diagnostics(plan.diagnostics)
    doc = {"form": ann.form.id, "coordinates": {"unit": "pt", "origin": "top-left"}} | plan.to_dict()
    text = json.dumps(doc, indent=2, default=str) + "\n"
    if args.output:
        _output(args.output).write_text(text)
        print(f"wrote {args.output}: {len(plan.ops)} draw operations")
    else:
        sys.stdout.write(text)
    return 1 if plan.errors else 0


def cmd_explain(args) -> int:
    ann = load_annotation(args.annotation)
    plan = build_plan(ann, load_data(args.data))

    texts: dict[str, list[str]] = {}
    for op in plan.ops:
        if isinstance(op, TextOp):
            texts.setdefault(op.field, []).append(op.run.text)
    marked: dict[str, list[str]] = {}  # choice boxes are named "field=value"
    for box in plan.boxes:
        if box.status == "printed" and box.field not in texts:
            fid, _, val = box.field.partition("=")
            marked.setdefault(fid, []).append(val)
    errors = {d.field: d for d in plan.errors}

    seen = set()
    for box in plan.boxes:
        fid = box.field.partition("=")[0]
        if fid in seen or (args.printed_only and fid not in texts and fid not in marked and fid not in errors):
            continue
        seen.add(fid)
        if fid in errors:
            shown = f"ERROR {errors[fid].code}: {errors[fid].message}"
        elif fid in texts:
            runs = texts[fid]
            comb = len(runs) > 1 and all(len(r) == 1 for r in runs)
            shown = "".join(runs) + "  (comb)" if comb else " / ".join(runs)
        elif fid in marked:
            shown = " ".join(["[X]", ", ".join(v for v in marked[fid] if v)]).rstrip()
        else:
            shown = f"({box.status})"
        print(f"p{box.page}  {fid:44} {shown}")
    for st in plan.statements:
        print(f"statement: {st.title} ({len(st.rows)} rows)")
    # errors already shown inline aren't repeated, but ones with no box
    # (whole repeat blew up) still need to come out somewhere
    _print_diagnostics([d for d in plan.diagnostics if d.severity != "error" or d.field not in seen])
    return 1 if plan.errors else 0


def cmd_lint(args) -> int:
    status = 0
    for path in args.annotations:
        try:
            ann = load_annotation(path)
        except AnnotationError as e:
            for p in e.problems:
                print(f"ERROR   SCHEMA {path}{p}", file=sys.stderr)
            status = 1
            continue
        diags = lint(ann, check_pdf=not args.no_pdf)
        _print_diagnostics(diags)
        errs = sum(d.severity == "error" for d in diags)
        warns = sum(d.severity == "warning" for d in diags)
        print(f"{path}: {errs} error(s), {warns} warning(s)")
        status = status or int(errs > 0)
    return status


def cmd_bootstrap(args) -> int:
    out = _output(args.output)
    schema_ref = os.path.relpath(SCHEMA_PATH, out.resolve().parent)
    doc = bootstrap(args.pdf, args.form_id, args.title, relative_to=out.resolve().parent, schema_ref=schema_ref)
    out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"wrote {out}: {len(doc['fields'])} fields from the PDF's form widgets")
    return 0


def cmd_grid(args) -> int:
    render_grid(args.pdf, _output(args.output), args.step)
    print(f"wrote {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tfa", description="Tax Form Annotation tools")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("render", help="print data onto a form")
    p.add_argument("annotation")
    p.add_argument("data")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--debug", action="store_true", help="outline and label every field")
    p.add_argument("--allow-errors", action="store_true", help="write the PDF even if some fields failed")
    p.add_argument("--no-verify", action="store_true", help="skip the source PDF checksum check")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("plan", help="write the draw operations as JSON (for other renderers)")
    p.add_argument("annotation")
    p.add_argument("data")
    p.add_argument("-o", "--output", help="file to write; stdout if omitted")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("explain", help="list what each field would print")
    p.add_argument("annotation")
    p.add_argument("data")
    p.add_argument("--printed-only", action="store_true")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("lint", help="check annotation files")
    p.add_argument("annotations", nargs="+")
    p.add_argument("--no-pdf", action="store_true", help="skip checks against the source PDF")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("bootstrap", help="draft an annotation from a fillable PDF's widgets")
    p.add_argument("pdf")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--form-id", required=True)
    p.add_argument("--title")
    p.set_defaults(func=cmd_bootstrap)

    p = sub.add_parser("grid", help="overlay a coordinate grid to measure boxes by hand")
    p.add_argument("pdf")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--step", type=float, default=10)
    p.set_defaults(func=cmd_grid)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (AnnotationError, SourceMismatch) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
