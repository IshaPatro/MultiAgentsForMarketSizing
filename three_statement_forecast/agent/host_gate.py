"""Host-side gate: never trust the sandbox's own checks. Re-run every check locally.

1. validate_inputs on the downloaded inputs.json
2. rebuild the model locally from inputs.json and diff formulas against the delivered file
   (catches hand-edited cells, a stale build, or a different builder version)
3. recalculate the delivered workbook with LibreOffice
4. lint (house standard) and verify (independent Python recomputation)

Usage: python -m agent.host_gate <run_dir>
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from openpyxl import load_workbook

from . import config

SCRIPTS = config.SKILL_DIR / "scripts"
SOFFICE_CANDIDATES = ["soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"]


def _run(script, *args):
    p = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"status": "error", "stdout": p.stdout[-2000:], "stderr": p.stderr[-2000:]}


def soffice():
    for c in SOFFICE_CANDIDATES:
        if shutil.which(c) or Path(c).exists():
            return shutil.which(c) or c
    return None


def recalc(src, dst_dir):
    exe = soffice()
    if not exe:
        return None
    subprocess.run([exe, "--headless", "--calc", "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir",
                    str(dst_dir), str(src)], capture_output=True, timeout=300)
    out = Path(dst_dir) / Path(src).name
    return out if out.exists() else None


def formula_diff(delivered, rebuilt, limit=20):
    a, b = load_workbook(delivered), load_workbook(rebuilt)
    diffs = []
    if a.sheetnames != b.sheetnames:
        return [f"sheet list differs: {a.sheetnames} vs {b.sheetnames}"]
    for name in a.sheetnames:
        wa, wb = a[name], b[name]
        for r in range(1, max(wa.max_row, wb.max_row) + 1):
            for c in range(1, max(wa.max_column, wb.max_column) + 1):
                va, vb = wa.cell(r, c).value, wb.cell(r, c).value
                if va != vb and not (va in (None, "") and vb in (None, "")):
                    diffs.append(f"{name}!{wa.cell(r, c).coordinate}: delivered {va!r} vs rebuilt {vb!r}")
                    if len(diffs) >= limit:
                        return diffs
    return diffs


def gate(run_dir):
    run_dir = Path(run_dir)
    report = {"status": "pass", "checks": {}}
    inputs = run_dir / "inputs.json"
    models = sorted(p for p in run_dir.glob("*.xlsx") if not p.name.startswith("~"))
    if not inputs.exists() or not models:
        return {"status": "fail", "checks": {"deliverables": "missing inputs.json or model .xlsx"}}
    model = models[-1]

    report["checks"]["validate_inputs"] = _run("validate_inputs.py", inputs)
    with tempfile.TemporaryDirectory() as tmp:
        rebuilt = Path(tmp) / "rebuilt.xlsx"
        built = subprocess.run([sys.executable, str(SCRIPTS / "build_model.py"), "--inputs", str(inputs), "--out",
                                str(rebuilt)], capture_output=True, text=True)
        if built.returncode:
            report["checks"]["rebuild"] = {"status": "fail", "stdout": built.stdout[-2000:]}
        else:
            diffs = formula_diff(model, rebuilt)
            report["checks"]["rebuild_matches_delivered"] = {"status": "pass" if not diffs else "fail",
                                                             "differences": diffs}
        recalced = recalc(model, Path(tmp) / "recalc")
        if recalced:
            final = run_dir / f"{model.stem}.checked.xlsx"
            shutil.copy(recalced, final)
            report["checks"]["lint"] = _run("lint_model.py", final)
            report["checks"]["verify"] = _run("verify_model.py", final)
            report["checked_workbook"] = final.name
        else:
            report["checks"]["recalc"] = {"status": "skipped", "reason": "LibreOffice not found on host"}

    for v in report["checks"].values():
        if isinstance(v, dict) and v.get("status") in ("fail", "error"):
            report["status"] = "fail"
    (run_dir / "gate_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report


if __name__ == "__main__":
    r = gate(sys.argv[1])
    print(json.dumps(r, indent=2, ensure_ascii=False))
    sys.exit(0 if r["status"] == "pass" else 1)
