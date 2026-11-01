"""Validate inputs.json before building: schema, ranges, evidence, and the opening balance sheet.

Usage: python validate_inputs.py inputs.json   (exit 1 when any error; warnings never fail)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_model as bm  # noqa: E402

# (min, max) sanity bands; outside them is an error, near the edge a warning
RANGES = {
    "pct": (0.0, 1.0),
    "conf": (0.0, 1.0),
}
PLAUSIBLE = {
    "Logo_Churn_Monthly": (0.0, 0.05),
    "Expansion_Monthly": (0.0, 0.05),
    "Hosting_Pct": (0.0, 0.40),
    "DSO_Days": (0, 180),
    "DPO_Days": (0, 180),
    "Avg_Deferred_Months": (0, 12),
    "Depr_Life_Months": (12, 120),
    "Equity_Raise_Month": (0, 36),
    "Debt_Draw_Month": (0, 36),
}


def validate(data):
    errors, warnings = bm.apply_inputs(data), []
    cat = bm._catalog()
    supplied = set(data.get("inputs", {}))
    for name, it in cat.items():
        v = it["value"]
        if name not in supplied:
            warnings.append(f"{name}: not supplied, template default kept (source '{it['source']}')")
        lo_hi = RANGES.get(it["fmt"])
        if lo_hi and isinstance(v, (int, float)) and not lo_hi[0] <= v <= lo_hi[1]:
            errors.append(f"{name}: {v} outside {lo_hi} (percentages are fractions: 0.09 = 9%)")
        if name in PLAUSIBLE and isinstance(v, (int, float)):
            lo, hi = PLAUSIBLE[name]
            if not lo <= v <= hi:
                warnings.append(f"{name}: {v} is outside the plausible band {PLAUSIBLE[name]}; confirm")
        if name in supplied and it["source"] == "Data-derived" and not it["note"]:
            errors.append(f"{name}: Data-derived inputs need evidence (file + how it was derived)")
        if name in supplied and it["source"] in ("Agent default", "Benchmark") and it["conf"] > 0.80:
            warnings.append(f"{name}: {it['source']} with confidence {it['conf']:.0%} is unusually high")

    v = {k: it["value"] for k, it in cat.items()}
    assets = v["Open_Cash"] + v["Open_AR"] + v["Open_Prepaid"] + v["Open_PPE"]
    claims = (v["Open_AP"] + v["Open_Accrued"] + v["Open_Deferred_Rev"] + v["Open_Debt"]
              + v["Open_Paid_In_Capital"] + v["Open_Retained_Earnings"])
    if abs(assets - claims) > v["Check_Tolerance"]:
        errors.append(f"Opening balance sheet does not balance: assets {assets:,.0f} vs L+E {claims:,.0f} "
                      f"(diff {assets - claims:,.0f}). Fix the trial-balance mapping before building.")
    if v["Open_Customers"] and not 1_000 <= v["Open_ARR"] / v["Open_Customers"] <= 5_000_000:
        warnings.append("Opening ARR per customer looks implausible; check units ($ vs $000s)")
    return {"status": "pass" if not errors else "fail", "errors": errors, "warnings": warnings}


if __name__ == "__main__":
    out = validate(json.loads(Path(sys.argv[1]).read_text()))
    print(json.dumps(out, indent=2))
    sys.exit(0 if out["status"] == "pass" else 1)
