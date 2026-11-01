"""Offline stand-in for an agent run: write inputs.json from ground truth, build and recalc like the
container would, so the host gate and scorer can be tested without API calls.

Usage: python eval/simulate_agent_output.py <run_dir>
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent import config, host_gate  # noqa: E402

run_dir = Path(sys.argv[1])
run_dir.mkdir(parents=True, exist_ok=True)
truth = json.loads((ROOT / "sample_data" / "ground_truth.json").read_text())
data = json.loads((config.SKILL_DIR / "scripts" / "inputs_template.json").read_text())
for name, value in truth["values"].items():
    data["inputs"][name] = {"value": value, "source": "Data-derived", "confidence": 0.85,
                            "evidence": "simulated: ground truth"}
for name in truth["expect_low_confidence"]:
    data["inputs"][name].update(source="Benchmark", confidence=0.55, evidence="No company data; benchmark")
data["flags"] = [{"input": n, "confidence": 0.55, "reason": "Not in data pack", "action": "Ask company"}
                 for n in truth["expect_low_confidence"]]
data["flags"].append({"sheet": "Balance Sheet", "row": "dr", "month": 36, "item": "Deferred revenue, Month 36",
                      "confidence": 0.5, "reason": "No billing schedule", "action": "Request invoice export"})
(run_dir / "inputs.json").write_text(json.dumps(data, indent=2))
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "Stratoline_Portfolio_Model.xlsx"
    subprocess.run([sys.executable, str(config.SKILL_DIR / "scripts" / "build_model.py"), "--inputs",
                    str(run_dir / "inputs.json"), "--out", str(out)], check=True, capture_output=True)
    shutil.copy(host_gate.recalc(out, Path(tmp) / "rc"), run_dir / out.name)
(run_dir / "agent_report.md").write_text("# Simulated run\n")
print(run_dir)
