"""Draft an annotation out of a fillable PDF's widgets.

IRS forms are mostly fillable, and the widgets already know the exact boxes,
comb cell counts and alignment. Names are junk (f1_47) and there's no data
binding of course, so this gets you the geometry and you do the rest.
Non-fillable forms: use `tfa grid` and a ruler.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from pypdf import PageObject, PdfReader
from pypdf.generic import DictionaryObject

from .pdf import display_size, sha256_file

FF_MULTILINE = 1 << 12
FF_RADIO = 1 << 15
FF_PUSHBUTTON = 1 << 16
FF_COMB = 1 << 24


def pdf_rect_to_tfa(page: PageObject, llx, lly, urx, ury) -> dict[str, float]:
    """PDF user-space rect -> TFA rect on the page as displayed."""
    left, bottom = float(page.cropbox.left), float(page.cropbox.bottom)
    width, height = float(page.cropbox.width), float(page.cropbox.height)

    def display(px, py):
        u, v = px - left, height - (py - bottom)  # unrotated, top-left origin
        rot = page.rotation % 360
        if rot == 90:
            return height - v, u
        if rot == 180:
            return width - u, height - v
        if rot == 270:
            return v, width - u
        return u, v

    (x0, y0), (x1, y1) = display(llx, lly), display(urx, ury)
    return {
        "x": round(min(x0, x1), 2),
        "y": round(min(y0, y1), 2),
        "width": round(abs(x1 - x0), 2),
        "height": round(abs(y1 - y0), 2),
    }


def _inherited(obj, key):
    # field attrs can live on any ancestor
    while obj is not None:
        if key in obj:
            return obj[key]
        obj = obj.get("/Parent")
        obj = obj.get_object() if obj is not None else None
    return None


def _on_state(widget: DictionaryObject) -> str | None:
    # export value of a checkbox/radio = the name of its non-Off appearance
    ap = widget.get("/AP")
    normal = ap.get_object().get("/N") if ap is not None else None
    if normal is None:
        return None
    states = normal.get_object()
    if not isinstance(states, DictionaryObject):
        return None
    return next((str(s)[1:] for s in states if s != "/Off"), None)


def _field_id(name: str, used: set[str]) -> str:
    base = re.sub(r"\[\d+\]", "", name)
    base = re.sub(r"[^A-Za-z0-9_-]", "_", base).strip("_") or "field"
    if not base[0].isalpha():
        base = f"f_{base}"
    cand, n = base, 2
    while cand in used:
        cand, n = f"{base}_{n}", n + 1
    used.add(cand)
    return cand


def bootstrap(
    pdf_path: str | Path,
    form_id: str,
    title: str | None = None,
    relative_to: Path | None = None,
    schema_ref: str = "https://example.com/schemas/tfa/1.0/tfa.schema.json",
) -> dict[str, Any]:
    pdf_path = Path(pdf_path)
    reader = PdfReader(pdf_path)
    info = reader.metadata or {}
    used: set[str] = set()
    fields: list[dict[str, Any]] = []
    pages = []

    for num, page in enumerate(reader.pages, start=1):
        w, h = display_size(page)
        pages.append({"width": round(w, 3), "height": round(h, 3)})

        # group widgets by field. XFA-exported forms (all the IRS ones) do radio
        # groups as sibling checkboxes c1_8[0], c1_8[1]... with different export
        # values, so buttons that share a base name get grouped as well
        groups: dict[Any, tuple[DictionaryObject, list[DictionaryObject]]] = {}
        for ref in page.get("/Annots") or []:
            wd = ref.get_object()
            if wd.get("/Subtype") != "/Widget":
                continue
            owner = wd if "/T" in wd else wd["/Parent"].get_object()
            if _inherited(owner, "/FT") == "/Btn":
                key: Any = ("button", re.sub(r"\[\d+\]$", "", str(owner.get("/T"))))
            else:
                key = owner.indirect_reference.idnum if owner.indirect_reference else id(owner)
            groups.setdefault(key, (owner, []))[1].append(wd)

        for owner, widgets in groups.values():
            ft = _inherited(owner, "/FT")
            flags = int(_inherited(owner, "/Ff") or 0)
            name = str(owner.get("/T"))
            fid = _field_id(name, used)
            label = str(owner.get("/TU") or f"TODO: describe {name}")
            rects = [pdf_rect_to_tfa(page, *(float(v) for v in wd["/Rect"])) for wd in widgets]
            entry: dict[str, Any] = {"id": fid, "page": num, "label": label}

            if ft == "/Btn":
                if flags & FF_PUSHBUTTON:
                    continue
                states = [_on_state(wd) or str(i + 1) for i, wd in enumerate(widgets)]
                if len(widgets) == 1 and not flags & FF_RADIO:
                    entry |= {"type": "checkbox", "rect": rects[0], "checked": {"path": f"$.TODO.{fid}", "op": "truthy"}}
                else:
                    entry |= {
                        "type": "choice",
                        "source": {"path": f"$.TODO.{fid}"},
                        "options": [{"value": s, "rect": r} for s, r in zip(states, rects)],
                    }
            elif ft in ("/Tx", "/Ch"):
                entry |= {"type": "text", "rect": rects[0], "source": {"path": f"$.TODO.{fid}"}}
                style: dict[str, Any] = {}
                align = {1: "center", 2: "right"}.get(int(_inherited(owner, "/Q") or 0))
                if align:
                    style["align"] = align
                if flags & FF_MULTILINE:
                    style |= {"overflow": "wrap", "valign": "top"}
                max_len = _inherited(owner, "/MaxLen")
                if flags & FF_COMB and max_len:
                    entry["comb"] = {"cells": int(max_len)}
                if style:
                    entry["style"] = style
            else:
                continue  # signatures etc
            fields.append(entry)

    def reading_order(f):
        r = f.get("rect") or f["options"][0]["rect"]
        return (f["page"], round(r["y"] / 4), r["x"])  # rows first, fuzzed a bit so a row stays a row

    fields.sort(key=reading_order)
    source_file = os.path.relpath(pdf_path.resolve(), relative_to) if relative_to else pdf_path.name
    return {
        "$schema": schema_ref,
        "specVersion": "1.0",
        "form": {
            "id": form_id,
            "title": title or str(info.get("/Title") or form_id),
            "source": {"file": source_file, "sha256": sha256_file(pdf_path)},
            "pages": pages,
        },
        "coordinates": {"unit": "pt", "origin": "top-left"},
        "styles": {"default": {"font": "Helvetica", "fontSize": 9}},
        "fields": fields,
    }
