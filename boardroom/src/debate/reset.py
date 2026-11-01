"""Delete stored debates so the next notebook session starts clean.

Removes run records (SQLite), debate-derived reports, LangGraph checkpoints and VC memory (Postgres).
Keeps the RAG corpus (`chunks`).
"""
from __future__ import annotations

import shutil
from pathlib import Path


def local_debate_files(data_dir: Path, reports_dir: Path) -> list[Path]:
    files = [p for p in Path(data_dir).glob("*") if p.is_file()]
    if Path(reports_dir).exists():
        files.append(Path(reports_dir))
    return sorted(files)


def reset_local_debates(data_dir: Path, reports_dir: Path, *, postgres: bool = True) -> dict:
    removed = local_debate_files(data_dir, reports_dir)
    for path in removed:
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    threads = memories = 0
    if postgres:
        from debate.memory.setup import persistence

        with persistence() as (saver, store):
            thread_ids = {c.config["configurable"]["thread_id"] for c in saver.list(None)}
            for thread_id in thread_ids:
                saver.delete_thread(thread_id)
            threads = len(thread_ids)
            for namespace in store.list_namespaces(limit=10_000):
                for item in store.search(namespace, limit=10_000):
                    store.delete(namespace, item.key)
                    memories += 1
    return {"files": [str(p) for p in removed], "checkpoint_threads": threads, "memory_items": memories}
