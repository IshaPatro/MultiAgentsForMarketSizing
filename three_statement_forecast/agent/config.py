"""Agent configuration. Change behaviour here, not in the orchestrator."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "bcv-portfolio-model"
SKILL_STATE = ROOT / ".skill_state.json"  # cached custom-skill id + pinned version
RUNS_DIR = ROOT / "runs"

MODEL = "claude-opus-5-5"
EFFORT = "high"  # Opus 5.5 defaults to medium; data mapping + self-checking benefits from high
MAX_TOKENS = 64_000  # streaming, so large outputs don't hit HTTP timeouts
CODE_EXECUTION_TOOL = {"type": "code_execution_20260521", "name": "code_execution"}

# Server-side refusal fallback (Claude API only). Set False on Bedrock/Vertex/Foundry.
USE_FALLBACKS = True
FALLBACK_BETA = "server-side-fallback-2026-07-01"

MAX_PAUSE_CONTINUATIONS = 12  # pause_turn resumes per round (long code-execution sessions)
MAX_GATE_ROUNDS = 2  # how many times the host gate may send failures back to the agent

ANTHROPIC_SKILLS = [{"type": "anthropic", "skill_id": "xlsx", "version": "latest"}]
DELIVERABLES = ("inputs.json", "agent_report.md")  # plus one *.xlsx model
