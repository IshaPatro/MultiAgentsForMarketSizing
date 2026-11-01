import pytest
from conftest import needs_pg

from debate.rag.evaluate import Question, evaluate, load_questions, report_markdown
from debate.rag.retriever import Chunk, Retrieval


class Ranked:
    """Returns a fixed ranking per question."""

    def __init__(self, rankings):
        self.rankings = rankings

    def search(self, query, company_id, k):
        return Retrieval(query, [Chunk(cid, text, None, None, "fact") for cid, text in self.rankings[query][:k]])


def test_hit_at_k_and_mrr_with_ids_or_answer_phrases():
    qs = [Question(q="a", kind="number", expect=["x"]),                         # rank 1
          Question(q="b", kind="number", expect=["y"]),                         # rank 3
          Question(q="c", kind="fact", expect=["z"], contains=["smart toilet"]),  # answered by phrase at rank 2
          Question(q="d", kind="fact", expect=["w"])]                           # never retrieved
    r = Ranked({"a": [("x", "")], "b": [("p", ""), ("q", ""), ("y", "")],
                "c": [("n1", "nothing"), ("n2", "Matus' Smart Toilet start-up")], "d": [("p", "")]})
    res = evaluate(r, "co", qs, k=2)
    assert [row["rank"] for row in res["rows"]] == [1, 3, 2, None]
    assert res["overall"]["hit_at_k"] == pytest.approx(0.5)                     # a and c within k=2
    assert res["overall"]["mrr"] == pytest.approx((1 + 1 / 3 + 1 / 2 + 0) / 4)
    assert res["by_kind"]["fact"]["hit_at_k"] == pytest.approx(0.5)
    assert "## Misses (2)" in report_markdown(res)


def test_question_set_is_well_formed():
    company, qs = load_questions()
    assert company == "biobot" and len(qs) >= 40 and all(q.expect for q in qs)
    assert len({q.q for q in qs}) == len(qs)


def _ingested():
    try:
        from debate.rag import db
        from debate.rag.embeddings import FastEmbedder
        conn = db.connect()
        n = conn.execute("SELECT count(*) FROM chunks WHERE company_id='biobot' AND doc_type='fact' AND "
                         "embedding_model=%s", (FastEmbedder.name,)).fetchone()[0]
        conn.close()
        return n > 0
    except Exception:  # noqa: BLE001
        return False


@needs_pg
@pytest.mark.skipif(not _ingested(), reason="structured Biobot corpus not ingested (debate build-corpus && debate ingest)")
def test_real_retrieval_stays_above_the_quality_bar():
    from debate.rag import db
    from debate.rag.embeddings import FastEmbedder
    from debate.rag.retriever import PgRetriever

    company, qs = load_questions()
    conn = db.connect()
    try:
        res = evaluate(PgRetriever(conn, FastEmbedder()), company, qs, k=4)
    finally:
        conn.close()
    assert res["overall"]["hit_at_k"] >= 0.90, report_markdown(res)
    assert res["by_kind"]["number"]["hit_at_k"] >= 0.95
    assert res["by_kind"]["scenario_history"]["hit_at_k"] >= 0.90, report_markdown(res)
    assert res["by_kind"]["scenario_current"]["hit_at_k"] >= 0.90, report_markdown(res)
