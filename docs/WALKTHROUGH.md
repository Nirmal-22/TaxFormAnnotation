# Video walkthrough outline (≤ 5 minutes)

A suggested script for the screen recording. The times are targets; the whole thing runs about 4:45. Before recording, open these tabs in the editor: `README.md`, `forms/irs-1040-2025/f1040.tfa.json`, `docs/SPEC.md`, and `examples/output/f1040-rivera.pdf`. Also have a terminal with the virtualenv active.

## 0:00 – 0:30 · The problem and the deliverable

*Show: README top, then the filled 1040 image.*

> "The task is a data structure for annotating tax-form boxes, so any application can print values onto the official PDF. I delivered four things: a specification, a JSON Schema, a reference renderer with tooling, and two real annotated forms. One is the complete 2025 Form 1040, all 199 boxes; the other is the W-9. This page was printed entirely from an annotation plus a JSON data set."

## 0:30 – 1:45 · The format, one field at a time

*Show: `f1040.tfa.json`: the `form` block, then `line2b`, then the `styles` block.*

* **Form identity.** The SHA-256 pins the exact PDF. If the IRS re-issues the form, rendering refuses rather than misprinting.
* **Positioning.** `rect` is in points from the top-left, as you see the page. It's the same frame as any screenshot, and the flip to PDF coordinates happens once, in the renderer.
* **Nested data.** `$.documents['1099'][?@.form == 'INT'].boxes['1']` with `aggregate: sum`. This is standard JSONPath (RFC 9535), cut down to the readable parts: no `..` search, no functions.
* **Formatting.** The `amount` style gives whole dollars, IRS half-up rounding, losses in parentheses and blank zeros. `amountDash` extends it to print `-0-` where the instructions ask for it.

*Show (scroll): `primarySsn` with comb groups, `filingStatus` (choice), `dependents` (repeat with `offset: {x: 108}`), and a `when` condition on the spouse fields.*

> "Four field types cover the whole form. SSNs printed into 3-2-4 cells are one field with comb groups. Filing status is a choice, and a value that matches no option is an error, never a random tick."

## 1:45 – 2:45 · Running it

```bash
tfa explain forms/irs-1040-2025/f1040.tfa.json examples/data/rivera-2025.json --printed-only | head -30
tfa render  forms/irs-1040-2025/f1040.tfa.json examples/data/rivera-2025.json -o out/f1040.pdf --debug
```

*Show: the debug PDF (boxes outlined and labelled), then page 3, the continuation statement.*

> "There are five dependents but four columns. The strategy is `statement`, so the 'more than four' box is checked and the fifth dependent goes on an appended statement, just as the IRS instructions say."

## 2:45 – 3:30 · Safety: fail closed

*Edit `examples/data/rivera-2025.json` live: set `"zip": "9410"` and `"status": "married"`, then rerun `explain` or `render`.*

> "Every problem is a diagnostic with a stable code and the exact field: MASK_MISMATCH on zip, CHOICE_NO_MATCH on filing status. Errored fields are never printed, and render writes nothing unless you pass `--allow-errors`. On a tax form, a blank box beats a wrong number."

*Undo the edits.*

## 3:30 – 4:15 · How someone annotates a new form

```bash
tfa bootstrap forms/irs-w9-2024/fw9.pdf -o /tmp/draft.tfa.json --form-id irs-w9-draft
tfa grid forms/irs-w9-2024/fw9.pdf -o /tmp/grid.pdf
tfa lint forms/*/*.tfa.json
```

> "Bootstrap harvests the fillable PDF's widgets: exact boxes, digit-cell counts, alignment and radio groups. The annotator only adds meaning. For boxes without widgets, like the W-9 signature date, the grid overlay gives coordinates. Lint checks everything the schema can't: bounds of every repeat slot, paths, style cycles, overlaps and the PDF hash. The schema also gives autocomplete in VS Code."

*Show briefly: autocomplete in the JSON file, then the rendered W-9.*

## 4:15 – 4:45 · Decisions and what's next

*Show: `docs/DESIGN.md` headings.*

> "Key decisions: annotations are declarative, so they can total W-2s but tax math stays in the engine. The layout rules are exact formulas, so two implementations print identically. Planning is separate from drawing, so your own code can draw the plan; `tfa plan` even hands it over as JSON. Next I would build a visual editor with live preview, box detection for non-fillable forms, automatic carry-forward to new revisions, and a language-neutral conformance suite."
