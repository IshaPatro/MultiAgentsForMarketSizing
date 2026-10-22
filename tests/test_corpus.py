"""Structured corpus: parsing, provenance checks, reconciliation, lineage and dating."""
import re

import pytest
import yaml

from debate.config import ROOT, CuratedFiles, ExhibitSpec, load_corpus
from debate.config import PublicSection, PublicSource
from debate.rag.corpus import (Assumption, CorpusChunk, Fact, compute_sizing, date_public_item, extract_note_markers,
                               load_assumptions, normalize, parse_budget, parse_matrix, parse_public,
                               resolve_evidence, validate_chunks)

KNOWN = {str(n) for n in range(1, 44)}


@pytest.mark.parametrize("text, clean, notes", [
    ("raised $25 million from investors.2 Next", "raised $25 million from investors. Next", ["2"]),
    ("the Best Inventions of the Year.”1 One of those", "the Best Inventions of the Year.” One of those", ["1"]),
    ("since the 1960s.4", "since the 1960s.", ["4"]),
    ("via 50,427 manholes43), and", "via 50,427 manholes), and", ["43"]),
    ("published several articles.27,28,29,30", "published several articles.", ["27", "28", "29", "30"]),
    ("growing both scientifically and commercially,31 very", "growing both scientifically and commercially, very", ["31"]),
    ("allocated $3.6 million from", "allocated $3.6 million from", []),          # a decimal is not a marker
    ("identified 58,309 viruses", "identified 58,309 viruses", []),
    ("in FY2018 the budget", "in FY2018 the budget", []),
    ("Luigi.99 is not an endnote", "Luigi.99 is not an endnote", []),         # unknown note numbers stay
])
def test_endnote_markers_are_resolved_without_touching_real_numbers(text, clean, notes):
    assert extract_note_markers(text, KNOWN) == (clean, notes)


def test_normalize_makes_quotes_comparable():
    assert normalize("They’d been told “no” – lab- based\n work") == "They'd been told \"no\" - lab-based work"


MATRIX = [  # Exhibit 5 shape: two blocks of segments, wrapped attribute cells, the second block's headings
    ["", "Federal", "Municipal"], ["", "Agencies", "Governments"],
    ["End User", "analysts", "mayor and staff"],
    ["Competition", "public health", "public health"], ["", "departments", "departments & police"],
    ["", "Hedge", "Insurance"], ["", "Funds", "Providers"],
    ["End User", "Analysts", "Analysts"],
    ["Competition", "CDC, pharma sales", "hospitals"],
]


def test_matrix_exhibits_become_one_row_per_segment_with_wrapped_cells_joined():
    spec = ExhibitSpec(id="ex5", number=5, page=22, layout="matrix", topic="customers_go_to_market", as_of="2017",
                       header_lines=2, groups=["Public sector", "Private sector"])
    rows = parse_matrix(MATRIX, spec)
    assert [(r["group"], r["segment"]) for r in rows] == [
        ("Public sector", "Federal Agencies"), ("Public sector", "Municipal Governments"),
        ("Private sector", "Hedge Funds"), ("Private sector", "Insurance Providers")]
    assert rows[1]["competition"] == "public health departments & police"
    assert rows[2]["end_user"] == "Analysts"


def _budget_spec():
    return ExhibitSpec(id="ex7", number=7, page=24, layout="budget", topic="financials", as_of="2017-07",
                       columns={"fy16": "FY16", "fy17": "FY17"})


BUDGET = [
    ["", "SERVICES", "FY16", "FY17"],
    ["", "Line A", "100", "110"], ["", "Line B", "200", "210"], ["", "Total Bureau X", "300", "320"],
    ["", "Standalone Y", "1,0,00", "500"],           # malformed digit grouping in the source
    ["", "", "", ""],
    ["", "Line C", "-", "40"], ["", "Total Bureau Z", "0", "40"],
    ["", "Total Services Expenditures", "1,300", "860"],
]


def test_budget_rows_reconcile_and_a_malformed_cell_is_implied_from_the_total():
    rows, notes = parse_budget(BUDGET, _budget_spec())
    kinds = {r["line_item"]: (r["kind"], r["bureau"]) for r in rows}
    assert kinds["Line A"] == ("line", "Bureau X") and kinds["Standalone Y"] == ("standalone", "Standalone Y")
    assert kinds["Total Services Expenditures"] == ("grand_total", "All")
    y = next(r for r in rows if r["line_item"] == "Standalone Y")
    assert y["fy16"] == 1000 and y["fy16_printed"] == "1,0,00" and "malformed" in y["note"]
    assert any("FY17: bureaus + standalone lines = 860 -> reconciles" in n for n in notes)
    assert next(r for r in rows if r["line_item"] == "Line C")["fy16"] == 0     # "-" means nothing budgeted


def test_budget_flags_a_subtotal_that_does_not_add_up():
    bad = [r[:] for r in BUDGET]
    bad[3] = ["", "Total Bureau X", "350", "320"]
    _, notes = parse_budget(bad, _budget_spec())
    assert any("Total Bureau X FY16: lines sum to 300, printed 350" in n for n in notes)


def test_fact_rules():
    base = {"id": "x", "topic": "market", "statement": "s", "as_of": "2017"}
    with pytest.raises(ValueError, match="quote"):
        Fact(**base, kind="reported")                                   # no quote, no page
    with pytest.raises(ValueError, match="attributed_to"):
        Fact(**base, kind="estimate", quote="q", page=1)                # whose estimate?
    with pytest.raises(ValueError, match="formula"):
        Fact(**base, kind="derived")
    with pytest.raises(ValueError):
        Fact(**{**base, "as_of": "2017/01"}, kind="reported", quote="q", page=1)   # dates are ISO-partial only
    Fact(**base, kind="derived", formula="2 x 3 = 6", derived_from=["a"])


def test_every_chunk_must_be_dated_unique_and_on_a_known_topic():
    m = load_corpus("biobot")
    ok = CorpusChunk("a", "case", "narrative", "market", "2017", "text")
    validate_chunks([ok], m)
    with pytest.raises(ValueError, match="as_of"):
        validate_chunks([CorpusChunk("b", "case", "narrative", "market", "", "text")], m)
    with pytest.raises(ValueError, match="duplicate"):
        validate_chunks([ok, ok], m)
    with pytest.raises(ValueError, match="unknown topic"):
        validate_chunks([CorpusChunk("c", "case", "narrative", "gossip", "2017", "text")], m)


def test_sizing_recomputes_from_inputs_and_records_lineage(tmp_path):
    (tmp_path / "a.yaml").write_text(yaml.safe_dump([
        {"id": "price", "statement": "p", "value": 1000, "unit": "USD", "as_of": "2026", "status": "assumption",
         "basis": "b"}]))
    (tmp_path / "s.yaml").write_text(yaml.safe_dump([
        {"id": "m", "name": "M", "approach": "bottom-up", "as_of": "2017",
         "steps": [{"ref": "sites"}, {"ref": "per_year"}, {"ref": "price"}]}]))
    m = load_corpus("biobot").model_copy(update={"curated": CuratedFiles(
        facts="f.yaml", assumptions=str(tmp_path / "a.yaml"), sizing=str(tmp_path / "s.yaml"))})
    facts = [Fact(id="sites", topic="market", statement="s", kind="reported", as_of="2017", quote="q", page=1,
                  value=500, unit="sites"),
             Fact(id="per_year", topic="market", statement="n", kind="derived", as_of="2017", formula="1 x 12 = 12",
                  derived_from=["sites"], value=12, unit="samples")]
    (result,) = compute_sizing(m, facts, load_assumptions(m))
    assert result["result"] == 6_000_000
    assert [(l["ref"], l["source"]) for l in result["lineage"]] == [
        ("sites", "fact"), ("per_year", "derived"), ("price", "assumption")]


# --------------------------------------------------------------------------------------- public ledger

@pytest.mark.parametrize("raw, cell, section_checked, first, expected", [
    ("| 2018-05 | raised |", "2018-05", None, "", ("2018-05", "date column")),
    ("| 2022–23 | CDC award |", "2022–23", None, "", ("2023", "date column")),                # a range ends late
    ("| 2023-09 to 2024-08 | pilot |", "2023-09 to 2024-08", None, "", ("2024-08", "date column")),
    ("- **Cary pilot, 2018.** 10 sites, cited 2019-03-26", None, None, "", ("2018", "event year in lead")),
    ("- **Commercial expansion, 2021–23.** HHS", None, None, "", ("2023", "event year in lead")),
    ("| Price | $15,000/year | report, 2025-11-24 |", None, None, "Price", ("2025-11-24", "latest cited publication date")),
    ("| 2021 financing, sites, team | in 2022 | x |", None, None, "2021 financing", ("2021", "year in first cell")),
    ("- Biobot's homepage lists 50 trends.", None, "2026-10-20", "", ("2026-10-20", "section checked date")),
    ("- A 2019 paper describes robots.", None, None, "", ("2019", "latest year mentioned")),
    ("- Biobot currently claims ZIP-level coverage.", None, None, "", ("2026-10-22", "ledger checked date (current-state item)")),
])
def test_public_items_are_dated_by_the_strongest_signal_they_carry(raw, cell, section_checked, first, expected):
    assert date_public_item(raw, cell, section_checked, "2026-10-22", first) == expected


LEDGER = """# Ledger
Intro paragraph that is not in any section.

## Funding (checked 2026-10-01)

| Date | Fact | Source |
|---|---|---|
| 2021-10-22 | Biobot announced a **\\$20 million** Series A. | [Press release](https://example.org/a) |

## History

- **Cary pilot, 2018.** Each sample represented roughly **4,000–15,000 people**. [Cary](https://example.org/b)
- Biobot currently sells dashboards.

## Leads

| Screenshot field | Transcription |
|---|---|
| 2018-01-04 | Pre-seed $120k |

## Unmapped

- Ignored entirely.
"""


def _ledger(tmp_path, **overrides):
    (tmp_path / "ledger.md").write_text(LEDGER)
    fields = {"id": "pub", "path": "ledger.md", "title": "Ledger", "checked": "2026-10-22",
              "sections": {"Funding": PublicSection(topic="financials"), "History": PublicSection(topic="company_history"),
                           "Leads": PublicSection(topic="financials", reliability="lead")}} | overrides
    return PublicSource(**fields)


def test_ledger_items_become_dated_topic_tagged_chunks_with_links_kept_aside(tmp_path):
    items = parse_public(_ledger(tmp_path, item_topics={"Biobot currently": "technology_ip"}), "co", tmp_path)
    assert [(i["id"], i["as_of"], i["topic"], i["reliability"]) for i in items] == [
        ("co_pub_funding_01", "2021-10-22", "financials", "sourced"),
        ("co_pub_history_01", "2018", "company_history", "sourced"),
        ("co_pub_history_02", "2026-10-22", "technology_ip", "sourced"),
        ("co_pub_leads_01", "2018-01-04", "financials", "lead")]
    funding = items[0]
    assert funding["text"] == "Date: 2021-10-22. Fact: Biobot announced a $20 million Series A. Source: Press release"
    assert funding["urls"] == ["https://example.org/a"]
    assert "**" not in items[1]["text"] and "4,000–15,000 people" in items[1]["text"]


def test_ledger_rejects_missing_sections_future_dates_and_ambiguous_overrides(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        parse_public(_ledger(tmp_path, sections={"Nope": PublicSection(topic="market")}), "co", tmp_path)
    with pytest.raises(ValueError, match="after the ledger was checked"):
        parse_public(_ledger(tmp_path, checked="2020-01-01"), "co", tmp_path)
    with pytest.raises(ValueError, match="exactly one item"):
        parse_public(_ledger(tmp_path, item_dates={"Nothing starts like this": "2020"}), "co", tmp_path)


def _assumption(**kw):
    base = {"id": "catchment", "statement": "s", "value": 10000, "unit": "people", "as_of": "2018",
            "status": "public_source", "basis": "b",
            "evidence": [{"section": "History", "contains": ["Cary pilot, 2018", "4,000–15,000"]}]}
    return Assumption(**(base | kw))


def test_public_source_inputs_must_resolve_to_exactly_one_ledger_item_with_the_same_date(tmp_path):
    items = parse_public(_ledger(tmp_path), "co", tmp_path)
    assert resolve_evidence([_assumption()], items) == {
        "catchment": [{"id": "co_pub_history_01", "as_of": "2018", "role": "source"}]}
    with pytest.raises(ValueError, match="matches 0 items"):
        resolve_evidence([_assumption(evidence=[{"section": "History", "contains": ["Boston"]}])], items)
    with pytest.raises(ValueError, match="differs from its source"):
        resolve_evidence([_assumption(as_of="2026-10-22")], items)
    with pytest.raises(ValueError, match="role source"):
        _assumption(evidence=[])                       # a public_source value must say where it comes from


# ------------------------------------------------------------------------------- the real corpus (licensed)

_m = load_corpus("biobot")
_have_case = (ROOT / _m.case.path).exists() and (ROOT / _m.curated.facts).exists()
needs_case = pytest.mark.skipif(not _have_case, reason="licensed case PDF or curated facts not present")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from debate.rag.corpus import build_corpus, load_chunks
    out = tmp_path_factory.mktemp("corpus")
    manifest = _m.model_copy(update={"corpus_dir": str(out)})   # never touch the real generated files
    stats = build_corpus(manifest)
    return manifest, stats, load_chunks(manifest), out


@needs_case
def test_real_build_is_complete_dated_and_typed(built):
    manifest, stats, chunks, out = built
    assert stats["endnotes"] == 44 and stats["narrative_chunks"] >= 50
    by_type = {}
    for c in chunks:
        by_type[c.doc_type] = by_type.get(c.doc_type, 0) + 1
    assert set(by_type) == {"narrative", "fact", "exhibit_row", "exhibit_caption", "endnote", "assumption", "sizing_model",
                            "public_source", "public_lead"}
    assert stats["public_items"] == by_type["public_source"] + by_type["public_lead"] >= 90
    assert by_type["fact"] >= 80 and by_type["exhibit_row"] >= 50
    assert all(re.match(r"^\d{4}(-\d{2}(-\d{2})?)?$", c.as_of) for c in chunks)
    assert {f.stem for f in (out / "narrative").glob("*.md")} >= {"02_founders_team", "06_market", "09_risks_and_objections"}


@needs_case
def test_real_narrative_has_no_licence_furniture_or_stray_markers(built):
    _, _, chunks, _ = built
    text = " ".join(c.text for c in chunks if c.doc_type == "narrative")
    for junk in ("For the exclusive use", "This document is authorized", "Copyright ©", "Isha Patro"):
        assert junk not in text
    assert "investors.2" not in text and "1960s.4" not in text and "manholes43" not in text


@needs_case
def test_real_exhibit_seven_reconciles_and_recovers_the_malformed_cell(built):
    _, stats, chunks, _ = built
    notes = " ".join(stats["reconciliation"])
    assert "implied by the column total: 49,693,309" in notes
    assert "FY17 budget: bureaus + standalone lines = 98,022,291 -> reconciles" in notes
    assert "FY18 budget: bureaus + standalone lines = 101,092,192 -> reconciles" in notes
    ems = next(c for c in chunks if c.chunk_id == "biobot_ex7_emergency_medical_services")
    assert "$49.69M" in ems.text and "malformed in the source" in ems.text


@needs_case
def test_real_endnotes_carry_their_own_dates_and_links(built):
    _, _, chunks, _ = built
    n39 = next(c for c in chunks if c.chunk_id == "biobot_note_39")
    assert n39.as_of == "2017-06-05" and "Cited on p.11" in n39.text
    fact = next(c for c in chunks if c.chunk_id == "biobot_fact_overdose_deaths_2016")
    assert fact.as_of == "2016" and "endnote 39" in fact.text


@needs_case
def test_real_sizing_lineage_reproduces_the_slide(built):
    _, _, chunks, out = built
    sizing = {c.chunk_id: c.meta["result"] for c in chunks if c.doc_type == "sizing_model"}
    assert sizing["biobot_sizing_beachhead_budgets"] == pytest.approx(511.4e6, rel=1e-3)
    assert sizing["biobot_sizing_share_of_existing_market"] == pytest.approx(744.2e6, rel=1e-3)
    assert sizing["biobot_sizing_manhole_sampling"] == pytest.approx(6.51e9, rel=1e-3)
    readme = (out / "README.md").read_text()
    assert "assumption (external_unverified)" in readme and "fact (claim)" in readme


@needs_case
def test_real_sizing_inputs_are_sourced_from_the_public_ledger(built):
    _, _, chunks, out = built
    by_id = {c.chunk_id: c for c in chunks}
    catchment = by_id["biobot_assume_people_per_catchment"]
    (ref,) = catchment.meta["evidence"]
    assert catchment.as_of == "2018" and "4,000–15,000 people" in by_id[ref["id"]].text
    assert by_id["biobot_assume_price_per_sample"].meta["evidence"][0]["role"] == "cross_check"
    floor = by_id["biobot_sizing_city_price_floor"]
    assert floor.meta["result"] == pytest.approx(870 * 15_000) and "Caveat:" in floor.text
    assert all(c.text.startswith("UNVERIFIED LEAD") for c in chunks if c.doc_type == "public_lead")
    assert "## Public sources" in (out / "README.md").read_text()
