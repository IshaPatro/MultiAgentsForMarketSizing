from datetime import date

import pytest

from debate.config import ROOT, ApprovedDate, CorpusManifest, TierConfig, load_corpus, load_eras
from debate.rag.dating import DateJudgement, date_chunk, later_years
from debate.rag.ingest import RawChunk, decide_date, text_fingerprint, MIN_EXHIBIT_WORDS, TARGET_WORDS, chunk_exhibit_page, chunk_narrative, clean_block, era_tags_for, extract_chunks

BASE = date(2017, 9, 30)
TIER = TierConfig(model="m", effort="low", max_tokens=100)


def test_clean_block_drops_licence_furniture_urls_and_endnote_markers():
    assert clean_block("For the exclusive use of I. Patro, 2026.\nThis document is authorized for use only by X.") == ""
    assert clean_block("Copyright © 2021, 2023 President and Fellows of Harvard College.") == ""
    assert clean_block("Biobot Analytics \n821-045 \n") == ""
    text = clean_block("They raised funds.” 39 Ghaeli agreed. Source: x, https://a.b/c, accessed December 2020.")
    assert "39" not in text and "http" not in text and "2020" not in text and "Ghaeli agreed" in text


def test_narrative_chunks_are_bounded_overlapping_and_carry_the_right_page():
    para = " ".join(f"w{i}" for i in range(120)) + "."
    paras = [(1, "Intro Heading")] + [(1 + i // 3, para) for i in range(9)]
    chunks = chunk_narrative(paras, "src", "co", "case")
    assert len(chunks) >= 3
    assert all(len(c.text.split()) <= TARGET_WORDS + 130 for c in chunks)
    assert chunks[0].section == "Intro Heading"
    assert [c.page for c in chunks] == sorted(c.page for c in chunks)
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    tail = chunks[0].text.split()[-20:]
    assert " ".join(tail) in chunks[1].text            # overlap carried into the next chunk


def test_exhibit_pages_stay_whole_and_empty_figures_are_dropped():
    words = ["Exhibit 7 City budget"] + ["row"] * 60
    (c,) = chunk_exhibit_page([" ".join(words)], 24, "src", "co")
    assert c.section.startswith("Exhibit 7") and c.doc_type == "exhibit"
    assert chunk_exhibit_page(["Exhibit 3 a caption only"], 17, "src", "co") == []
    assert MIN_EXHIBIT_WORDS > 5


def test_undated_years_fail_closed_without_a_checker():
    assert date_chunk("Sales began in 2016.", BASE).as_of == BASE
    d = date_chunk("The market will reach $138 billion by 2022.", BASE)
    assert d.as_of is None and d.needs_review


class FakeJudge:
    def __init__(self, j):
        self.j = j

    def generate(self, **kw):
        return self.j, None


def test_dating_with_a_checker_distinguishes_projections_from_events():
    text = "The market will reach $138 billion by 2022."
    proj = date_chunk(text, BASE, FakeJudge(DateJudgement(describes_later_events=False, latest_event_year=None, reason="forecast")), TIER)
    assert proj.as_of == BASE and proj.needs_review
    event = date_chunk(text, BASE, FakeJudge(DateJudgement(describes_later_events=True, latest_event_year=2020, reason="happened")), TIER)
    assert event.as_of == date(2020, 12, 31) and event.needs_review


def test_later_years_ignores_past_years():
    assert later_years("In 2012 and 2017 and 2022 and 1854", BASE) == [2022]


def test_era_tags_follow_the_cutoffs():
    eras = load_eras()
    assert era_tags_for(date(2017, 9, 30), eras) == ["pre_covid", "post_covid"]
    assert era_tags_for(date(2020, 5, 1), eras) == ["post_covid"]
    assert era_tags_for(None, eras) == []


def test_reviewed_dates_are_explicit_in_the_manifest():
    manifest = load_corpus("biobot")
    assert manifest.approved_dates["biobot_hbs_case_p5_c2"].as_of == BASE
    assert manifest.approved_dates["biobot_hbs_case_p24_c1"].as_of == BASE


@pytest.mark.skipif(not (ROOT / "Biobot/01.BiobotAnalytics.pdf").exists(), reason="licensed case PDF not present")
def test_real_case_pdf_has_no_furniture_no_endnotes_and_no_unflagged_late_years():
    m = load_corpus("biobot")
    chunks, skipped = extract_chunks(m.sources[0], "biobot")
    assert skipped and max(c.page for c in chunks) <= 24
    joined = " ".join(c.text for c in chunks)
    for junk in ("For the exclusive use", "This document is authorized", "Copyright ©", "Isha Patro"):
        assert junk not in joined
    assert all(len(c.text.split()) <= TARGET_WORDS + 120 for c in chunks)
    assert 35 <= len(chunks) <= 60


def _manifest(approved):
    return CorpusManifest(company_id="co", default_as_of=BASE, sources=[], approved_dates=approved)


def test_a_reviewed_date_applies_only_to_the_text_that_was_reviewed():
    reviewed = RawChunk("co_s_p5_c2", 5, "s", "case", "The market will reach $138 billion by 2022.")
    pin = {reviewed.chunk_id: ApprovedDate(as_of=BASE, text_sha256=text_fingerprint(reviewed.text))}
    ok = decide_date(reviewed, _manifest(pin), None, None)
    assert ok.as_of == BASE and not ok.needs_review

    # Same positional id after a chunking change, now holding unreviewed hindsight: must not inherit the approval.
    changed = RawChunk(reviewed.chunk_id, 5, "s", "case", "In 2020 the CDC adopted wastewater testing nationwide.")
    stale = decide_date(changed, _manifest(pin), None, None)
    assert stale.as_of is None and stale.needs_review and "stale" in stale.note


@pytest.mark.skipif(not (ROOT / "Biobot/01.BiobotAnalytics.pdf").exists(), reason="licensed case PDF not present")
def test_every_approval_in_the_manifest_still_matches_its_chunk():
    m = load_corpus("biobot")
    chunks = {c.chunk_id: c for c in extract_chunks(m.sources[0], "biobot")[0]}
    for cid, approval in m.approved_dates.items():
        assert text_fingerprint(chunks[cid].text) == approval.text_sha256, cid
