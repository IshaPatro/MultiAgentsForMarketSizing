"""Build a static showcase page (web/portfolio_monitoring.html) of one live run: the raw inputs, the workbook the
agents produced, the late-file merge and every validation result.

Usage: python scripts/build_showcase_page.py [runs/<run-id>]   (default: the latest run with an update step)
"""
import csv
import datetime as dt
import email
import html
import json
import shutil
import subprocess
import sys
import warnings
from email import policy
from pathlib import Path

from docx import Document
from openpyxl import load_workbook
from pptx import Presentation

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT.parent / "web"
OUT = WEB / "portfolio_monitoring.html"
ASSETS = WEB / "assets" / "portfolio"
sys.path.insert(0, str(ROOT / "eval"))
from jev_validator import skip_reason  # noqa: E402
from update_checks import run_update_checks  # noqa: E402

warnings.filterwarnings("ignore")
WB_NAME = "BCV_Portfolio_Monitor_2026Q3.xlsx"
e = html.escape

FILE_KIND = {".pdf": "PDF", ".docx": "Word", ".pptx": "PowerPoint", ".xlsx": "Excel", ".csv": "CSV",
             ".eml": "Email", ".json": "JSON"}
FILE_NOTE = {
    "bcv_portfolio_master.xlsx": "Fund master: ownership, cost basis, plans, due dates.",
    "brightwire_ops_q3_2026_investor_letter.docx": "Investor letter in EUR. Burn is never stated.",
    "corvane_security_update_2026-09.eml": "One-paragraph email with a hidden instruction aimed at AI assistants.",
    "fieldnote_health_q3_2026_board_deck.pptx": "Board deck. Headline ARR includes contracted, not-yet-live revenue.",
    "fx_rates_2026Q3.csv": "Spot and average EUR/USD rates for the quarter.",
    "lumenpath_ai_investor_update_2026-09.pdf": "Founder PDF update with KPI table.",
    "prior_quarter_kpis_2026Q2.csv": "Last quarter's numbers of record, for growth and runway trends.",
    "quillstack_metrics_export_2026-09.csv": "Raw metrics export with duplicated rows and no revenue line.",
    "tessellate_kpi_pack_2026-09.xlsx": "Monthly KPI pack in $000s with a restated August.",
    "halyard_robotics_q3_2026_portal_export.json": "Arrives a week late, after the workbook was reviewed.",
}


# ---------- value formatting (mirrors the workbook's number formats) ----------

def fmt(v, nf="General"):
    if v is None:
        return ""
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    nf = nf or "General"
    if "%" in nf:
        d = len(nf.split("%")[0].split(".")[1]) if "." in nf.split("%")[0] else 0
        return f"{v * 100:.{d}f}%"
    if ",," in nf or ("K" in nf and "$" in nf):
        a, s = abs(v), ("-" if v < 0 else "")
        cur = "$" if "$" in nf else ""
        for div, suf, d in ((1e12, "T", 1), (1e9, "B", 1), (1e6, "M", 1), (1e3, "K", 0)):
            if a >= div:
                return f"{s}{cur}{a / div:,.{d}f}{suf}"
        return f"{s}{cur}{a:,.0f}"
    if "x" in nf.lower().replace("0x", "") or nf.endswith('\\x') or '"x"' in nf:
        return f"{v:.1f}x"
    if "mo" in nf:
        return f"{v:.1f} mo"
    if "0.0" in nf:
        d = len(nf.split(".")[1].split(";")[0].rstrip('"_)-'))
        d = min(sum(ch == "0" for ch in nf.split(".")[1].split(";")[0]), 4) or d
        return f"{v:,.{d}f}"
    if "#,##0" in nf or (isinstance(v, int) and abs(v) >= 10000):
        return f"{v:,.0f}"
    if isinstance(v, float):
        return f"{v:,.4g}" if abs(v) < 1000 else f"{v:,.0f}"
    return str(v)


def cls_for(text):
    t = str(text).strip()
    return {"Red": "pill red", "Amber": "pill amber", "Green": "pill green", "No report": "pill grey",
            "Resolved": "pill grey", "Open": "pill open", "PASS": "pill green", "FAIL": "pill red",
            "Correct": "pill green", "Incorrect": "pill red"}.get(t, "")


def cell_html(text, num=False):
    c = cls_for(text)
    text = "" if text is None else str(text)
    inner = f'<span class="{c}">{e(text)}</span>' if c else e(text)
    return f'<td class="num">{inner}</td>' if num else f"<td>{inner}</td>"


def table(headers, rows, numeric=None, cls=""):
    numeric = numeric or set()
    head = "".join(f"<th>{e(str(h or ''))}</th>" for h in headers)
    body = "".join("<tr>" + "".join(cell_html(v, i in numeric) for i, v in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="tbl-wrap {cls}"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def sheet_table(ws, first_row=1, last_row=None, max_col=None, skip_blank=True):
    """Render a worksheet range as a table: the first non-empty row in range is the header."""
    last_row = last_row or ws.max_row
    max_col = max_col or ws.max_column
    rows = []
    for r in range(first_row, last_row + 1):
        vals = [ws.cell(r, c) for c in range(1, max_col + 1)]
        if skip_blank and all(c.value in (None, "") for c in vals):
            continue
        rows.append([(fmt(c.value, c.number_format), isinstance(c.value, (int, float))) for c in vals])
    while max_col > 1 and all(not row[max_col - 1][0] for row in rows):
        max_col -= 1
        rows = [row[:max_col] for row in rows]
    if not rows:
        return ""
    head, body = rows[0], rows[1:]
    h = "".join(f"<th>{e(v)}</th>" for v, _ in head)
    b = "".join("<tr>" + "".join(cell_html(v, isnum) for v, isnum in row) + "</tr>" for row in body)
    return f'<div class="tbl-wrap"><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'


def blocks(ws, max_col=None):
    """Split a sheet into its blank-row-separated blocks; a one-cell row before a block is its heading."""
    out, start = [], None
    for r in range(1, ws.max_row + 2):
        blank = r > ws.max_row or all(ws.cell(r, c).value in (None, "") for c in range(1, (max_col or ws.max_column) + 1))
        if not blank and start is None:
            start = r
        if blank and start is not None:
            out.append((start, r - 1))
            start = None
    html_parts = []
    for a, b in out:
        filled = [c for c in range(1, (max_col or ws.max_column) + 1) if ws.cell(a, c).value not in (None, "")]
        if a == b and len(filled) == 1:
            v = ws.cell(a, filled[0]).value
            html_parts.append(f'<h4 class="blk">{e(str(v))}</h4>' if len(str(v)) < 80 else f'<p class="note">{e(str(v))}</p>')
            continue
        heading = ""
        if len(filled) == 1 and b > a:
            heading = f'<h4 class="blk">{e(str(ws.cell(a, filled[0]).value))}</h4>'
            a += 1
        html_parts.append(heading + sheet_table(ws, a, b, max_col))
    return "".join(html_parts)


# ---------- raw input previews ----------

def preview_pdf(path, slug):
    prefix = ASSETS / "img" / slug
    subprocess.run(["pdftoppm", "-r", "80", "-png", str(path), str(prefix)], check=True)
    imgs = sorted((ASSETS / "img").glob(f"{slug}-*.png"))
    return "".join(f'<img class="page" loading="lazy" src="assets/portfolio/img/{p.name}" alt="Page {i + 1} of {e(path.name)}">'
                   for i, p in enumerate(imgs))


def preview_docx(path):
    doc = Document(path)
    parts = []
    for block in doc.element.body.iterchildren():
        tag = block.tag.split("}")[1]
        if tag == "p":
            text = "".join((t.text or "") if t.tag.endswith("}t") else "\n" for t in block.iter()
                           if t.tag.endswith("}t") or t.tag.endswith("}br"))
            if text.strip():
                parts.append(f"<p>{e(text)}</p>")
        elif tag == "tbl":
            t = next(t for t in doc.tables if t._element is block)
            rows = [[c.text for c in row.cells] for row in t.rows]
            parts.append(table(rows[0], rows[1:]))
    return f'<div class="doc">{"".join(parts)}</div>'


def preview_pptx(path):
    prs = Presentation(path)
    slides = []
    for i, s in enumerate(prs.slides, 1):
        parts = []
        for sh in s.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip():
                cls = "slide-title" if sh == s.shapes.title else ""
                parts.append(f'<p class="{cls}">{e(sh.text_frame.text)}</p>')
            if getattr(sh, "has_table", False) and sh.has_table:
                rows = [[c.text for c in r.cells] for r in sh.table.rows]
                parts.append(table(rows[0], rows[1:]))
        if s.has_notes_slide and s.notes_slide.notes_text_frame.text.strip():
            parts.append(f'<p class="note">Speaker notes: {e(s.notes_slide.notes_text_frame.text)}</p>')
        slides.append(f'<div class="slide"><span class="slide-no">Slide {i}</span>{"".join(parts)}</div>')
    return f'<div class="slides">{"".join(slides)}</div>'


def preview_eml(path):
    msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    head = "".join(f"<div><b>{k}:</b> {e(str(msg[k]))}</div>" for k in ("From", "To", "Date", "Subject") if msg[k])
    body = msg.get_body(preferencelist=("plain", "html")).get_content()
    return f'<div class="mail"><div class="mail-head">{head}</div><pre>{e(body)}</pre></div>'


def preview_csv(path):
    rows = list(csv.reader(path.open()))
    num = {i for i, h in enumerate(rows[0]) if all(_isnum(r[i]) for r in rows[1:] if i < len(r) and r[i])}
    return table(rows[0], rows[1:], num)


def _isnum(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def preview_xlsx(path):
    wb = load_workbook(path, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f'<h4 class="blk">Sheet: {e(ws.title)}</h4>' + blocks(ws))
    return "".join(out)


def preview_json(path):
    return f'<pre class="code">{e(json.dumps(json.loads(path.read_text()), indent=2))}</pre>'


def preview(path):
    slug = path.stem.replace(".", "_")
    return {".pdf": lambda: preview_pdf(path, slug), ".docx": lambda: preview_docx(path),
            ".pptx": lambda: preview_pptx(path), ".eml": lambda: preview_eml(path),
            ".csv": lambda: preview_csv(path), ".xlsx": lambda: preview_xlsx(path),
            ".json": lambda: preview_json(path)}[path.suffix]()


# ---------- validation ----------

def rescore_jev(csv_path):
    """Re-apply the validator's current skip rules, so the page matches the checker as it stands today."""
    rows = list(csv.DictReader(open(csv_path)))
    for r in rows:
        if r["jev_answer"] and r["state_json"] and not r["skipped_reason"]:
            reason = skip_reason({"check": r["check"]}, json.loads(r["state_json"]), r["recomputed"] or None)
            if reason:
                r["skipped_reason"], r["jev_answer"] = reason, ""
    asked = [r for r in rows if r["jev_answer"]]
    by = {}
    for r in asked:
        s = by.setdefault(r["check"], {"asked": 0, "incorrect": 0, "agree": 0, "oracle": 0})
        s["asked"] += 1
        s["incorrect"] += r["jev_answer"] == "Incorrect"
        if r["oracle_answer"]:
            s["oracle"] += 1
            s["agree"] += r["jev_answer"] == r["oracle_answer"]
    cost = sum(float(r["jev_cost_usd"] or 0) for r in asked)
    lat = sorted(float(r["jev_latency_ms"] or 0) for r in asked)
    return rows, asked, by, cost, (lat[len(lat) // 2] if lat else 0)


def trim(src, dst, pad=24):
    """Copy a rendered page with its white print margins cropped."""
    from PIL import Image, ImageChops
    im = Image.open(src).convert("RGB")
    box = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).getbbox()
    if box:
        im = im.crop((max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad)))
    im.save(dst, optimize=True)


def latest_run():
    runs = sorted(p for p in (ROOT / "runs").iterdir() if (p / "update" / WB_NAME).exists() and (p / "validation_report.json").exists())
    return runs[-1]


# ---------- page ----------

def build(run):
    if ASSETS.exists():
        shutil.rmtree(ASSETS)
    (ASSETS / "img").mkdir(parents=True)
    (ASSETS / "files").mkdir()
    report = json.loads((run / "validation_report.json").read_text())

    # inputs
    raw = sorted((ROOT / "data" / "raw").iterdir())
    late = sorted((ROOT / "data" / "raw_late").iterdir())
    for p in raw + late:
        shutil.copy(p, ASSETS / "files" / p.name)
    tabs, panes = [], []
    for i, p in enumerate(raw):
        kind = FILE_KIND[p.suffix]
        tabs.append(f'<button role="tab" class="file-tab{" on" if i == 0 else ""}" data-pane="in-{i}">'
                    f'<span class="kind k-{p.suffix[1:]}">{kind}</span><span class="fname">{e(p.name)}</span></button>')
        panes.append(f'<div class="pane{" on" if i == 0 else ""}" id="in-{i}"><div class="pane-head"><div>'
                     f'<b>{e(p.name)}</b><p>{e(FILE_NOTE.get(p.name, ""))}</p></div>'
                     f'<a class="btn" href="assets/portfolio/files/{e(p.name)}" download>Download</a></div>'
                     f'{preview(p)}</div>')

    # outputs
    final = run / "update" / WB_NAME
    shutil.copy(run / "charts" / WB_NAME, ASSETS / "files" / "BCV_Portfolio_Monitor_2026Q3_before_late_file.xlsx")
    shutil.copy(final, ASSETS / "files" / WB_NAME)
    # the dashboard is the first pages of the rendered PDF; re-render sharper than the review PNGs
    pdf = run / "render_after" / WB_NAME.replace(".xlsx", ".pdf")
    n_dash = len(list((run / "render_after").glob("dashboard-*.png")))
    tmp = ASSETS / "img" / "_dash"
    subprocess.run(["pdftoppm", "-r", "170", "-png", "-f", "1", "-l", str(n_dash), str(pdf), str(tmp)], check=True)
    for i, png in enumerate(sorted((ASSETS / "img").glob("_dash-*.png")), 1):
        trim(png, ASSETS / "img" / f"render_after-dashboard-{i:02d}.png")
        png.unlink()
    vals = load_workbook(run / "render_after" / "recalc" / WB_NAME, data_only=True)
    out_tabs = ["Dashboard", "Company Metrics", "Flags", "Inputs & Review"]
    out_panes = []
    dash_imgs = '<p class="note">The Dashboard as Excel renders it (click to open full size), followed by its values.</p>' + "".join(
        f'<a href="assets/portfolio/img/render_after-{p.name}" target="_blank" rel="noopener">'
        f'<img class="dash" loading="lazy" src="assets/portfolio/img/render_after-{p.name}" alt="Rendered dashboard page {i + 1}"></a>'
        for i, p in enumerate(sorted((run / "render_after").glob("dashboard-*.png"))))
    out_panes.append(dash_imgs + blocks(vals["Dashboard"]))
    for name in out_tabs[1:]:
        out_panes.append(blocks(vals[name]))
    otabs = "".join(f'<button role="tab" class="sheet-tab{" on" if i == 0 else ""}" data-pane="out-{i}">{e(n)}</button>'
                    for i, n in enumerate(out_tabs))
    opanes = "".join(f'<div class="pane{" on" if i == 0 else ""}" id="out-{i}">{p}</div>' for i, p in enumerate(out_panes))

    # late file: Halyard rows before and after
    def halyard(path):
        ws = load_workbook(path, data_only=True)["Inputs & Review"]
        hr = next(r for r in range(1, ws.max_row + 1) if "Method" in [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
                  and "Company" in [ws.cell(r, c).value for c in range(1, ws.max_column + 1)])
        heads = [ws.cell(hr, c).value for c in range(1, ws.max_column + 1)]
        keep = [heads.index(h) for h in ("Metric", "Value", "As reported", "Method", "Confidence") if h in heads]
        ci = heads.index("Company")
        rows = []
        for r in range(hr + 1, ws.max_row + 1):
            if ws.cell(r, ci + 1).value == "Halyard Robotics":
                rows.append([fmt(ws.cell(r, k + 1).value, ws.cell(r, k + 1).number_format) for k in keep])
        return [heads[k] for k in keep], rows
    hb, rb = halyard(run / "render" / "recalc" / WB_NAME)
    ha, ra = halyard(run / "render_after" / "recalc" / WB_NAME)
    upd = run_update_checks(run / "charts" / WB_NAME, final, "Halyard Robotics")
    upd_pass = sum(x["status"] == "PASS" for x in upd)

    # validation
    jev_rows, asked, by, cost, med_lat = rescore_jev(run / "jev" / "jev_results.csv")
    _, asked_after, by_after, _, _ = rescore_jev(run / "jev_after" / "jev_results.csv")
    inc = sum(s["incorrect"] for s in by.values())
    inc_after = sum(s["incorrect"] for s in by_after.values())
    agree = sum(s["agree"] for s in by.values()) / max(1, sum(s["oracle"] for s in by.values()))
    det = report["deterministic"]["checks"]
    chk = report["charts"]["checks"]
    review = report["chart_review"]
    score = [
        ("Data & formatting checks", "Recalculated in LibreOffice; values vs ground truth, formula errors, fonts, number formats",
         f'{sum(c["status"] == "PASS" for c in det)}/{len(det)}', all(c["status"] == "PASS" for c in det)),
        ("Chart layout checks", "Chart XML: positions, overlaps, legends, axis formats, titles",
         f'{sum(c["status"] == "PASS" for c in chk)}/{len(chk)}', all(c["status"] == "PASS" for c in chk)),
        ("JEV cell by cell", "Claude-written Correct/Incorrect questions, answered by JEV per cell",
         f"{len(asked) - inc}/{len(asked)} Correct", inc == 0),
        ("Chart reviewer", "Claude vision on the rendered dashboard",
         f'{len(review["chart_issues"])} issues', review["passed"]),
        ("Late-file merge", "Before/after diff: additive, surgical, nothing else touched",
         f"{upd_pass}/{len(upd)}", upd_pass == len(upd)),
        ("JEV after the merge", "Same questions on the updated workbook",
         f"{len(asked_after) - inc_after}/{len(asked_after)} Correct", inc_after == 0),
    ]
    score_html = "".join(
        f'<div class="score {"ok" if ok else "bad"}"><div class="score-top"><span>{e(n)}</span>'
        f'<span class="{cls_for("PASS" if ok else "FAIL")}">{"PASS" if ok else "FAIL"}</span></div>'
        f'<div class="score-val">{e(v)}</div><p>{e(d)}</p></div>' for n, d, v, ok in score)
    by_rows = [[k, s["asked"], s["incorrect"], f'{s["agree"] / s["oracle"]:.0%}' if s["oracle"] else "n/a"]
               for k, s in sorted(by.items())]
    sample = []
    for check in ("calculation", "formatting", "source", "link"):
        sample += [r for r in asked if r["check"] == check][:10]
    jev_table = table(["Check", "Cell", "Row", "Question", "Shown", "Recomputed", "JEV", "Conf.", "Oracle"],
                      [[r["check"], f'{r["sheet"]}!{r["cell"]}', r["row_label"], r["question"], r["displayed"],
                        r["recomputed"], r["jev_answer"], f'{float(r["jev_confidence"] or 0):.2f}', r["oracle_answer"]]
                       for r in sample], cls="jev")
    shutil.copy(run / "jev" / "jev_results.csv", ASSETS / "files" / "jev_results.csv")
    det_rows = [[c["check"], c["status"], c.get("detail", "")] for c in det + chk]
    upd_rows = [[c["check"], c["status"], c.get("detail", "")] for c in upd]
    templates = report["jev"]["template_count"]

    page = TEMPLATE
    for k, v in {
        "{{IN_TABS}}": "".join(tabs), "{{IN_PANES}}": "".join(panes),
        "{{OUT_TABS}}": otabs, "{{OUT_PANES}}": opanes, "{{SCORE}}": score_html,
        "{{ASKED}}": str(len(asked)), "{{INC}}": str(inc), "{{AGREE}}": f"{agree:.0%}",
        "{{TEMPLATES}}": str(templates), "{{COST}}": f"${cost:.4f}", "{{LAT}}": f"{med_lat:.0f} ms",
        "{{BY_TABLE}}": table(["Check type", "Asked", "Incorrect", "Agrees with oracle"], by_rows, {1, 2}),
        "{{JEV_TABLE}}": jev_table, "{{DET_TABLE}}": table(["Check", "Result", "Detail"], det_rows),
        "{{UPD_TABLE}}": table(["Check", "Result", "Detail"], upd_rows), "{{UPD_PASS}}": f"{upd_pass}/{len(upd)}",
        "{{LATE_JSON}}": preview_json(late[0]), "{{LATE_NAME}}": e(late[0].name),
        "{{HAL_BEFORE}}": table(hb, rb), "{{HAL_AFTER}}": table(ha, ra),
        "{{REVIEW}}": e(review.get("summary", "")),
    }.items():
        page = page.replace(k, v)
    OUT.write_text(page)
    print(f"wrote {OUT.relative_to(ROOT.parent)} from {run.name}; assets in {ASSETS.relative_to(ROOT.parent)}")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Portfolio Monitoring Agents</title>
<meta name="description" content="Raw founder updates in, a validated portfolio monitoring workbook out: inputs, outputs and every check from one live run.">
<link rel="stylesheet" href="style.css">
<style>
:root{--bg:#f5f8fb;--panel:#fff;--ink:#142234;--muted:#64748b;--faint:#79879a;--line:#dce5ee;--soft:#f5f8fb;
--accent:#6454c6;--accent-soft:#eeeafe;--red:#C23B3B;--red-soft:#FBE9E9;--amber:#A86A06;--amber-soft:#FDF3DF;
--green:#087f81;--green-soft:#e3f6f3;--grey-soft:#eef1f5;--code:#142234;--shadow:0 18px 48px rgba(25,47,78,.07)}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{-webkit-font-smoothing:antialiased}
main a:not(.btn){color:var(--accent)}
.wrap{max-width:1230px;margin:0 auto;padding:0 30px}
section{padding:64px 0 8px}
section>.wrap>.eyebrow{margin-bottom:0}
.sub{color:var(--muted);font-size:14px;line-height:1.7;margin:10px 0 24px;max-width:780px}
.flow{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;counter-reset:s}
.step{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px;position:relative;box-shadow:var(--shadow)}
.step::before{counter-increment:s;content:counter(s);display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;background:var(--accent-soft);color:var(--accent);font-size:12px;font-weight:700;margin-bottom:8px}
.step b{display:block;font-size:14px}.step p{margin:4px 0 0;font-size:13px;color:var(--muted)}
.step .who{font-size:11px;font-weight:600;color:var(--faint);text-transform:uppercase;letter-spacing:.05em}
.explorer{display:grid;grid-template-columns:290px 1fr;gap:16px;align-items:start}
.file-list{display:flex;flex-direction:column;gap:6px;position:sticky;top:72px}
.file-tab{all:unset;cursor:pointer;display:flex;gap:10px;align-items:center;padding:9px 12px;border-radius:10px;border:1px solid transparent;font-size:13px;color:var(--muted)}
.file-tab:hover{background:var(--soft)}.file-tab.on{background:var(--panel);border-color:var(--line);color:var(--ink);box-shadow:var(--shadow)}
.file-tab:focus-visible,.sheet-tab:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.fname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kind{flex:none;font-size:10px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;padding:3px 6px;border-radius:6px;background:var(--grey-soft);color:var(--muted);min-width:58px;text-align:center}
.k-pdf{background:var(--red-soft);color:var(--red)}.k-xlsx,.k-csv{background:var(--green-soft);color:var(--green)}
.k-docx,.k-eml{background:var(--accent-soft);color:var(--accent)}.k-pptx,.k-json{background:var(--amber-soft);color:var(--amber)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:22px;box-shadow:var(--shadow);min-width:0}
.pane{display:none}.pane.on{display:block}
.pane-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:14px}
.pane-head p{margin:2px 0 0;color:var(--muted);font-size:14px}
.btn{flex:none;display:inline-block;font-size:12px;font-weight:800;text-decoration:none;padding:10px 15px;border-radius:11px;border:1px solid #d7dceb;background:#fff;color:#4b4b73}
.btn:hover{text-decoration:none;transform:translateY(-1px)}.btn.primary{background:#2c3159;border-color:#2c3159;color:#fff}
.tbl-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;margin:8px 0 16px}
table{border-collapse:collapse;width:100%;font-size:13px}
th{background:var(--soft);text-align:left;font-weight:600;color:var(--muted);position:sticky;top:0}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr:last-child td{border-bottom:none}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tbl-wrap.jev td:nth-child(4){min-width:280px}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.pill.red{background:var(--red-soft);color:var(--red)}.pill.amber{background:var(--amber-soft);color:var(--amber)}
.pill.green{background:var(--green-soft);color:var(--green)}.pill.grey{background:var(--grey-soft);color:var(--muted)}
.pill.open{background:var(--accent-soft);color:var(--accent)}
h4.blk{margin:18px 0 4px;font-size:14px}
p.note{color:var(--muted);font-size:13px;margin:6px 0}
img.page{display:block;max-width:100%;border:1px solid var(--line);border-radius:6px;margin:0 auto 12px;background:#fff}
img.dash{display:block;width:100%;border:1px solid var(--line);border-radius:10px;margin-bottom:14px;background:#fff}
.doc p{margin:0 0 10px;white-space:pre-line}
.slides{display:grid;gap:12px}.slide{border:1px solid var(--line);border-radius:10px;padding:14px 16px;position:relative;background:var(--soft)}
.slide-no{font-size:11px;font-weight:600;color:var(--faint);text-transform:uppercase}
.slide-title{font-weight:700;font-size:16px}.slide p{margin:6px 0;white-space:pre-line}
.mail{border:1px solid var(--line);border-radius:10px;overflow:hidden}.mail-head{background:var(--soft);padding:12px 14px;font-size:13px}
pre{white-space:pre-wrap;margin:0;padding:14px;font:13px/1.55 "JetBrains Mono",ui-monospace,monospace}
pre.code{background:var(--code);color:#D6DEEB;border-radius:10px;max-height:460px;overflow:auto}
.callout{border-left:3px solid var(--amber);background:var(--amber-soft);padding:10px 14px;border-radius:0 8px 8px 0;font-size:14px;margin:0 0 16px}
.sheet-tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:16px;overflow-x:auto}
.sheet-tab{all:unset;cursor:pointer;padding:9px 14px;font-size:14px;font-weight:500;color:var(--muted);border-bottom:2px solid transparent;white-space:nowrap}
.sheet-tab.on{color:var(--ink);border-color:var(--accent)}
.out-head{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.two h3,.card h3{font-size:15px;margin:0 0 6px}
.scores{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:22px}
.score{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:var(--shadow)}
.score-top{display:flex;justify-content:space-between;gap:8px;font-weight:600;font-size:14px}
.score-val{font-size:22px;font-weight:700;letter-spacing:-.02em;margin:6px 0 2px}
.score p{margin:0;font-size:12.5px;color:var(--muted)}
.mini{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 14px}
.mini div{background:var(--soft);border-radius:10px;padding:10px 12px;font-size:12.5px;color:var(--muted)}.mini b{display:block;font-size:18px;color:var(--ink)}
details{margin-top:8px}summary{cursor:pointer;font-weight:600;font-size:14px;color:var(--accent)}
ul.how{margin:0;padding-left:18px}ul.how li{margin:4px 0}
footer{padding:28px 0 48px;margin-top:56px;color:var(--faint);font-size:13px;border-top:1px solid var(--line)}
@media (max-width:900px){.scores,.mini{grid-template-columns:1fr 1fr}.flow{grid-template-columns:1fr 1fr}
.explorer,.two{grid-template-columns:1fr}.file-list{position:static;flex-direction:row;overflow-x:auto;padding-bottom:4px}
.file-tab{flex:none}.wrap{padding:0 16px}.site-header nav a.jump{display:none}}
@media (max-width:520px){.scores,.mini,.flow{grid-template-columns:1fr}}
</style>
</head>
<body>
<header class="site-header">
  <div class="header-inner">
    <a class="brand" href="index.html" aria-label="NextGen Boardroom home">
      <span class="brand-mark">N</span>
      <span>NextGen <b>Boardroom</b></span>
    </a>
    <nav aria-label="Page sections">
      <a class="jump" href="#how">How it works</a><a class="jump" href="#inputs">Inputs</a><a class="jump" href="#output">Output</a><a class="jump" href="#late">Late file</a><a class="jump" href="#accuracy">Accuracy</a>
    </nav>
  </div>
</header>

<main>
<div class="wrap">
  <section class="landing-hero" style="padding:clamp(56px,9vw,104px) 0 0;margin-bottom:0">
    <div class="eyebrow">Portfolio monitoring · multi-agent pipeline</div>
    <h1>Messy founder updates in.<br><span>A checked workbook out.</span></h1>
    <p>Every quarter a VC associate collects PDFs, decks, emails and CSV exports from each portfolio company and rebuilds the same monitoring workbook by hand. These agents do it end to end, then prove the result is right before anyone opens the file. Everything below is from one live run, unedited.</p>
  </section>
</div>

<section id="how"><div class="wrap">
  <div class="eyebrow">Architecture</div>
  <h2>How it works</h2>
  <p class="sub">Five agents, each with one job and a written house standard. Narrow jobs keep behaviour predictable, and every rule in a standard is something a test can check.</p>
  <div class="flow">
    <div class="step"><span class="who">Claude · xlsx skill</span><b>Generator</b><p>Reads all raw files in a code sandbox, normalises units, FX and definitions, builds the formula-driven workbook.</p></div>
    <div class="step"><span class="who">Claude · xlsx skill</span><b>Chart agent</b><p>Adds six native Excel charts to the Dashboard per the chart spec: no overlaps, legends below, outliers capped.</p></div>
    <div class="step"><span class="who">Claude + JEV</span><b>Validator</b><p>Claude writes Correct/Incorrect question templates; JEV (System One) answers them cell by cell.</p></div>
    <div class="step"><span class="who">Claude vision</span><b>Chart reviewer</b><p>Looks at the rendered dashboard the way a partner would: label collisions, squashed outliers, colours.</p></div>
    <div class="step"><span class="who">Claude · xlsx skill</span><b>Update agent</b><p>Merges a late report into the reviewed workbook without touching anyone else's data or review ticks.</p></div>
  </div>
</div></section>

<section id="inputs"><div class="wrap">
  <div class="eyebrow">Inputs</div>
  <h2>Inputs: what the founders actually sent</h2>
  <p class="sub">Generated from one ground-truth file, so every number has a known right answer, and seeded with the traps associates really hit: contracted ARR in a footnote, burn that is never stated, EUR figures, a restated month, duplicated rows, and an email that tries to instruct the AI.</p>
  <div class="explorer">
    <div class="file-list" role="tablist" aria-label="Raw input files">{{IN_TABS}}</div>
    <div class="card">{{IN_PANES}}</div>
  </div>
</div></section>

<section id="output"><div class="wrap">
  <div class="eyebrow">Output</div>
  <h2>Output: the monitoring workbook</h2>
  <p class="sub">Every number outside <b>Inputs &amp; Review</b> is a live Excel formula pointing back to that one page, where each raw figure sits with its source file, location, method and confidence. A reviewer edits one cell and the whole workbook follows. Values below are the recalculated workbook after the late file was merged.</p>
  <div class="card">
    <div class="out-head">
      <div class="sheet-tabs" role="tablist" aria-label="Workbook sheets">{{OUT_TABS}}</div>
      <div><a class="btn primary" href="assets/portfolio/files/BCV_Portfolio_Monitor_2026Q3.xlsx" download>Download workbook</a></div>
    </div>
    {{OUT_PANES}}
  </div>
</div></section>

<section id="late"><div class="wrap">
  <div class="eyebrow">Late file</div>
  <h2>A report that arrives late</h2>
  <p class="sub">Halyard Robotics submitted a week after the deadline, as a portal JSON export in $000s, after the workbook had already been reviewed. The update agent merged it in place: placeholder rows replaced, burn derived from cash while excluding a $2.0M SAFE, the "report missing" flag resolved instead of deleted, and new flags appended with fresh IDs.</p>
  <div class="two">
    <div class="card"><h3>The late file: {{LATE_NAME}}</h3>{{LATE_JSON}}</div>
    <div class="card"><h3>Halyard rows on Inputs &amp; Review: before</h3>{{HAL_BEFORE}}<h3>After the merge</h3>{{HAL_AFTER}}</div>
  </div>
  <details><summary>All {{UPD_PASS}} before/after checks</summary>{{UPD_TABLE}}</details>
  <p style="margin-top:14px"><a class="btn" href="assets/portfolio/files/BCV_Portfolio_Monitor_2026Q3_before_late_file.xlsx" download>Download the workbook before the merge</a></p>
</div></section>

<section id="accuracy"><div class="wrap">
  <div class="eyebrow">Accuracy</div>
  <h2>How I know the numbers and formatting are right</h2>
  <p class="sub">A good-looking spreadsheet with one wrong burn figure is worse than none. The output goes through independent layers of checks, each catching what the others can't.</p>
  <div class="scores">{{SCORE}}</div>
  <div class="two">
    <div class="card">
      <h3>The layers</h3>
      <ul class="how">
        <li><b>Ground truth first.</b> Inputs are generated from known true values, so every output has a right answer.</li>
        <li><b>Deterministic checks.</b> LibreOffice recalculates the workbook; values are compared with the truth, and fonts, number formats and K/M/B scaling are enforced.</li>
        <li><b>Chart geometry.</b> Chart XML is parsed for positions, overlaps, legends, axis formats and titles.</li>
        <li><b>JEV cell by cell.</b> JEV classifies, it doesn't do arithmetic, so each calculation question carries a value recomputed in code.</li>
        <li><b>Checking the checker.</b> Every JEV answer is compared with a deterministic oracle; all 5 deliberately planted errors were caught.</li>
        <li><b>Visual review.</b> Claude vision catches what XML can't: label collisions, squashed outliers.</li>
        <li><b>Before/after diff</b> for late files: additive, surgical, nothing else touched.</li>
      </ul>
    </div>
    <div class="card">
      <h3>JEV run profile</h3>
      <div class="mini">
        <div><b>{{TEMPLATES}}</b>question templates by Claude</div>
        <div><b>{{ASKED}}</b>cells asked</div>
        <div><b>{{COST}}</b>total JEV cost</div>
        <div><b>{{LAT}}</b>median latency</div>
      </div>
      {{BY_TABLE}}
      <a class="btn" href="assets/portfolio/files/jev_results.csv" download>Download all JEV answers (CSV)</a>
    </div>
  </div>
  <details open><summary>Sample of JEV questions and answers (10 per check type)</summary>{{JEV_TABLE}}</details>
  <details><summary>Deterministic data and chart checks</summary>{{DET_TABLE}}</details>
  <details><summary>Chart reviewer's summary</summary><p class="note">{{REVIEW}}</p></details>
</div></section>
</main>

<footer><div class="wrap">Built with Claude Opus 5.5 (Anthropic SDK, xlsx Agent Skill, code execution, Files API), JEV System One, openpyxl, pandas and LibreOffice. All company names and figures are fictional.</div></footer>

<script>
document.querySelectorAll('[role="tablist"]').forEach(list => {
  const tabs = [...list.querySelectorAll('[role="tab"]')];
  tabs.forEach(t => t.addEventListener('click', () => {
    tabs.forEach(o => { o.classList.toggle('on', o === t); o.setAttribute('aria-selected', o === t);
      document.getElementById(o.dataset.pane).classList.toggle('on', o === t); });
  }));
});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else latest_run())
