# Design decisions and future work

This document explains why TFA looks the way it does: the alternatives considered, what was traded away, and what I would build next. The normative rules are in [SPEC.md](SPEC.md).

## Goals

1. **Unambiguous.** Two independent implementations given the same annotation and data should put the same characters in the same places. The spec defines the layout math, rounding and comparison rules for that reason.
2. **Safe by default.** A tax form with a wrong number is worse than one with a blank box. Anything surprising is an error, and errored fields are not printed.
3. **Easy to annotate and review.** An annotation should read like the form: one entry per box, labelled with its line number, pointing at an obvious place in the data.
4. **Independent of rendering technology.** The format describes intent; any PDF library, or a canvas on screen, can draw it.

## Decisions

### JSON, with JSON Schema as the contract

JSON is what every service at a tax platform already speaks, diffs well in code review, and needs no special parser. JSON Schema makes the format self-documenting and machine-checked. An annotation file that references it gets autocomplete, hover docs and red squiggles in VS Code with zero tooling work, which matters for annotators who are not engineers.

*Considered:* XML with XSD, which has similar strengths but is heavier and less familiar to web teams. Language classes as the primary definition, which tie the format to one language. The Python dataclasses in `src/tfa/model.py` are a typed view of the schema, not the definition of it.

### Points from the top-left corner, as displayed

PDF's own coordinates start at the bottom-left, depend on the page's crop box and rotation, and are invisible to someone looking at the page. TFA uses points (exact for PDFs, no DPI to agree on) measured from the visible top-left corner, the same frame as screenshots, image editors, browser canvases and the human eye. The conversion to PDF space is one line, done once in the renderer.

*Considered:* pixels, which depend on render DPI. Normalised 0–1 coordinates, which are resolution-free but unintuitive to read and review ("0.8235"?). The file still states `"coordinates": {"unit": "pt", "origin": "top-left"}` explicitly, so a reader needs no outside knowledge and a later version can add alternatives without ambiguity.

### An annotation is pinned to one exact PDF

The IRS re-issues forms, sometimes mid-season, and a box moving by 10 pt makes every coordinate wrong without any visible error. The annotation records the blank PDF's SHA-256, and rendering refuses a mismatch. A new revision is a new annotation file, never an edit, so old returns can always be re-printed exactly as filed.

### Referencing data: JSONPath (RFC 9535), deliberately restricted

The brief asks how to reference values in deeply nested data. Tax data is nested and list-heavy: several W-2s, a mix of 1099 types, dependents. The reference syntax needs member access, list indexes, "all of them" (for totals), and "the ones of this kind" (1099-INT versus 1099-DIV).

| Option | Verdict |
|---|---|
| Dot strings (`taxpayer.w2.0.wages`) | Home-grown and ambiguous for keys like `1099` or `1a`; no filters |
| JSON Pointer (RFC 6901) | Standard but single-value only: no wildcards, no filters |
| JMESPath / JSONata | Full expression languages; annotations would drift into code |
| **JSONPath, RFC 9535 subset** | Standardised in 2024, widely known, has wildcards and filters |

I kept the parts that make a path point at an obvious place and removed the rest. Descendant search (`$..amount`) is gone because it matches anything, anywhere. Slices and functions are gone because nothing on a form needs them. Filters are a single comparison. The one extension, a leading `@` for "the current item" inside repeats, maps back to standard JSONPath by substitution. The parser is under 300 lines with no dependencies, so porting it is cheap.

### Declarative, with aggregation but no arithmetic

An annotation can sum, count, pick first or join, because some lines are literally "total of box 1 of your W-2s". It cannot add, subtract, compare lines or branch on tax rules. Taxable income, credits and phase-outs belong in the tax engine, where they are tested as tax logic. Annotations stay reviewable by comparing them against the paper form, and a bug in tax math cannot hide in a layout file. Conditions (`when`, `checked`) exist only to decide *whether* to print, such as spouse fields on joint returns.

### Four field types

`text`, `checkbox`, `choice` and `repeat` cover every box in both examples: all 199 boxes of the 2025 Form 1040 (the same count as the widgets in the IRS PDF) and all 21 on the W-9. Recurring tax-form patterns are options on these rather than new types:

* **Grouped digit cells** (SSN 3-2-4, EIN 2-7, routing numbers) are `comb` groups. One field is one value, one source and one diagnostic, instead of three fields each slicing the SSN.
* **Separate dollars and cents boxes** are two fields with `part: dollars` / `part: cents`.
* **Radio groups** (filing status, Yes/No) are `choice`, which also makes "the data says `married` but the options say `married_filing_jointly`" a loud error instead of an unmarked form.
* **Lists** (dependents) are `repeat` with an offset per item. It handles rows or, as on the 2025 1040, columns.

### Styles carry formats

Most of a 1040 is the same kind of box: a right-aligned whole-dollar amount with losses in parentheses. Putting `format` inside styles lets one `"amount"` style express that once, with `extends` for variations (`"amountDash"` prints zero as `-0-` on lines whose instructions ask for it). The cascade is deliberately simple: whole-property override, no deep merge, no selectors.

### Layout rules are fully specified

"Centre the text" means different things in different libraries, so the spec gives the formulas. Vertical centring uses the font's cap height, so digits and capitals, which are most of a tax form, look centred. Shrink-to-fit computes the exact size and rounds down to 0.1 pt, so the text always fits. Wrapping has a defined algorithm. This is what lets a second implementation match the first.

### Fail closed, with stable diagnostic codes

Every problem is a diagnostic with a severity, a stable code (`MASK_MISMATCH`, `CHOICE_NO_MATCH`, …) and the exact field instance (`dependents[2].ssn`). Errored fields are not drawn, and the CLI writes no PDF at all when there are errors unless asked to. Stable codes let calling systems route problems: a data team fixes `MISSING_REQUIRED`, an annotator fixes `TEXT_OVERFLOW`. Exact decimal arithmetic and IRS half-up rounding are part of the same principle.

### Print on top of the form rather than fill its form fields

Many IRS PDFs are fillable, so why not just set their fields?

* Many forms are not fillable: state forms, older revisions, scanned forms. An overlay works on anything.
* Field names are opaque (`f1_47[0]`) and change between revisions.
* How filled fields look depends on each viewer's font and appearance handling. IRS PDFs also carry XFA, which some viewers prefer over the page content.
* We want our own formatting, overflow handling and continuation statements.

The fillable fields are still useful: `tfa bootstrap` harvests their exact boxes, cell counts and alignment into a draft annotation, turning hours of measuring into minutes of labelling. The renderer strips them from the output so they cannot cover the printed values.

### Plan first, draw second

`build_plan()` turns annotation and data into a flat list of positioned text runs and marks, plus diagnostics, without touching a PDF library. `render_pdf()` only draws. The interesting logic is testable without PDFs, a web preview could draw the same plan on a canvas, and porting to another language means re-implementing the plan while any drawing library will do.

### What was intentionally left out

* **Descendant JSONPath, arithmetic, scripting:** they make annotations powerful and unreviewable (see above).
* **Per-field `format` separate from `style`:** one mechanism is easier to learn; inline styles cover one-offs.
* **Auto-shrinking comb characters:** a digit that does not fit its cell indicates a wrong annotation, which should be fixed rather than hidden.

## Tooling in this repository

| Tool | Why |
|---|---|
| `tfa bootstrap` | Draft an annotation from a fillable PDF's widgets, grouping XFA-style radio buttons into `choice` fields |
| `tfa grid` | Overlay a labelled coordinate grid for measuring forms without widgets. The W-9 signature date was placed this way. |
| `tfa lint` | Schema plus semantic checks: ids, bounds of every repeat slot, style cycles, paths, overlaps, PDF hash |
| `tfa explain` | "What would each box print?" as text; quick QA without opening PDFs |
| `tfa plan` | The draw operations as JSON, so an application with its own PDF stack can print without the Python renderer |
| `tfa render --debug` | Outline every box, coloured by outcome (printed, empty, hidden, error) and labelled with its id |

## Future enhancements

**Authoring**

* **Visual editor.** Draw boxes on the PDF in the browser, pick data paths from a sample return with autocomplete, and see a live preview from the plan. `bootstrap`, `grid` and the debug overlay are the command-line seeds of this.
* **Box detection for non-fillable forms.** Find ruled boxes and cell dividers with line detection (or a small vision model) and propose rects and comb groups.
* **Revision carry-forward.** When a new form revision is published, match fields between old and new PDFs by nearby printed text, carry annotations across, and flag only the boxes that moved or changed.
* **Coverage report.** List the widgets or boxes in the PDF that no field covers.

**Correctness at scale**

* **Conformance suite.** Language-neutral cases of annotation, data and expected plan JSON, so a TypeScript or Go implementation can prove it matches the reference.
* **Golden-image tests in CI.** Render a library of sample returns and pixel-diff them against approved images on every annotation change.
* **Data contracts.** Derive the set of paths an annotation reads, and validate the tax engine's output against it before rendering. A renamed field in the engine then fails in CI, not on a printed form.

**Format**

* **Statement headers.** Continuation statements should carry the taxpayer's name and SSN, as the IRS requires for attachments. The natural fix is a `statement.header` source list.
* **Repeats spanning pages**, and overflow into a form's own continuation pages (e.g. Schedule B, Form 8949).
* Barcodes (2-D barcodes on some state forms), images, rotated or vertical text, letter spacing.
* Cross-form references (Schedule 1 line 10 → 1040 line 8) for previews before the engine has run. These should stay read-only references, not calculations.
* A TypeScript package generated from the JSON Schema for front-end use.

**Operations**

* Batch rendering with parsed annotations cached per form revision; a plan is cheap, and PDF merging is the cost.
* PII redaction in diagnostics before they reach logs.
* Tagged, accessible PDF output.
