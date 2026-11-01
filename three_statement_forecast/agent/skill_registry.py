"""Publish the company skill (bcv-portfolio-model) to the Skills API and pin its version.

The skill is re-uploaded as a new version only when its files change, so runs are
reproducible (pinned version id) and prompt caches stay warm between runs.
"""
import hashlib
import json
import subprocess
import sys

from anthropic.lib import files_from_dir

from . import config


def refresh_generated_files():
    """Regenerate the catalog and template from the builder so they never drift from it."""
    scripts = config.SKILL_DIR / "scripts"
    subprocess.run([sys.executable, "build_model.py", "--template", "inputs_template.json"], cwd=scripts, check=True)
    subprocess.run([sys.executable, "build_model.py", "--catalog", "../reference/input_catalog.md"], cwd=scripts,
                   check=True)


def skill_hash():
    h = hashlib.sha256()
    for p in sorted(config.SKILL_DIR.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(config.SKILL_DIR)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def ensure_custom_skill(client):
    """Return the container entry for the company skill, creating or versioning it as needed."""
    refresh_generated_files()
    digest = skill_hash()
    state = json.loads(config.SKILL_STATE.read_text()) if config.SKILL_STATE.exists() else {}
    if state.get("hash") != digest:
        files = files_from_dir(str(config.SKILL_DIR))
        if state.get("skill_id"):
            version = client.skills.versions.create(skill_id=state["skill_id"], files=files)
            state.update(version_id=version.id)
        else:
            skill = client.skills.create(files=files, display_name="BCV portfolio model standard")
            state.update(skill_id=skill.id, version_id=skill.latest_version_id)
        state["hash"] = digest
        config.SKILL_STATE.write_text(json.dumps(state, indent=2))
        print(f"Published skill {state['skill_id']} version {state['version_id']}")
    return {"type": "custom", "skill_id": state["skill_id"], "version": state["version_id"]}
