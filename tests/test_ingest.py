from debate.rag.ingest import MIN_EXHIBIT_WORDS, chunk_exhibit_page, clean_block


def test_clean_block_drops_licence_furniture_urls_and_spaced_endnote_markers():
    assert clean_block("For the exclusive use of I. Patro, 2026.\nThis document is authorized for use only by X.") == ""
    assert clean_block("Copyright © 2021, 2023 President and Fellows of Harvard College.") == ""
    assert clean_block("Biobot Analytics \n821-045 \n") == ""
    text = clean_block("They raised funds.” 39 Ghaeli agreed. Source: x, https://a.b/c, accessed December 2020.")
    assert "39" not in text and "http" not in text and "2020" not in text and "Ghaeli agreed" in text


def test_exhibit_figure_pages_keep_their_caption_and_empty_figures_are_dropped():
    words = ["Exhibit 2 National Wastewater Drug Monitoring Program"] + ["row"] * 40
    (c,) = chunk_exhibit_page([" ".join(words)], 15, "caption", "co")
    assert c.section.startswith("Exhibit 2") and c.chunk_id == "co_caption_p15_c1"
    assert chunk_exhibit_page(["Exhibit 3 a caption only"], 17, "caption", "co") == []
    assert MIN_EXHIBIT_WORDS > 5
