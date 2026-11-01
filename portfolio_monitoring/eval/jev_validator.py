"""Cell-by-cell validation of a monitoring workbook: Claude asks, JEV answers.

  System 2 (Claude, once per workbook layout): reads the workbook's structure and the house specs and writes
      Correct / Incorrect question templates. Each calculation template carries a `recompute` expression,
      because JEV judges but does not do arithmetic.
  Code: expands every template to every matching cell and builds a small JSON state per cell: the cell's
      value, how it displays, its formula, and the evidence (recomputed value, source text, number format).
  System 1 (JEV, typesafe.ai): answers each question with a typed `choice` (Correct / Incorrect) and a
      `noul` probability, in about 0.2 s per cell.

Every answer goes to a CSV with JEV's metadata (model, confidence, probabilities, tokens, latency) and a
deterministic oracle verdict where one exists, so JEV's accuracy is measured rather than assumed.

Usage:
  python eval/jev_validator.py <workbook.xlsx> --out runs/x/jev_results.csv [--templates t.json] [--late]
Env: JEV_API_KEY (and ANTHROPIC_API_KEY unless --templates points to an existing file).
"""
import argparse
import ast
import csv
import json
import os
import re
import sys
import tempfile
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from checks import MONEY_COLS, PCT_COLS, recalc  # noqa: E402
from portfolio_truth import Q3, Q3_LATE  # noqa: E402

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
ROOT = Path(__file__).resolve().parent.parent
JEV_URL = os.environ.get("JEV_BASE_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-latest")
JEV_USD_PER_M_INPUT = 0.042  # JEV bills input tokens only
FLAG_CONFIDENCE = 0.5  # an Incorrect answer below this confidence is reported as Uncertain
CLAUDE_MODEL = "claude-opus-5-5"
BLOCKS = {  # block name -> (sheet, headers that identify the header row)
    "metrics": ("Company Metrics", ("Company", "Status")),
    "glance": ("Dashboard", ("Company", "Status")),
    "kpis": ("Inputs & Review", ("Company", "Value", "Method")),
}
CHECKS = ("calculation", "formatting", "source", "link")


def var(header):
    return re.sub(r"[^a-z0-9]+", "_", str(header).lower()).strip("_")


# --------------------------------------------------------------------------------------
# Workbook reading
# --------------------------------------------------------------------------------------
def find_block(ws, needed):
    """(header row, {header: col}, data rows, key col). The key header (needed[0]) may sit in any column."""
    for r in range(1, min(ws.max_row, 120) + 1):
        heads = {str(ws.cell(r, c).value).strip(): c for c in range(1, ws.max_column + 1)
                 if isinstance(ws.cell(r, c).value, str) and not ws.cell(r, c).value.startswith("=")}
        if all(h in heads for h in needed):
            key = heads[needed[0]]
            rows, rr = [], r + 1
            while ws.cell(rr, key).value not in (None, ""):
                rows.append(rr)
                rr += 1
            return r, heads, rows, key
    return None, {}, [], 1


def named_values(wb_vals, wb_form):
    out = {}
    for name, dn in wb_form.defined_names.items():
        try:
            dests = list(dn.destinations)
        except Exception:
            continue
        if len(dests) == 1 and ":" not in dests[0][1] and dests[0][0] in wb_vals.sheetnames:
            out[name] = wb_vals[dests[0][0]][dests[0][1].replace("$", "")].value
    return out


def profile(path):
    """Compact description of the workbook for the question writer: blocks, columns, formulas, formats."""
    wb = load_workbook(path)
    blocks = {}
    for block, (sheet, needed) in BLOCKS.items():
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        hr, heads, rows, _ = find_block(ws, needed)
        if not hr:
            continue
        sample = rows[0] if rows else hr + 1
        blocks[block] = {"sheet": sheet, "rows": len(rows), "columns": [
            {"header": h, "var": var(h), "sample_formula": str(ws.cell(sample, c).value)[:160],
             "number_format": ws.cell(sample, c).number_format}
            for h, c in sorted(heads.items(), key=lambda kv: kv[1])]}
    return {"blocks": blocks, "named_ranges": sorted(n for n in wb.defined_names)}


# --------------------------------------------------------------------------------------
# System 2: Claude writes the question templates
# --------------------------------------------------------------------------------------
TEMPLATE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["templates"],
    "properties": {"templates": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "block", "column", "check", "question", "correct_if", "incorrect_if", "recompute"],
        "properties": {
            "id": {"type": "string"}, "block": {"type": "string", "enum": list(BLOCKS)},
            "column": {"type": "string"}, "check": {"type": "string", "enum": list(CHECKS)},
            "question": {"type": "string"}, "correct_if": {"type": "string"}, "incorrect_if": {"type": "string"},
            "recompute": {"type": "string"}}}}},
}

WRITER_SYSTEM = """You write validation questions for JEV, a System 1 classifier. JEV answers one Correct / Incorrect
question about ONE spreadsheet cell at a time, from a small JSON state. JEV judges quickly but CANNOT do arithmetic:
every calculation question must be answerable by comparing the cell's `value` with a `recomputed` value that code
computes from your `recompute` expression. Write criteria JEV can apply by comparison alone.

Write templates (each expands to every data row of its block):
- block "metrics" (Company Metrics): for every derived column, a "calculation" template with a recompute expression;
  for every numeric column, a "formatting" template stating the house display rule concretely.
- block "glance" (Dashboard at-a-glance table): a "link" template per numeric column; its recompute is
  metrics("<metrics column var>") (the value it must mirror for the same company).
- block "kpis" (Inputs & Review reported KPIs): one "source" template on the Value column: does Value equal the
  As reported figure after the unit conversion stated in Unit / Method / Note? (recompute "").

Recompute expressions are Python-like and may use only:
- the row's column vars (listed in the profile), e.g. arr / prior_arr - 1
- named("<NamedRange>") for single-cell named ranges (policy thresholds)
- metrics("<var>") for the same company's Company Metrics value; kpi("<metric label>") for its Inputs value
- isnum(x), abs, min, max, round, and/or/not, comparisons, and `a if cond else b`
Status must reproduce the RAG policy in the spec exactly (use isnum guards; companies without a report are
"No report"). Use `recompute` "" for formatting and source checks.
Keep questions short and specific to one cell. Use the exact header text in `column`."""


def write_templates(claude, path, specs):
    import anthropic  # noqa: F401  (only needed when templates are generated)
    prof = profile(path)
    user = ("Workbook profile:\n" + json.dumps(prof, indent=1) + "\n\n" +
            "\n\n".join(f"--- {Path(p).name} ---\n{Path(p).read_text()}" for p in specs))
    with claude.messages.stream(
        model=CLAUDE_MODEL, max_tokens=32000, system=WRITER_SYSTEM,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": TEMPLATE_SCHEMA}},
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"Question writer declined: {msg.stop_details}")
    data = json.loads(next(b.text for b in msg.content if b.type == "text"))
    data["generated_by"] = msg.model
    data["usage"] = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens}
    return data


# --------------------------------------------------------------------------------------
# Safe evaluation of recompute expressions
# --------------------------------------------------------------------------------------
_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Name, ast.Constant,
            ast.Call, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
            ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq, ast.Pow, ast.Mod)
_FUNCS = {"isnum", "abs", "min", "max", "round", "named", "metrics", "kpi"}


def isnum(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def safe_eval(expr, env):
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"not allowed: {type(node).__name__}")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _FUNCS):
            raise ValueError("only whitelisted functions")
    return eval(compile(tree, "<recompute>", "eval"), {"__builtins__": {}}, env)  # noqa: S307 (AST-whitelisted)


def names_in(expr):
    try:
        return sorted({n.id for n in ast.walk(ast.parse(expr, mode="eval")) if isinstance(n, ast.Name)} - _FUNCS)
    except SyntaxError:
        return []


# --------------------------------------------------------------------------------------
# Expanding templates to cells
# --------------------------------------------------------------------------------------
def displayed(v, fmt):
    """Approximate Excel's rendering of a value for the formats used in this workbook."""
    if not isnum(v):
        return "" if v is None else str(v)
    f = fmt or "General"
    if re.search(r'[KMBT]"|\\[KMBT]', f):
        a = abs(v)
        for lim, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
            if a >= lim:
                s = f"${a / lim:,.1f}{suf}" if suf != "K" else f"${a / lim:,.0f}K"
                return f"({s})" if v < 0 else s
        return f"${v:,.0f}"
    if "%" in f:
        dec = len(re.search(r"0\.?(0*)%", f).group(1)) if re.search(r"0\.?(0*)%", f) else 0
        s = f"{abs(v) * 100:,.{dec}f}%"
        return f"({s})" if v < 0 and "(" in f else ("-" + s if v < 0 else s)
    if '"x"' in f or "\\x" in f or f.endswith("x"):
        return f"{v:,.1f}x"
    if "mo" in f:
        return f"{v:,.1f} mo"
    if "$" in f:
        return f"${v:,.0f}" if v >= 0 else f"(${-v:,.0f})"
    return f"{v:,.4g}" if isinstance(v, float) else f"{v:,}"


def expand(templates, path, truth=None):
    fw = load_workbook(path)
    with tempfile.TemporaryDirectory() as tmp:
        calc = recalc(path, tmp)
        vw = load_workbook(calc or path, data_only=True)
    named = named_values(vw, fw)
    blocks = {}
    for block, (sheet, needed) in BLOCKS.items():
        if sheet in fw.sheetnames:
            hr, heads, rows, key = find_block(fw[sheet], needed)
            blocks[block] = (sheet, heads, rows, key)

    def row_vals(block, r):
        sheet, heads, _, _ = blocks[block]
        return {var(h): vw[sheet].cell(r, c).value for h, c in heads.items()}

    by_company = {b: {vw[s].cell(r, k).value: r for r in rows} for b, (s, _, rows, k) in blocks.items()}
    kpi_rows = {}
    if "kpis" in blocks:
        s, heads, rows, k = blocks["kpis"]
        mcol = heads.get("Metric ID") or heads.get("Metric")
        for r in rows:
            kpi_rows[(vw[s].cell(r, k).value, str(vw[s].cell(r, mcol).value).strip().lower())] = r

    records = []
    for t in templates:
        if t["block"] not in blocks:
            continue
        sheet, heads, rows, key = blocks[t["block"]]
        col = heads.get(t["column"])
        if not col:
            continue
        for r in rows:
            company = vw[sheet].cell(r, key).value
            fcell, vcell = fw[sheet].cell(r, col), vw[sheet].cell(r, col)
            vals = row_vals(t["block"], r)
            state = {"sheet": sheet, "cell": fcell.coordinate, "row_label": company, "column": t["column"],
                     "value": vcell.value, "displayed": displayed(vcell.value, fcell.number_format)}
            recomputed, err = None, ""
            if t["check"] in ("calculation", "link") and t["recompute"]:
                env = dict(vals, isnum=isnum, abs=abs, min=min, max=max, round=round,
                           named=lambda n: named.get(n),
                           metrics=lambda v, c=company: (row_vals("metrics", by_company["metrics"][c]).get(v)
                                                         if c in by_company.get("metrics", {}) else None),
                           kpi=lambda m, c=company: (vw[blocks["kpis"][0]].cell(
                               kpi_rows[(c, m.lower())], blocks["kpis"][1]["Value"]).value
                               if (c, m.lower()) in kpi_rows else None))
                try:
                    recomputed = safe_eval(t["recompute"], env)
                except Exception as exc:  # noqa: BLE001 (recorded, not raised)
                    err = f"{type(exc).__name__}: {exc}"
                state.update(formula=str(fcell.value)[:200], recomputed=recomputed,
                             recomputed_displayed=displayed(recomputed, fcell.number_format),
                             inputs={n: vals.get(n, named.get(n)) for n in names_in(t["recompute"])})
                if err:
                    state["recompute_error"] = err
            elif t["check"] == "formatting":
                state.update(number_format=fcell.number_format, font=fcell.font.name)
            elif t["check"] == "source":
                state.update({k: vals.get(var(k)) for k in ("Metric", "Metric ID", "Unit", "As reported", "Method",
                                                            "Note") if var(k) in vals})
            records.append({"template": t, "company": company, "cell": fcell.coordinate, "sheet": sheet,
                            "state": state, "recomputed": recomputed, "recompute_error": err,
                            "oracle": oracle(t, state, recomputed, truth), "skip": skip_reason(t, state, recomputed)})
    return records


def skip_reason(t, state, recomputed):
    """Cells with nothing for a System 1 judge to compare are recorded, not asked."""
    blank = state["value"] in (None, "")
    if blank and t["check"] == "formatting":
        return "blank cell: no display to judge"
    if blank and t["check"] in ("calculation", "link") and recomputed in (None, ""):
        return "blank cell and blank evidence (e.g. no report)"
    if t["check"] == "source":
        method = str(state.get("Method", "")).strip().lower()
        if method == "missing" or blank:
            return "metric missing in source"
        if method not in ("reported", "converted"):
            return f"{method} value: not a unit conversion; checked against truth by the data checks"
        evidence = [k for k in state if k.lower().startswith("as reported")]
        if evidence and all(state[k] in (None, "") for k in evidence):
            return "no as-reported text to compare (e.g. fund's own record); checked by the data checks"
    return ""


def oracle(t, state, recomputed, truth):
    """Deterministic verdict where one exists (Correct / Incorrect / '')."""
    v = state["value"]
    if t["check"] in ("calculation", "link"):
        if recomputed in (None, "") and v in (None, ""):
            return "Correct"
        if isnum(v) and isnum(recomputed):
            ok = abs(v - recomputed) <= max(1e-3, 0.005 * abs(recomputed))
            return "Correct" if ok else "Incorrect"
        if isinstance(recomputed, str) or isinstance(v, str):
            return "Correct" if str(v).strip() == str(recomputed).strip() else "Incorrect"
        return ""
    if t["check"] == "formatting":
        if t["column"] in MONEY_COLS and isnum(v):
            return "Correct" if re.search(r"[KMBT]\)?$", state["displayed"]) else "Incorrect"
        if t["column"] in PCT_COLS and isnum(v):
            return "Correct" if "%" in state.get("number_format", "") else "Incorrect"
        return ""
    if t["check"] == "source" and truth:
        key = {"arr": "arr", "revenue": "revenue", "gross margin": "gm", "gm": "gm", "net burn": "burn",
               "burn": "burn", "cash": "cash", "headcount": "headcount", "customers": "customers", "nrr": "nrr"}
        if str(state.get("Method", "")).strip().lower() not in ("reported", "converted"):
            return ""  # derived / adjusted values are judged on documentation, not a unit conversion
        if re.search(r"EUR|GBP|€|£", f"{state.get('Unit', '')} {state.get('Metric', '')}"):
            return ""  # truth is in USD; a local-currency as-reported row has no USD oracle
        label = str(state.get("Metric ID") or state.get("Metric") or "").strip().lower()
        label = re.sub(r"\s*\(.*\)", "", label)
        want = truth.get(state["row_label"], {}).get(key.get(label, ""))
        if want is None or not isnum(v):
            return ""
        return "Correct" if abs(v - want) <= 0.015 * abs(want) + 1e-9 else "Incorrect"
    return ""


# --------------------------------------------------------------------------------------
# System 1: JEV answers
# --------------------------------------------------------------------------------------
def jev_post(payload, key, retries=4):
    body = json.dumps(payload, default=str).encode()
    for attempt in range(retries + 1):
        req = Request(JEV_URL, data=body, method="POST",
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except HTTPError as exc:
            if (exc.code not in (408, 409, 429) and exc.code < 500) or attempt == retries:
                raise
        except (URLError, TimeoutError, ConnectionError):
            if attempt == retries:
                raise
        time.sleep(min(2 ** attempt, 30))


def ask(rec, key):
    if rec["skip"]:
        rec["jev"] = {"answer": "", "error": ""}
        return rec
    t = rec["template"]
    payload = {"model": JEV_MODEL, "state": json.dumps(rec["state"], default=str), "questions": {
        "verdict": {"type": "choice", "instructions": t["question"],
                    "criteria": {"Correct": t["correct_if"], "Incorrect": t["incorrect_if"]}},
        "p_correct": {"type": "noul", "instructions": f"{t['question']} (yes = Correct)"}}}
    started = time.monotonic()
    try:
        data = jev_post(payload, key)
        a = data["answers"]
        rec["jev"] = {"answer": a["verdict"]["choice"], "confidence": a["verdict"].get("confidence"),
                      "p_correct": a["verdict"].get("probabilities", {}).get("Correct"),
                      "p_incorrect": a["verdict"].get("probabilities", {}).get("Incorrect"),
                      "noul_p_correct": a["p_correct"].get("noul"), "model": data.get("model"),
                      "input_tokens": data.get("usage", {}).get("input_tokens"),
                      "output_tokens": data.get("usage", {}).get("output_tokens"), "error": ""}
    except Exception as exc:  # noqa: BLE001 (recorded per row)
        rec["jev"] = {"answer": "", "error": f"{type(exc).__name__}: {exc}"}
    rec["jev"]["latency_ms"] = int((time.monotonic() - started) * 1000)
    rec["jev"]["asked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return rec


def ask_all(records, key, workers=8):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda r: ask(r, key), records))


# --------------------------------------------------------------------------------------
FIELDS = ["question_id", "template_id", "check", "sheet", "cell", "row_label", "column", "question", "correct_if",
          "incorrect_if", "cell_value", "displayed", "recomputed", "recompute_error", "state_json", "jev_answer",
          "jev_confidence", "jev_p_correct", "jev_p_incorrect", "jev_noul_p_correct", "jev_model",
          "jev_input_tokens", "jev_output_tokens", "jev_cost_usd", "jev_latency_ms", "jev_error", "asked_at",
          "verdict", "skipped_reason", "oracle_answer", "agrees_with_oracle", "questions_written_by"]


def verdict(rec):
    j = rec.get("jev", {})
    if rec["skip"]:
        return "Skipped"
    if j.get("error") or not j.get("answer"):
        return "Error"
    if j["answer"] == "Incorrect" and (j.get("confidence") or 0) < FLAG_CONFIDENCE:
        return "Uncertain"
    return j["answer"]


def write_csv(records, out, written_by):
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for i, r in enumerate(records, 1):
            t, j, s = r["template"], r.get("jev", {}), r["state"]
            w.writerow({
                "question_id": f"Q{i:04d}", "template_id": t["id"], "check": t["check"], "sheet": r["sheet"],
                "cell": r["cell"], "row_label": r["company"], "column": t["column"], "question": t["question"],
                "correct_if": t["correct_if"], "incorrect_if": t["incorrect_if"], "cell_value": s["value"],
                "displayed": s["displayed"], "recomputed": r["recomputed"], "recompute_error": r["recompute_error"],
                "state_json": json.dumps(s, default=str), "jev_answer": j.get("answer", ""),
                "jev_confidence": j.get("confidence"), "jev_p_correct": j.get("p_correct"),
                "jev_p_incorrect": j.get("p_incorrect"), "jev_noul_p_correct": j.get("noul_p_correct"),
                "jev_model": j.get("model"), "jev_input_tokens": j.get("input_tokens"),
                "jev_output_tokens": j.get("output_tokens"),
                "jev_cost_usd": round((j.get("input_tokens") or 0) * JEV_USD_PER_M_INPUT / 1e6, 8),
                "jev_latency_ms": j.get("latency_ms"), "jev_error": j.get("error", ""), "asked_at": j.get("asked_at"),
                "verdict": verdict(r), "skipped_reason": r["skip"], "oracle_answer": r["oracle"],
                "agrees_with_oracle": "" if not r["oracle"] or not j.get("answer") else j["answer"] == r["oracle"],
                "questions_written_by": written_by})


def summarize(records):
    out = {}
    for chk in CHECKS:
        rs = [r for r in records if r["template"]["check"] == chk]
        if not rs:
            continue
        judged = [r for r in rs if r["oracle"] and r.get("jev", {}).get("answer")]
        out[chk] = {"cells": len(rs), "asked": sum(not r["skip"] for r in rs),
                    "incorrect": sum(verdict(r) == "Incorrect" for r in rs),
                    "uncertain": sum(verdict(r) == "Uncertain" for r in rs),
                    "oracle_incorrect": sum(r["oracle"] == "Incorrect" for r in rs),
                    "agreement": round(sum(r["jev"]["answer"] == r["oracle"] for r in judged) / len(judged), 3)
                    if judged else None,
                    "errors": sum(bool(r.get("jev", {}).get("error")) for r in rs)}
    return out


def run(workbook, out_csv, templates_path=None, late=False, claude=None, specs=None, workers=8):
    workbook, out_csv = Path(workbook), Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    templates_path = Path(templates_path or out_csv.with_name("jev_question_templates.json"))
    if templates_path.exists():
        tdata = json.loads(templates_path.read_text())
    else:
        if claude is None:
            import anthropic
            claude = anthropic.Anthropic()
        specs = specs or [ROOT / "standards" / "monitoring_workbook_spec.md"]
        tdata = write_templates(claude, workbook, specs)
        templates_path.write_text(json.dumps(tdata, indent=2))
    truth_src = Q3_LATE if late else Q3
    from portfolio_truth import COMPANIES
    truth = {c["name"]: truth_src[c["key"]] for c in COMPANIES if c["key"] in truth_src}
    records = expand(tdata["templates"], workbook, truth)
    records = ask_all(records, os.environ["JEV_API_KEY"], workers=workers)
    write_csv(records, out_csv, tdata.get("generated_by", ""))
    return {"csv": str(out_csv), "templates": str(templates_path), "template_count": len(tdata["templates"]),
            "summary": summarize(records),
            "incorrect": [{"cell": f"{r['sheet']}!{r['cell']}", "company": r["company"],
                           "column": r["template"]["column"], "check": r["template"]["check"],
                           "value": r["state"]["displayed"], "jev_confidence": r["jev"].get("confidence")}
                          for r in records if verdict(r) == "Incorrect"],
            "uncertain": [f"{r['sheet']}!{r['cell']}" for r in records if verdict(r) == "Uncertain"]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    ap.add_argument("--out", required=True)
    ap.add_argument("--templates")
    ap.add_argument("--late", action="store_true")
    a = ap.parse_args()
    print(json.dumps(run(a.workbook, a.out, a.templates, a.late), indent=2, default=str))
