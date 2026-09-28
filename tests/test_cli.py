"""The command line, driven through main() so exit codes and output are checked."""

import json

from conftest import ROOT

from tfa.cli import main

F1040 = str(ROOT / "forms/irs-1040-2025/f1040.tfa.json")
RIVERA = ROOT / "examples/data/rivera-2025.json"


def broken_data(tmp_path, **changes):
    data = json.loads(RIVERA.read_text())
    for dotted, value in changes.items():
        node = data
        *parents, last = dotted.split(".")
        for p in parents:
            node = node[p]
        node[last] = value
    path = tmp_path / "data.json"
    path.write_text(json.dumps(data))
    return str(path)


def test_render_writes_nothing_on_errors(tmp_path, capsys):
    data = broken_data(tmp_path, **{"household.address.zip": "9410", "filing.status": "married"})
    out = tmp_path / "nested" / "out.pdf"
    assert main(["render", F1040, data, "-o", str(out)]) == 1
    err = capsys.readouterr().err
    assert "MASK_MISMATCH zip" in err and "CHOICE_NO_MATCH filingStatus" in err
    assert not out.exists()
    assert main(["render", F1040, str(RIVERA), "-o", str(out)]) == 0
    assert out.exists()  # the missing directory was created


def test_explain_reports_errors_without_a_box(tmp_path, capsys):
    # Six dependents with the statement strategy is fine; make the repeat itself fail instead.
    data = broken_data(tmp_path, **{"household.dependents": {"name": "not a list"}})
    assert main(["explain", F1040, data, "--printed-only"]) == 1
    captured = capsys.readouterr()
    assert "NOT_A_LIST dependents" in captured.err
    assert "line1a" in captured.out  # the rest still prints


def test_plan_json(tmp_path, capsys):
    out = tmp_path / "plan.json"
    assert main(["plan", F1040, str(RIVERA), "-o", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["form"] == "irs-1040-2025"
    assert doc["coordinates"] == {"unit": "pt", "origin": "top-left"}
    line2b = next(op for op in doc["ops"] if op["field"] == "line2b")
    assert line2b == {
        "kind": "text", "page": 1, "field": "line2b", "x": 551.482, "baseline": 579.231,
        "text": "1,860", "font": "Helvetica", "size": 9.0, "color": "#000000",
    }
    assert [d["code"] for d in doc["diagnostics"]] == ["REPEAT_STATEMENT"]
    assert doc["statements"][0]["rows"][0][0] == "Theodore"


def test_lint_reports_schema_problems(tmp_path, capsys):
    bad = tmp_path / "bad.tfa.json"
    bad.write_text(json.dumps({"specVersion": "1.0"}))
    assert main(["lint", str(bad)]) == 1
    assert "SCHEMA" in capsys.readouterr().err
    assert main(["lint", F1040, str(ROOT / "forms/irs-w9-2024/fw9.tfa.json")]) == 0
