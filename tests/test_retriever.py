"""Retrieval stays inside one company's corpus and one embedding model. Needs the Postgres container."""
import pytest
from conftest import needs_pg

from debate.rag import db
from debate.rag.embeddings import HashEmbedder
from debate.rag.retriever import BM25, PgRetriever, tokenize

CO = "testco"
ROWS = [  # chunk_id, company, text
    ("t_robot", CO, "wastewater sampling robot pump prototype built from off the shelf parts"),
    ("t_opioid", CO, "the founders planned to sell opioid consumption data to cities"),
    ("t_other", "otherco", "wastewater opioid consumption data cities planted chunk from another company"),
]


@pytest.fixture
def retriever():
    conn = db.connect()
    db.ensure_schema(conn)
    emb = HashEmbedder()
    conn.execute("DELETE FROM chunks WHERE company_id = ANY(%s)", ([CO, "otherco"],))
    for cid, company, text in ROWS:
        conn.execute(
            "INSERT INTO chunks (chunk_id, company_id, source_id, source, doc_type, page, section, text,"
            " embedding_model, embedding) VALUES (%s,%s,'s','s','case',1,'x',%s,%s,%s)",
            (cid, company, text, emb.name, emb.embed_docs([text])[0]))
    yield PgRetriever(conn, emb)
    conn.execute("DELETE FROM chunks WHERE company_id = ANY(%s)", ([CO, "otherco"],))
    conn.close()


@needs_pg
def test_hybrid_search_ranks_the_matching_chunk_first(retriever):
    got = retriever.search("opioid consumption data for cities", CO, k=2)
    assert got.ids[0] == "t_opioid"


@needs_pg
def test_other_companies_are_invisible_even_on_an_exact_match(retriever):
    got = retriever.search("wastewater opioid consumption data cities planted chunk from another company", CO, k=10)
    assert "t_other" not in got.ids
    assert retriever.search("anything", "nobody", k=10).chunks == []


@needs_pg
def test_other_embedding_models_are_invisible(retriever):
    retriever.conn.execute("UPDATE chunks SET embedding_model='other-model' WHERE chunk_id='t_robot'")
    assert "t_robot" not in retriever.search("sampling robot pump", CO, k=10).ids


@needs_pg
def test_by_ids_returns_only_this_companys_chunks(retriever):
    assert {c.chunk_id for c in retriever.by_ids(["t_robot", "t_other"], CO)} == {"t_robot"}


def test_tokenize_keeps_figures_folds_plurals_and_drops_filler():
    assert tokenize("Which hedge funds paid $3.6 million for 50,427 manholes?") == [
        "hedge", "fund", "paid", "3.6", "million", "50,427", "manhole"]
    assert tokenize("?!") == []


def test_bm25_lets_a_rare_term_beat_common_ones():
    docs = [(f"d{i}", "public health data wastewater public health budget") for i in range(8)]
    docs.append(("hedge", "hedge funds buy wastewater data"))
    index = BM25(docs)
    assert index.top("hedge funds wastewater public health data", 1) == ["hedge"]
    assert index.top("nothing matches", 3) == []


def test_diversify_caps_assumptions_and_sizing_models_but_keeps_rank_order():
    from debate.rag.retriever import Chunk, diversify

    def c(cid, kind):
        return Chunk(cid, "t", None, None, kind)

    ranked = [c("a1", "assumption"), c("a2", "assumption"), c("s1", "sizing_model"), c("s2", "sizing_model"),
              c("f1", "fact"), c("n1", "narrative"), c("a3", "assumption")]
    assert [x.chunk_id for x in diversify(ranked, 4)] == ["a1", "s1", "f1", "n1"]
    leads = [c("l1", "public_lead"), c("l2", "public_lead"), c("p1", "public_source"), c("p2", "public_source")]
    assert [x.chunk_id for x in diversify(leads, 3)] == ["l1", "p1", "p2"]


def test_agenda_topics_reserve_evidence_slots_for_their_corpus_topics():
    from types import SimpleNamespace

    from debate.nodes.agents import evidence_for
    from debate.rag.retriever import Chunk, Retrieval

    general = [Chunk(f"g{i}", "t", None, None, "narrative", topic="founders_team") for i in range(10)]
    market = [Chunk(f"m{i}", "t", None, None, "fact", topic="market") for i in range(3)]

    class Search:
        def search(self, query, company_id, k):
            return Retrieval(query, (market if query == "topic words" else general)[:k])

    deps = SimpleNamespace(retriever=Search(), company=SimpleNamespace(company_id="co"))
    got = [c.chunk_id for c in evidence_for(deps, "question", 4, ("market",), "topic words").chunks]
    assert got == ["m0", "m1", "g0", "g1"]                       # half the slots from the topic, then the rest
    assert [c.chunk_id for c in evidence_for(deps, "question", 4).chunks] == ["g0", "g1", "g2", "g3"]
