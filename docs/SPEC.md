# Tax Form Annotation (TFA) — Specification 1.0

TFA is a JSON format for describing **where** each box on a tax form is, **which value** from a data set goes in it, and **how** that value is printed. An annotation file plus a data set is enough to print a completed form. Any team can do that with its own code: the rules below define exactly what gets drawn.

| | |
|---|---|
| Schema | [`schema/tfa.schema.json`](../schema/tfa.schema.json) (JSON Schema 2020-12) |
| Reference implementation | [`src/tfa`](../src/tfa) (Python) |
| Examples | [`forms/irs-1040-2025/f1040.tfa.json`](../forms/irs-1040-2025/f1040.tfa.json), [`forms/irs-w9-2024/fw9.tfa.json`](../forms/irs-w9-2024/fw9.tfa.json) |
| File extension | `.tfa.json` |

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119. The JSON Schema is normative for structure. This document is normative for behaviour.

---

## 1. Model

An annotation describes one revision of one form, such as the 2025 Form 1040. Printing a field runs the same pipeline every time:

```
 data set ──► resolve ──► transform ──► format ──► layout ──► draw
             (source)    (optional)    (style)    (style,     (text runs,
                                                   rect)       marks)
```

1. **Resolve.** The field's `source` selects a value from the data set with a path such as `$.household.primary.ssn`.
2. **Transform.** Optional operations normalise the value, for example keeping only the digits of `400-00-1001`.
3. **Format.** The value becomes the exact text to print, such as `(1,250)` for a loss.
4. **Layout.** The text is fitted into the field's box: alignment, padding, shrinking and one character per cell.
5. **Draw.** A backend draws positioned text runs and checkbox marks on top of the blank PDF.

The annotation is purely declarative. It never contains data, it contains no executable code, and it performs no tax calculation beyond simple aggregation (§6.3).

## 2. File structure

```json
{
  "$schema": "../../schema/tfa.schema.json",
  "specVersion": "1.0",
  "form": { "id": "irs-1040-2025", "title": "...", "source": { "file": "f1040.pdf", "sha256": "..." },
            "pages": [{ "width": 612, "height": 792 }, { "width": 612, "height": 792 }] },
  "coordinates": { "unit": "pt", "origin": "top-left" },
  "fonts":  { },
  "styles": { "default": { "font": "Helvetica", "fontSize": 9 }, "amount": { "align": "right" } },
  "fields": [ ]
}
```

| Property | Required | Meaning |
|---|---|---|
| `specVersion` | yes | Always `"1.0"` for this version. |
| `form` | yes | Identifies the form revision and the exact blank PDF (§3). |
| `coordinates` | yes | Always `{"unit": "pt", "origin": "top-left"}` in 1.0. It is stated explicitly so a reader needs no outside knowledge to interpret a rect, and so later versions can add other systems. |
| `fonts` | no | Custom TrueType fonts (§8.5). |
| `styles` | no | Named, reusable presentation (§8). |
| `fields` | yes | What to print (§5). |

Unknown properties are rejected everywhere. A typo such as `"colour"` fails validation instead of being silently ignored.

## 3. Form identity

```json
"form": {
  "id": "irs-1040-2025",
  "title": "U.S. Individual Income Tax Return",
  "issuer": "IRS", "formNumber": "1040", "taxYear": 2025, "revision": "2025 (Created 9/5/25)",
  "source": { "file": "f1040.pdf", "url": "https://www.irs.gov/pub/irs-pdf/f1040.pdf",
              "sha256": "3d31c226df0d189ced80e039d01cf0f8820c1019681a0f0ca6264de277b7e982" },
  "pages": [ { "width": 612, "height": 792 }, { "width": 612, "height": 792 } ]
}
```

* Coordinates are only meaningful for the exact PDF they were measured on. `source.sha256` pins that file. A renderer MUST refuse to print on a PDF whose hash differs, unless explicitly overridden. The IRS re-issues forms mid-year, and a shifted layout must fail loudly rather than print into the wrong boxes.
* `pages[i]` is the displayed size of page *i + 1*, in points. A renderer MUST check that the PDF's page count and sizes match.
* `id` names one form revision. A new revision of a form is a new annotation file with a new `id`. Revisions are never edited in place.
* `source.file` is resolved relative to the annotation file.

## 4. Coordinates and geometry

All positions are in **PDF points** (1 pt = 1/72 inch). They are measured from the **top-left corner** of the page as displayed, with **y increasing downward**.

"As displayed" means relative to the page's crop box (its visible area), after applying the page's `/Rotate`. Annotators measure what they see, in any viewer or image tool, and never deal with PDF internals.

```
(0,0) ─────────────── x ──►        rect = { "x": 504, "y": 450, "width": 72, "height": 12 }
  │     ┌───────────┐
  y     │ (x,y)     │ height       (x, y) is the rect's top-left corner
  │     └───────────┘
  ▼        width
```

**Converting to PDF user space** (unrotated page, crop box `[L, B, R, T]`):

```
pdf_x = L + x          pdf_y = T - y
```

Rotated pages are normalised first. The reference implementation bakes the rotation into the page content. For other backends, the reference bootstrapper (`src/tfa/bootstrap.py`, `pdf_rect_to_tfa`) gives the mapping for 90°, 180° and 270°.

**Converting to image pixels** at *dpi*: `px = pt × dpi / 72`. Because the origin is top-left, no flip is needed for raster images.

A `rect` is `{x, y, width, height}`, with `width` and `height` > 0. Rects SHOULD lie within the page; the linter reports those that do not.

## 5. Fields

`fields` is an array of field objects. Every field has:

| Property | Required | Meaning |
|---|---|---|
| `id` | yes | Unique among its siblings. Letters, digits, `_`, `-`; starts with a letter. |
| `type` | yes | `text`, `checkbox`, `choice` or `repeat`. |
| `page` | top level only | 1-based page number. Fields inside a `repeat` inherit it and MUST NOT set it. |
| `label` | no | Human-readable name, typically the line number and caption, e.g. `"1a Total amount from Form(s) W-2, box 1"`. Used in QA tools, diagnostics and continuation statements. |
| `description`, `tags` | no | Free-form notes for annotators and tools. |
| `when` | no | A condition (§6.6). The field is printed only if it holds. |

Diagnostics identify each printed field instance by a **qualified id**, such as `dependents[2].ssn` (§5.4).

### 5.1 `text`: print a value in a box

```json
{ "id": "line1a", "type": "text", "page": 1, "label": "1a Total amount from Form(s) W-2, box 1",
  "rect": { "x": 504, "y": 450, "width": 72, "height": 12 },
  "source": { "path": "$.documents.w2[*].boxes['1']", "aggregate": "sum" },
  "style": "amount" }
```

| Property | Meaning |
|---|---|
| `rect` | The box. |
| `source` | Where the value comes from (§6.3). |
| `style` | A style name or an inline style (§8). |
| `comb` | Optional: print one character per cell (§8.4). |
| `required` | If `true`, a missing value is an error (`MISSING_REQUIRED`). Default `false`: missing values leave the box empty. |

### 5.2 `checkbox`: mark a box when a condition holds

```json
{ "id": "line12dYouBlind", "type": "checkbox", "page": 2, "rect": { "x": 311.6, "y": 74, "width": 8, "height": 8 },
  "checked": { "path": "$.household.primary.blind", "op": "truthy" } }
```

`checked` is a condition (§6.6). The mark's shape comes from the style (§8.6).

### 5.3 `choice`: mark the option matching a value

A group of boxes where the value selects which to mark: filing status, Yes/No pairs, checking/savings.

```json
{ "id": "filingStatus", "type": "choice", "page": 1, "source": { "path": "$.filing.status" }, "required": true,
  "options": [
    { "value": "single",                 "label": "Single",                  "rect": { "x": 97.6, "y": 206, "width": 8, "height": 8 } },
    { "value": "married_filing_jointly", "label": "Married filing jointly",  "rect": { "x": 97.6, "y": 218, "width": 8, "height": 8 } }
  ] }
```

* The option whose `value` equals the resolved value is marked. Equality follows §6.2: `true` matches `true` but not `"true"` or `1`.
* If the value is a list, every matching option is marked (multi-select).
* A value that matches no option is an error (`CHOICE_NO_MATCH`), and nothing is marked. An unexpected enum value must never mark a random box.
* A missing value marks nothing; it is an error only if `required` is set.

### 5.4 `repeat`: print a list into repeated rows or columns

Dependents, W-2s and Schedule B payers are lists printed into a fixed number of slots.

```json
{ "id": "dependents", "type": "repeat", "page": 1,
  "source": { "path": "$.household.dependents" },
  "maxItems": 4,
  "offset": { "x": 108, "y": 0 },
  "overflow": { "strategy": "statement", "title": "Form 1040 (2025): Dependents (continued)" },
  "fields": [
    { "id": "firstName", "type": "text", "label": "First name",
      "rect": { "x": 145, "y": 309, "width": 106.25, "height": 12 }, "source": { "path": "@.name.first" } }
  ] }
```

* `source` MUST resolve to a list (`NOT_A_LIST` otherwise). A missing list is treated as empty.
* Child `fields` are positioned for **item 0**. Item *i* is shifted by `offset × i`, or by `offsets[i]` for unevenly spaced slots. `offsets` MUST have `maxItems` entries. Use `offset: {x: 0, y: 12}` for rows and `{x: 108, y: 0}` for columns (the 2025 Form 1040 lists dependents in columns).
* Inside a repeat, paths starting with `@` are relative to the current item (§6.2).
* Qualified ids are `repeatId[i].childId`, e.g. `dependents[2].ssn`. Repeats may nest.
* **Overflow**, when the list has more than `maxItems` items:

| `strategy` | Behaviour |
|---|---|
| `error` (default) | `REPEAT_OVERFLOW` error; nothing in the repeat is printed. |
| `truncate` | Print the first `maxItems`; `REPEAT_TRUNCATED` warning. |
| `statement` | Print the first `maxItems`; the rest go on an appended **continuation statement**, a table whose columns are the child fields (header = `label`, cell = the text the field would print, `X` for a checked checkbox, the option `label` for a choice). `REPEAT_STATEMENT` info diagnostic. |

This matches IRS practice ("If more than four dependents, … attach a statement"). The "check here" box itself is an ordinary checkbox:

```json
"checked": { "source": { "path": "$.household.dependents[*]", "aggregate": "count" }, "op": "gt", "value": 4 }
```

## 6. Data references

### 6.1 The data set

The data set is any JSON document; TFA imposes no schema on it. A renderer MUST read JSON numbers as exact decimals, never binary floating point. `2.675` must round to `2.68`, and sums of cents must be exact.

### 6.2 Paths

Values are referenced with a subset of **JSONPath (RFC 9535)**, the IETF standard, so existing libraries and developer knowledge carry over.

| Syntax | Meaning | Example |
|---|---|---|
| `$` | root of the data set | `$` |
| `@` | current item (inside a repeat, or inside a filter) | `@.name.first` |
| `.name` | object member | `$.household.primary.ssn` |
| `['name']` | member with any characters | `$.documents['1099']`, `$.boxes['1a']` |
| `[n]`, `[-n]` | list element (negative counts from the end) | `$.filing.otherDesignations[0]` |
| `[*]`, `.*` | every child | `$.documents.w2[*].boxes['1']` |
| `[?@.p <op> literal]` | children where the comparison holds | `$.documents['1099'][?@.form == 'INT']` |
| `[?@.p]` | children where `p` exists | `$.accounts[?@.closedOn]` |

Filter operators are `== != < <= > >=`. Literals are `'string'`, `"string"`, numbers, `true`, `false` and `null`. Filter paths use only member and index segments.

**Not supported, deliberately:** descendant search (`..`), slices and functions. `$..amount` would silently pick up amounts from anywhere in the document. Every TFA path names exactly where its value lives.

**Evaluation.** A path selects a list of values, possibly empty:

* A missing member, an out-of-range index, or a member of a non-object selects nothing. It is not an error.
* **Comparison** (filters, conditions, choice matching): numbers compare numerically, strings by code point (so ISO 8601 dates order correctly), and any other values only by equality. Comparing different types, or a missing value, is false, except `!=`, which is then true. There is no type coercion: `"10"` does not equal `10`.

Replacing a leading `@` with the item's absolute location turns any TFA path into a valid RFC 9535 query.

### 6.3 Sources

A `source` produces **one** value. It has exactly one of these forms:

| Form | Example | Result |
|---|---|---|
| `path` | `{"path": "$.household.address.city"}` | The selected value. More than one match is an error (`AMBIGUOUS_VALUE`) unless an `aggregate` is given. |
| `path` + `aggregate` | `{"path": "$.documents.w2[*].boxes['2']", "aggregate": "sum"}` | `sum`, `min`, `max` (numbers); `count`; `first`, `last`; `join` (with `separator`, default `", "`). |
| `literal` | `{"literal": "See attached"}` | A constant. |
| `template` | `{"template": "{$.p.name.first} {$.p.name.middleInitial}"}` | Text with `{path}` placeholders. |
| `coalesce` | `{"coalesce": [{"path": "$.a"}, {"path": "$.b"}]}` | The first source that has a value. |

Every form also accepts `default` (used when there is no value) and `transform` (§6.5).

**Aggregates** ignore `null`s. On an empty selection, `count` is `0` and every other aggregate has no value. A W-2 total with no W-2s leaves the line blank rather than printing `0`.

**Templates:** each placeholder MUST select at most one value; a missing one becomes the empty string. The result is then tidied: runs of spaces are collapsed, each line is trimmed, and empty lines are dropped. An empty result has no value, so `"{first} {middle} {last}"` works without a middle name. `\n` in a template is a line break (§8.3). `{{` and `}}` are literal braces.

> **Aggregation, not calculation.** Aggregates exist for lines that are literally a total of source documents ("Total amount from Form(s) W-2, box 1"). Lines that depend on tax law (AGI, taxable income, credits) MUST come from the tax engine's output, e.g. `$.calculation.form1040['11a']`. Annotations stay reviewable by non-engineers, and tax logic lives in one tested place.

### 6.4 Missing values

A value is **missing** if the path selects nothing, or it is `null`, or it is `""`. Resolution order:

1. Resolve the source.
2. If missing and `default` is set, use `default`.
3. Apply `transform`s in order. A transform producing a missing value stops here.
4. Still missing: the field prints nothing. That is an error only if `required: true`.

### 6.5 Transforms

| `op` | Effect | Example |
|---|---|---|
| `digits` | keep only 0-9 | `"400-00-1001"` → `"400001001"` |
| `slice` | substring `[start, end)`; negatives count from the end | `{"op": "slice", "start": 0, "end": 5}` on a ZIP+4 |
| `map` | table lookup; booleans and numbers are looked up by their JSON text (`true`, `12`) | `{"op": "map", "values": {"s_corporation": "S"}}`. A `null` entry maps to no value (the box stays empty). No match and no `default` → `MAP_NO_MATCH`. |
| `abs` / `negate` | numeric sign | print a loss as a positive number in a "loss" box |

### 6.6 Conditions

Used by `when` (every field type) and `checked` (checkboxes).

```json
{ "path": "$.filing.status", "op": "in", "value": ["married_filing_jointly", "married_filing_separately"] }
{ "path": "$.household.primary.dateOfBirth", "op": "lt", "value": "1961-01-02" }
{ "source": { "path": "$.household.dependents[*]", "aggregate": "count" }, "op": "gt", "value": 4 }
{ "all": [ … ] }   { "any": [ … ] }   { "not": { … } }
```

| `op` | True when |
|---|---|
| `eq`, `ne`, `gt`, `gte`, `lt`, `lte` | the comparison (§6.2) holds against `value` |
| `in`, `notIn` | the value equals / equals none of the items in the `value` list |
| `exists`, `notExists` | the value is (not) missing |
| `truthy`, `falsy` | truthy = not missing, not `false`, not `0`, not an empty string or list |

With a bare `path`, `exists` and `truthy` ask whether **any** selected value qualifies. So `{"path": "$.dependents[*]", "op": "exists"}` means "has at least one dependent". The comparison operators require at most one selected value.

## 7. Formats

A style's `format` turns a value into text. The default format is `text`.

| `type` | Options (defaults) | Examples |
|---|---|---|
| `text` | `case`: `none` \| `upper` \| `lower` | `Rivera` → `RIVERA` |
| `number` | `decimals` (0), `thousands` (true), `negative`: `minus` \| `parens` (minus), `zero`: `show` \| `blank` \| `dash` (show) | `1234.5` → `1,235` |
| `currency` | as `number`, plus `decimals` (2), `symbol` (`""`), `part`: `whole` \| `dollars` \| `cents` | `-1250` → `(1,250)`; `symbol: "$"` → `$1,250.00` |
| `percent` | `decimals` (0), `input`: `fraction` \| `percent`, `symbol` (true) | `0.2575` → `25.8%` |
| `date` | `pattern` (`MM/DD/YYYY`); tokens `YYYY YY MM M DD D` | `"2025-04-15"` → `04/15/2025`; `MM` alone for a month box |
| `mask` | `pattern`: string or list; `#` = next letter/digit | `###-##-####`; `["#####", "#####-####"]` for ZIP / ZIP+4 |

Rules:

* **Rounding** is half-up, away from zero (the IRS rule: drop amounts under 50 cents, round 50–99 cents up). It uses exact decimal arithmetic. `-0` is never printed.
* **Zero** is tested after rounding. `blank` prints nothing (the box stays empty but the field is not "missing"). `dash` prints `-0-`, as the IRS asks for on lines such as 1040 line 15.
* **Split amounts:** `part: dollars` prints the rounded amount's dollars, carrying the negative indicator: `(7)`. `part: cents` prints its two cent digits, unsigned. Two fields with the same source and different `part`s fill a form's separate dollars and cents boxes.
* Numeric formats accept JSON numbers and numeric strings (`"1,050.25"`). Anything else, including booleans, is `TYPE_MISMATCH`.
* `date` input MUST be ISO 8601 (`YYYY-MM-DD`, optionally followed by a time). Anything else is `INVALID_DATE`.
* `mask` picks the first pattern whose `#` count equals the number of letters and digits in the value. If none matches, it is `MASK_MISMATCH`: a 4-digit ZIP is an error, never a malformed print.
* The `text` format rejects booleans. Use a `map` transform (`{"true": "Yes", "false": "No"}`) or a checkbox.

## 8. Styles and layout

### 8.1 The cascade

A field's `style` is a **name** (`"amount"`) or an **inline style** (`{"extends": "amount", "fontSize": 8}`). The effective style is built from, lowest priority first:

1. The built-in defaults (below).
2. The style named `default`, if any.
3. The `extends` chain, most distant ancestor first.
4. The style itself.

Properties override whole: `format` and `padding` are replaced, not merged. `default` MUST NOT extend another style, and cycles are errors.

| Property | Built-in default | Meaning |
|---|---|---|
| `font` | `Helvetica` | Standard PDF font (§8.5) or a key of `fonts` |
| `fontSize` | `9` | points |
| `minFontSize` | `6` | smallest size `shrink` / `wrap` may use |
| `color` | `#000000` | `#RRGGBB` |
| `align` | `left` | `left` \| `center` \| `right` |
| `valign` | `middle` | `top` \| `middle` \| `bottom` |
| `padding` | 1 top/bottom, 2 left/right | number, or `{top, right, bottom, left}` |
| `overflow` | `shrink` | `shrink` \| `wrap` \| `truncate` \| `error` |
| `lineHeight` | `1.15` | line spacing ÷ font size (for `wrap`) |
| `format` | `{"type": "text"}` | §7 |
| `mark` | `cross` | checkbox / choice mark: `cross` \| `check` \| `dot` \| `fill` |
| `markInset` | `1` | points between the box edge and the mark |

Styles carry formats as well as typography, so one `"amount"` style can mean "right-aligned, whole dollars, losses in parentheses, zero left blank" for 60 lines of a 1040.

### 8.2 Placing a line of text

Let the **inner box** be the rect minus padding, `c` the font's cap height and `d` its descent (negative), both per point of font size. For font size `s`:

* **x:** `left` → inner left; `right` → inner right − text width; `center` → centred.
* **baseline (y grows downward):**
  * `top`: inner top + `c·s` (capitals touch the top edge)
  * `middle`: inner top + (inner height + `c·s`) / 2 (capitals and digits are optically centred)
  * `bottom`: inner bottom + `d·s` (descenders touch the bottom edge)

Centring on cap height rather than the em box is what makes digits look centred in a 12 pt box, and most of a tax form is digits. Cap heights for the standard fonts are the AFM values (Helvetica 0.718, Courier 0.562, Times-Roman 0.662). Implementations MUST use them so output matches across implementations.

Newlines in a single-line field are printed as spaces.

### 8.3 Overflow

When the text is wider than the inner box:

| `overflow` | Behaviour |
|---|---|
| `shrink` | Reduce the size to `s × innerWidth / textWidth`, rounded **down** to 0.1 pt. If that is below `minFontSize`, it is an error (`TEXT_OVERFLOW`). |
| `truncate` | Drop characters from the end until the text fits; `TEXT_TRUNCATED` warning. Use only where a partial value is acceptable, never for amounts or identifiers. |
| `error` | `TEXT_OVERFLOW` immediately. |
| `wrap` | Multi-line. Greedy word wrap at spaces, honouring `\n`; a word longer than a line is split. Lines are `lineHeight × s` apart and positioned as a block by `valign`: `top` puts the first line's cap top at the inner top, `bottom` puts the last line's descender at the inner bottom, and `middle` centres the span from the first cap top to the last baseline. The block needs `(c − d)·s + (n − 1)·lineHeight·s` of height. If it does not fit, the size drops in 0.5 pt steps and the text is re-wrapped, down to `minFontSize`, then `TEXT_OVERFLOW`. |

### 8.4 Comb fields (one character per cell)

SSNs, EINs, routing and account numbers are often printed into individual cells:

```json
"comb": { "cells": 9 }
"comb": { "groups": [ { "x": 0, "width": 31.65, "cells": 3 },
                      { "x": 31.65, "width": 21.7, "cells": 2 },
                      { "x": 53.35, "width": 53.65, "cells": 4 } ] }
```

* `cells: n` divides the rect's full width into `n` equal cells.
* `groups` lists runs of equal cells at an `x` offset from the rect's left edge. Printed dividers are often not evenly spaced. On the 2025 Form 1040 the SSN's 3-2-4 groups have different cell widths, and the W-9 has dashes printed between its groups. One field with groups keeps the SSN one value, one source and one diagnostic.
* Each character is centred horizontally in its cell. Vertical placement follows §8.2; horizontal padding does not apply.
* `align` chooses the cells: `left` fills from the first cell, `right` ends at the last cell, `center` centres the run.
* A space leaves its cell empty. More characters than cells is `COMB_OVERFLOW`. Combs do not shrink.
* The formatted text should contain only what goes in the cells. Use the `digits` transform, not a mask with dashes: the form's printed dividers already group the digits.

### 8.5 Fonts

The standard PDF fonts are always available: `Helvetica`, `Courier`, `Times-Roman`, each with `-Bold`, `-Oblique`/`-Italic` and `-BoldOblique`/`-BoldItalic` variants. Other fonts are declared by file:

```json
"fonts": { "OCRB": { "file": "fonts/OCR-B.ttf" } }
```

A font not in either set is a lint error (`UNKNOWN_FONT`).

### 8.6 Marks

Checkbox and choice marks are vector shapes, not font glyphs, so they do not depend on font support. A mark is drawn in a square with side `min(width, height) − 2 × markInset`, centred in the rect. The shapes:

* `cross`: both diagonals, stroke width `max(0.6, 0.12 × side)`.
* `check`: a tick.
* `dot`: a filled circle of diameter `0.6 × side`.
* `fill`: a filled square.

## 9. Rendering

A conforming renderer:

1. MUST verify the source PDF: hash, page count and page sizes (§3), and refuse a plan that draws on a page the PDF does not have.
2. MUST evaluate every field as in §5–§8 and collect diagnostics (§10).
3. MUST **fail closed**: a field that produced an error is not drawn at all, and a choice with an unmatched value marks nothing. A missing value leaves a blank box; a wrong value on a tax return is worse than a blank one. The reference CLI also refuses to write any output if there are errors, unless `--allow-errors` is given.
4. MUST draw text as real text, not rasterised, so output stays searchable and crisp.
5. SHOULD remove the blank form's own interactive fields (AcroForm widgets and any XFA). Otherwise viewers may draw empty widgets on top of the printed values; IRS PDFs contain XFA, which Acrobat prefers over page content.
6. MUST append continuation statements for repeats with `overflow.strategy: "statement"`.

The reference implementation separates **planning** (`build_plan`: annotation + data → positioned text runs, marks and diagnostics, no PDF library involved) from **drawing** (`render_pdf`). A plan is a flat list of "draw this string at (x, baseline) in font F at size S on page P" and "draw this mark in this rect". Porting a renderer to another language or PDF engine is mostly a matter of re-implementing §6–§8.

An application with its own PDF stack can also skip that work and consume the plan directly. `tfa plan annotation data` writes it as JSON, in TFA page coordinates:

```json
{ "form": "irs-1040-2025", "coordinates": { "unit": "pt", "origin": "top-left" },
  "ops": [
    { "kind": "text", "page": 1, "field": "line2b", "x": 551.482, "baseline": 579.231,
      "text": "1,860", "font": "Helvetica", "size": 9.0, "color": "#000000" },
    { "kind": "mark", "page": 1, "field": "filingStatus", "rect": { "x": 97.6, "y": 218.0, "width": 8.0, "height": 8.0 },
      "mark": "cross", "inset": 1.0, "color": "#000000" } ],
  "statements": [ { "title": "...", "columns": [ "..." ], "rows": [ [ "..." ] ] } ],
  "diagnostics": [ { "severity": "info", "code": "REPEAT_STATEMENT", "field": "dependents", "message": "..." } ] }
```

Drawing a plan needs only two primitives: place a string with its baseline at a point (§4 gives the conversion to PDF user space), and draw a mark in a rect (§8.6).

## 10. Diagnostics

Every problem is reported with a severity, a stable code and the qualified field id.

| Code | Severity | Raised when |
|---|---|---|
| `MISSING_REQUIRED` | error | a `required` field has no value |
| `AMBIGUOUS_VALUE` | error | a path matched several values and no aggregate was given |
| `RELATIVE_PATH` | error | a path uses `@` outside a repeat |
| `TYPE_MISMATCH` | error | a value cannot be formatted as requested (e.g. text in a currency field) |
| `INVALID_DATE` | error | a date is not ISO 8601 |
| `MASK_MISMATCH` | error | a value has the wrong number of characters for its mask |
| `MAP_NO_MATCH` | error | a `map` transform has no entry for the value |
| `TEXT_OVERFLOW` | error | text does not fit, even at `minFontSize` |
| `COMB_OVERFLOW` | error | more characters than cells |
| `CHOICE_NO_MATCH` | error | a choice value matches no option |
| `NOT_A_LIST` | error | a repeat's source is not a list |
| `REPEAT_OVERFLOW` | error | too many items and `strategy: error` |
| `TEXT_TRUNCATED` | warning | `overflow: truncate` cut the text |
| `REPEAT_TRUNCATED` | warning | `strategy: truncate` dropped items |
| `REPEAT_STATEMENT` | info | items were moved to a continuation statement |

## 11. Validating annotations

Annotations are checked in two layers:

1. **JSON Schema** (`schema/tfa.schema.json`) covers structure, types and required properties. Referencing it with `$schema` gives annotators autocomplete, hover documentation and inline errors in editors such as VS Code, at no extra cost.
2. **Lint** (`tfa lint`) covers what a schema cannot express:

| Code | Severity | Check |
|---|---|---|
| `DUPLICATE_ID` | error | ids are unique among siblings |
| `PAGE_OUT_OF_RANGE` | error | the page exists |
| `RECT_OUT_OF_BOUNDS` | error | every rect, including every repeat slot, is on the page |
| `COMB_OUT_OF_RECT` | error | comb groups lie within the rect |
| `DUPLICATE_OPTION` | error | choice option values are distinct |
| `OFFSETS_LENGTH` | error | `offsets` has `maxItems` entries |
| `BAD_STYLE` | error | styles exist, have no cycles, and `default` does not extend |
| `UNKNOWN_FONT` / `FONT_NOT_FOUND` | error | fonts are standard or declared, and their files exist (a font that exists but cannot be loaded fails at plan time with an `AnnotationError`) |
| `BAD_PATH` | error | every path, including template placeholders, parses |
| `RELATIVE_PATH` | error | `@` is used only inside repeats |
| `SOURCE_MISMATCH` | error | the PDF exists and matches `sha256`, page count and sizes |
| `MULTI_VALUE_PATH` | warning | a path with a wildcard or filter but no aggregate (fails if several values match) |
| `OVERLAP` | warning | two boxes on a page overlap |

## 12. Versioning

* `specVersion` follows semantic versioning. 1.x releases only add optional properties, and a 1.0 renderer MUST reject files with a `specVersion` it does not know rather than guess.
* Form revisions are separate annotation files (§3). Keeping the annotation and the blank PDF side by side in version control gives a reviewable history of every change to a form.

## 13. Security and privacy

* Annotations are data, not code. There is no expression language or script, and paths and conditions cannot call functions. An annotation from an untrusted source can at worst print the wrong thing, and lint plus the debug overlay make that visible.
* Annotations never contain taxpayer data. They can be shared, reviewed and versioned freely. Filled PDFs and data sets contain PII and must be handled accordingly.
* Diagnostics include the offending value in their message to help debugging. Systems that ship diagnostics to logs should redact them.

## Appendix A: Path grammar

```
path        = root *segment
root        = "$" / "@"
segment     = "." name / "." "*" / "[" ws selector ws "]"
selector    = quoted-name / index / "*" / "?" ws filter
filter      = "@" *simple-seg [ ws op ws literal ]
simple-seg  = "." name / "[" ws ( quoted-name / index ) ws "]"
name        = ( ALPHA / "_" ) *( ALPHA / DIGIT / "_" )
index       = [ "-" ] 1*DIGIT
quoted-name = "'" *char "'" / DQUOTE *char DQUOTE        ; backslash escapes the next character
op          = "==" / "!=" / "<=" / ">=" / "<" / ">"
literal     = quoted-name / number / "true" / "false" / "null"
```

## Appendix B: Worked example

Printing Form 1040 line 2b ("Taxable interest") from a data set containing:

```json
"documents": { "1099": [
  { "form": "INT", "boxes": { "1": 1204.18 } },
  { "form": "INT", "boxes": { "1": 655.40 } },
  { "form": "DIV", "boxes": { "1a": 3412.77 } } ] }
```

```json
{ "id": "line2b", "type": "text", "page": 1, "label": "2b Taxable interest",
  "rect": { "x": 504, "y": 570, "width": 72, "height": 12 },
  "source": { "path": "$.documents['1099'][?@.form == 'INT'].boxes['1']", "aggregate": "sum" },
  "style": "amount" }
```

| Step | Result |
|---|---|
| Resolve | the filter keeps the two INT forms; `boxes['1']` selects `1204.18` and `655.40`; `sum` gives `1859.58` |
| Format | `amount` = currency, 0 decimals, half-up gives `1,860` |
| Layout | Helvetica 9 pt; padding gives an inner box of x 506–574, y 571–581. Text width is 22.518 pt, so right-aligned x = 574 − 22.518 = 551.482. Baseline = 571 + (10 + 0.718 × 9) / 2 = 579.231 |
| Draw | `drawString(551.482, 792 − 579.231, "1,860")` in PDF user space |
