"""Chart checks for the Dashboard, read straight from the .xlsx XML (openpyxl drops charts on load).

Checks: chart count, titles, legend placement (bottom/top/right, not overlaid), cluttered data labels,
value-axis number formats, category counts, and geometry: no chart overlaps another chart, a table,
or anything above the last table row. Also renders the Dashboard to PNG for visual review.

Usage: python eval/chart_checks.py <workbook.xlsx> [--render out_dir]
"""
import json
import posixpath
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}
EMU_PX, EMU_PT = 9525, 12700
MIN_CHARTS, MAX_CATEGORIES, MAX_LEGEND = 4, 10, 7
SOFFICE = ["soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"]


def _rels(z, part):
    path = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    if path not in z.namelist():
        return {}
    root = ET.fromstring(z.read(path))
    def resolve(target):  # absolute ("/xl/...", as openpyxl writes) or relative to the part
        if target.startswith("/"):
            return target.lstrip("/")
        return posixpath.normpath(posixpath.join(posixpath.dirname(part), target))

    return {r.get("Id"): resolve(r.get("Target")) for r in root.findall("rel:Relationship", NS)}


def _col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1  # zero-based


def _cell(ref):
    m = re.match(r"\$?([A-Z]+)\$?(\d+)", ref)
    return _col_index(m.group(1)), int(m.group(2)) - 1


class Grid:
    """Column/row offsets in EMU for one worksheet."""

    def __init__(self, sheet_xml):
        fmt = sheet_xml.find("m:sheetFormatPr", NS)
        self.default_w = float(fmt.get("defaultColWidth", fmt.get("baseColWidth", 8.43))) if fmt is not None else 8.43
        self.default_h = float(fmt.get("defaultRowHeight", 15)) if fmt is not None else 15.0
        self.widths, self.heights = {}, {}
        for col in sheet_xml.findall("m:cols/m:col", NS):
            for i in range(int(col.get("min")) - 1, int(col.get("max"))):
                self.widths[i] = float(col.get("width"))
        for row in sheet_xml.findall("m:sheetData/m:row", NS):
            if row.get("ht"):
                self.heights[int(row.get("r")) - 1] = float(row.get("ht"))

    def x(self, col, off=0):
        return sum(int(self.widths.get(i, self.default_w) * 7 + 5) * EMU_PX for i in range(col)) + off

    def y(self, row, off=0):
        return sum(self.heights.get(i, self.default_h) * EMU_PT for i in range(row)) + off


def _sheet_part(z, name):
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = _rels(z, "xl/workbook.xml")
    for s in wb.findall("m:sheets/m:sheet", NS):
        if s.get("name") == name:
            return rels[s.get(f"{{{NS['r']}}}id")]
    return None


def _anchor_rect(anchor, grid):
    f = anchor.find("xdr:from", NS)
    col, colo, row, rowo = (int(f.find(f"xdr:{k}", NS).text) for k in ("col", "colOff", "row", "rowOff"))
    x0, y0 = grid.x(col, colo), grid.y(row, rowo)
    if anchor.tag.endswith("twoCellAnchor"):
        t = anchor.find("xdr:to", NS)
        c2, c2o, r2, r2o = (int(t.find(f"xdr:{k}", NS).text) for k in ("col", "colOff", "row", "rowOff"))
        return x0, y0, grid.x(c2, c2o), grid.y(r2, r2o)
    ext = anchor.find("xdr:ext", NS)
    return x0, y0, x0 + int(ext.get("cx")), y0 + int(ext.get("cy"))


def _overlap(a, b, tol=EMU_PX * 2):
    return a[0] < b[2] - tol and b[0] < a[2] - tol and a[1] < b[3] - tol and b[1] < a[3] - tol


def _title(chart):
    t = chart.find("c:chart/c:title", NS)
    if t is None:
        return ""
    return "".join(x.text or "" for x in t.iter(f"{{{NS['a']}}}t")) or "(formula title)"


def inspect(path, sheet="Dashboard"):
    """Return (charts, tables) with geometry and properties."""
    z = zipfile.ZipFile(path)
    part = _sheet_part(z, sheet)
    if part is None:
        return [], []
    sx = ET.fromstring(z.read(part))
    grid = Grid(sx)
    rels = _rels(z, part)
    tables = []
    for tp in sx.findall("m:tableParts/m:tablePart", NS):
        t = ET.fromstring(z.read(rels[tp.get(f"{{{NS['r']}}}id")]))
        a, b = t.get("ref").split(":")
        (c1, r1), (c2, r2) = _cell(a), _cell(b)
        tables.append({"name": t.get("displayName"), "ref": t.get("ref"), "last_row": r2,
                       "rect": (grid.x(c1), grid.y(r1), grid.x(c2 + 1), grid.y(r2 + 1))})
    charts = []
    d = sx.find("m:drawing", NS)
    if d is None:
        return charts, tables
    dpart = rels[d.get(f"{{{NS['r']}}}id")]
    dx, drels = ET.fromstring(z.read(dpart)), _rels(z, dpart)
    for anchor in list(dx):
        cref = anchor.find(".//c:chart", NS)
        if cref is None:
            continue
        cx = ET.fromstring(z.read(drels[cref.get(f"{{{NS['r']}}}id")]))
        plot = cx.find("c:chart/c:plotArea", NS)
        kinds = [el.tag.split("}")[1] for el in plot if el.tag.endswith("Chart")]
        series = plot.findall(".//c:ser", NS)
        legend = cx.find("c:chart/c:legend", NS)
        cats = []
        for ser in series:
            pts = ser.find("c:cat//c:ptCount", NS) or ser.find("c:xVal//c:ptCount", NS)
            ref = ser.find("c:cat//c:f", NS)
            if ref is not None and ref.text:
                m = re.findall(r"\$?(\d+)", ref.text.split("!")[-1])
                if len(m) == 2:
                    cats.append(int(m[1]) - int(m[0]) + 1)
            elif pts is not None:
                cats.append(int(pts.get("val")))
        dl_bad = any(el.get("val") == "1" for tag in ("showSerName", "showCatName", "showLegendKey")
                     for el in plot.iter(f"{{{NS['c']}}}{tag}"))
        val_axes = plot.findall("c:valAx", NS)
        fmt_missing = [ax for ax in val_axes
                       if ax.find("c:delete", NS) is None or ax.find("c:delete", NS).get("val") != "1"
                       if (ax.find("c:numFmt", NS) is None or ax.find("c:numFmt", NS).get("formatCode") == "General")]
        refs = [f.text for f in plot.iter(f"{{{NS['c']}}}f") if f.text]
        literal = [ser for ser in series if ser.find("c:val/c:numLit", NS) is not None
                   or ser.find("c:yVal/c:numLit", NS) is not None]
        charts.append({
            "title": _title(cx), "kinds": kinds, "series": len(series), "categories": max(cats or [0]),
            "legend": None if legend is None else {
                "pos": (legend.find("c:legendPos", NS).get("val") if legend.find("c:legendPos", NS) is not None
                        else "r"),
                "overlay": (legend.find("c:overlay", NS).get("val") if legend.find("c:overlay", NS) is not None
                            else "0") in ("1", "true")},
            "cluttered_labels": dl_bad, "axes_without_format": len(fmt_missing),
            "hardcoded_series": len(literal), "refs": refs, "rect": _anchor_rect(anchor, grid)})
    return charts, tables


def run_chart_checks(path):
    charts, tables = inspect(path)
    out = []

    def res(check, ok, detail=""):
        out.append({"check": check, "status": "PASS" if ok else "FAIL", "detail": detail})

    res(f"Dashboard has ≥ {MIN_CHARTS} charts", len(charts) >= MIN_CHARTS, f"{len(charts)} charts")
    res("Every chart has a title", all(c["title"] for c in charts),
        ", ".join(f"chart {i + 1}" for i, c in enumerate(charts) if not c["title"]))
    bad_leg = []
    for i, c in enumerate(charts):
        multi = c["series"] > 1 or any(k in ("pieChart", "doughnutChart") for k in c["kinds"])
        if multi and c["legend"] is None:
            bad_leg.append(f"'{c['title']}': multi-series chart without legend")
        if c["legend"] and c["legend"]["overlay"]:
            bad_leg.append(f"'{c['title']}': legend overlays the plot")
        if c["legend"] and c["legend"]["pos"] not in ("b", "t", "r"):
            bad_leg.append(f"'{c['title']}': legend position {c['legend']['pos']}")
        if c["legend"] and c["series"] > MAX_LEGEND:
            bad_leg.append(f"'{c['title']}': {c['series']} legend entries")
    res("Legends present where needed, not overlaid, ≤ 7 entries", not bad_leg, "; ".join(bad_leg))
    res("Data labels show values only", not any(c["cluttered_labels"] for c in charts),
        ", ".join(c["title"] for c in charts if c["cluttered_labels"]))
    res("Value axes have explicit number formats", not any(c["axes_without_format"] for c in charts),
        ", ".join(c["title"] for c in charts if c["axes_without_format"]))
    res(f"≤ {MAX_CATEGORIES} categories per chart", all(c["categories"] <= MAX_CATEGORIES for c in charts),
        ", ".join(f"{c['title']}: {c['categories']}" for c in charts if c["categories"] > MAX_CATEGORIES))
    res("Series reference cells (no typed-in chart data)", not any(c["hardcoded_series"] for c in charts),
        ", ".join(c["title"] for c in charts if c["hardcoded_series"]))
    clashes = [f"'{a['title']}' × '{b['title']}'" for i, a in enumerate(charts) for b in charts[i + 1:]
               if _overlap(a["rect"], b["rect"])]
    res("Charts do not overlap each other", not clashes, "; ".join(clashes))
    below = max((t["rect"][3] for t in tables), default=0)
    on_table = [c["title"] for c in charts if c["rect"][1] < below - EMU_PX * 2
                or any(_overlap(c["rect"], t["rect"]) for t in tables)]
    res("Charts sit below the tables and tiles", not on_table, ", ".join(on_table))
    return out


def render_dashboard(path, out_dir, dpi=80, max_pages=2):
    """Render the workbook with LibreOffice and return PNGs of the first pages (the Dashboard)."""
    exe = next((shutil.which(s) or s for s in SOFFICE if shutil.which(s) or Path(s).exists()), None)
    if not exe or not shutil.which("pdftoppm"):
        return []
    out_dir = Path(out_dir)
    calc_dir = out_dir / "recalc"
    calc_dir.mkdir(parents=True, exist_ok=True)
    # Recalculate first: charts over uncalculated formulas render wrong values.
    subprocess.run([exe, "--headless", "--calc", "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir",
                    str(calc_dir), str(path)], capture_output=True, timeout=300)
    calc = calc_dir / Path(path).name
    subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir),
                    str(calc if calc.exists() else path)], capture_output=True, timeout=300)
    pdf = out_dir / (Path(path).stem + ".pdf")
    if not pdf.exists():
        return []
    prefix = out_dir / "dashboard"
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", str(max_pages), str(pdf), str(prefix)],
                   capture_output=True, timeout=120)
    return sorted(out_dir.glob("dashboard*.png"))


if __name__ == "__main__":
    res = run_chart_checks(sys.argv[1])
    print(json.dumps(res, indent=2, ensure_ascii=False))
    if "--render" in sys.argv:
        print(render_dashboard(sys.argv[1], sys.argv[sys.argv.index("--render") + 1]))
    sys.exit(0 if all(r["status"] == "PASS" for r in res) else 1)
