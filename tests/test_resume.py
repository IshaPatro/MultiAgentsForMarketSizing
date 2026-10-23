import pytest

from conftest import needs_pg

from debate.memory.setup import persistence
from debate.storage.sqlite_store import SqliteStore


@needs_pg
def test_pause_resume_replay_and_no_duplicate_turns(tmp_path, monkeypatch, capsys):
    import debate.cli as cli

    monkeypatch.setattr(cli, "DATA", tmp_path)
    assert cli.main(["run", "--dry-run", "--rounds", "3", "--pause-after", "ceo"]) == 0
    store = SqliteStore(tmp_path / "debate.sqlite")
    run_id, thread_id = store.conn.execute("SELECT run_id, thread_id FROM runs").fetchone()
    try:
        assert store.get_run(run_id)["status"] == "paused"
        assert [r["seq"] for r in store.turns(run_id)] == [1, 2, 3]   # VC, moderator, CEO
        assert cli.main(["replay", run_id, "--list"]) == 0
        listing = capsys.readouterr().out
        checkpoint_id = next(line.split()[0] for line in listing.splitlines() if "turns=3" in line)
        assert cli.main(["replay", run_id, "--checkpoint-id", checkpoint_id]) == 0
        historical = capsys.readouterr().out
        assert "[3] CEO" in historical and "[4] DEVIL" not in historical
        store.conn.execute("UPDATE runs SET status='running' WHERE run_id=?", (run_id,))
        store.conn.commit()  # simulate a process that stopped before recording its paused status
        assert cli.main(["resume", run_id]) == 0
        assert store.get_run(run_id)["status"] == "completed"
        assert [r["seq"] for r in store.turns(run_id)] == list(range(1, 16))
        assert store.call_stats(run_id)["calls"] == 21  # includes the final VC decision and two routing calls (pitch, market size)
        assert store.get_decision(run_id)["decision"]
        assert cli.main(["replay", run_id]) == 0
        assert "[14] DEVIL" in capsys.readouterr().out
        assert cli.main(["resume", run_id]) == 2  # completed runs cannot replay work
    finally:
        with persistence() as (saver, _):
            saver.delete_thread(thread_id)
