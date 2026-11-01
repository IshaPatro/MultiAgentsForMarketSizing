"""Enforce the house standard on a built (and recalculated) model.

Rules
  1. Hardcoded numbers live only on Inputs (plus exempt agent-written columns).
  2. No magic numbers inside formulas outside Inputs (allowed_literals only).
  3. Font: house font everywhere; blue = input, green = pure link, black = formula.
  4. Number formats come from the spec.
  5. Each monthly row uses one formula pattern across all forecast months.
  6. After recalculation: no error values and no FAIL on Checks & Flags.

Usage: python lint_model.py model.xlsx   (exit code 1 on any violation)
"""
import json
import sys
from collections import defaultdict

from openpyxl import load_workbook
from openpyxl.formula.translate import Translator
from openpyxl.utils import get_column_letter

from model_rules import SPEC, cell_kind, font_color, formula_literals


def norm_fmt(fmt):
    """LibreOffice re-escapes formats on save ('\\$#,##0' vs '$#,##0'); compare the rendered form."""
    return fmt.replace("\\", "").replace('"', "")

MONTHLY = ["Revenue", "Headcount & Opex", "P&L", "Balance Sheet", "Cash Flow", "KPIs"]
ERRORS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!")


def lint(path):
    wb = load_workbook(path)
    vals = load_workbook(path, data_only=True)
    rules = SPEC["rules"]
    allowed = set(rules["allowed_literals"])
    formats = {norm_fmt(f) for f in SPEC["number_formats"].values()}
    first_row = SPEC["layout"]["first_content_row"]
    v = defaultdict(list)

    exempt_cols = defaultdict(set)
    for ex in rules["hardcode_exempt"]:
        ws = wb[ex["sheet"]]
        for row in ws.iter_rows():
            for c in row:
                if c.value == ex["header"]:
                    exempt_cols[ex["sheet"]].add(c.column)

    for ws in wb.worksheets:
        header_rows = first_row if ws.title in MONTHLY else 7
        for row in ws.iter_rows():
            for c in row:
                if c.value is None:
                    continue
                where = f"{ws.title}!{c.coordinate}"
                if c.font.name != SPEC["font"]["name"]:
                    v["font_name"].append(where)
                kind = cell_kind(c.value)
                if kind is None:
                    continue
                if norm_fmt(c.number_format) not in formats:
                    v["number_format"].append(f"{where} ({c.number_format})")
                if ws.title != "Inputs":
                    if kind == "input" and c.value not in (0, 1) and c.column not in exempt_cols[ws.title]:
                        v["hardcode_outside_inputs"].append(f"{where} = {c.value}")
                    if kind != "input":
                        bad = [x for x in formula_literals(c.value) if x not in allowed]
                        if bad:
                            v["magic_number_in_formula"].append(f"{where}: {bad}")
                if c.row >= header_rows:
                    rgb = c.font.color.rgb if c.font.color is not None else "FF000000"
                    got = rgb[-6:] if isinstance(rgb, str) else "theme"
                    if got.upper() != font_color(kind).upper():
                        v["font_color"].append(f"{where} is {got}, expected {font_color(kind)} ({kind})")

    # Formula consistency across forecast months
    for name in MONTHLY:
        ws = wb[name]
        for r in range(first_row, ws.max_row + 1):
            anchor = ws[f"D{r}"].value
            if not (isinstance(anchor, str) and anchor.startswith("=")):
                continue
            for ci in range(5, ws.max_column + 1):
                col = get_column_letter(ci)
                got = ws[f"{col}{r}"].value
                want = Translator(anchor, origin=f"D{r}").translate_formula(f"{col}{r}")
                if got != want:
                    v["inconsistent_row_formula"].append(f"{name}!{col}{r}")
                    break

    # Post-recalc results
    for ws in vals.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value in ERRORS:
                    v["error_value"].append(f"{ws.title}!{c.coordinate} {c.value}")
    checks = vals["Checks & Flags"]
    for r in range(1, checks.max_row + 1):
        if checks[f"G{r}"].value == "FAIL":
            v["integrity_check_failed"].append(checks[f"B{r}"].value)
    if all(c.value is None for c in vals["P&L"]["D"]):
        v["not_recalculated"].append("Run recalc.py before linting")

    return {"status": "pass" if not v else "fail", "violations": {k: (len(x), x[:10]) for k, x in v.items()}}


if __name__ == "__main__":
    out = lint(sys.argv[1])
    print(json.dumps(out, indent=2, ensure_ascii=False))
    sys.exit(0 if out["status"] == "pass" else 1)
