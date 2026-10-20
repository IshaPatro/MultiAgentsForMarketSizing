"""Leakage tests for retrieval (implementation.md section 9, leak 1). Needs the Postgres container."""
from datetime import date

import pytest
from conftest import chunk, needs_pg

from debate.config import load_era
from debate.rag import db
from debate.rag.embeddings import HashEmbedder
from debate.rag.retriever import LeakError, PgRetriever, assert_no_leak, or_query

PRE, POST = load_era("pre_covid"), load_era("post_covid")
CO = "testco"
ROWS = [  # chunk_id, text, as_of_date, era_tags
    ("t_old", "wastewater sampling robot pump prototype built from off the shelf parts", date(2015, 6, 1), ["pre_covid", "post_covid"]),
    ("t_2017", "the founders planned to sell opioid consumption data to cities", date(2017, 9, 30), ["pre_covid", "post_covid"]),
    ("t_future", "wastewater surveillance of covid became a national priority and raised millions", date(2021, 3, 1), ["post_covid"]),
    ("t_undated", "wastewater opioid cities covid planted undated chunk", None, []),
    ("t_badtags", "wastewater opioid cities consumption data planted chunk with wrong era tags", date(2016, 1, 1), ["post_covid"]),
]


@pytest.fixture
def retriever():
    conn = db.connect()
    db.ensure_schema(conn)
    emb = HashEmbedder()
    conn.execute("DELETE FROM chunks WHERE company_id=%s", (CO,))
    for cid, text, as_of, tags in ROWS:
        conn.execute(
            "INSERT INTO chunks (chunk_id, company_id, source_id, source, doc_type, page, section, text, as_of_date,"
            " era_tags, embedding_model, embedding) VALUES (%s,%s,'s','s','case',1,'x',%s,%s,%s,%s,%s)",
            (cid, CO, text, as_of, tags, emb.name, emb.embed_docs([text])[0]))
    yield PgRetriever(conn, emb)
    conn.execute("DELETE FROM chunks WHERE company_id=%s", (CO,))
    conn.close()


QUERY = "wastewater covid opioid consumption data cities surveillance"


@needs_pg
def test_pre_covid_never_sees_later_undated_or_mistagged_chunks(retriever):
    got = retriever.search(QUERY, PRE, CO, k=10)
    assert {c.chunk_id for c in got.chunks} == {"t_old", "t_2017"}
    assert got.max_as_of_date <= PRE.cutoff_date


@needs_pg
def test_post_covid_sees_later_chunks_but_still_not_undated_ones(retriever):
    ids = {c.chunk_id for c in retriever.search(QUERY, POST, CO, k=10).chunks}
    assert "t_future" in ids and "t_old" in ids
    assert "t_undated" not in ids


@needs_pg
def test_an_exact_text_match_does_not_beat_the_filter(retriever):
    got = retriever.search("wastewater surveillance of covid became a national priority and raised millions", PRE, CO, k=10)
    assert "t_future" not in {c.chunk_id for c in got.chunks}


@needs_pg
def test_other_companies_are_invisible(retriever):
    assert retriever.search(QUERY, PRE, "someone_else", k=10).chunks == []


@needs_pg
def test_unreviewed_and_other_embedding_models_are_invisible(retriever):
    conn = retriever.conn
    conn.execute("UPDATE chunks SET needs_review=TRUE WHERE chunk_id=%s", ("t_old",))
    conn.execute("UPDATE chunks SET embedding_model=%s WHERE chunk_id=%s", ("other-model", "t_2017"))
    assert retriever.search(QUERY, PRE, CO, k=10).chunks == []


def test_assert_no_leak_raises_on_a_late_or_undated_chunk():
    assert_no_leak([chunk(as_of=date(2017, 1, 1))], PRE)
    with pytest.raises(LeakError):
        assert_no_leak([chunk(as_of=date(2018, 1, 1))], PRE)
    with pytest.raises(LeakError):
        assert_no_leak([chunk(as_of=None)], PRE)


def test_or_query_drops_stopwords_and_punctuation():
    assert or_query("What is the market, and who pays?") == "market | who | pays"
    assert or_query("?!") == ""
